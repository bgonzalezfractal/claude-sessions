import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentctl import status as S
from agentctl.providers import claude as C
from agentctl.providers import codex as X

G = C.__dict__
ACCOUNT = {'provider': 'claude', 'label': 'benjr', 'config_dir': '/unused',
           'suffix_dir': '/unused', 'shell_alias': 'claude-benjr'}


class StatusTests(unittest.TestCase):
    def test_screen_reader_duplicate_percent_and_redraw(self):
        rows = C.parse_tui('Current session 0% 0% used Resets 8:10am (Chile) Current week (all models) 63% 63% used Resets Sep 14 at 10pm (Chile) Refreshing… 64% 64% used Resets Sep 14 at 10pm (Chile) Current week (Fable) 71% 71% used Resets Sep 14 at 10pm (Chile)')
        self.assertEqual([r['percent'] for r in rows], [0, 64, 71])
        self.assertEqual(rows[2]['scope']['model']['display_name'], 'Fable')

    def test_expired_token_is_renewed_before_fetch(self):
        with patch.dict(G, get_email=lambda _: 'email', read_oauth=Mock(side_effect=[({'accessToken': 'old', 'expiresAt': 1}, None), ({'accessToken': 'new', 'expiresAt': 9999999999999}, None)]), refresh_token=lambda *_: 'interactive', fetch_usage=Mock(return_value=({'limits': []}, 0))):
            result = C.usage(ACCOUNT)
            G['fetch_usage'].assert_called_once_with('new')
            self.assertTrue(result['refreshed'])

    def test_failed_renewal_does_not_fetch_expired_token(self):
        with patch.dict(G, get_email=lambda _: 'email', read_oauth=lambda _: ({'accessToken': 'old', 'expiresAt': 1}, None), refresh_token=lambda *_: None, fetch_usage=Mock()):
            result = C.usage(ACCOUNT)
            self.assertIn('claude-benjr auth login', result['error'])
            G['fetch_usage'].assert_not_called()

    def test_429_uses_tui_without_repeated_api_calls(self):
        get = Mock(side_effect=HTTPError('url', 429, 'rate limit', {}, None))
        with patch.dict(G, get_email=lambda _: 'email', read_oauth=lambda _: ({'accessToken': 'valid'}, None), fetch_usage=get, tui_status=lambda *_: [{'kind': 'session', 'percent': 7}]):
            result = C.usage(ACCOUNT)
            self.assertEqual(result['source'], 'tui')
            get.assert_called_once()

    def test_json_uses_shared_recovery(self):
        import contextlib, io, json
        usage = Mock(return_value={'email': 'email', 'limits': []})
        with patch.dict(S.__dict__, discover_accounts=lambda *a, **k: [ACCOUNT], provider_of=lambda a: Mock(usage=usage)), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(S.main(['--json']), 0)
            usage.assert_called_once_with(ACCOUNT)
            self.assertIn('benjr', json.loads(output.getvalue()))

    def test_codex_window_shape(self):
        w = X._window('session', {'used_percent': 100, 'limit_window_seconds': 18000,
                                  'reset_at': 1789367328}, True)
        self.assertEqual(w['percent'], 100)
        self.assertTrue(w['is_active'])
        self.assertEqual(w['window_minutes'], 300)
        self.assertEqual(w['resets_at'], '2026-09-14T06:28:48Z')


    def test_codex_kind_comes_from_window_length_not_slot(self):
        # Free plan: primary_window is 30 days, there is no 5 h window.
        w = X._window('session', {'used_percent': 3, 'limit_window_seconds': 2592000,
                                  'reset_at': 1789367328})
        self.assertEqual(w['kind'], 'period')
        self.assertEqual(S.limit_label(w), '30-day')
        week = X._window('session', {'used_percent': 1, 'limit_window_seconds': 604800})
        self.assertEqual(week['kind'], 'weekly_all')
        self.assertEqual(X._window('weekly_all', {'used_percent': 1})['kind'], 'weekly_all')

    def test_codex_free_plan_has_no_session_window(self):
        u = {'email': 'e', 'limits': [X._window('session', {
            'used_percent': 3, 'limit_window_seconds': 2592000, 'reset_at': 1789367328})]}
        with patch.object(X, 'usage', lambda acct: u):
            self.assertEqual(X.session_reset_at({}), (None, 'no session limit in response'))

if __name__ == '__main__':
    unittest.main()


ACCOUNT_CODEX = {"provider": "codex", "label": "codex",
                 "config_dir": "/unused", "suffix_dir": None,
                 "shell_alias": "codex"}


class CodexDeliveryTests(unittest.TestCase):
    """A queued message that lands and then dies on the limit is DELIVERED.
    Calling that 'limited' made the daemon retry and duplicate the
    instruction on a live session (seen live 2026-09-14 03:37)."""

    def _send(self, wait_result):
        from agentctl.providers import codex
        run = unittest.mock.Mock(return_value=SimpleNamespace(
            stdout="Queued message abc for thread xyz.", stderr="", returncode=0))
        with patch.object(codex.subprocess, "run", run), \
             patch.object(codex, "_wait_for_rollout_text", lambda *a, **k: wait_result):
            return codex.send_message(ACCOUNT_CODEX, "new-acp", "/tmp", "continue please")

    def test_seen_then_limited_is_sent_limited(self):
        status, detail = self._send((True, True))
        self.assertEqual(status, "sent-limited")
        self.assertIn("hit the usage limit", detail)

    def test_seen_is_sent(self):
        self.assertEqual(self._send((True, False))[0], "sent")

    def test_unconfirmed_is_still_sent(self):
        status, detail = self._send((False, False))
        self.assertEqual(status, "sent")
        self.assertIn("queued", detail)

    def test_unpicked_message_on_a_stale_session_is_flagged(self):
        from agentctl.providers import codex
        run = unittest.mock.Mock(return_value=SimpleNamespace(
            stdout="Queued message abc.", stderr="", returncode=0))
        with patch.object(codex.subprocess, "run", run), \
             patch.object(codex, "_wait_for_rollout_text", lambda *a, **k: (False, False)), \
             patch.object(codex, "_transcript_idle_since", lambda *a: 18000):
            status, detail = codex.send_message(ACCOUNT_CODEX, "new-acp", "/tmp", "go")
        self.assertEqual(status, "sent")
        self.assertIn("NOT PICKED UP", detail)
