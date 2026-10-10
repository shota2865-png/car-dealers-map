#!/bin/zsh
# むらいクリップ: 毎日の切り抜き Shorts を作って 18:00（日本時間）に予約投稿する。launchd から呼ばれる。
# 鍵は ~/.muraiclip/（自分だけ読める）に置いてある。同じ日に何度動いても、足りない本数だけ作る。
set -u
export PATH="$HOME/muraiclip/venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export LANG=ja_JP.UTF-8 PYTHONUNBUFFERED=1
K="$HOME/.muraiclip"
export CLIP_YOUTUBE_CLIENT_ID="$(cat $K/client_id)" CLIP_YOUTUBE_CLIENT_SECRET="$(cat $K/client_secret)"
export CLIP_YOUTUBE_REFRESH_TOKEN="$(cat $K/refresh_token)" GEMINI_API_KEY="$(cat $K/gemini_key)"
LOG="$HOME/muraiclip/logs/$(date +%Y-%m-%d).log"
LOCK="$HOME/muraiclip/.lock"
# 二重起動を防ぐ（前の実行がまだ動いていたら何もしない）
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -mmin +240 2>/dev/null)" ]; then rmdir "$LOCK"; mkdir "$LOCK"; else echo "$(date) 実行中のため何もしません" >> "$LOG"; exit 0; fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
{
  echo "=== $(date) 開始 ==="
  cd "$HOME/muraiclip/repo" && git fetch -q origin main && git reset -q --hard origin/main || echo "更新を取り込めませんでした（手元の版で動かします）"
  cd yt-clip && caffeinate -i python -m clip --state "$HOME/muraiclip/state/state.json" --out "$HOME/muraiclip/out" "$@"
  echo "=== $(date) 終了（$?） ==="
  # 7 日より古い動画とログは消す
  find "$HOME/muraiclip/out" -maxdepth 1 -mtime +7 -exec rm -rf {} + 2>/dev/null
  find "$HOME/muraiclip/logs" -name "*.log" -mtime +30 -delete 2>/dev/null
} >> "$LOG" 2>&1
