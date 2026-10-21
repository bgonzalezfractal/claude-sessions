# agentctl accounts — Reference

List the accounts on this Mac across every provider, or scaffold a new one.

```bash
agentctl accounts list [--json]
agentctl accounts add work            # a new Claude Code account
```

## list

One row per account: label, provider, shell alias, login state (email or "not
logged in"), credential presence, and live session count.

An **account** is one login of one provider:

| Provider | What counts as an account | Credential |
|----------|---------------------------|------------|
| claude | `~/.claude` (label `default`) or a `~/.claude-<alias>` dir containing `.claude.json` | macOS keychain |
| codex | `~/.codex` (label `codex`) or a `~/.codex-<alias>` dir containing `auth.json` | `auth.json` in the config dir |

Labels are unique across providers, so every other subcommand takes just the
label (`agentctl status bggai`, `agentctl renew add codex new-acp`).

Non-account dirs (`.claude-deepinfra` proxy wrapper, `.claude-session-backups`)
are excluded — they have no `.claude.json`. The default Claude account's live
config is **`~/.claude.json`** — not `~/.claude/.claude.json`, which can be a
stale migration artifact (see [session-migration.md](session-migration.md)
gotcha #1).

## add

`add <alias>` creates `~/.claude-<alias>` and appends
`alias claude-<alias>='CLAUDE_CONFIG_DIR=$HOME/.claude-<alias> claude'` to
`~/.zshrc`. **Login stays manual** (it's interactive): open a new terminal, run
the alias, `/login`. It deliberately does NOT create a `.claude.json` stub —
the first login writes it, and a hand-made stub is exactly what once wiped an
account's history.

For a second **Codex** account the equivalent is manual and just as short:

```bash
mkdir -p ~/.codex-work
echo "alias codex-work='CODEX_HOME=\$HOME/.codex-work codex'" >> ~/.zshrc
CODEX_HOME=$HOME/.codex-work codex login      # writes auth.json
```

After first login the account is **auto-discovered** by `agentctl status`,
`agentctl renew`, `agentctl copy`, the UI, and the morning
[anchor job](anchor.md) — no lists to edit anywhere. (Keychain service for a new
Claude account: `Claude Code-credentials-<first 8 hex of sha256(config-dir
path)>`; Codex keeps its token in the config dir instead.)

See [providers.md](providers.md) for the full discovery rules and the
default-account env-var gotcha.
