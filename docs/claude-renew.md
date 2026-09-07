# claude-renew — Reference

Resume an **interrupted** session the moment its 5-hour limit renews, no matter
the hour, then anchor the next window at the earliest useful moment of your day.
Supersedes the `session-restarter.py` prototype (see
[session-restarter-legacy.md](session-restarter-legacy.md)). Built 2026-09-07.

## The scenario it solves

- A session (say `radal-v2` on `bggai`, living in `~/Documents/repos/radal/radal-app`)
  runs out at 2–3 AM **mid-task**; the limit renews at 5:40 AM. You go to bed.
- The morning anchor job fires at 7:30, but you start from scratch at 9–10 AM —
  that's fine for *fresh* days: a 2–3 h old window means a fast renewal right
  when you go all-in.
- But here work is already pending, so waiting until 7:30 wastes the 5:40
  renewal. Register the session and it continues at **5:45** (reset + 5 min);
  a follow-up "hi" at **10:50** (continue + 5 h 05 m) anchors the next window so
  the earliest reset is already ticking when your day starts.

**Rule of thumb:** nothing interrupted → don't register, let the 7:30/10:30
anchor do its thing. Something interrupted → `claude-renew add` before bed.

## Commands

```bash
# Register (reset time auto-detected from the usage API — exact, read-only):
claude-renew add bggai radal-v2 --path ~/Documents/repos/radal/radal-app

# Personalize everything:
claude-renew add default clay \
    --reset 5:40am \                 # explicit instead of auto
    --delay 5 \                      # minutes after reset for the continue
    --follow-up +5h5m \              # or a clock time (10:50am), or 'off'
    --message "continue the migration task" \
    --follow-up-message "hi" \
    --ttl 24

claude-renew list                    # pending tasks + daemon state
claude-renew remove radal-v2         # cancel one (add --account to disambiguate)
claude-renew remove all              # cancel everything
claude-renew start                   # revive daemon (e.g. after a reboot)
claude-renew log -f                  # follow the activity log
```

Accounts: `default` | `benjr` | `bggai`.

## Semantics

- **`--reset auto`** (default) reads the account's OAuth token from the keychain
  and GETs `api/oauth/usage`; the session limit's `resets_at` is the exact
  renewal moment (better than parsing the "resets 5:40am" banner). Read-only —
  costs nothing, anchors nothing. Expired tokens are auto-refreshed via
  `claude auth status --json`. If the reset is already in the past, `add`
  refuses — the account is usable now.
- **`--path`** pins the session to a working directory: registration and firing
  match the live session by **name and cwd**. Omitted → taken from the live
  session at registration.
- The **session must be live** (terminal open) — this tool messages running
  sessions; it does not resume closed ones. `add` refuses if it isn't, and the
  daemon re-checks at fire time (10-min retries, max 8).
- The **continue** message tells the session its limit reset and to resume the
  pending task; it runs under the session's own model and permission mode.
- The **follow-up** (default reset + 5 h 10 m) is a bare "hi" that wakes the
  session again purely to anchor the next window. `--follow-up off` if you'll
  be awake by then anyway.
- **Daemon:** plain detached process, ticks every 30 s, retries when the account
  is still limited or the session isn't visible, exits when nothing is pending.
  Tasks expire after `--ttl` (24 h default). Survives closing the terminal, not
  a reboot (`claude-renew start` revives it). Mac asleep at fire time → fires on
  next wake.

## Files

| Path | Role |
|------|------|
| `bin/claude-renew` (repo) → `/opt/homebrew/bin/claude-renew` | the CLI |
| `~/.claude/claude-renew/registry.json` | pending tasks |
| `~/.claude/claude-renew/renew.log` | timestamped activity |
| `~/.claude/claude-renew/daemon.pid` | daemon liveness |

## How delivery works (validated live 2026-09-06)

At fire time a headless helper runs **on the target session's account**:
`claude -p --model haiku --allowedTools "ListAgents,SendMessage"` (prompt via
stdin — `--allowedTools` is variadic and would swallow a trailing argument).
It finds the session by name via ListAgents and delivers the message; the
session wakes and processes it in its own terminal. Peer visibility is per
config dir, hence per-account helpers; the default account runs **without**
`CLAUDE_CONFIG_DIR` (keychain gotcha — see
[limit-reset-cron.md](limit-reset-cron.md)). Each helper is a haiku call on
that account (pennies) which also anchors its window — at these fire times,
exactly the intent.

## Testing (fast-forward trick)

1. `claude-renew add benjr <live-session> --reset 11:00pm`
2. Edit `~/.claude/claude-renew/registry.json`: set the actions' `"at"` to a
   minute from now and swap `"message"` for `"TEST — reply with just: ok"`.
   The daemon re-reads the registry every 30 s; no restart needed.
3. `claude-renew log -f` — expect `FIRED continue`, `FIRED follow-up`, then
   `nothing pending — daemon exiting`, and the target session replies in its
   own terminal.

## Caveats

- Two live sessions with the same name on one account: the helper addresses by
  name and may hit either — the daemon logs a warning. Rename one (`/rename`).
- The continue runs under the session's own permission mode: anything it would
  normally ask you about, it still asks (and idles) until you return.
- Registering is per (account, session, path) — re-adding replaces the pending
  task for that trio.
