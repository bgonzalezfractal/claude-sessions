"""Codex CLI provider (OpenAI `codex`, ChatGPT login).

Verified live 2026-09-14 against codex-cli 0.154.0:
  * Config dir: ~/.codex (or $CODEX_HOME). Login state is ~/.codex/auth.json:
    {"auth_mode", "tokens": {"access_token", "id_token", "refresh_token",
    "account_id"}, "last_refresh"}. There is exactly one account per config
    dir; extra accounts live in ~/.codex-<alias> and run with CODEX_HOME set.
  * Usage: GET https://chatgpt.com/backend-api/wham/usage with the bearer
    access token + `ChatGPT-Account-Id` header. Read-only, anchors nothing.
    rate_limit.primary_window = the 5 h session window (limit_window_seconds
    18000, used_percent, reset_at epoch), secondary_window = the weekly one.
    The same numbers are also written into every session's rollout file as
    `token_count` events (payload.info.rate_limits.primary/secondary with
    resets_at epoch) — the offline fallback when the token is stale.
  * Sessions: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl; names
    (from /rename or the auto title) and cwd are in ~/.codex/state_5.sqlite
    table `threads` (columns id, name, title, cwd, source). A session is LIVE
    when a codex process holds its rollout file open (lsof).
  * Messaging a live session: `codex queue --thread <name|uuid> --message TEXT`
    (built in; no helper model needed). When nothing is live it prints
    "Error: No active session found matching '<name>'" — and exits 0, so the
    text must be checked, not the exit code.
  * `queue` only INSERTS into ~/.codex/queue_1.sqlite and bumps a revision the
    running TUI is meant to notice; the TUI submits the item when it next goes
    idle. A session whose last turn FAILED (usage limit) stops draining the
    queue and stays idle indefinitely — a message queued at 03:47 was still
    unread five hours later (2026-09-14). Delivery therefore has to be
    confirmed against the transcript, and a stuck session needs a keystroke in
    its own terminal; nothing external can unstick it.
  * Cheapest window-opening request: `codex exec --ephemeral
    --skip-git-repo-check -s read-only -C ~ "Hello, only answer hello"`.
  * Limit banner text: "You've hit your usage limit ... try again at 3:28 AM."
"""
import glob, json, os, re, sqlite3, subprocess, time
import urllib.error, urllib.request
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
CODEX_BIN = os.environ.get("CODEX_BIN", "/opt/homebrew/bin/codex")
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ANCHOR_PROMPT = "Hello, only answer hello"
LIMIT_RE = re.compile(r"hit your usage limit|rate limit(?:ed| reached)|usage limit", re.I)


# ---------------------------------------------------------------- accounts ---
def discover():
    accounts = []
    if os.path.isfile(os.path.join(HOME, ".codex", "auth.json")):
        accounts.append({"provider": "codex", "label": "codex",
                         "config_dir": os.path.join(HOME, ".codex"),
                         "suffix_dir": None, "shell_alias": "codex"})
    for d in sorted(glob.glob(os.path.join(HOME, ".codex-*"))):
        if os.path.isfile(os.path.join(d, "auth.json")):
            alias = os.path.basename(d)[len(".codex-"):]
            accounts.append({"provider": "codex", "label": f"codex-{alias}",
                             "config_dir": d, "suffix_dir": d,
                             "shell_alias": f"codex-{alias}"})
    return accounts


def env(account):
    e = dict(os.environ)
    e.pop("CODEX_HOME", None)
    if account.get("suffix_dir"):
        e["CODEX_HOME"] = account["suffix_dir"]
    return e


def _cmd(account, *args):
    """`codex ...` as a list; CODEX_HOME goes through env()."""
    return [CODEX_BIN, *args]


def login_hint(account):
    pre = f"CODEX_HOME={account['suffix_dir']} " if account.get("suffix_dir") else ""
    return f"{pre}codex login"


def resume_hint(account, session_name):
    pre = f"CODEX_HOME={account['suffix_dir']} " if account.get("suffix_dir") else ""
    return f"{pre}codex resume {json.dumps(session_name)}"


# ------------------------------------------------------------------- usage ---
def _auth(account):
    try:
        with open(os.path.join(account["config_dir"], "auth.json")) as f:
            j = json.load(f)
        t = j.get("tokens") or {}
        return t.get("access_token"), t.get("account_id"), j
    except (OSError, ValueError):
        return None, None, {}


def _iso(epoch):
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat().replace("+00:00", "Z")


def _window(kind, w, limit_reached=False):
    pct = int(round(w.get("used_percent") or 0))
    secs = w.get("limit_window_seconds")
    mins = int(secs / 60) if secs else w.get("window_minutes")
    return {"kind": kind, "percent": pct,
            "resets_at": _iso(w.get("reset_at") or w.get("resets_at")),
            "is_active": bool(pct >= 100 and (limit_reached or kind == "session")),
            "window_minutes": mins}


def _fetch(token, account_id):
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "ChatGPT-Account-Id": account_id or "",
        "User-Agent": "agentctl (codex-cli usage check)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _rollout_files(account):
    files = glob.glob(os.path.join(account["config_dir"], "sessions", "*", "*", "*", "rollout-*.jsonl"))
    return sorted(files, key=os.path.getmtime, reverse=True)


def _rollout_rate_limits(account, max_files=3):
    """Newest rate_limits block written by any session, with its timestamp."""
    for path in _rollout_files(account)[:max_files]:
        last = None
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    if '"rate_limits"' in line:
                        last = line
        except OSError:
            continue
        if not last:
            continue
        try:
            j = json.loads(last)
            rl = j["payload"]["info"]["rate_limits"]
            return rl, j.get("timestamp")
        except (ValueError, KeyError, TypeError):
            continue
    return None, None


def usage(account, interactive=False):
    """See providers/__init__.py. `interactive` is accepted for parity; Codex
    has no TUI path, the rollout files are the offline fallback instead."""
    token, account_id, auth = _auth(account)
    result = {"email": "?"}
    if not token:
        return {**result, "error": f"not logged in — run `{login_hint(account)}`"}
    err = None
    try:
        j = _fetch(token, account_id)
        rl = j.get("rate_limit") or {}
        limits = []
        if rl.get("primary_window"):
            limits.append(_window("session", rl["primary_window"], rl.get("limit_reached")))
        if rl.get("secondary_window"):
            limits.append(_window("weekly_all", rl["secondary_window"], rl.get("limit_reached")))
        result.update({"email": j.get("email", "?"), "plan": j.get("plan_type"),
                       "limits": limits, "source": "api"})
        credits = j.get("credits") or {}
        if credits.get("has_credits") or credits.get("unlimited"):
            result["extra_usage"] = {"is_enabled": True, "used_credits": credits.get("balance")}
        return result
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}" + (" (token expired — open `codex` once to refresh it)"
                                  if e.code in (401, 403) else "")
    except (OSError, ValueError) as e:
        err = str(e)
    # Offline fallback: the numbers the last session saw.
    rl, ts = _rollout_rate_limits(account)
    if not rl:
        return {**result, "error": err}
    limits = []
    if rl.get("primary"):
        limits.append(_window("session", rl["primary"]))
    if rl.get("secondary"):
        limits.append(_window("weekly_all", rl["secondary"]))
    return {**result, "limits": limits, "source": "rollout",
            "note": f"usage API failed ({err}); showing what the last session saw at {ts}"}


def session_reset_at(account):
    u = usage(account)
    if "error" in u:
        return None, u["error"]
    for l in u.get("limits", []):
        if l["kind"] == "session":
            if not l.get("resets_at"):
                return None, "no active session window"
            dt = datetime.fromisoformat(l["resets_at"].replace("Z", "+00:00")).astimezone()
            info = f"{l['percent']}% used" + (", LIMITED" if l.get("is_active") else "")
            if u.get("source") == "rollout":
                info += " (from last session's rollout; API unavailable)"
            return dt.replace(tzinfo=None), info
    return None, "no session limit in response"


# ---------------------------------------------------------------- sessions ---
def _threads(account):
    """{id: {name, title, cwd, source, created}} from the state db (read-only)."""
    out = {}
    for db in sorted(glob.glob(os.path.join(account["config_dir"], "state_*.sqlite")), reverse=True):
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = c.execute("SELECT id, name, title, cwd, source, created_at FROM threads").fetchall()
            c.close()
        except sqlite3.Error:
            continue
        for tid, name, title, cwd, source, created in rows:
            out[tid] = {"id": tid, "name": name or "", "title": (title or "").strip(),
                        "cwd": cwd, "source": source, "created": created or 0}
        break
    return out


def _open_rollouts(account):
    """{session_id: pid} for every rollout file a codex process holds open."""
    try:
        r = subprocess.run(["lsof", "-c", "codex", "-F", "pn"], capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    root = os.path.realpath(os.path.join(account["config_dir"], "sessions"))
    out, pid = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and "/rollout-" in line:
            path = line[1:]
            if not os.path.realpath(path).startswith(root):
                continue
            m = re.search(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path)
            if m:
                out[m.group(1)] = pid
    return out


def _display_name(t):
    if t["name"]:
        return t["name"]
    first = t["title"].splitlines()[0] if t["title"] else ""
    return first[:40] or "(unnamed)"


def live_sessions(account):
    """User-facing sessions a codex process currently has open, newest first."""
    threads = _threads(account)
    out = []
    for sid, pid in _open_rollouts(account).items():
        t = threads.get(sid)
        if not t or t["source"] != "cli":        # subagent threads are not addressable
            continue
        out.append({"name": _display_name(t), "cwd": t["cwd"], "pid": pid,
                    "id": sid, "started": t["created"]})
    return sorted(out, key=lambda s: -s["started"])


def _norm(p):
    return os.path.realpath(os.path.expanduser(p)) if p else None


def find_live_sessions(account, name, path=None):
    hits = []
    for s in live_sessions(account):
        if s["name"] != name and s["id"] != name:
            continue
        if path and _norm(s["cwd"]) != _norm(path):
            continue
        hits.append(s)
    return hits


# --------------------------------------------------------------- messaging ---
def send_message(account, name, cwd, text, timeout=60, confirm_seconds=45):
    try:
        r = subprocess.run(_cmd(account, "queue", "--thread", name, "--message", text),
                           env=env(account), cwd=cwd if cwd and os.path.isdir(cwd) else HOME,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "failed", "codex queue timed out"
    except OSError as e:
        return "failed", str(e)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if "No active session" in out:
        return "not-live", out
    if LIMIT_RE.search(out):
        return "limited", out
    if r.returncode != 0 or out.startswith("Error"):
        return "failed", out or f"exit {r.returncode}"
    # `queue` only enqueues; the TUI submits it when idle. Confirm by watching
    # the session's rollout for the text (a user message event) for a while.
    #
    # Once the text IS in the rollout the message was delivered — even if the
    # turn it started then died on the usage limit. Reporting that as "limited"
    # (which it was, until 2026-09-14) makes the caller retry and stack a
    # DUPLICATE instruction onto a session that already has it. Say
    # "sent-limited" instead: delivered, but the work did not happen.
    seen, limited_after = _wait_for_rollout_text(account, name, text,
                                                 seconds=confirm_seconds)
    if seen and limited_after:
        return "sent-limited", (out or "queued") + \
            " — submitted, but the turn hit the usage limit"
    if seen:
        return "sent", (out or "queued") + " — submitted (seen in rollout)"
    # Not picked up. `codex queue` only bumps a revision the TUI is supposed to
    # notice; a session whose last turn died (e.g. on the usage limit) stops
    # draining the queue entirely and sits there — verified live 2026-09-14,
    # where a message queued at 03:47 was still unread at 08:40. Say so, since
    # the only cure is a keystroke in that session's terminal.
    note = " — queued; the TUI submits it when idle"
    if _transcript_idle_since(account, name) > confirm_seconds:
        note = (" — QUEUED BUT NOT PICKED UP: the session has written nothing "
                "since before this message. It is probably stuck after a failed "
                "turn; press a key in its terminal to make it drain the queue.")
    return "sent", (out or "queued") + note


def _transcript_idle_since(account, name):
    """Seconds since the session last wrote anything. 0 when unknown."""
    for s in live_sessions(account):
        if s["name"] == name or s["id"] == name:
            paths = glob.glob(os.path.join(account["config_dir"], "sessions",
                                           "*", "*", "*", f"rollout-*{s['id']}.jsonl"))
            if paths:
                try:
                    return max(0.0, time.time() - os.path.getmtime(paths[0]))
                except OSError:
                    return 0.0
    return 0.0


def _wait_for_rollout_text(account, name, text, seconds=45):
    """Watch the session's transcript for `text` -> (seen, limited_after).

    `seen` is True once the session has actually consumed the queued message.
    `limited_after` is True when a usage-limit error follows it, i.e. the
    message landed but its turn could not run. Both False on timeout."""
    sid = None
    for s in live_sessions(account):
        if s["name"] == name or s["id"] == name:
            sid = s["id"]
            break
    if not sid:
        return False, False
    paths = glob.glob(os.path.join(account["config_dir"], "sessions", "*", "*", "*", f"rollout-*{sid}.jsonl"))
    if not paths:
        return False, False
    path = paths[0]
    try:
        start = os.path.getsize(path)
    except OSError:
        return False, False
    needle = text[:60]
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            with open(path, errors="replace") as f:
                f.seek(start)
                tail = f.read()
        except OSError:
            return False, False
        marker = json.dumps(needle)[1:-1]
        if marker in tail:
            # Give the turn a moment to fail, so a limit error right after the
            # message is attributed to it rather than missed.
            time.sleep(3)
            try:
                with open(path, errors="replace") as f:
                    f.seek(start)
                    tail = f.read()
            except OSError:
                return True, False
            return True, bool(LIMIT_RE.search(tail.split(marker, 1)[1]))
        time.sleep(3)
    return False, False


def anchor_ping(account, cwd=None, timeout=240):
    """Throwaway `codex exec` that opens the window; never touches a session."""
    cmd = _cmd(account, "exec", "--ephemeral", "--skip-git-repo-check",
               "-s", "read-only", "-C", HOME, ANCHOR_PROMPT)
    try:
        r = subprocess.run(cmd, env=env(account), cwd=HOME, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "failed", "anchor ping timed out"
    except OSError as e:
        return "failed", str(e)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if LIMIT_RE.search(out):
        return "limited", out
    return ("ok" if r.returncode == 0 else "failed"), out
