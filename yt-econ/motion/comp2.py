"""PSYCH DATA LAB ショーリール: 3D のコマ（frames/）に、文字・HUD・めたん（口パク）・切り替えの効果を重ねる.

  python comp.py <from> <to>     → out/c00000.jpg ...
"""
import glob
import json
import os as _os0
import random
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, _os0.path.join(_os0.path.dirname(_os0.path.abspath(__file__)), "..", "src"))
from ytecon import character, lab  # noqa: E402
from ytecon.config import load_config  # noqa: E402
from ytecon.quiz import font  # noqa: E402

M = _os0.environ.get("MOTION_DIR", _os0.path.dirname(_os0.path.abspath(__file__)))  # 作業フォルダ（frames2/ audio/ out/）
FR = _os0.environ.get("FR", "frames2")
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


# ---------------- 声（口パク: 経済チャンネルの立ち絵と同じ決め方・同じ速さ） ----------------
from ytecon import character as _ch  # noqa: E402
lines = json.load(open(f"{M}/audio/" + _os0.environ.get("LINES", "lines2.json")))
_TRACK = []                        # (始まり秒, 1 秒 20 回の口の形)
for k, ln in enumerate(lines):
    env20 = _ch.envelope(__import__("pathlib").Path(ln["file"]))
    _TRACK.append((ln["t"], _ch.apply_blinks(cfg, _ch.mouth_states(cfg, env20), seed=7 + k)))


def mouth_state(fi):
    t = fi / FPS
    for t0, st in _TRACK:
        i = int((t - t0) * _ch.FPS)
        if 0 <= i < len(st):
            return st[i]
    return "blink" if (fi % 110) in (0, 1, 2, 3) else "base"


def talking(fi):
    return mouth_state(fi) in ("mouth_half", "mouth_open")


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
    return MET[expr][mouth_state(fi)]


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
SCENES = [(0, "01", "NEURAL DOME"), (10.6, "02", "4D PROJECTION"), (21.4, "03", "BRAIN / TIME"), (33.4, "04", "CROWD PLANET"), (44.4, "05", "ANALYST"), (53, "06", "LAB")]
CUTS = [10.6, 21.4, 33.4, 44.4, 53]


def hud(img, t, fi):
    a = clamp(t / 1.0) * (1 - seg(t, 54.0, 55.0))
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
    # 01 神経細胞の星空ドーム
    kinetic(img, "見上げてください。", W / 2, 470, t, 1.4, 4.6, size=88, anchor="m")
    kinetic(img, "これは、あなたの頭の中。", W / 2, 470, t, 5.0, 9.9, size=88, hl=("あなたの頭の中",), anchor="m")
    plain(img, "約860億個の神経細胞が、星座のようにつながる（Azevedo et al., 2009）", (W / 2, 610), 28, SUB,
          seg(t, 6.2, 6.8) * (1 - seg(t, 9.6, 10.1)), anchor="ma")
    # 02 4 次元
    kinetic(img, "心を、4次元で見る。", 120, 140, t, 11.2, 20.8, size=80, hl=("4次元",))
    kinetic(img, "縦・横・奥行き　＋　時間", 124, 250, t, 13.4, 20.8, size=44, color=SUB, weight=700)
    kinetic(img, "感情は、時間の中で形を変える。", W / 2, 900, t, 16.8, 20.8, size=68, hl=("時間",), anchor="m")
    plain(img, "4D → 3D 射影（テッセラクト／ホップ・ファイブレーション）", (W - 120, H - 150), 22, SUB,
          seg(t, 12, 12.6) * (1 - seg(t, 20.6, 21.2)), anchor="ra")
    # 03 脳と時間
    kinetic(img, "時間とともに、光る場所が変わる。", 120, 140, t, 21.9, 32.8, size=66, hl=("光る場所",))
    plain(img, "※脳の働きのイメージ図です（実際の脳画像ではありません）", (W - 120, H - 150), 22, SUB,
          seg(t, 22.5, 23.1) * (1 - seg(t, 32.6, 33.2)), anchor="ra")
    if m.get("hubs"):
        labels = [("扁桃体", "AMYGDALA", "感情"), ("海馬", "HIPPOCAMPUS", "記憶"), ("前頭前野", "PREFRONTAL CORTEX", "判断")]
        act = m.get("act") or [0, 0, 0]
        for i, ((x, y, vis), (jp, en, role)) in enumerate(zip(m["hubs"], labels)):
            k = clamp(act[i] * 2.2) * (1 - seg(t, 32.4, 33.0))
            if k <= 0.02 or not vis:
                continue
            lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            a = int(255 * k)
            right = x < W * 0.62
            lx, ly = x + (170 if right else -170), y - 130
            d.ellipse([x - 30, y - 30, x + 30, y + 30], outline=CYAN + (a,), width=3)
            d.line([(x + (20 if right else -20), y - 20), (lx, ly), (lx + (240 if right else -240), ly)], fill=CYAN + (a,), width=2)
            tx = lx if right else lx - 240
            d.text((tx, ly - 60), en, font=F(20, 500), fill=SUB + (a,))
            d.text((tx, ly - 38), f"{jp}｜{role}", font=F(42), fill=WHITE + (a,), stroke_width=3, stroke_fill=(3, 6, 12, a))
            img.alpha_composite(lay)
    # 04 惑星の群衆
    kinetic(img, "検証 01", 120, 120, t, 33.8, 44.0, size=34, color=CYAN, weight=700)
    kinetic(img, "選択肢が多いと、選べない？", 120, 166, t, 34.0, 44.0, size=72, hl=("選べない",))
    plain(img, "ジャムの試食コーナーの実験（再現イメージ）", (124, 270), 28, SUB, seg(t, 34.6, 35.2) * (1 - seg(t, 43.8, 44.3)))
    if m.get("groupL"):
        k = seg(t, 35.8, 36.3) * (1 - seg(t, 43.8, 44.3))
        ck = eo(seg(t, 37.0, 38.8))
        for key, txt, val, col in (("groupL", "24種類の売り場", 3, SUB), ("groupR", "6種類の売り場", 30, GOLD)):
            x, y, vis = m[key]
            if not vis:
                continue
            y = min(y, H - 290)                       # カメラが上がっても、数字が画面の下にはみ出さない
            plain(img, txt, (x, y + 40), 40, WHITE, k, anchor="ma", weight=700)
            if t >= 37.0:
                plain(img, f"{round(val * ck)}%", (x, y + 96), 110 if val == 30 else 84, col, k, anchor="ma", weight=900)
        plain(img, "買った人の割合　出典：Iyengar & Lepper, 2000", (W - 120, 130), 26, SUB, seg(t, 38.2, 38.8) * (1 - seg(t, 43.8, 44.3)), anchor="ra")
        plain(img, "※あとの研究では、いつも起きるとは限らない（判定：半分本当）", (W - 120, 166), 26, SUB, seg(t, 40.0, 40.6) * (1 - seg(t, 43.8, 44.3)), anchor="ra")
    # 05 めたん（経済チャンネルと同じ速さの口パク）
    if 44.4 <= t < 53.4:
        kin = eo(seg(t, 44.4, 45.2)) * (1 - eio(seg(t, 52.6, 53.3)))
        expr = "笑" if t < 47.3 else "指"
        im = metan(fi, expr)
        bob = -6 if talking(fi) else 0                     # 話している間は少し上へ（経済の立ち絵と同じ動き）
        x = int(W - im.width + 60 + (1 - kin) * 700)
        if x < W:
            img.alpha_composite(im, (x, H - im.height + 40 + bob))
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        px, py = W - 560, H - 200
        d.rounded_rectangle([px, py, px + 430, py + 110], radius=10, fill=(6, 10, 18, int(220 * kin)), outline=CYAN + (int(255 * kin),), width=2)
        d.text((px + 26, py + 14), "ANALYST / 解析担当", font=F(22, 500), fill=CYAN + (int(255 * kin),))
        d.text((px + 26, py + 44), "四国めたん", font=F(46), fill=WHITE + (int(255 * kin),))
        img.alpha_composite(lay)
        for ln, hl in zip(lines[:2], (("解析担当",), ("研究のデータ",))):
            t0, t1 = ln["t"], ln["t"] + ln["dur"]
            if t0 - 0.1 <= t < t1 + 0.45:
                for r, p in enumerate(wrap(ln["text"], 60, 1060)):
                    kinetic(img, p, 140, 400 + r * 96, t, t0 + r * 0.5, t1 + 0.1, size=60, hl=hl, stagger=0.03)
    # 06 ロゴ
    if t >= 55.0:
        a = seg(t, 55.0, 55.7) * (1 - seg(t, 59.3, 60))
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        sp = 14 * (1 - eo(seg(t, 55.0, 56.3))) + 6
        f = F(36)
        txt = "PSYCH DATA LAB"
        tw = sum(d.textlength(c, font=f) + sp for c in txt) - sp
        x = W / 2 - tw / 2
        for c in txt:
            d.text((x, 420), c, font=f, fill=CYAN + (int(255 * a),))
            x += d.textlength(c, font=f) + sp
        img.alpha_composite(lay)
        kinetic(img, "現代人のための心理学", W / 2, 480, t, 55.5, 59.3, size=92, anchor="m")
        plain(img, "毎日20時　研究データで「よく聞く話」を確かめる", (W / 2, 620), 32, SUB, seg(t, 56.4, 57.0) * (1 - seg(t, 59.3, 60)), anchor="ma")
        b = eo(seg(t, 57.2, 57.6)) * (1 - seg(t, 59.3, 60))
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
