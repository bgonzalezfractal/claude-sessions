# Claude Morning Limit-Reset Job — Reference

A scheduled job that fires a tiny `claude -p --model haiku "Hello, only answer hello"`
prompt once **per account**, at **7:30 AM Mon–Fri** and **10:30 AM Sat–Sun** (local
time; weekends later because we get up late — changed 2026-09-06). Purpose: anchor
Claude's **5-hour usage window** to the morning so the day's windows land at
**12:30 PM → 5:30 PM → 10:30 PM** on weekdays (weekend: 3:30 PM → 8:30 PM).
The ping runs on **haiku** so it burns almost no credit.

> **Timezone:** launchd's `StartCalendarInterval` fires at local wall-clock time, so
> Chile's DST changes (like the 2026-09-06 switch from -04 to -03) need no adjustment —
> 7:30 is always the real 7:30.

**Accounts pinged** (bggai added 2026-09-06) — all anchored at the same 7:30 time:

| Label | Config dir | Shell alias |
|-------|------------|-------------|
| `default` | `~/.claude` (no `CLAUDE_CONFIG_DIR` — see warning below) | `claude` |
| `benjr` | `~/.claude-benjr` | `claude-benjr` (defined in `~/.zshrc`) |
| `bggai` | `~/.claude-bggai` | `claude-bggai` (defined in `~/.zshrc`) |

> ⚠️ **The default account must be pinged WITHOUT `CLAUDE_CONFIG_DIR` set.** When the
> env var is present — even pointing at `~/.claude` — the CLI derives a path-suffixed
> keychain service name (`Claude Code-credentials-<hash>`) and misses the default
> credential, which lives under the plain `Claude Code-credentials` entry. The result is
> a false `Not logged in · Please run /login` while the account is actually fine. This
> silently broke the default ping from 2026-08-24 to 2026-09-05; the script now uses
> `env -u CLAUDE_CONFIG_DIR` for the default account.

> **Scope note:** This only affects the rolling **5-hour** window. It does **not** reset
> the separate **weekly** cap, and it does **not** clear usage already consumed in an
> active window — it just starts a fresh window when none is running.

Created: 2026-07-07 · Runs as user `bgg`.

---

## What was set up (and why launchd, not cron)

The first attempt used a normal `crontab` entry. It silently never fired because
**modern macOS sandboxes `/usr/sbin/cron`** (Apple deprecated it; it needs Full Disk
Access to run and even then is unreliable). The crontab was removed.

The working setup uses **launchd**, the native macOS scheduler. It runs as your user,
reads your home directory without special permissions, and runs the job on wake if the
Mac was asleep at 7:30.

### The three pieces

| Piece | Path | Role |
|-------|------|------|
| **LaunchAgent** (schedule) | `~/Library/LaunchAgents/com.grail.limitreset.plist` | Tells launchd to run the script at 7:30 AM daily |
| **Script** (logic + rotation) | `~/.claude/limit-reset.sh` | Rotates the log if >50 KB, then runs `claude -p ...` once per account |
| **Log** | `~/.claude/cron-limit-reset.log` (+ `.log.1` rotated backup) | Timestamped record of each run |
| **Error log** | `~/.claude/cron-limit-reset.err` | launchd stderr (usually empty) |

The job label is **`com.grail.limitreset`**.

### Log rotation

The script checks the log size before each run. If `cron-limit-reset.log` exceeds
**50 KB**, it is moved to `cron-limit-reset.log.1` (overwriting the previous backup) and
a fresh log starts. Ceiling: ~100 KB total (current + one backup). At ~60 bytes/day this
cap won't be reached for years, but the log can never grow unbounded.

---

## Everyday commands

Set the user-id variable once per terminal session (used by all `launchctl` commands):

```bash
UID_NUM=$(id -u)
```

### Check status / "did it run?"
```bash
launchctl print gui/$UID_NUM/com.grail.limitreset | grep -E "state|runs|last exit"
cat ~/.claude/cron-limit-reset.log        # timestamped run history
```
`state = not running` is normal — the job is idle until 7:30. `last exit code = 0` means
the last run succeeded.

### Run it right now (test)
```bash
launchctl kickstart -k gui/$UID_NUM/com.grail.limitreset
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
1. Edit the plist: `~/Library/LaunchAgents/com.grail.limitreset.plist`
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

### Add / remove an account
Edit the `ACCOUNTS` array in `~/.claude/limit-reset.sh` — one `"label:config-dir"` entry
per account, pinged in order:

```zsh
ACCOUNTS=(
    "default:$HOME/.claude"
    "benjr:$HOME/.claude-benjr"
    "bggai:$HOME/.claude-bggai"
)
```

Each run writes one labeled line per account, e.g. `[benjr] Hello`. No reload needed.
Remember: any account whose credential lives under the **plain** keychain entry (the
default account) must be run without `CLAUDE_CONFIG_DIR` — the script special-cases the
`default` label for this.

### Change the prompt or rotation size
Edit `~/.claude/limit-reset.sh`:
- The prompt text is in the `claude -p "..."` line.
- The rotation threshold is `MAXBYTES=51200` (50 KB).
- No reload needed — the plist just calls the script, so script edits take effect on the
  next run. (Keep it executable: `chmod +x ~/.claude/limit-reset.sh`.)

---

## How to DELETE it completely

```bash
UID_NUM=$(id -u)

# 1. Unload from launchd
launchctl bootout gui/$UID_NUM ~/Library/LaunchAgents/com.grail.limitreset.plist

# 2. Remove the files
rm ~/Library/LaunchAgents/com.grail.limitreset.plist
rm ~/.claude/limit-reset.sh
rm -f ~/.claude/cron-limit-reset.log ~/.claude/cron-limit-reset.log.1 ~/.claude/cron-limit-reset.err
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
  "Could not find service", the agent isn't loaded; run the re-enable command.
- **`last exit code` non-zero** → check `~/.claude/cron-limit-reset.err` and confirm
  `claude` still lives at `/opt/homebrew/bin/claude` (`which claude`). If Homebrew moved it,
  update the path in `~/.claude/limit-reset.sh`.
- **Nothing happens on kickstart** → ensure the script is executable:
  `chmod +x ~/.claude/limit-reset.sh`.
- **`[label] SKIPPED — .../.claude.json missing or empty`** → that account's config file
  is gone. Restore it from `~/<config-dir>/backups/.claude.json.backup.*` (pick the
  largest/most recent) before the next run. The guard exists because a `claude` launched
  against a config dir with no `.claude.json` **silently creates a fresh stub**, wiping
  `oauthAccount` + project history and leaving the account "Not logged in".
- **`Not logged in · Please run /login` in the log** → same root cause as above; the
  config was stubbed. Restore the backup, don't re-login.

> ⚠️ **Don't `kickstart` this job while an interactive `claude` session is open on the
> same account.** Two processes touching one `.claude.json` can race, and the losing side
> can find the file mid-rewrite and recreate it as a stub. This bit us on 2026-08-03: a
> manual kickstart during a live session clobbered the default account's 127 KB config
> down to 389 bytes. At the real 7:30 AM trigger no session is typically open, so the
> scheduled path is safe — test with the guard in place, or test only the second account.
