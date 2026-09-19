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
@dataclass
class Visual:
    kind: str = "stock"              # stock | chart | textcard
    query: str = ""                  # stock 用の検索語（英語）
    chart: dict[str, Any] | None = None   # chart 用の仕様
    caption: str = ""                # 図表の出典キャプション


@dataclass
class Section:
    heading: str                     # チャプター名 兼 画面見出し
    narration: str                   # 読み上げ本文
    on_screen: list[str] = field(default_factory=list)   # 画面に出す箇条書き
    visual: Visual = field(default_factory=Visual)

    @property
    def char_count(self) -> int:
        return len(re.sub(r"\s", "", self.narration))


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

    @property
    def narration_blocks(self) -> list[tuple[str, str]]:
        """(セクションID, 読み上げテキスト) の並び。音声生成の入力になる."""
        blocks = [("hook", self.hook)]
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
                    visual=Visual(
                        kind=v.get("kind", "stock"),
                        query=v.get("query", ""),
                        chart=v.get("chart") or None,
                        caption=v.get("caption", ""),
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
        )

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "VideoScript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


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
    }
)

_SCRIPT_SCHEMA = llm.obj(
    {
        "topic_title": llm.STR,
        "hook": llm.STR,
        "sections": llm.arr(
            llm.obj(
                {
                    "heading": llm.STR,
                    "narration": llm.STR,
                    "on_screen": llm.arr(llm.STR),
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
{tone}

# この回の型
{horizon_guide}

# 構成の型（必ずこの流れ）
1. hook（15〜25秒）: 視聴者の生活に起きている「違和感」から入る。
   結論の予告を1文入れる。「今日は〜が分かります」で締める。
2. 本編 {sections} セクション:
   - 各セクションは「問い → 事実（数字） → なぜそうなるか → だから何」の順
   - 数字を出したら必ず出典（機関名と年）を narration 内で口頭で言う
   - 前のセクションの結論を1句受けてから次に進む（接続を切らない）
3. closing: 3行でまとめ → 視聴者への問いかけ → チャンネル登録の一言。
   押し付けがましくしない。

# 画面（visual）の決め方
- kind="chart": 数値の推移・比較を語るセクション。実在する公開統計の
  おおよその値のみを使い、値を創作しないこと。不確かなら kind を変える。
  chart.note に「出典: 総務省 消費者物価指数(2024)」のように必ず明記する。
- kind="textcard": 定義・仕組み・3つのポイントなど、文字で見せた方が早いもの
- kind="stock": 上記以外。query は英語の検索語（例 "tokyo office workers commuting"）
- on_screen は画面に出す短い箇条書き。1項目20字以内、最大4項目。
  ナレーションの丸写しにしない。

# 事実の扱い（最重要）
- 断定できない予測は「〜という見方があります」と主体を明示する
- 数字は「およそ」「約」を付け、桁を間違えない
- 出典が示せない主張は書かない。書くなら「諸説あります」と明言する
- 扱ってはいけない話題: {banned}

# 文字数
読み上げ本文（hook + 全 narration + closing）の合計を
**{lo}〜{hi}文字**に収めてください。これは動画尺 {mins_lo}〜{mins_hi} 分に相当します。
セクションごとの分量はほぼ均等にしてください。

# 出力上の注意
- narration に記号（「」以外の括弧、箇条書き記号、URL、絵文字）を入れない。
  音声合成がそのまま読んでしまいます。
- 数字は読み上げ可能な表記にする（「1,200億円」→「千二百億円」ではなく
  「1200億円」でよいが、「%」は「パーセント」と書く）
- description は YouTube 概要欄。冒頭2行で内容が分かるようにし、
  チャプター（0:00 形式）はこちらで後付けするので入れないこと。
- tags は日本語中心に12〜15個。
"""

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

この内容で動画1本ぶんの台本を作ってください。"""


def _fmt_sources(sources: list[dict[str, str]]) -> str:
    if not sources:
        return "(指定なし。公的統計を自分で選んでください)"
    return "\n".join(f"- {s.get('name','')} {s.get('url','')}" for s in sources)


def generate(cfg: Config, topic: Topic) -> VideoScript:
    """台本を生成し、尺と事実の観点で補正して返す."""
    lo, hi = cfg.target_chars
    system = _SYSTEM.format(
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
    )

    model = cfg.get("script.model", llm.DEFAULT_MODEL)
    effort = cfg.get("script.effort", "high")

    data = llm.complete_json(system, user, _SCRIPT_SCHEMA, model=model, effort=effort)
    script = VideoScript.from_dict(data)
    script.topic_title = script.topic_title or topic.title

    if cfg.get("script.fact_check", True):
        script = fact_check(cfg, script)

    script = fit_length(cfg, script)
    _sanitize(script)
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
- 語り口は元のまま
"""


def fit_length(cfg: Config, script: VideoScript) -> VideoScript:
    """目標文字数レンジに収まるまで伸縮リライトする."""
    lo, hi = cfg.target_chars
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
    """音声合成がつまずく記号を落とす."""
    out = text
    for pattern, repl in _TTS_REPLACEMENTS:
        out = re.sub(pattern, repl, out)
    return out.strip()


def _sanitize(script: VideoScript) -> None:
    script.hook = tts_text(script.hook)
    script.closing = tts_text(script.closing)
    for sec in script.sections:
        sec.narration = tts_text(sec.narration)
        sec.on_screen = [s.strip()[:24] for s in sec.on_screen][:4]


def split_sentences(text: str) -> list[str]:
    """日本語の文分割。音声合成とテロップの単位になる."""
    parts = re.split(r"(?<=[。！？!?])\s*", text)
    return [p.strip() for p in parts if p.strip()]
