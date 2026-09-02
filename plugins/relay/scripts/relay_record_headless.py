"""relay_record_headless.py — the recorder, run from outside the session.

SessionEnd starts this and gets out of the way. By then the session is over,
so there is nobody left to run a subagent; the recording is done by the
headless CLI instead. What this script adds on top of that is order: it
runs the same jobs the in-session procedure runs, in the same sequence,
applying each one before the next is built — which is exactly what a model
asked to "do these three things in order" fails at often enough to matter.

Three things it is careful about.

**It writes no files itself, and neither does the model.** The CLI runs with
`--allowedTools Read`, so the recording comes back on stdout and Python
drops it in the inbox. Every mutation stays in relay_apply, where it has
always been (2026-07-22).

**It holds a per-project lock.** Two recorders overwriting current.md in
parallel produce whichever finished last — the failure the .status sidecar
was built for in 2026-08-01. Ordering them at the source means the second
recorder reads a diary that already holds the first one's entry.

**It clears its own marker.** The marker is what stops the next startup
from recording a session that is being recorded right now; if this process
dies the marker goes stale after RECORDER_STALE_SECS and the session is
offered again, which is the right answer for a recorder that never finished.

Usage: relay_record_headless.py --cwd <project> [--sid <session id>]
"""
import contextlib
import io
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay_apply
import relay_spawn
from relay_common import (
    acquire_lock,
    claude_cli,
    clear_running,
    force_utf8,
    inbox_dir,
    jobs_dir,
    log,
    merge_due,
    read_text,
    release_lock,
)
from relay_detect import pending_sessions
from relay_jobs import DEFAULT_MODEL, write_jobs

# The job bodies tell the model to write its answer to a file with the Write
# tool, because in-session that is what a subagent does. Here there is no
# Write tool at all, so the same body is prefixed with the one instruction
# that redirects it. Keeping a single body for both routes is the point:
# two copies of a 60-line prompt drift apart, and the drift shows up as a
# section that only one route ever produces.
HEADLESS_PREFIX = """\
**この作業ではファイルを一切書きません。**下の指示に「Write ツールで次のファイルに\
書き出してください」とありますが、**書き込みは行わず、同じ内容をあなたの応答本文と\
してそのまま出力してください。**区切り行（`===DIARY===` など）も含めて、指示された\
とおりの形で出力すること。前後に説明を付けないこと。

---

"""


def option(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def run_job(cli, model, job_path, out_path):
    """Run one job through the CLI and drop its answer in the inbox.

    Returns True when something landed. A non-zero exit is almost always
    authentication, so the log says so rather than making the reader guess.
    """
    body = read_text(job_path)
    if not body.strip():
        log("rec", f"job body missing: {job_path.name}")
        return False

    env = dict(os.environ)
    if env.get("RELAY_KEEP_API_KEY") != "1":
        # This key outranks the subscription and cannot be deprioritised by
        # configuration, so a stale one makes every recorder die at 401
        # (2026-07-25). Removing it is the default; keeping it is opt-in.
        env.pop("ANTHROPIC_API_KEY", None)
    # The recorder runs `claude`, and `claude` runs relay's own hooks. Without
    # this, SessionStart fires *inside* the recorder and its notice ("1
    # recorder still at work", "N sessions unrecorded") lands in the
    # recorder's own prompt — observed. relay must not read itself.
    env["RELAY_DISABLED"] = "1"

    kw = {}
    if os.name == "nt":
        # This process was started with CREATE_NO_WINDOW and so owns no
        # console. Windows hands a brand-new *visible* one to any console
        # app it launches, which is how a terminal window popped up in the
        # middle of a recording. Inherited silence has to be asked for.
        kw["creationflags"] = relay_spawn.CREATE_NO_WINDOW

    try:
        p = subprocess.run(
            [cli, "-p", HEADLESS_PREFIX + body, "--model", model,
             "--allowedTools", "Read"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, **kw)
    except OSError as e:
        log("rec", f"could not start the CLI: {e!r}")
        return False

    answer = (p.stdout or "").strip()
    if p.returncode != 0:
        log("rec", f"abort: claude exited {p.returncode} on {job_path.name}"
                   " (auth? try `claude auth status`;"
                   " API-key-only setups need RELAY_KEEP_API_KEY=1)")
        return False
    if not answer:
        log("rec", f"empty answer for {job_path.name}")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(answer + "\n", encoding="utf-8")
    return True


def apply_once(cwd):
    """Run relay_apply in this process and read back its summary line."""
    buf = io.StringIO()
    saved = sys.argv
    sys.argv = ["relay_apply.py", "--cwd", str(cwd)]
    try:
        with contextlib.redirect_stdout(buf):
            relay_apply.main()
    finally:
        sys.argv = saved
    try:
        return dict(kv.split("=", 1) for kv in buf.getvalue().split())
    except ValueError:
        return {}


def main():
    force_utf8()
    argv = sys.argv[1:]
    cwd = option(argv, "--cwd") or os.getcwd()
    sid = option(argv, "--sid")

    cli = claude_cli()
    if not cli:
        log("rec", f"no claude CLI on PATH; leaving {cwd} to the next startup")
        return

    # Held for the whole run, not per job: the point is that no other
    # recorder writes current.md while this one is deciding what it says.
    if not acquire_lock(cwd):
        log("rec", f"another recorder holds {cwd}; leaving it to the next "
                   "startup")
        return

    # The marker was written by the hook, before this process was sure to
    # exist. Clearing it is this side's job, and the only one.
    try:
        pending = pending_sessions(cwd)
        if not pending:
            log("rec", f"nothing to record in {cwd}")
            return

        model = os.environ.get("RELAY_MODEL", DEFAULT_MODEL)
        jobs, inbox = jobs_dir(cwd), inbox_dir(cwd)
        write_jobs(cwd, pending)   # bodies on disk; the notice is unused here

        done = 0
        for p in pending:
            if run_job(cli, model, jobs / f"{p.sid}.md",
                       inbox / f"{p.sid}.txt"):
                done += 1
        summary = apply_once(cwd)
        log("rec", f"{done}/{len(pending)} recorded in {cwd}: "
                   + " ".join(f"{k}={v}" for k, v in summary.items()))

        # The overwrite job reads the diary this run just wrote, so it only
        # makes sense once something was written.
        if summary.get("diary", "0") != "0":
            if run_job(cli, model, jobs / "overwrite.md",
                       inbox / "overwrite.txt"):
                summary = apply_once(cwd)

        if merge_due(cwd):
            if run_job(cli, model, jobs / "prune.md", inbox / "prune.txt"):
                apply_once(cwd)
    finally:
        if sid:
            clear_running(cwd, sid)
        release_lock(cwd)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("rec", f"error: {e!r}")
    sys.exit(0)
