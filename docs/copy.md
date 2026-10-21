# agentctl copy — Reference

Migrate a session between Claude Code accounts **quickly, by name** — a
front-end over the proven `claude-migrate-session.sh` (bundled in `bin/`,
originally from `nirvanav0/backend_ml_cpu/prompts/claude_sessions/`; full
mechanics and gotchas in [session-migration.md](session-migration.md)).

```bash
agentctl copy bggai default radal-v2          # by /rename title
agentctl copy bggai default <uuid> --dry-run  # uuid works too
agentctl copy --list bggai [--json]           # sessions on an account

# Paths are personalizable on both ends:
agentctl copy default bggai workflow-bug \
    --path ~/Documents/repos/nirvana/nirvanav0 \   # origin: picks THIS project's
                                                    #  session when titles collide
    --to-path ~/Documents/repos/radal/radal-app     # destination: re-homes the copy
```

`--to-path` lands the transcript, sidecar and memory under the destination
path's project key, merges the `.claude.json` entry under the new cwd, and the
printed resume line uses the new path (the transcript keeps its old cwd values
— known re-home behavior, gotcha #5).

- Accounts are auto-discovered (default + every logged-in `~/.claude-*`).
- A title matching several sessions resolves to the **most recent transcript**
  and prints the others so you can pass a uuid instead.
- `--list` marks LIVE sessions and sorts newest-first; `--json` feeds the UI.
- After a real copy it re-prints the two critical reminders: **exit the source
  session** (a copy is a fork — they diverge silently) and consider
  `"autoCompactEnabled": false` on the destination before resuming a large
  transcript. The underlying script already warns when a live process holds the
  session, backs up any existing destination copy, merges project memory
  additively, and prints the exact resume line.

## Provider scope

**Claude Code only.** Codex stores sessions as rollout `.jsonl` files plus rows
in `~/.codex/state_5.sqlite`, with a different on-disk shape; copying a thread
between Codex accounts is not implemented. Passing a Codex label is rejected
with that message rather than silently doing nothing. See
[providers.md](providers.md) for the storage layouts.
