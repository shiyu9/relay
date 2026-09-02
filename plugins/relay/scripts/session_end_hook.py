"""relay SessionEnd hook — start the recorder and get out of the way.

**This hook has 1.5 seconds and cannot ask for more.** Measured on 2026-09-02:
a timeout declared in a plugin's hooks.json is ignored (5 and 30 behave
exactly like no declaration), while the same declaration in settings.json
works. When a hook exceeds its timeout, its whole process tree is killed —
so a recorder started here dies with it. Inside the timeout nothing is
killed at all.

That gives this file its shape. It drops a marker, starts one process, and
writes one line: about 50ms, with the ceiling two orders of magnitude away.
Anything that could block belongs in the recorder, which has all the time it
needs once it is running.

The recorder is started with its parent re-pointed outside this hook's tree
(relay_spawn), so that even an over-run — an antivirus scanning python.exe
on launch is the realistic cause — cannot take it down.

Without the CLI there is nobody to record with, so the hook leaves only the
end marker and the next session picks the transcript up. That is the same
path a session whose SessionEnd never fired takes, so it is not a second
mechanism, just a second way into the one that already exists.
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay_spawn
from relay_common import (
    claude_cli,
    ended_dir,
    in_scope,
    is_disabled,
    log,
    mark_running,
    read_hook_input,
)

SCRIPTS = pathlib.Path(__file__).resolve().parent


def main():
    if is_disabled():
        return
    data = read_hook_input()
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id")
    if not sid or not in_scope(cwd):
        return

    # Always, whether or not a recorder follows: this is the fact that lets
    # the next session tell "over" from "merely quiet" without waiting out
    # the idle rule.
    d = ended_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    open(d / str(sid), "w").close()

    cli = claude_cli()
    if not cli:
        log("end", f"no claude CLI; {sid[:8]} is left for the next startup")
        return

    pid, how = relay_spawn.start(
        [sys.executable, str(SCRIPTS / "relay_record_headless.py"),
         "--cwd", str(cwd), "--sid", str(sid)])
    if pid:
        # Written here rather than in the recorder: the marker has to exist
        # before the next session can possibly start, and the recorder may
        # still be getting off the ground.
        mark_running(cwd, sid, pid=pid, note=how)
        log("end", f"recorder pid={pid} for {sid[:8]} ({how})")
    else:
        log("end", f"no recorder for {sid[:8]}: {how}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never break session shutdown. The idle rule and the next startup
        # both still work when this hook does nothing at all.
        log("end", f"error: {e!r}")
    sys.exit(0)
