"""`agentctl update` fast-forwards the clone and nothing else."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentctl import update  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(repo, path, text, msg):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    sh(repo, "add", path)
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.remote, self.clone, self.other = root / "remote.git", root / "clone", root / "other"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.other)],
                       check=True, capture_output=True)
        commit(self.other, "README.md", "v1\n", "v1")
        sh(self.other, "push", "-q", "origin", "HEAD:main")
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.clone)], check=True)
        self.installs = []

    def tearDown(self):
        self.tmp.cleanup()

    def push(self, path, text, msg):
        commit(self.other, path, text, msg)
        sh(self.other, "push", "-q", "origin", "HEAD:main")

    def run_update(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(update, "REPO_ROOT", str(self.clone)), \
             patch.object(update.subprocess, "call",
                          lambda cmd: self.installs.append(cmd) or 0), \
             patch.object(update, "daemon_pid", lambda: None), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = update.main(list(argv))
        return rc, out.getvalue() + err.getvalue()

    def test_up_to_date(self):
        rc, out = self.run_update()
        self.assertEqual(rc, 0)
        self.assertIn("up to date", out)

    def test_fast_forwards_without_reinstall_for_code_changes(self):
        self.push("agentctl/x.py", "x = 1\n", "code change")
        rc, out = self.run_update()
        self.assertEqual(rc, 0, out)
        self.assertIn("code change", out)
        self.assertTrue((self.clone / "agentctl/x.py").exists())
        self.assertEqual(self.installs, [])

    def test_reinstalls_when_anchor_script_changed(self):
        self.push("bin/anchor.sh", "#!/bin/zsh\n", "anchor change")
        rc, out = self.run_update()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.installs), 1)
        self.assertTrue(self.installs[0][-1].endswith("install.sh"))

    def test_check_changes_nothing(self):
        self.push("agentctl/x.py", "x = 1\n", "code change")
        head = sh(self.clone, "rev-parse", "HEAD")
        rc, out = self.run_update("--check")
        self.assertEqual(rc, 0)
        self.assertIn("1 new commit", out)
        self.assertEqual(sh(self.clone, "rev-parse", "HEAD"), head)

    def test_refuses_uncommitted_changes(self):
        self.push("agentctl/x.py", "x = 1\n", "code change")
        (self.clone / "README.md").write_text("local edit\n")
        rc, out = self.run_update()
        self.assertEqual(rc, 1)
        self.assertIn("uncommitted", out)
        self.assertEqual((self.clone / "README.md").read_text(), "local edit\n")

    def test_refuses_unpushed_commits(self):
        self.push("agentctl/x.py", "x = 1\n", "code change")
        commit(self.clone, "local.txt", "mine\n", "local work")
        head = sh(self.clone, "rev-parse", "HEAD")
        rc, out = self.run_update()
        self.assertEqual(rc, 1)
        self.assertIn("not on origin/main", out)
        self.assertEqual(sh(self.clone, "rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
