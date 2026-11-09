"""`agentctl update` — pull the latest agentctl into this machine's clone.

`/opt/homebrew/bin/agentctl` is a symlink into the clone, so a fast-forward
of the clone IS the upgrade for almost every change. Only `install.sh` and
`bin/anchor.sh` live on outside it (the launchd plist and the regular copy at
~/.agentctl/anchor.sh), so install.sh is re-run only when one of those changed.

Never destructive: it refuses a clone with uncommitted edits to tracked files
or with local commits the remote doesn't have, and only ever fast-forwards.
"""
import argparse
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(REPO_ROOT, "install.sh")
REINSTALL_PATHS = ("install.sh", "bin/anchor.sh")


def git(*args, check=True):
    r = subprocess.run(["git", "-C", REPO_ROOT, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def daemon_pid():
    from agentctl import renew
    try:
        pid = int(open(renew.PIDFILE).read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="agentctl update",
        description="Fast-forward this machine's agentctl clone to its upstream; "
                    "re-run install.sh when the installed pieces changed.")
    ap.add_argument("--check", action="store_true",
                    help="only report whether an update is available")
    a = ap.parse_args(argv)

    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                       check=False)
        if not upstream:
            print(f"agentctl update: branch {branch!r} in {REPO_ROOT} has no upstream",
                  file=sys.stderr)
            return 1
        git("fetch", "--quiet")
        ahead, behind = (int(n) for n in
                         git("rev-list", "--left-right", "--count", "HEAD...@{u}").split())
        old = git("rev-parse", "--short", "HEAD")
    except (RuntimeError, OSError) as e:
        print(f"agentctl update: {e}", file=sys.stderr)
        return 1

    if behind == 0:
        print(f"agentctl is up to date ({old} on {branch})"
              + (f" — {ahead} local commit(s) not pushed" if ahead else ""))
        return 0
    if a.check:
        print(f"update available: {behind} new commit(s) on {upstream} "
              f"(this machine is at {old})")
        return 0
    if ahead:
        print(f"agentctl update: {REPO_ROOT} has {ahead} local commit(s) not on "
              f"{upstream} — not touching it; rebase or push them first",
              file=sys.stderr)
        return 1
    if git("status", "--porcelain", "--untracked-files=no"):
        print(f"agentctl update: {REPO_ROOT} has uncommitted changes — commit or "
              f"stash them first", file=sys.stderr)
        return 1

    try:
        git("merge", "--ff-only", "--quiet", "@{u}")
        new = git("rev-parse", "--short", "HEAD")
        print(f"updated {old} -> {new} ({behind} commit(s)):")
        print(git("log", "--oneline", "--no-decorate", f"{old}..{new}"))
        changed = git("diff", "--name-only", old, new, "--", *REINSTALL_PATHS)
    except RuntimeError as e:
        print(f"agentctl update: {e}", file=sys.stderr)
        return 1

    rc = 0
    if changed:
        print(f"\n{', '.join(changed.split())} changed — re-running install.sh")
        rc = subprocess.call(["/bin/zsh", INSTALL])
    pid = daemon_pid()
    if pid:
        print(f"\nnote: the renew daemon (pid {pid}) still runs the old code until it "
              f"exits; it does once its queue empties")
    return rc


if __name__ == "__main__":
    sys.exit(main())
