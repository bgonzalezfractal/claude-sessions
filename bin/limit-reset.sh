#!/bin/zsh
# Fires the daily "reset my 5-hour window" ping for every Claude account and
# self-rotates its log.
# Invoked by launchd agent com.grail.limitreset (7:30 Mon-Fri / 10:30 Sat-Sun,
# local time). Lives in the claude-sessions repo; ~/.claude/limit-reset.sh is a
# symlink here.

LOG="$HOME/.claude/cron-limit-reset.log"
MAXBYTES=51200   # 50 KB -> rotate; keeps LOG + LOG.1, ~100 KB ceiling total
CLAUDE_BIN="/opt/homebrew/bin/claude"

# Accounts are DISCOVERED: default + every ~/.claude-* dir that has logged in
# (has a .claude.json). A new `claude-account add` alias joins automatically
# after its first login; wrapper dirs without .claude.json are skipped.
ACCOUNTS=("default:$HOME/.claude")
for d in "$HOME"/.claude-*(N/); do
    [ -s "$d/.claude.json" ] && ACCOUNTS+=("${d##*/.claude-}:$d")
done

# Rotate before writing if the current log has grown past the cap.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt "$MAXBYTES" ]; then
    mv -f "$LOG" "$LOG.1"
fi

echo "=== $(date) ===" >> "$LOG"
for entry in $ACCOUNTS; do
    label="${entry%%:*}"
    dir="${entry#*:}"

    # Safety gate: if the account's config json is absent, claude silently
    # recreates a stub and the account loses its oauthAccount + project
    # history. Skip instead. NOTE: the default account's LIVE config is
    # ~/.claude.json (not ~/.claude/.claude.json — that one can be a stale
    # migration artifact); see docs/session-migration.md gotcha #1.
    if [ "$label" = "default" ]; then
        cfg="$HOME/.claude.json"
    else
        cfg="$dir/.claude.json"
    fi
    if [ ! -s "$cfg" ]; then
        echo "[$label] SKIPPED — $cfg missing or empty (restore it first)" >> "$LOG"
        continue
    fi

    # Each account is independent: one failing must not skip the others.
    # --model haiku: the ping only needs to open the 5-hour window; use the
    # cheapest model so it burns almost no credit.
    #
    # The default account must run WITHOUT CLAUDE_CONFIG_DIR: when the env var
    # is set (even to ~/.claude), the CLI derives a path-suffixed keychain
    # service name and misses the default credential -> "Not logged in".
    # This is what broke the default ping from 2026-08-24 to 2026-09-05.
    if [ "$label" = "default" ]; then
        out=$(env -u CLAUDE_CONFIG_DIR "$CLAUDE_BIN" -p --model haiku "Hello, only answer hello" 2>&1)
    else
        out=$(CLAUDE_CONFIG_DIR="$dir" "$CLAUDE_BIN" -p --model haiku "Hello, only answer hello" 2>&1)
    fi
    echo "[$label] ${out:-(no output)}" >> "$LOG"
done
