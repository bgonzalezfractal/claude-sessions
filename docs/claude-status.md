# claude-status — Reference

One command that prints the **usage limits of all three Claude accounts** —
`default`, `benjr`, `bggai` — without opening a single interactive session.
Replaces the manual ritual of `claude` → `/status` → tab·tab (Usage) three times.
Created 2026-09-06.

```
$ claude-status

default  ben@nirvana-ai.com
  session (5h)   ████░░░░░░░░░░░░░░░░  20%  resets 18:19
  weekly         █████░░░░░░░░░░░░░░░  25%  resets Fri 01:59
  weekly Fable   ████████░░░░░░░░░░░░  42%  resets Fri 01:59

benjr    benjasanchez0@gmail.com
  …
```

Per account it shows the same three limits as the /status Usage tab — **session
(5 h)**, **weekly**, **weekly Fable** — as a color bar (green < 60 % ≤ yellow
< 85 % ≤ red), the percentage, and the reset time in local time (day prefix when
not today). A maxed-out limit that is actively blocking gets a `◀ LIMITED` marker,
and enabled extra usage is shown when present.

## How it works — no TUI, no cost

The /status screen is interactive-only, but the data behind it isn't: the CLI
renders the Usage tab from **`GET https://api.anthropic.com/api/oauth/usage`**
(headers: `Authorization: Bearer <oauth token>`, `anthropic-beta: oauth-2025-04-20`,
`anthropic-version: 2023-06-01`). The script calls that endpoint directly, once per
account. It is **read-only**: costs nothing, consumes no tokens, and — unlike any
`claude -p` ping — does **not** anchor or start a 5-hour usage window. Safe to run
at any hour — but not in a tight loop: the endpoint rate-limits frequent
checks with 429 (a 429 is reported with its retry-after and deliberately NOT
retried; hammering a rate limit extends it).

Tokens come from the CLI's own macOS keychain entries:

| Account | Keychain service |
|---------|------------------|
| `default` | `Claude Code-credentials` |
| any custom config dir | `Claude Code-credentials-<first 8 hex of sha256(config-dir path)>` — benjr → `-da3e029b`, bggai → `-c92558f3` |

Account emails are read from each config dir's `.claude.json` (`oauthAccount`).

## Files / install

| Path | Role |
|------|------|
| `~/.claude/claude-status.py` | the source (single-file python3, stdlib only) |
| `/opt/homebrew/bin/claude-status` | symlink to the source → on PATH everywhere |
| `~/.zshrc` (`alias claude-status=…`) | redundant alias, kept harmlessly |

Edit the source and the command updates instantly (it's a symlink). To add an
account, extend the `ACCOUNTS` list at the top: `(label, config_dir, suffix_dir)`
where `suffix_dir` is `None` for the default account (plain keychain service) and
the config-dir path otherwise.

## Resilience (rewritten 2026-09-06)

The first version made one bare `urlopen` per account and reported whatever came
back, so a single bad moment looked like two dead accounts:

```
default ben@nirvana-ai.com
  ✗ <urlopen error [Errno 54] Connection reset by peer>   ← one TLS flake
bggai bengonzalez1995@gmail.com
  ✗ HTTP 401 — token expired? open claude once           ← token had just lapsed
```

Neither account was actually broken. Both are now handled in-process:

- **Transient faults are retried** — connection reset, timeout, 408/425/429 and
  5xx, three attempts with 0.4 s / 1.2 s backoff. A run that needed retries says
  so: `↺ retried 1×`. Non-transient errors (401/403) are *not* retried.
- **An expired token heals itself.** `expiresAt` in the keychain payload is
  checked first, so the call that would 401 is skipped; either way the script
  runs `claude auth status --json` on that account, re-reads the keychain and
  retries once, then reports `↺ refreshed token`. If it still fails you get
  `✗ access token expired 2h04m ago; refresh did not renew it — run
  `claude auth login` on this account` instead of a bare HTTP code.
- **Accounts are fetched in parallel** (~0.6 s for all three), so one account's
  retries no longer delay the rest.
- **Exit code** is 1 if any account failed, 2 for a bad argument — usable in
  scripts. `--json` dumps the raw endpoint payload per account.
  `claude-status bggai` limits the run to named accounts.

`claude auth status --json` is safe to use as the refresh trigger: it is
non-interactive, costs nothing, does not anchor a usage window, and — measured,
sha *and* mtime unchanged — **does not write `.claude.json`**. The usual "never
run a second Claude process against a config dir that has a live session, they
clobber `.claude.json`" rule therefore does not apply to this one command.

## TUI fallback (when the API rate-limits)

If the usage endpoint answers 429, claude-status automatically drives a
headless interactive `claude` through `/status` → → (Usage tab) in a pty and
parses the rendered panel — the TUI has its own data path and keeps working
while the endpoint is throttled (verified live 2026-09-07). Rows are marked
`↺ via interactive /status`. Costs ~15 s per account, spawns (and SIGKILLs) a
scratch session, and shows only what the TUI shows: session + weekly bars with
humanized reset times, no weekly-Fable split.

## Troubleshooting

- **`✗ … refresh did not renew it`** → the automatic refresh could not renew that
  account; run `claude auth login` on it (`CLAUDE_CONFIG_DIR=~/.claude-bggai claude
  auth login`, or plain `claude auth login` for the default account).
- **`✗ no keychain entry`** → the account was never logged in on this Mac, or the
  suffix changed (recompute: `sha256` of the exact config-dir path, first 8 hex).
- **Reset times look off** → they're converted to local time; remember Chile DST
  shifts (the API returns UTC).

## Why this matters beyond status

The endpoint returns exact reset timestamps (`limits[].resets_at`) and an
`is_active` flag per limit. Future automation — e.g. the session-restarter
(`claude-session-restarter.md`) — can query this instead of parsing the
"You've hit your session limit · resets 5:40am" banner, and schedule against the
precise UTC timestamp.
