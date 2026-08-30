"""relay_apply.py — the only thing that writes diary/ and knowledge/.

Subagents produce delimited text and drop it in the inbox; every mutation
(headings, S-numbers, timestamps, insertion points, overwrite-vs-append)
happens here, deterministically. A cheap model given an "append" instruction
once overwrote existing diary entries out of existence, so the model is kept
away from the files entirely.

Usage: relay_apply.py --cwd <project>
Prints one summary line the session reads to decide what to run next.
"""
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import (
    DROPPED_HEADING,
    PRUNE_LINES,
    diary_dir,
    force_utf8,
    inbox_dir,
    knowledge_path,
    local_now,
    log,
    parse_diary_file,
    read_text,
    write_atomic,
)
from relay_detect import plan_entry

# Longest names first so ===PRUNE_PITFALLS=== can never be read as a plain
# ===PITFALLS===; the two mean append and replace respectively.
SECTION_RE = re.compile(
    r"===(PRUNE_PITFALLS|PRUNE_WORKFLOW|DIARY|PITFALLS|WORKFLOW|CURRENT|DECIDED|END)===")


def parse_sections(text):
    """Delimited sections present in the text. Missing ones stay missing."""
    parts = SECTION_RE.split(text)
    found = {}
    for i in range(1, len(parts) - 1, 2):
        found.setdefault(parts[i], parts[i + 1].strip())
    return found


def bullets_only(section, existing_text):
    """Bullet lines that are not literal duplicates of what is already there."""
    existing = {line.strip() for line in existing_text.splitlines()}
    out = []
    for line in section.splitlines():
        s = line.strip()
        if s.startswith("- ") and s not in existing:
            out.append(s)
    return out


def _splice(text, start, stop, block):
    """Replace text[start:stop] with block, keeping one blank line between
    entries and exactly one trailing newline at the end of the file."""
    head = text[:start]
    tail = text[stop:]
    if tail.strip():
        block = block.rstrip() + "\n\n"
    else:
        block = block.rstrip() + "\n"
        tail = ""
    return head + block + tail


def upsert_entry(cwd, pending, body):
    """Put one session's body in the diary file for its own date.

    Replacing rather than appending is what keeps a resumed session from
    growing a second summary of itself; the entry is found by the session id
    in its heading, so headings without one are never matched and never move.
    """
    path = diary_dir(cwd) / f"{pending.date}.md"
    text = read_text(path)
    entries = parse_diary_file(path)
    mine = next((e for e in entries if e.sid8 == pending.sid8), None)

    if mine is not None and pending.mode != "resume":
        num = mine.num
    else:
        num = len(entries) + 1
        mine = None

    heading = (f"## S{num} {pending.start:%H:%M}-{pending.end:%H:%M} "
               f"({pending.sid8})")
    block = heading + "\n" + body.strip() + "\n"

    if mine is not None:
        new = _splice(text, mine.span[0], mine.span[1], block)
    else:
        later = [e for e in entries
                 if e.start is not None and e.start > pending.start]
        if later:
            new = _splice(text, later[0].span[0], later[0].span[0], block)
        else:
            new = _splice(text, len(text), len(text), block)
    write_atomic(path, new)
    return num


def append_dropped(cwd, lines):
    """Record what the overwrite of decided.md removed.

    decided.md is a replace-in-full layer, so an item that disappears leaves
    no trace of having existed; without this, a later reader cannot tell a
    resolved deferral from one the model simply forgot.
    """
    path = diary_dir(cwd) / f"{local_now():%Y-%m-%d}.md"
    text = read_text(path)
    body = "\n".join(lines)
    for e in parse_diary_file(path):
        if e.heading.strip() == DROPPED_HEADING:
            existing = text[e.span[0]:e.span[1]].rstrip()
            write_atomic(path, _splice(text, e.span[0], e.span[1],
                                       existing + "\n" + body + "\n"))
            return
    write_atomic(path, _splice(text, len(text), len(text),
                               DROPPED_HEADING + "\n" + body + "\n"))


def line_count(path):
    text = read_text(path)
    return len([l for l in text.splitlines() if l.strip()])


def main():
    force_utf8()
    argv = sys.argv[1:]
    cwd = None
    if "--cwd" in argv:
        i = argv.index("--cwd")
        if i + 1 < len(argv):
            cwd = argv[i + 1]
    if not cwd:
        cwd = os.getcwd()

    inbox = inbox_dir(cwd)
    pitfalls_path = knowledge_path(cwd, "pitfalls.md")
    workflow_path = knowledge_path(cwd, "workflow.md")
    current_path = knowledge_path(cwd, "current.md")
    decided_path = knowledge_path(cwd, "decided.md")

    stats = {"diary": 0, "pitfalls": 0, "workflow": 0,
             "current": "-", "decided": "-", "dropped": 0, "bad": 0}

    files = sorted(inbox.glob("*.txt")) if inbox.is_dir() else []
    for f in files:
        text = read_text(f)
        name = f.stem
        try:
            if "NOTHING_TO_RECORD" in text and "===DIARY===" not in text:
                log("apply", f"nothing to record ({name})")
                continue
            sections = parse_sections(text)
            if not sections:
                stats["bad"] += 1
                log("apply", f"no delimiters in {f.name}; discarded")
                continue

            diary_body = sections.get("DIARY", "")
            if diary_body:
                pending = plan_entry(cwd, name)
                if pending is None:
                    stats["bad"] += 1
                    log("apply", f"cannot place entry for {name}; discarded")
                else:
                    num = upsert_entry(cwd, pending, diary_body)
                    stats["diary"] += 1
                    log("apply", f"diary S{num} ({pending.mode}) "
                                 f"{pending.date} {name[:8]}")

            for path, key in ((pitfalls_path, "PITFALLS"),
                              (workflow_path, "WORKFLOW")):
                items = bullets_only(sections.get(key, ""), read_text(path))
                if items:
                    old = read_text(path).rstrip()
                    body = "\n".join(items)
                    write_atomic(path, (old + "\n" if old else "") + body + "\n")
                    stats[key.lower()] += len(items)

            # Overwrite layers: an empty section means "no update", never
            # "everything is finished" — a model that forgets the section
            # must not be able to erase the page.
            for path, key in ((current_path, "CURRENT"),
                              (decided_path, "DECIDED")):
                body = sections.get(key, "")
                if not body:
                    if key in sections:
                        log("apply", f"{key} section empty; {path.name} kept")
                    continue
                if key == "DECIDED":
                    before = {l.strip() for l in read_text(path).splitlines()
                              if l.strip().startswith("- ")}
                    after = {l.strip() for l in body.splitlines()
                             if l.strip().startswith("- ")}
                    gone = sorted(before - after)
                    if gone:
                        append_dropped(cwd, gone)
                        stats["dropped"] += len(gone)
                write_atomic(path, body.rstrip() + "\n")
                stats[key.lower()] = "written"

            for path, key in ((pitfalls_path, "PRUNE_PITFALLS"),
                              (workflow_path, "PRUNE_WORKFLOW")):
                body = sections.get(key, "")
                if body:
                    write_atomic(path, body.rstrip() + "\n")
                    log("apply", f"pruned {path.name} to {len(body.splitlines())} lines")
        except Exception as e:  # one bad inbox file must not block the rest
            stats["bad"] += 1
            log("apply", f"error on {f.name}: {e!r}")
        finally:
            # Always consume the file: leaving it would replay the same write
            # on every run, and an unrecorded session is picked up again by
            # detection anyway.
            try:
                f.unlink()
            except OSError:
                pass

    print("diary={diary} pitfalls=+{pitfalls} workflow=+{workflow} "
          "current={current} decided={decided} dropped={dropped} bad={bad} "
          "pitfalls_lines={pl} workflow_lines={wl} limit={limit}".format(
              pl=line_count(pitfalls_path), wl=line_count(workflow_path),
              limit=PRUNE_LINES, **stats))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("apply", f"error: {e!r}")
        print(f"diary=0 bad=1 error={e!r}")
    sys.exit(0)
