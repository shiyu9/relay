"""relay_record.py — the manual way in: record now, this session included.

SessionStart records what is already over. This is what a session asks for
when it is itself the thing that needs recording: it names itself with a
token, and the session it is running in joins the backlog it prints.

Naming itself works because a tool call reaches the transcript before it
runs, so the token on this command line is already in the file by the time
the search happens. When the token does not resolve to exactly one recent
transcript the answer is a refusal rather than a guess — recording the wrong
one would mean summarising somebody else's session while they are still
typing into it.

A refusal is not the end of the run: whatever else is outstanding is still
worth recording, so the notice carries the failure and the procedure both.

Usage: relay_record.py --cwd <project> --self <token>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay_prompts
from relay_common import (
    find_self,
    force_utf8,
    in_scope,
    is_disabled,
    log,
)
from relay_detect import pending_sessions
from relay_jobs import write_jobs

NO_SELF = ("relay: このセッション自身は特定できませんでした（トークンが transcript に"
           "見つからない、または複数に一致）。別のトークンで実行し直すと拾えます。\n\n")


def option(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main():
    force_utf8()
    argv = sys.argv[1:]
    cwd = option(argv, "--cwd") or os.getcwd()
    token = option(argv, "--self")

    if is_disabled():
        print("relay: このプロジェクトでは無効です（RELAY_DISABLED=1）。")
        return
    if not in_scope(cwd):
        print("relay: このプロジェクトは RELAY_SCOPE の外なので記録しません。")
        return

    me = find_self(cwd, token) if token else None
    note = NO_SELF if token and me is None else ""

    pending = pending_sessions(cwd, force_sid=me)
    if not pending:
        print(note + "relay: 記録するセッションはありません。")
        return

    print(note + write_jobs(cwd, pending, template=relay_prompts.MANUAL))
    log("record", f"{len(pending)} session(s) for {cwd} "
                  + (f"(self={me[:8]})" if me else "(self not identified)"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("record", f"error: {e!r}")
        print(f"relay: 記録の準備に失敗しました（{e!r}）。")
    sys.exit(0)
