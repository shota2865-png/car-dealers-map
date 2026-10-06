# PSYCH DATA LAB のモーショングラフィックス（1 分のショーリール）

```bash
cd yt-econ/motion && npm install
export MOTION_DIR=$PWD/work            # コマ・音・書き出しの置き場
python make_cards.py                   # 検証カードの画像（日本語は Python で描く）
node render.mjs $PWD reel.html $MOTION_DIR/frames 30 0 1800      # 3D（約 1 秒/コマ。2 つに分けて並べて回すと速い）
python comp.py 0 1800                   # 文字・めたんの口パク・HUD を重ねる（声は audio/lines.json）
python mix.py                           # BGM・効果音・声
ffmpeg -framerate 30 -i $MOTION_DIR/out/c%05d.jpg -i $MOTION_DIR/audio/mix.wav -c:v libx264 -crf 18 -pix_fmt yuv420p -af loudnorm=I=-15:TP=-1.5 reel.mp4
```

- Chromium は `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`（render.mjs の executablePath を環境に合わせる）
- 参考にしたチャンネルと取り入れたことは `docs/心理学_参考チャンネル.md`

## v3: データ検証のサンプル（1440p・細い近未来フォント）

ジャムの実験を「立ち止まった人 60% vs 40%」「買った人 3% vs 30%」「100 人の流れ」「50 の実験のまとめ」「効いてくる 4 つの条件」「判定 半分本当」で見せる 78 秒。

```bash
python make_voice.py 3                  # めたんの声 → audio3/（台本と時刻は audio3/lines3.json）
VW=2560 VH=1440 node render.mjs $PWD reel3.html $PWD/frames3 30 0 2340      # 3D（約 3.5 秒/コマ。3 つに分けて並べて回す）
python comp3.py 0 2340                  # 文字・3D に付いたラベル・めたんの口パク・字幕 → out3/
python mix3.py                          # BGM・効果音（切り替え・数字が出る瞬間）・声 → audio3/mix3.wav
ffmpeg -framerate 30 -i out3/c%05d.jpg -i audio3/mix3.wav -c:v libx264 -crf 16 -preset slow -pix_fmt yuv420p -af loudnorm=I=-15:TP=-1.5 reel3.mp4
```

- 字体: 日本語は M PLUS 1（細め）、英字の飾りは Orbitron、数字は Oxanium（0 に斜線が無い）。どれも OFL（`assets/fonts/OFL-*.txt`）
- 3D の点の画面上の位置は `renderAt(t)` が返す（カメラを先に決めてから写すので、文字がずれない）
