# claude-sessions

Tooling to squeeze the most out of Claude Code's 5-hour usage windows across
the three accounts on this Mac (`default`, `benjr`, `bggai`).

## The system

Three pieces, one strategy — anchor windows early, never waste a renewal:

| Piece | What it does | Doc |
|-------|--------------|-----|
| **`claude-status`** | All three accounts' limits (session / weekly / weekly-Fable) in one command, read-only, zero cost, no window anchored. | [docs/claude-status.md](docs/claude-status.md) |
| **morning limit-reset job** | launchd pings each account with a haiku "hello" at 7:30 Mon–Fri / 10:30 Sat–Sun, anchoring the day's windows. | [docs/limit-reset-cron.md](docs/limit-reset-cron.md) |
| **`claude-renew`** | When a session was **cut mid-task** overnight: auto-continues it at reset + 5 min (e.g. 5:45 AM) and sends a follow-up anchor 5 h later (10:50) — the earliest possible restart of your day. | [docs/claude-renew.md](docs/claude-renew.md) |

**When to use which:** if nothing is in progress, do nothing — the morning job
anchors at 7:30 and you start planning at 9–10 with a fresh window coming.
Register with `claude-renew` **only when work was interrupted**: then the 5:40
renewal is worth catching at 5:45 rather than waiting for 7:30.

## Quick reference

```bash
claude-status                       # limits for all accounts
claude-status bggai --json          # one account, machine-readable

claude-renew add bggai radal-v2 --path ~/Documents/repos/radal/radal-app
claude-renew list                   # pending tasks + daemon state
claude-renew remove radal-v2        # cancel (or: remove all)
claude-renew log -f                 # watch it work
```

## Install

```bash
./install.sh    # symlinks bin/* into /opt/homebrew/bin
```

## Layout

- `bin/claude-status` — usage limits CLI (python3, stdlib only)
- `bin/claude-renew` — renewal scheduler CLI + its 24 h self-expiring daemon
- `docs/` — one reference doc per piece, plus the legacy prototype doc
- Runtime state lives outside the repo: `~/.claude/claude-renew/`
  (registry, log, daemon pid); the launchd job in `~/Library/LaunchAgents/`.

## Key discoveries this is built on (all verified live 2026-09-06)

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
  `claude auth status --json` safely refreshes an expired token without
  touching `.claude.json`.
