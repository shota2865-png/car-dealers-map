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

try:                                    # 変形・合成を速くする（無ければ PIL で同じことをする）
    import cv2
except Exception:                       # pragma: no cover
    cv2 = None

log = logging.getLogger(__name__)

FPS = 60
TRANSITION = 0.3                        # 場面のつなぎ（前の場面がズームしながら溶けて、次が奥から来る）
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


def _char_tile(ch: str, f: ImageFont.FreeTypeFont, color, shadow: bool = True) -> Image.Image:
    """キネティック・タイポグラフィ用の 1 字."""
    w = int(f.getlength(ch)) + 12
    h = int(f.size * 1.45)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shadow:
        d.text((8, 9), ch, font=f, fill=(0, 0, 0, 150))
    d.text((6, 6), ch, font=f, fill=color)
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


SFX_OF = {"chapter": ("WHOOSH", -9), "opening": ("RISER", -12), "compare": ("POP", -14),
          "kinetic": ("TYPE", -17), "modal": ("POP2", -11), "radio": ("POP", -16), "carousel": ("PAGE", -12),
          "phone": ("PAGE", -12), "footage": ("PAGE", -13), "menu": ("SWOOSH", -14), "steps": ("SWOOSH", -15),
          "bars3d": ("RISER", -16), "grid": ("SWOOSH", -16), "accordion": ("SWOOSH", -16), "pulldown": ("SWOOSH", -17),
          "cta": ("SWOOSH", -15), "statement": ("SWOOSH", -18), "anim": ("SWOOSH", -18), "checklist": ("SWOOSH", -17)}
_MONEY = re.compile(r"[円$＄]|ドル")


def plan(scenes: list[Scene], gap: float = 0.07, tail: float = 0.16, lead: float = 0.1) -> Plan:
    """語りの時刻を決め、効果音を置く（テンポ優先: 行の間 0.07 秒・場面の頭 0.1 秒）.

    効果音は場面の頭・部品が出るたび・数字が出たとき・正解/不正解・押したとき・お金の動き、に多めに置く。
    """
    t = 0.0
    sfx: list[tuple[float, str, float]] = []
    for sc in scenes:
        sc.start = t
        t += float(sc.spec.get("lead", lead))
        if sc.kind in ("chapter",):
            t += 0.25
        for ln in sc.lines:
            ln.start = t
            ln.end = t + wav_len(ln.wav)
            t = ln.end + gap
        if not sc.lines:
            t += float(sc.spec.get("hold", 2.0))
        t += float(sc.spec.get("tail", tail))
        sc.end = t
        s = sc.spec
        kind = sc.kind
        n = len(sc.lines)
        if kind in SFX_OF:
            name, db = SFX_OF[kind]
            sfx.append((sc.start, name, db))
        if kind == "chapter":
            sfx.append((sc.start + 0.35, "IMPACT", -15))
        if any(st == "box" for _, st in runs(str(s.get("text", "")) + str(s.get("text2", "")))):
            sfx.append((sc.at(1 if s.get("text2") and "[[" in str(s.get("text2")) else 0) + 0.02, "IMPACT", -13))
        if kind == "statement" and s.get("text2"):
            sfx.append((sc.at(int(s.get("at2", 1))), "POP", -15))
        if kind == "number":                                  # 数え上げ → 止まったところで鳴らす
            sfx.append((sc.at(0), "RISER", -18))
            hit = "COIN" if _MONEY.search(str(s.get("value", ""))) else "IMPACT"
            sfx.append((sc.at(0) + 0.9, hit, -11))
        if kind in ("compare", "points", "steps", "checklist", "phone", "accordion", "kinetic", "menu"):
            for i in range(1, n):
                sfx.append((sc.at(i), "POP", -16))
        if kind == "compare":
            for i, it in enumerate(s.get("items") or []):
                if _MONEY.search(str(it.get("value", ""))):
                    sfx.append((sc.at(int(it.get("at", i))) + 0.1, "COIN", -16))
        if kind == "grid":                                    # 埋まっていく間、小さく刻む
            t0 = sc.at(int(s.get("start_line", 0)))
            t1 = sc.lines[-1].end if sc.lines else sc.end
            k = t0
            while k < min(t1, t0 + 2.4):
                sfx.append((k, "TICK", -20))
                k += 0.16
        if kind == "bars3d":
            for i in range(len(s.get("values") or [])):
                at = int((s.get("at") or list(range(99)))[i])
                sfx.append((sc.at(at) + 0.55, "COIN" if (s.get("tops") and _MONEY.search(str(s["tops"][i]))) else "POP", -15))
        if kind in ("carousel",):
            for i, it in enumerate(s.get("items") or []):
                if i:
                    sfx.append((sc.at(int(it.get("at", i))), "SWOOSH", -14))
        for tc in click_times(sc):
            sfx.append((tc, "CLICK", -9))
        if kind == "cta":
            sfx.append((click_times(sc)[0] + 0.12, "DING", -13))
        if kind == "anim":
            pre = str(s.get("preset", ""))
            if pre == "jar_fill":
                sfx += [(sc.at(i) + 0.62, "COIN", -11) for i in range(n)]
            elif pre == "coin_stacks":
                sfx += [(sc.at(int((s.get("at") or list(range(99)))[i])) + 0.4, "COIN", -13) for i in range(len(s.get("values") or []))]
            elif pre == "think":
                sfx += [(sc.at(i), "POP2", -13) for i in range(n)]
            elif pre == "flow":
                sfx += [(sc.at(i), "POP", -14) for i in range(n)] + [(sc.at(0) + 0.3, "COIN", -17)]
            elif pre == "walk":
                sfx += [(sc.at(i) + 0.2, "TICK", -16) for i in range(n)]
            elif pre == "risk":
                sfx += [(sc.at(i), "IMPACT", -13) for i in range(1, n)] + [(sc.at(i) + 0.15, "BUZZ", -19) for i in range(1, n)]
        if kind == "radio" and s.get("answer") is not None:
            ta = sc.at(int(s.get("at_answer", 2)))
            sfx.append((ta, "DING", -11))
            if int(s.get("choose", -1)) != int(s["answer"]):
                sfx.append((ta + 0.05, "BUZZ", -17))
            sfx.append((sc.at(0) + 0.3, "SUSPENSE", -22))
        if kind == "modal" and s.get("close", True):
            sfx.append((click_times(sc)[0] + 0.1, "SWOOSH", -16))
    return Plan(scenes=scenes, duration=t, sfx=sfx)



# ----------------------------------------------------------------------
# 映像を 1 コマずつ読む（背景のループ・実写・引用の映像）
# ----------------------------------------------------------------------
class Stream:
    """ffmpeg で mp4 を W×H・fps に直して、前から順に読む。前に戻るときは読み直す."""

    def __init__(self, path: Path, W: int, H: int, fps: int, loop: bool = False, ffmpeg: str = "ffmpeg"):
        self.path, self.W, self.H, self.fps, self.loop, self.ffmpeg = Path(path), W, H, fps, loop, ffmpeg
        self.proc: subprocess.Popen | None = None
        self.idx = -1
        self.last: np.ndarray | None = None

    def _open(self) -> None:
        if self.proc:
            self.proc.kill()
        cmd = [self.ffmpeg, "-v", "error"] + (["-stream_loop", "-1"] if self.loop else []) + [
            "-i", str(self.path), "-vf",
            f"scale={self.W}:{self.H}:force_original_aspect_ratio=increase,crop={self.W}:{self.H},fps={self.fps}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.idx = -1

    def get(self, i: int) -> np.ndarray | None:
        i = max(0, i)
        if self.proc is None or i < self.idx:
            self._open()
        n = self.W * self.H * 3
        while self.idx < i:
            assert self.proc and self.proc.stdout
            buf = self.proc.stdout.read(n)
            if len(buf) < n:                       # 終わり: 最後のコマのまま止める
                break
            self.last = np.frombuffer(buf, np.uint8).reshape(self.H, self.W, 3)
            self.idx += 1
        return self.last

    def close(self) -> None:
        if self.proc:
            self.proc.kill()
            self.proc = None


# ----------------------------------------------------------------------
# 場面の描画
# ----------------------------------------------------------------------
class Renderer:
    def __init__(self, W: int = 1920, H: int = 1080, sub_size: int | None = None, fps: int = FPS):
        self.W, self.H = W, H
        self.fps = fps
        self._streams: dict[str, Stream] = {}
        self.v = H > W                                  # 縦（Shorts）
        self.k = W / 1920 if not self.v else W / 1080   # 文字の大きさの基準
        self.sub_size = sub_size or (50 if not self.v else 72)
        self.ts = 1.22                                  # テロップ全体の大きさ（見やすさ優先で大きめ）
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
        size = self.S(size * self.ts)
        return self.tile(("t", text, size, tuple(sorted(kw.items()))), lambda: text_tile(text, size, **kw))

    def fit(self, text: str, size: float, maxw: float, **kw) -> Image.Image:
        while size > 20:
            t = self.txt(text, size, **kw)
            if t.width <= maxw:
                return t
            size *= 0.92
        return self.txt(text, size, **kw)

    def subtitle(self, canvas: Image.Image, text: str, alpha: float = 1.0) -> None:
        if not text:
            return
        key = ("sub", text)
        tile = self._tiles.get(key)
        if tile is None:
            f = font(self.sub_size, "bold")
            maxw = int(self.W * (0.86 if not self.v else 0.88))
            rows = wrap(text, f, maxw)
            lh = int(self.sub_size * 1.32)
            tile = Image.new("RGBA", (self.W, lh * len(rows) + self.sub_size), (0, 0, 0, 0))
            d = ImageDraw.Draw(tile)
            for i, r in enumerate(rows):                         # 行ごとに半透明の黒い帯＋縁取りで、どの背景でも読める
                w = f.getlength(r)
                x = (self.W - w) / 2
                y = i * lh + 4
                pad = self.sub_size * 0.35
                d.rounded_rectangle([x - pad, y - pad * 0.35, x + w + pad, y + self.sub_size * 1.18], int(pad * 0.6), fill=(0, 0, 0, 150))
                d.text((x, y), r, font=f, fill=WHITE, stroke_width=max(2, self.sub_size // 16), stroke_fill=(0, 0, 0))
            if len(self._tiles) > 4000:
                self._tiles.clear()
            self._tiles[key] = tile
        nrows = max(1, round((tile.height - self.sub_size) / int(self.sub_size * 1.32)))
        base = self.H - int(self.H * (0.055 if not self.v else 0.2)) - int(self.sub_size * 1.32) * (nrows - 1) - self.sub_size - 4
        t2 = tile
        if alpha < 0.999:
            t2 = tile.copy()
            t2.putalpha(tile.getchannel("A").point(lambda v: int(v * alpha)))
        canvas.alpha_composite(t2, (0, int(base + (1 - alpha) * 6)))

    def corner(self, canvas: Image.Image, source: str, date: str) -> None:
        d = ImageDraw.Draw(canvas)
        f = font(self.S(26), "bold")
        m = self.S(34)
        if source:
            d.text((m, m * 0.7), source, font=f, fill=GRAY)
        if date:
            w = f.getlength(date)
            d.text((self.W - m - w, m * 0.7), date, font=f, fill=GRAY)

    # --- 場面 -----------------------------------------------------------
    def compose(self, sc: Scene, t: float) -> Image.Image:
        """その場面の t の絵（背景＋カメラで動かした前景）。字幕と隅の文字は frame で載せる."""
        spec = sc.spec
        tone = str(spec.get("bg", DEFAULT_BG.get(sc.kind, "green")))
        if sc.kind == "footage" and not (spec.get("clip") and Path(str(spec["clip"])).exists()):
            tone = str(spec.get("bg", "green"))                       # 映像が無いときは宇宙にしない
        canvas = self.backdrop(tone, t)
        self._bgc = canvas                       # カメラで動かさない背景（引用の投稿のぼかしなど）はここに描く
        fg = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        draw = getattr(self, "s_" + sc.kind, self.s_statement)
        draw(fg, sc, t)
        self.camera(canvas, fg, sc, t, dots=tone in ("green", "red", "dark"))
        return canvas

    def backdrop(self, tone: str, t: float) -> Image.Image:
        """背景。green / red / dark は無地、space は星空と光る床、tesseract は 4D の立体入り（assets/space）."""
        if tone in ("space", "tesseract"):
            path = _ROOT / "assets" / "space" / ("loop_b.mp4" if tone == "tesseract" else "loop_a.mp4")
            if path.exists():
                st = self._streams.get(tone)
                if st is None:
                    st = self._streams[tone] = Stream(path, self.W, self.H, self.fps, loop=True)
                fr = st.get(int(round(t * self.fps)))
                if fr is not None:
                    img = Image.fromarray(fr, "RGB").convert("RGBA")
                    img.alpha_composite(self.tile(("veil",), lambda: _veil(self.W, self.H)))
                    return img
            tone = "dark"
        return self.bgs.get(tone, self.bgs["green"]).convert("RGBA")

    def frame(self, plan_: Plan, t: float) -> Image.Image:
        scenes = plan_.scenes
        i = next((k for k, s_ in enumerate(scenes) if s_.start <= t < s_.end), len(scenes) - 1)
        sc = scenes[i]
        img = self.compose(sc, t)
        if i > 0 and t < sc.start + TRANSITION and not sc.spec.get("cut"):
            k = ease_inout((t - sc.start) / TRANSITION)
            prev = self.compose(scenes[i - 1], t)
            img = _blend_push(prev, img, k)
        spec = sc.spec
        self.corner(img, str(spec.get("source", "")), str(spec.get("date", "")))
        if sc.kind == "chapter":
            self.breadcrumb(img, sc, t)
        if spec.get("subtitle", True):
            for ln in sc.lines:
                a = min(1.0, (t - ln.start + 0.06) / 0.12, (ln.end + 0.2 - t) / 0.12)
                if a > 0:
                    self.subtitle(img, ln.text, a)
                    break
        return img.convert("RGB")

    # --- カメラ ---------------------------------------------------------
    def camera(self, canvas: Image.Image, fg: Image.Image, sc: Scene, t: float, dots: bool = True) -> None:
        """前景（テロップ・図）と奥の点の層を、別の量だけ動かして奥行きを出す.

        dolly_in 寄る / pan 横に振る / tilt 縦に振る / track 並走（奥だけ速く流れる）/
        crane 上がりながら全体へ / orbit 回り込む / dolly_zoom 被写体はそのまま奥だけ伸びる（めまい）/ still
        """
        cam = str(sc.spec.get("cam") or DEFAULT_CAM.get(sc.kind, "dolly_in"))
        u = (t - sc.start) / max(0.01, sc.end - sc.start)
        e = u * u * (3 - 2 * u) if 0 <= u <= 1 else (0.0 if u < 0 else 1.0)
        S = self.S
        fs, fx, fy, ang = 1.0, 0.0, 0.0, 0.0
        ds, dx, dy = 1.0, 0.0, 0.0
        if cam == "dolly_in":
            fs, ds = 1 + 0.06 * e, 1 + 0.02 * e
            fx = S(10) * math.sin(u * 3.1)               # 手持ちのような小さな揺れ（止まって見えない）
        elif cam == "pan":
            fx, dx = S(60) - S(120) * e, S(22) - S(44) * e
        elif cam == "tilt":
            fy, dy = S(45) - S(90) * e, S(18) - S(36) * e
        elif cam == "track":
            fx, dx, fs = S(8) * math.sin(u * 6.28), -S(520) * e, 1.02
        elif cam == "crane":
            fs, fy = 1.16 - 0.16 * e, S(110) * (1 - e)
            ds, dy = 1.1 - 0.1 * e, S(260) * (1 - e)
        elif cam == "orbit":
            ang, dx = -13 + 26 * e, S(90) - S(180) * e
        elif cam == "dolly_zoom":
            ds = 1 + 0.5 * e
        if dots:
            canvas.alpha_composite(_affine(self._dots_layer(), self.W, self.H, ds, dx, dy))
        if ang:
            canvas.alpha_composite(_orbit(fg, ang))
        elif abs(fs - 1) > 1e-3 or abs(fx) > 0.5 or abs(fy) > 0.5:
            canvas.alpha_composite(_affine(fg, self.W, self.H, fs, fx, fy))
        else:
            canvas.alpha_composite(fg)

    def _dots_layer(self) -> Image.Image:
        """奥の層: うすい点の格子（カメラが動くと、これが奥行きの手がかりになる）."""
        def make():
            w, h = int(self.W * 1.6), int(self.H * 1.6)
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            g = self.S(46)
            r = max(1, self.S(2))
            for y in range(0, h, g):
                for x in range(0, w, g):
                    d.ellipse([x - r, y - r, x + r, y + r], fill=(160, 190, 165, 34))
            return img
        return self.tile(("dots",), make)

    def breadcrumb(self, c: Image.Image, sc: Scene, t: float) -> None:
        """パンくずリスト「ホーム ＞ 第3章 ＞ 題」: 1 つずつ出て、今いる場所だけ白く太く."""
        s = sc.spec
        crumbs = s.get("crumbs") or ["ホーム", f"第{int(s.get('index', 1))}章", plain(str(s.get("title", "")))]
        f_on, f_off = font(self.S(24), "black"), font(self.S(24), "bold")
        x = self.S(34)
        y = self.S(26)
        d = ImageDraw.Draw(c)
        for i, cr in enumerate(crumbs):
            k = ease_out((t - sc.start - 0.15 - i * 0.18) / 0.3)
            if k <= 0:
                break
            last = i == len(crumbs) - 1
            f = f_on if last else f_off
            col = WHITE if last else GRAY
            a = int(255 * k)
            d.text((x, y + (1 - k) * self.S(8)), cr, font=f, fill=col + (a,))
            w = f.getlength(cr)
            if not last:
                d.line([x, y + self.S(30), x + w, y + self.S(30)], fill=DIM + (a,), width=1)
                x += w + self.S(14)
                d.text((x, y), "＞", font=f_off, fill=DIM + (a,))
                x += f_off.getlength("＞") + self.S(14)

    def P(self, sc: Scene, i: int, t: float, dur: float = 0.28) -> float:
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
        cx, cy = self.W / 2, self.H * (0.6 if not self.v else 0.55)
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
        cx, cy = self.W / 2, self.H * (0.6 if not self.v else 0.55)       # 上は 4D の立体が浮かぶ場所
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
        if s.get("count", True) and m and len(re.findall(r"\d+", value.replace(",", ""))) == 1 and not re.search(r"[万億千]", value):
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
            val = self.fit(str(it.get("value", "")), float(s.get("size", 96)), (self.W / (n + 1) * 0.92) if not self.v else self.W * 0.86)
            paste(c, lab, x, y - val.height / 2 - self.S(16), p)
            paste(c, val, x, y, p)
            if it.get("note"):
                paste(c, self.txt(str(it["note"]), 26, weight="bold", color=GRAY), x, y + val.height / 2 + self.S(14), p)

    def s_steps(self, c: Image.Image, sc: Scene, t: float) -> None:
        """ルール1・2・3 を 1 つずつ出し替える（前のものは上へ抜けて消え、次が下から来る）."""
        s = sc.spec
        items = s.get("items") or []
        cx, cy = self.W / 2, self.H * 0.43
        for i, it in enumerate(items):
            t_in = sc.at(int(it.get("at", i)))
            t_out = sc.at(int(items[i + 1].get("at", i + 1))) if i + 1 < len(items) else 1e9
            if t < t_in or t > t_out + 0.4:
                continue
            out_k = ease_inout((t - t_out) / 0.35)
            head = self.txt(str(it.get("head", "")), 44, color=GRAY)
            body = self.fit(str(it.get("text", "")), float(s.get("size", 96)), self.W * 0.84)
            dy = -self.S(90) * out_k
            p = self.P(sc, int(it.get("at", i)), t) * (1 - out_k) if out_k > 0 else self.P(sc, int(it.get("at", i)), t)
            if out_k >= 1:
                continue
            paste(c, head, cx, cy - body.height / 2 - self.S(30) + dy, p, slide=self.S(40))
            paste(c, body, cx, cy + dy, max(0.0, p - 0.1) if out_k == 0 else p, slide=self.S(60))
            if it.get("note"):
                paste(c, self.txt(str(it["note"]), 32, weight="bold", color=GRAY), cx, cy + body.height / 2 + self.S(26) + dy, p - 0.3)

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
        """ショート動画の引用: 右に縦長の投稿（本物の映像・表紙があればそれ）、背景にそのぼかし、左にテロップ."""
        s = sc.spec
        card = self.phone_card(s, t - sc.start)
        blur = self.tile(("phb", s.get("handle"), s.get("quote"), s.get("media")), lambda: self._blur_of(self.phone_card(s, 0.0)))
        self._bgc.alpha_composite(blur, (0, 0))                     # 背景は止めたまま
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

    def phone_card(self, it: dict[str, Any], dt: float, scale: float = 1.0) -> Image.Image:
        """投稿カード。media が映像ならそのコマ、画像ならゆっくり寄る。無ければ文字だけのカード."""
        handle, quote, meta = str(it.get("handle", "")), str(it.get("quote", "")), str(it.get("meta", ""))
        media = str(it.get("media", "") or "")
        w, h = int(self.S(400) * scale), int(self.S(700) * scale)
        if not media or not Path(media).exists():
            return self.tile(("ph", handle, quote, meta, w), lambda: self._phone(handle, quote, meta, w, h))
        frame = None
        if media.lower().endswith((".mp4", ".mov", ".webm")):
            st = self._streams.get(media)
            if st is None:
                st = self._streams[media] = Stream(Path(media), self.S(400), self.S(700), self.fps, loop=True)
            arr = st.get(int(dt * self.fps))
            if arr is not None:
                frame = Image.fromarray(arr, "RGB")
        if frame is None:
            still = self.tile(("still", media), lambda: _cover(Image.open(media).convert("RGB"), self.S(400), self.S(700)))
            z = 1.0 + 0.06 * min(1.0, dt / 8.0)                    # ゆっくり寄る
            zw, zh = int(still.width * z), int(still.height * z)
            frame = still.resize((zw, zh), Image.BILINEAR).crop(((zw - still.width) // 2, (zh - still.height) // 2,
                                                                 (zw - still.width) // 2 + still.width, (zh - still.height) // 2 + still.height))
        frame = frame.resize((w, h), Image.BILINEAR).convert("RGBA")
        over = self.tile(("pho", handle, quote, meta, w), lambda: self._phone_overlay(handle, quote, meta, w, h))
        frame.alpha_composite(over)
        mask = self.tile(("phm", w, h), lambda: rounded((w, h), self.S(26), (255, 255, 255, 255)).getchannel("A"))
        frame.putalpha(mask)
        return frame

    def _phone(self, handle: str, quote: str, meta: str, w: int | None = None, h: int | None = None) -> Image.Image:
        w, h = w or self.S(400), h or self.S(700)
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

    def _phone_overlay(self, handle: str, quote: str, meta: str, w: int, h: int) -> Image.Image:
        """本物の映像の上に重ねる: 上に投稿者、下に暗い幕と日本語訳のひと言、縁取り."""
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        a = (np.clip((np.arange(h) / h - 0.5) / 0.5, 0, 1) ** 1.3 * 225).astype(np.uint8)
        top = (np.clip((0.16 - np.arange(h) / h) / 0.16, 0, 1) * 150).astype(np.uint8)
        alpha = np.maximum(a, top)
        img.putalpha(Image.fromarray(np.repeat(alpha[:, None], w, axis=1), "L"))
        d = ImageDraw.Draw(img)
        pad = int(w * 0.07)
        d.text((pad, pad), handle, font=font(max(12, int(w * 0.058)), "bold"), fill=WHITE)
        f = font(max(14, int(w * 0.075)), "black")
        rows = wrap(quote, f, w - pad * 2)
        y = h - pad - int(w * 0.06) - len(rows) * int(f.size * 1.4)
        for ln in rows:
            d.text((pad + 1, y + 2), ln, font=f, fill=(0, 0, 0, 200))
            d.text((pad, y), ln, font=f, fill=WHITE)
            y += int(f.size * 1.4)
        if meta:
            d.text((pad, h - pad - int(w * 0.045)), meta, font=font(max(11, int(w * 0.05)), "bold"), fill=LIME)
        d.rounded_rectangle([0, 0, w - 1, h - 1], self.S(26), outline=(90, 96, 92, 255), width=self.S(3))
        return img

    def _blur_of(self, card: Image.Image) -> Image.Image:
        big = card.resize((self.W, int(self.W * card.height / card.width)) if not self.v else (int(self.H * card.width / card.height) * 2, self.H * 2))
        big = big.crop(((big.width - self.W) // 2, (big.height - self.H) // 2, (big.width - self.W) // 2 + self.W,
                        (big.height - self.H) // 2 + self.H)).filter(ImageFilter.GaussianBlur(self.S(60)))
        a = big.getchannel("A").point(lambda v: int(v * 0.42))
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
        tc = click_times(sc)[0]
        clicked = t >= tc
        btn = self.tile(("btn", clicked), lambda: self._button("登録済み" if clicked else "チャンネル登録", clicked))
        sq = 1 - 0.07 * max(0.0, 1 - abs(t - tc) / 0.12)            # 押した瞬間に少し縮む
        if sq < 0.999:
            btn = btn.resize((int(btn.width * sq), int(btn.height * sq)), Image.BILINEAR)
        paste(c, btn, cx, cy, self.P(sc, 0, t))
        ripple(c, cx, cy, t - tc, self.S(120))
        if s.get("text"):
            paste(c, self.txt(str(s["text"]), 40, weight="bold"), cx, cy + btn.height / 2 + self.S(46), self.P(sc, 0, t) - 0.3)
        # 矢印のカーソル
        k = ease_out((t - (tc - 0.6)) / 0.55)
        cursor(c, cx + self.S(170) - self.S(110) * k, cy + self.S(100) - self.S(70) * k, self.S(30))

    def _button(self, text: str, clicked: bool) -> Image.Image:
        f = font(self.S(40), "black")
        w, h = int(f.getlength(text)) + self.S(90), self.S(88)
        img = rounded((w, h), h // 2, ((70, 76, 72) if clicked else (245, 245, 243)) + (255,))
        ImageDraw.Draw(img).text((w / 2, h / 2), text, font=f, fill=WHITE if clicked else (16, 18, 17), anchor="mm")
        return img


    # --- キネティック・タイポグラフィ ------------------------------------
    def s_kinetic(self, c: Image.Image, sc: Scene, t: float) -> None:
        """文字が 1 字ずつ跳ねて落ちてくる。赤い帯は字に合わせて伸びる。text の i 行目は i 行目の語りで出る."""
        s = sc.spec
        size = self.S(float(s.get("size", 100)))
        rows = str(s.get("text", "")).split("\n")
        per_line = bool(s.get("per_line", len(rows) <= len(sc.lines)))
        f = font(size)
        lh = int(size * 1.25)
        y0 = self.H * (0.44 if not self.v else 0.4) - lh * len(rows) / 2
        d = ImageDraw.Draw(c)
        step = float(s.get("stagger", 0.03))
        for ri, row in enumerate(rows):
            chars = [(ch, st) for txt, st in runs(row) for ch in txt]
            widths = [f.getlength(ch) + (size * 0.0 if st != "box" else 0) for ch, st in chars]
            total = sum(widths)
            if total > self.W * 0.9:
                k = self.W * 0.9 / total
                widths = [w * k for w in widths]
                total = self.W * 0.9
            x = self.W / 2 - total / 2
            t0 = sc.at(ri) if per_line else sc.at(0) + ri * 0.25
            y = y0 + ri * lh
            box_x0 = box_x1 = None
            for j, ((ch, st), w) in enumerate(zip(chars, widths)):
                p = (t - t0 - j * step) / 0.32
                if p <= 0:
                    x += w
                    continue
                if st == "box":
                    if box_x0 is None:
                        box_x0 = x - size * 0.1
                    box_x1 = x + w * min(1.0, ease_out(p)) + size * 0.1
                elif box_x0 is not None:
                    d.rectangle([box_x0, y + size * 0.1, box_x1, y + size * 1.18], fill=RED_BOX)
                    box_x0 = None
                x += w
            if box_x0 is not None:
                d.rectangle([box_x0, y + size * 0.1, box_x1, y + size * 1.18], fill=RED_BOX)
            x = self.W / 2 - total / 2
            for j, ((ch, st), w) in enumerate(zip(chars, widths)):
                p = (t - t0 - j * step) / 0.32
                if p > 0:
                    col = {"w": WHITE, "box": WHITE, "red": RED, "lime": LIME}[st]
                    tile = self.tile(("ch", ch, size, col), lambda: _char_tile(ch, f, col, st != "box"))
                    dy = -size * 0.55 * (1 - ease_back(p))
                    paste(c, tile, x + w / 2, y + size * 0.62 + dy, p * 1.6, pop=True)
                x += w

    # --- ハンバーガーメニュー --------------------------------------------
    def s_menu(self, c: Image.Image, sc: Scene, t: float) -> None:
        """3 本線を押す → 線が ✕ に変わり、左から目次が出る。i 行目の語りで i 番目の項目が光る."""
        s = sc.spec
        items = [str(x) for x in s.get("items") or []]
        tc = click_times(sc)[0]
        d = ImageDraw.Draw(c)
        hx, hy, hs = self.S(120), self.S(150), self.S(56)
        k = ease_out((t - tc) / 0.35)                         # 0: 三本線 → 1: ✕
        # 引き出し
        dw = self.W * (0.46 if not self.v else 0.86)
        dk = ease_out((t - tc - 0.05) / 0.45)
        if dk > 0:
            x1 = -dw + dw * dk
            d.rectangle([0, 0, x1 + dw, self.H], fill=(16, 21, 18, 240))
            d.line([x1 + dw, 0, x1 + dw, self.H], fill=CARD_LINE, width=2)
            if s.get("title"):
                d.text((x1 + self.S(100), self.S(240)), str(s["title"]), font=font(self.S(30), "bold"), fill=GRAY)
            follow = int(s.get("offset", 1))
            cur_i = max([i for i in range(len(items)) if t >= sc.at(i + follow)] or [-1])
            for i, it in enumerate(items):
                ik = ease_out((t - tc - 0.25 - i * 0.08) / 0.35)
                if ik <= 0:
                    continue
                y = self.S(320) + i * self.S(92)
                on = i == cur_i
                if on:
                    d.rectangle([x1 + self.S(70), y - self.S(12), x1 + self.S(78), y + self.S(56)], fill=RED)
                f = font(self.S(42), "black" if on else "bold")
                d.text((x1 + self.S(100) + (1 - ik) * self.S(30), y), f"{i + 1:02d}  {it}", font=f,
                       fill=(WHITE if on else GRAY) + (int(255 * ik),))
            if not self.v and 0 <= cur_i < len(items):
                big = self.fit(f"[{items[cur_i]}]", 84, self.W * 0.44)
                paste(c, big, self.W * 0.74, self.H * 0.45, self.P(sc, cur_i + follow, t))
        # アイコン（三本線 ⇄ ✕）
        w = self.S(5)
        for j, sgn in ((0, 1), (2, -1)):
            yy = hy - hs * 0.32 + j * hs * 0.32
            ay, by = yy + (hy - yy) * k, yy + (hy - yy) * k
            dyy = hs * 0.36 * k * sgn
            d.line([hx - hs / 2, ay - dyy, hx + hs / 2, by + dyy], fill=WHITE, width=w)
        if k < 1:
            d.line([hx - hs / 2, hy, hx + hs / 2, hy], fill=WHITE + (int(255 * (1 - k)),), width=w)
        ripple(c, hx, hy, t - tc, self.S(70))
        mk = ease_out((t - (tc - 0.6)) / 0.55)
        if t < tc + 0.5:
            cursor(c, hx + self.S(200) - self.S(190) * mk, hy + self.S(160) - self.S(150) * mk, self.S(30))

    # --- カルーセル --------------------------------------------------------
    def s_carousel(self, c: Image.Image, sc: Scene, t: float) -> None:
        """横に並んだショート動画のカードが、語りに合わせて 1 枚ずつ横へ滑る。下に今の位置の点."""
        s = sc.spec
        items = s.get("items") or []
        n = len(items)
        if not n:
            return
        ats = [int(it.get("at", i)) for i, it in enumerate(items)]
        pos = 0.0
        for i in range(1, n):
            pos += ease_out((t - sc.at(ats[i])) / 0.55)
        gap = self.S(400) if not self.v else self.S(560)
        cy = self.H * (0.54 if not self.v else 0.42)
        order = sorted(range(n), key=lambda i: -abs(i - pos))
        for i in order:
            it = items[i]
            off = i - pos
            if abs(off) > 2.2:
                continue
            sc_ = (1 - 0.22 * min(1.0, abs(off))) * (0.82 if not self.v else 1.0)
            al = 1 - 0.55 * min(1.0, abs(off))
            im = self.phone_card(it, t - sc.at(ats[i]) if abs(off) < 0.5 else 0.0, scale=sc_)
            if al < 0.999:
                im = im.copy()
                im.putalpha(im.getchannel("A").point(lambda v: int(v * al)))
            k = self.P(sc, 0, t)
            paste(c, im, self.W / 2 + off * gap, cy, k, pop=False)
        cur = int(round(pos))
        d = ImageDraw.Draw(c)
        for i in range(n):
            x = self.W / 2 + (i - (n - 1) / 2) * self.S(30)
            r = self.S(7) if i == cur else self.S(5)
            yb = cy + self.S(318) if not self.v else cy + self.S(480)
            d.ellipse([x - r, yb - r, x + r, yb + r], fill=WHITE if i == cur else DIM)
        tel = items[cur].get("telop")
        if tel:
            tt = self.txt(str(tel), 54)
            ty = self.H * 0.075
            paste(c, tt, self.W / 2, ty + tt.height / 2, self.P(sc, ats[cur], t))

    # --- モーダル ----------------------------------------------------------
    def s_modal(self, c: Image.Image, sc: Scene, t: float) -> None:
        """画面に重なる小窓。後ろは暗くなる。最後に「閉じる」を押すと消える."""
        s = sc.spec
        tcs = click_times(sc)
        tc = tcs[0] if tcs else None
        open_k = ease_out((t - sc.at(0)) / 0.3)
        close_k = ease_out((t - tc - 0.08) / 0.25) if tc is not None else 0.0
        vis = open_k * (1 - close_k)
        if s.get("behind"):
            back = self.fit(str(s["behind"]), 80, self.W * 0.8)
            paste(c, back, self.W / 2, self.H * 0.44, 1.0, pop=False)
        if vis <= 0:
            return
        ov = Image.new("RGBA", c.size, (0, 0, 0, int(170 * vis)))
        c.alpha_composite(ov)
        win = self.tile(("modal", s.get("title"), s.get("body"), s.get("button")), lambda: self._modal(
            str(s.get("title", "")), str(s.get("body", "")), str(s.get("button", "閉じる"))))
        sq = 1 - 0.06 * max(0.0, 1 - abs(t - tc) / 0.12) if tc is not None else 1
        scale = (0.92 + 0.08 * ease_back(open_k)) * (1 - 0.08 * close_k) * sq
        im = win.resize((int(win.width * scale), int(win.height * scale)), Image.BILINEAR)
        im.putalpha(im.getchannel("A").point(lambda v: int(v * vis)))
        cx, cy = self.W / 2, self.H * 0.44
        c.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))
        if tc is not None:
            bx, by = cx, cy + win.height / 2 - self.S(70)
            mk = ease_out((t - (tc - 0.6)) / 0.55)
            if t < tc + 0.35:
                cursor(c, bx + self.S(200) - self.S(170) * mk, by + self.S(130) - self.S(120) * mk, self.S(30))
            ripple(c, bx, by, t - tc, self.S(90))

    def _modal(self, title: str, body: str, button: str) -> Image.Image:
        w = self.S(900 if not self.v else 860)
        pad = self.S(44)
        fb = font(self.S(40), "black")
        rows = wrap(plain(body), fb, w - pad * 2)
        h = pad * 2 + self.S(70) + len(rows) * int(self.S(40) * 1.45) + self.S(130)
        img = rounded((w, h), self.S(20), (246, 246, 243, 255))
        d = ImageDraw.Draw(img)
        d.text((pad, pad), title, font=font(self.S(30), "black"), fill=RED_BOX)
        xc, yc, r = w - pad - self.S(12), pad + self.S(20), self.S(11)
        d.line([xc - r, yc - r, xc + r, yc + r], fill=(120, 124, 120), width=self.S(3))
        d.line([xc - r, yc + r, xc + r, yc - r], fill=(120, 124, 120), width=self.S(3))
        d.line([pad, pad + self.S(56), w - pad, pad + self.S(56)], fill=(220, 220, 214), width=2)
        y = pad + self.S(76)
        for r in rows:
            d.text((pad, y), r, font=fb, fill=(22, 24, 23))
            y += int(self.S(40) * 1.45)
        bw, bh = self.S(240), self.S(72)
        d.rounded_rectangle([w / 2 - bw / 2, h - pad - bh, w / 2 + bw / 2, h - pad], bh // 2, fill=(22, 24, 23))
        d.text((w / 2, h - pad - bh / 2), button, font=font(self.S(30), "black"), fill=WHITE, anchor="mm")
        return img

    # --- アコーディオン ----------------------------------------------------
    def s_accordion(self, c: Image.Image, sc: Scene, t: float) -> None:
        """見出しが並び、i+1 行目の語りで i 番目が開く（前のものは閉じる）."""
        s = sc.spec
        items = s.get("items") or []
        w = self.W * (0.62 if not self.v else 0.88)
        x0 = self.W / 2 - w / 2
        hh = self.S(96)
        fb = font(self.S(32), "bold")
        if s.get("head"):
            head = self.txt(str(s["head"]), 60)
            paste(c, head, self.W / 2, self.H * (0.14 if not self.v else 0.16), self.P(sc, 0, t))
        y = self.H * (0.24 if not self.v else 0.24)
        d = ImageDraw.Draw(c)
        for i, it in enumerate(items):
            ap = self.P(sc, 0, t) - i * 0.25
            if ap <= 0:
                continue
            a = int(255 * min(1.0, ap))
            t_open = sc.at(i + 1)
            t_close = sc.at(i + 2) if i + 2 <= len(sc.lines) - 1 and i < len(items) - 1 else 1e9
            k = ease_out((t - t_open) / 0.35) * (1 - ease_out((t - t_close) / 0.35))
            body = str(it.get("body", ""))
            rows = wrap(body, fb, int(w - self.S(80)))
            bh = int(len(rows) * self.S(32) * 1.5 + self.S(30))
            vis = int(bh * k)
            on = k > 0.5
            d.rounded_rectangle([x0, y, x0 + w, y + hh + vis], self.S(12), fill=CARD + (a,),
                                outline=((RED if on else CARD_LINE) + (a,)), width=self.S(2))
            d.text((x0 + self.S(30), y + hh / 2), f"{i + 1}", font=font(self.S(34), "black"), fill=(RED if on else GRAY) + (a,), anchor="lm")
            d.text((x0 + self.S(80), y + hh / 2), str(it.get("title", "")), font=font(self.S(40), "black"), fill=WHITE + (a,), anchor="lm")
            # 開閉の矢印（開くと上を向く）
            ax, ay, r = x0 + w - self.S(48), y + hh / 2, self.S(12)
            pts = [(ax - r, ay - r * 0.5), (ax, ay + r * 0.5), (ax + r, ay - r * 0.5)]
            ang = math.pi * k
            rot = [(ax + (px - ax) * math.cos(ang) - (py - ay) * math.sin(ang), ay + (px - ax) * math.sin(ang) + (py - ay) * math.cos(ang)) for px, py in pts]
            d.line(rot, fill=WHITE + (a,), width=self.S(4))
            if vis > 4:
                yy = y + hh
                for r_ in rows:
                    if yy + self.S(32) > y + hh + vis:
                        break
                    d.text((x0 + self.S(80), yy), r_, font=fb, fill=(205, 212, 206, a))
                    yy += int(self.S(32) * 1.5)
            y += hh + vis + self.S(18)

    # --- ラジオボタン（クイズ） --------------------------------------------
    def s_radio(self, c: Image.Image, sc: Scene, t: float) -> None:
        """問い＋選択肢。カーソルが 1 つを選ぶ（丸が埋まる）→ 答えの行で正解が黄緑、外れは赤."""
        s = sc.spec
        opts = [str(x) for x in s.get("options") or []]
        choose = int(s.get("choose", 0))
        answer = s.get("answer")
        tc = click_times(sc)[0]
        t_ans = sc.at(int(s.get("at_answer", 2))) if answer is not None else 1e9
        q = self.fit(str(s.get("question", "")), 64, self.W * 0.86)
        qy = self.H * (0.2 if not self.v else 0.2)
        paste(c, q, self.W / 2, qy, self.P(sc, 0, t))
        lh = self.S(104)
        w = self.W * (0.5 if not self.v else 0.84)
        x0 = self.W / 2 - w / 2
        y0 = qy + q.height / 2 + self.S(80)
        d = ImageDraw.Draw(c)
        for i, op in enumerate(opts):
            ap = self.P(sc, 0, t) - 0.3 - i * 0.2
            if ap <= 0:
                continue
            a = int(255 * min(1.0, ap))
            y = y0 + i * lh
            sel = i == choose and t >= tc
            col = CARD_LINE
            if t >= t_ans and answer is not None:
                col = LIME if i == int(answer) else (RED if sel else CARD_LINE)
            elif sel:
                col = WHITE
            d.rounded_rectangle([x0, y, x0 + w, y + lh * 0.82], self.S(12), fill=CARD + (a,), outline=col + (a,), width=self.S(3))
            rx, ry, rr = x0 + self.S(52), y + lh * 0.41, self.S(18)
            d.ellipse([rx - rr, ry - rr, rx + rr, ry + rr], outline=WHITE + (a,), width=self.S(3))
            if sel:
                k = ease_back((t - tc) / 0.25)
                ir = rr * 0.55 * k
                d.ellipse([rx - ir, ry - ir, rx + ir, ry + ir], fill=WHITE)
                ripple(c, rx, ry, t - tc, self.S(50))
            d.text((x0 + self.S(100), ry), op, font=font(self.S(44), "black"), fill=WHITE + (a,), anchor="lm")
            if t >= t_ans and answer is not None and i == int(answer):
                d.text((x0 + w - self.S(40), ry), "正解", font=font(self.S(34), "black"), fill=LIME, anchor="rm")
        tx, ty = x0 + self.S(52), y0 + choose * lh + lh * 0.41
        mk = ease_out((t - (tc - 0.6)) / 0.55)
        if tc - 0.6 < t < tc + 0.5:
            cursor(c, tx + self.S(260) - self.S(250) * mk, ty + self.S(140) - self.S(130) * mk, self.S(30))

    # --- プルダウンメニュー ------------------------------------------------
    def s_pulldown(self, c: Image.Image, sc: Scene, t: float) -> None:
        """選ぶ箱を押す → 選択肢が下に開く → 1 つ選ぶと閉じて、箱に選んだものが入る."""
        s = sc.spec
        opts = [str(x) for x in s.get("options") or []]
        choose = int(s.get("choose", 0))
        t_open, t_pick = click_times(sc)
        w, hh = self.W * (0.42 if not self.v else 0.8), self.S(92)
        x0 = self.W / 2 - w / 2
        y0 = self.H * (0.24 if not self.v else 0.26)
        d = ImageDraw.Draw(c)
        if s.get("label"):
            d.text((x0, y0 - self.S(56)), str(s["label"]), font=font(self.S(34), "bold"), fill=GRAY)
        ap = self.P(sc, 0, t)
        if ap <= 0:
            return
        picked = t >= t_pick
        shown = opts[choose] if picked else str(s.get("placeholder", "選んでください"))
        sq = 1 - 0.04 * max(0.0, 1 - abs(t - t_open) / 0.12)
        d.rounded_rectangle([x0 + w * (1 - sq) / 2, y0, x0 + w - w * (1 - sq) / 2, y0 + hh], self.S(12), fill=(246, 246, 243),
                            outline=(LIME if picked else (200, 200, 196)), width=self.S(3))
        d.text((x0 + self.S(30), y0 + hh / 2), shown, font=font(self.S(42), "black"), fill=(20, 22, 21) if picked else (120, 124, 120), anchor="lm")
        ok = ease_out((t - t_open) / 0.3) * (1 - ease_out((t - t_pick - 0.12) / 0.25))
        # ▼ は開いている間だけ上を向く
        ax, ay, r = x0 + w - self.S(44), y0 + hh / 2, self.S(12)
        sgn = 1 - 2 * ok
        d.polygon([(ax - r, ay - r * 0.45 * sgn), (ax + r, ay - r * 0.45 * sgn), (ax, ay + r * 0.6 * sgn)], fill=(60, 64, 62))
        if ok > 0:
            lh = self.S(84)
            full = lh * len(opts)
            vis = full * ok
            panel = Image.new("RGBA", (int(w), int(full) + 4), (0, 0, 0, 0))
            pd = ImageDraw.Draw(panel)
            pd.rounded_rectangle([0, 0, w - 1, full + 2], self.S(12), fill=(250, 250, 248, 255), outline=(200, 200, 196, 255), width=2)
            hover = min(len(opts) - 1, int(max(0.0, (t - t_open - 0.3)) / 0.22)) if t < t_pick else choose
            hover = min(hover, choose) if t < t_pick else choose
            for i, op in enumerate(opts):
                yy = i * lh
                if i == hover:
                    pd.rectangle([2, yy + 2, w - 3, yy + lh - 2], fill=(226, 240, 205, 255))
                pd.text((self.S(30), yy + lh / 2), op, font=font(self.S(38), "black" if i == hover else "bold"), fill=(20, 22, 21), anchor="lm")
            crop = panel.crop((0, 0, panel.width, max(1, int(vis))))
            c.alpha_composite(crop, (int(x0), int(y0 + hh + self.S(10))))
            hy = y0 + hh + self.S(10) + hover * lh + lh / 2
            if t < t_pick + 0.3:
                cursor(c, x0 + w * 0.7, hy, self.S(30))
                ripple(c, x0 + w * 0.7, hy, t - t_pick, self.S(70))
        elif t < t_open + 0.1:
            mk = ease_out((t - (t_open - 0.6)) / 0.55)
            cursor(c, x0 + w * 0.7 + self.S(160) * (1 - mk), y0 + hh / 2 + self.S(120) * (1 - mk), self.S(30))
            ripple(c, x0 + w * 0.7, y0 + hh / 2, t - t_open, self.S(70))
        if s.get("result") and picked:
            rt = self.fit(str(s["result"]), 80, self.W * 0.86)
            paste(c, rt, self.W / 2, self.H * (0.62 if not self.v else 0.6), self.P(sc, int(s.get("at_result", 2)), t))

    # --- 実写の映像（Veo などで作った mp4） ---------------------------------
    def s_footage(self, c: Image.Image, sc: Scene, t: float) -> None:
        """実写の映像。縦の投稿（TikTok など）は中央に大きく・背景にそのぼかし、横の映像は画面いっぱい.

        映像が無いときは、ふつうのテロップの場面として描く（宇宙の背景に字だけ、にはしない）。
        """
        s = sc.spec
        clip = str(s.get("clip", "") or "")
        if not (clip and Path(clip).exists()):
            self.s_statement(c, sc, t) if s.get("text") else self._telop_only(c, sc, t)
            return
        vertical_src = str(s.get("fit", "")) == "phone" or clip.endswith(".jpg")
        dt = t - sc.start
        if vertical_src and not self.v:
            ch = int(self.H * 0.84)
            cw = int(ch * 9 / 16)
            st = self._streams.get(clip + "#p")
            if st is None:
                st = self._streams[clip + "#p"] = Stream(Path(clip), cw, ch, self.fps, loop=True)
            arr = st.get(int(dt * self.fps))
            if arr is None:
                return
            fr = Image.fromarray(arr, "RGB")
            small = fr.resize((max(1, cw // 10), max(1, ch // 10)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3))
            bg = _cover(small, self.W // 8, self.H // 8).resize((self.W, self.H), Image.BILINEAR).convert("RGBA")
            bg.putalpha(150)
            self._bgc.alpha_composite(bg)                           # 背景は止めたまま
            card = fr.convert("RGBA")
            card.putalpha(self.tile(("fm", cw, ch), lambda: rounded((cw, ch), self.S(24), (255, 255, 255, 255)).getchannel("A")))
            x = self.W * (0.64 if s.get("telop") else 0.5)
            paste(c, card, x, self.H * 0.47, self.P(sc, 0, t), pop=False, slide=self.S(30))
            if s.get("telop"):
                tt = self.txt(str(s["telop"]), 66, align="left")
                paste(c, tt, self.W * 0.06 + tt.width / 2, self.H * 0.42, self.P(sc, int(s.get("at_telop", 0)), t))
            return
        st = self._streams.get(clip)
        if st is None:
            st = self._streams[clip] = Stream(Path(clip), self.W, self.H, self.fps, loop=True)
        arr = st.get(int(dt * self.fps))
        if arr is not None:
            b = self._bgc                                           # 画面いっぱいの映像も止めたまま（カメラで動かさない）
            b.alpha_composite(Image.fromarray(arr, "RGB").convert("RGBA"))
            b.alpha_composite(self.tile(("dim",), lambda: Image.new("RGBA", (self.W, self.H), (0, 0, 0, 70))))
            b.alpha_composite(self.tile(("shade",), lambda: _shade(self.W, self.H)))
        if s.get("telop"):
            tt = self.fit(str(s["telop"]), 84, self.W * 0.86)
            paste(c, tt, self.W / 2, self.H * (0.42 if not self.v else 0.4), self.P(sc, int(s.get("at_telop", 0)), t))

    def _telop_only(self, c: Image.Image, sc: Scene, t: float) -> None:
        if sc.spec.get("telop"):
            tt = self.fit(str(sc.spec["telop"]), 96, self.W * 0.86)
            paste(c, tt, self.W / 2, self.H * (0.44 if not self.v else 0.4), self.P(sc, 0, t))

    # --- 2D / 3D アニメ（anim.py） ------------------------------------------
    def s_anim(self, c: Image.Image, sc: Scene, t: float) -> None:
        from . import anim
        anim.render(self, c, sc, t)

    # --- 立体の棒グラフ（宇宙の床に立つ光る角柱） --------------------------
    def s_bars3d(self, c: Image.Image, sc: Scene, t: float) -> None:
        """3D の角柱が i 行目の語りで 1 本ずつ伸びる。上に値、下に名前、最後に「約N倍」."""
        from . import space
        s = sc.spec
        vals = [float(v) for v in s.get("values") or []]
        labs = [str(x) for x in s.get("labels") or []]
        tops = [str(x) for x in s.get("tops") or []]
        hl = [i in [int(x) for x in s.get("hl") or []] for i in range(len(vals))]
        ats = [int(x) for x in (s.get("at") or list(range(len(vals))))]
        lay = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        cam = space.CAM if len(vals) <= 5 else space.Camera(pos=(1.9, 3.6, 14.5), target=(1.9, 1.45, 0.0), fov=44.0)   # 本数が多いときはカメラを引く
        infos = space.draw_bars3d(Image.new("RGBA", (8, 8)), vals, hl, p=1.0, cam=cam, only=-1)
        for i in range(len(vals)):
            k = ease_out((t - sc.at(ats[i])) / 0.8)
            if k > 0:
                space.draw_bars3d(lay, vals, hl, p=k, cam=cam, only=i)
        d = ImageDraw.Draw(lay)
        for i, inf in enumerate(infos):
            k = ease_out((t - sc.at(ats[i]) - 0.5) / 0.4)
            if k <= 0:
                continue
            tx, ty = inf["top_full"]
            bx, by = inf["base"]
            if i < len(tops):
                f = font(50, "black")
                d.text((tx, ty - 34 - (1 - k) * 20), tops[i], font=f, fill=(GOLD3D if hl[i] else WHITE) + (int(255 * k),), anchor="ms")
            if i < len(labs):
                d.text((bx, by + 40), labs[i], font=font(38, "bold"), fill=(220, 230, 240, int(255 * k)), anchor="mt")
        if self.v:
            sw = self.W / 1920
            lay = lay.resize((self.W, int(1080 * sw)), Image.BILINEAR)
            c.alpha_composite(lay, (0, int(self.H * 0.45 - lay.height / 2)))
        else:
            k = 0.9 if len(vals) == 5 else 1.0                       # 5 本は少し縮めて、左右が切れないように
            if s.get("side"):
                k = min(k, 0.88)
            if k < 1:
                small = lay.resize((int(1920 * k), int(1080 * k)), Image.BILINEAR)
                c.alpha_composite(small, (int((self.W - small.width) / 2), int(self.H - small.height - self.H * 0.04)))
            else:
                c.alpha_composite(lay)
        if s.get("title"):
            tt = self.fit(str(s["title"]), 60, self.W * 0.86)
            paste(c, tt, self.W / 2, self.H * (0.09 if not self.v else 0.2), self.P(sc, 0, t))
        if s.get("side"):
            side = self.fit(str(s["side"]), 64, self.W * (0.4 if not self.v else 0.86))
            i = int(s.get("at_side", len(sc.lines) - 1))
            if self.v:
                paste(c, side, self.W / 2, self.H * 0.68, self.P(sc, i, t))
            else:
                paste(c, side, self.W / 2, self.H * 0.2, self.P(sc, i, t))


def _shade(W: int, H: int) -> Image.Image:
    """下ほど暗くする幕（実写の上でも字幕が読める）."""
    a = (np.clip((np.arange(H) / H - 0.55) / 0.45, 0, 1) ** 1.5 * 200).astype(np.uint8)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.putalpha(Image.fromarray(np.repeat(a[:, None], W, axis=1), "L"))
    return img


def _veil(W: int, H: int) -> Image.Image:
    """宇宙の背景の上に薄く暗い幕（文字を読みやすく）."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W * 0.6)) ** 2 + ((yy - H * 0.45) / (H * 0.6)) ** 2)
    a = (np.clip(0.28 + r * 0.35, 0, 0.75) * 255).astype(np.uint8)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.putalpha(Image.fromarray(a, "L"))
    return img


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    k = max(w / img.width, h / img.height)
    im = img.resize((int(img.width * k + 0.5), int(img.height * k + 0.5)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def ease_inout(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * p * (p * (6 * p - 15) + 10)


def _blend_push(a: Image.Image, b: Image.Image, k: float) -> Image.Image:
    """場面のつなぎ: 前の絵は少し寄りながら消え、次の絵は少し奥から来る."""
    W, H = a.size
    A = _affine(a, W, H, 1 + 0.07 * k, 0, 0)
    B = _affine(b, W, H, 0.95 + 0.05 * k, 0, 0)
    if cv2 is not None:
        out = cv2.addWeighted(np.asarray(A), 1 - k, np.asarray(B), k, 0)
        return Image.fromarray(out, "RGBA")
    return Image.blend(A, B, k)


def _dot(s: int, col) -> Image.Image:
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([0, 0, s - 1, s - 1], fill=col)
    return img

# ----------------------------------------------------------------------
# カメラ・操作の動き（UI/UX アニメーション）の小道具
# ----------------------------------------------------------------------
GOLD3D = (255, 206, 92)
DEFAULT_BG = {"opening": "tesseract", "chapter": "tesseract", "bars3d": "space"}
DEFAULT_CAM = {"opening": "crane", "chapter": "dolly_in", "number": "dolly_zoom", "grid": "orbit", "bars": "tilt",
               "carousel": "still", "phone": "still", "cta": "crane", "menu": "still", "modal": "still",
               "radio": "dolly_in", "pulldown": "still", "accordion": "tilt", "footage": "still", "bars3d": "dolly_in", "anim": "dolly_in"}


def _affine(img: Image.Image, W: int, H: int, s: float, dx: float, dy: float) -> Image.Image:
    """img の中心を画面の中心に合わせ、s 倍して (dx, dy) ずらした W×H の画像."""
    cx, cy = img.width / 2, img.height / 2
    if cv2 is not None:
        M = np.array([[s, 0, W / 2 + dx - cx * s], [0, s, H / 2 + dy - cy * s]], np.float32)
        out = cv2.warpAffine(np.asarray(img), M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        return Image.fromarray(out, img.mode)
    a = 1 / s
    c = cx - (W / 2 + dx) * a
    f = cy - (H / 2 + dy) * a
    return img.transform((W, H), Image.AFFINE, (a, 0, c, 0, a, f), resample=Image.BILINEAR)


def _persp_coeffs(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    """出力の点 dst → 入力の点 src に写す PIL の PERSPECTIVE 係数."""
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        B += [u, v]
    return list(np.linalg.solve(np.array(A, float), np.array(B, float)))


def _orbit(img: Image.Image, deg: float) -> Image.Image:
    """前景の板を縦の軸で回す（被写体のまわりを回り込むカメラ）."""
    W, H = img.size
    th = math.radians(deg)
    D = W * 2.2
    out = []
    for x, y in ((0, 0), (W, 0), (W, H), (0, H)):
        X, Y = x - W / 2, y - H / 2
        Xr, Z = X * math.cos(th), X * math.sin(th)
        k = D / (D + Z)
        out.append((W / 2 + Xr * k, H / 2 + Y * k))
    if cv2 is not None:
        M = cv2.getPerspectiveTransform(np.float32([(0, 0), (W, 0), (W, H), (0, H)]), np.float32(out))
        res = cv2.warpPerspective(np.asarray(img), M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        return Image.fromarray(res, img.mode)
    co = _persp_coeffs([(0, 0), (W, 0), (W, H), (0, H)], out)
    return img.transform((W, H), Image.PERSPECTIVE, co, resample=Image.BILINEAR)


def cursor(c: Image.Image, x: float, y: float, r: float) -> None:
    d = ImageDraw.Draw(c)
    d.polygon([(x, y), (x, y + r * 1.3), (x + r * 0.38, y + r * 0.95), (x + r * 0.62, y + r * 1.45),
               (x + r * 0.8, y + r * 1.36), (x + r * 0.56, y + r * 0.86), (x + r, y + r * 0.86)],
              fill=WHITE, outline=(0, 0, 0))


def ripple(c: Image.Image, x: float, y: float, dt: float, rmax: float) -> None:
    """押した所から広がる波紋."""
    if not 0 <= dt <= 0.45:
        return
    k = dt / 0.45
    r = rmax * ease_out(k)
    a = int(150 * (1 - k))
    ImageDraw.Draw(c).ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255, a), width=3)


def click_times(sc: "Scene") -> list[float]:
    """その場面で「押す」時刻（効果音と画面の動きの両方で使う）."""
    s = sc.spec
    k = sc.kind
    if k == "cta":
        return [sc.at(int(s.get("at_click", 1)))]
    if k == "menu":
        return [sc.at(0) + 0.35]
    if k == "modal":
        return [max(sc.at(0) + 0.8, (sc.lines[-1].end if sc.lines else sc.end) - 0.1)] if s.get("close", True) else []
    if k == "radio":
        return [sc.at(int(s.get("at_choose", 1))) + 0.45]
    if k == "pulldown":
        return [sc.at(0) + 0.45, sc.at(int(s.get("at_choose", 1))) + 0.5]
    return []


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


def loop_bgm(ffmpeg: str, src: Path, seconds: float, out: Path, xfade: float = 3.0, sr: int = 48000) -> Path:
    """曲を必要な長さまでつなぐ。つなぎ目は 3 秒かけて重ねる（ぶつ切りにしない）."""
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(src), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32).reshape(-1, 2)
    n_total, n_x = int(seconds * sr), int(xfade * sr)
    if len(x) <= n_x * 2:
        reps = int(n_total / max(1, len(x))) + 1
        y = np.tile(x, (reps, 1))[:n_total]
    else:
        y = x.copy()
        ramp = np.linspace(0, 1, n_x, dtype=np.float32)[:, None]
        while len(y) < n_total:
            y[-n_x:] = y[-n_x:] * (1 - ramp) + x[:n_x] * ramp
            y = np.concatenate([y, x[n_x:]])
        y = y[:n_total]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return out


def mix(ffmpeg: str, voice: Path, plan_: Plan, sfx_dir: Path, bgm: Path | None, out: Path,
        bgm_db: float = -21.0) -> Path:
    files: dict[str, Path] = {}
    for d in (sfx_dir / "mixkit", sfx_dir):                  # 取ってきた効果音 → 手持ちがあればそちらを優先
        if d.exists():
            files.update({p.stem.upper(): p for p in d.glob("*.mp3")})
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(voice)]
    filters = ["[0:a]aresample=48000,aformat=channel_layouts=stereo[v]"]
    labels = ["[v]"]
    k = 1
    if bgm and bgm.exists():
        looped = loop_bgm(ffmpeg, bgm, plan_.duration + 1, out.with_name("bgm_loop.wav"))
        cmd += ["-i", str(looped)]
        filters.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={bgm_db}dB,"
                       f"atrim=0:{plan_.duration:.2f},afade=t=in:d=1.5,afade=t=out:st={max(0, plan_.duration - 3):.2f}:d=3[b]")
        labels.append("[b]")
        k += 1
    for j, (t, name, db) in enumerate(plan_.sfx):
        p = files.get(name)
        if not p:
            continue
        cmd += ["-i", str(p)]
        ms = int(max(0, t) * 1000)
        filters.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:1.6,afade=t=out:st=1.2:d=0.4,"
                       f"volume={db}dB,adelay={ms}|{ms}[s{j}]")
        labels.append(f"[s{j}]")
        k += 1
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=first,loudnorm=I=-15:TP=-1.5:LRA=11[out]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)
    return out


# ----------------------------------------------------------------------
# 書き出し
# ----------------------------------------------------------------------
def render(spec: dict[str, Any], out: Path, workdir: Path, synth: Callable[[str], bytes], *,
           vertical: bool = False, ffmpeg: str = "ffmpeg", sfx_dir: Path | None = None,
           bgm: Path | None = None, bgm_db: float = -21.0, size: tuple[int, int] | None = None,
           preview: tuple[float, float] | None = None, fps: int = FPS) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    scenes = synth_lines(spec, workdir, synth)
    pl = plan(scenes)
    W, H = size or ((1080, 1920) if vertical else (1920, 1080))
    r = Renderer(W, H, fps=fps)
    voice = voice_track(pl, workdir / "voice.wav")
    audio = mix(ffmpeg, voice, pl, sfx_dir or (_ROOT / "assets" / "sfx"), bgm, workdir / "audio.m4a", bgm_db)
    t0, t1 = preview or (0.0, pl.duration)
    n = int((t1 - t0) * fps)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(audio),
           "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin
    for f in range(n):
        proc.stdin.write(r.frame(pl, t0 + f / fps).tobytes())
    proc.stdin.close()
    proc.wait()
    for st in r._streams.values():
        st.close()
    (workdir / "plan.json").write_text(json.dumps(
        [{"type": s.kind, "start": round(s.start, 2), "end": round(s.end, 2)} for s in pl.scenes], ensure_ascii=False, indent=1))
    write_srt(pl, out.with_name("subtitles.srt"))
    out.with_name("chapters.json").write_text(json.dumps(chapters(pl), ensure_ascii=False), encoding="utf-8")
    log.info("書き出し: %s（%.1f 秒）", out, pl.duration)
    return out


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s_, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s_:02d},{ms:03d}"


def write_srt(pl: Plan, path: Path) -> Path:
    """YouTube に一緒に上げる字幕（語りの 1 行 = 1 字幕）."""
    rows = []
    k = 1
    for sc in pl.scenes:
        for ln in sc.lines:
            rows.append(f"{k}\n{_ts(ln.start)} --> {_ts(ln.end)}\n{ln.text}\n")
            k += 1
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def chapters(pl: Plan) -> list[tuple[float, str]]:
    """概要欄のもくじ（0:00 は冒頭、あとは章の扉の時刻と題）."""
    out: list[tuple[float, str]] = [(0.0, "冒頭")]
    for sc in pl.scenes:
        if sc.kind == "chapter":
            out.append((round(sc.start, 1), plain(str(sc.spec.get("title", "")))))
    return out
