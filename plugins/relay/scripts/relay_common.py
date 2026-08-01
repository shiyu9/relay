"""Shared helpers for relay hooks."""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

# On Windows, an IANA-style TZ (e.g. "Asia/Tokyo" set by Git Bash) is not
# understood by the C runtime and silently degrades localtime to UTC.
# Drop it so datetime.now() uses the real system timezone; children inherit
# the cleaned environment.
if os.name == "nt" and "/" in os.environ.get("TZ", ""):
    del os.environ["TZ"]


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
            pass
    return datetime.datetime.now()


def force_utf8():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def log(tag, msg):
    try:
        d = pathlib.Path.home() / ".claude" / "relay"
        d.mkdir(parents=True, exist_ok=True)
        ts = local_now().isoformat(timespec="seconds")
        with open(d / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"{ts} [{tag}] {msg}\n")
    except OSError:
        pass


def read_hook_input():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}


def is_reentry():
    return os.environ.get("RELAY_HOOK_ACTIVE") == "1"


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


def build_recorder_env(base_env):
    """Env for the detached recorder. Pure: base_env is not mutated.

    ANTHROPIC_API_KEY overrides subscription auth even when logged in, and in
    headless (-p) mode it is used without any prompt — a stale key silently
    401s the recorder. Clearing it is the only documented way to fall back to
    subscription auth, so it is the default; RELAY_KEEP_API_KEY=1 opts out.
    """
    env = dict(base_env)
    env["RELAY_HOOK_ACTIVE"] = "1"
    if env.get("RELAY_KEEP_API_KEY") != "1":
        env.pop("ANTHROPIC_API_KEY", None)
    return env


def sanitize_cwd(cwd):
    """Same sanitization Claude Code uses for ~/.claude/projects dir names."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def transcripts_dir(cwd):
    return pathlib.Path.home() / ".claude" / "projects" / sanitize_cwd(cwd)


def derive_transcript_path(cwd, session_id):
    """Fallback: ~/.claude/projects/<sanitized-cwd>/<session_id>.jsonl"""
    if not session_id:
        return None
    p = transcripts_dir(cwd) / f"{session_id}.jsonl"
    return str(p) if p.is_file() else None


def ledger_dir(cwd):
    """Per-project markers of processed transcripts, for catch-up at start."""
    return pathlib.Path.home() / ".claude" / "relay" / "ledger" / sanitize_cwd(cwd)


# Sits beside the per-session markers in ledger_dir; the leading dot cannot
# collide with a session id (those are UUIDs).
STATUS_MARKER = ".status"


def recorded_size(cwd, session_id):
    """Transcript size stored when it was last processed, or None."""
    try:
        return int((ledger_dir(cwd) / session_id).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None


def mark_recorded(cwd, session_id, size):
    """Marker content is the transcript size at processing time, so a session
    resumed (and grown) after being recorded is picked up again by catch-up."""
    try:
        d = ledger_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        (d / session_id).write_text(str(size), encoding="ascii")
    except OSError:
        pass


def status_path(cwd):
    """The project's single-page "what is still open" file."""
    return pathlib.Path(cwd) / "status.md"


def status_source_mtime(cwd):
    """mtime of the transcript whose recorder last wrote status.md, or None.

    None means "unknown" and deliberately fails open: a missing or corrupt
    marker must not freeze status.md forever.
    """
    try:
        return float((ledger_dir(cwd) / STATUS_MARKER).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None


def mark_status_written(cwd, mtime):
    """status.md is overwritten, not appended, so unlike the diary it needs an
    ordering guard: catch-up can spawn recorders for several sessions at once
    and an older one finishing last would otherwise clobber a newer status."""
    try:
        d = ledger_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        (d / STATUS_MARKER).write_text(repr(float(mtime)), encoding="ascii")
    except OSError:
        pass


def write_atomic(path, text):
    """Replace a file in one step, so a crash mid-write cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def count_user_messages(transcript_path):
    """Rough count of real user messages. On any parse trouble, return None."""
    try:
        count = 0
        parsed = 0
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed += 1
                if obj.get("type") != "user":
                    continue
                content = obj.get("message", {}).get("content")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    continue
                count += 1
        if parsed == 0:
            return None  # nothing parseable -> treat count as unknown
        return count
    except OSError:
        return None


def spawn_recorder(cwd, transcript):
    """Fire-and-forget: start relay_recorder.py detached from this console."""
    recorder = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "relay_recorder.py")
    kwargs = {"cwd": str(cwd), "env": build_recorder_env(os.environ),
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, recorder, str(transcript), str(cwd)], **kwargs)
