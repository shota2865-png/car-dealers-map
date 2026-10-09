"""YouTube に予約投稿する（refresh token → アクセストークン → resumable upload）."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


def access_token(prefix: str) -> str:
    cid = os.environ.get(f"{prefix}YOUTUBE_CLIENT_ID")
    sec = os.environ.get(f"{prefix}YOUTUBE_CLIENT_SECRET")
    ref = os.environ.get(f"{prefix}YOUTUBE_REFRESH_TOKEN")
    if not (cid and sec and ref):
        raise RuntimeError(f"{prefix}YOUTUBE_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN が設定されていません")
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": cid, "client_secret": sec, "refresh_token": ref, "grant_type": "refresh_token"})
    r.raise_for_status()
    return r.json()["access_token"]


def my_channel_id(token: str) -> str:
    r = requests.get("https://www.googleapis.com/youtube/v3/channels", timeout=30,
                     params={"part": "id", "mine": "true"}, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    items = r.json().get("items", [])
    return items[0]["id"] if items else ""


def upload(token: str, path: Path, meta: dict[str, Any], publish_at: dt.datetime | None) -> str:
    status: dict[str, Any] = {"selfDeclaredMadeForKids": meta.get("made_for_kids", False)}
    if publish_at and publish_at > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15):
        status["privacyStatus"] = "private"        # 予約は private + publishAt でないと受け付けられない
        status["publishAt"] = publish_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        status["privacyStatus"] = meta.get("privacy_after_publish", "public")
    body = {"snippet": {"title": meta["title"][:100], "description": meta["description"][:4900],
                        "tags": meta.get("tags", []), "categoryId": meta.get("category_id", "22"),
                        "defaultLanguage": "ja", "defaultAudioLanguage": "ja"},
            "status": status}
    size = path.stat().st_size
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        timeout=60, data=json.dumps(body),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    if init.status_code != 200:
        raise RuntimeError(f"投稿の準備に失敗: {init.status_code} {init.text[:400]}")
    url = init.headers["Location"]
    for attempt in range(5):
        with path.open("rb") as fh:
            r = requests.put(url, data=fh, timeout=600,
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"})
        if r.status_code in (200, 201):
            return r.json()["id"]
        if r.status_code in (500, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        raise RuntimeError(f"投稿に失敗: {r.status_code} {r.text[:400]}")
    raise RuntimeError("投稿に失敗（再試行しても通らない）")
