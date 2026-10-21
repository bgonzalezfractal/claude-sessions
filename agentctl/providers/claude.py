"""Claude Code provider (Anthropic `claude`, claude.ai OAuth login).

Verified live against the Claude Code CLI:
  * Config dir: ~/.claude (the default account) or ~/.claude-<label>, selected
    with CLAUDE_CONFIG_DIR. The DEFAULT account must run WITHOUT that variable
    set — with it, the CLI looks for a hashed keychain entry that does not
    exist (the default-account gotcha).
  * Credentials live in the macOS keychain under the CLI's own entries:
    default -> service "Claude Code-credentials"; any other config dir ->
    "Claude Code-credentials-<first 8 hex of sha256(config dir path)>". The
    payload is {"claudeAiOauth": {"accessToken", "expiresAt" (epoch ms), ...}}.
  * Usage: GET https://api.anthropic.com/api/oauth/usage with the bearer token
    plus `anthropic-beta: oauth-2025-04-20` / `anthropic-version: 2023-06-01`.
    Same read-only endpoint the /status Usage tab uses: costs nothing and does
    NOT anchor a usage window. Three limits per account: session (5 h), weekly
    (all models), weekly-scoped (e.g. Fable).
  * 429 is deliberately never retried — hammering the rate limit extends it
    (learned 2026-09-07). The interactive /status Usage tab keeps working
    during a 429 because the TUI has its own data path, so the fallback drives
    a headless `claude --safe-mode --ax-screen-reader` through
    /status -> right -> right in a pty and parses the panel. Those numbers
    carry no exact reset timestamp, only the human "Resets ..." text.
  * `claude auth status` only reports authentication — it is NOT a token
    refresh trigger. The only non-interactive way to renew is to open the
    interactive /status terminal (which the recovery path does, without ever
    sending a model prompt or accepting a trust dialog).
  * Sessions: <config_dir>/sessions/<pid>.json with {name, cwd, pid,
    sessionId, startedAt}. A session is LIVE when that pid is alive; it is
    addressed by its /rename alias.
  * Messaging a live session: a headless `claude -p --model haiku
    --allowedTools ListAgents,SendMessage` helper on the SAME account (peer
    visibility is per config dir). The prompt goes in on stdin because
    --allowedTools is variadic.
  * Cheapest window-opening request: a throwaway `claude -p --model haiku`
    answering "Hello, only answer hello" — never the working session, which
    runs on its own (expensive) model.
"""
import fcntl, glob, hashlib, json, os, pty, re, select, struct, subprocess
import termios, threading, time
import urllib.error, urllib.request
from datetime import datetime

HOME = os.path.expanduser("~")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/opt/homebrew/bin/claude")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
ANCHOR_PROMPT = "Hello, only answer hello"
LIMIT_RE = re.compile(r"hit your session limit", re.I)

# 429 is deliberately NOT retried: it means we're checking too often, and
# hammering a rate limit extends it (learned 2026-09-07 when the UI's 60 s
# auto-poll × 3 accounts × 3 retries got every token throttled).
RETRY_CODES = {408, 425, 500, 502, 503, 504}
BACKOFF = (0.4, 1.2, 3.0)          # one entry per retry after the first attempt
CLOCK_SKEW = 60                    # treat a token expiring within a minute as expired


# ---------------------------------------------------------------- accounts ---
def discover():
    """default + every ~/.claude-* dir that has logged in (has .claude.json)."""
    accounts = [{"provider": "claude", "label": "default",
                 "config_dir": os.path.join(HOME, ".claude"),
                 "suffix_dir": None, "shell_alias": "claude"}]
    for d in sorted(glob.glob(os.path.join(HOME, ".claude-*"))):
        if os.path.isfile(os.path.join(d, ".claude.json")):
            label = os.path.basename(d)[len(".claude-"):]
            accounts.append({"provider": "claude", "label": label,
                             "config_dir": d, "suffix_dir": d,
                             "shell_alias": f"claude-{label}"})
    return accounts


def env(account):
    """The default account must run WITHOUT CLAUDE_CONFIG_DIR (keychain gotcha)."""
    e = dict(os.environ)
    e.pop("CLAUDE_CONFIG_DIR", None)
    if account.get("suffix_dir") is not None:
        e["CLAUDE_CONFIG_DIR"] = account["suffix_dir"]
    return e


def login_hint(account):
    label = account["label"]
    return "claude auth login" if label == "default" else f"claude-{label} auth login"


def resume_hint(account, session_name):
    return f"{account['shell_alias']} --resume  # then pick {session_name!r}"


# ---------------------------------------------------------------- keychain ---
def keychain_service(suffix_dir):
    if suffix_dir is None:
        return "Claude Code-credentials"
    h = hashlib.sha256(suffix_dir.encode()).hexdigest()[:8]
    return f"Claude Code-credentials-{h}"


def read_oauth(suffix_dir):
    """-> (payload dict, None) | (None, reason)."""
    r = subprocess.run(["security", "find-generic-password", "-s",
                        keychain_service(suffix_dir), "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, "no keychain entry — log in with this account once"
    try:
        return json.loads(r.stdout)["claudeAiOauth"], None
    except Exception as e:
        return None, f"bad keychain payload ({e})"


def read_token(account):
    """Just the access token for this account, or None."""
    payload, _ = read_oauth(account.get("suffix_dir"))
    return (payload or {}).get("accessToken")


def expiry_state(payload):
    """-> (is_expired, human note). expiresAt is epoch milliseconds."""
    exp = (payload or {}).get("expiresAt")
    if not exp:
        return False, ""
    delta = exp / 1000 - time.time()
    if delta > CLOCK_SKEW:
        return False, ""
    return True, f"access token expired {human_delta(-delta)} ago"


def human_delta(secs):
    secs = max(0, int(secs))
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


# ------------------------------------------------------------------- state ---
def get_email(cfg_dir):
    # The default account's live config is ~/.claude.json, NOT ~/.claude/.claude.json
    # (that one can be a stale migration artifact) — see docs/session-migration.md.
    path = (os.path.join(HOME, ".claude.json")
            if cfg_dir == os.path.join(HOME, ".claude")
            and os.path.isfile(os.path.join(HOME, ".claude.json"))
            else cfg_dir + "/.claude.json")
    try:
        j = json.load(open(path))
        return (j.get("oauthAccount") or {}).get("emailAddress", "?")
    except Exception:
        return "?"


def refresh_token(cfg_dir, suffix_dir):
    """Initialize Claude's interactive OAuth path, then verify keychain renewal."""
    before, _ = read_oauth(suffix_dir)
    tui_status(cfg_dir, suffix_dir)
    after, _ = read_oauth(suffix_dir)
    if after and not expiry_state(after)[0] and (
            not before or after.get("accessToken") != before.get("accessToken")):
        return "interactive /status"
    return None


# --------------------------------------------------- TUI fallback (no API) ---
# The interactive /status Usage tab keeps working even when the oauth/usage
# endpoint is rate-limited (verified 2026-09-07 during a 429 episode): the TUI
# has its own data path. So when the API throttles us, we drive a headless
# `claude` through /status -> right -> right in a pty and parse the panel.
TUI_KINDS = {"session": "session",
             "week (all models)": "weekly_all",
             "week (Fable)": "weekly_scoped"}


def suffix_for_default(cfg_dir):
    return cfg_dir == os.path.join(HOME, ".claude")


def _live_cwd(cfg_dir):
    """A trusted cwd for this account: any live session's, else $HOME."""
    for f in glob.glob(os.path.join(cfg_dir, "sessions", "*.json")):
        try:
            j = json.load(open(f))
            os.kill(j["pid"], 0)
            if os.path.isdir(j.get("cwd") or ""):
                return j["cwd"]
        except Exception:
            pass
    config = (os.path.join(HOME, ".claude.json") if suffix_for_default(cfg_dir)
              else os.path.join(cfg_dir, ".claude.json"))
    try:
        with open(config) as f:
            projects = json.load(f).get("projects", {})
        for cwd, project in projects.items():
            if project.get("hasTrustDialogAccepted") and os.path.isdir(cwd):
                return cwd
    except (OSError, ValueError):
        pass
    return os.path.expanduser("~")


TUI_LOCK = threading.Lock()


def parse_tui(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[78]|\x1b\(B", "", text)
    text = re.sub(r"\s+", " ", text)
    found = {}
    heading = r"Current (session|week \(all models\)|week \((?:Fable|Sonnet|Opus)\))"
    for m in re.finditer(heading + r"(.*?)(?=" + heading + r"|$)", text, re.I):
        values = list(re.finditer(r"(\d{1,3})%\s*used(?:\s*Resets\s*([^()]{1,100})\s*\()?", m.group(2), re.I))
        if not values:
            continue
        value = values[-1]  # screen-reader redraws can omit the heading
        title = m.group(1).lower()
        kind = "session" if title == "session" else "weekly_all" if "all models" in title else "weekly_scoped"
        row = {"kind": kind, "percent": int(value.group(1)),
               "reset_text": "resets " + value.group(2).strip() if value.group(2) else ""}
        if kind == "weekly_scoped":
            row["scope"] = {"model": {"display_name": m.group(1).split("(")[1].rstrip(")")}}
        found[(kind, title)] = row
    return list(found.values()) or None


def tui_status(cfg_dir, suffix_dir, boot_wait=10):
    """Drive /status → right → right. Never send a model prompt or accept trust.

    Use a sized PTY and Popen rather than forking Python inside worker threads.
    Serialize scratch terminals and always reap the child.
    """
    with TUI_LOCK:
        child_env = dict(os.environ, TERM="xterm-256color")
        child_env.pop("CLAUDE_CONFIG_DIR", None)
        if suffix_dir is not None:
            child_env["CLAUDE_CONFIG_DIR"] = suffix_dir
        fd, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        child = None
        buf = bytearray()

        def drain(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if select.select([fd], [], [], 0.2)[0]:
                    try:
                        data = os.read(fd, 65536)
                        if not data:
                            break
                        buf.extend(data)
                    except OSError:
                        break

        try:
            child = subprocess.Popen(
                [CLAUDE_BIN, "--safe-mode", "--ax-screen-reader"],
                stdin=slave, stdout=slave, stderr=slave,
                cwd=_live_cwd(cfg_dir), env=child_env, start_new_session=True)
            os.close(slave)
            slave = None
            drain(boot_wait)
            if b"Enter y/n" in buf or b"Yes, I trust this folder" in buf:
                return None
            for keys, wait in [(b"/status\r", 3), (b"\x1b[C", 1.2), (b"\x1b[C", 5)]:
                if child.poll() is not None:
                    break
                os.write(fd, keys)
                drain(wait)
            return parse_tui(buf.decode(errors="replace"))
        except (OSError, subprocess.SubprocessError):
            return None
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait()
            os.close(fd)
            if slave is not None:
                os.close(slave)


# ------------------------------------------------------------------- fetch ---
def _get(token):
    req = urllib.request.Request(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}",
                 "anthropic-beta": "oauth-2025-04-20",
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_usage(token, attempts=3):
    """-> (usage, retries). Retries transient faults; 401/403 raise immediately."""
    for i in range(attempts):
        try:
            return _get(token), i
        except urllib.error.HTTPError as e:
            err, transient = e, e.code in RETRY_CODES
        except OSError as e:                 # URLError, connection reset, timeout
            err, transient = e, True
        # `raise` alone would run outside the handler, where there is no active
        # exception — re-raise the captured one.
        if not transient or i == attempts - 1:
            raise err
        time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])


def _norm_limit(l):
    """Contract keys on every row, keeping whatever else the API sent (scope …)."""
    row = dict(l)
    row["kind"] = l.get("kind") or l.get("type") or "unknown"
    try:
        row["percent"] = int(round(float(l.get("percent") or 0)))
    except (TypeError, ValueError):
        row["percent"] = 0
    row["resets_at"] = l.get("resets_at") or l.get("resetsAt")
    row["is_active"] = bool(l.get("is_active"))
    return row


def usage(account, interactive=False):
    """Shared recovery for text, JSON, the UI and renewal detection."""
    cfg_dir, suffix_dir = account["config_dir"], account.get("suffix_dir")
    result = {"email": get_email(cfg_dir)}
    if interactive:
        rows = tui_status(cfg_dir, suffix_dir)
        return {**result, "limits": rows, "source": "tui"} if rows else {
            **result,
            "error": f"interactive /status unavailable — try {account['shell_alias']} manually"}
    payload, err = read_oauth(suffix_dir)
    if not payload:
        return {**result, "error": err}
    recovered, retries = False, 0
    expired, why = expiry_state(payload)
    if expired:
        recovered = bool(refresh_token(cfg_dir, suffix_dir))
        payload, err = read_oauth(suffix_dir)
        if not payload or expiry_state(payload)[0]:
            return {**result, "error": f"{why}; interactive recovery did not renew it "
                                       f"— run `{login_hint(account)}`"}
    try:
        api, retries = fetch_usage(payload["accessToken"])
    except urllib.error.HTTPError as e:
        if e.code == 401 and not expired:
            recovered = bool(refresh_token(cfg_dir, suffix_dir))
            payload, err = read_oauth(suffix_dir)
            if recovered and payload:
                try:
                    api, retries = fetch_usage(payload["accessToken"])
                except Exception as retry_error:
                    return {**result, "error": f"usage check after renewal failed: {retry_error}"}
            else:
                return {**result, "error": "HTTP 401; interactive recovery did not "
                                           f"renew it — run `{login_hint(account)}`"}
        elif e.code == 429:
            rows = tui_status(cfg_dir, suffix_dir)
            if rows:
                return {**result, "limits": rows, "source": "tui"}
            return {**result, "error": "rate-limited (429); interactive /status "
                                       "unavailable — wait a few minutes"}
        else:
            return {**result, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {**result, "error": str(e)}
    result.update(api)
    result["limits"] = [_norm_limit(l) for l in (api.get("limits") or [])]
    result["source"] = "api"
    if recovered:
        result["refreshed"] = True
    if retries:
        result["retries"] = retries
    return result


def session_reset_at(account):
    """Exact moment the 5-hour session window resets (naive local), or None."""
    u = usage(account)
    if "error" in u:
        return None, u["error"]
    if u.get("source") == "tui":
        return None, ("usage API rate-limited; interactive status has no exact "
                      "reset timestamp — use --reset HH:MM")
    for l in u.get("limits", []):
        if l.get("kind") == "session":
            iso = l.get("resets_at")
            if not iso:
                return None, "no active session window"
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
            info = f"{l.get('percent', '?')}% used" + \
                   (", LIMITED" if l.get("is_active") else "")
            return dt.replace(tzinfo=None), info
    return None, "no session limit in response"


# ---------------------------------------------------------------- sessions ---
def _norm(p):
    return os.path.realpath(os.path.expanduser(p)) if p else None


def live_sessions(account):
    """Every live session on the account, newest first."""
    sess_dir = os.path.join(account["config_dir"], "sessions")
    out = []
    if not os.path.isdir(sess_dir):
        return out
    for fn in os.listdir(sess_dir):
        if not fn.endswith(".json"):
            continue
        try:
            j = json.load(open(os.path.join(sess_dir, fn)))
            os.kill(j["pid"], 0)
        except Exception:
            continue
        out.append({"name": j.get("name") or "(unnamed)", "cwd": j.get("cwd"),
                    "pid": j["pid"], "id": j.get("sessionId"),
                    "started": j.get("startedAt", 0)})
    return sorted(out, key=lambda s: -s["started"])


def find_live_sessions(account, name, path=None):
    """All live sessions with this name (and this real cwd, when path is given)."""
    hits = []
    for s in live_sessions(account):
        if s["name"] != name:
            continue
        if path and _norm(s.get("cwd")) != _norm(path):
            continue
        hits.append(s)
    return hits


# --------------------------------------------------------------- messaging ---
def helper_prompt(session_name, message):
    return (f"You are a scheduling helper. Use ListAgents to find the local session "
            f"named '{session_name}', then use SendMessage to send it exactly this "
            f"message: {json.dumps(message)}. Then output DONE. If no session with "
            f"that name is listed, output NOT-FOUND plus the names you saw. "
            f"Do nothing else.")


def run_helper(account, cwd, prompt_text, timeout=300):
    """-> (rc == 0, combined output). The prompt goes in on stdin because
    --allowedTools is variadic."""
    cmd = [CLAUDE_BIN, "-p", "--model", "haiku",
           "--allowedTools", "ListAgents,SendMessage"]
    try:
        r = subprocess.run(cmd, input=prompt_text, text=True, env=env(account),
                           cwd=cwd if cwd and os.path.isdir(cwd) else HOME,
                           capture_output=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out.strip()
    except subprocess.TimeoutExpired:
        return False, "helper timed out"
    except OSError as e:
        return False, str(e)


def send_message(account, name, cwd, text, timeout=300):
    live = find_live_sessions(account, name)
    if not live:
        return "not-live", f"session {name!r} not live"
    ok, out = run_helper(account, cwd or live[0].get("cwd"),
                         helper_prompt(name, text), timeout=timeout)
    tail = out[-200:].replace("\n", " ")
    if ok and "DONE" in out:
        return "sent", tail
    if LIMIT_RE.search(out):
        return "limited", tail
    if "NOT-FOUND" in out:
        return "not-live", tail
    return "failed", tail


def anchor_ping(account, cwd=None, timeout=240):
    """A separate throwaway haiku one-shot: any request opens the window, and
    the working session (on its own expensive model) is left alone."""
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", "haiku"],
                           input=ANCHOR_PROMPT, text=True, env=env(account),
                           cwd=cwd if cwd and os.path.isdir(cwd) else HOME,
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "failed", "anchor ping timed out"
    except OSError as e:
        return "failed", str(e)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if LIMIT_RE.search(out):
        return "limited", out
    return ("ok" if r.returncode == 0 else "failed"), out
