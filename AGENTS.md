# AGENTS.md — 共有引き継ぎファイル（Muse / Claude Code / Codex 共通）

このファイルは、Muse（イロ）・Claude Code・Codex の3つのエージェントが
GitHub経由で同じリポジトリを触るときの共通ルールと引き継ぎ場所です。
どれか1つのエージェントだけが読むものではありません。

## このリポジトリについて

- 目的：懸賞マネージャー。Flask + SQLiteで、RSSから懸賞を集めて管理し、Playwrightで応募フォームを自動入力する。24時間ワーカーとLINE通知あり
- 言語・主要技術：Python（Flask + SQLite、Playwright）
- 動かし方：`python -m venv .venv` → `pip install -r requirements.txt` → `playwright install chromium` → `python app.py`（管理画面 http://127.0.0.1:5000、まず「プロフィール」を登録）。24時間運用は `python app.py` と `python worker.py` を別ウィンドウで2つ動かす。PC起動時の自動起動は `start_windows.bat` / `start_mac.command`。LINE通知はMessaging APIのチャネルアクセストークン（長期）とユーザーIDを管理画面「設定」に入れ、「LINE にテスト通知を送る」で確認（毎日のまとめは既定20時）
- テスト・チェック：`pip install pytest` → `python -m pytest tests`

## 共通ルール

1. 作業は1タスク＝1ブランチで行い、ブランチ名は `agent/内容-日付` の形にする。
2. デフォルトブランチ（main）へ直接pushしない。プルリクエストを作り、内容を要約する。
3. 作業を始める前に、下の「引き継ぎログ」を読む。終わったら自分の行を追記する。
4. 秘密情報（APIキー、パスワード、トークン）はコードやログに書かない。
5. 破壊的なコマンド（`rm -rf`、DB削除、force push）は実行しない。
6. 分からないことは推測で進めず、「未確認」と書いて次へ渡す。

## 引き継ぎログ

| 日付 | 担当 | やったこと | 次にやること |
| --- | --- | --- | --- |
| 2026-11-28 | Muse | 3リポジトリの内容を確認。共通AGENTS.mdの下書きを用意 | `app.py` と `worker.py` のローカル起動確認 |

## 今のタスク

- 進行中：（なし）
- 次：`app.py` と `worker.py` をローカルで起動して、管理画面からRSS取り込み、自動入力、LINE通知まで通しで動作確認する

## 注意

- 2026-11-25の初回コミット1件だけの新しいリポジトリ。IssueやPRはまだなし。
