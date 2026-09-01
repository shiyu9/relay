"""Which transcripts still need a diary entry.

There is no ledger. A transcript is already handled exactly when the diary
holds an entry for it whose end time is not older than the transcript's last
timestamp, or when it ended before relay was adopted here (see
session_epoch). SessionStart and the manual skill both call
pending_sessions() so they can never disagree about what is outstanding —
the skill additionally names the session it is running in, which by
definition has not ended yet.
"""
import datetime
import os
import re

from relay_common import (
    IDLE_SECS,
    MARKER_GRACE_SECS,
    MAX_JOBS,
    DEFAULT_MIN_USER_MSGS,
    RESUME_CUTOFF_DAYS,
    all_entries,
    count_user_messages,
    ended_dir,
    floor_minute,
    local_now,
    log,
    session_epoch,
    session_span,
    transcripts_dir,
)

BROKEN_TOKEN_RE = re.compile(r"\(([0-9a-fA-F]{4,12})\)\s*$")


class Pending:
    """A transcript that needs writing, and how.

    mode is "new" (no entry yet), "replace" (its entry is stale) or "resume"
    (the gap is so long the old entry is left alone and a fresh one is added).
    """

    def __init__(self, path, sid, start, end, mode, entry=None):
        self.path = path
        self.sid = sid
        self.sid8 = sid[:8]
        self.start = start
        self.end = end
        self.mode = mode
        self.entry = entry

    @property
    def date(self):
        return self.start.date().isoformat()


def _ended(cwd, sid, path, end, now):
    """Did this session finish?

    The SessionEnd marker is the fast path — it is what lets a session closed
    a minute ago be recorded by the one opened right after. Writes that land
    well after the marker mean the session came back, so the marker is
    ignored and the idle rule decides.
    """
    marker = ended_dir(cwd) / sid
    try:
        if os.path.getmtime(path) <= marker.stat().st_mtime + MARKER_GRACE_SECS:
            return True
    except OSError:
        pass  # no marker, or unreadable -> fall through to the idle rule
    return (now - end).total_seconds() >= IDLE_SECS


def _diary_index(cwd):
    """Newest entry per session id, plus the ids we must not touch."""
    newest = {}
    collided = set()
    broken_tokens = []
    per_file = {}
    for e in all_entries(cwd):
        if e.broken:
            m = BROKEN_TOKEN_RE.search(e.heading)
            if m:
                broken_tokens.append(m.group(1).lower())
                log("detect", f"unparsable id heading in {e.path.name}: {e.heading!r}")
            continue
        if e.sid8 is None:
            continue  # someone else's heading; not ours to read or write
        key = (e.path, e.sid8)
        if key in per_file:
            collided.add(e.sid8)
        per_file[key] = True
        if e.sid8 not in newest or e.end > newest[e.sid8].end:
            newest[e.sid8] = e
    return newest, collided, broken_tokens


def pending_sessions(cwd, now=None, limit=MAX_JOBS, force_sid=None):
    """Transcripts needing a diary entry, newest first, capped at `limit`.

    Cheap tests run first: reading a transcript in full is the one expensive
    step here and only the surviving candidates pay for it.

    `force_sid` waives the "has it ended" test for one session, and only
    that one: it is how the manual skill records the session running it,
    which is still being written to and can never satisfy the idle rule.
    Every other transcript keeps the rule, so a window open in parallel is
    never summarised while its user is still typing into it.
    """
    now = local_now() if now is None else now
    # Drawn before anything can return early. A project whose transcripts
    # directory does not exist yet is a project relay is present for from its
    # very first session, and that session must not be written off as backlog
    # by a line drawn later.
    epoch = session_epoch(cwd, now)
    tdir = transcripts_dir(cwd)
    if not tdir.is_dir():
        return []

    newest, collided, broken_tokens = _diary_index(cwd)
    cutoff = datetime.timedelta(days=RESUME_CUTOFF_DAYS)
    min_msgs = int(os.environ.get("RELAY_MIN_USER_MSGS", DEFAULT_MIN_USER_MSGS))

    candidates = []
    before_epoch = 0
    for path in tdir.glob("*.jsonl"):
        sid = path.stem
        sid8 = sid[:8]
        span = session_span(path)
        if span is None:
            continue
        start, end = span
        # Compared against the end, never the start: a session still running
        # when relay was adopted keeps growing past the line and is recorded
        # normally, and so is an old one that gets resumed later. Only a
        # transcript that had already stopped before adoption is written off.
        if epoch is not None and end <= epoch:
            before_epoch += 1
            continue
        if sid != force_sid and not _ended(cwd, sid, path, end, now):
            continue
        if sid8 in collided:
            log("detect", f"skip {sid8}: duplicate heading in one diary file")
            continue
        if any(sid8.startswith(t) or t.startswith(sid8) for t in broken_tokens):
            log("detect", f"skip {sid8}: an unparsable heading may already cover it")
            continue

        entry = newest.get(sid8)
        if entry is None:
            mode = "new"
        elif floor_minute(end) <= entry.end:
            continue  # nothing new since the entry was written
        elif end - entry.end >= cutoff:
            mode = "resume"
        else:
            mode = "replace"
        candidates.append(Pending(path, sid, start, end, mode, entry))

    if before_epoch:
        log("detect", f"{before_epoch} transcript(s) ended before the epoch "
                      f"({epoch:%Y-%m-%d %H:%M}); treated as recorded")

    candidates.sort(key=lambda c: c.end, reverse=True)

    out = []
    for c in candidates:
        if len(out) >= limit:
            break
        after = c.entry.end if c.mode == "resume" else None
        count, first_after = count_user_messages(c.path, after=after)
        if count is not None and count < min_msgs:
            continue
        if c.mode == "resume":
            # The fresh entry starts where the session woke up, not where it
            # originally began a fortnight ago.
            c.start = first_after or c.end
        out.append(c)
    return out


def plan_entry(cwd, sid):
    """Where and how one session's entry should land, ignoring readiness.

    pending_sessions() decides *whether* to record; this answers *where to
    put it* for a session already recorded into the inbox. Both derive the
    answer from the diary, so they cannot drift apart.
    """
    path = transcripts_dir(cwd) / f"{sid}.jsonl"
    span = session_span(path)
    if span is None:
        return None
    start, end = span
    newest, collided, _ = _diary_index(cwd)
    sid8 = sid[:8]
    if sid8 in collided:
        log("apply", f"skip {sid8}: duplicate heading in one diary file")
        return None
    entry = newest.get(sid8)
    if entry is None:
        mode = "new"
    elif end - entry.end >= datetime.timedelta(days=RESUME_CUTOFF_DAYS):
        mode = "resume"
        _, first_after = count_user_messages(path, after=entry.end)
        start = first_after or end
    else:
        mode = "replace"
    return Pending(path, sid, start, end, mode, entry)
