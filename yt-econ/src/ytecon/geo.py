"""地理雑学チャンネルの動画（40 代以上向け・16:9）.

地図が主役。1 人の落ち着いたナレーターが話し、画面は「地図・写真・カード」の場面が切り替わる。
年配の視聴者が読みやすいよう、文字はユニバーサルデザインの BIZ UDPゴシックで大きく、字幕は下にはっきり出す。

  spec = {"title": ..., "tts": "gemini" | "voicevox", "voice": "Charon" | 30, "bgm": path,
          "layers": {"muni": {"n03": ["30", "29"]}, "pref": {"url": ".../japan.geojson", "key": "nam_ja"}},
          "scenes": [{"kind": "map" | "photo" | "card", "lines": [文, ...], ...}]}
  render(cfg, spec, outdir) -> video.mp4

地図の場面:
  {"kind": "map", "base": "satellite" | "flat", "layer": "muni", "bbox": [lon0, lat0, lon1, lat1],
   "highlight": ["30427"], "fills": {"30": "#F4D6A0"}, "labels": [[文字, lon, lat, 大きさ]], "markers": [[lon, lat]],
   "arrows": [[lon0, lat0, lon1, lat1]], "zoom": 1.2, "zoom_center": [0.5, 0.5]}
どの場面にも: "heading"（見出し）, "place"（見出しの下の地名）, "locator": [lon, lat]（右上の日本の小さな地図に赤い点）
出典は右上（小さな日本地図の下）に、寄りの動きとは別に重ねる

素材（どれも出典を画面の右上に小さく出す）:
  - 境界: 国土数値情報 行政区域 N03（国土交通省）、dataofjapan/land（都道府県）、Natural Earth（国）
  - 空中写真: 国土地理院 シームレス空中写真（地理院タイル。出典の明示で商用可）
  - 衛星画像（海外）: Sentinel-2 cloudless 2016 by EOX IT Services GmbH（CC BY 4.0）
  - 声: Google Gemini の音声合成（GEMINI_API_KEY）。無ければ VOICEVOX
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .config import Config

log = logging.getLogger(__name__)

W, H = 1920, 1080
SS = 2                     # 地図は 2 倍の大きさで描いてから、ゆっくり寄る（ズームしても荒れない）
FPS = 30
SEA = "#CFE8F3"
LAND = "#F3EEE3"
BORDER = "#FFFFFF"
INK = "#1F2A33"
ACCENT = "#E4572E"
NAVY = (14, 32, 56)
_UA = {"User-Agent": "ytecon-geo/0.1 (YouTube map channel)"}

TILES = {
    "gsi": {"url": "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", "max": 18,
            "credit": "出典：国土地理院（シームレス空中写真）"},
    "eox": {"url": "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg", "max": 14,
            "credit": "Sentinel-2 cloudless 2016 by EOX IT Services GmbH (CC BY 4.0)"},
}
N03_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_{code}_GML.zip"


# ----------------------------------------------------------------------
# 文字
# ----------------------------------------------------------------------
def _font(cfg: Config, size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    d = cfg.root / "assets" / "fonts"
    name = {"bold": "BIZUDPGothic-Bold.ttf", "regular": "BIZUDPGothic-Regular.ttf", "black": "NotoSansJP-Black.ttf"}.get(weight)
    p = d / name if name else d / "BIZUDPGothic-Bold.ttf"
    if not p.exists():
        p = d / "NotoSansJP-Black.ttf"
    return ImageFont.truetype(str(p), size)


def _outlined(d: ImageDraw.ImageDraw, xy, text: str, f, fill=INK, stroke="white", width=8, anchor="mm") -> None:
    d.text(xy, text, font=f, fill=fill, stroke_width=width, stroke_fill=stroke, anchor=anchor)


# ----------------------------------------------------------------------
# 素材の取得（手元に置いて使い回す）
# ----------------------------------------------------------------------
def _cache_dir(cfg: Config, sub: str) -> Path:
    d = cfg.workdir / "materials" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str, dest: Path, retries: int = 3) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    last = None
    for i in range(retries):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=60).read()
            dest.write_bytes(data)
            return dest
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"取得できませんでした: {url} ({last})")


def fetch_file(cfg: Config, src: str) -> Path:
    """URL なら落として手元のパスを返す（ファイル名は URL のハッシュ + 拡張子）."""
    if not src.startswith("http"):
        return Path(src)
    ext = os.path.splitext(urllib.parse.urlparse(src).path)[1] or ".bin"
    return _get(src, _cache_dir(cfg, "geo") / (hashlib.md5(src.encode()).hexdigest()[:16] + ext))


def layer_files(cfg: Config, spec: dict[str, Any]) -> tuple[list[Path], str]:
    """layers の書き方: {"n03": ["30"]}（国土数値情報の行政区域）/ {"url": geojson}/ {"files": [...]}."""
    if spec.get("n03"):
        out = []
        for code in spec["n03"]:
            z = _get(N03_URL.format(code=code), _cache_dir(cfg, "geo") / f"N03-20240101_{code}_GML.zip")
            gj = z.with_suffix("").with_name(f"N03-20240101_{code}.geojson")
            if not gj.exists():
                with zipfile.ZipFile(z) as zf:
                    name = next(n for n in zf.namelist() if n.endswith(".geojson"))
                    gj.write_bytes(zf.read(name))
            out.append(gj)
        return out, spec.get("key", "N03_007")
    if spec.get("url"):
        return [fetch_file(cfg, spec["url"])], spec.get("key", "id")
    return [Path(f) for f in spec.get("files", [])], spec.get("key", "id")


# ----------------------------------------------------------------------
# 地図
# ----------------------------------------------------------------------
def load_layer(files: list[str | Path], key: str) -> list[tuple[str, list[list[tuple[float, float]]]]]:
    """GeoJSON を (コード, 輪郭の並び) にする。MultiPolygon は輪郭ごとに分ける（穴は無視）."""
    out = []
    for f in files:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        for feat in data.get("features", []):
            code = str((feat.get("properties") or {}).get(key) or "")
            g = feat.get("geometry") or {}
            polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
            rings = [[(float(x), float(y)) for x, y, *_ in poly[0]] for poly in polys if poly]
            if rings:
                out.append((code, rings))
    return out


class Mercator:
    """経度・緯度 → 画面の座標（bbox が画面いっぱいに入るように）."""

    def __init__(self, bbox: list[float], size: tuple[int, int], pad: float = 0.04):
        lon0, lat0, lon1, lat1 = bbox
        self.x0, self.x1 = math.radians(lon0), math.radians(lon1)
        self.y0, self.y1 = self._y(lat0), self._y(lat1)
        w, h = size
        sx = w * (1 - 2 * pad) / (self.x1 - self.x0)
        sy = h * (1 - 2 * pad) / (self.y1 - self.y0)
        self.s = min(sx, sy)
        self.ox = (w - (self.x1 - self.x0) * self.s) / 2
        self.oy = (h - (self.y1 - self.y0) * self.s) / 2
        self.h = h

    @staticmethod
    def _y(lat: float) -> float:
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        x = self.ox + (math.radians(lon) - self.x0) * self.s
        y = self.h - (self.oy + (self._y(lat) - self.y0) * self.s)
        return x, y

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        lon = math.degrees((x - self.ox) / self.s + self.x0)
        my = (self.h - y - self.oy) / self.s + self.y0
        lat = math.degrees(2 * math.atan(math.exp(my)) - math.pi / 2)
        return lon, lat


def _tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180) / 360 * n
    lat_r = math.radians(max(-85.0, min(85.0, lat)))
    y = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n
    return x, y


def tile_basemap(cfg: Config, source: str, proj: Mercator, size: tuple[int, int]) -> Image.Image:
    """空中写真・衛星画像のタイルを並べて、画面の範囲ぴったりに切り出す."""
    t = TILES[source]
    w, h = size
    lon0, lat1 = proj.inverse(0, 0)
    lon1, lat0 = proj.inverse(w, h)
    # 画面の幅に対して、タイルの画素が足りる最小のズーム
    z = 1
    while z < t["max"]:
        x0, _ = _tile_xy(lon0, lat1, z)
        x1, _ = _tile_xy(lon1, lat1, z)
        if (x1 - x0) * 256 >= w * 0.9:
            break
        z += 1
    while True:                                            # 広い範囲はタイルが多くなりすぎないよう、少し粗くする
        fx0, fy0 = _tile_xy(lon0, lat1, z)
        fx1, fy1 = _tile_xy(lon1, lat0, z)
        tx0, ty0, tx1, ty1 = int(fx0), int(fy0), int(fx1), int(fy1)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= 300 or z <= 2:
            break
        z -= 1
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), "#0B2B40")
    cache = _cache_dir(cfg, f"tiles/{source}")
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            p = cache / f"{z}_{tx}_{ty}.jpg"
            try:
                _get(t["url"].format(z=z, x=tx % (2 ** z), y=ty), p)
                mosaic.paste(Image.open(p).convert("RGB").resize((256, 256)), ((tx - tx0) * 256, (ty - ty0) * 256))
            except Exception as exc:                      # 海の上など、タイルが無いところは地の色のまま
                log.debug("タイルなし %s: %s", p.name, exc)
    crop = (int((fx0 - tx0) * 256), int((fy0 - ty0) * 256), int((fx1 - tx0) * 256), int((fy1 - ty0) * 256))
    img = mosaic.crop(crop).resize(size, Image.LANCZOS)
    img = ImageEnhance.Color(img).enhance(1.15)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    return ImageEnhance.Brightness(img).enhance(0.88)


def draw_map(cfg: Config, layer, scene: dict[str, Any], size: tuple[int, int]) -> Image.Image:
    proj = Mercator(scene["bbox"], size)
    sat = scene.get("base") == "satellite"
    if sat:
        img = tile_basemap(cfg, scene.get("tiles", "gsi"), proj, size).convert("RGBA")
    else:
        img = Image.new("RGBA", size, SEA)
    d = ImageDraw.Draw(img)
    over = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    fills = scene.get("fills") or {}
    hl = set(scene.get("highlight") or [])

    def color(code: str) -> str | None:
        best = ""
        for k in fills:
            if code.startswith(k) and len(k) > len(best):
                best = k
        return fills.get(best) if best else (None if sat else LAND)

    lon0, lat0, lon1, lat1 = scene["bbox"]
    mlon, mlat = (lon1 - lon0) * 0.6, (lat1 - lat0) * 0.6

    def rings_of(codes=None):
        for code, rings in layer:
            if codes is not None and code not in codes:
                continue
            for ring in rings:
                if any(lon0 - mlon <= x <= lon1 + mlon and lat0 - mlat <= y <= lat1 + mlat for x, y in ring[:: max(1, len(ring) // 20)]):
                    pts = [proj(x, y) for x, y in ring]
                    if len(pts) >= 3:
                        yield code, pts

    for code, pts in rings_of():
        c = color(code)
        if sat:
            if c:
                od.polygon(pts, fill=c + "2E")
            od.line(pts + [pts[0]], fill=(255, 255, 255, 150), width=max(1, int(1.5 * SS)))
        else:
            d.polygon(pts, fill=c or LAND, outline=BORDER, width=max(1, int(2 * SS)))
    img.alpha_composite(over)
    if hl:
        glow = Image.new("L", size, 0)
        gd = ImageDraw.Draw(glow)
        for _, pts in rings_of(hl):
            gd.polygon(pts, outline=255, width=int(12 * SS))
        glow = glow.filter(ImageFilter.GaussianBlur(7 * SS))
        img.paste(Image.new("RGBA", size, "#FFD34D"), (0, 0), glow)
        top = Image.new("RGBA", size, (0, 0, 0, 0))
        td = ImageDraw.Draw(top)
        for _, pts in rings_of(hl):
            td.polygon(pts, fill=(228, 87, 46, 120 if sat else 255), outline=(255, 244, 214, 255), width=int(4 * SS))
        img.alpha_composite(top)
    d = ImageDraw.Draw(img)
    for a in scene.get("arrows") or []:
        _arrow(d, proj(a[0], a[1]), proj(a[2], a[3]), SS, color="#4FC3F7" if sat else "#1565C0")
    for mk in scene.get("markers") or []:
        x, y = proj(mk[0], mk[1])
        r = int((mk[2] if len(mk) > 2 else 16) * SS)
        d.ellipse([x - r - 5 * SS, y - r - 5 * SS, x + r + 5 * SS, y + r + 5 * SS], fill="white")
        d.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT)
    for lab in scene.get("labels") or []:
        text, lon, lat = lab[0], lab[1], lab[2]
        fs = int((lab[3] if len(lab) > 3 else 56) * SS)
        if sat:
            _outlined(d, proj(lon, lat), str(text), _font(cfg, fs), fill="white", stroke=(10, 20, 30), width=int(7 * SS))
        else:
            _outlined(d, proj(lon, lat), str(text), _font(cfg, fs), fill=lab[4] if len(lab) > 4 else INK, width=int(6 * SS))
    return img.convert("RGB")


def credit_of(scene: dict[str, Any]) -> str:
    """画面の右上に小さく出す出典（寄っても切れないよう、絵とは別に重ねる）."""
    kind = scene.get("kind", "map")
    if kind == "map" and scene.get("base") == "satellite":
        return TILES[scene.get("tiles", "gsi")]["credit"]
    if kind == "photo":
        return "　".join(x for x in (scene.get("note", "写真はイメージです"), scene.get("credit", "写真：ぱくたそ")) if x)
    return str(scene.get("credit", "") or "")


def _arrow(d: ImageDraw.ImageDraw, p0, p1, s: float, color: str = "#1565C0") -> None:
    """太い曲がった矢印（川の流れ・ものの動き）."""
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), (x1 - x0)
    k = 0.18
    cx, cy = mx + nx * k, my + ny * k
    pts = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1)
           for t in [i / 40 for i in range(41)]]
    d.line(pts, fill="white", width=int(24 * s), joint="curve")
    d.line(pts, fill=color, width=int(16 * s), joint="curve")
    ang = math.atan2(pts[-1][1] - pts[-4][1], pts[-1][0] - pts[-4][0])
    L = 50 * s
    tip = pts[-1]
    tri = [tip, (tip[0] - L * math.cos(ang - 0.45), tip[1] - L * math.sin(ang - 0.45)),
           (tip[0] - L * math.cos(ang + 0.45), tip[1] - L * math.sin(ang + 0.45))]
    d.polygon(tri, fill=color, outline="white")


def draw_photo(cfg: Config, scene: dict[str, Any], size: tuple[int, int]) -> Image.Image:
    im = Image.open(fetch_file(cfg, scene["file"])).convert("RGB")
    w, h = size
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    im = im.crop((left, top, left + w, top + h))
    return im


def draw_card(cfg: Config, scene: dict[str, Any], size: tuple[int, int]) -> Image.Image:
    """大きな数字や言葉のカード（例: 明治 4 年 / 廃藩置県）。背景は前の地図を暗くぼかしたもの（無ければ紺）."""
    w, h = size
    bg = scene.get("_bg")
    if bg is not None:
        img = bg.resize(size).filter(ImageFilter.GaussianBlur(18 * SS))
        img = Image.blend(img, Image.new("RGB", size, NAVY), 0.68)
    else:
        img = Image.new("RGB", size, NAVY)
    d = ImageDraw.Draw(img)
    cy = h * 0.47
    if scene.get("kicker"):
        f = _font(cfg, int(58 * SS))
        tw = d.textlength(scene["kicker"], font=f)
        d.rounded_rectangle([w / 2 - tw / 2 - 40 * SS, h * 0.27 - 50 * SS, w / 2 + tw / 2 + 40 * SS, h * 0.27 + 50 * SS],
                            radius=int(12 * SS), fill=ACCENT)
        d.text((w / 2, h * 0.27), scene["kicker"], font=f, fill="white", anchor="mm")
    d.text((w / 2, cy), scene.get("big", ""), font=_font(cfg, int(160 * SS)), fill="white", anchor="mm",
           stroke_width=int(4 * SS), stroke_fill=(0, 0, 0))
    if scene.get("sub"):
        d.line([(w / 2 - 260 * SS, h * 0.60), (w / 2 + 260 * SS, h * 0.60)], fill="#FFD34D", width=int(4 * SS))
        d.text((w / 2, h * 0.67), scene["sub"], font=_font(cfg, int(56 * SS), "regular"), fill="#E6EEF5", anchor="mm")
    return img


# ----------------------------------------------------------------------
# 見出し（左上）と、右上の日本の小さな地図
# ----------------------------------------------------------------------
def heading_png(cfg: Config, number: int, text: str, place: str, out: Path) -> Path:
    """ドキュメンタリー風の見出し: 番号の四角 + 見出し + 下に地名。これを左からすべり込ませる."""
    img = Image.new("RGBA", (W, 260), (0, 0, 0, 0))
    if text:
        d = ImageDraw.Draw(img)
        f = _font(cfg, 62)
        fp = _font(cfg, 34, "regular")
        tw = d.textlength(text, font=f)
        pw = d.textlength(place, font=fp) if place else 0
        x0, y0, bh = 48, 44, 104
        box_w = max(tw, pw) + 70
        # 半透明の紺の帯（左に番号）
        d.rectangle([x0, y0, x0 + bh, y0 + bh], fill=ACCENT)
        d.text((x0 + bh / 2, y0 + bh / 2 + 2), f"{number:02d}", font=_font(cfg, 52), fill="white", anchor="mm")
        d.rectangle([x0 + bh, y0, x0 + bh + box_w, y0 + bh], fill=NAVY + (225,))
        d.text((x0 + bh + 34, y0 + bh / 2 + 2), text, font=f, fill="white", anchor="lm")
        d.rectangle([x0 + bh, y0 + bh, x0 + bh + box_w * 0.55, y0 + bh + 6], fill="#FFD34D")
        if place:
            d.text((x0 + bh + 34, y0 + bh + 26), place, font=fp, fill="white", anchor="lt",
                   stroke_width=4, stroke_fill=(0, 0, 0, 200))
    img.save(out)
    return out



def corner_png(cfg: Config, pref_layer, point: list[float] | None, credit: str, out: Path) -> Path:
    """動かない飾り（画面全体の透明な絵）: 字幕の下地になる下のかげ、右上の日本の小さな地図（その場所に赤い点）と出典."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shade = Image.linear_gradient("L").resize((W, 320)).point(lambda v: int(v * 0.62))
    img.paste(Image.new("RGBA", (W, 320), (0, 0, 0, 255)), (0, H - 320), shade)
    y = 40
    loc = _locator(pref_layer, point) if point and pref_layer else None
    if loc is not None:
        img.alpha_composite(loc, (W - loc.width - 40, y))
        y += loc.height + 14
    if credit:
        d = ImageDraw.Draw(img)
        d.text((W - 40, y if loc is not None else 28), credit, font=_font(cfg, 22, "regular"), fill=(255, 255, 255, 230),
               anchor="ra", stroke_width=3, stroke_fill=(0, 0, 0, 170))
    img.save(out)
    return out


def _locator(pref_layer, point: list[float]) -> Image.Image:
    size = (300, 330)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=18, fill=NAVY + (200,), outline=(255, 255, 255, 120), width=2)
    proj = Mercator([128.0, 30.2, 146.5, 45.8], size, pad=0.07)
    for _, rings in pref_layer:
        for ring in rings:
            pts = [proj(x, y) for x, y in ring[:: max(1, len(ring) // 60)]]
            if len(pts) >= 3:
                d.polygon(pts, fill=(236, 230, 214, 255))
    x, y = proj(point[0], point[1])
    d.ellipse([x - 16, y - 16, x + 16, y + 16], fill=(228, 87, 46, 90))
    d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=ACCENT, outline="white", width=3)
    return img


# ----------------------------------------------------------------------
# 声（Gemini / VOICEVOX）
# ----------------------------------------------------------------------
def synth_voicevox(text: str, speaker: int, speed: float, url: str) -> bytes:
    q = urllib.request.urlopen(urllib.request.Request(
        f"{url}/audio_query?speaker={speaker}&text={urllib.parse.quote(text)}", method="POST"), timeout=120).read()
    qd = json.loads(q)
    qd["speedScale"] = speed
    qd["prePhonemeLength"] = 0.1
    qd["postPhonemeLength"] = 0.1
    return urllib.request.urlopen(urllib.request.Request(
        f"{url}/synthesis?speaker={speaker}", data=json.dumps(qd).encode(), headers={"Content-Type": "application/json"},
        method="POST"), timeout=300).read()


def synth_gemini(text: str, voice: str, style: str, model: str, key: str) -> bytes:
    """Gemini の音声合成（24kHz・16bit・モノラルの PCM）を WAV にして返す。混んでいたら待って再試行."""
    prompt = f"{style}\n「{text}」" if style else text
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for attempt in range(8):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
            res = json.loads(urllib.request.urlopen(req, timeout=180).read())
            part = res["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm = base64.b64decode(part["data"])
            rate = 24000
            for kv in str(part.get("mimeType", "")).split(";"):
                if kv.strip().startswith("rate="):
                    rate = int(kv.strip()[5:])
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(pcm)
            return buf.getvalue()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 503) and attempt < 7:
                wait = float(exc.headers.get("Retry-After") or 0) or min(60, 5 * (attempt + 1))
                log.info("Gemini の音声合成が混んでいます（%s）。%.0f 秒待ちます", exc.code, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini の音声合成に失敗: {exc.code} {exc.read()[:300]!r}") from exc
    raise RuntimeError("Gemini の音声合成に失敗しました")


def synth(cfg: Config, spec: dict[str, Any], text: str) -> bytes:
    """声を作る（同じ文・同じ声は手元のものを使い回す）."""
    provider = str(spec.get("tts", "gemini" if os.environ.get("GEMINI_API_KEY") else "voicevox"))
    if provider == "gemini" and not os.environ.get("GEMINI_API_KEY"):
        log.warning("GEMINI_API_KEY が無いので VOICEVOX で読みます")
        provider = "voicevox"
    if provider == "gemini":
        voice = str(spec.get("voice", "Charon"))
        model = str(spec.get("tts_model", "gemini-2.5-flash-preview-tts"))
        style = str(spec.get("voice_style", "落ち着いたドキュメンタリー番組のナレーターとして、ゆっくり、はっきり、温かく読んでください。"))
        key = hashlib.md5(f"g|{model}|{voice}|{style}|{text}".encode()).hexdigest()
        p = _cache_dir(cfg, "tts") / f"{key}.wav"
        if not p.exists():
            p.write_bytes(synth_gemini(text, voice, style, model, os.environ["GEMINI_API_KEY"]))
            time.sleep(float(spec.get("tts_interval", 2.0)))
        return p.read_bytes()
    speaker = int(spec.get("voicevox_voice", spec.get("voice", 30)) if str(spec.get("voice", "30")).isdigit() else spec.get("voicevox_voice", 30))
    return synth_voicevox(text, speaker, float(spec.get("speed", 1.0)), str(cfg.get("voicevox.url", "http://127.0.0.1:50021")))


def _wav_frames(b: bytes) -> tuple[Any, bytes]:
    with wave.open(io.BytesIO(b)) as w:
        return w.getparams(), w.readframes(w.getnframes())


# ----------------------------------------------------------------------
# 書き出し
# ----------------------------------------------------------------------
def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ass_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _wrap_sub(text: str, n: int = 21) -> str:
    """字幕は 2 行まで・1 行 21 字まで。読点 → 助詞のあと、の順に、真ん中に近いところで折る（「土地／を」のように割らない）."""
    if len(text) <= n:
        return text
    L = len(text)

    def best(cands: list[int], slack: int = 2) -> int | None:
        ok = [i for i in cands if L - i <= n + slack and i <= n + slack]
        return min(ok, key=lambda i: abs(i - L / 2)) if ok else None

    punct = [i + 1 for i, ch in enumerate(text[:-1]) if ch in "、。"]
    parts = [i + 1 for i, ch in enumerate(text[:-1]) if ch in "はがをにでとのもへや" and text[i + 1] not in "、。」ゃゅょっー"
             and not text[i + 1:].startswith(("いう", "いわ", "して", "なる"))]
    cut = best(punct) or best(punct, 4) or best(parts) or n
    rest = text[cut:]
    return text[:cut] + r"\N" + (rest if len(rest) <= n + 2 else _wrap_sub(rest, n))


def _resample_wav(b: bytes, rate: int) -> bytes:
    """声の形式をそろえる（Gemini は 24kHz、VOICEVOX は 24kHz。違えば ffmpeg で直す）."""
    params, _ = _wav_frames(b)
    if params.framerate == rate and params.nchannels == 1 and params.sampwidth == 2:
        return b
    r = subprocess.run([_ffmpeg(), "-loglevel", "error", "-i", "pipe:0", "-ar", str(rate), "-ac", "1", "-sample_fmt", "s16",
                        "-f", "wav", "pipe:1"], input=b, capture_output=True, check=True)
    return r.stdout


def render(cfg: Config, spec: dict[str, Any], outdir: str | Path) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    gap, tail = float(spec.get("gap", 0.5)), float(spec.get("scene_tail", 0.8))
    layers = {}
    for name, v in (spec.get("layers") or {}).items():
        files, key = layer_files(cfg, v)
        layers[name] = load_layer(files, key)
    pref_layer = layers.get(spec.get("locator_layer", "pref"))

    # 1) 声を先に作って、場面の長さを決める
    rate = 24000
    audio = bytearray()
    t = 0.0
    cues, spans = [], []
    for sc in spec["scenes"]:
        start = t
        for li, line in enumerate(sc.get("lines") or []):
            wav = _resample_wav(synth(cfg, spec, line), rate)
            params, frames = _wav_frames(wav)
            dur = params.nframes / params.framerate
            audio += frames
            cues.append((t, t + dur, line))
            t += dur
            pause = gap if li < len(sc["lines"]) - 1 else tail
            audio += b"\x00" * int(rate * pause) * 2
            t += pause
        spans.append((start, t))
    voice = outdir / "voice.wav"
    with wave.open(str(voice), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(audio))

    # 2) 場面ごとに、2 倍の大きさの絵を作ってゆっくり寄る。見出しは左からすべり込む
    segs = []
    last_img = None
    num = 0
    for si, (sc, (a, b)) in enumerate(zip(spec["scenes"], spans)):
        big = (W * SS, H * SS)
        kind = sc.get("kind", "map")
        if kind == "map":
            im = draw_map(cfg, layers[sc.get("layer", next(iter(layers)))], sc, big)
        elif kind == "photo":
            im = draw_photo(cfg, sc, big)
        else:
            im = draw_card(cfg, {**sc, "_bg": last_img}, big)
        if kind != "card":
            last_img = im
        still = outdir / f"scene_{si:02d}.jpg"
        im.save(still, quality=92)
        if sc.get("heading"):
            num += 1
        head = heading_png(cfg, num, str(sc.get("heading", "")), str(sc.get("place", "")), outdir / f"head_{si:02d}.png")
        loc = corner_png(cfg, pref_layer, sc.get("locator"), credit_of(sc), outdir / f"corner_{si:02d}.png")
        dur = b - a
        n = max(1, int(round(dur * FPS)))
        z1 = float(sc.get("zoom", 1.12 if kind != "card" else 1.04))
        cx, cy = (sc.get("zoom_center") or [0.5, 0.5])
        zexpr = f"1+({z1}-1)*on/{n}"
        vf = (f"scale={W * SS}:{H * SS},zoompan=z='{zexpr}':x='(iw-iw/zoom)*{cx}':y='(ih-ih/zoom)*{cy}':d={n}:s={W}x{H}:fps={FPS}")
        inputs = ["-loop", "1", "-i", str(still), "-loop", "1", "-i", str(head)]
        # 見出しは 0.5 秒で左からすべり込む（ease-out）
        chain = f"[0:v]{vf}[z];[z][1:v]overlay=x='if(lt(t,0.5),-W*(1-sin(t/0.5*PI/2)),0)':y=0[h]"
        if loc:
            inputs += ["-loop", "1", "-i", str(loc)]
            chain += ";[h][2:v]overlay=0:0:format=auto,format=yuv420p[v]"
        else:
            chain += ";[h]format=yuv420p[v]"
        seg = outdir / f"seg_{si:02d}.mp4"
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", *inputs, "-filter_complex", chain, "-map", "[v]",
                        "-frames:v", str(n), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", str(seg)],
                       check=True)
        segs.append(seg)
    lst = outdir / "segs.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in segs), encoding="utf-8")
    body = outdir / "body.mp4"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(body)],
                   check=True, cwd=outdir)

    # 3) 字幕（BIZ UDPゴシック・大きく・下に帯）と BGM
    font_dir = cfg.root / "assets" / "fonts"
    ass = outdir / "subs.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Sub,BIZ UDPGothic,76,&H00FFFFFF,&H00FFFFFF,&H00141414,&H80000000,-1,0,0,0,100,100,1,0,1,6,2,2,80,80,50,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "".join(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Sub,,0,0,0,,{_wrap_sub(txt)}\n" for s, e, txt in cues),
        encoding="utf-8")
    out = outdir / "video.mp4"
    bgm = spec.get("bgm")
    total = spans[-1][1]
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(body), "-i", str(voice)]
    if bgm and Path(bgm).exists():
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        af = (f"[2:a]volume={float(spec.get('bgm_volume', 0.10))},afade=t=in:d=2,afade=t=out:st={max(0, total - 3):.2f}:d=3[m];"
              f"[1:a]aresample=48000[vo];[vo][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        af = "[1:a]aresample=48000[a]"
    cmd += ["-filter_complex", f"[0:v]ass={ass.name}:fontsdir={font_dir}[v];{af}", "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, cwd=outdir)
    (outdir / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("地理の動画: %s（%.0f 秒）", out, total)
    return out
