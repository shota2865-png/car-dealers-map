"""公開した動画（本編・Shorts）に、動画の要約を問いかけの形で 2 行のコメントとして付ける.

リンクは貼らない（視聴者がコピペできないので役に立たない）。コメント欄の最初の一言として、
「自分ならどうだろう」と答えたくなる問い（オープンクエスチョン）を置き、コメントを呼び込む。

  post_summaries(cfg, store, records)   公開済みでまだ付いていない動画にだけ付ける（何度呼んでも二重にならない）
  remove_link_comments(cfg)             以前の「▶ 本編はこちら + URL」のコメントを消す
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable

from . import domain, llm, youtube
from .config import Config

log = logging.getLogger(__name__)

_SYSTEM = """あなたは YouTube チャンネル（{field}）の運営者です。公開した動画のコメント欄の最初に、運営者として 2 行のコメントを書きます。

- 1 行目: この動画の要点を、問いかけの形で 1 文にまとめる（例:「給料が上がっても楽にならないのは、なぜだと思いますか？」）
- 2 行目: 見た人が自分のことを答えたくなるオープンクエスチョン（はい・いいえで終わらない。例:「あなたが最近『高くなったな』と感じたものは何ですか？」）
- 各行 45 字以内。です・ます調。小学 5 年生にも分かる言葉
- URL・ハッシュタグ・絵文字・「本編はこちら」は入れない。煽らない
- 2 行だけを返す（前置きや番号は付けない）"""


def summary_text(cfg: Config, title: str, description: str = "") -> str:
    user = f"タイトル: {title}\n\n概要欄:\n{(description or '')[:1200]}"
    text = llm.complete_text(_SYSTEM.format(field=domain.field(cfg)), user,
                             model=str(cfg.get("comments.model", cfg.get("shorts.model", llm.DEFAULT_MODEL))), effort="low")
    lines = [ln.strip().lstrip("・-0123456789.、） ") for ln in text.strip().splitlines() if ln.strip()]
    lines = [ln for ln in lines if "http" not in ln and "#" not in ln]
    return "\n".join(lines[:2])


def _own_channel_id(cfg: Config) -> str:
    return youtube.build_service(cfg).channels().list(part="id", mine=True).execute()["items"][0]["id"]


def _is_link_comment(text: str) -> bool:
    return "youtu.be/" in text or "本編" in text and "はこちら" in text


def own_comments(cfg: Config, video_id: str, channel_id: str) -> list[dict[str, Any]]:
    """その動画に付いている、運営者自身のトップレベルコメント."""
    try:
        res = youtube.build_service(cfg).commentThreads().list(part="snippet", videoId=video_id, maxResults=50).execute()
    except Exception as exc:                          # コメントが無効な動画など
        log.warning("コメントを読めませんでした（%s）: %s", video_id, str(exc)[:160])
        return []
    out = []
    for th in res.get("items", []):
        top = th["snippet"]["topLevelComment"]
        sn = top["snippet"]
        if (sn.get("authorChannelId") or {}).get("value") == channel_id:
            out.append({"id": top["id"], "text": sn.get("textOriginal") or sn.get("textDisplay") or ""})
    return out


def delete_comment(cfg: Config, comment_id: str) -> bool:
    try:
        youtube.build_service(cfg).comments().delete(id=comment_id).execute()
        return True
    except Exception as exc:
        log.warning("コメントを消せませんでした（%s）: %s", comment_id, str(exc)[:160])
        return False


def ensure_summary(cfg: Config, store, video_id: str, title: str, description: str, channel_id: str) -> str | None:
    """リンクのコメントがあれば消し、要約のコメントが無ければ付ける。付けた（または既にある）コメント ID を返す."""
    mine = own_comments(cfg, video_id, channel_id)
    for c in mine:
        if _is_link_comment(c["text"]):
            delete_comment(cfg, c["id"])
    keep = [c for c in mine if not _is_link_comment(c["text"])]
    if keep:
        return keep[0]["id"]
    text = summary_text(cfg, title, description)
    if not text:
        return None
    return youtube.post_comment(cfg, store, video_id, text)


def post_summaries(cfg: Config, store, records: Iterable, now: dt.datetime | None = None) -> int:
    """公開済みで、まだ要約のコメントが無い動画に付ける（store の stage.summary_comment_id で覚える）."""
    now = now or dt.datetime.now(dt.timezone.utc)
    todo = []
    for r in records:
        st = r.stage or {}
        if not r.youtube_id or st.get("summary_comment_id"):
            continue
        if r.publish_at:
            try:
                if dt.datetime.fromisoformat(r.publish_at.replace("Z", "+00:00")) > now:
                    continue
            except ValueError:
                pass
        todo.append(r)
    if not todo:
        return 0
    yt = youtube.build_service(cfg)
    snip: dict[str, dict] = {}
    ids = [r.youtube_id for r in todo]
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,status", id=",".join(ids[i:i + 50])).execute().get("items", []):
            if v["status"].get("privacyStatus") == "public":
                snip[v["id"]] = v["snippet"]
    channel_id = _own_channel_id(cfg)
    done = 0
    for r in todo:
        sn = snip.get(r.youtube_id)
        if not sn:                                    # まだ公開されていない（予約中）か、消された
            continue
        cid = ensure_summary(cfg, store, r.youtube_id, sn.get("title", ""), sn.get("description", ""), channel_id)
        if cid:
            store.update_video(r.slug, stage={"summary_comment_id": cid})
            done += 1
    if done:
        log.info("%d 本の動画に要約のコメントを付けました", done)
    return done
