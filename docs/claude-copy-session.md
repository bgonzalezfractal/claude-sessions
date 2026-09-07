# claude-copy-session — Reference

Migrate a session between accounts **quickly, by name** — a front-end over the
proven `claude-migrate-session.sh` (bundled in `bin/`, originally from
`nirvanav0/backend_ml_cpu/prompts/claude_sessions/`; full mechanics and gotchas
in [session-migration.md](session-migration.md)).

```bash
claude-copy-session bggai default radal-v2          # by /rename title
claude-copy-session bggai default <uuid> --dry-run  # uuid works too
claude-copy-session --list bggai [--json]           # sessions on an account
```

- Aliases are auto-discovered (default + every logged-in `~/.claude-*`).
- A title matching several sessions resolves to the **most recent transcript**
  and prints the others so you can pass a uuid instead.
- `--list` marks LIVE sessions and sorts newest-first; `--json` feeds the UI.
- After a real copy it re-prints the two critical reminders: **exit the source
  session** (a copy is a fork — they diverge silently) and consider
  `"autoCompactEnabled": false` on the destination before resuming a large
  transcript. The underlying script already warns when a live process holds the
  session, backs up any existing destination copy, merges project memory
  additively, and prints the exact resume line.
