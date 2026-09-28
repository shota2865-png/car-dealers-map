"""サムネイル生成.

YouTube の長尺はサムネのCTRで再生数がほぼ決まる。ここを機械任せにしすぎると
伸びないので、「大きな主コピー + 補足 + 数字」の型に固定して、
台本側（thumbnail_copy）に文言だけ考えさせる構成にしている。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .assets import _prepare_photo, _wrap, fetch_stock, gradient, load_font, palette
from .config import Config
from .script import VideoScript

SIZE = (1280, 720)


def build(cfg: Config, script: VideoScript, out: str | Path) -> Path:
    """自動サムネ。thumbnail.style =
    cast（ずんだもん解説の定番: 大きな立ち絵 + 太い縁取りの文字 + 集中線）| bar（左にアクセントバー・ゴシック 3 行）| framed（写真 + 細枠 + 明朝 2 行）."""
    main = (script.thumbnail_copy or {}).get("main") or script.topic_title
    sub = (script.thumbnail_copy or {}).get("sub") or ""
    style = str(cfg.get("thumbnail.style", "bar") or "bar").strip().lower()
    if style == "cast":
        return render_cast(cfg, main, sub, out, bubble=(script.thumbnail_copy or {}).get("bubble") or "",
                           seed=script.topic_title)
    if style == "framed":
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        query = next((s.visual.query for s in script.sections if s.visual.query), "") or script.topic_title
        photo = find_photo(cfg, query, out.parent)
        return render_framed(cfg, main, sub, out, photo=photo)
    return _build_bar(cfg, script, out, main, sub)


def _build_bar(cfg: Config, script: VideoScript, out: str | Path, main: str, sub: str) -> Path:
    main = main.replace("／", "")
    pal = palette(cfg)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # 背景: 写真が取れれば写真、駄目ならグラデーション
    img = None
    query = next((s.visual.query for s in script.sections if s.visual.query), "")
    if query:
        raw = fetch_stock(cfg, query, out.parent / "_thumb_raw.jpg")
        if raw:
            tmp = out.parent / "_thumb_bg.jpg"
            # _prepare_photo は動画解像度基準なので一旦それで作ってから縮める
            _prepare_photo(cfg, raw, tmp)
            img = Image.open(tmp).convert("RGB").resize(SIZE, Image.LANCZOS)
            raw.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
    if img is None:
        img = gradient(SIZE, pal["surface"], pal["bg"])

    d = ImageDraw.Draw(img)

    # 左端のアクセントバー
    d.rectangle([0, 0, 18, SIZE[1]], fill=pal["accent"])

    # 主コピー: 1行12字前後、最大3行
    f_main = load_font(cfg, 108, "black")
    lines = _wrap(d, main, f_main, SIZE[0] - 140)[:3]
    if len(lines) == 3:
        f_main = load_font(cfg, 92, "black")
        lines = _wrap(d, main, f_main, SIZE[0] - 140)[:3]
    line_h = f_main.size + 16
    y = (SIZE[1] - line_h * len(lines)) // 2 - (40 if sub else 0)
    for line in lines:
        d.text((66, y), line, font=f_main, fill=pal["text"],
               stroke_width=10, stroke_fill="#06090F")
        y += line_h

    # 補足コピー: 黄色の帯に黒文字で、視認性を上げる
    if sub:
        f_sub = load_font(cfg, 54, "black")
        sub = sub[:24]
        tw = d.textlength(sub, font=f_sub)
        pad = 22
        box = [58, y + 18, 58 + tw + pad * 2, y + 18 + f_sub.size + pad]
        d.rectangle(box, fill=pal["accent2"])
        d.text((58 + pad, y + 18 + pad // 2), sub, font=f_sub, fill="#101010")

    img.save(out, quality=92)
    # YouTube のサムネ上限は 2MB。超えたら品質を落として収める
    q = 92
    while out.stat().st_size > 2_000_000 and q > 50:
        q -= 10
        img.save(out, quality=q)
    return out


# ----------------------------------------------------------------------
# 手で用意したサムネイル（ChatGPT などで作った画像）
# ----------------------------------------------------------------------
import datetime as _dt
import re as _re

_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def manual_dir(cfg: Config) -> Path:
    d = str(cfg.get("upload.thumbnail_dir", "thumbnails") or "thumbnails")
    p = Path(d)
    return p if p.is_absolute() else cfg.root / p


def _date_keys(day: _dt.date | None) -> list[str]:
    if day is None:
        return []
    return [day.isoformat(), day.strftime("%Y%m%d"), day.strftime("%m%d"), day.strftime("%m-%d")]


def pick_manual(cfg: Config, slug: str = "", day: _dt.date | None = None,
                extra: list[str] | None = None) -> Path | None:
    """thumbnails/ から、その本編用の画像を探す。

    優先: 完成品の名前（yt_001_20260922）→ slug → 公開日（YYYY-MM-DD / YYYYMMDD / MMDD）.
    """
    d = manual_dir(cfg)
    if not d.is_dir():
        return None
    keys = [k for k in (extra or []) if k] + ([slug] if slug else []) + _date_keys(day)
    files = [p for p in sorted(d.iterdir()) if p.suffix.lower() in _EXTS and not p.name.startswith(".")]
    for key in keys:
        for p in files:
            if p.stem == key:
                return p
    return None


def prepare(src: str | Path, out: str | Path) -> Path:
    """どんな大きさの画像でも 1280x720 の JPEG（2MB 以下）にする。縦横比が違えば中央で切る."""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGB")
    w, h = img.size
    target = SIZE[0] / SIZE[1]
    if abs(w / h - target) > 0.01:
        if w / h > target:                       # 横に長い → 左右を切る
            nw = int(h * target)
            img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:                                    # 縦に長い → 上下を切る
            nh = int(w / target)
            img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    img = img.resize(SIZE, Image.LANCZOS)
    q = 92
    img.save(out, "JPEG", quality=q)
    while out.stat().st_size > 2_000_000 and q > 50:
        q -= 10
        img.save(out, "JPEG", quality=q)
    return out


def name_for(day: _dt.date | None = None, slug: str = "") -> str:
    """thumbnails/ に置くときのファイル名（slug があれば slug、無ければ公開日）."""
    return (slug or (day or _dt.date.today()).isoformat()) + ".jpg"


# ----------------------------------------------------------------------
# framed スタイル: 暗くした写真 + 細い枠 + 明朝 2 行（2 チャンネル目の既定）
# ----------------------------------------------------------------------
import hashlib as _hashlib
import shutil as _shutil
import subprocess as _subprocess
from collections import Counter as _Counter

from .assets import font_path as _font_path, _wrap as _wrap_text


def _ffmpeg() -> str | None:
    exe = _shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def footage_frame(cfg: Config, query: str, out: Path) -> Path | None:
    """背景素材（assets/footage の実写）から、語の近い 1 本の 1 コマを切り出す。無ければ None."""
    try:
        from . import footage
        clips = [c for c in footage.library(cfg) if c.kind == "broll"]
        if not clips:
            return None
        seed = int(_hashlib.md5(query.encode("utf-8")).hexdigest(), 16) % 1000
        clip = footage.pick(clips, query, "broll", _Counter(), seed=seed) or clips[seed % len(clips)]
        exe = _ffmpeg()
        if not exe:
            return None
        t = max(0.5, min(clip.duration * 0.4, 8.0)) if clip.duration else 1.0
        out.parent.mkdir(parents=True, exist_ok=True)
        _subprocess.run([exe, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(clip.path),
                         "-frames:v", "1", "-q:v", "2", str(out)], check=True, timeout=60)
        return out if out.exists() and out.stat().st_size > 0 else None
    except Exception as exc:                       # サムネは止めない。写真なしで描く
        import logging
        logging.getLogger(__name__).warning("背景素材からコマを取れませんでした: %s", exc)
        return None


def find_photo(cfg: Config, query: str, workdir: Path) -> Path | None:
    """写真の候補: Pexels（鍵があれば）→ 背景素材の 1 コマ → なし."""
    raw = fetch_stock(cfg, query, workdir / "_thumb_raw.jpg") if query else None
    if raw:
        return raw
    return footage_frame(cfg, query, workdir / "_thumb_frame.jpg")


def serif_path(cfg: Config) -> str:
    """明朝のフォント。visuals.font（字幕と同じ丸ゴシック）に引きずられないよう候補を直接見る。無ければ本文の Black."""
    from .assets import _FONT_CANDIDATES
    one = str(cfg.get("thumbnail.serif_font", "") or "").strip()
    cands = ([one] if one else []) + list(_FONT_CANDIDATES.get("serif", []))
    for cand in cands:
        q = Path(cand)
        q = q if q.is_absolute() else cfg.root / q
        if q.exists():
            return str(q)
    return _font_path(cfg, "black")


def _serif(cfg: Config, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(serif_path(cfg), size)
    try:                                             # 可変フォント（NotoSerifJP[wght]）なら Bold に寄せる
        f.set_variation_by_axes([700])
    except Exception:
        pass
    return f


def render_framed(cfg: Config, main: str, sub: str, out: str | Path, *, photo: str | Path | None = None,
                  brand: str | None = None) -> Path:
    """写真を暗くし、細い枠を引き、明朝で 2 行。文字は少なく、余白で見せる."""
    main = (main or "").replace("／", "")
    pal = palette(cfg)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = SIZE

    img = None
    if photo and Path(photo).exists():
        try:
            src = Image.open(photo).convert("RGB")
            scale = max(W / src.width, H / src.height)
            src = src.resize((int(src.width * scale) + 1, int(src.height * scale) + 1), Image.LANCZOS)
            left, top = (src.width - W) // 2, (src.height - H) // 2
            img = src.crop((left, top, left + W, top + H))
        except Exception:
            img = None
    if img is None:
        img = gradient(SIZE, pal["surface_high"], pal["bg"])
    # 暗幕（中央をやや明るく残す）
    dark = Image.new("RGB", SIZE, pal["bg"])
    img = Image.blend(img, dark, 0.58)
    vign = Image.radial_gradient("L").resize(SIZE)
    img = Image.composite(dark, img, vign.point(lambda v: int(min(255, v * 0.55))))

    d = ImageDraw.Draw(img, "RGBA")
    # 細い二重枠
    inset = 34
    d.rectangle([inset, inset, W - inset, H - inset], outline=pal["text"] + "D8", width=3)
    d.rectangle([inset + 10, inset + 10, W - inset - 10, H - inset - 10], outline=pal["text"] + "40", width=1)

    # 主コピー: 明朝、最大 2 行。入らなければ縮める
    main = (main or "").strip()
    f_main = None
    lines: list[str] = []
    for size in (124, 112, 100, 90, 80):
        f_main = _serif(cfg, size)
        lines = _wrap_text(d, main, f_main, W - 2 * (inset + 70))
        if len(lines) <= 2:
            break
    lines = lines[:2]
    line_h = int(f_main.size * 1.22)
    block_h = line_h * len(lines) + (int(f_main.size * 0.9) if sub else 0)
    y = (H - block_h) // 2 - 10
    for line in lines:
        tw = d.textlength(line, font=f_main)
        x = (W - tw) // 2
        d.text((x + 3, y + 4), line, font=f_main, fill=(0, 0, 0, 150))          # 柔らかい影
        d.text((x, y), line, font=f_main, fill=pal["text"])
        y += line_h

    # 補足: 短い罫線 + アクセント色の小さな明朝
    if sub:
        sub = sub.strip()[:22]
        f_sub = _serif(cfg, 40)
        y += 14
        d.line([(W // 2 - 60, y), (W // 2 + 60, y)], fill=pal["accent"], width=2)
        y += 18
        tw = d.textlength(sub, font=f_sub)
        d.text(((W - tw) // 2, y), sub, font=f_sub, fill=pal["accent"])

    # 右下にチャンネル名（小さく）
    brand = (brand if brand is not None else str(cfg.get("channel.name", "") or "")).strip()
    if brand:
        f_b = load_font(cfg, 26, "bold")
        tw = d.textlength(brand, font=f_b)
        d.text((W - inset - 26 - tw, H - inset - 26 - 30), brand, font=f_b, fill=pal["text"] + "B0")

    img = img.convert("RGB")
    q = 92
    img.save(out, quality=q)
    while out.stat().st_size > 2_000_000 and q > 50:
        q -= 10
        img.save(out, quality=q)
    return out


# ----------------------------------------------------------------------
# cast: ずんだもん解説の定番の型（大きな立ち絵 + 太い縁取りの大きな文字 + 集中線）
# ----------------------------------------------------------------------
# 背景の色（タイトルから 1 つ選ぶ。毎日同じ色だと一覧で区別がつかない）: (中心, 外側, 集中線)
CAST_BG = [("#FFF27A", "#FFB319", "#FFFFFF"), ("#BFF3FF", "#2E9BFF", "#FFFFFF"),
           ("#FFD6E8", "#FF5C8A", "#FFFFFF"), ("#D9FFB8", "#3CC45A", "#FFFFFF")]
_EMPH = _re.compile(r"[0-9０-９][0-9０-９,，.．]*\s*(?:億|万|千|兆)?\s*(?:円|%|％|倍|人|年|歳|時間|分|割|社)?")


def _burst(size: tuple[int, int], center: tuple[int, int], inner: str, outer: str, ray: str, n: int = 36) -> Image.Image:
    """放射グラデーション + 集中線."""
    W, H = size
    grad = Image.radial_gradient("L").resize((int(W * 1.9), int(W * 1.9)))
    cx, cy = center
    grad = grad.crop((grad.width // 2 - cx, grad.height // 2 - cy, grad.width // 2 - cx + W, grad.height // 2 - cy + H))
    img = Image.composite(Image.new("RGB", size, outer), Image.new("RGB", size, inner), grad)
    import math
    rays = Image.new("L", size, 0)
    dr = ImageDraw.Draw(rays)
    R = W * 2
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = a0 + math.pi / n * 0.55
        dr.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)), (cx + R * math.cos(a1), cy + R * math.sin(a1))], fill=70)
    return Image.composite(Image.new("RGB", size, ray), img, rays)


def _cast_images(cfg: Config, expressions: dict[str, str]) -> list[Image.Image]:
    """掛け合いの 2 人（聞き役を先頭 = 前に）。立ち絵が無ければ空."""
    from . import character
    entries = character.cast_entries(cfg)
    entries.sort(key=lambda e: 0 if str(e.get("role", "")) == "student" else 1)
    cfgs = [character.char_cfg(cfg, e) for e in entries] or character.characters(cfg)
    out = []
    for c in cfgs:
        key = str(c.get("character.key", "") or "")
        a = character.find_assets(c, expressions.get(key, "通常"))
        if not a or not Path(a.get("base", "")).exists():
            continue
        im = Image.open(a["base"]).convert("RGBA")
        im = im.crop(im.getbbox() or (0, 0, im.width, im.height))
        if c.get("character.flip", False):           # 動画用に中央へ向けた反転を戻し、2 人とも左（文字）を向かせる
            from PIL import ImageOps
            im = ImageOps.mirror(im)
        out.append(im)
    return out


def _outlined(d: ImageDraw.ImageDraw, xy, text: str, font, fill: str, stroke: str, width: int, outer: str | None = None) -> None:
    x, y = xy
    if outer:                                        # 二重の縁取り（外側を太く）
        d.text((x, y), text, font=font, fill=outer, stroke_width=width + max(6, width // 2), stroke_fill=outer)
    d.text((x, y), text, font=font, fill=fill, stroke_width=width, stroke_fill=stroke)


def render_cast(cfg: Config, main: str, sub: str, out: str | Path, *, bubble: str = "",
                expressions: dict[str, str] | None = None, seed: str = "") -> Path:
    """ずんだもん解説の定番の見た目: 右に 2 人の立ち絵を大きく、左に太い縁取りの大きな文字（数字は赤）、背景は集中線.

    main  : 大きな文字（2〜3 行に折る。12〜18 字が目安）
    sub   : 上の赤い帯（10〜16 字）。空なら出さない
    bubble: 聞き役の吹き出し（8 字まで）。空なら出さない
    """
    import hashlib
    W, H = SIZE
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pick = int(hashlib.md5((seed or main).encode()).hexdigest(), 16) % len(CAST_BG)
    inner, outer, ray = CAST_BG[pick]
    img = _burst(SIZE, (int(W * 0.36), int(H * 0.52)), inner, outer, ray).convert("RGBA")

    # 立ち絵: 解説役を右端（半分ほど画面の外）、聞き役をその手前・左に。下は画面の外へはみ出させる
    cast = _cast_images(cfg, expressions or {"zundamon": "驚", "metan": "指"})
    text_right = W - 60
    head_y = 0
    if cast:
        front = cast[0]
        fh = int(H * float(cfg.get("thumbnail.cast_height", 0.80)))
        front = front.resize((int(front.width * fh / front.height), fh), Image.LANCZOS)
        if len(cast) > 1:
            back = cast[1]
            bh = int(fh * 1.04)
            back = back.resize((int(back.width * bh / back.height), bh), Image.LANCZOS)
            bx = W - int(back.width * 0.62)
            img.alpha_composite(back, (bx, H - bh + 30))
            fx = bx - int(front.width * 0.72)
        else:
            fx = W - int(front.width * 0.92)
        img.alpha_composite(front, (fx, H - fh + 30))
        head_y = H - fh + 30
        text_right = fx + int(front.width * 0.04)

    d = ImageDraw.Draw(img)
    x0, y0 = 44, 40

    # 上の赤い帯
    if sub:
        f_sub = load_font(cfg, 56, "black")
        sub = sub.strip()[:16]
        while d.textlength(sub, font=f_sub) > text_right - x0 - 60 and f_sub.size > 36:
            f_sub = load_font(cfg, f_sub.size - 4, "black")
        tw = d.textlength(sub, font=f_sub)
        d.rounded_rectangle([x0 - 8, y0, x0 + tw + 40, y0 + f_sub.size + 34], radius=10, fill="#E60012", outline="#FFFFFF", width=5)
        d.text((x0 + 16, y0 + 12), sub, font=f_sub, fill="#FFFFFF")
        y0 += f_sub.size + 60

    # 大きな文字: 入る大きさまで縮める（最大 3 行）
    main = (main or "").strip()
    avail = max(text_right - x0 - 20, 420)
    font, lines = None, []
    manual = [p.strip() for p in _re.split(r"\n|／|/", main) if p.strip()]   # 「／」か改行で行を指定できる
    for size in (156, 144, 132, 120, 110, 100, 90):
        font = load_font(cfg, size, "black")
        if len(manual) > 1:
            lines = manual
            if max(d.textlength(t, font=font) for t in lines) > avail:
                continue
        else:
            lines = _wrap_text(d, main, font, avail)
        if len(lines) <= 3 and (y0 + len(lines) * int(size * 1.18)) <= H - 30:
            break
    lines = lines[:3]
    lh = int(font.size * 1.18)
    y = max(y0, (H - lh * len(lines)) // 2 + (30 if sub else 0))
    for ln in lines:
        x = x0
        # 数字（と単位）は赤、それ以外は白。縁は黒 + 外側に白
        pos = 0
        segs = []
        for m in _EMPH.finditer(ln):
            if m.group(0).strip():
                segs.append((ln[pos:m.start()], False))
                segs.append((m.group(0), True))
                pos = m.end()
        segs.append((ln[pos:], False))
        for t, emph in segs:
            if not t:
                continue
            _outlined(d, (x, y), t, font, "#FF1F1F" if emph else "#FFFFFF", "#111111", max(8, font.size // 11), outer="#FFFFFF")
            x += d.textlength(t, font=font)
        y += lh

    # 聞き役の吹き出し（頭の上）
    if bubble and cast:
        bubble = bubble.strip()[:8]
        f_b = load_font(cfg, 50, "black")
        tw = d.textlength(bubble, font=f_b)
        bw, bh_ = tw + 60, 92
        bx0 = min(max(fx + front.width // 2 - bw // 2, text_right - 40), W - bw - 16)
        by0 = max(12, head_y - bh_ - 22)
        tip = (fx + front.width * 0.45, head_y + 18)
        d.polygon([(bx0 + bw * 0.35, by0 + bh_ - 8), (bx0 + bw * 0.55, by0 + bh_ - 8), tip], fill="#FFFFFF", outline="#111111", width=5)
        d.rounded_rectangle([bx0, by0, bx0 + bw, by0 + bh_], radius=46, fill="#FFFFFF", outline="#111111", width=6)
        d.text((bx0 + 30, by0 + 16), bubble, font=f_b, fill="#111111")

    img = img.convert("RGB")
    q = 92
    img.save(out, quality=q)
    while out.stat().st_size > 2_000_000 and q > 50:
        q -= 10
        img.save(out, quality=q)
    return out
