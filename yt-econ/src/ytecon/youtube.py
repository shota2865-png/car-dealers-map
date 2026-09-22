"""YouTube Data API v3 へのアップロード.

クォータの現実:
  1日の既定上限 = 10,000ユニット / 動画1本のアップロード = 1,600ユニット
  → 1日6本が物理的な上限。2本/日なら 3,200 + サムネ(50) + 字幕(50) 程度で
    十分収まる。それでも事故防止のため送信前に残量を自前で数えている。

認証は「一度ブラウザで許可 → refresh token を保存」方式。
CI から動かす場合は YOUTUBE_REFRESH_TOKEN を Secret に入れるだけで済む。
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import time
from pathlib import Path
from typing import Any

from .config import Config
from .metadata import Metadata
from .state import Store

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"

QUOTA_LIMIT = 10_000
COST_UPLOAD = 1_600
COST_THUMBNAIL = 50
COST_CAPTION = 400
COST_PLAYLIST = 50
COST_WRITE = 50


class UploadError(RuntimeError):
    pass


class QuotaExceeded(UploadError):
    pass


def _credentials(cfg: Config):
    from google.oauth2.credentials import Credentials

    client_id = cfg.env("YOUTUBE_CLIENT_ID")
    client_secret = cfg.env("YOUTUBE_CLIENT_SECRET")
    refresh_token = cfg.env("YOUTUBE_REFRESH_TOKEN")
    missing = [
        name
        for name, val in [
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        ]
        if not val
    ]
    if missing:
        raise UploadError(
            f"{', '.join(missing)} が未設定です。\n"
            "  python scripts/auth_youtube.py\n"
            "を一度実行して refresh token を取得してください。"
        )
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )


def build_service(cfg: Config):
    from googleapiclient.discovery import build as gbuild

    return gbuild("youtube", "v3", credentials=_credentials(cfg),
                  cache_discovery=False)


# ----------------------------------------------------------------------
class QuotaGuard:
    """送信前にその日の消費見込みを確認する."""

    def __init__(self, store: Store):
        self.store = store

    @property
    def today(self) -> str:
        return dt.date.today().isoformat()

    def check(self, cost: int) -> None:
        used = self.store.quota_used(self.today)
        if used + cost > QUOTA_LIMIT:
            raise QuotaExceeded(
                f"本日の YouTube API クォータが足りません "
                f"(使用 {used} + 必要 {cost} > 上限 {QUOTA_LIMIT})。"
                "明日の午前0時(太平洋時間)にリセットされます。"
            )

    def spend(self, cost: int) -> None:
        total = self.store.add_quota(self.today, cost)
        log.debug("クォータ消費 +%d (本日計 %d/%d)", cost, total, QUOTA_LIMIT)


# ----------------------------------------------------------------------
def next_publish_time(cfg: Config, slot_index: int,
                      base: dt.datetime | None = None,
                      times: list[str] | None = None) -> dt.datetime:
    """config の publish_times_jst から次の投稿時刻(UTC)を決める.

    times を渡すとその時刻表（Shorts 用など）を使う。
    """
    jst = dt.timezone(dt.timedelta(hours=9))
    now = (base or dt.datetime.now(jst)).astimezone(jst)
    times = times or cfg.get("upload.publish_times_jst", ["07:30", "19:30"]) or ["07:30"]
    slots: list[dt.datetime] = []
    for day_offset in (0, 1):
        day = now.date() + dt.timedelta(days=day_offset)
        for t in times:
            hh, mm = (int(x) for x in str(t).split(":"))
            slots.append(dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=jst))
    future = [s for s in slots if s > now + dt.timedelta(minutes=10)]
    future.sort()
    chosen = future[min(slot_index, len(future) - 1)]
    return chosen.astimezone(dt.timezone.utc)


# ----------------------------------------------------------------------
def upload_video(
    cfg: Config,
    store: Store,
    video_path: str | Path,
    meta: Metadata,
    *,
    publish_at: dt.datetime | None = None,
) -> str:
    """動画をアップロードして videoId を返す."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    guard = QuotaGuard(store)
    guard.check(COST_UPLOAD)

    privacy = str(cfg.get("upload.privacy", "public"))
    status: dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": bool(cfg.get("upload.made_for_kids", False)),
    }
    if publish_at is not None and cfg.get("upload.schedule", True):
        # 予約投稿は private + publishAt の組み合わせでないと受け付けられない
        status["privacyStatus"] = "private"
        # タイムゾーン付きの datetime（JST など）を渡されても UTC に直してから "Z" を付ける
        if publish_at.tzinfo is not None:
            publish_at = publish_at.astimezone(dt.timezone.utc)
        status["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    body = {
        "snippet": {
            "title": meta.title[:100],
            "description": meta.description[:5000],
            "tags": meta.tags,
            "categoryId": meta.category_id,
            "defaultLanguage": meta.language,
            "defaultAudioLanguage": meta.language,
        },
        "status": status,
    }

    service = build_service(cfg)
    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    retry = 0
    while response is None:
        try:
            progress, response = request.next_chunk()
            if progress:
                log.info("アップロード %d%%", int(progress.progress() * 100))
        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504) and retry < 5:
                delay = 2 ** retry + random.random()
                log.warning("一時エラー %s。%.1f秒後に再試行", exc.resp.status, delay)
                time.sleep(delay)
                retry += 1
                continue
            raise UploadError(f"アップロードに失敗しました: {exc}") from exc

    guard.spend(COST_UPLOAD)
    video_id = response["id"]
    log.info("アップロード完了: https://youtu.be/%s", video_id)
    return video_id


def set_thumbnail(cfg: Config, store: Store, video_id: str,
                  image_path: str | Path) -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    guard = QuotaGuard(store)
    guard.check(COST_THUMBNAIL)
    try:
        build_service(cfg).thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(image_path), mimetype="image/jpeg"),
        ).execute()
        guard.spend(COST_THUMBNAIL)
        log.info("サムネイルを設定しました")
    except HttpError as exc:
        # カスタムサムネは電話番号認証済みチャンネルのみ。未認証でも本編は残す
        log.warning("サムネイル設定に失敗（チャンネルの確認が未完了かもしれません）: %s", exc)


def upload_caption(cfg: Config, store: Store, video_id: str,
                   srt_path: str | Path) -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    guard = QuotaGuard(store)
    guard.check(COST_CAPTION)
    try:
        build_service(cfg).captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": "ja",
                              "name": "日本語", "isDraft": False}},
            media_body=MediaFileUpload(str(srt_path), mimetype="application/octet-stream"),
        ).execute()
        guard.spend(COST_CAPTION)
        log.info("字幕を登録しました")
    except HttpError as exc:
        log.warning("字幕の登録に失敗: %s", exc)


def update_video_metadata(
    cfg: Config,
    store: Store,
    video_id: str,
    *,
    title: str | None = None,
    description_prefix: str | None = None,
) -> None:
    """公開済み動画のタイトル・概要欄を書き換える（掘り起こし用）.

    snippet は部分更新ができず、送らなかったフィールドが消える。
    必ず現在の snippet を読んでから、変える部分だけ差し替えて送り返す。
    """
    from googleapiclient.errors import HttpError

    guard = QuotaGuard(store)
    guard.check(COST_WRITE + 1)
    service = build_service(cfg)

    res = service.videos().list(part="snippet", id=video_id).execute()
    items = res.get("items", [])
    if not items:
        raise UploadError(f"動画が見つかりません: {video_id}")
    snippet = items[0]["snippet"]

    if title:
        snippet["title"] = title[:100]
    if description_prefix:
        current = snippet.get("description", "")
        # 二重に足さないよう、既に同じ文が頭にあれば入れ替える
        if not current.startswith(description_prefix.strip()):
            snippet["description"] = (description_prefix.strip() + "\n\n" + current)[:5000]

    # categoryId は必須。読み出した値をそのまま返す
    body = {"id": video_id, "snippet": snippet}
    try:
        service.videos().update(part="snippet", body=body).execute()
        guard.spend(COST_WRITE + 1)
        log.info("メタデータを更新しました: %s", video_id)
    except HttpError as exc:
        raise UploadError(f"メタデータの更新に失敗しました: {exc}") from exc


def ensure_playlist(cfg: Config, store: Store, title: str) -> str | None:
    from googleapiclient.errors import HttpError

    if not title:
        return None
    service = build_service(cfg)
    try:
        res = service.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        for item in res.get("items", []):
            if item["snippet"]["title"] == title:
                return item["id"]
        guard = QuotaGuard(store)
        guard.check(COST_WRITE)
        created = service.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": title},
                  "status": {"privacyStatus": "public"}},
        ).execute()
        guard.spend(COST_WRITE)
        return created["id"]
    except HttpError as exc:
        log.warning("再生リストの準備に失敗: %s", exc)
        return None


def add_to_playlist(cfg: Config, store: Store, playlist_id: str,
                    video_id: str) -> None:
    from googleapiclient.errors import HttpError

    guard = QuotaGuard(store)
    guard.check(COST_PLAYLIST)
    try:
        build_service(cfg).playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video",
                                             "videoId": video_id}}},
        ).execute()
        guard.spend(COST_PLAYLIST)
    except HttpError as exc:
        log.warning("再生リストへの追加に失敗: %s", exc)


def publish(
    cfg: Config,
    store: Store,
    video_path: str | Path,
    meta: Metadata,
    thumbnail: str | Path | None = None,
    srt: str | Path | None = None,
    slot_index: int = 0,
    publish_times: list[str] | None = None,
    playlist: bool = True,
) -> dict[str, Any]:
    """アップロード一式（本編 → サムネ → 字幕 → 再生リスト）.

    publish_times を渡すと、その時刻表（Shorts 用）で予約する。
    """
    publish_at = (
        next_publish_time(cfg, slot_index, times=publish_times)
        if cfg.get("upload.schedule", True) else None
    )
    video_id = upload_video(cfg, store, video_path, meta, publish_at=publish_at)

    if thumbnail and Path(thumbnail).exists():
        set_thumbnail(cfg, store, video_id, thumbnail)
    if srt and Path(srt).exists():
        upload_caption(cfg, store, video_id, srt)

    playlist_title = cfg.get("upload.playlist_title", "") if playlist else ""
    if playlist_title:
        pid = ensure_playlist(cfg, store, playlist_title)
        if pid:
            add_to_playlist(cfg, store, pid, video_id)

    return {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "publish_at": publish_at.isoformat() if publish_at else None,
    }
