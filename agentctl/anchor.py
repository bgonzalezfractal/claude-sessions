"""`agentctl anchor` — open a usage window on every account, early.

The pings themselves are bin/anchor.sh: one throwaway "Hello, only answer
hello" per logged-in account (Claude Code and Codex), logged to
~/.agentctl/anchor.log in the format `agentctl log` parses.

Why this is more than a wrapper around that script
--------------------------------------------------
A 5-hour window is fixed from the FIRST request that opens it. A ping sent
while a window is already running is absorbed: it costs a few tokens and
anchors nothing. So on a Mac where sessions are deliberately left open
overnight, the 7:30 job only helps the accounts that happened to be idle at
7:30 — the others drift to whenever their window expires and someone happens
to type. Measured on 2026-09-14: benjr was idle and got anchored at 7:30,
while default sat mid-window until 8:40 and was not anchored until 10:00, an
80-minute loss.

So this command reads each account's window state BEFORE pinging, and for the
accounts that are mid-window it queues an anchor ping (through the renew
daemon) for the minute their current window actually expires. One extra ping
per account per run, and no window is left unanchored during the day.

Accounts whose window expires after --until (22:00 by default) are left alone;
anchoring a window at 3 AM only burns weekly quota. So is any "window" that
ends more than 5 hours out: that is not a session window at all (Codex free
reported its 30-day window as one on 2026-11-07 and got a catch-up 11 days
out).
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, time as dtime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "bin", "anchor.sh")

from agentctl import renew                              # noqa: E402
from agentctl.providers import discover_accounts, provider_of   # noqa: E402

LEAD_MINUTES = 1          # fire just after the window expires, not exactly on it
SESSION_WINDOW = timedelta(hours=5, minutes=5)   # longest a session window can have left


def parse_until(text):
    try:
        h, m = (int(x) for x in text.split(":", 1))
        return dtime(hour=h, minute=m)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(f"invalid time {text!r} — use HH:MM")


def window_state(account):
    """-> (open_until | None, note). open_until None = nothing to absorb a ping."""
    prov = provider_of(account)
    try:
        reset_at, info = prov.session_reset_at(account)
    except Exception as e:                     # a broken provider must not stop the pings
        return None, f"could not read usage ({e})"
    if not reset_at:
        return None, info
    now = datetime.now()
    if reset_at <= now:
        return None, "window already expired"
    if reset_at - now > SESSION_WINDOW:
        return None, f"window ends {reset_at:%a %d %b %H:%M} — not a 5 h session window"
    return reset_at, info


def run_pings(argv):
    if not os.path.isfile(SCRIPT):
        print(f"agentctl anchor: {SCRIPT} not found", file=sys.stderr)
        return 1
    try:
        return subprocess.call(["/bin/zsh", SCRIPT, *argv])
    except OSError as e:
        print(f"agentctl anchor: {e}", file=sys.stderr)
        return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="agentctl anchor",
        description="Ping every account so its usage window starts now; queue a "
                    "catch-up ping for accounts that are mid-window.")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be pinged, change nothing")
    ap.add_argument("--no-catch-up", action="store_true",
                    help="just ping (the pre-2026-09-14 behaviour)")
    ap.add_argument("--until", type=parse_until, default=dtime(22, 0), metavar="HH:MM",
                    help="don't queue a catch-up whose window expires after this "
                         "(default 22:00)")
    a = ap.parse_args(argv)

    if a.dry_run:
        return run_pings(["--dry-run"])

    # Read the windows FIRST: after the pings, an idle account looks identical
    # to one that was already running, and would be queued a pointless
    # catch-up five hours out.
    mid_window = []
    if not a.no_catch_up:
        for account in discover_accounts():
            open_until, note = window_state(account)
            if open_until:
                mid_window.append((account, open_until, note))

    rc = run_pings([])

    queued = False
    cutoff = datetime.combine(datetime.now().date(), a.until)
    for account, open_until, note in mid_window:
        at = open_until + timedelta(minutes=LEAD_MINUTES)
        if at > cutoff:             # incl. after midnight: no overnight anchors
            print(f"{account['label']}: mid-window until {open_until:%H:%M} — "
                  f"after {a.until:%H:%M}, not queuing a catch-up")
            continue
        renew.schedule_account_anchor(account, at, spawn=False)
        queued = True
        print(f"{account['label']}: was mid-window ({note}) — the ping could not "
              f"anchor it; catch-up queued for {at:%a %H:%M}")
    if queued:
        renew.ensure_daemon()      # once, after the batch — not per account
    return rc


if __name__ == "__main__":
    sys.exit(main())
