"""Shared scaffolding: a throwaway HOME, a throwaway project, and builders
for transcripts and diary files shaped like the real ones.

Transcripts here carry the same oddities as the real files — a first line
with no timestamp and a trailing "last-prompt" record with none either — so
the span logic is exercised against the shape it actually meets.
"""
import datetime
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "relay" / "scripts"))

import relay_common  # noqa: E402


def utc_text(local_dt):
    """Render a local datetime the way a transcript stores it (UTC + Z)."""
    utc = local_dt - relay_common.local_offset()
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def dt(*args):
    return datetime.datetime(*args)


class RelayCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self._tmp.name)
        self.cwd = str(self.home / "proj" / "demo")
        (self.home / "proj" / "demo").mkdir(parents=True)
        patcher = mock.patch.object(pathlib.Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        # RELAY_* in the developer's own environment must not reach the tests.
        # RELAY_SCOPE in particular would put the throwaway project out of
        # scope and turn every hook into a silent no-op.
        clean = {k: v for k, v in os.environ.items() if not k.startswith("RELAY_")}
        env = mock.patch.dict(os.environ, clean, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)
        # A project relay has already been running in. Without this every
        # fixture transcript would sit before the epoch the first call writes,
        # and the whole suite would go green while detecting nothing at all.
        self.set_epoch(dt(2000, 1, 1))

    def set_epoch(self, when):
        """Pin the cutoff. `when=None` removes it, back to an unseen project."""
        p = relay_common.epoch_path(self.cwd)
        if when is None:
            p.unlink(missing_ok=True)
            return p
        relay_common.write_atomic(p, when.isoformat() + "\n")
        return p

    def read_epoch(self):
        return relay_common.read_text(relay_common.epoch_path(self.cwd)).strip()

    # --- transcripts ------------------------------------------------------

    def make_transcript(self, sid, start, end, n_user=3, mtime=None):
        """A transcript running from `start` to `end` (local datetimes)."""
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{sid}.jsonl"
        lines = [json.dumps({"type": "summary", "leafUuid": "x", "sessionId": sid})]
        step = (end - start) / max(n_user, 1)
        for i in range(n_user):
            lines.append(json.dumps({
                "type": "user", "sessionId": sid,
                "timestamp": utc_text(start + step * i),
                "message": {"content": f"m{i}"}}))
        lines.append(json.dumps({
            "type": "assistant", "sessionId": sid,
            "timestamp": utc_text(end), "message": {"content": "ok"}}))
        # Real transcripts end on a record with no timestamp at all.
        lines.append(json.dumps({
            "type": "last-prompt", "lastPrompt": "x",
            "leafUuid": "y", "sessionId": sid}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def make_transcript_at(self, sid, times, mtime=None):
        """A transcript whose messages sit at exactly the given local times."""
        tdir = relay_common.transcripts_dir(self.cwd)
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"{sid}.jsonl"
        lines = [json.dumps({"type": "summary", "leafUuid": "x", "sessionId": sid})]
        for i, when in enumerate(times):
            lines.append(json.dumps({
                "type": "user", "sessionId": sid, "timestamp": utc_text(when),
                "message": {"content": f"m{i}"}}))
        lines.append(json.dumps({
            "type": "last-prompt", "lastPrompt": "x",
            "leafUuid": "y", "sessionId": sid}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def mark_ended(self, sid, mtime=None):
        d = relay_common.ended_dir(self.cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / sid
        p.touch()
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    # --- project files ----------------------------------------------------

    def write_diary(self, date, text):
        d = relay_common.diary_dir(self.cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{date}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def read_diary(self, date):
        return relay_common.read_text(
            relay_common.diary_dir(self.cwd) / f"{date}.md")

    def write_knowledge(self, name, text):
        p = relay_common.knowledge_path(self.cwd, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def read_knowledge(self, name):
        return relay_common.read_text(relay_common.knowledge_path(self.cwd, name))

    def write_tasks(self, text):
        p = relay_common.tasks_path(self.cwd)
        p.write_text(text, encoding="utf-8")
        return p

    def read_tasks(self):
        return relay_common.read_text(relay_common.tasks_path(self.cwd))

    def put_inbox(self, name, text):
        d = relay_common.inbox_dir(self.cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def hook_stdin(self, payload):
        """Feed a hook its JSON on stdin and capture what it prints."""
        import io
        buf = io.StringIO()
        return mock.patch.multiple(
            sys, stdin=io.StringIO(json.dumps(payload)), stdout=buf), buf
