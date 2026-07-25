"""Shared helpers for relay hooks."""
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


def derive_transcript_path(cwd, session_id):
    """Fallback: ~/.claude/projects/<sanitized-cwd>/<session_id>.jsonl"""
    if not session_id:
        return None
    sanitized = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    p = pathlib.Path.home() / ".claude" / "projects" / sanitized / f"{session_id}.jsonl"
    return str(p) if p.is_file() else None
