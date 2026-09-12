# tasks

- [ ] **v0.9.1 の受け入れ確認** — 配信済み（`2a15f68`・cache に `0.9.1`・配信物14件が改行正規化後に一致）。**更新後に新しく開いたセッションで**、次に 1MB 級のセッションが記録されたときに (1) パートの実サイズが全部 40,000B 以下、(2) `short=0`、(3) `~/.claude/relay/log.txt` の `[start]` 行が `<sid8>: ` で始まっていること、を見る。**わざわざ仕込まず、自然に記録される回で確認すれば足りる**（0.9.0 で分割読みそのものは実機で緑・境界が動いただけで内容は分割前とバイト一致を確認済み）

受け入れ試験は **v0.9.0 で完走・4項目すべて緑**（2026-09-03・freetalk 側で実施。6e9d59f3 を replace で記録／part01〜08 の8本すべてが Read され Bash 0回・offset 指定 0件／`short=0`／`reading_tokens=119,884` で予算超過なし／78.7秒）。
- [ ] **spec.html の再生成** — 案C（読み物のパート分割・`===READ===` の照合・`RELAY_HOME`）が仕様書に入っていない。`rally:spec-digest` で再生成する
- [ ] **overwrite 係が旧 current.md をなぞる件（freetalk 5(a)）** — 完了済みの項目を未実施として書き戻した。プロンプトには既に「現在地を過去へ戻さないこと」がある。**再現の現物が1件しかないので保留**。次に同型が出たら、プロンプトではなく機械側で弾く形を設計する
- [ ] **epoch のリポジトリ内サブディレクトリ由来のキー** — `C--claude-projects-relay-plugins-relay` / `-plugins-relay-scripts` / `-tests` の3件。試験の残骸ではなく、リポジトリ内を cwd にして claude を起動した実績なので消していない。要否を判断する

## relay の撤退（2026-09-10 の4者議論で方針合意。着手指示があるまで実装しない）

- [ ] **第1段: 注入停止・移送・生成停止** — (1) SessionStart の注入を止める（`session_start_hook.py:64-89` の `build_sections`）。(2) `knowledge/current.md` と `knowledge/decided.md` を `tasks.md` へ移送する（decided は「保留」節へ。`待ち: <誰の何を待っているか>` を必須欄にする）。(3) `knowledge/pitfalls.md`・`knowledge/workflow.md` の生成を止める。pitfalls は各行に `@dest:` を付けて (a) リポジトリの振る舞い→試験 / (b) AI 操作の罠→`~/.claude/rules` へ配り、`grep -vc '@dest:'` が 0 になったら削除。workflow は行ごとに吸収済みを確認してから削除。**削除（第2段）は shiori の再発検知改修を実機で確認してから**——それより先に消すと、再開条件（同種の mistake が別セッションに2回以上）を数える者が居なくなる
- [ ] **`export/` の修正（2026-09-11）の取り込み確認** — 実データ由来の偽陽性を2件直した（`mistake_items` の切れ目を箇条書きからラベル「した事」基準へ・`SECTION_RE` に廃止済みの `PITFALLS`/`WORKFLOW` を追加）。claude-memory へ `relay_port.py` と `test_relay_port.py` の再コピーを依頼済み。**待ち: claude-memory の再コピー完了報告**
- [ ] **第1段・手順2（current/decided → tasks.md の移送）の `--apply`** — dry-run まで完了（`export/migrate_tasks.py`・単体試験40件・`export/migrate_tasks.dry-run.txt`・`1e481d6`）。対象11プロジェクト・追加89件（current 由来58・decided 由来31）・tasks.md 新規作成4件。冪等・knowledge 無傷・CRLF 保持は実データの複製で確認済み。**待ち: freetalk-9d からの `--apply` 可否と、判断が要る点5件への回答**（①対象を「current か decided の片方でもあれば」に広げた件 ②current.md の変換が発見的で取りこぼし・誤検出があり未抽出行を全部 dry-run に出してある件 ③近似重複17件を自動スキップしていない件＋印の付かない重複がある件〈kuchu-saiban の5項目〉 ④`待ち:` 欄31件は機械が埋めた値で要確認 ⑤imagegen が未完48件になり tally の6KB上限から溢れる件）
