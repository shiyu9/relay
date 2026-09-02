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
import relay_jobs  # noqa: E402
import relay_prompts  # noqa: E402
import relay_record  # noqa: E402
import relay_record_headless  # noqa: E402
import relay_spawn  # noqa: E402
import session_end_hook  # noqa: E402
import user_prompt_hook  # noqa: E402
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

    def end_hook(self, sid=SID):
        payload = {"cwd": self.cwd, "session_id": sid, "reason": "exit"}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            session_end_hook.main()

    def prompt_hook(self, sid="new"):
        payload = {"cwd": self.cwd, "session_id": sid}
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
             contextlib.redirect_stdout(out):
            user_prompt_hook.main()
        return out.getvalue()

    def run_record(self, token=None):
        """What the startup notice now tells the session to run.

        SessionStart used to print the procedure itself; it points at
        relay_record instead, so anything that looks at a job body goes
        through here rather than through the hook.
        """
        argv = ["relay_record.py", "--cwd", self.cwd]
        if token is not None:
            argv += ["--self", token]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             contextlib.redirect_stdout(out):
            relay_record.main()
        return out.getvalue()

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
        self.assertIn(f"## S1 23:50-01:30+1d ({OTHER[:8]})",
                      self.read_diary("2026-08-29"))
        self.assertEqual(self.read_diary("2026-08-30"), "")

    def test_a_multi_day_session_records_how_many_days_it_spanned(self):
        self.make_transcript(OTHER, dt(2026, 8, 26, 8, 14),
                             dt(2026, 8, 29, 8, 13), mtime=time.time() - 7200)
        self.record(OTHER)
        # "08:14-08:13" alone would read as ending before it started.
        self.assertIn(f"## S1 08:14-08:13+3d ({OTHER[:8]})",
                      self.read_diary("2026-08-26"))

    def test_a_multi_day_entry_is_not_recorded_a_second_time(self):
        """The end time has to survive the round trip through the heading.

        Dropping the day count parses the entry back as ending three days
        early, so the transcript looks newer than its own entry and is
        re-recorded on every single startup, forever.
        """
        self.make_transcript(OTHER, dt(2026, 8, 26, 8, 14),
                             dt(2026, 8, 29, 8, 13), mtime=time.time() - 7200)
        self.record(OTHER)
        still_pending = [p.sid8 for p in relay_detect.pending_sessions(self.cwd)]
        self.assertNotIn(OTHER[:8], still_pending)

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

    def test_a_machine_without_the_cli_still_records(self):
        """The CLI decides *when* a session is recorded, never *whether*.

        SessionEnd hands the work to a headless `claude` when there is one,
        which is what keeps current.md from being a session behind. With no
        CLI on the machine it leaves the end marker and nothing else, and
        the next startup picks the transcript up — the same path a session
        whose SessionEnd never fired takes.
        """
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        with mock.patch.object(relay_common.shutil, "which",
                               return_value=None):
            self.end_hook(SID)
        # No recorder was started, so nothing is marked as being recorded.
        self.assertEqual(relay_common.running_sids(self.cwd), set())
        self.assertTrue((relay_common.ended_dir(self.cwd) / SID).exists())
        # And the session is still on offer, through the in-session route.
        notice = self.run_record()
        self.assertIn(SID8, notice)
        self.assertIn("relay_apply.py", notice)


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

    def test_the_overwrite_job_sees_the_newest_diary_not_only_this_run(self):
        self.write_diary("2026-08-29",
                         "## S1 09:00-10:00 (aaaaaaaa)\n### done\n- 昨日の話\n")
        self.make_transcript(SID, dt(2026, 8, 10, 9, 0), dt(2026, 8, 10, 11, 30),
                             mtime=time.time() - 7200)
        self.run_record()
        job = relay_common.read_text(
            relay_common.jobs_dir(self.cwd) / "overwrite.md")
        self.assertIn("diary/2026-08-10.md", job)
        self.assertIn("diary/2026-08-29.md", job)

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


class TestPruneCannotEraseNewBullets(PipelineCase):
    """Feature: 剪定は、剪定係が見ていない項目を消さない"""

    def setUp(self):
        super().setUp()
        self.write_knowledge("pitfalls.md", "- [2026-01-01] 古い罠\n")
        self.write_knowledge("workflow.md", "- [2026-01-01] 古い進め方\n")
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        self.run_record()   # prune.md はここで「古い罠」だけを埋め込む

    def prune_job(self):
        return relay_common.read_text(
            relay_common.jobs_dir(self.cwd) / "prune.md")

    def test_a_prune_built_before_the_appends_is_refused(self):
        """剪定係の入力に無い項目は、剪定の適用で消えてはならない。

        剪定は2ファイルを全文で置き換える。ジョブが書かれたあとに追記が
        入っていると、その項目は剪定係の入力に存在せず、出力にも現れない
        ——そのまま適用すれば痕跡なく消える。
        """
        self.write_knowledge(
            "pitfalls.md", "- [2026-01-01] 古い罠\n- [2026-08-30] 新しい罠\n")
        self.put_inbox("p", "===PRUNE_PITFALLS===\n- [2026-01-01] 古い罠\n"
                            "===PRUNE_WORKFLOW===\n- [2026-01-01] 古い進め方\n"
                            "===END===\n")
        summary = self.summary()
        self.assertEqual(summary["stale"], "1")
        self.assertIn("新しい罠", self.read_knowledge("pitfalls.md"))

    def test_apply_rebuilds_the_prune_job_from_the_current_files(self):
        self.assertNotIn("新しい罠", self.prune_job())
        self.put_inbox(SID, "===DIARY===\n### done\n- x\n===PITFALLS===\n"
                            "- [2026-08-30] 新しい罠\n===WORKFLOW===\n===END===\n")
        self.apply()
        self.assertIn("新しい罠", self.prune_job())

    def test_the_job_is_never_told_to_hit_a_line_count(self):
        """行数のための削除が、剪定が防ぐはずの「一次情報の消失」を起こした。

        imagegen 実測: pitfalls 130項目→43項目。落ちた92件は重複でも古い項目
        でもなく、測って得た一次情報だった。重複は実質1組しかなく、80行には
        統合では届かないので、従う限り削除するしかなかった。
        """
        body = relay_prompts.PRUNE
        self.assertIn("項目を削除しないこと", body)
        self.assertNotIn("行以内に収める", body)

    def test_move_candidates_go_to_the_diary_and_nothing_is_deleted(self):
        self.put_inbox("p", "===PRUNE_PITFALLS===\n- [2026-01-01] 古い罠\n"
                            "===PRUNE_WORKFLOW===\n===MOVE===\n"
                            "- [2026-01-01] 古い罠。理由: docs/ の資料に属する\n"
                            "===END===\n")
        summary = self.summary()
        self.assertEqual(summary["move"], "1")
        today = self.read_diary(f"{relay_common.local_now():%Y-%m-%d}")
        self.assertIn(relay_common.MOVE_HEADING, today)
        self.assertIn("docs/ の資料に属する", today)
        # 候補を挙げただけ。knowledge からは何も消えない。
        self.assertIn("古い罠", self.read_knowledge("pitfalls.md"))

    def test_the_move_note_is_not_injected(self):
        self.put_inbox("p", "===PRUNE_PITFALLS===\n- [2026-01-01] 古い罠\n"
                            "===PRUNE_WORKFLOW===\n===MOVE===\n"
                            "- [2026-01-01] 古い罠。理由: docs/ に属する\n"
                            "===END===\n")
        self.apply()
        today = f"{relay_common.local_now():%Y-%m-%d}"
        self.assertIn(relay_common.MOVE_HEADING, self.read_diary(today))
        headings = [e.heading.strip()
                    for e in session_start_hook.recent_entries(self.cwd)]
        self.assertNotIn(relay_common.MOVE_HEADING, headings)

    def test_the_merge_is_due_only_while_something_changed(self):
        """大きいだけのプロジェクトで毎起動発火させない。

        削除が禁じられた以上、行数は上限を下回らない。閾値だけを条件にすると
        同じ報告を毎回作り直すことになる。
        """
        self.write_knowledge(
            "pitfalls.md", "".join(f"- [2026-01-01] 行{i}\n" for i in range(81)))
        self.assertEqual(self.summary()["merge_due"], "yes")
        kept = "".join(f"- [2026-01-01] 行{i}\n" for i in range(81))
        self.put_inbox("p", f"===PRUNE_PITFALLS===\n{kept}"
                            "===PRUNE_WORKFLOW===\n===MOVE===\n===END===\n")
        self.assertEqual(self.summary()["merge_due"], "no")
        self.put_inbox("a", "===PITFALLS===\n- [2026-08-30] 新しい罠\n===END===\n")
        self.assertEqual(self.summary()["merge_due"], "yes")

    def test_a_prune_matching_the_current_files_is_applied(self):
        self.put_inbox("p", "===PRUNE_PITFALLS===\n- [2026-01-01] 統合した罠\n"
                            "===PRUNE_WORKFLOW===\n- [2026-01-01] 古い進め方\n"
                            "===END===\n")
        summary = self.summary()
        self.assertEqual(summary["stale"], "0")
        self.assertEqual(self.read_knowledge("pitfalls.md"),
                         "- [2026-01-01] 統合した罠\n")


class TestTheCallTheSessionMakes(PipelineCase):
    """Feature: 分類器に通る呼び出し"""

    def setUp(self):
        super().setUp()
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)

    def block(self, notice, n=1):
        """The nth prompt the notice hands over, between its two markers."""
        return notice.split("ここから ---")[n].split("--- ここまで")[0]

    def test_the_prompt_is_printed_whole_instead_of_described(self):
        """呼び出しそのものが分類器の採点対象なので、本文まで書き出す。

        実測（permlog.jsonl の DENY と transcript の突き合わせ）: relay の
        サブエージェント呼び出し17回中7回が `Blocked by classifier`。
        「<パス> を読み、指示に従え」＋説明1文の形は2回連続で拒否され、
        目的から始めて手順に番号を振り、各手順の道具と書き先1本を名指しした
        形で通った。
        """
        notice = self.run_record()
        body = self.block(notice)
        self.assertIn("手順:", body)
        self.assertIn("を Read で読む", body)
        self.assertIn("Write で1ファイルだけ書き出す", body)
        self.assertIn("そのまま渡すこと", notice)
        # 記録1本＋現在地＋統合。パスだけ並べた形に戻ると数が減る。
        self.assertEqual(notice.count("ここから ---"), 3)

    def test_the_call_names_the_job_the_transcript_and_the_one_file_written(self):
        body = self.block(self.run_record())
        self.assertIn(f"{SID}.md", body)       # 指示書
        self.assertIn(f"{SID}.jsonl", body)    # 読む対象
        self.assertIn(f"{SID}.txt", body)      # 書き先。これ1本だけ

    def test_the_job_body_still_stays_out_of_the_notice(self):
        self.assertNotIn("===DIARY===", self.run_record())

    def test_the_apply_command_is_one_command_with_nothing_chained_to_it(self):
        """`cd ... && python ...` も実測で1回拒否され、`cd` を外して通った。"""
        notice = self.run_record()
        line = next(l for l in notice.splitlines() if "relay_apply.py" in l)
        self.assertNotIn("cd ", line)
        self.assertNotIn("&&", line)
        self.assertIn("--cwd", line)
        self.assertIn("`cd` を前に付けない", notice)

    def test_a_refused_job_is_left_pending_instead_of_being_repeated(self):
        notice = self.run_record()
        self.assertIn("同じ呼び出しを繰り返さないこと", notice)
        # 拒否されても記録は失われない: 何も書かなければ次の起動でまた候補になる。
        self.assertEqual([p.sid for p in relay_detect.pending_sessions(self.cwd)],
                         [SID])

    def test_the_overwrite_and_merge_calls_are_printed_the_same_way(self):
        notice = self.run_record()
        self.assertIn("現在地テキストの作成", notice)
        self.assertIn("ナレッジ統合テキストの作成", notice)
        self.assertIn("overwrite.md", self.block(notice, 2))
        self.assertIn("prune.md", self.block(notice, 3))


class TestKnowledgeDatesComeFromPython(PipelineCase):
    """Feature: ナレッジ項目の日付"""

    def lesson(self, line, start=dt(2026, 8, 27, 19, 11)):
        self.make_transcript(SID, start, start + datetime.timedelta(hours=1),
                             mtime=time.time() - 7200)
        self.put_inbox(SID, f"===DIARY===\n### done\n- x\n===PITFALLS===\n"
                            f"{line}\n===WORKFLOW===\n===END===\n")
        self.apply()
        return self.read_knowledge("pitfalls.md")

    def test_an_item_written_without_a_date_still_gets_one(self):
        """実測: workflow.md に足された3行のうち1行は日付が丸ごと無かった。"""
        self.assertIn("- [2026-08-27] 日付の無い学び",
                      self.lesson("- 日付の無い学び"))

    def test_a_date_the_model_chose_itself_is_replaced(self):
        got = self.lesson("- [2020-01-01] 別の日付を書いた学び")
        self.assertIn("- [2026-08-27] 別の日付を書いた学び", got)
        self.assertNotIn("2020-01-01", got)

    def test_an_old_session_is_not_stamped_with_the_day_it_was_dug_up(self):
        """遡り記録では「掘った日」で刻むと古い知見が全部いちばん新しくなる。"""
        got = self.lesson("- 遡って掘り出した学び", start=dt(2026, 1, 5, 9, 0))
        self.assertIn("- [2026-01-05] 遡って掘り出した学び", got)
        self.assertNotIn(f"{relay_common.local_now():%Y-%m-%d}", got)

    def test_a_leading_bracket_that_is_not_a_date_is_left_alone(self):
        self.assertIn("- [2026-08-27] [進行中] 途中の話",
                      self.lesson("- [進行中] 途中の話"))

    def test_the_job_body_needs_only_what_the_hook_hands_it(self):
        """`{date}` を書き戻すと hook の format が KeyError で黙って落ちる。"""
        body = relay_prompts.RECORD.format(out="o", transcript="t",
                                           reading="r",
                                           pitfalls="p", workflow="w")
        self.assertIn("===DIARY===", body)

    def test_the_job_says_an_empty_section_is_the_normal_outcome(self):
        """重複を入口で止める唯一の手段がこの2節の書き方になった。"""
        self.assertIn("空のまま終わるのが普通です", relay_prompts.RECORD)
        self.assertIn("言い回しを変えても書かない", relay_prompts.RECORD)
        self.assertIn("日付は Python が付けます", relay_prompts.RECORD)


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


class TestTheAdoptionCutoff(PipelineCase):
    """Feature: 導入時点の線（epoch）"""

    OLD = (dt(2026, 8, 10, 9, 0), dt(2026, 8, 10, 11, 30))

    def _old_transcript(self, sid=SID):
        return self.make_transcript(sid, *self.OLD, mtime=time.time() - 7200)

    def _sid8s(self, **kw):
        return [p.sid8 for p in relay_detect.pending_sessions(self.cwd, **kw)]

    def test_adopting_a_project_offers_none_of_its_backlog(self):
        self.set_epoch(None)
        self.write_knowledge("pitfalls.md", "- [2026-08-01] 何かの罠\n")
        for sid in (SID, OTHER, "3f1c9a20-1111-2222-3333-444455556666"):
            self.make_transcript(sid, *self.OLD, mtime=time.time() - 7200)
        out = self.start_hook()
        self.assertIn("pitfalls.md", out)
        self.assertNotIn("未記録のセッションが", out)
        self.assertEqual(self._sid8s(), [])
        self.assertTrue(self.read_epoch())

    def test_a_transcript_that_ended_before_the_line_is_never_offered(self):
        self._old_transcript()
        self.set_epoch(dt(2026, 8, 20))
        self.assertEqual(self._sid8s(), [])

    def test_a_transcript_that_ended_after_the_line_is_recorded_as_usual(self):
        self._old_transcript()
        self.set_epoch(dt(2026, 8, 1))
        self.assertEqual(self._sid8s(), [SID8])

    def test_the_line_is_drawn_once_and_never_moves(self):
        self.set_epoch(None)
        self._sid8s(now=dt(2026, 8, 20, 12, 0))
        self.assertEqual(self.read_epoch(), "2026-08-20T12:00:00")
        self._sid8s(now=dt(2026, 9, 30, 12, 0))
        self.assertEqual(self.read_epoch(), "2026-08-20T12:00:00")

    def test_nothing_can_fall_out_of_the_line_later(self):
        """The one property a retention window does not have."""
        self.set_epoch(None)
        self._sid8s(now=dt(2026, 8, 20, 12, 0))
        self.make_transcript(SID, dt(2026, 8, 21, 9, 0), dt(2026, 8, 21, 11, 0),
                             mtime=time.time() - 7200)
        for much_later in (dt(2026, 8, 22), dt(2026, 12, 31)):
            self.assertEqual(self._sid8s(now=much_later), [SID8])

    def test_a_session_still_running_when_the_line_was_drawn_is_kept(self):
        end = relay_common.local_now() - datetime.timedelta(minutes=40)
        self.set_epoch(end - datetime.timedelta(hours=1))
        self.make_transcript(SID, end - datetime.timedelta(hours=3), end,
                             mtime=time.time() - 7200)
        self.assertEqual(self._sid8s(), [SID8])

    def test_an_old_transcript_that_resumes_comes_back(self):
        self.set_epoch(dt(2026, 8, 20))
        self._old_transcript()
        self.assertEqual(self._sid8s(), [])
        self.make_transcript(SID, self.OLD[0], dt(2026, 8, 25, 10, 0),
                             mtime=time.time() - 7200)
        self.assertEqual(self._sid8s(), [SID8])

    def test_a_broken_line_records_everything_rather_than_nothing(self):
        self._old_transcript()
        relay_common.write_atomic(relay_common.epoch_path(self.cwd), "yesterday\n")
        self.assertEqual(self._sid8s(), [SID8])
        self.assertEqual(self.read_epoch(), "yesterday")

    def test_the_line_moves_by_editing_the_file(self):
        self._old_transcript()
        self.set_epoch(dt(2026, 8, 20))
        self.assertEqual(self._sid8s(), [])
        self.set_epoch(dt(2026, 8, 1))
        self.assertEqual(self._sid8s(), [SID8])

    def test_a_date_with_no_time_is_accepted(self):
        self._old_transcript()
        relay_common.write_atomic(relay_common.epoch_path(self.cwd), "2026-08-20\n")
        self.assertEqual(self._sid8s(), [])

    def test_relay_epoch_off_lifts_the_cutoff_without_touching_the_file(self):
        self._old_transcript()
        self.set_epoch(dt(2026, 8, 20))
        with mock.patch.dict(os.environ, {"RELAY_EPOCH": "none"}):
            self.assertEqual(self._sid8s(), [SID8])
        self.assertEqual(self.read_epoch(), "2026-08-20T00:00:00")

    def test_a_path_that_is_not_a_directory_gets_no_line(self):
        """`C:\\claude-projects\\x` unquoted through a shell arrives as
        `C:claude-projectsx`; a line filed under that name is debris."""
        self.set_epoch(None)
        mangled = self.cwd.replace(os.sep, "").replace("/", "")
        self.assertFalse(os.path.isdir(mangled))
        self.assertEqual(relay_detect.pending_sessions(mangled), [])
        self.assertFalse(relay_common.epoch_path(mangled).exists())
        self.assertFalse(relay_common.epoch_path(self.cwd).exists())

    def test_relay_epoch_overrides_the_file(self):
        self._old_transcript()
        self.set_epoch(dt(2026, 8, 1))
        with mock.patch.dict(os.environ, {"RELAY_EPOCH": "2026-08-20"}):
            self.assertEqual(self._sid8s(), [])


TASKS = """\
# tasks

未完のタスクだけを置く。

## 作業が残っているもの

- [ ] **実機確認**: iPad Safari での再生を確かめる
      PR #6 の申し送りに「未確認（要実機）」とある
- [ ] **bundle 分割**: 760KB / gzip 236KB
"""
IPAD = "- [ ] **実機確認**: iPad Safari での再生を確かめる"


class TestTasksReachTheReader(PipelineCase):
    """tasks.md is injected, and it is injected where it belongs."""

    def setUp(self):
        super().setUp()
        self.write_tasks(TASKS)

    def test_the_unchecked_items_are_injected(self):
        out = self.start_hook()
        self.assertIn("## tasks.md（未完のタスク）", out)
        self.assertIn(IPAD, out)
        self.assertIn("PR #6 の申し送り", out)

    def test_the_prose_around_them_is_not(self):
        self.assertNotIn("未完のタスクだけを置く", self.start_hook())

    def test_it_sits_between_the_diary_and_current(self):
        self.write_diary("2026-08-27", "## S1 19:11-22:30 (124c52dd)\n"
                                       "### done\n- 何かした\n")
        self.write_knowledge("current.md", "いまここ")
        out = self.start_hook()
        self.assertLess(out.index("## diary"), out.index("## tasks.md"))
        self.assertLess(out.index("## tasks.md"),
                        out.index("knowledge/current.md"))

    def test_a_project_without_the_file_gets_no_section(self):
        relay_common.tasks_path(self.cwd).unlink()
        self.assertNotIn("tasks.md", self.start_hook())


class TestTheStrikeOffJobHasWhatItNeeds(PipelineCase):
    """The overwrite job must see the same text relay_apply will match on."""

    def setUp(self):
        super().setUp()
        self.write_tasks(TASKS)
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        self.run_record()

    def job_body(self):
        return relay_common.read_text(
            relay_common.jobs_dir(self.cwd) / "overwrite.md")

    def job_body_for_record(self):
        return relay_common.read_text(
            relay_common.jobs_dir(self.cwd) / f"{SID}.md")

    def test_the_items_are_pasted_into_the_job(self):
        self.assertIn(IPAD, self.job_body())

    def test_the_job_asks_for_the_section(self):
        self.assertIn("===TASKS_DONE===", self.job_body())

    def test_the_job_points_at_the_condensed_reading(self):
        """Asked to read all of it, the recorder needs something it can."""
        body = self.job_body_for_record()
        self.assertIn("condensed", body)
        self.assertIn("最初から最後まで全部読むこと", body)

    def test_the_job_names_the_reading_by_path(self):
        parts = relay_common.condensed_parts(self.cwd, SID)
        self.assertTrue(parts)
        body = self.job_body_for_record()
        for p in parts:
            self.assertIn(p.as_posix(), body)

    def test_the_job_asks_for_the_count_it_will_be_checked_against(self):
        self.assertIn("===READ===", self.job_body_for_record())

    def test_the_job_still_carries_the_original(self):
        """Clipping is only safe while the full text is reachable."""
        self.assertIn(".jsonl", self.job_body_for_record())

    def test_nothing_tells_it_to_favour_the_end(self):
        """The one line that caused two sessions to be summarised wrong."""
        self.assertNotIn("末尾を優先", self.job_body_for_record())

    def test_the_job_names_the_diary_as_the_only_ground(self):
        self.assertIn("日記の `done`", self.job_body())

    def test_the_call_keeps_tasks_read_only(self):
        notice = self.run_record()
        self.assertIn("`tasks.md` を含む他の", notice)


class TestTheWholeStrikeOffPath(PipelineCase):
    """From a recorded session to an item gone from tasks.md."""

    def setUp(self):
        super().setUp()
        self.write_tasks(TASKS)

    def test_a_recorded_session_can_strike_an_item_off(self):
        self.record(SID, "### done\n- iPad Safari で再生を確認した")
        self.put_inbox("overwrite", "===CURRENT===\n実機確認まで終わった\n"
                                    f"===TASKS_DONE===\n{IPAD}\n===END===\n")
        summary = self.summary()
        self.assertEqual(summary["tasks"], "-1")
        self.assertNotIn("iPad Safari", self.read_tasks())
        self.assertIn("bundle 分割", self.read_tasks())
        self.assertEqual(self.read_knowledge("current.md").strip(),
                         "実機確認まで終わった")

    def test_the_next_startup_no_longer_offers_it(self):
        self.put_inbox("overwrite", f"===TASKS_DONE===\n{IPAD}\n===END===\n")
        self.apply()
        self.assertNotIn("iPad Safari", self.start_hook())

    def test_an_item_left_alone_comes_back_next_time(self):
        self.put_inbox("overwrite", "===TASKS_DONE===\n===END===\n")
        self.apply()
        self.assertIn(IPAD, self.start_hook())


TOKEN = "20260902-0130-k3v9"


class TestTheManualEntryPoint(PipelineCase):
    """The skill records the session it is running in, and only that one."""

    def run_skill(self, token=None):
        argv = ["relay_record.py", "--cwd", self.cwd]
        if token is not None:
            argv += ["--self", token]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             contextlib.redirect_stdout(out):
            relay_record.main()
        return out.getvalue()

    def live(self, sid=SID, token=None):
        """A transcript still being written to — no marker, freshly touched."""
        end = relay_common.local_now() - datetime.timedelta(minutes=2)
        p = self.make_transcript(sid, end - datetime.timedelta(hours=1), end)
        if token:
            with open(p, "a", encoding="utf-8") as f:
                f.write(token + "\n")
        return p

    def test_the_session_running_it_is_recorded_too(self):
        self.live(SID, TOKEN)
        # Proof the idle rule would otherwise have excluded it.
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])
        out = self.run_skill(TOKEN)
        self.assertIn(SID8, out)
        self.assertIn("いま動いているこのセッションを含む", out)

    def test_a_parallel_window_is_still_left_alone(self):
        self.live(SID, TOKEN)
        self.live(OTHER)  # somebody else's session, still being typed into
        out = self.run_skill(TOKEN)
        self.assertIn(SID8, out)
        self.assertNotIn(OTHER[:8], out)

    def test_an_unresolvable_token_says_so_and_records_the_rest(self):
        self.live(SID, TOKEN)
        self.make_transcript(OTHER, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        out = self.run_skill("20260902-9999-zzzz")
        self.assertIn("特定できませんでした", out)
        self.assertIn(OTHER[:8], out)   # the backlog is still worth doing
        self.assertNotIn(SID8, out)     # but we did not guess at ourselves

    def test_two_matches_name_neither(self):
        self.live(SID, TOKEN)
        self.live(OTHER, TOKEN)
        out = self.run_skill(TOKEN)
        self.assertIn("特定できませんでした", out)
        self.assertIn("記録するセッションはありません", out)

    def test_nothing_outstanding_says_so_plainly(self):
        self.assertIn("記録するセッションはありません", self.run_skill(TOKEN))

    def test_it_hands_over_the_same_jobs_startup_would(self):
        self.live(SID, TOKEN)
        out = self.run_skill(TOKEN)
        for fragment in ("general-purpose", "relay_apply.py", "merge_due",
                         "Blocked by classifier"):
            self.assertIn(fragment, out)

    def test_a_disabled_project_is_left_alone(self):
        self.live(SID, TOKEN)
        with mock.patch.dict(os.environ, {"RELAY_DISABLED": "1"}):
            out = self.run_skill(TOKEN)
        self.assertIn("無効です", out)
        self.assertNotIn(SID8, out)

    def test_a_project_out_of_scope_is_left_alone(self):
        self.live(SID, TOKEN)
        with mock.patch.dict(os.environ, {"RELAY_SCOPE": str(self.home / "no")}):
            out = self.run_skill(TOKEN)
        self.assertIn("RELAY_SCOPE", out)
        self.assertNotIn(SID8, out)


class TestTheSkillAndTheHookAgree(PipelineCase):
    """Two ways in, one procedure: the framing differs, the steps do not."""

    def test_the_steps_are_one_string(self):
        self.assertIn(relay_prompts._PROCEDURE, relay_prompts.TODO)
        self.assertIn(relay_prompts._PROCEDURE, relay_prompts.MANUAL)

    def test_only_the_framing_differs(self):
        self.assertIn("いま動いているこのセッションを含む", relay_prompts.MANUAL)
        self.assertNotIn("いま動いているこのセッションを含む", relay_prompts.TODO)

    def test_startup_says_what_it_is_about_to_do_before_doing_it(self):
        """Recording takes minutes; going silent for them looks like a hang.

        The user asked to be told first, so the notice has to put speaking
        ahead of the command that does the work.
        """
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        out = self.start_hook()
        self.assertLess(out.index("記録してから始めます"),
                        out.index("relay_record.py"))


class TestSessionEndStartsTheRecorder(PipelineCase):
    """With a CLI, the recording happens before the next session opens."""

    def setUp(self):
        super().setUp()
        self.ended_transcript = self.make_transcript(
            SID, dt(2026, 8, 27, 19, 11), dt(2026, 8, 27, 22, 30),
            mtime=time.time() - 7200)

    def end_with_cli(self, cli=r"C:\bin\claude.exe", pid=4321):
        with mock.patch.object(relay_common.shutil, "which", return_value=cli), \
             mock.patch.object(relay_spawn, "start",
                               return_value=(pid, "reparented")) as started:
            self.end_hook(SID)
        return started

    def test_it_starts_a_recorder_and_says_which_session(self):
        started = self.end_with_cli()
        argv = started.call_args[0][0]
        self.assertIn("relay_record_headless.py", " ".join(argv))
        self.assertIn(SID, argv)
        self.assertIn(self.cwd, argv)

    def test_the_marker_exists_before_the_next_session_can_open(self):
        """Written by the hook, not the recorder.

        The next session may start while the recorder is still getting off
        the ground; a marker written by the recorder itself would be too
        late to stop that session recording the same transcript.
        """
        self.end_with_cli()
        self.assertIn(SID, relay_common.running_sids(self.cwd))

    def test_the_end_marker_is_written_either_way(self):
        self.end_with_cli()
        self.assertTrue((relay_common.ended_dir(self.cwd) / SID).exists())

    def test_a_recorder_that_will_not_start_leaves_no_marker(self):
        with mock.patch.object(relay_common.shutil, "which",
                               return_value=r"C:\bin\claude.exe"), \
             mock.patch.object(relay_spawn, "start",
                               return_value=(None, "failed: no")):
            self.end_hook(SID)
        self.assertEqual(relay_common.running_sids(self.cwd), set())
        # Still on offer through the startup path, which is the point.
        self.assertIn(SID8, self.run_record())

    def test_a_disabled_project_starts_nothing(self):
        with mock.patch.dict(os.environ, {"RELAY_DISABLED": "1"}), \
             mock.patch.object(relay_spawn, "start") as started:
            self.end_hook(SID)
        self.assertFalse(started.called)


class TestTheRecorderDoesNotDisturbAnyone(PipelineCase):
    """It works in the background, and it must not read itself."""

    def run_one(self):
        """Run the CLI step with subprocess.run captured."""
        job = relay_common.jobs_dir(self.cwd) / "j.md"
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_text("body", encoding="utf-8")
        finished = mock.Mock(returncode=0, stdout="===DIARY===")
        with mock.patch.object(relay_record_headless.subprocess, "run",
                               return_value=finished) as ran:
            relay_record_headless.run_job(
                "claude", "haiku", job,
                relay_common.inbox_dir(self.cwd) / "j.txt")
        return ran.call_args

    def test_it_asks_windows_for_no_console(self):
        """This process owns no console, so a console app is given a fresh
        visible one. Observed: a terminal window appearing mid-recording."""
        if os.name != "nt":
            self.skipTest("Windows only")
        self.assertEqual(self.run_one().kwargs.get("creationflags"),
                         relay_spawn.CREATE_NO_WINDOW)

    def test_relay_is_switched_off_inside_the_recorder(self):
        """`claude` runs relay's hooks, so relay would otherwise read itself.

        Observed twice over: SessionStart put its own notice into the
        recorder's prompt, and the recorder's SessionEnd started a second
        recorder, which started a third.
        """
        self.assertEqual(self.run_one().kwargs["env"].get("RELAY_DISABLED"), "1")

    def test_the_api_key_is_dropped_by_default(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-x"}):
            env = self.run_one().kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_the_api_key_is_kept_when_asked(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-x",
                                          "RELAY_KEEP_API_KEY": "1"}):
            env = self.run_one().kwargs["env"]
        self.assertEqual(env.get("ANTHROPIC_API_KEY"), "sk-x")

    def test_the_model_never_gets_a_way_to_write(self):
        argv = self.run_one().args[0]
        self.assertIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read")


class TestASessionOpenedMidRecording(PipelineCase):
    """What the next session is told while the previous one is being written."""

    def setUp(self):
        super().setUp()
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        self.write_knowledge("current.md", "いまここ")
        relay_common.mark_running(self.cwd, SID, pid=4321)

    def test_the_reader_is_told_the_page_is_incomplete(self):
        """Never hand over a stale current.md without saying so."""
        out = self.start_hook()
        self.assertIn("記録中", out)
        self.assertIn("まだ入っていない", out)
        self.assertIn(SID8, out)

    def test_the_session_is_not_offered_for_recording_again(self):
        self.assertNotIn("relay_record.py", self.start_hook())

    def test_a_watch_is_left_so_the_wait_can_be_ended(self):
        self.start_hook()
        watch = relay_common.notified_dir(self.cwd) / "new.watch"
        self.assertEqual(relay_common.read_text(watch).split(), [SID])


class TestTheEndOfTheWaitIsAnnounced(PipelineCase):
    """The one place relay speaks mid-session, and it speaks once."""

    def setUp(self):
        super().setUp()
        relay_common.mark_running(self.cwd, SID, pid=4321)
        self.start_hook()          # leaves the watch for session "new"

    def test_nothing_is_said_while_the_recorder_is_working(self):
        self.assertEqual(self.prompt_hook(), "")

    def test_one_line_when_the_recording_lands(self):
        relay_common.clear_running(self.cwd, SID)
        out = self.prompt_hook()
        self.assertIn("記録が完了", out)
        self.assertIn(SID8, out)
        self.assertIn("current.md", out)

    def test_and_never_again_after_that(self):
        relay_common.clear_running(self.cwd, SID)
        self.assertNotEqual(self.prompt_hook(), "")
        self.assertEqual(self.prompt_hook(), "")
        self.assertEqual(self.prompt_hook(), "")

    def test_a_session_that_left_no_watch_is_never_spoken_to(self):
        relay_common.clear_running(self.cwd, SID)
        self.assertEqual(self.prompt_hook(sid="someone-else"), "")

    def test_the_watch_does_not_reach_another_project(self):
        other = str(pathlib.Path(self.cwd).parent / "other")
        pathlib.Path(other).mkdir(parents=True, exist_ok=True)
        relay_common.clear_running(self.cwd, SID)
        out = io.StringIO()
        payload = {"cwd": other, "session_id": "new"}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
             contextlib.redirect_stdout(out):
            user_prompt_hook.main()
        self.assertEqual(out.getvalue(), "")



class TestTheReadingIsHandedOverInParts(PipelineCase):
    """Feature: 読み物は Read が断れない大きさで、パートごとに名指しで渡す

    実測（2026-09-03）: 2.97MB のセッションの縮約版 282KB を「全部読め」と
    渡したところ、Read は 256KB / 25,000 トークンで拒否し、録音係は
    head / tail / grep に逃げて 3971 行のうち 100〜3800 行を読まずに書いた。
    """

    def big_transcript(self, turns=250):
        """A session long enough that its reading has to be split."""
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        rows = []
        for i in range(turns):
            rows.append({"type": "user", "sessionId": SID,
                         "timestamp": utc_text(dt(2026, 8, 27, 19, 11)),
                         "message": {"content": [
                             {"type": "text", "text": f"発言 {i}"}]}})
            rows.append({"type": "assistant", "sessionId": SID,
                         "message": {"content": [
                             {"type": "text", "text": f"応答 {i}"},
                             {"type": "tool_use", "name": "Bash",
                              "input": {"command": "x" * 2000}}]}})
        p = tdir / f"{SID}.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                for r in rows) + "\n", encoding="utf-8")
        os.utime(p, (time.time() - 7200, time.time() - 7200))
        return p

    def job_for(self, sid=SID):
        return relay_common.read_text(
            relay_common.jobs_dir(self.cwd) / f"{sid}.md")

    def test_a_long_session_is_listed_part_by_part(self):
        self.big_transcript()
        self.run_record()
        parts = relay_common.condensed_parts(self.cwd, SID)
        self.assertGreater(len(parts), 1)
        body = self.job_for()
        for i, p in enumerate(parts, 1):
            self.assertIn(f"  {i}. {p.as_posix()}", body)

    def test_it_is_told_not_to_fall_back_to_head_and_grep(self):
        """That fallback is exactly what produced the entry we lost."""
        self.big_transcript()
        self.run_record()
        body = self.job_for()
        self.assertIn("`offset` も `limit` も付けず", body)
        self.assertIn("代用してはいけません", body)

    def test_a_short_session_is_still_handed_one_file(self):
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        self.run_record()
        self.assertEqual(
            [p.name for p in relay_common.condensed_parts(self.cwd, SID)],
            [f"{SID}.md"])


class TestTheReadingIsCheckedAgainstWhatWasOpened(PipelineCase):
    """Feature: 読んだパート数を relay_apply が実物と突き合わせる"""

    def setUp(self):
        super().setUp()
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 22, 30), mtime=time.time() - 7200)
        self.parts = []
        d = relay_common.condensed_dir(self.cwd)
        d.mkdir(parents=True, exist_ok=True)

    def split_into(self, n):
        d = relay_common.condensed_dir(self.cwd)
        (d / f"{SID}.md").unlink(missing_ok=True)
        for i in range(1, n + 1):
            (d / f"{SID}.part{i:02d}.md").write_text("x", encoding="utf-8")

    def record(self, read_section):
        self.put_inbox(SID, f"===READ===\n{read_section}\n===DIARY===\n"
                            f"### done\n- 何かした\n===PITFALLS===\n"
                            f"===WORKFLOW===\n===END===\n")
        return self.summary()

    def entry(self):
        return relay_common.read_text(
            relay_common.diary_dir(self.cwd) / "2026-08-27.md")

    def test_reading_all_of_it_passes_without_a_mark(self):
        self.split_into(8)
        self.assertEqual(self.record("8")["short"], "0")
        self.assertNotIn("relay 注意", self.entry())

    def test_stopping_early_is_written_into_the_entry(self):
        self.split_into(8)
        self.assertEqual(self.record("3")["short"], "1")
        entry = self.entry()
        self.assertIn("relay 注意", entry)
        self.assertIn("8 パート", entry)
        self.assertIn("3 パート", entry)

    def test_the_entry_is_kept_even_when_it_is_short(self):
        """Refusing it would offer the same transcript again forever."""
        self.split_into(8)
        self.record("1")
        self.assertIn("### done", self.entry())
        self.assertIn("何かした", self.entry())

    def test_saying_nothing_at_all_counts_as_stopping_early(self):
        self.split_into(8)
        self.put_inbox(SID, "===DIARY===\n### done\n- 何かした\n"
                            "===PITFALLS===\n===WORKFLOW===\n===END===\n")
        self.assertEqual(self.summary()["short"], "1")
        self.assertIn("申告なし", self.entry())

    def test_a_one_file_reading_is_not_checked(self):
        """It was never refused, and older jobs have no count to give."""
        (relay_common.condensed_dir(self.cwd)
         / f"{SID}.md").write_text("x", encoding="utf-8")
        self.put_inbox(SID, "===DIARY===\n### done\n- 何かした\n"
                            "===PITFALLS===\n===WORKFLOW===\n===END===\n")
        self.assertEqual(self.summary()["short"], "0")
        self.assertNotIn("relay 注意", self.entry())

    def test_reading_more_than_there_is_is_not_an_error(self):
        self.split_into(3)
        self.assertEqual(self.record("4")["short"], "0")

if __name__ == "__main__":
    unittest.main()
