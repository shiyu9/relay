"""Catch-up at SessionStart: the reliable half of the recording pipeline.

The SessionEnd hook can be killed at any point when the terminal window
closes together with claude (observed: sometimes before it logs a single
line), so recovery relies on (a) the recorder marking processed transcripts
in a per-project ledger and (b) session_start_hook spawning recorders for
ended transcripts that have no up-to-date marker.
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "relay", "scripts"))

import relay_common
import relay_recorder
import session_start_hook


def user_line(text="hi"):
    return json.dumps({"type": "user", "message": {"content": text}})


class HomeCase(unittest.TestCase):
    """Base: every test runs against a throwaway HOME and project cwd."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self._tmp.name)
        self.cwd = str(self.home / "proj" / "demo")
        patcher = mock.patch.object(pathlib.Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def make_transcript(self, session_id, n_user_msgs, mtime_age_secs=None):
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{session_id}.jsonl"
        p.write_text(
            "\n".join(user_line(f"m{i}") for i in range(n_user_msgs)) + "\n",
            encoding="utf-8")
        if mtime_age_secs is not None:
            t = time.time() - mtime_age_secs
            os.utime(p, (t, t))
        return p


class TestLedger(HomeCase):
    def test_roundtrip(self):
        relay_common.mark_recorded(self.cwd, "abc", 1234)
        self.assertEqual(relay_common.recorded_size(self.cwd, "abc"), 1234)

    def test_missing_marker_is_none(self):
        self.assertIsNone(relay_common.recorded_size(self.cwd, "nope"))

    def test_corrupt_marker_is_none(self):
        d = relay_common.ledger_dir(self.cwd)
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad").write_text("not-a-number", encoding="ascii")
        self.assertIsNone(relay_common.recorded_size(self.cwd, "bad"))

    def test_marker_is_per_project(self):
        relay_common.mark_recorded(self.cwd, "abc", 1)
        self.assertIsNone(relay_common.recorded_size(self.cwd + "2", "abc"))


class CatchUpCase(HomeCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(session_start_hook, "spawn_recorder")
        self.spawn = patcher.start()
        self.addCleanup(patcher.stop)

    def catch_up(self):
        session_start_hook.catch_up(self.cwd)


class TestFirstRunAdoption(CatchUpCase):
    def test_existing_transcripts_are_adopted_not_recorded(self):
        p = self.make_transcript("old", 5, mtime_age_secs=3600)
        self.catch_up()
        self.spawn.assert_not_called()
        self.assertEqual(relay_common.recorded_size(self.cwd, "old"),
                         p.stat().st_size)

    def test_adoption_happens_once(self):
        self.make_transcript("old", 5, mtime_age_secs=3600)
        self.catch_up()
        self.make_transcript("lost", 5, mtime_age_secs=3600)
        self.catch_up()
        self.assertEqual(self.spawn.call_count, 1)

    def test_no_transcript_dir_is_a_noop(self):
        self.catch_up()
        self.spawn.assert_not_called()


class TestCatchUpSelection(CatchUpCase):
    def setUp(self):
        super().setUp()
        relay_common.ledger_dir(self.cwd).mkdir(parents=True, exist_ok=True)

    def test_ended_unmarked_transcript_is_caught(self):
        p = self.make_transcript("lost", 5, mtime_age_secs=3600)
        self.catch_up()
        self.spawn.assert_called_once_with(self.cwd, p)

    def test_fresh_transcript_is_left_alone(self):
        """A live (or just-ended) session must not be recorded prematurely."""
        self.make_transcript("live", 5, mtime_age_secs=60)
        self.catch_up()
        self.spawn.assert_not_called()

    def test_ancient_transcript_is_left_alone(self):
        self.make_transcript("ancient", 5, mtime_age_secs=15 * 86400)
        self.catch_up()
        self.spawn.assert_not_called()

    def test_marker_with_same_size_skips(self):
        p = self.make_transcript("done", 5, mtime_age_secs=3600)
        relay_common.mark_recorded(self.cwd, "done", p.stat().st_size)
        self.catch_up()
        self.spawn.assert_not_called()

    def test_grown_transcript_is_recorded_again(self):
        """A session resumed after being recorded gets picked up again."""
        p = self.make_transcript("resumed", 8, mtime_age_secs=3600)
        relay_common.mark_recorded(self.cwd, "resumed", p.stat().st_size - 10)
        self.catch_up()
        self.spawn.assert_called_once_with(self.cwd, p)

    def test_spawns_are_capped_and_newest_first(self):
        for i in range(5):
            self.make_transcript(f"s{i}", 5,
                                 mtime_age_secs=3600 + i * 60)  # s0 newest
        self.catch_up()
        self.assertEqual(self.spawn.call_count,
                         session_start_hook.CATCHUP_MAX_SPAWNS)
        spawned = [c.args[1].stem for c in self.spawn.call_args_list]
        self.assertEqual(spawned, ["s0", "s1", "s2"])


class TestRecorderLedger(HomeCase):
    """The recorder's own guards (moved out of the SessionEnd hook)."""

    def run_recorder(self, transcript, claude_output=None):
        completed = types.SimpleNamespace(
            returncode=0, stdout=claude_output or "", stderr="")
        run = mock.MagicMock(return_value=completed)
        with mock.patch.object(relay_recorder.shutil, "which",
                               return_value="claude"), \
             mock.patch.object(relay_recorder.subprocess, "run", run), \
             mock.patch.dict(os.environ, {"RELAY_MIN_USER_MSGS": "3"}), \
             mock.patch.object(sys, "argv",
                               ["relay_recorder.py", str(transcript), self.cwd]):
            relay_recorder.main()
        return run

    def log_text(self):
        return (self.home / ".claude" / "relay" / "log.txt").read_text(
            encoding="utf-8")

    def test_thin_session_is_skipped_and_marked(self):
        p = self.make_transcript("thin", 1)
        run = self.run_recorder(p)
        run.assert_not_called()
        self.assertIn("skip: thin session", self.log_text())
        self.assertEqual(relay_common.recorded_size(self.cwd, "thin"),
                         p.stat().st_size)

    def test_already_recorded_transcript_is_not_reprocessed(self):
        p = self.make_transcript("dup", 5)
        relay_common.mark_recorded(self.cwd, "dup", p.stat().st_size)
        run = self.run_recorder(p)
        run.assert_not_called()
        self.assertIn("skip: already recorded", self.log_text())

    def test_successful_recording_writes_marker_and_diary(self):
        p = self.make_transcript("ok", 5)
        out = ("===DIARY===\n### done\n- something\n"
               "===PITFALLS===\n===WORKFLOW===\n===END===")
        run = self.run_recorder(p, claude_output=out)
        run.assert_called_once()
        self.assertEqual(relay_common.recorded_size(self.cwd, "ok"),
                         p.stat().st_size)
        self.assertIn("recorded: diary S1", self.log_text())
        self.assertEqual(len(list((pathlib.Path(self.cwd) / "diary").glob("*.md"))), 1)

    def test_nothing_to_record_is_marked(self):
        p = self.make_transcript("empty", 5)
        run = self.run_recorder(p, claude_output="NOTHING_TO_RECORD")
        run.assert_called_once()
        self.assertEqual(relay_common.recorded_size(self.cwd, "empty"),
                         p.stat().st_size)

    def test_unparseable_output_leaves_no_marker_so_catchup_retries(self):
        p = self.make_transcript("garbled", 5)
        self.run_recorder(p, claude_output="=== broken ===")
        self.assertIsNone(relay_common.recorded_size(self.cwd, "garbled"))


if __name__ == "__main__":
    unittest.main()
