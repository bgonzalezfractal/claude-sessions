import os, subprocess, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'bin/anchor.sh'


class AnchorTests(unittest.TestCase):
    def _home(self, tmp):
        """A HOME with three claude accounts and one codex account."""
        home = Path(tmp)
        (home / '.claude').mkdir()
        (home / '.claude.json').write_text('{}')
        for name in ('benjr', 'bggai'):
            (home / f'.claude-{name}').mkdir()
            (home / f'.claude-{name}/.claude.json').write_text('{}')
        (home / '.codex').mkdir()
        (home / '.codex/auth.json').write_text('{"tokens":{"access_token":"x"}}')
        return home

    def test_account_routing_and_failure_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            claude = home / 'claude'
            claude.write_text('#!/bin/zsh\n'
                              '[[ "${CLAUDE_CONFIG_DIR:-}" == *benjr ]] && { print "Not logged in"; exit 1; }\n'
                              'print "hello ${CLAUDE_CONFIG_DIR:-default}"\n')
            claude.chmod(0o755)
            codex = home / 'codex'
            codex.write_text('#!/bin/zsh\n'
                             'print -r -- "codex-args: $@" >> "$HOME/codex-args"\n'
                             'print "hello codex"\n')
            codex.chmod(0o755)
            env = dict(os.environ, HOME=tmp, CLAUDE_BIN=str(claude), CODEX_BIN=str(codex),
                       CLAUDE_CONFIG_DIR='/wrong-inherited-account',
                       CODEX_HOME='/wrong-inherited-codex')
            result = subprocess.run(['zsh', str(SCRIPT)], env=env,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1, result.stderr)
            log = (home / '.agentctl/anchor.log').read_text()
            self.assertIn('[default] attempt=1 exit=0 hello default', log)
            self.assertIn('[benjr] attempt=1 exit=1 Not logged in', log)
            self.assertIn('[bggai] attempt=1 exit=0 hello', log)
            self.assertIn('[codex] attempt=1 exit=0 hello codex', log)
            self.assertNotIn('/wrong-inherited-account', log)
            self.assertNotIn('/wrong-inherited-codex', log)
            # The codex ping is a throwaway `codex exec`, never a session.
            args = (home / 'codex-args').read_text()
            self.assertIn('codex-args: exec', args)
            self.assertIn('--ephemeral', args)
            self.assertFalse((home / '.agentctl/anchor.lock').exists())

            claude.write_text('#!/bin/zsh\nprint hello\n')
            result = subprocess.run(['zsh', str(SCRIPT)], env=env,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_dry_run_lists_every_account_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            fake = home / 'boom'
            fake.write_text('#!/bin/zsh\nprint "SHOULD NOT RUN" >&2\nexit 3\n')
            fake.chmod(0o755)
            env = dict(os.environ, HOME=tmp, CLAUDE_BIN=str(fake), CODEX_BIN=str(fake))
            r = subprocess.run(['zsh', str(SCRIPT), '--dry-run'], env=env,
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)
            labels = [line.split(':', 1)[0] for line in r.stdout.splitlines()]
            self.assertEqual(labels, ['default', 'benjr', 'bggai', 'codex'])
            self.assertIn('provider=claude', r.stdout)
            self.assertIn('provider=codex', r.stdout)
            self.assertIn('--model haiku', r.stdout)
            self.assertIn('exec --ephemeral', r.stdout)
            self.assertFalse((home / '.agentctl/anchor.log').exists())

    def test_missing_config_is_a_failure_not_a_ping(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            (home / '.claude.json').write_text('')          # stubbed/emptied config
            fake = home / 'claude'
            fake.write_text('#!/bin/zsh\nprint hello\n')
            fake.chmod(0o755)
            codex = home / 'codex'
            codex.write_text('#!/bin/zsh\nprint "hello codex"\n')
            codex.chmod(0o755)
            env = dict(os.environ, HOME=tmp, CLAUDE_BIN=str(fake), CODEX_BIN=str(codex))
            r = subprocess.run(['zsh', str(SCRIPT)], env=env,
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 1, r.stderr)
            log = (home / '.agentctl/anchor.log').read_text()
            self.assertIn('[default] FAILED —', log)
            self.assertIn('[codex] attempt=1 exit=0 hello codex', log)


if __name__ == '__main__':
    unittest.main()
