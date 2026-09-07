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
| **`claude-copy-session`** | Migrate a session between accounts by name — front-end over the proven migrate script (transcript, sidecar, memory, tasks, config entry). | [docs/claude-copy-session.md](docs/claude-copy-session.md) |
| **`claude-account`** | List account aliases (login state, keychain, live sessions) or scaffold a new one; new accounts auto-join every tool after first login. | [docs/claude-account.md](docs/claude-account.md) |
| **`claude-log`** | One chronological stream of everything the toolkit did: morning reset pings, scheduled continues, follow-up anchors, retries (`-n`, `--kind`, `--json`, `-f`). Also shown in the app's Activity view. | — |
| **`claude-sessions-ui`** | Localhost dashboard (stdlib server + SQLite audit log at 127.0.0.1:8788) wrapping all of the above. | [docs/claude-sessions-ui.md](docs/claude-sessions-ui.md) |

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

claude-copy-session bggai default radal-v2      # migrate by title (--dry-run first)
claude-copy-session --list bggai                # sessions on an account

claude-account list                 # aliases + login/keychain/live state
claude-account add work             # scaffold ~/.claude-work + zshrc alias

claude-sessions-ui                  # dashboard at http://127.0.0.1:8788

claude-log                          # merged log: reset pings + continues + follow-ups
claude-log --kind continue,follow-up -f   # filter / live-tail
```

## Install

```bash
./install.sh    # symlinks bin/* into /opt/homebrew/bin
```

## Layout

- `bin/claude-status` — usage limits CLI (python3, stdlib only)
- `bin/claude-renew` — renewal scheduler CLI + its 24 h self-expiring daemon
- `bin/claude-copy-session` + `bin/claude-migrate-session.sh` — session migration
- `bin/claude-account` — alias listing / scaffolding
- `bin/claude-sessions-ui` — localhost dashboard (stdlib http + SQLite)
- `bin/limit-reset.sh` — the morning anchor script (`~/.claude/limit-reset.sh`
  is a symlink here; the launchd plist stays in `~/Library/LaunchAgents/`)
- `docs/` — one reference doc per piece, plus the legacy prototype doc
- Accounts are **auto-discovered** everywhere: default + every `~/.claude-*`
  dir with a `.claude.json` (wrapper dirs like `.claude-deepinfra` excluded).
- Runtime state lives outside the repo: `~/.claude/claude-renew/`
  (registry, log, daemon pid) and `~/.claude/claude-sessions-ui.db`.

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
