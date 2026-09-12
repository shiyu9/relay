# tasks

relay は 2026-09-12 に `claude plugin uninstall relay@relay` でアンインストールされた。
**hook は新しく開くセッションから一切動かない。**このリポジトリは claude-memory への移植元として残す
（`export/`）。本体のコードは変更しない。
**`export/` は git 管理外（`.gitignore`・2026-09-13）。**この repo は public なので、11プロジェクトの
tasks 本文を含む `export/migrate_tasks.*` は追跡しない。現物はローカルの `export/` にある。

## relay の撤退

- [x] **第1段・手順1（注入停止）と手順3（生成停止）** — 2026-09-12 に freetalk 側で
      `claude plugin uninstall relay@relay` を実行して完了。`~/.claude/settings.json` の
      `enabledPlugins` から `relay@relay` が消え、`~/.claude/plugins/installed_plugins.json` にも
      無いことを確認済み。hook ごと止まるので `session_start_hook.py` の `build_sections` も
      `pitfalls.md`/`workflow.md` の生成も走らない。`extraKnownMarketplaces` の `relay` は
      移植元として残してある
- [x] **第1段・手順2（`knowledge/current.md`・`knowledge/decided.md` → 各プロジェクトの `tasks.md`）**
      — 2026-09-12 に `--apply` 済み。10プロジェクトへ 46 件（current 由来 17・保留 29）。
      機械変換 89 件のうち 44 件は校正で落とした。正本 `export/migrate_tasks.curated.json`、
      最終 diff `export/migrate_tasks.final.txt`、退避 `C:\claude-projects\_backup\tasks-migration-2026-09-12\`
      （`MANIFEST.txt` に戻し方）。各プロジェクトの tasks.md はわざとコミットしていない

### 第2段（残り）

- [ ] **`knowledge/pitfalls.md` の配布** — 各行に `@dest:` を付けて (a) リポジトリの振る舞い→その
      リポジトリの試験 / (b) AI 操作の罠→`~/.claude/rules` へ配る。`grep -vc '@dest:'` が 0 になったら
      ファイルを削除してよい。**削除は shiori の再発検知改修（claude-memory の `mistakes --since` 約77行）を
      実機で確認してから**——それより先に消すと、再開条件（同種の mistake が別セッションに2回以上）を
      数える者が居なくなる。**待ち: claude-memory の再発検知改修の実機確認**
- [ ] **`knowledge/workflow.md` の削除** — 行ごとに吸収済みを確認してから消す。上と同じく実機確認の後
- [ ] **`knowledge/` の削除（全プロジェクト）** — 上の2つが済んでから。`current.md`・`decided.md` は
      手順2で `tasks.md` へ移送済みなので、消しても失われるものは無い。生成はもう走らないので
      放置しても増えない
- [ ] **context-lint の `extraPaths` から `knowledge/*.md` を外す** — `~/.claude/settings.json` の
      `pluginConfigs."context-lint@context-lint".options.extraPaths` に `knowledge/*.md` が残っている
      （2026-09-12 実測）。`knowledge/` を消す工程と同時に外す。**設定変更はユーザーの承認が要る**
- [x] **`export/` の修正（2026-09-11）の取り込み確認** — 実データ由来の偽陽性を2件直した
      （`mistake_items` の切れ目を箇条書きからラベル「した事」基準へ・`SECTION_RE` に廃止済みの
      `PITFALLS`/`WORKFLOW` を追加）。**2026-09-13 に現物で照合して完了を確認**（報告待ちではなく
      こちらから見た）。`claude-memory/shiori/relay_port.py`・`record_prompt.py` は改行以外の差が0、
      `claude-memory/tests/test_relay_port.py` は差20行がすべて import パスの読み替え。生の sha256 は
      改行が LF→CRLF に化けるため不一致になる（手順は `export/README.md` の「中身」節）
- [x] **着手不能4件を `docs/adr.md` へ移送・push（2026-09-13）** — v0.9.1 の受け入れ確認／spec.html の
      再生成／overwrite 係／epoch のサブディレクトリ由来キーを ADR 1項目に畳み、この節から削除した。
      あわせて未 push だった 9 コミットを push（`a29d2f9..e20dacd`・force なし）。この repo が public
      だと分かったため、`export/` を **履歴ごと** 外してから送った（tip で消すだけでは `02df928` 等の
      コミットから blob が読める）。9 コミットとも origin 未反映だったので force 不要。`export/` のみを
      触る3コミットは空になり消滅し 6+1 コミットに。公開されたのは `.gitignore`・`docs/adr.md`・
      `tasks.md`・`tests/e2e/test_recording.py` の 51 行のみ（`gh api .../contents/export` → 404、
      `git/trees/e20dacd?recursive=1` の `^export/` → 0 件で確認）
- [ ] **`tasks.md`・`docs/adr.md` に残る `export/...` 参照の扱いを決める** — public 側では実体の無い
      パスを指す（`export/migrate_tasks.curated.json`・`export/migrate_tasks.final.txt`・
      `export/README.md`）。ローカルの記録としては正しいので、書き換えると手元の追跡性が落ちる。
      **保留の理由:** どちらを優先するかはユーザー判断。**再開条件:** public の README や ADR を
      外部の人が読む用途が出てきたとき、または `export/` をローカルからも消すと決めたとき
- [ ] **`backup/pre-export-strip-2026-09-13` ブランチの削除** — 履歴書き換え前の 9 コミット（旧 tip
      `100c054`）をローカルに保持している。**再開条件:** `export/*.py` を公開し直す必要が無いと
      確定し、かつ `export/` の現物がローカルに揃っていることを再確認できたとき。それまでは消さない
