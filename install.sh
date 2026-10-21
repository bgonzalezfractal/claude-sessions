#!/bin/zsh
# Install agentctl: one symlink, the launchd anchor job, and cleanup of the
# pre-rename claude-* installation. Idempotent — safe to re-run after edits.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
BINDIR="/opt/homebrew/bin"
STATE="$HOME/.agentctl"

# ------------------------------------------------------------- the command ---
chmod +x "$DIR/bin/agentctl" "$DIR/bin/anchor.sh" "$DIR/bin/claude-migrate-session.sh"
ln -sf "$DIR/bin/agentctl" "$BINDIR/agentctl"
echo "linked $BINDIR/agentctl -> $DIR/bin/agentctl"

# ------------------------------------------- retire the old claude-* CLIs ----
# Only remove symlinks that point back into THIS repo; anything else is the
# user's own and stays untouched.
for old in claude-status claude-renew claude-copy-session claude-account claude-log claude-sessions-ui; do
    link="$BINDIR/$old"
    if [[ -L "$link" ]] && [[ "$(readlink "$link")" == *"$DIR"* ]]; then
        rm -f "$link"
        echo "removed old symlink $link"
    fi
done

for legacy in "$HOME/.claude/claude-status.py" "$HOME/.claude/limit-reset.sh"; do
    if [[ -e "$legacy" || -L "$legacy" ]]; then
        rm -f "$legacy"
        echo "removed legacy file $legacy"
    fi
done

# The old ~/.zshrc alias + its two comment lines. Exact-match only, backed up.
python3 - "$HOME/.zshrc" <<'PY'
import os, shutil, sys
path = sys.argv[1]
if not os.path.isfile(path):
    print("no ~/.zshrc — nothing to clean")
    sys.exit(0)
ALIAS = "alias claude-status='python3 $HOME/.claude/claude-status.py'"
COMMENTS = ("# All three accounts' usage limits", "# read-only via the /status")
lines = open(path).read().splitlines(keepends=True)
drop = set()
for i, line in enumerate(lines):
    if line.strip() != ALIAS:
        continue
    drop.add(i)
    for j in (i - 1, i - 2):                    # the two comment lines above it
        if j >= 0 and lines[j].strip().startswith(COMMENTS):
            drop.add(j)
if not drop:
    print("~/.zshrc: no legacy claude-status alias found")
    sys.exit(0)
shutil.copy2(path, os.path.expanduser("~/.zshrc.agentctl.bak"))
with open(path, "w") as f:
    f.writelines(l for i, l in enumerate(lines) if i not in drop)
print(f"~/.zshrc: removed {len(drop)} legacy line(s) (backup: ~/.zshrc.agentctl.bak)")
PY

# --------------------------------------------------- renew state migration ---
mkdir -p "$STATE"
OLD_REG="$HOME/.claude/claude-renew/registry.json"
if [[ -f "$OLD_REG" && ! -f "$STATE/renew/registry.json" ]]; then
    mkdir -p "$STATE/renew"
    cp "$OLD_REG" "$STATE/renew/registry.json"
    [[ -f "$HOME/.claude/claude-renew/renew.log" ]] && \
        cp "$HOME/.claude/claude-renew/renew.log" "$STATE/renew/renew.log"
    echo "migrated renew state -> $STATE/renew/ (originals left in place)"
fi

# ---------------------------------------------------------- the anchor job ---
# launchd must not follow a symlink into Documents (macOS privacy protection),
# so install a regular copy — atomically, in case the job is mid-run.
ANCHOR_TMP=$(mktemp "$STATE/anchor.sh.XXXXXX")
cp "$DIR/bin/anchor.sh" "$ANCHOR_TMP"
chmod +x "$ANCHOR_TMP"
mv -f "$ANCHOR_TMP" "$STATE/anchor.sh"
echo "installed regular copy $STATE/anchor.sh (manual fallback; re-run install.sh after edits)"

PLIST="$HOME/Library/LaunchAgents/com.grail.limitreset.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.grail.limitreset</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-lc</string>
        <string>$BINDIR/agentctl anchor</string>
    </array>

    <!-- Weekdays 7:30, weekends 10:30 (Weekday: 0=Sun … 6=Sat) -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Weekday</key><integer>1</integer>
            <key>Hour</key><integer>7</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>2</integer>
            <key>Hour</key><integer>7</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>3</integer>
            <key>Hour</key><integer>7</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>4</integer>
            <key>Hour</key><integer>7</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>5</integer>
            <key>Hour</key><integer>7</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>6</integer>
            <key>Hour</key><integer>10</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
        <dict>
            <key>Weekday</key><integer>0</integer>
            <key>Hour</key><integer>10</integer>
            <key>Minute</key><integer>30</integer>
        </dict>
    </array>

    <key>StandardErrorPath</key>
    <string>$HOME/.agentctl/anchor.err</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST_EOF
echo "wrote $PLIST (weekdays 7:30, weekends 10:30) -> agentctl anchor"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "reloaded launchd job com.grail.limitreset"

# ----------------------------------------------------------------- summary ---
cat <<SUMMARY

agentctl installed.
  command    $BINDIR/agentctl -> $DIR/bin/agentctl
  anchor     $BINDIR/agentctl anchor  (log: $STATE/anchor.log)
  fallback   $STATE/anchor.sh  (pings only, no catch-up scheduling)
  plist      $PLIST
  state      $STATE/

run \`agentctl status\` to verify
SUMMARY
