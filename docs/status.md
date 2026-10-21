# agentctl status — Reference

One command that prints the **usage limits of every account, on every provider** —
Claude Code (`default`, `benjr`, `bggai`) and Codex (`codex`) — without opening a
single interactive session. Replaces the manual ritual of `claude` → `/status` →
tab·tab (Usage), once per account. Created 2026-09-06, multi-provider 2026-09-14.

```
$ agentctl status

default  ben@nirvana-ai.com                                   claude
  session (5h)   ████░░░░░░░░░░░░░░░░  20%  resets 18:19
  weekly         █████░░░░░░░░░░░░░░░  25%  resets Fri 01:59
  weekly Fable   ████████░░░░░░░░░░░░  42%  resets Fri 01:59

benjr    benjasanchez0@gmail.com                              claude
  …

codex    bengonzalez1995@gmail.com                            codex
  session (5h)   ██░░░░░░░░░░░░░░░░░░  11%  resets 21:05
  weekly         ███████░░░░░░░░░░░░░  36%  resets Mon 04:00
```

```bash
agentctl status                 # every account, both providers
agentctl status bggai           # one account, by label
agentctl status codex --json    # machine-readable (this is what the UI eats)
agentctl status --interactive   # Claude only: read the real /status Usage tab
```

Per account it shows that provider's limits — Claude: **session (5 h)**,
**weekly**, **weekly-scoped** (e.g. Fable); Codex: **session (5 h)** and
**weekly** — as a color bar (green < 60 % ≤ yellow < 85 % ≤ red), the percentage,
and the reset time in local time (day prefix when not today). A maxed-out limit
that is actively blocking gets a `◀ LIMITED` marker, and enabled extra usage /
credits are shown when present.

## How it works — no TUI, no cost

Neither provider's status screen has to be opened: both render from a plain HTTP
endpoint that `agentctl status` calls directly, once per account.

| Provider | Endpoint | Token |
|----------|----------|-------|
| claude | `GET https://api.anthropic.com/api/oauth/usage` (`Authorization: Bearer …`, `anthropic-beta: oauth-2025-04-20`, `anthropic-version: 2023-06-01`) | macOS keychain |
| codex | `GET https://chatgpt.com/backend-api/wham/usage` (`Authorization: Bearer …`, `ChatGPT-Account-Id: …`) | `~/.codex/auth.json` → `tokens.access_token` |

Both are **read-only**: they cost nothing, consume no tokens, and — unlike any
`claude -p` / `codex exec` ping — do **not** anchor or start a 5-hour usage
window. Safe to run at any hour — but not in a tight loop: the Anthropic
endpoint rate-limits frequent checks with 429 (a 429 is reported with its
retry-after and deliberately **not** retried; hammering a rate limit extends it).

Where the tokens live:

| Account | Credential |
|---------|------------|
| claude `default` | keychain service `Claude Code-credentials` |
| any other claude config dir | `Claude Code-credentials-<first 8 hex of sha256(config-dir path)>` — benjr → `-da3e029b`, bggai → `-c92558f3` |
| codex | `<config dir>/auth.json`, key `tokens.access_token` (+ `tokens.account_id` for the header) |

Account emails come from each claude config dir's `.claude.json` (`oauthAccount`)
and from the Codex usage response itself.

## Files / install

| Path | Role |
|------|------|
| `agentctl/status.py` | the command |
| `agentctl/providers/claude.py`, `codex.py` | all endpoint/credential specifics |
| `/opt/homebrew/bin/agentctl` | symlink to `bin/agentctl` → on PATH everywhere |

Edit the source and the command updates instantly (it's a symlink into the repo).
There is **no account list to extend** — accounts are auto-discovered
(see [providers.md](providers.md)); logging in is enough.

## Resilience

The first version made one bare `urlopen` per account and reported whatever came
back, so a single bad moment looked like two dead accounts:

```
default ben@nirvana-ai.com
  ✗ <urlopen error [Errno 54] Connection reset by peer>   ← one TLS flake
bggai bengonzalez1995@gmail.com
  ✗ HTTP 401 — token expired? open claude once           ← token had just lapsed
```

Neither account was actually broken. Both are now handled in-process:

- **Transient faults are retried** — connection reset, timeout, 408/425 and 5xx,
  three attempts with 0.4 s / 1.2 s / 3.0 s backoff. A run that needed retries
  says so: `↺ retried 1×`. Non-transient errors (401/403) and **429** are not
  retried.
- **Expired-token recovery (claude):** open a temporary interactive Claude
  terminal in an already trusted folder, navigate `/status` → right → right, then
  re-read the keychain and verify renewal before fetching usage. No model prompt
  is sent. `claude auth status --json` is a diagnostic, not a refresh trigger.
  Recovery was verified on benjr on 2026-09-12; revoked refresh tokens still need
  login.
- **Stale-token fallback (codex):** when the usage API fails, the newest session
  rollout file's `rate_limits` block is shown instead, labelled with the moment
  it was captured (`source: "rollout"`). Refresh the token by opening `codex`
  once.
- Text and `--json` share the same recovery, retry and fallback code. The
  dashboard and `agentctl renew`'s reset detection use that same path.
- Accounts are fetched in parallel; scratch interactive terminals are serialized.
- Exit status is 1 if any account failed, 2 for argument errors.

## Interactive fallback (Claude only)

When the API returns 429, status reads the Usage tab through a sized PTY.
It skips trust prompts rather than accepting them, disables customizations,
and always terminates and reaps the scratch process. Rows may include session,
weekly and scoped weekly limits, depending on what Claude displays. Reset text
is humanized, so `agentctl renew` requires `--reset HH:MM` if the exact API
timestamp is unavailable. Interactive startup uses the account's normal
configuration; it does not guarantee that configuration files remain unchanged.
Codex has no TUI path — its offline fallback is the rollout file above.

## Troubleshooting

- **`✗ … refresh did not renew it`** → the automatic refresh could not renew that
  account; run `claude-bggai auth login` (or the corresponding account alias), or plain
  `claude auth login` for the default account.
- **`✗ no keychain entry`** → the claude account was never logged in on this Mac, or the
  suffix changed (recompute: `sha256` of the exact config-dir path, first 8 hex).
- **`✗ not logged in — run \`codex login\``** → `~/.codex/auth.json` is missing or
  unreadable. For an extra account: `CODEX_HOME=~/.codex-<alias> codex login`.
- **`HTTP 401 (token expired — open \`codex\` once to refresh it)`** → exactly that;
  the Codex CLI refreshes `auth.json` on start.
- **Reset times look off** → they're converted to local time; remember Chile DST
  shifts (both APIs return UTC).

## Why this matters beyond status

Both endpoints return exact reset timestamps and an active/blocking flag per
limit. That is what [`agentctl renew`](renew.md) schedules against, instead of
parsing the "You've hit your session limit · resets 5:40am" banner.
