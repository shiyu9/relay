# tasks

- [ ] **v0.9.1 の受け入れ確認** — 配信済み（`2a15f68`・cache に `0.9.1`・配信物14件が改行正規化後に一致）。**更新後に新しく開いたセッションで**、次に 1MB 級のセッションが記録されたときに (1) パートの実サイズが全部 40,000B 以下、(2) `short=0`、(3) `~/.claude/relay/log.txt` の `[start]` 行が `<sid8>: ` で始まっていること、を見る。**わざわざ仕込まず、自然に記録される回で確認すれば足りる**（0.9.0 で分割読みそのものは実機で緑・境界が動いただけで内容は分割前とバイト一致を確認済み）

受け入れ試験は **v0.9.0 で完走・4項目すべて緑**（2026-09-03・freetalk 側で実施。6e9d59f3 を replace で記録／part01〜08 の8本すべてが Read され Bash 0回・offset 指定 0件／`short=0`／`reading_tokens=119,884` で予算超過なし／78.7秒）。
- [ ] **spec.html の再生成** — 案C（読み物のパート分割・`===READ===` の照合・`RELAY_HOME`）が仕様書に入っていない。`rally:spec-digest` で再生成する
- [ ] **overwrite 係が旧 current.md をなぞる件（freetalk 5(a)）** — 完了済みの項目を未実施として書き戻した。プロンプトには既に「現在地を過去へ戻さないこと」がある。**再現の現物が1件しかないので保留**。次に同型が出たら、プロンプトではなく機械側で弾く形を設計する
- [ ] **epoch のリポジトリ内サブディレクトリ由来のキー** — `C--claude-projects-relay-plugins-relay` / `-plugins-relay-scripts` / `-tests` の3件。試験の残骸ではなく、リポジトリ内を cwd にして claude を起動した実績なので消していない。要否を判断する

## relay の撤退（2026-09-10 の4者議論で方針合意。着手指示があるまで実装しない）

- [ ] **第1段: 注入停止・移送・生成停止** — (1) SessionStart の注入を止める（`session_start_hook.py:64-89` の `build_sections`）。(2) `knowledge/current.md` と `knowledge/decided.md` を `tasks.md` へ移送する（decided は「保留」節へ。`待ち: <誰の何を待っているか>` を必須欄にする）。(3) `knowledge/pitfalls.md`・`knowledge/workflow.md` の生成を止める。pitfalls は各行に `@dest:` を付けて (a) リポジトリの振る舞い→試験 / (b) AI 操作の罠→`~/.claude/rules` へ配り、`grep -vc '@dest:'` が 0 になったら削除。workflow は行ごとに吸収済みを確認してから削除。**削除（第2段）は shiori の再発検知改修を実機で確認してから**——それより先に消すと、再開条件（同種の mistake が別セッションに2回以上）を数える者が居なくなる
- [ ] **`export/` の修正（2026-09-11）の取り込み確認** — 実データ由来の偽陽性を2件直した（`mistake_items` の切れ目を箇条書きからラベル「した事」基準へ・`SECTION_RE` に廃止済みの `PITFALLS`/`WORKFLOW` を追加）。claude-memory へ `relay_port.py` と `test_relay_port.py` の再コピーを依頼済み。**待ち: claude-memory の再コピー完了報告**
- [ ] **第1段・手順3（注入停止・生成停止）の着手指示待ち** — 手順2（`knowledge/current.md`・`knowledge/decided.md` → 各プロジェクトの `tasks.md`）は 2026-09-12 に `--apply` 済み。10プロジェクトへ 46 件（current 由来 17・保留 29）を追加し、機械変換 89 件のうち 44 件は校正で落とした。正本は `export/migrate_tasks.curated.json`、最終 diff は `export/migrate_tasks.final.txt`、退避は `C:\claude-projects\_backup\tasks-migration-2026-09-12\`（MANIFEST.txt に戻し方）。各プロジェクトの tasks.md は**わざとコミットしていない**（各セッションの仕事。tally の S4 が未コミットとして見せる）。**待ち: freetalk-9d からの手順3（`session_start_hook.py:64-89` の `build_sections` 停止・`pitfalls.md`/`workflow.md` の生成停止）の着手指示**
