"""2D / 3D アニメの場面（anim）: どのプリセットも縦横で描け、人が描けること."""
from __future__ import annotations

from PIL import Image

from ytecon import anim, docu

PRESETS = {
    "jar_fill": {"values": ["100円", "300円"], "jar_label": "100円単位", "talk": True},
    "coin_stacks": {"values": [1, 3, 5], "labels": ["a", "b", "c"], "tops": ["1", "3", "5"], "hl": [2]},
    "flow": {"nodes": [{"label": "給料", "icon": "note"}, {"label": "税金", "icon": "coin", "hl": True}, {"label": "手取り", "icon": "person"}]},
    "walk": {"marks": [{"label": "30日目", "amount": "4.7万円", "pos": 0.3}]},
    "risk": {"hazards": ["fire", "thief", "lost"]},
    "think": {"thoughts": [{"text": "やるぞ", "mood": "happy"}, {"text": "きつい…", "mood": "worry"}]},
}


def _plan(spec):
    scenes = []
    t = 0.0
    for s in spec:
        lines = [docu.Line(text="x", say="x", start=t + 0.3 + k * 1.0, end=t + 1.1 + k * 1.0) for k in range(3)]
        sc = docu.Scene(spec=s, lines=lines, start=t, end=t + 3.6)
        scenes.append(sc)
        t += 3.6
    return docu.Plan(scenes=scenes, duration=t)


def test_every_preset_draws_in_both_orientations():
    pl = _plan([{"type": "anim", "preset": k, **v} for k, v in PRESETS.items()])
    for size in ((640, 360), (360, 640)):
        r = docu.Renderer(*size, fps=30)
        for sc in pl.scenes:
            for k in (0.2, 0.6, 0.95):
                im = r.frame(pl, sc.start + (sc.end - sc.start) * k)
                assert im.size == size
                assert im.getbbox() is not None


def test_person_returns_hand_and_paints():
    img = Image.new("RGBA", (800, 800), (0, 0, 0, 0))
    hand = anim.person(anim.Pen(img), 200, 380, 300, walk=1.0, mood="tired", hold="coin")
    assert 100 < hand[0] < 320 and 150 < hand[1] < 400
    assert img.getbbox() is not None


def test_jar_with_tiny_level_does_not_crash():
    img = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    for lv in (0.0, 0.001, 0.02, 1.0):
        anim.jar(anim.Pen(img), 300, 500, 200, 260, lv)
