"""status.md: the third layer, overwritten instead of appended.

The diary and the knowledge files only ever grow, so a bad model turn costs a
duplicate line. status.md is replaced wholesale, which brings back two failure
modes relay had designed away: a section the model simply forgot erasing real
open work, and a recorder running late for an older session clobbering a status
already written from a newer one. Both are guarded in code, not in the prompt.
"""
import io
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


def model_output(diary="### done\n- did a thing", status=None,
                 pitfalls="", workflow=""):
    """status=None omits ===STATUS=== entirely (output from the older prompt)."""
    parts = ["===DIARY===\n" + diary]
    if status is not None:
        parts.append("===STATUS===\n" + status)
    parts += ["===PITFALLS===\n" + pitfalls,
              "===WORKFLOW===\n" + workflow,
              "===END==="]
    return "\n".join(parts)


class StatusCase(unittest.TestCase):
    """Base: throwaway HOME and project cwd, recorder with claude stubbed out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self._tmp.name)
        self.cwd = str(self.home / "proj" / "demo")
        patcher = mock.patch.object(pathlib.Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def make_transcript(self, session_id, n_user_msgs=5, mtime=None):
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{session_id}.jsonl"
        p.write_text("\n".join(
            json.dumps({"type": "user", "message": {"content": f"m{i}"}})
            for i in range(n_user_msgs)) + "\n", encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def run_recorder(self, transcript, claude_output="", returncode=0):
        completed = types.SimpleNamespace(
            returncode=returncode, stdout=claude_output, stderr="")
        run = mock.MagicMock(return_value=completed)
        with mock.patch.object(relay_recorder.shutil, "which",
                               return_value="claude"), \
             mock.patch.object(relay_recorder.subprocess, "run", run), \
             mock.patch.dict(os.environ, {"RELAY_MIN_USER_MSGS": "3"}), \
             mock.patch.object(sys, "argv",
                               ["relay_recorder.py", str(transcript), self.cwd]):
            relay_recorder.main()
        return run

    def status_text(self):
        p = relay_common.status_path(self.cwd)
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def write_status(self, text):
        p = relay_common.status_path(self.cwd)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def diary_text(self):
        return "".join(p.read_text(encoding="utf-8")
                       for p in (pathlib.Path(self.cwd) / "diary").glob("*.md"))


class TestStatusWrite(StatusCase):
    def test_created_when_absent(self):
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- 案3 の検証が未実施"))
        self.assertIn("- 案3 の検証が未実施", self.status_text())

    def test_existing_status_is_handed_to_the_model(self):
        """Differential update is only possible if the model sees the old one."""
        self.write_status("- 案3 の検証が未実施\n")
        p = self.make_transcript("s1")
        run = self.run_recorder(p, model_output(status="- x"))
        prompt = run.call_args.args[0][2]
        self.assertIn("- 案3 の検証が未実施", prompt)

    def test_empty_section_keeps_existing(self):
        self.write_status("- 案3 の検証が未実施\n")
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status=""))
        self.assertEqual(self.status_text(), "- 案3 の検証が未実施\n")

    def test_missing_delimiter_keeps_existing_and_still_writes_diary(self):
        self.write_status("- 案3 の検証が未実施\n")
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status=None))
        self.assertEqual(self.status_text(), "- 案3 の検証が未実施\n")
        self.assertIn("did a thing", self.diary_text())

    def test_empty_section_with_no_file_creates_nothing(self):
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status=""))
        self.assertIsNone(self.status_text())

    def test_nothing_to_record_leaves_status(self):
        self.write_status("- pending\n")
        p = self.make_transcript("s1")
        self.run_recorder(p, "NOTHING_TO_RECORD")
        self.assertEqual(self.status_text(), "- pending\n")

    def test_thin_session_leaves_status(self):
        self.write_status("- pending\n")
        p = self.make_transcript("thin", n_user_msgs=1)
        run = self.run_recorder(p, model_output(status="- clobbered"))
        run.assert_not_called()
        self.assertEqual(self.status_text(), "- pending\n")

    def test_unparseable_output_leaves_status(self):
        self.write_status("- pending\n")
        p = self.make_transcript("s1")
        self.run_recorder(p, "=== broken ===")
        self.assertEqual(self.status_text(), "- pending\n")

    def test_failed_claude_run_leaves_status(self):
        self.write_status("- pending\n")
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- clobbered"), returncode=1)
        self.assertEqual(self.status_text(), "- pending\n")

    def test_reprocessing_the_same_transcript_is_skipped(self):
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- first"))
        self.run_recorder(p, model_output(status="- second"))
        self.assertIn("- first", self.status_text())

    def test_non_ascii_round_trip(self):
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- 日本語の未完了項目 ✅"))
        self.assertIn("- 日本語の未完了項目 ✅", self.status_text())

    def test_single_line_status(self):
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- 未完了なし"))
        self.assertEqual(self.status_text(), "- 未完了なし\n")

    def test_long_status_is_not_truncated(self):
        """The length cap lives in the prompt; code must not silently drop items."""
        body = "\n".join(f"- item {i}" for i in range(200))
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status=body))
        self.assertIn("- item 199", self.status_text())

    def test_no_tmp_file_left_behind(self):
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- x"))
        self.assertEqual(list(pathlib.Path(self.cwd).glob("*.tmp")), [])

    def test_status_write_is_logged(self):
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- x"))
        log = (self.home / ".claude" / "relay" / "log.txt").read_text(encoding="utf-8")
        self.assertIn("status.md", log)


class TestStatusOrdering(StatusCase):
    def test_older_session_does_not_clobber_but_still_writes_its_diary(self):
        newer = self.make_transcript("B", mtime=time.time() - 100)
        self.run_recorder(newer, model_output(status="- B の未完了"))
        older = self.make_transcript("A", mtime=time.time() - 10000)
        self.run_recorder(older, model_output(diary="### done\n- A did work",
                                              status="- A の未完了"))
        self.assertIn("- B の未完了", self.status_text())
        self.assertNotIn("- A の未完了", self.status_text())
        self.assertIn("- A did work", self.diary_text())

    def test_newer_session_wins(self):
        older = self.make_transcript("A", mtime=time.time() - 10000)
        self.run_recorder(older, model_output(status="- A の未完了"))
        newer = self.make_transcript("B", mtime=time.time() - 100)
        self.run_recorder(newer, model_output(status="- B の未完了"))
        self.assertIn("- B の未完了", self.status_text())

    def test_resumed_transcript_can_update_its_own_status(self):
        t0 = time.time() - 10000
        p = self.make_transcript("A", mtime=t0)
        self.run_recorder(p, model_output(status="- first"))
        p = self.make_transcript("A", n_user_msgs=9, mtime=t0 + 3600)
        self.run_recorder(p, model_output(status="- second"))
        self.assertIn("- second", self.status_text())

    def test_corrupt_marker_fails_open(self):
        """A broken marker must not freeze status.md forever."""
        d = relay_common.ledger_dir(self.cwd)
        d.mkdir(parents=True, exist_ok=True)
        (d / relay_common.STATUS_MARKER).write_text("not-a-number", encoding="ascii")
        p = self.make_transcript("s1")
        self.run_recorder(p, model_output(status="- written anyway"))
        self.assertIn("- written anyway", self.status_text())

    def test_unreadable_mtime_writes_status_without_a_marker(self):
        p = self.make_transcript("s1")
        real = os.path.getmtime

        def boom(path):
            if str(path) == str(p):
                raise OSError("no mtime")
            return real(path)

        with mock.patch.object(os.path, "getmtime", boom):
            self.run_recorder(p, model_output(status="- x"))
        self.assertIn("- x", self.status_text())
        self.assertIsNone(relay_common.status_source_mtime(self.cwd))

    def test_marker_roundtrip(self):
        relay_common.mark_status_written(self.cwd, 1234.5)
        self.assertEqual(relay_common.status_source_mtime(self.cwd), 1234.5)

    def test_marker_missing_is_none(self):
        self.assertIsNone(relay_common.status_source_mtime(self.cwd))

    def test_marker_is_per_project(self):
        relay_common.mark_status_written(self.cwd, 1.0)
        self.assertIsNone(relay_common.status_source_mtime(self.cwd + "2"))

    def test_marker_does_not_disturb_catch_up_adoption(self):
        """The sidecar shares ledger_dir with per-session markers."""
        relay_common.mark_status_written(self.cwd, 1.0)
        self.assertIsNone(relay_common.recorded_size(
            self.cwd, relay_common.STATUS_MARKER))


class TestWriteAtomic(StatusCase):
    def test_replaces_existing_content(self):
        p = self.home / "f.md"
        relay_common.write_atomic(p, "a\n")
        relay_common.write_atomic(p, "b\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "b\n")

    def test_creates_parent_directories(self):
        p = self.home / "deep" / "nested" / "f.md"
        relay_common.write_atomic(p, "x\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "x\n")


class TestInjection(StatusCase):
    """SessionStart puts status last: it changes every session, so keeping it
    at the end leaves the longest cacheable prefix."""

    HEADING = "status.md（"

    def inject(self):
        stdin = io.StringIO(json.dumps({"source": "startup", "cwd": self.cwd}))
        buf = io.StringIO()
        env = {k: v for k, v in os.environ.items()
               if k not in ("RELAY_HOOK_ACTIVE", "RELAY_DISABLED", "RELAY_SCOPE")}
        with mock.patch.object(sys, "stdin", stdin), \
             mock.patch.object(sys, "stdout", buf), \
             mock.patch.dict(os.environ, env, clear=True):
            session_start_hook.main()
        return buf.getvalue()

    def seed_knowledge_and_diary(self):
        base = pathlib.Path(self.cwd)
        (base / "knowledge").mkdir(parents=True, exist_ok=True)
        (base / "knowledge" / "pitfalls.md").write_text("- a pitfall",
                                                        encoding="utf-8")
        (base / "diary").mkdir(parents=True, exist_ok=True)
        (base / "diary" / "2026-08-01.md").write_text("## S1 10:00\n- a day",
                                                      encoding="utf-8")

    def test_status_comes_last(self):
        self.seed_knowledge_and_diary()
        self.write_status("- 未完了の一件")
        text = self.inject()
        self.assertLess(text.index("knowledge/pitfalls.md"),
                        text.index("diary/2026-08-01.md"))
        self.assertLess(text.index("diary/2026-08-01.md"),
                        text.index(self.HEADING))
        self.assertIn("- 未完了の一件", text)

    def test_absent_status_is_not_injected(self):
        self.seed_knowledge_and_diary()
        text = self.inject()
        self.assertIn("knowledge/pitfalls.md", text)
        self.assertNotIn(self.HEADING, text)

    def test_blank_status_is_not_injected(self):
        self.seed_knowledge_and_diary()
        self.write_status("   \n")
        self.assertNotIn(self.HEADING, self.inject())

    def test_status_alone_is_enough_to_inject(self):
        self.write_status("- 未完了の一件")
        text = self.inject()
        self.assertIn(self.HEADING, text)
        self.assertIn("- 未完了の一件", text)

    def test_preamble_says_status_is_read_only(self):
        self.write_status("- x")
        self.assertIn("手で編集しない", self.inject())


if __name__ == "__main__":
    unittest.main()
