"""実話ドキュメンタリー型（docu）の素材集め: 引用するショート動画・Google の動画生成（Veo）・BGM.

  quote_media(url)   引用元の投稿の映像（取れなければ表紙の画像）。TikTok / YouTube は oEmbed で表紙が取れる
  veo(prompt)        Gemini API の Veo で 8 秒の映像を作る（GEMINI_API_KEY が無ければ作らない）
  prepare(spec)      台本の phone / carousel / footage に素材のパスを書き込む
  bgm(cfg)           情報系の BGM。曲はリポジトリに置かず、config の url から取ってくる（再配布できないため）

Veo は従量課金（Google AI Pro / Ultra の Google Cloud クレジットを Gemini API に回せる場合がある）。
1 本の動画で作る数は docu.footage.max_per_video までにする。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0"}
GEMINI = "https://generativelanguage.googleapis.com/v1beta"


def _get(url: str, timeout: float = 30, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _oembed(url: str) -> dict[str, Any]:
    if "tiktok.com" in url:
        api = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(url, safe="")
    elif "youtube.com" in url or "youtu.be" in url:
        api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    else:
        return {}
    try:
        return json.loads(_get(api, 20).decode("utf-8", "ignore"))
    except Exception as exc:
        log.info("oEmbed を取れませんでした（%s）: %s", url, exc)
        return {}


def quote_media(url: str, workdir: Path, seconds: float = 10.0, ytdlp: str = "yt-dlp") -> Path | None:
    """引用する投稿の映像（最初の数秒）。取れなければ表紙の画像。どちらも無理なら None."""
    workdir.mkdir(parents=True, exist_ok=True)
    key = "".join(ch if ch.isalnum() else "_" for ch in url)[-80:]
    for ext in (".mp4", ".jpg"):
        p = workdir / f"q_{key}{ext}"
        if p.exists() and p.stat().st_size > 1000:
            return p
    mp4 = workdir / f"q_{key}.mp4"
    try:
        subprocess.run([ytdlp, "--no-warnings", "-q", "-f", "mp4[height<=1280]/best[height<=1280]/best",
                        "--download-sections", f"*0-{seconds:.0f}", "-o", str(mp4), url],
                       check=True, timeout=180, capture_output=True)
        if mp4.exists() and mp4.stat().st_size > 1000:
            return mp4
    except Exception as exc:
        log.info("引用の映像は取れませんでした（%s）。表紙の画像を使います: %s", url, str(exc)[:200])
    thumb = _oembed(url).get("thumbnail_url")
    if thumb:
        jpg = workdir / f"q_{key}.jpg"
        try:
            jpg.write_bytes(_get(str(thumb), 30))
            return jpg
        except Exception as exc:
            log.info("表紙の画像も取れませんでした: %s", exc)
    return None


def veo(prompt: str, out: Path, *, api_key: str | None = None, model: str = "veo-3.1-lite-generate-preview",
        aspect: str = "16:9", seconds: int = 8, resolution: str = "720p", timeout: float = 600) -> Path | None:
    """Gemini API の Veo で映像を作って out に保存。キーが無い・失敗したら None（動画づくりは止めない）."""
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key or not prompt.strip():
        return None
    if out.exists() and out.stat().st_size > 1000:
        return out
    hdr = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {"instances": [{"prompt": prompt}],
            "parameters": {"aspectRatio": aspect, "durationSeconds": str(seconds), "resolution": resolution,
                           "personGeneration": "allow_all"}}
    try:
        req = urllib.request.Request(f"{GEMINI}/models/{model}:predictLongRunning", data=json.dumps(body).encode(),
                                     headers={**_UA, **hdr}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            op = json.loads(r.read())
        name = op.get("name")
        t0 = time.time()
        while name and time.time() - t0 < timeout:
            st = json.loads(_get(f"{GEMINI}/{name}", 30, hdr))
            if st.get("done"):
                if st.get("error"):
                    log.warning("Veo がエラーを返しました: %s", st["error"])
                    return None
                samples = (st.get("response", {}).get("generateVideoResponse", {}) or {}).get("generatedSamples") or []
                uri = samples[0]["video"]["uri"] if samples else None
                if not uri:
                    log.warning("Veo の結果に映像がありません（安全フィルタの可能性）")
                    return None
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(_get(uri, 120, hdr))
                return out
            time.sleep(10)
        log.warning("Veo が時間内に終わりませんでした")
    except Exception as exc:
        log.warning("Veo で映像を作れませんでした: %s", exc)
    return None


def prepare(spec: dict[str, Any], workdir: Path, *, vertical: bool = False, max_clips: int = 3,
            model: str = "veo-3.1-lite-generate-preview", resolution: str = "720p") -> dict[str, Any]:
    """台本に素材のパスを書き込んだコピーを返す（元の spec は変えない）."""
    spec = copy.deepcopy(spec)
    made = 0
    for i, sc in enumerate(spec.get("scenes") or []):
        kind = sc.get("type")
        if kind == "phone" and sc.get("url") and not sc.get("media"):
            m = quote_media(str(sc["url"]), workdir / "quotes")
            if m:
                sc["media"] = str(m)
        if kind == "carousel":
            for it in sc.get("items") or []:
                if it.get("url") and not it.get("media"):
                    m = quote_media(str(it["url"]), workdir / "quotes")
                    if m:
                        it["media"] = str(m)
        if kind == "footage" and sc.get("prompt") and not sc.get("clip") and made < max_clips:
            clip = veo(str(sc["prompt"]), workdir / "veo" / f"scene{i:02d}.mp4", model=model,
                       aspect="9:16" if vertical else "16:9", resolution=resolution)
            if clip:
                sc["clip"] = str(clip)
                made += 1
    return spec


def bgm(root: Path, file: str, url: str = "") -> Path | None:
    """assets/bgm/<file>。無ければ url から取ってくる."""
    p = root / "assets" / "bgm" / file
    if p.exists():
        return p
    if not url:
        return None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_get(url, 60))
        return p
    except Exception as exc:
        log.warning("BGM を取れませんでした: %s", exc)
        return None
