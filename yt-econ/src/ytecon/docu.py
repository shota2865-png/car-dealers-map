"""実話ドキュメンタリー型の動画（黒緑の背景・白の極太テロップ・赤の強調・毎行字幕・出典表記）.

参考: 実在の人や話題を、本人の投稿（ショート動画・ブログ）を引用しながら時系列でたどる形式。
  - 背景は黒〜深緑の無地（劇的な場面は赤み）。テロップは白の極太、強調は赤字か赤い帯、補助は黄緑
  - 下に毎行の字幕、左上に小さく出典（「引用：〇〇」）、右上に日付
  - 部品は単純な図（数字・比較・人や封筒のアイコン・棒グラフ・カレンダー・メーター・チェックリスト）
  - 語りは一人（VOICEVOX の男性）。効果音は POP / WHOOSH / IMPACT / CLICK / RISER（assets/sfx）

台本（spec）は場面の並び。場面の中の部品は「i 行目の語りが始まったとき」に i 番目が出る。

  {"title": "...", "scenes": [
     {"type": "statement", "lines": ["語り1", "語り2"], "text": "AIは[[知りません]]", "source": "引用：…", "date": "2024/08/01"},
     ...]}

テロップの記法: [[赤い帯]]  [赤字]  {黄緑}
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger(__name__)

FPS = 30
WHITE = (242, 242, 240)
RED = (255, 52, 52)
RED_BOX = (226, 30, 38)
LIME = (182, 242, 60)
GRAY = (138, 148, 140)
DIM = (70, 80, 72)
CARD = (24, 31, 27)
CARD_LINE = (48, 58, 51)
YELLOW = (255, 214, 64)

_ROOT = Path(__file__).resolve().parents[2]
_FONT_DIR = _ROOT / "assets" / "fonts"
_FONTS: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(size: int, weight: str = "black") -> ImageFont.FreeTypeFont:
    name = {"black": "NotoSansJP-Black.ttf", "bold": "NotoSansJP-Bold.ttf"}[weight]
    key = (name, size)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(str(_FONT_DIR / name), size)
    return _FONTS[key]


# ----------------------------------------------------------------------
# テロップ（記法つきの文字）
# ----------------------------------------------------------------------
_TOKEN = re.compile(r"\[\[(.+?)\]\]|\[(.+?)\]|\{(.+?)\}")


def runs(text: str) -> list[tuple[str, str]]:
    """'AIは[[知りません]]' → [('AIは','w'), ('知りません','box')]."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _TOKEN.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], "w"))
        if m.group(1) is not None:
            out.append((m.group(1), "box"))
        elif m.group(2) is not None:
            out.append((m.group(2), "red"))
        else:
            out.append((m.group(3), "lime"))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], "w"))
    return out


def plain(text: str) -> str:
    return "".join(t for t, _ in runs(text))


def text_tile(text: str, size: int, weight: str = "black", color=WHITE, align: str = "center",
              line_gap: float = 0.22, shadow: bool = True) -> Image.Image:
    """記法つきの文字を 1 枚の透明画像に（改行は \\n）。赤い帯は字の後ろに敷く."""
    f = font(size, weight)
    lines = text.split("\n")
    rows = []
    for ln in lines:
        rr = runs(ln)
        w = 0
        parts = []
        for t, st in rr:
            tw = f.getlength(t)
            pad = int(size * 0.14) if st == "box" else 0
            parts.append((t, st, tw, pad))
            w += tw + pad * 2
        rows.append((parts, w))
    lh = int(size * (1 + line_gap))
    W = int(max((w for _, w in rows), default=1)) + 24
    H = lh * len(rows) + 24
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    asc, desc = f.getmetrics()
    for i, (parts, w) in enumerate(rows):
        x = 12 + {"center": (W - 24 - w) / 2, "left": 0, "right": W - 24 - w}[align]
        y = 12 + i * lh
        for t, st, tw, pad in parts:
            if st == "box":
                top = y + size * 0.08
                d.rectangle([x, top, x + tw + pad * 2, top + size * 1.08], fill=RED_BOX + (255,))
                d.text((x + pad, y - size * 0.02), t, font=f, fill=WHITE)
                x += tw + pad * 2
                continue
            c = {"w": color, "red": RED, "lime": LIME}[st]
            if shadow:
                d.text((x + 2, y + 3), t, font=f, fill=(0, 0, 0, 150))
            d.text((x, y), t, font=f, fill=c)
            x += tw
    return img


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """字幕を幅で折り返す（日本語は 1 字ずつ。数字・英語のかたまりは切らない。句読点は行頭に来ないように）."""
    toks = re.findall(r"[0-9０-９][0-9０-９,.，．万億千円%％]*|[A-Za-z@][A-Za-z0-9_.@]*|.", text)
    out, cur = [], ""
    for tk in toks:
        if f.getlength(cur + tk) > width and cur:
            if tk in "、。」）！？":
                cur += tk
                continue
            out.append(cur)
            cur = tk
        else:
            cur += tk
    if cur:
        out.append(cur)
    return out


# ----------------------------------------------------------------------
# 画面
# ----------------------------------------------------------------------
def background(W: int, H: int, tone: str = "green") -> Image.Image:
    """黒〜深緑（tone=red で赤み）の無地に、周辺を落とす."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = W * 0.5, H * 0.45
    r = np.sqrt(((xx - cx) / (W * 0.62)) ** 2 + ((yy - cy) / (H * 0.62)) ** 2)
    k = np.clip(1.0 - r, 0, 1) ** 1.4
    base = np.array([6, 9, 7], np.float32)
    lift = np.array({"green": [22, 34, 25], "red": [44, 14, 14], "dark": [16, 18, 17]}[tone], np.float32)
    img = base[None, None, :] + k[..., None] * lift[None, None, :]
    rng = np.random.default_rng(7)
    img += rng.normal(0, 1.4, img.shape[:2])[..., None]           # 帯（バンディング）を消す粒
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def ease_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def ease_back(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    c1, c3 = 1.4, 2.4
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def paste(canvas: Image.Image, tile: Image.Image, cx: float, cy: float, p: float = 1.0,
          pop: bool = True, slide: float = 0.0) -> None:
    """tile を中心 (cx, cy) に。p = 出てからの進み（0→1）で、少し大きく → 等倍・透明 → 不透明."""
    if p <= 0:
        return
    a = ease_out(p / 0.6)
    s = 1.0
    if pop:
        s = 0.9 + 0.1 * ease_back(p)
    t = tile
    if abs(s - 1) > 0.004:
        t = tile.resize((max(1, int(tile.width * s)), max(1, int(tile.height * s))), Image.BILINEAR)
    if a < 0.999:
        al = t.getchannel("A").point(lambda v: int(v * a))
        t = t.copy()
        t.putalpha(al)
    dy = slide * (1 - ease_out(p / 0.5))
    canvas.alpha_composite(t, (int(cx - t.width / 2), int(cy - t.height / 2 + dy)))


def rounded(size: tuple[int, int], r: int, fill, outline=None, width: int = 2) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], r, fill=fill, outline=outline, width=width)
    return img


# --- アイコン（単純な図形で描く） ---------------------------------------
def icon_envelope(s: int, filled: bool, hl: bool = False) -> Image.Image:
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = s * 0.86, s * 0.6
    x0, y0 = (s - w) / 2, (s - h) / 2
    col = (LIME if not hl else RED) if filled else DIM
    fillc = (col + (60,)) if filled else (0, 0, 0, 0)
    d.rectangle([x0, y0, x0 + w, y0 + h], outline=col + (255,), width=max(2, s // 18), fill=fillc)
    d.line([x0, y0, s / 2, y0 + h * 0.55, x0 + w, y0], fill=col + (255,), width=max(2, s // 18))
    return img


def icon_person(s: int, on: bool, hl: bool = False) -> Image.Image:
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (RED if hl else WHITE) if on else DIM
    d.ellipse([s * 0.34, s * 0.08, s * 0.66, s * 0.4], fill=col)
    d.rounded_rectangle([s * 0.2, s * 0.46, s * 0.8, s * 0.95], int(s * 0.18), fill=col)
    return img


# ----------------------------------------------------------------------
# 台本 → 時刻
# ----------------------------------------------------------------------
@dataclass
class Line:
    text: str                 # 字幕に出す文
    say: str                  # 読み上げる文（読みの補正込み）
    start: float = 0.0
    end: float = 0.0
    wav: Path | None = None


@dataclass
class Scene:
    spec: dict[str, Any]
    lines: list[Line]
    start: float = 0.0
    end: float = 0.0

    @property
    def kind(self) -> str:
        return str(self.spec.get("type", "statement"))

    def at(self, i: int) -> float:
        """i 番目の部品が出る時刻（i 行目の語りの頭。行が足りなければ最後の行）."""
        if not self.lines:
            return self.start
        i = max(0, min(i, len(self.lines) - 1))
        return self.lines[i].start


@dataclass
class Plan:
    scenes: list[Scene]
    duration: float
    sfx: list[tuple[float, str, float]] = field(default_factory=list)   # (時刻, 種類, 音量 dB)


_READ = [
    (re.compile(r"\$([\d,]+)"), lambda m: m.group(1).replace(",", "") + "ドル"),
    (re.compile(r"(\d),(\d{3})"), lambda m: m.group(1) + m.group(2)),
    (re.compile(r"@([A-Za-z0-9_.]+)"), lambda m: "投稿者"),
    (re.compile(r"TikTok"), lambda m: "ティックトック"),
]


def say_text(text: str, readings: dict[str, str]) -> str:
    s = plain(text)
    for k, v in readings.items():
        s = s.replace(k, v)
    for pat, rep in _READ:
        s = pat.sub(rep, s)
    return s


def synth_lines(spec: dict[str, Any], workdir: Path, synth: Callable[[str], bytes]) -> list[Scene]:
    readings = dict(spec.get("readings") or {})
    scenes = []
    n = 0
    for sc in spec["scenes"]:
        lines = []
        for t in sc.get("lines") or []:
            ln = Line(text=plain(t), say=say_text(t, readings))
            p = workdir / f"v{n:04d}.wav"
            n += 1
            if not p.exists():
                p.write_bytes(synth(ln.say))
            ln.wav = p
            lines.append(ln)
        scenes.append(Scene(spec=sc, lines=lines))
    return scenes


def wav_len(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


SFX_OF = {"chapter": ("WHOOSH", -9), "opening": ("RISER", -12), "number": ("POP", -12), "compare": ("POP", -14),
          "cta": ("CLICK", -8)}


def plan(scenes: list[Scene], gap: float = 0.16, tail: float = 0.35, lead: float = 0.25) -> Plan:
    t = 0.0
    sfx: list[tuple[float, str, float]] = []
    for sc in scenes:
        sc.start = t
        t += float(sc.spec.get("lead", lead))
        if sc.kind in ("chapter",):
            t += 0.5
        for ln in sc.lines:
            ln.start = t
            ln.end = t + wav_len(ln.wav)
            t = ln.end + gap
        if not sc.lines:
            t += float(sc.spec.get("hold", 2.0))
        t += float(sc.spec.get("tail", tail))
        sc.end = t
        # 効果音: 場面の頭 ＋ 部品が出るたび
        kind = sc.kind
        if kind in SFX_OF:
            name, db = SFX_OF[kind]
            sfx.append((sc.start + (0.0 if kind != "number" else lead), name, db))
        if any(st == "box" for _, st in runs(str(sc.spec.get("text", "")) + str(sc.spec.get("text2", "")))):
            sfx.append((sc.at(1 if sc.spec.get("text2") and "[[" in str(sc.spec.get("text2")) else 0) + 0.02, "IMPACT", -13))
        if kind in ("compare", "points", "steps", "checklist", "phone"):
            for i in range(1, len(sc.lines)):
                sfx.append((sc.at(i), "POP", -17))
    return Plan(scenes=scenes, duration=t, sfx=sfx)


# ----------------------------------------------------------------------
# 場面の描画
# ----------------------------------------------------------------------
class Renderer:
    def __init__(self, W: int = 1920, H: int = 1080, sub_size: int | None = None):
        self.W, self.H = W, H
        self.v = H > W                                  # 縦（Shorts）
        self.k = W / 1920 if not self.v else W / 1080   # 文字の大きさの基準
        self.sub_size = sub_size or (38 if not self.v else 64)
        self.bgs = {tone: background(W, H, tone) for tone in ("green", "red", "dark")}
        self._tiles: dict[Any, Image.Image] = {}

    # --- 共通 -----------------------------------------------------------
    def S(self, px: float) -> int:
        return max(1, int(px * (1.3 if self.v else self.k)))

    def tile(self, key, make: Callable[[], Image.Image]) -> Image.Image:
        if key not in self._tiles:
            self._tiles[key] = make()
        return self._tiles[key]

    def txt(self, text: str, size: float, **kw) -> Image.Image:
        size = self.S(size)
        return self.tile(("t", text, size, tuple(sorted(kw.items()))), lambda: text_tile(text, size, **kw))

    def fit(self, text: str, size: float, maxw: float, **kw) -> Image.Image:
        while size > 20:
            t = self.txt(text, size, **kw)
            if t.width <= maxw:
                return t
            size *= 0.92
        return self.txt(text, size, **kw)

    def subtitle(self, canvas: Image.Image, text: str) -> None:
        if not text:
            return
        f = font(self.sub_size, "bold")
        maxw = int(self.W * (0.86 if not self.v else 0.88))
        rows = wrap(text, f, maxw)
        lh = int(self.sub_size * 1.32)
        d = ImageDraw.Draw(canvas)
        base = self.H - int(self.H * (0.055 if not self.v else 0.2)) - lh * (len(rows) - 1)
        for i, r in enumerate(rows):
            w = f.getlength(r)
            x = (self.W - w) / 2
            y = base + i * lh - self.sub_size
            for dx, dy in ((0, 3), (2, 2), (-2, 2)):
                d.text((x + dx, y + dy), r, font=f, fill=(0, 0, 0, 200))
            d.text((x, y), r, font=f, fill=WHITE)

    def corner(self, canvas: Image.Image, source: str, date: str) -> None:
        d = ImageDraw.Draw(canvas)
        f = font(self.S(21), "bold")
        m = self.S(34)
        if source:
            d.text((m, m * 0.7), source, font=f, fill=GRAY)
        if date:
            w = f.getlength(date)
            d.text((self.W - m - w, m * 0.7), date, font=f, fill=GRAY)

    # --- 場面 -----------------------------------------------------------
    def frame(self, plan_: Plan, t: float) -> Image.Image:
        sc = next((s for s in plan_.scenes if s.start <= t < s.end), plan_.scenes[-1])
        spec = sc.spec
        tone = str(spec.get("bg", "red" if sc.kind in ("opening",) else "green"))
        canvas = self.bgs.get(tone, self.bgs["green"]).convert("RGBA")
        draw = getattr(self, "s_" + sc.kind, self.s_statement)
        draw(canvas, sc, t)
        self.corner(canvas, str(spec.get("source", "")), str(spec.get("date", "")))
        cur = next((ln for ln in sc.lines if ln.start - 0.05 <= t < ln.end + 0.16), None)
        if cur is None:
            prev = [ln for ln in sc.lines if ln.end <= t]
            cur = prev[-1] if prev and t - prev[-1].end < 0.4 else None
        if spec.get("subtitle", True) and cur is not None:
            self.subtitle(canvas, cur.text)
        return canvas.convert("RGB")

    def P(self, sc: Scene, i: int, t: float, dur: float = 0.35) -> float:
        return (t - sc.at(i)) / dur

    def s_statement(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        cx, cy = self.W / 2, self.H * (0.44 if not self.v else 0.4)
        items = []
        if s.get("small"):
            items.append((self.txt(str(s["small"]), 34, weight="bold", color=GRAY), 0))
        items.append((self.fit(str(s.get("text", "")), float(s.get("size", 92)), self.W * 0.86), 0))
        if s.get("text2"):
            items.append((self.fit(str(s["text2"]), float(s.get("size2", s.get("size", 92))), self.W * 0.86),
                          int(s.get("at2", 1))))
        if s.get("note"):
            items.append((self.txt(str(s["note"]), 30, weight="bold", color=GRAY), int(s.get("at_note", len(sc.lines) - 1))))
        total = sum(it.height for it, _ in items) + self.S(18) * (len(items) - 1)
        y = cy - total / 2
        for it, i in items:
            paste(c, it, cx, y + it.height / 2, self.P(sc, i, t))
            y += it.height + self.S(18)

    def s_opening(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        cx, cy = self.W / 2, self.H * 0.42
        lab = self.tile(("lab", s.get("label", "実話")), lambda: self._label(str(s.get("label", "実話"))))
        title = self.fit(str(s.get("title", "")), float(s.get("size", 76)), self.W * 0.88)
        paste(c, lab, cx, cy - title.height / 2 - self.S(40), self.P(sc, 0, t))
        paste(c, title, cx, cy, self.P(sc, 0, t) - 0.15)
        if s.get("sub"):
            sub = self.txt(str(s["sub"]), 28, weight="bold", color=GRAY)
            paste(c, sub, cx, cy + title.height / 2 + self.S(34), self.P(sc, 0, t) - 0.4)

    def _label(self, text: str) -> Image.Image:
        f = font(self.S(26), "black")
        w = int(f.getlength(text)) + self.S(30)
        h = self.S(42)
        img = rounded((w, h), self.S(8), RED_BOX + (255,))
        ImageDraw.Draw(img).text((w / 2, h / 2), text, font=f, fill=WHITE, anchor="mm")
        return img

    def s_chapter(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        idx, total = int(s.get("index", 1)), int(s.get("total", 5))
        cx, cy = self.W / 2, self.H * 0.42
        p = (t - sc.start) / 0.4
        lab = self.txt(f"第{idx}章", 30, color=RED)
        title = self.fit(str(s.get("title", "")), 80, self.W * 0.84)
        paste(c, lab, cx, cy - title.height / 2 - self.S(24), p)
        paste(c, title, cx, cy, p - 0.2)
        # 章の進み（点と線）
        d = ImageDraw.Draw(c)
        span = self.W * (0.36 if not self.v else 0.7)
        x0 = cx - span / 2
        y = cy + title.height / 2 + self.S(70)
        d.line([x0, y, x0 + span, y], fill=DIM, width=self.S(3))
        k = ease_out((t - sc.start - 0.2) / 0.9)
        prog = ((idx - 1) + k) / max(total, 1)
        d.line([x0, y, x0 + span * prog, y], fill=WHITE, width=self.S(4))
        for i in range(total + 1):
            x = x0 + span * i / total
            r = self.S(6)
            d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE if i <= idx - 1 + k else DIM)

    def s_number(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        cx, cy = self.W / 2, self.H * 0.42
        value = str(s.get("value", ""))
        p = self.P(sc, 0, t)
        shown = value
        m = re.search(r"[\d,]+(\.\d+)?", value)
        if s.get("count", True) and m:
            num = float(m.group(0).replace(",", ""))
            k = ease_out((t - sc.at(0)) / 0.9)
            cur = num * k
            dec = len(m.group(1)) - 1 if m.group(1) else 0
            txt = f"{cur:,.{dec}f}" if "," in m.group(0) else f"{cur:.{dec}f}"
            shown = value[:m.start()] + txt + value[m.end():]
        size = float(s.get("size", 150))
        big = text_tile(f"[{shown}]" if not shown.startswith("[") else shown, self.S(size))
        lab = self.txt(str(s.get("label", "")), 38, weight="bold") if s.get("label") else None
        note = self.txt(str(s.get("note", "")), 30, weight="bold", color=GRAY) if s.get("note") else None
        y = cy
        if lab:
            paste(c, lab, cx, y - big.height / 2 - self.S(20), p)
        paste(c, big, cx, y, p, pop=True)
        if note:
            paste(c, note, cx, y + big.height / 2 + self.S(22), self.P(sc, int(s.get("at_note", 1)), t))

    def s_compare(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        items = s.get("items") or []
        n = max(1, len(items))
        if self.v:
            pos = [(self.W / 2, self.H * (0.25 + 0.42 * i / max(n - 1, 1))) for i in range(n)] if n > 1 else [(self.W / 2, self.H * 0.42)]
        else:
            pos = [(self.W * (i + 1) / (n + 1), self.H * 0.44) for i in range(n)]
        for i, (it, (x, y)) in enumerate(zip(items, pos)):
            p = self.P(sc, int(it.get("at", i)), t)
            lab = self.txt(str(it.get("label", "")), 34, weight="bold", color=GRAY)
            val = self.fit(str(it.get("value", "")), float(s.get("size", 96)), (self.W / (n + 0.4)) if not self.v else self.W * 0.86)
            paste(c, lab, x, y - val.height / 2 - self.S(16), p)
            paste(c, val, x, y, p)
            if it.get("note"):
                paste(c, self.txt(str(it["note"]), 26, weight="bold", color=GRAY), x, y + val.height / 2 + self.S(14), p)

    def s_steps(self, c: Image.Image, sc: Scene, t: float) -> None:
        """ルール1・2・3 を 1 つずつ出し替える（左上に見出し、中央に大きく）."""
        s = sc.spec
        items = s.get("items") or []
        cur = 0
        for i in range(len(items)):
            if t >= sc.at(int(items[i].get("at", i))):
                cur = i
        it = items[cur] if items else {}
        p = self.P(sc, int(it.get("at", cur)), t)
        cx, cy = self.W / 2, self.H * 0.43
        head = self.txt(str(it.get("head", "")), 44, color=GRAY)
        body = self.fit(str(it.get("text", "")), float(s.get("size", 96)), self.W * 0.84)
        paste(c, head, cx, cy - body.height / 2 - self.S(30), p)
        paste(c, body, cx, cy, p - 0.1)
        if it.get("note"):
            paste(c, self.txt(str(it["note"]), 32, weight="bold", color=GRAY), cx, cy + body.height / 2 + self.S(26), p - 0.3)

    def s_grid(self, c: Image.Image, sc: Scene, t: float) -> None:
        """100 個のアイコンが、場面の長さに合わせて埋まっていく."""
        s = sc.spec
        n, cols = int(s.get("n", 100)), int(s.get("cols", 10 if not self.v else 10))
        rows = math.ceil(n / cols)
        area_w = self.W * (0.5 if not self.v else 0.86)
        area_h = self.H * (0.56 if not self.v else 0.4)
        cell = int(min(area_w / cols, area_h / rows))
        x0 = self.W / 2 - cell * cols / 2
        y0 = self.H * (0.45 if not self.v else 0.42) - cell * rows / 2
        a, b = float(s.get("from", 0)), float(s.get("to", n))
        i0 = int(s.get("start_line", 0))
        t0 = sc.at(i0)
        t1 = sc.lines[-1].end if sc.lines else sc.end
        k = ease_out((t - t0) / max(0.5, (t1 - t0) * 0.85)) if t >= t0 else 0.0
        filled = int(round(a + (b - a) * k))
        hl = set(int(x) for x in (s.get("highlight") or []))
        icon = icon_envelope if s.get("icon", "envelope") == "envelope" else icon_person
        appear = min(1.0, (t - sc.start) / 0.5)
        for j in range(n):
            r, q = divmod(j, cols)
            on = j < filled
            im = self.tile(("ic", icon.__name__, cell, on, (j + 1) in hl), lambda: icon(int(cell * 0.86), on, (j + 1) in hl))
            if appear < 1:
                im = im.copy()
                im.putalpha(im.getchannel("A").point(lambda v: int(v * appear)))
            c.alpha_composite(im, (int(x0 + q * cell + cell * 0.07), int(y0 + r * cell + cell * 0.07)))
        if s.get("label"):
            lab = self.txt(str(s["label"]).replace("{n}", f"{filled}"), 40, weight="bold")
            paste(c, lab, self.W / 2, y0 - self.S(46), 1.0, pop=False)
        if s.get("side"):
            side = self.fit(str(s["side"]), 70, self.W * (0.2 if not self.v else 0.8))
            sx = x0 + cell * cols + (self.W - (x0 + cell * cols)) / 2 if not self.v else self.W / 2
            sy = self.H * 0.45 if not self.v else y0 + cell * rows + self.S(110)
            paste(c, side, sx, sy, self.P(sc, int(s.get("at_side", 1)), t))

    def s_bars(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        vals = [float(v) for v in s.get("values") or []]
        labs = [str(x) for x in s.get("labels") or []]
        tops = [str(x) for x in s.get("tops") or []]
        n = max(1, len(vals))
        vmax = max(vals) if vals else 1
        area_w = self.W * (0.56 if not self.v else 0.84)
        area_h = self.H * (0.46 if not self.v else 0.34)
        x0 = self.W / 2 - area_w / 2
        base = self.H * (0.7 if not self.v else 0.62)
        bw = area_w / n * 0.62
        d = ImageDraw.Draw(c)
        d.line([x0 - 10, base, x0 + area_w + 10, base], fill=DIM, width=self.S(2))
        for i, v in enumerate(vals):
            ti = sc.at(int((s.get("at") or list(range(n)))[i])) if s.get("at") else sc.start + 0.3 + i * 0.35
            k = ease_out((t - ti) / 0.6)
            if k <= 0:
                continue
            h = area_h * v / vmax * k
            x = x0 + area_w * (i + 0.5) / n - bw / 2
            col = _grad(i / max(n - 1, 1))
            d.rectangle([x, base - h, x + bw, base], fill=col)
            if i < len(labs):
                lt = self.txt(labs[i], 24, weight="bold", color=GRAY)
                c.alpha_composite(lt, (int(x + bw / 2 - lt.width / 2), int(base + self.S(8))))
            if i < len(tops) and k > 0.6:
                tt = self.txt(tops[i], 26, weight="bold")
                c.alpha_composite(tt, (int(x + bw / 2 - tt.width / 2), int(base - h - tt.height - 2)))
        if s.get("title"):
            tt = self.fit(str(s["title"]), 58, self.W * 0.8)
            paste(c, tt, self.W / 2, base - area_h - self.S(70), self.P(sc, 0, t))
        if s.get("side"):
            side = self.fit(str(s["side"]), 64, self.W * 0.3 if not self.v else self.W * 0.8)
            i = int(s.get("at_side", len(sc.lines) - 1))
            if self.v:
                paste(c, side, self.W / 2, base + self.S(120), self.P(sc, i, t))
            else:
                paste(c, side, x0 + area_w * 0.25, base - area_h * 0.75, self.P(sc, i, t))

    def s_hbars(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        items = s.get("items") or []
        vmax = max((float(it.get("value", 0)) for it in items), default=1) or 1
        w = self.W * (0.5 if not self.v else 0.62)
        x0 = self.W / 2 - w / 2 + (self.S(60) if not self.v else self.S(110))
        hh = self.S(46 if not self.v else 60)
        y0 = self.H * 0.42 - (len(items) - 1) * hh * 1.1
        d = ImageDraw.Draw(c)
        for i, it in enumerate(items):
            ti = sc.at(int(it.get("at", i)))
            k = ease_out((t - ti) / 0.7)
            y = y0 + i * hh * 2.2
            lt = self.txt(str(it.get("label", "")), 30, weight="bold", color=GRAY)
            c.alpha_composite(lt, (int(x0 - lt.width - self.S(18)), int(y + hh / 2 - lt.height / 2)))
            d.rectangle([x0, y, x0 + w, y + hh], fill=(30, 38, 33))
            if k > 0:
                col = RED if it.get("hl") else LIME
                d.rectangle([x0, y, x0 + w * float(it.get("value", 0)) / vmax * k, y + hh], fill=col)
                vt = self.txt(str(it.get("text", "")), 28, color=(10, 14, 11), shadow=False)
                c.alpha_composite(vt, (int(x0 + self.S(10)), int(y + hh / 2 - vt.height / 2)))

    def s_calendar(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        p = self.P(sc, 0, t)
        card = self.tile(("cal", s.get("top"), s.get("day")), lambda: self._calendar(str(s.get("top", "")), str(s.get("day", ""))))
        if self.v:
            paste(c, card, self.W / 2, self.H * 0.33, p)
            if s.get("side"):
                paste(c, self.fit(str(s["side"]), 96, self.W * 0.86), self.W / 2, self.H * 0.56, self.P(sc, int(s.get("at_side", 1)), t))
        else:
            paste(c, card, self.W * 0.4, self.H * 0.43, p)
            if s.get("side"):
                paste(c, self.fit(str(s["side"]), 96, self.W * 0.36), self.W * 0.64, self.H * 0.43, self.P(sc, int(s.get("at_side", 1)), t))

    def _calendar(self, top: str, day: str) -> Image.Image:
        w, h = self.S(250), self.S(270)
        img = rounded((w, h), self.S(18), (246, 246, 244, 255))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, w - 1, self.S(64)], self.S(18), fill=RED_BOX)
        d.rectangle([0, self.S(40), w - 1, self.S(64)], fill=RED_BOX)
        d.text((w / 2, self.S(33)), top, font=font(self.S(28), "black"), fill=WHITE, anchor="mm")
        f = font(self.S(130 if len(day) <= 2 else 96), "black")
        d.text((w / 2, self.S(64) + (h - self.S(64)) / 2), day, font=f, fill=(20, 22, 21), anchor="mm")
        return img

    def s_phone(self, c: Image.Image, sc: Scene, t: float) -> None:
        """ショート動画の引用: 右に縦長の投稿カード、背景にそのぼかし、左にテロップ."""
        s = sc.spec
        card = self.tile(("ph", s.get("handle"), s.get("quote"), s.get("meta")),
                         lambda: self._phone(str(s.get("handle", "")), str(s.get("quote", "")), str(s.get("meta", ""))))
        blur = self.tile(("phb", s.get("handle"), s.get("quote")), lambda: self._blur_of(card))
        c.alpha_composite(blur, (0, 0))
        p = self.P(sc, 0, t)
        if self.v:
            paste(c, card, self.W / 2, self.H * 0.4, p, slide=self.S(40))
        else:
            paste(c, card, self.W * 0.66, self.H * 0.46, p, slide=self.S(40))
        tel = s.get("telops") or ([s["telop"]] if s.get("telop") else [])
        cur = None
        for i, tx in enumerate(tel):
            if t >= sc.at(i):
                cur = (i, tx)
        if cur:
            i, tx = cur
            tt = self.txt(str(tx), float(s.get("size", 62)), align="left")
            if self.v:
                paste(c, tt, self.W / 2, self.H * 0.7, self.P(sc, i, t))
            else:
                paste(c, tt, self.W * 0.06 + tt.width / 2, self.H * 0.4, self.P(sc, i, t))

    def _phone(self, handle: str, quote: str, meta: str) -> Image.Image:
        w, h = self.S(400), self.S(700)
        img = rounded((w, h), self.S(26), (14, 16, 15, 255), outline=(70, 76, 72, 255), width=self.S(3))
        d = ImageDraw.Draw(img)
        pad = self.S(28)
        d.ellipse([pad, pad, pad + self.S(54), pad + self.S(54)], fill=(60, 66, 62))
        d.text((pad + self.S(70), pad + self.S(27)), handle, font=font(self.S(24), "bold"), fill=WHITE, anchor="lm")
        d.text((pad, pad + self.S(76)), "ショート動画", font=font(self.S(19), "bold"), fill=GRAY)
        f = font(self.S(34), "black")
        y = self.S(190)
        for ln in wrap(quote, f, w - pad * 2):
            d.text((pad, y), ln, font=f, fill=WHITE)
            y += int(self.S(34) * 1.45)
        if meta:
            d.text((pad, h - pad - self.S(30)), meta, font=font(self.S(22), "bold"), fill=LIME)
        d.polygon([(w / 2 - self.S(16), h * 0.86 - self.S(20)), (w / 2 - self.S(16), h * 0.86 + self.S(20)),
                   (w / 2 + self.S(20), h * 0.86)], fill=(255, 255, 255, 70))
        return img

    def _blur_of(self, card: Image.Image) -> Image.Image:
        big = card.resize((self.W, int(self.W * card.height / card.width)) if not self.v else (int(self.H * card.width / card.height) * 2, self.H * 2))
        big = big.crop(((big.width - self.W) // 2, (big.height - self.H) // 2, (big.width - self.W) // 2 + self.W,
                        (big.height - self.H) // 2 + self.H)).filter(ImageFilter.GaussianBlur(self.S(70)))
        a = big.getchannel("A").point(lambda v: int(v * 0.3))
        big.putalpha(a)
        return big

    def s_article(self, c: Image.Image, sc: Scene, t: float) -> None:
        """記事・ブログの引用: 白いカードに本文、引用部分に黄色のマーカー."""
        s = sc.spec
        card = self.tile(("ar", s.get("title"), s.get("text")), lambda: self._article(str(s.get("title", "")), str(s.get("text", ""))))
        p = self.P(sc, 0, t)
        paste(c, card, self.W / 2, self.H * (0.36 if not self.v else 0.36), p, slide=self.S(30))
        if s.get("telop"):
            tt = self.txt(str(s["telop"]), 54)
            paste(c, tt, self.W / 2, self.H * 0.36 + card.height / 2 + tt.height / 2 + self.S(30), self.P(sc, int(s.get("at_telop", 1)), t))

    def _article(self, title: str, text: str) -> Image.Image:
        w = self.S(1100 if not self.v else 940)
        pad = self.S(48)
        ft, fb = font(self.S(30), "black"), font(self.S(34), "bold")
        rows = wrap(text, fb, w - pad * 2)
        h = pad * 2 + self.S(60) + len(rows) * int(self.S(34) * 1.7)
        img = rounded((w, h), self.S(10), (240, 240, 236, 255))
        d = ImageDraw.Draw(img)
        d.text((pad, pad), title, font=ft, fill=(90, 96, 92))
        y = pad + self.S(66)
        for r in rows:
            tw = fb.getlength(r)
            d.rectangle([pad - 4, y + self.S(18), pad + tw + 4, y + self.S(42)], fill=(255, 226, 90))
            d.text((pad, y), r, font=fb, fill=(24, 26, 25))
            y += int(self.S(34) * 1.7)
        return img

    def s_points(self, c: Image.Image, sc: Scene, t: float) -> None:
        """「3つの注意」: 見出し＋伏せたカード。i 行目でカード i が開く（最後に大きく出す）."""
        s = sc.spec
        items = [str(x) for x in s.get("items") or []]
        n = max(1, len(items))
        head = self.txt(str(s.get("head", "")), 64)
        hy = self.H * (0.2 if not self.v else 0.18)
        paste(c, head, self.W / 2, hy, self.P(sc, 0, t))
        cw = (self.W * 0.8 / n) * 0.9 if not self.v else self.W * 0.84
        ch = self.S(300 if not self.v else 220)
        for i, it in enumerate(items):
            if self.v:
                x, y = self.W / 2, hy + self.S(240) + i * (ch + self.S(30))
            else:
                x, y = self.W * 0.1 + (self.W * 0.8) * (i + 0.5) / n, self.H * 0.52
            opened = t >= sc.at(i + 1)
            key = ("pt", i, it, opened, int(cw), ch)
            card = self.tile(key, lambda: self._point_card(i + 1, it if opened else "？", int(cw), ch, opened))
            paste(c, card, x, y, self.P(sc, i + 1 if opened else 0, t))

    def _point_card(self, num: int, text: str, w: int, h: int, opened: bool) -> Image.Image:
        img = rounded((w, h), self.S(14), CARD + (255,), outline=(RED if opened else CARD_LINE) + (255,), width=self.S(3))
        d = ImageDraw.Draw(img)
        d.text((self.S(22), self.S(16)), f"{num}", font=font(self.S(30), "black"), fill=RED if opened else GRAY)
        f = font(self.S(40 if opened else 70), "black")
        rows = wrap(text, f, w - self.S(50))
        lh = int(f.size * 1.3)
        y = h / 2 - lh * len(rows) / 2
        for r in rows:
            d.text((w / 2, y + lh / 2), r, font=f, fill=WHITE if opened else DIM, anchor="mm")
            y += lh
        return img

    def s_checklist(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        items = [str(x) for x in s.get("items") or []]
        if s.get("head"):
            paste(c, self.txt(str(s["head"]), 40, weight="bold", color=GRAY), self.W / 2, self.H * 0.24, self.P(sc, 0, t))
        lh = self.S(96)
        y0 = self.H * 0.42 - lh * (len(items) - 1) / 2
        for i, it in enumerate(items):
            p = self.P(sc, int(s.get("offset", 0)) + i, t)
            hl = i in [int(x) for x in s.get("hl") or []]
            tt = self.txt(("[" + it + "]") if hl else it, 54, align="left")
            dot = self.tile(("dot", hl), lambda: _dot(self.S(26), RED if hl else LIME))
            x = self.W * (0.28 if not self.v else 0.1)
            if p > 0:
                paste(c, dot, x, y0 + i * lh, p, pop=False)
                paste(c, tt, x + self.S(36) + tt.width / 2, y0 + i * lh, p, pop=False, slide=self.S(12))

    def s_cta(self, c: Image.Image, sc: Scene, t: float) -> None:
        s = sc.spec
        cx, cy = self.W / 2, self.H * 0.44
        clicked = t >= sc.at(int(s.get("at_click", 1)))
        btn = self.tile(("btn", clicked), lambda: self._button("登録済み" if clicked else "チャンネル登録", clicked))
        paste(c, btn, cx, cy, self.P(sc, 0, t))
        if s.get("text"):
            paste(c, self.txt(str(s["text"]), 40, weight="bold"), cx, cy + btn.height / 2 + self.S(46), self.P(sc, 0, t) - 0.3)
        # 矢印のカーソル
        k = ease_out((t - sc.at(0) - 0.3) / 0.8)
        d = ImageDraw.Draw(c)
        x = cx + self.S(160) - self.S(100) * k
        y = cy + self.S(90) - self.S(60) * k
        r = self.S(30)
        d.polygon([(x, y), (x, y + r * 1.3), (x + r * 0.38, y + r * 0.95), (x + r * 0.62, y + r * 1.45),
                   (x + r * 0.8, y + r * 1.36), (x + r * 0.56, y + r * 0.86), (x + r, y + r * 0.86)],
                  fill=WHITE, outline=(0, 0, 0))

    def _button(self, text: str, clicked: bool) -> Image.Image:
        f = font(self.S(40), "black")
        w, h = int(f.getlength(text)) + self.S(90), self.S(88)
        img = rounded((w, h), h // 2, ((70, 76, 72) if clicked else (245, 245, 243)) + (255,))
        ImageDraw.Draw(img).text((w / 2, h / 2), text, font=f, fill=WHITE if clicked else (16, 18, 17), anchor="mm")
        return img


def _dot(s: int, col) -> Image.Image:
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([0, 0, s - 1, s - 1], fill=col)
    return img


def _grad(k: float):
    """黄緑 → 黄 → 赤."""
    stops = [(0.0, LIME), (0.55, YELLOW), (1.0, RED)]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if k <= b:
            u = (k - a) / (b - a)
            return tuple(int(ca[j] + (cb[j] - ca[j]) * u) for j in range(3))
    return RED


# ----------------------------------------------------------------------
# 音
# ----------------------------------------------------------------------
def _read_wav(p: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(p)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() == 2:
            x = x.reshape(-1, 2).mean(axis=1)
    return x, sr


def voice_track(plan_: Plan, out: Path, sr: int = 24000) -> Path:
    buf = np.zeros(int((plan_.duration + 1) * sr), np.float32)
    for sc in plan_.scenes:
        for ln in sc.lines:
            x, r = _read_wav(ln.wav)
            if r != sr:
                x = np.interp(np.arange(0, len(x), r / sr), np.arange(len(x)), x).astype(np.float32)
            i = int(ln.start * sr)
            buf[i:i + len(x)] += x[: len(buf) - i]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(buf, -1, 1) * 32767).astype(np.int16).tobytes())
    return out


def mix(ffmpeg: str, voice: Path, plan_: Plan, sfx_dir: Path, bgm: Path | None, out: Path,
        bgm_db: float = -21.0) -> Path:
    files = {p.stem.upper(): p for p in sfx_dir.glob("*.mp3")} if sfx_dir.exists() else {}
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(voice)]
    filters = ["[0:a]aresample=48000,aformat=channel_layouts=stereo[v]"]
    labels = ["[v]"]
    k = 1
    if bgm and bgm.exists():
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        filters.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={bgm_db}dB,"
                       f"atrim=0:{plan_.duration:.2f},afade=t=out:st={max(0, plan_.duration - 3):.2f}:d=3[b]")
        labels.append("[b]")
        k += 1
    for j, (t, name, db) in enumerate(plan_.sfx):
        p = files.get(name)
        if not p:
            continue
        cmd += ["-i", str(p)]
        ms = int(max(0, t) * 1000)
        filters.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={db}dB,adelay={ms}|{ms}[s{j}]")
        labels.append(f"[s{j}]")
        k += 1
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=first,alimiter=limit=0.95[out]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)
    return out


# ----------------------------------------------------------------------
# 書き出し
# ----------------------------------------------------------------------
def render(spec: dict[str, Any], out: Path, workdir: Path, synth: Callable[[str], bytes], *,
           vertical: bool = False, ffmpeg: str = "ffmpeg", sfx_dir: Path | None = None,
           bgm: Path | None = None, bgm_db: float = -21.0, size: tuple[int, int] | None = None,
           preview: tuple[float, float] | None = None) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    scenes = synth_lines(spec, workdir, synth)
    pl = plan(scenes)
    W, H = size or ((1080, 1920) if vertical else (1920, 1080))
    r = Renderer(W, H)
    voice = voice_track(pl, workdir / "voice.wav")
    audio = mix(ffmpeg, voice, pl, sfx_dir or (_ROOT / "assets" / "sfx"), bgm, workdir / "audio.m4a", bgm_db)
    t0, t1 = preview or (0.0, pl.duration)
    n = int((t1 - t0) * FPS)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(audio),
           "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin
    for f in range(n):
        proc.stdin.write(r.frame(pl, t0 + f / FPS).tobytes())
    proc.stdin.close()
    proc.wait()
    (workdir / "plan.json").write_text(json.dumps(
        [{"type": s.kind, "start": round(s.start, 2), "end": round(s.end, 2)} for s in pl.scenes], ensure_ascii=False, indent=1))
    log.info("書き出し: %s（%.1f 秒）", out, pl.duration)
    return out
