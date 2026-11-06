#!/usr/bin/env bash
# Copy a Claude Code session (transcript + all sidecar state) from one config dir
# (alias/account) to another, keeping the same session id so it can be resumed.
#
#   claude-migrate-session.sh <src-config-dir> <dst-config-dir> <session-id> \
#       [--dry-run] [--dst-cwd <path>]     # --dst-cwd re-homes the copy
#
# Docs, gotchas and verification steps: ./session_migration.md
#
# Example:
#   claude-migrate-session.sh ~/.claude ~/.claude-bggai 2fcc8337-04b2-432a-814a-4bbe66c6c630
set -euo pipefail

SRC="${1:?src config dir, e.g. ~/.claude}"
DST="${2:?dst config dir, e.g. ~/.claude-bggai}"
SID="${3:?session id (uuid)}"
shift 3
DRY=""; DST_CWD_OVERRIDE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY="--dry-run"; shift ;;
    --dst-cwd) DST_CWD_OVERRIDE="${2:?--dst-cwd needs a path}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done
RS=(rsync -a)
[ "$DRY" = "--dry-run" ] && RS=(rsync -a --dry-run -v) && echo "*** DRY RUN ***"
# A dry run must leave the destination untouched — not even empty dirs.
mkd() { [ "$DRY" = "--dry-run" ] || mkdir -p "$@"; }

SRC="${SRC%/}"; DST="${DST%/}"
[ -d "$SRC" ] || { echo "no such src: $SRC" >&2; exit 1; }
[ -d "$DST" ] || { echo "no such dst: $DST" >&2; exit 1; }

# ---- resolve the real config json for each dir ------------------------------
# The default account (no CLAUDE_CONFIG_DIR) keeps its config at ~/.claude.json;
# aliased dirs keep it at <dir>/.claude.json. For ~/.claude, ~/.claude.json is
# always the live file — a <dir>/.claude.json there is a stale copy (seeded by a
# past migration) that plain `claude` never reads.
pick_json() {
  local dir="$1" a="$1/.claude.json" b="$HOME/.claude.json"
  if [ "$dir" = "$HOME/.claude" ] && [ -f "$b" ]; then
    echo "$b"
  else
    echo "$a"
  fi
}
SRC_JSON="$(pick_json "$SRC")"
DST_JSON="$(pick_json "$DST")"

# ---- locate the transcript ---------------------------------------------------
TRANSCRIPT="$(find "$SRC/projects" -maxdepth 2 -name "$SID.jsonl" -print -quit)"
[ -n "$TRANSCRIPT" ] || { echo "session $SID not found under $SRC/projects" >&2; exit 1; }
PROJ_DIR="$(dirname "$TRANSCRIPT")"
PROJ_KEY="$(basename "$PROJ_DIR")"
# A re-homed session (gotcha #5) carries several cwds; the first one is stale.
# Prefer the cwd whose slug matches <project-key>, else the most recent one.
CWD="$(python3 - "$TRANSCRIPT" "$PROJ_KEY" <<'PY'
import json,re,sys
slug=lambda w: re.sub(r"[^A-Za-z0-9]", "-", w)
key=sys.argv[2]; last=""; match=""
for line in open(sys.argv[1]):
    try: d=json.loads(line)
    except Exception: continue
    w=d.get("cwd")
    if not w: continue
    last=w
    if not match and slug(w)==key: match=w
print(match or last)
PY
)"
TITLE="$(python3 - "$TRANSCRIPT" <<'PY'
import json,sys
t=""
for line in open(sys.argv[1]):
    try: d=json.loads(line)
    except Exception: continue
    if d.get("type")=="custom-title": t=d.get("customTitle","")
    elif d.get("type")=="ai-title" and not t: t=d.get("aiTitle","")
print(t)
PY
)"
# Optional re-home: land the copy under a different working directory in the
# destination (gotcha #5 — the transcript keeps old cwd values, but the resumed
# session records the new cwd going forward and files under the new project key).
DST_CWD="${DST_CWD_OVERRIDE:-$CWD}"
DST_KEY="$(python3 -c "import re,sys;print(re.sub(r'[^A-Za-z0-9]','-',sys.argv[1]))" "$DST_CWD")"

echo "session : $SID  ($TITLE)"
echo "project : $PROJ_KEY"
echo "cwd     : $CWD"
[ "$DST_CWD" != "$CWD" ] && echo "re-home : $DST_CWD  (project key $DST_KEY)"
echo "src->dst: $SRC -> $DST"
echo "config  : $SRC_JSON -> $DST_JSON"
echo

# ---- guard: warn if a live process still holds this session ------------------
# (gotcha #3 in session_migration.md: a copy is a fork; exit the source first)
for sj in "$SRC"/sessions/*.json "$DST"/sessions/*.json; do
  [ -f "$sj" ] || continue
  if grep -q "\"sessionId\":\"$SID\"" "$sj" 2>/dev/null; then
    PID="$(python3 -c "import json;print(json.load(open('$sj')).get('pid',''))" 2>/dev/null)"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
      echo "!! session $SID is OPEN in a live process (pid $PID, $sj)"
      echo "!! exit that session before/right after copying, or the copies will diverge."
      echo
    fi
  fi
done

# ---- 0. guard: don't silently clobber an existing copy -----------------------
DST_T="$DST/projects/$DST_KEY/$SID.jsonl"
if [ -f "$DST_T" ] && [ "$DRY" != "--dry-run" ]; then
  STAMP="$(date +%Y%m%d-%H%M)"
  echo "[0/6] dst copy exists -> backing up as *.bak-$STAMP"
  cp -p "$DST_T" "$DST_T.bak-$STAMP"
  [ -d "$DST/projects/$DST_KEY/$SID" ] && cp -Rp "$DST/projects/$DST_KEY/$SID" "$DST/projects/$DST_KEY/$SID.bak-$STAMP"
fi

# ---- 1. transcript + per-session sidecar (subagents/, workflows/, tool-results/)
mkd "$DST/projects/$DST_KEY"
echo "[1/6] transcript + sidecar dirs"
"${RS[@]}" "$TRANSCRIPT" "$DST/projects/$DST_KEY/"
[ -d "$PROJ_DIR/$SID" ] && "${RS[@]}" "$PROJ_DIR/$SID/" "$DST/projects/$DST_KEY/$SID/"
# A session that ran tools from subdirectories can leave sidecars under OTHER
# project keys too (e.g. a Workflow launched while cd'd into frontend/ writes
# its scriptPath under <proj>-frontend/<sid>/workflows/scripts/). The transcript
# references those absolute paths, so copy each one under the same key
# (hit 2026-10-05: a backend workflow's script lived only under -frontend).
EXTRA_SIDECARS=()
for d in "$SRC"/projects/*/"$SID"; do
  [ -d "$d" ] || continue
  k="$(basename "$(dirname "$d")")"
  [ "$k" = "$PROJ_KEY" ] && continue
  echo "      + extra sidecar under $k"
  mkd "$DST/projects/$k/$SID"
  "${RS[@]}" "$d/" "$DST/projects/$k/$SID/"
  EXTRA_SIDECARS+=("$DST/projects/$k/$SID")
done

# ---- 1b. rewrite absolute config-dir paths inside the copy -------------------
# Tool results, workflow journals and scripts record absolute paths under the
# SOURCE config dir (e.g. an ultracode Workflow's scriptPath + its wf_*.json
# resume state). The copy runs under the destination dir and tools refuse the
# old paths (hit 2026-09-07 resuming a workflow after migration). Rewrite
# "$SRC/" -> "$DST/" — trailing slash matters: without it, migrating FROM
# ~/.claude would also mangle ~/.claude-benjr paths.
if [ "$DRY" != "--dry-run" ]; then
  echo "[1b/6] rewrite $SRC/ -> $DST/ inside the copy"
  python3 - "$SRC/" "$DST/" "$DST/projects/$DST_KEY/$SID.jsonl" "$DST/projects/$DST_KEY/$SID" ${EXTRA_SIDECARS[@]+"${EXTRA_SIDECARS[@]}"} <<'PY'
import os, sys
src, dst, transcript = sys.argv[1:4]
targets = [transcript]
for sidecar in sys.argv[4:]:
    for root, _, files in os.walk(sidecar):
        targets += [os.path.join(root, f) for f in files
                    if f.endswith((".json", ".jsonl", ".js", ".md", ".txt"))]
n = 0
for p in targets:
    if not os.path.isfile(p):
        continue
    try:
        t = open(p, encoding="utf-8").read()
    except (UnicodeDecodeError, OSError):
        continue
    if src in t:
        open(p, "w", encoding="utf-8").write(t.replace(src, dst))
        n += 1
print(f"      rewrote {n} file(s)")
PY
else
  echo "[1b/6] (dry run) would rewrite $SRC/ -> $DST/ inside the copy"
fi

# ---- 2. project memory (MEMORY.md + memory files) ---------------------------
# ADDITIVE: the destination account has its own memory for this project. Never
# overwrite a memory file that already exists there (its MEMORY.md index is
# usually the richer one) — copy only the new files and merge the index lines.
if [ -d "$PROJ_DIR/memory" ]; then
  echo "[2/6] project memory ($(ls "$PROJ_DIR/memory" | wc -l | tr -d ' ') files, additive)"
  mkd "$DST/projects/$DST_KEY/memory"
  "${RS[@]}" --ignore-existing "$PROJ_DIR/memory/" "$DST/projects/$DST_KEY/memory/"
  if [ "$DRY" = "--dry-run" ]; then
    echo "      would merge MEMORY.md pointer lines"
  else
    python3 - "$PROJ_DIR/memory" "$DST/projects/$DST_KEY/memory" <<'PY'
import os,re,shutil,sys
src,dst=sys.argv[1:3]
si,di=os.path.join(src,"MEMORY.md"),os.path.join(dst,"MEMORY.md")
if not os.path.exists(si): sys.exit(0)
if not os.path.exists(di):
    shutil.copy2(si,di); print("      MEMORY.md: copied (dst had none)"); sys.exit(0)
d=open(di).read()
link=re.compile(r"\]\(([^)]+\.md)\)")
have={m.group(1) for m in link.finditer(d)}
new=[l.rstrip("\n") for l in open(si)
     if l.lstrip().startswith("- ") and (m:=link.search(l)) and m.group(1) not in have]
if not new:
    print("      MEMORY.md: no new pointers"); sys.exit(0)
shutil.copy2(di,di+".bak")
with open(di,"a") as f:
    f.write("\n## Merged from the migrated session\n"+"\n".join(new)+"\n")
print("      MEMORY.md: appended %d pointer(s)"%len(new))
PY
  fi
else
  echo "[2/6] project memory: none"
fi

# ---- 3. per-session state dirs ----------------------------------------------
echo "[3/6] tasks / file-history / session-env"
for d in tasks file-history session-env; do
  if [ -d "$SRC/$d/$SID" ]; then
    mkd "$DST/$d/$SID"
    "${RS[@]}" "$SRC/$d/$SID/" "$DST/$d/$SID/"
    # a running source session leaves a .lock behind; it is meaningless in the copy
    [ "$DRY" = "--dry-run" ] || rm -f "$DST/$d/$SID/.lock"
    echo "      + $d/$SID"
  fi
done

# ---- 4. prompt history for this session -------------------------------------
echo "[4/6] history.jsonl entries"
if [ -f "$SRC/history.jsonl" ]; then
  N=$(grep -c "\"$SID\"" "$SRC/history.jsonl" || true)
  if [ "$DRY" = "--dry-run" ]; then
    echo "      would append $N entries"
  else
    # Dedup in python: `grep -qxF "$huge_line"` can exhaust memory on
    # multi-MB pasted-content entries (hit 2026-09-07).
    python3 - "$SRC/history.jsonl" "$DST/history.jsonl" "$SID" <<'PY'
import hashlib, sys
src, dst, sid = sys.argv[1:4]
h = lambda l: hashlib.sha1(l.encode()).digest()
try:
    have = {h(l) for l in open(dst, errors="replace")}
except FileNotFoundError:
    have = set()
n = 0
with open(dst, "a") as out:
    for l in open(src, errors="replace"):
        if sid in l and h(l) not in have:
            out.write(l if l.endswith("\n") else l + "\n")
            n += 1
print(f"      appended {n} entries")
PY
  fi
fi

# ---- 5. .claude.json project entry (trust, allowedTools, mcp, ignorePatterns)
echo "[5/6] .claude.json project entry"
if [ "$DRY" != "--dry-run" ]; then
  python3 - "$SRC_JSON" "$DST_JSON" "$CWD" "$DST_CWD" <<'PY'
import json,sys,os,shutil
src,dst,cwd,dst_cwd=sys.argv[1:5]
if not (os.path.exists(src) and os.path.exists(dst)): sys.exit(0)
s=json.load(open(src)); d=json.load(open(dst))
sp=s.get("projects",{}).get(cwd)
if not sp: print("      no src project entry for",cwd); sys.exit(0)
KEEP=["allowedTools","mcpContextUris","mcpServers","enabledMcpjsonServers",
      "disabledMcpjsonServers","disabledMcpServers","hasTrustDialogAccepted",
      "ignorePatterns","hasClaudeMdExternalIncludesApproved","exampleFiles",
      "exampleFilesGeneratedAt","projectOnboardingSeenCount"]
tgt=d.setdefault("projects",{}).setdefault(dst_cwd,{})
added=[]
for k in KEEP:
    if k not in sp: continue
    if k not in tgt:
        tgt[k]=sp[k]; added.append(k)
    # A dict key that already exists (often an empty mcpServers: {}) still gets
    # the source's missing entries — existing destination entries win.
    elif isinstance(sp[k],dict) and isinstance(tgt[k],dict):
        new=[n for n in sp[k] if n not in tgt[k]]
        for n in new: tgt[k][n]=sp[k][n]
        added += [f"{k}.{n}" for n in new]
shutil.copy2(dst,dst+".bak")
json.dump(d,open(dst,"w"),indent=2)
print("      merged keys:", ", ".join(added) or "(nothing new)")
PY
else
  echo "      (skipped in dry run)"
fi

# ---- 6. done -----------------------------------------------------------------
echo "[6/6] done"
echo
echo "Resume with:"
if [ "$DST" = "$HOME/.claude" ]; then
  echo "  cd $DST_CWD && claude --resume $SID"
else
  echo "  cd $DST_CWD && CLAUDE_CONFIG_DIR=$DST claude --resume $SID"
fi
