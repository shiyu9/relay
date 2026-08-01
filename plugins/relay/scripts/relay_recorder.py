"""relay recorder (runs detached, spawned by session_end_hook).

Runs a headless Claude that only READS the transcript and emits section
text between delimiters. All file mutations (S-numbering, appending,
timestamps) happen here, deterministically — the model never touches
files, so it can neither overwrite the diary nor miscount sessions.

Usage: relay_recorder.py <transcript_path> <project_cwd>
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import (
    count_user_messages,
    force_utf8,
    local_now,
    log,
    mark_recorded,
    mark_status_written,
    recorded_size,
    status_path,
    status_source_mtime,
    write_atomic,
)

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MIN_USER_MSGS = 3

PROMPT_TEMPLATE = """You are the "relay" session recorder. What you write is read by \
the NEXT Claude Code session in this project, which uses it to continue the work — \
write for that reader. Summarize the finished session below. You must NOT modify any \
file — read the transcript, then OUTPUT TEXT ONLY in the exact delimited format at \
the end.

Transcript (JSONL; if large, prioritize the tail; "type":"user" entries \
containing tool results are not real user messages):
{transcript}

Write in the SAME LANGUAGE the user used in the conversation, but keep file paths, \
commands, identifiers and error messages verbatim — never paraphrase or translate them.

Where the transcript's "thinking" blocks have content, you may use them as evidence \
for decisions (options weighed and dropped) and mistakes (why a wrong call was made). \
They are empty unless the user opted in, and nothing changes when they are.

Existing knowledge items (do not repeat anything that says the same thing):

--- pitfalls.md ---
{existing_pitfalls}
--- workflow.md ---
{existing_workflow}
--- status.md (what is currently NOT finished) ---
{existing_status}
---

Output format — exactly these delimiters, nothing before or after:

===DIARY===
### done
- what was actually accomplished (facts, file paths where useful)
### decisions
- decisions made and why (omit this heading if none)
### mistakes
- mistakes / rework this session, as concrete facts (omit heading if none)
### handoff
- what the next session needs: unfinished work, pending confirmations, next actions
===STATUS===
- the new full contents of status.md (see the rules below)
===PITFALLS===
- [{date}] new technical pitfall + how to avoid it (imperative, one fact per bullet)
===WORKFLOW===
- [{date}] new lesson about how to work with this user (imperative, one fact per bullet)
===END===

Always include all five delimiter lines, even when a section is empty.

Rules for STATUS (the one section that is OVERWRITTEN, not appended):
- Start from the status.md above and update it. Keep items this session never
  touched. Drop an item only when the transcript shows it was actually finished.
  Add whatever this session left open.
- Present tense, one item per bullet, about 20 lines at most. No finished work —
  that is what the diary is for.
- This section REPLACES status.md whole, so anything you leave out is lost.
- If nothing is pending, say exactly that in a single bullet. Leaving the section
  empty keeps the previous status.md as it is.

Rules for PITFALLS/WORKFLOW (leave the section empty if nothing qualifies):
- Only lessons born from an actual failure, rework, or user correction in THIS session.
- Nothing derivable from the code, CLAUDE.md, adr.md, or git history.
- Nothing that repeats an existing item above.

If the session had no substantive content, output exactly: NOTHING_TO_RECORD"""


def read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def append_block(path, block):
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_text(path)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        if existing:
            f.write("\n")
        f.write(block.rstrip() + "\n")


def parse_sections(text):
    """Tolerant parse: sections may be omitted entirely by the model.

    DIARY is the only required one. STATUS being optional is what keeps output
    from an older prompt (no ===STATUS===) parsing exactly as it used to.
    """
    parts = re.split(r"===(DIARY|STATUS|PITFALLS|WORKFLOW|END)===", text)
    found = {}
    for i in range(1, len(parts) - 1, 2):
        found.setdefault(parts[i], parts[i + 1].strip())
    return found if "DIARY" in found else None


def bullets_only(section, existing_text):
    """Keep bullet lines that are not literal duplicates of existing ones."""
    existing = {line.strip() for line in existing_text.splitlines()}
    out = []
    for line in section.splitlines():
        s = line.strip()
        if s.startswith("- ") and s not in existing:
            out.append(s)
    return out


def main():
    force_utf8()
    transcript, cwd = sys.argv[1], sys.argv[2]
    project = pathlib.Path(cwd)
    diary_dir = project / "diary"
    pitfalls_path = project / "knowledge" / "pitfalls.md"
    workflow_path = project / "knowledge" / "workflow.md"

    # Guards live here, not in the SessionEnd hook: the hook must reach
    # Popen before a closing console kills it, so once detached we do the
    # filtering. Skips are marked in the ledger so SessionStart catch-up
    # does not retry them; failures are not, so catch-up can.
    session_id = pathlib.Path(transcript).stem
    try:
        size = os.path.getsize(transcript)
    except OSError:
        size = None
    try:
        mtime = os.path.getmtime(transcript)
    except OSError:
        mtime = None
    if size is not None and recorded_size(cwd, session_id) == size:
        log("rec", f"skip: already recorded ({session_id})")
        return
    min_msgs = int(os.environ.get("RELAY_MIN_USER_MSGS", DEFAULT_MIN_USER_MSGS))
    n = count_user_messages(transcript)
    if n is not None and n < min_msgs:
        log("rec", f"skip: thin session ({n} user msgs < {min_msgs})")
        if size is not None:
            mark_recorded(cwd, session_id, size)
        return

    claude = shutil.which("claude")
    if not claude:
        log("rec", "abort: claude CLI not found")
        return

    now = local_now()
    prompt = PROMPT_TEMPLATE.format(
        transcript=transcript,
        existing_pitfalls=read_text(pitfalls_path).strip() or "(empty)",
        existing_workflow=read_text(workflow_path).strip() or "(empty)",
        existing_status=read_text(status_path(cwd)).strip() or "(empty)",
        date=f"{now:%Y-%m-%d}",
    )
    model = os.environ.get("RELAY_MODEL", DEFAULT_MODEL)
    p = subprocess.run(
        [claude, "-p", prompt, "--model", model, "--allowedTools", "Read"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (p.stdout or "").strip()
    (pathlib.Path.home() / ".claude" / "relay").mkdir(parents=True, exist_ok=True)
    (pathlib.Path.home() / ".claude" / "relay" / "last_run.log").write_text(
        out + ("\n--- stderr ---\n" + p.stderr if p.stderr else ""), encoding="utf-8")

    if p.returncode != 0:
        log("rec", f"abort: claude exited {p.returncode}"
                   " (auth? check `claude auth status`;"
                   " API-key-only setups need RELAY_KEEP_API_KEY=1)")
        return
    if "NOTHING_TO_RECORD" in out and "===DIARY===" not in out:
        log("rec", "skip: nothing to record")
        if size is not None:
            mark_recorded(cwd, session_id, size)
        return
    sections = parse_sections(out)
    if not sections:
        log("rec", "abort: unparseable model output")
        return
    diary_body = sections["DIARY"]

    # timestamp/S-number are computed at append time, deterministically
    now = local_now()
    diary_file = diary_dir / f"{now:%Y-%m-%d}.md"
    s_num = len(re.findall(r"^## S\d+", read_text(diary_file), re.MULTILINE)) + 1
    if diary_body:
        append_block(diary_file, f"## S{s_num} {now:%H:%M}\n{diary_body}")

    wrote = [f"diary S{s_num}"] if diary_body else []
    for path, key in ((pitfalls_path, "PITFALLS"), (workflow_path, "WORKFLOW")):
        items = bullets_only(sections.get(key, ""), read_text(path))
        if items:
            append_block(path, "\n".join(items))
            wrote.append(f"{path.name} +{len(items)}")

    # status.md is replaced, not appended, so an empty section must mean "no
    # update" rather than "nothing is pending" — otherwise a model that simply
    # forgot the section would silently erase the open work. For the same
    # reason a recorder running late for an older session must not clobber a
    # status already written from a newer one.
    status_sec = sections.get("STATUS", "")
    if status_sec:
        prev = status_source_mtime(cwd)
        if mtime is not None and prev is not None and mtime <= prev:
            log("rec", "skip: status.md already holds a newer session")
        else:
            write_atomic(status_path(cwd), status_sec.rstrip() + "\n")
            if mtime is not None:
                mark_status_written(cwd, mtime)
            wrote.append("status.md")

    if size is not None:
        mark_recorded(cwd, session_id, size)
    log("rec", f"recorded: {', '.join(wrote) if wrote else 'nothing'} in {cwd}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("rec", f"error: {e!r}")
    sys.exit(0)
