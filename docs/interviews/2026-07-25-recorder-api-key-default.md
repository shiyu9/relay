# インタビュー記録: recorder の API キー既定を反転（v0.2.0）

日付: 2026-07-25
要望: recorder 起動時の `ANTHROPIC_API_KEY` の扱いを、オプトイン除去から**既定で除去**に反転する。

## 発端

freetalk で「relay はちゃんと動いているか」を確認したところ、`~/.claude/relay/log.txt` に
`2026-07-25T09:49:45 [rec] abort: claude exited 129` が見つかった。環境に残った無効な
`ANTHROPIC_API_KEY` を recorder が引き継ぎ、401 で沈黙死していた。同日 09:58 に
`RELAY_CLEAR_API_KEY=1` を settings.json へ追加した直後（10:04）は成功しており、
失敗→成功がこの設定投入を挟んで切り替わっている。

## 質問ラウンド

### ラウンド 1（観点④不変条件・①入力の異常系・③制御フロー）

| 質問 | 回答 | 分類 |
|---|---|---|
| オプトアウト手段を残すか | `RELAY_KEEP_API_KEY=1` を新設 | 確定 |
| 旧 `RELAY_CLEAR_API_KEY` の扱い | 完全撤去 | 確定 |
| 除去対象の範囲 | `ANTHROPIC_API_KEY` のみ（`ANTHROPIC_AUTH_TOKEN`・Bedrock/Vertex 系には触らない） | 確定 |
| 認証失敗時の挙動 | abort ログに原因ヒントを追記 | 確定 |

### ラウンド 2（検証方針・設計・ADR・受け入れ条件）

| 質問 | 回答 | 分類 |
|---|---|---|
| テスト基盤の無いリポジトリでどう検証するか | unittest を新設し、加えて launch-remote で実機セッションを立てて日記生成まで試験する。実行は極力 Claude、ユーザー操作が要る箇所は手順提示 | 確定 |
| env 加工を純関数へ切り出すか | 「おまかせします」 | **委任** |
| `docs/adr.md:10` の既存判断記録をどうするか | 旧行を書き換える | 確定 |
| 実機で何をもって合格とするか | unittest pass + 実機で日記生成 + キー保持側も実機確認 | 確定 |

### ラウンド 3（launch-remote の干渉・試験環境・反映順序）

回答から穴を検出して提示した: `launch-remote.ps1` は起動時に自ら `ANTHROPIC_API_KEY` を削除するため、
そのセッションでは親環境にキーが無く、relay の除去ロジックは no-op になる。`RELAY_KEEP_API_KEY=1` も
保持する対象が存在せず試験が成立しない。**launch-remote だけで試験を組むと、今回変更した分岐が
一度も実行されない。**

| 質問 | 回答 | 分類 |
|---|---|---|
| launch-remote の干渉をどうするか | 「ちょっと議論したい」 | **保留**（ラウンド 4 で解消） |
| 実機試験の場所 | 試験用プロジェクトを使い回す（リモートは初回認証があるため毎回新規は不可）。毎回クリーンアップ | 確定 |
| 反映と push の順序 | ローカル検証 → 合格後に push | 確定 |
| キー保持側の合格条件 | キーの有効性で分岐。無効なら abort ログ、有効なら日記生成まで確認 | 確定 |

### ラウンド 4（保留の解消・環境変数そのものの扱い）

ユーザーからの問い「launch-remote はなぜ API キーを削除するのか。削除ではなく、
デフォルトでサブスクリプション認証を有効にすればいいのでは」に対し、推測を避けて実地調査した。

**調査結果（この案は Claude Code の仕様上できない）**:

1. 公式ドキュメント（env-vars）: `ANTHROPIC_API_KEY` は
   「When set, this key is used instead of your Claude Pro, Max, Team, or Enterprise subscription
   **even if you are logged in**. In non-interactive mode (`-p`), the key is always used when present.
   ... To use your subscription instead, run `unset ANTHROPIC_API_KEY`」
   → サブスクを優先させる設定は存在しない。公式が示す唯一の回避策が `unset` = 削除そのもの。
   recorder は headless（`-p`）なので承認プロンプトすら出ない。
2. `claude auth status` の実測: `authMethod: "claude.ai"` で login 済みなのに
   `apiKeySource: "ANTHROPIC_API_KEY"`。
3. `claude config list` の実測:
   `⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY ... takes precedence over your
   claude.ai login` / `Failed to authenticate. API Error: 401 API key is invalid.`

→ launch-remote の削除実装は妥当であり、relay を既定除去へ変える方針も公式作法に沿う。

| 質問 | 回答 | 分類 |
|---|---|---|
| 無効な環境変数自体を消すか | relay の変更だけやる（環境変数は残す） | 確定 |
| 実機試験の組み方（保留の解消） | 通常セッションの確認も E2E 項目に入れるが、実施はユーザー判断。未実施なら層 A は unittest だけで許容 | 確定 |

### ラウンド 5（版上げ・スコープ外・試験環境の具体）

| 質問 | 回答 | 分類 |
|---|---|---|
| バージョン番号 | v0.2.0 | 確定 |
| 合格・push 後の入れ直し | `/plugin` で正規経路から入れ直す | 確定 |
| スコープ外の確定 | `session_start_hook.py`・`launch-remote.ps1`・ユーザー環境変数の 3 件は触らない | 確定 |
| 試験環境 | `C:\claude-projects\relay-test` を新設。毎回 `diary/`・`knowledge/` を削除。`log.txt` は共用のため削除せず開始時刻以降の行を見る | 確定 |

### ラウンド 6（追加注文）

- 過去分の日記を読み取る試験を E2E でやる場合は、過去 diary は毎回ダミーを用意する（確定）

## 「なし」と判断した観点

- **②境界** — 0 件/1 件/上限といった量的境界が生じる変更ではない
- **③状態と順序**（一部） — 再入は既存の `RELAY_HOOK_ACTIVE` ガードが担保。二重実行・同時実行の新たな論点なし
- **①入力の異常系**（一部） — 非 ASCII・永続化ファイルの外部編集は本変更の対象外

## 要件整合チェック

- **矛盾**: 1 件検出・解消。ラウンド 2 の「キー保持側も実機確認」がラウンド 4 で
  「実施は任意、未実施なら unittest で担保」に緩和された。後の回答を有効とし、
  E2E 項目には残したうえで「実施は任意」と明記した。
- **抜け**: 確定 19 件・委任 1 件・保留 0 件をすべてプランへ反映。
- **穴**: 6 観点の最終見直しで 1 点発見。既定除去に変えると認証失敗の主因が
  「無効キー」から「サブスク未ログイン」へ移るため、abort ログのヒントは
  `RELAY_KEEP_API_KEY=1` の案内だけでは不足し、`claude auth status` の確認も併記が必要。

## 確定した Gherkin

```gherkin
Feature: recorder 起動時の ANTHROPIC_API_KEY の扱い

  Scenario: 既定ではキーを除去する
    Given ANTHROPIC_API_KEY が環境にある
    And RELAY_KEEP_API_KEY が設定されていない
    When SessionEnd hook が recorder 用の環境を作る
    Then その環境に ANTHROPIC_API_KEY は含まれない

  Scenario: RELAY_KEEP_API_KEY=1 なら保持する
    Given ANTHROPIC_API_KEY が "sk-ant-xxx" である
    And RELAY_KEEP_API_KEY が "1" である
    When SessionEnd hook が recorder 用の環境を作る
    Then その環境の ANTHROPIC_API_KEY は "sk-ant-xxx" のままである

  Scenario: "1" 以外の値は保持と見なさない
    Given ANTHROPIC_API_KEY が環境にある
    And RELAY_KEEP_API_KEY が "true" である
    When SessionEnd hook が recorder 用の環境を作る
    Then その環境に ANTHROPIC_API_KEY は含まれない

  Scenario: 旧変数は結果に影響しない
    Given ANTHROPIC_API_KEY が環境にある
    And RELAY_CLEAR_API_KEY が "1" である
    And RELAY_KEEP_API_KEY が設定されていない
    When SessionEnd hook が recorder 用の環境を作る
    Then その環境に ANTHROPIC_API_KEY は含まれない

  Scenario: キーが元々無くても壊れない
    Given ANTHROPIC_API_KEY が環境に無い
    When SessionEnd hook が recorder 用の環境を作る
    Then 例外は発生せず ANTHROPIC_API_KEY を含まない環境が返る

  Scenario: 再入ガードが必ず立つ
    Given 任意の環境
    When SessionEnd hook が recorder 用の環境を作る
    Then その環境の RELAY_HOOK_ACTIVE は "1" である

  Scenario: 呼び出し元の環境を壊さない
    Given ANTHROPIC_API_KEY を含む辞書 base
    When base から recorder 用の環境を作る
    Then base の ANTHROPIC_API_KEY はそのまま残っている

  Scenario: 他の relay 設定は素通しする
    Given RELAY_SCOPE と RELAY_MODEL が設定されている
    When SessionEnd hook が recorder 用の環境を作る
    Then 両者は同じ値のまま含まれている

  Scenario: 記録失敗時にログへ原因ヒントを出す
    Given recorder が起動し claude が非ゼロで終了する
    When recorder が abort を記録する
    Then log.txt の行に終了コードと claude auth status の案内と RELAY_KEEP_API_KEY=1 の案内が含まれる

  Scenario: 既定設定で日記が生成される（通常セッション・任意）
    Given relay-test の diary/ と knowledge/ が空である
    And ANTHROPIC_API_KEY が環境にあり RELAY_KEEP_API_KEY は未設定である
    When 通常セッションで 3 往復以上会話して終了する
    Then log.txt に recorded: が記録される
    And relay-test/diary/<当日>.md が生成される

  Scenario: キー保持側は認証状態どおりの結果になる（通常セッション・任意）
    Given RELAY_KEEP_API_KEY=1 が設定されている
    When 通常セッションで 3 往復以上会話して終了する
    Then claude auth status が無効キーを示すなら log.txt に abort と原因ヒントが出て日記は生成されない
    And 有効キーを示すなら日記が生成される

  Scenario: リモートセッションでも日記が生成される（launch-remote）
    Given relay-test の diary/ と knowledge/ が空である
    When launch-remote で起動し 3 往復以上会話して終了する
    Then relay-test/diary/<当日>.md が生成される

  Scenario: 過去日記の読み取りを試験する場合はダミーを使う
    Given relay-test/diary/ に試験用ダミー日記を配置する
    When セッションを開始する
    Then 注入内容にダミーの記述が含まれる
```

## 技術選定

- **採用**: Python 標準ライブラリの `unittest` のみ。実行は `python -m unittest discover -s tests`
- **外部依存**: なし。**脆弱性チェック対象なし**（OSV 照会・パッケージ調査は不要）
- **捨てた案**: `pytest` — relay は `requirements.txt` すら持たない完全な標準ライブラリ構成であり、
  テストのために外部依存を導入すると配布プラグインの前提が変わる
- **配置**: リポジトリルート `tests/`（`plugins/relay/` の外に置くため配布物に含まれない）

## 委任された点と仮決め

- `build_recorder_env(base_env)` という関数名で `relay_common.py` に配置する
- abort ログのヒント文言:
  `abort: claude exited <code> (auth? check \`claude auth status\`; API-key-only setups need RELAY_KEEP_API_KEY=1)`

## 保留された点

なし（ラウンド 3 の保留はラウンド 4 で解消済み）。
