# agentctl ui — Reference

A localhost dashboard for the whole toolkit — one dark page instead of six CLIs.

```bash
agentctl ui                       # http://127.0.0.1:8788 (opens the browser)
agentctl ui --port 9000 --no-open
```

## Views (menu navigation)

- **Home** — at-a-glance: per-account session bar + next reset, live count,
  pending renews, daemon state. Accounts from every provider share the list,
  tagged with their provider.
- **Usage** — every account with its session (cyan) / weekly /
  weekly-scoped bars, percentages, reset times. **Refresh is manual** (the
  "↻ refresh usage" button): the usage endpoints rate-limit frequent checks
  (429), so nothing polls them — opening the page and every action refresh only
  the cheap local state (accounts, sessions, queue, events), and the last
  fetched usage stays cached with an "as of" timestamp.
- **Live sessions** — per account with cwd; a one-click **renew** button
  schedules the default auto-continue for that session.
- **Renew queue** — pending `agentctl renew` tasks with fire times, done marks,
  daemon state, cancel buttons; the schedule form uses a real
  **datetime picker** (a "detect" button fills it from the usage API), a
  **delay selector** (immediately / +5…+30 min in steps of 5), a follow-up
  selector (5h05m / off / at a time), a **path** field auto-filled from the
  selected session's cwd, and a **query** field that becomes `--query` (the
  standard "Your usage limit has reset …" preamble is kept — see
  [renew.md](renew.md)). Quick-renew buttons pass the session's path too.
- **Copy** — from/to account pickers, session dropdown (newest first, LIVE
  flagged), **origin and destination path fields** (destination editable to
  re-home the copy), dry-run and real copy; output lands in a toast.
  Claude Code accounts only.
- **New account** — scaffolds an alias via `agentctl accounts add` (login stays
  manual, instructions in the toast).
- **Activity** — two sections: the merged scheduler log (`agentctl log`: anchor
  pings, continues, follow-up anchors, retries) and the SQLite-backed UI action
  history.

## Architecture

Single-file python3 **stdlib-only** server (`ThreadingHTTPServer`), bound to
**127.0.0.1 only**. It performs no logic of its own: every action goes through
the sibling subcommand (`agentctl status --json`, `agentctl renew add/remove`,
`agentctl copy`, `agentctl accounts`), so behavior lives in exactly one place.
Reads the renew registry/daemon-pid directly for display.

**SQLite** at `~/.agentctl/ui.db`, table
`events(id, ts, kind, ok, detail, output)` — every POST is recorded with its
full output (truncated to 4 KB), giving a persistent audit trail of renewals
scheduled, copies run, and accounts created:

```bash
sqlite3 ~/.agentctl/ui.db "SELECT ts,kind,ok,detail FROM events ORDER BY id DESC LIMIT 10"
```

## API (for scripting against it)

| Endpoint | Payload |
|----------|---------|
| `GET /api/overview[?usage=1]` | accounts, sessions, queue, events; `usage=1` also calls the usage endpoints |
| `GET /api/reset?account=X` | detected session reset time for the renew form |
| `POST /api/renew_add` | `{account, session, path?, reset?, delay? (0-30/5), follow_up?, query?}` |
| `POST /api/renew_remove` | `{session, account?}` |
| `POST /api/copy` | `{src, dst, session, src_path?, dst_path?, dry_run?}` |
| `POST /api/account_add` | `{alias}` |

Responses: `{ok, output}`. The server dies with the terminal that started it —
run it under launchd or tmux if you want it always-on.
