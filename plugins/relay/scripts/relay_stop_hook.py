"""relay Stop hook — re-asks for recording that the session skipped.

Recording is now a procedure printed at SessionStart for the session itself
to carry out, which means it can simply not happen: the model may go
straight to the user's request and never come back to it. Nothing else
notices. This hook is the backstop, and it asks the same question detection
does, so the two can never disagree about what is outstanding.

It stays quiet unless there is something to record: Stop fires on every turn,
and a nudge that repeats itself is worse than no nudge at all.

Output: {"decision": "block", "reason": ...} when nudging, otherwise nothing.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay_prompts
from relay_common import (
    force_utf8,
    hook_state_dir,
    in_scope,
    is_disabled,
    jobs_dir,
    log,
    sanitize_cwd,
)
from relay_detect import pending_sessions

STATE_TTL_SECONDS = 7 * 24 * 3600
# Per project, not per session: remote sessions get a fresh id each turn, so
# a session-scoped limit would not hold.
COOLDOWN_SECONDS = 30 * 60


def should_nudge(n_pending, last_nudge_at, now):
    """Pure decision: is there work outstanding and are we allowed to say so?"""
    if n_pending <= 0:
        return False
    if last_nudge_at is not None and (now - last_nudge_at) < COOLDOWN_SECONDS:
        return False
    return True


def _cleanup(state_dir, now):
    try:
        for p in state_dir.iterdir():
            try:
                if now - p.stat().st_mtime > STATE_TTL_SECONDS:
                    p.unlink()
            except OSError:
                pass  # one stale file we could not remove costs nothing
    except OSError:
        pass  # housekeeping is best-effort; never let it reach the session


def _read_last_nudge(state_file):
    try:
        return json.loads(state_file.read_text(encoding="utf-8")).get("last_nudge_at")
    except (OSError, ValueError):
        return None


def main():
    force_utf8()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return  # unreadable input always falls to "do nothing"

    # Never fire on the turn our own block restarted (official loop guard).
    if payload.get("stop_hook_active"):
        return
    cwd = payload.get("cwd")
    if not cwd or is_disabled() or not in_scope(cwd):
        return

    pending = pending_sessions(cwd)
    state_dir = hook_state_dir()
    state_file = state_dir / f"{sanitize_cwd(cwd)}.json"
    now = time.time()
    if not should_nudge(len(pending), _read_last_nudge(state_file), now):
        return

    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"last_nudge_at": now}), encoding="utf-8")
        _cleanup(state_dir, now)
    except OSError:
        return  # cannot remember having nudged -> do not nudge

    reason = relay_prompts.STOP_NUDGE.format(
        n=len(pending), jobs_dir=jobs_dir(cwd).as_posix())
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    log("stop", f"nudged: {len(pending)} unrecorded in {cwd}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("stop", f"error: {e!r}")
    sys.exit(0)
