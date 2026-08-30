"""Shared helpers for relay hooks.

The recording pipeline has no ledger: a transcript is "already recorded"
exactly when the diary holds an entry for it whose end time is not older
than the transcript's last timestamp. Everything below exists to make that
one comparison cheap and exact.
"""
import datetime
import json
import os
import pathlib
import re
import sys

# On Windows, an IANA-style TZ (e.g. "Asia/Tokyo" set by Git Bash) is not
# understood by the C runtime and silently degrades localtime to UTC.
# Drop it so datetime.now() uses the real system timezone; children inherit
# the cleaned environment.
if os.name == "nt" and "/" in os.environ.get("TZ", ""):
    del os.environ["TZ"]

# How much of a transcript to read when looking for its first/last timestamp.
# Transcripts reach tens of megabytes (37.8 MB observed), so the span must
# never cost a full scan.
SPAN_CHUNK = 64 * 1024
# A session left alone this long is treated as ended even without an end
# marker. The marker (written by the SessionEnd hook) is the fast path; this
# is the fallback for when the console dies before the hook can write it.
IDLE_SECS = 30 * 60
# Writes this long after the end marker mean the session came back to life.
MARKER_GRACE_SECS = 60
# A gap this large between the recorded entry and new activity is a new
# session in all but name, so it gets its own entry instead of rewriting a
# fortnight-old one.
RESUME_CUTOFF_DAYS = 15
# Jobs handed to the model in one session start. The rest are caught next
# time; candidates shrink monotonically, so nothing is stranded.
MAX_JOBS = 3
DEFAULT_MIN_USER_MSGS = 3
# knowledge/ files are meant to stay small; past this the pruning job runs.
PRUNE_LINES = 80

# "## S1 19:11-22:30 (124c52dd)" — the only headings relay owns.
ENTRY_RE = re.compile(
    r"^## S(\d+) (\d{2}):(\d{2})-(\d{2}):(\d{2}) \(([0-9a-f]{8})\)\s*$")
# A heading that looks like it carries an id but does not parse. Kept apart
# from "has no id at all": those are other people's headings and we leave
# them alone, while a broken one means our own writing went wrong.
IDISH_RE = re.compile(r"^## .*\([0-9a-fA-F]{4,12}\)\s*$")
DIARY_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.md")
# Written by relay_apply, never by a model; excluded from injection.
DROPPED_HEADING = "## relay: decided.md から落とした項目"


def local_now():
    """datetime.now() that survives a mangled TZ env var on Windows.

    The MSVC runtime caches TZ and misparses IANA names (e.g. Git Bash's
    TZ=Asia/Tokyo), silently degrading localtime to UTC. GetLocalTime
    bypasses the C runtime entirely.
    """
    if os.name == "nt":
        try:
            import ctypes

            class SYSTEMTIME(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ushort) for n in (
                    "wYear", "wMonth", "wDayOfWeek", "wDay",
                    "wHour", "wMinute", "wSecond", "wMilliseconds")]

            st = SYSTEMTIME()
            ctypes.windll.kernel32.GetLocalTime(ctypes.byref(st))
            return datetime.datetime(st.wYear, st.wMonth, st.wDay,
                                     st.wHour, st.wMinute, st.wSecond)
        except Exception:
            pass  # no ctypes / not really Windows -> the portable clock below
    return datetime.datetime.now()


def local_offset():
    """UTC->local offset, derived from local_now() rather than the C runtime.

    astimezone() would go through the same mangled-TZ path local_now() exists
    to avoid, so the offset is measured instead: the difference between the
    two clocks, rounded to the minute (no real zone is finer).
    """
    utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    delta = local_now() - utc
    return datetime.timedelta(minutes=round(delta.total_seconds() / 60))


def parse_ts(text):
    """'2026-08-29T10:11:52.334Z' -> naive local datetime, or None."""
    if not text:
        return None
    try:
        utc = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if utc.tzinfo is not None:
        utc = utc.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return utc + local_offset()


def force_utf8():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass  # already-wrapped or redirected streams have nothing to fix


def relay_home():
    return pathlib.Path.home() / ".claude" / "relay"


def log(tag, msg):
    try:
        d = relay_home()
        d.mkdir(parents=True, exist_ok=True)
        ts = local_now().isoformat(timespec="seconds")
        with open(d / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"{ts} [{tag}] {msg}\n")
    except OSError:
        pass  # a hook must never fail because its log file was unwritable


def read_hook_input():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}


def is_disabled():
    return os.environ.get("RELAY_DISABLED") == "1"


def in_scope(cwd):
    """RELAY_SCOPE unset -> everywhere. Set -> cwd must be under it."""
    scope = os.environ.get("RELAY_SCOPE")
    if not scope:
        return True
    try:
        norm_cwd = os.path.normcase(os.path.normpath(os.path.abspath(cwd)))
        norm_scope = os.path.normcase(os.path.normpath(os.path.abspath(scope)))
        return norm_cwd == norm_scope or norm_cwd.startswith(norm_scope + os.sep)
    except (OSError, ValueError):
        return False


def sanitize_cwd(cwd):
    """Same sanitization Claude Code uses for ~/.claude/projects dir names."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def transcripts_dir(cwd):
    return pathlib.Path.home() / ".claude" / "projects" / sanitize_cwd(cwd)


def ended_dir(cwd):
    """Markers dropped by the SessionEnd hook: <sid> exists once it ended."""
    return relay_home() / "ended" / sanitize_cwd(cwd)


def jobs_dir(cwd):
    """Instruction files the session's subagents are pointed at."""
    return relay_home() / "jobs" / sanitize_cwd(cwd)


def inbox_dir(cwd):
    """Where subagents drop their delimited output for relay_apply to read."""
    return relay_home() / "inbox" / sanitize_cwd(cwd)


def hook_state_dir():
    return relay_home() / "hook-state"


def diary_dir(cwd):
    return pathlib.Path(cwd) / "diary"


def knowledge_path(cwd, name):
    return pathlib.Path(cwd) / "knowledge" / name


def write_atomic(path, text):
    """Replace a file in one step, so a crash mid-write cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def read_text(path):
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _chunk_timestamps(path, from_end):
    """Timestamps found in one chunk at either end of the file.

    Returns None when the chunk holds none, so the caller can widen to a full
    scan rather than mistake a marker-only head/tail for a timestamp-less file.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if from_end and size > SPAN_CHUNK:
                f.seek(size - SPAN_CHUNK)
                raw = f.read()
                raw = raw.split(b"\n", 1)[1] if b"\n" in raw else b""
            else:
                raw = f.read(SPAN_CHUNK)
    except OSError:
        return None
    found = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if '"timestamp"' not in line:
            continue
        try:
            ts = parse_ts(json.loads(line).get("timestamp"))
        except json.JSONDecodeError:
            continue
        if ts:
            found.append(ts)
    return found or None


def _all_timestamps(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"timestamp"' not in line:
                    continue
                try:
                    ts = parse_ts(json.loads(line).get("timestamp"))
                except json.JSONDecodeError:
                    continue
                if ts:
                    out.append(ts)
    except OSError:
        pass  # partial results are fine: the caller treats none as 'no span'
    return out


def session_span(path):
    """(first, last) timestamps of a transcript as local datetimes, or None.

    Read from both ends rather than straight through: the last line is
    routinely a marker record with no timestamp at all (observed: 264 of 307
    ended transcripts end on a "last-prompt" line), so the scan has to look
    past it, and transcripts are far too large to walk in full.
    """
    head = _chunk_timestamps(path, from_end=False)
    tail = _chunk_timestamps(path, from_end=True)
    if head is None or tail is None:
        every = _all_timestamps(path)
        if not every:
            return None
        return every[0], every[-1]
    return head[0], tail[-1]


def floor_minute(dt):
    return dt.replace(second=0, microsecond=0)


def count_user_messages(transcript_path, after=None):
    """Real user messages, plus the first timestamp after `after`.

    Returns (count, first_ts_after). Count is None when nothing parsed, so
    an unreadable transcript is treated as unknown rather than empty. The
    second value only matters for a resumed session that needs a fresh entry,
    and riding along here keeps that case from costing a second full scan.
    """
    try:
        count = 0
        parsed = 0
        first_after = None
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed += 1
                if after is not None and first_after is None:
                    ts = parse_ts(obj.get("timestamp"))
                    if ts is not None and ts > after:
                        first_after = ts
                if obj.get("type") != "user":
                    continue
                content = obj.get("message", {}).get("content")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    continue
                count += 1
        if parsed == 0:
            return None, None
        return count, first_after
    except OSError:
        return None, None


class Entry:
    """One `## ...` block of a diary file.

    `sid8` is set only for headings relay wrote; `broken` marks a heading that
    advertises an id but does not parse, which must stop us from creating a
    duplicate entry for that session.
    """

    def __init__(self, path, order, heading, span, raw, sid8=None,
                 start=None, end=None, num=None, broken=False):
        self.path = path
        self.raw = raw            # the heading and body verbatim
        self.order = order          # position within its file
        self.heading = heading
        self.span = span            # (start_offset, end_offset) in the file text
        self.sid8 = sid8
        self.start = start
        self.end = end
        self.num = num
        self.broken = broken

    @property
    def date(self):
        return self.path.name[:-3]  # YYYY-MM-DD

    def sort_key(self):
        """File date first, then position: hand-written headings carry no
        time of their own, so their place in the file is all we can honour."""
        return (self.date, self.order)


def parse_diary_file(path):
    """Split one diary file into Entry objects (headings and their bodies)."""
    text = read_text(path)
    if not text:
        return []
    heads = [(m.start(), m.group(0)) for m in re.finditer(r"(?m)^## .*$", text)]
    entries = []
    for i, (pos, heading) in enumerate(heads):
        stop = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        m = ENTRY_RE.match(heading)
        if m:
            num, sh, sm, eh, em, sid8 = m.groups()
            day = datetime.date.fromisoformat(path.name[:-3])
            start = datetime.datetime.combine(
                day, datetime.time(int(sh), int(sm)))
            end = datetime.datetime.combine(day, datetime.time(int(eh), int(em)))
            if end < start:  # ran past midnight
                end += datetime.timedelta(days=1)
            entries.append(Entry(path, i, heading, (pos, stop), text[pos:stop],
                                 sid8=sid8, start=start, end=end, num=int(num)))
        else:
            entries.append(Entry(path, i, heading, (pos, stop), text[pos:stop],
                                 broken=bool(IDISH_RE.match(heading))))
    return entries


def diary_files(cwd):
    d = diary_dir(cwd)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if DIARY_NAME_RE.fullmatch(p.name))


def all_entries(cwd):
    out = []
    for p in diary_files(cwd):
        out.extend(parse_diary_file(p))
    return out
