# 完全AI動画システム v2 — Master Build Prompt

撮影しない。音声も映像もすべて生成する。
既存の `yt-econ`（企画・台本・音声・投稿まで動く）を土台に、
**映像の質を上げるレイヤー**を足して完成させるための仕様です。

> `=== PROMPT START ===` から `=== PROMPT END ===` までを、
> `yt-econ` を含むリポジトリのルートで起動した Claude Code に貼ってください。

---

## この設計が、撮影編集システムと何が違うか

同じ「自動編集」でも、因果の向きが逆です。

| | 撮影編集（SVE） | 完全AI（この文書） |
|---|---|---|
| 音声 | 既にある | **台本から作る** |
| タイムコード | WhisperX で**推定**（ズレる） | **所有している**（誤差ゼロ） |
| 最初の工程 | 素材を削る（rough cut） | 画面を作る（scene plan） |
| 最大の敵 | 失敗テイク・言い淀み | **画面が持たないこと** |

**丸ごと不要になるもの**（SVE の Phase 2〜3 全部）

- WhisperX
- カット点の無音スナップ
- リテイク検出
- フィラー削除
- 言い直し判定
- rough cut とそのロック

台本の文が音声の単位そのものなので、「どの秒に何を言っているか」は
生成した瞬間に確定します。推定する対象が存在しません。

**そのまま持ってくるもの**

- 判断（JSON）とレンダリング（エンジン）の分離
- フォルダがシステムであるという考え方、skills / presets
- Second Pass と部分レンダリング
- 自動 QC とゲート
- ブランドプリセットによる統一

**新しく必要になるもの**

- 画面の持続設計（後述。**ここが全て**）
- AI画像・AI動画の生成と管理
- モーショングラフィック（Remotion）
- 生成コストの制御

---

## 1. 最大の課題：人がいない画面を8〜10分持たせる

撮影動画は、話者の顔が映っているだけで視線が持ちます。
完全AIにはそれがありません。**静止画にナレーションを乗せただけの動画は、
30秒で離脱されます。** ここを設計で潰さないと、他が全部よくても伸びません。

### 6秒ルール

**画面上で何も変化しない時間が6秒を超えてはいけない。**

これを「気をつける」ではなく、**機械的に検査できる制約**にします。
各シーンは `visual_change_events`（変化が起きる時刻の配列）を持ち、
QC が隣接する変化の間隔を検査します。6秒を超えたら critical。

変化としてカウントするもの:

| 種別 | 例 |
|---|---|
| `cut` | シーンの切り替え |
| `motion` | ズーム・パン（**開始時刻のみ**カウント。継続中は変化とみなさない） |
| `text_in` / `text_out` | テロップの出入り |
| `reveal` | 図の要素が段階的に現れる |
| `highlight` | 強調位置の移動 |
| `data_change` | カウンター・グラフの値が動く |

**ゆっくりズームは「変化」として弱い**ので、`motion` は開始時刻だけを
イベントとして数えます。Ken Burns をかけ続けても6秒ルールは満たせません。
ここを甘くすると、結局いまの yt-econ と同じ「動かない動画」になります。

### 画面の密度設計

8分の動画なら、目安として:

- シーン（背景が変わる単位）: **25〜40個**（1シーン 12〜20秒）
- 変化イベント: **80〜130個**（平均 4〜6秒に1回）
- うち図表・データ由来: 6〜10個（信頼性の担保にもなる）

`presets/pacing.json` に持たせ、QC で検査します。

```json
{
  "max_static_seconds": 6.0,
  "scene_seconds": {"min": 6.0, "target": 15.0, "max": 25.0},
  "change_events_per_minute": {"min": 8, "target": 12},
  "min_charts_per_video": 4,
  "hook_first_change_seconds": 2.5
}
```

`hook_first_change_seconds` は**冒頭の最初の変化までの秒数**。
ここが遅いと即離脱するので、2.5秒以内に何か動かします。

---

## 2. パイプライン

```text
テーマ（flow / bridge / stock）
  ↓
[1] 台本生成                    ← yt-econ 既存
  ↓  script.json
[2] 音声合成（文ごとに合成して連結）  ← yt-econ 既存
  ↓  narration.wav + 正確なタイムコード
[3] Scene Plan                  ★新規（rough cut の代わり）
  ↓  scene_plan.json
[4] アセット生成                 ★新規
  ├─ AI画像（背景・イメージカット）
  ├─ AI動画（短尺クリップ。要所のみ）
  ├─ 図表（matplotlib）          ← yt-econ 既存
  └─ 生成物キャッシュ
  ↓
[5] モーショングラフィック（Remotion） ★新規
  ↓  graphics.mov（アルファ付き）
[6] 合成                        ← yt-econ の render.py を拡張
  ↓  composite.mp4
[7] Second Pass                 ★新規（プレビュー → 部分修正）
  ↓
[8] 字幕                        ← yt-econ 既存（再文字起こし不要）
  ↓
[9] 音声仕上げ（BGM・SE・ダッキング）
  ↓
[10] QC                         ★新規
  ↓  qc_report.md
[11] Export → 投稿              ← yt-econ 既存
```

`★新規` が今回作るもの。既存部分は動いているので**壊さないこと。**
既存のテストが通り続けることを各 Phase で確認してください。

---

## 3. scene_plan.json — システムの中心

```json
{
  "slug": "20260919-070000-yen-weak-1234",
  "timeline": "final",
  "duration": 548.2,
  "fps": 30,
  "resolution": [1920, 1080],
  "scenes": [
    {
      "id": "sc003",
      "block_id": "s1",
      "start": 84.2,
      "end": 99.6,
      "narration": "日本銀行によると、政策金利の差が為替に影響します。",
      "background": {
        "kind": "ai_image",
        "prompt": "minimal isometric illustration of two central bank buildings, dark navy background, cyan accent, no text, no people",
        "negative_prompt": "text, watermark, logo,人物の顔",
        "asset": "assets/generated/sc003.png",
        "cache_key": "sha256:…",
        "motion": {"type": "push_in", "from": 1.0, "to": 1.06, "easing": "ease_out"}
      },
      "overlays": [
        {"id": "ov012", "type": "lower_third", "start": 85.0, "end": 91.0,
         "props": {"text": "日銀 政策金利"}},
        {"id": "ov013", "type": "bullet_list", "start": 91.5, "end": 99.0,
         "props": {"items": ["金利差が広がる", "円が売られる"], "reveal": "sequential"}}
      ],
      "visual_change_events": [
        {"t": 84.2, "kind": "cut"},
        {"t": 85.0, "kind": "text_in"},
        {"t": 89.0, "kind": "highlight"},
        {"t": 91.5, "kind": "text_in"},
        {"t": 94.0, "kind": "reveal"},
        {"t": 97.0, "kind": "reveal"}
      ]
    }
  ]
}
```

`visual_change_events` は**プランナーに書かせるのではなく、
背景とオーバーレイの定義から機械的に導出する**こと。
AI に「6秒以内に変化を入れました」と自己申告させると、平気で嘘をつきます。
コードで計算し、コードで検査してください。

```python
def derive_change_events(scene) -> list[dict]:
    """背景とオーバーレイの定義から変化イベントを導出する。
    AI の自己申告を信用しない。"""
```

---

## 4. Phase 0 — 現状の棚卸し（最初にやる）

`yt-econ` は既に動いています。壊さないために、まず現状を把握してください。

```bash
cd yt-econ
python -m pytest -q          # 56件通ることを確認
python -m ytecon doctor
```

既存の資産と、今回どう扱うかを表にして報告してください。

| 既存モジュール | 今回の扱い |
|---|---|
| `topics.py` `script.py` `tts.py` | そのまま使う |
| `subtitles.py` | そのまま使う（TTSの時刻をそのまま利用） |
| `assets.py` の `render_chart` | 図表生成として残す |
| `assets.py` の textcard / stock 写真 | **Remotion に置き換える**（段階的に） |
| `render.py` | 合成レイヤーとして拡張（overlay 追加） |
| `metadata.py` `youtube.py` `revive.py` | そのまま使う |

**既存の静止画カードをいきなり全部消さないこと。**
Remotion 経路が動くまではフォールバックとして残し、
`config` で切り替えられるようにしてください（`visuals.engine: pil | remotion`）。

### 受け入れ
既存 56 テストが通ること。棚卸し表を報告すること。

---

## 5. Phase 1 — Scene Planner

`yt-econ/src/ytecon/scene.py` を新規作成。

### 入力
- `script.json`（台本）
- `narration.json`（文ごとのタイムコード。**既に存在する**）
- `presets/pacing.json`

### やること

1. 台本のブロック（hook / s0..sN / closing）と音声タイムコードから、
   シーンの区切り候補を作る
2. `scene_seconds.max`（25秒）を超えるブロックは**文の切れ目で分割**する
3. 各シーンに背景の種別と生成プロンプトを Claude に決めさせる
4. オーバーレイ（テロップ・箇条書き・図表）を配置させる
5. **`visual_change_events` をコードで導出**する
6. `pacing.json` の制約を検査し、違反があれば Claude に**その箇所だけ**
   作り直させる（最大3回。それでも駄目なら警告して続行）

### 背景の種別

| kind | 使いどころ | コスト |
|---|---|---|
| `chart` | 数値の推移・比較 | 無料（matplotlib） |
| `motion_text` | 定義・3つのポイント・引用 | 無料（Remotion） |
| `diagram` | 仕組み・フロー・因果 | 無料（Remotion） |
| `pattern` | 図表・文字の下地 | 無料（自前生成） |
| `photo` | 場所・雰囲気（多用しない） | 無料（Pexels） |

**`ai_video` は1本あたり最大6カットまで**にハード制約をかけること。
理由は次章。

### 受け入れテスト

```
1. 25秒を超えるブロックが分割されること（文の途中で割らないこと）
2. derive_change_events が、オーバーレイ定義から正しい時刻列を出すこと
3. 6秒を超える無変化区間があるプランを、検査が critical として弾くこと
4. motion の継続中は変化とみなさないこと
   （ゆっくりズームだけで6秒ルールを満たせないことの確認）
5. 有料バックエンドが未設定でも、全シーンに背景が割り当てられること
6. シーンの start/end が narration のタイムコードと矛盾しないこと
   （隙間・重なりが無い、合計が総尺と一致する）
```

---

## 6. Phase 2 — 費用ゼロで作る（設計の前提）

**このシステムは、追加の課金を一切発生させずに完成させます。**
有料の画像生成・動画生成サービスは使いません。

理由は単純で、割に合わないからです。8分の動画を全部AI動画で埋めると
1本あたり数千〜数万円、月60本なら月18万〜180万円になります。
一方で、**視聴維持率を決めているのはAI動画の有無ではなく、
画面が変化し続けているかどうか**です。そこはモーショングラフィックで
足ります。金をかけるべき場所ではありません。

### 使える素材と、その費用

| 種別 | 手段 | 費用 |
|---|---|---|
| 図表 | matplotlib（既存の `assets.py`） | 0円 |
| モーションテキスト | Remotion | 0円 |
| 図解・ダイアグラム | Remotion（SVG を組む） | 0円 |
| 手続き的な背景 | グラデーション・幾何パターンを自前生成 | 0円 |
| 写真 | Pexels API（無料枠） | 0円 |
| 音声 | VOICEVOX（ローカル） | 0円 |
| フォント | Noto Sans JP（OFL） | 0円 |
| 台本・企画 | `claude -p`（後述） | サブスクの枠内 |

### 台本生成の費用をゼロにする

`ANTHROPIC_API_KEY` で直接叩くとトークン従量課金になります。
代わりに **Claude Code の headless モード**を使ってください。

```bash
claude -p "<プロンプト>" --output-format json --model sonnet \
  --max-turns 1 --session-id <毎回新しいUUID> \
  --disallowedTools Bash Edit Write Read Glob Grep WebFetch WebSearch Task \
  --strict-mcp-config
```

`yt-econ/src/ytecon/llm_claudecode.py` に実装済みです。
`YTECON_LLM_PROVIDER=claude_code`（既定は `auto` で、`claude` コマンドが
あれば自動でこちら）。

**正確に言うと:** これは「どんな場合でも無料」ではありません。
Claude Code をサブスクリプションで認証しているなら、台本生成のぶんは
プランの利用枠に含まれるので追加の請求が出ない、という意味です。
Claude Code 自体を API キーで認証しているなら、結局は従量課金です。

呼び出しは毎回まっさらなセッションで行ってください。
プロジェクトの会話履歴を引き継ぐと、コンテキストが膨らんで利用枠を無駄に食います。

### 有料サービスを足したくなったら

`generate/` に `ImageBackend` / `VideoBackend` のインターフェースだけ
用意しておき、**既定では未設定**にしてください。
無料素材だけで動画が完成することが先で、生成AIは後から差せる飾りです。

```python
class ImageBackend(Protocol):
    def generate(self, prompt: str, *, negative: str,
                 size: tuple[int, int], seed: int | None) -> Path: ...
```

未設定時は、そのシーンを `chart` / `motion_text` / `diagram` に自動で
振り替えます。**バックエンドが1つも無くても QC を通る動画ができること**を
受け入れテストで保証してください。

### 受け入れテスト

```
1. 画像・動画生成バックエンドを一切設定せずに、動画が完成すること
2. そのとき QC の critical が 0 件であること（特に6秒ルールを満たすこと）
3. YTECON_LLM_PROVIDER=claude_code で台本が生成できること
4. claude コマンドが無い環境では api 経路に自動で落ちること
5. Pexels のキーが無くても、手続き的な背景で成立すること
```

**2 が肝です。** 「無料素材だけだと画面が持たない」なら設計が負けています。
モーショングラフィックで6秒ルールを満たせることを、ここで証明してください。

---

## 7. Phase 3 — 無料素材で画面を作る

有料生成を使わないぶん、**Remotion 側の作り込みが全てになります。**

### 背景の種別（すべて無料）

| kind | 中身 | 使いどころ |
|---|---|---|
| `chart` | matplotlib の図表 | 数値の推移・比較 |
| `motion_text` | Remotion。文字が主役 | 定義・3つのポイント・引用 |
| `diagram` | Remotion。箱と矢印を組む | 仕組み・フロー・因果関係 |
| `pattern` | 手続き的に生成する幾何背景 | 上記を置く下地 |
| `photo` | Pexels | 場所・雰囲気（多用しない） |

**`photo` に頼らないこと。** ストック写真は内容と無関係なことが多く、
連発すると安っぽく見えます。**図解とモーションテキストが主役**です。
経済の解説は元々「目に見えないものを説明する」ので、写真より図のほうが合います。

### 手続き的背景

`pattern` は Remotion で自前生成します。ライセンスも費用も発生しません。

- グラデーション（`brand.json` の2色を補間）
- 等間隔のグリッド / ドット（ゆっくり流す）
- 同心円・斜線などの幾何パターン（低コントラストで背景に置く）
- 図表の下地として使うと、白背景より締まって見えます

### 図解（diagram）を主力にする

経済の話は**因果**が本体です。箱と矢印で描けるものが多い。

```
[米の金利上昇] ──→ [ドルが買われる] ──→ [円安] ──→ [輸入品が高くなる]
```

これを Remotion で**1要素ずつ順に出す**と、それだけで変化イベントが4回稼げます。
`Diagram` コンポーネントは次を受け取れるようにしてください。

```json
{
  "type": "diagram",
  "props": {
    "nodes": [{"id": "a", "label": "米の金利上昇"}, {"id": "b", "label": "ドルが買われる"}],
    "edges": [{"from": "a", "to": "b", "label": ""}],
    "layout": "horizontal",
    "reveal": "sequential"
  }
}
```

**`reveal: "sequential"` が6秒ルールを満たす主力です。**

## 8. Phase 4 — Remotion モーショングラフィック

### 方式

グラフィックを**アルファ付きの別レイヤー**として出し、FFmpeg で重ねる。

```
背景（AI画像/動画/図表 + モーション）─┐
                                      ├─ FFmpeg overlay ─→ composite.mp4
Remotion graphics.mov（アルファ付き）─┘
```

```bash
npx remotion render GraphicsTrack out/graphics.mov \
  --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le
```

**なぜ分けるか:** テロップを1つ直すたびに背景ごと再エンコードすると、
修正のたびに数分待つことになり、Second Pass が回りません。

### コンポーネント

```
Title / Subtitle / LowerThird / BulletList / Comparison
Quote / Counter / Diagram / Highlight / ChartReveal / ProgressBar
```

要件:

- 色・フォント・アニメーションは **`presets/brand.json` から読む。
  コンポーネント内にハードコードしない。**
- `spring()` / `interpolate()` を使う
- 日本語フォントは `delayRender()` / `continueRender()` で
  **読み込みを待ってからレンダリングする**。待たないとフォント未適用の
  フレームが混ざります
- `BulletList` の `reveal: "sequential"` は、項目を順に出す
  （これが変化イベントを稼ぐ主力になる）
- `ChartReveal` は matplotlib の静止画を受け取り、
  **マスクで左から出す**。図表を一気に出すより見られます

### Remotion の環境

`ai-video-system/remotion-poc/` に**実機で動作確認済みの最小構成**があります。
ゼロから書き始めず、まずこれを動かしてから拡張してください。
`LowerThird` / `BulletList` と、日本語フォントの待ち処理が入っています。

踏んだ問題が2つあります。

1. `tsconfig.json` が無いとレンダリングが始まらない（npm init では作られない）
2. Chrome Headless Shell のダウンロードが要る。ブロックされる環境では
   既存の Chromium を指定する

```bash
npx remotion render src/index.ts GraphicsTrack out/graphics.mov \
  --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le \
  --browser-executable=/path/to/headless_shell
```

出力が `yuva444p12le` になっていればアルファ付きで成功しています。

環境構築で詰まったら、**`visuals.engine: pil` にフォールバックして
先に進めてください。** Remotion はあくまで質を上げるレイヤーで、
無くても動画は完成します。

### 受け入れテスト

```
1. 3秒 × Title をアルファ付き出力し、ffprobe で pix_fmt にアルファがあること
2. brand.json の accent を変えたらフレームの色が変わること
   （ハードコードしていないことの証明。1フレーム抜いてピクセル比較）
3. 日本語が豆腐（□）にならないこと
   （レンダリング結果に文字が描画されているか、非背景ピクセル数で判定）
4. overlay 合成後の尺が背景と一致すること
5. BulletList の sequential で、項目数ぶんの変化が起きること
```

**3 を必ず入れてください。** Remotion + 日本語フォントで最も多い事故です。

---

## 9. Phase 5 — Second Pass と部分レンダリング

### 修正指示

```
84秒のテロップが図に重なっている。右下に移動。
アニメーションと表示時間はそのまま。他は変更しない。
```

1. 時刻から対象 overlay を特定
2. `scene_plan.json` の**その item だけ**書き換える
3. 変更前を `plans/history/scene_plan.<timestamp>.json` に退避
4. 影響範囲 ± 2秒だけ再レンダリング

### 部分レンダリング

```bash
npx remotion render GraphicsTrack out/preview/seg.mov --frames=<start>-<end>
```

背景側も該当範囲だけ切り出して overlay し、`renders/preview/` に置く。
**全編を再レンダリングしない。**

`CLAUDE.md` に明記:

```
## Partial Render Policy
修正中は全体をレンダリングしない。
影響範囲を特定し、前後2秒を足した範囲だけプレビューする。
フルレンダリングは最終承認後のみ。
```

### 受け入れテスト

```
1. 1 item だけ変えたとき、影響範囲が正しく計算されること
2. 変更なしなら None を返すこと
3. 隣接する2 item の変更が1範囲にマージされること
4. history/ に変更前が残ること
```

---

## 10. Phase 6 — QC

**全項目を自動判定にすること。** 目視項目を混ぜると運用されません。

| 分類 | チェック | 判定 |
|---|---|---|
| **ペース** | 無変化が6秒超 | critical |
| ペース | 冒頭の初回変化が2.5秒超 | critical |
| ペース | 変化イベント密度が下限未満 | warning |
| ペース | シーンが25秒超 | warning |
| 構成 | 図表が最低本数未満 | warning |
| 映像 | オーバーレイが safe area 外 | critical |
| 映像 | 同時刻のオーバーレイ同士が重なる | critical |
| 映像 | 同時表示3つ以上 | warning |
| 映像 | 表示1.2秒未満（読めない） | critical |
| 映像 | 黒フレーム 0.1秒超（`blackdetect`） | critical |
| 映像 | 解像度・fps が仕様通り | critical |
| 字幕 | 音声タイムコードとの一致 | critical |
| 字幕 | 1行の文字数超過 | warning |
| 音声 | 最終 LUFS が -14 ± 1.0 | critical |
| 音声 | クリッピング（peak > -1.0dB） | critical |
| 音声 | 声とBGMの差が 10 LU 未満 | warning |
| 尺 | 目標レンジ外 | warning |

`critical` が1つでもあれば **export をブロック**。
`reports/qc_report.md` を生成。

### 受け入れテスト

```
1. 6秒の無変化を含むプランで critical が立つこと
2. critical があるとき export が例外で止まること
3. 意図的にクリッピングさせた音声で critical が立つこと
4. 正常なプランでは critical が 0 件であること
```

---

## 11. 音声

すべてAI音声なので、次を守ってください。

- **概要欄に AI 音声使用を明記する**（yt-econ の `metadata.py` は既に出力している）
- VOICEVOX を使う場合は**キャラクター名のクレジット表記が必要**
- BGM は固定 dB で決めない。声の integrated LUFS を測り、
  そこから `music_below_voice_lu`（15 LU 目安）だけ下げる
- 最終ミックスは `loudnorm=I=-14:TP=-1.5:LRA=11`

```
[music]volume=<計算値>dB,afade=t=in:d=1.5,afade=t=out:st=<末尾-2>:d=2[bgm];
[voice][bgm]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[mix];
[mix]loudnorm=I=-14:TP=-1.5:LRA=11[aout]
```

---

## 12. CLI

既存の `ytecon` に足す形で。

```bash
ytecon scene plan <slug>          # scene_plan.json を作る
ytecon scene check <slug>         # pacing 制約だけ検査
ytecon assets generate <slug>     # 画像・動画・図表を用意（キャッシュあり）
ytecon graphics render <slug>     # Remotion でアルファレイヤー
ytecon composite <slug>           # 背景 + graphics を合成
ytecon fix <slug> "84秒のテロップを右下へ"
ytecon preview <slug> 01:24-01:40
ytecon qc <slug>
ytecon budget                     # 今日の生成コスト
ytecon run                        # 全部通す（既存を拡張）
```

---

## 13. 実装順と報告

Phase 0 → 6 の順。各 Phase で:

1. 実装する
2. 受け入れテストを書いて**実際に走らせて通す**
3. **既存の 56 テストが通り続けることを確認する**
4. `git commit` する
5. 何を作り何を確認したかを1段落で報告し、**いったん止まる**

一度に全部やらないこと。

外部サービス（画像・動画生成）の API キーが必要な段階では、
キーが無くても動く経路（図表 + モーションテキスト）を先に完成させ、
生成AIは後から差し込む形にしてください。

**優先順位は Phase 1（Scene Planner）と Phase 4（Remotion）です。**
この2つが「静止画にナレーションを乗せただけ」から抜け出す本体で、
AI画像・AI動画はその上の飾りです。順番を逆にしないでください。

=== PROMPT END ===
