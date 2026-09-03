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
import shutil
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
DEFAULT_MIN_USER_MSGS = 3
# A recorder marker older than this belongs to a recorder that died. Measured
# runs take 2-4 minutes, so this is generous by design: treating a slow
# recorder as dead means recording the same session twice.
RECORDER_STALE_SECS = 30 * 60
# How long a recorder waits for the project lock before giving up. Waiting
# costs a live process, so a project whose sessions close back to back must
# not stack them up; the one that gives up leaves its marker and the next
# startup picks the session up instead.
LOCK_WAIT_SECS = 10 * 60
LOCK_POLL_SECS = 5
# How recently a transcript must have been written to be a candidate for
# "the session running this". The manual entry point names itself with a
# token, and a tool call reaches the transcript before it runs, so the file
# holding the token was touched seconds ago; anything older is somebody else.
SELF_WINDOW_SECS = 5 * 60
# knowledge/ files are meant to stay small; past this the pruning job runs.
PRUNE_LINES = 80
# Read refuses a file over 256 KB, and refuses any single call over 25,000
# tokens. Measured on the condensed form of a 2.97 MB session: its first
# 1000 lines were 60,095 bytes and 25,449 tokens — 2.36 bytes per token, so
# the 25,000-token ceiling is about 59 KB of this text. Parts are cut at
# 40 KB to keep half again as much headroom, because density is not even:
# across that file's 100-line windows it ran from 3.8 KB to 11.0 KB, a
# threefold spread that a fixed line count cannot ride out.
READ_PART_BYTES = 40 * 1000
BLOCK_SEP = "\n\n"
BYTES_PER_TOKEN = 2.36
# Past this the reading no longer fits in one context beside the job and the
# answer, and the recorder runs out before the end. The largest reading seen
# so far is 282 KB (about 120,000 tokens) and it fit, so this is a line for
# noticing the first one that does not — not a design for handling it.
CONTEXT_BUDGET_TOKENS = 150_000

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
# The whole body of an entry for a session the recorder found nothing in.
# Detection reads the diary and nothing else, so a session left unwritten is
# offered again on every single startup — the mark is what lets "we looked,
# there was nothing" be said at all. Kept out of the injection: an empty
# session must not take one of the five slots a real handover needs.
THIN_MARK = "（記録するものがなかったセッション）"

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
    """Where relay keeps everything that is not in the project.

    `RELAY_HOME` exists for the tests. They patch `Path.home` to a temporary
    directory, which holds for anything running in-process — but the end
    hook spawns a real detached recorder, and that child starts a fresh
    interpreter where the patch never existed. It wrote into the developer's
    own `~/.claude/relay/` instead: 13 stray epoch files and 25 stray
    `running/` directories keyed by long-deleted temp paths, one more with
    every full run of the suite. An environment variable is the only kind of
    patch a child process inherits.
    """
    override = os.environ.get("RELAY_HOME")
    if override:
        return pathlib.Path(override)
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

    # A line is only drawn for a directory that exists. A path that is not
    # one was mangled on the way in — `C:\claude-projects\x` handed to a
    # shell unquoted arrives as `C:claude-projectsx` (five such files turned
    # up on 2026-09-01) — and a line filed under that name is one no project
    # will ever read: debris that looks like a real cutoff. A directory that
    # does not exist holds no transcripts, so refusing changes no verdict; it
    # only swaps a silent orphan for a log line.
    if not os.path.isdir(cwd):
        log("epoch", f"{cwd!r} is not a directory; no line drawn")
        return None

    stamp = local_now() if now is None else now
    try:
        write_atomic(path, stamp.isoformat(timespec="seconds") + "\n")
    except OSError as e:
        log("epoch", f"could not write {path}: {e!r}; no cutoff applied")
        return None
    log("epoch", f"set to {stamp:%Y-%m-%d %H:%M} for {cwd}: "
                 "transcripts that ended earlier count as recorded")
    return stamp


def find_self(cwd, token, now=None):
    """The transcript naming `token`, when exactly one recent file does.

    A tool call reaches the transcript before it runs, so a script handed a
    token on its own command line can find the file it is being written
    into. Verified by running exactly that: the script matched its own
    transcript and no other.

    Ambiguity is a failure, never a guess. Choosing the newest of two would
    mean treating somebody else's live session as this one and summarising
    it mid-flight — the single thing the idle rule exists to prevent. A
    token quoted elsewhere (in a message, in a diary entry) really can land
    in two files, and refusing costs one retry with a fresh token.
    """
    now = time.time() if now is None else now
    tdir = transcripts_dir(cwd)
    if not token or not tdir.is_dir():
        return None
    hits = []
    for p in tdir.glob("*.jsonl"):
        try:
            if now - p.stat().st_mtime > SELF_WINDOW_SECS:
                continue
            if token in p.read_text(encoding="utf-8", errors="replace"):
                hits.append(p.stem)
        except OSError:
            pass  # unreadable -> not the file we are looking for
    return hits[0] if len(hits) == 1 else None


def inbox_dir(cwd):
    """Where subagents drop their delimited output for relay_apply to read."""
    return relay_home() / "inbox" / sanitize_cwd(cwd)


def diary_dir(cwd):
    return pathlib.Path(cwd) / "diary"


def knowledge_path(cwd, name):
    return pathlib.Path(cwd) / "knowledge" / name


def tasks_path(cwd):
    """The project's own task list. relay reads it and strikes items off;
    everything that gets added to it is written by the session itself."""
    return pathlib.Path(cwd) / "tasks.md"


def condensed_dir(cwd):
    """Where the readable form of a transcript is kept for the recorder."""
    return relay_home() / "condensed" / sanitize_cwd(cwd)


def _clip(value, limit):
    text = value if isinstance(value, str) else json.dumps(value,
                                                           ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[:limit] + f" …[{len(text) - limit} 文字省略]"


def condense_transcript(src, dst):
    """Rewrite one transcript as the conversation, and nothing else.

    Measured on a 5321 KB transcript: 63% of it is JSON the recorder never
    reads — uuid, parentUuid, timestamps — and the actual spoken text is 3%.
    Asked to summarise that, and told to favour the end when it is large,
    the recorder read the end and nothing else: two sessions in a row lost
    everything before their midpoint, and one of them wrote a `mistakes`
    entry that was the opposite of what happened.

    So the reading is made small enough that "read all of it" is a request
    that can be honoured. The same file drops to 737 KB (14%) — the text
    and the thinking verbatim, tool calls and their results clipped to a
    few hundred characters, laid out as `### user` / `### assistant`.

    The clipping is why the job also carries the original path: what is cut
    here can still be fetched there, on the rare occasion it matters.

    Small enough to summarise turned out not to be small enough to read.
    Read refuses a file over 256 KB, and refuses any single call over 25,000
    tokens, so on a 2.97 MB session the recorder met "read all of it" with a
    file it could not open: it asked for the whole thing, then 1000 lines,
    then settled for 100, and finished the job on `head`, `tail` and `grep`.
    Lines 100 to 3800 were never read, and sixteen hours came out as the
    last one. So the reading is handed over already cut into parts that Read
    cannot refuse, and the job names them one by one — the recorder opens
    files rather than doing arithmetic against a ceiling it keeps hitting.

    Returns the parts in order. One part means one file, named as before.
    """
    lines = []
    for raw in read_text(src).splitlines():
        try:
            item = json.loads(raw)
        except ValueError:
            continue  # a half-written line is not worth failing over
        role = item.get("type")
        if role not in ("user", "assistant"):
            continue
        content = item.get("message", {}).get("content")
        blocks = (content if isinstance(content, list)
                  else [{"type": "text", "text": content or ""}])
        parts = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if kind == "text":
                parts.append(b.get("text", ""))
            elif kind == "thinking":
                parts.append("[thinking] " + b.get("thinking", ""))
            elif kind == "tool_use":
                parts.append(f"[{b.get('name')}] " + _clip(b.get("input", {}), 300))
            elif kind == "tool_result":
                parts.append("[result] " + _clip(b.get("content", ""), 200))
        body = "\n".join(p for p in parts if p).strip()
        if body:
            lines.append(f"### {role}\n{body}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    return _write_reading(dst, lines)


def _split_blocks(blocks, budget=READ_PART_BYTES):
    """Group `blocks` into runs that each stay under `budget` bytes.

    Cuts fall between turns, so no part opens midway through somebody's
    sentence. A single turn bigger than the budget cannot be kept whole and
    is cut on line boundaries instead — rare, but one pasted log makes one,
    and a part that overshot the ceiling would be refused exactly like the
    file this whole mechanism exists to stop producing.
    """
    out, run, size = [], [], 0
    for b in blocks:
        n = len(b.encode("utf-8")) + 2  # the blank line that follows it
        if n > budget:
            if run:
                out.append(run)
                run, size = [], 0
            out.extend([piece] for piece in _split_one(b, budget))
            continue
        if run and size + n > budget:
            out.append(run)
            run, size = [], 0
        run.append(b)
        size += n
    if run:
        out.append(run)
    return out


def _split_one(block, budget):
    """One oversized turn, cut on line boundaries — then, if it has to, inside
    a line. A pasted log arrives as one line of half a megabyte, and a piece
    that stayed over the ceiling would be refused like the file this exists
    to stop producing. Cuts land between characters, never inside one."""
    pieces, cur, size = [], [], 0
    for line in block.splitlines(True):
        for chunk in _cut_bytes(line, budget):
            n = len(chunk.encode("utf-8"))
            if cur and size + n > budget:
                pieces.append("".join(cur))
                cur, size = [], 0
            cur.append(chunk)
            size += n
    if cur:
        pieces.append("".join(cur))
    return pieces


def _cut_bytes(text, budget):
    """`text` in runs of at most `budget` bytes, split between characters."""
    if len(text.encode("utf-8")) <= budget:
        return [text]
    out, cur, size = [], [], 0
    for ch in text:
        n = len(ch.encode("utf-8"))
        if cur and size + n > budget:
            out.append("".join(cur))
            cur, size = [], 0
        cur.append(ch)
        size += n
    if cur:
        out.append("".join(cur))
    return out


def _header_reserve(stem):
    """Bytes to keep back for a part's header, whatever its number turns out
    to be. 64 covers the fixed decoration (23), a five-digit count on both
    sides of the slash, and the blank line after it, with room to spare."""
    return len(stem.encode("utf-8")) + 64


def _write_reading(dst, blocks):
    """Write the reading as `dst`, or as numbered parts beside it.

    Whatever the last run left is removed first. A session condensed again
    after it grew would otherwise keep a stale whole file next to fresh
    parts, and the count the job promises would not be the count on disk.
    """
    stem = dst.stem
    for old in [dst, *sorted(dst.parent.glob(stem + ".part*" + dst.suffix))]:
        try:
            old.unlink()
        except OSError:
            pass

    # The header is part of the file Read has to open, so it comes out of
    # the budget rather than sitting on top of it. Its exact length is not
    # knowable here — "1/9" and "1/10" differ by a byte, and the count is
    # what the split is about to decide — so the reserve is the session id
    # plus room for the decoration, the digits and the blank line. Measured
    # without it: a 1.29 MB session produced a part of 40,031 bytes.
    runs = _split_blocks(blocks, READ_PART_BYTES - _header_reserve(stem))
    if len(runs) <= 1:
        write_atomic(dst, BLOCK_SEP.join(blocks) + "\n")
        return [dst]

    paths = []
    for i, run in enumerate(runs, 1):
        p = dst.parent / "{}.part{:02d}{}".format(stem, i, dst.suffix)
        head = "<!-- {} パート {}/{} -->".format(stem, i, len(runs))
        write_atomic(p, head + BLOCK_SEP + BLOCK_SEP.join(run) + "\n")
        paths.append(p)
    return paths


def condensed_parts(cwd, sid):
    """The reading the recorder was handed for `sid`, in order.

    Read back off the disk rather than remembered: the check that uses it
    runs in a different process from the one that wrote the job.
    """
    d = condensed_dir(cwd)
    parts = sorted(d.glob(sid + ".part*.md"))
    if parts:
        return parts
    whole = d / (sid + ".md")
    return [whole] if whole.exists() else []


def reading_tokens(paths):
    """Roughly how many tokens the whole reading is, for the budget check."""
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return int(total / BYTES_PER_TOKEN)


def running_dir(cwd):
    """Markers for recorders currently at work in this project."""
    return relay_home() / "running" / sanitize_cwd(cwd)


def notified_dir(cwd):
    """Sessions already told that a recording they were waiting on finished."""
    return relay_home() / "notified" / sanitize_cwd(cwd)


def claude_cli():
    """The headless CLI, or None when it is not installed.

    This is what decides which half of the design a machine gets: with a CLI
    the recorder runs at SessionEnd and the next startup finds current.md
    already up to date; without one, SessionEnd leaves a marker and the next
    session records it. Both paths exist anyway — the startup path is also
    what catches sessions whose SessionEnd never fired — so the branch costs
    a condition, not a second mechanism.
    """
    return shutil.which("claude")


def mark_running(cwd, sid, pid=None, note=""):
    """Record that a recorder is working on `sid`.

    Detection reads the diary, and the diary stays unchanged until the
    recorder finishes — for the two to four minutes in between, the session
    looks exactly like one nobody has touched. Without this marker the next
    startup would record it a second time.
    """
    d = running_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / sid
    body = f"{local_now().isoformat(timespec='seconds')} pid={pid} {note}\n"
    try:
        p.write_text(body, encoding="utf-8")
    except OSError as e:
        log("run", f"could not mark {sid[:8]} as running: {e!r}")
    return p


def clear_running(cwd, sid):
    try:
        (running_dir(cwd) / sid).unlink(missing_ok=True)
    except OSError as e:
        log("run", f"could not clear the marker for {sid[:8]}: {e!r}")


def running_sids(cwd, now=None):
    """Sessions with a recorder that still looks alive.

    Stale markers are ignored rather than deleted. Removing one is the
    recorder's own job, and a hook that tidies up after a recorder it cannot
    see risks clearing the marker of one that is merely slow.
    """
    now = time.time() if now is None else now
    out = set()
    try:
        for p in running_dir(cwd).iterdir():
            if p.name.startswith("."):
                continue  # the lock lives here too
            try:
                if now - p.stat().st_mtime < RECORDER_STALE_SECS:
                    out.add(p.name)
            except OSError:
                pass
    except OSError:
        pass  # no directory yet -> nothing is running
    return out


def acquire_lock(cwd, wait=LOCK_WAIT_SECS, now=None):
    """Serialise recorders within one project. True if we hold the lock.

    Two recorders that overwrite current.md in parallel produce whichever
    answer finished last, which is the failure the .status sidecar was built
    for in 2026-08-01. Ordering them fixes it at the source: the second
    recorder reads a diary that already holds the first one's entry.

    A lock older than RECORDER_STALE_SECS is taken, because its owner is
    gone. The owner's pid and time are written inside for the log to name,
    but the decision rests on the file's age alone — pids get reused, and a
    reused pid reads as "still alive" forever.
    """
    d = running_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    lock = d / ".lock"
    started = time.time() if now is None else now
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{local_now().isoformat(timespec='seconds')} "
                         f"pid={os.getpid()}\n".encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            pass
        except OSError as e:
            log("run", f"lock unavailable in {cwd}: {e!r}")
            return False
        try:
            if time.time() - lock.stat().st_mtime > RECORDER_STALE_SECS:
                log("run", f"taking a lock abandoned by {read_text(lock).strip()}")
                lock.unlink(missing_ok=True)
                continue
        except OSError:
            continue  # it vanished between the two calls; try to take it
        if time.time() - started >= wait:
            log("run", f"gave up waiting for the lock in {cwd}")
            return False
        time.sleep(LOCK_POLL_SECS)


def release_lock(cwd):
    try:
        (running_dir(cwd) / ".lock").unlink(missing_ok=True)
    except OSError as e:
        log("run", f"could not release the lock in {cwd}: {e!r}")


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


def is_thin_entry(entry):
    """Was this entry written only to say the session held nothing?

    It still belongs in the diary — detection asks the diary whether a
    session was looked at, and this is the answer — but it carries nothing
    a reader needs, so it is kept out of the injection and out of the
    material the overwrite job reads.
    """
    parts = entry.raw.split("\n", 1)
    return len(parts) > 1 and parts[1].strip() == THIN_MARK


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
                                            TASKS_DONE_HEADING)
               and not is_thin_entry(e)]
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
