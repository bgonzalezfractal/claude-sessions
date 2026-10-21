"""
agentctl copy — migrate a session between accounts, quickly, by NAME.

A thin front-end over bin/claude-migrate-session.sh (see docs/session-migration.md
for what gets copied and the gotchas). You give labels and the session's /rename
title instead of config dirs and uuids:

    agentctl copy bggai default radal-v2            # copy + print resume line
    agentctl copy bggai default radal-v2 --dry-run
    agentctl copy --list bggai [--json]             # sessions on an account

Claude only for now: the migration script rewrites Claude's projects/ +
transcript layout. `--list` works for a Codex account too (its live sessions),
so the UI can show them, but copying between Codex accounts is not supported.

Reminders baked in (from docs/session-migration.md):
  * a copy is a FORK — exit the source session first or they diverge;
  * turn off auto-compact on the destination before resuming a big transcript;
  * resume from the cwd the script prints.
"""
import argparse, glob, json, os, re, subprocess, sys

from agentctl import providers

HOME = os.path.expanduser("~")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
MIGRATE = os.path.join(REPO_ROOT, "bin", "claude-migrate-session.sh")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def all_accounts():
    """{label: account} across providers."""
    try:
        found = providers.discover_accounts()
    except Exception:
        found = []
        for p in providers.PROVIDERS:
            try:
                found.extend(providers.module(p).discover())
            except Exception:
                pass
    return {a["label"]: a for a in found}


def claude_dirs(accounts):
    """{label: config_dir} for the claude accounts only — what copy can touch."""
    return {label: a["config_dir"] for label, a in accounts.items()
            if a["provider"] == "claude"}


def sessions_on(cfg_dir):
    """All known Claude sessions: {sid: {title, project, cwd, live, mtime}}."""
    out = {}
    for f in glob.glob(os.path.join(cfg_dir, "projects", "*", "*", "custom-title.json")):
        sid_dir = os.path.dirname(f)
        sid = os.path.basename(sid_dir)
        if not UUID_RE.match(sid):
            continue  # .bak-stamped copies etc.
        try:
            title = json.load(open(f)).get("customTitle", "").strip('"')
        except Exception:
            continue
        t = os.path.join(os.path.dirname(sid_dir), sid + ".jsonl")
        out[sid] = {"title": title,
                    "project": os.path.basename(os.path.dirname(sid_dir)),
                    "live": False, "pid": None, "cwd": None,
                    "mtime": os.path.getmtime(t) if os.path.exists(t) else 0}
    for f in glob.glob(os.path.join(cfg_dir, "sessions", "*.json")):
        try:
            j = json.load(open(f))
            os.kill(j["pid"], 0)
        except Exception:
            continue
        sid = j.get("sessionId")
        rec = out.setdefault(sid, {"title": j.get("name", ""), "project": "",
                                   "mtime": 0})
        rec.update(live=True, pid=j["pid"], cwd=j.get("cwd"),
                   title=j.get("name") or rec.get("title", ""))
    return out


def codex_sessions(account):
    """Live Codex sessions in the same row shape, so the UI can list them."""
    rows = []
    try:
        live = providers.provider_of(account).live_sessions(account)
    except Exception as e:
        print(f"could not read Codex sessions: {e}", file=sys.stderr)
        return rows
    for s in live:
        rows.append({"sid": s.get("id"), "name": s.get("name"),
                     "title": s.get("name"), "project": "", "live": True,
                     "pid": s.get("pid"), "cwd": s.get("cwd"),
                     "mtime": s.get("started") or 0})
    return rows


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def resolve(cfg_dir, ref, path=None):
    if UUID_RE.match(ref):
        return ref
    hits = {sid: s for sid, s in sessions_on(cfg_dir).items()
            if s.get("title") == ref}
    if path:  # origin path filter: live cwd or the transcript's project key
        p = os.path.realpath(os.path.expanduser(path))
        hits = {sid: s for sid, s in hits.items()
                if s.get("cwd") == p or s.get("project") == slug(p)}
    if not hits:
        where = f" under {path}" if path else ""
        sys.exit(f"no session titled {ref!r}{where} on that account — "
                 f"try `agentctl copy --list <label>`")
    if len(hits) > 1:
        # newest transcript wins, but say so
        sid = max(hits, key=lambda k: hits[k]["mtime"])
        print(f"note: {len(hits)} sessions titled {ref!r}; using the most recent "
              f"({sid}). Others: "
              + ", ".join(k for k in hits if k != sid), file=sys.stderr)
        return sid
    return next(iter(hits))


def cmd_list(label, as_json, accounts):
    account = accounts.get(label)
    if account is None:
        print(f"unknown account {label!r} — one of: {', '.join(accounts)}",
              file=sys.stderr)
        return 2
    if account["provider"] != "claude":
        rows = codex_sessions(account)
        if as_json:
            print(json.dumps(rows, indent=1))
        else:
            for s in rows:
                print(f"LIVE {(s.get('title') or '(untitled)'):<28} {s.get('sid') or '':<38}"
                      f"  {s.get('cwd') or ''}")
            if not rows:
                print("no live sessions")
        return 0
    sess = sessions_on(account["config_dir"])
    rows = sorted(sess.items(), key=lambda kv: -kv[1]["mtime"])
    if as_json:
        print(json.dumps([{"sid": k, **v} for k, v in rows], indent=1))
    else:
        for sid, s in rows:
            mark = "LIVE " if s["live"] else "     "
            print(f"{mark}{s.get('title') or '(untitled)':<28} {sid}  "
                  f"{s.get('project', '')}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agentctl copy",
        description="Copy a session between accounts by label + session title.")
    ap.add_argument("--list", metavar="LABEL", help="list sessions on an account")
    ap.add_argument("--json", action="store_true", help="with --list: JSON output")
    ap.add_argument("src", nargs="?", help="source account label")
    ap.add_argument("dst", nargs="?", help="destination account label")
    ap.add_argument("session", nargs="?", help="session title (or uuid)")
    ap.add_argument("--path", help="origin path — picks the session living there "
                                   "when a title exists in several projects")
    ap.add_argument("--to-path", help="destination path — re-home the copy into "
                                      "a different working directory")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    accounts = all_accounts()
    claude = claude_dirs(accounts)

    if a.list:
        return cmd_list(a.list, a.json, accounts)

    if not (a.src and a.dst and a.session):
        ap.error("need: <src-label> <dst-label> <session-title-or-uuid> "
                 "(or --list <label>)")
    for label in (a.src, a.dst):
        if label in accounts and accounts[label]["provider"] != "claude":
            print("session copy between Codex accounts is not supported yet",
                  file=sys.stderr)
            return 2
        if label not in claude:
            print(f"unknown account {label!r} — one of: {', '.join(claude)}",
                  file=sys.stderr)
            return 2
    if a.src == a.dst:
        print("src and dst are the same account", file=sys.stderr)
        return 2

    sid = resolve(claude[a.src], a.session, a.path)
    cmd = [MIGRATE, claude[a.src], claude[a.dst], sid]
    if a.dry_run:
        cmd.append("--dry-run")
    if a.to_path:
        p = os.path.realpath(os.path.expanduser(a.to_path))
        if not os.path.isdir(p):
            print(f"--to-path is not a directory: {p}", file=sys.stderr)
            return 2
        cmd += ["--dst-cwd", p]
    r = subprocess.run(cmd)
    if r.returncode == 0 and not a.dry_run:
        print("\nReminders: exit the source session (a copy is a fork); "
              "consider \"autoCompactEnabled\": false on the destination "
              "before resuming a large transcript.")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
