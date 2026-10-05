"""心理学チャンネル「データで検証」の見た目（研究所の解析画面）.

  - 配色はデザイントークンの lab（黒に近い紺・シアンの光）。場面の中身は wide.py / quiz.py のまま
  - 本編は、場面を左の「解析モニター」に縮めて置き、右にめたん（解析担当）を立たせる（Frame）
  - 背景には薄い格子と光。場面の地の色のところだけ格子を見せる（文字や箱の上には出さない）
  - 自動サムネも同じ見た目（thumbnail）

めたんの立ち絵は config の presenter（cast と同じ書き方。PSD の差分の組み合わせ）。無ければ立ち絵なしで描く。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .config import Config

log = logging.getLogger(__name__)

W, H = 1920, 1080
PANEL = (48, 160, 1430, 937)          # 左の解析モニター（場面を 0.72 倍で置く。中身の右端は x=1352 あたり）
STAGE_X = 1400                        # めたんが立つ範囲の左端（めたんは x=1430 から右）

# 場面の種類 → めたんの表情
EXPR_BY_KIND = {
    "opening": "笑", "chapter": "通常", "question": "考", "countdown": "考", "result": "驚",
    "data": "指", "verdict": "笑", "point": "指", "meter": "考", "flow": "指", "branch": "考",
    "versus": "驚", "steps": "笑", "closing": "笑",
}


def enabled(cfg: Config) -> bool:
    return str(cfg.get("video.design", "") or "") == "lab"


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ----------------------------------------------------------------------
# 背景（格子と光）
# ----------------------------------------------------------------------
def grid_layer(size: tuple[int, int], color: str, step: int = 48, major: int = 4, alpha: int = 26) -> Image.Image:
    """薄い方眼。major 本ごとに少し濃い線."""
    w, h = size
    lay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    r, g, b = _rgb(color)
    for i, x in enumerate(range(0, w, step)):
        d.line([(x, 0), (x, h)], fill=(r, g, b, alpha * (2 if i % major == 0 else 1)), width=1)
    for i, y in enumerate(range(0, h, step)):
        d.line([(0, y), (w, y)], fill=(r, g, b, alpha * (2 if i % major == 0 else 1)), width=1)
    return lay


def glow_layer(size: tuple[int, int], color: str, centers: list[tuple[float, float, float]], alpha: int = 70) -> Image.Image:
    """ぼんやりした光の丸（centers = [(x 比, y 比, 半径比)]）."""
    w, h = size
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for cx, cy, rr in centers:
        r = rr * max(w, h)
        d.ellipse([cx * w - r, cy * h - r, cx * w + r, cy * h + r], fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(max(w, h) * 0.08))
    lay = Image.new("RGBA", size, _rgb(color) + (0,))
    lay.putalpha(m)
    return lay


def backdrop(size: tuple[int, int], pal: dict[str, str], glows=None) -> Image.Image:
    img = Image.new("RGBA", size, pal.get("bg", "#060A12"))
    img.alpha_composite(glow_layer(size, pal.get("accent", "#22D3EE"), glows or [(0.12, 0.08, 0.28), (0.92, 0.95, 0.30)], 46))
    img.alpha_composite(grid_layer(size, pal.get("accent", "#22D3EE")))
    return img


def finish(img: Image.Image, bg_hex: str, back: Image.Image) -> Image.Image:
    """場面の絵（地は bg 一色）の、地の色のところだけ back（格子と光）に差し替える."""
    rgb = img.convert("RGB")
    flat = Image.new("RGB", rgb.size, bg_hex)
    diff = ImageChops.difference(rgb, flat).convert("L").point(lambda v: 255 if v > 6 else 0)
    out = back.convert("RGB").resize(rgb.size) if back.size != rgb.size else back.convert("RGB").copy()
    out.paste(rgb, (0, 0), diff)
    return out


# ----------------------------------------------------------------------
# めたん（解析担当）
# ----------------------------------------------------------------------
def presenter_cfg(cfg: Config) -> Config | None:
    entry = cfg.get("presenter") or None
    if not entry:
        return None
    from . import character
    return character.char_cfg(cfg, dict(entry))


def presenter(cfg: Config, expr: str, max_w: int, max_h: int, crop_bottom: float = 0.0, state: str = "base") -> Image.Image | None:
    """めたんの立ち絵（胸から上。下の切り方は character.crop_bottom で済んでいる）。PSD が無い・読めないときは None.

    state: base（口を閉じる）/ mouth_half / mouth_open / blink。どれも base と同じ位置・大きさに切る（口パクでずれない）.
    """
    c = presenter_cfg(cfg)
    if c is None:
        return None
    return _presenter_cached(id(cfg), c, expr, max_w, max_h, crop_bottom, state)


_PCACHE: dict[tuple, Image.Image | None] = {}


def _presenter_cached(key, c: Config, expr: str, max_w: int, max_h: int, crop_bottom: float,
                      state: str = "base") -> Image.Image | None:
    k = (key, expr, max_w, max_h, crop_bottom, state)
    if k in _PCACHE:
        return _PCACHE[k]
    from . import character
    im = None
    try:
        exprs = c.get("character.expressions", {}) or {}
        a = character.find_assets(c, expr if expr in exprs else "通常")
        if a and Path(a.get("base", "")).exists():
            box = Image.open(a["base"]).convert("RGBA").getbbox()
            src = a.get(state) if Path(str(a.get(state, ""))).exists() else a["base"]
            im = Image.open(src).convert("RGBA").crop(box)
            if crop_bottom > 0:
                im = im.crop((0, 0, im.width, int(im.height * (1 - crop_bottom))))
            if c.get("character.flip", False):
                im = ImageOps.mirror(im)
            s = min(max_w / im.width, max_h / im.height)
            im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    except Exception as exc:                       # 立ち絵が無くても動画は作る
        log.warning("めたんの立ち絵を読めませんでした（立ち絵なしで描きます）: %s", exc)
        im = None
    _PCACHE[k] = im
    return im


# ----------------------------------------------------------------------
# 本編の枠（左に解析モニター、右にめたん）
# ----------------------------------------------------------------------
def _font(cfg: Config, size: int, weight: int = 700):
    from .quiz import font
    return font(cfg, size, weight)


def brackets(d: ImageDraw.ImageDraw, box, color, L: int = 36, w: int = 5) -> None:
    """四隅のカギ（解析画面の枠）."""
    x0, y0, x1, y1 = box
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([(x, y + sy * L), (x, y), (x + sx * L, y)], fill=color, width=w)


class Frame:
    """quiz.build(wide=True) が書き出す 1 コマずつを、解析画面に収める."""

    def __init__(self, cfg: Config, pal: dict[str, str], title: str = "") -> None:
        self.cfg, self.pal = cfg, pal
        self.bg = pal.get("bg", "#060A12")
        acc = pal.get("accent", "#22D3EE")
        self.back = backdrop((W, H), pal)
        base = self.back.copy()
        d = ImageDraw.Draw(base)
        # 上の帯: ロゴ・チャンネル名・今日の検証テーマ
        d.rectangle([0, 0, W, 112], fill=_rgb(self.bg) + (235,))
        d.line([(0, 112), (W, 112)], fill=_rgb(acc) + (150,), width=2)
        d.polygon([(56, 34), (96, 34), (76, 78)], fill=acc)
        d.text((112, 30), "PSYCH DATA LAB", font=_font(cfg, 30), fill=acc)
        d.text((112, 66), str(cfg.get("channel.name", "") or ""), font=_font(cfg, 26, 500), fill=pal.get("text_secondary", "#8FB3CC"))
        if title:
            f = _font(cfg, 34)
            t = title
            while d.textlength("検証テーマ｜" + t, font=f) > 1100 and len(t) > 4:
                t = t[:-2] + "…"
            d.text((W - 56, 56), "検証テーマ｜" + t, font=f, fill=pal.get("text", "#EAF6FF"), anchor="rm")
        # 解析モニターの枠
        x0, y0, x1, y1 = PANEL
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=_rgb(acc) + (140,), width=8)
        base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
        self.base = base
        # 枠の線とカギは場面の上に描く
        over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(over)
        od.rectangle([x0, y0, x1, y1], outline=_rgb(acc) + (170,), width=2)
        brackets(od, (x0 - 10, y0 - 10, x1 + 10, y1 + 10), acc)
        red = pal.get("negative", "#FF4D7D")
        od.ellipse([x0 + 4, y1 + 30, x0 + 22, y1 + 48], fill=red)
        od.text((x0 + 32, y1 + 24), "ANALYZING", font=_font(cfg, 24), fill=red)
        od.text((x0 + 200, y1 + 24), "研究データで「よく聞く話」を確かめる", font=_font(cfg, 24, 500), fill=pal.get("text_secondary", "#8FB3CC"))
        self.over = over
        self._stage: dict[tuple[str, str], Image.Image] = {}
        self._panel_key = None
        self._panel = None

    def stage(self, expr: str, state: str = "base") -> Image.Image:
        """右側（めたん・足もとの光の輪・名札）の重ね絵。表情 × 口の形ごとに 1 回だけ作る."""
        if (expr, state) in self._stage:
            return self._stage[(expr, state)]
        pal, cfg = self.pal, self.cfg
        acc = pal.get("accent", "#22D3EE")
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cx = (STAGE_X + W) // 2 + 10
        ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        for k, a in ((0, 150), (18, 90), (36, 50)):
            rd.ellipse([cx - 210 - k, 1000 - 34 - k // 3, cx + 210 + k, 1000 + 34 + k // 3], outline=_rgb(acc) + (a,), width=4)
        lay.alpha_composite(ring.filter(ImageFilter.GaussianBlur(2)))
        m = presenter(cfg, expr, 520, 760, state=state)
        if m is not None:
            lay.paste(m, (W - m.width + 30, H - m.height), m)
        # 名札
        nd = ImageDraw.Draw(lay)
        px0, py0 = STAGE_X + 70, 952
        nd.rounded_rectangle([px0, py0, W - 40, py0 + 92], radius=8, fill=_rgb(pal.get("bg", "#060A12")) + (225,),
                             outline=acc, width=2)
        nd.text((px0 + 22, py0 + 12), "解析担当", font=_font(cfg, 22, 500), fill=acc)
        nd.text((px0 + 22, py0 + 40), "四国めたん", font=_font(cfg, 36), fill=pal.get("text", "#EAF6FF"))
        self._stage[(expr, state)] = lay
        return lay

    def compose(self, scene_img: Image.Image, kind: str = "", mouth: str = "base") -> Image.Image:
        """mouth: 口の形（base / mouth_half / mouth_open / blink）。同じ場面の絵が続くときは枠までを使い回す."""
        key = (id(scene_img), scene_img.size)
        if self._panel_key != key or self._panel is None:
            x0, y0, x1, y1 = PANEL
            pw, ph = x1 - x0, y1 - y0
            sc = scene_img.convert("RGB").resize((pw, ph), Image.LANCZOS)
            sc = finish(sc, self.bg, self.back.crop(PANEL))
            out = self.base.copy()
            out.paste(sc, (x0, y0))
            out.alpha_composite(self.over)
            self._panel_key, self._panel = key, out
        out = self._panel.copy()
        out.alpha_composite(self.stage(EXPR_BY_KIND.get(kind, "通常"), mouth))
        return out.convert("RGB")


def mouth_track(wav_bytes: bytes, fps: int = 12, blink_every: float = 3.6) -> list[tuple[float, float, str]]:
    """声の音量から口の形の並び [(始まり秒, 長さ, 形)] を作る（同じ形が続くところはまとめる）。間が空いたらまばたき."""
    import io
    import wave

    import numpy as np
    with wave.open(io.BytesIO(wav_bytes)) as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(axis=1)
    hop = max(1, sr // fps)
    rms = np.array([np.sqrt(np.mean(a[i:i + hop] ** 2)) for i in range(0, len(a), hop)] or [0.0])
    peak = max(float(np.percentile(rms, 95)), 1e-4)
    states = []
    for i, v in enumerate(rms / peak):
        st = "mouth_open" if v > 0.55 else ("mouth_half" if v > 0.18 else "base")
        if st == "base" and i > 0 and (i / fps) % blink_every < 1.5 / fps:
            st = "blink"
        states.append(st)
    out: list[tuple[float, float, str]] = []
    for i, st in enumerate(states):
        if out and out[-1][2] == st:
            s0, d0, _ = out[-1]
            out[-1] = (s0, d0 + 1 / fps, st)
        else:
            out.append((i / fps, 1 / fps, st))
    return out


# ----------------------------------------------------------------------
# サムネ
# ----------------------------------------------------------------------
_VERDICT_COLOR = {"本当": "positive", "半分本当": "warning", "条件つき": "warning", "ウソ": "negative", "根拠うすい": "negative"}


def stamp(cfg: Config, text: str, color: str, size: int = 120, angle: float = -9) -> Image.Image:
    """斜めの判定スタンプ（枠つきの太字）."""
    f = _font(cfg, size)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = int(tmp.textlength(text, font=f))
    pad = int(size * 0.35)
    im = Image.new("RGBA", (tw + pad * 2, int(size * 1.5) + pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([6, 6, im.width - 6, im.height - 6], radius=int(size * 0.18), outline=color, width=max(6, size // 14))
    d.text((im.width / 2, im.height / 2), text, font=f, fill=color, anchor="mm")
    return im.rotate(angle, expand=True, resample=Image.BICUBIC)


def verdict_color(pal: dict[str, str], verdict: str) -> str:
    return pal.get(_VERDICT_COLOR.get(verdict, "warning"), "#FFC857")


def mini_chart(size: tuple[int, int], pal: dict[str, str], values=(0.35, 0.8, 0.5, 0.95)) -> Image.Image:
    """飾りの小さな棒グラフ（データの雰囲気）."""
    w, h = size
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    n = len(values)
    bw = w / (n * 1.6)
    for i, v in enumerate(values):
        x = i * bw * 1.6 + bw * 0.3
        col = pal.get("accent", "#22D3EE") if i == n - 1 else pal.get("muted", "#557189")
        d.rectangle([x, h - v * h, x + bw, h], fill=col)
    d.line([(0, h - 2), (w, h - 2)], fill=pal.get("text_secondary", "#8FB3CC"), width=3)
    return im


def thumbnail(cfg: Config, data: dict[str, Any], out) -> Path:
    """解析画面のサムネ: 大きな問い（初心者が一瞬で分かる言葉）＋「データで検証」＋めたん."""
    from .assets import palette
    from .quiz import Parts, theme
    pal = palette(cfg)
    TW, TH = 1280, 720
    img = backdrop((TW, TH), pal, [(0.15, 0.2, 0.35), (0.85, 0.9, 0.35)])
    d = ImageDraw.Draw(img)
    acc = pal.get("accent", "#22D3EE")
    d.polygon([(40, 34), (70, 34), (55, 66)], fill=acc)
    d.text((84, 30), "PSYCH DATA LAB", font=_font(cfg, 30), fill=acc)
    # めたん（右）
    m = presenter(cfg, "指", 560, 680)
    if m is not None:
        img.paste(m, (TW - m.width + 30, TH - m.height), m)
    # 大きな問い（左）
    claim = str(data.get("thumb_claim") or data.get("keyword") or data.get("title") or "")
    hl = str(data.get("thumb_hl") or "")
    th = theme(cfg)
    P = Parts(cfg, th)
    max_w = 800
    f, lines = P.fit(d, claim, max_w, 118, 900, min_size=70)
    y = 150 if len(lines) > 1 else 210
    for ln in lines:
        x = 48
        parts = P.split_hl(ln, hl) if hl and hl in ln else [(ln, False)]
        for t, is_hl in parts:
            d.text((x, y), t, font=f, fill=acc if is_hl else pal.get("text", "#EAF6FF"),
                   stroke_width=8, stroke_fill=pal.get("bg", "#060A12"))
            x += int(d.textlength(t, font=f))
        y += int(f.size * 1.25)
    # 「データで検証」のスタンプと小さなグラフ
    st = stamp(cfg, "データで検証", pal.get("warning", "#FFC857"), size=64, angle=-6)
    img.alpha_composite(st, (40, TH - st.height - 34))
    ch = mini_chart((210, 120), pal)
    img.alpha_composite(ch, (60 + st.width, TH - 120 - 50))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=92)
    return out
