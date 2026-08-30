# relay

セッション間の引き継ぎリレー。Claude Code の会話ログ（transcript）から**日記**（セッションごとの記録）・**ナレッジ**（永続知見）・**現在地**を自動で書き起こし、次のセッション開始時に注入するプラグイン。

A session-to-session handoff relay for Claude Code. It turns each transcript into a **diary** entry, distils durable **knowledge** and a single **current** page, and injects them back at the start of the next session.

**記録はセッション自身のサブエージェントが行うため、ヘッドレスの `claude` CLI を必要としません。** / Recording runs as a subagent of the session itself, so no headless `claude` CLI is required.

## 仕組み / How it works

### 1 transcript = 1 日記エントリ

日記のエントリは**そのセッション自身の日付**のファイルに置かれ、見出しに時間帯とセッション ID を持つ。

```
diary/2026-08-27.md

## S1 19:11-22:30 (124c52dd)
### done
- 実際に達成したこと
### decisions
- 下した判断とその理由
### mistakes
- 誤り・手戻り
### handoff
- 次のセッションに要ること
```

日付をまたいだセッションは、またいだ日数が終了時刻に付く（`## S2 08:14-08:13+3d (00cb0081)` は 8/26 08:14 から 8/29 08:13 まで）。

同一性が ID で決まるので、**同じセッションを二重に記録しない。**resume で transcript が伸びたときは、新しい要約でそのエントリを**丸ごと差し替える**（追記して2つに分かれることがない）。

**relay 側に「何日前まで」という窓は無い。**Claude Code が `cleanupPeriodDays`（既定30日）で transcript を消すので、残っているものはすべて記録の対象になる。

### 3つのフック

- **SessionStart**（新規起動・`/clear` 後のみ）
  - `knowledge/pitfalls.md` `workflow.md` `decided.md`、**日記の直近5エントリ**、`knowledge/current.md` をこの順に注入する（変わりにくいものから変わりやすいものへ）
  - 未記録の transcript があれば、それを記録する**手順**も注入する。手順はセッション自身が実行する:
    1. 記録ジョブ（transcript 1本につき1回・最大3本）をサブエージェントで実行。**3本は互いに独立なのでまとめて呼ぶ**
    2. `relay_apply.py` が結果をファイルへ反映し、要約を1行返す
    3. 日記が書かれていれば「現在地・見送り」ジョブを実行して再度反映
    4. 要約行の `merge_due` が `yes` なら統合ジョブを実行して再度反映

    **統合ジョブは項目を削除しない。**重複を1行にまとめ、矛盾を新しい `[日付]` に合わせるだけで、行数のために削ることはしない。80行はそこが育ったという合図で、超えたときは「別ファイルへ移してよさそうな項目」を理由付きで挙げ、**その候補は日記に書かれる**（移すかどうか、どこへ移すかは人が決める）。

    発火条件は Python が計算する。**行数が上限を超えていて、かつ前回の統合から中身が変わっている**ときだけ `merge_due=yes` になる（削除しない以上、行数だけを条件にすると大きいプロジェクトで毎回発火し続けるため）。

    統合は2ファイルを全文で置き換えるため、`relay_apply.py` は**ジョブが作られたときの本文と現在のファイルが一致するときだけ**適用する（一致しなければ飛ばし、要約行に `stale=1` を出す）。手順1が同じファイルに追記するので、ジョブは反映のたびに作り直される。
  - **ジョブの本文はファイルに置かれ、サブエージェントだけが読む。**本体のコンテキストには数行の手順しか入らない
- **SessionEnd** — `~/.claude/relay/ended/` に空ファイルを1つ作るだけ。「このセッションは終了した」という事実だけを残す。これがあると、閉じてすぐ開き直しても直前のセッションがその場で記録される（無ければ「30分以上更新なし」で判定する）
- **Stop** — 未記録の transcript が残ったまま進んでいるときだけ、記録を促す。プロジェクト単位で30分のクールダウンがあり、公式の `stop_hook_active` ガードで自分の促しには反応しない

### 4つのファイル

| ファイル | 性質 | 誰が書くか |
|---|---|---|
| `diary/YYYY-MM-DD.md` | セッションごとの記録。エントリ単位で差し替え | relay |
| `knowledge/pitfalls.md` | 技術的な罠と回避策。追記＋重複の統合（削除はしない） | relay |
| `knowledge/workflow.md` | このユーザーとの進め方の学び。追記＋重複の統合（削除はしない） | relay |
| `knowledge/current.md` | **現在地**。1つの節・毎回上書き・行数の上限なし | relay |
| `knowledge/decided.md` | 見送った案と**再開条件**。毎回上書き・剪定あり | relay |

`current.md` と `decided.md` は**レコーダーが唯一の書き手**で、セッション中に手で編集しない（次の記録で上書きされる）。`decided.md` から落ちた項目は、Python がその日の日記に記録するので**消えた理由をあとから追える**。

**ファイルへの書き込みはすべて Python が決定的に行う。**サブエージェントは区切り付きのテキストを一時ファイルへ出すだけで、日記もナレッジも直接触らない（安価なモデルに追記を任せて既存エントリを消失させた実バグがある）。

## インストール / Install

```
/plugin marketplace add shiyu9/relay
/plugin install relay@relay
```

## 設定 / Configuration (env)

| 変数 | 既定 | 意味 |
|---|---|---|
| `RELAY_SCOPE` | (なし=全プロジェクト) | このパス配下のプロジェクトだけで動作 |
| `RELAY_DISABLED` | - | `1` でそのプロジェクト無効（`.claude/settings.json` の `env` に設定） |
| `RELAY_MODEL` | `claude-haiku-4-5` | 記録のサブエージェントに指定するモデル |
| `RELAY_MIN_USER_MSGS` | `3` | これ未満のユーザー発言数のセッションは記録しない |

例: 特定フォルダ配下だけで使う（ユーザー settings.json）

```json
{ "env": { "RELAY_SCOPE": "C:\\claude-projects" } }
```

### 任意: thinking summary を記録に活かす

`~/.claude/settings.json` に `"showThinkingSummaries": true` を入れると（`/config` からは設定できません）、Claude Code が API に `thinking.display: "summarized"` を要求し、thinking の中身が transcript に残るようになります。relay はそれを **decisions**（捨てた案・検討過程）と **mistakes**（なぜ誤った判断をしたか）の根拠に使います。`done` / `handoff` には効きません。

```json
{ "showThinkingSummaries": true }
```

- 表示が変わるだけで、thinking トークンの消費＝課金は変わりません。
- `false`（既定）でも relay は全機能そのまま動きます。transcript の thinking が空なら、記録係はそれを使わないだけです。

## 注意 / Notes

- **導入直後は、残っている transcript を遡って記録します。**1回の起動につき最大3本なので、履歴の多いプロジェクトでは数回〜十数回の起動をかけて埋まります。それぞれのエントリは**そのセッション自身の日付**のファイルに入るので、当日の日記に過去がまとめて流れ込むことはありません。
- 記録はセッション開始時に走るため、**起動が少し待たされます。**その代わり、ヘッドレスの `claude` CLI が使えない環境でも動きます。
- サブエージェントは中間結果を `~/.claude/relay/inbox/` に書きます。プロジェクト外への書き込みなので、環境によっては許可を求められます。毎回聞かれるのが煩わしい場合は settings.json の `permissions.allow` に `Write(//<ホーム>/.claude/relay/**)` を足してください。
- **日記のファイルを手で消すと、その範囲が次の起動で記録し直されます**（未記録かどうかを日記そのものから判定しているため）。
- 記録は transcript が残っている間だけ可能です。`cleanupPeriodDays`（既定30日）を過ぎた分は遡れません。
- **Windows でのみ動作検証済み**です。コードは macOS/Linux を考慮していますが未検証です。/ Only tested on Windows; macOS/Linux paths exist in code but are unverified.
- 実行ログ: `~/.claude/relay/log.txt`

## License

MIT
