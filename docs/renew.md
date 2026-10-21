# agentctl renew — Reference

Resume an **interrupted** session the moment its 5-hour limit renews, no matter
the hour, then anchor the next window at the earliest useful moment of your day.
Works on both providers: Claude Code sessions and Codex threads. Supersedes the
`session-restarter.py` prototype (see
[session-restarter-legacy.md](session-restarter-legacy.md)). Built 2026-09-07,
multi-provider 2026-09-14.

## The scenario it solves

- A session (say `radal-v2` on `bggai`, living in `~/Documents/repos/radal/radal-app`)
  runs out at 2–3 AM **mid-task**; the limit renews at 5:40 AM. You go to bed.
- The morning [anchor job](anchor.md) fires at 7:30, but you start from scratch
  at 9–10 AM — that's fine for *fresh* days: a 2–3 h old window means a fast
  renewal right when you go all-in.
- But here work is already pending, so waiting until 7:30 wastes the 5:40
  renewal. Register the session and it continues at **5:45** (reset + 5 min);
  a follow-up "hi" at **10:50** (continue + 5 h 05 m) anchors the next window so
  the earliest reset is already ticking when your day starts.

**Rule of thumb:** nothing interrupted → don't register, let the 7:30/10:30
anchor do its thing. Something interrupted → `agentctl renew add` before bed.

## Commands

```bash
# Easiest: bare `agentctl renew` opens the interactive wizard — numbered menus
# for account (across providers) and live session, every option validated with
# its default pre-filled (path defaults to the session's cwd; auto-detected
# reset shown before you accept it), summary + confirm at the end.
# `agentctl renew setup` is the same thing.
agentctl renew

# Non-interactive (reset time auto-detected from the usage API):
agentctl renew add bggai radal-v2 --path ~/Documents/repos/radal/radal-app

# Steer the continuation without rewriting the whole prompt:
agentctl renew add codex new-acp \
    --query "Continue, and consider the standardization style of ..."

# Personalize everything:
agentctl renew add default clay \
    --reset 5:40am \                 # or 17:40, or 2026-09-08T05:40 (real datetime,
                                     #  validated — past times rejected)
    --delay 10 \                     # 0-30 in steps of 5 (0 = immediately)
    --follow-up +5h5m \              # or a clock time (10:50am), or 'off'
    --message "continue the migration task" \
    --follow-up-message "hi" \
    --ttl 24

agentctl renew list                  # pending tasks + daemon state
agentctl renew detect bggai [--json] # just the detected reset time
agentctl renew remove radal-v2       # cancel one (add --account to disambiguate)
agentctl renew remove all            # cancel everything
agentctl renew start                 # revive daemon (e.g. after a reboot)
agentctl renew log -f                # follow the activity log
```

Accounts: any label from `agentctl accounts list` — `default` | `benjr` |
`bggai` (Claude Code) | `codex` (Codex).

## The continue message

What the session receives is always composed as:

```
Your usage limit has reset (scheduled auto-continue by agentctl). <tail>
```

| Flag | Effect |
|------|--------|
| *(nothing)* | `<tail>` = **"Continue with the pending task exactly where you left off."** |
| `--query "…"` | `<tail>` = your text. The preamble stays, so the session still learns *why* it is being woken. Use this to add context or redirect the continuation. |
| `--message "…"` | Overrides **everything** — preamble included. The session receives exactly your text and nothing else. |

`--query` and `--message` are mutually exclusive; `--message` wins if both are
somehow set. The follow-up anchor is unrelated to all of this — it is a separate
throwaway ping (`--follow-up-message` only matters for legacy setups that message
the session).

## Semantics

- **Reset time is a real datetime**: `5:40am`, `17:40`, or `YYYY-MM-DDTHH:MM`;
  wall times resolve to their next future occurrence, past datetimes are
  rejected with a clean error. The wizard pre-fills the **detected** reset as
  the default value. `agentctl renew detect <account> [--json]` prints it alone.
- **`--reset auto`** (default) asks the account's provider for its session
  limit's exact reset moment — Claude via the keychain token and
  `api/oauth/usage`, Codex via `auth.json` and the wham usage endpoint (better
  than parsing the "resets 5:40am" banner). Read-only — costs nothing, anchors
  nothing. Expired Claude tokens recover through the shared interactive
  `/status` path; a stale Codex token falls back to the newest rollout file's
  numbers. A rate-limited API requires an explicit `--reset` time. If the reset
  is already in the past, `add` refuses — the account is usable now.
- **`--path`** pins the session to a working directory: registration and firing
  match the live session by **name and cwd**. Omitted → taken from the live
  session at registration.
- The **session must be live** (terminal open) — this tool messages running
  sessions; it does not resume closed ones. `add` refuses if it isn't, and the
  daemon re-checks at fire time (10-min retries, max 8).
- The **continue** message runs under the session's own model and permission
  mode.
- The **follow-up** (default reset + 5 h 10 m) anchors the next window as a
  **separate throwaway ping** on the account — a `claude -p --model haiku` call
  for Claude, a `codex exec --ephemeral` for Codex. It never touches the working
  session, which stays on its own model (changed 2026-09-07; previously it
  messaged the session). `--follow-up off` if you'll be awake by then anyway.
- **Daemon:** plain detached process, ticks every 30 s, retries when the account
  is still limited or the session isn't visible, exits when nothing is pending.
  Tasks expire after `--ttl` (24 h default). Survives closing the terminal, not
  a reboot (`agentctl renew start` revives it). Mac asleep at fire time → fires
  on next wake.

## Files

| Path | Role |
|------|------|
| `agentctl/renew.py` (repo) → `/opt/homebrew/bin/agentctl` | the CLI + daemon |
| `~/.agentctl/renew/registry.json` | pending tasks |
| `~/.agentctl/renew/renew.log` | timestamped activity |
| `~/.agentctl/renew/daemon.pid` | daemon liveness |

(`./install.sh` copies a pre-rename `~/.claude/claude-renew/registry.json` +
`renew.log` into `~/.agentctl/renew/` on first run, leaving the originals alone.)

## How delivery works

### Claude Code (validated live 2026-09-06)

At fire time a headless helper runs **on the target session's account**:
`claude -p --model haiku --allowedTools "ListAgents,SendMessage"` (prompt via
stdin — `--allowedTools` is variadic and would swallow a trailing argument).
It finds the session by name via ListAgents and delivers the message; the
session wakes and processes it in its own terminal. Peer visibility is per
config dir, hence per-account helpers; the default account runs **without**
`CLAUDE_CONFIG_DIR` (keychain gotcha — see [anchor.md](anchor.md)). Each helper
is a haiku call on that account (pennies) which also anchors its window — at
these fire times, exactly the intent.

### Codex (validated live 2026-09-14)

`codex queue --thread <name> --message "<text>"` — built into the CLI, so there
is no helper model and no cost. `CODEX_HOME` is set for non-default accounts.
Two things to know:

- **The session must be open in a terminal.** `codex queue` delivers into a
  running thread; it cannot start one. When nothing is live it prints
  `Error: No active session found matching '<name>'` **and exits 0**, so the
  output text is what gets checked (the daemon treats this as `not-live` and
  retries).
- **`codex resume <name>` reopens it** (prefix with `CODEX_HOME=…` for an extra
  account) — that is what the CLI prints when you register a thread that has
  since been closed.

Threads are matched by their `/rename` name or their uuid; only `source = "cli"`
threads are addressable (subagent threads are filtered out).

## When the continue itself hits the limit

A delivered continue can still die: the session picks the message up, starts a
turn, and that turn immediately exhausts the freshly reset window (a large
transcript with subagents can burn a five-hour allowance in one turn — observed
live on 2026-09-14).

The daemon treats that as **delivered, not failed**. Re-sending would leave the
session holding the same instruction twice. Instead it closes the action, asks
the provider for the account's new reset time, and arms a fresh continue at
`reset + delay` with the same message. At most `MAX_REARMS` (3) re-arms happen
before it gives up and says so in the log:

```
FIRED continue -> codex/new-acp: submitted, but the turn hit the usage limit
re-armed continue 1/3 for codex/new-acp @ Mon 08:43 (turn hit the limit; 100% used, LIMITED)
```

The task's lifetime is extended automatically so it outlives the re-armed
action.

### Codex: a stuck session will not drain its queue

`codex queue` inserts the message and bumps a revision; the running terminal is
what actually submits it. A Codex session whose last turn **failed** stops
draining that queue and sits idle indefinitely. On 2026-09-14 a message queued
at 03:47 was still unread at 08:40, through a full window reset.

agentctl detects this: when the message is not consumed within the confirmation
window *and* the session has written nothing since before it was sent, the log
says `QUEUED BUT NOT PICKED UP`. Nothing external can fix it — press a key in
that session's own terminal and it will drain everything queued for it.

## Testing (fast-forward trick)

1. `agentctl renew add benjr <live-session> --reset 11:00pm`
2. Edit `~/.agentctl/renew/registry.json`: set the actions' `"at"` to a
   minute from now and swap `"message"` for `"TEST — reply with just: ok"`.
   The daemon re-reads the registry every 30 s; no restart needed.
3. `agentctl renew log -f` — expect `FIRED continue`, `FIRED follow-up`, then
   `nothing pending — daemon exiting`, and the target session replies in its
   own terminal.

## Caveats

- Two live sessions with the same name on one account: delivery addresses by
  name and may hit either — the daemon logs a warning. Rename one (`/rename`).
- The continue runs under the session's own permission mode: anything it would
  normally ask you about, it still asks (and idles) until you return.
- Registering is per (account, session, path) — re-adding replaces the pending
  task for that trio.
- A Codex thread closed between registration and fire time cannot be revived by
  the daemon; reopen it with `codex resume <name>` and re-register.
