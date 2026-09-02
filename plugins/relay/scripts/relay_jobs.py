"""The recording procedure: the job files, and the notice that points at them.

SessionStart builds these when it finds unrecorded transcripts, and the manual
skill builds them again for whatever is outstanding at the moment it is asked.
Rebuilding rather than pointing at the directory is the point: the jobs written
at startup name the transcripts that were pending at startup, so a session that
recorded those and then worked for another hour has a jobs directory full of
instructions for sessions that are already in the diary.
"""
import os
import pathlib

import relay_prompts
from relay_common import (
    condense_transcript,
    condensed_dir,
    inbox_dir,
    jobs_dir,
    knowledge_path,
    read_text,
    recent_entries,
    tasks_outline,
    write_prune_job,
)

DEFAULT_MODEL = "claude-haiku-4-5"
SCRIPTS = pathlib.Path(__file__).resolve().parent


def write_jobs(cwd, pending, here=SCRIPTS, template=None):
    """Write the job bodies and return the notice describing how to run them.

    `template` picks the framing (startup's backlog notice or the skill's),
    never the steps: both callers hand the model the same jobs.
    """
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
        # Built here, once per job: the recorder is asked to read all of it,
        # and that is only a reasonable request against the condensed form.
        # The original path goes along too, for the clipped tool results.
        condensed = condense_transcript(
            p.path, condensed_dir(cwd) / f"{p.sid}.md")
        job.write_text(relay_prompts.RECORD.format(
            out=out.as_posix(), transcript=p.path.as_posix(),
            condensed=condensed.as_posix(),
            pitfalls=pitfalls, workflow=workflow), encoding="utf-8")
        lines.append(relay_prompts.CALL_BLOCK.format(
            title=f"ジョブ {i}/{len(pending)}: {p.date} のセッション {p.sid8}",
            desc="セッション記録テキストの作成",
            body=relay_prompts.CALL_RECORD.format(
                project=project, job=job.as_posix(),
                transcript=p.path.as_posix(), out=out.as_posix())))

    # The overwrite job distils "where we are now", so the newest diary there
    # is has to be in front of it — not only whatever this run happened to
    # write. Handing it a backlog session alone hands it a handoff from weeks
    # ago, and current.md is then rewritten from that: observed on two
    # consecutive cycles, the second one announcing that a delivery was still
    # awaiting a decision that had already been made.
    dates = {p.date for p in pending} | {e.date for e in recent_entries(cwd)}
    diary_paths = "\n".join(
        f"  - {(pathlib.Path(cwd) / 'diary' / (d + '.md')).as_posix()}"
        for d in sorted(dates))
    # The task list is pasted in rather than pointed at, like current.md and
    # decided.md: the job answers with lines copied out of it, and a match is
    # exact, so the model has to be looking at the same text relay_apply will
    # compare against.
    overwrite = jobs / "overwrite.md"
    overwrite.write_text(relay_prompts.OVERWRITE.format(
        out=(inbox / "overwrite.txt").as_posix(),
        diary_paths=diary_paths,
        current=read_text(knowledge_path(cwd, "current.md")).strip() or "(まだ無い)",
        decided=read_text(knowledge_path(cwd, "decided.md")).strip() or "(まだ無い)",
        tasks=tasks_outline(cwd).strip() or "(まだ無い)",
    ), encoding="utf-8")

    # Written here so the path in the notice exists, and stamped so a run of
    # it can be refused later. relay_apply rewrites it after every apply,
    # because the recording jobs append to the very files it embeds.
    prune = write_prune_job(cwd)

    apply_cmd = 'python "{}" --cwd "{}"'.format(
        (pathlib.Path(here) / "relay_apply.py").as_posix(), project)
    return (template or relay_prompts.TODO).format(
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
