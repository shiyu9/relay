# 要望: platform docs 調査にもとづく relay 改善(3件)

freetalk セッション(2026-08-01)で Claude platform docs を調査し、relay に活かせる知見を
洗い出した結果の引き継ぎ。実装前に rally(spec-interview)で仕様を詰めること。

## 背景

X 上の「Claude の thinking は揮発するので jsonl から振り返らせる仕組みが要る」という
議論の検証から始まり、platform docs を relay/rally の観点で調査した。
検証結果: thinking の揮発は旧モデル(Opus 4.4 以前・Haiku 系)の話で、現行モデル
(Opus 4.5+/Sonnet 4.6+/Fable 5)は過去ターンの thinking をコンテキストに保持する。
セッションを「またぐ」振り返りには依然 relay のような外部記録が必要。

- 根拠: https://platform.claude.com/docs/en/build-with-claude/context-windows
- 根拠: https://platform.claude.com/docs/en/build-with-claude/thinking (Thinking block preservation by model)

## 改善案 1: status.md 層の追加(優先度高)

**問題**: relay は日記(時系列)と knowledge(教訓)の2層だが「現在の状態」を1枚で表す層が
ない。handoff は日記の各セッション末尾に散らばるため、注入ウィンドウ(直近2日分)から
外れた未完了事項は次セッションに届かなくなる。

**案**: レコーダー出力に `===STATUS===` セクションを追加し、`knowledge/status.md`
(名称は要検討)を**追記ではなく毎回上書き**で維持する。内容は「未完了の作業・保留中の
確認・次のアクション」だけを現在形で。モデルには新旧 status を見せて統合させ、
上書き処理自体は relay_recorder.py 側で決定論的に行う(既存の S番号・タイムスタンプ
処理と同じ思想)。SessionStart 注入にも status.md を含める。

**根拠**: memory tool 公式の multisession pattern が progress_log(時系列)と
feature_checklist(現在の状態)の2本立てを推奨。「ASSUME INTERRUPTION: 記録されて
いない進捗は失われる前提で設計せよ」「完了マークは end-to-end 検証後に付ける」。
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool

## 改善案 2: レコーダープロンプトの改善(優先度中・小変更)

**案**: relay_recorder.py の PROMPT_TEMPLATE に2点追加:

1. 冒頭に読者と目的の明示: 「このサマリーは次セッションの Claude が読んで作業を
   継続するためのもの」(公式 compaction のデフォルトプロンプトが目的明示から始まる
   のに倣う。要約の取捨選択が安定する)
2. 「ファイルパス・コマンド・識別子は言い換えずそのまま残す」を明示
   (公式の compaction カスタム instructions 例 "Focus on preserving code snippets,
   variable names, and technical decisions." に相当。現状は "file paths where useful" 止まり)

**根拠**: relay の done/decisions/mistakes/handoff は公式 compaction サマリーの構成
(state/next steps/learnings)とほぼ同型で方向性は正しい。上記2点が差分。
- https://platform.claude.com/docs/en/build-with-claude/compaction

## 改善案 3: thinking のオプトイン活用(優先度中)

**問題/機会**: 現行上位モデルはデフォルトで thinking 表示が omitted のため、jsonl には
空の thinking フィールド+署名しか残らない。Claude Code の `showThinkingSummaries: true`
(~/.claude/settings.json、/config からは設定不可)にすると thinking summary が見える。
thinking の中身が jsonl にあれば、mistakes(なぜ誤った判断をしたか)と decisions
(捨てた案・検討過程)の記録精度が上がる。done/handoff には効かない。

**案**:
1. PROMPT_TEMPLATE に「jsonl に thinking の中身があれば decisions/mistakes の根拠として
   使ってよい。空でも動作は変わらない」を追記(データ駆動で自然にオプトインになる。
   レコーダーは thinking 非依存を維持する — デフォルト設定のユーザーの jsonl では空のため)
2. README に `showThinkingSummaries: true` を推奨として記載: 「mistakes/decisions の
   記録精度が上がる。表示が変わるだけで課金(thinking トークン消費)は変わらない。
   false でも relay は全機能動く」

**実装前の必須検証**: 「`showThinkingSummaries: true` にすると jsonl に thinking summary
の中身が実際に記録される」は X ポスト由来の主張で未検証。true にして1セッション回し、
jsonl に summary が入ることを確認してからプロンプトを改修すること。
(false 時に jsonl の thinking が空なのは確認済み)

> **検証結果 (2026-08-01 実施・主張は正しい。ただし対話モード限定)**
>
> Claude Code 本体 (`~/.local/bin/claude`, v2.1.220) の display 解決関数を確認した:
>
> ```js
> function C5i(){ return eo().showThinkingSummaries ?? false }
> function uUc({explicitDisplay, isNonInteractive, outputFormat, verbose}){
>   if (explicitDisplay) return explicitDisplay;
>   if (!isNonInteractive) return C5i() ? "summarized" : undefined;   // 対話モード
>   if (outputFormat==="text" || outputFormat==="json" && !verbose) return "omitted";
>   return;
> }
> ```
>
> - 設定は実在する: `showThinkingSummaries: v.boolean().optional().describe("Request
>   API-side thinking summaries and show them in the conversation and in the
>   transcript view (ctrl+o).")`
> - バンドル内に「set `thinking.display` to `"summarized"` … **it is still
>   `block.thinking` on a `thinking` block**」の記述があり、レコーダーが読むフィールドに
>   中身が入る。
> - **`-p`(headless・`--output-format text`) は常に `omitted` に固定され、フラグは
>   参照されない。** レコーダー自身が headless で走ることには影響しない。
>
> 補助データ: 全 transcript の thinking ブロック集計では entrypoint で完全に分離した
> (desktop v2.1.181〜2.1.219 で非空1021/空88、cli は全版で非空0/空2645、
> sdk-* は非空0/空412、desktop も現行 v2.1.220 では非空0/空484)。ただし `settings.json`
> の過去版が残っておらず当時のフラグ値を復元できないため、この集計だけでは因果を
> 決められなかった。`claude -p --settings '{"showThinkingSummaries":true|false}'` での
> A/B は上記のとおり headless では差が出ようがなく、無効な試験だった。
>
> → 案3-1・案3-2 とも実装済み。残るは「`summarized` の内容が実際に jsonl に永続化される」
> ことの実地確認のみ (フラグは 2026-08-01 に `~/.claude/settings.json` へ投入済み。
> 過去のデスクトップ由来セッションで非空 thinking が実際に残っているため、内容が届けば
> 永続化されること自体は実証済み)。

**根拠**:
- https://platform.claude.com/docs/en/build-with-claude/thinking
  (display: "summarized" / "omitted" の仕様、omitted 時は空 thinking+署名のみ、課金は同一)
- https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows

## ガードレール(実装しない・してはいけないこと)

- **注入コンテンツに毎回変わる要素(現在時刻・セッションID等)を入れない**。
  relay の SessionStart 注入はセッション中不変でプレフィックスキャッシュに乗っている。
  可変要素を入れると全ターンでキャッシュミスし、同じ内容に毎回フル課金される。
  並び順も「変わりにくい→変わりやすい」(knowledge→日記)の現行順を維持。
  - https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- **structured outputs はレコーダーに使えない**(claude -p から output_config を渡す
  方法が docs に記載なし。--output-format json は封筒だけで schema 強制ではない)。
  現行の区切り文字+寛容パーサが現実解。API 直叩きへの切り替えは「unparseable
  model output での abort が頻発したら」を判断基準に。
  - https://platform.claude.com/docs/en/build-with-claude/structured-outputs
