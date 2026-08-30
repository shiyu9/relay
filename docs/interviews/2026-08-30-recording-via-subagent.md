# インタビュー記録: 記録手段のサブエージェント化と日記の transcript 単位化（v0.5.0）

対象: 資料整理プラン（`freetalk/docs/plan-doc-restructure.md`）第1段 1-1。
日付: 2026-08-29〜30。ラウンド数: 4（＋認識合わせ1回）。
体制: このセッション（relay）が実装、freetalk がプラン全体の整合を持つ。未決事項の一部は
ユーザー指示により freetalk へ回して判断を得た。

## 背景と、着手前に崩れた前提

依頼は4件（記録手段のヘッドレス CLI 離脱・SessionStart を記録の本線に・ナレッジ剪定の発火点・
Stop hook の新設）＋新設2件（`knowledge/current.md` と `decided.md`）。

**着手前の実測でプランの前提が1つ崩れた。**プラン全文に `status` の語が0件で、relay v0.4.0
（2026-08-01）で入った `status.md` 層を認識していない。プランの `current.md`（現在地・上書き型・
`handoff` から蒸留）は `status.md` と同一の層である。

そして `status.md` は一度も生成されていなかった。

1. `/c/claude-projects` 配下17プロジェクトに `status.md` が **0件**（成果物）
2. `~/.claude/relay/log.txt` 1600行に `status.md` の記録が **0件**。同期間の `recorded:` は
   **114回**あり、記録自体は動いている（ログ）

**原因は当初「Haiku が `===STATUS===` を落としている」と診断したが、これは誤りだった**
（下記「訂正2」）。**真の原因は v0.4.0 が配信されていないこと**で、`plugin.json` /
`marketplace.json` のバージョンを `0.3.0` のままにしたため `claude plugin update` が新版を
認識せず、利用者の環境には STATUS 節を知らないコードが載り続けていた。

**バージョン上げ忘れは「ついでに直す些細な点」ではなく、status 層が存在しない理由そのものだった。**

さらに、いま実在する欠陥が1つ。resume で伸びた transcript は台帳のサイズ不一致で再記録され、
**同じセッションをもう一度まるごと要約した新しい `## S<n>` が追記される**
（`tests/test_catchup.py:134` がその振る舞いを固定している）。

## 手順1.5 の軸の表（最終状態）

| 軸 | 値の候補 | 最終 |
|---|---|---|
| 現在地の面 | `status.md` を正 / `knowledge/current.md` へ改名 / 両方 | **`status.md` 廃止・`current.md` 採用**（確定1） |
| `current.md` の粒度 | 現在地の数行 / 未完了一覧 / 2節構成 | **現在地。行数上限なし**（確定15） |
| `decided.md` の維持 | 上書き・剪定あり / 追記のみ | **上書き・剪定あり**（確定16） |
| `decided.md` と ADR の境界 | 見送りのみ / 設計判断も | **再開条件のある見送りだけ**（確定17） |
| 記録の実行主体 | headless CLI / サブエージェント / 併用 | **サブエージェント一本**（確定2） |
| ファイル書き込みの主体 | Python / サブエージェント | **Python**（確定3） |
| 未記録の検知 | 台帳 / 日記から直接 | **日記から直接。台帳廃止**（確定7） |
| 保持の窓 | 7日 / 14日 / 30日 / なし | **relay 側に置かない。transcript の規定に従う**（確定12） |
| 終了の検知 | 30分アイドル / transcript の終端マーカー / SessionEnd の印 | **印 or 30分**（確定23） |
| 日記の見出し | `## S1 10:24` / 時間帯＋id / HTML コメント | **`## S1 19:11-22:30 (124c52dd)`**（確定9） |
| 再記録の扱い | 差し替え / 追記 | **伸びたときだけ丸ごと差し替え**（確定10） |
| 既存の日記 | 触らない / id を付与 | **一切触らない**（確定11） |
| 注入の切り方 | 日数 / 件数 | **直近5エントリ**（確定22） |
| `handoff` の注入 | する / しない | **しない（書くのは従来どおり）**（確定21） |
| handoff とタスクの境界 | 案A / 現状維持 | **現状維持**（確定14。案Aは第4段 4-2 へ） |
| Stop hook の中身 | 空 / 剪定の促し / 記録の取りこぼし | **記録の取りこぼしの再促し**（確定4） |
| 剪定の発火条件 | 常時 / 80行超 | **80行超のときだけ**（委任1） |
| 対象環境 | Windows 検証済み / mac・Linux 未検証 | 変更なし（スコープ外） |

## 質問ラウンドと三分類

### ラウンド1（観点: ④不変条件・③制御フロー・⑦対立軸）

| # | 質問 | 回答 | 分類 |
|---|---|---|---|
| 1 | `current.md` と実装済み `status.md` の関係 | **`status` を廃止して `current` を採用** | 確定 |
| 2 | 既存の headless 経路をどう扱うか | **全面置換（headless を捨てる）** | 確定 |
| 3 | diary / knowledge への書き込みは誰が行うか | **Python が書く** | 確定 |
| 4 | Stop hook は第1段で何を出すか | **発動条件を詰めて実際に動くものを作り試験する** | 確定 |

### ラウンド2（観点: ③状態と順序・④不変条件・⑤解釈の言い直し）

| # | 質問 | 回答 | 分類 |
|---|---|---|---|
| 5 | Stop hook は何を促すか | **記録の取りこぼしの再促し** | 確定 |
| 6 | `status.md` の死因（節の欠落）を繰り返さない機構 | **お任せ** | 委任 |
| 7 | `knowledge/` 配下だと剪定指示が掛かる問題 | **前置きを書き分ける** | 確定 |
| 8 | 未決2（handoff とタスクの境界） | **freetalk に確認して** | 委譲 → 確定14 |

委任6の仮決め: **サブエージェントを2回に分ける。**①が transcript を読んで
`===DIARY===` `===PITFALLS===` `===WORKFLOW===`、②が transcript を読まず
`===CURRENT===` `===DECIDED===`。出力契約を小さくして節の欠落そのものを起こさせない。

### ラウンド3（観点: ⑤解釈の言い直し・②境界・①異常系）— 全問を freetalk へ

ユーザー指示により4問とも freetalk が判断した。

| # | 質問 | 回答 | 分類 |
|---|---|---|---|
| 9 | 「現在地。1つだけ」は数行か一覧か | **現在地。ただし行数上限は設けない** | 確定15 |
| 10 | `decided.md` は上書きか追記か | **上書き・剪定あり。落とした項目は日記に残す** | 確定16 |
| 11 | `decided.md` と `docs/adr.md` の棲み分け | **再開条件のある見送りだけ** | 確定17 |
| 12 | 未記録が複数あるときの本数 | **順に3回。ただし所要時間を実測する** | 確定18 |

### ラウンド4（ユーザーからの設計提案を受けて）

ユーザーが「日記は transcript の要約なのだから、transcript ごとに日記を書けば検知の仕組みは
要らないのでは」と提案。これを受けて7件の抜け漏れを洗い出し、freetalk と相談した。

| # | 質問 | 回答 | 分類 |
|---|---|---|---|
| 13 | 導入直後に遡って記録するか | **遡る** | 確定8 |
| 14 | 日記の見出しの形 | **開始〜終了時刻と id を8桁で出す** | 確定9 |
| 15 | 伸びたときの既存エントリの扱い | **伸びたときだけ書き換える** | 確定10 |
| 16 | id を持たない既存の日記 | **一切触らない** | 確定11 |
| 17 | 発掘の窓 | **relay としては指定せず、transcript の規定に従う** | 確定12 |
| 18 | 日記の注入をどうするか | **handoff を外す。日数ではなく件数で** | 確定21・22 |
| 19 | 注入の件数 | **5件** | 確定22 |
| 20 | セッション終了の検知 | **SessionEnd を印だけの hook として残す**（`claude` を叩かないなら） | 確定23 |
| 21 | `decided.md` から落とした跡の残し先 | **Python が差分を日記に追記** | 確定24 |

### 選ばなかった観点（「なし」判断）

- **②境界の一部**（同時編集）: 書き手が `relay_apply.py` 1つに集約されるため、
  ADR 2026-08-01 が扱った上書き競合が構造的に起きない
- **⑤信頼境界（HTML エスケープ等）**: 出力先が Markdown ファイルのみで、
  外部へ配信する経路を持たない
- **⑥隣接機能への波及**: 第2段以降の作業はプランが明示的に分離済み

## 確定した要件と「誰が・いつ使うか」

| 要件 | 誰が | いつ |
|---|---|---|
| CLI 無しでも記録できる | ヘッドレスを使えない環境の relay 利用者 | セッション開始のたび |
| 1 transcript = 1 日記エントリ | 次のセッションの Claude、および過去を調べる人 | 記録のたび／後日の検索 |
| `current.md`（現在地） | 次のセッションの Claude | セッション開始時（注入の末尾） |
| `decided.md`（見送りと再開条件） | 次のセッションの Claude と、再開条件を判定する人 | セッション開始時／条件が満たされたとき |
| ナレッジ剪定の発火点 | プロジェクトの knowledge を読む全員 | 80行を超えたとき |
| Stop hook（取りこぼしの再促し） | 記録が落ちたことに気づけない利用者 | 注入した手順が実行されなかったとき |
| SessionEnd の印 | 閉じてすぐ開き直す利用者 | セッション終了のたび |
| 日記の注入を件数で切る | 次のセッションの Claude | セッション開始時 |

## 要件整合チェックの結果

**穴が3つ見つかり、ラウンドに戻して解消した。**

1. **依頼3（剪定の発火点）の実現手段が未定義だった。**「サブエージェントに記録と刈り込みを
   一括で任せる」という方針はあったが、①は「区切りテキストを出すだけでファイルを書かない」
   契約なので、上書きである剪定を①に入れられない。→ ③として独立させ、**発火条件（80行超）を
   Python が計算する**。「気づいたら」が「条件を満たしたら」に変わり、診断と処方が一致した
2. **確定16（落とした見送りを日記に残す）が②の契約と矛盾していた。**②は日記を書けない
   （①は先に終わっている）。→ 確定24（Python が差分を日記に追記）
3. **確定20（見出しの終了時刻＝transcript の最後の timestamp）が丸めで壊れる。**見出しは
   分単位、transcript は秒単位で、そのまま比べると常に「伸びた」判定になり**毎起動で同じ
   セッションを差し替え続ける**。→ 分に切り捨てた値どうしで比較する

**さらに、認識合わせで4か所の未定義を埋めた**（下記「委任」参照）。矛盾・使い道の書けない
要件は無し。判定不能(C)は0件。

## spec-readiness の章別判定

| 章 | 判定 | 根拠 |
|---|---|---|
| 1 文書情報 / 2 用語定義 | ○ | 仕様の正は `tests/e2e/`。「エントリ」「印」「受け箱」「ジョブ」を定義済み |
| 3.1 概要 / 3.2 外部との境界 / 3.3 適用場面 | ○ | — |
| 4.1 / 4.2 構成要素と関係 | ○ | — |
| 5 インターフェース仕様 | ○ | — |
| 6 データ仕様 | ○ | — |
| 8 機能仕様 / 9 エラー仕様 | ○ | — |
| 10 非機能要件 | **△** | 「起動時の待ち時間が許容範囲」を試験で押さえられない。**P2 の実測待ち**で、
数値が出るまで受け入れ条件を書けない。判断18が「測る前に妥協案へ逃げない」としているため、
先に基準を決めるのではなく測定を先に置く |
| 11 適用範囲外 / 12 設計判断 | ○ | — |
| 14 未確定事項 | ○ | 案Aの採否（4-2 へ）のみ |

## 確定 Gherkin

```gherkin
Feature: 終了の検知
  Scenario: 閉じてすぐ開き直しても直前のセッションが記録される
    Given セッション A が終わり ended/<cwd>/<A> の印がある
    And A の transcript は印の30秒前に最後の書き込みをした
    When 1分後に新しいセッションを開く
    Then A は記録対象になる

  Scenario: 印が無いときは30分ルールに落ちる
    Given ended に印が無く、A の最後の timestamp が40分前
    Then A は記録対象になる

  Scenario: 走行中の別ウィンドウを要約しない
    Given ended に印が無く、B の最後の timestamp が5分前
    Then B は記録対象にならない

  Scenario: 印のあとに走り続けたら resume とみなす
    Given ended の印があり、その10分後に transcript が更新された
    And 最後の timestamp が5分前
    Then そのセッションは記録対象にならない

Feature: 1 transcript = 1 日記エントリ
  Scenario: エントリはセッション自身の日付のファイルに入る
    Given 8/27 19:11 に始まり 8/27 22:30 に終わった transcript がある
    And 今日は 8/30 である
    Then diary/2026-08-27.md に "## S1 19:11-22:30 (<id8>)" が作られる
    And diary/2026-08-30.md は作られない

  Scenario: 再適用しても二重にならない
    Given そのエントリが既にある
    When 同じ内容をもう一度適用する
    Then そのファイルの "(<id8>)" を含む見出しは1つのままである

  Scenario: 伸びた transcript はエントリを丸ごと差し替える
    Given "## S1 19:11-22:30 (<id8>)" のエントリがある
    And transcript の最後の timestamp が 23:48 になった
    Then 見出しは "## S1 19:11-23:48 (<id8>)" になり、見出しは1つのままである

  Scenario: 同じ分の中の伸びでは再記録しない（丸めによる無限ループの防止）
    Given エントリの終了時刻が 22:30
    And transcript の最後の timestamp が 22:30:59
    Then そのセッションは記録対象にならない

  Scenario: 見出しの終了時刻は transcript の最後の timestamp を分に切り捨てた値
    Given 最後の timestamp が 2026-08-27T13:30:07Z（ローカル 22:30:07）
    Then 見出しの終了時刻は 22:30 であり、記録を実行した時刻ではない

  Scenario: 最終行が timestamp を持たなくても span が取れる
    Given transcript の最終行が type=last-prompt（timestamp なし）
    Then span.end はその手前の timestamp から求まる

  Scenario: 日付をまたぐセッションは開始日のファイルに1つ
    Given 8/29 23:50 に始まり 8/30 01:30 に終わった transcript がある
    Then diary/2026-08-29.md に "## S<n> 23:50-01:30+1d (<id8>)" が入る

  Scenario: またいだ日数が見出しに残る
    Given 8/26 08:14 に始まり 8/29 08:13 に終わった transcript がある
    Then diary/2026-08-26.md に "## S<n> 08:14-08:13+3d (<id8>)" が入る

  Scenario: 日付をまたいだエントリは二度目に記録されない
    Given 上のエントリが書かれている
    Then そのセッションは pending_sessions() に現れない
    # 日数が無いと終了時刻が2日ぶん過去に読まれ、毎起動 replace で再記録される

  Scenario: 15日以上の断絶があれば新しいエントリになる
    Given diary/2026-08-10.md に "## S1 19:11-22:30 (<id8>)" がある
    And 同じ transcript が 8/30 14:03 から 15:40 まで伸びた
    Then diary/2026-08-10.md のエントリは1文字も変わらない
    And diary/2026-08-30.md に "## S<n> 14:03-15:40 (<id8>)" が作られる
    And 新エントリの開始時刻は「前回エントリ終了後の最初の timestamp」である

Feature: 既存の日記に触らない
  Scenario: id の無い見出しは差し替えに使わない
    Given diary/2026-08-29.md に "## S1 10:24"（id 無し）がある
    When 新しいエントリを追加する
    Then "## S1 10:24" の行と本文は1文字も変わらない

  Scenario: relay 以前の手書きの日記を壊さない
    Given diary/2026-08-21.md が "## セッション1（14:01 終了時記録・開始は 8/16）" で始まる
    Then 既存の見出しと本文は変わらず、新しいエントリが末尾に付く

  Scenario: id を持つはずが壊れている見出しはログに残して飛ばす
    Given "## S1 19:11-22:30 (124c52d)"（7桁）がある
    Then その id のセッションは記録対象にならず、relay のログに1行残る

  Scenario: id8 が衝突したらそのセッションを飛ばす
    Given 同じファイルに "(124c52dd)" の見出しが2つある
    Then 差し替えも新規作成も行わず、ログに残す

Feature: 検知（台帳なし・窓なし）
  Scenario: 薄いセッションは記録しない
    Given ユーザー発言が2件（既定の閾値は3）
    Then 記録対象にならず、次の起動でも同じ判定になる（無限に再試行しない）

  Scenario: 1回の起動で3本まで・候補は単調に減る
    Given 条件を満たす未記録 transcript が5本ある
    Then ジョブは終了時刻の新しい順に3本だけ作られる
    When その3本を記録して再度検知する
    Then 残る候補は2本である

  Scenario: relay 側の窓が無い
    Given 25日前に終わった未記録 transcript がある
    Then 記録対象になる

  Scenario: claude CLI が無くても手順が出る
    Given PATH に claude が存在しない、かつ未記録の transcript が1本ある
    Then 標準出力に relay_apply.py を実行する手順が含まれる

Feature: 書き込みの決定性
  Scenario: 区切りの取り違えが起きない
    Given 受け箱に ===PITFALLS=== だけがある
    Then pitfalls.md は末尾に追記される（全文置換されない）
    Given 受け箱に ===PRUNE_PITFALLS=== だけがある
    Then pitfalls.md は全文置換される

  Scenario: 適用済みの受け箱ファイルは消える
    Then 適用後にそのファイルは存在しない

  Scenario: 適用結果が stdout に出る
    Then "diary=" と "pitfalls_lines=" を含む1行が出力される

Feature: 上書き層が生成される（status.md の死因の根治）
  Scenario: ②が求める節は2つだけ
    Then ②のジョブ本文が求める節は ===CURRENT=== と ===DECIDED=== の2つだけである

  Scenario: CURRENT 節が空なら既存を維持する
    Given knowledge/current.md に "- ep07 の再合成" がある、かつ ===CURRENT=== が空
    Then current.md の中身は変わらず、ログに欠落が記録される

  Scenario: 400行の現在地を切り詰めない
    Given ===CURRENT=== が400行ある
    Then current.md は400行になる

  Scenario: 落とした見送りは Python が日記に残す
    Given decided.md に2行あり、===DECIDED=== が1行だけを返した
    Then decided.md は1行になる
    And 当日の日記に "## relay: decided.md から落とした項目" と消えた行が追記される
    And その見出しは注入されない

Feature: 発火の連鎖
  Scenario: 日記を1件も書かなければ②を起動しない
    Given ①が NOTHING_TO_RECORD を返した
    Then stdout の diary は 0 であり、手順は②をスキップする

  Scenario: ①の追記で80行を超えたら③が起動する
    Given pitfalls.md が79行で、①が2行追記した
    Then stdout の pitfalls_lines は 81 であり、手順は③を起動する

  Scenario: 前回の統合から中身が変わっていなければ③は起動しない
    Given pitfalls.md が81行で、③が一度完了している
    Then stdout の merge_due は no である
    When さらに1項追記される
    Then merge_due は yes に戻る

Feature: 統合は項目を消さない
  Scenario: 行数のために削らせない
    Given ジョブの規則に「行以内に収める」は無い
    Then 「項目を削除しないこと」が明記されている

  Scenario: 移設候補は日記に出る（knowledge からは消えない）
    Given ③が MOVE 節に候補を1件挙げた
    Then 日記に "## relay: knowledge/ の移設候補" として書かれる
    And pitfalls.md からその項目は消えていない
    And この見出しは注入の直近5件に数えられない

Feature: 統合は、統合係が見ていない項目を消さない
  Scenario: ジョブ生成後の追記がある剪定は適用しない
    Given prune.md が "古い罠" だけを埋め込んで生成されている
    And そのあと pitfalls.md に "新しい罠" が追記された
    When "古い罠" だけを剪定した全文が返る
    Then pitfalls.md は置き換えられず、"新しい罠" が残る
    And stdout の stale は 1 になる

  Scenario: apply が剪定ジョブを現在の本文で作り直す
    Given prune.md が "新しい罠" を含まない状態で生成されている
    When ①が "新しい罠" を追記して apply が走る
    Then prune.md は作り直され、"新しい罠" を含む

  Scenario: 実ファイルと一致する剪定はそのまま適用される
    Given pitfalls.md がジョブ生成時から変わっていない
    Then 剪定した全文がそのまま書かれ、stale は 0 のまま

Feature: 注入
  Scenario: 日記は件数で切る（日数ではない）
    Given 8/29 に4エントリ、8/28 に1エントリ、8/27 に3エントリある
    Then 注入されるのは新しい順に5エントリで、8/27 のものが1つ含まれる

  Scenario: handoff 節は注入されない
    Given エントリに "### done" と "### handoff" がある
    Then 注入本文に "### done" は含まれ "### handoff" は含まれない
    And diary ファイル本体の "### handoff" は消えていない

  Scenario: id を持たない旧エントリも件数に数える
    Given "## S1 10:24"（id 無し）と "## S2 19:11-22:30 (<id8>)" がある
    Then どちらも注入され、2件として数える

  Scenario: current.md は注入の末尾に来る
    Then 注入順は pitfalls → workflow → decided → 日記 → current である

Feature: 剪定の発火点
  Scenario: 閾値を超えたときだけ剪定ジョブを作る
    Given knowledge/pitfalls.md が81行ある
    Then 剪定ジョブが起動される
    Given pitfalls.md が79行・workflow.md が79行
    Then 剪定ジョブは起動されない

Feature: Stop hook（記録の取りこぼしの再促し）
  Scenario: 未記録が残っていれば促す
    Given pending_sessions が1本返る、直近30分に促していない、stop_hook_active が偽
    Then {"decision":"block"} が出力される

  Scenario: 自分の transcript では促さない
    Given いま走っているセッションの transcript だけがある
    Then 何も出力しない

  Scenario: 自分の block で再開したターンでは再発火しない
    Given stop_hook_active が真
    Then 何も出力しない

  Scenario: 30分クールダウン中は黙る / relay 対象外では干渉しない
    Given 10分前に促した記録がある、または RELAY_DISABLED=1
    Then 何も出力しない

# 以下は人間プローブ（段2c）で見つかった欠陥から足したもの。インタビューの
# 場では出ていない——auto mode の分類器も、記録係の日付の逸脱も、実機で
# 動かすまで見えなかった。

Feature: 分類器に通る呼び出し
  Scenario: 呼び出し文は説明ではなく完成形で出る
    Given 未記録の transcript が1本ある
    Then 「ここから」と「ここまで」に挟まれた本文が3つ出る（記録・現在地・統合）
    And 本文には「手順:」「を Read で読む」「Write で1ファイルだけ書き出す」がある

  Scenario: 呼び出し文が指示書・読む対象・書き先を名指しする
    Then 記録ジョブの本文に <sid>.md と <sid>.jsonl と <sid>.txt が含まれる

  Scenario: ジョブ本文は注意書きに展開されない
    Then 注意書きに ===DIARY=== は含まれない

  Scenario: apply は1行のコマンドで、何も連結しない
    Then relay_apply.py の行に "cd " も "&&" も無く、--cwd がある
    And 注意書きに「`cd` を前に付けない」がある

  Scenario: 拒否されたジョブは再送せず次の起動に回す
    Then 注意書きに「同じ呼び出しを繰り返さないこと」がある
    And 記録しなければ、そのセッションは次の検知でも候補に残る

  Scenario: 現在地と統合の呼び出しも同じ形で出る
    Then overwrite.md と prune.md の本文がそれぞれ1つずつ出る

Feature: ナレッジ項目の日付
  Scenario: 日付を書かずに返された項目にも日付が付く
    Given ===PITFALLS=== が "- 日付の無い学び" だけを返した
    Then pitfalls.md に "- [<セッションの日付>] 日付の無い学び" が入る

  Scenario: モデルが自分で選んだ日付は置き換わる
    Given 項目が "- [2020-01-01] ..." で返った
    Then 日付はセッション自身の日付になり、2020-01-01 は残らない

  Scenario: 遡って記録した分は「掘った日」で刻まない
    Given 2026-01-05 に走ったセッションを今日記録する
    Then その項目の日付は 2026-01-05 である

  Scenario: 日付でない角括弧は消さない
    Given 項目が "- [進行中] 途中の話" で返った
    Then "- [<日付>] [進行中] 途中の話" になる

  Scenario: ジョブ本文は hook が渡す材料だけで組み立つ
    Then RECORD.format(out, transcript, pitfalls, workflow) が KeyError にならない

  Scenario: 空の節が普通だと本文に書いてある
    Then RECORD に「空のまま終わるのが普通です」「言い回しを変えても書かない」
         「日付は Python が付けます」がある

Feature: 導入時点の線（epoch）
  Scenario: 導入したプロジェクトの backlog は1件も出さない
    Given epoch がまだ無く、終了済みの古い transcript が3本ある
    When SessionStart が走る
    Then ナレッジは注入されるが「未記録のセッションが」は出ない
    And epoch ファイルが書かれている

  Scenario: 線より前に終わった transcript は候補にならない
    Given epoch が 8/20 で、transcript が 8/10 11:30 に終わっている
    Then それは記録対象にならない

  Scenario: 線より後に終わった transcript は普段どおり記録される
    Given epoch が 8/1 で、transcript が 8/10 11:30 に終わっている
    Then それは記録対象になる

  Scenario: 線は一度だけ引かれ、二度と動かない
    Given epoch が 8/20 12:00 に引かれた
    When 9/30 に検知が走る
    Then epoch は 8/20 12:00 のままである

  Scenario: 一度線を越えたものが後から外へ落ちることはない
    Given 8/20 に線が引かれ、8/21 に終わったセッションがある
    When 8/22 と 12/31 に検知が走る
    Then どちらでもそのセッションは候補に残る

  Scenario: 線を引いた時点で走っていたセッションは切り捨てない
    Given transcript の開始が線より前、終了が線より後
    Then それは記録対象になる

  Scenario: 線より前の transcript が resume で伸びたら戻ってくる
    Given epoch が 8/20 で、8/10 に終わった transcript が候補から外れている
    When 同じ transcript の末尾が 8/25 まで伸びる
    Then それは記録対象になる

  Scenario: 壊れた線は「全部記録する」に倒す
    Given epoch ファイルの中身が "yesterday" である
    Then 古い transcript も記録対象になる
    And ファイルは黙って書き直されない

  Scenario: 線はファイルを編集して動かせる
    Given epoch が 8/20 で候補が空である
    When ファイルを 8/1 に書き換える
    Then 8/10 の transcript が候補に戻る

  Scenario: 時刻の無い日付だけでも受け付ける
    Given epoch ファイルの中身が "2026-08-20" である
    Then 8/10 の transcript は候補にならない

  Scenario: RELAY_EPOCH=none は線を無効にし、ファイルには触れない
    Given epoch が 8/20 である
    When RELAY_EPOCH=none で検知する
    Then 8/10 の transcript が候補になり、ファイルは 8/20 のままである

  Scenario: RELAY_EPOCH はファイルより優先される
    Given epoch が 8/1 で、RELAY_EPOCH=2026-08-20 が設定されている
    Then 8/10 の transcript は候補にならない

  Scenario: Stop hook も同じ線を読む
    Given epoch が 8/20 で、8/10 の transcript がある
    Then Stop hook は促さない

  Scenario: 線のちょうど上は「前」として扱う
    Given epoch が transcript の末尾 timestamp と同じ瞬間である
    Then それは記録対象にならない

  Scenario: タイムゾーン付きの指定は同じ瞬間に落ちる
    Given RELAY_EPOCH が末尾 timestamp と同じ瞬間を UTC 表記で指している
    Then それは記録対象にならない

  Scenario: 線を書けなかったら backlog は見えたままにする
    Given epoch がまだ無く、書き込みが OSError で失敗する
    Then 古い transcript は候補に残る

Feature: Stop hook が指すジョブ
  Scenario: 促しはジョブ置き場ではなく手順そのものを載せる
    Given 未記録のセッションが1件ある
    Then reason に対象の id8 と relay_apply.py と --cwd がある
    And STOP_NUDGE に {jobs_dir} という差し込みは無い

  Scenario: ジョブ本文は促しに展開されない
    Then reason に ===DIARY=== は含まれない

  Scenario: 組み直したジョブは「いま残っているもの」を名指しする
    Given 起動時に2件分のジョブが書かれ、そのうち1件は記録済みになった
    And 残りのジョブ指示書は15分より古い
    Then reason には残った1件の id8 だけがあり、記録済みの id8 は無い

  Scenario: 渡したばかりのジョブは促しを黙らせる
    Given ジョブ指示書を書いた直後である
    Then Stop hook は何も出さない

  Scenario: 猶予を過ぎたら促しが戻る
    Given ジョブ指示書が15分より古い
    Then Stop hook は促す

  Scenario: in-flight の判定はジョブ組み直しより前に行う
    Given ジョブ指示書が15分より古い
    When Stop hook が促す
    Then 促しは出て、かつそのジョブ指示書は書き直されている

  Scenario: 実行中のジョブは他人の促しで触られない
    Given 1件が15分より古く、もう1件は書いたばかりである
    When Stop hook が促す
    Then 書いたばかりのジョブ指示書の更新時刻は変わらない

Feature: 現在地の材料
  Scenario: 現在地ジョブは今回の分だけでなく直近の日記も見る
    Given 8/29 の日記があり、今回記録するのは 8/10 のセッションだけである
    Then overwrite.md は 8/10 と 8/29 の両方を材料に挙げる
```

## 技術選定

**標準ライブラリのみ。外部依存なし＝脆弱性チェック対象なし。**`docs/packages/` は作らない。
使うのは Claude Code の SessionStart / SessionEnd / Stop hook と Task ツール。

捨てた案:

- **transcript の終端マーカーで終了を判定する** — 実測で不成立。60分以上更新のない307本の
  最終行の型を数えたところ `last-prompt` 264本（86%）に対し残り43本が
  `atis-latch` `mode` `cost-state` `file-history-snapshot` など14種類にばらけた。しかも
  `last-prompt` はセッション中に31〜37回書かれる型で、走行中でもターンの合間に最終行になる
- **`~/.claude/relay/ledger/` を残す** — 日記自体が同じ情報を持つので二重管理になる
- **保持の窓を relay 側に置く** — 境界があると「窓の外に取り残される」経路が生まれる
  （抜け漏れ A・B・C・E は全部これが原因だった）

## 判定手段の割り当て

| 項目 | 判定 |
|---|---|
| 検知の全条件（終了・伸び・断絶・薄い・上限） | **A-全域**（`pending_sessions()` が純関数。任意の入力で真偽が決まる） |
| 見出しの生成とパースの往復一致 | **A-全域** |
| 既存行の不変（適用前後で id 無しの行が変わらない） | **A-全域** |
| 区切りの取り違え（追記 vs 全文置換） | **A-サンプル**（列挙した節名でのみ検証） |
| `relay_apply.py` の stdout 形式・終了コード | **A-サンプル** |
| Stop hook の `should_nudge()` | **A-全域** |
| 注入の件数・順序・handoff 除去 | **A-全域** |
| 本体が注入手順を実行するか | **B**（P1） |
| 起動時の待ち時間 | **B**（P2。10章が△なのはここ） |
| `current.md` が「現在地」になっているか | **B**（P3） |
| 落とした根拠が追えるか | **B**（P4） |
| 見出しに id が入った日記の読みやすさ | **B**（P5） |
| handoff を外したぶんを `current.md` が埋めているか | **B**（P6） |
| 印による即時検知 | **B**（P7） |

**C（判定不能）は0件。**

## 人間プローブ計画

| # | 見せるもの | 見て判断すること |
|---|---|---|
| P1 | `relay-test/` で新規セッションを開き、本体が①→apply→②→apply を実行した記録 | 注入した手順を本体が実行したか |
| P2 | 起動から記録完了までの経過時間。1回あたりと、3本＋現在地の4回ぶんの合計 | 起動時の待ち時間が許容できるか。導入直後の遡り（`freetalk` なら22回の起動）が現実的か |
| P3 | 生成された `current.md` の全文 | 「現在地」になっているか。未完了の網羅一覧に化けていないか |
| P4 | `decided.md` と、同じ日の日記の「落とした項目」節 | 落とした根拠が追える形になっているか |
| P5 | 見出しに id と時間帯が入った日記の実物 | 読んで邪魔にならないか。8桁で足りるか |
| P6 | SessionStart が実際に注入した全文 | handoff を外したぶんを `current.md` が本当に埋めているか |
| P7 | セッションを閉じて1分後に開き直したときの `log.txt` | 印による即時検知が効いているか |

## 判定手段が未定(C)のまま実装に入る項目

なし。

## 委任（AI が仮決めして明記した内容）

1. **死因の根治** = サブエージェント2分割（①記録／②現在地・見送り）
2. **剪定の発火条件** = `pitfalls.md` か `workflow.md` が80行超のときだけ③を起動
3. **`RELAY_MODEL`** は維持し、意味を「サブエージェントに指定するモデル」に読み替える
4. **`RELAY_KEEP_API_KEY` と `build_recorder_env` を削除**（下記「訂正」参照）
5. ~~**既存 `status.md` は削除しない。**書かない・注入しないだけ~~ → **取り下げ**（訂正2）。
   v0.4.0 が配信されていないため、利用者の環境に `status.md` は存在しない。移行の考慮は不要
6. **サブエージェントの出力先**は `~/.claude/relay/inbox/`
7. **試験の置き場所** — 振る舞い仕様は `tests/e2e/`
8. **`plugin.json` / `marketplace.json` を `0.5.0` へ**（v0.4.0 を飛ばす）
9. **確定13 の「15日」の起点** = 前回エントリの終了時刻 → 今回の活動の終了時刻の断絶
10. **打ち切り新エントリの開始時刻** = 前回エントリの終了時刻より後の最初の `timestamp`
11. **手書き・旧形式エントリの並べ替え順** = ファイルの日付が主キー、ファイル内の出現順が従
12. **壊れた見出し** = ログに残して**その id を飛ばす**（無いものとして扱うと新規作成に回り重複する）

## 保留

なし。案Aの採否のみ第4段 4-2 へ持ち越し（確定14）。

## 訂正2: 「Haiku が `===STATUS===` を落としている」は誤診断だった

**2026-08-30、freetalk の指摘を受けて実物で検証し、覆した。**

- インストール済みの relay は **0.3.0**（`installed_plugins.json` の `gitCommitSha` が
  `ce80cae`、`lastUpdated` が 2026-07-27）で、**v0.4.0 以降更新されていない**
- その `relay_recorder.py:95` は `re.split(r"===(DIARY|PITFALLS|WORKFLOW|END)===", text)`。
  **STATUS 節の指示も解析もそもそも無い**
- したがって `last_run.log` に `===STATUS===` が無いのは当然で、**Haiku の挙動の証拠にならない。**
  私が読んだ `last_run.log` は imagegen の実行結果＝0.3.0 のものだった

**当初ここに「relay リポジトリ内ではリポジトリ版のフックが動いている」と書いたが、それも誤り
だった**（2026-08-30 中に再訂正）。根拠として「0.3.0 の `PREAMBLE` に `status.md` の段落は
無いのに、このセッションに注入された `PREAMBLE` には入っていた」と書いたが、**注入された現物を
読み直すとその段落は無い。**リポジトリのソース（`session_start_hook.py:54`）で読んだ文面を、
注入された現物と取り違えていた。`~/.claude/plugins/marketplaces/relay` のクローンも `ce80cae`
＝v0.3.0 で、`status.md` の段落は0件。

**したがって STATUS 対応版のレコーダーは一度も走っていない。**2026-08-01 の relay 自身の記録も
0.3.0 によるもので、`status.md` が書かれなかったのは当然である。**死因は「配信されていない」
の一点で、モデルの挙動については何も分かっていない。**

**配信前に新コードを試す手段は `claude --plugin-dir <ディレクトリ>` である**（`claude --help`
で実在を確認。「Load a plugin from a directory or .zip for this session only」）。`settings.json`
も marketplace 登録も要らず、セッション限りで作業ツリー版を読み込む。`launch-remote.ps1` が
`-PluginDir` で対応済み。**プローブは `relay-test/` を `--plugin-dir` 付きで起動して行う。**

**設計への影響はほぼ無い。**

- `status.md` 廃止・`current.md` 採用（確定1）は変わらない
- **移行の考慮は不要になった。**v0.4.0 が誰にも届いていない以上、利用者の環境に `status.md` は
  存在しない。委任5（既存 `status.md` は削除しない・README に1行）は**取り下げる**
- 確定6（サブエージェント2分割）と確定18（順に呼ぶ）は、**根拠が「実証された死因」から
  「出力契約は小さいほうが安全」に弱まる**が、害が無いので維持する
- **配信の確認を検証手順に追加する。**`plugin.json` と `marketplace.json` の両方を上げ、
  push 後に `installed_plugins.json` の version が実際に変わることまで見る。
  v0.4.0 が消えたのはここを見なかったためで、同じ失敗を繰り返さない

## 訂正1: ADR 2026-07-25 の「401」という記述自体が誤り

`RELAY_KEEP_API_KEY` を消す理由を書くときに、ADR 2026-07-25 の
「無効キーが残った環境で記録係が401で沈黙死する」をそのまま引かないこと。

2026-08-12 に訂正されている（`~/.claude/projects/C--claude-projects-freetalk/memory/anthropic_api_key_invalid.md`）。
**キーは有効で、実際に課金される。**kuchu-saiban で約$14 を消費した。`exited 129` のログは
2026-07-25 に実在するが、その解釈が誤っていた。

正しくは「`ANTHROPIC_API_KEY` はサブスク認証（OAuth）より常に優先され、`-p` では承認プロンプトも
出ずに使われる。外す以外の回避策がない」。そして v0.5.0 では relay が子プロセスの `claude` を
起動しなくなるため、**危険が世界から消えたのではなく、relay の責任範囲から出た。**

## 1行メモ（観点漏れ候補）

- **（自認）原因を1つ手前で取り違えた。**`current.md` の粒度を論じるとき「一覧は堆積して腐る／
  行数を小さく固めれば防げる」と読んだが、freetalk の指摘で誤りと判明。imagegen `tasks.md` の
  `## 現在地` は **399行**（3〜401行・`2026-08-26 時点`）あって腐っておらず、腐ったのは同じ
  ファイルの未完了 `[ ]` 45件のうち43件だった。**分かれ目は上書きされるかどうかで、行数でも
  一覧かどうかでもない。**実物を1つ開けば分かることを、推論で埋めていた
- **（自認）確定20 を書いた時点で、丸めの不一致に気づいていなかった。**「見出しの終了時刻＝
  transcript の最後の timestamp」と書きながら、見出しが分単位・timestamp が秒単位であることを
  突き合わせていない。**単位の異なる2つの値を「同じ」と書いたら、必ず突き合わせる**
- **（自認・最も重い）配信されているコードと、リポジトリのコードを同一視した。**「17プロジェクトで
  `status.md` が0件」「ログに記録0件」「`last_run.log` に `===STATUS===` が無い」の3つを並べて
  「Haiku が節を落としている」と断定したが、**そのログを出したのはリポジトリ版ではなく
  インストール済みの 0.3.0** だった。0.3.0 のプロンプトには STATUS 節が存在しないので、
  観察は診断を1ミリも支えていない。**「動いているコードはどれか」を確かめる前に、
  出力からコードの挙動を推論した。**プラグインやフックのように配信物と作業ツリーが分かれる
  対象では、**実行主体の版を先に固定する**
- **（自認）同じ誤りを、訂正の最中にもう一度やった。**訂正2を書くとき「relay リポジトリ内では
  リポジトリ版が動く」と主張し、根拠に「注入された `PREAMBLE` に `status.md` の段落が入っていた」
  を挙げた。**現物を読み直すとその段落は無い。**リポジトリのソースで読んだ文面を、注入された
  現物と取り違えていた。1度目とまったく同じ「観察したつもりの記憶で断定する」形で、
  **しかも1度目を訂正している最中に起きている。**「現物を見る」は、見た記憶ではなく
  **その場でもう一度開くこと**を指す
- **（自認）「30分アイドル」を所与として設計に組み込んでいた。**ユーザーの指摘で初めて
  「閉じてすぐ開き直すと引き継ぎが落ちる」に気づいた。SessionEnd を廃止した時点で、
  30分ルールが唯一のゲートになっていたのに、その帰結を検討していない。**ある仕組みを消したら、
  それが担っていた役割を引き受ける側の負荷を必ず見直す**

---

## 標準試験バッテリー適用結果

実装後に追記。E2E 45件（`tests/e2e/test_recording.py`）＋ バッテリー 32件（`tests/test_battery.py`）＝ **77件**。

| カテゴリ | 適用 | 試験名 / 該当なしの理由 |
|---|---|---|
| 1 境界値 | あり | `test_no_transcripts_at_all` `test_exactly_the_minimum_user_messages` `test_one_below_the_minimum` `test_the_cap_is_three_not_four` `test_the_limit_can_be_raised_by_the_caller` `test_fourteen_days_still_replaces_and_fifteen_resumes` `test_zero_and_the_limit_and_one_past_it` `test_seconds_are_floored_never_rounded_up` `test_an_id_of_the_wrong_length_is_not_one_of_ours` |
| 2 入力異常系 | あり | `test_an_inbox_file_with_no_delimiters_is_counted_and_dropped` `test_a_diary_section_with_no_transcript_behind_it` `test_a_transcript_with_no_timestamps_has_no_span` `test_unparsable_json_lines_are_stepped_over` `test_non_ascii_survives_the_round_trip` |
| 3 データ永続化の頑健性 | あり | `test_a_hand_edited_diary_is_still_parsed` `test_a_broken_marker_directory_falls_back_to_the_idle_rule` `test_an_unreadable_diary_file_does_not_raise` `test_a_broken_id_heading_is_logged_and_its_session_skipped` `test_a_colliding_id_skips_that_session` |
| 4 冪等性・二重実行 | あり | `test_detection_repeated_gives_the_same_answer` `test_applying_the_same_bullets_twice_adds_them_once` `test_applying_an_empty_inbox_is_a_no_op` `test_applying_twice_does_not_duplicate_the_entry` |
| 5 状態遷移の違反 | あり | `test_new_then_replace` `test_a_gap_of_fifteen_days_gets_a_fresh_entry` `test_writes_long_after_the_marker_mean_the_session_resumed` |
| 6 往復一致 | あり | `test_every_heading_we_write_parses_back_to_what_we_meant`（生成入力50本で見出しの書き出しと解析を突き合わせ） |
| 7 不変条件 | あり | `test_lines_outside_our_own_entries_are_never_altered`（**生成入力**。見出しの形を列挙ではなく組み合わせで作り、他人の行がバイト単位で残ることを検査）`test_an_empty_overwrite_section_can_never_erase_the_page` |
| 8 出力契約 | あり | `test_the_summary_has_every_field_the_procedure_reads` `test_it_exits_zero_even_when_everything_is_wrong` `test_the_summary_is_one_line` |
| 9 環境依存 | あり | `test_japanese_reaches_stdout_even_on_a_cp932_console` `test_utc_timestamps_become_local_times` `test_a_mangled_tz_does_not_shift_the_clock` `test_relay_scope_still_keeps_other_projects_out` |
| 10 スケール smoke | あり | `test_a_large_transcript_is_never_read_end_to_end`（2MB 超の transcript で `_all_timestamps` を呼んだら失敗させ、両端 64KB だけで span が取れることを確認） |

**A-サンプルのまま残したもの**: 区切りの取り違え（`===PITFALLS===` と `===PRUNE_PITFALLS===`）は
節名を列挙して検査している。性質に書き直すなら「区切り名の集合に対して、追記系と置換系が
交わらない」だが、節名は6つで固定されており列挙で尽きるため書き直していない。

**試験の環境隔離について1件。**最初の実行で E2E の大半が「フックの出力が空」で落ちた。原因は
開発環境の `RELAY_SCOPE=C:\claude-projects` で、一時ディレクトリの試験プロジェクトが
`in_scope` に弾かれていた。**試験が開発者の環境設定に依存していた**ので、`RelayCase.setUp` で
`RELAY_*` を環境から落とすようにした（カテゴリ9）。

## 静的検査の結果

```
python "C:/Users/winba/.claude/plugins/cache/rally/rally/0.5.0/scripts/rally_test_audit.py" --root .
  1. 記録にあるが tests/ に無いテスト名: 0 件
  2. assert の無い test 関数: 0 件
  3. 理由の無い skip: 0 件
  4. 例外の握り潰し: 9 件
```

4 は9件すべて**意図したフェイルオープン**で、直さず残す。理由を1件ずつ記す（コード側にも
同じ内容を1行コメントで置いた）。

| 箇所 | 残す理由 |
|---|---|
| `relay_apply.py` 受け箱ファイルの削除 | 消せなくても、次の適用で同じ書き込みが冪等に繰り返されるだけ。未記録なら検知が拾い直す |
| `relay_common.log()` | フックがログを書けないことで失敗してはいけない |
| `relay_common._all_timestamps()` | 途中まで読めた分を返す。1つも無ければ呼び出し側が「span 無し」として扱う |
| `relay_common.local_now()` の ctypes | ctypes が無い・Windows でない環境では下の移植版の時計に落ちる |
| `relay_common.force_utf8()` | 既にラップ済み・リダイレクト済みのストリームには直すものが無い |
| `relay_detect._ended()` の印の stat | 印が無い・読めないときは30分ルールに落ちる。これが設計そのもの |
| `relay_stop_hook._cleanup()` 内側 | 消せなかった古い state ファイル1つは無害 |
| `relay_stop_hook._cleanup()` 外側 | 掃除は best-effort。セッションに伝播させない |
| `session_end_hook` の最上位 | セッション終了を止めない。印が書けなくても30分ルールが覆う |

## 煙試験（実装を1か所壊して red を確認）

`relay_detect.py:129` の `floor_minute(end) <= entry.end` から丸めを外し
`end <= entry.end` にしたところ、E2E の
**`test_growth_inside_the_same_minute_does_not_retrigger` が red**（`[] != [Pending]`）。
見出しは分単位・transcript は秒単位なので、丸めを外すと差し替え判定が毎回真になり
**毎起動で同じセッションを記録し直す**——その一点を突く試験が実際に効いていることを確認して、
元に戻した（77件 green）。

---

## 追補: v0.6.0（導入時点の線と、Stop hook の2つの隙間）

依頼（2026-08-30 夜・ユーザー直接決定、freetalk-e3 経由）:「プラグイン導入・更新時点より古い
transcript は記録済み扱いにして記録対象から外す」一括初期化を足す。同時に、同じ節に記録されていた
既知の隙間3件を同便で扱うかどうかを判断する。**3件とも同便で扱うと判断した**——1と2は Stop hook
そのものの正しさで epoch とは独立に壊れており、3は epoch で backlog が消えても resume 判定の
たびに再発する設計の穴だったため。

設計判断は `docs/adr.md` の 2026-08-30 の11項目に記した。要点だけ:

- **線は一度きり。**更新のたびに引き直すと「保持の窓」になり、この設計が明示的に捨てた形に戻る
- **末尾 timestamp と比べる。**線を跨いで走っていたセッションと、後から resume したものを守る
- **フェイルオープン。**壊れた線は「全部記録する」に倒す。人が編集する平文だから
- **判定は Python。**プロンプトには一切置かない
- **in-flight 判定はジョブ組み直しより前。**順序が逆だと自分で猶予をリセットする

### 標準試験バッテリー適用結果（追補分）

| 区分 | 追加した観点 |
|---|---|
| 境界 | 線のちょうど上（前として扱う）・1分手前・猶予15分のちょうど端 |
| 異常系 | 壊れた epoch ファイル・空のファイル・span の無い transcript・空のリスト |
| 環境 | `RELAY_EPOCH` による上書きと無効化・タイムゾーン付き指定の換算 |
| 耐久性 | epoch が書けない（OSError）ときに backlog を見せたままにする |
| 冪等 | 検知を3回繰り返しても線が動かない |

試験 98件 → **130件**（e2e +22・バッテリー +10）、全 green。

### 静的検査の結果

```
python "C:/Users/winba/.claude/plugins/cache/rally/rally/0.6.0/scripts/rally_test_audit.py" --root .
  1. 記録にあるが tests/ に無いテスト名: 0 件
  2. assert の無い test 関数: 0 件
  3. 理由の無い skip: 0 件
  4. 例外の握り潰し: 10 件
```

4 は前回の9件に1件増えた。増えた分も意図したフェイルオープンで、直さず残す。

| 箇所 | 残す理由 |
|---|---|
| `relay_common.jobs_in_flight()` | ジョブ指示書が無い＝そもそも渡されていない。`stat` が失敗したら「実行中ではない」に倒すのが安全側（促しが出るだけで、黙って記録が飛ぶことはない） |

2 は前回「0件」と記録していたが、実際には
`test_the_job_body_needs_only_what_the_hook_hands_it` が assert を持たないまま残っていた
（`format` が KeyError を投げることだけを頼りにしていた）。本文が組み上がったことを
1行で確かめる assert を足して解消した。

### 煙試験（実装を6か所壊して red を確認）

| 壊した箇所 | 結果 |
|---|---|
| `relay_detect` の epoch 判定を削除 | **9 failed** |
| `session_epoch` を毎回引き直すように変更 | **41 failed** |
| 壊れた epoch をフェイルクローズ（現在時刻）に変更 | **1 failed** |
| `jobs_in_flight` が常に空集合を返すように変更 | **4 failed** |
| Stop hook の in-flight 判定をジョブ組み直しの後ろへ移動 | **8 failed** |
| 現在地ジョブの材料から直近日記の合流を削除 | **1 failed** |

6件すべてで red を確認し、戻して 130件 green。
