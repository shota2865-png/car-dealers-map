# 引き継ぎメモ（新しいチャットはまずここを読む）

このリポジトリの本体は `yt-econ/`。YouTube チャンネルを台本から投稿まで全自動で回している。
ほかのフォルダ（`ai-video-system/` `oyasumi/` `shota-video-editor/` `index.html`）は別件で、今は触らない。

`yt-clip/` は別チャンネル「むらいクリップ【切り抜き】」（マックスむらいの切り抜き Shorts、毎日 10 本を 18:00 に予約）。
`.github/workflows/clip-daily.yml` が動かす。Claude のクレジットは使わない作り（場面選びは YouTube のチャット・ヒートマップ・字幕、タイトルと字幕の校正は Gemini API の無料枠）。詳しくは `yt-clip/README.md`。

ユーザーとは日本語で、短く・専門用語を避けて話す。数字や結果は先に言う。

## チャンネルと目標

| | おやすみ経済学（main） | 現代人のための心理学（psych） |
|---|---|---|
| チャンネル ID | UCuOZdUBs68sCzq1g6GS9PIQ | UCv-h9prBycfOLwu2ieAUcMw |
| 設定 | `yt-econ/config/channel.yaml` | `yt-econ/config/channels/psych/channel.yaml`（本体を extends） |
| 本編の公開 | 毎日 19:00 | 毎日 19:00（経済と同じ） |
| Shorts | 本編 1 本につき 6 本（21:30 / 0:00 / 7:00 / 12:00 / 15:00 / 18:00） | 6 本（経済と同じ時刻）。約 45 秒、めたんは出さず字幕だけ（経済の Shorts と同じ大きさ・細い字） |
| 声 | VOICEVOX（ずんだもん＆めたんの掛け合い） | VOICEVOX（四国めたんの一人語り）。**本編だけ**ニュース調（速さ 1.22・BGM は Mixkit「Delayed Flight」。公開リポジトリなので曲は置かず `render.bgm.url` から取得）。**Shorts は今までどおり**（1.02・Hush Move＝`shorts.bgm_file`） |
| 見た目 | ずんだもん解説の定番 | **宇宙の解析室**（`video.design: lab` + `lab_style: space`）。背景はショーリールと同じ three.js の星空ドームと光る床（`assets/space/loop_a.mp4`、冒頭と章の扉は 4D テッセラクト入りの `loop_b.mp4`。元は `motion/space_loop.html`）。データは床に立つ光る角柱＋「約N倍」、判定はメーター、右にめたん（口パク）、下に細い字の字幕。**上の帯・ホログラムの絵・字幕の左の棒は出さない**（ユーザーが「ごちゃごちゃ」と却下）。字は M PLUS 1＋英字 Orbitron＋数字 Oxanium |
| 番組の型 | 経済の掛け合い解説。**本編は YouTube で検索されている言葉から作る**（`topics.search_first`・`searchdemand.py`。暮らしのお金の言葉の検索候補から 1 つ選び、その疑問に答える。タイトルの頭もその言葉。冒頭 2 文で答えを言い切る）。ニュースは材料 | **今日の問い 1 つを、4 つの検証で順に答える**（①本当に起きる？②なぜ？③どんなとき？④どうする？。各章: 扉 → クイズ → 説明 → データ → 判定。章の中は同じ例え・前に戻らない）。本編は 20 分以内（`honpen.max_chars`） |
| サムネ | `panel`（コマ割り＋ぱくたそ写真＋ずんだもん）を自動生成 | 手で作ったもの優先、無い日は `lab`（大きな問い＋「データで検証」＋めたん） |

- 目標（10/31 まで）: 登録者 100 人・本編の総再生時間 500 時間・Shorts 平均 1 万回（`config/goals.yaml`）
- 経済は毎週日曜に「睡眠用・1週間まとめ」の総集編を 22:00 公開（`compilation.py`）

## 自動で動いているもの（GitHub Actions）

| ワークフロー | いつ | 何を |
|---|---|---|
| `daily-upload.yml` | 毎日（cron 01:10 UTC。実際は JST 15:30〜16:00 ごろ始まる） | 2 チャンネルぶん、本編 1 本＋Shorts 6 本を作って予約投稿。目標の進捗レポートもログに出る |
| `weekly-compilation.yml` | 日曜 JST 10:40 | 経済の 1 週間まとめを作って予約投稿 |
| `replace-thumbnails.yml` | `yt-econ/thumbnails/replace/<main\|psych>/<動画ID>.jpg` が main に入ったとき | その動画のサムネを差し替え |
| `publish-file.yml` | 手動 | 手で仕上げた mp4 を投稿 |

- daily-upload の中で、経済の**伸びなかった本編のタイトルを付け直す**（`retitle.py`。公開 3 日以上・100 回未満の本編を 1 日 1 本、検索されている言葉を頭に。元のタイトルは状態 DB の `stage.old_title`）

- **状態のキャッシュ（state.sqlite3 と goal）の path は変えない**。変えると前回までの状態が読めなくなる（10/7 に起きて心理学の本編が同じ日に 2 本予約された）
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
  1. `thumbpanel.render(cfg, spec, out)` で描く（組み立ての例は `yt-econ/reference/thumbnail_specs_main.json`）
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
- 経済: 長さは今の 30〜40 秒のまま。**45 秒に伸ばすのは無し**（ユーザーが却下）
- 心理学: **約 45 秒**（ユーザーの指定）。めたんの立ち絵は出さず、字幕だけ（大きさは経済の Shorts と同じ）
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
- 心理学: `honpen.py` 本編の台本（今日の問い 1 本の軸・4 つの検証）/ `wide.py` 本編の場面 / `labscene.py` 空間に置く場面（冒頭・章の扉・データ・判定）/ `space.py` 動く背景・立体グラフ・ホログラム / `lab.py` 枠（SpaceFrame）・めたん・字幕・サムネ
- ドキュメントは `yt-econ/docs/`（自動投稿の始め方、心理学チャンネルの引き継ぎ など）
