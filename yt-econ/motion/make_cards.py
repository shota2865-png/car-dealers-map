"""検証カードの画像（3D の通路を飛ぶカード。日本語は three.js では描けないので先に PNG にする）."""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from ytecon.config import load_config  # noqa: E402
from ytecon.quiz import font  # noqa: E402

CLAIMS = ["怒りは6秒で消える？", "選択肢が多いと選べない？", "SNSを見ると疲れる？", "夜になると不安が大きくなる？"]

cfg = load_config(channel="psych")
for i, c in enumerate(CLAIMS):
    W, H = 1280, 720
    im = Image.new("RGBA", (W, H), (8, 20, 34, 215))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W - 1, H - 1], outline=(34, 211, 238, 255), width=6)
    for (x, y, sx, sy) in ((14, 14, 1, 1), (W - 15, 14, -1, 1), (14, H - 15, 1, -1), (W - 15, H - 15, -1, -1)):
        d.line([(x, y + sy * 70), (x, y), (x + sx * 70, y)], fill=(34, 211, 238, 255), width=10)
    d.text((70, 70), f"FILE {i + 1:02d}", font=font(cfg, 44), fill=(34, 211, 238, 255))
    d.rounded_rectangle([W - 330, 60, W - 70, 130], radius=10, outline=(255, 200, 87, 255), width=4)
    d.text((W - 200, 95), "検証中", font=font(cfg, 42), fill=(255, 200, 87, 255), anchor="mm")
    f = font(cfg, 96)
    while d.textlength(c, font=f) > W - 140:
        f = font(cfg, f.size - 4)
    d.text((W / 2, H / 2 + 30), c, font=f, fill=(234, 246, 255, 255), anchor="mm")
    d.line([(70, H - 150), (W - 70, H - 150)], fill=(31, 58, 82, 255), width=3)
    d.text((70, H - 120), "PSYCH DATA LAB  /  研究データで確かめる", font=font(cfg, 36, 500), fill=(143, 179, 204, 255))
    im.save(os.path.join(HERE, f"card_{i}.png"))
