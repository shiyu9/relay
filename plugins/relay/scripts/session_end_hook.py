"""relay SessionEnd hook.

Guards, then spawns the detached recorder (relay_recorder.py), which runs
a read-only headless Claude and appends diary/knowledge deterministically.
Never blocks the ending session: every guard exits 0.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import (
    build_recorder_env,
    derive_transcript_path,
    force_utf8,
    in_scope,
    is_disabled,
    is_reentry,
    log,
    read_hook_input,
)

DEFAULT_MIN_USER_MSGS = 3


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


def main():
    force_utf8()
    if is_reentry():
        log("end", "skip: reentry guard")
        return
    if is_disabled():
        log("end", "skip: RELAY_DISABLED")
        return

    data = read_hook_input()
    cwd = data.get("cwd") or os.getcwd()
    reason = data.get("reason", "")
    if reason == "logout":
        log("end", "skip: logout")
        return
    if not in_scope(cwd):
        log("end", f"skip: out of scope ({cwd})")
        return

    transcript = data.get("transcript_path")
    if not transcript or not os.path.isfile(transcript):
        transcript = derive_transcript_path(cwd, data.get("session_id"))
    if not transcript:
        log("end", "skip: no transcript")
        return

    min_msgs = int(os.environ.get("RELAY_MIN_USER_MSGS", DEFAULT_MIN_USER_MSGS))
    n = count_user_messages(transcript)
    if n is not None and n < min_msgs:
        log("end", f"skip: thin session ({n} user msgs < {min_msgs})")
        return

    if not shutil.which("claude"):
        log("end", "skip: claude CLI not found")
        return

    recorder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay_recorder.py")
    env = build_recorder_env(os.environ)

    kwargs = {"cwd": cwd, "env": env, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen([sys.executable, recorder, transcript, cwd], **kwargs)
    log("end", f"spawned recorder for {cwd} ({n} user msgs)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break session shutdown
        log("end", f"error: {e!r}")
    sys.exit(0)
