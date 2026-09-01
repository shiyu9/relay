"""Shared helpers for relay hooks.

The recording pipeline has no ledger: a transcript is "already recorded"
exactly when the diary holds an entry for it whose end time is not older
than the transcript's last timestamp. Everything below exists to make that
one comparison cheap and exact.
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import time

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
# How long a job file counts as "possibly still running". The recording
# subagents finish invisibly to Python, so the Stop hook cannot tell an
# in-flight job from one that was never started; the mtime of the job file
# is the only evidence either way. Measured runs take 2-4 minutes, and a
# 165 MB transcript takes longer, so this is deliberately generous: a nudge
# that arrives too early costs a duplicated job, and duplicated jobs are
# harmless (upsert_entry replaces by id) but not free.
JOB_GRACE_SECS = 15 * 60
DEFAULT_MIN_USER_MSGS = 3
# knowledge/ files are meant to stay small; past this the pruning job runs.
PRUNE_LINES = 80

# "## S1 19:11-22:30 (124c52dd)" — the only headings relay owns. A session
# that ran past midnight carries the day count too ("08:14-08:13+3d"): without
# it a multi-day span reads as ending before it started, and the end time it
# parses back to is wrong by however many days were dropped, which keeps
# detection seeing the transcript as newer than its own entry forever.
ENTRY_RE = re.compile(
    r"^## S(\d+) (\d{2}):(\d{2})-(\d{2}):(\d{2})(?:\+(\d+)d)? "
    r"\(([0-9a-f]{8})\)\s*$")
# A heading that looks like it carries an id but does not parse. Kept apart
# from "has no id at all": those are other people's headings and we leave
# them alone, while a broken one means our own writing went wrong.
IDISH_RE = re.compile(r"^## .*\([0-9a-fA-F]{4,12}\)\s*$")
DIARY_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.md")
# Written by relay_apply, never by a model; excluded from injection.
DROPPED_HEADING = "## relay: decided.md から落とした項目"
MOVE_HEADING = "## relay: knowledge/ の移設候補"
TASKS_DONE_HEADING = "## relay: 完了として tasks.md から消した項目"

# "- [ ] ..." at any indent. Only the unchecked box: "[x]" is the project
# saying it already dealt with the item, and striking those off is not
# relay's business. Both the injection and the strike-off read items through
# this, so they can never disagree about where one item ends.
TASK_OPEN_RE = re.compile(r"^(\s*)- \[ \]")
MD_HEADING_RE = re.compile(r"^#{1,6} ")


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


def epoch_path(cwd):
    """Where this project's cutoff is remembered, as editable plain text."""
    return relay_home() / "epoch" / sanitize_cwd(cwd)


# RELAY_EPOCH values meaning "no cutoff at all".
EPOCH_OFF = ("", "0", "no", "none", "off")


def parse_epoch(text):
    """An ISO date or date-time -> naive local datetime, or None."""
    try:
        stamp = datetime.datetime.fromisoformat(text.strip())
    except (AttributeError, ValueError):
        return None
    if stamp.tzinfo is not None:
        stamp = (stamp.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                 + local_offset())
    return stamp


def session_epoch(cwd, now=None):
    """The instant before which transcripts already count as recorded.

    relay is adopted into projects that already hold weeks of transcripts,
    and without a cutoff every one of them is offered for recording. The
    measured cost when v0.5.0 changed the heading format and stopped
    recognising what v0.3.0 had written: 86 sessions, ~394 MB, three of them
    re-summarised on every single startup, with current.md dragged backwards
    each time. The cutoff is what makes adopting relay free.

    Written once, on first sight of the project, and never moved afterwards.
    A cutoff that advanced on every update would be a retention window, and
    the window is the one shape this design already rejected: with a line
    that moves, a transcript inside it today falls outside it tomorrow and
    is dropped with nothing recording that it existed. A line drawn once has
    no outside to fall into — once a session is newer than the epoch it
    stays newer forever.

    Returns None for "no cutoff", which is also what a hand-broken file
    gives. The file is plain text precisely so a human can move the line by
    editing it, and a typo there must not silently erase a real backlog:
    failing open floods the next startup with candidates, which is loud and
    recoverable, while failing closed loses the summaries leaving no trace
    that anything was skipped.
    """
    override = os.environ.get("RELAY_EPOCH")
    if override is not None:
        if override.strip().lower() in EPOCH_OFF:
            return None
        stamp = parse_epoch(override)
        if stamp is None:
            log("epoch", f"RELAY_EPOCH={override!r} is not a date; no cutoff")
        return stamp

    path = epoch_path(cwd)
    text = read_text(path).strip()
    if text:
        stamp = parse_epoch(text)
        if stamp is None:
            log("epoch", f"{path} holds {text!r}, which is not a date; "
                         "no cutoff applied")
        return stamp

    stamp = local_now() if now is None else now
    try:
        write_atomic(path, stamp.isoformat(timespec="seconds") + "\n")
    except OSError as e:
        log("epoch", f"could not write {path}: {e!r}; no cutoff applied")
        return None
    log("epoch", f"set to {stamp:%Y-%m-%d %H:%M} for {cwd}: "
                 "transcripts that ended earlier count as recorded")
    return stamp


def jobs_in_flight(cwd, sids, now=None):
    """Which of `sids` have a job file young enough to still be running.

    The recording subagents are invisible to Python: they finish without
    telling anyone, and the file they write lands in the inbox only once
    they are done, so "no inbox file" covers not-started, running and
    already-applied alike. The age of the job file is the one piece of
    evidence there is, and it is what keeps the Stop hook from shouting
    "3 unrecorded" at a session that is recording those three right now.
    """
    now = time.time() if now is None else now
    jobs = jobs_dir(cwd)
    out = set()
    for sid in sids:
        try:
            if now - os.path.getmtime(jobs / f"{sid}.md") < JOB_GRACE_SECS:
                out.add(sid)
        except OSError:
            pass  # no job file -> nothing was ever handed out for it
    return out


def inbox_dir(cwd):
    """Where subagents drop their delimited output for relay_apply to read."""
    return relay_home() / "inbox" / sanitize_cwd(cwd)


def hook_state_dir():
    return relay_home() / "hook-state"


def diary_dir(cwd):
    return pathlib.Path(cwd) / "diary"


def knowledge_path(cwd, name):
    return pathlib.Path(cwd) / "knowledge" / name


def tasks_path(cwd):
    """The project's own task list. relay reads it and strikes items off;
    everything that gets added to it is written by the session itself."""
    return pathlib.Path(cwd) / "tasks.md"


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


def prune_fingerprint(cwd):
    """Hash of the two knowledge files, as they are right now.

    The prune job embeds their text and answers with a full replacement, so
    it is only safe to apply while the files still say what the job was built
    from. Anything appended in between would be silently erased.
    """
    h = hashlib.sha256()
    for name in ("pitfalls.md", "workflow.md"):
        h.update(read_text(knowledge_path(cwd, name)).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def write_prune_job(cwd):
    """(Re)write the prune job from the knowledge files as they stand.

    Written once at session start and again at the end of every apply run:
    the recording jobs append to pitfalls.md and workflow.md *after* the
    session start hook has run, so a job written only at start time is stale
    by the time the model is told to run it.
    """
    import relay_prompts  # here, not at import time: relay_prompts imports nothing

    jobs = jobs_dir(cwd)
    jobs.mkdir(parents=True, exist_ok=True)
    inbox = inbox_dir(cwd)
    inbox.mkdir(parents=True, exist_ok=True)
    pitfalls = read_text(knowledge_path(cwd, "pitfalls.md")).strip() or "(empty)"
    workflow = read_text(knowledge_path(cwd, "workflow.md")).strip() or "(empty)"
    path = jobs / "prune.md"
    path.write_text(relay_prompts.PRUNE.format(
        out=(inbox / "prune.txt").as_posix(),
        pitfalls=pitfalls, workflow=workflow,
        pitfalls_lines=len(pitfalls.splitlines()),
        workflow_lines=len(workflow.splitlines()),
        limit=PRUNE_LINES), encoding="utf-8")
    (jobs / "prune.stamp").write_text(prune_fingerprint(cwd), encoding="utf-8")
    return path


def line_count(path):
    return len([l for l in read_text(path).splitlines() if l.strip()])


def merge_due(cwd):
    """Should the merge job run? Size alone is not enough to decide.

    The job may no longer delete items to hit a line count, so a project that
    is simply large stays over the mark forever: the threshold on its own
    would fire every single startup and re-report the same thing. Pairing it
    with the fingerprint of the last completed merge turns it back into
    "something changed here since we last looked".
    """
    if not any(line_count(knowledge_path(cwd, n)) > PRUNE_LINES
               for n in ("pitfalls.md", "workflow.md")):
        return False
    return read_text(jobs_dir(cwd) / "prune.done").strip() != prune_fingerprint(cwd)


def mark_merge_done(cwd):
    """Remember what the files looked like when the merge last finished."""
    jobs = jobs_dir(cwd)
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "prune.done").write_text(prune_fingerprint(cwd), encoding="utf-8")


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
            num, sh, sm, eh, em, days, sid8 = m.groups()
            day = datetime.date.fromisoformat(path.name[:-3])
            start = datetime.datetime.combine(
                day, datetime.time(int(sh), int(sm)))
            end = datetime.datetime.combine(day, datetime.time(int(eh), int(em)))
            if days is not None:
                end += datetime.timedelta(days=int(days))
            elif end < start:  # ran past midnight; written before "+Nd" existed
                end += datetime.timedelta(days=1)
            entries.append(Entry(path, i, heading, (pos, stop), text[pos:stop],
                                 sid8=sid8, start=start, end=end, num=int(num)))
        else:
            entries.append(Entry(path, i, heading, (pos, stop), text[pos:stop],
                                 broken=bool(IDISH_RE.match(heading))))
    return entries


class Task:
    """One unchecked item of tasks.md, with the lines that belong to it."""

    def __init__(self, heading, lines, span):
        self.heading = heading  # nearest heading above it, or None
        self.lines = lines      # the "- [ ]" line and its continuations
        self.span = span        # (start, stop) as line indices

    @property
    def key(self):
        """What the model has to reproduce to name this item."""
        return self.lines[0].strip()


def parse_tasks(path):
    """The unchecked items of a tasks.md, each with its continuation lines.

    An item runs until a line at the same indent or shallower begins, or a
    blank line or heading arrives. The continuations are where the file
    paths and the reasoning live ("手がかり: src/view/motion.ts"), so an
    item cut down to its first line loses what makes it actionable — and
    the same parse decides what gets struck off, which is why a nested
    bullet counts as part of its parent rather than as a new item.
    """
    text = read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    out = []
    heading = None
    i = 0
    while i < len(lines):
        if MD_HEADING_RE.match(lines[i]):
            heading = lines[i]
            i += 1
            continue
        m = TASK_OPEN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        start = i
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip() or MD_HEADING_RE.match(nxt):
                break
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
            i += 1
        out.append(Task(heading, lines[start:i], (start, i)))
    return out


def tasks_outline(cwd):
    """The unchecked items of tasks.md as they are injected and offered.

    Only the items and the heading each sits under: the rest of the file is
    prose the project wrote for itself — status paragraphs, format notes,
    tables of where the originals live — and none of it is what the next
    session needs in front of it. Measured over the eight projects that
    have the file, this keeps 4-86 lines out of 18-159.
    """
    out = []
    seen = None
    for t in parse_tasks(tasks_path(cwd)):
        if t.heading and t.heading != seen:
            out.append(t.heading)
            seen = t.heading
        out.extend(t.lines)
    return "\n".join(out)


def remove_tasks(cwd, keys):
    """Strike the named items off tasks.md; return the lines removed.

    `keys` are first lines as the model reproduced them, and an item goes
    only when one of them matches its own first line exactly. A paraphrase,
    a truncation, or an item edited since the job was built therefore
    removes nothing at all: the file is not touched and the caller sees an
    empty list. That is the whole safety story — the diary copy exists to
    recover from a wrong-but-matching answer, not to excuse a loose match.
    """
    path = tasks_path(cwd)
    tasks = parse_tasks(path)
    wanted = {k.strip() for k in keys}
    hit = [t for t in tasks if t.key in wanted]
    if not hit:
        return []
    drop = set()
    removed = []
    for t in hit:
        drop.update(range(t.span[0], t.span[1]))
        removed.extend(t.lines)
    lines = read_text(path).splitlines()
    kept = [l for i, l in enumerate(lines) if i not in drop]
    write_atomic(path, "\n".join(kept).rstrip() + "\n")
    return removed


# Enough diary to see what just happened, without the volume swinging with
# how many sessions a given day happened to hold.
INJECT_ENTRIES = 5


def recent_entries(cwd, limit=INJECT_ENTRIES):
    """The newest entries across all diary files, oldest of them first.

    Counted as entries rather than days: a day holding four sessions would
    otherwise inject four times as much as a day holding one. Both the
    injection and the overwrite job read from here, so "the newest diary"
    means the same thing to the reader and to the job that writes current.md.
    """
    entries = [e for e in all_entries(cwd)
               if e.heading.strip() not in (DROPPED_HEADING, MOVE_HEADING,
                                            TASKS_DONE_HEADING)]
    entries.sort(key=lambda e: e.sort_key())
    return entries[-limit:]


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
