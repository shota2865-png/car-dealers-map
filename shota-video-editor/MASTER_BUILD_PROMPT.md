# Shota Video Editor v1.0 — Master Build Prompt

> このファイルの本文（`=== PROMPT START ===` から `=== PROMPT END ===` まで）を
> 空の Claude Code セッションにそのまま貼り付けてください。
> 貼り付け先は、このシステムを置きたい空ディレクトリです。
>
> 一発で全部作らせません。**7つのフェーズに分け、各フェーズの最後に
> 受け入れテストを置いています。** テストが通るまで次へ進ませない構成です。

---

=== PROMPT START ===

あなたはこれから、ローカルで実際に動く動画編集システム
**Shota Video Editor (SVE)** を構築します。

これは「動画を編集してください」という依頼ではありません。
**編集作業を自動化するシステムそのものを実装する**タスクです。
あなたが書くのはコードと仕様であり、動画処理は FFmpeg / WhisperX /
Remotion が行います。

## 0. 大原則

1. **段階を飛ばさない。** 下の Phase 1 から順に実装し、各 Phase 末尾の
   受け入れテストを通してから次へ進む。テストが落ちたら次へ行かない。
2. **原素材を絶対に上書きしない。** `input/` は読み取り専用として扱う。
   各段階は必ず別ファイルを出力する。
3. **判断とレンダリングを分離する。** AI の編集判断は必ず JSON に書き出し、
   レンダラはその JSON だけを読む。動画バイナリを直接いじる判断コードを書かない。

   ```
   raw.mp4 → WhisperX → words.json → (AI判断) → edit_plan.json → FFmpeg → cut.mp4
   ```

   こうしておくと、後から「なぜここを切ったか」を人間が検証でき、
   やり直しも JSON の修正だけで済む。
4. **各段階は再実行可能にする。** `state.json` に進捗を持ち、既に完了した
   段階はスキップする。途中で落ちても続きから走ること。
5. **わからないことを推測で埋めない。** 外部ツールの API が不確かなときは、
   実際に `--help` を叩くか公式ドキュメントを確認してから書く。
   確認できなければその旨をコメントに残し、人間に質問する。

## 1. 技術選定（固定）

| 役割 | ツール | 備考 |
|---|---|---|
| オーケストレーション | Python 3.11+ | CLI は `sve` |
| 文字起こし・word timestamp | WhisperX | `large-v3`、`--language ja` |
| 動画処理・切り貼り・合成 | FFmpeg | フレーム精度が要るので再エンコード前提 |
| グラフィック | Remotion (React + TypeScript) | **アルファ付きで別レイヤーに出す**（後述） |
| プレビュー | Remotion Studio / ffplay | |
| 字幕 | words.json から自作フォーマッタ | 再文字起こしはしない |

**グラフィックレンダラは差し替え可能にすること。** `renderers/` 配下に
`RemotionRenderer` を実装し、`GraphicsRenderer` という抽象インターフェース
（`render(plan, out_path, frame_range=None) -> Path`）を切る。
HyperFrames など他のレンダラを後から足せるようにする。
**HyperFrames の API を推測で書かないこと。** 使う場合は実際のドキュメントを
確認してから実装する。未確認なら Remotion だけを実装する。

## 2. ディレクトリ構造

```text
shota-video-editor/
├── CLAUDE.md                   # 総監督。編集哲学と Pipeline
├── README.md
├── pyproject.toml
├── requirements.txt
│
├── input/                      # 原素材（読み取り専用）
├── output/                     # 最終成果物
│
├── projects/                   # 1本ごとの作業領域
│   └── <project_id>/
│       ├── state.json          # 各段階の進捗
│       ├── project.json        # intake の解析結果
│       ├── transcript/
│       │   ├── words.json
│       │   ├── segments.json
│       │   └── transcript.txt
│       ├── plans/
│       │   ├── rough_cut_plan.json
│       │   ├── graphics_plan.json
│       │   └── caption_plan.json
│       ├── renders/
│       │   ├── rough_cut.mp4
│       │   ├── graphics.mov      # アルファ付きグラフィックレイヤー
│       │   ├── composite.mp4
│       │   └── preview/          # 部分レンダリング結果
│       └── reports/
│           └── qc_report.md
│
├── skills/                     # 各工程の仕様書（人間もAIも読む）
│   ├── intake/SKILL.md
│   ├── transcribe/SKILL.md
│   ├── rough-cut/SKILL.md
│   ├── graphics/SKILL.md
│   ├── refine/SKILL.md
│   ├── captions/SKILL.md
│   ├── audio/SKILL.md
│   ├── qc/SKILL.md
│   └── export/SKILL.md
│
├── presets/
│   ├── brand.json              # フォント・色・アニメーション
│   ├── captions.json
│   ├── audio.json
│   └── dictionary.json         # 固有名詞の表記ゆれ補正
│
├── assets/
│   ├── fonts/
│   ├── music/
│   ├── logos/
│   └── images/
│
├── reference/                  # 過去動画から学ぶ用
│   ├── STYLE_GUIDE.md          # 自動生成
│   └── editing_preset.json     # 自動生成
│
├── src/sve/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── state.py
│   ├── probe.py                # ffprobe ラッパ
│   ├── intake.py
│   ├── transcribe.py
│   ├── silence.py              # ★カット点のスナップ（後述・最重要）
│   ├── roughcut.py
│   ├── ffmpeg_cut.py
│   ├── graphics.py
│   ├── captions.py
│   ├── audio.py
│   ├── qc.py
│   ├── export.py
│   ├── partial.py              # 部分レンダリング
│   ├── llm.py                  # Claude API ラッパ
│   └── renderers/
│       ├── base.py
│       └── remotion.py
│
├── remotion/                   # Remotion プロジェクト
│   ├── package.json
│   ├── remotion.config.ts
│   └── src/
│       ├── Root.tsx
│       ├── GraphicsTrack.tsx   # graphics_plan.json を読んで並べる
│       └── components/
│           ├── Title.tsx
│           ├── Subtitle.tsx
│           ├── Comparison.tsx
│           ├── Quote.tsx
│           ├── ImagePopup.tsx
│           ├── Counter.tsx
│           ├── BulletList.tsx
│           └── LowerThird.tsx
│
└── tests/
```

---

## Phase 1 — 基盤・Intake・state

### 実装するもの

`src/sve/config.py`, `state.py`, `probe.py`, `intake.py`, `cli.py` の骨格。

**state.json のスキーマ**

```json
{
  "project_id": "20260919-malaysia",
  "source": "input/raw_video.mp4",
  "source_sha256": "…",
  "stages": {
    "intake":     {"status": "done",    "at": "2026-09-19T10:00:00Z", "output": "project.json"},
    "transcribe": {"status": "pending", "at": null, "output": null},
    "rough_cut":  {"status": "pending", "at": null, "output": null},
    "rough_cut_approved": {"status": "pending", "at": null, "output": null},
    "graphics_plan": {"status": "pending"},
    "graphics_render": {"status": "pending"},
    "captions":   {"status": "pending"},
    "audio":      {"status": "pending"},
    "qc":         {"status": "pending"},
    "export":     {"status": "pending"}
  },
  "locks": {
    "rough_cut": null
  }
}
```

`status` は `pending` / `running` / `done` / `failed` のいずれか。

**project.json のスキーマ**（intake の出力）

```json
{
  "source": "input/raw_video.mp4",
  "duration": 812.42,
  "resolution": "3840x2160",
  "width": 3840,
  "height": 2160,
  "fps": 30.0,
  "video_codec": "hevc",
  "audio_codec": "aac",
  "audio_channels": 2,
  "audio_sample_rate": 48000,
  "orientation": "landscape",
  "mean_volume_db": -24.3,
  "max_volume_db": -1.2,
  "language": "ja",
  "content_type": "talking_head",
  "warnings": ["音声が2chだがモノラル相当。1chに落として文字起こしする"]
}
```

`mean_volume_db` / `max_volume_db` は `ffmpeg -af volumedetect` から取る。

### 注意点

- `ffprobe` の `r_frame_rate` は `"30000/1001"` のような分数で返る。必ず割る。
- 可変フレームレート (VFR) の素材はカットがずれる。`avg_frame_rate` と
  `r_frame_rate` が食い違ったら warning に出し、`export` 時に CFR へ固定する。
- 縦動画・4K・HDR を判定して warnings に入れる。

### 受け入れテスト

`tests/test_intake.py`

```
1. FFmpeg で 10 秒のテスト動画を生成する（testsrc + sine）
2. sve intake を走らせる
3. project.json の duration が 10.0 ± 0.1、fps が正しいこと
4. state.json の stages.intake.status が "done" になること
5. もう一度 intake を走らせても再計算せずスキップすること
6. --force を付けたら再計算すること
```

---

## Phase 2 — WhisperX と、日本語で必ず踏む問題

### 実装するもの

`src/sve/transcribe.py`

```bash
whisperx "<audio.wav>" \
  --model large-v3 \
  --language ja \
  --output_format json \
  --output_dir <transcript/>
```

音声は事前に FFmpeg で `-ac 1 -ar 16000 -c:a pcm_s16le` の wav に落とす。

**words.json のスキーマ**

```json
[
  {"word": "今日は", "start": 1.24, "end": 1.58, "score": 0.91},
  {"word": "マレーシア", "start": 1.61, "end": 2.14, "score": 0.88}
]
```

**segments.json のスキーマ**

```json
[
  {"id": 0, "start": 1.24, "end": 6.80, "text": "今日はマレーシアについて話します。",
   "words": [0, 1, 2, 3]}
]
```

`words` は words.json のインデックス配列。

### ★ここが日本語で一番危ない

**WhisperX の日本語 word timestamp は「単語」の境界ではありません。**
アライメントは wav2vec2 のかな単位で行われ、漢字混じり表記では
**境界が数十〜百数十ミリ秒ずれます。**
この値をそのまま信じてカットすると、語頭が欠けたり、次の語の頭が
混入したりします。

対策を必ず実装してください。

1. `score`（アライメント信頼度）を保持し、`score < 0.5` の語は
   カット境界に使わない。
2. **すべてのカット点を、実際の無音にスナップする**（Phase 3 で使う）。
3. 語頭・語尾にパディングを入れる（Phase 3 で規定）。

`src/sve/silence.py` を実装する。

```python
def detect_silences(audio_path, noise_db=-32.0, min_dur=0.15) -> list[tuple[float, float]]:
    """ffmpeg silencedetect で無音区間 [(start, end), ...] を返す。"""
    # ffmpeg -i in.wav -af silencedetect=noise=-32dB:d=0.15 -f null -
    # stderr の silence_start / silence_end をパースする

def snap_to_silence(t: float, silences, max_shift=0.25, prefer="center") -> float:
    """カット点 t を、max_shift 秒以内で最も近い無音区間へ寄せる。

    寄せ先が無ければ t をそのまま返す（失敗させない）。
    prefer="center" なら無音区間の中央、"start"/"end" なら端。
    """
```

**この層があるかないかで、仕上がりが決定的に変わります。**
アライメント誤差を波形という一次情報で吸収する仕組みです。

### 受け入れテスト

`tests/test_silence.py`

```
1. 「音1秒 → 無音0.8秒 → 音1秒」の wav を FFmpeg で合成する
2. detect_silences が 1 区間を返し、その範囲が [1.0, 1.8] ± 0.1 であること
3. snap_to_silence(1.05, ...) が無音区間の中に寄ること
4. snap_to_silence(5.0, ...) は近傍に無音が無いので 5.0 のまま返ること
5. 無音が1つも無い音声でも例外を投げずに入力値を返すこと
```

`tests/test_transcribe.py` は WhisperX の実行が重いので、
**保存済みの WhisperX 出力サンプルを fixture に置いて**パース処理だけ検証する。
（実機での疎通は `sve transcribe --smoke` で人間が1回やる）

---

## Phase 3 — Rough Cut（システムの心臓）

### 入力と出力

```
words.json + segments.json + silences → (Claude判断) → rough_cut_plan.json → FFmpeg → rough_cut.mp4
```

**rough_cut_plan.json のスキーマ**

```json
{
  "source": "input/raw_video.mp4",
  "source_duration": 812.42,
  "generated_at": "2026-09-19T10:20:00Z",
  "padding": {"before": 0.08, "after": 0.12},
  "keep": [
    {
      "id": 0,
      "source_start": 12.43,
      "source_end": 18.92,
      "snapped_start": 12.39,
      "snapped_end": 19.01,
      "transcript": "今日はマレーシアの物価について話します。",
      "reason": "final_successful_take",
      "confidence": 0.93
    }
  ],
  "removed": [
    {
      "source_start": 8.10,
      "source_end": 12.43,
      "transcript": "今日はマレ… あ、ごめんもう一回",
      "reason": "failed_take",
      "confidence": 0.97
    }
  ],
  "stats": {
    "source_duration": 812.42,
    "output_duration": 623.8,
    "removed_duration": 188.62,
    "cut_count": 47
  }
}
```

`reason` は次の enum に限定する。

```
failed_take / repeated_take / restart / long_silence / filler
accidental_sound / technical_interruption / off_topic
```

### 削除アルゴリズムの仕様

#### (a) 無音

**0.6 秒を超える無音**を削除候補にする。ただし次は削除しない。

- 直前の文が疑問形で終わっている（間を置いている）
- 直前 2 秒以内に笑い・感嘆がある
- セクションの切れ目（話題転換の直前）で 1.0 秒以内

削除するときは、**無音を全部消さず 0.25 秒残す。** ゼロにすると
機関銃のような不自然な話し方になります。

#### (b) 日本語のフィラー

判定対象の語（`presets/filler.json` に外出しする）。

```json
{
  "always_remove": ["えー", "えーと", "えっと", "ええと", "あー", "うーん", "んー"],
  "conditional": ["あの", "その", "まあ", "なんか", "ちょっと", "やっぱり", "っていうか"],
  "never_remove": ["つまり", "要するに", "ただ", "でも", "しかも"]
}
```

**`conditional` は文脈で意味が変わるので、無条件に消してはいけません。**
次の条件を**すべて**満たすときだけ削除する。

1. 前後が 0.2 秒以上の無音で挟まれている（＝言い淀みとして独立している）
2. 直前の語との係り受けがない（「あの本」の「あの」は連体詞なので残す）
3. その文の中で同じ語が 2 回以上出ている

「なんか」は若年層の話し言葉では意味を持つことがあるので、
**1文に3回以上出た場合のみ2回目以降を削る**という上限方式にする。

#### (c) 言い直し・リテイクの検出

```python
def detect_retakes(segments) -> list[list[int]]:
    """同じ内容を複数回言っているセグメント群を返す。

    1. 連続する、または 10 秒以内に出現するセグメント対を比較する
    2. カタカナ・ひらがなに正規化し（読みベース）、記号を落とす
    3. difflib.SequenceMatcher の ratio が 0.80 以上ならリテイク候補
    4. 「あ」「ごめん」「もう一回」「すみません」を含むセグメントは
       その直後を「やり直し後の本番」とみなす強いシグナル
    """
```

採用の判断は次の順で行う。

1. **文として完結しているか**（述語で終わっているか）
2. 言い淀みが少ないか
3. 途中で切れていないか
4. 上記が同点なら**最後のテイクを採る**

**同じ内容が2つ残る状態を絶対に作らない。**

#### (d) カットのパディング

```
語頭の前:  80ms
語尾の後: 120ms
```

日本語は語尾の母音が伸びるので、**後ろを厚めに取ること。**
`snap_to_silence` でスナップした結果パディングが無音に食い込むのは問題ない。
逆に、パディングを足した結果**隣のカットと重なったら、中点で分割する。**

#### (e) 最低カット長

0.4 秒未満のカットは作らない。前後のどちらかに吸収する。
短すぎるカットの連続は見ていて不快になります。

### Claude への渡し方

トランスクリプト全体を一度に渡すと長すぎるので、
**5分ごとのチャンクに分割**し、前後 30 秒をオーバーラップさせて渡す。
チャンク境界をまたぐリテイクを取りこぼさないため。

出力は `output_config.format` の json_schema で強制する。

### FFmpeg での切り出し

`src/sve/ffmpeg_cut.py`

**`-c copy` を使わないこと。** キーフレームにスナップされてカット点がずれます。

カット数が 100 以下なら単一の `filter_complex` で1パス。

```
[0:v]trim=start=12.39:end=19.01,setpts=PTS-STARTPTS[v0];
[0:a]atrim=start=12.39:end=19.01,asetpts=PTS-STARTPTS[a0];
… 各カット …
[v0][a0][v1][a1]…concat=n=<N>:v=1:a=1[outv][outa]
```

100 を超えるなら、セグメントを個別に書き出してから concat demuxer で連結する
（filter graph が巨大になると FFmpeg が落ちるため）。
その際、各セグメントは同一のエンコード設定で出すこと。

### 受け入れテスト

`tests/test_roughcut.py`

```
1. 既知の words.json fixture から、フィラー判定が仕様通り動くこと
   - "えー" は単独無音に挟まれていれば消える
   - "あの" は「あの本」の文脈では消えない
   - "なんか" は1文2回までは残り、3回目以降が消える
2. detect_retakes が、意図的に重複させた fixture で 1 グループを返すこと
3. パディング適用後にカットが重なったら中点分割されること
4. 0.4秒未満のカットが生成されないこと
5. rough_cut_plan.json の keep が時間順に並び、重複区間が無いこと
6. stats.output_duration が keep の合計と一致すること
7. 実際に10秒のテスト動画から2区間を切り出し、出力が想定尺 ±0.1秒になること
```

**7 は必ず実機の FFmpeg で検証すること。** 計画だけ正しくても意味がない。

---

## Phase 4 — Rough Cut のロック

グラフィックはタイムライン上の秒数に貼り付きます。
**rough cut を後から変更すると、以降の全グラフィックがずれます。**

`sve lock-roughcut` を実装する。

1. `rough_cut.mp4` の sha256 を計算する
2. `state.json.locks.rough_cut` に `{"sha256": "...", "at": "...", "duration": 623.8}` を書く
3. 以降、graphics 系のコマンドは起動時にこのハッシュを照合する
4. 不一致なら**エラーで停止**し、「rough cut が変更されています。
   `sve unlock-roughcut` で解除し、graphics_plan を作り直してください」と出す

`CLAUDE.md` にも「rough cut が承認されるまで graphics に進まない」と明記する。

---

## Phase 5 — Graphics

### graphics_plan.json のスキーマ

**時間はすべて rough cut のタイムライン基準**（原素材の時間ではない）。

```json
{
  "timeline": "rough_cut",
  "duration": 623.8,
  "fps": 30,
  "resolution": [3840, 2160],
  "items": [
    {
      "id": "g001",
      "start": 18.2,
      "end": 21.5,
      "type": "title",
      "props": {"text": "なぜマレーシアなのか", "position": "center"},
      "reason": "セクションの開始を示す",
      "anchor_text": "なぜマレーシアなのか、という話をします"
    },
    {
      "id": "g002",
      "start": 42.1,
      "end": 48.3,
      "type": "comparison",
      "props": {
        "left": {"label": "日本", "value": "家賃 8万円"},
        "right": {"label": "マレーシア", "value": "家賃 3万円"}
      },
      "reason": "2つの数字の対比を視覚化する",
      "anchor_text": "日本だと8万円、マレーシアだと3万円"
    }
  ]
}
```

`anchor_text` は必須。**どの発言に紐づくグラフィックかを残す**ためで、
rough cut が変わったときに再配置の手がかりになります。

`type` の enum は Remotion のコンポーネント名と 1:1 対応させる。

```
title / subtitle / lower_third / comparison / quote
image_popup / counter / bullet_list / diagram / zoom
```

### 配置ルール（Claude に守らせる）

- **画面を賑やかにするためだけに入れない。** 次のいずれかに当てはまるときだけ:
  情報を明確にする / 重要な発言を強調する / 抽象を可視化する /
  無変化が長く続くのを防ぐ / 言及された物・場所・人を見せる / 列挙や比較を分かりやすくする
- 同時に画面に出るグラフィックは **2つまで**
- 最短表示時間 **1.2秒**（それ未満は読めない）
- 前のグラフィックが消えてから **0.5秒以上**空ける（連続で出さない）
- 話者の顔の領域を避ける。`presets/brand.json` の `safe_margin` と
  `speaker_zone`（顔が映る矩形。intake で推定するか手で設定）を尊重する

### レンダリング方式（重要な設計判断）

**グラフィックを rough cut に焼き込まず、アルファ付きの別レイヤーとして出す。**

```
rough_cut.mp4  ─┐
                ├─ FFmpeg overlay ─→ composite.mp4
graphics.mov   ─┘   (アルファ付き)
```

Remotion 側:

```bash
npx remotion render GraphicsTrack out/graphics.mov \
  --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le
```

FFmpeg 側:

```
[0:v][1:v]overlay=0:0:format=auto[outv]
```

**なぜこうするか:** グラフィックを1つ直すたびに本編を再エンコードすると
4K では現実的な時間で回りません。レイヤーを分ければ、グラフィックだけ
差し替えて overlay をやり直すだけで済みます。

ProRes 4444 は容量を食うので、ディスクが厳しければ
VP9 + `yuva420p` (webm) に切り替えられるようにする。

### Remotion コンポーネントの要件

- すべて `presets/brand.json` から色・フォント・アニメーションを読む。
  **コンポーネント内に色やフォント名をハードコードしない。**
- `spring()` / `interpolate()` でアニメーションする。
- 日本語フォントは `assets/fonts/` から `@font-face` で読む。
  Remotion では `delayRender()` / `continueRender()` でフォント読み込みを待つこと。
  **待たずにレンダリングするとフォントが反映されないフレームが出ます。**
- 各コンポーネントは props の型を TypeScript で定義し、
  その型から JSON Schema を生成して Python 側の検証に使う
  （`graphics_plan.json` の props が壊れていたらレンダリング前に落とす）。

### presets/brand.json

```json
{
  "font_primary": "Noto Sans JP",
  "font_weights": {"title": 900, "subtitle": 700, "caption": 700, "body": 500},
  "colors": {
    "bg": "#0E1525", "surface": "#18223A", "text": "#F2F5FA",
    "accent": "#4CC2FF", "accent2": "#FFC857",
    "positive": "#5BD99A", "negative": "#FF6B6B"
  },
  "title_animation": "scale_in",
  "default_transition": "ease_out",
  "transition_duration_frames": 8,
  "safe_margin": 0.08,
  "speaker_zone": {"x": 0.30, "y": 0.10, "w": 0.40, "h": 0.75},
  "graphics_density": "medium",
  "max_concurrent_graphics": 2,
  "style": "clean energetic documentary"
}
```

### 受け入れテスト

```
1. graphics_plan.json のバリデータが、
   - 同時表示3つ以上
   - 表示時間1.2秒未満
   - speaker_zone と重なる配置
   をすべてエラーとして検出すること
2. Remotion で 3 秒 × Title コンポーネントをアルファ付き出力し、
   ffprobe で pix_fmt にアルファが含まれること
3. overlay 合成後の動画の尺が rough_cut と一致すること
4. brand.json の accent を変えたら、出力画像の色が変わること
   （フレームを1枚抜いてピクセル値を比較する）
```

**4 は「ハードコードしていないこと」の証明なので必ず入れる。**

---

## Phase 6 — Second Pass と部分レンダリング

### 修正指示の受け方

人間はこう書きます。

```
At 00:34 the graphic covers my face.
Move it to the lower-left safe area.
Keep the existing animation and duration.
Do not modify any other segment.
```

これを `graphics_plan.json` の**該当 item だけ**書き換える形で処理する。
実装として:

1. 時刻から対象 item を特定する
2. 変更を適用し、`plans/graphics_plan.json` を更新する
3. **変更前の plan を `plans/history/graphics_plan.<timestamp>.json` に退避**する
4. 影響範囲を計算し、そこだけ再レンダリングする

### 部分レンダリング

`src/sve/partial.py`

```python
def affected_range(old_plan, new_plan, context=2.0) -> tuple[float, float] | None:
    """変更された item を洗い出し、影響時間範囲に前後 context 秒を足して返す。
    変更が無ければ None。"""
```

Remotion はフレーム範囲を指定して部分レンダリングできる。

```bash
npx remotion render GraphicsTrack out/preview/seg_0319_0328.mov \
  --frames=<start_frame>-<end_frame> \
  --codec=prores --prores-profile=4444
```

FFmpeg 側も該当範囲だけ切り出して overlay し、
`renders/preview/` に置く。**20分動画全体を再レンダリングしない。**

`CLAUDE.md` に次を明記する。

```
## Partial Render Policy

修正作業中は、必要が無い限り動画全体をレンダリングしない。

1. 影響を受ける時間範囲を特定する
2. 前後に2秒のコンテキストを足す
3. その範囲だけをプレビューとして出力する
4. 最終承認後にのみフルレンダリングを行う
```

### 受け入れテスト

```
1. affected_range が、1 item だけ変えた plan で正しい範囲を返すこと
2. 変更なしの plan では None を返すこと
3. 隣接する2 item を変えたら、範囲がマージされて1つになること
4. 実際に部分プレビューを生成し、尺が (範囲 + 4秒) になること
5. history/ に変更前の plan が残ること
```

---

## Phase 7 — Captions / Audio / QC / Export

### Captions

**再文字起こしをしない。** `words.json` を整形するだけ。

`presets/captions.json`

```json
{
  "max_lines": 2,
  "chars_per_unit_min": 8,
  "chars_per_unit_max": 16,
  "min_duration": 0.7,
  "max_duration": 4.0,
  "highlight_keywords": true,
  "no_split_patterns": ["[0-9]+(円|ドル|%|パーセント|年|月|日|人|倍)"],
  "position": "bottom",
  "margin_bottom": 0.10
}
```

分割ルール:

- 1ユニット 8〜16 文字、最大 2 行
- **固有名詞・複合語・「数値＋単位」を跨いで割らない**
- 文節境界（助詞の直後）を優先して割る
- フィラーはハイライトしない

`presets/dictionary.json` で誤認識を補正する。

```json
{
  "ヘルプ大学": "HELP University",
  "立教": "立教大学",
  "クイーンズランド": "University of Queensland",
  "Chat GPT": "ChatGPT",
  "チャット GPT": "ChatGPT",
  "クロード コード": "Claude Code"
}
```

補正は**単語単位ではなく連続する語をまたいでマッチ**させること
（WhisperX は「チャット」「GPT」と分割して出すことがある）。
置換時は、元の語群の start / end を引き継ぐ。

出力は `captions.srt`（YouTube 投稿用）と `captions.ass`（焼き込み用）の両方。

### Audio

`presets/audio.json`

```json
{
  "voice_target_lufs": -16.0,
  "final_target_lufs": -14.0,
  "true_peak_db": -1.5,
  "music_below_voice_lu": 15.0,
  "fade_in": 1.5,
  "fade_out": 2.0,
  "ducking": true,
  "duck_ratio": 8,
  "duck_attack_ms": 20,
  "duck_release_ms": 400
}
```

**BGM の音量を固定 dB で決めないこと。**
参考動画で -23 dB だったのは、その素材の声量に対する相対値です。
素材ごとに声の LUFS を測り、**そこから `music_below_voice_lu` だけ下げる**
という決め方にする。

```
1. 声トラックの integrated LUFS を ffmpeg loudnorm の解析パスで測る
2. BGM を (voice_lufs - music_below_voice_lu) になるようゲイン調整する
3. sidechaincompress で声に合わせてダッキングする
4. 最終ミックスを loudnorm=I=-14:TP=-1.5:LRA=11 で整える
```

FFmpeg のダッキング例:

```
[music]volume=<計算値>dB,afade=t=in:d=1.5,afade=t=out:st=<末尾-2>:d=2[bgm];
[voice][bgm]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[mixed];
[mixed]loudnorm=I=-14:TP=-1.5:LRA=11[aout]
```

### QC

`src/sve/qc.py` — **すべて自動判定できる形にする。** 目視項目を作らない。

| 分類 | チェック | 実装 |
|---|---|---|
| 編集 | 語頭・語尾の切れ | 各カット境界の前後 60ms が閾値以下の音量か |
| 編集 | 重複テイクの残存 | 最終トランスクリプトで類似度 0.85 超の文対が無いか |
| 編集 | 不自然な長無音 | 出力音声に 1.5 秒超の無音が無いか |
| 編集 | 唐突な終わり | 末尾 1 秒が無音で終わっているか |
| グラフィック | safe area 逸脱 | plan の座標と brand.json の照合 |
| グラフィック | 顔との重なり | plan の bbox と speaker_zone の交差判定 |
| グラフィック | 重なり | 同時刻の item の bbox 交差 |
| 字幕 | 同期ずれ | 字幕の start と words.json の照合（±150ms） |
| 字幕 | 固有名詞 | dictionary.json のキーが未変換で残っていないか |
| 音声 | 声と音楽の差 | 区間 LUFS を測り差が 10 LU 以上あるか |
| 音声 | クリッピング | `astats` の peak_level が -1.0 dB 未満か |
| 映像 | 黒フレーム | `blackdetect` で 0.1 秒超の黒が無いか |
| 映像 | 解像度・fps | ffprobe と project.json の照合 |

`reports/qc_report.md` を生成する。
**critical が1つでもあれば export をブロックする。**
`warning` は通すが報告する。

### Export

```
output/
├── FINAL.mp4
├── thumbnail_frame.jpg      # 最も動きの少ない明るいフレームを自動選択
├── transcript.txt
├── captions.srt
├── edit_plan.json           # rough_cut_plan + graphics_plan をまとめたもの
└── qc_report.md
```

エンコード設定:

```
-c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p
-profile:v high -level 4.2
-c:a aac -b:a 320k -ar 48000
-movflags +faststart
```

4K なら `-crf 20`、可変フレームレート素材なら `-vsync cfr -r <fps>` を足す。

### 受け入れテスト

```
1. captions: 「1000円」を跨いで分割されないこと
2. captions: dictionary.json の連続語マッチが効くこと
   （"チャット" + "GPT" → "ChatGPT" で、start/end が引き継がれること）
3. audio: 声 -16 LUFS / 音楽 -31 LUFS の素材を合成し、
   最終出力が -14 LUFS ± 1.0 に収まること
4. qc: 意図的にクリッピングさせた音声で critical が立つこと
5. qc: critical があるとき export が例外で止まること
6. export: 出力の解像度・fps・音声サンプルレートが仕様通りであること
```

---

## CLI 仕様

```bash
sve init <project_id>              # projects/<id>/ を作る
sve intake [--force]
sve transcribe [--model large-v3]
sve roughcut                        # 計画生成 + レンダリング
sve roughcut --plan-only            # 計画だけ
sve review roughcut                 # プレビューを開く
sve lock-roughcut
sve graphics plan
sve graphics render
sve composite                       # rough_cut + graphics を overlay
sve fix "At 00:34 move the title to lower-left"   # Second Pass
sve preview 03:19-03:28             # 部分プレビュー
sve captions
sve audio
sve qc
sve export
sve run --until roughcut            # 複数段階を一気に
sve status                          # state.json を人間向けに表示
sve learn reference/                # 過去動画から STYLE_GUIDE.md を生成
```

すべてのコマンドが:

- `state.json` を更新する
- 既に done の段階は再実行しない（`--force` で上書き）
- 失敗したら `state.json` の status を `failed` にし、理由を残す

---

## CLAUDE.md に書く内容

プロジェクトルートの `CLAUDE.md` に、次を必ず含めてください。

```md
# SHOTA VIDEO EDITOR

あなたは自律的な動画編集システムです。
撮影済みのトーキングヘッド素材を、話者の人柄と自然な話し方を保ったまま
公開可能な動画に仕上げます。

## Pipeline

必ずこの順で処理する。段階を飛ばさない。

1. Intake
2. Transcription
3. Rough Cut
4. Rough Cut Review（人間の承認が必要）
5. Graphics Planning
6. Graphics Rendering
7. Graphics Refinement
8. Captions
9. Audio / Music
10. Final QC
11. Export

## 編集哲学

**圧縮率より話者の人柄を優先する。**

削除するもの:
- 事故的な無音
- 失敗テイク
- 繰り返したテイク
- 不要なフィラー
- 技術的なミス
- 明らかな言い直し

残すもの:
- 意図的な間
- 冗談
- 感情の動き
- 人柄
- 日本語として自然なリズム
- 語りのための間

同じ文が複数回ある場合、原則として最後の成功テイクを採る。
ただし前のテイクが明らかに良ければそちらを残す。

くだけた言い方であることを理由に削除しない。

## 安全規則

原素材を上書きしない。各段階は必ず別ファイルを出力する。

## 品質規則

rough cut が承認されるまで graphics に進まない。
グラフィックは理解を助けるためのもので、話者の邪魔をしてはならない。

## Partial Render Policy

修正作業中は動画全体をレンダリングしない。
影響範囲 + 前後2秒だけをプレビューする。
フルレンダリングは最終承認後のみ。
```

---

## 実装の進め方

1. Phase 1 から順に実装する
2. 各 Phase の受け入れテストを `tests/` に書き、**実際に走らせて通す**
3. 通ったら `git commit` する（Phase ごとに1コミット）
4. 次の Phase へ進む前に、その Phase で何を作り何を確認したかを1段落で報告する

**7つの Phase を一度に実装しようとしないこと。**
Phase 1 が終わったら一度止まり、報告してください。

外部ツール（WhisperX / Remotion）のインストールが必要な段階では、
まず `--version` で導入済みか確認し、無ければインストール手順を提示して
人間の判断を仰いでください。勝手に大きなモデルをダウンロードしない。

=== PROMPT END ===
