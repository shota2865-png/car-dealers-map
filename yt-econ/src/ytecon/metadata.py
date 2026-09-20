"""YouTube 用メタデータの組み立て.

タイトルは台本生成時の候補から Claude に選び直させる（サムネ文言との
重複を避け、クリック理由を1つに絞るため）。チャプターは音声の実測秒から
機械的に作る。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from . import llm
from .config import Config
from .script import VideoScript
from .tts import VoiceTrack

log = logging.getLogger(__name__)

MAX_TITLE = 100
MAX_DESCRIPTION = 5000


@dataclass
class Metadata:
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    category_id: str = "25"
    language: str = "ja"


_TITLE_SCHEMA = llm.obj(
    {
        "title": llm.STR,
        "reason": llm.STR,
        "thumbnail_main": llm.STR,
        "thumbnail_sub": llm.STR,
    }
)

_TITLE_SYSTEM = """あなたは日本語YouTubeのタイトル設計者です。視聴者は{audience}。

# この回の戦い方
{horizon_guide}

良いタイトルの条件:
- 40字以内。スマホで切れずに読めるのは冒頭28字程度なので、前半に要点を置く
- 「知らないと損」「ヤバい」など煽り語を使わない。内容と一致させる
- 数字か固有名詞を1つ入れる
- サムネの文言と同じ言葉を繰り返さない（同じ情報を2回見せると密度が下がる）
- 疑問形か、意外性のある事実の提示のどちらか

サムネ文言の条件:
- main は最大13字。遠目で読める短さ
- sub は最大14字。main を補う一言。無理なら空文字
"""


_TITLE_HORIZON = {
    "flow": """いま日本で話題の件です。**推薦（ブラウジング）で戦います。**
一覧に並んだときクリックされる語を選んでください。検索されることは
あまり期待しなくてよいので、意外性や問いを優先します。""",

    "bridge": """数ヶ月後に日本で話題化する件です。**推薦と検索の両取り**を狙います。
今クリックされる語を前半に、後から検索される語（制度名・カタカナ語など
固有の名詞）を後半に入れてください。""",

    "stock": """日本ではまだ話題になっていない件です。**検索で後から掘られるのが本番**で、
今日のクリック率はほぼ意味がありません。したがって:
  - 日本で話題化したときに人が打ち込む語を、**そのままの表記で**入れる
    （略称と正式名称が両方あるなら、検索されるほうを優先）
  - 「とは」「わかりやすく」「解説」のいずれかを入れる。後追いで調べる人は
    この語を付けて検索します
  - 意外性より説明性を優先する。煽ると、検索で来た人に不信感を与えます
  - 時点に依存する語（速報、今週、最新）は入れない。1年後に古びます""",
}


def choose_title(cfg: Config, script: VideoScript,
                 horizon: str = "flow") -> tuple[str, dict[str, str]]:
    candidates = "\n".join(f"- {t}" for t in script.title_candidates) or "- (候補なし)"
    user = f"""動画の内容:
テーマ: {script.topic_title}
導入: {script.hook}
各セクション見出し: {', '.join(s.heading for s in script.sections)}
まとめ: {script.closing[:200]}

台本側のタイトル候補:
{candidates}

この動画に最適なタイトルを1つ決め、サムネ文言も併せて出してください。
候補をそのまま使っても、書き直しても構いません。"""
    data = llm.complete_json(
        _TITLE_SYSTEM.format(
            audience=cfg.get("channel.audience", ""),
            horizon_guide=_TITLE_HORIZON.get(horizon, _TITLE_HORIZON["flow"]),
        ),
        user,
        _TITLE_SCHEMA,
        model=cfg.get("script.model", llm.DEFAULT_MODEL),
        effort="medium",
    )
    title = (data.get("title") or script.topic_title)[:MAX_TITLE]
    thumb = {
        "main": (data.get("thumbnail_main") or "")[:14],
        "sub": (data.get("thumbnail_sub") or "")[:16],
    }
    log.info("タイトル決定: %s", title)
    return title, thumb


def build_chapters(script: VideoScript, track: VoiceTrack) -> list[str]:
    """YouTube のチャプターは 0:00 始まり・3つ以上・各10秒以上が条件."""
    rows = []
    blocks = [("hook", "今日の話")] + [
        (f"s{i}", s.heading) for i, s in enumerate(script.sections)
    ] + [("closing", "まとめ")]
    last = -10.0
    for block_id, label in blocks:
        start, end = track.block_span(block_id)
        if end <= start or start - last < 10:
            continue
        m, s = divmod(int(start), 60)
        rows.append(f"{m}:{s:02d} {label}")
        last = start
    if rows and not rows[0].startswith("0:00"):
        rows[0] = "0:00 " + rows[0].split(" ", 1)[1]
    return rows if len(rows) >= 3 else []


# VOICEVOX は商用利用できるが、キャラクター名のクレジット表記が要る。
# 表記が無いと規約違反になるので、概要欄に自動で入れる。
_VOICEVOX_SPEAKERS = {
    1: "ずんだもん（あまあま）", 3: "ずんだもん（ノーマル）",
    5: "ずんだもん（セクシー）", 7: "ずんだもん（ツンツン）",
    22: "ずんだもん（ささやき）", 38: "ずんだもん（ヒソヒソ）",
    2: "四国めたん（ノーマル）", 8: "春日部つむぎ", 10: "雨晴はう",
    13: "青山龍星", 11: "玄野武宏", 14: "冥鳴ひまり", 16: "九州そら",
}


def _voice_credit(cfg: Config) -> str:
    """音声合成の使用明記とクレジット."""
    provider = str(cfg.get("tts.provider", "")).lower()
    if provider != "voicevox":
        return "この動画のナレーションは音声合成を使用しています。"
    speaker_id = int(cfg.get("tts.voicevox.speaker", 3))
    name = _VOICEVOX_SPEAKERS.get(speaker_id, f"話者ID {speaker_id}")
    return (
        "この動画のナレーションは音声合成ソフト VOICEVOX を使用しています。\n"
        f"VOICEVOX：{name}\n"
        "https://voicevox.hiroshiba.jp/"
    )


def build(cfg: Config, script: VideoScript, track: VoiceTrack,
          title: str | None = None) -> Metadata:
    title = title or (script.title_candidates or [script.topic_title])[0]

    parts = [script.description.strip()]

    chapters = build_chapters(script, track)
    if chapters:
        parts.append("■ もくじ\n" + "\n".join(chapters))

    if script.sources:
        srcs = "\n".join(
            f"・{s.get('name','')} {s.get('url','')}".rstrip() for s in script.sources
        )
        parts.append("■ 参考・出典\n" + srcs)

    disclaimer = script.disclaimer.strip()
    parts.append(
        "■ ご注意\n"
        + (disclaimer + "\n" if disclaimer else "")
        + "この動画は経済の仕組みを解説するもので、特定の金融商品の購入を\n"
        "推奨するものではありません。投資の判断はご自身の責任でお願いします。\n"
        "内容には万全を期していますが、誤りにお気づきの際はコメントで\n"
        "ご指摘いただけると助かります。"
    )

    parts.append("■ 音声\n" + _voice_credit(cfg))

    # 立ち絵・BGM などのクレジット（配布元の規約で表記が要るものは必ずここに書く）
    from .character import detect_credits
    credits = detect_credits(cfg) + [str(cfg.get("render.bgm.credit", "") or "").strip()]
    credits = [c for c in credits if c]
    if credits:
        parts.append("■ 素材\n" + "\n".join(credits))

    description = "\n\n".join(p for p in parts if p.strip())[:MAX_DESCRIPTION]

    tags = [t.strip() for t in script.tags if t.strip()][:15]
    return Metadata(
        title=title,
        description=description,
        tags=tags,
        category_id=str(cfg.get("upload.category_id", "25")),
        language=str(cfg.get("upload.language", "ja")),
    )
