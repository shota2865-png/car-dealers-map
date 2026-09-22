"""ログイン不要のフリー素材を自動で集める（Mixkit）.

Mixkit（https://mixkit.co）は動画・音楽・効果音を Mixkit License で配布している:
商用可・クレジット不要・ログイン不要。ページに直リンクがあるので自動で落とせる。
Artlist を使う場合の「先に何も無い」状態を埋める既定の供給源。

    python -m ytecon.freeassets            # 既定の一覧（動画 30 本前後 + 曲 4 + 効果音 8）
    python -m ytecon.freeassets --videos 2 # 検索語ごと 2 本

ダウンロード先は assets/footage/<kind>/ ・ assets/bgm/ ・ assets/sfx/。
同じファイルがあれば飛ばすので、何度実行しても増え続けない。
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from pathlib import Path

import requests

from .config import Config, load_config

log = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
BASE = "https://mixkit.co"

# 検索語（Mixkit のタグ／カテゴリの slug）→ 保存先の種類, タグ
VIDEO_QUERIES: list[tuple[str, str, list[str]]] = [
    # 抽象（カードの後ろ）
    ("abstract", "abstract", ["abstract", "loop"]),
    ("particles", "abstract", ["particles", "loop", "dark"]),
    ("bokeh", "abstract", ["bokeh", "light", "dark"]),
    ("technology", "abstract", ["technology", "digital", "network", "data"]),
    ("light-leaks", "texture", ["light", "leak"]),
    ("smoke", "abstract", ["smoke", "slow", "dark"]),
    # 街・人
    ("tokyo", "broll", ["tokyo", "japan", "city", "street", "crowd"]),
    ("japan", "broll", ["japan", "street", "city"]),
    ("city", "broll", ["city", "street", "night", "skyline"]),
    ("crowd", "broll", ["crowd", "people", "street"]),
    ("office", "broll", ["office", "workers", "business", "salary"]),
    ("commute", "broll", ["commuters", "train", "morning", "work"]),
    ("smartphone", "broll", ["smartphone", "people", "sns"]),
    ("family", "broll", ["family", "home", "kitchen"]),
    # お金・買い物・経済
    ("money", "broll", ["money", "cash", "banknotes", "yen", "dollar"]),
    ("coins", "broll", ["coins", "money", "falling"]),
    ("supermarket", "broll", ["supermarket", "shopping", "grocery", "price", "shelves"]),
    ("shopping", "broll", ["shopping", "price", "shop", "cash", "register"]),
    ("stock-market", "broll", ["stock", "market", "trading", "chart", "finance", "screen"]),
    ("finance", "broll", ["finance", "bank", "graph", "calculator", "accounting"]),
    ("factory", "broll", ["factory", "production", "manufacturing", "industry"]),
    ("shipping", "broll", ["shipping", "containers", "port", "trade", "export"]),
    ("real-estate", "broll", ["real", "estate", "house", "housing", "apartment", "rent"]),
    ("calculator", "broll", ["calculator", "desk", "budget", "tax", "receipt"]),
    ("farm", "broll", ["farmer", "field", "harvest", "food", "agriculture"]),
    ("gas-station", "broll", ["gas", "fuel", "price", "energy"]),
    ("airport", "broll", ["airport", "travel", "tourism"]),
    # 会社員・日常（掛け合いの後ろに敷く、無難な実写）
    ("business-people", "broll", ["business", "people", "office", "workers", "walking", "salary"]),
    ("walking", "broll", ["people", "walking", "street", "commuters", "crowd"]),
    ("businessman", "broll", ["businessman", "suit", "office", "work", "salary"]),
    ("meeting", "broll", ["meeting", "office", "business", "team"]),
    ("typing", "broll", ["laptop", "typing", "office", "work", "desk"]),
    ("train", "broll", ["train", "commuters", "station", "morning"]),
    ("cafe", "broll", ["cafe", "coffee", "price", "shop", "morning"]),
    ("receipt", "broll", ["receipt", "cash", "register", "shopping", "price"]),
    ("wallet", "broll", ["wallet", "cash", "money", "budget"]),
    ("convenience-store", "broll", ["convenience", "store", "shopping", "price"]),
    ("paycheck", "broll", ["salary", "pay", "money", "budget", "calculator"]),
    ("skyscraper", "broll", ["city", "office", "skyline", "business"]),
    ("electric-car", "broll", ["electric", "car", "ev", "charging", "auto"]),
]

MUSIC_QUERIES: list[tuple[str, str]] = [
    ("ambient", "ambient"),
    ("chill", "curiosity"),
    ("suspense", "tension"),
    ("piano", "reflective"),
]

SFX_QUERIES: list[tuple[str, str]] = [
    ("pop", "POP"), ("click", "CLICK"), ("whoosh", "WHOOSH"), ("impact", "IMPACT"),
    ("cartoon", "COMEDY"), ("error", "ERROR"), ("riser", "RISER"), ("swoosh", "TRANSITION"),
]


def _get(url: str, **kw) -> requests.Response | None:
    try:
        r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 30), **kw)
        if r.status_code == 200:
            return r
        log.debug("HTTP %s %s", r.status_code, url)
    except Exception as exc:
        log.debug("取得失敗 %s: %s", url, exc)
    return None


def _download(url: str, dest: Path, min_bytes: int = 20_000) -> Path | None:
    if dest.exists() and dest.stat().st_size >= min_bytes:
        return dest
    r = _get(url, stream=True, timeout=120)
    if r is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    if tmp.stat().st_size < min_bytes:
        tmp.unlink(missing_ok=True)
        return None
    tmp.rename(dest)
    return dest


# ----------------------------------------------------------------------
def video_ids(slug: str, limit: int) -> list[str]:
    """一覧ページから動画 ID を拾う（順序どおり）."""
    ids: list[str] = []
    for url in (f"{BASE}/free-stock-video/{slug}/", f"{BASE}/free-stock-video/tag/{slug}/"):
        r = _get(url)
        if r is None:
            continue
        for m in re.finditer(r"https://assets\.mixkit\.co/videos/(\d+)/\1-360\.mp4", r.text):
            if m.group(1) not in ids:
                ids.append(m.group(1))
        if ids:
            break
    return ids[:limit]


def fetch_videos(cfg: Config, per_query: int = 1, queries=None, min_height: int = 720) -> list[Path]:
    """Mixkit の動画を取り込む。min_height=1080 なら 1080p 版がある素材だけ（画質優先）."""
    from . import footage

    got: list[Path] = []
    root = footage.footage_dir(cfg)
    tags_file = root / "tags.yaml"
    import yaml
    tags: dict = {}
    if tags_file.exists():
        tags = yaml.safe_load(tags_file.read_text(encoding="utf-8")) or {}
    for slug, kind, words in (queries or VIDEO_QUERIES):
        ids = video_ids(slug, per_query * 2)
        n = 0
        for vid in ids:
            if n >= per_query:
                break
            dest = root / kind / f"mixkit_{slug}_{vid}.mp4"
            # 1080p → 720p → 360p の順（min_height より低いものは取らない）
            heights = [hh for hh in (1080, 720, 360) if hh >= min_height]
            p = None
            for hh in heights:
                p = _download(f"https://assets.mixkit.co/videos/{vid}/{vid}-{hh}.mp4", dest, min_bytes=200_000)
                if p is not None:
                    break
            if p is None:
                continue
            tags[f"{kind}/{p.name}"] = {"kind": kind, "tags": sorted(set(words + slug.split("-")))}
            got.append(p)
            n += 1
            time.sleep(0.6)
        log.info("動画 %-14s %d 本", slug, n)
    root.mkdir(parents=True, exist_ok=True)
    tags_file.write_text(yaml.safe_dump(tags, allow_unicode=True, sort_keys=True), encoding="utf-8")
    return got


def fetch_music(cfg: Config, queries=None) -> list[Path]:
    got: list[Path] = []
    d = cfg.root / "assets" / "bgm"
    for slug, mood in (queries or MUSIC_QUERIES):
        dest = d / f"{mood}.mp3"
        if dest.exists():
            got.append(dest)
            continue
        ids: list[str] = []
        for url in (f"{BASE}/free-stock-music/tag/{slug}/", f"{BASE}/free-stock-music/{slug}/"):
            r = _get(url)
            if r is None:
                continue
            ids = re.findall(r"https://assets\.mixkit\.co/music/(\d+)/\1\.mp3", r.text)
            if ids:
                break
        for mid in ids[:6]:
            p = _download(f"https://assets.mixkit.co/music/{mid}/{mid}.mp3", dest, min_bytes=300_000)
            if p:
                got.append(p)
                log.info("曲 %-10s → %s", mood, p.name)
                break
    return got


def fetch_sfx(cfg: Config, queries=None) -> list[Path]:
    got: list[Path] = []
    d = cfg.root / "assets" / "sfx"
    for slug, kind in (queries or SFX_QUERIES):
        dest = d / f"{kind}.mp3"
        if dest.exists():
            got.append(dest)
            continue
        r = _get(f"{BASE}/free-sound-effects/{slug}/")
        if r is None:
            continue
        urls = re.findall(r"https://assets\.mixkit\.co/active_storage/sfx/(\d+)/\1-preview\.mp3", r.text)
        urls = [f"https://assets.mixkit.co/active_storage/sfx/{i}/{i}-preview.mp3" for i in dict.fromkeys(urls)]
        dest = d / f"{kind}.mp3"
        for u in urls[:6]:
            p = _download(u, dest, min_bytes=5_000)
            if p:
                got.append(p)
                log.info("効果音 %-10s → %s", kind, u.rsplit("/", 1)[-1])
                break
    return got


def fetch_all(cfg: Config, per_query: int = 1) -> dict[str, int]:
    v = fetch_videos(cfg, per_query)
    m = fetch_music(cfg)
    s = fetch_sfx(cfg)
    log.info("フリー素材: 動画 %d / 曲 %d / 効果音 %d", len(v), len(m), len(s))
    return {"videos": len(v), "music": len(m), "sfx": len(s)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", type=int, default=1, help="検索語ごとの本数")
    ap.add_argument("--min-height", type=int, default=720, help="この高さ未満の動画は取らない（1080 で画質優先）")
    ap.add_argument("--broll-only", action="store_true", help="実写だけ取る（抽象・質感は取らない）")
    a = ap.parse_args()
    if a.broll_only or a.min_height != 720:
        qs = [q for q in VIDEO_QUERIES if q[1] == "broll"] if a.broll_only else None
        got = fetch_videos(load_config(), a.videos, queries=qs, min_height=a.min_height)
        print(f"動画 {len(got)} 本")
    else:
        print(fetch_all(load_config(), a.videos))
