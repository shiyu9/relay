# relay

セッション間の引き継ぎリレー。Claude Code のセッション終了時に会話ログから**日記**（時系列の引き継ぎ）と**ナレッジ**（永続知見）を自動記録し、次のセッション開始時に自動注入するプラグイン。

A session-to-session handoff relay for Claude Code. On session end, a detached headless Claude reads the transcript and records a **diary** (chronological handoff) and **knowledge** (durable lessons); on session start, they are injected back into context.

## 仕組み / How it works

- **SessionEnd**: フックが headless Claude（既定: Haiku 4.5）をデタッチ起動。transcript を読んで以下を書く:
  - `diary/YYYY-MM-DD.md` — セッションごとに `## S<n> HH:MM` + `done / decisions / mistakes / handoff`
  - `knowledge/pitfalls.md` / `knowledge/workflow.md` — 実際の失敗・訂正から生まれた永続知見のみを `[日付]` 付き命令形で単純追記（各ファイル約80行上限）
  - ユーザー発言が閾値未満の薄いセッションはスキップ（利用枠を消費しない）
- **SessionStart**（新規起動・/clear 後のみ、resume では動かない）: knowledge 全文＋直近2日分の日記を注入。矛盾・重複・陳腐化の整理はセッション本体の Claude が前置き指示に従って行う（判断できない矛盾はユーザーに確認）
- **キャッチアップ**: ウィンドウがセッション終了と同時に閉じる構成などで SessionEnd フックが完走できず記録が落ちた場合、次の SessionStart 時に検出して記録し直す（30分以上更新のない未記録トランスクリプトが対象、1回の起動で最大3件、直近14日以内）

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
| `RELAY_MODEL` | `claude-haiku-4-5` | 記録に使う headless モデル |
| `RELAY_MIN_USER_MSGS` | `3` | これ未満のユーザー発言数ならスキップ |
| `RELAY_KEEP_API_KEY` | - | `1` で記録時も `ANTHROPIC_API_KEY` を使う。既定では除去して claude.ai サブスクリプション認証で実行する（このキーは設定されているとサブスクより優先され、headless 実行では確認なしに使われるため、無効なキーが残っていると記録係が 401 で死ぬ） |

例: 特定フォルダ配下だけで使う（ユーザー settings.json）

```json
{ "env": { "RELAY_SCOPE": "C:\\claude-projects" } }
```

## 注意 / Notes

- セッション終了ごとに headless Claude が1回走り、サブスクリプション利用枠を少し消費します。/ Each session end consumes a small amount of your subscription quota.
- ウィンドウ強制クローズ等で SessionEnd フックが走れなかったセッションは、その場では記録されず、次にそのプロジェクトでセッションを開始したときにキャッチアップで記録されます。
- キャッチアップが回収するのは、そのプロジェクトで**プラグイン導入後に最初にセッションを開始した時点**より後に終了したセッションです。この初回起動時に既存のトランスクリプトは「記録済み」として台帳に登録されるため、導入前に失われたセッションは遡って回収されません（過去数週間分のセッションが当日の日記にまとめて記録されるのを防ぐための仕様です）。
- **ターミナルが claude の終了と同時に自動で閉じる構成**（ランチャーやラッパースクリプト経由など）では、フックが起動を完了する前にコンソールごと殺され、その場での記録が失われやすくなります（キャッチアップで後から回収はされます）。即時記録を維持したい場合は、ラッパーの claude 呼び出しの後に数秒のディレイを入れてください。例（PowerShell）: `claude; Start-Sleep -Seconds 2`
- **Windows でのみ動作検証済み**です。コードは macOS/Linux を考慮していますが未検証です。/ Only tested on Windows; macOS/Linux paths exist in code but are unverified.
- 実行ログ: `~/.claude/relay/log.txt`（スキップ理由・起動記録）、`~/.claude/relay/last_run.log`（直近の headless 出力）

## License

MIT
