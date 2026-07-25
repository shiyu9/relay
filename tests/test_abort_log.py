"""The recorder's abort line must say how to fix an auth failure.

With key clearing now the default, the usual cause of a non-zero exit shifts
from "stale API key" to "not logged in to a subscription", so the hint has to
name both remedies.
"""
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "relay", "scripts"))

import relay_recorder


class TestAbortLogHint(unittest.TestCase):
    def run_recorder(self, returncode):
        """Run relay_recorder.main() with claude stubbed out; return log text."""
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            completed = types.SimpleNamespace(
                returncode=returncode, stdout="", stderr="401 API key is invalid.")
            with mock.patch.object(pathlib.Path, "home", return_value=home), \
                 mock.patch.object(relay_recorder.shutil, "which",
                                   return_value="claude"), \
                 mock.patch.object(relay_recorder.subprocess, "run",
                                   return_value=completed), \
                 mock.patch.object(sys, "argv",
                                   ["relay_recorder.py",
                                    str(home / "t.jsonl"), str(home / "proj")]):
                relay_recorder.main()
            return (home / ".claude" / "relay" / "log.txt").read_text(encoding="utf-8")

    def test_exit_code_is_recorded(self):
        self.assertIn("claude exited 129", self.run_recorder(129))

    def test_hint_names_auth_status_check(self):
        self.assertIn("claude auth status", self.run_recorder(129))

    def test_hint_names_the_opt_out_variable(self):
        self.assertIn("RELAY_KEEP_API_KEY=1", self.run_recorder(129))

    def test_hint_covers_any_non_zero_exit(self):
        log = self.run_recorder(1)
        self.assertIn("claude exited 1", log)
        self.assertIn("claude auth status", log)


if __name__ == "__main__":
    unittest.main()
