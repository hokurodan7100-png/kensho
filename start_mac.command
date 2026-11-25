#!/bin/bash
# 懸賞マネージャー（管理画面とワーカー）を起動する。ログイン項目に追加して使う。
cd "$(dirname "$0")"
nohup .venv/bin/python app.py >> app.log 2>&1 &
# caffeinate -i：ワーカーが動いている間、Mac がスリープしないようにする
nohup caffeinate -i .venv/bin/python worker.py >> worker.log 2>&1 &
echo "起動しました。http://127.0.0.1:5000 を開いてください。"
