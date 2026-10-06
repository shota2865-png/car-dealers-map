"""PSYCH DATA LAB サンプル v3（1440p）: 3D のコマ（frames3/）に、細い近未来フォントの文字・3D に付いたラベル・HUD・
めたん（経済と同じ速さの口パク）・字幕・切り替えの効果を重ねる.

  python comp3.py <from> <to>     → out3/c00000.jpg ...

字体: 日本語は M PLUS 1（細め 200〜500）、数字と英字は Orbitron。
"""
import glob
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, "/home/user/car-dealers-map/yt-econ/src")
from ytecon import character as ch  # noqa: E402
from ytecon import lab  # noqa: E402
from ytecon.config import load_config  # noqa: E402

M = os.path.dirname(os.path.abspath(__file__))
FR = os.environ.get("FR", "frames3")
OUT = os.environ.get("OUT", "out3")
FONTS = "/home/user/car-dealers-map/yt-econ/assets/fonts"
W, H, FPS, END = 2560, 1440, 30, 78.0
cfg = load_config(channel="psych")
CYAN, VIO, MAG, GOLD = (34, 211, 238), (124, 92, 255), (255, 61, 154), (255, 200, 87)
WHITE, SUB, RED, INK = (234, 246, 255), (150, 186, 210), (255, 77, 125), (3, 6, 12)

# 3D の点の画面上の位置。META に小さい画面（例 640x360）で測ったものがあれば、W に合わせて拡大して使う
META, META_W = os.environ.get("META", FR), int(os.environ.get("META_W", W))


def _scale(v):
    k = W / META_W
    return {a: ([round(b[0] * k), round(b[1] * k), b[2]] if isinstance(b, list) and len(b) == 3 else b) for a, b in v.items()}


meta = {}
for f in glob.glob(f"{M}/{META}/meta_*.json"):
    meta.update({int(k): _scale(v) for k, v in json.load(open(f)).items()})


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def seg(t, a, b):
    return clamp((t - a) / (b - a))


def eo(x):
    return 1 - (1 - clamp(x)) ** 3


def eio(x):
    x = clamp(x)
    return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


def win(t, a, b, f=0.4):
    """a〜b の間だけ 1（前後 f 秒で出入り）."""
    return seg(t, a, a + f) * (1 - seg(t, b - f, b))


# ---------------- 字体 ----------------
_FC = {}


def F(size, w=300, en=False):
    """en=False: 日本語（M PLUS 1） / True: 英字だけの飾り（Orbitron） / "num": 数字（Oxanium。0 に斜線が無い）."""
    key = (en, int(size), int(w))
    if key not in _FC:
        name, lo, hi = {False: ("MPLUS1", 100, 900), True: ("Orbitron", 400, 900), "num": ("Oxanium", 200, 800)}[en]
        f = ImageFont.truetype(f"{FONTS}/{name}[wght].ttf", int(size))
        f.set_variation_by_axes([max(lo, min(hi, w))])
        _FC[key] = f
    return _FC[key]


_D = ImageDraw.Draw(Image.new("RGB", (8, 8)))


def tlen(text, f):
    return _D.textlength(text, font=f)


# ---------------- 声（口パク: 経済チャンネルの立ち絵と同じ決め方・同じ速さ） ----------------
lines = json.load(open(f"{M}/audio3/lines3.json"))
_TRACK = []
for k, ln in enumerate(lines):
    env20 = ch.envelope(Path(M) / ln["file"])
    _TRACK.append((ln["t"], ch.apply_blinks(cfg, ch.mouth_states(cfg, env20), seed=7 + k)))


def mouth_state(fi):
    t = fi / FPS
    for t0, st in _TRACK:
        i = int((t - t0) * ch.FPS)
        if 0 <= i < len(st):
            return st[i]
    return "blink" if (fi % 110) in (0, 1, 2, 3) else "base"


def talking(fi):
    return mouth_state(fi) in ("mouth_half", "mouth_open")


# ---------------- めたん ----------------
pc = lab.presenter_cfg(cfg)
MET = {}
for expr in ("通常", "笑", "指", "驚", "考"):
    a = ch.find_assets(pc, expr)
    box = Image.open(a["base"]).convert("RGBA").getbbox()
    MET[expr] = {}
    for k in ("base", "mouth_half", "mouth_open", "blink"):
        im = Image.open(a[k]).convert("RGBA").crop(box).transpose(Image.FLIP_LEFT_RIGHT)
        MET[expr][k] = im
_MC = {}


def metan(fi, expr, h):
    st = mouth_state(fi)
    key = (expr, st, h)
    if key not in _MC:
        im = MET[expr][st]
        _MC[key] = im.resize((int(im.width * h / im.height), h), Image.LANCZOS)
    return _MC[key]


# 場面ごとの表情（研究所の本編と同じ考え方: 問い=考 / 結果=驚 / データ=指 / 判定=笑）
EXPR = [(0, "笑"), (11, "指"), (23, "驚"), (33, "指"), (44, "考"), (54, "指"), (64, "笑"), (71.5, "笑")]


def expr_at(t):
    return [e for s, e in EXPR if t >= s][-1]


# ---------------- 文字を描く層（細い字は暗い影で読ませる・光る字は発光） ----------------
class Layers:
    def __init__(self):
        self.txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.txt)
        self.g = ImageDraw.Draw(self.glow)

    def text(self, xy, s, f, color, a=1.0, anchor="la", glow=False):
        if a <= 0.004 or not s:
            return
        c = color + (int(255 * clamp(a)),)
        self.d.text(xy, s, font=f, fill=c, anchor=anchor)
        if glow:
            self.g.text(xy, s, font=f, fill=c, anchor=anchor)

    def finish(self, base):
        al = self.txt.getchannel("A")
        if al.getbbox():
            sh = al.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3)).resize((W, H), Image.BILINEAR)
            sh = sh.point(lambda v: min(255, int(v * 1.6)))
            dark = Image.new("RGBA", (W, H), INK + (0,))
            dark.putalpha(sh)
            base.alpha_composite(dark)
        if self.glow.getchannel("A").getbbox():
            g = self.glow.resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(4)).resize((W, H), Image.BILINEAR)
            base_rgb = ImageChops.add(base.convert("RGB"), Image.composite(g.convert("RGB"), Image.new("RGB", (W, H)), g.getchannel("A")))
            base.paste(base_rgb)
        base.alpha_composite(self.txt)


def kinetic(L, text, x, y, t, t_in, t_out=None, size=72, w=300, color=WHITE, hl=(), hl_color=CYAN, anchor="l", stagger=0.03, glow_hl=True):
    """1 文字ずつ、下からふわっと出る細い字。hl の語は色つき."""
    if t < t_in:
        return
    out_k = 1.0 if t_out is None else 1 - seg(t, t_out, t_out + 0.35)
    if out_k <= 0:
        return
    f = F(size, w)
    cols = [color] * len(text)
    hit = [False] * len(text)
    for h in hl:
        i = text.find(h)
        if i >= 0:
            for j in range(i, i + len(h)):
                cols[j], hit[j] = hl_color, True
    total = tlen(text, f)
    cx = x - total / 2 if anchor == "m" else (x - total if anchor == "r" else x)
    for i, c in enumerate(text):
        k = eo(seg(t, t_in + i * stagger, t_in + i * stagger + 0.4))
        if k > 0:
            L.text((cx, y + (1 - k) * size * 0.45), c, f, cols[i], k * out_k, glow=glow_hl and hit[i])
        cx += tlen(c, f)


def decode(text, t, t_in, dur=0.5, seed=0):
    """英数字が乱数から決まった文字に変わる（IT っぽい出方）."""
    if t < t_in:
        return ""
    k = seg(t, t_in, t_in + dur)
    n = int(len(text) * k)
    rnd = random.Random(int(t * 30) + seed)
    pool = "0123456789ABCDEF#/<>"
    return text[:n] + "".join(rnd.choice(pool) if c != " " else " " for c in text[n:min(len(text), n + 4)])


def rich(L, x, y, parts, a=1.0, anchor="l", glow=False):
    """字体の違う部品を、下の線（ベースライン）でそろえて横に並べる. parts=[(文字, 大きさ, 太さ, 英字?, 色)]."""
    fs = [F(s, w, en) for (_, s, w, en, _) in parts]
    total = sum(tlen(p[0], f) for p, f in zip(parts, fs))
    cx = x - total / 2 if anchor == "m" else (x - total if anchor == "r" else x)
    for (s, _, _, _, col), f in zip(parts, fs):
        L.text((cx, y), s, f, col, a, anchor="ls", glow=glow)
        cx += tlen(s, f)
    return total


def header(L, t, a, b, no, title, sub=None):
    """左上の見出し: DATA 01 ＋ 細い日本語."""
    k = win(t, a, b, 0.35)
    if k <= 0:
        return
    L.text((150, 196), decode(f"DATA {no}", t, a + 0.1, 0.45, seed=int(a)), F(36, 500, "num"), CYAN, k, glow=True)
    L.d.line([(150, 246), (150 + 460 * eo(seg(t, a + 0.2, a + 0.9)), 246)], fill=CYAN + (int(200 * k),), width=2)
    kinetic(L, title, 148, 262, t, a + 0.3, b - 0.35, size=70, w=300)
    if sub:
        L.text((152, 370), sub, F(32, 400), SUB, k * seg(t, a + 0.8, a + 1.3))


def source(L, t, a, b, text):
    k = win(t, a, b, 0.4)
    L.text((W - 150, 210), text, F(28, 400), SUB, k, anchor="ra")


def arrow(L, x0, y, x1, color, a, width=3):
    """字体に矢印が無いので、線で描く."""
    if a <= 0:
        return
    c = color + (int(255 * a),)
    L.d.line([(x0, y), (x1, y)], fill=c, width=width)
    s = 1 if x1 > x0 else -1
    L.d.line([(x1, y), (x1 - s * 22, y - 13)], fill=c, width=width)
    L.d.line([(x1, y), (x1 - s * 22, y + 13)], fill=c, width=width)


def leader(L, p, q, color, a, dot=True):
    """3D の点 p から文字 q へ引き出し線."""
    if a <= 0:
        return
    c = color + (int(230 * a),)
    L.d.line([p, q], fill=c, width=2)
    if dot:
        L.d.ellipse([p[0] - 7, p[1] - 7, p[0] + 7, p[1] + 7], outline=c, width=2)


# ---------------- 飾り（HUD・粒子のノイズ・周辺減光） ----------------
yy, xx = np.mgrid[0:H, 0:W]
VIG = np.clip(1.12 - 0.42 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2), 0.55, 1.0).astype(np.float32)[..., None]
del yy, xx
rng = np.random.default_rng(3)
GRAIN = [rng.normal(0, 4.5, (H // 2, W // 2)).astype(np.float32).repeat(2, 0).repeat(2, 1)[..., None] for _ in range(4)]
SCENES = [(0, "00", "TOPIC"), (11, "01", "STOP RATE"), (23, "02", "BUY RATE"), (33, "03", "FLOW / 100"),
          (44, "04", "META-ANALYSIS"), (54, "05", "CONDITIONS"), (64, "06", "RESULT"), (71.5, "07", "LAB")]
CUTS = [11, 23, 33, 44, 54, 64, 71.6]


def hud(L, t, fi):
    a = clamp(t / 1.0) * (1 - seg(t, 71.0, 72.0))
    if a <= 0:
        return
    c = CYAN + (int(140 * a),)
    for (x, y, sx, sy) in ((56, 56, 1, 1), (W - 56, 56, -1, 1), (56, H - 56, 1, -1), (W - 56, H - 56, -1, -1)):
        L.d.line([(x, y + sy * 60), (x, y), (x + sx * 60, y)], fill=c, width=3)
    L.text((96, 80), "PSYCH DATA LAB", F(26, 600, True), CYAN, a * 0.9)
    tc = f"{int(t // 60):02d}:{int(t % 60):02d}:{fi % FPS:02d}"
    L.d.ellipse([W - 420, 88, W - 402, 106], fill=RED + (int(255 * a * (0.4 + 0.6 * (fi // 15 % 2))),))
    L.text((W - 388, 80), f"REC {tc}", F(28, 400, "num"), WHITE, a * 0.85)
    sc = [s for s in SCENES if t >= s[0]][-1]
    L.text((96, H - 112), f"SCENE {sc[1]}  /  {sc[2]}", F(24, 400, "num"), SUB, a * 0.85)
    # 進み具合の細い線
    L.d.line([(96, H - 76), (96 + 520, H - 76)], fill=SUB + (int(70 * a),), width=2)
    L.d.line([(96, H - 76), (96 + 520 * t / END, H - 76)], fill=CYAN + (int(220 * a),), width=2)


def glitch(img, t):
    for c in CUTS:
        k = 1 - abs(t - c) / 0.12
        if k > 0:
            r, g, b, al = img.split()
            off = int(22 * k)
            r = ImageChops.offset(r, off, 0)
            b = ImageChops.offset(b, -off, int(off / 3))
            img = Image.merge("RGBA", (r, g, b, al))
            img = Image.blend(img, Image.new("RGBA", img.size, (220, 250, 255, 255)), 0.3 * k)
    return img


# ---------------- 字幕（読点で区切り、文字数で時間を割る） ----------------
def _chunks(text, maxc=24):
    parts, cur = [], ""
    for p in text.replace("、", "、\n").replace("。", "。\n").split("\n"):
        if not p:
            continue
        if cur and len(cur + p) > maxc:
            parts.append(cur)
            cur = p
        else:
            cur += p
        if cur.endswith("。"):
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    return parts


CAPS = []
for ln in lines:
    cs = _chunks(ln["text"])
    n = sum(len(c) for c in cs)
    t0 = ln["t"]
    for c in cs:
        d = ln["dur"] * len(c) / n
        CAPS.append((t0, t0 + d, c.rstrip("、。")))
        t0 += d


def caption(img, t):
    cur = [c for c in CAPS if c[0] - 0.05 <= t < c[1] + 0.15]
    if not cur:
        return
    a0, a1, s = cur[-1]
    k = seg(t, a0 - 0.05, a0 + 0.12) * (1 - seg(t, a1 + 0.05, a1 + 0.15))
    f = F(54, 400)      # 字幕も本編と同じ細さ
    tw = tlen(s, f)
    cx, cy = 1120, H - 168
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle([cx - tw / 2 - 40, cy - 46, cx + tw / 2 + 40, cy + 46], radius=10, fill=(5, 10, 20, int(185 * k)))
    d.line([(cx - tw / 2 - 40, cy - 46), (cx - tw / 2 - 40, cy + 46)], fill=CYAN + (int(255 * k),), width=4)
    d.text((cx, cy), s, font=f, fill=WHITE + (int(255 * k),), anchor="mm")
    img.alpha_composite(lay)


# ---------------- 場面ごとの文字 ----------------
def P(m, key):
    v = m.get(key)
    return (v[0], v[1]) if v and v[2] else None


def scene_text(L, t, fi, m):
    # ===== 00 検証テーマ（星空ドームを見上げる） =====
    k0 = win(t, 4.6, 10.8, 0.4)
    if k0 > 0:
        L.text((180, 470), decode("TOPIC", t, 4.6, 0.4), F(36, 500, True), CYAN, k0, glow=True)
        L.text((340, 466), "今日の検証テーマ", F(34, 400), SUB, k0 * seg(t, 4.9, 5.3))
        kinetic(L, "選択肢が多いと、", 172, 540, t, 5.2, 10.4, size=104, w=200)
        kinetic(L, "人は選べなくなる？", 172, 690, t, 5.7, 10.4, size=104, w=200, hl=("選べなくなる",))
        L.text((180, 880), "ジャムの実験／50の実験のまとめ／条件の研究　を見比べます", F(32, 400), SUB, k0 * seg(t, 7.0, 7.6))

    # ===== 01 立ち止まった人の割合 60% vs 40% =====
    header(L, t, 11.5, 22.8, "01", "売り場で立ち止まった人の割合", "試食コーナーの前を通った人のうち")
    source(L, t, 12.2, 22.8, "出典：Iyengar & Lepper (2000)")
    k = win(t, 12.4, 22.8, 0.4)
    if k > 0:
        for key, base, val, t0, col, lab_ in (("b24", "b24base", "v24", 14.6, CYAN, "24種類の売り場"), ("b6", "b6base", "v6", 18.6, VIO, "6種類の売り場")):
            p, q = P(m, key), P(m, base)
            if q:
                L.text((q[0], q[1] + 24), lab_, F(46, 400), WHITE, k, anchor="ma")
            if p and t >= t0:
                rich(L, p[0], max(p[1] - 34, 520), [(str(m.get(val, 0)), 132, 400, "num", col), ("%", 64, 300, "num", col)], k, anchor="m", glow=True)
        # 比べる線: 60 ÷ 40 = 1.5 倍
        p1, p2 = P(m, "b24"), P(m, "b6")
        kc = k * eo(seg(t, 21.2, 21.8))
        if p1 and p2 and kc > 0:
            # 2 本の棒の高さを点線で横にのばして比べる → 間に「約1.5倍」
            for p in (p1, p2):
                for x in range(int(min(p1[0], p2[0])), int(max(p1[0], p2[0])), 28):
                    L.d.line([(x, p[1] + 34), (x + 14, p[1] + 34)], fill=GOLD + (int(170 * kc),), width=2)
            bx, by = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 + 40
            L.d.rounded_rectangle([bx - 150, by - 56, bx + 150, by + 56], radius=56, outline=GOLD + (int(255 * kc),), width=3, fill=(30, 22, 4, int(160 * kc)))
            rich(L, bx, by + 24, [("約", 40, 400, False, GOLD), ("1.5", 70, 500, "num", GOLD), ("倍", 40, 400, False, GOLD)], kc, anchor="m", glow=True)

    # ===== 02 買った人の割合 3% vs 30% =====
    header(L, t, 23.5, 32.8, "02", "立ち止まった人のうち、買った人", "金色の人形 ＝ 買った人（100人あたり）")
    source(L, t, 24.0, 32.8, "出典：Iyengar & Lepper (2000)")
    k = win(t, 24.2, 32.8, 0.4)
    if k > 0:
        for key, base, val, t0, col, lab_ in (("p24", "p24base", "w24", 25.6, CYAN, "24種類"), ("p6", "p6base", "w6", 27.4, GOLD, "6種類")):
            p, q = P(m, key), P(m, base)
            if q:
                L.text((q[0], q[1] + 30), lab_, F(46, 400), WHITE, k, anchor="ma")
            if p and t >= t0:
                rich(L, p[0], p[1] - 40, [(str(m.get(val, 0)), 140 if val == "w6" else 112, 400, "num", col), ("%", 64, 300, "num", col)], k, anchor="m", glow=True)
        kb = k * eo(seg(t, 29.6, 30.1))
        if kb > 0:
            p3 = P(m, "p24") or (W * 0.3, 900)
            bx, by = p3[0], max(560, p3[1] - 300) + (1 - kb) * 30
            L.d.rounded_rectangle([bx - 200, by - 70, bx + 200, by + 70], radius=70, outline=GOLD + (int(255 * kb),), width=3, fill=(30, 22, 4, int(150 * kb)))
            rich(L, bx, by + 30, [("約", 48, 400, False, GOLD), ("10", 92, 500, "num", GOLD), ("倍", 48, 400, False, GOLD)], kb, anchor="m", glow=True)
            L.text((bx, by + 92), "6種類のほうが売れた", F(32, 400), WHITE, kb, anchor="ma")

    # ===== 03 100人が通りかかったら =====
    header(L, t, 33.5, 43.8, "03", "100人が通りかかったら（計算）")
    source(L, t, 34.0, 43.8, "Iyengar & Lepper (2000) の割合から計算")
    k = win(t, 33.8, 43.8, 0.4)
    if k > 0:
        rows = [(0, 34.0, "通りかかる", ("100", "100"), WHITE), (1, 35.9, "立ち止まる", ("60", "40"), CYAN), (2, 38.6, "買う", ("約2", "約12"), GOLD)]
        for li, t0, name, (vl, vr), col in rows:
            kk = k * eo(seg(t, t0, t0 + 0.5))
            if kk <= 0:
                continue
            for side, v, anc in (("L", vl, "r"), ("R", vr, "l")):
                p = P(m, f"lv{side}{li}")
                if not p:
                    continue
                pl_, pr_ = P(m, f"lvL{li}"), P(m, f"lvR{li}")
                if side == "R" and li == 2 and pl_ and pr_:
                    # 一番下は、めたんに隠れないよう右の漏斗の内側（左）に出す
                    p = (pr_[0] - (pr_[0] - pl_[0]) * 3.8 / 9.0, p[1])
                    anc = "r"
                x = p[0] + (-20 if anc == "r" else 20)
                L.d.line([(p[0] - 70 if anc == "r" else p[0], p[1]), (p[0] if anc == "r" else p[0] + 70, p[1])], fill=col + (int(160 * kk),), width=2)
                num = v.replace("約", "")
                parts = ([("約", 40, 400, False, col)] if "約" in v else []) + [(num, 84, 400, "num", col), ("人", 40, 400, False, col)]
                rich(L, x + (-60 if anc == "r" else 60), p[1] + 28, parts, kk, anchor=anc, glow=li == 2)
                L.text((x + (-60 if anc == "r" else 60), p[1] + 40), name, F(30, 400), SUB, kk, anchor="ra" if anc == "r" else "la")
        for key, lab_ in (("lvL2", "24種類の売り場"), ("lvR2", "6種類の売り場")):
            p = P(m, key)
            if p:
                L.text((p[0] + (290 if key[2] == "L" else -290), p[1] + 60), lab_, F(42, 400), WHITE, k, anchor="ma")

    # ===== 04 50の実験のまとめ（メタ分析） =====
    header(L, t, 44.5, 53.8, "04", "その後の50の実験をまとめると", "1つの点 ＝ 1つの実験（模式図）")
    source(L, t, 45.0, 53.8, "Scheibehenne et al. (2010)  50実験・5,036人")
    k = win(t, 45.0, 53.8, 0.4)
    if k > 0:
        n = int(50 * eo(seg(t, 45.2, 48.9)))
        rich(L, W - 150, 330, [("STUDIES ", 28, 400, True, SUB), (f"{n:02d}", 56, 400, "num", WHITE), (" / 50", 30, 300, "num", SUB)], k, anchor="r")
        pl, pr, z = P(m, "axL"), P(m, "axR"), P(m, "zero")
        ka = k * seg(t, 46.4, 47.0)
        if pl and pr:
            yb, cx = 1060, 1000
            xl, xr = 170, 1830
            arrow(L, cx - 130, yb, xl, CYAN, ka)
            arrow(L, cx + 130, yb, xr, GOLD, ka)
            L.text((xl, yb - 24), "選択肢が多いほうが よく売れた", F(34, 400), CYAN, ka, anchor="ld")
            L.text((xr, yb - 24), "少ないほうが よく売れた", F(34, 400), GOLD, ka, anchor="rd")
            L.text((cx, yb), "差なし", F(32, 400), WHITE, ka, anchor="mm")
        mp = P(m, "mean")
        km = k * eo(seg(t, 50.4, 50.9))
        if mp and km > 0:
            q = (mp[0] + 260, mp[1] - 120)
            leader(L, mp, q, MAG, km)
            rich(L, q[0] + 12, q[1] + 14, [("平均 ", 44, 400, False, MAG), ("≈ ", 60, 300, False, MAG), ("0", 80, 400, "num", MAG)], km, glow=True)
            L.text((q[0] + 14, q[1] + 34), "ほぼ「効果なし」", F(34, 400), WHITE, km)

    # ===== 05 効いてくる条件 =====
    header(L, t, 54.5, 63.8, "05", "効いてくるのは、こんなとき")
    source(L, t, 55.0, 63.8, "Chernev, Böckenholt & Goodman (2015)")
    k = win(t, 55.0, 63.8, 0.4)
    if k > 0:
        names = ["選ぶのが難しい", "選択肢が複雑", "好みが決まっていない", "早く決めたい"]
        cols = [CYAN, VIO, MAG, GOLD]
        placed = []
        order = sorted(range(4), key=lambda i: -(P(m, f"c{i}") or (0, 0))[1])     # 下にあるものから置く
        for i in order:
            p = P(m, f"c{i}")
            kk = k * eo(seg(t, 57.0 + i * 1.1, 57.5 + i * 1.1))
            if not p or kk <= 0:
                continue
            tw = tlen(names[i], F(46, 400)) / 2 + 20
            qy = p[1] - 90
            moved = True
            while moved:                                   # 先に置いたラベルと重なったら上へずらす
                moved = False
                for (x0, x1, y0, y1) in placed:
                    if p[0] - tw < x1 and p[0] + tw > x0 and qy - 110 < y1 and qy + 10 > y0:
                        qy = y0 - 12
                        moved = True
            qy = max(qy, 470 if p[0] < 1250 else 330)      # 左上の見出しにはかからない範囲で
            px = p[0]
            for (x0, x1, y0, y1) in placed:                # まだ重なるなら横へよける
                if px - tw < x1 and px + tw > x0 and qy - 110 < y1 and qy + 10 > y0:
                    px = x1 + tw + 10 if px >= (x0 + x1) / 2 else x0 - tw - 10
            p = (px, p[1]) if px == p[0] else p
            placed.append((px - tw, px + tw, qy - 110, qy + 10))
            q = (px, qy)
            leader(L, p, q, cols[i], kk)
            L.text((q[0], q[1] - 56), decode(f"FACTOR {i + 1:02d}", t, 57.0 + i * 1.1, 0.4, seed=i), F(26, 500, "num"), cols[i], kk, anchor="md", glow=True)
            L.text((q[0], q[1] - 6), names[i], F(46, 400), WHITE, kk, anchor="md")

    # ===== 06 検証結果 =====
    k = win(t, 64.4, 71.4, 0.4)
    if k > 0:
        L.text((170, 330), decode("RESULT", t, 64.5, 0.45), F(40, 600, True), CYAN, k, glow=True)
        L.text((170 + tlen("RESULT", F(40, 600, True)) + 30, 336), "検証結果", F(36, 400), SUB, k)
        L.d.line([(170, 400), (170 + 720 * eo(seg(t, 64.7, 65.4)), 400)], fill=CYAN + (int(200 * k),), width=2)
        kinetic(L, "選択肢が多いと、", 164, 430, t, 65.0, 71.0, size=88, w=200)
        kinetic(L, "選べなくなる", 164, 548, t, 65.4, 71.0, size=88, w=200)
        L.text((170, 1000), "条件しだいで起きる。いつもではない。", F(44, 400), WHITE, k * seg(t, 68.8, 69.4))
        L.text((170, 1066), "50の実験の平均ではほぼ差なし／難しい・好みが未定のときに起きやすい", F(30, 400), SUB, k * seg(t, 69.2, 69.8))


def verdict_stamp(img, t):
    """判定スタンプ「半分本当」: 大きく出て、ドンと押される."""
    if not (67.8 <= t < 71.4):
        return
    k = eo(seg(t, 67.8, 68.1))
    a = k * (1 - seg(t, 71.0, 71.4))
    s = 1.0 + 0.6 * (1 - k)
    st = STAMP.resize((int(STAMP.width * s), int(STAMP.height * s)), Image.LANCZOS)
    if a < 1:
        st.putalpha(st.getchannel("A").point(lambda v: int(v * a)))
    img.alpha_composite(st, (int(470 - st.width / 2 + 160), int(830 - st.height / 2)))


def make_stamp():
    text, size = "半分本当", 128
    f = F(size, 500)
    tw = int(tlen(text, f))
    pad = 56
    im = Image.new("RGBA", (tw + pad * 2, size + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([6, 6, im.width - 6, im.height - 6], radius=24, outline=GOLD + (255,), width=6)
    d.rounded_rectangle([20, 20, im.width - 20, im.height - 20], radius=16, outline=GOLD + (140,), width=2)
    d.text((im.width / 2, im.height / 2), text, font=f, fill=GOLD + (255,), anchor="mm")
    glow = im.filter(ImageFilter.GaussianBlur(10))
    out = Image.new("RGBA", (im.width + 60, im.height + 60), (0, 0, 0, 0))
    out.alpha_composite(glow, (30, 30))
    out.alpha_composite(glow, (30, 30))
    out.alpha_composite(im, (30, 30))
    return out.rotate(8, resample=Image.BICUBIC, expand=True)


STAMP = make_stamp()


def logo(L, img, t):
    if t < 72.6:
        return
    a = seg(t, 72.6, 73.3) * (1 - seg(t, 77.2, 78))
    sp = 26 * (1 - eo(seg(t, 72.6, 74.0))) + 10
    f = F(40, 500, True)
    txt = "PSYCH DATA LAB"
    tw = sum(tlen(c, f) + sp for c in txt) - sp
    x = W / 2 - tw / 2
    for c in txt:
        L.text((x, 560), c, f, CYAN, a, glow=True)
        x += tlen(c, f) + sp
    kinetic(L, "現代人のための心理学", W / 2, 630, t, 73.1, 77.2, size=104, w=200, anchor="m")
    L.text((W / 2, 800), "毎日20時　よく聞く話を、研究データで確かめる", F(36, 400), SUB, seg(t, 73.8, 74.4) * (1 - seg(t, 77.2, 78)), anchor="ma")
    b = eo(seg(t, 74.6, 75.0)) * (1 - seg(t, 77.2, 78))
    if b > 0:
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        bw, bh = 400, 88
        bx, by = W / 2 - bw / 2, 880 + (1 - b) * 24
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=44, fill=(230, 33, 23, int(255 * b)))
        d.text((W / 2, by + bh / 2), "チャンネル登録", font=F(40, 500), fill=(255, 255, 255, int(255 * b)), anchor="mm")
        img.alpha_composite(lay)


def presenter(img, L, t, fi):
    """めたん: 冒頭は大きく、データの間は右下に小さく。話している間は少し上へ（経済の立ち絵と同じ）."""
    bob = -8 if talking(fi) else 0
    expr = expr_at(t)
    if t < 11.2:
        kin = eo(seg(t, 0.8, 1.8)) * (1 - eio(seg(t, 10.3, 11.0)))
        if kin <= 0:
            return
        im = metan(fi, expr, 1500)
        x = int(W - im.width + 40 + (1 - kin) * 900)
        img.alpha_composite(im, (x, H - 1100 + bob)) if x < W else None
        plate(L, W - 760, H - 330, kin)
        return
    kin = eo(seg(t, 11.6, 12.4)) * (1 - seg(t, 77.0, 77.8))
    if kin <= 0:
        return
    im = metan(fi, expr, 900)
    x = int(W - im.width + 10 + (1 - kin) * 600)
    if x < W:
        img.alpha_composite(im, (x, H - 640 + bob))


def plate(L, px, py, k):
    L.d.rounded_rectangle([px, py, px + 520, py + 132], radius=10, fill=(6, 10, 18, int(210 * k)), outline=CYAN + (int(255 * k),), width=2)
    L.text((px + 30, py + 20), "ANALYST", F(24, 500, True), CYAN, k)
    L.text((px + 190, py + 18), "解析担当", F(28, 400), SUB, k)
    L.text((px + 30, py + 58), "四国めたん", F(54, 400), WHITE, k)


def compose(fi):
    t = fi / FPS
    base = Image.open(f"{M}/{FR}/f{fi:05d}.png").convert("RGBA")
    m = meta.get(fi, {}) or {}
    L = Layers()
    scene_text(L, t, fi, m)
    hud(L, t, fi)
    lg = Layers()
    logo(lg, base, t)
    presenter(base, L, t, fi)
    L.finish(base)
    lg.finish(base)
    verdict_stamp(base, t)
    caption(base, t)
    base = glitch(base, t)
    arr = np.asarray(base.convert("RGB"), dtype=np.float32)
    arr = arr * VIG + GRAIN[fi % len(GRAIN)]
    rgb = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    fade = min(seg(t, 0, 0.6), 1 - seg(t, END - 0.6, END))
    if fade < 1:
        rgb = Image.blend(Image.new("RGB", rgb.size, (0, 0, 0)), rgb, fade)
    return rgb


if __name__ == "__main__":
    os.makedirs(f"{M}/{OUT}", exist_ok=True)
    if sys.argv[1].startswith("t="):
        for x in sys.argv[1][2:].split(","):
            fi = round(float(x) * FPS)
            compose(fi).save(f"{M}/{OUT}/c{fi:05d}.jpg", quality=92)
    else:
        a, b = int(sys.argv[1]), int(sys.argv[2])
        for fi in range(a, b):
            if os.path.exists(f"{M}/{FR}/f{fi:05d}.png"):
                compose(fi).save(f"{M}/{OUT}/c{fi:05d}.jpg", quality=93)
