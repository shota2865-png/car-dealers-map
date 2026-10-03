# 引き継ぎメモ（新しいチャットはまずここを読む）

このリポジトリの本体は `yt-econ/`。YouTube チャンネルを台本から投稿まで全自動で回している。
ほかのフォルダ（`ai-video-system/` `oyasumi/` `shota-video-editor/` `index.html`）は別件で、今は触らない。

ユーザーとは日本語で、短く・専門用語を避けて話す。数字や結果は先に言う。

## チャンネルと目標

| | おやすみ経済学（main） | 現代人のための心理学（psych） |
|---|---|---|
| チャンネル ID | UCuOZdUBs68sCzq1g6GS9PIQ | UCv-h9prBycfOLwu2ieAUcMw |
| 設定 | `yt-econ/config/channel.yaml` | `yt-econ/config/channels/psych/channel.yaml`（本体を extends） |
| 本編の公開 | 毎日 19:00 | 毎日 20:00 |
| Shorts | 本編 1 本につき 6 本（21:30 / 0:00 / 7:00 / 12:00 / 15:00 / 18:00） | 6 本（21:30 / 23:30 / 7:30 / 12:30 / 15:00 / 17:30） |
| 声 | VOICEVOX（ずんだもん＆めたんの掛け合い） | VOICEVOX |
| サムネ | `panel`（コマ割り＋ぱくたそ写真＋ずんだもん）を自動生成 | 手で作ったもの優先、無い日は `framed` |

- 目標（10/31 まで）: 登録者 100 人・本編の総再生時間 500 時間・Shorts 平均 1 万回（`config/goals.yaml`）
- 経済は毎週日曜に「睡眠用・1週間まとめ」の総集編を 22:00 公開（`compilation.py`）

## 自動で動いているもの（GitHub Actions）

| ワークフロー | いつ | 何を |
|---|---|---|
| `daily-upload.yml` | 毎日（cron 01:10 UTC。実際は JST 15:30〜16:00 ごろ始まる） | 2 チャンネルぶん、本編 1 本＋Shorts 6 本を作って予約投稿。目標の進捗レポートもログに出る |
| `weekly-compilation.yml` | 日曜 JST 10:40 | 経済の 1 週間まとめを作って予約投稿 |
| `replace-thumbnails.yml` | `yt-econ/thumbnails/replace/<main\|psych>/<動画ID>.jpg` が main に入ったとき | その動画のサムネを差し替え |
| `publish-file.yml` | 手動 | 手で仕上げた mp4 を投稿 |

- YouTube の鍵は GitHub Secrets だけにある（`YOUTUBE_*`, `PSY_YOUTUBE_*`）。クラウドのコンテナには無いので、
  YouTube に書き込む作業（サムネ差し替え・コメントなど）は **Actions 経由** でやる
- 進捗や再生数は、daily-upload のジョブログの「目標の進捗と打ち手」に出る（Analytics は 2〜3 日遅れ）
- クォータ: 動画アップロードは 1 日 100 本まで（今は 14 本/日）、API は 1 日 10,000 ユニット

## 作業のしかた

- ブランチ `claude/youtube-economics-automation-rckve0` で作業 → PR を作る → **マージはユーザーが押す**
  （こちらからのマージは許可設定で止まる）。マージ済みなら `git merge --ff-only origin/main` で追いつかせてから次の作業
- テスト: `cd yt-econ && python -m pytest -q`（全部通してから push）。lint は `ruff check`（既存の指摘が 50 件ほどある。増やさない）
- コミットとPRの末尾には、そのセッションで指定された署名を付ける。モデル名は書かない
- サムネを作り直して YouTube に反映するとき:
  1. `thumbpanel.render(cfg, spec, out)` で描く（組み立ての例は `yt-econ/thumbnails/replace/main_specs.json`）
  2. `yt-econ/thumbnails/replace/main/<動画ID>.jpg` に置いて PR → マージで自動差し替え

## 決まった方針（ユーザーの好み）

### サムネ（経済・panel）
- **空白を作らない**。空いた場所は自動で小物（絵文字）が埋める（`fill_gaps`）
- **初心者が一瞬で分かる言葉**。「7%」「1.25%」のような何の率か分からない数字や、「実質」「料率」などの専門語は見出しに使わない。
  使う数字は「月1300円」「1ドル158円」「億」のような暮らしの実感があるものだけ
- 見出しは「〜なのに〜？」の疑問形が良い（例: 「減税なのに食費は減らない？」）。11 字まで
- 人の写真は 1 枚に 2 人まで。人とずんだもんを重ねない。字は角ゴシックの極太（NotoSansJP-Black）
- 背景は極端な色（gold / red / blue / dark / glitch）。写真はぱくたそ（出典を概要欄に書く）
- 矢印「→」は字体に無いので使わない（自動で「から」に置き換わる）

### Shorts
- 長さは今の 30〜40 秒のまま。**45 秒に伸ばすのは無し**（ユーザーが却下）
- 最後の「続きは本編で」は入れない（ループする終わり方）

### タイトル
- 検索される語を頭に。【】の引きは本題の後ろ。煽り語は使わない

## 保留中・やりかけ

- **地理雑学チャンネル（40 代以上向け）**: 試作だけ。`yt-econ/src/ytecon/geo.py`
  - 衛星写真（EOX）・空中写真（国土地理院）の地図、番号つき見出し、BIZ UDPゴシック
  - サンプル台本は `yt-econ/reference/geo_sample_kitayama.json`（北山村）
  - **声は Google（Gemini TTS）にしたい**とユーザーが希望。`GEMINI_API_KEY` を環境変数と GitHub Secrets に登録してもらう必要がある
    （まだ。キーをチャットに貼ってもらってはいけない）。キーが無いと VOICEVOX で読む
  - ユーザーが参考にしたい Instagram のリール（https://www.instagram.com/reel/DdypTtRsFSm/）はログインが必要で見られなかった。
    画面収録かスクショを送ってもらう約束
  - チャンネル開設・自動投稿への組み込みはまだ
- **9/29 に手で上げた「生涯賃金」の追加 Shorts 3 本**（p5lbdiMNsgA / AY4Dk4NjBrw / n4tsdwWzaQE）に要約コメントが付いていない
  （状態 DB に無いため自動で付かない）。下書きは渡した。手で付けるか、Actions で付ける仕組みを足すかはユーザー次第
- **bro の動画編集**: ユーザーの指示で保留
- 新チャンネル案として、赤ちゃん向け・海外の切り抜きも挙がっている（未着手）

## 主なファイル（yt-econ/src/ytecon/）

- `pipeline.py` 1 日分の流れ / `script.py` 台本 / `tts.py` 声 / `shorts.py` Shorts / `quiz.py` 心理学の Shorts
- `thumbnail.py` サムネの入口（style で振り分け）/ `thumbpanel.py` 経済のコマ割りサムネ
- `metadata.py` タイトル・概要欄 / `youtube.py` 投稿・クォータ / `comments.py` 要約コメント
- `goals.py` 目標の進捗 / `compilation.py` 週 1 の総集編 / `geo.py` 地理の試作
- ドキュメントは `yt-econ/docs/`（自動投稿の始め方、心理学チャンネルの引き継ぎ など）
