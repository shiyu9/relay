"""relay SessionStart hook.

Injects the project's knowledge files and the two most recent diary files
into the new session's context (stdout of a SessionStart hook is added as
context). Runs only on startup/clear (matcher + guard); silent when there
is nothing to inject.
"""
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import (
    force_utf8,
    in_scope,
    is_disabled,
    is_reentry,
    log,
    read_hook_input,
)

PREAMBLE = """# relay: 前セッションからの引き継ぎ

以下は relay プラグインが自動記録した、このプロジェクトのナレッジ（永続知見）と直近の日記（セッション記録）である。作業の前提として扱うこと。

ナレッジの手入れ（気づいたときだけでよい。毎回の義務ではない）:
1. knowledge/ 内に重複・矛盾・80行超過に気づいたら、新しい情報を正として統合・修正・剪定する。更新は上書きし、古い項目を残さない。
2. [日付] が古い項目に依拠する前に実態を確認する。実態が変わっていたら項目を修正または削除する。
3. どちらが正しいか判断できない矛盾は、勝手に決めずユーザーに確認する。
"""


def read_file(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


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

    if not sections:
        return

    print(PREAMBLE)
    print("\n\n".join(sections))
    log("start", f"injected {len(sections)} sections for {cwd}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("start", f"error: {e!r}")
    sys.exit(0)
