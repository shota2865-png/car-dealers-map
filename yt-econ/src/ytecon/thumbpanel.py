"""コマ割りの迫力サムネ（ずんだもん解説の定番の型）.

2 つの型がある（spec["layout"]）:
  flow   : 上に黄色い見出し 3 つを ≫ でつなぎ、3 コマで流れを見せる。下に大見出し（例: 賃上げ ≫ でも値上げ ≫ 実質マイナス / 昇給しても貧乏な謎）
  versus : 上に黒地の大見出し、左右 2 コマで比べる（例: 給料アップでも貧乏 / 賃上げ5% ≫ 実質賃金マイナス）

背景は「気分（mood）」で決める: gold（金色 + 光）/ red（真っ赤）/ blue（青 + 光）/ dark（紫と黒 + 赤い集中線）/ glitch（dark + 横筋）。
写真はフリー素材「ぱくたそ」から検索して使う（商用可・クレジット不要。概要欄には念のため書く）。
人の写真は白い背景のものだけを切り抜いて使い、1 枚のサムネに 2 人まで（MAX_PEOPLE）。
ずんだもんは各コマに白い縁つきで置き、吹き出しでひと言しゃべる。

  spec = write_spec(cfg, script)          台本から LLM が組み立てを決める
  render(cfg, spec, out)                  組み立てどおりに描く
  build(cfg, script, out)                 上の 2 つをまとめて
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .config import Config

log = logging.getLogger(__name__)

W, H = 1280, 720
MAX_PEOPLE = 2
MOODS = ("gold", "red", "blue", "dark", "glitch")
EXPRESSIONS = ("喜", "驚愕", "絶望", "困", "考", "指", "笑", "怒", "驚")
_UA = {"User-Agent": "Mozilla/5.0 (ytecon thumbnail)"}


# ----------------------------------------------------------------------
# 文字・図形
# ----------------------------------------------------------------------
_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(cfg: Config, size: int) -> ImageFont.FreeTypeFont:
    """サムネの文字は角ゴシックの極太（thumbnail.font）。動画の丸ゴシック（visuals.font）とは分ける."""
    path = cfg.root / str(cfg.get("thumbnail.font", "assets/fonts/NotoSansJP-Black.ttf"))
    if not path.exists():
        from .assets import load_font
        return load_font(cfg, size, "black")
    key = (str(path), size)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(str(path), size)
    return _FONTS[key]


def _tw(text: str, font) -> float:
    return ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=font)


def cover(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def grade(im: Image.Image, sat=1.6, con=1.3, bri=1.0, tint: str | None = None, amt=0.0) -> Image.Image:
    im = ImageEnhance.Color(im.convert("RGB")).enhance(sat)
    im = ImageEnhance.Contrast(im).enhance(con)
    im = ImageEnhance.Brightness(im).enhance(bri)
    if tint:
        im = Image.blend(im, Image.new("RGB", im.size, tint), amt)
    return im


def rays(size, center, color, n=30, alpha=120, width=0.5) -> Image.Image:
    w, h = size
    cx, cy = center
    r = max(w, h) * 2
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = a0 + 2 * math.pi / n * width
        d.polygon([(cx, cy), (cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1))], fill=alpha)
    lay = Image.new("RGBA", size, color)
    lay.putalpha(m)
    return lay


def fit_h(im: Image.Image, h: int) -> Image.Image:
    return im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)


def cutout_white(im: Image.Image, tol: int = 48) -> Image.Image | None:
    """白い背景の写真から人を切り抜く（四隅から塗りつぶして透明に）。うまく抜けなければ None."""
    im = im.convert("RGB")
    w, h = im.size
    work = im.copy()
    key = (255, 0, 255)
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0)]
    if sum(1 for p in seeds if sum(im.getpixel(p)) > 640) < 3:
        return None
    for p in seeds:
        if sum(work.getpixel(p)) > 600:
            ImageDraw.floodfill(work, p, key, thresh=tol)
    a = ImageChops.difference(work, Image.new("RGB", im.size, key)).convert("L").point(lambda v: 255 if v > 8 else 0)
    cover_ratio = sum(a.resize((64, 64)).point(lambda v: 1 if v else 0).getdata()) / 4096
    if not 0.12 <= cover_ratio <= 0.8:
        return None
    a = a.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    out = im.convert("RGBA")
    out.putalpha(a)
    return out.crop(a.getbbox())


def sticker(im: Image.Image, w: int = 9, color: str = "white") -> Image.Image:
    """白い縁（ステッカー風）と影."""
    pad = w * 3
    base = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
    base.alpha_composite(im, (pad, pad))
    edge = base.getchannel("A").point(lambda v: 255 if v > 60 else 0).filter(ImageFilter.MaxFilter(w * 2 + 1)).filter(ImageFilter.GaussianBlur(1))
    out = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", base.size, (0, 0, 0, 160))
    sh.putalpha(edge.point(lambda v: v * 150 // 255).filter(ImageFilter.GaussianBlur(8)))
    out.alpha_composite(sh, (8, 10))
    ring = Image.new("RGBA", base.size, color)
    ring.putalpha(edge)
    out.alpha_composite(ring)
    out.alpha_composite(base)
    return out


def big_text(cfg: Config, parts: list[tuple[str, str]], size: int, *, inner=10, outer=22, skew=0.12, shadow=True) -> Image.Image:
    """大見出し。parts = [(文字, "red" | "white")]。赤はグラデーション + 白の内縁、白は黒の内縁。外側に黒の太い縁と影."""
    f = _font(cfg, size)
    wsum = sum(_tw(t, f) for t, _ in parts)
    cw, ch = int(wsum + outer * 4 + 40), int(size * 1.5 + outer * 2)
    lay = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    x, y = outer * 2, outer
    for t, color in parts:
        d = ImageDraw.Draw(lay)
        red = color != "white"
        inn = "white" if red else "black"
        d.text((x, y), t, font=f, fill="black", stroke_width=outer, stroke_fill="black")
        d.text((x, y), t, font=f, fill=inn, stroke_width=inner, stroke_fill=inn)
        m = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(m).text((x, y), t, font=f, fill=255)
        if red:
            g = Image.linear_gradient("L").resize((cw, int(size * 1.1)))
            col = Image.composite(Image.new("RGB", g.size, "#B80000"), Image.new("RGB", g.size, "#FF4545"), g)
            fill = Image.new("RGB", (cw, ch), "#B80000")
            fill.paste(col, (0, y + int(size * 0.15)))
        else:
            fill = Image.new("RGB", (cw, ch), "white")
        lay.paste(fill, (0, 0), m)
        x += _tw(t, f)
    lay = lay.crop(lay.getbbox())
    if skew:
        lay = lay.transform((lay.width + int(lay.height * skew), lay.height), Image.AFFINE,
                            (1, skew, -lay.height * skew, 0, 1, 0), Image.BICUBIC)
    if shadow:
        out = Image.new("RGBA", (lay.width + 16, lay.height + 16), (0, 0, 0, 0))
        sh = Image.new("RGBA", lay.size, (0, 0, 0, 200))
        sh.putalpha(lay.getchannel("A").point(lambda v: v * 200 // 255))
        out.alpha_composite(sh, (12, 12))
        out.alpha_composite(lay)
        lay = out
    return lay


def bubble(cfg: Config, text: str, size: int = 34, tail=(0.62, 0.9), tail_to=None, pad=(34, 20), border=6):
    """吹き出し（楕円 + しっぽを 1 つの形として縁取る）。tail_to は楕円の左上からの相対座標。(画像, 楕円の左上) を返す."""
    f = _font(cfg, size)
    w, h = int(_tw(text, f) + pad[0] * 2), int(size * 1.3 + pad[1] * 2)
    tx, ty = tail_to if tail_to else (w * 0.5, h + 50)
    mg = 80
    cw, ch = w + 2 * mg + abs(int(tx)) + 10, h + 2 * mg + abs(int(ty)) + 10
    ox, oy = mg + max(0, -int(tx)), mg + max(0, -int(ty))
    m = Image.new("L", (cw, ch), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([ox, oy, ox + w, oy + h], fill=255)
    bx, by = ox + w * tail[0], oy + h * tail[1]
    ang = math.atan2(oy + ty - by, ox + tx - bx)
    nx, ny = -math.sin(ang), math.cos(ang)
    base = h * 0.18
    d.polygon([(bx + nx * base - math.cos(ang) * base, by + ny * base - math.sin(ang) * base),
               (bx - nx * base - math.cos(ang) * base, by - ny * base - math.sin(ang) * base), (ox + tx, oy + ty)], fill=255)
    ring = m.filter(ImageFilter.MaxFilter(border * 2 + 1))
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    b = Image.new("RGBA", (cw, ch), "black")
    b.putalpha(ring)
    out.alpha_composite(b)
    c = Image.new("RGBA", (cw, ch), "white")
    c.putalpha(m)
    out.alpha_composite(c)
    ImageDraw.Draw(out).text((ox + pad[0], oy + pad[1] - size * 0.08), text, font=f, fill="black")
    return out, (ox, oy), (w, h)


def label(cfg: Config, text: str, size=50, fill="#FFE600", fg="black", border=5, pad=(18, 6)) -> Image.Image:
    f = _font(cfg, size)
    w, h = int(_tw(text, f) + pad[0] * 2 + border * 2), int(size * 1.25 + pad[1] * 2 + border * 2)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w - 1, h - 1], fill="black")
    d.rectangle([border, border, w - 1 - border, h - 1 - border], fill=fill)
    d.text((border + pad[0], border + pad[1] - size * 0.1), text, font=f, fill=fg)
    return im


def fit_label(cfg: Config, text: str, max_w: int, size=50, **kw) -> Image.Image:
    lb = label(cfg, text, size, **kw)
    while lb.width > max_w and size > 28:
        size -= 4
        lb = label(cfg, text, size, **kw)
    return lb


def chevrons(n=3, h=60, colors=("#FF1E1E", "#FF1E1E", "#FFB400")) -> Image.Image:
    w = int(h * 0.55)
    im = Image.new("RGBA", (int(n * w * 0.62 + w), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for k in range(n):
        x = k * w * 0.62
        d.polygon([(x, 0), (x + w * 0.45, 0), (x + w, h / 2), (x + w * 0.45, h), (x, h), (x + w * 0.55, h / 2)],
                  fill=colors[k % len(colors)], outline="black", width=3)
    return im


def say_box(cfg: Config, text: str, size=44, border="#1EAF3C") -> Image.Image:
    """緑の枠の白い箱のセリフ（versus の下）."""
    f = _font(cfg, size)
    w, h = int(_tw(text, f) + 44), int(size * 1.3 + 22)
    im = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w + 11, h + 11], radius=14, fill="black")
    d.rounded_rectangle([4, 4, w + 7, h + 7], radius=12, fill=border)
    d.rounded_rectangle([10, 10, w + 1, h + 1], radius=8, fill="white")
    d.text((28, 12), text, font=f, fill="black")
    return im


def emoji_image(ch: str, size: int) -> Image.Image | None:
    """カラー絵文字を 1 つ、指定の幅で（小物として置く）."""
    for path in ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "/usr/share/fonts/noto/NotoColorEmoji.ttf"):
        if os.path.exists(path):
            break
    else:
        return None
    try:
        f = ImageFont.truetype(path, 109)
        im = Image.new("RGBA", (180, 180), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((10, 10), ch, font=f, embedded_color=True)
        box = im.getbbox()
        if not box:
            return None
        im = im.crop(box)
        return im.resize((size, max(1, int(im.height * size / im.width))), Image.LANCZOS)
    except Exception:
        return None


def place_props(im: Image.Image, props: list[str], right_limit: int) -> None:
    """人を置かないコマの左側に、絵文字の小物を白い縁つきで大きく置く（空のコマにしない）."""
    spots = [(18, 70, 190), (int(right_limit * 0.35), 300, 140)]
    for ch, (x, y, size) in zip([p for p in props if p][:2], spots):
        e = emoji_image(ch, min(size, max(80, right_limit - x - 10)))
        if e is not None:
            im.alpha_composite(sticker(e, w=7), (x, y))


def place_person(bg: Image.Image, person: Image.Image, *, right: int | None = None, left: int | None = None,
                 top: int = 100, max_h: int = 470, min_h: int = 330, max_out: float = 0.3) -> None:
    """人の切り抜きを置く。right（その x より右に出さない）/ left（その x より左に出さない）で、ずんだもんと重ねない.

    下端はコマの下（大見出しの裏）まで下ろし、写真の切れ目を見せない。横は画面の外へ max_out まではみ出してよく、
    それでも小さくなりすぎる（min_h 未満）ときは、はみ出しを 0.45 まで広げる。
    """
    pw, ph = bg.size
    span = right if right is not None else pw - left

    def sized(out_ratio: float) -> Image.Image:
        room = span / (1 - out_ratio)
        h = max_h
        im = sticker(fit_h(person, h), w=8)
        while im.width > room and h > 200:          # 白い縁の分も入れて収める
            h = max(200, int(h * room / im.width) - 2)
            im = sticker(fit_h(person, h), w=8)
        return im

    out_ratio = max_out
    pim = sized(out_ratio)
    if pim.height < min_h:
        out_ratio = 0.45
        pim = sized(out_ratio)
    # 重ねないことを最優先（画面の外へのはみ出しが max_out を超えても、ずんだもん側には出さない）
    if right is not None:
        x = min(right - pim.width, 10)
    else:
        x = max(left, pw - pim.width - 10)
    y = max(top, ph - pim.height + 30)
    bg.alpha_composite(pim, (x, y))


def up_arrows(im: Image.Image, down: bool = False) -> None:
    """黄色い太い矢印を 3 本（値上がり・値下がり）."""
    d = ImageDraw.Draw(im)
    for ax, ay, s in [(24, 150, 1.0), (130, 70, 1.5), (270, 170, 0.9)]:
        w_, h_ = 90 * s, 190 * s
        pts = [(ax, ay + h_ * 0.42), (ax + w_ / 2, ay), (ax + w_, ay + h_ * 0.42), (ax + w_ * 0.7, ay + h_ * 0.42),
               (ax + w_ * 0.7, ay + h_), (ax + w_ * 0.3, ay + h_), (ax + w_ * 0.3, ay + h_ * 0.42)]
        if down:
            pts = [(x, 2 * ay + h_ - y) for x, y in pts]
        d.polygon(pts, fill="#FFE600", outline="black", width=7)


# ----------------------------------------------------------------------
# 素材（ぱくたそ）
# ----------------------------------------------------------------------
def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30).read()


def material_dir(cfg: Config) -> Path:
    d = cfg.workdir / "materials" / "pakutaso"
    d.mkdir(parents=True, exist_ok=True)
    return d


def search_photos(cfg: Config, query: str, limit: int = 8) -> list[Path]:
    """ぱくたそを検索して、写真を手元に落とす（同じ検索は使い回す）."""
    d = material_dir(cfg)
    idx = d / "_index.json"
    try:
        index = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        index = {}
    if query in index:
        return [d / n for n in index[query] if (d / n).exists()]
    names: list[str] = []
    try:
        html = _get("https://www.pakutaso.com/search.html?search=" + urllib.parse.quote(query)).decode("utf-8", "ignore")
        posts = list(dict.fromkeys(re.findall(r"https://www\.pakutaso\.com/\d+post-\d+\.html", html)))[:limit]
        for p in posts:
            try:
                m = re.search(r'og:image" content="([^"]+)', _get(p).decode("utf-8", "ignore"))
                if not m:
                    continue
                name = os.path.basename(m.group(1))
                if not (d / name).exists():
                    (d / name).write_bytes(_get(m.group(1)))
                names.append(name)
            except Exception as exc:
                log.debug("素材を落とせませんでした %s: %s", p, exc)
    except Exception as exc:
        log.warning("ぱくたその検索に失敗（%s）: %s", query, exc)
    index[query] = names
    idx.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return [d / n for n in names]


def _resolve(cfg: Config, name: str) -> Path | None:
    """spec で名前を指定した素材（絶対パス / 素材の置き場 / thumbnail.material_dirs）."""
    p = Path(name)
    if p.is_absolute():
        return p if p.exists() else None
    for d in [material_dir(cfg)] + [cfg.root / str(x) for x in (cfg.get("thumbnail.material_dirs", []) or [])]:
        if (d / name).exists():
            return d / name
    return None


def bust(im: Image.Image, ratio: float = 1.35) -> Image.Image:
    """全身の切り抜きは上半身だけにする（小さくならないように）."""
    if im.height > im.width * ratio * 1.12:
        return im.crop((0, 0, im.width, int(im.width * ratio)))
    return im


def background_for(cfg: Config, p: dict, used: set[str]) -> Image.Image | None:
    if p.get("bg_file"):
        f = _resolve(cfg, str(p["bg_file"]))
        if f:
            used.add(f.name)
            return Image.open(f).convert("RGB")
    return pick_background(cfg, str(p["bg_query"]), used) if p.get("bg_query") else None


def person_for(cfg: Config, p: dict, used: set[str]) -> Image.Image | None:
    cut = None
    if p.get("person_file"):
        f = _resolve(cfg, str(p["person_file"]))
        if f:
            used.add(f.name)
            cut = cutout_white(Image.open(f))
    if cut is None and p.get("person_query"):
        cut = pick_person(cfg, str(p["person_query"]), used)
    return bust(cut) if cut is not None else None


def pick_background(cfg: Config, query: str, avoid: set[str]) -> Image.Image | None:
    for p in search_photos(cfg, query):
        if p.name in avoid:
            continue
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        avoid.add(p.name)
        return im
    return None


def pick_person(cfg: Config, query: str, avoid: set[str], max_aspect: float = 0.95) -> Image.Image | None:
    """白い背景で、きれいに切り抜ける人物写真。腕を広げていない細身の写真（幅 / 高さ ≦ max_aspect）を先に選ぶ.

    コマの幅は狭く、ずんだもんとも重ねないので、横に広い写真は小さくなってしまう。
    """
    fallback = None
    for q in (query, query.split()[-1] if " " in query else ""):
        if not q:
            continue
        for p in search_photos(cfg, q):
            if p.name in avoid:
                continue
            try:
                cut = cutout_white(Image.open(p))
            except Exception:
                cut = None
            if cut is None:
                continue
            b = bust(cut)
            if b.width <= b.height * max_aspect:
                avoid.add(p.name)
                return cut
            if fallback is None:
                fallback = (p.name, cut)
    if fallback:
        avoid.add(fallback[0])
        return fallback[1]
    return None


# ----------------------------------------------------------------------
# 背景（mood）
# ----------------------------------------------------------------------
def mood_bg(mood: str, size: tuple[int, int], photo: Image.Image | None, seed: int = 0) -> Image.Image:
    w, h = size
    mood = mood if mood in MOODS else "dark"
    if mood in ("dark", "glitch"):
        g = Image.radial_gradient("L").resize((w * 2, w * 2)).crop((w // 2, w // 2 - 80, w // 2 + w, w // 2 - 80 + h))
        im = Image.composite(Image.new("RGB", size, "#050008"), Image.new("RGB", size, "#5A00B0" if mood == "dark" else "#6A00FF"), g)
        if photo is not None:                       # 写真があれば、暗い紫に沈めて重ねる（夜・不安）
            ph = ImageChops.multiply(grade(cover(photo, size), sat=0.6, con=1.4, bri=1.1), Image.new("RGB", size, "#8A40FF"))
            im = Image.blend(im, ph, 0.55)
        im = im.convert("RGBA")
        im.alpha_composite(rays(size, (w // 2 - 50, 200), "#FF0040", n=30, alpha=130, width=0.33))
        if mood == "glitch":
            d = ImageDraw.Draw(im)
            rnd = random.Random(seed)
            for _ in range(12):
                y = rnd.randint(0, h)
                d.rectangle([0, y, w, y + rnd.randint(4, 12)], fill=rnd.choice(["#00F0FF55", "#FF00A055", "#FFFFFF33"]))
        return im
    if photo is None:
        base = {"gold": "#FFB000", "red": "#E00010", "blue": "#0A6CFF"}[mood]
        photo = Image.new("RGB", size, base)
    ph = cover(photo, size)
    if mood == "gold":
        im = grade(ph, sat=1.5, con=1.2, bri=1.25, tint="#FFC000", amt=0.42).convert("RGBA")
        im.alpha_composite(rays(size, (w // 2 - 60, 180), "#FFFFFF", n=26, alpha=120))
    elif mood == "red":
        im = ImageChops.multiply(grade(ph, sat=1.6, con=1.4, bri=1.25), Image.new("RGB", size, "#FF2A2A")).convert("RGBA")
    else:  # blue
        im = ImageChops.multiply(grade(ph, sat=1.4, con=1.3, bri=1.3), Image.new("RGB", size, "#3AA0FF")).convert("RGBA")
        im.alpha_composite(rays(size, (w // 2 - 60, 180), "#FFFFFF", n=26, alpha=90))
    return im


# ----------------------------------------------------------------------
# ずんだもん
# ----------------------------------------------------------------------
def zunda(cfg: Config, expr: str, h: int) -> Image.Image | None:
    from . import character
    entries = [e for e in character.cast_entries(cfg) if str(e.get("role", "")) == "student"] or character.cast_entries(cfg)
    c = character.char_cfg(cfg, entries[0]) if entries else cfg
    try:
        a = character.find_assets(c, expr if expr in EXPRESSIONS else "通常")
    except Exception:
        a = None
    if not a or not Path(a.get("base", "")).exists():
        return None
    im = Image.open(a["base"]).convert("RGBA")
    im = im.crop(im.getbbox())
    if c.get("character.flip", False):
        from PIL import ImageOps
        im = ImageOps.mirror(im)
    return sticker(fit_h(im, h), w=9)


# ----------------------------------------------------------------------
# 組み立て
# ----------------------------------------------------------------------
def _limit_people(panels: list[dict]) -> list[dict]:
    n = 0
    out = []
    for p in panels:
        p = dict(p)
        if p.get("person_query") or p.get("person_file"):
            n += 1
            if n > MAX_PEOPLE:
                p["person_query"] = p["person_file"] = ""
        out.append(p)
    return out


def render_flow(cfg: Config, spec: dict[str, Any], out: Path) -> Path:
    top = 96
    pw, ph = W // 3, H - 96
    img = Image.new("RGBA", (W, H), "black")
    panels = _limit_people((spec.get("panels") or [])[:3])
    while len(panels) < 3:
        panels.append({"mood": "dark"})
    used: set[str] = set()
    has_person = [bool(p.get("person_query") or p.get("person_file")) for p in panels]
    zs = [zunda(cfg, str(p.get("zunda") or "驚"), 175 if has_person[i] else 250) for i, p in enumerate(panels)]
    for i, p in enumerate(panels):
        z = zs[i]
        zx_rel = pw - z.width + (36 if has_person[i] else 50) if z is not None else pw
        zleft = zx_rel + ((z.getbbox() or (0, 0, 0, 0))[0] if z is not None else 0)   # ずんだもんの見えている左端
        mood = str(p.get("mood") or "dark")
        bg = mood_bg(mood, (pw, ph), background_for(cfg, p, used), seed=i)
        person = person_for(cfg, p, used) if (p.get("person_query") or p.get("person_file")) else None
        if person is not None:
            place_person(bg, person, right=zleft - 4, top=90, max_h=470, min_h=400)
        else:
            if p.get("arrows") in ("up", "down"):
                up_arrows(bg, down=p["arrows"] == "down")
            elif p.get("props"):
                place_props(bg, [str(x) for x in p["props"]], zleft)
        if p.get("stamp"):
            st = label(cfg, str(p["stamp"])[:6], 66, fill="#E00000", fg="white").rotate(-10, expand=True, resample=Image.BICUBIC)
            bg.alpha_composite(st, (4, 120))
        img.paste(bg, (i * pw, top))
    for i, p in enumerate(panels):
        z = zs[i]
        if z is None:
            continue
        zx = i * pw + pw - z.width + (36 if has_person[i] else 50)
        zy = (596 - z.height) if has_person[i] else 320
        img.alpha_composite(z, (zx, zy))
        line = str(p.get("line") or "")[:9]
        if line:
            _, _, (bw, bh) = bubble(cfg, line, 34)
            ex, ey = i * pw + pw - bw - 10, top + 16
            if ex < i * pw + 6:
                ex = i * pw + 6
            b, (ox, oy), _ = bubble(cfg, line, 34, tail_to=(zx + z.width * 0.42 - ex, zy + 40 - ey))
            img.alpha_composite(b, (ex - ox, ey - oy))
    d = ImageDraw.Draw(img)
    for i in (1, 2):
        d.rectangle([i * pw - 4, top, i * pw + 4, H], fill="black")
    d.rectangle([0, 0, W, top], fill="black")
    labels = [str(x) for x in (spec.get("labels") or [])][:3]
    for i, t in enumerate(labels):
        lb = fit_label(cfg, t[:8], pw - 90)
        img.alpha_composite(lb, (i * pw + (pw - lb.width) // 2, (top - lb.height) // 2))
    for i in range(1, len(labels)):
        ch = chevrons(3, 60)
        img.alpha_composite(ch, (i * pw - ch.width // 2, (top - 60) // 2))
    head = big_text(cfg, _parts(spec), 160, inner=11, outer=22)
    if head.width > W - 16:
        head = head.resize((W - 16, int(head.height * (W - 16) / head.width)), Image.LANCZOS)
    img.alpha_composite(head, ((W - head.width) // 2, H - head.height + 4))
    return _save(img, out)


def render_versus(cfg: Config, spec: dict[str, Any], out: Path) -> Path:
    top = 150
    hw, ph = W // 2, H - 150
    img = Image.new("RGBA", (W, H), "black")
    panels = _limit_people((spec.get("panels") or [])[:2])
    while len(panels) < 2:
        panels.append({"mood": "dark"})
    used: set[str] = set()
    zl = zunda(cfg, str(panels[0].get("zunda") or "喜"), 260)
    zr = zunda(cfg, str(panels[1].get("zunda") or "絶望"), 260)
    zl_x = hw - zl.width + 10 if zl is not None else hw
    zr_x = hw + 4
    zl_left = zl_x + ((zl.getbbox() or (0, 0, 0, 0))[0] if zl is not None else 0)             # 左のずんだもんの見えている左端
    zr_right = zr_x + ((zr.getbbox() or (0, 0, 0, 0))[2] if zr is not None else 0) - hw       # 右のずんだもんの右端（右のコマの中で）
    for i, p in enumerate(panels):
        mood = str(p.get("mood") or ("gold" if i == 0 else "glitch"))
        bg = mood_bg(mood, (hw, ph), background_for(cfg, p, used), seed=i + 7)
        person = person_for(cfg, p, used) if (p.get("person_query") or p.get("person_file")) else None
        if person is not None:
            if i == 0:
                place_person(bg, person, right=zl_left - 4, top=90, max_h=440)
            else:
                place_person(bg, person, left=zr_right + 4, top=90, max_h=440)
        elif p.get("arrows") in ("up", "down"):
            up_arrows(bg, down=p["arrows"] == "down")
        elif p.get("props"):
            if i == 0:
                place_props(bg, [str(x) for x in p["props"]], zl_left)
            else:
                sub = Image.new("RGBA", (hw - zr_right, ph), (0, 0, 0, 0))
                place_props(sub, [str(x) for x in p["props"]], hw - zr_right)
                bg.alpha_composite(sub, (zr_right, 40))
        img.paste(bg, (i * hw, top))
    d = ImageDraw.Draw(img)
    d.rectangle([hw - 5, top, hw + 5, H], fill="white")
    for i, p in enumerate(panels):
        if p.get("label"):
            img.alpha_composite(fit_label(cfg, str(p["label"])[:10], hw - 60, 54), (i * hw + 24, top + 18))
    ch = chevrons(3, 130)
    img.alpha_composite(ch, (hw - ch.width // 2, top + ph // 2 - 110))
    if zl is not None:
        img.alpha_composite(zl, (zl_x, H - zl.height + 60))
    if zr is not None:
        img.alpha_composite(zr, (zr_x, H - zr.height + 60))
    for i, p in enumerate(panels):
        line = str(p.get("line") or "")[:11]
        if line:
            b = say_box(cfg, line)
            if b.width > hw - 60:
                b = say_box(cfg, line, 36)
            img.alpha_composite(b, (20, H - b.height - 16) if i == 0 else (W - b.width - 20, H - b.height - 16))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, top], fill="black")
    head = big_text(cfg, _parts(spec), 150, inner=8, outer=14, skew=0.0, shadow=False)
    s = min((W - 30) / head.width, (top - 10) / head.height)
    head = head.resize((int(head.width * s), int(head.height * s)), Image.LANCZOS)
    img.alpha_composite(head, ((W - head.width) // 2, (top - head.height) // 2))
    return _save(img, out)


def _parts(spec: dict[str, Any]) -> list[tuple[str, str]]:
    parts = []
    for x in spec.get("headline") or []:
        if isinstance(x, (list, tuple)) and x:
            parts.append((str(x[0]), "white" if len(x) > 1 and str(x[1]) == "white" else "red"))
        elif isinstance(x, dict):
            parts.append((str(x.get("text", "")), "white" if x.get("color") == "white" else "red"))
    parts = [(t, c) for t, c in parts if t]
    return parts or [("？", "red")]


def _save(img: Image.Image, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    q = 92
    img = img.convert("RGB")
    img.save(out, quality=q)
    while out.stat().st_size > 2_000_000 and q > 50:
        q -= 10
        img.save(out, quality=q)
    return out


def render(cfg: Config, spec: dict[str, Any], out: str | Path) -> Path:
    if str(spec.get("layout")) == "versus":
        return render_versus(cfg, spec, Path(out))
    return render_flow(cfg, spec, Path(out))


# ----------------------------------------------------------------------
# 組み立てを決める（LLM）
# ----------------------------------------------------------------------
_SPEC_SYSTEM = """あなたは YouTube の「ずんだもん解説」のサムネイル職人です。{field}の動画の内容から、サムネの組み立てを JSON で決めます。
一覧で指が止まる、迫力のあるサムネにします。文字は少なく、大きく、一瞬で意味がわかること。

# 型（layout）
- flow   : 3 コマで流れを見せる。labels は 3 つ（各 7 字以内。例: 「賃上げ5%」「でも値上げ」「実質マイナス」）。panels も 3 つ
- versus : 左右で比べる（前と後、得する人と損する人、建前と本音）。panels は 2 つで、各 label は 9 字以内
話の中に「A なのに B」「A → B → C」の流れがあれば flow、はっきりした対比があれば versus

# 大見出し（headline）
- 合計 9 字以内。[文字, 色] の並び。色は "red"（赤）か "white"（白）。いちばん刺さる語を片方の色に分ける
  例: [["昇給しても", "red"], ["貧乏な謎", "white"]]、[["給料アップでも", "white"], ["貧乏", "red"]]
- 煽り語（ヤバい・終わった・知らないと損）は使わない。内容とずらさない。数字は動画にあるものだけ

# 各コマ（panels）
- mood: 背景の気分。gold（うれしい・お金が増える）/ red（警告・値上がり・焦り）/ blue（冷静・仕組み）/ dark（不安・損・絶望）/ glitch（異常・崩れる）
  コマごとに違う mood にして、左から右へ気分が変わるようにする
- bg_query: 背景の写真を探す短い日本語の言葉。**人が写っていない物・場所**にする（例:「札束」「スーパー 野菜」「ATM」「電卓」「貯金箱」「朝日」「夜 ベッド」）。
  企業名・ブランド名は入れない
- person_query: 人の写真を置くなら、その検索語（例:「札束 男性」「頭を抱える 会社員」「喜ぶ 女性」「驚く 男性」）。**人は全部で 2 コマまで**。置かないコマは空
- arrows: 人を置かないコマで、"up"（値上がり）か "down"（値下がり）の大きな矢印を出すなら。なければ空
- props: 人も矢印も置かないコマに置く絵文字の小物 1〜2 個（例: ["☀️", "☕"]、["💻", "🤖"]、["🏦", "💴"]）。**中身のない空のコマは作らない**
- stamp: 人を置かないコマに斜めの赤い判子（4 字以内。例:「値上げ」「減額」）。なければ空
- zunda: ずんだもんの表情。喜（両手を上げて喜ぶ）/ 驚愕（目を見開く）/ 絶望（ぐるぐる目・青ざめ）/ 困 / 考 / 指 / 笑 / 怒
- line: ずんだもんのひと言。flow は 8 字以内、versus は 10 字以内。「〜のだ」口調（例:「やったのだ！」「高すぎるのだ！」「どうしてこうなった…」）
- label: versus のときだけ、そのコマの黄色い見出し

JSON だけを返す。"""

_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "layout": {"type": "string"},
        "headline": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "panels": {"type": "array", "items": {"type": "object", "properties": {
            "mood": {"type": "string"}, "bg_query": {"type": "string"}, "person_query": {"type": "string"},
            "arrows": {"type": "string"}, "stamp": {"type": "string"}, "props": {"type": "array", "items": {"type": "string"}}, "zunda": {"type": "string"},
            "line": {"type": "string"}, "label": {"type": "string"}}}},
    },
    "required": ["layout", "headline", "panels"],
}


def write_spec(cfg: Config, script) -> dict[str, Any]:
    from . import domain, llm
    from .script import plain_heading, strip_tags
    body = "\n".join(f"- {plain_heading(s.heading)}: {strip_tags(s.narration)[:160]}" for s in script.sections)
    copy = script.thumbnail_copy or {}
    user = (f"タイトル: {strip_tags(script.topic_title)}\n"
            f"サムネの文言の案: {copy.get('main', '')} / {copy.get('sub', '')}\n"
            f"導入: {strip_tags(script.hook)[:300]}\n\n各セクション:\n{body}")
    spec = llm.complete_json(_SPEC_SYSTEM.format(field=domain.field(cfg)), user, _SPEC_SCHEMA,
                             model=str(cfg.get("thumbnail.model", cfg.get("script.model", llm.DEFAULT_MODEL))), effort="medium")
    spec["panels"] = _limit_people(spec.get("panels") or [])
    return spec


def build(cfg: Config, script, out: str | Path) -> Path:
    """台本 → 組み立て（LLM）→ 素材 → サムネ。組み立ては out と同じ場所に thumbnail_spec.json で残す."""
    out = Path(out)
    spec_path = out.parent / "thumbnail_spec.json"
    spec = None
    if spec_path.exists():
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except Exception:
            spec = None
    if spec is None:
        spec = write_spec(cfg, script)
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("サムネ（%s）: %s", spec.get("layout"), "".join(t for t, _ in _parts(spec)))
    return render(cfg, spec, out)

