#!/bin/zsh
# Symlink the claude-sessions CLIs into /opt/homebrew/bin (idempotent).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
for tool in claude-status claude-renew claude-copy-session claude-account claude-log claude-sessions-ui; do
    chmod +x "$DIR/bin/$tool"
    ln -sf "$DIR/bin/$tool" "/opt/homebrew/bin/$tool"
    echo "linked /opt/homebrew/bin/$tool -> $DIR/bin/$tool"
done
