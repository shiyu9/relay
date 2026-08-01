"""relay SessionStart hook.

Injects the project's knowledge files and the two most recent diary files
into the new session's context (stdout of a SessionStart hook is added as
context). Runs only on startup/clear (matcher + guard); silent when there
is nothing to inject.

Also runs catch-up: sessions whose SessionEnd hook was killed before it
could spawn the recorder (e.g. the terminal window closed together with
claude) left ended transcripts with no ledger marker; they get a detached
recorder now. Session start never races console teardown, so this is the
reliable half of the recording pipeline.
"""
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import (
    force_utf8,
    in_scope,
    is_disabled,
    is_reentry,
    ledger_dir,
    log,
    mark_recorded,
    read_hook_input,
    recorded_size,
    spawn_recorder,
    status_path,
    transcripts_dir,
)

# A transcript untouched this long is considered ended, not idle-but-open.
# Too low records a session someone merely walked away from; the cost of
# too high is only a later catch-up.
CATCHUP_IDLE_SECS = 30 * 60
# Do not resurrect ancient history into today's diary.
CATCHUP_WINDOW_DAYS = 14
# Bound headless spawns per session start; the rest are caught next time.
CATCHUP_MAX_SPAWNS = 3

PREAMBLE = """# relay: 前セッションからの引き継ぎ

以下は relay プラグインが自動記録した、このプロジェクトのナレッジ（永続知見）と直近の日記（セッション記録）である。作業の前提として扱うこと。

ナレッジの手入れ（気づいたときだけでよい。毎回の義務ではない）:
1. knowledge/ 内に重複・矛盾・80行超過に気づいたら、新しい情報を正として統合・修正・剪定する。更新は上書きし、古い項目を残さない。
2. [日付] が古い項目に依拠する前に実態を確認する。実態が変わっていたら項目を修正または削除する。
3. どちらが正しいか判断できない矛盾は、勝手に決めずユーザーに確認する。

status.md は「いま終わっていないこと」だけを載せた一枚で、セッション終了時にレコーダーが毎回上書きして維持する。読む対象であり、セッション中に手で編集しない（次の記録で上書きされる）。
"""


def read_file(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def catch_up(cwd, now=None):
    """Spawn recorders for ended-but-unprocessed transcripts of this project."""
    tdir = transcripts_dir(cwd)
    if not tdir.is_dir():
        return
    now = time.time() if now is None else now

    entries = []
    for p in tdir.glob("*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append((p, st.st_mtime, st.st_size))

    if not ledger_dir(cwd).is_dir():
        # First catch-up for this project: adopt existing transcripts as
        # already handled, instead of recording weeks-old sessions into
        # today's diary.
        ledger_dir(cwd).mkdir(parents=True, exist_ok=True)
        for p, _, size in entries:
            mark_recorded(cwd, p.stem, size)
        if entries:
            log("start", f"ledger initialized: adopted {len(entries)} transcripts for {cwd}")
        return

    spawned = 0
    for p, mtime, size in sorted(entries, key=lambda e: e[1], reverse=True):
        if spawned >= CATCHUP_MAX_SPAWNS:
            break
        age = now - mtime
        if age < CATCHUP_IDLE_SECS or age > CATCHUP_WINDOW_DAYS * 86400:
            continue
        if recorded_size(cwd, p.stem) == size:
            continue
        spawn_recorder(cwd, p)
        spawned += 1
    if spawned:
        log("start", f"catch-up: spawned {spawned} recorder(s) for {cwd}")


def main():
    force_utf8()
    if is_reentry() or is_disabled():
        return

    data = read_hook_input()
    source = data.get("source", "startup")
    if source not in ("startup", "clear"):
        return

    cwd = pathlib.Path(data.get("cwd") or os.getcwd())
    if not in_scope(str(cwd)):
        return

    sections = []

    for name, title in (("pitfalls.md", "knowledge/pitfalls.md（技術的ハマりと回避策）"),
                        ("workflow.md", "knowledge/workflow.md（このプロジェクトでの進め方の学び）")):
        content = read_file(cwd / "knowledge" / name)
        if content:
            sections.append(f"## {title}\n\n{content}")

    diary_dir = cwd / "diary"
    if diary_dir.is_dir():
        diaries = sorted(
            (p for p in diary_dir.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", p.name)),
            key=lambda p: p.name,
            reverse=True,
        )[:2]
        for p in reversed(diaries):  # older first, newest last
            content = read_file(p)
            if content:
                sections.append(f"## diary/{p.name}\n\n{content}")

    # Last on purpose: status is rewritten every session, so keeping it at the
    # end leaves the longest shared prefix for caching (and puts the most
    # actionable page closest to the conversation).
    status = read_file(status_path(cwd))
    if status:
        sections.append(
            "## status.md（未完了の作業・保留中の確認・次のアクション）\n\n" + status)

    if sections:
        print(PREAMBLE)
        print("\n\n".join(sections))
        log("start", f"injected {len(sections)} sections for {cwd}")

    try:
        catch_up(cwd)
    except Exception as e:  # catch-up must never break injection/startup
        log("start", f"catch-up error: {e!r}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("start", f"error: {e!r}")
    sys.exit(0)
