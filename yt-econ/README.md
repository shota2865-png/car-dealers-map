# yt-econ — 経済解説チャンネルの全自動運用

話題選び・台本・音声・図表・字幕・編集・サムネ・アップロードまでを
1コマンドで回すためのパイプラインです。

```
話題選定 → 台本生成 → ファクトチェック → 尺調整 → 音声合成
   → 画面素材 → 字幕 → 動画合成 → サムネ → メタデータ → YouTube投稿(予約)
                                                            ↓
                          （数ヶ月後、日本で話題化したら）掘り起こし
```

```bash
python -m ytecon learn <参考動画のURL>...   # 語り口を参照動画から取り込む
python -m ytecon run                        # 当日分を作って予約投稿まで
python -m ytecon revive                     # 寝かせた動画が日本で話題化したか照合
```

---

## 最初に知っておいてほしい3つのこと

**1. CapCut は「完全自動」にはできません。**
CapCut には公開APIがなく、外部から書き出しを実行する手段がありません。
そこで本パイプラインの既定は **ffmpeg** です（`render.backend: ffmpeg`）。
これなら人が一切触らずに mp4 まで出ます。
CapCut を使いたい場合は `render.backend: capcut` にすると、
**タイムラインが組み上がったドラフト**が生成され、CapCut で開いて
書き出しボタンを押すだけの状態になります（＝最後の1手だけ手動）。

**2. 収益化の条件は「時間」が効くので、1ヶ月で5万円は届きません。**
YouTube パートナープログラム（YPP）の参加条件は
「登録者1,000人 + 直近12ヶ月の公開長尺の総再生時間4,000時間」。
申請してから審査に通るまでさらに時間がかかります。
つまり **1ヶ月目は広告収益がそもそも発生しません**。
到達可能な形に分解した計画を [`docs/収益化ロードマップ.md`](docs/収益化ロードマップ.md)
に置きました。ここは正直にお伝えしておきます。

**3. 日本は海外に遅れて追いつくので、企画を3つの枠で持ちます。**
先行すればブルーオーシャンですが、刺さるまで時間がかかります。
そこで企画を `flow`（いま日本で話題）/ `bridge`（半年以内に来る）/
`stock`（海外のみ。まだ先）に分け、比率で持ちます。
stock は今日の再生数では評価しません。**日本で話題化したときに
「最初に見つかる1本」になれるか**が評価軸です。
そして `ytecon revive` が毎日ニュースを照合し、仕込んだテーマが
日本で立ち上がった瞬間にタイトル・サムネ・概要欄を作り替えます。
詳細は [`docs/収益化ロードマップ.md`](docs/収益化ロードマップ.md) の6節。

**4. AIで量産するだけだと収益化審査で落ちます。**
YouTube の「再利用されたコンテンツ」ポリシーは、
ナレーションを載せただけ・独自の解説がないものを弾きます。
そのため本パイプラインは、
図表の自動生成／一次情報の明示／ファクトチェック工程／
同一テーマの重複排除 を最初から組み込んでいます。
**ここを外すと審査に通らなくなる**ので、外さないでください。

---

## 画面づくりの決まり（3つの設計ファイル）

| ファイル | 何を決めるか | 解説 |
|---|---|---|
| `config/style_bible.yaml` | 台本の 9 ブロック構成・接続詞でのカット・BGM の気分・タイトル型 | [`docs/スタイルバイブル.md`](docs/スタイルバイブル.md) |
| `config/design_tokens.yaml` | 文字の大きさ・色・角丸・余白（デジタル庁 / Apple HIG / Material 3） | [`docs/デザインシステム.md`](docs/デザインシステム.md) |
| `assets/footage/` | カードの後ろで常に動く背景・実写 B-roll（Artlist など） | [`docs/Artlistの使い方.md`](docs/Artlistの使い方.md) |

## 参考にしたい動画に寄せる

「こういう感じにして」を言葉で伝えるのは難しいので、**参照動画を実測して
文体プロファイルを作る**コマンドを用意しました。

```bash
pip install yt-dlp
python -m ytecon learn https://youtu.be/xxxx https://youtu.be/yyyy https://youtu.be/zzzz
```

やっていること:

1. yt-dlp で**字幕とメタデータだけ**取得（動画本体は落とさないので速い）
2. 話速・1文の長さ・導入の秒数・チャプター構成を機械的に実測
3. 文字起こしを読ませて、導入の型・つなぎ方・専門用語の出し方・
   よく使う言い回し・**使っていない表現**を言語化
4. `config/style.yaml` に書き出す

以降 `ytecon run` はこのプロファイルに寄せて台本を書きます。
実測した話速は `video.chars_per_minute`（初期値は当て推量の340）を上書きするので、
**尺の精度も上がります**。

3本くらい指定すると、共通する型だけが抽出されて安定します。
プロファイルには「真似しないほうがいい点」（その人の経歴に依存する語りなど）も
入るので、事故りにくくなっています。

> 自動生成字幕は同じ語を次の行が繰り返す形式なので、そのまま数えると話速を
> 1.5倍以上に誤認します。ここは除去したうえで実測しています。

## セットアップ

### 1. 依存をそろえる

```bash
cd yt-econ
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/install_fonts.py                      # 日本語フォント
```

`ffmpeg` はシステムに無くても `imageio-ffmpeg`（requirements に同梱）の
バイナリに自動で切り替わるので、sudo が使えない環境でも動きます。

日本語フォントは Noto Sans JP の **Bold(700) と Black(900)** を入れます。
システムに入っている IPAGothic は線が細く、動画のテロップだと潰れて読めません。
本文は Bold、サムネと見出しは Black を使います。
字幕だけは丸ゴシックの **M PLUS Rounded 1c Black**（`assets/fonts/MPLUSRounded1c-Black.ttf`、OFL、同梱）を使います。
ずんだもんの柔らかい声に合わせたもので、`visuals.subtitle.font` を空にすると Noto Sans JP Black に戻ります。
字幕の中では数字＝黄、減少・リスク・その回の主張＝赤、キーワード・用語＝青と黄緑を交互に色分けし、
同じ語にはいつも同じ色が付きます（`visuals.subtitle.highlight`）。

取得は npm の `@fontsource/noto-sans-jp` 経由で、woff2 を TTF に戻しています。
Google Fonts に直接繋げない環境でも npm さえ通れば入ります。

### 2. 音声エンジンを立てる（無料）

```bash
bash scripts/start_voicevox.sh
# = docker run --rm -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

既定の話者は **ずんだもん（ノーマル / 話者ID 3）** です。
`python -m ytecon speakers` で一覧が出ます。

**声を変えたら口調も変えてください。** ずんだもんの声で です・ます調 を
読ませると、視聴者に強い違和感が出ます。`channel.speech_style` が
`zundamon` のとき、台本の語尾が「〜のだ」調になります。
`doctor` が両者の食い違いを警告します。

口調は可愛くても中身は手を抜かない設計にしてあります（数字と出典はむしろ
丁寧に出す）。そのギャップが差別化になるためです。
語尾は3文に1回程度に留めています。毎文「のだ」だと音声が単調になります。

VOICEVOX は商用利用可ですが、**キャラクター名のクレジット表記が必要**です。
概要欄への記載は `metadata.py` が話者IDから自動で入れます。

### 3. 鍵を入れる

```bash
cp .env.example .env
```

| 変数 | 必須 | 取得先 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ◎ | console.anthropic.com |
| `PEXELS_API_KEY` | △ | pexels.com/api （無いと背景はグラデーションになります） |
| `YOUTUBE_CLIENT_ID` / `_SECRET` | ◎(投稿時) | Google Cloud Console → OAuth クライアント(デスクトップ) |
| `YOUTUBE_REFRESH_TOKEN` | ◎(投稿時) | `python scripts/auth_youtube.py` |

### 4. 点検する

```bash
python -m ytecon doctor
```

足りないものを全部指摘してくれます。ここが緑になってから次へ。

---

## 使い方

```bash
# 話題だけ見る（APIは叩くが動画は作らない）
python -m ytecon topics -n 2

# 台本を1本だけ試し書き
python -m ytecon script "円安はなぜ起きるのか" -a "家計への波及に絞る"

# 1本を投稿せずローカルに mp4 まで（最初はこれで品質を確認）
python -m ytecon run -n 1 --no-upload

# 本番。当日分（既定 1 本）を作って 19:00 に予約投稿
python -m ytecon run

# 途中で落ちたら
python -m ytecon status
python -m ytecon resume 20260919-070000-topic-1234

# 企画の偏りと、掘り起こし待ちの在庫を見る
python -m ytecon portfolio

# 寝かせた動画が日本で話題化したか照合（--apply で YouTube に反映）
python -m ytecon revive
python -m ytecon revive --apply
```

成果物は `output/<slug>/` に残ります。

```
output/20260919-070000-.../
├── script.json        台本（構造化データ）
├── narration.wav      ナレーション
├── narration.json     文ごとのタイムコード
├── images/            シーンごとの背景（図表・カード・写真）
├── subtitles.ass      焼き込み用字幕
├── subtitles.srt      YouTube 登録用字幕
├── thumbnail.jpg      サムネイル
├── video.mp4          完成品
└── metadata.json      タイトル・概要欄・タグ
```

---

## 毎日 1 本（19:00 公開）を自動で回す

### A. サーバ / 自宅PC で cron

```cron
# JST 17:00 に 1 本作る（予約投稿で 19:00 に公開される。upload.publish_times_jst で変更）
0 17 * * * cd /path/to/yt-econ && .venv/bin/python -m ytecon run -n 1 >> logs/daily.log 2>&1
```

### B. GitHub Actions（PCを起動しっぱなしにしなくていい）

`.github/workflows/daily-upload.yml` が入っています。
リポジトリの Secrets に下記を登録すれば動きます。

```
ANTHROPIC_API_KEY / PEXELS_API_KEY
YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN
```

VOICEVOX は service コンテナとして起動するので、別途用意は不要です。
「過去に扱った話題」の記憶は Actions のキャッシュで持ち越しています。

> プライベートリポジトリだと Actions の実行時間が課金対象です。
> 1本あたり20〜35分かかるので、1日1本でも月 600〜1,000 分になり無料枠（2,000分）の半分を使います。
> 常時稼働PCがあるなら A の cron のほうが安上がりです。

---

## 費用（追加の課金が出ない構成にしてあります）

| 項目 | 手段 | 費用 |
|---|---|---|
| 台本・企画・メタデータ | `claude -p`（Claude Code の枠） | **サブスクの利用枠内** |
| 音声 | VOICEVOX（ローカル） | 0円 |
| 図表 | matplotlib | 0円 |
| テロップ・カード | Pillow / Remotion | 0円 |
| 写真 | Pexels 無料枠 | 0円 |
| フォント | Noto Sans JP（OFL） | 0円 |
| 動画合成 | ffmpeg | 0円 |
| YouTube 投稿 | Data API v3 | 0円（1日1〜2本ならクォータ内） |

### 台本生成の課金を避ける仕組み

`ANTHROPIC_API_KEY` で直接叩くとトークン従量課金になります。
代わりに Claude Code の headless モード（`claude -p`）を使います。
既定は `auto` で、`claude` コマンドがあれば自動でこちらを選びます。

```bash
YTECON_LLM_PROVIDER=auto          # 既定。claude があれば claude_code
YTECON_LLM_PROVIDER=claude_code   # 強制
YTECON_LLM_PROVIDER=api           # ANTHROPIC_API_KEY を使う（従量課金）
```

`python -m ytecon doctor` を実行すると、いまどちらの経路かが表示されます。

> **正確に言うと** これは「どんな場合でも無料」ではありません。
> Claude Code を**サブスクリプションで認証している**なら、台本生成のぶんは
> プランの利用枠に含まれるので追加の請求が出ない、という意味です。
> Claude Code 自体を API キーで認証しているなら、結局は従量課金になります。
>
> 呼び出しは毎回まっさらなセッションで行っています。会話履歴を引き継ぐと
> コンテキストが膨らんで利用枠を無駄に食うためです。

## 画面のつくり（2回目の見直しで変えたこと）

1本目を見た感想「画面が変わらない・動きがない・スライドが勝手に拡大する」を
受けて、絵の作り方を作り直しました。

| 変更 | 中身 |
|---|---|
| **8秒ごとに画を切り替える** | 1セクション1枚 → 約7〜8秒ごとのシーンに分割。図表・キーワード・数字・一文・用語・出典・写真を順に切り替える |
| **カードは動かさない** | ズームは写真だけ。テキストカードや図表にズームがかかる不具合（写真が取れずカードに落ちたのに写真扱いになっていた）を修正 |
| **右下にキャラクター** | 音声の大きさに合わせて口が動き、数秒おきに瞬き。字幕は画面中央のまま、キャラの幅ぶん左右対称に空けて 1 行で出す（全行おなじ大きさ） |
| **BGM** | 声に対して体感3割ほど。話している間は自動で下げる。音源が無ければリラックス系の合成音 |
| **ビジネス用語カード** | 毎回2〜4語。用語 → 一文の意味 → 数字つきの例（出典入り） |
| **出典カード** | 参考にした記事・統計を、自分の様式で「参考・出典」として表示 |
| **見出しは標準語** | 「〜のだ」は読み上げだけ。画面の文字は標準語 |
| **1文20〜30字・小5に伝わる内容** | 話し方は大人向けのまま、中身だけ噛み砕く |

### キャラクター画像の置き方

`assets/character/` に透過PNGを4枚（同じサイズ）:

| ファイル | 状態 |
|---|---|
| `base.png` | 口を閉じている・目を開けている |
| `mouth_half.png` | 口を半分開けている |
| `mouth_open.png` | 口を開けている |
| `blink.png` | 目を閉じている |

無い場合は汎用の仮キャラ（丸い顔）を自動生成します。**本番前に必ず差し替えてください。**
ずんだもんの立ち絵を使う場合は、配布元のガイドライン（クレジット表記など）に従ってください。
大きさは `character.height_ratio`（既定 0.42 = 画面の高さの42%）で変えられます。

### 記事のスクリーンショットについて

「参考記事のタイトルをスクショで載せたい」という要望は、**他社サイトの画面を
そのまま貼ると著作権の問題が出る**ため、記事の見出し・媒体名・URLを
こちらの様式で組む「出典カード」に置き換えています。見た目は似た効果で、
権利面が安全です。

### 写真・AI画像

写真は Pexels（無料・要キー）、AI画像は pollinations.ai（無料・キー不要）の順に試し、
どちらも駄目なら幾何パターンの背景に落ちます。AI画像の経路は検証環境から外に
出られないため未検証です。動かなかったら教えてください。


### 週間スケジュール（手動の企画キュー）

`config/schedule.yaml` に日付つきで企画を書いておくと、その日の `ytecon run` は自動選定より先にそれを使います。
使い終わった企画は投稿履歴との重複判定で自動的に飛ばされ、書いた本数が足りない日は従来どおり
ニュースと常設テーマから自動で選びます。企画には title / angle のほか、why_now・audience_hook・
key_questions・sources を書けるので、扱ってほしい企業の具体例や統計はここに書いておくと台本に反映されます。

### 掛け合いモード（四国めたん × ずんだもん）

`config/channel.yaml` の `cast.mode: dialogue` で、左に四国めたん（解説役の先輩）、右にずんだもん
（新卒社会人の聞き役）を置いた掛け合いになります。台本は文頭の話者タグ
`【めたん】` `【ずんだもん】` で発言が分かれ、音声は文ごとに VOICEVOX の話者（めたん 2 / ずんだもん 3）を
切り替えます。立ち絵は自分の発言の間だけ口が動き、字幕は話者ごとに縁の色（めたん紫 / ずんだもん緑）が
変わります。カードや図解は 2 人のあいだに収まるよう自動で位置と大きさを調整します。
出演者の立ち絵・声・向き・人物設定は `cast.characters` に書きます。`mode: solo` にすれば従来の 1 人に戻ります。

立ち絵は坂本アヒル様の「ずんだもん立ち絵素材」「四国めたん立ち絵素材」（PSD）を `assets/character/` に
置いて使っています。概要欄には自動で「立ち絵：坂本アヒル 様」が入ります。


## 品質を上げるつまみ

すべて `config/channel.yaml` にあります。

| やりたいこと | いじる場所 |
|---|---|
| 参考動画に寄せる | `ytecon learn <URL>` → `config/style.yaml` |
| 話し方のトーンを手で変える | `channel.tone`（style.yaml があるとそちらが優先的に効く） |
| 声を変える | `tts.voicevox.speaker`（`ytecon speakers` で一覧） |
| 口調を変える | `channel.speech_style`（`plain` / `zundamon`） |
| 尺を変える | `video.target_minutes_min/max` |
| 尺がいつもズレる | `video.chars_per_minute`（実行ログに実測値が出ます） |
| 配色を変える | `visuals.palette` |
| 扱うニュース源を変える | `topics.rss_sources` |
| 同じテーマの再訪を許す | `topics.dedupe_window_days` を短く |
| 先行仕込みを増やす/減らす | `topics.horizon_mix`（既定 flow3 : bridge2 : stock1） |
| 立ち上げ期の長さを変える | `topics.ramp_up_videos`（既定30本。ここまでは flow 厚め） |
| 先行テーマの種を入れ替える | `topics.frontier_seeds`（半年に一度は見直す） |
| 掘り起こしの再通知間隔 | `revive.cooldown_days`（既定45日） |
| BGMを入れる | `assets/bgm/` に置く（入手先は `docs/BGMの用意.md`） |
| BGMの大きさ | `render.bgm.volume_db`（既定 -8。声は自動で -16 LUFS に揃えてから混ぜる） |
| 画の切り替え頻度 | `visuals.scene_seconds`（既定 7秒） |
| キャラの大きさ・位置 | `character.height_ratio` / `margin_right` |
| 口の動きの感度 | `character.mouth_half_threshold` / `mouth_open_threshold` |
| 声の抑揚・速さ | `tts.voicevox.intonation`（既定1.35） / `speed`（既定1.2） |
| 投稿時刻を変える | `upload.publish_times_jst` |

---

## 設計メモ

- **音声を文単位で合成してから連結している。**
  こうすると各文の開始・終了秒がサンプル精度で確定するので、
  字幕もカット割りも推測なしで作れます。音ズレが構造的に起きません。
- **台本は「読み上げ文」と「画面に出すもの」を分離している。**
  後段が一切の判断をせず機械的に処理できるようにするためです。
- **各工程の成果物をファイルに残し、DBに進捗を持つ。**
  途中で落ちても `resume` で続きから走ります。
- **素材取得の失敗でパイプラインを止めない。**
  Pexels が落ちてもグラデーション背景に、図表データが壊れていても
  テキストカードに、自動で落ちます。
- **企画の配分をその日だけで閉じない。**
  1日2本で 3:2:1 をその場で割ると stock が毎日0本になり、先行仕込みが
  永久に貯まりません。直近30日の偏りを見て、不足している枠から埋めます。
  逆に flow が枯れると燃料切れになるので、下限も持たせています。

---

## テスト

```bash
python -m pytest -q
```

API を叩かずに検証できる範囲（尺計算・字幕の分割・重複判定・シーン割り・
チャプター条件・状態管理・企画配分・掘り起こしの照合・参照動画の字幕解析）を
カバーしています。
とくに「1日2本でも stock が作られること」「flow が枯れないこと」
「同じ動画が二度鳴らないこと」は、壊れても気づきにくいので固定してあります。
