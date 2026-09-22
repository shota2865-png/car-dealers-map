"""サムネイル生成.

YouTube の長尺はサムネのCTRで再生数がほぼ決まる。ここを機械任せにしすぎると
伸びないので、「大きな主コピー + 補足 + 数字」の型に固定して、
台本側（thumbnail_copy）に文言だけ考えさせる構成にしている。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .assets import _prepare_photo, _wrap, fetch_stock, gradient, load_font, palette
from .config import Config
from .script import VideoScript

SIZE = (1280, 720)


def build(cfg: Config, script: VideoScript, out: str | Path) -> Path:
    pal = palette(cfg)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    main = (script.thumbnail_copy or {}).get("main") or script.topic_title
    sub = (script.thumbnail_copy or {}).get("sub") or ""

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


def pick_manual(cfg: Config, slug: str = "", day: _dt.date | None = None) -> Path | None:
    """thumbnails/ から、その本編用の画像を探す。優先: slug 一致 → 公開日一致（YYYY-MM-DD / YYYYMMDD / MMDD）."""
    d = manual_dir(cfg)
    if not d.is_dir():
        return None
    keys = ([slug] if slug else []) + _date_keys(day)
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
