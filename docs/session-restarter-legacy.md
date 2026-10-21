# Claude Session Restarter — Reference (LEGACY)

> **Superseded 2026-09-07 by [`agentctl renew`](renew.md)** — same mechanism,
> plus exact reset detection via the usage API, `--path` pinning, personalizable
> timings/messages, and proper remove/list/log commands. The prototype below
> (`~/.claude/session-restarter.py`) still works but is no longer maintained.

A **24-hour, self-expiring** scheduler that revives named Claude sessions the moment
their account's 5-hour limit resets — no matter the hour. Created 2026-09-06.

**The play:** a session (e.g. `radal-v2`) runs out of limits at night with reset at,
say, 5:40 AM. You register it before going to bed. Then:

| When | What | Message sent to the session |
|------|------|------------------------------|
| reset + **5 min** (5:45) | **continue** | "Your usage limit has reset (scheduled auto-continue). Please continue with the pending task exactly where you left off." |
| reset + **5 h 10 min** (10:50) | **hi** | "hi (scheduled window anchor — just reply 'hi', nothing else)" |

So the work window runs 5:45→10:45 and the next window is anchored at 10:50 —
back-to-back usage with no dead time.

## How it works (validated live 2026-09-06)

- **Sessions are addressed by their name** (the `/rename` alias, e.g. `radal-v2`),
  resolved via `~/.claude*/sessions/<pid>.json` — only **live** sessions (terminal
  still open) can be messaged. Keep the session's terminal open.
- At fire time a **headless haiku helper** runs on the *same account* as the target
  (`claude -p --model haiku --allowedTools "ListAgents,SendMessage"`) and delivers the
  message via inter-session messaging. The target session wakes and processes it in
  its own terminal, on its own model and permission mode. Peer visibility is per
  config dir, hence the per-account helper.
- The **default account helper runs without `CLAUDE_CONFIG_DIR`** (keychain gotcha —
  see [anchor.md](anchor.md)).
- If the account is **still limited** at fire time, the helper fails with the limit
  message; the daemon retries every 10 min (max 8 tries). Same if the session isn't
  found live.
- **Reset-time auto-detection:** a limited account answers `claude -p` instantly with
  `You've hit your session limit · resets 5:40am (America/Santiago)` at zero cost —
  `register` probes and parses that when you omit `--reset`.
- **24 h lifetime:** each registration expires 24 h after creation; the daemon (plain
  detached python process, no launchd) exits when nothing is pending. Nothing
  permanent is installed.

## Files

| Path | Role |
|------|------|
| `~/.claude/session-restarter.py` | the tool (register / list / cancel / daemon) |
| `~/.claude/session-restarter/registry.json` | pending registrations |
| `~/.claude/session-restarter/restarter.log` | timestamped history |
| `~/.claude/session-restarter/daemon.pid` | daemon liveness |

## Usage

```bash
# Register (account: default | benjr | bggai). Reset time auto-detected by probing:
python3 ~/.claude/session-restarter.py register bggai radal-v2

# Or state the reset time you saw in the limit banner:
python3 ~/.claude/session-restarter.py register bggai radal-v2 --reset 5:40am

# Inspect / cancel
python3 ~/.claude/session-restarter.py list
python3 ~/.claude/session-restarter.py cancel radal-v2
tail ~/.claude/session-restarter/restarter.log
```

The daemon auto-spawns on `register` and exits on its own. It survives closing the
registering terminal, but **not** a reboot; if the Mac sleeps past a fire time, it
fires on the next wake (keep the Mac asleep, not shut down — and `pmset` can schedule
a wake, see [anchor.md](anchor.md)).

## Testing it later (fast-forward trick, used for the 2026-09-06 validation)

1. Register any **live** session with a dummy reset time:
   `python3 ~/.claude/session-restarter.py register benjr <session-name> --reset 11:00pm`
2. Edit `~/.claude/session-restarter/registry.json`: set both actions' `"at"` to a
   minute or two from now, and (to avoid triggering real work) replace `"message"`
   with something like `"TEST — reply with just: ok"`.
3. Watch `tail -f ~/.claude/session-restarter/restarter.log` — expect a
   `FIRED continue`, a `FIRED hi`, then `nothing pending — daemon exiting`, and the
   target session replies in its own terminal. The daemon picks up registry edits
   live (it re-reads every 30 s); no restart needed.

## Caveats

- The session must be **live** (open terminal). Registration refuses otherwise.
- The "continue" runs under the target session's own permission mode — whatever the
  session would ask a human for, it still asks (and idles) until you return.
- Registering a not-limited account: the probe itself is a tiny haiku call that
  anchors that account's window — pass `--reset` explicitly to skip probing.
- Each helper run is a haiku `-p` call on the target account (pennies), and it also
  anchors that account's window — which at these fire times is exactly the intent.
