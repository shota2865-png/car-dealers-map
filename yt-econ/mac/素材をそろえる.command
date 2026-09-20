#!/bin/bash
# ============================================================
#  素材をそろえる（Mac 用・ダブルクリック）
#   1. Artlist から動画・曲を落とす（artlist_fetch.py）
#   2. ~/Downloads の立ち絵 zip・Artlist ファイルを assets/ に振り分ける（import_assets.py）
# ============================================================
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "先に「動画をつくる.command」を一度実行して、準備を済ませてください。"; read -r -p "Enter で閉じる"; exit 1
fi
.venv/bin/python -m pip install -q playwright pyyaml && .venv/bin/python -m playwright install chromium
echo
echo "Artlist を開きます。初回はログインしてください。"
.venv/bin/python artlist_fetch.py "$@"
echo
echo "ダウンロードフォルダの素材を振り分けます…"
.venv/bin/python import_assets.py
read -r -p "Enter で閉じる"
