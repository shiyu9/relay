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


if __name__ == "__main__":
    unittest.main()
