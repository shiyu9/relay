"""relay SessionEnd hook.

Spawns the detached recorder (relay_recorder.py) as fast as possible and
does nothing else. In terminal setups where the window closes together
with claude, this hook can be killed mid-flight at any moment (observed:
sometimes before it can even log one line), so anything heavier than
"resolve transcript, log, Popen" lives in the recorder, which survives
the console teardown once spawned. Never blocks the ending session:
every guard exits 0.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import (
    derive_transcript_path,
    force_utf8,
    in_scope,
    is_disabled,
    is_reentry,
    log,
    read_hook_input,
    spawn_recorder,
)


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

    # Log before spawning: if the console dies during Popen, the trail
    # still shows how far we got.
    log("end", f"spawning recorder for {cwd}")
    spawn_recorder(cwd, transcript)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break session shutdown
        log("end", f"error: {e!r}")
    sys.exit(0)
