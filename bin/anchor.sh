#!/bin/zsh
# agentctl anchor — the morning window-anchor job.
#
# Fires one throwaway "Hello, only answer hello" per logged-in account so the
# day's 5-hour usage window starts now instead of whenever work starts.
# Providers: Claude Code (~/.claude + ~/.claude-*) and Codex (~/.codex +
# ~/.codex-*).  launchd runs the installed regular copy ~/.agentctl/anchor.sh,
# outside Documents (macOS privacy protection blocks a symlink into it).
#
#   anchor.sh             run the pings, log to ~/.agentctl/anchor.log
#   anchor.sh --dry-run   print what would run, send nothing
#
# Exit 0 when every account was pinged successfully, 1 if any failed.
setopt NULL_GLOB

mkdir -p "$HOME/.agentctl"
LOG="$HOME/.agentctl/anchor.log"
LOCK="$HOME/.agentctl/anchor.lock"
CLAUDE_BIN="${CLAUDE_BIN:-/opt/homebrew/bin/claude}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"
PROMPT='Hello, only answer hello'
# Pinned: the user's config.toml model may be one a ChatGPT login rejects
# (model_not_found), which failed every codex ping until 2026-11-09.
CODEX_MODEL="${CODEX_ANCHOR_MODEL:-gpt-6-luna}"
MAXBYTES=51200                      # rotate the log past 50 KB

# --------------------------------------------------------------- accounts ---
# One entry per account: "provider|label|config-dir".
ACCOUNTS=("claude|default|$HOME/.claude")
for dir in "$HOME"/.claude-*(/); do
    [[ -s "$dir/.claude.json" ]] && ACCOUNTS+=("claude|${dir##*/.claude-}|$dir")
done
[[ -s "$HOME/.codex/auth.json" ]] && ACCOUNTS+=("codex|codex|$HOME/.codex")
for dir in "$HOME"/.codex-*(/); do
    [[ -s "$dir/auth.json" ]] && ACCOUNTS+=("codex|codex-${dir##*/.codex-}|$dir")
done

# config_file <provider> <label> <dir> — the file that proves the account exists.
config_file() {
    if [[ "$1" != claude ]]; then
        print -r -- "$3/auth.json"
    elif [[ "$2" == default ]]; then
        print -r -- "$HOME/.claude.json"      # NOT ~/.claude/.claude.json
    else
        print -r -- "$3/.claude.json"
    fi
}

# ping_cmd <provider> <label> <dir> — fills the global CMD array.
# The default account of each provider must run WITHOUT its config-dir env var:
# with it set, the Claude CLI looks for a hashed keychain entry that does not
# exist ("Not logged in" on a perfectly good account).
ping_cmd() {
    local provider="$1" label="$2" dir="$3"
    if [[ "$provider" == claude ]]; then
        # Disable custom hooks, tools and persistence for this throwaway ping.
        if [[ "$label" == default ]]; then
            CMD=(env -u CLAUDE_CONFIG_DIR "$CLAUDE_BIN" --safe-mode -p --model haiku --tools '' --no-session-persistence "$PROMPT")
        else
            CMD=(env CLAUDE_CONFIG_DIR="$dir" "$CLAUDE_BIN" --safe-mode -p --model haiku --tools '' --no-session-persistence "$PROMPT")
        fi
    else
        if [[ "$label" == codex ]]; then
            CMD=(env -u CODEX_HOME "$CODEX_BIN" exec --ephemeral --skip-git-repo-check -s read-only -C "$HOME" -m "$CODEX_MODEL" -c 'model_reasoning_effort="low"' "$PROMPT")
        else
            CMD=(env CODEX_HOME="$dir" "$CODEX_BIN" exec --ephemeral --skip-git-repo-check -s read-only -C "$HOME" -m "$CODEX_MODEL" -c 'model_reasoning_effort="low"' "$PROMPT")
        fi
    fi
}

split_entry() {                      # sets provider / label / dir from an entry
    provider="${1%%|*}"
    local rest="${1#*|}"
    label="${rest%%|*}"
    dir="${rest#*|}"
}

case "${1:-}" in
    -h|--help)
        print -r -- "usage: anchor.sh [--dry-run]   (log: $LOG)"
        exit 0 ;;
    --dry-run)
        for entry in "${ACCOUNTS[@]}"; do
            split_entry "$entry"
            ping_cmd "$provider" "$label" "$dir"
            print -r -- "$label: provider=$provider config=$dir; cmd=${(j: :)${(@q-)CMD}}"
        done
        exit 0 ;;
    "") ;;
    *)  print -r -- "anchor.sh: unknown option $1" >&2; exit 2 ;;
esac

cd "$HOME" || exit 1

# ------------------------------------------------------------------- lock ---
if ! mkdir "$LOCK" 2>/dev/null; then
    if [[ -s "$LOCK/pid" ]] && kill -0 "$(cat "$LOCK/pid")" 2>/dev/null; then
        print -r -- "=== $(date) === SKIPPED — another anchor job is running" >> "$LOG"
        exit 1
    fi
    # Recover a lock left by an interrupted job.
    [[ -f "$LOCK/pid" ]] && unlink "$LOCK/pid"
    rmdir "$LOCK" 2>/dev/null || exit 1
    mkdir "$LOCK" || exit 1
fi
print $$ > "$LOCK/pid"
RUN_TMP=$(mktemp -d "${TMPDIR:-/tmp}/agentctl-anchor.XXXXXX") || exit 1
trap '[[ -n ${ping_pid:-} ]] && kill "$ping_pid" 2>/dev/null; [[ -n ${timer_pid:-} ]] && kill "$timer_pid" 2>/dev/null; /bin/rm -rf "$RUN_TMP"; unlink "$LOCK/pid"; rmdir "$LOCK"' EXIT

if [[ -f "$LOG" ]] && [[ $(stat -f%z "$LOG") -gt $MAXBYTES ]]; then
    mv -f "$LOG" "$LOG.1"
fi

# ------------------------------------------------------------------ pings ---
# `agentctl log` parses these lines: "[label] attempt=N exit=RC <output>"
# under a "=== <date> ===" header. Keep the format.
print -r -- "=== $(date) ===" >> "$LOG"
failed=0
for entry in "${ACCOUNTS[@]}"; do
    split_entry "$entry"
    cfg=$(config_file "$provider" "$label" "$dir")
    if [[ ! -s "$cfg" ]]; then
        print -r -- "[$label] FAILED — $cfg missing or empty" >> "$LOG"
        failed=1
        continue
    fi
    ping_cmd "$provider" "$label" "$dir"
    for attempt in 1 2 3; do
        "${CMD[@]}" < /dev/null > "$RUN_TMP/output" 2>&1 &
        ping_pid=$!
        ( sleep 90 &
          alarm_pid=$!
          trap 'kill "$alarm_pid" 2>/dev/null; exit' TERM
          wait "$alarm_pid"
          kill -TERM "$ping_pid" 2>/dev/null
          sleep 5 &
          alarm_pid=$!
          wait "$alarm_pid"
          kill -KILL "$ping_pid" 2>/dev/null
        ) </dev/null >/dev/null 2>&1 &
        timer_pid=$!
        wait "$ping_pid"
        rc=$?
        kill "$timer_pid" 2>/dev/null
        wait "$timer_pid" 2>/dev/null
        unset ping_pid timer_pid
        out=$(cat "$RUN_TMP/output")
        print -r -- "[$label] attempt=$attempt exit=$rc ${out:-(no output)}" >> "$LOG"
        if [[ "$rc" == 0 ]]; then
            break
        fi
        # An account that is already limited needs a later run, not a retry.
        if [[ "$out" == *('hit your usage limit'|'usage limit'|'hit your session limit')* ]]; then
            failed=1
            break
        fi
        # Retry network/wake failures only. Auth and active limits need a later run.
        if [[ "$attempt" -lt 3 && "$out" == *('ENOTFOUND'|'ConnectionRefused'|'Unable to connect'|'Can’t reach'|"Can't reach"|'timed out'|'ECONNRESET')* ]]; then
            sleep 15
        else
            failed=1
            break
        fi
    done
done
exit "$failed"
