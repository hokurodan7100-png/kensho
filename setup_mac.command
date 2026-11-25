#!/bin/bash
# ワンクリックセットアップ（Mac）：.venv を作って必要なパッケージを入れる
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 が見つかりません。https://www.python.org/downloads/ から Python 3.10 以上を入れてから、もう一度実行してください。"
  read -r -p "Enter キーで閉じます" _
  exit 1
fi

echo "[1/3] Python の仮想環境（.venv）を作っています..."
python3 -m venv .venv || { echo "仮想環境を作れませんでした。"; read -r -p "Enter キーで閉じます" _; exit 1; }

echo "[2/3] パッケージを入れています..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt || { echo "パッケージを入れられませんでした。ネット接続を確認して、もう一度実行してください。"; read -r -p "Enter キーで閉じます" _; exit 1; }

echo "[3/3] Playwright 用のブラウザを入れています（Google Chrome が見つからないときに使います）..."
.venv/bin/python -m playwright install chromium || echo "  （スキップしました。Google Chrome を使います）"

echo
echo "セットアップが終わりました。"
echo "次は start_mac.command をダブルクリックして、http://127.0.0.1:5000 を開いてください。"
read -r -p "Enter キーで閉じます" _
