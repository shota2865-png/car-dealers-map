"""PSYCH DATA LAB ショーリール: 3D のコマ（frames/）に、文字・HUD・めたん（口パク）・切り替えの効果を重ねる.

  python comp.py <from> <to>     → out/c00000.jpg ...
"""
import glob
import json
import os as _os0
import math
import random
import sys
import wave

import numpy as np
from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, _os0.path.join(_os0.path.dirname(_os0.path.abspath(__file__)), "..", "src"))
from ytecon import character, lab  # noqa: E402
from ytecon.config import load_config  # noqa: E402
from ytecon.quiz import font  # noqa: E402

M = _os0.environ.get("MOTION_DIR", _os0.path.dirname(_os0.path.abspath(__file__)))  # 作業フォルダ（frames/ audio/ out/）
FR = _os0.environ.get("FR", "frames")                # 3D のコマの置き場（M の下）
W, H, FPS = 1920, 1080, 30
cfg = load_config(channel="psych")
CYAN, MAG, GOLD, WHITE, SUB, RED = (34, 211, 238), (255, 61, 154), (255, 200, 87), (234, 246, 255), (143, 179, 204), (255, 77, 125)

meta = {}
for f in glob.glob(f"{M}/{FR}/meta_*.json"):
    meta.update({int(k): v for k, v in json.load(open(f)).items()})


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def seg(t, a, b):
    return clamp((t - a) / (b - a))


def eo(x):
    return 1 - (1 - clamp(x)) ** 3


def eio(x):
    x = clamp(x)
    return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


# ---------------- 声（口パク用の音量） ----------------
lines = json.load(open(f"{M}/audio/lines.json"))
env = np.zeros(int(60 * FPS) + 2)
for ln in lines:
    with wave.open(ln["file"]) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop = sr // FPS
    for k in range(len(a) // hop):
        fi = int(round(ln["t"] * FPS)) + k
        if fi < len(env):
            env[fi] = max(env[fi], float(np.sqrt(np.mean(a[k * hop:(k + 1) * hop] ** 2))))
env = env / max(env.max(), 1e-6)


def mouth_state(fi):
    v = env[fi]
    return "mouth_open" if v > 0.45 else ("mouth_half" if v > 0.16 else "base")


# ---------------- めたん ----------------
pc = lab.presenter_cfg(cfg)
MET = {}
for expr in ("通常", "笑", "指"):
    a = character.find_assets(pc, expr)
    MET[expr] = {}
    for k in ("base", "mouth_half", "mouth_open", "blink"):
        im = Image.open(a[k]).convert("RGBA")
        im = im.crop(Image.open(a["base"]).convert("RGBA").getbbox())
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
        s = 1000 / im.height
        MET[expr][k] = im.resize((int(im.width * s), 1000), Image.LANCZOS)


def metan(fi, expr):
    st = mouth_state(fi)
    blink = (fi % 97) in (0, 1, 2) and st == "base"
    return MET[expr]["blink" if blink else st]


# ---------------- 文字 ----------------
def F(size, w=700):
    return font(cfg, size, w)


def kinetic(img, text, x, y, t, t_in, t_out=None, size=72, color=WHITE, hl=(), hl_color=CYAN, anchor="l", stagger=0.035, weight=900):
    """1 文字ずつ下から出る文字。hl に入った語はシアン。t_out から消える."""
    if t < t_in:
        return
    f = F(size, weight)
    d0 = ImageDraw.Draw(img)
    colors = [color] * len(text)
    for h in hl:
        i = text.find(h)
        if i >= 0:
            for j in range(i, i + len(h)):
                colors[j] = hl_color
    total = d0.textlength(text, font=f)
    x0 = x - total / 2 if anchor == "m" else (x - total if anchor == "r" else x)
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    cx = x0
    out_k = 1.0 if t_out is None else 1 - seg(t, t_out, t_out + 0.35)
    for i, ch in enumerate(text):
        k = eo(seg(t, t_in + i * stagger, t_in + i * stagger + 0.35))
        if k > 0:
            a = int(255 * k * out_k)
            d.text((cx, y + (1 - k) * size * 0.6), ch, font=f, fill=colors[i] + (a,), stroke_width=max(2, size // 22), stroke_fill=(3, 6, 12, int(a * 0.85)))
        cx += d0.textlength(ch, font=f)
    img.alpha_composite(lay)


def plain(img, text, xy, size, color=SUB, a=1.0, anchor="la", weight=500):
    if a <= 0:
        return
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).text(xy, text, font=F(size, weight), fill=color + (int(255 * a),), anchor=anchor,
                             stroke_width=2, stroke_fill=(3, 6, 12, int(200 * a)))
    img.alpha_composite(lay)


def wrap(text, size, max_w):
    """読点で区切って、1 行が max_w に入るようにつなぐ."""
    f = F(size, 900)
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    chunks, cur = [], ""
    for piece in text.replace("、", "、\n").split("\n"):
        if cur and d.textlength(cur + piece, font=f) > max_w:
            chunks.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        chunks.append(cur)
    return chunks


# ---------------- 飾り（HUD・粒子のノイズ・周辺減光） ----------------
yy, xx = np.mgrid[0:H, 0:W]
vig = np.clip(1.15 - 0.55 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2), 0.45, 1.0)
rng = np.random.default_rng(3)
GRAIN = [Image.fromarray(rng.normal(128, 9, (H // 2, W // 2)).clip(0, 255).astype(np.uint8)).resize((W, H)) for _ in range(6)]
SCENES = [(0, "01", "GENESIS"), (7, "02", "NEURAL MAP"), (14.5, "03", "CROWD DATA"), (26, "04", "ANALYST"), (38.5, "05", "FILES"), (50, "06", "LAB")]
CUTS = [14.5, 26, 38.5, 50]


def hud(img, t, fi):
    a = clamp(t / 1.0) * (1 - seg(t, 53.5, 54.5))
    if a <= 0:
        return
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    c = CYAN + (int(150 * a),)
    for (x, y, sx, sy) in ((40, 40, 1, 1), (W - 40, 40, -1, 1), (40, H - 40, 1, -1), (W - 40, H - 40, -1, -1)):
        d.line([(x, y + sy * 46), (x, y), (x + sx * 46, y)], fill=c, width=3)
    d.text((70, 58), "PSYCH DATA LAB", font=F(24), fill=CYAN + (int(220 * a),))
    tc = f"{int(t // 60):02d}:{int(t % 60):02d}:{fi % FPS:02d}"
    d.ellipse([W - 300, 66, W - 286, 80], fill=RED + (int(255 * a * (0.4 + 0.6 * (fi // 15 % 2))),))
    d.text((W - 276, 58), f"REC  {tc}", font=F(24), fill=WHITE + (int(200 * a),))
    sc = [s for s in SCENES if t >= s[0]][-1]
    d.text((70, H - 86), f"SCENE {sc[1]} — {sc[2]}", font=F(22, 500), fill=SUB + (int(200 * a),))
    d.text((W - 70, H - 86), "DATA-DRIVEN PSYCHOLOGY", font=F(22, 500), fill=SUB + (int(200 * a),), anchor="ra")
    img.alpha_composite(lay)


def glitch(img, t):
    """切り替えの瞬間: 色ずれ + 白い光（0.2 秒）."""
    for c in CUTS:
        k = 1 - abs(t - c) / 0.12
        if k > 0:
            r, g, b, a = img.split()
            off = int(18 * k)
            r = ImageChops.offset(r, off, 0)
            b = ImageChops.offset(b, -off, int(off / 3))
            img = Image.merge("RGBA", (r, g, b, a))
            img = Image.blend(img, Image.new("RGBA", img.size, (220, 250, 255, 255)), 0.35 * k)
    return img


# ---------------- 場面ごとの文字 ----------------
def scene_text(img, t, fi, m):
    # 01 はじまり
    kinetic(img, "あなたの心は、", W / 2, 780, t, 1.8, 6.5, size=84, anchor="m")
    kinetic(img, "データで、見える。", W / 2, 890, t, 4.0, 6.5, size=84, hl=("データ",), anchor="m")
    # 02 神経網
    kinetic(img, "約860億個の神経細胞が、つながり合う。", 120, 150, t, 7.5, 13.6, size=54, hl=("約860億個",), weight=700)
    plain(img, "出典：Azevedo et al., 2009", (124, 228), 24, a=seg(t, 8.2, 8.8) * (1 - seg(t, 13.6, 14.0)))
    labels = [("扁桃体", "AMYGDALA", "感情"), ("海馬", "HIPPOCAMPUS", "記憶"), ("前頭前野", "PREFRONTAL CORTEX", "判断")]
    hubs = m.get("hubs")
    if hubs:
        for i, ((x, y, vis), (jp, en, role)) in enumerate(zip(hubs, labels)):
            k = eo(seg(t, 8.6 + i * 1.2, 9.1 + i * 1.2)) * (1 - seg(t, 13.4, 13.9))
            if k <= 0 or not vis:
                continue
            lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            a = int(255 * k)
            lx, ly = x + 150 * (1 if x < W * 0.7 else -1), y - 120
            d.ellipse([x - 26, y - 26, x + 26, y + 26], outline=CYAN + (a,), width=3)
            d.line([(x + 18, y - 18), (lx, ly), (lx + 220 * (1 if lx > x else -1) * k, ly)], fill=CYAN + (a,), width=2)
            tx = lx if lx > x else lx - 220
            d.text((tx, ly - 58), en, font=F(20, 500), fill=SUB + (a,))
            d.text((tx, ly - 36), f"{jp}｜{role}", font=F(40), fill=WHITE + (a,), stroke_width=3, stroke_fill=(3, 6, 12, a))
            img.alpha_composite(lay)
    # 03 群衆
    kinetic(img, "検証 01", 120, 130, t, 15.0, 25.4, size=34, color=CYAN, weight=700)
    kinetic(img, "選択肢が多いと、選べない？", 120, 176, t, 15.2, 25.4, size=72, hl=("選べない",))
    plain(img, "ジャムの試食コーナーの実験（再現イメージ）", (124, 280), 28, a=seg(t, 16.0, 16.6) * (1 - seg(t, 25.4, 25.9)))
    if m.get("groupL"):
        k = seg(t, 19.4, 19.9) * (1 - seg(t, 25.4, 25.9))
        for key, txt in (("groupL", "24種類の売り場"), ("groupR", "6種類の売り場")):
            x, y, _ = m[key]
            plain(img, txt, (x, y + 30), 40, WHITE, k, anchor="ma", weight=700)
        ck = eo(seg(t, 21.4, 23.4))
        if t >= 21.4:
            for key, val, col in (("groupL", 3, SUB), ("groupR", 30, GOLD)):
                x, y, _ = m[key]
                plain(img, f"{round(val * ck)}%", (x, y + 84), 110 if val == 30 else 84, col, k, anchor="ma", weight=900)
        plain(img, "買った人の割合　出典：Iyengar & Lepper, 2000", (W - 120, H - 170), 26, SUB, seg(t, 22.0, 22.6) * (1 - seg(t, 25.4, 25.9)), anchor="ra")
        plain(img, "※あとの研究では、いつも起きるとは限らない", (W - 120, H - 134), 26, SUB, seg(t, 24.0, 24.6) * (1 - seg(t, 25.4, 25.9)), anchor="ra")
    # 04 めたん
    if 26 <= t < 38.6:
        kin = eo(seg(t, 26.0, 26.8)) * (1 - eio(seg(t, 37.7, 38.4)))
        expr = "笑" if t < 29 else ("指" if t < 34.3 else "通常")
        im = metan(fi, expr)
        bob = int(6 * env[fi] * math.sin(fi * 0.9))
        x = int(W - im.width + 60 + (1 - kin) * 700)
        img.alpha_composite(im, (x, H - im.height + 40 + bob)) if x < W else None
        plate_a = kin
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        px, py = W - 560, H - 200
        d.rounded_rectangle([px, py, px + 430, py + 110], radius=10, fill=(6, 10, 18, int(220 * plate_a)), outline=CYAN + (int(255 * plate_a),), width=2)
        d.text((px + 26, py + 14), "ANALYST / 解析担当", font=F(22, 500), fill=CYAN + (int(255 * plate_a),))
        d.text((px + 26, py + 44), "四国めたん", font=F(46), fill=WHITE + (int(255 * plate_a),))
        img.alpha_composite(lay)
        for ln, hl in zip(lines[:3], (("解析担当",), ("研究のデータ",), ("データで確かめて",))):
            t0, t1 = ln["t"], ln["t"] + ln["dur"]
            if t0 - 0.1 <= t < t1 + 0.45:
                for r, p in enumerate(wrap(ln["text"], 60, 1060)):
                    kinetic(img, p, 140, 400 + r * 96, t, t0 + r * 0.5, t1 + 0.1, size=60, hl=hl, stagger=0.03)
    # 05 検証カード
    kinetic(img, "よく聞く、あの話。", W / 2, 900, t, 39.2, 44.2, size=72, anchor="m")
    kinetic(img, "ぜんぶ、データで確かめる。", W / 2, 900, t, 44.8, 49.4, size=72, hl=("データ",), anchor="m")
    if m.get("card1") and 42.0 <= t < 43.9:
        x, y, vis = m["card1"]
        k = eo(seg(t, 42.0, 42.25))
        st = lab.stamp(cfg, "半分本当", "#FFC857", size=110)
        s = 1.6 - 0.6 * k
        st = st.resize((int(st.width * s), int(st.height * s)))
        st.putalpha(st.getchannel("A").point(lambda v: int(v * k * (1 - seg(t, 43.5, 43.9)))))
        img.alpha_composite(st, (int(x - st.width / 2 + 120), int(y - st.height / 2 + 60)))
    # 06 ロゴ
    if t >= 54.3:
        a = seg(t, 54.3, 55.0) * (1 - seg(t, 59.3, 60))
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        sp = 14 * (1 - eo(seg(t, 54.3, 55.6))) + 6
        f = F(36)
        txt = "PSYCH DATA LAB"
        tw = sum(d.textlength(c, font=f) + sp for c in txt) - sp
        x = W / 2 - tw / 2
        for c in txt:
            d.text((x, 420), c, font=f, fill=CYAN + (int(255 * a),))
            x += d.textlength(c, font=f) + sp
        img.alpha_composite(lay)
        kinetic(img, "現代人のための心理学", W / 2, 480, t, 54.9, 59.3, size=92, anchor="m")
        plain(img, "毎日20時　研究データで「よく聞く話」を確かめる", (W / 2, 620), 32, SUB, seg(t, 55.8, 56.4) * (1 - seg(t, 59.3, 60)), anchor="ma")
        b = eo(seg(t, 56.8, 57.2)) * (1 - seg(t, 59.3, 60))
        if b > 0:
            lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            bw, bh = 320, 72
            bx, by = W / 2 - bw / 2, 700 + (1 - b) * 20
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=36, fill=(230, 33, 23, int(255 * b)))
            d.text((W / 2, by + bh / 2), "チャンネル登録", font=F(34), fill=(255, 255, 255, int(255 * b)), anchor="mm")
            img.alpha_composite(lay)


def compose(fi):
    t = fi / FPS
    base = Image.open(f"{M}/{FR}/f{fi:05d}.png").convert("RGBA")
    scene_text(base, t, fi, meta.get(fi, {}) or {})
    hud(base, t, fi)
    base = glitch(base, t)
    arr = np.asarray(base.convert("RGB"), dtype=np.float32)
    arr = arr * vig[..., None] + (np.asarray(GRAIN[fi % len(GRAIN)], dtype=np.float32)[..., None] - 128) * 0.7
    rgb = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    fade = min(seg(t, 0, 0.6), 1 - seg(t, 59.4, 60.0))
    if fade < 1:
        rgb = Image.blend(Image.new("RGB", rgb.size, (0, 0, 0)), rgb, fade)
    return rgb


if __name__ == "__main__":
    a, b = int(sys.argv[1]), int(sys.argv[2])
    import os
    os.makedirs(f"{M}/out", exist_ok=True)
    for fi in range(a, b):
        if not __import__("os").path.exists(f"{M}/{FR}/f{fi:05d}.png"):
            continue
        compose(fi).save(f"{M}/out/c{fi:05d}.jpg", quality=93)
    random.seed(0)
