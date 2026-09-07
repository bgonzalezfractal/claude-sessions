# claude-account — Reference

List the Claude account aliases on this Mac, or scaffold a new one.

```bash
claude-account list [--json]
claude-account add work
```

## list

One row per account: alias, shell alias, login state (email or "not logged
in"), keychain-credential presence, and live session count.

An **account** is `~/.claude` (default) or a `~/.claude-<alias>` dir containing
`.claude.json`. Non-account dirs (`.claude-deepinfra` proxy wrapper,
`.claude-session-backups`) are excluded. The default account's live config is
**`~/.claude.json`** — not `~/.claude/.claude.json`, which can be a stale
migration artifact (see [session-migration.md](session-migration.md) gotcha #1).

## add

`add <alias>` creates `~/.claude-<alias>` and appends
`alias claude-<alias>='CLAUDE_CONFIG_DIR=$HOME/.claude-<alias> claude'` to
`~/.zshrc`. **Login stays manual** (it's interactive): open a new terminal, run
the alias, `/login`. It deliberately does NOT create a `.claude.json` stub —
the first login writes it, and a hand-made stub is exactly what once wiped an
account's history.

After first login the account is **auto-discovered** by `claude-status`,
`claude-renew`, `claude-copy-session`, the UI, and the morning limit-reset job
— no lists to edit anywhere. (Keychain service for a new account:
`Claude Code-credentials-<first 8 hex of sha256(config-dir path)>`.)
