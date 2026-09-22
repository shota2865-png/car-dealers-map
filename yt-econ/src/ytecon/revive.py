"""寝かせた動画の掘り起こし.

海外先行の話題（horizon が stock / bridge）は、公開直後には数字が出ない。
価値が出るのは**日本でその話題が立ち上がった瞬間**で、そのとき
「すでに解説がある1本」として検索で掘り起こされる。

このモジュールは、企画時に決めておいた watch_keywords が
日本のニュース見出しに現れたかを毎日照合し、現れたら
  - タイトルとサムネの差し替え案
  - 概要欄への追記文
  - 続編1本の企画
を作る。--apply を付ければ YouTube 側のタイトル/概要欄まで更新する。

時差のある市場では、公開が「終わり」ではなく「仕込み」になる。
その仕込みを回収するのがここ。
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .config import Config
from .state import Store
from .topics import fetch_rss

log = logging.getLogger(__name__)


@dataclass
class Hit:
    video_id: int
    slug: str
    title: str
    youtube_id: str
    keyword: str
    headline: str
    source: str
    url: str
    months_asleep: float
    plan: dict[str, Any] = field(default_factory=dict)
    revival_id: int | None = None


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def scan(cfg: Config, store: Store) -> list[Hit]:
    """監視中の動画のキーワードが、今日の見出しに出ていないか照合する."""
    watchlist = store.watchlist()
    if not watchlist:
        log.info("掘り起こし待ちの動画はまだありません")
        return []

    headlines = fetch_rss(cfg)
    if not headlines:
        log.warning("ニュースを取得できなかったので照合をスキップします")
        return []

    import time

    cooldown = float(cfg.get("revive.cooldown_days", 45))

    hits: list[Hit] = []
    for item in watchlist:
        # 1本の動画に監視語は複数ある。同じニュースに別の語が反応して
        # 何度も鳴るのを防ぐため、動画単位でクールダウンを掛ける
        if store.revived_recently(item["video_id"], cooldown):
            continue
        for keyword in item["keywords"]:
            needle = _norm(keyword)
            if not needle:
                continue
            match = next(
                (h for h in headlines
                 if needle in _norm(h["title"]) or needle in _norm(h["summary"])),
                None,
            )
            if not match:
                continue
            if store.revival_seen(item["video_id"], keyword):
                continue     # 同じ語で二度騒がない
            months = (time.time() - float(item["published_at"])) / (86400 * 30.4)
            hits.append(Hit(
                video_id=item["video_id"],
                slug=item["slug"],
                title=item["title"],
                youtube_id=item["youtube_id"],
                keyword=keyword,
                headline=match["title"],
                source=match["source"],
                url=match["url"],
                months_asleep=months,
            ))
            break            # 1本につき1件で十分

    log.info("掘り起こし候補 %d 件（監視中 %d 本）", len(hits), len(watchlist))
    return hits


# ----------------------------------------------------------------------
_PLAN_SCHEMA = llm.obj(
    {
        "new_title": llm.STR,
        "thumbnail_main": llm.STR,
        "thumbnail_sub": llm.STR,
        "description_append": llm.STR,
        "pinned_comment": llm.STR,
        "followup_title": llm.STR,
        "followup_angle": llm.STR,
        "urgency": {"type": "string", "enum": ["now", "this_week", "watch"]},
        "reason": llm.STR,
    }
)

_PLAN_SYSTEM = """あなたは日本語YouTubeチャンネルの運用担当です。

状況: このチャンネルは海外で先行している話題を、日本で一般化する前に
先回りして解説しています。数ヶ月前に公開した動画のテーマが、
いま日本のニュースに出始めました。**仕込みが回収できる瞬間**です。

やること: その旧作を「今まさに探されている1本」に作り替える案を出す。

タイトルの考え方:
- 公開当時は「まだ知られていない話」なので説明的なタイトルだったはず。
  いま必要なのは**いま検索されている語**が入ったタイトル
- ニュース見出しに出ている語をそのまま入れる。検索はその語で来る
- 「解説」「わかりやすく」など、後追いで探す人が付ける語を1つ入れる
- 40字以内。煽らない
- 古い動画だと分かる表記（「再掲」など）は入れない。内容が古びていないなら不要

description_append は概要欄の**冒頭に足す**2〜3行。
「このニュースの背景を、話題になる前に解説した回です」という趣旨を、
嫌味なく書く。日付を入れて信頼性を出す。

pinned_comment は固定コメント1つ。視聴者に今何が起きたかを1〜2文で伝える。

followup は続編1本の企画。旧作が「背景」、続編が「今どうなったか」になる
組み合わせにする。旧作へ内部リンクを張る前提で考える。

urgency:
  now       = 今日中に差し替えるべき（大きく報じられている）
  this_week = 今週中でよい
  watch     = まだ早い。様子見（誤検知に近い場合もここ）
"""


def make_plan(cfg: Config, hit: Hit) -> dict[str, Any]:
    user = f"""# 旧作
タイトル: {hit.title}
公開からの経過: 約{hit.months_asleep:.1f}ヶ月
URL: https://youtu.be/{hit.youtube_id}

# 反応した監視キーワード
{hit.keyword}

# 今日の日本のニュース見出し
[{hit.source}] {hit.headline}
{hit.url}

この旧作を、いま探している人に届く形に作り替える案を出してください。"""
    return llm.complete_json(
        _PLAN_SYSTEM, user, _PLAN_SCHEMA,
        model=cfg.get("topics.model", llm.DEFAULT_MODEL), effort="medium",
    )


def run(cfg: Config, store: Store, apply: bool = False) -> list[Hit]:
    """照合 → 案の生成 →（任意で）YouTube への反映."""
    hits = scan(cfg, store)
    if not hits:
        return []

    for hit in hits:
        hit.plan = make_plan(cfg, hit)
        hit.revival_id = store.add_revival(
            hit.video_id, hit.keyword, hit.headline, hit.url, hit.plan
        )
        log.info("[%s] %s → 「%s」(%s)", hit.plan.get("urgency", "?"),
                 hit.title, hit.plan.get("new_title", ""), hit.keyword)

        if apply and hit.plan.get("urgency") in ("now", "this_week"):
            _apply(cfg, store, hit)

    return hits


def _apply(cfg: Config, store: Store, hit: Hit) -> None:
    from . import youtube

    plan = hit.plan
    try:
        youtube.update_video_metadata(
            cfg, store, hit.youtube_id,
            title=plan.get("new_title") or None,
            description_prefix=plan.get("description_append") or None,
        )
    except Exception as exc:
        log.warning("タイトル/概要欄の更新に失敗: %s", exc)
        return

    # サムネも作り直す（台本が残っていれば）
    script_path = cfg.workdir / hit.slug / "script.json"
    if script_path.exists() and plan.get("thumbnail_main"):
        try:
            from . import thumbnail
            from .script import VideoScript

            script = VideoScript.load(script_path)
            script.thumbnail_copy = {
                "main": plan.get("thumbnail_main", ""),
                "sub": plan.get("thumbnail_sub", ""),
            }
            out = cfg.workdir / hit.slug / "thumbnail_revived.jpg"
            thumbnail.build(cfg, script, out)
            youtube.set_thumbnail(cfg, store, hit.youtube_id, out)
        except Exception as exc:
            log.warning("サムネイルの差し替えに失敗: %s", exc)

    if hit.revival_id:
        store.mark_revival_applied(hit.revival_id, hit.video_id)
    log.info("反映しました: https://youtu.be/%s", hit.youtube_id)


def format_report(hits: list[Hit]) -> str:
    """人が読むための要約."""
    if not hits:
        return "掘り起こす動画はありませんでした。"
    order = {"now": 0, "this_week": 1, "watch": 2}
    rows = ["== 掘り起こし候補 =="]
    for hit in sorted(hits, key=lambda h: order.get(h.plan.get("urgency", "watch"), 9)):
        plan = hit.plan
        label = {"now": "今日やる", "this_week": "今週中", "watch": "様子見"}.get(
            plan.get("urgency", "watch"), "?")
        rows += [
            "",
            f"[{label}] {hit.title}",
            f"  https://youtu.be/{hit.youtube_id}  （{hit.months_asleep:.1f}ヶ月前）",
            f"  反応: 「{hit.keyword}」 ← {hit.source}「{hit.headline}」",
            f"  新タイトル案: {plan.get('new_title','')}",
            f"  サムネ案    : {plan.get('thumbnail_main','')} / {plan.get('thumbnail_sub','')}",
            f"  続編企画    : {plan.get('followup_title','')}",
            f"                {plan.get('followup_angle','')}",
        ]
    return "\n".join(rows)
