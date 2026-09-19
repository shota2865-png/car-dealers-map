#!/usr/bin/env bash
# 日本語フォント(Noto Sans JP Bold)を assets/fonts に入れる
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p assets/fonts

if [ -f assets/fonts/NotoSansJP-Bold.ttf ] || [ -f assets/fonts/NotoSansJP-Bold.otf ]; then
  echo "フォントは導入済みです"
  exit 0
fi

# 1) OS のパッケージにあればそれを使う（一番速い）
for p in \
  /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc \
  /usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf ; do
  if [ -f "$p" ]; then
    echo "システムのフォントを使います: $p"
    exit 0
  fi
done

if command -v apt-get >/dev/null 2>&1; then
  echo "fonts-noto-cjk を導入します（sudo が要ります）"
  sudo apt-get update -qq && sudo apt-get install -y fonts-noto-cjk && exit 0
fi

# 2) Google Fonts から直接取得
echo "Google Fonts から取得します"
URL="https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/JP/NotoSansJP-Bold.otf"
if command -v curl >/dev/null 2>&1; then
  curl -fL "$URL" -o assets/fonts/NotoSansJP-Bold.otf
else
  wget -O assets/fonts/NotoSansJP-Bold.otf "$URL"
fi
echo "完了: assets/fonts/NotoSansJP-Bold.otf"
