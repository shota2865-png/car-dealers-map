#!/bin/bash
# ============================================================
#  動画を1本つくる（Mac 用・ダブルクリックで実行）
#
#  1回目だけ Python の部品を入れるので 3〜5 分かかります。
#  2回目以降は動画を作る時間だけです（10分の動画で 5〜8 分）。
#
#  もし「開発元を確認できないため開けません」と出たら、
#  システム設定 → プライバシーとセキュリティ → 「このまま開く」。
#  あるいはターミナルで:  bash "このファイルのパス"
# ============================================================
cd "$(dirname "$0")" || exit 1

echo "== 動画をつくる =="

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import sys; sys.exit(sys.version_info < (3, 9))"; then
  echo "Python 3 が見つかりません。Apple の開発ツールを入れます（画面が出たら「インストール」）。"
  xcode-select --install 2>/dev/null
  echo "入れ終わったら、もう一度このファイルをダブルクリックしてください。"
  read -r -p "Enter で閉じる"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "初回の準備をしています（3〜5分）…"
  python3 -m venv .venv || { echo "仮想環境を作れませんでした"; read -r -p "Enter で閉じる"; exit 1; }
  .venv/bin/python -m pip install -q --upgrade pip
fi
.venv/bin/python -m pip install -q pyyaml pillow matplotlib requests gtts imageio-ffmpeg \
  || { echo "部品を入れられませんでした（ネット接続を確認）"; read -r -p "Enter で閉じる"; exit 1; }

.venv/bin/python make_video.py "$@"
status=$?
echo
if [ $status -eq 0 ]; then
  echo "できあがりです。out フォルダを開きました。"
else
  echo "途中で止まりました。上に出ているメッセージをそのまま貼って教えてください。"
fi
read -r -p "Enter で閉じる"
