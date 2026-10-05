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
