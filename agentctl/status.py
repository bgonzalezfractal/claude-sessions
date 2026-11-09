"""`agentctl status` — usage limits for every account, non-interactively.

Provider-agnostic: the accounts come from agentctl.providers.discover_accounts()
and every number comes from that account's provider.usage(). Read-only — it
never anchors a usage window (Claude reads the /status endpoint, Codex the
ChatGPT usage endpoint).

    agentctl status                 # all accounts, bars
    agentctl status bggai codex     # only these labels (any provider)
    agentctl status --provider codex
    agentctl status --json
    agentctl status --interactive   # Claude only: drive the /status Usage tab
"""
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from agentctl.providers import PROVIDERS, discover_accounts, provider_of

BOLD, DIM, RED, YEL, GRN, RST = "\033[1m", "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[0m"
CYN = "\033[96m"  # bright cyan — session (5h) bar, the one that matters day-to-day


# ----------------------------------------------------------------- collect ---
def account_usage(account, interactive=False):
    """provider.usage(), with --interactive only where it means something."""
    p = provider_of(account)
    if interactive and account.get("provider") == "claude":
        return p.usage(account, interactive=True)
    return p.usage(account)


def collect(account, interactive=False):
    """-> (label, provider, email, [rendered lines], ok)."""
    label, provider = account["label"], account.get("provider", "?")
    usage = account_usage(account, interactive)
    email = usage.get("email", "?")
    if "error" in usage:
        return label, provider, email, [f"  {RED}✗ {usage['error']}{RST}"], False
    lines = render_rows(usage.get("limits") or []) or [f"  {DIM}(no limits reported){RST}"]
    extra = usage.get("extra_usage") or {}
    if extra.get("is_enabled"):
        lines.append(f"  {DIM}extra usage on: {extra.get('used_credits', 0)} used{RST}")
    if usage.get("plan"):
        lines.append(f"  {DIM}plan: {usage['plan']}{RST}")
    notes = []
    if usage.get("source") == "tui":
        notes.append("via interactive /status")
    if usage.get("source") == "rollout" and usage.get("note"):
        notes.append(usage["note"])
    if usage.get("refreshed"):
        notes.append("refreshed token")
    if usage.get("retries"):
        notes.append(f"retried {usage['retries']}×")
    if notes:
        lines.append(f"  {DIM}↺ {', '.join(notes)}{RST}")
    return label, provider, email, lines, True


# ------------------------------------------------------------------ render ---
def fmt_reset(iso):
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    now = datetime.now(timezone.utc).astimezone()
    day = "" if t.date() == now.date() else t.strftime("%a ")
    return f"resets {day}{t.strftime('%H:%M')}"


def bar(pct, width=20, base=GRN):
    filled = min(width, round(pct / 100 * width))
    color = base if pct < 60 else YEL if pct < 85 else RED
    return f"{color}{'█' * filled}{DIM}{'░' * (width - filled)}{RST}"


def limit_label(l):
    kind = l.get("kind")
    if kind == "session":
        return "session (5h)"
    if kind == "weekly_all":
        return "weekly"
    if kind == "weekly_scoped":
        scope = ((l.get("scope") or {}).get("model") or {}).get("display_name")
        return f"weekly {scope}" if scope else "weekly Fable"
    if kind == "period" and l.get("window_minutes"):
        return f"{round(l['window_minutes'] / 1440)}-day"
    return kind or "?"


def render_rows(rows):
    """Bar lines from either API limits (resets_at) or TUI rows (reset_text)."""
    lines = []
    for l in rows:
        pct = l.get("percent") or 0
        active = f" {RED}◀ LIMITED{RST}" if l.get("is_active") and pct >= 100 else ""
        base = CYN if l.get("kind") == "session" else GRN
        when = fmt_reset(l.get("resets_at")) if l.get("resets_at") \
            else l.get("reset_text", "")
        lines.append(f"  {limit_label(l):<14} {bar(pct, base=base)} {pct:>3}%  "
                     f"{DIM}{when}{RST}{active}")
    return lines


# -------------------------------------------------------------------- main ---
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agentctl status",
        description="Usage limits for every account, across providers.")
    ap.add_argument("accounts", nargs="*", metavar="ACCOUNT",
                    help="only these account labels (default: all)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--interactive", action="store_true",
                    help="Claude only: read the /status Usage tab directly")
    ap.add_argument("--provider", choices=PROVIDERS, help="only this provider")
    args = ap.parse_args(argv)

    accounts = discover_accounts((args.provider,)) if args.provider else discover_accounts()
    wanted = accounts
    if args.accounts:
        pick = [a.lower() for a in args.accounts]
        known = {a["label"].lower() for a in accounts}
        missing = [a for a in pick if a not in known]
        if missing:
            print(f"no such account: {', '.join(missing)}", file=sys.stderr)
            return 2
        wanted = [a for a in accounts if a["label"].lower() in set(pick)]

    # One account's slow retry must not hold up the others.
    with ThreadPoolExecutor(max_workers=max(1, len(wanted))) as pool:
        if args.json:
            usages = list(pool.map(lambda a: account_usage(a, args.interactive), wanted))
            out = {a["label"]: u for a, u in zip(wanted, usages)}
            print(json.dumps(out, indent=2))
            return 1 if any("error" in u for u in out.values()) else 0
        results = list(pool.map(lambda a: collect(a, args.interactive), wanted))

    failed = False
    for label, provider, email, lines, ok in results:
        print(f"\n{BOLD}{label}{RST} {DIM}{provider} · {email}{RST}")
        for line in lines:
            print(line)
        failed |= not ok
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
