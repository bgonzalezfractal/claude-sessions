# claude-sessions-ui — Reference

A localhost dashboard for the whole toolkit — one dark page instead of four CLIs.

```bash
claude-sessions-ui                # http://127.0.0.1:8788 (opens the browser)
claude-sessions-ui --port 9000 --no-open
```

## Panels

- **Accounts & usage** — every account with its session (cyan) / weekly /
  weekly-Fable bars, percentages, reset times; refreshed every 60 s.
- **Live sessions** — per account with cwd; a one-click **renew** button
  schedules the default auto-continue for that session.
- **Renew queue** — pending `claude-renew` tasks with fire times, done marks,
  daemon state, cancel buttons; a form for personalized scheduling
  (reset auto/explicit, follow-up `+5h5m` / clock / off).
- **Copy session** — from/to account pickers, session dropdown (newest first,
  LIVE flagged), dry-run and real copy; output lands in a toast.
- **New account** — scaffolds an alias via `claude-account add` (login stays
  manual, instructions in the toast).
- **Activity** — the SQLite-backed action history.

## Architecture

Single-file python3 **stdlib-only** server (`ThreadingHTTPServer`), bound to
**127.0.0.1 only**. It performs no logic of its own: every action shells out to
the sibling CLI (`claude-status --json`, `claude-renew add/remove`,
`claude-copy-session`, `claude-account`), so behavior lives in exactly one
place. Reads the renew registry/daemon-pid directly for display.

**SQLite** at `~/.claude/claude-sessions-ui.db`, table
`events(id, ts, kind, ok, detail, output)` — every POST is recorded with its
full CLI output (truncated to 4 KB), giving a persistent audit trail of
renewals scheduled, copies run, and accounts created:

```bash
sqlite3 ~/.claude/claude-sessions-ui.db "SELECT ts,kind,ok,detail FROM events ORDER BY id DESC LIMIT 10"
```

## API (for scripting against it)

| Endpoint | Payload |
|----------|---------|
| `GET /api/overview` | — (accounts, usage, sessions, renew queue, events) |
| `POST /api/renew_add` | `{account, session, path?, reset?, delay?, follow_up?}` |
| `POST /api/renew_remove` | `{session, account?}` |
| `POST /api/copy` | `{src, dst, session, dry_run?}` |
| `POST /api/account_add` | `{alias}` |

Responses: `{ok, output}`. The server dies with the terminal that started it —
run it under launchd or tmux if you want it always-on.
