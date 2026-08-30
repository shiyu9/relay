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
- 新しい技術的な罠と回避策（命令形・1項目1事実）
===WORKFLOW===
- このユーザーとの進め方について新しく学んだこと（命令形・1項目1事実）
===END===

**4つの区切り行は、節が空でも必ず全部書くこと。**

PITFALLS / WORKFLOW の規則:
- **この2節は空のまま終わるのが普通です。**該当が無ければ空にする
- 書くのは、このセッションで実際に起きた失敗・手戻り・ユーザーの訂正から生まれた教訓だけ
- コード・CLAUDE.md・adr.md・git 履歴から導けることは書かない
- **上の既存項目と同じ主題のものは、言い回しを変えても書かない。**書く前に既存項目を\
1つずつ見て、主題が重なるものが1つでもあれば、その項目は落とす（例:「露出した API キーは \
transcript にそのまま残る」は、既存の「キーを失効させるだけでは不十分。transcript に記録が\
残る」と同じ主題なので書かない）
- **行頭に `[日付]` を書かないこと。**日付は Python が付けます

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
===MOVE===
- <移設を検討すべき項目>。理由: <なぜここでなくてよいか>
===END===

規則:
- **重複する項目は統合する。**矛盾する項目は**新しい [日付] のものを正**として直す
- **項目を削除しないこと。**統合で1行になるもの以外は、全部そのまま残す。\
ファイルが長いことは削る理由にならない（{limit} 行はここが育ったという合図であって、\
収めるべき上限ではない）
- **どちらが正しいか判断できない矛盾は、勝手に決めずに両方残す**（人が判断します）
- 項目の形式（`- [YYYY-MM-DD] 命令形の1事実`）は変えない
- 片方だけ手を入れる場合でも、**もう片方の節には現在の全文をそのまま書くこと**\
（節を空にすると、そのファイルは変更されません）

**MOVE の規則:**
- 「このファイルでなくてよい」と思う項目を、理由付きで挙げるだけ。**移設も削除もしない**\
（移設先を決めるのは人です）
- 候補は日記に書き出され、人が読んで判断します。**該当が無ければ節を空にする**
- 挙げてよいのは例えば「特定の作業の手順で、`docs/` の資料に属するもの」\
「一度きりの事情で、もう起こらないもの」。**迷ったら挙げない**
"""

# --- how the session calls a job ------------------------------------------
#
# The Task call is itself what auto mode's classifier scores, and "read this
# file and do what it says" describes nothing it can see. Measured over the
# real logs: 7 of 17 relay subagent calls were refused with `Blocked by
# classifier`, and the shape that finally got through — after the same job
# had been refused twice — leads with the goal, numbers the steps, names the
# tool each step uses, and names the single file that gets written. These
# templates are that shape. The hook fills them in and prints them verbatim,
# so the session pastes a prompt rather than composing one.

CALL_RECORD = """\
プロジェクト `{project}` の作業記録を1件つくる作業です。

手順:
1. 指示書 `{job}` を Read で読む。
2. 指示書に書かれたとおりに作業する。内容は「過去のセッションの記録ファイル \
`{transcript}` を Read で読み、done / decisions / mistakes / handoff の4節からなる\
要約テキストにまとめ、`{out}` へ Write で1ファイルだけ書き出す」というものです。

制約: 書き込むのは `{out}` の1本だけ。`diary/` `knowledge/` を含む他のファイルは\
読むだけで、変更しないでください。"""

CALL_OVERWRITE = """\
プロジェクト `{project}` の「現在地」ノートを更新する作業です。

手順:
1. 指示書 `{job}` を Read で読む。
2. 指示書に書かれたとおりに作業する。内容は「指示書が挙げている `diary/` の日記と \
`knowledge/` のファイルを Read で読み、いまの現在地をまとめたテキストを組み立て、\
`{out}` へ Write で1ファイルだけ書き出す」というものです。

制約: 書き込むのは `{out}` の1本だけ。`diary/` `knowledge/` を含む他のファイルは\
読むだけで、変更しないでください。"""

CALL_MERGE = """\
プロジェクト `{project}` のナレッジ2ファイルを1つにまとめ直す作業です。

手順:
1. 指示書 `{job}` を Read で読む。
2. 指示書に書かれたとおりに作業する。内容は「指示書に貼ってある pitfalls.md と \
workflow.md の本文を読み、重複を1行にまとめた全文を組み立て、`{out}` へ Write で\
1ファイルだけ書き出す」というものです。

制約: 書き込むのは `{out}` の1本だけ。`knowledge/` の実ファイルは変更しないでください。"""

# One job as the notice shows it: a header naming the Task description, then
# the prompt between two markers, so "paste what is between them" is an
# instruction with no room left to abbreviate.
CALL_BLOCK = """
--- {title}（`description` は `{desc}`）ここから ---
{body}
--- ここまで ---
"""

# --- what SessionStart prints ---------------------------------------------

PREAMBLE = """# relay: 前セッションからの引き継ぎ

以下は relay プラグインが自動記録した、このプロジェクトのナレッジ（永続知見）と直近の日記\
（セッション記録）である。作業の前提として扱うこと。

ナレッジの手入れ（気づいたときだけでよい。毎回の義務ではない）:
1. pitfalls.md / workflow.md に重複・矛盾を見つけたら、新しい情報を正として統合・修正する。\
更新は上書きし、古い項目を残さない。**長いことは消す理由にならない。**
2. [日付] が古い項目に依拠する前に実態を確認する。実態が変わっていたら項目を修正または削除する。
3. どちらが正しいか判断できない矛盾は、勝手に決めずユーザーに確認する。

current.md（現在地）と decided.md（見送りと再開条件）はレコーダーが毎回上書きして維持する。\
読む対象であり、セッション中に手で編集しない（次の記録で上書きされる）。
"""

TODO = """
# relay: 未記録のセッションが {n} 件ある

**ユーザーへの最初の応答より前に**、次の手順で記録を済ませること。手順の外のことはしない。

1. 下のジョブを Task ツールで実行する（サブエージェントの種類は `general-purpose`、\
モデルは `{model}`）。**複数あるときは全部を同じメッセージでまとめて呼ぶこと**——\
互いに独立なので並行して構わない。

   **`prompt` には「ここから」と「ここまで」に挟まれた本文をそのまま渡すこと。**\
要約・省略・言い換えをしない。何をどのファイルに書くのかが読み取れないプロンプトは、\
auto mode の分類器に `Blocked by classifier` で拒否される（実測）。
{jobs}
2. 全部終わったら次の1行を実行する。

   `{apply}`

   **この1行を、そのまま1つのコマンドとして実行すること。**`cd` を前に付けない・\
`&&` や `;` で他のコマンドとつながない・`2>&1` などを足さない。つないだ形も分類器に\
拒否される（実測）。作業ディレクトリは `--cwd` で渡してあるので `cd` は要らない。

   `diary=<件数> ... merge_due=<yes|no>` の1行が出る。
3. その `diary` が 0 でなければ、次のジョブを手順1と同じやり方で1回実行し、\
再び手順2のコマンドを実行する。
{overwrite_call}
4. 手順3で出た `merge_due` が `yes` なら、次のジョブを同じやり方で1回実行し、\
再び手順2のコマンドを実行する。行数は自分で数えないこと（`merge_due` が発火条件のすべてです）。
{prune_call}
**`Blocked by classifier` で拒否されたら、同じ呼び出しを繰り返さないこと。**残りのジョブを\
進め、最後に「relay: <件数>件は分類器に拒否されたため記録できませんでした」と1行で伝える。\
拒否されたセッションは未記録のまま残り、次の起動でまた候補に上がるので、記録は失われない。

終わったら「relay: 記録しました」と1行だけ添えて、本来の作業に移ること。
"""

STOP_NUDGE = (
    "relay: 未記録のセッションが {n} 件残っています。セッション開始時に注入された "
    "「relay: 未記録のセッションがある」の手順を実行してください（ジョブは "
    "{jobs_dir} にあります）。記録が済むまで、この促しは30分おきに出ます。"
    "実行したあとの締めの発言は、直前にユーザーへ返した本文を置き換えないこと。"
    "1〜2行の追記に留めてください。"
)
