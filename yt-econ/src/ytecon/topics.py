"""話題づくり.

RSS から経済ニュースを集め、過去に扱ったテーマと重複するものを外し、
「高校生〜20代に刺さるか」を Claude に採点させて当日分を選ぶ。

ニュースが取れない日でも止まらないよう、常設テーマ（evergreen）の種を
必ず混ぜる。1日2本なら「ニュース1本 + 常設1本」が回しやすい。
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from . import llm
from .config import Config
from .state import Store

log = logging.getLogger(__name__)


@dataclass
class Topic:
    title: str                      # 動画で扱うテーマ（タイトル案ではない）
    angle: str                      # 切り口。ここが被ると動画も被る
    kind: str                       # news | evergreen
    why_now: str = ""
    audience_hook: str = ""         # なぜ視聴者が自分ごと化できるか
    key_questions: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    score: float = 0.0
    id: int | None = None


# ----------------------------------------------------------------------
# RSS 収集
# ----------------------------------------------------------------------
def fetch_rss(cfg: Config) -> list[dict[str, str]]:
    """設定された RSS を巡回して記事見出しを集める."""
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser 未導入のため RSS 収集をスキップします")
        return []

    limit = int(cfg.get("topics.fetch_limit", 60))
    items: list[dict[str, str]] = []
    for src in cfg.get("topics.rss_sources", []) or []:
        name, url = src.get("name", "?"), src.get("url")
        if not url:
            continue
        try:
            feed = feedparser.parse(url)
        except Exception as exc:  # ネットワーク断でも全体は止めない
            log.warning("RSS取得失敗 %s: %s", name, exc)
            continue
        if getattr(feed, "bozo", False) and not feed.entries:
            log.warning("RSS解析失敗 %s", name)
            continue
        for entry in feed.entries[:20]:
            items.append(
                {
                    "source": name,
                    "title": _clean(getattr(entry, "title", "")),
                    "summary": _clean(getattr(entry, "summary", ""))[:300],
                    "url": getattr(entry, "link", ""),
                    "published": str(getattr(entry, "published", "")),
                }
            )
    log.info("RSS %d件を収集", len(items))
    return items[:limit]


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return unicodedata.normalize("NFKC", text).strip()


# ----------------------------------------------------------------------
# 重複排除
# ----------------------------------------------------------------------
def _normalize(title: str) -> str:
    t = unicodedata.normalize("NFKC", title).lower()
    return re.sub(r"[\s　、。・:：\-—\[\]「」『』（）()！!？?]", "", t)


def is_duplicate(title: str, history: list[str], threshold: float) -> bool:
    """過去テーマとの表記ゆれを吸収した類似判定."""
    norm = _normalize(title)
    if not norm:
        return True
    for past in history:
        if SequenceMatcher(None, norm, _normalize(past)).ratio() >= threshold:
            return True
    return False


# ----------------------------------------------------------------------
# Claude による選定
# ----------------------------------------------------------------------
_SELECT_SYSTEM = """あなたは日本語の経済解説YouTubeチャンネルの企画担当ディレクターです。
視聴者は{audience}。彼らは経済の予備知識が乏しく、しかし「自分の生活や給料に
どう効くのか」には強い関心があります。

企画を選ぶときの基準（この順に重い）:
1. 自分ごと化できるか — 視聴者の財布・就職・進路に接続できる話題か
2. 8〜10分で説明しきれるか — 論点が3〜5個に収まるか
3. 一次情報があるか — 公的統計や公式発表で数字を裏づけられるか
4. 誤解が多いか — 「実は違う」を提示できるテーマは強い
5. 賞味期限 — ニュースものは1週間以内に公開して価値がある内容か

扱ってはいけない話題:
{banned}

出力の注意:
- title は「動画で扱うテーマ」。釣りタイトルではなく内容の要約にする
- angle は他の動画と被らない切り口。ここが企画の本体
- 数字を断定せず、確認すべき一次情報を sources に入れる
"""

_SELECT_SCHEMA = llm.obj(
    {
        "topics": llm.arr(
            llm.obj(
                {
                    "title": llm.STR,
                    "angle": llm.STR,
                    "kind": {"type": "string", "enum": ["news", "evergreen"]},
                    "why_now": llm.STR,
                    "audience_hook": llm.STR,
                    "key_questions": llm.arr(llm.STR),
                    "sources": llm.arr(llm.obj({"name": llm.STR, "url": llm.STR})),
                    "score": llm.NUM,
                    "reject_reason": llm.STR,
                }
            )
        )
    }
)


def select_topics(cfg: Config, store: Store, count: int) -> list[Topic]:
    """当日分の話題を選んで DB に登録して返す."""
    history = store.recent_topic_titles(int(cfg.get("topics.dedupe_window_days", 120)))
    threshold = float(cfg.get("topics.dedupe_threshold", 0.72))

    news = fetch_rss(cfg)
    seeds = [
        s for s in (cfg.get("topics.evergreen_seeds", []) or [])
        if not is_duplicate(s, history, threshold)
    ]

    mix = cfg.get("topics.mix", {"news": 1, "evergreen": 1}) or {}
    ratio_news = int(mix.get("news", 1))
    ratio_ever = int(mix.get("evergreen", 1))
    total_ratio = max(ratio_news + ratio_ever, 1)
    want_news = max(0, round(count * ratio_news / total_ratio)) if news else 0
    want_ever = count - want_news
    if not seeds:
        want_news, want_ever = count, 0

    system = _SELECT_SYSTEM.format(
        audience=cfg.get("channel.audience", "20代の社会人"),
        banned="\n".join(f"- {b}" for b in cfg.get("channel.banned_topics", []) or []),
    )

    news_block = "\n".join(
        f"- [{i['source']}] {i['title']} ({i['url']})\n  {i['summary']}" for i in news
    ) or "(今日はニュースを取得できませんでした)"
    seeds_block = "\n".join(f"- {s}" for s in seeds) or "(常設テーマの在庫なし)"
    history_block = "\n".join(f"- {h}" for h in history[-60:]) or "(まだ投稿履歴なし)"

    user = f"""今日は {dt.date.today().isoformat()} です。

# 収集した経済ニュース見出し
{news_block}

# 常設テーマの種（ニュース性はないが需要が安定している）
{seeds_block}

# 過去に扱ったテーマ（これらと内容が重なる企画は出さないでください）
{history_block}

# 依頼
ニュース由来を {want_news} 本、常設テーマ由来を {want_ever} 本、
合計 {count} 本ぶんの企画を作ってください。
各企画に 0〜100 の score（この視聴者層への刺さり具合）を付けてください。
採用しなかった候補は含めず、採用する {count} 本だけを返してください。
reject_reason は採用企画では空文字で構いません。"""

    data = llm.complete_json(
        system,
        user,
        _SELECT_SCHEMA,
        model=cfg.get("topics.model", llm.DEFAULT_MODEL),
        effort=cfg.get("topics.effort", "medium"),
    )

    chosen: list[Topic] = []
    for item in data.get("topics", []):
        title = (item.get("title") or "").strip()
        if not title or is_duplicate(title, history, threshold):
            log.info("重複のためスキップ: %s", title)
            continue
        topic = Topic(
            title=title,
            angle=(item.get("angle") or "").strip(),
            kind=item.get("kind", "evergreen"),
            why_now=item.get("why_now", ""),
            audience_hook=item.get("audience_hook", ""),
            key_questions=item.get("key_questions", []) or [],
            sources=item.get("sources", []) or [],
            score=float(item.get("score", 0) or 0),
        )
        topic.id = store.add_topic(
            topic.title, topic.angle, topic.kind, topic.sources, topic.score
        )
        history.append(topic.title)  # 同一バッチ内の共食いも防ぐ
        chosen.append(topic)
        if len(chosen) >= count:
            break

    if not chosen:
        raise RuntimeError(
            "採用できる話題がありませんでした。"
            "config の evergreen_seeds を増やすか dedupe_window_days を短くしてください。"
        )
    log.info("話題を %d 件選定: %s", len(chosen), [t.title for t in chosen])
    return chosen
