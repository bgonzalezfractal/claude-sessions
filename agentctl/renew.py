#!/usr/bin/env python3
"""
agentctl renew — resume an interrupted session the moment its limit renews.

Works for every provider agentctl knows (Claude Code and Codex): accounts are
addressed by their unique label ("default", "benjr", "bggai", "codex"), and the
provider behind that label does the delivery.

The scenario: a session runs out at 2–3 AM mid-task and renews at, say, 5:40 AM.
You go to bed. Because the work was interrupted, waiting for the 7:30 morning
anchor wastes the 5:40 renewal. So you register the session before bed:

    agentctl renew add codex new-acp --query "Continue, and consider ..."

and the daemon (24 h, self-expiring) will:
  1. at reset + 5 min (5:45) message the live session to CONTINUE its pending task;
  2. at continue + 5 h 05 m (10:50) send a follow-up anchor ping that opens the
     next window at the earliest possible moment of your day.

If nothing was interrupted, don't register — the morning anchor job
(7:30 weekdays / 10:30 weekends) already anchors the day.

Every timing and message is personalizable per task; `remove` cancels pending
tasks. Run `agentctl renew --help` / `agentctl renew add --help`.

Mechanics:
  * Sessions are addressed by their /rename alias and must be LIVE (terminal
    open); `--path` additionally pins the session's working directory.
  * Delivery is the provider's job (providers/__init__.py contract):
      - codex  : `codex queue --thread <name> --message TEXT` (built in, no
                 helper model involved);
      - claude : a headless `claude -p --model haiku` helper on the SAME account
                 using ListAgents + SendMessage (peer visibility is per config
                 dir; the default account runs WITHOUT CLAUDE_CONFIG_DIR).
  * `--reset auto` (the default) asks the provider for the exact session
    `resets_at` — read-only, no window anchored.
  * The follow-up is a window anchor only: a separate throwaway ping on the
    account (`claude -p --model haiku` / `codex exec --ephemeral`), never
    touching the working session, which runs on its own (expensive) model.
"""

import argparse, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta

from agentctl import HOME, RENEW_DIR, RENEW_REGISTRY, RENEW_PIDFILE, RENEW_LOG
from agentctl.providers import discover_accounts, get_account, provider_of

BASE = RENEW_DIR
REGISTRY = RENEW_REGISTRY
PIDFILE = RENEW_PIDFILE
LOG = RENEW_LOG
LEGACY_REGISTRY = os.path.join(HOME, ".claude", "claude-renew", "registry.json")

PREFIX = "Your usage limit has reset (scheduled auto-continue by agentctl). "
DEFAULT_CONTINUE = "Continue with the pending task exactly where you left off."
DEFAULT_FOLLOWUP = "Hello, only answer hello"

TICK_SECONDS = 30
RETRY_MINUTES = 10
MAX_RETRIES = 8
MAX_REARMS = 3          # re-schedules after a delivered continue died on the limit

DELAY_CHOICES = (0, 5, 10, 15, 20, 25, 30)


def continue_message(query=None, message=None):
    """Default continue text, optionally extended with the user's own query.
    `message` (--message) is a full verbatim override."""
    if message:
        return message
    return PREFIX + (query.strip() if query else DEFAULT_CONTINUE)


# ------------------------------------------------------------------ helpers ---
def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    os.makedirs(BASE, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line)


def load_registry():
    try:
        with open(REGISTRY) as f:
            return json.load(f)
    except Exception:
        return []


def save_registry(entries):
    os.makedirs(BASE, exist_ok=True)
    tmp = REGISTRY + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=1)
    os.replace(tmp, REGISTRY)


def migrate_legacy():
    """One-time: adopt ~/.claude/claude-renew/registry.json (claude-renew's
    state) the first time agentctl runs. The old files are left in place."""
    if os.path.exists(REGISTRY) or not os.path.isfile(LEGACY_REGISTRY):
        return
    try:
        with open(LEGACY_REGISTRY) as f:
            entries = json.load(f)
    except Exception:
        return
    if not isinstance(entries, list) or not entries:
        return                       # nothing worth adopting
    for e in entries:
        if isinstance(e, dict):
            e.setdefault("provider", "claude")
    save_registry(entries)
    log(f"migrated {len(entries)} task(s) from {LEGACY_REGISTRY}")


def norm_path(p):
    return os.path.realpath(os.path.expanduser(p)) if p else None


def resolve_account(label):
    """Account dict for a label, or exit with the list of known labels."""
    acct = get_account(label)
    if not acct:
        known = ", ".join(f"{a['label']} ({a['provider']})"
                          for a in discover_accounts()) or "none logged in"
        sys.exit(f"unknown account {label!r} — one of: {known}")
    return acct


def parse_clock(s):
    """A real datetime, not a string. Accepts:
      '5:40am' / '11pm'        (12-hour wall time)
      '05:40' / '17:40'        (24-hour wall time)
      '2026-09-07T05:40'       (full datetime, e.g. from a datetime picker)
    Wall times resolve to their NEXT future occurrence; full datetimes must be
    in the future."""
    s = s.strip().lower()
    now = datetime.now()
    if "t" in s or "-" in s:               # full datetime (datetime-local / ISO)
        try:
            t = datetime.fromisoformat(s.upper().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"invalid datetime {s!r} — expected YYYY-MM-DDTHH:MM")
        if t.tzinfo:
            t = t.astimezone().replace(tzinfo=None)
        if t <= now:
            raise ValueError(f"{t:%Y-%m-%d %H:%M} is in the past")
        return t
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m or (m.group(2) is None and m.group(3) is None):
        raise ValueError(f"invalid time {s!r} — use 5:40am, 17:40, "
                         f"or a full YYYY-MM-DDTHH:MM")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"invalid time {s!r}")
    t = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t


def parse_followup(spec, continue_at):
    """'off' -> None; '+5h5m'/'+5:05' -> continue_at+delta; '10:50am' -> clock."""
    s = spec.strip().lower()
    if s in ("off", "none", "no"):
        return None
    if s.startswith("+"):
        m = re.fullmatch(r"\+(?:(\d+)h)?(?:(\d+)m)?", s) or \
            re.fullmatch(r"\+(\d+):(\d{2})", s)
        if not m or not any(m.groups()):
            raise ValueError(f"can't parse follow-up {spec!r} — use +5h5m, +5:05, "
                             f"a clock time like 10:50am, or off")
        h, mi = int(m.group(1) or 0), int(m.group(2) or 0)
        return continue_at + timedelta(hours=h, minutes=mi)
    return parse_clock(s)


# ---------------------------------------------------------------- messaging ---
def fire(entry, action):
    label, name, path = entry["account"], entry["session"], entry.get("path")
    acct = get_account(label)
    if not acct:
        _reschedule(action, f"account {label!r} not found (logged out?)")
        return
    prov = provider_of(acct)

    if action["kind"] in ("follow-up", "anchor"):
        # Anchor ping: a separate throwaway request on the account. Any request
        # opens the window; the working session is left alone.
        status, out = prov.anchor_ping(acct, path)
        out = (out or "").strip()
        if status == "ok":
            action["done"] = True
            log(f"FIRED {action['kind']} (anchor ping) -> {label}: "
                f"{out[-120:].replace(chr(10), ' ')}")
        elif status == "limited":
            _reschedule(action, f"{label} still limited")
        else:
            _reschedule(action, f"anchor ping failed: {out[-150:]}")
        return

    live = prov.find_live_sessions(acct, name, path)
    if len(live) > 1:
        log(f"warning: {len(live)} live sessions named {name!r} — messaging by "
            f"name may hit either")
    if not live:
        _reschedule(action, f"session {name!r} not live"
                            + (f" at {path}" if path else ""))
        return

    status, detail = prov.send_message(acct, name, path or live[0].get("cwd"),
                                       action["message"])
    detail = (detail or "").strip()
    tail = detail[-200:].replace("\n", " ")
    if status == "sent-limited":
        # Delivered, but its turn died on the usage limit, so the work never
        # happened. Re-sending now would only stack a duplicate instruction on
        # a session that already has it: close this action and arm a fresh one
        # for the NEW reset instead.
        action["done"] = True
        log(f"FIRED {action['kind']} -> {label}/{name}: {tail}")
        _rearm(entry, action, acct, prov)
    elif status == "sent":
        action["done"] = True
        log(f"FIRED {action['kind']} -> {label}/{name}: {tail}")
    elif status == "not-live":
        _reschedule(action, f"session {name!r} not live")
    elif status == "limited":
        _reschedule(action, f"{label} still limited")
    else:
        _reschedule(action, f"delivery failed: {detail[-150:]}")


def _rearm(entry, action, acct, prov):
    """Queue another continue at the account's next reset, after a delivered
    message was killed by the limit. Bounded by MAX_REARMS."""
    rearms = action.get("rearms", 0) + 1
    if rearms > MAX_REARMS:
        log(f"not re-arming {entry['account']}/{entry['session']} — "
            f"{MAX_REARMS} continues in a row died on the usage limit")
        return
    reset_at, info = prov.session_reset_at(acct)
    if not reset_at or reset_at <= datetime.now():
        log(f"can't re-arm {entry['account']}/{entry['session']} — "
            f"no future reset detected ({info})")
        return
    at = reset_at + timedelta(minutes=entry.get("delay", 5))
    entry["reset_at"] = reset_at.isoformat(timespec="seconds")
    # The entry's TTL must outlive the action it now carries, or the daemon
    # would drop it before firing.
    horizon = at + timedelta(hours=1)
    if datetime.fromisoformat(entry["expires_at"]) < horizon:
        entry["expires_at"] = horizon.isoformat(timespec="seconds")
    entry["actions"].append({"kind": "continue",
                             "at": at.isoformat(timespec="seconds"),
                             "message": action["message"], "retries": 0,
                             "rearms": rearms, "done": False})
    log(f"re-armed continue {rearms}/{MAX_REARMS} for {entry['account']}/"
        f"{entry['session']} @ {at:%a %H:%M} (turn hit the limit; {info})")


def _reschedule(action, why):
    action["retries"] += 1
    if action["retries"] > MAX_RETRIES:
        action["done"] = True
        log(f"GAVE UP on {action['kind']} after {MAX_RETRIES} retries ({why})")
        return
    action["at"] = (datetime.now() + timedelta(minutes=RETRY_MINUTES)
                    ).isoformat(timespec="seconds")
    log(f"{why} — retry {action['retries']}/{MAX_RETRIES} at {action['at'][11:16]}")


# ------------------------------------------------------------------- daemon ---
def dispatcher():
    """Path of bin/agentctl next to this package."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                        "bin", "agentctl")


def ensure_daemon():
    try:
        pid = int(open(PIDFILE).read().strip())
        os.kill(pid, 0)
        return
    except Exception:
        pass
    p = subprocess.Popen([sys.executable, dispatcher(), "renew", "run-daemon"],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"daemon spawned (pid {p.pid})")


def cmd_run_daemon(_a):
    os.makedirs(BASE, exist_ok=True)
    try:                                  # another daemon already owns the queue
        running = int(open(PIDFILE).read().strip())
        if running != os.getpid():
            os.kill(running, 0)
            log(f"daemon {os.getpid()} exiting — {running} already running")
            return
    except (OSError, ValueError):
        pass
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    log(f"daemon started (pid {os.getpid()})")
    try:
        while True:
            now = datetime.now()
            entries = [e for e in load_registry()
                       if datetime.fromisoformat(e["expires_at"]) > now]
            changed = False
            for e in entries:
                for a in e["actions"]:
                    if not a["done"] and datetime.fromisoformat(a["at"]) <= now:
                        fire(e, a)
                        changed = True
            pending = [e for e in entries
                       if any(not a["done"] for a in e["actions"])]
            if changed or len(pending) != len(load_registry()):
                save_registry(pending)
            if not pending:
                log("nothing pending — daemon exiting")
                break
            time.sleep(TICK_SECONDS)
    finally:
        try:
            os.remove(PIDFILE)
        except OSError:
            pass


# ------------------------------------------------------------------- wizard ---
def ask(prompt, default=None, validate=None, allow_empty=False):
    while True:
        sfx = f" [{default}]" if default not in (None, "") else ""
        v = input(f"  {prompt}{sfx}: ").strip() or (default or "")
        if not v:
            if allow_empty:
                return ""
            print("    ✗ required")
            continue
        if validate:
            try:
                validate(v)
            except Exception as e:
                print(f"    ✗ {e}")
                continue
        return v


def pick(title, items, render):
    print(f"\n{title}")
    for i, it in enumerate(items, 1):
        print(f"    {i}. {render(it)}")
    def valid(v):
        if not (v.isdigit() and 1 <= int(v) <= len(items)):
            raise ValueError(f"pick 1-{len(items)}")
    return items[int(ask("choice", "1", valid)) - 1]


def cmd_wizard(_a=None):
    """Interactive, validated setup with every option defaulted."""
    if not sys.stdin.isatty():
        sys.exit("no TTY — use: agentctl renew add <account> <session> [options]")
    print("── agentctl renew · schedule an auto-continue ──")

    accounts = discover_accounts()
    if not accounts:
        sys.exit("no logged-in accounts found")
    rows = [(a, provider_of(a).live_sessions(a)) for a in accounts]
    acct, live = pick("account", rows,
                      lambda r: f"{r[0]['label']:<10} {r[0]['provider']:<7} "
                                f"{len(r[1])} live session(s)")
    label = acct["label"]
    prov = provider_of(acct)

    if not live:
        sys.exit(f"no live sessions on {label!r} — open the session in a "
                 f"terminal first (this tool messages running sessions).")
    sess = pick(f"live sessions on {label}", live,
                lambda s: f"{s['name']:<26} {s['cwd']}")

    def vpath(v):
        p = norm_path(v)
        if not os.path.isdir(p):
            raise ValueError(f"not a directory: {p}")
    path = norm_path(ask("path", sess["cwd"], vpath))
    if path != norm_path(sess["cwd"]):
        print(f"    ! differs from the session's cwd ({sess['cwd']}) — the task "
              f"only fires while a live session named {sess['name']!r} runs in "
              f"that path.")
        if ask("continue anyway? (y/n)", "n").lower() not in ("y", "yes"):
            return cmd_wizard()

    detected, info = prov.session_reset_at(acct)
    if detected and detected > datetime.now():
        print(f"\n  detected reset: {detected:%a %d %H:%M} ({info})")
        reset = ask("reset (Enter = detected)",
                    detected.strftime("%Y-%m-%dT%H:%M"), parse_clock)
    else:
        why = info if not detected else f"{detected:%H:%M} already past"
        print(f"\n  ! couldn't auto-detect ({why}) — give the reset time "
              f"from the limit banner")
        reset = ask("reset (5:40am / 17:40 / YYYY-MM-DDTHH:MM)", None, parse_clock)
    print(f"    → reset at {parse_clock(reset):%a %d %b %H:%M}")

    def vdelay(v):
        if int(v) not in DELAY_CHOICES:
            raise ValueError(f"pick one of {', '.join(map(str, DELAY_CHOICES))}")
    delay = int(ask(f"delay after reset, minutes ({'/'.join(map(str, DELAY_CHOICES))})",
                    "5", vdelay))
    fu = ask("follow-up (+5h5m / 10:50am / off)", "+5h5m",
             lambda v: parse_followup(v, datetime.now()))

    query = ask("extra query (Enter = none)", "", allow_empty=True).strip()
    msg = None                      # None => default continue text (+ query)
    if not query:
        shown = DEFAULT_CONTINUE[:44] + "…"
        typed = ask("continue message", shown)
        if typed != shown:
            msg = typed             # full verbatim override
    fmsg = ask("follow-up message", DEFAULT_FOLLOWUP[:44] + "…") \
        if fu.lower() not in ("off", "none", "no") else DEFAULT_FOLLOWUP
    if fmsg == DEFAULT_FOLLOWUP[:44] + "…":
        fmsg = DEFAULT_FOLLOWUP
    ttl = int(ask("task lifetime, hours", "24", lambda v: int(v) > 0 or True))

    print(f"""
── summary ──
  account   : {label} ({acct['provider']})
  session   : {sess['name']}
  path      : {path}
  reset     : {reset}   continue: reset + {delay}m   follow-up: {fu}
  message   : {continue_message(query or None, msg)}
  ttl       : {ttl}h""")
    if ask("schedule? (y/n)", "y").lower() not in ("y", "yes"):
        print("aborted")
        return
    import types
    cmd_add(types.SimpleNamespace(
        account=label, session=sess["name"], path=path, reset=reset,
        delay=delay, follow_up=fu, query=query or None, message=msg,
        follow_up_message=fmsg, ttl=ttl))


# ----------------------------------------------------------------- commands ---
def schedule_account_anchor(acct, at, message=None, ttl_hours=12, spawn=True):
    """Register a throwaway anchor ping for a whole ACCOUNT at `at`.

    Used by `agentctl anchor` for an account that was mid-window when the
    morning job ran: the ping it sent could not open a window (a 5-hour window
    is fixed from its first request), so one is queued for the moment the
    current window actually expires. Replaces any pending anchor for the same
    account. -> True when it was (re)scheduled."""
    label = acct["label"]
    entries = [e for e in load_registry()
               if not (e.get("kind") == "account-anchor" and e["account"] == label)]
    entries.append({
        "provider": acct["provider"], "kind": "account-anchor",
        "account": label, "session": None, "path": None,
        "reset_at": at.isoformat(timespec="seconds"),
        "actions": [{"kind": "anchor", "at": at.isoformat(timespec="seconds"),
                     "message": message or DEFAULT_FOLLOWUP,
                     "retries": 0, "done": False}],
        "expires_at": (at + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
    })
    save_registry(entries)
    log(f"scheduled anchor for {label} @ {at:%a %H:%M} "
        f"(account was mid-window; ping could not open a new one)")
    if spawn:
        ensure_daemon()          # callers scheduling several in a row pass
                                 # spawn=False and call ensure_daemon() once
    return True


def cmd_add(a):
    acct = resolve_account(a.account)
    label, prov = acct["label"], provider_of(acct)
    path = norm_path(a.path)

    live = prov.find_live_sessions(acct, a.session, path)
    if not live:
        where = f" with cwd {path}" if path else ""
        others = prov.find_live_sessions(acct, a.session) if path else []
        hint = (f" (a live {a.session!r} exists at {others[0].get('cwd')})"
                if others else "")
        sys.exit(f"no LIVE session named {a.session!r}{where} on {label!r}{hint}"
                 f" — keep the session's terminal open, then re-run.")
    if not path:
        path = norm_path(live[0].get("cwd"))

    if a.reset == "auto":
        reset_at, info = prov.session_reset_at(acct)
        if not reset_at:
            sys.exit(f"couldn't auto-detect reset time ({info}) — pass "
                     f"--reset HH:MMam explicitly.")
        log(f"detected session reset for {label}: {reset_at:%a %H:%M} ({info})")
        if reset_at <= datetime.now():
            sys.exit(f"detected reset {reset_at:%H:%M} is already past — the "
                     f"account is usable now; just continue the session yourself.")
    else:
        try:
            reset_at = parse_clock(a.reset)
        except ValueError as e:
            sys.exit(f"✗ {e}")

    continue_at = reset_at + timedelta(minutes=a.delay)
    try:
        followup_at = parse_followup(a.follow_up, continue_at)
    except ValueError as e:
        sys.exit(f"✗ {e}")

    message = continue_message(getattr(a, "query", None), a.message)
    actions = [{"kind": "continue", "at": continue_at.isoformat(timespec="seconds"),
                "message": message, "retries": 0, "done": False}]
    if followup_at:
        actions.append({"kind": "follow-up",
                        "at": followup_at.isoformat(timespec="seconds"),
                        "message": a.follow_up_message, "retries": 0, "done": False})

    entries = [e for e in load_registry()
               if not (e["account"] == label and e["session"] == a.session
                       and e.get("path") == path)]
    entries.append({
        "provider": acct["provider"],
        "account": label, "session": a.session, "path": path,
        "reset_at": reset_at.isoformat(timespec="seconds"),
        "delay": a.delay,
        "actions": actions,
        "expires_at": (datetime.now() + timedelta(hours=a.ttl)
                       ).isoformat(timespec="seconds"),
    })
    save_registry(entries)
    plan = f"continue @ {continue_at:%a %H:%M}"
    if followup_at:
        plan += f", follow-up @ {followup_at:%a %H:%M}"
    log(f"added {label}/{a.session} ({path}): {plan} (expires in {a.ttl}h)")
    ensure_daemon()


def cmd_detect(a):
    acct = resolve_account(a.account)
    dt, info = provider_of(acct).session_reset_at(acct)
    if a.json:
        print(json.dumps({"account": acct["label"], "provider": acct["provider"],
                          "reset_at": dt.strftime("%Y-%m-%dT%H:%M") if dt else None,
                          "info": info}))
    elif dt:
        print(f"{acct['label']}: session resets {dt:%a %d %b %H:%M} ({info})")
    else:
        print(f"{acct['label']}: {info}")


def cmd_list(_a):
    entries = load_registry()
    if not entries:
        print("no pending tasks")
    for e in entries:
        acts = ", ".join(
            f"{x['kind']} @ {x['at'][:16].replace('T', ' ')}"
            + (" ✓" if x["done"] else (f" (retry {x['retries']})" if x["retries"] else ""))
            for x in e["actions"])
        title = (f"{e['account']}  (account anchor)" if e.get("kind") == "account-anchor"
                 else f"{e['account']}/{e['session']}  [{e.get('path', '?')}]")
        print(f"{title}\n"
              f"  {e.get('provider', '?')} — reset "
              f"{e['reset_at'][:16].replace('T', ' ')} — {acts}")
    try:
        pid = int(open(PIDFILE).read().strip())
        os.kill(pid, 0)
        print(f"daemon: running (pid {pid})")
    except Exception:
        if entries and any(not x["done"] for e in entries for x in e["actions"]):
            print("daemon: NOT running — run any `agentctl renew add` or "
                  "`agentctl renew start` to revive it")
        else:
            print("daemon: not running (nothing pending)")


def cmd_remove(a):
    entries = load_registry()
    if a.session == "all":
        kept = []
    else:
        kept = [e for e in entries
                if e["session"] != a.session
                or (a.account and e["account"] != a.account)]
    save_registry(kept)
    n = len(entries) - len(kept)
    log(f"removed {n} task(s)" + ("" if n else f" — nothing matched {a.session!r}"))


def cmd_start(_a):
    if any(not x["done"] for e in load_registry() for x in e["actions"]):
        ensure_daemon()
    else:
        print("nothing pending — daemon not needed")


def cmd_log(a):
    if not os.path.exists(LOG):
        print("no log yet")
        return
    if a.follow:
        subprocess.run(["tail", "-f", LOG])
    else:
        subprocess.run(["tail", "-30", LOG])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    migrate_legacy()
    if not argv and sys.stdin.isatty():
        cmd_wizard()      # bare `agentctl renew` -> interactive, validated setup
        return 0
    ap = argparse.ArgumentParser(
        prog="agentctl renew",
        description="Resume interrupted sessions the moment their limit renews "
                    "(Claude Code and Codex). Run with no arguments for the "
                    "interactive setup wizard.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="register a session to auto-continue at reset")
    p.add_argument("account", help="account label (agentctl accounts)")
    p.add_argument("session", help="session name (its /rename alias)")
    p.add_argument("--path", help="project path the session must live in "
                                  "(default: taken from the live session)")
    p.add_argument("--reset", default="auto",
                   help="'auto' (ask the provider, default) or HH:MMam")
    p.add_argument("--delay", type=int, default=5, metavar="MIN",
                   choices=DELAY_CHOICES,
                   help="minutes after reset to send the continue "
                        "(0-30 in steps of 5; default 5; 0 = immediately)")
    p.add_argument("--follow-up", default="+5h5m", metavar="WHEN",
                   help="anchor message: '+5h5m' after continue (default), "
                        "a clock time like 10:50am, or 'off'")
    p.add_argument("--query", metavar="TEXT",
                   help="extra instruction appended to the default continue "
                        'message, e.g. --query "Continue, and consider ..."')
    p.add_argument("--message",
                   help="full verbatim continue message (replaces the default "
                        "and --query)")
    p.add_argument("--follow-up-message", default=DEFAULT_FOLLOWUP,
                   help="custom follow-up message")
    p.add_argument("--ttl", type=int, default=24, metavar="HOURS",
                   help="task lifetime (default 24h)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("setup", help="interactive setup wizard (same as no args)")
    p.set_defaults(fn=cmd_wizard)

    p = sub.add_parser("detect", help="print an account's session reset time")
    p.add_argument("account", help="account label")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_detect)

    p = sub.add_parser("list", help="show pending tasks and daemon state")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("remove", help="cancel pending task(s)")
    p.add_argument("session", help="session name, or 'all'")
    p.add_argument("--account", help="restrict to one account")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("start", help="revive the daemon if tasks are pending")
    p.set_defaults(fn=cmd_start)

    p = sub.add_parser("log", help="show the activity log")
    p.add_argument("-f", "--follow", action="store_true")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("run-daemon", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_run_daemon)

    a = ap.parse_args(argv)
    rc = a.fn(a)
    return 0 if rc is None else rc


if __name__ == "__main__":
    sys.exit(main())
