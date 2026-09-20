"""台本生成.

出力は「読み上げ用の文」と「画面に出すもの」を分離した構造化データ。
ここで visual を決めておくので、後段の素材取得・レンダリングが
一切の判断をせずに機械的に処理できる。

尺の制御は2段構え:
  1. 生成時に目標文字数をプロンプトで指示
  2. 生成後に文字数を数えて、レンジ外なら伸縮リライトを回す
最終的な尺は TTS の実測で決まるので、audio.py 側でも再チェックする。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .config import Config
from .topics import Topic

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# データ構造
# ----------------------------------------------------------------------
def _normalize_beat(name: str) -> str:
    from .bible import normalize_beat
    return normalize_beat(str(name or "ACADEMIC_LENS"))


@dataclass
class Visual:
    kind: str = "stock"              # stock | chart | textcard
    query: str = ""                  # stock 用の検索語（英語）
    chart: dict[str, Any] | None = None   # chart 用の仕様
    caption: str = ""                # 図表の出典キャプション
    image_prompt: str = ""           # AI 画像用。概念の視覚化（英語。画風はこちらで足す）


@dataclass
class Caption:
    """画面に出すテロップ1つ。字幕とは別で、意味ごとに見た目が変わる."""
    text: str
    type: str = "NORMAL"     # NORMAL/KEYWORD/EMPHASIS/PUNCHLINE/EDITORIAL/DATA
    after_sentence: int = 0  # そのセクションの何文目の後に出すか


@dataclass
class Card:
    """画面に出す文字カード。**文章ではなく体言止め**で書く。

    例) text="実質賃金がマイナスの月が26か月連続", source="毎月勤労統計調査（厚生労働省）"
        text="連合「1990年代前半以来の水準」", source=""
    """
    text: str
    source: str = ""
    after_sentence: int = 0


@dataclass
class Diagram:
    """図解。言葉で説明される部分を「絵」にする.

    type:
      flow    : A → B → C（因果・順番）。items は 2〜4 個の短い語（各 ≤10 字）
      compare : 左右の比較。items は "見出し|値" を 2〜4 行、title に左右の名前を「A vs B」で
      steps   : 番号つきの手順・条件。items は 2〜4 行（各 ≤16 字）
      balance : 2 つの数字の差し引き。items は ["名目 +5.1%", "物価 +3.2%", "実質 −"] のように 3 個
      table   : 2 列の表。items は "項目|値" を 2〜5 行
    """
    type: str
    title: str = ""
    items: list[str] = field(default_factory=list)
    note: str = ""              # 出典や補足（小さく出す）
    after_sentence: int = 0


@dataclass
class SoundCue:
    """効果音1つ."""
    type: str = "POP"        # POP/CLICK/WHOOSH/IMPACT/COMEDY/ERROR/RISER/TRANSITION
    after_sentence: int = 0


@dataclass
class Section:
    heading: str                     # チャプター名 兼 画面見出し
    narration: str                   # 読み上げ本文
    on_screen: list[str] = field(default_factory=list)   # 画面に出す箇条書き
    visual: Visual = field(default_factory=Visual)
    beat: str = "STORY"              # この節が構成上どこか
    captions: list[Caption] = field(default_factory=list)
    sounds: list[SoundCue] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)   # 体言止めの文字カード（一文カードの代わり）
    diagrams: list[Diagram] = field(default_factory=list)   # 図解（流れ・比較・手順・差し引き・表）

    @property
    def char_count(self) -> int:
        return len(re.sub(r"\s", "", self.narration))


@dataclass
class Term:
    """ビジネス用語。用語カードとして画面に出す."""
    term: str
    meaning: str
    example: str = ""
    section: int = 0


@dataclass
class OpenLoop:
    """前半で投げて後半で回収する問い。回収しない伏線は禁止."""
    question: str
    payoff_section: int = -1         # 何番目のセクションで回収するか


@dataclass
class VideoScript:
    topic_title: str
    hook: str                        # 冒頭15秒のつかみ
    sections: list[Section]
    closing: str                     # まとめ + CTA
    title_candidates: list[str]
    description: str
    tags: list[str]
    thumbnail_copy: dict[str, str]
    sources: list[dict[str, str]]
    disclaimer: str = ""
    proof: str = ""                  # 0-15秒。結論を裏づける具体
    promise: str = ""                # 15-30秒。この動画で何が分かるか
    open_loops: list[OpenLoop] = field(default_factory=list)
    terms: list[Term] = field(default_factory=list)
    research: dict[str, Any] = field(default_factory=dict)   # 台本前の「リサーチの木」
    block_cards: dict[str, list[Card]] = field(default_factory=dict)  # hook/proof/promise/closing の文字カード
    block_diagrams: dict[str, list[Diagram]] = field(default_factory=dict)  # 同じく図解

    @property
    def narration_blocks(self) -> list[tuple[str, str]]:
        """(セクションID, 読み上げテキスト) の並び。音声生成の入力になる."""
        blocks = [("hook", self.hook)]
        if self.proof.strip():
            blocks.append(("proof", self.proof))
        if self.promise.strip():
            blocks.append(("promise", self.promise))
        for i, sec in enumerate(self.sections):
            blocks.append((f"s{i}", sec.narration))
        blocks.append(("closing", self.closing))
        return blocks

    @property
    def total_chars(self) -> int:
        return sum(len(re.sub(r"\s", "", t)) for _, t in self.narration_blocks)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoScript":
        sections = []
        for s in d.get("sections", []):
            v = s.get("visual") or {}
            sections.append(
                Section(
                    heading=s.get("heading", ""),
                    narration=s.get("narration", ""),
                    on_screen=s.get("on_screen", []) or [],
                    beat=_normalize_beat(s.get("beat", "ACADEMIC_LENS")),
                    captions=[
                        Caption(text=c.get("text", ""),
                                type=c.get("type", "NORMAL"),
                                after_sentence=int(c.get("after_sentence", 0) or 0))
                        for c in (s.get("captions") or []) if c.get("text")
                    ],
                    sounds=[
                        SoundCue(type=c.get("type", "POP"),
                                 after_sentence=int(c.get("after_sentence", 0) or 0))
                        for c in (s.get("sounds") or [])
                    ],
                    cards=[
                        Card(text=c.get("text", ""), source=c.get("source", "") or "",
                             after_sentence=int(c.get("after_sentence", 0) or 0))
                        for c in (s.get("cards") or []) if c.get("text")
                    ],
                    diagrams=[
                        Diagram(type=str(g.get("type", "flow")), title=g.get("title", "") or "",
                                items=[str(x) for x in (g.get("items") or []) if str(x).strip()],
                                note=g.get("note", "") or "",
                                after_sentence=int(g.get("after_sentence", 0) or 0))
                        for g in (s.get("diagrams") or []) if g.get("items")
                    ],
                    visual=Visual(
                        kind=v.get("kind", "stock"),
                        query=v.get("query", ""),
                        chart=v.get("chart") or None,
                        caption=v.get("caption", ""),
                        image_prompt=v.get("image_prompt", "") or "",
                    ),
                )
            )
        return cls(
            topic_title=d.get("topic_title", ""),
            hook=d.get("hook", ""),
            sections=sections,
            closing=d.get("closing", ""),
            title_candidates=d.get("title_candidates", []) or [],
            description=d.get("description", ""),
            tags=d.get("tags", []) or [],
            thumbnail_copy=d.get("thumbnail_copy", {}) or {},
            sources=d.get("sources", []) or [],
            disclaimer=d.get("disclaimer", ""),
            proof=d.get("proof", ""),
            promise=d.get("promise", ""),
            open_loops=[
                OpenLoop(question=o.get("question", ""),
                         payoff_section=int(o.get("payoff_section", -1) or -1))
                for o in (d.get("open_loops") or []) if o.get("question")
            ],
            terms=[
                Term(term=t.get("term", ""), meaning=t.get("meaning", ""),
                     example=t.get("example", ""),
                     section=int(t.get("section", 0) or 0))
                for t in (d.get("terms") or []) if t.get("term")
            ],
            research=d.get("research") or {},
            block_cards={
                k: [Card(text=c.get("text", ""), source=c.get("source", "") or "",
                         after_sentence=int(c.get("after_sentence", 0) or 0))
                    for c in (v or []) if c.get("text")]
                for k, v in (d.get("block_cards") or {}).items()
            },
            block_diagrams={
                k: [Diagram(type=str(g.get("type", "flow")), title=g.get("title", "") or "",
                            items=[str(x) for x in (g.get("items") or []) if str(x).strip()],
                            note=g.get("note", "") or "", after_sentence=int(g.get("after_sentence", 0) or 0))
                    for g in (v or []) if g.get("items")]
                for k, v in (d.get("block_diagrams") or {}).items()
            },
        )

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "VideoScript":
        script = cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        # 保存済みの台本にも言い換え・出典の掃除を効かせる（何度掛けても同じ結果）
        _sanitize(script)
        return script


# ----------------------------------------------------------------------
# スキーマ
# ----------------------------------------------------------------------
_CHART_SCHEMA = llm.obj(
    {
        "type": {"type": "string", "enum": ["line", "bar", "stacked_bar", "pie", "none"]},
        "title": llm.STR,
        "x_label": llm.STR,
        "y_label": llm.STR,
        "labels": llm.arr(llm.STR),
        "series": llm.arr(llm.obj({"name": llm.STR, "values": llm.arr(llm.NUM)})),
        "note": llm.STR,
    }
)

_VISUAL_SCHEMA = llm.obj(
    {
        "kind": {"type": "string", "enum": ["stock", "chart", "textcard"]},
        "query": llm.STR,
        "chart": _CHART_SCHEMA,
        "caption": llm.STR,
        "image_prompt": llm.STR,
    }
)

_CAPTION_TYPES = ["NORMAL", "KEYWORD", "EMPHASIS", "PUNCHLINE", "EDITORIAL", "DATA"]
_SE_TYPES = ["POP", "CLICK", "WHOOSH", "IMPACT", "COMEDY", "ERROR",
             "RISER", "TRANSITION"]
# 本編セクションに付ける beat。config/style_bible.yaml の 9 ブロックのうち
# section に割り当てるもの（S03〜S08）
_BEATS = ["FAMILIAR_SCENE", "ACADEMIC_LENS", "EVIDENCE_DROP",
          "MECHANISM_REVEAL", "PERSPECTIVE_FLIP", "HUMAN_RETURN"]

_DIAGRAM_TYPES = ["flow", "compare", "steps", "balance", "table"]
_DIAGRAM_SCHEMA = llm.obj({
    "type": {"type": "string", "enum": _DIAGRAM_TYPES},
    "title": llm.STR,
    "items": llm.arr(llm.STR),
    "note": llm.STR,
    "after_sentence": llm.INT,
})

# 既存の台本に図解だけ後付けするとき用
_DIAGRAMS_SCHEMA = llm.obj({
    "sections": llm.arr(llm.obj({"heading": llm.STR, "diagrams": llm.arr(_DIAGRAM_SCHEMA)})),
})

# 既存の台本にカードだけ後付けするとき用
_CARDS_SCHEMA = llm.obj({
    "sections": llm.arr(llm.obj({
        "heading": llm.STR,
        "cards": llm.arr(llm.obj({"text": llm.STR, "source": llm.STR, "after_sentence": llm.INT})),
    })),
})

# 台本の前に作る「リサーチの木」
_RESEARCH_SCHEMA = llm.obj(
    {
        "question": llm.STR,                 # 動画全体で解く1つの問い（日常語）
        "common_belief": llm.STR,            # 普通はこう思われている
        "paradox": llm.STR,                  # でも実際はこう（hook の種）
        "everyday_scene": llm.STR,           # 視聴者が自分の生活で見た場面
        "lenses": llm.arr(
            llm.obj({
                "id": llm.STR,               # R01〜R06
                "name": llm.STR,
                "hypothesis": llm.STR,       # このレンズだとこう説明できる
                "evidence": llm.STR,         # 使える実在の統計・調査（名前と年）
                "interest": llm.INT,         # 1〜5。意外さ×説明力
                "adopt": {"type": "boolean"},
            })
        ),
        "counterargument": llm.STR,          # 一番強い反論と、それでも成り立つ理由
        "flip": llm.STR,                     # 中盤で反転させる第二の疑問
        "human_return": llm.STR,             # 最後に給料・買い物・働き方へどう戻すか
        "title_candidates": llm.arr(llm.STR),
    }
)

_SCRIPT_SCHEMA = llm.obj(
    {
        "topic_title": llm.STR,
        "hook": llm.STR,
        "proof": llm.STR,
        "promise": llm.STR,
        "open_loops": llm.arr(
            llm.obj({"question": llm.STR, "payoff_section": llm.INT})
        ),
        "sections": llm.arr(
            llm.obj(
                {
                    "heading": llm.STR,
                    "narration": llm.STR,
                    "beat": {"type": "string", "enum": _BEATS},
                    "on_screen": llm.arr(llm.STR),
                    "captions": llm.arr(
                        llm.obj({
                            "text": llm.STR,
                            "type": {"type": "string", "enum": _CAPTION_TYPES},
                            "after_sentence": llm.INT,
                        })
                    ),
                    "sounds": llm.arr(
                        llm.obj({
                            "type": {"type": "string", "enum": _SE_TYPES},
                            "after_sentence": llm.INT,
                        })
                    ),
                    "cards": llm.arr(
                        llm.obj({
                            "text": llm.STR,          # 体言止め。22字以内
                            "source": llm.STR,        # 出典（機関・調査名）。無ければ空
                            "after_sentence": llm.INT,
                        })
                    ),
                    "diagrams": llm.arr(_DIAGRAM_SCHEMA),
                    "visual": _VISUAL_SCHEMA,
                }
            )
        ),
        "closing": llm.STR,
        "title_candidates": llm.arr(llm.STR),
        "description": llm.STR,
        "tags": llm.arr(llm.STR),
        "thumbnail_copy": llm.obj({"main": llm.STR, "sub": llm.STR}),
        "sources": llm.arr(llm.obj({"name": llm.STR, "url": llm.STR})),
        "terms": llm.arr(
            llm.obj({
                "term": llm.STR,
                "meaning": llm.STR,
                "example": llm.STR,
                "section": llm.INT,     # 何番目のセクションで初出か
            })
        ),
        "disclaimer": llm.STR,
    }
)


# ----------------------------------------------------------------------
# プロンプト
# ----------------------------------------------------------------------
_SYSTEM = """あなたは日本語の経済解説YouTube動画の構成作家です。

# 視聴者
{audience}
経済の授業を受けたことはあっても、実務・生活との接続ができていません。
専門用語を並べると3秒で離脱します。

# 語り口の決まり
{speech_style}

{tone}

{style_block}

# この回の型
{horizon_guide}

# 内容の難しさと、話し方の高さは別物

- **内容は小学5年生に伝わる水準まで噛み砕く。**
  仕組みの説明は、身近な物（お小遣い・スーパー・ゲーム）に置き換える。
  数字は「多い/少ない」だけでなく「何と比べて」を必ず添える。
- **話し方は大学生〜20代のビジネスパーソンに向ける。**
  子ども扱いしない。「みんな」と呼びかけるが、口調は対等。
  比喩は身近でも、結論は大人の判断材料になる形で言う。
つまり「中身はやさしく、態度は対等」。この組み合わせを崩さないこと。

# ビジネス用語を毎回 2〜4 個、正面から扱う

このチャンネルの視聴者は、ニュースに出てくる**ビジネスの言葉**を
知りたがっている。日常語ではなく、仕事や経済の場面でしか使わない語のこと。
（例: 実質賃金 / 政策金利 / 貿易収支 / 購買力平価 / 名目と実質 / 為替介入）

- 動画ごとに 2〜4 個を terms に入れる。term（用語）、meaning（一文の意味）、
  example（**数字つきの具体例**。「〜を示す〇〇という数字が、アメリカは△△、
  日本は□□」のような形）を必ず埋める
- 本文でその用語を初めて出す文の直後に、言い換えを1文添える
- example の数字には出典を口頭で添える（「〇〇によると」）

# 視聴者を置き去りにしない（このチャンネルの生命線）

解説動画で人が離脱する理由は「難しいから」ではなく、
**「自分が置いていかれたと感じた瞬間」**です。これを構造で防ぎます。

1. **新しい言葉を出したら、必ずその場で止まる。**
   「〇〇という言葉が出てきたけど、これは要するに△△のこと」
   後でまとめて説明する、は禁止。出した瞬間に払う。

2. **1セクションに1回、視聴者に問いを投げる。**
   「ここで一度考えてみてほしいのだ。もし〜だったらどうなるかな」
   投げたら、すぐ答えずワンテンポ置いてから答える。
   この間が、視聴者が自分で考える時間になります。

3. **難所の直前で予告する。**
   「ここから少しややこしくなるけど、結論だけ先に言うと〇〇なのだ」
   結論を先に置くと、途中で分からなくなっても脱落しません。

4. **セクションの終わりで必ず回収する。**
   「つまりここまでで分かったのは〇〇ということなのだ」
   次に進む前に、いま立っている場所を確認させます。

5. **視聴者の反応を先回りして言語化する。**
   「たぶん今、それって結局どういうことって思ったかな」
   「ここ、ぼくも最初は分からなかったところなのだ」
   一人で喋っている感じをなくし、会話しているように見せます。

6. **専門用語を避けない。ただし必ず言い換えを添える。**
   用語を避けると、視聴者は結局その言葉を知らないままになります。
   正しい言葉を使って、隣に翻訳を置く。これが誠実なやり方です。

# 構成の型（必ずこの流れ）

このチャンネルは「雑学」でも「論文解説」でもなく、
**日常の違和感を、経済学を使って映画のように説明する**映像エッセイです。
骨格は次の 9 ブロック。順番を崩さないこと。

{bible}

## 冒頭 55 秒の書き方（ここで残るか決まる）

1. **hook = S01 PARADOX_HOOK**: 「こんにちは」「今日は〜の話」から始めない。
   「普通はこう思うのだ」→「でも、実際は逆なのだ」で違和感を突きつける。
   **答えは言わない。**
2. **proof = S05a**: その違和感が本当にあることを、数字1つと出典で裏づける。
   「実際、〇〇によると△△は□□まで上がっているのだ」
3. **promise = S02 QUESTION_LOCK**: 「では、なぜ〜なのか？」と問いを1つに固定し、
   「今日は〇〇と△△の2つの視点で解いていくのだ」と道筋だけ約束する。

## 伏線（open_loops）

前半で問いを投げ、後半で回収する。**投げたら必ず回収する。**
回収しない引っ張りは視聴者への裏切りなので禁止。
open_loops には「question」と「payoff_section（何番目のセクションで回収するか）」
を入れること。0〜2本まで。無理に作らない。

## 本編 {sections} セクション:
   - 各セクションの beat は、上の 9 ブロックのうち S03〜S08 を**順に**割り当てる
     （FAMILIAR_SCENE → ACADEMIC_LENS → EVIDENCE_DROP → MECHANISM_REVEAL →
       PERSPECTIVE_FLIP → HUMAN_RETURN）。セクション数が 6 でなければ順序を保って比例配分
   - 各セクションは「問い → 事実（数字） → なぜそうなるか → だから何」の順
   - 数字を出したら必ず出典（機関名と年）を narration 内で口頭で言う
   - 前のセクションの結論を1句受けてから次に進む（接続を切らない）
   - **各セクションに、視聴者への問いかけを1つ必ず入れる**
   - **各セクションの最後に、そこで分かったことを1文で回収する**
   - MECHANISM_REVEAL は「研究によると〇〇」で終わらせない。
     A → B → C → だから生活で D が起きる、という因果の鎖を必ず言葉にする
   - PERSPECTIVE_FLIP は「ただし、ここで面白いのが」で始め、別のレンズで説明し直す
3. **closing = S09 REFLECTIVE_ENDING**: 3行で要点 → 「もしかすると〜なのかもしれないのだ」
   という余韻 → 視聴者への問いを1つ → チャンネル登録の一言。
   「だから〜しましょう」で閉じない。押し付けがましくしない。

# 表情タグ（立ち絵の表情を変える）

読み上げ本文の**文頭**に、次のタグを置くと、その一文のあいだ右下のキャラの表情が変わります。
音声には読まれず、字幕にも出ません。**1 セクションに 1〜2 回**。付けすぎると安っぽくなります。

| タグ | 使う場面 |
|---|---|
| [驚] | 「でも実際は逆」の瞬間、意外な数字を出す文 |
| [考] | 視聴者に問いを投げる文（「ここで一度考えてみてほしいのだ」） |
| [指] | その回の主張、いちばん覚えて帰ってほしい一文 |
| [困] | 困りごと・不安・うまくいかない話 |
| [笑] | 締めくくり、ほっとする話、余韻 |
| [怒] | 理不尽・怒りを代弁する文（まれに） |

例) 「[驚]ところが、数字は逆を向いているのだ。」

# 図解（diagrams）を出す — 言葉だけで説明しない（最重要）

視聴者は「タイトルだけ出て言葉で説明される」と分からなくなる。**仕組み・順番・比較・
差し引きは必ず図にする。** 各セクションに **3〜5 個**（画面は 8 秒ごとに変わるので、その半分以上を図解に）。
**調査結果・統計は積極的に引用し、必ず note に引用元（機関名・調査名・年）を書く。**
「〇〇の調査では△△が□□%」のような文には table か balance を必ず 1 つ付ける。
type と items の書き方:

| type | 使う場面 | items の例 |
|---|---|---|
| flow | 因果・順番（A→B→C） | ["輸入コスト上昇", "企業間の取引価格", "店頭の値札"]（2〜4 個、各 10 字以内） |
| compare | 2 つのものの違い | title "値札 vs 給料", items ["見る回数\|週に何十回\|月に1回", "動く頻度\|毎週\|年1回"]（行は 見出し\|左\|右） |
| balance | 数字の差し引き | ["名目賃金 +5.1%", "物価 +3.2%", "実質 ▲1.9%"]（3 個。最後が結果） |
| steps | 手順・見分け方・条件 | ["額面と手取りを分ける", "物価の伸びを引く", "100gあたりで比べる"]（2〜4 行、各 16 字以内） |
| table | 数字の一覧 | ["2022年\|2.1%", "2023年\|3.6%", "2024年\|5.1%"]（項目\|値 を 2〜5 行） |

- title は 16 字以内の体言止め。note に出典（機関名・調査名・年）。出典が無い図も「概念図」と分かるようにする
- after_sentence は、その説明を話している文の番号（0 始まり）
- 図の中の数字は台本にあるものだけ。作らない
- 文字だけのカード（cards）は図にできないものに限る。図にできるなら diagrams にする

# 画面の文字は「文章」ではなく「体言止め」で書く（最重要の見た目の規則）

見出し(heading)・箇条書き(on_screen)・テロップ(captions)・文字カード(cards)は、
ナレーションの文をそのまま書かない。**名詞で止める。誰かの発言は「発言者「引用」」の形。
数字は「何が・いくつ・いつ」を名詞句にし、出典は別行（source）に分ける。**

| ナレーション（音声） | 画面に出す形 |
|---|---|
| 連合はこれを1990年代前半以来の水準だとしているのだ | 連合「1990年代前半以来の水準」 |
| それなのに厚生労働省の毎月勤労統計調査では、実質賃金がマイナスの月が長く続いた | text: 実質賃金がマイナスの月が26か月連続 / source: 毎月勤労統計調査（厚生労働省） |
| 値札が先に動き、給料は年1回しか動かないのだ | 先に動く値札、年1回の給料 |
| 値上げが止まる会社のほうが危ないのだ | 値上げが止まる会社ほど危険 |

- heading は 18 字以内の体言止め。「〜のか」「〜する」「〜だ」で終わらせない
- cards は各セクションに 2〜4 枚。text は 22 字以内の体言止め、source は出典があるときだけ。
  after_sentence（何文目の後に出すか）を付ける。**数字や固有名詞を含む文には必ず 1 枚作る**
- on_screen・captions も同じ体言止め

# テロップ（captions）の決め方

字幕とは別物です。字幕は全部の発言を出しますが、テロップは
**意味を圧縮して画面に置くもの**です。喋った通りに書かない。

例) 発言「マレーシアに来て一番びっくりしたのが家賃なんですよ」
    テロップ「一番驚いたこと → 家賃」

種類は6つ。after_sentence で「そのセクションの何文目の後に出すか」を指定する。

| type | 使いどころ | 目安 |
|---|---|---|
| NORMAL | 補助的な見出し | 少なめ |
| KEYWORD | 重要な単語だけを置く | 1セクション1〜2回 |
| EMPHASIS | その回の主張。ここだけは覚えて帰ってほしい一文 | 動画全体で2〜3回 |
| PUNCHLINE | オチ・意外な一言 | 動画全体で1〜3回 |
| EDITORIAL | 編集者視点の注釈「※ここ、ぼくも最初は分からなかった」 | 1分あたり0〜3回 |
| DATA | 数字・金額・割合（**台本にある実際の数字だけ**。「数十円（例）」のような仮の数字は付けない） | 数字を言うたび |

- テロップは1つ**16字以内**。長いと読めません
- EDITORIAL を入れすぎない。編集の声が本人より目立つと嘘くさくなります
- PUNCHLINE は本当にオチのときだけ。連発すると効きません

# 効果音（sounds）の決め方

意味のあるところにだけ置く。**入れすぎると安っぽくなります。**

| type | 意味 |
|---|---|
| POP | テロップの出現 |
| CLICK | 数値・箇条書きの提示 |
| WHOOSH | 場面転換 |
| IMPACT | 重大な発言 |
| COMEDY | オチ・ツッコミ |
| ERROR | 失敗・矛盾の指摘 |
| RISER | 次の展開への引き |
| TRANSITION | 章の切り替え |

**1分あたり8個まで、最低5秒は間隔を空ける。**
静かな場面では意図的に何も鳴らさないこと。無音も設計のうちです。

# 画面（visual）の決め方
- kind="chart": 数値の推移・比較を語るセクション。実在する公開統計の
  おおよその値のみを使い、値を創作しないこと。不確かなら kind を変える。
  chart.note に「出典: 総務省 消費者物価指数(2024)」のように必ず明記する。
- kind="textcard": 定義・仕組み・3つのポイントなど、文字で見せた方が早いもの
- kind="stock": 上記以外。query は英語の検索語（例 "tokyo office workers commuting"）
- **image_prompt は全セクションに必ず書く**（kind が chart でも）。実写素材が取れない
  ときに AI 画像で「概念の視覚化」を作るためのもの。上の画像プロンプトの式に従う
- on_screen は画面に出す短い箇条書き。1項目20字以内、最大4項目。
  ナレーションの丸写しにしない。

# 事実の扱い（最重要）
- 断定できない予測は「〜という見方があります」と主体を明示する
- 数字は「およそ」「約」を付け、桁を間違えない
- 出典が示せない主張は書かない。書くなら「諸説あります」と明言する
- 扱ってはいけない話題: {banned}

# 文字数とリズム
読み上げ本文（hook + proof + promise + 全 narration + closing）の合計を
**{lo}〜{hi}文字**に収めてください。これは動画尺 {mins_lo}〜{mins_hi} 分に相当します。
セクションごとの分量はほぼ均等にしてください。

**1文は20〜30字。** 40字を超える文を作らないこと。
長い文が必要に見えたら、それは2つの内容が混ざっているサインです。分けてください。

# 出力上の注意
- narration に記号（「」以外の括弧、箇条書き記号、URL、絵文字）を入れない。
  音声合成がそのまま読んでしまいます。
- 数字は読み上げ可能な表記にする（「1,200億円」→「千二百億円」ではなく
  「1200億円」でよいが、「%」は「パーセント」と書く）
- description は YouTube 概要欄。冒頭2行で内容が分かるようにし、
  チャプター（0:00 形式）はこちらで後付けするので入れないこと。
- tags は日本語中心に12〜15個。
"""

_DIALOGUE_STYLE = """**この台本は 2 人の掛け合いです。読み上げ本文（hook / proof / promise / narration / closing）は
すべて、文頭に話者タグを付けた発言の連なりで書いてください。**

出演者:
{cast}

書き方:
- 発言の先頭に【{teacher_tag}】または【{student_tag}】を付ける。表情タグ（[驚] など）はその後ろ。
    例) 【{student_tag}】[困]先輩、給料は上がったはずなのに、なんで苦しいままなのだ？
        【{teacher_tag}】いい質問ね。答えは「見る回数の差」にあるのよ。
- 話者が変わるたびにタグを付ける。同じ人が続けて話すときは最初の文だけでよい
- 分量は {teacher_tag} が 7 割、{student_tag} が 3 割。{student_tag} の発言は 1〜2 文で短く、
  「視聴者が今まさに思っている疑問」や「素朴な聞き返し」「分かった！の言い直し」にする
- 各セクションは {student_tag} の疑問か反応で始め、{teacher_tag} が答える形で進める
- 数字・出典・因果の説明は {teacher_tag} が言う。{student_tag} は数字を言わない
- {student_tag} は分かったふりをしない。難しい語が出たら必ず「それって何なのだ？」と聞き返し、
  {teacher_tag} が身近な例で言い換える（ここが視聴者の理解の階段になる）
- hook は {student_tag} の困りごと（新卒の生活実感）から始め、{teacher_tag} が「今日はそこを解くわ」と受ける
- closing は {teacher_tag} のまとめ → {student_tag} の「今日分かったこと」の言い直し → {teacher_tag} の締め

やってはいけないこと:
- タグ無しの文を作らない（誰の声で読むか決まらなくなる）
- 2 人の語尾を混ぜない（{teacher_tag} が「のだ」、{student_tag} が「〜よ」にならないように）
- 見出し(heading)・箇条書き(on_screen)・テロップ(captions)・タイトル案・サムネ文言・用語カードは
  **すべて標準語**で書き、話者タグも語尾の癖も入れない
- 1文は 20〜30 字。合成音声は長い文だと単調になる
"""

# 声のキャラクターと台本の語尾は必ずセットで変える。
# ずんだもんの声で「です・ます」を読ませると、視聴者には強い違和感が出る。
_SPEECH_STYLE = {
    "plain": """語尾は「です・ます」。落ち着いた解説者の口調で書いてください。""",

    "zundamon": """**語尾を「〜のだ」「〜なのだ」にしてください（ずんだもん口調）。**

この口調は声とセットです。声はずんだもんなので、です・ます調で書くと
視聴者に強い違和感が出ます。必ず守ってください。

書き方:
- 断定は「〜なのだ」「〜のだ」
    例) 円の価値が下がることを円安と言うのだ
- 問いかけは「〜かな」「〜だろうか」
    例) なぜ円が売られるのかな
- 呼びかけは「〜なのだ」で受ける
    例) ここが今日いちばん大事なところなのだ
- 一人称は「ぼく」。視聴者への呼びかけは「みんな」

やってはいけないこと:
- 全文の語尾を「のだ」で揃えない。**3文に1回程度**に留める。
  毎文「のだ」だと読んでいて疲れるし、音声でも単調になる
- 幼稚にしない。**口調は可愛くても、中身は手を抜かない。**
  数字・出典・因果の説明はむしろ丁寧にする。
  そのギャップがこのチャンネルの価値になる
- 「〜だぞ」「〜だね」など他キャラの語尾を混ぜない
- 専門用語を避けない。言い換えを添えたうえで、正しい語を使うのだ
- **hook の最後の1文と closing の最後の1文は必ず「のだ」調で締める。**
  ここに です・ます が混ざると、声と合わずいちばん目立つ
- **「のだ」調は読み上げ本文（hook / proof / promise / narration / closing）だけ。**
  見出し(heading)・箇条書き(on_screen)・テロップ(captions)・タイトル案・
  サムネ文言・用語カードは**すべて標準語**で書く。
  画面に出る文字に「のだ」が入ると幼稚に見える。
    悪い例) 見出し「じゃあ、ぼくらはどうするのだ」
    良い例) 見出し「私たちはどうすればいいか」
- **1文を20〜30字に収める。** 合成音声は長い文だと抑揚が単調になり、
  聞き手が置いていかれる。実測でも、ずんだもん音声のチャンネルは
  1文19.5字、人の声のチャンネルは41字と倍以上の差があった。
  声が合成である以上、短いほうに寄せるのが正しい""",
}


_HORIZON_GUIDE = {
    "flow": """いま日本で話題になっている件です。視聴者はニュースを見た直後に
来ます。**前提説明を長くしないこと。** 「何が起きたか」は30秒で流し、
残りを「なぜ起きたか」と「自分にどう効くか」に使ってください。""",

    "bridge": """海外で先に起きており、日本には数ヶ月以内に降りてくる件です。
視聴者はまだ自分ごとだと思っていません。次の順で橋を架けてください。
  1. 日本のいまの状況（視聴者が知っている足場）から入る
  2. 海外ではすでにこうなっている、という事実を数字で示す
  3. **なぜ日本には遅れて来るのか**を制度・商習慣・規制で説明する。
     ここがこのチャンネルの独自性になる部分です。省略しないこと
  4. 日本に来たとき何が変わるか。いつ頃かの見立ても言う
  5. だから今のうちに何をしておくと得か""",

    "stock": """海外のみで起きており、日本ではまだほとんど誰も話していない件です。
**今日の再生数のために作る回ではありません。** 日本でこの話題が立ち上がった
ときに、検索して最初に見つかる1本を作るのが目的です。そのため:
  - 「先週」「今月」のような**時点に依存する表現を使わない**。1年後に見ても
    古びない書き方にする。年号は「2026年時点では」と明示して使う
  - 用語の定義を省略しない。後から来た初見の人がこの1本で足りる状態にする
  - hook は煽らず、「この言葉を初めて聞いた人向けに、最初から説明します」
    という入口にする。検索で来た人は説明を求めており、驚きを求めていない
  - 日本にいつ・どういう形で来るかの見立てを必ず1セクション割く
  - 最後に「この動画は日本で話題になる前に作りました」と言わない。
    それは概要欄の仕事です""",
}


_USER = """# 今日つくる動画

テーマ: {title}
切り口: {angle}
この回の位置づけ: {horizon}（日本での普及段階 {stage}/3、一般化まで推定 {lag}ヶ月）
なぜ今か: {why_now}
日本の視聴者への接続: {bridge}
視聴者が自分ごと化できる点: {hook}
answer すべき問い:
{questions}

参考になる一次情報（未確認のものは使わない）:
{sources}

# 台本の前に作った「リサーチの木」
{research}

採用（adopt=true）したレンズを ACADEMIC_LENS と PERSPECTIVE_FLIP に使い、
paradox を hook に、question を promise に、flip を PERSPECTIVE_FLIP に、
human_return を HUMAN_RETURN と closing に反映してください。
この内容で動画1本ぶんの台本を作ってください。"""


_RESEARCH_SYSTEM = """あなたは日本語の経済解説YouTube動画のリサーチャーです。
**まだ台本は書きません。** 1本の動画を「日常の違和感 → 経済学のレンズ → 意外な説明」
で作るために、説明できる視点を先に集めます。

手順:
1. 視聴者（{audience}）が生活で感じている違和感を1つの問い（日常語）にする
2. 「普通はこう思われている」と「でも実際は」を対にする（ここが冒頭になる）
3. 次のレンズをすべて当て、それぞれ仮説・使える実在の統計（名前と年）・意外さ(1〜5)を書く
{lenses}
4. 一番強い反論を書き、それでも説明が成り立つ理由を書く
5. 中盤で視点を反転させる第二の疑問（flip）を1つ決める
6. 最後に、給料・買い物・働き方など視聴者の生活へどう戻すかを書く
7. Knowledge Gap 型のタイトル案を5つ（なぜ〜なのか？／〜な人ほど〜／意外な〜）

守ること:
- 統計は実在するものだけ。不確かなら「要確認」と書く。数値を創作しない
- 扱ってはいけない話題: {banned}
"""


def research_tree(cfg: Config, topic: Topic) -> dict[str, Any]:
    """台本を書く前に、説明できる視点を集めて 2〜3 個を選ぶ（リサーチの木）."""
    from . import bible

    system = _RESEARCH_SYSTEM.format(
        audience=cfg.get("channel.audience", ""),
        lenses=bible.research_prompt_block(cfg),
        banned="、".join(cfg.get("channel.banned_topics", []) or []),
    )
    user = _USER.split("# 台本の前に作った")[0].format(
        title=topic.title, angle=topic.angle, horizon=topic.horizon,
        stage=topic.diffusion_stage, lag=f"{topic.lag_months:.0f}",
        bridge=topic.japan_bridge or "(指定なし)", why_now=topic.why_now or "(常設テーマ)",
        hook=topic.audience_hook,
        questions="\n".join(f"- {q}" for q in topic.key_questions) or "- (自由)",
        sources=_fmt_sources(topic.sources),
    ) + "\nこのテーマのリサーチの木を作ってください。"
    data = llm.complete_json(
        system, user, _RESEARCH_SCHEMA,
        model=cfg.get("script.model", llm.DEFAULT_MODEL),
        effort=cfg.get("script.research_effort", "medium"),
    )
    adopted = [x for x in data.get("lenses", []) if x.get("adopt")]
    log.info("リサーチの木: 問い「%s」/ レンズ %d 個中 %d 個採用",
             data.get("question", "")[:30], len(data.get("lenses", [])), len(adopted))
    return data


def _fmt_research(r: dict[str, Any]) -> str:
    if not r:
        return "(なし)"
    lines = [
        f"問い: {r.get('question', '')}",
        f"普通はこう思われている: {r.get('common_belief', '')}",
        f"でも実際は: {r.get('paradox', '')}",
        f"自分ごと化の場面: {r.get('everyday_scene', '')}",
        "レンズ:",
    ]
    for x in r.get("lenses", []):
        mark = "採用" if x.get("adopt") else "不採用"
        lines.append(f"  - [{mark}] {x.get('id', '')} {x.get('name', '')}: {x.get('hypothesis', '')}"
                     f"（根拠: {x.get('evidence', '')}）")
    lines += [
        f"反論: {r.get('counterargument', '')}",
        f"反転させる第二の疑問: {r.get('flip', '')}",
        f"生活へ戻す: {r.get('human_return', '')}",
    ]
    return "\n".join(lines)


def _fmt_sources(sources: list[dict[str, str]]) -> str:
    if not sources:
        return "(指定なし。公的統計を自分で選んでください)"
    return "\n".join(f"- {s.get('name','')} {s.get('url','')}" for s in sources)


def target_chars(cfg: Config) -> tuple[int, int]:
    """目標文字数。参照動画から実測した話速があればそちらを使う.

    config の chars_per_minute は当て推量の初期値。learn を走らせたあとは
    実測値のほうが当たるので、そちらを優先する。
    """
    from .learn import load_style

    style = load_style(cfg)
    measured = ((style or {}).get("measured") or {}).get("chars_per_minute")
    if not measured:
        return cfg.target_chars
    lo_min = float(cfg.get("video.target_minutes_min", 8.0))
    hi_min = float(cfg.get("video.target_minutes_max", 10.0))
    return int(measured * lo_min), int(measured * hi_min)


def _style_block(cfg: Config) -> str:
    from .learn import load_style, render_for_prompt

    style = load_style(cfg)
    return render_for_prompt(style) if style else ""


def cast_tags(cfg: Config) -> dict[str, str]:
    """{話者タグ名: 話者キー}。掛け合いモードでなければ空."""
    cast = cfg.get("cast", {}) or {}
    if str(cast.get("mode", "solo")) != "dialogue":
        return {}
    out: dict[str, str] = {}
    for c in cast.get("characters", []) or []:
        key = str(c.get("key", "")).strip()
        if not key:
            continue
        for name in (c.get("tag"), c.get("name"), key):
            if name:
                out[str(name)] = key
    return out


def speech_style(cfg: Config) -> str:
    """語り口の指示。掛け合いモードなら 2 人分の人物設定を含む."""
    cast = cfg.get("cast", {}) or {}
    chars = cast.get("characters", []) or []
    if str(cast.get("mode", "solo")) == "dialogue" and len(chars) >= 2:
        teacher = next((c for c in chars if c.get("role") == "teacher"), chars[0])
        student = next((c for c in chars if c.get("role") == "student"), chars[1])
        desc = "\n".join(f"- 【{c.get('tag') or c.get('name')}】{c.get('name')}: {str(c.get('persona', '')).strip()}"
                         for c in chars)
        return _DIALOGUE_STYLE.format(cast=desc, teacher_tag=teacher.get("tag") or teacher.get("name"),
                                      student_tag=student.get("tag") or student.get("name"))
    return _SPEECH_STYLE.get(str(cfg.get("channel.speech_style", "plain")), _SPEECH_STYLE["plain"])


def generate(cfg: Config, topic: Topic) -> VideoScript:
    """台本を生成し、尺と事実の観点で補正して返す."""
    from . import bible

    lo, hi = target_chars(cfg)
    n_sections = int(cfg.get("video.body_sections", 5))
    research: dict[str, Any] = {}
    if cfg.get("script.research_tree", True):
        try:
            research = research_tree(cfg, topic)
        except Exception as exc:              # リサーチが落ちても台本は作る
            log.warning("リサーチの木を作れませんでした（台本だけ作ります）: %s", exc)
    system = _SYSTEM.format(
        bible=bible.render_for_prompt(cfg, n_sections),
        style_block=_style_block(cfg),
        speech_style=speech_style(cfg),
        horizon_guide=_HORIZON_GUIDE.get(topic.horizon, _HORIZON_GUIDE["flow"]),
        audience=cfg.get("channel.audience", ""),
        tone=cfg.get("channel.tone", ""),
        sections=int(cfg.get("video.body_sections", 5)),
        banned="、".join(cfg.get("channel.banned_topics", []) or []),
        lo=lo,
        hi=hi,
        mins_lo=cfg.get("video.target_minutes_min", 8),
        mins_hi=cfg.get("video.target_minutes_max", 10),
    )
    user = _USER.format(
        title=topic.title,
        angle=topic.angle,
        horizon=topic.horizon,
        stage=topic.diffusion_stage,
        lag=f"{topic.lag_months:.0f}",
        bridge=topic.japan_bridge or "(指定なし)",
        why_now=topic.why_now or "(常設テーマ)",
        hook=topic.audience_hook,
        questions="\n".join(f"- {q}" for q in topic.key_questions) or "- (自由)",
        sources=_fmt_sources(topic.sources),
        research=_fmt_research(research),
    )

    model = cfg.get("script.model", llm.DEFAULT_MODEL)
    effort = cfg.get("script.effort", "high")

    data = llm.complete_json(system, user, _SCRIPT_SCHEMA, model=model, effort=effort)
    script = VideoScript.from_dict(data)
    script.topic_title = script.topic_title or topic.title
    script.research = research
    _assign_beats(cfg, script)

    if cfg.get("script.fact_check", True):
        script = fact_check(cfg, script)

    script = fit_length(cfg, script)
    script.research = research            # 校閲・尺調整で作り直されても残す
    _assign_beats(cfg, script)
    if cfg.get("script.cards", True):
        try:
            ensure_cards(cfg, script)
        except Exception as exc:
            log.warning("文字カードを作れませんでした（一文カードで代用）: %s", exc)
    if cfg.get("script.diagrams", True):
        try:
            ensure_diagrams(cfg, script)
        except Exception as exc:
            log.warning("図解を作れませんでした（カードで代用）: %s", exc)
    _sanitize(script)
    _check_style(cfg, script)
    log.info("台本生成完了: %s (%d文字)", script.topic_title, script.total_chars)
    return script


# ----------------------------------------------------------------------
# 尺あわせ
# ----------------------------------------------------------------------
_REPAIR_SYSTEM = """あなたは日本語動画台本の編集者です。
与えられた台本の**読み上げ本文の文字数だけ**を調整します。

守ること:
- 構成・セクション数・visual・on_screen・出典は変えない
- 削るときは具体例と繰り返しから削る。数字と出典は残す
- 足すときは「なぜそうなるか」の説明と身近な例えを足す。新しい数字を創作しない
- **語尾の口調を変えない**（「〜のだ」調ならそのまま維持する）
- 文頭の話者タグ（【めたん】【ずんだもん】など）と表情タグ（[驚] など）は消さない。誰の発言かを変えない
- 語り口は元のまま
"""


def fit_length(cfg: Config, script: VideoScript) -> VideoScript:
    """目標文字数レンジに収まるまで伸縮リライトする."""
    lo, hi = target_chars(cfg)
    tries = int(cfg.get("script.max_length_repairs", 2))
    for attempt in range(tries):
        n = script.total_chars
        if lo <= n <= hi:
            return script
        direction = "短く" if n > hi else "長く"
        target = (lo + hi) // 2
        log.info("尺調整 %d回目: 現在%d文字 → %d文字前後へ(%s)", attempt + 1, n, target, direction)
        user = (
            f"現在の読み上げ本文は合計{n}文字です。これを{target}文字前後"
            f"（許容{lo}〜{hi}文字）に{direction}してください。\n\n"
            f"```json\n{json.dumps(script.to_dict(), ensure_ascii=False, indent=2)}\n```"
        )
        data = llm.complete_json(
            _REPAIR_SYSTEM, user, _SCRIPT_SCHEMA,
            model=cfg.get("script.model", llm.DEFAULT_MODEL), effort="medium",
        )
        script = VideoScript.from_dict(data)
    if not (lo <= script.total_chars <= hi):
        log.warning("尺が目標レンジ外のままです: %d文字 (目標 %d〜%d)",
                    script.total_chars, lo, hi)
    return script


# ----------------------------------------------------------------------
# ファクトチェック
# ----------------------------------------------------------------------
_FACT_SYSTEM = """あなたは経済メディアの校閲担当です。
渡された台本の中の「事実主張」を洗い出し、次の方針で**台本を修正**してください。

- 出典を示せない具体的な数字は、幅を持たせた表現か定性的表現に書き換える
- 出典があるものは narration 内で「◯◯によると」と口頭で言う形に整える
- 予測・見通しは必ず主体を明示する（「◯◯は〜と予測しています」）
- 投資助言に読める表現は削除する（「買い時」「上がる」等）
- グラフ(chart)の数値に自信が持てない場合は kind を textcard に変え、
  該当セクションの narration もそれに合わせて書き換える
- 断定できない箇所が残る場合は disclaimer に一文添える

構成・セクション数・文字数は大きく変えないでください。
**語尾の口調も変えないでください**（「〜のだ」調ならそのまま維持する）。
文頭の話者タグ（【めたん】【ずんだもん】など）と表情タグは消さず、誰の発言かも変えないでください。
"""


def fact_check(cfg: Config, script: VideoScript) -> VideoScript:
    user = (
        "次の台本を校閲し、修正後の台本をそのまま返してください。\n\n"
        f"```json\n{json.dumps(script.to_dict(), ensure_ascii=False, indent=2)}\n```"
    )
    data = llm.complete_json(
        _FACT_SYSTEM, user, _SCRIPT_SCHEMA,
        model=cfg.get("script.model", llm.DEFAULT_MODEL), effort="high",
    )
    log.info("ファクトチェック完了")
    return VideoScript.from_dict(data)


# ----------------------------------------------------------------------
# 読み上げ用の整形
# ----------------------------------------------------------------------
# 表情タグ（ゆっくりMovieMaker の「表情切り替え」に相当）。文頭に [驚] のように書く。
# 音声には読まれず、字幕にも出ず、その一文のあいだだけ立ち絵の表情が変わる
EXPRESSIONS = ("通常", "笑", "驚", "困", "考", "指", "怒")
_TAG_RE = re.compile(r"[\[【（(]\s*(" + "|".join(EXPRESSIONS) + r")\s*[\]】）)]")
# 話者タグ（掛け合い台本）。【めたん】【ずんだもん】のように文頭に置く。表情タグはその後ろ
_SPEAKER_RE = re.compile(r"[【\[]\s*([^\]】\[【]{1,8}?)\s*[】\]]")


def parse_expression(sentence: str) -> tuple[str, str]:
    """文頭（または文中）の表情タグを取り出し、(表情, タグを除いた文) を返す."""
    m = _TAG_RE.search(sentence)
    if not m:
        return "", sentence
    return m.group(1), _TAG_RE.sub("", sentence).strip()


def parse_speaker(sentence: str, tags: dict[str, str] | None = None) -> tuple[str, str]:
    """文頭の話者タグを取り出し、(話者キー, タグを除いた文) を返す。無ければ ("", 文).

    tags は {タグ名: 話者キー}（例 {"めたん": "metan", "ずんだもん": "zundamon"}）。
    None なら表情タグ以外の【…】を話者名とみなす。
    """
    s = sentence.lstrip()
    m = _SPEAKER_RE.match(s)
    if not m or m.group(1) in EXPRESSIONS:
        return "", sentence
    name = m.group(1).strip()
    if tags is not None and name not in tags:
        return "", sentence
    return (tags[name] if tags else name), s[m.end():].lstrip()


def strip_speaker(text: str, tags: dict[str, str] | None = None) -> str:
    """文中の話者タグを全部落とす（画面に出す文字用）."""
    def rep(m):
        name = m.group(1).strip()
        if name in EXPRESSIONS:
            return m.group(0)
        if tags is not None and name not in tags:
            return m.group(0)
        return ""
    return _SPEAKER_RE.sub(rep, text)


def strip_tags(text: str) -> str:
    return strip_speaker(_TAG_RE.sub("", text))


_TTS_REPLACEMENTS = [
    (r"https?://\S+", ""),
    (r"[【】\[\]（）\(\)]", " "),
    (r"[※＊*・･]", ""),
    (r"[☀-➿\U0001F300-\U0001FAFF]", ""),   # 絵文字
    (r"％", "パーセント"),
    (r"%", "パーセント"),
    (r"〜", "から"),
    (r"～", "から"),
    (r"\s{2,}", " "),
]


def tts_text(text: str) -> str:
    """音声合成がつまずく記号を落とす（表情タグ [驚] などは残す）."""
    # 表情タグ・話者タグは括弧を落とす処理から守る
    out = _TAG_RE.sub(lambda m: f"\ue000{m.group(1)}\ue001", text)
    out = _SPEAKER_RE.sub(lambda m: f"\ue002{m.group(1).strip()}\ue003", out)
    for pattern, repl in _TTS_REPLACEMENTS:
        out = re.sub(pattern, repl, out)
    out = re.sub("\ue000(" + "|".join(EXPRESSIONS) + ")\ue001", r"[\1]", out)
    out = re.sub("\ue002([^\ue003]{1,8})\ue003", r"【\1】", out)
    return out.strip()


_QUESTION_WORDS = ("なぜ", "どう", "何", "なに", "どこ", "いつ", "誰", "だれ", "どれ", "どちら")


def plain_heading(text: str) -> str:
    """画面に出す文字から「のだ」調を落とす（プロンプトの指示が漏れたとき用の保険）.

    疑問語を含む見出しは「〜のか」に、それ以外は語尾を削る。
      「なぜ苦しいのだ」          → 「なぜ苦しいのか」
      「じゃあ、ぼくらはどうするのだ」→ 「じゃあ、ぼくらはどうするのか」
      「これが円安なのだ」        → 「これが円安」
    """
    t = strip_tags(text).strip()          # 話者タグ・表情タグは画面に出さない
    for tail in ("なのだ", "のだ"):
        if t.endswith(tail):
            base = t[: -len(tail)]
            if any(q in t for q in _QUESTION_WORDS):
                return base + "のか"
            return base
    return t


_CARDS_SYSTEM = """あなたは日本語の解説動画のテロップ担当です。台本の各セクションの本文を読み、
画面に出す文字カード(cards)を 2〜4 枚ずつ作ります。

規則:
- **文章ではなく体言止め。** 名詞で止める。「〜のだ」「〜する」「〜だ」「〜のか」で終わらせない
- 誰かの発言・見解は 発言者「引用」 の形（例: 連合「1990年代前半以来の水準」）
- 数字は「何が・いくつ・いつ」を名詞句にし（例: 実質賃金がマイナスの月が26か月連続）、
  出典は source に分ける（例: 毎月勤労統計調査（厚生労働省））
- text は 22 字以内。source は出典があるときだけ、機関名・調査名を短く
- after_sentence は、その内容を話している文の番号（0 始まり）
- 数字や固有名詞を含む文には必ず 1 枚作る。それ以外は要点だけ
- 台本に無い数字・出典を作らない
"""


def ensure_cards(cfg: Config, script: VideoScript, force: bool = False) -> VideoScript:
    """cards が無い（古い）台本に、体言止めの文字カードを後付けする."""
    if not force and all(sec.cards for sec in script.sections) and script.block_cards:
        return script
    # 導入（hook / proof / promise）と締め（closing）も「セクション」として一緒に頼む
    blocks = [("hook", "導入・つかみ", script.hook), ("proof", "導入・裏づけ", script.proof),
              ("promise", "導入・約束", script.promise), ("closing", "締め", script.closing)]
    body = []
    for key, name, text in blocks:
        sents = split_sentences(strip_tags(text))
        if sents:
            body.append(f"## {key}: {name}\n" + "\n".join(f"{k}: {t}" for k, t in enumerate(sents)))
    for i, sec in enumerate(script.sections):
        sents = split_sentences(strip_tags(sec.narration))
        body.append(f"## s{i}: {sec.heading}\n" + "\n".join(f"{k}: {t}" for k, t in enumerate(sents)))
    user = ("次の台本の各ブロック（hook / proof / promise / s0.. / closing）に cards を作ってください。"
            "sections の並びと数は入力と同じにし、heading にブロック名（hook, s0 など）を入れてください。\n\n"
            + "\n\n".join(body))
    data = llm.complete_json(_CARDS_SYSTEM, user, _CARDS_SCHEMA,
                             model=cfg.get("script.model", llm.DEFAULT_MODEL), effort="medium")
    got = data.get("sections") or []

    def to_cards(items):
        return [Card(text=plain_heading(c.get("text", ""))[:40], source=(c.get("source") or "")[:30],
                     after_sentence=int(c.get("after_sentence", 0) or 0))
                for c in (items or []) if c.get("text")]

    by_name = {str(item.get("heading", "")).strip().split(":")[0]: item for item in got}
    keys = [k for k, _, t in blocks if split_sentences(strip_tags(t))] + [f"s{i}" for i in range(len(script.sections))]
    for pos, key in enumerate(keys):
        item = by_name.get(key) or (got[pos] if pos < len(got) else None)
        if not item:
            continue
        cards = to_cards(item.get("cards"))
        if key.startswith("s") and key[1:].isdigit():
            script.sections[int(key[1:])].cards = cards
        else:
            script.block_cards[key] = cards
    log.info("文字カードを後付け: 本編 %d 枚 / 導入・締め %d 枚",
             sum(len(s.cards) for s in script.sections), sum(len(v) for v in script.block_cards.values()))
    return script


_DIAGRAMS_SYSTEM = """あなたは日本語の解説動画の図解担当です。台本の各セクションの本文を読み、
言葉だけで説明している「仕組み・順番・比較・差し引き・手順・数字の一覧」を図解(diagrams)にします。
各セクションに **3〜5 個**（できるだけ多く。画面は 8 秒ごとに変わる）。仕組みの説明（因果の鎖）があれば flow、
**調査結果・統計・数字がある文には必ず table か balance** を付け、note に引用元（機関名・調査名・年）を書く。
概念の対比は compare、条件・手順は steps。

type と items の書き方:
- flow    : 因果・順番。items は 2〜4 個の短い語（各 10 字以内）。例 ["輸入コスト上昇", "企業間の取引価格", "店頭の値札"]
- compare : 2 つの違い。title は「A vs B」、items は "見出し|左|右" を 2〜4 行。例 "見る回数|週に何十回|月に1回"
- balance : 数字の差し引き。items は 3 個で最後が結果。例 ["名目賃金 +5.1%", "物価 +3.2%", "実質 ▲1.9%"]
- steps   : 手順・条件・見分け方。items は 2〜4 行（各 16 字以内）
- table   : 数字の一覧。items は "項目|値" を 2〜5 行

規則:
- title は 16 字以内の体言止め。**title だけで何の図か分かる**ようにする（「〜の2つの形」「〜の順番」など）
- note は **出典（機関名・調査名・年）だけ**。出典が無い図は note を空にする。
  「台本の〜より」「例示」「仮の数値」「一般的な期待」のような出典でない文は書かない
- after_sentence は、その説明を話している文の番号（0 始まり）
- 数字・固有名詞は台本にあるものだけ。作らない。「（例）」「（仮）」の付いた数字は使わない
- sections の並びと数は入力と同じにし、heading にブロック名（s0 など）を入れる
"""


def ensure_diagrams(cfg: Config, script: VideoScript, force: bool = False) -> VideoScript:
    """diagrams が無い（古い）台本に、図解を後付けする."""
    if not force and any(sec.diagrams for sec in script.sections):
        return script
    blocks = [("hook", "導入・つかみ", script.hook), ("proof", "導入・裏づけ", script.proof),
              ("promise", "導入・約束", script.promise), ("closing", "締め", script.closing)]
    body = []
    keys: list[str] = []
    for key, name, text in blocks:
        sents = split_sentences(strip_tags(text))
        if sents:
            body.append(f"## {key}: {name}\n" + "\n".join(f"{k}: {t}" for k, t in enumerate(sents)))
            keys.append(key)
    for i, sec in enumerate(script.sections):
        sents = split_sentences(strip_tags(sec.narration))
        body.append(f"## s{i}: {sec.heading}（{sec.beat}）\n" + "\n".join(f"{k}: {t}" for k, t in enumerate(sents)))
        keys.append(f"s{i}")
    user = ("次の台本の各ブロック（hook / proof / promise / s0.. / closing）に diagrams を作ってください。"
            "sections の並びと数は入力と同じにし、heading にブロック名（hook, s0 など）を入れてください。\n\n"
            + "\n\n".join(body))
    data = llm.complete_json(_DIAGRAMS_SYSTEM, user, _DIAGRAMS_SCHEMA,
                             model=cfg.get("script.model", llm.DEFAULT_MODEL), effort="medium")
    got = data.get("sections") or []
    by_name = {str(item.get("heading", "")).strip().split(":")[0]: item for item in got}

    def to_diagrams(items):
        return [Diagram(type=str(g.get("type", "flow")), title=plain_heading(g.get("title", "") or "")[:20],
                        items=[plain_heading(str(x))[:40] for x in (g.get("items") or []) if str(x).strip()],
                        note=(g.get("note") or "")[:48],
                        after_sentence=int(g.get("after_sentence", 0) or 0))
                for g in (items or []) if g.get("items")]

    for pos, key in enumerate(keys):
        item = by_name.get(key) or (got[pos] if pos < len(got) else None)
        if not item:
            continue
        if key.startswith("s") and key[1:].isdigit():
            script.sections[int(key[1:])].diagrams = to_diagrams(item.get("diagrams"))
        else:
            script.block_diagrams[key] = to_diagrams(item.get("diagrams"))
    log.info("図解を後付け: 本編 %d 個 / 導入・締め %d 個",
             sum(len(s.diagrams) for s in script.sections), sum(len(v) for v in script.block_diagrams.values()))
    return script


_NOMINAL_TAILS = ("なのだ", "のだ", "のです", "です", "ます", "である", "だ", "のかな", "かな", "だろうか")


def nominalize(sentence: str) -> str:
    """文を体言止めふうに縮める簡易変換（LLM のカードが無いときの保険）.

    「〜なのだ」「〜です」などの語尾を落とし、「〜のか」の問いは残す。完全ではない。
    """
    t = strip_tags(sentence).strip().rstrip("。！？!?")
    for tail in _NOMINAL_TAILS:
        if t.endswith(tail) and len(t) > len(tail) + 2:
            t = t[: -len(tail)]
            break
    return t.rstrip("、，")


def _assign_beats(cfg: Config, script: VideoScript) -> None:
    """beat が欠けた／順序が崩れたセクションに、9ブロックの順で beat を振り直す."""
    from . import bible

    order = bible.section_beats(cfg)
    n = len(script.sections)
    if not order or not n:
        return
    seen: list[str] = []
    for i, sec in enumerate(script.sections):
        want = bible.beat_for_section(cfg, i, n)
        ok = sec.beat in order and sec.beat not in seen and \
            order.index(sec.beat) >= (order.index(seen[-1]) if seen else -1)
        if not ok:
            sec.beat = want
        seen.append(sec.beat)


def _check_style(cfg: Config, script: VideoScript) -> None:
    """バイブルの規則に外れているところを警告する（生成は止めない）."""
    from . import bible

    bad = [t for t in script.title_candidates if not bible.title_matches(cfg, t)]
    if bad and len(bad) == len(script.title_candidates):
        log.warning("タイトル案が全部 Knowledge Gap 型でない: %s", bad[:3])
    main = (script.thumbnail_copy or {}).get("main", "")
    if bible.thumbnail_overlaps_title(cfg, main, script.topic_title):
        log.warning("サムネ主コピーがタイトルをなぞっている: 「%s」 / 「%s」", main, script.topic_title)
    words = bible.semantic_cut_words(cfg)
    pivots = sum(1 for sec in script.sections for sent in split_sentences(sec.narration)
                 if sent.startswith(words))
    if pivots < len(script.sections):
        log.warning("接続詞で始まる転換文が少ない（%d 文 / %d セクション）。カット点が減る",
                    pivots, len(script.sections))


# 言い換え（画面にも音声にも効く）。「釣り」は単独だと魚釣りに読めるので「お釣り」
_WORD_FIXES = (
    (re.compile(r"(?<!お)釣り"), "お釣り"),
)
# 出典ではない note（図解の下に「— 台本の〜より」と出てしまう）
_BAD_NOTE = re.compile(r"台本|描写|例示|仮の|一般的な期待|イメージ|想定")
# 仮の数字を数字カードにしない
_PLACEHOLDER = re.compile(r"[（(]\s*(例|仮|イメージ)\s*[)）]|（例|\(例")


def fix_words(text: str) -> str:
    for pat, rep_ in _WORD_FIXES:
        text = pat.sub(rep_, text)
    return text


def clean_note(note: str) -> str:
    """出典として成り立つ note だけ残す."""
    note = (note or "").strip()
    if not note or _BAD_NOTE.search(note):
        return ""
    return note


def _sanitize(script: VideoScript) -> None:
    script.hook = fix_words(tts_text(script.hook))
    script.proof = fix_words(tts_text(script.proof))
    script.promise = fix_words(tts_text(script.promise))
    script.closing = fix_words(tts_text(script.closing))
    script.topic_title = fix_words(script.topic_title)
    script.title_candidates = [fix_words(plain_heading(t)) for t in script.title_candidates]
    if script.thumbnail_copy:
        script.thumbnail_copy = {k: fix_words(plain_heading(v)) for k, v in script.thumbnail_copy.items()}
    for sec in script.sections:
        sec.heading = fix_words(plain_heading(sec.heading))
        sec.narration = fix_words(tts_text(sec.narration))
        sec.on_screen = [fix_words(plain_heading(s))[:24] for s in sec.on_screen][:4]
        sec.captions = [c for c in sec.captions if not _PLACEHOLDER.search(c.text)]
        for cap in sec.captions:
            cap.text = fix_words(plain_heading(cap.text))[:16]
        sec.cards = [c for c in sec.cards if not _PLACEHOLDER.search(c.text)]
        for card in sec.cards:
            card.text = fix_words(plain_heading(card.text))[:40]
        for g in sec.diagrams:
            g.title = fix_words(plain_heading(g.title))[:20]
            g.items = [fix_words(plain_heading(x))[:40] for x in g.items]
            g.note = clean_note(g.note)
    for key, cards in script.block_cards.items():
        script.block_cards[key] = [c for c in cards if not _PLACEHOLDER.search(c.text)]
        for card in script.block_cards[key]:
            card.text = fix_words(plain_heading(card.text))[:40]
    for diagrams in script.block_diagrams.values():
        for g in diagrams:
            g.title = fix_words(plain_heading(g.title))[:20]
            g.items = [fix_words(plain_heading(x))[:40] for x in g.items]
            g.note = clean_note(g.note)
    for term in script.terms:
        term.term = fix_words(plain_heading(term.term))
        term.meaning = fix_words(plain_heading(term.meaning))


def split_sentences(text: str) -> list[str]:
    """日本語の文分割。音声合成とテロップの単位になる."""
    parts = re.split(r"(?<=[。！？!?])\s*", text)
    return [p.strip() for p in parts if p.strip()]
