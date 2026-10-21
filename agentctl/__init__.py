"""agentctl — one CLI for the usage windows of AI coding-agent CLIs
(Claude Code, Codex; desktop apps later).

Layout:
  agentctl/providers/   one module per provider (claude, codex) behind the
                        contract documented in providers/__init__.py
  agentctl/status.py    `agentctl status`   usage limits per account
  agentctl/renew.py     `agentctl renew`    auto-continue at limit reset
  agentctl/accounts.py  `agentctl accounts` list / scaffold accounts
  agentctl/copy.py      `agentctl copy`     migrate a session between accounts
  agentctl/log.py       `agentctl log`      merged activity stream
  agentctl/ui.py        `agentctl ui`       localhost dashboard
  bin/agentctl          the dispatcher symlinked into /opt/homebrew/bin
  bin/anchor.sh         the launchd morning anchor job (zsh; copied to ~/.agentctl)

Runtime state lives in ~/.agentctl/ (see agentctl.paths).
"""
import os

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".agentctl")          # registry, logs, pid, ui db
RENEW_DIR = os.path.join(STATE_DIR, "renew")
RENEW_REGISTRY = os.path.join(RENEW_DIR, "registry.json")
RENEW_PIDFILE = os.path.join(RENEW_DIR, "daemon.pid")
RENEW_LOG = os.path.join(RENEW_DIR, "renew.log")
ANCHOR_LOG = os.path.join(STATE_DIR, "anchor.log")   # morning anchor job (bin/anchor.sh)
UI_DB = os.path.join(STATE_DIR, "ui.db")
