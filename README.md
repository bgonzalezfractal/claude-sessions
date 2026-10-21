# agentctl

One CLI for the usage windows of AI coding-agent CLIs — **Claude Code** (three
accounts on this Mac: `default`, `benjr`, `bggai`) and **Codex** (one account,
`codex`). Desktop apps later. One strategy throughout: anchor windows early,
never waste a renewal.

## The system

| Subcommand | What it does | Doc |
|------------|--------------|-----|
| **`agentctl status`** | Every account's limits (session / weekly / weekly-scoped) in one command, read-only, zero cost, no window anchored. | [docs/status.md](docs/status.md) |
| **`agentctl anchor`** | The morning launchd job: a tiny "hello" ping per account at 7:30 Mon–Fri / 10:30 Sat–Sun. An account that is **mid-window** cannot be anchored by a ping, so it queues a catch-up for the minute that window expires. | [docs/anchor.md](docs/anchor.md) |
| **`agentctl renew`** | When a session was **cut mid-task** overnight: auto-continues it at reset + 5 min (e.g. 5:45 AM) and sends a follow-up anchor 5 h later (10:50) — the earliest possible restart of your day. | [docs/renew.md](docs/renew.md) |
| **`agentctl copy`** | Migrate a session between accounts by name — front-end over the proven migrate script (transcript, sidecar, memory, tasks, config entry). | [docs/copy.md](docs/copy.md) |
| **`agentctl accounts`** | List accounts across providers (login state, keychain/token, live sessions) or scaffold a new one; new accounts auto-join every subcommand after first login. | [docs/accounts.md](docs/accounts.md) |
| **`agentctl log`** | One chronological stream of everything agentctl did: anchor pings, scheduled continues, follow-up anchors, retries (`-n`, `--kind`, `--json`, `-f`). Also shown in the UI's Activity view. | — |
| **`agentctl ui`** | Localhost dashboard (stdlib server + SQLite audit log at 127.0.0.1:8788) wrapping all of the above. | [docs/ui.md](docs/ui.md) |

**When to use which:** if nothing is in progress, do nothing — the morning
anchor fires at 7:30 and you start planning at 9–10 with a fresh window coming.
Register with `agentctl renew` **only when work was interrupted**: then the 5:40
renewal is worth catching at 5:45 rather than waiting for 7:30.

## Quick reference

```bash
agentctl status                     # limits for every account, both providers
agentctl status bggai --json        # one account, machine-readable
agentctl status codex               # Codex too — same table

agentctl renew                      # interactive wizard (account → session → times)
agentctl renew add bggai radal-v2 --path ~/Documents/repos/radal/radal-app
agentctl renew add codex new-acp --query "Continue, and consider the standardization style of ..."
agentctl renew list                 # pending tasks + daemon state
agentctl renew remove radal-v2      # cancel (or: remove all)
agentctl renew log -f               # watch it work

agentctl copy bggai default radal-v2      # migrate by title (--dry-run first)
agentctl copy --list bggai                # sessions on an account

agentctl accounts list              # every provider's accounts + login/live state
agentctl accounts add work          # scaffold ~/.claude-work + zshrc alias

agentctl anchor --dry-run           # what the morning job would ping
agentctl anchor                     # ping now

agentctl ui                         # dashboard at http://127.0.0.1:8788

agentctl log                        # merged: anchor pings + continues + follow-ups
agentctl log --kind continue,follow-up -f
```

### The continue message

What the resumed session actually receives is always:

```
Your usage limit has reset (scheduled auto-continue by agentctl). <tail>
```

- `<tail>` defaults to **"Continue with the pending task exactly where you left off."**
- `--query "..."` replaces only the tail, keeping the standard preamble — use it
  to steer the continuation (`--query "Continue, and consider the
  standardization style of ..."`).
- `--message "..."` overrides **everything**, preamble included, when you want
  to dictate the whole prompt.

## Install

```bash
./install.sh
```

Symlinks `bin/agentctl` into `/opt/homebrew/bin`, installs the anchor job
(a regular copy at `~/.agentctl/anchor.sh` plus the `com.grail.limitreset`
LaunchAgent) and cleans up the pre-rename `claude-*` symlinks, the legacy
`~/.claude/claude-status.py` / `~/.claude/limit-reset.sh`, and the old
`claude-status` alias in `~/.zshrc` (backed up to `~/.zshrc.agentctl.bak`).
Re-run it after editing `bin/anchor.sh` — launchd uses the installed copy.

## Layout

- `agentctl/` — the package (stdlib only)
  - `cli.py` — `agentctl <command>` dispatcher; every command is a module with `main(argv) -> int`
  - `status.py`, `renew.py`, `accounts.py`, `copy.py`, `log.py`, `ui.py`, `anchor.py` — one module per subcommand
  - `providers/claude.py`, `providers/codex.py` — everything provider-specific, behind the contract in `providers/__init__.py`
- `bin/agentctl` — the dispatcher symlinked into `/opt/homebrew/bin`
- `bin/anchor.sh` — the morning anchor script (zsh); `~/.agentctl/anchor.sh` is
  an installed **regular copy** (launchd may not follow a symlink into
  Documents), and the plist lives in `~/Library/LaunchAgents/`
- `bin/claude-migrate-session.sh` — the proven session-migration script `agentctl copy` drives
- `tests/` — `python3 -m unittest discover -s tests -v`
- `docs/` — one reference doc per subcommand, plus the provider contract and the legacy prototype doc
- Accounts are **auto-discovered** everywhere (see below) — no lists to edit
- Runtime state lives outside the repo, in `~/.agentctl/`:

| Path | Role |
|------|------|
| `~/.agentctl/renew/registry.json` | pending renew tasks |
| `~/.agentctl/renew/renew.log` | renew activity |
| `~/.agentctl/renew/daemon.pid` | renew daemon liveness |
| `~/.agentctl/anchor.log` (+ `.1`) | morning anchor runs |
| `~/.agentctl/anchor.sh` | the installed copy launchd runs |
| `~/.agentctl/ui.db` | UI audit log (SQLite) |

## Providers

Everything provider-specific lives in `agentctl/providers/` behind one duck-typed
contract (`discover`, `usage`, `session_reset_at`, `live_sessions`,
`find_live_sessions`, `send_message`, `anchor_ping`, `login_hint`,
`resume_hint`). Full details in [docs/providers.md](docs/providers.md).

| | **Claude Code** | **Codex** |
|---|---|---|
| **Accounts** | `~/.claude` (default) + every `~/.claude-<alias>` with a `.claude.json`; selected with `CLAUDE_CONFIG_DIR` — the default account must run **without** it | `~/.codex` (label `codex`) + every `~/.codex-<alias>` with an `auth.json`; selected with `CODEX_HOME` |
| **Limits read** | `GET api.anthropic.com/api/oauth/usage`, bearer token from the macOS keychain; 429 falls back to the interactive `/status` Usage tab | `GET chatgpt.com/backend-api/wham/usage` with the `auth.json` access token + `ChatGPT-Account-Id` header; offline fallback = the `rate_limits` block in the newest rollout file |
| **Messaging a session** | headless `claude -p --model haiku --allowedTools ListAgents,SendMessage` helper on the same account | `codex queue --thread <name> --message ...` (built in; the session must be open in a terminal) |
| **Anchoring a window** | throwaway `claude -p --model haiku "Hello, only answer hello"` | `codex exec --ephemeral --skip-git-repo-check -s read-only` |

## Key discoveries this is built on

**Claude Code** (verified live 2026-09-06 · 2026-09-12):

- The /status Usage tab is fed by `GET api.anthropic.com/api/oauth/usage` —
  callable read-only with the CLI's own keychain token; returns exact
  `resets_at` timestamps per limit. No TUI scraping, no cost, no anchoring.
- Keychain service per account: `Claude Code-credentials` (default) or
  `Claude Code-credentials-<first 8 hex of sha256(config-dir path)>`.
- Live sessions are enumerable in `<config>/sessions/<pid>.json` (name from
  `/rename`, pid, cwd, status) and can be messaged by a headless
  `claude -p --model haiku --allowedTools "ListAgents,SendMessage"` helper on
  the same account; the target session wakes and processes the message in its
  own terminal.
- The default account must be invoked **without** `CLAUDE_CONFIG_DIR`, and
  expired tokens recover through a temporary interactive `/status` terminal.
  `claude auth status --json` only reports authentication; it does not renew tokens.
- 429 is never retried — hammering the rate limit extends it. The interactive
  Usage tab keeps working during a 429 (separate data path), so that is the
  fallback; it carries human "Resets …" text, not exact timestamps.

**Codex** (verified live 2026-09-14 against codex-cli 0.154.0):

- Login state is a plain file, not the keychain: `~/.codex/auth.json` with
  `tokens.access_token` / `account_id`. One account per config dir; extra
  accounts live in `~/.codex-<alias>` and run with `CODEX_HOME` set.
- Usage: `GET chatgpt.com/backend-api/wham/usage` with that bearer token plus a
  `ChatGPT-Account-Id` header. `rate_limit.primary_window` is the 5 h session
  window (`limit_window_seconds` 18000, `used_percent`, `reset_at` epoch),
  `secondary_window` the weekly one. Read-only; anchors nothing.
- The same numbers are written into every session's rollout file as
  `token_count` events (`payload.info.rate_limits`) — the offline fallback when
  the access token is stale.
- Sessions are `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`; their
  names (from `/rename` or the auto title) and cwd live in
  `~/.codex/state_5.sqlite` table `threads`. A session is **live** when a codex
  process holds its rollout file open (`lsof`).
- `codex queue --thread <name> --message TEXT` delivers to a live session — no
  helper model needed. When nothing is live it prints "Error: No active session
  found matching '<name>'" **and exits 0**, so the text must be checked, not the
  exit code.
- Cheapest window-opening request: `codex exec --ephemeral
  --skip-git-repo-check -s read-only -C ~ "Hello, only answer hello"`.
- Limit banner text: "You've hit your usage limit … try again at 3:28 AM."

## Tests

```bash
python3 -m unittest discover -s tests -v
```
