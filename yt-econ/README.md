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
python -m ytecon run          # 当日分を作って予約投稿まで
python -m ytecon revive       # 寝かせた動画が日本で話題化したか照合
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

## セットアップ

### 1. 依存をそろえる

```bash
cd yt-econ
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
bash scripts/install_fonts.sh                        # 日本語フォント
```

`ffmpeg` はシステムに無くても `imageio-ffmpeg`（requirements に同梱）の
バイナリに自動で切り替わるので、sudo が使えない環境でも動きます。

### 2. 音声エンジンを立てる（無料）

```bash
bash scripts/start_voicevox.sh
# = docker run --rm -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

VOICEVOX は商用利用可ですが、**キャラクター名のクレジット表記が必要**です。
`python -m ytecon speakers` で話者一覧が出るので、選んだ話者名を
概要欄に入れてください（`config/channel.yaml` の説明文に追記できます）。

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

# 本番。当日分2本を作って予約投稿
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

## 毎日2本を自動で回す

### A. サーバ / 自宅PC で cron

```cron
# JST 06:00 と 18:00 に1本ずつ（予約投稿で 07:30 / 19:30 に公開される）
0 6  * * * cd /path/to/yt-econ && .venv/bin/python -m ytecon run -n 1 >> logs/am.log 2>&1
0 18 * * * cd /path/to/yt-econ && .venv/bin/python -m ytecon run -n 1 >> logs/pm.log 2>&1
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
> 1本あたり10〜20分かかるので、月2本/日なら無料枠を超えます。
> 常時稼働PCがあるなら A の cron のほうが安上がりです。

---

## 費用の目安（1日2本 = 月60本）

| 項目 | 月額の目安 |
|---|---|
| Claude API（台本＋校閲＋メタデータ、1本あたり3〜5回の呼び出し） | 約 1,500〜4,000円 |
| VOICEVOX | 0円 |
| Pexels | 0円 |
| YouTube Data API | 0円（1日2本ならクォータ内） |
| 合計 | **月 2,000〜4,000円程度** |

コストを下げたいときは `config/channel.yaml` の
`script.effort` を `high` → `medium`、`script.fact_check` を `false` に。
ただしファクトチェックを切ると収益化審査のリスクが上がるので、
落とすなら effort のほうを先に落としてください。

---

## 品質を上げるつまみ

すべて `config/channel.yaml` にあります。

| やりたいこと | いじる場所 |
|---|---|
| 話し方のトーンを変える | `channel.tone` |
| 声を変える | `tts.voicevox.speaker`（`ytecon speakers` で一覧） |
| 尺を変える | `video.target_minutes_min/max` |
| 尺がいつもズレる | `video.chars_per_minute`（実行ログに実測値が出ます） |
| 配色を変える | `visuals.palette` |
| 扱うニュース源を変える | `topics.rss_sources` |
| 同じテーマの再訪を許す | `topics.dedupe_window_days` を短く |
| 先行仕込みを増やす/減らす | `topics.horizon_mix`（既定 flow3 : bridge2 : stock1） |
| 立ち上げ期の長さを変える | `topics.ramp_up_videos`（既定30本。ここまでは flow 厚め） |
| 先行テーマの種を入れ替える | `topics.frontier_seeds`（半年に一度は見直す） |
| 掘り起こしの再通知間隔 | `revive.cooldown_days`（既定45日） |
| BGMを入れる | `assets/bgm/` に置いて `render.bgm.file` に指定 |
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
チャプター条件・状態管理・企画配分・掘り起こしの照合）をカバーしています。
とくに「1日2本でも stock が作られること」「flow が枯れないこと」
「同じ動画が二度鳴らないこと」は、壊れても気づきにくいので固定してあります。
