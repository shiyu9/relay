"""E2E: the confirmed behaviour of the recording pipeline.

This file is the spec of record for docs/interviews/2026-08-30-recording-via-subagent.md.
Each test name matches a Scenario there.
"""
import contextlib
import datetime
import io
import json
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from relay_test_support import RelayCase, dt, utc_text  # noqa: E402

import relay_apply  # noqa: E402
import relay_common  # noqa: E402
import relay_detect  # noqa: E402
import relay_stop_hook  # noqa: E402
import session_end_hook  # noqa: E402
import session_start_hook  # noqa: E402

SID = "124c52dd-f17a-4ad0-8b34-f94bdf3609f1"
SID8 = SID[:8]
OTHER = "bd0da51f-225a-42c2-a745-58483d649f36"


class PipelineCase(RelayCase):
    """Adds the two operations every scenario needs: apply and inject."""

    def apply(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv",
                               ["relay_apply.py", "--cwd", self.cwd]), \
             contextlib.redirect_stdout(out):
            relay_apply.main()
        return out.getvalue().strip()

    def summary(self):
        return dict(kv.split("=", 1) for kv in self.apply().split())

    def start_hook(self, source="startup"):
        payload = {"cwd": self.cwd, "source": source, "session_id": "new"}
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
             contextlib.redirect_stdout(out):
            session_start_hook.main()
        return out.getvalue()

    def stop_hook(self, stop_hook_active=False):
        payload = {"cwd": self.cwd, "stop_hook_active": stop_hook_active}
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
             contextlib.redirect_stdout(out):
            relay_stop_hook.main()
        return out.getvalue().strip()

    def record(self, sid, body="### done\n- something"):
        self.put_inbox(sid, f"===DIARY===\n{body}\n===PITFALLS===\n"
                            f"===WORKFLOW===\n===END===\n")
        return self.apply()


class TestEndDetection(PipelineCase):
    """Feature: 終了の検知"""

    def test_reopening_a_minute_later_still_records_the_previous_session(self):
        end = relay_common.local_now() - datetime.timedelta(minutes=1)
        p = self.make_transcript(SID, end - datetime.timedelta(hours=3), end)
        os.utime(p, (time.time() - 90, time.time() - 90))
        self.mark_ended(SID, mtime=time.time() - 60)
        self.assertEqual([c.sid for c in relay_detect.pending_sessions(self.cwd)],
                         [SID])

    def test_without_a_marker_the_idle_rule_applies(self):
        end = relay_common.local_now() - datetime.timedelta(minutes=40)
        self.make_transcript(SID, end - datetime.timedelta(hours=1), end)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 1)

    def test_a_running_window_is_not_summarised(self):
        end = relay_common.local_now() - datetime.timedelta(minutes=5)
        self.make_transcript(SID, end - datetime.timedelta(hours=1), end)
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_writes_long_after_the_marker_mean_the_session_resumed(self):
        end = relay_common.local_now() - datetime.timedelta(minutes=5)
        p = self.make_transcript(SID, end - datetime.timedelta(hours=1), end)
        self.mark_ended(SID, mtime=time.time() - 600)
        os.utime(p, (time.time(), time.time()))
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])


class TestOneEntryPerTranscript(PipelineCase):
    """Feature: 1 transcript = 1 日記エントリ"""

    def setUp(self):
        super().setUp()
        self.start = dt(2026, 8, 27, 19, 11)
        self.end = dt(2026, 8, 27, 22, 30)
        self.make_transcript(SID, self.start, self.end,
                             mtime=time.time() - 7200)

    def test_the_entry_lands_in_the_files_dated_for_the_session(self):
        self.record(SID)
        self.assertIn(f"## S1 19:11-22:30 ({SID8})", self.read_diary("2026-08-27"))
        self.assertFalse((relay_common.diary_dir(self.cwd) /
                          f"{relay_common.local_now():%Y-%m-%d}.md").exists())

    def test_applying_twice_does_not_duplicate_the_entry(self):
        self.record(SID)
        self.record(SID)
        self.assertEqual(self.read_diary("2026-08-27").count(f"({SID8})"), 1)

    def test_a_grown_transcript_replaces_its_entry_whole(self):
        self.record(SID)
        self.make_transcript(SID, self.start, dt(2026, 8, 27, 23, 48),
                             mtime=time.time() - 7200)
        self.record(SID, "### done\n- more")
        text = self.read_diary("2026-08-27")
        self.assertIn(f"## S1 19:11-23:48 ({SID8})", text)
        self.assertEqual(text.count(f"({SID8})"), 1)
        self.assertNotIn("- something", text)

    def test_an_unchanged_transcript_is_not_offered_again(self):
        self.record(SID)
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_growth_inside_the_same_minute_does_not_retrigger(self):
        self.record(SID)
        self.make_transcript(SID, self.start, dt(2026, 8, 27, 22, 30, 59),
                             mtime=time.time() - 7200)
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_the_heading_end_time_comes_from_the_transcript_not_the_clock(self):
        self.record(SID)
        self.assertIn("19:11-22:30", self.read_diary("2026-08-27"))
        self.assertNotIn(f"{relay_common.local_now():%H:%M}-",
                         self.read_diary("2026-08-27"))

    def test_a_span_is_found_even_though_the_last_line_has_no_timestamp(self):
        span = relay_common.session_span(
            relay_common.transcripts_dir(self.cwd) / f"{SID}.jsonl")
        self.assertEqual(span, (self.start, self.end))

    def test_a_session_crossing_midnight_stays_in_its_start_date_file(self):
        self.make_transcript(OTHER, dt(2026, 8, 29, 23, 50),
                             dt(2026, 8, 30, 1, 30), mtime=time.time() - 7200)
        self.record(OTHER)
        self.assertIn(f"## S1 23:50-01:30 ({OTHER[:8]})",
                      self.read_diary("2026-08-29"))
        self.assertEqual(self.read_diary("2026-08-30"), "")

    def _long_gap(self):
        """An entry from 20 days ago, and the same transcript woken up today.

        Dates are relative because the fixture has to stay in the past: a
        transcript whose last timestamp is in the future never counts as
        ended, and the test would silently assert nothing.
        """
        woke = relay_common.local_now().replace(
            second=0, microsecond=0) - datetime.timedelta(hours=2)
        old_day = (woke - datetime.timedelta(days=20)).replace(hour=19, minute=11)
        self.old_end = old_day.replace(hour=22, minute=30)
        self.write_diary(f"{old_day:%Y-%m-%d}",
                         f"## S1 19:11-22:30 ({SID8})\n### done\n- old work\n")
        self.make_transcript_at(SID, [
            old_day, old_day + datetime.timedelta(minutes=90), self.old_end,
            woke - datetime.timedelta(minutes=40),
            woke - datetime.timedelta(minutes=20), woke],
            mtime=time.time() - 7200)
        return old_day, woke

    def test_a_gap_of_fifteen_days_gets_a_fresh_entry(self):
        old_day, woke = self._long_gap()
        pending = relay_detect.pending_sessions(self.cwd)
        self.assertEqual([p.mode for p in pending], ["resume"])
        self.record(SID, "### done\n- new work")
        self.assertIn("- old work", self.read_diary(f"{old_day:%Y-%m-%d}"))
        self.assertIn(f"({SID8})", self.read_diary(f"{woke:%Y-%m-%d}"))

    def test_the_resumed_entry_starts_after_the_previous_one_ended(self):
        old_day, woke = self._long_gap()
        pending = relay_detect.pending_sessions(self.cwd)[0]
        self.assertGreater(pending.start, self.old_end)
        self.assertEqual(pending.start, woke - datetime.timedelta(minutes=40))


class TestExistingDiariesAreUntouched(PipelineCase):
    """Feature: 既存の日記に触らない"""

    def setUp(self):
        super().setUp()
        self.make_transcript(SID, dt(2026, 8, 29, 19, 11),
                             dt(2026, 8, 29, 22, 30), mtime=time.time() - 7200)

    def test_a_heading_without_an_id_is_never_matched(self):
        before = "## S1 10:24\n### done\n- 旧形式のまま\n"
        self.write_diary("2026-08-29", before)
        self.record(SID)
        self.assertIn(before.strip(), self.read_diary("2026-08-29"))

    def test_a_hand_written_diary_survives_intact(self):
        before = ("## セッション1（14:01 終了時記録・開始は 8/16）\n\n"
                  "### 作業ログ\n\n#### 音声モデルの勉強セッション\n- 手で書いた行\n")
        self.write_diary("2026-08-29", before)
        self.record(SID)
        after = self.read_diary("2026-08-29")
        self.assertIn("## セッション1（14:01 終了時記録・開始は 8/16）", after)
        self.assertIn("- 手で書いた行", after)
        self.assertIn(f"({SID8})", after)

    def test_a_broken_id_heading_is_logged_and_its_session_skipped(self):
        self.write_diary("2026-08-29", f"## S1 19:11-22:30 ({SID8[:7]})\n- x\n")
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])
        self.assertIn("unparsable id heading",
                      relay_common.read_text(relay_common.relay_home() / "log.txt"))

    def test_a_colliding_id_skips_that_session(self):
        self.write_diary("2026-08-29",
                         f"## S1 19:11-22:30 ({SID8})\n- a\n\n"
                         f"## S2 19:11-22:30 ({SID8})\n- b\n")
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])
        self.assertIn("duplicate heading",
                      relay_common.read_text(relay_common.relay_home() / "log.txt"))


class TestDetection(PipelineCase):
    """Feature: 検知（台帳なし・窓なし）"""

    def test_a_thin_session_is_never_recorded_and_never_retried_forever(self):
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 19, 20), n_user=2,
                             mtime=time.time() - 7200)
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_three_at_a_time_and_the_pool_shrinks(self):
        base = dt(2026, 8, 20, 9, 0)
        sids = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(5)]
        for i, sid in enumerate(sids):
            self.make_transcript(sid, base + datetime.timedelta(days=i),
                                 base + datetime.timedelta(days=i, hours=1),
                                 mtime=time.time() - 7200)
        first = relay_detect.pending_sessions(self.cwd)
        self.assertEqual(len(first), 3)
        self.assertEqual([p.sid for p in first], sids[:2:-1] + [sids[2]])
        for p in first:
            self.record(p.sid)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 2)

    def test_relay_keeps_no_window_of_its_own(self):
        old = relay_common.local_now() - datetime.timedelta(days=25)
        self.make_transcript(SID, old, old + datetime.timedelta(hours=1),
                             mtime=time.time() - 7200)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 1)

    def test_the_procedure_never_depends_on_the_claude_cli(self):
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        with mock.patch.dict(os.environ, {"PATH": ""}):
            notice = self.start_hook()
        self.assertIn("relay_apply.py", notice)
        scripts = pathlib.Path(relay_common.__file__).parent
        for f in scripts.glob("*.py"):
            self.assertNotIn("shutil.which", relay_common.read_text(f))


class TestWriteDeterminism(PipelineCase):
    """Feature: 書き込みの決定性"""

    def test_append_and_replace_use_different_delimiters(self):
        self.write_knowledge("pitfalls.md", "- [2026-01-01] 既存\n")
        self.put_inbox("a", "===PITFALLS===\n- [2026-08-30] 追加\n===END===\n")
        self.apply()
        self.assertIn("既存", self.read_knowledge("pitfalls.md"))
        self.assertIn("追加", self.read_knowledge("pitfalls.md"))
        self.put_inbox("b", "===PRUNE_PITFALLS===\n- [2026-08-30] 剪定後\n===END===\n")
        self.apply()
        self.assertNotIn("既存", self.read_knowledge("pitfalls.md"))
        self.assertEqual(self.read_knowledge("pitfalls.md").strip(),
                         "- [2026-08-30] 剪定後")

    def test_a_consumed_inbox_file_is_gone(self):
        p = self.put_inbox("a", "===PITFALLS===\n- x\n===END===\n")
        self.apply()
        self.assertFalse(p.exists())

    def test_the_summary_line_reports_what_the_session_needs(self):
        line = self.apply()
        self.assertIn("diary=", line)
        self.assertIn("pitfalls_lines=", line)


class TestOverwriteLayers(PipelineCase):
    """Feature: 上書き層が生成される"""

    def test_the_overwrite_job_asks_for_two_sections_only(self):
        import relay_prompts
        body = relay_prompts.OVERWRITE
        self.assertIn("===CURRENT===", body)
        self.assertIn("===DECIDED===", body)
        for other in ("===DIARY===", "===PITFALLS===", "===WORKFLOW==="):
            self.assertNotIn(other, body)

    def test_an_empty_current_section_keeps_the_page(self):
        self.write_knowledge("current.md", "- ep07 の再合成\n")
        self.put_inbox("o", "===CURRENT===\n===DECIDED===\n===END===\n")
        self.apply()
        self.assertEqual(self.read_knowledge("current.md"), "- ep07 の再合成\n")
        self.assertIn("CURRENT section empty",
                      relay_common.read_text(relay_common.relay_home() / "log.txt"))

    def test_a_four_hundred_line_current_is_not_truncated(self):
        body = "\n".join(f"- 行{i}" for i in range(400))
        self.put_inbox("o", f"===CURRENT===\n{body}\n===DECIDED===\n===END===\n")
        self.apply()
        self.assertEqual(len(self.read_knowledge("current.md").splitlines()), 400)

    def test_a_dropped_deferral_is_written_to_todays_diary(self):
        self.write_knowledge(
            "decided.md",
            "- 2026-08-12 コスト表示を見送り。再開条件: 1回10円超\n"
            "- 2026-08-20 自動クラスタを見送り。再開条件: 教師3倍\n")
        self.put_inbox("o", "===CURRENT===\n- いま\n===DECIDED===\n"
                            "- 2026-08-20 自動クラスタを見送り。再開条件: 教師3倍\n"
                            "===END===\n")
        summary = self.summary()
        self.assertEqual(summary["dropped"], "1")
        self.assertEqual(len(self.read_knowledge("decided.md").splitlines()), 1)
        today = self.read_diary(f"{relay_common.local_now():%Y-%m-%d}")
        self.assertIn(relay_common.DROPPED_HEADING, today)
        self.assertIn("2026-08-12 コスト表示を見送り", today)

    def test_the_dropped_heading_is_not_injected(self):
        self.write_diary(f"{relay_common.local_now():%Y-%m-%d}",
                         f"{relay_common.DROPPED_HEADING}\n- 落ちた項目\n")
        self.assertNotIn("落ちた項目", self.start_hook())


class TestChaining(PipelineCase):
    """Feature: 発火の連鎖"""

    def test_nothing_to_record_leaves_diary_at_zero(self):
        self.put_inbox(SID, "NOTHING_TO_RECORD")
        self.assertEqual(self.summary()["diary"], "0")

    def test_crossing_the_line_limit_shows_up_in_the_summary(self):
        self.write_knowledge(
            "pitfalls.md", "".join(f"- [2026-01-01] 行{i}\n" for i in range(79)))
        self.put_inbox("a", "===PITFALLS===\n- [2026-08-30] a\n- [2026-08-30] b\n"
                            "===END===\n")
        summary = self.summary()
        self.assertEqual(summary["pitfalls_lines"], "81")
        self.assertGreater(int(summary["pitfalls_lines"]),
                           int(summary["limit"]))


class TestInjection(PipelineCase):
    """Feature: 注入"""

    def _five_days(self):
        self.write_diary("2026-08-27", "".join(
            f"## S{i} 0{i}:00-0{i}:30 (aaaaaaa{i})\n### done\n- 27-{i}\n\n"
            for i in range(1, 4)))
        self.write_diary("2026-08-28",
                         "## S1 01:00-01:30 (bbbbbbb1)\n### done\n- 28-1\n")
        self.write_diary("2026-08-29", "".join(
            f"## S{i} 0{i}:00-0{i}:30 (ccccccc{i})\n### done\n- 29-{i}\n\n"
            for i in range(1, 5)))

    def test_the_diary_is_cut_by_entry_count_not_by_day(self):
        self._five_days()
        out = self.start_hook()
        self.assertEqual(sum(out.count(f"- 29-{i}") for i in range(1, 5)), 4)
        self.assertIn("- 28-1", out)
        self.assertNotIn("- 27-1", out)

    def test_handoff_is_not_injected_but_stays_in_the_file(self):
        self.write_diary("2026-08-29",
                         "## S1 01:00-01:30 (ccccccc1)\n### done\n- やった\n"
                         "### handoff\n- 次はこれ\n")
        out = self.start_hook()
        self.assertIn("- やった", out)
        self.assertNotIn("- 次はこれ", out)
        self.assertIn("### handoff", self.read_diary("2026-08-29"))

    def test_old_headings_count_towards_the_five(self):
        self.write_diary("2026-08-29",
                         "## S1 10:24\n### done\n- 旧\n\n"
                         "## S2 19:11-22:30 (ccccccc2)\n### done\n- 新\n")
        entries = session_start_hook.recent_entries(self.cwd)
        self.assertEqual(len(entries), 2)
        out = self.start_hook()
        self.assertIn("- 旧", out)
        self.assertIn("- 新", out)

    def test_current_is_injected_last(self):
        for name in ("pitfalls.md", "workflow.md", "decided.md", "current.md"):
            self.write_knowledge(name, f"- {name} の中身\n")
        self.write_diary("2026-08-29", "## S1 01:00-01:30 (ccccccc1)\n- 日記\n")
        out = self.start_hook()
        order = [out.index(s) for s in ("pitfalls.md の中身", "workflow.md の中身",
                                        "decided.md の中身", "- 日記",
                                        "current.md の中身")]
        self.assertEqual(order, sorted(order))


class TestPruneTrigger(PipelineCase):
    """Feature: 剪定の発火点"""

    def test_the_prune_job_is_offered_only_past_the_limit(self):
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        self.write_knowledge(
            "pitfalls.md", "".join(f"- [2026-01-01] 行{i}\n" for i in range(81)))
        self.start_hook()
        summary = self.summary()
        self.assertGreater(int(summary["pitfalls_lines"]), int(summary["limit"]))

    def test_below_the_limit_nothing_needs_pruning(self):
        self.write_knowledge(
            "pitfalls.md", "".join(f"- [2026-01-01] 行{i}\n" for i in range(79)))
        self.write_knowledge(
            "workflow.md", "".join(f"- [2026-01-01] 行{i}\n" for i in range(79)))
        summary = self.summary()
        self.assertLessEqual(int(summary["pitfalls_lines"]), int(summary["limit"]))
        self.assertLessEqual(int(summary["workflow_lines"]), int(summary["limit"]))


class TestStopHook(PipelineCase):
    """Feature: Stop hook（記録の取りこぼしの再促し）"""

    def _unrecorded(self):
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)

    def test_it_asks_again_when_something_is_unrecorded(self):
        self._unrecorded()
        payload = json.loads(self.stop_hook())
        self.assertEqual(payload["decision"], "block")
        self.assertIn("未記録", payload["reason"])

    def test_it_stays_quiet_about_the_running_session(self):
        end = relay_common.local_now() - datetime.timedelta(minutes=5)
        self.make_transcript(SID, end - datetime.timedelta(hours=1), end)
        self.assertEqual(self.stop_hook(), "")

    def test_it_never_fires_on_the_turn_its_own_block_restarted(self):
        self._unrecorded()
        self.assertEqual(self.stop_hook(stop_hook_active=True), "")

    def test_the_cooldown_silences_a_repeat(self):
        self._unrecorded()
        self.assertNotEqual(self.stop_hook(), "")
        self.assertEqual(self.stop_hook(), "")

    def test_a_disabled_project_is_left_alone(self):
        self._unrecorded()
        with mock.patch.dict(os.environ, {"RELAY_DISABLED": "1"}):
            self.assertEqual(self.stop_hook(), "")


class TestSessionEndMarker(PipelineCase):
    """The SessionEnd hook does exactly one thing."""

    def test_it_writes_the_marker(self):
        payload = {"cwd": self.cwd, "session_id": SID, "reason": "exit"}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            session_end_hook.main()
        self.assertTrue((relay_common.ended_dir(self.cwd) / SID).exists())

    def test_it_never_starts_a_process(self):
        source = relay_common.read_text(
            pathlib.Path(session_end_hook.__file__))
        for forbidden in ("subprocess", "Popen", "claude -p", "shutil"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
