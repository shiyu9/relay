"""relay SessionStart hook.

Two jobs, both done through stdout (a SessionStart hook's stdout becomes
context):

1. Inject the project's knowledge and the most recent diary entries.
2. When transcripts are still unrecorded, print the procedure that records
   them. Recording runs as subagents of this session rather than a detached
   headless `claude`, so it works in environments where the CLI is not
   available at all. The long job bodies live in files; what is printed is
   the short prompt that points a subagent at one, keeping the main session's
   context clear of the bodies themselves.

Runs only on startup/clear; silent when there is nothing to say.
"""
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay_prompts
from relay_common import (
    DROPPED_HEADING,
    MOVE_HEADING,
    all_entries,
    force_utf8,
    in_scope,
    inbox_dir,
    is_disabled,
    jobs_dir,
    knowledge_path,
    log,
    read_hook_input,
    read_text,
    write_prune_job,
)
from relay_detect import pending_sessions

# Enough diary to see what just happened, without the volume swinging with
# how many sessions a given day happened to hold.
INJECT_ENTRIES = 5
DEFAULT_MODEL = "claude-haiku-4-5"

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


def recent_entries(cwd, limit=INJECT_ENTRIES):
    """The newest entries across all diary files, oldest of them first.

    Counted as entries rather than days: a day holding four sessions would
    otherwise inject four times as much as a day holding one.
    """
    entries = [e for e in all_entries(cwd)
               if e.heading.strip() not in (DROPPED_HEADING, MOVE_HEADING)]
    entries.sort(key=lambda e: e.sort_key())
    return entries[-limit:]


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


def write_jobs(cwd, pending, here):
    """Write the job bodies and return the notice describing how to run them."""
    jobs = jobs_dir(cwd)
    jobs.mkdir(parents=True, exist_ok=True)
    inbox = inbox_dir(cwd)
    inbox.mkdir(parents=True, exist_ok=True)

    pitfalls = read_text(knowledge_path(cwd, "pitfalls.md")).strip() or "(empty)"
    workflow = read_text(knowledge_path(cwd, "workflow.md")).strip() or "(empty)"
    project = pathlib.Path(cwd).as_posix()

    # The call the session will make is written out here rather than described:
    # the classifier scores that call, and a prompt the session composed from a
    # path plus a sentence of guidance is the shape that got refused.
    lines = []
    for i, p in enumerate(pending, 1):
        job = jobs / f"{p.sid}.md"
        out = inbox / f"{p.sid}.txt"
        job.write_text(relay_prompts.RECORD.format(
            out=out.as_posix(), transcript=p.path.as_posix(),
            pitfalls=pitfalls, workflow=workflow), encoding="utf-8")
        lines.append(relay_prompts.CALL_BLOCK.format(
            title=f"ジョブ {i}/{len(pending)}: {p.date} のセッション {p.sid8}",
            desc="セッション記録テキストの作成",
            body=relay_prompts.CALL_RECORD.format(
                project=project, job=job.as_posix(),
                transcript=p.path.as_posix(), out=out.as_posix())))

    diary_paths = "\n".join(
        f"  - {(pathlib.Path(cwd) / 'diary' / (d + '.md')).as_posix()}"
        for d in sorted({p.date for p in pending}))
    overwrite = jobs / "overwrite.md"
    overwrite.write_text(relay_prompts.OVERWRITE.format(
        out=(inbox / "overwrite.txt").as_posix(),
        diary_paths=diary_paths,
        current=read_text(knowledge_path(cwd, "current.md")).strip() or "(まだ無い)",
        decided=read_text(knowledge_path(cwd, "decided.md")).strip() or "(まだ無い)",
    ), encoding="utf-8")

    # Written here so the path in the notice exists, and stamped so a run of
    # it can be refused later. relay_apply rewrites it after every apply,
    # because the recording jobs append to the very files it embeds.
    prune = write_prune_job(cwd)

    apply_cmd = 'python "{}" --cwd "{}"'.format(
        (here / "relay_apply.py").as_posix(), project)
    return relay_prompts.TODO.format(
        n=len(pending), model=os.environ.get("RELAY_MODEL", DEFAULT_MODEL),
        jobs="".join(lines), apply=apply_cmd,
        overwrite_call=relay_prompts.CALL_BLOCK.format(
            title="現在地のジョブ", desc="現在地テキストの作成",
            body=relay_prompts.CALL_OVERWRITE.format(
                project=project, job=overwrite.as_posix(),
                out=(inbox / "overwrite.txt").as_posix())),
        prune_call=relay_prompts.CALL_BLOCK.format(
            title="ナレッジ統合のジョブ", desc="ナレッジ統合テキストの作成",
            body=relay_prompts.CALL_MERGE.format(
                project=project, job=prune.as_posix(),
                out=(inbox / "prune.txt").as_posix())))


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
        print(write_jobs(cwd, pending, pathlib.Path(__file__).resolve().parent))
        log("start", f"{len(pending)} session(s) to record for {cwd}")
    except Exception as e:
        log("start", f"job error: {e!r}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("start", f"error: {e!r}")
    sys.exit(0)
