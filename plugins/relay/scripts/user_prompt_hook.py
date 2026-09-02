"""relay UserPromptSubmit hook — say once when a recording finishes.

relay does not speak during a session. The Stop hook was retired for
exactly that reason: it fired every turn, walked every transcript, and
repeated a nudge the session sometimes could not act on. This hook is the
one deliberate exception, and it is narrow enough to stay one.

It exists because of a promise made at startup. When a session begins while
the previous one is still being recorded, the injected text says so — that
current.md does not yet include that session, and that conclusions about it
are unconfirmed. A promise like that needs an end: without it the reader
either keeps distrusting a page that is now correct, or forgets the caveat
and trusts a page that was not. So this hook watches for the recording to
finish and says so, once.

What it costs when there is nothing to say: two stats. It reads the watch
file written at startup, and if that file is absent — which is the common
case, because nothing was being recorded — it returns having done nothing
else. There is no transcript walk here and there must never be one.

Output: one line on stdout, only on the turn where the wait ends.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import (
    force_utf8,
    in_scope,
    is_disabled,
    log,
    notified_dir,
    read_hook_input,
    read_text,
    running_sids,
)

NOTICE = ("relay: 前のセッション（{sids}）の記録が完了しました。"
          "`knowledge/current.md` は最新になっています。"
          "起動時の注入内容より新しいので、**続ける前に読み直してください。**")


def main():
    force_utf8()
    if is_disabled():
        return
    data = read_hook_input()
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id")
    if not sid or not in_scope(cwd):
        return

    watch = notified_dir(cwd) / f"{sid}.watch"
    waiting = read_text(watch).split()
    if not waiting:
        return  # nothing was being recorded when this session started

    finished = [s for s in waiting if s not in running_sids(cwd)]
    if not finished:
        return

    # Removed before the notice is printed, so a crash between the two
    # costs silence rather than a line repeated every turn.
    try:
        watch.unlink()
    except OSError as e:
        log("prompt", f"could not clear the watch for {sid[:8]}: {e!r}")
        return

    print(NOTICE.format(sids=" ".join(s[:8] for s in finished)))
    log("prompt", f"told {sid[:8]} that {len(finished)} recording(s) finished")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("prompt", f"error: {e!r}")
    sys.exit(0)
