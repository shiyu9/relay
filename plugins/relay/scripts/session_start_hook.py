"""relay SessionStart hook.

Two jobs, both done through stdout (a SessionStart hook's stdout becomes
context):

1. Inject the project's knowledge and the most recent diary entries.
2. When transcripts are still unrecorded, print the procedure that records
   them. Recording runs as subagents of this session rather than a detached
   headless `claude`, so it works in environments where the CLI is not
   available at all. The long job bodies live in files; what is printed is
   the short prompt that points a subagent at one (built by relay_jobs),
   keeping the main session's context clear of the bodies themselves.

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
    read_hook_input,
    read_text,
    recent_entries,
)
from relay_detect import pending_sessions
from relay_jobs import write_jobs

KNOWLEDGE_TITLES = (
    ("pitfalls.md", "knowledge/pitfalls.md（技術的ハマりと回避策）"),
    ("workflow.md", "knowledge/workflow.md（このプロジェクトでの進め方の学び）"),
    ("decided.md", "knowledge/decided.md（見送った案と再開条件）"),
)

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

    sections = build_sections(cwd)
    if sections:
        print(relay_prompts.PREAMBLE)
        print("\n\n".join(sections))
        log("start", f"injected {len(sections)} sections for {cwd}")

    # Detection must never break startup or swallow the injection above.
    try:
        pending = pending_sessions(cwd)
    except Exception as e:
        log("start", f"detect error: {e!r}")
        return
    if not pending:
        return
    try:
        print(write_jobs(cwd, pending))
        log("start", f"{len(pending)} session(s) to record for {cwd}")
    except Exception as e:
        log("start", f"job error: {e!r}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("start", f"error: {e!r}")
    sys.exit(0)
