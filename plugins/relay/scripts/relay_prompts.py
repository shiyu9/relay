"""Job bodies handed to the session's subagents, and the SessionStart notice.

Each job is written to a file and the subagent is pointed at it with a
one-line prompt, so these bodies never enter the main session's context.

The three jobs are deliberately separate. Every one of them asks for the
smallest set of delimiters that can do its work: an output contract the
model can satisfy without dropping a section is the whole point of the
split.
"""

# --- job 1: record one transcript -----------------------------------------

RECORD = """あなたは relay のセッション記録係です。下の transcript を読み、区切り付きの\
テキストを Write ツールで次のファイルに書き出してください。

  出力先: {out}

**このファイル以外を一切変更してはいけません。**diary/ や knowledge/ には触らないこと\
（それらの更新は Python が決定的に行います）。

transcript: {transcript}

JSONL です。大きい場合は末尾を優先して読んでください。`"type":"user"` でも `tool_result` を\
含む項目は実際のユーザー発言ではありません。

読者は**次のセッションの Claude** で、この記録を読んで作業を続けます。その読者に向けて\
書いてください。会話でユーザーが使ったのと同じ言語で書き、**ファイルパス・コマンド・識別子・\
エラーメッセージは逐語で保つこと**（言い換えも翻訳もしない）。

transcript の `thinking` ブロックに中身があれば、decisions（検討して捨てた案）と\
mistakes（なぜ誤った判断をしたか）の根拠に使ってよい。空のことがあり、そのときは何も変わりません。

既存のナレッジ（同じことを言う項目を繰り返さない）:

--- pitfalls.md ---
{pitfalls}
--- workflow.md ---
{workflow}
---

出力先に書く内容は、次の区切りだけ。前後に何も付けないこと:

===DIARY===
### done
- 実際に達成したこと（事実。有用ならファイルパスも）
### decisions
- 下した判断とその理由（無ければ見出しごと省く）
### mistakes
- このセッションでの誤り・手戻りを具体的な事実として（無ければ見出しごと省く）
### handoff
- 次のセッションに要ること: 未完了の作業・保留中の確認・次のアクション
===PITFALLS===
- [{date}] 新しい技術的な罠と回避策（命令形・1項目1事実）
===WORKFLOW===
- [{date}] このユーザーとの進め方について新しく学んだこと（命令形・1項目1事実）
===END===

**4つの区切り行は、節が空でも必ず全部書くこと。**

PITFALLS / WORKFLOW の規則（該当が無ければ節を空にする）:
- このセッションで実際に起きた失敗・手戻り・ユーザーの訂正から生まれた教訓だけ
- コード・CLAUDE.md・adr.md・git 履歴から導けることは書かない
- 上の既存項目と同じことを言うものは書かない

**`## S1 ...` のような見出し行は書かないこと。**時刻も S 番号も Python が付けます。

セッションに実質的な中身が無ければ、出力先に `NOTHING_TO_RECORD` とだけ書いてください。
"""

# --- job 2: the overwrite layer -------------------------------------------

OVERWRITE = """あなたは relay の「現在地」係です。**transcript は読みません。**下の材料\
だけを読み、区切り付きのテキストを Write ツールで次のファイルに書き出してください。

  出力先: {out}

**このファイル以外を一切変更してはいけません。**

今回書かれた日記（この中の `### handoff` と `### decisions` が主な材料です）:
{diary_paths}

現在の knowledge/current.md:
--- ここから ---
{current}
--- ここまで ---

現在の knowledge/decided.md:
--- ここから ---
{decided}
--- ここまで ---

出力先に書く内容は、次の区切りだけ:

===CURRENT===
（current.md の新しい全文）
===DECIDED===
（decided.md の新しい全文）
===END===

**CURRENT の規則:**
- 「**いまどこにいて、次に何をするか**」を書く1つの節。**未完了の網羅的な一覧ではない**
- 上の current.md を出発点に更新する。今回のセッションが触れていない内容は残す
- **行数の上限は無い。**現在地を伝えるのに必要なだけ書いてよい
- この節は current.md を**丸ごと置き換える**。書かなかったものは失われる
- 節を空にすると既存の current.md がそのまま維持される。空にしてよいのは\
「更新するものが何も無い」ときだけ

**DECIDED の規則:**
- 1件＝`- YYYY-MM-DD <見送った案>。理由: <なぜ>。再開条件: <何が起きたら戻すか>`
- **再開条件のある見送りだけを書く。**確定した設計判断は `docs/adr.md` の領分なので書かない
- 上の decided.md を出発点に更新する。**再開条件が満たされた項目、前提ごと消えた項目は落とす**
- この節も丸ごと置き換える。節を空にすると既存の decided.md が維持される

日本語で書くか英語で書くかは、既存ファイルと日記に合わせてください。
"""

# --- job 3: prune the knowledge files -------------------------------------

PRUNE = """あなたは relay のナレッジ剪定係です。下の2ファイルだけを読み、剪定した**全文**を\
Write ツールで次のファイルに書き出してください。

  出力先: {out}

**このファイル以外を一切変更してはいけません。**

--- knowledge/pitfalls.md（現在 {pitfalls_lines} 行）---
{pitfalls}
--- knowledge/workflow.md（現在 {workflow_lines} 行）---
{workflow}
---

出力先に書く内容:

===PRUNE_PITFALLS===
（pitfalls.md の新しい全文）
===PRUNE_WORKFLOW===
（workflow.md の新しい全文）
===END===

規則:
- **重複する項目は統合する。**矛盾する項目は**新しい [日付] のものを正**として直す
- 各ファイルを {limit} 行以内に収める
- **どちらが正しいか判断できない矛盾は、勝手に決めずに両方残す**（人が判断します）
- 項目の形式（`- [YYYY-MM-DD] 命令形の1事実`）は変えない
- 片方だけ剪定が必要な場合でも、**もう片方の節には現在の全文をそのまま書くこと**\
（節を空にすると、そのファイルは変更されません）
"""

# --- what SessionStart prints ---------------------------------------------

PREAMBLE = """# relay: 前セッションからの引き継ぎ

以下は relay プラグインが自動記録した、このプロジェクトのナレッジ（永続知見）と直近の日記\
（セッション記録）である。作業の前提として扱うこと。

ナレッジの手入れ（気づいたときだけでよい。毎回の義務ではない）:
1. pitfalls.md / workflow.md に重複・矛盾・80行超過を見つけたら、新しい情報を正として統合・\
修正・剪定する。更新は上書きし、古い項目を残さない。
2. [日付] が古い項目に依拠する前に実態を確認する。実態が変わっていたら項目を修正または削除する。
3. どちらが正しいか判断できない矛盾は、勝手に決めずユーザーに確認する。

current.md（現在地）と decided.md（見送りと再開条件）はレコーダーが毎回上書きして維持する。\
読む対象であり、セッション中に手で編集しない（次の記録で上書きされる）。
"""

TODO = """
# relay: 未記録のセッションが {n} 件ある

**ユーザーへの最初の応答より前に**、次の手順で記録を済ませること。手順の外のことはしない。

1. 次のジョブを**1つずつ順に** Task ツールで実行する（サブエージェントの種類は\
`general-purpose`、モデルは `{model}`）。**サブエージェントに渡すプロンプトは\
「<ジョブのパス> を読み、書かれた指示に従え」の1行でよい**（本文をここに展開しないこと）。
{jobs}
2. 全部終わったら次を1回実行する。

   `{apply}`

   `diary=<件数> ... pitfalls_lines=<行数> workflow_lines=<行数>` の1行が出る。
3. その `diary` が 0 でなければ、`{overwrite_job}` を同じやり方で1回実行し、\
再び手順2のコマンドを実行する。
4. 手順3で出た `pitfalls_lines` か `workflow_lines` が {limit} を超えていれば、\
`{prune_job}` を同じやり方で1回実行し、再び手順2のコマンドを実行する。

終わったら「relay: 記録しました」と1行だけ添えて、本来の作業に移ること。
"""

STOP_NUDGE = (
    "relay: 未記録のセッションが {n} 件残っています。セッション開始時に注入された "
    "「relay: 未記録のセッションがある」の手順を実行してください（ジョブは "
    "{jobs_dir} にあります）。記録が済むまで、この促しは30分おきに出ます。"
    "実行したあとの締めの発言は、直前にユーザーへ返した本文を置き換えないこと。"
    "1〜2行の追記に留めてください。"
)
