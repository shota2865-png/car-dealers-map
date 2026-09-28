"""YouTube 用メタデータの組み立て.

タイトルは台本生成時の候補から Claude に選び直させる（サムネ文言との
重複を避け、クリック理由を1つに絞るため）。チャプターは音声の実測秒から
機械的に作る。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from . import domain, llm
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
        "keyword": llm.STR,
        "hook_tag": llm.STR,
        "reason": llm.STR,
        "thumbnail_main": llm.STR,
        "thumbnail_sub": llm.STR,
        "thumbnail_bubble": llm.STR,
    }
)

_HOOK_TAG_GUIDE = """
# 先頭の【】（hook_tag）
タイトルの先頭に【…】で 5〜10 字の引きを付けます。次のどれかの型で:
- 視聴者の状況を言い当てる問い（例: {question}）
- 数字の落差（例: {gap}）
- 意外な断言（例: {claim}）
【】は含めず、中身だけを hook_tag に入れる。煽り語（ヤバい・終わった・知らないと損）は使わない。
本文の title と同じ語を繰り返さない。
"""

_TITLE_SYSTEM = """あなたは日本語YouTubeのタイトル設計者です。視聴者は{audience}。

# この回の戦い方
{horizon_guide}

良いタイトルの条件:
- **先頭の 12 字以内に、人が検索窓に打ち込む語（keyword）を置く**。制度名・商品名・お金の言葉など、
  そのままの表記で（例:「生涯賃金」「新NISA」「年収の壁」）。keyword にはその語だけを入れる
- 40字以内。スマホで切れずに読めるのは冒頭28字程度なので、前半に要点を置く
- 「知らないと損」「ヤバい」など煽り語を使わない。内容と一致させる
- 数字か固有名詞を1つ入れる
- サムネの文言と同じ言葉を繰り返さない（同じ情報を2回見せると密度が下がる）
- 疑問形か、意外性のある事実の提示のどちらか

サムネ文言の条件:
- main は最大16字。遠目で読める短さ。数字があれば入れる（数字は赤で大きく出る）。
  2〜3 行に分けて出すので、行の切れ目に「／」を入れる（1 行 8 字まで。例:「会社員の一生／給料は2億円／ない」）
- sub は最大12字。main を補う一言（上の赤い帯に出る）。無理なら空文字
- bubble は聞き役（ずんだもん）の吹き出しのひと言。8字まで。視聴者の本音を代わりに言う（例:「足りるのだ？」「逆なのだ！」）
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
        ) + (_HOOK_TAG_GUIDE.format(**domain.hook_examples(cfg)) if cfg.get("upload.title_hook_tag", True) else ""),
        user,
        _TITLE_SCHEMA,
        model=cfg.get("script.model", llm.DEFAULT_MODEL),
        effort="medium",
    )
    body = keyword_first(data.get("title") or script.topic_title, data.get("keyword") or "")
    title = format_title(cfg, body, data.get("hook_tag") or "")
    thumb = {
        "main": (data.get("thumbnail_main") or "")[:20],
        "sub": (data.get("thumbnail_sub") or "")[:16],
        "bubble": (data.get("thumbnail_bubble") or "")[:8],
    }
    log.info("タイトル決定: %s", title)
    return title, thumb


def keyword_first(title: str, keyword: str, within: int = 14) -> str:
    """検索される語がタイトルの頭に無ければ「語｜タイトル」にする（一覧でも検索でも最初に目に入る位置）."""
    title, keyword = title.strip(), keyword.strip().strip("【】「」")
    if not keyword or keyword in title[:within]:
        return title
    return f"{keyword}｜{title}"


def format_title(cfg: Config, body: str, hook_tag: str = "") -> str:
    """タイトルの型: 本題【引き】【ずんだもん&めたん解説】。全体が MAX_TITLE を超えるなら本題を詰める.

    検索と一覧で最初に読まれる頭は本題（検索される語）に空け、引きの【】は本題の後ろに置く
    （upload.title_hook_position: front で以前の【引き】本題 に戻せる）。
    """
    suffix = str(cfg.get("upload.title_suffix", "") or "").strip()
    body = body.strip().strip("【】")
    tag = hook_tag.strip().strip("【】")[:12]
    hook = f"【{tag}】" if tag and cfg.get("upload.title_hook_tag", True) else ""
    room = MAX_TITLE - len(hook) - len(suffix)
    if len(body) > room:
        body = body[: max(room - 1, 8)].rstrip("、。 ") + "…"
    if str(cfg.get("upload.title_hook_position", "back")) == "front":
        return f"{hook}{body}{suffix}"[:MAX_TITLE]
    return f"{body}{hook}{suffix}"[:MAX_TITLE]


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
    # 掛け合いなら出演者全員の話者を並べる（VOICEVOX の規約はキャラクターごとの表記）
    ids: list[int] = []
    for c in (cfg.get("cast.characters", []) or []) if str(cfg.get("cast.mode", "solo")) == "dialogue" else []:
        if c.get("voicevox_speaker") is not None:
            ids.append(int(c["voicevox_speaker"]))
    if not ids:
        ids = [int(cfg.get("tts.voicevox.speaker", 3))]
    names = []
    for speaker_id in ids:
        name = _VOICEVOX_SPEAKERS.get(speaker_id, f"話者ID {speaker_id}")
        if name not in names:
            names.append(name)
    return (
        "この動画のナレーションは音声合成ソフト VOICEVOX を使用しています。\n"
        + "\n".join(f"VOICEVOX：{n}" for n in names) + "\n"
        "https://voicevox.hiroshiba.jp/"
    )


def subscribe_line(cfg: Config) -> str:
    """概要欄の登録リンク（channel.id があるときだけ）。sub_confirmation=1 で押すと登録の確認が出る."""
    cid = str(cfg.get("channel.id", "") or "").strip()
    if not cid:
        return ""
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    return (f"▶ チャンネル登録（毎日{times[0] if times else ''}に新しい動画）\n"
            f"https://www.youtube.com/channel/{cid}?sub_confirmation=1")


def build(cfg: Config, script: VideoScript, track: VoiceTrack,
          title: str | None = None) -> Metadata:
    title = title or (script.title_candidates or [script.topic_title])[0]

    parts = [script.description.strip(), subscribe_line(cfg)]

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
        + domain.disclaimer(cfg).rstrip() + "\n"
        "内容には万全を期していますが、誤りにお気づきの際はコメントで\n"
        "ご指摘いただけると助かります。"
    )

    parts.append("■ 音声\n" + _voice_credit(cfg))

    # 立ち絵・BGM などのクレジット（配布元の規約で表記が要るものは必ずここに書く）
    from .character import detect_credits
    credits = detect_credits(cfg) + [str(cfg.get("render.bgm.credit", "") or "").strip()]
    if str(cfg.get("thumbnail.style", "")) == "panel":
        credits.append(str(cfg.get("thumbnail.credit", "") or "").strip())
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
