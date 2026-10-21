"""
agentctl log — one chronological stream of everything agentctl did:
morning anchor pings, scheduled continues, follow-up anchors, retries.

    agentctl log [-n 30] [--json] [-f] [--kind continue|follow-up|reset-ping|...]

Sources (merged by timestamp):
  ~/.agentctl/renew/renew.log          scheduled continues / follow-ups
  ~/.agentctl/anchor.log(.1)           the morning window-anchor pings
  ~/.claude/claude-renew/renew.log     legacy (pre-agentctl) renew log
  ~/.claude/cron-limit-reset.log(.1)   legacy morning anchor log

Kinds: reset-ping · continue · follow-up · scheduled · retry · gave-up ·
       removed · daemon
"""
import argparse, json, os, re, sys
from datetime import datetime

import agentctl

HOME = os.path.expanduser("~")
LEGACY_RENEW_LOG = os.path.join(HOME, ".claude", "claude-renew", "renew.log")
LEGACY_ANCHOR_LOGS = [os.path.join(HOME, ".claude", "cron-limit-reset.log.1"),
                      os.path.join(HOME, ".claude", "cron-limit-reset.log")]

RENEW_LOGS = [LEGACY_RENEW_LOG, agentctl.RENEW_LOG]
ANCHOR_LOGS = LEGACY_ANCHOR_LOGS + [agentctl.ANCHOR_LOG + ".1", agentctl.ANCHOR_LOG]

BOLD, DIM, RST = "\033[1m", "\033[2m", "\033[0m"
COLORS = {"continue": "\033[96m", "follow-up": "\033[95m",
          "reset-ping": "\033[32m", "retry": "\033[33m",
          "gave-up": "\033[31m", "scheduled": "\033[36m",
          "removed": "\033[33m", "daemon": "\033[2m"}


def classify_renew(msg):
    if msg.startswith("FIRED continue"):
        return "continue"
    if msg.startswith("FIRED follow-up"):
        return "follow-up"
    if msg.startswith(("added", "registered", "detected")):
        return "scheduled"
    if "retry" in msg:
        return "retry"
    if msg.startswith("GAVE UP"):
        return "gave-up"
    if msg.startswith("removed") or msg.startswith("cancelled"):
        return "removed"
    return "daemon"


def parse_renew(paths=RENEW_LOGS):
    """`[YYYY-MM-DD HH:MM:SS] message` lines."""
    events, seen = [], set()
    for path in paths:
        real = os.path.realpath(path)
        if real in seen or not os.path.exists(path):
            continue
        seen.add(real)
        with open(path, errors="replace") as f:
            for line in f:
                m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)", line)
                if not m:
                    continue
                ts, msg = m.groups()
                events.append({"ts": ts, "source": "renew",
                               "kind": classify_renew(msg), "detail": msg.strip()})
    return events


def parse_anchor(paths=ANCHOR_LOGS):
    """`=== <date> ===` headers followed by `[label] output` lines."""
    events, seen = [], set()
    for path in paths:
        real = os.path.realpath(path)
        if real in seen or not os.path.exists(path):
            continue
        seen.add(real)
        ts = None
        with open(path, errors="replace") as f:
            for line in f:
                m = re.match(r"=== (.*) ===", line)
                if m:
                    # e.g. "Sun Sep  6 07:30:05 -03 2026" — the short "-03" offset
                    # is not %z-parseable; times are local anyway, so drop it.
                    parts = [p for p in m.group(1).split()
                             if not re.fullmatch(r"[+-]\d{2}(:?\d{2})?", p)]
                    try:
                        ts = datetime.strptime(" ".join(parts),
                                               "%a %b %d %H:%M:%S %Y") \
                            .strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        ts = None
                    continue
                m = re.match(r"\[(\S+)\] (.*)", line)
                if m and ts:
                    label, out = m.groups()
                    events.append({"ts": ts, "source": "anchor",
                                   "kind": "reset-ping",
                                   "detail": f"{label}: {out.strip()}"})
    return events


def follow():
    """Live-tail the current logs (the legacy ones are frozen)."""
    paths = [agentctl.RENEW_LOG, agentctl.ANCHOR_LOG]
    for p in paths:
        try:                       # tail -F complains about files that never existed
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if not os.path.exists(p):
                open(p, "a").close()
        except OSError:
            pass
    os.execvp("tail", ["tail", "-n", "5", "-F", *paths])


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agentctl log",
        description="Merged log of anchor pings, continues and follow-ups.")
    ap.add_argument("-n", type=int, default=30, help="events to show (default 30)")
    ap.add_argument("--kind", help="filter by kind (comma-separated)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-f", "--follow", action="store_true",
                    help="live-tail the renew and anchor logs")
    a = ap.parse_args(argv)

    if a.follow:
        follow()
        return 0

    events = sorted(parse_renew() + parse_anchor(), key=lambda e: e["ts"])
    if a.kind:
        kinds = {k.strip() for k in a.kind.split(",")}
        events = [e for e in events if e["kind"] in kinds]
    events = events[-a.n:]

    if a.json:
        print(json.dumps(events, indent=1))
        return 0

    if not events:
        print("no events yet")
        return 0
    for e in events:
        c = COLORS.get(e["kind"], "")
        print(f"{DIM}{e['ts'][5:16]}{RST}  {c}{e['kind']:<10}{RST} {e['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
