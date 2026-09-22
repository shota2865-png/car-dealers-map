# Remotion PoC（実機検証済み）

Phase 4 の出発点。**この環境で実際にレンダリングが通ることを確認済み**です。

```bash
npm install
npx remotion render src/index.ts GraphicsTrack out/graphics.mov \
  --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le
```

出力: `yuva444p12le` / 1920x1080 / 30fps / 5秒（アルファ付き）

背景と合成する:

```bash
ffmpeg -y -loop 1 -t 5 -i background.jpg -i out/graphics.mov \
  -filter_complex "[0:v]scale=1920:1080,fps=30[b];[b][1:v]overlay=0:0:format=auto[v]" \
  -map "[v]" -frames:v 150 -c:v libx264 -crf 20 -pix_fmt yuv420p composite.mp4
```

## ハマった点（2つとも実際に踏みました）

**1. `tsconfig.json` が無いとレンダリングが始まりません。**
エラーメッセージは出ますが、npm init しただけでは作られないので忘れがちです。

**2. Chrome Headless Shell のダウンロードが要ります。**
ブロックされる環境では、既にある Chromium を指定してください。

```bash
--browser-executable=/path/to/headless_shell
```

## 日本語フォントの扱い（最重要）

`GraphicsTrack.tsx` の `useJapaneseFont()` を見てください。
`delayRender()` / `continueRender()` でフォント読み込みを待っています。
**これを省くとフォント未適用のフレームが混ざります**（Remotion + 日本語で
最も多い事故）。`.catch()` で必ず `continueRender` するのも忘れないこと。
待ちっぱなしになるとレンダリングがタイムアウトします。

## 色とフォントの扱い

`src/brand.ts` に集約しています。**コンポーネント内にハードコードしない。**
これを守っておくと、後から配色を変えたときに全コンポーネントへ一斉に効きます。
Phase 4 の受け入れテストに「accent を変えたらピクセルが変わること」を
入れてあるのは、この規律を機械的に守らせるためです。

## このデモが示していること

`BulletList` の `reveal` は項目を順に出します。これが「6秒ルール」を満たす
変化イベントの主力になります。静止画にゆっくりズームをかけ続けるより、
テキストが順に出るほうがはるかに画面が持ちます。

一方でこのデモの合成結果は、**箇条書きがグラフに重なっています。**
これは仕様書に「重ねるな」と書いても防げない種類のミスです。
だから QC で `speaker_zone` / safe area / 同時刻の bbox 交差を
機械判定させます（Phase 6）。
