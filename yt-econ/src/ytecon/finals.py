"""完成品の命名と、手で仕上げた動画の投稿（yt_001_20260922 形式）.

完成した本編は `yt_<通し番号 3 桁>_<公開日 yyyymmdd>` の名前で残す（サムネも同じ名前）。
番号は 001 から昇順。DB に記録した番号と、finals/ にある手動ぶんの記録の大きいほうの次を使う。

手で仕上げた動画（CapCut でカットして再出力したものなど）は `ytecon publish-file` で投稿する。
メタデータは finals/<名前>.json（title / description / tags）にあればそれを使う。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from .config import Config
from .metadata import Metadata
from .state import Store

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^yt_(\d{3,})_(\d{8})$")
JST = dt.timezone(dt.timedelta(hours=9))


def name_for(number: int, day: dt.date) -> str:
    return f"yt_{number:03d}_{day:%Y%m%d}"


def parse_name(name: str) -> tuple[int, dt.date] | None:
    m = NAME_RE.match(Path(name).stem)
    if not m:
        return None
    try:
        return int(m.group(1)), dt.datetime.strptime(m.group(2), "%Y%m%d").date()
    except ValueError:
        return None


def finals_dir(cfg: Config) -> Path:
    """手で仕上げた動画の記録（json）を置く場所。リポジトリの finals/."""
    d = Path(str(cfg.get("upload.finals_dir", "finals") or "finals"))
    d = d if d.is_absolute() else cfg.root / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir(cfg: Config) -> Path:
    """完成した mp4 / jpg のコピー先（output/finals/）。git には入れない."""
    d = cfg.workdir / "finals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def used_numbers(cfg: Config, store: Store | None) -> set[int]:
    nums: set[int] = set()
    for p in list(finals_dir(cfg).glob("yt_*.json")) + list(output_dir(cfg).glob("yt_*.mp4")):
        got = parse_name(p.stem)
        if got:
            nums.add(got[0])
    if store is not None:
        from .state import STATUSES
        for r in store.videos_by_status(*STATUSES):          # 途中の回に付けた番号も飛ばさない
            fn = (r.stage or {}).get("final_name")
            got = parse_name(fn) if fn else None
            if got:
                nums.add(got[0])
    return nums


def next_number(cfg: Config, store: Store | None) -> int:
    used = used_numbers(cfg, store)
    return (max(used) + 1) if used else 1


def assign(cfg: Config, store: Store | None, day: dt.date) -> str:
    return name_for(next_number(cfg, store), day)


def keep(cfg: Config, name: str, video: Path, thumb: Path | None = None) -> dict[str, str]:
    """完成品を output/finals/<name>.mp4（.jpg）にコピーする."""
    d = output_dir(cfg)
    out = {"video": str(d / f"{name}.mp4")}
    shutil.copy2(video, d / f"{name}.mp4")
    if thumb and Path(thumb).exists():
        shutil.copy2(thumb, d / f"{name}.jpg")
        out["thumbnail"] = str(d / f"{name}.jpg")
    return out


# ----------------------------------------------------------------------
# 手で仕上げた動画の投稿
# ----------------------------------------------------------------------
def load_meta(cfg: Config, name: str) -> Metadata | None:
    p = finals_dir(cfg) / f"{name}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return Metadata(
        title=str(d.get("title", name))[:100],
        description=str(d.get("description", ""))[:5000],
        tags=[str(t) for t in (d.get("tags") or [])][:15],
        category_id=str(d.get("category_id") or cfg.get("upload.category_id", "25")),
        language=str(d.get("language") or cfg.get("upload.language", "ja")),
    )


def find_thumbnail(cfg: Config, name: str) -> Path | None:
    from .thumbnail import manual_dir
    for d in (manual_dir(cfg), finals_dir(cfg), output_dir(cfg)):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = d / f"{name}{ext}"
            if p.exists():
                return p
    return None


def fetch(source: str, dest: Path) -> Path:
    """URL ならダウンロード、ローカルならそのまま。Google Drive の共有リンクは gdown で落とす."""
    if "drive.google.com" in source or "docs.google.com" in source:
        import gdown                                 # 大きいファイルの「ウイルススキャンできません」確認を越えるため
        dest.parent.mkdir(parents=True, exist_ok=True)
        got = gdown.download(url=source, output=str(dest), quiet=False, fuzzy=True)
        if not got or not dest.exists() or dest.stat().st_size < 1024:
            raise RuntimeError("Google Drive から取得できませんでした。共有設定が「リンクを知っている全員」になっているか確認してください")
        return dest
    if re.match(r"^https?://", source):
        import requests
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(source, stream=True, timeout=600) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return dest
    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(source)
    return p


def publish_file(cfg: Config, store: Store, source: str, name: str, *, privacy: str = "private",
                 publish_at: dt.datetime | None = None, title: str | None = None,
                 thumbnail: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """完成ファイルを YouTube に上げる。privacy=private なら予約せず非公開のまま置く."""
    from . import youtube
    from .thumbnail import prepare

    parsed = parse_name(name)
    if not parsed:
        raise ValueError(f"名前は yt_001_20260922 の形にしてください: {name}")
    video = fetch(source, output_dir(cfg) / f"{name}.mp4")
    meta = load_meta(cfg, name) or Metadata(title=title or name, description="",
                                              category_id=str(cfg.get("upload.category_id", "25")),
                                              language=str(cfg.get("upload.language", "ja")))
    if title:
        meta.title = title[:100]
    thumb_src = Path(thumbnail) if thumbnail else find_thumbnail(cfg, name)
    thumb_path = None
    if thumb_src and thumb_src.exists():
        thumb_path = prepare(thumb_src, output_dir(cfg) / f"{name}.jpg")

    if dry_run:
        return {"name": name, "video": str(video), "title": meta.title, "thumbnail": str(thumb_path or ""),
                "privacy": privacy, "dry_run": True}

    slug = name
    if not store.get_video(slug):
        store.create_video(slug, None, meta.title)
    store.update_video(slug, stage={"final_name": name, "kind": "long", "manual": True, "privacy": privacy})

    # privacy が private/unlisted のときは予約しない（あとで ytecon visibility で公開する）
    raw_privacy = cfg.raw.setdefault("upload", {})
    saved = dict(raw_privacy)
    raw_privacy["privacy"] = privacy
    raw_privacy["schedule"] = bool(publish_at is not None and privacy == "public")
    try:
        video_id = youtube.upload_video(cfg, store, video, meta,
                                        publish_at=publish_at if raw_privacy["schedule"] else None)
    finally:
        raw_privacy.clear()
        raw_privacy.update(saved)
    if thumb_path:
        youtube.set_thumbnail(cfg, store, video_id, thumb_path)
    playlist_title = cfg.get("upload.playlist_title", "")
    if playlist_title and privacy == "public":
        pid = youtube.ensure_playlist(cfg, store, playlist_title)
        if pid:
            youtube.add_to_playlist(cfg, store, pid, video_id)
    store.update_video(slug, status="uploaded", youtube_id=video_id,
                       publish_at=(publish_at or dt.datetime.now(dt.timezone.utc)).isoformat(),
                       stage={"url": f"https://youtu.be/{video_id}", "thumbnail": "manual" if thumb_path else "none"})
    return {"name": name, "video_id": video_id, "url": f"https://youtu.be/{video_id}",
            "privacy": privacy, "thumbnail": str(thumb_path or "")}


def set_visibility(cfg: Config, store: Store, target: str, privacy: str,
                   publish_at: dt.datetime | None = None) -> dict[str, Any]:
    """非公開で上げた動画を公開にする（target は yt_001_20260922 か YouTube の動画 ID）."""
    from googleapiclient.errors import HttpError
    from . import youtube

    if privacy not in ("public", "private", "unlisted"):
        raise ValueError("privacy は public / private / unlisted")
    rec = store.get_video(target)
    video_id = rec.youtube_id if rec and rec.youtube_id else target
    guard = youtube.QuotaGuard(store)
    guard.check(youtube.COST_WRITE + 1)
    service = youtube.build_service(cfg)
    res = service.videos().list(part="status", id=video_id).execute()
    items = res.get("items", [])
    if not items:
        raise youtube.UploadError(f"動画が見つかりません: {video_id}")
    status = items[0]["status"]
    status["privacyStatus"] = privacy
    status.pop("publishAt", None)
    if publish_at is not None and privacy == "public":
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        service.videos().update(part="status", body={"id": video_id, "status": status}).execute()
        guard.spend(youtube.COST_WRITE + 1)
    except HttpError as exc:
        raise youtube.UploadError(f"公開設定の変更に失敗しました: {exc}") from exc
    if rec:
        store.update_video(rec.slug, stage={"privacy": privacy}, publish_at=(publish_at or dt.datetime.now(dt.timezone.utc)).isoformat())
    return {"video_id": video_id, "privacy": privacy,
            "publish_at": publish_at.isoformat() if publish_at else None, "url": f"https://youtu.be/{video_id}"}


def parse_jst(text: str | None) -> dt.datetime | None:
    """'2026-09-23 19:00' のような JST 表記を datetime に."""
    if not text:
        return None
    t = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(t, fmt).replace(tzinfo=JST)
        except ValueError:
            continue
    raise ValueError(f"日時は 2026-09-23 19:00 の形で: {text}")
