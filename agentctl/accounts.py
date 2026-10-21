"""
agentctl accounts — list the agent-CLI accounts on this Mac, or scaffold a new one.

    agentctl accounts list [--json] [--provider claude|codex]
    agentctl accounts add <alias> [--provider claude|codex]

An *account* is one config dir of one provider:

    claude   ~/.claude (default) or ~/.claude-<alias>, logged in once it has a
             .claude.json inside. The default account's live config is
             ~/.claude.json, NOT ~/.claude/.claude.json.
    codex    ~/.codex (default) or ~/.codex-<alias>, logged in once it has an
             auth.json inside.

`list` shows the logged-in accounts (providers.discover_accounts()) plus the
dirs that exist but have never logged in, so a half-finished `add` is visible.
`add` creates the dir and the shell alias, then tells you how to log in — login
itself is interactive and cannot be automated.

Every other command discovers accounts the same way, so a new account joins
them automatically after its first login.
"""
import argparse, base64, glob, hashlib, json, os, re, subprocess, sys

from agentctl import providers

HOME = os.path.expanduser("~")
ZSHRC = os.path.join(HOME, ".zshrc")
# dirs that look like claude accounts but aren't OAuth accounts
NOT_ACCOUNTS = {".claude-deepinfra", ".claude-session-backups"}

ALIAS_TEMPLATE = {
    "claude": "alias claude-{name}='CLAUDE_CONFIG_DIR=$HOME/.claude-{name} claude'",
    "codex":  "alias codex-{name}='CODEX_HOME=$HOME/.codex-{name} codex'",
}


# ----------------------------------------------------------------- helpers ---
def keychain_has(service):
    return subprocess.run(["security", "find-generic-password", "-s", service],
                          capture_output=True).returncode == 0


def keychain_service(suffix_dir):
    """Claude keychain service name; the default account has no dir suffix."""
    if not suffix_dir:
        return "Claude Code-credentials"
    return "Claude Code-credentials-" + hashlib.sha256(suffix_dir.encode()).hexdigest()[:8]


def zshrc_aliases():
    try:
        txt = open(ZSHRC, errors="replace").read()
    except OSError:
        return {}
    return dict(re.findall(r"^alias ((?:claude|codex)-[\w-]+)='([^']+)'", txt, re.M))


def claude_email(cfg_dir):
    """oauthAccount.emailAddress — the default account reads ~/.claude.json."""
    default = os.path.join(HOME, ".claude.json")
    path = (default if cfg_dir == os.path.join(HOME, ".claude")
            and os.path.isfile(default) else os.path.join(cfg_dir, ".claude.json"))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, errors="replace") as f:
            return (json.load(f).get("oauthAccount") or {}).get("emailAddress")
    except (OSError, ValueError):
        return None


def _jwt_payload(token):
    """Decode a JWT's payload without verifying it (we only want the email)."""
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg.encode()))
    except Exception:
        return {}


def codex_auth(cfg_dir):
    try:
        with open(os.path.join(cfg_dir, "auth.json"), errors="replace") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def codex_email(cfg_dir):
    tokens = (codex_auth(cfg_dir).get("tokens") or {})
    claims = _jwt_payload(tokens.get("id_token") or "")
    email = claims.get("email")
    if not email:
        profile = claims.get("https://api.openai.com/profile") or {}
        if isinstance(profile, dict):
            email = profile.get("email")
    return email or None


def provider_module(name):
    """The provider module, or None when it isn't importable (yet)."""
    try:
        return providers.module(name)
    except Exception:
        return None


def live_count(account):
    mod = provider_module(account["provider"])
    if not mod:
        return 0
    try:
        return len(mod.live_sessions(account))
    except Exception:
        return 0


# -------------------------------------------------------------------- rows ---
def _row(provider, label, cfg_dir, suffix_dir, shell_alias, account=None):
    if provider == "claude":
        email = claude_email(cfg_dir)
        logged_in = bool(email)
        keychain = keychain_has(keychain_service(suffix_dir))
    else:
        tokens = codex_auth(cfg_dir).get("tokens") or {}
        keychain = bool(tokens.get("access_token"))
        logged_in = keychain
        email = codex_email(cfg_dir) if logged_in else None
    acct = account or {"provider": provider, "label": label, "config_dir": cfg_dir,
                       "suffix_dir": suffix_dir, "shell_alias": shell_alias}
    return {"provider": provider, "alias": label, "dir": cfg_dir,
            "shell_alias": shell_alias, "email": email, "logged_in": logged_in,
            "keychain": keychain,
            "live_sessions": live_count(acct) if logged_in else 0}


def account_rows(only_provider=None):
    """Every account dir on this Mac — logged in or not, claude first."""
    try:
        discovered = providers.discover_accounts()
    except Exception:
        discovered = []
        for p in providers.PROVIDERS:
            mod = provider_module(p)
            if mod:
                try:
                    discovered.extend(mod.discover())
                except Exception:
                    pass
    by_dir = {os.path.normpath(a["config_dir"]): a for a in discovered}
    aliases = zshrc_aliases()
    rows = []

    def add(provider, label, cfg_dir, suffix_dir, shell_alias):
        if only_provider and provider != only_provider:
            return
        acct = by_dir.get(os.path.normpath(cfg_dir))
        if acct:
            label, suffix_dir = acct["label"], acct.get("suffix_dir")
        rows.append(_row(provider, label, cfg_dir, suffix_dir, shell_alias, acct))

    add("claude", "default", os.path.join(HOME, ".claude"), None, "claude")
    for d in sorted(glob.glob(os.path.join(HOME, ".claude-*"))):
        base = os.path.basename(d)
        if base in NOT_ACCOUNTS or not os.path.isdir(d):
            continue
        name = base[len(".claude-"):]
        add("claude", name, d, d, f"claude-{name}" if f"claude-{name}" in aliases else None)

    codex_default = os.path.join(HOME, ".codex")
    if os.path.isdir(codex_default):
        add("codex", "codex", codex_default, None, "codex")
    for d in sorted(glob.glob(os.path.join(HOME, ".codex-*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)[len(".codex-"):]
        add("codex", f"codex-{name}", d, d,
            f"codex-{name}" if f"codex-{name}" in aliases else None)
    return rows


# ---------------------------------------------------------------- commands ---
def cmd_list(a):
    rows = account_rows(a.provider)
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        login_hint = ("run the alias once and /login" if r["provider"] == "claude"
                      else "run the alias once, then `codex login`")
        state = f"✓ {r['email']}" if r["logged_in"] else f"✗ not logged in — {login_hint}"
        kc = "" if r["keychain"] else ("  (no keychain credential)"
                                       if r["provider"] == "claude" else "  (no token)")
        live = f"  {r['live_sessions']} live" if r["live_sessions"] else ""
        sa = r["shell_alias"] or "NO SHELL ALIAS"
        print(f"{r['alias']:<10} {r['provider']:<7} {sa:<14} {state}{kc}{live}")
    return 0


def cmd_add(a):
    name = a.alias.lower()
    provider = a.provider or "claude"
    if not re.fullmatch(r"[a-z0-9][\w-]*", name):
        sys.exit(f"bad alias {a.alias!r} — letters/digits/dashes only")
    label = name if provider == "claude" else f"codex-{name}"
    taken = {r["alias"].lower() for r in account_rows()} | {"default", "codex"}
    if label.lower() in taken:
        sys.exit(f"account {label!r} already exists")
    d = os.path.join(HOME, f".{provider}-{name}")
    if os.path.exists(d):
        sys.exit(f"{d} already exists")
    os.makedirs(d)
    shell_alias = f"{provider}-{name}"
    line = ALIAS_TEMPLATE[provider].format(name=name)
    if shell_alias in zshrc_aliases():
        print(f"~/.zshrc already has {shell_alias}")
    else:
        with open(ZSHRC, "a") as f:
            f.write(f"\n# {provider} account '{name}' (agentctl accounts add, auto)\n{line}\n")
        print(f"created {d} and added {shell_alias} to ~/.zshrc")
    login = "log in with /login" if provider == "claude" else "run `codex login`"
    note = (f"\nNote: do NOT create {d}/.claude.json by hand — the first login writes it."
            if provider == "claude"
            else f"\nNote: do NOT create {d}/auth.json by hand — `codex login` writes it.")
    print(f"""
Next steps:
  1. open a NEW terminal (or `source ~/.zshrc`)
  2. run `{shell_alias}` and {login}
  3. after login the account is auto-discovered by agentctl status / renew /
     anchor — nothing else to do.
{note}""")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agentctl accounts",
        description="List or scaffold the accounts of every agent CLI.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="show all accounts and their state")
    p.add_argument("--json", action="store_true")
    p.add_argument("--provider", choices=providers.PROVIDERS,
                   help="only this provider's accounts")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("add", help="scaffold a new account alias")
    p.add_argument("alias")
    p.add_argument("--provider", choices=providers.PROVIDERS, default="claude")
    p.set_defaults(fn=cmd_add)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
