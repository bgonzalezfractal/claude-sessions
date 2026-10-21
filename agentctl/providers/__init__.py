"""Provider registry + the contract every provider module implements.

An *account* is one login of one provider, addressed everywhere by a unique
label:  claude -> "default", "benjr", "bggai" ...   codex -> "codex",
"codex-<alias>" ...   Labels are unique across providers, so every command
takes just the label (`agentctl renew add codex new-acp`).

Account dict (returned by provider.discover()):
    {"provider": "claude"|"codex",
     "label": str,               # unique, what the user types
     "config_dir": str,          # ~/.claude, ~/.claude-bggai, ~/.codex ...
     "suffix_dir": str|None,     # claude only: value for CLAUDE_CONFIG_DIR
                                 # (None = default account = run WITHOUT it)
     "shell_alias": str}         # what the user types to open that account

Every provider module exposes these functions (duck-typed, no base class):

    discover() -> [account]
        Logged-in accounts of this provider. Cheap; no network.

    usage(account, interactive=False) -> dict
        Read-only limit check that never anchors a usage window.
        {"email": str,
         "limits": [{"kind": "session"|"weekly_all"|"weekly_scoped",
                     "percent": int, "resets_at": ISO-8601 str|None,
                     "reset_text": str (only when resets_at is unknown),
                     "is_active": bool, "window_minutes": int|None,
                     "scope": {...} (weekly_scoped only)}],
         "source": "api"|"tui"|"rollout",   # where the numbers came from
         "refreshed": bool, "retries": int, # optional notes
         "error": str}                       # present => nothing else usable

    session_reset_at(account) -> (naive local datetime | None, info str)
        Exact moment the 5-hour session window resets. Read-only.

    live_sessions(account) -> [{"name": str, "cwd": str, "pid": int|None,
                                "id": str|None, "started": float}]
        Sessions that can receive a message right now, newest first.

    find_live_sessions(account, name, path=None) -> [session]
        Subset of live_sessions() with this name (and this real cwd if path).

    send_message(account, name, cwd, text) -> (status, detail)
        Deliver `text` to the live session called `name`.
        status: "sent" | "sent-limited" | "not-live" | "limited" | "failed"
        "sent-limited" = the session received it but the turn it started died
        on the usage limit. It is a DELIVERED status: never re-send the same
        text after it, or the session ends up with the instruction twice.

    anchor_ping(account, cwd) -> (status, output)
        Cheapest possible request that opens a usage window on the account
        (never touches a working session).  status: "ok"|"limited"|"failed"

    login_hint(account) -> str        # command to log in again
    resume_hint(account, session_name) -> str   # command to reopen a session

Provider modules must import only the stdlib.
"""
import importlib

PROVIDERS = ("claude", "codex")


def module(provider):
    return importlib.import_module(f"agentctl.providers.{provider}")


def discover_accounts(providers=PROVIDERS):
    """All logged-in accounts across providers, claude first."""
    out = []
    for p in providers:
        out.extend(module(p).discover())
    return out


def get_account(label, accounts=None):
    """Look an account up by its label (case-insensitive). None if unknown."""
    for a in accounts or discover_accounts():
        if a["label"].lower() == label.lower():
            return a
    return None


def provider_of(account):
    return module(account["provider"])
