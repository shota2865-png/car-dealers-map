"""地理雑学チャンネルの動画（40 代以上向け・16:9）.

地図が主役。1 人の落ち着いたナレーター（VOICEVOX）が話し、画面は「地図・写真・カード」の場面が切り替わる。
年配の視聴者が読みやすいよう、文字は大きく、色は少なく、字幕は下にはっきり出す。

  spec = {"title": ..., "voice": 30, "speed": 1.0, "bgm": path,
          "layers": {"name": {"files": [geojson...], "key": "N03_007"}},
          "scenes": [{"kind": "map" | "photo" | "card", "lines": [文, ...], ...}]}
  render(cfg, spec, outdir) -> video.mp4

地図の場面:
  {"kind": "map", "layer": "muni", "bbox": [lon0, lat0, lon1, lat1], "zoom_to": [lon0, lat0, lon1, lat1],
   "fills": {"30": "#F4D6A0", ...}（コードの先頭一致で塗る）, "highlight": ["30427"], "labels": [[文字, lon, lat, 大きさ]],
   "arrows": [[lon0, lat0, lon1, lat1]], "heading": "見出し"}
境界データは 国土数値情報（行政区域 N03、国土交通省）と dataofjapan/land（都道府県）を使う。
"""

from __future__ import annotations

import io
import json
import logging
import math
import shutil
import subprocess
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


# ----------------------------------------------------------------------
# 文字
# ----------------------------------------------------------------------
def _font(cfg: Config, size: int, weight: str = "black") -> ImageFont.FreeTypeFont:
    name = {"black": "NotoSansJP-Black.ttf", "bold": "NotoSansJP-Bold.ttf"}.get(weight, "NotoSansJP-Black.ttf")
    return ImageFont.truetype(str(cfg.root / "assets" / "fonts" / name), size)


def _outlined(d: ImageDraw.ImageDraw, xy, text: str, f, fill=INK, stroke="white", width=8, anchor="mm") -> None:
    d.text(xy, text, font=f, fill=fill, stroke_width=width, stroke_fill=stroke, anchor=anchor)


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


def draw_map(cfg: Config, layer, scene: dict[str, Any], size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, SEA)
    proj = Mercator(scene["bbox"], size)
    d = ImageDraw.Draw(img)
    fills = scene.get("fills") or {}
    hl = set(scene.get("highlight") or [])

    def color(code: str) -> str:
        if code in hl:
            return ACCENT
        best = ""
        for k in fills:
            if code.startswith(k) and len(k) > len(best):
                best = k
        return fills.get(best, LAND) if best else LAND

    lon0, lat0, lon1, lat1 = scene["bbox"]
    mlon, mlat = (lon1 - lon0) * 0.5, (lat1 - lat0) * 0.5
    for code, rings in layer:
        for ring in rings:
            if not any(lon0 - mlon <= x <= lon1 + mlon and lat0 - mlat <= y <= lat1 + mlat for x, y in ring[:: max(1, len(ring) // 20)]):
                continue
            pts = [proj(x, y) for x, y in ring]
            if len(pts) >= 3:
                d.polygon(pts, fill=color(code), outline=BORDER, width=max(1, int(2 * SS)))
    # 目立たせる場所は、上から太い縁と光
    if hl:
        glow = Image.new("L", size, 0)
        gd = ImageDraw.Draw(glow)
        for code, rings in layer:
            if code in hl:
                for ring in rings:
                    pts = [proj(x, y) for x, y in ring]
                    if len(pts) >= 3:
                        gd.polygon(pts, outline=255, width=int(10 * SS))
        glow = glow.filter(ImageFilter.GaussianBlur(6 * SS))
        img.paste(Image.new("RGB", size, "#FFD34D"), (0, 0), glow)
        d = ImageDraw.Draw(img)
        for code, rings in layer:
            if code in hl:
                for ring in rings:
                    pts = [proj(x, y) for x, y in ring]
                    if len(pts) >= 3:
                        d.polygon(pts, fill=ACCENT, outline="#8C1C13", width=int(3 * SS))
    for a in scene.get("arrows") or []:
        _arrow(d, proj(a[0], a[1]), proj(a[2], a[3]), SS)
    for mk in scene.get("markers") or []:           # 地点のピン（町の場所）
        x, y = proj(mk[0], mk[1])
        r = int((mk[2] if len(mk) > 2 else 16) * SS)
        d.ellipse([x - r - 4 * SS, y - r - 4 * SS, x + r + 4 * SS, y + r + 4 * SS], fill="white")
        d.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT)
    for lab in scene.get("labels") or []:
        text, lon, lat = lab[0], lab[1], lab[2]
        fs = int((lab[3] if len(lab) > 3 else 56) * SS)
        col = lab[4] if len(lab) > 4 else INK
        _outlined(d, proj(lon, lat), str(text), _font(cfg, fs), fill=col, width=int(6 * SS))
    return img


def _arrow(d: ImageDraw.ImageDraw, p0, p1, s: float) -> None:
    """太い曲がった矢印（川の流れ・ものの動き）."""
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), (x1 - x0)
    k = 0.18
    cx, cy = mx + nx * k, my + ny * k
    pts = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1)
           for t in [i / 40 for i in range(41)]]
    d.line(pts, fill="#1565C0", width=int(16 * s), joint="curve")
    ang = math.atan2(pts[-1][1] - pts[-4][1], pts[-1][0] - pts[-4][0])
    L = 46 * s
    tip = pts[-1]
    d.polygon([tip, (tip[0] - L * math.cos(ang - 0.45), tip[1] - L * math.sin(ang - 0.45)),
               (tip[0] - L * math.cos(ang + 0.45), tip[1] - L * math.sin(ang + 0.45))], fill="#1565C0")


def draw_photo(cfg: Config, scene: dict[str, Any], size: tuple[int, int]) -> Image.Image:
    im = Image.open(scene["file"]).convert("RGB")
    w, h = size
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    im = im.crop((left, top, left + w, top + h))
    if scene.get("note", "写真はイメージです"):
        d = ImageDraw.Draw(im)
        f = _font(cfg, int(26 * SS), "bold")
        d.text((w - 30 * SS, 150 * SS), scene.get("note", "写真はイメージです"), font=f, fill="white", anchor="ra",
               stroke_width=int(3 * SS), stroke_fill="#00000099")
    return im


def draw_card(cfg: Config, scene: dict[str, Any], size: tuple[int, int]) -> Image.Image:
    """大きな数字や言葉のカード（例: 明治 4 年 / 廃藩置県）."""
    w, h = size
    img = Image.new("RGB", size, "#16324F")
    g = Image.radial_gradient("L").resize((w, w)).crop((0, (w - h) // 2, w, (w - h) // 2 + h))
    img = Image.composite(Image.new("RGB", size, "#0B1C2C"), img, g)
    d = ImageDraw.Draw(img)
    if scene.get("kicker"):
        d.text((w / 2, h * 0.30), scene["kicker"], font=_font(cfg, int(64 * SS), "bold"), fill="#FFD34D", anchor="mm")
    d.text((w / 2, h * 0.48), scene.get("big", ""), font=_font(cfg, int(170 * SS)), fill="white", anchor="mm")
    if scene.get("sub"):
        d.text((w / 2, h * 0.66), scene["sub"], font=_font(cfg, int(60 * SS), "bold"), fill="#DCE6EE", anchor="mm")
    return img


def heading_png(cfg: Config, text: str, out: Path) -> Path:
    """左上の見出し帯（ズームの外に置く）."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        d = ImageDraw.Draw(img)
        f = _font(cfg, 64)
        tw = d.textlength(text, font=f)
        d.rounded_rectangle([40, 36, 40 + tw + 70, 36 + 108], radius=18, fill=(22, 50, 79, 235))
        d.rectangle([40, 36, 54, 144], fill=ACCENT)
        d.text((40 + 42, 36 + 54), text, font=f, fill="white", anchor="lm")
    img.save(out)
    return out


# ----------------------------------------------------------------------
# 声（VOICEVOX）
# ----------------------------------------------------------------------
def synth(text: str, speaker: int, speed: float, url: str) -> bytes:
    q = urllib.request.urlopen(urllib.request.Request(
        f"{url}/audio_query?speaker={speaker}&text={urllib.parse.quote(text)}", method="POST"), timeout=120).read()
    qd = json.loads(q)
    qd["speedScale"] = speed
    qd["prePhonemeLength"] = 0.1
    qd["postPhonemeLength"] = 0.1
    return urllib.request.urlopen(urllib.request.Request(
        f"{url}/synthesis?speaker={speaker}", data=json.dumps(qd).encode(), headers={"Content-Type": "application/json"},
        method="POST"), timeout=300).read()


def _wav_seconds(b: bytes) -> float:
    with wave.open(io.BytesIO(b)) as w:
        return w.getnframes() / w.getframerate()


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


def render(cfg: Config, spec: dict[str, Any], outdir: str | Path) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    url = str(cfg.get("voicevox.url", "http://127.0.0.1:50021"))
    speaker, speed = int(spec.get("voice", 30)), float(spec.get("speed", 1.0))
    gap, tail = float(spec.get("gap", 0.45)), float(spec.get("scene_tail", 0.6))
    layers = {name: load_layer(v["files"], v["key"]) for name, v in (spec.get("layers") or {}).items()}

    # 1) 声を先に作って、場面の長さを決める
    audio = bytearray()
    params = None
    t = 0.0
    cues, spans = [], []
    for si, sc in enumerate(spec["scenes"]):
        start = t
        for li, line in enumerate(sc.get("lines") or []):
            wav = synth(line, speaker, speed, url)
            with wave.open(io.BytesIO(wav)) as wf:
                params = params or wf.getparams()
                frames = wf.readframes(wf.getnframes())
            dur = _wav_seconds(wav)
            audio += frames
            cues.append((t, t + dur, line))
            t += dur
            pause = gap if li < len(sc["lines"]) - 1 else tail
            audio += b"\x00" * int(params.framerate * pause) * params.sampwidth * params.nchannels
            t += pause
        spans.append((start, t))
    voice = outdir / "voice.wav"
    with wave.open(str(voice), "wb") as wf:
        wf.setparams(params)
        wf.writeframes(bytes(audio))

    # 2) 場面ごとに、2 倍の大きさの絵を作ってゆっくり寄る
    segs = []
    for si, (sc, (a, b)) in enumerate(zip(spec["scenes"], spans)):
        big = (W * SS, H * SS)
        kind = sc.get("kind", "map")
        if kind == "map":
            im = draw_map(cfg, layers[sc.get("layer", next(iter(layers)))], sc, big)
        elif kind == "photo":
            im = draw_photo(cfg, sc, big)
        else:
            im = draw_card(cfg, sc, big)
        still = outdir / f"scene_{si:02d}.png"
        im.save(still)
        head = heading_png(cfg, str(sc.get("heading", "")), outdir / f"head_{si:02d}.png")
        dur = b - a
        n = max(1, int(round(dur * FPS)))
        z1 = float(sc.get("zoom", 1.12 if kind != "card" else 1.04))
        # 寄る中心（地図は zoom_center [0..1, 0..1]、既定は中央）
        cx, cy = (sc.get("zoom_center") or [0.5, 0.5])
        zexpr = f"1+({z1}-1)*on/{n}"
        vf = (f"scale={W * SS}:{H * SS},zoompan=z='{zexpr}':x='(iw-iw/zoom)*{cx}':y='(ih-ih/zoom)*{cy}':d={n}:s={W}x{H}:fps={FPS},"
              f"format=yuv420p")
        seg = outdir / f"seg_{si:02d}.mp4"
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-loop", "1", "-i", str(still), "-i", str(head),
                        "-filter_complex", f"[0:v]{vf}[z];[z][1:v]overlay=0:0,format=yuv420p[v]", "-map", "[v]",
                        "-frames:v", str(n), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        str(seg)], check=True)
        segs.append(seg)
    lst = outdir / "segs.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in segs), encoding="utf-8")
    body = outdir / "body.mp4"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(body)],
                   check=True, cwd=outdir)

    # 3) 字幕（大きく、下に帯）と BGM
    font_dir = cfg.root / "assets" / "fonts"
    ass = outdir / "subs.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Sub,Noto Sans JP Black,86,&H00FFFFFF,&H00FFFFFF,&H00202020,&H99000000,0,0,0,0,100,100,0,0,3,14,0,2,80,80,54,1\n\n"
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
              f"[1:a][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        af = "[1:a]anull[a]"
    cmd += ["-filter_complex", f"[0:v]ass={ass.name}:fontsdir={font_dir}[v];{af}", "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, cwd=outdir)
    (outdir / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("地理の動画: %s（%.0f 秒）", out, total)
    return out
