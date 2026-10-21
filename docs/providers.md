# Providers — Reference

Everything that differs between Claude Code and Codex lives in
`agentctl/providers/`. The subcommands (`status`, `renew`, `accounts`, `copy`,
`log`, `ui`) never special-case a provider; they call the contract below.

## The contract

An **account** is one login of one provider, addressed everywhere by a unique
**label**: claude → `default`, `benjr`, `bggai` …; codex → `codex`,
`codex-<alias>` … Labels are unique across providers, so every command takes
just the label (`agentctl renew add codex new-acp`).

`provider.discover()` returns account dicts:

```python
{"provider": "claude" | "codex",
 "label":      str,        # unique, what the user types
 "config_dir": str,        # ~/.claude, ~/.claude-bggai, ~/.codex …
 "suffix_dir": str | None, # value for CLAUDE_CONFIG_DIR / CODEX_HOME
                           # (None = default account = run WITHOUT it)
 "shell_alias": str}       # what the user types to open that account
```

Every provider module exposes these functions (duck-typed, no base class):

| Function | Contract |
|----------|----------|
| `discover() -> [account]` | Logged-in accounts of this provider. Cheap; no network. |
| `usage(account, interactive=False) -> dict` | Read-only limit check that **never anchors a usage window**. |
| `session_reset_at(account) -> (datetime\|None, info)` | Exact moment the 5-hour session window resets (naive local). Read-only. |
| `live_sessions(account) -> [session]` | Sessions that can receive a message right now, newest first: `{name, cwd, pid, id, started}`. |
| `find_live_sessions(account, name, path=None)` | Subset of `live_sessions()` with this name (and this real cwd if `path`). |
| `send_message(account, name, cwd, text) -> (status, detail)` | Deliver `text` to the live session called `name`. Status: `sent` \| `sent-limited` \| `not-live` \| `limited` \| `failed`. `sent-limited` means it arrived but its turn died on the usage limit — delivered, so never re-send the same text. |
| `anchor_ping(account, cwd) -> (status, output)` | Cheapest request that opens a usage window, never touching a working session. Status: `ok` \| `limited` \| `failed`. |
| `login_hint(account) -> str` | The command to log in again. |
| `resume_hint(account, session_name) -> str` | The command to reopen a session. |

The `usage()` dict:

```python
{"email": str,
 "limits": [{"kind": "session" | "weekly_all" | "weekly_scoped",
             "percent": int,
             "resets_at": "ISO-8601" | None,
             "reset_text": str,            # only when resets_at is unknown
             "is_active": bool,            # this limit is blocking right now
             "window_minutes": int | None,
             "scope": {...}}],             # weekly_scoped only
 "source": "api" | "tui" | "rollout",      # where the numbers came from
 "refreshed": bool, "retries": int,        # optional notes
 "error": str}                             # present => nothing else usable
```

Registry helpers in `providers/__init__.py`: `discover_accounts()` (all
providers, claude first), `get_account(label)` (case-insensitive lookup),
`provider_of(account)` (the module). **Provider modules import only the stdlib.**

---

## Feature support

Every subcommand works on both providers unless this table says otherwise.

| Feature | Claude Code | Codex |
|---|---|---|
| `agentctl status` bars, `--json` | yes | yes |
| Session (5 h) + weekly limits | yes | yes |
| Weekly **per-model** limit (e.g. Fable) | yes | no — the endpoint exposes two windows only |
| Exact reset timestamps | yes | yes |
| Plan / extra-credit line | extra-usage credits | plan name + credit balance |
| Fallback when the usage API fails | interactive `/status` Usage tab (used on 429) | `rate_limits` from the newest rollout file |
| Automatic expired-token recovery | yes — a temporary interactive `/status` terminal | no — open `codex` once, then retry |
| `agentctl status --interactive` | yes | ignored (no TUI data path) |
| `agentctl accounts list` | yes | yes |
| `agentctl accounts add` (dir + shell alias) | yes | yes |
| Multiple accounts on one Mac | yes — `~/.claude-<alias>` + `CLAUDE_CONFIG_DIR` | yes — `~/.codex-<alias>` + `CODEX_HOME` |
| Live-session discovery | yes — `<config>/sessions/<pid>.json` | yes — rollout files held open by a codex process |
| Address a session by name | yes — the `/rename` alias | yes — the `/rename` name, the auto title, or the uuid |
| Pin a session to a working directory (`--path`) | yes | yes |
| `agentctl renew` scheduled continue | yes | yes |
| `--query` / `--message` on the continue | yes | yes |
| Follow-up window anchor | yes | yes |
| Delivery mechanism | headless haiku helper (`ListAgents` + `SendMessage`) | `codex queue --thread` (built in) |
| Delivery cost | one haiku request — which also anchors the window | free, local |
| Delivery confirmation | the helper reports `DONE` | the transcript is watched for up to 45 s |
| Continue died on the limit | retried | detected as `sent-limited`, re-armed at the next reset |
| Session stuck after a failed turn | n/a — the helper reaches a live session directly | the queue is not drained; logged as `QUEUED BUT NOT PICKED UP`, needs a keystroke in that terminal |
| `agentctl anchor` (morning launchd job) | yes | yes |
| `agentctl log` | yes | yes |
| `agentctl ui` | yes | yes, except the Copy view |
| `agentctl copy` (migrate a session between accounts) | yes | `--list` only; copying is not supported yet |
| Desktop app | not yet | not yet |

## Claude Code

Verified live against the Claude Code CLI (2026-09-06 · 2026-09-12).

- **Config dir:** `~/.claude` (default) or `~/.claude-<label>`, selected with
  `CLAUDE_CONFIG_DIR`. The **default account must run WITHOUT that variable**:
  with it set, the CLI looks for a hashed keychain entry that does not exist and
  reports a false "Not logged in". Discovery = `~/.claude` plus every
  `~/.claude-*` dir with a `.claude.json` (wrapper dirs like `.claude-deepinfra`
  are excluded because they have none). The default account's live config is
  `~/.claude.json`, **not** `~/.claude/.claude.json`.
- **Credentials:** the macOS keychain, under the CLI's own entries —
  `Claude Code-credentials` (default) or
  `Claude Code-credentials-<first 8 hex of sha256(config-dir path)>`
  (benjr → `-da3e029b`, bggai → `-c92558f3`). Payload:
  `{"claudeAiOauth": {"accessToken", "expiresAt" (epoch ms), …}}`.
- **Usage:** `GET https://api.anthropic.com/api/oauth/usage` with the bearer
  token plus `anthropic-beta: oauth-2025-04-20` and
  `anthropic-version: 2023-06-01` — the same read-only endpoint the /status
  Usage tab renders from. Costs nothing, anchors nothing. Three limits per
  account: session (5 h), weekly (all models), weekly-scoped (e.g. Fable).
- **429 is never retried** — hammering the rate limit extends it (learned
  2026-09-07 when the UI's 60 s auto-poll × 3 accounts × 3 retries throttled
  every token). The interactive Usage tab keeps working during a 429 because the
  TUI has its own data path, so the fallback drives a headless
  `claude --safe-mode --ax-screen-reader` through `/status → right → right` in a
  pty and parses the panel. Those numbers carry **no exact reset timestamp**,
  only the human "Resets …" text (`source: "tui"`).
- **Token refresh:** `claude auth status` only *reports* authentication — it is
  not a refresh trigger. The only non-interactive renewal is opening the
  interactive `/status` terminal, which the recovery path does without ever
  sending a model prompt or accepting a trust dialog.
- **Sessions:** `<config_dir>/sessions/<pid>.json` with
  `{name, cwd, pid, sessionId, startedAt}`. A session is **live** when that pid
  is alive; it is addressed by its `/rename` alias.
- **Messaging a live session:** a headless
  `claude -p --model haiku --allowedTools ListAgents,SendMessage` helper on the
  **same account** (peer visibility is per config dir). The prompt goes in on
  **stdin**, because `--allowedTools` is variadic and would swallow a trailing
  argument.
- **Anchor ping:** a throwaway `claude -p --model haiku` answering
  "Hello, only answer hello" — never the working session, which runs on its own
  (expensive) model.

## Codex

Verified live 2026-09-14 against codex-cli 0.154.0.

- **Config dir:** `~/.codex` (or `$CODEX_HOME`). Login state is
  `~/.codex/auth.json`:
  `{"auth_mode", "tokens": {"access_token", "id_token", "refresh_token", "account_id"}, "last_refresh"}`.
  There is exactly **one account per config dir**; extra accounts live in
  `~/.codex-<alias>` and run with `CODEX_HOME` set. Discovery = any of those
  dirs with an `auth.json`.
- **Usage:** `GET https://chatgpt.com/backend-api/wham/usage` with the bearer
  access token plus a `ChatGPT-Account-Id` header. Read-only, anchors nothing.
  `rate_limit.primary_window` is the 5 h session window
  (`limit_window_seconds` 18000, `used_percent`, `reset_at` epoch);
  `secondary_window` is the weekly one. No weekly-scoped limit exists.
- **Offline fallback:** the same numbers are written into every session's
  rollout file as `token_count` events
  (`payload.info.rate_limits.primary/secondary`, `resets_at` epoch). When the
  token is stale the newest rollout file is read instead (`source: "rollout"`),
  and the answer is reported with the timestamp it was captured at. A `401`/`403`
  means the token expired — opening `codex` once refreshes it.
- **Sessions:** `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`. Names
  (from `/rename` or the auto title), `cwd` and `source` live in
  `~/.codex/state_5.sqlite`, table `threads` (read opened read-only, `mode=ro`).
  A session is **live** when a codex process holds its rollout file open
  (`lsof -c codex`). Only threads with `source = "cli"` are addressable —
  subagent threads are filtered out.
- **Messaging a live session:** `codex queue --thread <name|uuid> --message TEXT`,
  built into the CLI — **no helper model needed**, so it costs nothing by itself.
  When nothing is live it prints `Error: No active session found matching
  '<name>'` **and exits 0**, so the *text* must be checked, not the exit code.
  The session must be open in a terminal; `codex resume <name>` reopens it.
- **Anchor ping:** `codex exec --ephemeral --skip-git-repo-check -s read-only
  -C ~ "Hello, only answer hello"` — ephemeral, read-only sandbox, no git
  requirement, no session written.
- **Limit banner text:** "You've hit your usage limit … try again at 3:28 AM."

## Differences that leak into the UI

| | Claude Code | Codex |
|---|---|---|
| Credential store | macOS keychain | `auth.json` file |
| Limits | session + weekly + weekly-scoped | session + weekly |
| Exact reset timestamp | yes (API), no (TUI fallback) | yes (API and rollout) |
| Messaging cost | a haiku call (pennies) — which also anchors the window | free (`codex queue` is local) |
| Session identity | pid file, `/rename` name | sqlite `threads` row, uuid or name |

## How to add a provider

1. Write `agentctl/providers/<name>.py` implementing every function above.
   Stdlib only. Start from `codex.py` — it is the smaller of the two.
2. Give accounts **globally unique labels**. Prefix them with the provider name
   unless the provider owns the bare word (claude labels are bare for history;
   codex uses `codex` / `codex-<alias>`).
3. Add `<name>` to `PROVIDERS` in `providers/__init__.py`. That is the only edit
   outside your module: `discover_accounts()` picks it up and every subcommand
   follows.
4. Teach `bin/anchor.sh` about it — an `ACCOUNTS` discovery block and a branch in
   `ping_cmd` — if the provider should be anchored in the morning. Remember to
   run the default account **without** its config-dir env var if it has the same
   keychain/default-path quirk. Then `./install.sh`.
5. Add a case to `tests/test_anchor.py` (a fake binary on `$PATH` via a
   `<NAME>_BIN` env var) and document the facts you verified in the module
   docstring — the docstrings are the source of truth this file summarises.
