"""参加型テストの Shorts（quiz.py）: 台本の整形・場面の描画・文字の収まり・メタデータ."""
from __future__ import annotations

import copy

import pytest
from PIL import Image, ImageDraw

from ytecon import quiz
from ytecon.config import load_config


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config(channel="psych"))


def test_normalize_adds_countdown_and_cta_and_renumbers_steps():
    q = quiz.normalize({"title": "t", "hook": "h", "scenes": [
        {"kind": "question", "options": ["a", "b"], "narration": [["x", 0], ["y", 2]]},
        {"kind": "flow", "boxes": [{"text": "1"}], "narration": [["z", 5]]},
    ]})
    kinds = [s["kind"] for s in q["scenes"]]
    assert kinds == ["question", "countdown", "flow", "cta"]
    assert [st for _, st in q["scenes"][0]["narration"]] == [0, 1]
    assert [st for _, st in q["scenes"][2]["narration"]] == [0]


def test_example_scenes_render_and_stay_in_safe_area(cfg):
    th = quiz.theme(cfg)
    assert th.bg.upper() == "#FFFFFF" and th.blue.upper() == "#0017C1"      # dads プリセット
    q = quiz.normalize(copy.deepcopy(quiz._EXAMPLE))
    for sc in q["scenes"]:
        s = quiz.build_scene(cfg, th, sc if sc["kind"] != "countdown" else dict(q["scenes"][0], kind="countdown"))
        img = s.render(s.last_step(), 99.0)
        assert img.size == (1080, 1920)
        dy = quiz._content_offset(s, th, with_extra=sc["kind"] == "countdown")
        moved = quiz._shift(img, dy, th)
        from PIL import ImageChops
        body = moved.crop((0, 230, 1080, 1920))
        bb = ImageChops.difference(body, Image.new("RGB", body.size, th.bg)).getbbox()
        assert bb and bb[1] + 230 >= th.safe_top - 2 and bb[3] + 230 <= th.safe_bottom + 60, (sc["kind"], bb)


def test_box_text_fits_and_breaks_naturally(cfg):
    th = quiz.theme(cfg)
    P = quiz.Parts(cfg, th)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for text in ("「確認させてください」", "気分が乗った日に、まとめてやる", "作業記憶が狭まる", "短い"):
        for w in (400, 600, 900):
            f, lines = P.fit(d, text, w, 64)
            assert "".join(lines) == text
            assert all(d.textlength(ln, font=f) <= w for ln in lines), (text, w, lines)
            assert len(lines) <= 2
    f, lines = P.fit(d, "「確認させてください」", 400, 64)
    assert lines == ["「確認させて", "ください」"]


def test_metadata_has_choices_sources_credit_and_disclaimer(cfg):
    m = quiz.quiz_metadata(cfg, quiz.normalize(copy.deepcopy(quiz._EXAMPLE)))
    assert m.title.endswith("#Shorts") and len(m.title) <= 100
    assert "A. 今日、少しだけ手をつける" in m.description and "B. " in m.description
    assert "Sirois" in m.description and "VOICEVOX：四国めたん" in m.description
    assert "診断や治療の代わり" in m.description and "#心理学" in m.description
    assert m.category_id == "27"


def test_psych_is_voice_only(cfg):
    assert cfg.get("cast.mode") == "solo" and not cfg.get("character.enabled")
    assert int(cfg.get("tts.voicevox.speaker")) == 2
    assert cfg.get("shorts.mode") == "quiz" and cfg.get("video.design") == "dads"
