"""2D / 3D（アイソメ）アニメの場面: キャラクターと物を動かして、話の中身を絵で見せる.

docu の場面 {"type": "anim", "preset": ..., "lines": [...]} から呼ばれる。i 行目の語りで i 番目の動きが起きる。

  jar_fill     人が瓶（封筒）にお金を入れていく。瓶が満ちて、上の金額が増える
  coin_stacks  立体のコインの山が並んで伸びる（比べる）
  flow         お金が 左 → 途中 → 右 と流れる（給料 → 税金 → 手取り など）
  walk         人が日付の道を歩く。後ろの山が大きくなり、最後は疲れる
  risk         立体の家の中のお金に、火事・どろぼう・なくす が順に起きる
  think        人の頭の上に吹き出し。語りに合わせて考えていることと表情が変わる

絵はすべて 2 倍の大きさで描いてから縮める（線がなめらかになる）。外部の素材は使わない。
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageDraw

INK = (22, 28, 25)
SKIN = (255, 222, 196)
HAIR = (58, 40, 36)
SHIRT = (120, 200, 160)
PANTS = (60, 72, 96)
GOLD = (255, 200, 64)
GOLD_D = (214, 150, 30)
NOTE = (130, 200, 120)
WHITE = (246, 246, 242)
RED = (240, 60, 60)
LIME = (182, 242, 60)
GLASS = (190, 230, 255)
SS = 2                                  # 2 倍で描いて縮める


def _e(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _back(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 + 2.4 * (p - 1) ** 3 + 1.4 * (p - 1) ** 2


class Pen:
    """座標は画面の px（1 倍）。中では SS 倍で描く."""

    def __init__(self, img: Image.Image, k: float = SS):
        self.d = ImageDraw.Draw(img)
        self.k = k

    def _p(self, pts):
        return [(x * self.k, y * self.k) for x, y in pts]

    def circle(self, x, y, r, fill=None, outline=INK, w=4):
        k = self.k
        self.d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=fill, outline=outline, width=int(w * k) if outline else 0)

    def ellipse(self, x, y, rx, ry, fill=None, outline=INK, w=4):
        k = self.k
        self.d.ellipse([(x - rx) * k, (y - ry) * k, (x + rx) * k, (y + ry) * k], fill=fill, outline=outline, width=int(w * k) if outline else 0)

    def poly(self, pts, fill=None, outline=INK, w=4):
        self.d.polygon(self._p(pts), fill=fill, outline=outline, width=int(w * self.k) if outline else 0)

    def line(self, pts, fill=INK, w=4, joint=True):
        self.d.line(self._p(pts), fill=fill, width=int(w * self.k), joint="curve" if joint else None)
        r = w / 2
        for x, y in (pts[0], pts[-1]):
            self.circle(x, y, r, fill=fill, outline=None)

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=INK, w=4):
        k = self.k
        self.d.rounded_rectangle([x0 * k, y0 * k, x1 * k, y1 * k], int(r * k), fill=fill, outline=outline, width=int(w * k) if outline else 0)

    def arc(self, x, y, r, a0, a1, fill=INK, w=4):
        k = self.k
        self.d.arc([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], a0, a1, fill=fill, width=int(w * k))

    def text(self, x, y, s, font, fill=WHITE, anchor="mm", stroke=0, stroke_fill=INK):
        self.d.text((x * self.k, y * self.k), s, font=font, fill=fill, anchor=anchor, stroke_width=int(stroke * self.k), stroke_fill=stroke_fill)


# ----------------------------------------------------------------------
# キャラクター
# ----------------------------------------------------------------------
def person(pen: Pen, x: float, y: float, h: float, *, walk: float | None = None, arm_r: float = 20, arm_l: float = -20,
           talk: float = 0.0, mood: str = "smile", blink: bool = False, shirt=SHIRT, hold: str | None = None,
           look: float = 0.0) -> tuple[float, float]:
    """足もと (x, y)、身長 h の人。右手の先の位置を返す（物を持たせる・投げる起点）.

    walk: 歩きの位相（ラジアン）。arm_*: 腕の角度（度、下向き 0・前へ正）。talk: 口の開き 0〜1。
    mood: smile / worry / surprise / tired / happy
    """
    w = max(2.5, h / 70)
    hr = h * 0.2                              # 頭の半径
    leg = h * 0.27
    body = h * 0.3
    hip_y = y - leg
    sh_y = hip_y - body
    bob = abs(math.sin(walk)) * h * 0.012 if walk is not None else 0
    hip_y -= bob
    sh_y -= bob
    # 脚
    for side, ph in ((-1, 0.0), (1, math.pi)):
        sw = math.sin((walk or 0) + ph) * 0.45 if walk is not None else 0
        fx = x + side * h * 0.06 + math.sin(sw) * leg
        fy = hip_y + math.cos(sw) * leg
        pen.line([(x + side * h * 0.06, hip_y), (fx, fy)], fill=PANTS, w=h * 0.075)
        pen.ellipse(fx + h * 0.025, fy, h * 0.05, h * 0.025, fill=INK, outline=None)
    # 胴
    bw = h * 0.2
    pen.poly([(x - bw * 0.8, sh_y), (x + bw * 0.8, sh_y), (x + bw, hip_y + h * 0.02), (x - bw, hip_y + h * 0.02)], fill=shirt, w=w)
    pen.poly([(x - bw * 0.25, sh_y), (x + bw * 0.25, sh_y), (x, sh_y + h * 0.06)], fill=WHITE, w=w * 0.7)

    def arm(side, ang):
        sx, sy = x + side * bw * 0.85, sh_y + h * 0.03
        a = math.radians(ang)
        la = h * 0.15
        ex, ey = sx + side * math.sin(abs(a)) * la * (1 if ang * side >= 0 else 1) * 0.3 + math.sin(a) * la * 0.7, sy + math.cos(a) * la
        hx, hy = ex + math.sin(a * 1.3) * la, ey + math.cos(a * 1.3) * la
        pen.line([(sx, sy), (ex, ey), (hx, hy)], fill=shirt, w=h * 0.06)
        pen.circle(hx, hy, h * 0.035, fill=SKIN, w=w * 0.8)
        return hx, hy

    swing = math.sin(walk or 0) * 25 if walk is not None else 0
    arm(-1, arm_l - swing)
    hand = arm(1, arm_r + swing)
    # 頭
    hx, hy = x + look * hr * 0.1, sh_y - hr * 0.95
    pen.circle(hx, hy, hr, fill=SKIN, w=w)
    # 髪（ボブ＋前髪）
    pen.d.chord([(hx - hr * 1.08) * pen.k, (hy - hr * 1.12) * pen.k, (hx + hr * 1.08) * pen.k, (hy + hr * 0.9) * pen.k],
                180, 360, fill=HAIR, outline=INK, width=int(w * pen.k))
    pen.poly([(hx - hr * 1.02, hy - hr * 0.1), (hx - hr * 1.05, hy + hr * 0.55), (hx - hr * 0.78, hy + hr * 0.5), (hx - hr * 0.7, hy - hr * 0.2)], fill=HAIR, w=w * 0.8)
    pen.poly([(hx + hr * 1.02, hy - hr * 0.1), (hx + hr * 1.05, hy + hr * 0.55), (hx + hr * 0.78, hy + hr * 0.5), (hx + hr * 0.7, hy - hr * 0.2)], fill=HAIR, w=w * 0.8)
    # 目
    ex = hr * 0.38
    ey = hy + hr * 0.08
    for s in (-1, 1):
        cx = hx + s * ex + look * hr * 0.12
        if blink or mood == "tired":
            pen.arc(cx, ey - hr * 0.02, hr * 0.13, 20, 160, w=w)
        elif mood == "happy":
            pen.arc(cx, ey + hr * 0.08, hr * 0.13, 200, 340, w=w * 1.1)
        else:
            ry = hr * (0.2 if mood == "surprise" else 0.17)
            pen.ellipse(cx, ey, hr * 0.11, ry, fill=INK, outline=None)
            pen.circle(cx + hr * 0.04, ey - ry * 0.4, hr * 0.045, fill=WHITE, outline=None)
        if mood in ("worry", "tired"):                                  # 困り眉
            pen.line([(cx - s * hr * 0.16, ey - hr * 0.32), (cx + s * hr * 0.12, ey - hr * 0.24)], w=w * 0.8)
        pen.ellipse(cx + s * hr * 0.12, ey + hr * 0.3, hr * 0.13, hr * 0.06, fill=(255, 170, 170), outline=None)
    # 口
    my = hy + hr * 0.52
    if talk > 0.05 or mood == "surprise":
        o = max(talk, 0.6 if mood == "surprise" else 0)
        pen.ellipse(hx, my, hr * 0.11, hr * (0.05 + 0.1 * o), fill=(160, 40, 50), w=w * 0.8)
    elif mood in ("worry", "tired"):
        pen.line([(hx - hr * 0.14, my + hr * 0.03), (hx - hr * 0.05, my - hr * 0.02), (hx + hr * 0.05, my + hr * 0.03), (hx + hr * 0.14, my - hr * 0.02)], w=w * 0.8)
    else:
        pen.arc(hx, my - hr * 0.1, hr * 0.16, 30, 150, w=w * 0.9)
    if mood == "tired":                                                  # 汗
        pen.poly([(hx + hr * 1.05, hy - hr * 0.5), (hx + hr * 0.95, hy - hr * 0.25), (hx + hr * 1.15, hy - hr * 0.25)], fill=GLASS, w=w * 0.6)
    if hold:
        draw_item(pen, hold, hand[0], hand[1] - h * 0.04, h * 0.12)
    return hand


# ----------------------------------------------------------------------
# 物
# ----------------------------------------------------------------------
def coin(pen: Pen, x, y, r, spin: float = 0.0, mark: str = "¥", font=None):
    """回るコイン（spin で横に細くなる）."""
    sx = max(0.12, abs(math.cos(spin)))
    pen.ellipse(x + r * 0.12 * sx, y + r * 0.1, r * sx, r, fill=GOLD_D, w=max(2, r / 9))
    pen.ellipse(x, y, r * sx, r, fill=GOLD, w=max(2, r / 9))
    if sx > 0.5 and font is not None:
        pen.text(x, y + r * 0.04, mark, font, fill=GOLD_D)


def note(pen: Pen, x, y, w, ang=0.0):
    hw, hh = w / 2, w * 0.24
    c, s = math.cos(ang), math.sin(ang)
    pts = [(x + px * c - py * s, y + px * s + py * c) for px, py in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
    pen.poly(pts, fill=NOTE, w=max(2, w / 40))
    pen.circle(x, y, hh * 0.55, fill=(100, 170, 95), outline=None)


def envelope(pen: Pen, x, y, w, label: str = "", font=None, fill=WHITE):
    h = w * 0.62
    pen.rrect(x - w / 2, y - h / 2, x + w / 2, y + h / 2, w * 0.05, fill=fill, w=max(2, w / 40))
    pen.line([(x - w / 2 + 3, y - h / 2 + 3), (x, y + h * 0.08), (x + w / 2 - 3, y - h / 2 + 3)], w=max(2, w / 40))
    if label and font is not None:
        pen.text(x, y + h * 0.25, label, font, fill=INK)


def draw_item(pen: Pen, kind: str, x, y, size, font=None):
    if kind == "coin":
        coin(pen, x, y, size * 0.45, font=font)
    elif kind == "note":
        note(pen, x, y, size)
    elif kind == "envelope":
        envelope(pen, x, y, size)
    elif kind == "phone":
        pen.rrect(x - size * 0.28, y - size * 0.5, x + size * 0.28, y + size * 0.5, size * 0.08, fill=(40, 46, 50))
        pen.rrect(x - size * 0.22, y - size * 0.42, x + size * 0.22, y + size * 0.4, size * 0.04, fill=(120, 200, 255), outline=None)


def jar(pen: Pen, x, y, w, h, level: float, font=None, label: str = ""):
    """ガラスの瓶（足もと y）。level 0〜1 でお金が満ちる."""
    x0, x1, y0, y1 = x - w / 2, x + w / 2, y - h, y
    # 中身（下から）
    pen.rrect(x0, y0, x1, y1, w * 0.12, fill=(190, 230, 255, 40), outline=None)
    if level > 0:
        top = y1 - (h * 0.86) * min(1.0, level)
        pen.rrect(x0 + 8, top, x1 - 8, y1 - 8, w * 0.08, fill=GOLD, outline=None)
        for i in range(int(6 * min(1.0, level)) + 1):
            yy = y1 - 20 - i * (h * 0.86 * min(1.0, level)) / 7
            for j in range(4):
                coin(pen, x0 + w * (0.2 + 0.2 * j) + (i % 2) * 8, yy, w * 0.07)
    pen.rrect(x0, y0, x1, y1, w * 0.12, fill=None, outline=(200, 232, 255), w=7)
    pen.rrect(x0 + w * 0.08, y0 - h * 0.08, x1 - w * 0.08, y0 + h * 0.04, w * 0.05, fill=(170, 140, 110), outline=(230, 210, 180), w=5)
    pen.line([(x0 + w * 0.14, y0 + h * 0.15), (x0 + w * 0.14, y1 - h * 0.25)], fill=(255, 255, 255, 140), w=w * 0.05)
    if label and font is not None:
        pen.rrect(x - w * 0.38, y + 14, x + w * 0.38, y + 14 + h * 0.18, 8, fill=WHITE, w=3)
        pen.text(x, y + 14 + h * 0.09, label, font, fill=INK)


def iso_stack(pen: Pen, x, y, r, n: float):
    """アイソメのコインの山（足もと中心 x, y）。n 枚ぶん積む（小数で途中まで）."""
    th = r * 0.18
    full = int(n)
    for i in range(full + (1 if n - full > 0.01 else 0)):
        yy = y - i * th
        if i == full:
            yy -= (1 - (n - full)) * r * 1.5                             # 落ちてくる途中
        pen.ellipse(x, yy + th * 0.5, r, r * 0.5, fill=GOLD_D, w=2.5)
        pen.d.rectangle([(x - r) * pen.k, (yy - th * 0.0) * pen.k, (x + r) * pen.k, (yy + th * 0.5) * pen.k], fill=GOLD_D)
        pen.line([(x - r, yy), (x - r, yy + th * 0.5)], w=2.5)
        pen.line([(x + r, yy), (x + r, yy + th * 0.5)], w=2.5)
        pen.ellipse(x, yy, r, r * 0.5, fill=GOLD, w=2.5)


def iso_house(pen: Pen, x, y, s, lit: bool = True):
    """アイソメの家（足もと中心 x, y、大きさ s）。窓の中にお金."""
    a, b = s * 0.5, s * 0.29
    h = s * 0.42
    L = [(x - a, y - b), (x, y), (x, y - h), (x - a, y - b - h)]
    R = [(x, y), (x + a, y - b), (x + a, y - b - h), (x, y - h)]
    pen.poly(L, fill=(232, 220, 200), w=4)
    pen.poly(R, fill=(200, 186, 166), w=4)
    lt, fr, rt = (x - a - 10, y - b - h + 4), (x, y - h + 10), (x + a + 10, y - b - h + 4)
    apex = (x, y - b - h - s * 0.34)
    pen.poly([lt, fr, apex], fill=(200, 70, 60), w=4)
    pen.poly([fr, rt, apex], fill=(168, 50, 44), w=4)
    # 窓（中にお金）
    wx, wy = x - a * 0.5, y - b * 0.5 - h * 0.45
    pen.poly([(wx - a * 0.22, wy - b * 0.22 - h * 0.18), (wx + a * 0.22, wy + b * 0.22 - h * 0.18), (wx + a * 0.22, wy + b * 0.22 + h * 0.18), (wx - a * 0.22, wy - b * 0.22 + h * 0.18)],
             fill=(255, 236, 160) if lit else (80, 90, 100), w=4)
    note(pen, wx, wy + 4, a * 0.26, ang=0.5)


def flame(pen: Pen, x, y, s, t):
    for i, col in enumerate(((255, 120, 40), (255, 190, 60), (255, 240, 160))):
        k = 1 - i * 0.3
        wob = math.sin(t * 9 + i) * s * 0.05
        pen.poly([(x - s * 0.3 * k, y), (x - s * 0.18 * k + wob, y - s * 0.45 * k), (x + wob, y - s * k), (x + s * 0.2 * k - wob, y - s * 0.4 * k), (x + s * 0.3 * k, y)],
                 fill=col, outline=INK if i == 0 else None, w=3)


def thief(pen: Pen, x, y, h, t):
    """黒い人影（目出し帽）がしのび足."""
    sway = math.sin(t * 6) * h * 0.02
    pen.ellipse(x + sway, y - h * 0.72, h * 0.15, h * 0.16, fill=(30, 32, 36), w=3)
    pen.rrect(x + sway - h * 0.12, y - h * 0.78, x + sway + h * 0.12, y - h * 0.7, 6, fill=WHITE, outline=None)
    pen.circle(x + sway - h * 0.05, y - h * 0.74, h * 0.025, fill=INK, outline=None)
    pen.circle(x + sway + h * 0.05, y - h * 0.74, h * 0.025, fill=INK, outline=None)
    pen.poly([(x + sway - h * 0.17, y - h * 0.58), (x + sway + h * 0.17, y - h * 0.58), (x + h * 0.15, y - h * 0.18), (x - h * 0.15, y - h * 0.18)], fill=(30, 32, 36), w=3)
    for s in (-1, 1):
        pen.line([(x + s * h * 0.07, y - h * 0.2), (x + s * h * 0.12 + math.sin(t * 6 + s) * h * 0.05, y)], fill=(30, 32, 36), w=h * 0.07)
    pen.ellipse(x + sway + h * 0.22, y - h * 0.45, h * 0.12, h * 0.13, fill=(220, 210, 190), w=3)     # 袋


def bubble(pen: Pen, x, y, w, h, tail_to: tuple[float, float]):
    """考えごとの吹き出し（雲形の角丸）＋頭へ向かう小さな丸 3 つ."""
    tx, ty = tail_to
    sx, sy = x - w * 0.3, y + h / 2
    for i, (k, rr) in enumerate(((0.3, 0.13), (0.58, 0.09), (0.82, 0.06))):
        pen.circle(sx + (tx - sx) * k, sy + (ty - sy) * k, h * rr, fill=WHITE, outline=(120, 130, 124), w=3)
    pen.rrect(x - w / 2, y - h / 2, x + w / 2, y + h / 2, h * 0.45, fill=WHITE, outline=(120, 130, 124), w=4)


# ----------------------------------------------------------------------
# 場面（プリセット）
# ----------------------------------------------------------------------
def render(r: Any, c: Image.Image, sc: Any, t: float) -> None:
    """docu.Renderer から呼ぶ。2 倍の舞台に描いて、縮めて c に重ねる."""
    from .docu import font

    s = sc.spec
    preset = str(s.get("preset", "jar_fill"))
    W, H = r.W, r.H
    stage = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    pen = Pen(stage)
    f = {k: font(int(r.S(v) * SS), "black") for k, v in (("s", 26), ("m", 38), ("l", 56), ("xl", 76))}
    P = {"jar_fill": _jar_fill, "coin_stacks": _coin_stacks, "flow": _flow, "walk": _walk, "risk": _risk, "think": _think}
    P.get(preset, _jar_fill)(r, pen, sc, t, f)
    small = stage.reduce(SS)
    c.alpha_composite(small)
    if s.get("telop"):
        tt = r.fit(str(s["telop"]), 60, W * 0.86)
        from .docu import paste
        paste(c, tt, W / 2, H * (0.1 if not r.v else 0.14), r.P(sc, int(s.get("at_telop", 0)), t))


def _ground(r, pen: Pen, y, x0=None, x1=None):
    x0 = r.W * 0.05 if x0 is None else x0
    x1 = r.W * 0.95 if x1 is None else x1
    pen.line([(x0, y), (x1, y)], fill=(70, 90, 78), w=r.S(4))


def _talk(sc, t) -> float:
    on = any(ln.start <= t < ln.end for ln in sc.lines)
    return (0.5 + 0.5 * math.sin(t * 22)) if on and sc.spec.get("talk") else 0.0


def _blink(t) -> bool:
    return (t % 3.1) < 0.12


def _jar_fill(r, pen, sc, t, f):
    """人が 1 行ごとにお金を投げ入れる。瓶が満ち、上の金額が増える."""
    s = sc.spec
    vals = [str(v) for v in s.get("values") or []]
    n = max(1, len(sc.lines))
    gy = r.H * (0.8 if not r.v else 0.62)
    _ground(r, pen, gy)
    px = r.W * (0.32 if not r.v else 0.3)
    jx = r.W * (0.64 if not r.v else 0.72)
    ph = r.H * (0.5 if not r.v else 0.24)
    jw, jh = r.S(300), r.S(380)
    # 投げる動き: 各行の頭から 0.7 秒
    k_level = 0.0
    arm = 20
    for i in range(n):
        dt = t - sc.at(i)
        if dt >= 0.7:
            k_level = (i + 1) / n
        elif dt >= 0:
            k_level = i / n + _e(max(0, dt - 0.45) / 0.25) / n
            arm = 20 + 120 * math.sin(min(1.0, dt / 0.35) * math.pi)
    level = float(s.get("level", 0.9)) * k_level
    hand = person(pen, px, gy, ph, arm_r=arm, talk=_talk(sc, t), mood=str(s.get("mood", "smile")), blink=_blink(t), look=1)
    for i in range(n):
        dt = t - sc.at(i)
        if 0 <= dt < 0.7:
            u = dt / 0.7
            x = hand[0] + (jx - hand[0]) * u
            y = hand[1] + (gy - jh - hand[1]) * u - math.sin(u * math.pi) * r.S(220)
            item = str(s.get("item", "coin"))
            if item == "coin":
                coin(pen, x, y, r.S(30), spin=dt * 14, font=f["s"])
            else:
                draw_item(pen, item, x, y, r.S(90))
    jar(pen, jx, gy, jw, jh, level, font=f["s"], label=str(s.get("jar_label", "")))
    cur = -1
    for i in range(len(vals)):
        if t >= sc.at(i) + 0.6:
            cur = i
    if cur >= 0:
        bounce = _back((t - sc.at(cur) - 0.6) / 0.35)
        pen.text(jx, gy - jh - r.S(70) - (1 - bounce) * r.S(20), vals[cur], f["xl"], fill=GOLD, stroke=r.S(5))


def _coin_stacks(r, pen, sc, t, f):
    """立体のコインの山が並ぶ。i 行目で i 番目が積み上がる。上に金額、下に名前."""
    s = sc.spec
    vals = [float(v) for v in s.get("values") or []]
    labs = [str(x) for x in s.get("labels") or []]
    tops = [str(x) for x in s.get("tops") or []]
    n = max(1, len(vals))
    vmax = max(vals) if vals else 1
    gy = r.H * (0.78 if not r.v else 0.62)
    span = r.W * (0.7 if not r.v else 0.86)
    rad = min(r.S(80), span / n * 0.32)
    maxn = float(s.get("max_coins", 22))
    hl = [int(x) for x in s.get("hl") or []]
    ats = [int(x) for x in (s.get("at") or list(range(n)))]
    # 床（アイソメのひし形）
    cx = r.W / 2
    pen.poly([(cx - span * 0.55, gy + r.S(20)), (cx, gy - r.S(70)), (cx + span * 0.55, gy + r.S(20)), (cx, gy + r.S(110))], fill=(28, 40, 34), outline=(60, 84, 70), w=r.S(3))
    for i, v in enumerate(vals):
        x = cx - span / 2 + span * (i + 0.5) / n
        k = _e((t - sc.at(ats[i])) / 1.1)
        cnt = max(1.0 * min(1.0, k * 3), maxn * v / vmax * k)
        iso_stack(pen, x, gy, rad, cnt)
        if i < len(labs):
            pen.text(x, gy + r.S(60), labs[i], f["m"], fill=(205, 214, 206))
        if i < len(tops) and k > 0.85:
            col = RED if i in hl else WHITE
            stag = (i % 2) * r.S(46) if r.v else 0
            pen.text(x, gy - cnt * rad * 0.18 - rad * 0.6 - r.S(46) - stag, tops[i], f["m"] if r.v else f["l"], fill=col, stroke=r.S(4))


def _flow(r, pen, sc, t, f):
    """お金が左から右へ流れる。途中の箱で減る（取られる）。i 行目で i 番目の箱が光る."""
    s = sc.spec
    nodes = s.get("nodes") or []
    n = max(2, len(nodes))
    y = r.H * (0.5 if not r.v else 0.42)
    xs = [r.W * (0.14 + 0.72 * i / (n - 1)) for i in range(n)] if not r.v else [r.W * 0.5] * n
    ys = [y] * n if not r.v else [r.H * (0.22 + 0.5 * i / (n - 1)) for i in range(n)]
    # 道
    for i in range(n - 1):
        pen.line([(xs[i], ys[i]), (xs[i + 1], ys[i + 1])], fill=(80, 110, 92), w=r.S(14))
    # 流れるお金
    for j in range(10):
        u = ((t * 0.35 + j / 10) % 1.0)
        seg = u * (n - 1)
        i = min(n - 2, int(seg))
        v = seg - i
        if t < sc.at(i):
            continue
        x = xs[i] + (xs[i + 1] - xs[i]) * v
        yy = ys[i] + (ys[i + 1] - ys[i]) * v + math.sin(u * 30) * r.S(6)
        coin(pen, x, yy, r.S(18), spin=t * 8 + j, font=None)
    for i, nd in enumerate(nodes):
        on = t >= sc.at(i)
        k = _back((t - sc.at(i)) / 0.4)
        bw, bh = r.S(330) * (0.9 + 0.1 * k), r.S(210) * (0.9 + 0.1 * k)
        col = (RED if nd.get("hl") else LIME) if on else (50, 60, 56)
        pen.rrect(xs[i] - bw / 2, ys[i] - bh / 2, xs[i] + bw / 2, ys[i] + bh / 2, r.S(20), fill=(24, 31, 27), outline=col, w=r.S(5))
        icon = str(nd.get("icon", "coin"))
        if icon == "person":
            person(pen, xs[i], ys[i] + bh * 0.42, bh * 0.85, mood="smile", blink=_blink(t + i))
        else:
            draw_item(pen, icon, xs[i], ys[i], r.S(120), font=f["m"])
        pen.text(xs[i], ys[i] + bh / 2 + r.S(36), str(nd.get("label", "")), f["m"], fill=WHITE if on else (110, 120, 114))
        if nd.get("amount") and on:
            pen.text(xs[i], ys[i] - bh / 2 - r.S(36), str(nd["amount"]), f["l"], fill=col, stroke=r.S(4))


def _walk(r, pen, sc, t, f):
    """人が日付の道を歩く。目印の日に来ると札が立ち、後ろの山が大きくなる。最後は疲れる."""
    s = sc.spec
    marks = s.get("marks") or []
    gy = r.H * (0.74 if not r.v else 0.6)
    x0, x1 = r.W * 0.1, r.W * 0.9
    _ground(r, pen, gy, x0, x1)
    t0 = sc.start + 0.2
    t1 = (sc.lines[-1].end if sc.lines else sc.end) - 0.2
    u = min(1.0, max(0.0, (t - t0) / max(0.5, t1 - t0)))
    px = x0 + (x1 - x0) * (0.05 + 0.85 * u)
    for i, m in enumerate(marks):
        mx = x0 + (x1 - x0) * float(m.get("pos", (i + 1) / (len(marks) + 1)))
        on = px >= mx
        pen.line([(mx, gy), (mx, gy - r.S(120))], fill=(110, 120, 114), w=r.S(5))
        pen.rrect(mx - r.S(80), gy - r.S(170), mx + r.S(80), gy - r.S(110), r.S(10), fill=(LIME if on else (60, 72, 64)), w=r.S(3))
        pen.text(mx, gy - r.S(140), str(m.get("label", "")), f["s"], fill=INK)
        if on and m.get("amount"):
            pen.text(mx, gy - r.S(210), str(m["amount"]), f["m"], fill=GOLD, stroke=r.S(4))
    tired = u > float(s.get("tired_at", 0.75))
    person(pen, px, gy, r.H * (0.34 if not r.v else 0.2), walk=t * 7 if u < 1 else None, mood="tired" if tired else "smile",
           blink=_blink(t), hold=str(s.get("hold", "envelope")), arm_r=60, look=1)
    # 背負う山（封筒の数がふえる）
    k = int(1 + u * 9)
    for j in range(k):
        envelope(pen, px - r.S(90) - (j % 3) * r.S(26), gy - r.S(160) - (j // 3) * r.S(30), r.S(60))


def _risk(r, pen, sc, t, f):
    """立体の家。i+1 行目で i 番目の危険（fire / thief / lost）が起きる."""
    s = sc.spec
    hz = [str(x) for x in s.get("hazards") or ["fire", "thief", "lost"]]
    hx, hy = r.W * 0.5, r.H * (0.72 if not r.v else 0.56)
    size = r.S(520) if not r.v else r.S(440)
    iso_house(pen, hx, hy, size)
    labels = {"fire": "火事", "thief": "どろぼう", "lost": "なくす"}
    for i, h in enumerate(hz):
        ts = sc.at(i + int(s.get("offset", 1)))
        if t < ts:
            continue
        k = _back((t - ts) / 0.4)
        pos = {"fire": (hx + size * 0.62, hy - size * 0.55), "thief": (hx - size * 0.66, hy - size * 0.55),
               "lost": (hx, hy - size * 1.22)}
        rx, ry = pos.get(h, (hx, hy - size * 1.2))
        rx = min(max(rx, r.S(110)), r.W - r.S(110))
        if h == "fire":
            flame(pen, hx + size * 0.25, hy - size * 0.18, r.S(190) * min(1.2, k), t)
            flame(pen, hx + size * 0.42, hy - size * 0.28, r.S(130) * min(1.2, k), t + 0.7)
        elif h == "thief":
            thief(pen, max(r.S(90), hx - size * 0.62) + math.sin(t * 1.2) * r.S(20), hy + r.S(10), r.S(230) * min(1.0, k), t)
        elif h == "lost":
            for j in range(5):
                a = j * 1.25 + t * 1.5
                coin(pen, hx + math.cos(a) * size * 0.4, hy - size * 0.9 + math.sin(a) * r.S(30) - (t - ts) * r.S(40) % r.S(120), r.S(22), spin=t * 6 + j, font=None)
            pen.text(hx + size * 0.18, hy - size * 1.0, "？", f["xl"], fill=WHITE, stroke=r.S(4))
        pen.rrect(rx - r.S(95), ry - r.S(36), rx + r.S(95), ry + r.S(36), r.S(30), fill=RED, w=r.S(3))
        pen.text(rx, ry, labels.get(h, h), f["m"], fill=WHITE)


def _think(r, pen, sc, t, f):
    """人の頭の上の吹き出しに、i 行目の考えが出る。表情も変わる."""
    s = sc.spec
    th = s.get("thoughts") or []
    gy = r.H * (0.86 if not r.v else 0.66)
    px = r.W * (0.3 if not r.v else 0.5)
    ph = r.H * (0.52 if not r.v else 0.28)
    cur = 0
    for i in range(len(th)):
        if t >= sc.at(i):
            cur = i
    mood = str(th[cur].get("mood", "smile")) if th else "smile"
    person(pen, px, gy, ph, mood=mood, talk=_talk(sc, t), blink=_blink(t), arm_r=10, arm_l=-10, look=0.5)
    if th:
        k = _back((t - sc.at(cur)) / 0.35)
        bx = r.W * (0.64 if not r.v else 0.5)
        by = r.H * (0.34 if not r.v else 0.2)
        bw, bh = r.W * (0.5 if not r.v else 0.84) * (0.9 + 0.1 * k), r.S(200) * (0.9 + 0.1 * k)
        bubble(pen, bx, by, bw, bh, (px + ph * 0.12, gy - ph * 1.08))
        txt = str(th[cur].get("text", ""))
        size = f["l"] if len(txt) <= 14 else f["m"]
        pen.text(bx, by, txt, size, fill=INK)
