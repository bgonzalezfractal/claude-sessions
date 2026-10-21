# Migrating a Claude Code session between accounts (aliases)

_Written 2026-08-24. Verified by migrating the `workflows` session from `claude` → `claude-bggai`,
and again 2026-09-06 migrating `claude-sessions` from `claude-benjr` → `claude` (default)._

This machine runs several Claude Code accounts side by side, each isolated by its own
config directory (`.bashrc` / `.zshrc`):

| Alias | Config dir | Config JSON |
|---|---|---|
| `claude` (default) | `~/.claude` | **`~/.claude.json`** (not inside the dir) |
| `claude-benjr` | `~/.claude-benjr` | `~/.claude-benjr/.claude.json` |
| `claude-bggai` | `~/.claude-bggai` | `~/.claude-bggai/.claude.json` |

```bash
alias claude-benjr='CLAUDE_CONFIG_DIR=$HOME/.claude-benjr claude'
alias claude-bggai='CLAUDE_CONFIG_DIR=$HOME/.claude-bggai claude'
```

When one account hits its usage limit, a session can be **copied** to another account and
resumed there with the same session id, keeping the full transcript, subagent/workflow
history, task list, file-history and project memory.

## Usage

```bash
./claude-migrate-session.sh <src-config-dir> <dst-config-dir> <session-id> [--dry-run]

# the one we ran:
./claude-migrate-session.sh ~/.claude ~/.claude-bggai 2fcc8337-04b2-432a-814a-4bbe66c6c630
```

Then resume (the script prints this line for you):

```bash
cd /Users/bgg/Documents/repos/nirvana/nirvanav0 && claude-bggai --resume <session-id>
```

### Finding the session id

Sessions are addressed by uuid, but you usually remember the **custom title** (`workflows`,
`nirvai-ga-test`, …). Two ways to map title → uuid:

```bash
# a) live sessions — one json per running process
grep -l '"name":"workflows"' ~/.claude/sessions/*.json | xargs cat

# b) any session, from the transcripts
grep -h '"custom-title"' ~/.claude/projects/*/*.jsonl | sort -u
```

## What the script copies

Everything below is keyed by session id, and all of it is needed for a clean resume:

| # | Item | Path (relative to the config dir) |
|---|---|---|
| 1 | Transcript | `projects/<project-key>/<sid>.jsonl` |
| 2 | Session sidecar — subagent transcripts, `workflows/scripts/*.js`, workflow journals, tool-results | `projects/<project-key>/<sid>/` — plus any `projects/<other-key>/<sid>/` (a session that worked from a subdirectory can write sidecars, e.g. a Workflow's `scriptPath`, under that subdir's key; each is copied under the same key) |
| 3 | Project memory (`MEMORY.md` + memory files), merged additively | `projects/<project-key>/memory/` |
| 4 | Task list | `tasks/<sid>/` |
| 5 | File-history versions (used by rewind / undo) | `file-history/<sid>/` |
| 6 | Session env | `session-env/<sid>/` |
| 7 | Prompt history for that session (↑ arrow recall) | appended to `history.jsonl`, deduped |
| 8 | Project entry — trust flag, `allowedTools`, MCP servers, `ignorePatterns` | the config JSON's `projects[<cwd>]` |

`<project-key>` is the cwd with `/` replaced by `-`, e.g.
`/Users/bgg/Documents/repos/nirvana/nirvanav0` → `-Users-bgg-Documents-repos-nirvana-nirvanav0`.

### What it deliberately does NOT copy

- **Scratchpad and task output files** under `/private/tmp/claude-501/<project-key>/<sid>/`.
  That path is derived from project + session id only — it is *not* per-account, so the
  destination alias already sees the same files.
- **Credentials.** Each account keeps its own login; that is the whole point of the split.
- **`settings.json`.** Global to the alias, not to the session — see the gotcha below.

## Gotchas learned the hard way

1. **The default account's config JSON is `~/.claude.json`, not `~/.claude/.claude.json`.**
   Aliased dirs keep theirs inside the dir; only the default account keeps it at `$HOME`.
   Beware: past migrations can leave a full-looking (but stale, never-read) copy at
   `~/.claude/.claude.json` — "has a `projects` key" is *not* proof it's the live file.
   The script now hard-codes the rule: for `~/.claude` it always uses `~/.claude.json`.
   (Found 2026-09-06: the old "pick whichever has `projects`" heuristic merged the project
   entry into the stale file when migrating *into* the default account.)

2. **Turn off auto-compact on the destination before resuming a large session.**
   These transcripts run 3–40 MB; resuming one with auto-compact on can compact away the
   context you were trying to preserve.
   ```json
   // ~/.claude-bggai/settings.json
   { "autoCompactEnabled": false }
   ```

3. **Exit the source session first.** A copy is a *fork*, not a move. If both stay open they
   diverge silently — as of this writing session `2fcc8337` exists in three places
   (`~/.claude` under `nirvanav0`, `~/.claude-benjr` under `nirvai-clone`, and an older
   `~/.claude-benjr` copy under `nirvanav0`), all with different contents. The script now
   prints a `!!` warning when a live process holds the session, but doesn't abort — you can
   still check manually:
   ```bash
   ps aux | grep '[c]laude --resume'
   cat ~/.claude*/sessions/*.json | grep -o '"sessionId":"[^"]*"'
   ```

4. **Remove the stale `tasks/<sid>/.lock`** if you copy while the source is running. The
   script does this for you.

5. **The same session can be resumed from a different cwd**, which files it under a
   different `<project-key>` in the destination. Resume from the cwd the script prints
   unless you intend to re-home it.
   A re-homed session carries *several* `cwd` values in one transcript, and the first one
   is the stale original. The script now picks the cwd whose slug matches `<project-key>`
   (falling back to the most recent), so step 5 merges the right `projects[<cwd>]` entry
   and the printed resume line is correct.
   (Found 2026-09-06 migrating `claude-sessions`: 165 lines said `radal/radal-app`, 315
   said `nirvana/nirvanav0`, and the old "first cwd wins" rule picked `radal-app`.)

6. **Project memory is per-account and the destination usually has more of it.**
   Step 2 is now *additive*: `rsync --ignore-existing` for the memory files, and
   `MEMORY.md` is merged by appending only the pointer lines whose target file the
   destination index doesn't already list (under a `## Merged from the migrated session`
   heading; the old index is kept as `MEMORY.md.bak`). A plain copy would have replaced a
   12 KB / 85-file index with a 3 KB / 16-file one.

### Path rewriting (added 2026-09-07)

After copying, the script rewrites `<src-config-dir>/` → `<dst-config-dir>/`
inside the copied transcript and sidecar text files (`.json/.jsonl/.js/.md/.txt`).
Without this, a resumed session reads stale absolute paths out of its own
history — e.g. an ultracode **Workflow** whose `scriptPath` and `wf_*.json`
resume journals point at the source config dir, which the Workflow tool
refuses. (The trailing slash in the match prevents `~/.claude` from also
mangling `~/.claude-benjr` paths.) Consequence: the destination transcript is
no longer byte-identical to the source — hash-compare verification only holds
for sessions that never referenced their config dir.

## Verifying a migration

```bash
SID=<session-id>; S=~/.claude/projects/<key>; D=~/.claude-bggai/projects/<key>
shasum "$S/$SID.jsonl" "$D/$SID.jsonl"        # identical hashes
rsync -rcn --out-format='DIFF %n' "$S/$SID/" "$D/$SID/"   # empty output = byte-identical
```

## Safety behaviour

- `--dry-run` prints every file that would move and changes nothing.
- If the destination already holds that session, the transcript and sidecar are backed up
  as `*.bak-YYYYmmdd-HHMM` before being overwritten.
- The destination config JSON is backed up to `.claude.json.bak` before the merge, and the
  merge is additive — existing destination keys are never overwritten.
- Project memory is likewise additive: existing destination memory files are never
  overwritten, and `MEMORY.md` is backed up to `MEMORY.md.bak` before pointers are appended.
