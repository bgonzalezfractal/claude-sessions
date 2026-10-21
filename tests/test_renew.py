import os
import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentctl import renew  # noqa: E402


def fake_provider(send=("sent", "queued"), sessions=None, reset=None,
                  anchor=("ok", "hello")):
    """A provider module stand-in implementing the bits `renew` calls."""
    sessions = [{"name": "new-acp", "cwd": "/tmp", "pid": 1, "id": "x",
                 "started": 0}] if sessions is None else sessions
    return SimpleNamespace(
        live_sessions=lambda acct: list(sessions),
        find_live_sessions=lambda acct, name, path=None: list(sessions),
        send_message=lambda acct, name, cwd, text: send,
        anchor_ping=lambda acct, cwd: anchor,
        session_reset_at=lambda acct: reset)


ACCOUNT = {"provider": "codex", "label": "codex", "config_dir": "/tmp/.codex",
           "suffix_dir": None, "shell_alias": "codex"}


class TempState(unittest.TestCase):
    """Every test writes registry/log/pid into a throwaway dir."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="renew-test-")
        self.patches = [
            patch.object(renew, "BASE", self.dir),
            patch.object(renew, "REGISTRY", os.path.join(self.dir, "registry.json")),
            patch.object(renew, "PIDFILE", os.path.join(self.dir, "daemon.pid")),
            patch.object(renew, "LOG", os.path.join(self.dir, "renew.log")),
            patch.object(renew, "LEGACY_REGISTRY", os.path.join(self.dir, "nope.json")),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def logged(self):
        with open(os.path.join(self.dir, "renew.log")) as f:
            return f.read()


class MessageTests(unittest.TestCase):
    def test_default_message(self):
        self.assertEqual(renew.continue_message(),
                         renew.PREFIX + renew.DEFAULT_CONTINUE)

    def test_query_replaces_the_default_tail(self):
        self.assertEqual(renew.continue_message("Continue, and consider X"),
                         renew.PREFIX + "Continue, and consider X")

    def test_message_overrides_everything(self):
        self.assertEqual(renew.continue_message(query="x", message="verbatim"),
                         "verbatim")


class ParseTests(unittest.TestCase):
    def test_parse_clock_wall_time_is_in_the_future(self):
        t = renew.parse_clock("5:40am")
        self.assertEqual((t.hour, t.minute), (5, 40))
        self.assertGreater(t, datetime.now())

    def test_parse_followup_offset(self):
        base = datetime(2026, 9, 14, 5, 45)
        self.assertEqual(renew.parse_followup("+5h5m", base),
                         datetime(2026, 9, 14, 10, 50))
        self.assertIsNone(renew.parse_followup("off", base))


class FireTests(TempState):
    def entry_and_action(self):
        entry = {"provider": "codex", "account": "codex", "session": "new-acp",
                 "path": "/tmp"}
        action = {"kind": "continue", "at": datetime.now().isoformat(timespec="seconds"),
                  "message": "go on", "retries": 0, "done": False}
        return entry, action

    def fire_with(self, prov, entry, action):
        with patch.object(renew, "get_account", lambda label: ACCOUNT), \
             patch.object(renew, "provider_of", lambda acct: prov):
            renew.fire(entry, action)

    def test_sent_marks_the_action_done(self):
        entry, action = self.entry_and_action()
        self.fire_with(fake_provider(send=("sent", "queued")), entry, action)
        self.assertTrue(action["done"])
        self.assertEqual(action["retries"], 0)
        self.assertIn("FIRED continue -> codex/new-acp", self.logged())

    def test_sent_limited_rearms_instead_of_duplicating(self):
        """Delivered, then the turn died on the limit: close the action and
        arm a NEW continue at the next reset — never re-send the same text."""
        entry, action = self.entry_and_action()
        entry["delay"] = 5
        entry["expires_at"] = (datetime.now() + timedelta(hours=1)
                               ).isoformat(timespec="seconds")
        entry["actions"] = [action]
        reset = datetime.now().replace(microsecond=0) + timedelta(hours=5)
        prov = fake_provider(send=("sent-limited", "submitted, but the turn hit "
                                   "the usage limit"),
                             reset=(reset, "100% used, LIMITED"))
        self.fire_with(prov, entry, action)

        self.assertTrue(action["done"])
        self.assertEqual(action["retries"], 0)          # not a retry, a re-arm
        self.assertEqual(len(entry["actions"]), 2)
        armed = entry["actions"][1]
        self.assertEqual(armed["message"], action["message"])
        self.assertEqual(datetime.fromisoformat(armed["at"]),
                         reset + timedelta(minutes=5))
        self.assertEqual(armed["rearms"], 1)
        # the entry must outlive the action it now carries
        self.assertGreater(datetime.fromisoformat(entry["expires_at"]),
                           reset + timedelta(minutes=5))
        self.assertIn("re-armed continue 1/3", self.logged())

    def test_rearm_stops_after_max_rearms(self):
        entry, action = self.entry_and_action()
        entry["actions"] = [action]
        entry["expires_at"] = (datetime.now() + timedelta(hours=9)
                               ).isoformat(timespec="seconds")
        action["rearms"] = renew.MAX_REARMS
        prov = fake_provider(send=("sent-limited", "hit the usage limit"),
                             reset=(datetime.now() + timedelta(hours=5), "100%"))
        self.fire_with(prov, entry, action)
        self.assertEqual(len(entry["actions"]), 1)
        self.assertIn("not re-arming", self.logged())

    def test_rearm_without_a_future_reset_is_skipped(self):
        entry, action = self.entry_and_action()
        entry["actions"] = [action]
        entry["expires_at"] = (datetime.now() + timedelta(hours=9)
                               ).isoformat(timespec="seconds")
        prov = fake_provider(send=("sent-limited", "hit the usage limit"),
                             reset=(None, "no active session window"))
        self.fire_with(prov, entry, action)
        self.assertEqual(len(entry["actions"]), 1)
        self.assertIn("can't re-arm", self.logged())

    def test_not_live_reschedules_ten_minutes_out(self):
        entry, action = self.entry_and_action()
        self.fire_with(fake_provider(send=("not-live", "No active session")),
                       entry, action)
        self.assertFalse(action["done"])
        self.assertEqual(action["retries"], 1)
        delta = datetime.fromisoformat(action["at"]) - datetime.now()
        self.assertAlmostEqual(delta.total_seconds() / 60, renew.RETRY_MINUTES,
                               delta=1)

    def test_no_live_session_skips_send_message(self):
        entry, action = self.entry_and_action()
        prov = fake_provider(sessions=[])
        prov.send_message = lambda *a, **k: self.fail("send_message called")
        self.fire_with(prov, entry, action)
        self.assertEqual(action["retries"], 1)
        self.assertIn("not live", self.logged())

    def test_follow_up_uses_anchor_ping(self):
        entry, action = self.entry_and_action()
        action["kind"] = "follow-up"
        self.fire_with(fake_provider(anchor=("ok", "hello")), entry, action)
        self.assertTrue(action["done"])
        self.assertIn("FIRED follow-up (anchor ping) -> codex", self.logged())

    def test_limited_follow_up_reschedules(self):
        entry, action = self.entry_and_action()
        action["kind"] = "follow-up"
        self.fire_with(fake_provider(anchor=("limited", "usage limit")), entry, action)
        self.assertFalse(action["done"])
        self.assertEqual(action["retries"], 1)


class AddTests(TempState):
    def registry(self):
        with open(os.path.join(self.dir, "registry.json")) as f:
            return json.load(f)

    def test_add_writes_the_entry_without_spawning_a_daemon(self):
        reset = (datetime.now() + timedelta(hours=1)).replace(microsecond=0)
        prov = fake_provider(reset=(reset, "0% used"))
        args = SimpleNamespace(account="codex", session="new-acp", path=None,
                               reset="auto", delay=5, follow_up="+5h5m",
                               query=None, message=None,
                               follow_up_message=renew.DEFAULT_FOLLOWUP, ttl=24)
        with patch.object(renew, "get_account", lambda label: ACCOUNT), \
             patch.object(renew, "provider_of", lambda acct: prov), \
             patch.object(renew, "ensure_daemon") as daemon:
            renew.cmd_add(args)
        daemon.assert_called_once()          # stubbed: no real daemon process

        entries = self.registry()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["provider"], "codex")
        self.assertEqual((e["account"], e["session"]), ("codex", "new-acp"))
        self.assertEqual(e["path"], os.path.realpath("/tmp"))
        cont, follow = e["actions"]
        continue_at = reset + timedelta(minutes=5)
        self.assertEqual(cont["message"], renew.PREFIX + renew.DEFAULT_CONTINUE)
        self.assertEqual(datetime.fromisoformat(cont["at"]), continue_at)
        self.assertEqual(datetime.fromisoformat(follow["at"]),
                         continue_at + timedelta(hours=5, minutes=5))
        self.assertEqual(follow["kind"], "follow-up")

    def test_add_with_query_builds_the_message(self):
        reset = (datetime.now() + timedelta(hours=1)).replace(microsecond=0)
        prov = fake_provider(reset=(reset, "0% used"))
        args = SimpleNamespace(account="codex", session="new-acp", path=None,
                               reset="auto", delay=5, follow_up="off",
                               query="Continue, and consider X", message=None,
                               follow_up_message=renew.DEFAULT_FOLLOWUP, ttl=24)
        with patch.object(renew, "get_account", lambda label: ACCOUNT), \
             patch.object(renew, "provider_of", lambda acct: prov), \
             patch.object(renew, "ensure_daemon"):
            renew.cmd_add(args)
        e = self.registry()[0]
        self.assertEqual(len(e["actions"]), 1)
        self.assertEqual(e["actions"][0]["message"],
                         renew.PREFIX + "Continue, and consider X")


if __name__ == "__main__":
    unittest.main()
