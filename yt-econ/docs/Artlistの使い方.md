# Artlist の素材を使う（動く背景・B-roll・BGM）

「背景がずっと同じでつまらない」への対処です。画面の下地は**常に動画**になりました。
素材が無いときは抽象ループを合成しますが、Artlist の実写・モーション素材を置くと
そちらが優先されて、ぐっと見栄えが上がります。

Artlist には API が無く、規約上も**自分のアカウントで落とした素材**を使う必要があります。
そのため「落として、フォルダに置く」だけは手作業です。一度置けば、以後の全動画で
自動的に使い回されます（1本ごとに探す必要はありません）。

## 置き場所

```
assets/footage/
  abstract/     ← 抽象ループ・モーショングラフィックス（カードの後ろに敷く）
  broll/        ← 実写（街・スーパー・オフィス・お金・スマホ など）
  texture/      ← 質感（紙・布・光の漏れ・ボケ）。abstract と同じ扱い
  tags.yaml     ← （任意）ファイルごとのタグ
assets/bgm/
  ambient.mp3   curiosity.mp3   tension.mp3   reflective.mp3   ← 4つの気分の BGM
```

- ファイル名の英単語がそのままタグになります（`tokyo_street_night_4k.mp4` → tokyo / street / night）。
  Artlist のダウンロード名は長いので、**中身が分かる英単語にリネーム**しておくと当たりが良くなります。
- 台本の `visual.query` / `image_prompt`（英語）と語が重なる実写素材が選ばれます。
  一語も重ならない実写は「関係ない絵」になるので使わず、写真 → AI 画像 → 抽象背景に落ちます。
- タグを手で足すなら `tags.yaml`:

```yaml
broll/supermarket_shelf.mp4:
  tags: [supermarket, price, grocery, inflation, shopping]
abstract/blue_particles_loop.mp4:
  kind: abstract
  tags: [particles, loop, calm]
```

## 何を何本落とすか（最初の1回）

経済解説で使い回しが利くものを、まず **40 本前後**。

| 種類 | 本数 | Artlist での検索語（英語） |
|---|---|---|
| 抽象ループ | 10 | `abstract particles loop`, `bokeh dark background`, `data network lines`, `slow gradient motion`, `light leak dark`, `digital grid`, `smoke slow motion dark`, `ink in water dark` |
| 街・人 | 10 | `tokyo street crowd`, `shibuya crossing`, `office workers japan`, `commuters train`, `city night aerial`, `people smartphone`, `young professionals walking` |
| お金・買い物 | 8 | `japanese yen banknotes`, `coins falling`, `supermarket shelves`, `price tag`, `cash register`, `receipt`, `wallet` |
| 経済・仕事 | 8 | `stock market screen`, `trading chart`, `bank building`, `factory production line`, `shipping containers`, `real estate`, `calculator desk` |
| 質感 | 4 | `paper texture`, `film grain overlay`, `dust particles`, `soft light bokeh` |

条件のおすすめ: **1080p 以上・10 秒以上・横向き・ロゴなし**。暗め（ダーク基調）の素材のほうが
白い文字が読みやすく、デザイントークンの配色とも合います。

## BGM（Artlist Music）

4つの気分でそれぞれ 1 曲ずつ。ファイル名を気分名にして `assets/bgm/` に置くだけです。

| 気分 | 使う場面 | 探し方の目安 |
|---|---|---|
| `ambient` | 冒頭（矛盾・問い） | Ambient / Minimal / Low tempo / Pads |
| `curiosity` | 自分ごと化・レンズ投入 | Light pulse / Plucks / Curious / Documentary |
| `tension` | 統計・仕組み・視点反転 | Tension / Minor / Suspense（過剰に暗くしない） |
| `reflective` | 生活へ戻す・余韻 | Piano / Reflective / Hopeful |

`config/channel.yaml` の `render.bgm.credit` に Artlist の指定どおりのクレジットを書くと
概要欄に自動で入ります（Artlist は表記必須ではありませんが、書いておいて損はありません）。

## ライセンスの注意

- Artlist のライセンスは**契約中に落とした素材を、契約後も使い続けられる**形です。
  ただし落とした記録（アカウント）が証拠になるので、他人のアカウントの素材は使わないこと。
- YouTube の収益化で使えます。Content ID の申し立てが来たら、Artlist のライセンス証明書
  （ダウンロード履歴から発行）で解除できます。

## 素材を置いたあと

何もしなくてよいです。次に動画を作るときから自動で使われます。
ログに `フッテージ: 素材 40 本（実写 22 / 抽象 14 / 質感 4）` のように出れば認識されています。
