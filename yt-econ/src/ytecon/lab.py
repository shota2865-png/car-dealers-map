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


def _font_en(cfg: Config, size: int, weight: int = 500):
    """英字の飾り（PSYCH DATA LAB・ANALYZING）は近未来の字体（Orbitron）."""
    from .quiz import font_en
    return font_en(cfg, size, weight)


def _bold_cfg(cfg: Config) -> Config:
    """サムネは小さく表示されるので、細い字体（video.typeface）を外して太い角ゴシックのまま."""
    import copy
    raw = copy.deepcopy(cfg.raw)
    for k in ("typeface", "weight_shift"):
        (raw.get("video") or {}).pop(k, None)
    return Config(raw=raw, root=cfg.root, path=cfg.path)


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
        d.text((112, 30), "PSYCH DATA LAB", font=_font_en(cfg, 30, 600), fill=acc)
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
        od.text((x0 + 32, y1 + 24), "ANALYZING", font=_font_en(cfg, 24, 600), fill=red)
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


def mouth_track(wav_bytes: bytes, cfg: Config | None = None, seed: int = 7) -> list[tuple[float, float, str]]:
    """声から口の形の並び [(始まり秒, 長さ, 形)] を作る。経済チャンネルの立ち絵（character.py）と同じ速さ・同じ決め方:

    1 秒 20 回で音量を測り（ピークを 0.7 くらいに正規化）、閉じるのを少し遅らせてバタつかせない。
    閾値は character.mouth_half_threshold / mouth_open_threshold、まばたきは character.apply_blinks と同じ.
    """
    import tempfile

    from . import character
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        tmp.write(wav_bytes)
        tmp.flush()
        env = character.envelope(Path(tmp.name))
    fps = character.FPS
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    states = character.apply_blinks(cfg, character.mouth_states(cfg, env), fps=fps, seed=seed)
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
    logo_cfg, cfg = cfg, _bold_cfg(cfg)
    pal = palette(cfg)
    TW, TH = 1280, 720
    img = backdrop((TW, TH), pal, [(0.15, 0.2, 0.35), (0.85, 0.9, 0.35)])
    d = ImageDraw.Draw(img)
    acc = pal.get("accent", "#22D3EE")
    d.polygon([(40, 34), (70, 34), (55, 66)], fill=acc)
    d.text((84, 30), "PSYCH DATA LAB", font=_font_en(logo_cfg, 30, 600), fill=acc)
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


# ----------------------------------------------------------------------
# 宇宙の解析室（video.lab_style: space）: 後ろに動く 3D の背景（space.background_loop）を敷く前提の、透明な重ね絵
# ----------------------------------------------------------------------
GLASS = (48, 150, 1400, 910)          # 説明の場面を置くガラスの板（16:9、0.704 倍）
FULL_KINDS = ("opening", "chapter", "data", "verdict")       # 板を使わず、宇宙の空間にじかに置く場面
STEP_OF_KIND = {"chapter": 0, "question": 0, "countdown": 0, "result": 0, "point": 1, "meter": 1, "flow": 1,
                "branch": 1, "versus": 1, "data": 2, "verdict": 3}
STEP_NAMES = ["問い", "しくみ", "データ", "判定"]


def space_enabled(cfg: Config) -> bool:
    return enabled(cfg) and str(cfg.get("video.lab_style", "") or "") == "space"


def caption_chunks(start: float, dur: float, text: str, maxc: int = 26) -> list[tuple[float, float, str]]:
    """字幕を読点・句点で区切り、1 回に出す長さを maxc 字までにする（字数に比例して時間を割る）."""
    pieces, cur = [], ""
    for p in text.replace("、", "、\n").replace("。", "。\n").replace("？", "？\n").split("\n"):
        if not p:
            continue
        if cur and len(cur + p) > maxc:
            pieces.append(cur)
            cur = p
        else:
            cur += p
        if cur.endswith(("。", "？")):
            pieces.append(cur)
            cur = ""
    if cur:
        pieces.append(cur)
    # 「です」「でした」だけが 1 回の字幕にならないよう、短い切れ端は前につなぐ
    merged: list[str] = []
    for p in pieces:
        if merged and len(p.rstrip("、。？")) <= 5 and not merged[-1].endswith(("。", "？")):
            merged[-1] += p
        else:
            merged.append(p)
    pieces = merged
    total = sum(len(p) for p in pieces) or 1
    out, t = [], start
    for p in pieces:
        d = dur * len(p) / total
        out.append((t, d, p.rstrip("、。")))
        t += d
    return out


class SpaceFrame:
    """quiz.build(wide=True) の 1 コマを、宇宙の解析室の重ね絵（RGBA。地は透明）にする.

    左: 説明の場面はガラスの板に、データ・判定・章の扉は空間にじかに
    右: めたん（口パク） / 下: 細い字の字幕。上の帯や飾りは置かない（見る所を中身だけにする）
    """

    def __init__(self, cfg: Config, pal: dict[str, str], question: str = "", chapters_total: int = 4) -> None:
        self.cfg, self.pal = cfg, pal
        self.acc = _rgb(pal.get("accent", "#22D3EE"))
        self.text = _rgb(pal.get("text", "#EAF6FF"))
        self.sub = _rgb(pal.get("text_secondary", "#8FB3CC"))
        self.question = question
        self.total = max(1, chapters_total)
        self._head: dict[tuple, Image.Image] = {}
        self._glass = self._make_glass()
        self._stage: dict[tuple[str, str], Image.Image | None] = {}
        self._icon: dict[str, Image.Image | None] = {}
        self._cap: dict[str, Image.Image] = {}
        self._key = None
        self._mid = None

    # --- 部品 ---
    def _make_glass(self) -> Image.Image:
        x0, y0, x1, y1 = GLASS
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle([x0 - 3, y0 - 3, x1 + 3, y1 + 3], radius=18, outline=self.acc + (110,), width=6)
        lay.alpha_composite(glow.filter(ImageFilter.GaussianBlur(9)))
        d = ImageDraw.Draw(lay)
        d.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=(5, 11, 22, 178), outline=self.acc + (120,), width=2)
        brackets(d, (x0 - 10, y0 - 10, x1 + 10, y1 + 10), self.acc + (220,), L=30, w=3)
        return lay

    def header(self, chapter: int, step: int) -> Image.Image:
        key = (chapter, step)
        if key in self._head:
            return self._head[key]
        cfg = self.cfg
        lay = Image.new("RGBA", (W, 132), (0, 0, 0, 0))
        grad = Image.linear_gradient("L").rotate(180).resize((W, 132)).point(lambda v: int(v * 0.75))
        shade = Image.new("RGBA", (W, 132), (3, 6, 14, 0))
        shade.putalpha(grad)
        lay.alpha_composite(shade)
        d = ImageDraw.Draw(lay)
        d.text((56, 26), "PSYCH DATA LAB", font=_font_en(cfg, 22, 600), fill=self.acc)
        if chapter > 0:
            d.text((56, 60), f"検証 {chapter:02d} / {self.total:02d}", font=_font(cfg, 30, 700), fill=self.text)
        else:
            d.text((56, 60), "はじめに", font=_font(cfg, 30, 700), fill=self.text)
        if self.question:
            x = 330
            d.line([(x - 26, 30), (x - 26, 100)], fill=self.acc + (90,), width=2)
            d.text((x, 24), "今日の問い", font=_font(cfg, 22, 700), fill=self.acc)
            f = _font(cfg, 38, 600)
            q = self.question
            while d.textlength(q, font=f) > 900 and len(q) > 4:
                q = q[:-2] + "…"
            d.text((x, 54), q, font=f, fill=self.text)
        # いまどこを話しているか（軸の上の位置）
        if chapter > 0:
            pw, gap, x0, y0 = 118, 14, W - 56 - (118 * 4 + 14 * 3), 40
            for i, name in enumerate(STEP_NAMES):
                x = x0 + i * (pw + gap)
                box = [x, y0, x + pw, y0 + 50]
                f = _font(cfg, 26, 800 if i == step else 600)
                if i == step:
                    d.rounded_rectangle(box, radius=25, fill=self.acc + (235,))
                    d.text(((box[0] + box[2]) / 2, y0 + 25), name, font=f, fill=(4, 10, 20), anchor="mm")
                elif i < step:
                    d.rounded_rectangle(box, radius=25, outline=self.acc + (200,), width=2)
                    d.text(((box[0] + box[2]) / 2, y0 + 25), name, font=f, fill=self.acc, anchor="mm")
                else:
                    d.rounded_rectangle(box, radius=25, outline=self.sub + (90,), width=2)
                    d.text(((box[0] + box[2]) / 2, y0 + 25), name, font=f, fill=self.sub + (150,), anchor="mm")
                if i < 3:
                    d.line([(x + pw + 2, y0 + 25), (x + pw + gap - 2, y0 + 25)], fill=self.acc + (120 if i < step else 60,), width=2)
        self._head[key] = lay
        return lay

    def stage(self, expr: str, state: str = "base") -> Image.Image | None:
        k = (expr, state)
        if k not in self._stage:
            self._stage[k] = presenter(self.cfg, expr, 500, 700, state=state)
        return self._stage[k]

    def icon(self, ch: str) -> Image.Image | None:
        if ch not in self._icon:
            from . import space
            self._icon[ch] = space.holo_icon(ch, 150, self.acc)
        return self._icon[ch]

    def caption(self, text: str) -> Image.Image:
        if text in self._cap:
            return self._cap[text]
        f = _font(self.cfg, 42, 700)                     # 細い字体（weight_shift で 400）
        tmp = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        tw = tmp.textlength(text, font=f)
        while tw > 1280 and f.size > 28:
            f = _font(self.cfg, f.size - 2, 700)
            tw = tmp.textlength(text, font=f)
        lay = Image.new("RGBA", (int(tw) + 80, 76), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        d.rounded_rectangle([0, 0, lay.width - 1, 75], radius=10, fill=(4, 9, 20, 200))
        d.text((lay.width / 2, 38), text, font=f, fill=self.text, anchor="mm")
        if len(self._cap) > 400:
            self._cap.clear()
        self._cap[text] = lay
        return lay

    # --- 組み立て ---
    def compose(self, scene_img: Image.Image, kind: str = "", mouth: str = "base", caption: str = "",
                icon: str = "", chapter: int = 0) -> Image.Image:
        key = (id(scene_img), kind, icon, chapter)
        if key != self._key or self._mid is None:
            out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sc = scene_img if scene_img.mode == "RGBA" else scene_img.convert("RGBA")
            if kind in FULL_KINDS:
                out.alpha_composite(sc.resize((W, H)) if sc.size != (W, H) else sc)
            else:
                x0, y0, x1, y1 = GLASS
                out.alpha_composite(self._glass)
                out.alpha_composite(sc.resize((x1 - x0, y1 - y0), Image.LANCZOS), (x0, y0))
            # 上の帯（今日の問い・現在地）と例え話のホログラムは出さない（画面をすっきりさせ、見る所を中身だけにする）
            self._key, self._mid = key, out
        out = self._mid.copy()
        m = self.stage(EXPR_BY_KIND.get(kind, "通常"), mouth)
        if m is not None:
            out.alpha_composite(m, (W - m.width + 20, H - m.height))
        if caption:
            c = self.caption(caption)
            out.alpha_composite(c, (int(724 - c.width / 2), 948))
        return out
