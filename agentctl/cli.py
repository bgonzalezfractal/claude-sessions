"""`agentctl <command> ...` — every command is a module with main(argv) -> int."""
import importlib, sys

COMMANDS = [
    ("status",   "agentctl.status",   "usage limits for every account (read-only, anchors nothing)"),
    ("renew",    "agentctl.renew",    "auto-continue an interrupted session when its limit resets"),
    ("accounts", "agentctl.accounts", "list accounts across providers, or scaffold a new one"),
    ("copy",     "agentctl.copy",     "migrate a session between accounts"),
    ("log",      "agentctl.log",      "one chronological stream of everything agentctl did"),
    ("ui",       "agentctl.ui",       "localhost dashboard"),
    ("anchor",   "agentctl.anchor",   "run the morning window-anchor pings now"),
]


def usage():
    width = max(len(c[0]) for c in COMMANDS)
    lines = ["usage: agentctl <command> [args]", "",
             "Squeeze the most out of the usage windows of AI coding agents "
             "(Claude Code, Codex).", "", "commands:"]
    for name, _, help_ in COMMANDS:
        lines.append(f"  {name:<{width}}  {help_}")
    lines += ["", "`agentctl <command> --help` for each command's options."]
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    name, rest = argv[0], argv[1:]
    for cmd, module, _ in COMMANDS:
        if cmd == name:
            sys.argv = [f"agentctl {cmd}", *rest]        # argparse prog + args
            rc = importlib.import_module(module).main(rest)
            return 0 if rc is None else rc
    print(f"agentctl: unknown command {name!r}\n\n{usage()}", file=sys.stderr)
    return 2
