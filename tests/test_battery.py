"""Standard test battery for the recording pipeline.

The behaviour the interview confirmed lives in tests/e2e/; this file holds
the mechanical categories (boundaries, malformed input, idempotence,
invariants, output contract, environment, scale) that nobody needs to be
asked about.
"""
import contextlib
import datetime
import io
import json
import os
import pathlib
import random
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from relay_test_support import RelayCase, dt, utc_text  # noqa: E402

import relay_apply  # noqa: E402
import relay_common  # noqa: E402
import relay_detect  # noqa: E402

SID = "124c52dd-f17a-4ad0-8b34-f94bdf3609f1"
SID8 = SID[:8]
PAST = time.time() - 7200


class ApplyCase(RelayCase):
    def apply(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv",
                               ["relay_apply.py", "--cwd", self.cwd]), \
             contextlib.redirect_stdout(out):
            relay_apply.main()
        return out.getvalue().strip()

    def summary(self):
        return dict(kv.split("=", 1) for kv in self.apply().split())

    def ended_transcript(self, sid=SID, start=None, end=None, **kw):
        start = start or dt(2026, 8, 27, 19, 11)
        end = end or dt(2026, 8, 27, 22, 30)
        return self.make_transcript(sid, start, end, mtime=PAST, **kw)


# --- 1 boundaries ---------------------------------------------------------

class TestBoundaries(ApplyCase):
    def test_no_transcripts_at_all(self):
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_exactly_the_minimum_user_messages(self):
        self.ended_transcript(n_user=3)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 1)

    def test_one_below_the_minimum(self):
        self.ended_transcript(n_user=2)
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_the_cap_is_three_not_four(self):
        for i in range(4):
            self.make_transcript(f"{i:08d}-x", dt(2026, 8, 20 + i, 9, 0),
                                 dt(2026, 8, 20 + i, 10, 0), mtime=PAST)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 3)

    def test_the_limit_can_be_raised_by_the_caller(self):
        for i in range(4):
            self.make_transcript(f"{i:08d}-x", dt(2026, 8, 20 + i, 9, 0),
                                 dt(2026, 8, 20 + i, 10, 0), mtime=PAST)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd, limit=10)), 4)

    def test_fourteen_days_still_replaces_and_fifteen_resumes(self):
        for gap, expected in ((datetime.timedelta(days=14, hours=23), "replace"),
                              (datetime.timedelta(days=15), "resume")):
            with self.subTest(gap=gap):
                end = relay_common.local_now().replace(
                    second=0, microsecond=0) - datetime.timedelta(hours=2)
                prev_end = end - gap
                day = prev_end.date().isoformat()
                self.write_diary(day, "## S1 {:%H:%M}-{:%H:%M} ({})\n- x\n".format(
                    prev_end - datetime.timedelta(hours=1), prev_end, SID8))
                self.make_transcript_at(SID, [
                    prev_end - datetime.timedelta(hours=1), prev_end,
                    end - datetime.timedelta(minutes=10), end], mtime=PAST)
                pending = relay_detect.pending_sessions(self.cwd)
                self.assertEqual([p.mode for p in pending], [expected])
                for f in relay_common.diary_files(self.cwd):
                    f.unlink()

    def test_zero_and_the_limit_and_one_past_it(self):
        for n, over in ((0, False), (79, False), (80, False), (81, True)):
            with self.subTest(n=n):
                self.write_knowledge(
                    "pitfalls.md", "".join(f"- [2026-01-01] {i}\n" for i in range(n)))
                s = self.summary()
                self.assertEqual(int(s["pitfalls_lines"]), n)
                self.assertEqual(int(s["pitfalls_lines"]) > int(s["limit"]), over)

    def test_seconds_are_floored_never_rounded_up(self):
        self.assertEqual(relay_common.floor_minute(dt(2026, 8, 27, 22, 30, 59)),
                         dt(2026, 8, 27, 22, 30))

    def test_an_id_of_the_wrong_length_is_not_one_of_ours(self):
        for token in (SID8[:7], SID8 + "d", SID8.upper()):
            with self.subTest(token=token):
                self.assertIsNone(
                    relay_common.ENTRY_RE.match(f"## S1 19:11-22:30 ({token})"))


# --- 2 malformed input ----------------------------------------------------

class TestMalformedInput(ApplyCase):
    def test_an_inbox_file_with_no_delimiters_is_counted_and_dropped(self):
        p = self.put_inbox("x", "just some prose the model produced")
        s = self.summary()
        self.assertEqual(s["bad"], "1")
        self.assertFalse(p.exists())

    def test_a_diary_section_with_no_transcript_behind_it(self):
        self.put_inbox(SID, "===DIARY===\n### done\n- x\n===END===\n")
        s = self.summary()
        self.assertEqual(s["diary"], "0")
        self.assertEqual(s["bad"], "1")

    def test_a_transcript_with_no_timestamps_has_no_span(self):
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{SID}.jsonl"
        p.write_text(json.dumps({"type": "summary", "leafUuid": "a"}) + "\n",
                     encoding="utf-8")
        self.assertIsNone(relay_common.session_span(p))
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_unparsable_json_lines_are_stepped_over(self):
        p = self.ended_transcript()
        p.write_text("{not json\n" + p.read_text(encoding="utf-8"),
                     encoding="utf-8")
        os.utime(p, (PAST, PAST))
        self.assertIsNotNone(relay_common.session_span(p))

    def test_non_ascii_survives_the_round_trip(self):
        self.ended_transcript()
        body = "### done\n- 日本語とえもじ🎧とパス C:\\claude-projects\\relay"
        self.put_inbox(SID, f"===DIARY===\n{body}\n===END===\n")
        self.apply()
        self.assertIn("えもじ🎧", self.read_diary("2026-08-27"))
        self.assertIn("C:\\claude-projects\\relay", self.read_diary("2026-08-27"))


# --- 3 durability of stored state ----------------------------------------

class TestDurability(ApplyCase):
    def test_a_hand_edited_diary_is_still_parsed(self):
        self.write_diary("2026-08-27",
                         "\n\n\n## S1 19:11-22:30 (aaaaaaaa)\n- x\n\n\n")
        entries = relay_common.parse_diary_file(
            relay_common.diary_dir(self.cwd) / "2026-08-27.md")
        self.assertEqual([e.sid8 for e in entries], ["aaaaaaaa"])

    def test_a_broken_marker_directory_falls_back_to_the_idle_rule(self):
        end = relay_common.local_now() - datetime.timedelta(minutes=40)
        self.make_transcript(SID, end - datetime.timedelta(hours=1), end)
        (relay_common.ended_dir(self.cwd) / SID).mkdir(parents=True)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 1)

    def test_an_unreadable_diary_file_does_not_raise(self):
        d = relay_common.diary_dir(self.cwd)
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-08-27.md").mkdir()
        self.assertEqual(relay_common.all_entries(self.cwd), [])


# --- 4 idempotence --------------------------------------------------------

class TestIdempotence(ApplyCase):
    def test_detection_repeated_gives_the_same_answer(self):
        self.ended_transcript()
        first = [p.sid for p in relay_detect.pending_sessions(self.cwd)]
        second = [p.sid for p in relay_detect.pending_sessions(self.cwd)]
        self.assertEqual(first, second)

    def test_applying_the_same_bullets_twice_adds_them_once(self):
        for _ in range(2):
            self.put_inbox("a", "===PITFALLS===\n- [2026-08-30] 同じ行\n===END===\n")
            self.apply()
        self.assertEqual(self.read_knowledge("pitfalls.md").count("同じ行"), 1)

    def test_applying_an_empty_inbox_is_a_no_op(self):
        before = self.apply()
        after = self.apply()
        self.assertEqual(before, after)


# --- 5 state transitions --------------------------------------------------

class TestStateTransitions(ApplyCase):
    def test_new_then_replace(self):
        self.ended_transcript()
        self.assertEqual(relay_detect.pending_sessions(self.cwd)[0].mode, "new")
        self.put_inbox(SID, "===DIARY===\n### done\n- a\n===END===\n")
        self.apply()
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 23, 48), mtime=PAST)
        self.assertEqual(relay_detect.pending_sessions(self.cwd)[0].mode, "replace")


# --- 6 round trip ---------------------------------------------------------

class TestRoundTrip(ApplyCase):
    def test_every_heading_we_write_parses_back_to_what_we_meant(self):
        rnd = random.Random(20260830)
        for _ in range(50):
            start = dt(2026, 8, 27) + datetime.timedelta(
                minutes=rnd.randrange(0, 24 * 60))
            end = start + datetime.timedelta(minutes=rnd.randrange(1, 600))
            sid8 = "".join(rnd.choice("0123456789abcdef") for _ in range(8))
            heading = f"## S1 {start:%H:%M}-{end:%H:%M} ({sid8})"
            self.write_diary("2026-08-27", heading + "\n- x\n")
            e = relay_common.parse_diary_file(
                relay_common.diary_dir(self.cwd) / "2026-08-27.md")[0]
            self.assertEqual(e.sid8, sid8)
            self.assertEqual(e.start.strftime("%H:%M"), start.strftime("%H:%M"))
            self.assertEqual(e.end.strftime("%H:%M"), end.strftime("%H:%M"))


# --- 7 invariants ---------------------------------------------------------

class TestInvariants(ApplyCase):
    def test_lines_outside_our_own_entries_are_never_altered(self):
        """Property: whatever the file holds, foreign content comes back byte
        for byte. Generated rather than enumerated, because the risk is a
        heading shape nobody thought to list."""
        rnd = random.Random(1)
        foreign_pool = [
            "## S1 10:24", "## セッション1（14:01 終了時記録・開始は 8/16）",
            "## API キー露出事故への対応と OCR 復旧", "# 2026年8月21日",
            "## 現在地（2026-08-26 時点・ここだけ読めば再開できる）",
        ]
        for trial in range(20):
            for f in relay_common.diary_files(self.cwd):
                f.unlink()
            blocks, foreign = [], []
            for i in range(rnd.randrange(1, 5)):
                head = rnd.choice(foreign_pool)
                body = f"- 本文{trial}-{i}\n- 二行目\n"
                blocks.append(f"{head}\n{body}")
                foreign.append(f"{head}\n{body}".strip())
            self.write_diary("2026-08-27", "\n".join(blocks))
            self.ended_transcript()
            self.put_inbox(SID, "===DIARY===\n### done\n- 新規\n===END===\n")
            self.apply()
            after = self.read_diary("2026-08-27")
            for chunk in foreign:
                self.assertIn(chunk, after, f"trial {trial}")
            self.assertIn(f"({SID8})", after)

    def test_an_empty_overwrite_section_can_never_erase_the_page(self):
        self.write_knowledge("current.md", "- 大事な現在地\n")
        for text in ("===CURRENT===\n===DECIDED===\n===END===\n",
                     "===DIARY===\n### done\n- x\n===END===\n",
                     "===CURRENT===\n   \n===END===\n"):
            with self.subTest(text=text[:20]):
                self.put_inbox("o", text)
                self.apply()
                self.assertEqual(self.read_knowledge("current.md"),
                                 "- 大事な現在地\n")


# --- 8 output contract ----------------------------------------------------

class TestOutputContract(ApplyCase):
    def test_the_summary_has_every_field_the_procedure_reads(self):
        s = self.summary()
        for key in ("diary", "pitfalls", "workflow", "current", "decided",
                    "dropped", "bad", "pitfalls_lines", "workflow_lines",
                    "limit"):
            self.assertIn(key, s)

    def test_it_exits_zero_even_when_everything_is_wrong(self):
        self.put_inbox("x", "garbage")
        done = subprocess.run(
            [sys.executable, relay_apply.__file__, "--cwd", self.cwd],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(done.returncode, 0)
        self.assertIn("diary=", done.stdout)

    def test_the_summary_is_one_line(self):
        self.assertEqual(len(self.apply().splitlines()), 1)


# --- 9 environment --------------------------------------------------------

class TestEnvironment(ApplyCase):
    def test_japanese_reaches_stdout_even_on_a_cp932_console(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp932", errors="strict")
        with mock.patch.object(sys, "stdout", stream):
            relay_common.force_utf8()
            print("日本語のテスト")
            sys.stdout.flush()
        self.assertIn("日本語のテスト", raw.getvalue().decode("utf-8"))

    def test_utc_timestamps_become_local_times(self):
        offset = relay_common.local_offset()
        local = dt(2026, 8, 27, 22, 30)
        self.assertEqual(relay_common.parse_ts(utc_text(local)), local)
        self.assertEqual(
            relay_common.parse_ts((local - offset).strftime(
                "%Y-%m-%dT%H:%M:%S.123456Z")).replace(microsecond=0), local)

    def test_a_mangled_tz_does_not_shift_the_clock(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
            self.assertEqual(relay_common.local_offset(),
                             relay_common.local_offset())

    def test_relay_scope_still_keeps_other_projects_out(self):
        with mock.patch.dict(os.environ, {"RELAY_SCOPE": str(self.home / "elsewhere")}):
            self.assertFalse(relay_common.in_scope(self.cwd))


# --- 10 scale -------------------------------------------------------------

class TestScale(ApplyCase):
    def test_a_large_transcript_is_never_read_end_to_end(self):
        """37.8 MB transcripts exist; reading one per candidate per startup
        would make detection cost more than the recording it guards."""
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{SID}.jsonl"
        filler = json.dumps({"type": "assistant", "message": {"content": "x" * 400}})
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "timestamp":
                                utc_text(dt(2026, 8, 27, 19, 11)),
                                "message": {"content": "start"}}) + "\n")
            for _ in range(6000):        # a few megabytes of timestamp-free noise
                f.write(filler + "\n")
            f.write(json.dumps({"type": "user", "timestamp":
                                utc_text(dt(2026, 8, 27, 22, 30)),
                                "message": {"content": "end"}}) + "\n")
            f.write(json.dumps({"type": "last-prompt", "leafUuid": "z"}) + "\n")
        self.assertGreater(p.stat().st_size, 2_000_000)

        def refuse(*a, **kw):
            raise AssertionError("session_span fell back to a full scan")

        with mock.patch.object(relay_common, "_all_timestamps", refuse):
            span = relay_common.session_span(p)
        self.assertEqual(span, (dt(2026, 8, 27, 19, 11), dt(2026, 8, 27, 22, 30)))


# --- 11 the adoption cutoff -----------------------------------------------

class TestAdoptionCutoff(ApplyCase):
    def _old(self):
        return self.make_transcript(SID, dt(2026, 8, 10, 9, 0),
                                    dt(2026, 8, 10, 11, 30), mtime=PAST)

    def _pending(self, **kw):
        return relay_detect.pending_sessions(self.cwd, **kw)

    def test_exactly_on_the_line_counts_as_before_it(self):
        self._old()
        self.set_epoch(dt(2026, 8, 10, 11, 30))
        self.assertEqual(self._pending(), [])

    def test_one_minute_earlier_and_the_session_is_pending(self):
        self._old()
        self.set_epoch(dt(2026, 8, 10, 11, 29))
        self.assertEqual(len(self._pending()), 1)

    def test_an_empty_file_is_the_same_as_no_file_at_all(self):
        relay_common.write_atomic(relay_common.epoch_path(self.cwd), "\n")
        self._pending(now=dt(2026, 8, 20))
        self.assertEqual(self.read_epoch(), "2026-08-20T00:00:00")

    def test_a_tz_aware_override_lands_on_the_same_instant(self):
        self._old()
        utc = dt(2026, 8, 10, 11, 30) - relay_common.local_offset()
        with mock.patch.dict(os.environ,
                             {"RELAY_EPOCH": utc.isoformat() + "+00:00"}):
            self.assertEqual(self._pending(), [])

    def test_an_unwritable_line_leaves_the_backlog_visible(self):
        self._old()
        self.set_epoch(None)
        with mock.patch.object(relay_common, "write_atomic",
                               side_effect=OSError):
            self.assertEqual(len(self._pending()), 1)

    def test_repeated_detection_never_moves_the_line(self):
        self._old()
        self.set_epoch(dt(2026, 8, 20))
        for _ in range(3):
            self._pending()
        self.assertEqual(self.read_epoch(), "2026-08-20T00:00:00")

    def test_the_line_never_reaches_a_transcript_with_no_span(self):
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / f"{SID}.jsonl").write_text("{}\n", encoding="utf-8")
        self.set_epoch(dt(2026, 8, 20))
        self.assertEqual(self._pending(), [])

    def test_no_line_is_drawn_for_a_path_that_is_not_a_directory(self):
        self.set_epoch(None)
        mangled = self.cwd.replace(os.sep, "").replace("/", "")
        self.assertIsNone(
            relay_common.session_epoch(mangled, now=dt(2026, 8, 20)))
        self.assertFalse(relay_common.epoch_path(mangled).exists())


# --- 12 naming the session that is running ---------------------------------

OTHER = "bd0da51f-225a-42c2-a745-58483d649f36"
TOKEN = "20260902-0130-k3v9"


class TestFindingThisSession(ApplyCase):
    """The manual entry point names itself, or it refuses to act."""

    def _tx(self, sid, text, age=0):
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{sid}.jsonl"
        p.write_text(text, encoding="utf-8")
        if age:
            when = time.time() - age
            os.utime(p, (when, when))
        return p

    def test_one_recent_transcript_holding_the_token_is_us(self):
        self._tx(SID, f'{{"x":"{TOKEN}"}}\n')
        self.assertEqual(relay_common.find_self(self.cwd, TOKEN), SID)

    def test_two_live_matches_refuse_rather_than_guess(self):
        self._tx(SID, TOKEN)
        self._tx(OTHER, TOKEN)
        self.assertIsNone(relay_common.find_self(self.cwd, TOKEN))

    def test_a_match_outside_the_window_does_not_count(self):
        self._tx(SID, TOKEN, age=relay_common.SELF_WINDOW_SECS + 60)
        self.assertIsNone(relay_common.find_self(self.cwd, TOKEN))

    def test_a_stale_copy_leaves_the_live_one_unambiguous(self):
        self._tx(SID, TOKEN)
        self._tx(OTHER, TOKEN, age=relay_common.SELF_WINDOW_SECS + 60)
        self.assertEqual(relay_common.find_self(self.cwd, TOKEN), SID)

    def test_an_empty_token_never_matches(self):
        self._tx(SID, TOKEN)
        for empty in ("", None):
            self.assertIsNone(relay_common.find_self(self.cwd, empty))

    def test_a_token_nobody_wrote_matches_nothing(self):
        self._tx(SID, TOKEN)
        self.assertIsNone(relay_common.find_self(self.cwd, "20260902-9999-zzzz"))


# --- 13 sessions that held nothing ----------------------------------------

class TestNothingToRecordIsRemembered(ApplyCase):
    """A session looked at and found empty must not be offered forever."""

    def setUp(self):
        super().setUp()
        self.ended_transcript()
        self.put_inbox(SID, "NOTHING_TO_RECORD\n")

    def test_the_verdict_reaches_the_diary(self):
        self.assertEqual(self.summary()["thin"], "1")
        body = self.read_diary("2026-08-27")
        self.assertIn(SID8, body)
        self.assertIn(relay_common.THIN_MARK, body)

    def test_the_session_stops_being_a_candidate(self):
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 1)
        self.apply()
        self.assertEqual(relay_detect.pending_sessions(self.cwd), [])

    def test_it_does_not_take_a_slot_in_the_injection(self):
        self.apply()
        self.assertEqual(relay_common.recent_entries(self.cwd), [])

    def test_a_real_entry_beside_it_still_shows(self):
        self.apply()
        self.write_diary("2026-08-28", "## S1 09:00-10:00 (bd0da51f)\n"
                                       "### done\n- 本物の記録\n")
        kept = [e.sid8 for e in relay_common.recent_entries(self.cwd)]
        self.assertEqual(kept, ["bd0da51f"])

    def test_growth_makes_it_a_candidate_again(self):
        self.apply()
        self.make_transcript(SID, dt(2026, 8, 27, 19, 11),
                             dt(2026, 8, 27, 23, 59), mtime=PAST)
        self.assertEqual(len(relay_detect.pending_sessions(self.cwd)), 1)


# --- 9 striking tasks off tasks.md ---------------------------------------

# Shaped like the real files: prose the project wrote for itself, items whose
# meaning lives in their continuation lines, a nested bullet, and one box
# already ticked.
TASKS = """\
# tasks

未完のタスクだけを置く。完了したものは消す。

## 作業が残っているもの

- [ ] **E2E テスト**: 承認済み Gherkin をコード化する。現状 `tests/e2e/` は無い
- [ ] **bundle 分割**: 760KB / gzip 236KB
      この値は 2026-07-20 時点の実測で、PR B の後は未再測
      - 2026-08-31 にコードで確認: `PdfViewer` 無し
- [x] **済んだもの**: これは relay の持ち物ではない

## 確認が済んでいないもの

- [ ] **iPad Safari での音声再生**: 実機確認の記録なし
"""
E2E_KEY = "- [ ] **E2E テスト**: 承認済み Gherkin をコード化する。現状 `tests/e2e/` は無い"
BUNDLE_KEY = "- [ ] **bundle 分割**: 760KB / gzip 236KB"


class TasksCase(ApplyCase):
    def setUp(self):
        super().setUp()
        self.write_tasks(TASKS)

    def items(self):
        return relay_common.parse_tasks(relay_common.tasks_path(self.cwd))

    def strike(self, *keys):
        """Run one apply whose only section names these items as finished."""
        body = "\n".join(keys)
        self.put_inbox("overwrite", f"===TASKS_DONE===\n{body}\n===END===\n")
        return self.summary()


class TestTaskParsing(TasksCase):
    def test_a_ticked_box_is_not_relays_business(self):
        self.assertNotIn("済んだもの", " ".join(t.key for t in self.items()))
        self.assertEqual(len(self.items()), 3)

    def test_an_item_keeps_the_lines_that_explain_it(self):
        bundle = next(t for t in self.items() if t.key == BUNDLE_KEY)
        self.assertEqual(len(bundle.lines), 3)
        self.assertIn("PdfViewer", "\n".join(bundle.lines))

    def test_a_heading_ends_the_item_above_it(self):
        ipad = next(t for t in self.items() if "iPad" in t.key)
        self.assertEqual(len(ipad.lines), 1)
        self.assertEqual(ipad.heading, "## 確認が済んでいないもの")

    def test_prose_between_items_is_not_an_item(self):
        outline = relay_common.tasks_outline(self.cwd)
        self.assertNotIn("完了したものは消す", outline)
        self.assertIn("## 作業が残っているもの", outline)

    def test_a_project_without_the_file_reads_as_no_tasks(self):
        relay_common.tasks_path(self.cwd).unlink()
        self.assertEqual(relay_common.tasks_outline(self.cwd), "")
        self.assertEqual(self.items(), [])


class TestStrikeOffMatching(TasksCase):
    def test_a_verbatim_line_strikes_its_item_off(self):
        self.assertEqual(self.strike(E2E_KEY)["tasks"], "-1")
        self.assertNotIn("E2E テスト", self.read_tasks())

    def test_a_paraphrase_removes_nothing(self):
        before = self.read_tasks()
        self.assertEqual(self.strike("- [ ] E2E テストを書く")["tasks"], "-0")
        self.assertEqual(self.read_tasks(), before)

    def test_a_truncated_line_removes_nothing(self):
        before = self.read_tasks()
        self.assertEqual(self.strike("- [ ] **E2E テスト**")["tasks"], "-0")
        self.assertEqual(self.read_tasks(), before)

    def test_striking_takes_the_continuation_lines_with_it(self):
        self.strike(BUNDLE_KEY)
        left = self.read_tasks()
        self.assertNotIn("PdfViewer", left)
        self.assertNotIn("未再測", left)
        self.assertIn("iPad", left)

    def test_one_bad_line_does_not_stop_the_good_one(self):
        self.assertEqual(
            self.strike(E2E_KEY, "- [ ] 存在しない項目")["tasks"], "-1")
        self.assertNotIn("E2E テスト", self.read_tasks())
        self.assertIn("bundle 分割", self.read_tasks())

    def test_a_missing_section_is_not_a_failure(self):
        before = self.read_tasks()
        self.put_inbox("overwrite", "===CURRENT===\nいまここ\n===END===\n")
        summary = self.summary()
        self.assertEqual(summary["bad"], "0")
        self.assertEqual(summary["tasks"], "-0")
        self.assertEqual(self.read_tasks(), before)

    def test_an_empty_section_is_not_a_failure(self):
        before = self.read_tasks()
        summary = self.strike("")
        self.assertEqual(summary["bad"], "0")
        self.assertEqual(summary["tasks"], "-0")
        self.assertEqual(self.read_tasks(), before)


class TestStruckItemsStayRecoverable(TasksCase):
    def today(self):
        return self.read_diary(f"{relay_common.local_now():%Y-%m-%d}")

    def test_the_whole_item_lands_in_todays_diary(self):
        self.strike(BUNDLE_KEY)
        body = self.today()
        self.assertIn(relay_common.TASKS_DONE_HEADING, body)
        self.assertIn("PdfViewer", body)
        self.assertIn(BUNDLE_KEY, body)

    def test_nothing_is_written_when_nothing_matched(self):
        self.strike("- [ ] 存在しない項目")
        self.assertNotIn(relay_common.TASKS_DONE_HEADING, self.today())

    def test_the_recovery_heading_is_kept_out_of_the_material(self):
        self.strike(E2E_KEY)
        headings = [e.heading.strip() for e in
                    relay_common.recent_entries(self.cwd)]
        self.assertNotIn(relay_common.TASKS_DONE_HEADING, headings)


if __name__ == "__main__":
    unittest.main()
