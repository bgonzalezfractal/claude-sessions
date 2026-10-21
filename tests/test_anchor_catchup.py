"""`agentctl anchor` must queue a catch-up for accounts it could not anchor.

A 5-hour window is fixed from its first request, so a ping sent while one is
running is absorbed. Reproduced live on 2026-09-14: the 07:30 job pinged all
three Claude accounts successfully, but only the idle one (benjr) got a window.
"""
import os
import sys
import unittest
from datetime import datetime, time as dtime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentctl import anchor  # noqa: E402

IDLE = {"provider": "claude", "label": "benjr", "config_dir": "/x",
        "suffix_dir": "/x", "shell_alias": "claude-benjr"}
BUSY = {"provider": "claude", "label": "default", "config_dir": "/y",
        "suffix_dir": None, "shell_alias": "claude"}


def provider_returning(reset):
    return SimpleNamespace(session_reset_at=lambda acct: reset)


class CatchUpTests(unittest.TestCase):
    def run_anchor(self, accounts, resets, argv=()):
        self.scheduled = []
        self.pinged = []
        self.daemons = []
        provider = lambda acct: provider_returning(resets[acct["label"]])
        with patch.object(anchor, "discover_accounts", lambda: accounts), \
             patch.object(anchor, "provider_of", provider), \
             patch.object(anchor, "run_pings",
                          lambda argv: self.pinged.append(argv) or 0), \
             patch.object(anchor.renew, "schedule_account_anchor",
                          lambda acct, at, **kw: self.scheduled.append((acct["label"], at))), \
             patch.object(anchor.renew, "ensure_daemon",
                          lambda: self.daemons.append(1)):
            return anchor.main(list(argv))

    def test_mid_window_account_gets_a_catch_up_at_its_reset(self):
        reset = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=2)
        rc = self.run_anchor([IDLE, BUSY],
                             {"benjr": (None, "no active session window"),
                              "default": (reset, "40% used")})
        self.assertEqual(rc, 0)
        self.assertEqual(self.pinged, [[]])             # everyone still pinged
        self.assertEqual([label for label, _ in self.scheduled], ["default"])
        self.assertEqual(self.scheduled[0][1],
                         reset + timedelta(minutes=anchor.LEAD_MINUTES))
        self.assertEqual(self.daemons, [1])          # spawned once, not per account

    def test_daemon_is_spawned_once_for_many_accounts(self):
        reset = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=2)
        busy2 = dict(BUSY, label="bggai")
        self.run_anchor([BUSY, busy2],
                        {"default": (reset, "40%"), "bggai": (reset, "5%")})
        self.assertEqual(len(self.scheduled), 2)
        self.assertEqual(self.daemons, [1])

    def test_no_daemon_when_nothing_was_queued(self):
        self.run_anchor([IDLE], {"benjr": (None, "idle")})
        self.assertEqual(self.daemons, [])

    def test_idle_accounts_are_never_queued(self):
        self.run_anchor([IDLE], {"benjr": (None, "no active session window")})
        self.assertEqual(self.scheduled, [])

    def test_expired_window_counts_as_idle(self):
        past = datetime.now() - timedelta(minutes=5)
        self.run_anchor([BUSY], {"default": (past, "stale")})
        self.assertEqual(self.scheduled, [])

    def test_catch_up_after_the_cutoff_is_skipped(self):
        late = datetime.now().replace(hour=23, minute=30, second=0, microsecond=0)
        self.run_anchor([BUSY], {"default": (late, "10% used")})
        self.assertEqual(self.scheduled, [])

    def test_no_catch_up_flag_skips_the_usage_reads(self):
        called = []
        with patch.object(anchor, "discover_accounts", lambda: called.append(1) or []), \
             patch.object(anchor, "run_pings", lambda argv: 0):
            anchor.main(["--no-catch-up"])
        self.assertEqual(called, [])

    def test_a_broken_provider_does_not_stop_the_pings(self):
        def boom(acct):
            raise RuntimeError("keychain locked")
        with patch.object(anchor, "discover_accounts", lambda: [BUSY]), \
             patch.object(anchor, "provider_of",
                          lambda acct: SimpleNamespace(session_reset_at=boom)), \
             patch.object(anchor, "run_pings", lambda argv: 0) as _:
            self.assertEqual(anchor.main([]), 0)

    def test_dry_run_only_delegates(self):
        seen = []
        with patch.object(anchor, "run_pings", lambda argv: seen.append(argv) or 0), \
             patch.object(anchor, "discover_accounts",
                          lambda: (_ for _ in ()).throw(AssertionError("read usage"))):
            anchor.main(["--dry-run"])
        self.assertEqual(seen, [["--dry-run"]])


if __name__ == "__main__":
    unittest.main()
