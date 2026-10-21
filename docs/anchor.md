# agentctl anchor — Reference

The morning **window-anchor job**: one tiny "Hello, only answer hello" per
account, at **7:30 AM Mon–Fri** and **10:30 AM Sat–Sun** (local time; weekends
later because we get up late — changed 2026-09-06). Purpose: anchor each
account's **5-hour usage window** to the morning so the day's windows land at
**12:30 PM → 5:30 PM → 10:30 PM** on weekdays (weekend: 3:30 PM → 8:30 PM).
Each ping is the cheapest request its provider offers, so it burns almost no
credit.

```bash
agentctl anchor --dry-run     # what would be pinged, sends nothing
agentctl anchor               # run the pings now
```

> **Timezone:** launchd's `StartCalendarInterval` fires at local wall-clock time, so
> Chile's DST changes (like the 2026-09-06 switch from -04 to -03) need no adjustment —
> 7:30 is always the real 7:30.

**Accounts pinged** — auto-discovered, all anchored at the same time:

| Provider | Label | Config dir | Ping |
|----------|-------|------------|------|
| claude | `default` | `~/.claude` (no `CLAUDE_CONFIG_DIR` — see warning below) | `claude --safe-mode -p --model haiku --tools '' --no-session-persistence` |
| claude | `benjr` | `~/.claude-benjr` | same, with `CLAUDE_CONFIG_DIR=~/.claude-benjr` |
| claude | `bggai` | `~/.claude-bggai` | same, with `CLAUDE_CONFIG_DIR=~/.claude-bggai` |
| codex | `codex` | `~/.codex` (no `CODEX_HOME`) | `codex exec --ephemeral --skip-git-repo-check -s read-only -C ~` |
| codex | `codex-<alias>` | `~/.codex-<alias>` | same, with `CODEX_HOME=~/.codex-<alias>` |

Discovery rule: a claude account is `~/.claude` plus every `~/.claude-*` dir
with a **non-empty `.claude.json`**; a codex account is `~/.codex` plus every
`~/.codex-*` dir with an **`auth.json`**. Nothing to edit when an account is
added — log in once and the next run includes it.

> ⚠️ **The default account of each provider must be pinged WITHOUT its config-dir env
> var.** For Claude, when `CLAUDE_CONFIG_DIR` is present — even pointing at `~/.claude` —
> the CLI derives a path-suffixed keychain service name (`Claude Code-credentials-<hash>`)
> and misses the default credential, which lives under the plain `Claude Code-credentials`
> entry. The result is a false `Not logged in · Please run /login` while the account is
> actually fine. This silently broke the default ping from 2026-08-24 to 2026-09-05; the
> script uses `env -u CLAUDE_CONFIG_DIR` (and `env -u CODEX_HOME`) for the default account.

> **Scope note:** This only affects the rolling **5-hour** window. It does **not** reset
> the separate **weekly** cap, and it does **not** clear usage already consumed in an
> active window — it just starts a fresh window when none is running.

Created: 2026-07-07 · Runs as user `bgg`.

---

## Why a ping is not always enough (and what agentctl does about it)

A five-hour window is fixed from the **first request that opens it**. A ping
sent while a window is already running is absorbed: it costs a few tokens and
anchors nothing. Leaving sessions open overnight — which is the point of
leaving them open — therefore defeats a fixed-time morning job for exactly the
accounts you were using.

Measured on 2026-09-14, with the job running at 07:30:

| Account | State at 07:30 | Ping result | Window it ended up with |
|---|---|---|---|
| benjr | idle | anchored | 07:30 → 12:30 |
| bggai | mid-window until ~08:30 | absorbed | 08:39 → 13:30 |
| default | mid-window until ~08:40 | absorbed | 10:00 → 15:00 |

The default account lost 80 minutes: its window expired at 08:40 and nothing
reopened it until someone typed at 10:00.

So `agentctl anchor` reads every account's window state **before** pinging, and
for each account that is mid-window it queues a catch-up anchor ping, through
the renew daemon, for the minute its current window actually expires:

```
default: was mid-window (40% used) — the ping could not anchor it;
         catch-up queued for Mon 15:01
```

One extra ping per account per run. Accounts whose window expires after
`--until` (22:00 by default) are skipped, since anchoring a window at 3 AM only
spends weekly quota. `--no-catch-up` restores the old ping-only behaviour, and
`agentctl renew list` shows the queued anchors.

## What was set up (and why launchd, not cron)

The first attempt used a normal `crontab` entry. It silently never fired because
**modern macOS sandboxes `/usr/sbin/cron`** (Apple deprecated it; it needs Full Disk
Access to run and even then is unreliable). The crontab was removed.

The working setup uses **launchd**, the native macOS scheduler. It runs as your user,
reads your home directory without special permissions, and runs the job on wake if the
Mac was asleep at 7:30.

### The pieces

| Piece | Path | Role |
|-------|------|------|
| **LaunchAgent** (schedule) | `~/Library/LaunchAgents/com.grail.limitreset.plist` | Tells launchd to run the script at 7:30 / 10:30 |
| **Script** (logic + rotation) | `~/.agentctl/anchor.sh` — an installed **regular copy** of `bin/anchor.sh` | Rotates the log if >50 KB, then pings once per account |
| **Repo source** | `bin/anchor.sh` | What you edit; `./install.sh` copies it into place |
| **Front-end** | `agentctl anchor` (`agentctl/anchor.py`) | Runs the repo script by hand |
| **Log** | `~/.agentctl/anchor.log` (+ `.log.1` rotated backup) | Timestamped record of each run |
| **Error log** | `~/.agentctl/anchor.err` | launchd stderr (usually empty) |

The job label is still **`com.grail.limitreset`** (kept so the existing schedule
survives the rename).

> **Edit `bin/anchor.sh`, then re-run `./install.sh`.** launchd runs the copy in
> `~/.agentctl`, not the repo file — a repo-only edit changes `agentctl anchor` but not
> the 7:30 run. The installer replaces the copy atomically (mktemp + mv), so it is safe
> to run while the job is idle or mid-flight.

### Log format

Each run appends a `=== <date> ===` header and one line per account and attempt:

```
=== Sun Sep 14 07:30:02 -03 2026 ===
[default] attempt=1 exit=0 Hello!
[benjr] attempt=1 exit=0 Hello!
[bggai] attempt=1 exit=0 Hello!
[codex] attempt=1 exit=0 hello
```

`agentctl log` parses exactly this format (kind `reset-ping`), so keep it if you
edit the script.

### Log rotation

The script checks the log size before each run. If `anchor.log` exceeds **50 KB**, it is
moved to `anchor.log.1` (overwriting the previous backup) and a fresh log starts.
Ceiling: ~100 KB total (current + one backup). At ~80 bytes/day this cap won't be
reached for years, but the log can never grow unbounded.

### Robustness

- Runs from `$HOME`; disables customizations, tools and session persistence for
  the Claude pings, and uses `--ephemeral` + `-s read-only` for the Codex one —
  nothing it does can touch a real session or write state.
- A **lock dir** (`~/.agentctl/anchor.lock`) prevents overlapping runs and is
  recovered if a previous job was killed; it is always removed on exit.
- Each ping is limited to **90 seconds** plus 5 s for termination.
- **Three attempts**, but only transient network failures are retried
  (`ENOTFOUND`, `ECONNRESET`, `Unable to connect`, `Can't reach`, `timed out`).
  Auth failures and "hit your usage limit" are **not** retried — they need a
  later run, not a faster one.
- A missing or empty config file is logged as
  `[label] FAILED — <path> missing or empty` and the account is skipped; the
  guard exists because a `claude` launched against a config dir with no
  `.claude.json` **silently creates a fresh stub**, wiping `oauthAccount` +
  project history.
- Exit status is **1 if any account failed**, 0 otherwise.

---

## Everyday commands

Set the user-id variable once per terminal session (used by all `launchctl` commands):

```bash
UID_NUM=$(id -u)
```

### Check status / "did it run?"
```bash
launchctl print gui/$UID_NUM/com.grail.limitreset | grep -E "state|runs|last exit"
cat ~/.agentctl/anchor.log        # timestamped run history
agentctl log --kind reset-ping    # same thing, merged with the renew activity
```
`state = not running` is normal — the job is idle until 7:30. `last exit code = 0` means
the last run succeeded.

### Run it right now (test)
```bash
agentctl anchor                                       # the repo script, in your terminal
launchctl kickstart -k gui/$UID_NUM/com.grail.limitreset   # the real scheduled path
```

### Temporarily disable / re-enable
```bash
# Off
launchctl bootout gui/$UID_NUM ~/Library/LaunchAgents/com.grail.limitreset.plist

# On
launchctl bootstrap gui/$UID_NUM ~/Library/LaunchAgents/com.grail.limitreset.plist
```

---

## How to EDIT it

### Change the time
1. Edit the plist: `~/Library/LaunchAgents/com.grail.limitreset.plist` (or the
   heredoc in `install.sh`, which rewrites it — edit both, or the next install
   reverts you).
2. `StartCalendarInterval` is an **array** of dicts, one per weekday
   (`Weekday`: 0=Sunday … 6=Saturday). Mon–Fri entries fire at 7:30, the
   Sat (6) and Sun (0) entries at 10:30. Change `Hour`/`Minute` in whichever
   entries you want:
   ```xml
   <dict>
       <key>Weekday</key><integer>6</integer>
       <key>Hour</key><integer>10</integer>
       <key>Minute</key><integer>30</integer>
   </dict>
   ```
3. Reload for the change to take effect:
   ```bash
   UID_NUM=$(id -u)
   launchctl bootout   gui/$UID_NUM ~/Library/LaunchAgents/com.grail.limitreset.plist
   launchctl bootstrap gui/$UID_NUM ~/Library/LaunchAgents/com.grail.limitreset.plist
   ```

> **Always reload after editing the plist** — launchd caches the schedule at load time.
> (`./install.sh` does the unload/load for you.)

### Add / remove an account
Nothing to edit. Log in on a new account (`agentctl accounts add work`, then
`claude-work` → `/login`; or `CODEX_HOME=~/.codex-work codex login`) and the next run
discovers it. To *stop* pinging an account, log it out or move its config dir aside.

### Change the prompt or rotation size
Edit `bin/anchor.sh` and re-run `./install.sh`:
- The prompt text is `PROMPT='Hello, only answer hello'`.
- The rotation threshold is `MAXBYTES=51200` (50 KB).
- The per-ping timeout is the `sleep 90` in the watchdog subshell.
- The binaries are `CLAUDE_BIN` / `CODEX_BIN` (both overridable from the
  environment — that is how the tests inject fakes).

---

## How to DELETE it completely

```bash
UID_NUM=$(id -u)

# 1. Unload from launchd
launchctl bootout gui/$UID_NUM ~/Library/LaunchAgents/com.grail.limitreset.plist

# 2. Remove the files
rm ~/Library/LaunchAgents/com.grail.limitreset.plist
rm ~/.agentctl/anchor.sh
rm -f ~/.agentctl/anchor.log ~/.agentctl/anchor.log.1 ~/.agentctl/anchor.err
```

That leaves no trace. (The original crontab was already removed during setup — nothing
to clean up there.)

---

## Running when the Mac is asleep or off

| State at 7:30 | Runs? |
|---------------|-------|
| Awake | ✅ On time |
| Asleep (powered, lid closed) | ✅ launchd runs it on next wake; it won't wake the Mac by itself |
| Fully shut down / powered off | ❌ Nothing runs while off; catches up at the next 7:30 the Mac is on |

**If you shut the Mac down overnight, the 7:30 ping won't happen.** To have it wake and
run, schedule a wake with `pmset` (needs `sudo`, run it yourself):

```bash
# Wake (or power on, hardware permitting) weekdays at 7:29 AM
sudo pmset repeat wakeorpoweron MTWRF 07:29:00

# Check the scheduled wake
pmset -g sched

# Cancel the scheduled wake
sudo pmset repeat cancel
```

Notes:
- **Wake from sleep** works on both Apple Silicon and Intel.
- **Power on from a full shutdown** is reliable mainly on Intel/desktop Macs; unreliable on
  Apple Silicon laptops. If you truly power off nightly, leaving the Mac **asleep** instead
  of **off** is the dependable path.

---

## Optional: a GUI to view/manage it

macOS has no built-in GUI for launchd jobs (Activity Monitor only shows processes while
running, and this one runs ~5 s then exits). Third-party apps that list every LaunchAgent
with its schedule, last exit code, and logs:

- **LaunchControl** (~$10) — most capable
- **Lingon X** (~$15) — friendlier

Either will show `com.grail.limitreset`. Not required — the terminal commands above cover
everything.

---

## Troubleshooting

- **`[default] Not logged in · Please run /login` while interactive `claude` works fine** →
  the ping is being run with `CLAUDE_CONFIG_DIR` set (see the warning at the top). Do not
  re-login; fix the invocation to drop the env var for the default account.
- **No new log line after 7:30** → Mac was off/asleep (see table above), or the agent was
  unloaded. Check: `launchctl print gui/$(id -u)/com.grail.limitreset` — if it errors with
  "Could not find service", the agent isn't loaded; run `./install.sh` or the re-enable
  command.
- **`last exit code` non-zero** → check `~/.agentctl/anchor.err` and confirm the binaries
  are still where the script expects (`which claude`, `which codex`). If Homebrew moved
  one, set `CLAUDE_BIN` / `CODEX_BIN` in `bin/anchor.sh` and re-install.
- **Repo edits have no effect at 7:30** → you forgot `./install.sh`; launchd runs
  `~/.agentctl/anchor.sh`.
- **`can't open input file`** → `~/.agentctl/anchor.sh` is a symlink into `Documents`.
  macOS privacy restrictions block launchd from following it (this broke the job from
  2026-09-06 to 2026-09-12). The installer writes a **regular copy** instead; re-run it.
- **Nothing happens on kickstart** → ensure the script is executable:
  `chmod +x ~/.agentctl/anchor.sh`.
- **`[label] FAILED — .../.claude.json missing or empty`** → that account's config file
  is gone. Restore it from `~/<config-dir>/backups/.claude.json.backup.*` (pick the
  largest/most recent) before the next run.
- **`[codex] … No active session` / `codex: command not found`** → Codex isn't installed
  at `CODEX_BIN` (default `/opt/homebrew/bin/codex`), or `~/.codex/auth.json` is missing.
  `codex login` once; the account then joins automatically.
- **`Not logged in · Please run /login` in the log** → same root cause as the stub case
  above; the config was stubbed. Restore the backup, don't re-login.

> ⚠️ **Don't `kickstart` this job while an interactive `claude` session is open on the
> same account.** Two processes touching one `.claude.json` can race, and the losing side
> can find the file mid-rewrite and recreate it as a stub. This bit us on 2026-08-03: a
> manual kickstart during a live session clobbered the default account's 127 KB config
> down to 389 bytes. At the real 7:30 AM trigger no session is typically open, so the
> scheduled path is safe — test with the guard in place, or test only a second account.

## Limits this job does not lift

An active 5-hour or weekly limit can still reject the ping; this job does not clear
limits or schedule a later retry for them — that is [`agentctl renew`](renew.md).
Successful greetings anchor usage only when the account can accept a new request.
