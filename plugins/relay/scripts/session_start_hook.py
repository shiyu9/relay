"""relay SessionStart hook.

Everything here goes through stdout, which a SessionStart hook turns into
context. Three things get said, and two of them are usually nothing:

1. The project's knowledge, recent diary, open tasks and current page.
2. That a recorder is still at work — which means the page just injected is
   knowingly incomplete. Saying so is the whole point: handing over a stale
   current.md without a word is what the design refuses to do.
3. That transcripts are waiting, plus the two steps that record them.

On a machine with the CLI, recording happens at SessionEnd and this hook
normally adds nothing. Without one — or when SessionEnd never fired at all —
this is where the backlog is caught. The work itself is run by relay_record
either way, so the two entry points cannot drift apart.

Runs only on startup/clear; silent when there is nothing to say.
"""
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay_prompts
from relay_common import (
    INJECT_ENTRIES,
    force_utf8,
    in_scope,
    is_disabled,
    knowledge_path,
    log,
    notified_dir,
    read_hook_input,
    read_text,
    recent_entries,
    running_sids,
    tasks_outline,
)
from relay_detect import pending_sessions

KNOWLEDGE_TITLES = (
    ("pitfalls.md", "knowledge/pitfalls.md（技術的ハマりと回避策）"),
    ("workflow.md", "knowledge/workflow.md（このプロジェクトでの進め方の学び）"),
    ("decided.md", "knowledge/decided.md（見送った案と再開条件）"),
)

SCRIPTS = pathlib.Path(__file__).resolve().parent

HANDOFF_RE = re.compile(r"(?ms)^### handoff\b.*?(?=^### |\Z)")


def strip_handoff(raw):
    """Drop the handoff section from an entry before injecting it.

    handoff is what current.md distills, so injecting both puts two or three
    competing "what is left" lists in front of the reader — the very problem
    the current.md layer exists to remove. It stays in the file: the diary is
    still the record of what was outstanding at that point in time.
    """
    return HANDOFF_RE.sub("", raw).rstrip()


def build_sections(cwd):
    sections = []
    for name, title in KNOWLEDGE_TITLES:
        content = read_text(knowledge_path(cwd, name)).strip()
        if content:
            sections.append(f"## {title}\n\n{content}")

    body = "\n\n".join(strip_handoff(e.raw) for e in recent_entries(cwd))
    if body.strip():
        sections.append(
            f"## diary（直近{INJECT_ENTRIES}件・handoff は current.md に集約）\n\n{body}")

    # Between the diary and current.md, on the same "least changeable first"
    # rule: tasks.md only changes on the runs where an item is struck off,
    # while current.md is rewritten in full every single session.
    outline = tasks_outline(cwd).strip()
    if outline:
        sections.append("## tasks.md（未完のタスク）\n\n" + outline)

    # Last on purpose: current.md is rewritten every session, so keeping it at
    # the end leaves the longest shared prefix for caching (and puts the most
    # actionable page closest to the conversation).
    current = read_text(knowledge_path(cwd, "current.md")).strip()
    if current:
        sections.append("## knowledge/current.md（現在地）\n\n" + current)
    return sections


def main():
    force_utf8()
    if is_disabled():
        return

    data = read_hook_input()
    if data.get("source", "startup") not in ("startup", "clear"):
        return

    cwd = pathlib.Path(data.get("cwd") or os.getcwd())
    if not in_scope(str(cwd)):
        return

    # One log holds every project's startups, so a `[start]` line on its own
    # says which project but not which session — and two sessions of the same
    # project a minute apart are told apart only by lining timestamps up
    # against transcripts. The id costs eight characters.
    me = (data.get("session_id") or "????????")[:8]

    sections = build_sections(cwd)
    if sections:
        print(relay_prompts.PREAMBLE)
        print("\n\n".join(sections))
        log("start", f"{me}: injected {len(sections)} sections for {cwd}")

    # Detection must never break startup or swallow the injection above.
    try:
        running = running_sids(cwd)
        pending = [p for p in pending_sessions(cwd) if p.sid not in running]
    except Exception as e:
        log("start", f"{me}: detect error: {e!r}")
        return

    if running:
        # Two things at once: tell the reader the page they were just handed
        # is incomplete, and leave the note that lets UserPromptSubmit say
        # when it stops being incomplete.
        print(relay_prompts.RECORDING_NOW.format(
            sids=" ".join(s[:8] for s in sorted(running))))
        sid = data.get("session_id")
        if sid:
            try:
                d = notified_dir(cwd)
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{sid}.watch").write_text("\n".join(sorted(running)),
                                                encoding="utf-8")
            except OSError as e:
                log("start", f"could not leave a watch for {sid[:8]}: {e!r}")
        log("start", f"{me}: {len(running)} recorder(s) still at work in {cwd}")

    if not pending:
        return
    # The procedure itself comes from relay_record, the same script the skill
    # runs. What is injected here is the two steps that get there: say what
    # is about to happen, then run it.
    cmd = 'python "{}" --cwd "{}"'.format(
        (SCRIPTS / "relay_record.py").as_posix(), pathlib.Path(cwd).as_posix())
    print(relay_prompts.STARTUP_NOTICE.format(n=len(pending), cmd=cmd))
    log("start", f"{me}: {len(pending)} session(s) to record for {cwd}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("start", f"error: {e!r}")
    sys.exit(0)
