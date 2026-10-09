"""切り抜き元の動画を集める（新しい配信は RSS、アーカイブは人気順の一覧）."""

from __future__ import annotations

import datetime as dt
import json
import logging
import subprocess
import xml.etree.ElementTree as ET
from typing import Any

import requests

from . import ytdlp

log = logging.getLogger(__name__)

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}


def rss(channel_id: str) -> list[dict[str, Any]]:
    """チャンネルの最新 15 本（配信のアーカイブも含む）."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall("a:entry", NS):
        out.append({
            "id": e.findtext("yt:videoId", namespaces=NS),
            "title": e.findtext("a:title", namespaces=NS) or "",
            "published": dt.datetime.fromisoformat(e.findtext("a:published", namespaces=NS)),
            "url": e.find("a:link", NS).attrib["href"],
        })
    return out


def is_short_url(item: dict[str, Any]) -> bool:
    return "/shorts/" in item.get("url", "")


def fresh_videos(src: dict[str, Any], exclude_words: list[str]) -> list[dict[str, Any]]:
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for v in rss(src["id"]):
        if is_short_url(v):
            continue          # Shorts は切り抜き対象にしない（尺が短すぎる・むらいのショートは対象外）
        if any(w in v["title"] for w in exclude_words):
            continue
        if (now - v["published"]).days > int(src.get("max_age_days", 4)):
            continue
        out.append({**v, "source_name": src["name"], "per_video": int(src.get("per_video", 3)), "kind": "fresh"})
    return out


def archive_pool(handle: str, size: int) -> list[dict[str, Any]]:
    """本チャンネルの通常動画を再生数順に並べて上から size 本（一覧の取得に 1〜3 分かかるので週 1 回だけ作り直す）."""
    cmd = [*ytdlp.base(), "--flat-playlist", "-J", f"https://www.youtube.com/{handle}/videos"]
    data = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    items = [e for e in data.get("entries", []) if e.get("id")]
    items.sort(key=lambda e: -(e.get("view_count") or 0))
    return [{"id": e["id"], "title": e.get("title", ""), "views": e.get("view_count") or 0,
             "url": f"https://www.youtube.com/watch?v={e['id']}"} for e in items[:size]]
