from PIL import Image, ImageDraw

from ytecon import thumbpanel as T
from ytecon.config import load_config


def test_arrow_that_the_font_lacks_becomes_words():
    cfg = load_config()
    assert T._glyphs(cfg, "税8%→1%") == "税8%から1%"
    assert "→" not in T._glyphs(cfg, "A→B→C") and "»" in T._glyphs(cfg, "A→B→C")
    assert T._glyphs(cfg, "店内は10%") == "店内は10%"


def test_fill_gaps_uses_only_empty_space():
    img = Image.new("RGBA", (400, 300), "navy")
    occ = Image.new("L", (400, 300), 0)
    ImageDraw.Draw(occ).rectangle([0, 0, 199, 299], fill=255)        # 左半分はもう埋まっている
    before = img.copy()
    n = T.fill_gaps(img, occ, (0, 0, 400, 300), ["💰"], max_items=3)
    if T.emoji_image("💰", 50) is None:                                # 絵文字の字体が無い環境では何も置かない
        assert n == 0
        return
    assert n >= 1
    left = Image.new("RGBA", (190, 300))
    left.paste(before.crop((0, 0, 190, 300)))
    assert list(img.crop((0, 0, 190, 300)).getdata()) == list(left.getdata())   # 埋まっている所には置かない


def test_occupancy_marks_only_changed_pixels():
    base = Image.new("RGB", (100, 100), "red")
    now = base.copy()
    ImageDraw.Draw(now).rectangle([40, 40, 60, 60], fill="white")
    occ = T.occupancy(base, now)
    assert occ.getpixel((50, 50)) == 255 and occ.getpixel((5, 5)) == 0
