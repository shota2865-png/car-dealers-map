"""スタイルバイブル・デザイントークン・立ち絵合成のテスト."""
import copy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from ytecon.config import load_config
from ytecon import bible, design
from ytecon.tts import Line


@pytest.fixture
def cfg():
    # load_config() は共有インスタンスを返すので、テストで書き換える前に複製する
    return copy.deepcopy(load_config())


# --- スタイルバイブル -------------------------------------------------
def test_bible_has_nine_blocks_in_order(cfg):
    ids = bible.beat_ids(cfg)
    assert ids[0] == "PARADOX_HOOK" and ids[-1] == "REFLECTIVE_ENDING"
    assert bible.section_beats(cfg) == ["FAMILIAR_SCENE", "ACADEMIC_LENS", "EVIDENCE_DROP",
                                        "MECHANISM_REVEAL", "PERSPECTIVE_FLIP", "HUMAN_RETURN"]


def test_beats_scale_to_other_section_counts(cfg):
    # 4 セクションでも順序が保たれ、最初と最後は固定
    got = [bible.beat_for_section(cfg, i, 4) for i in range(4)]
    assert got[0] == "FAMILIAR_SCENE" and got[-1] in ("PERSPECTIVE_FLIP", "HUMAN_RETURN")
    assert got == sorted(got, key=bible.section_beats(cfg).index)


def test_bgm_mood_follows_the_arc(cfg):
    moods = [bible.bgm_mood_for_beat(cfg, bible.beat_for_block(cfg, b, 6))
             for b in ("hook", "s0", "s3", "s5", "closing")]
    assert moods == ["ambient", "curiosity", "tension", "reflective", "reflective"]


def test_title_and_thumbnail_rules(cfg):
    assert bible.title_matches(cfg, "なぜ給料は上がらないのか？")
    assert not bible.title_matches(cfg, "円安について解説")
    assert bible.thumbnail_overlaps_title(cfg, "給料が上がらない", "なぜ給料は上がらないのか？")
    assert not bible.thumbnail_overlaps_title(cfg, "置いていかれる", "なぜ給料は上がらないのか？")


def test_legacy_beats_are_mapped():
    from ytecon.script import VideoScript
    s = VideoScript.from_dict({"topic_title": "t", "hook": "h", "closing": "c",
                               "sections": [{"heading": "a", "narration": "b", "beat": "STORY"}]})
    assert s.sections[0].beat == "ACADEMIC_LENS"


def test_prompt_block_mentions_semantic_cut_and_titles(cfg):
    text = bible.render_for_prompt(cfg, 6)
    assert "Semantic Cut" in text and "しかし" in text
    assert "なぜ{X}は{Y}なのか？" in text


# --- 接続詞でのカット -----------------------------------------------
def test_chunk_lines_cuts_before_pivot_words(cfg):
    from ytecon.scenes import chunk_lines
    words = bible.semantic_cut_words(cfg)
    lines = [Line("s0", 0, "普通はこう思う。", 0.0, 2.5),
             Line("s0", 1, "みんなそう考える。", 2.5, 5.0),
             Line("s0", 2, "しかし、数字は逆を向いている。", 5.0, 7.5),
             Line("s0", 3, "実際に見てみよう。", 7.5, 10.0)]
    plain = chunk_lines(lines, target=8.0, lo=4.0, hi=14.0)
    cut = chunk_lines(lines, target=8.0, lo=4.0, hi=14.0, pivots=words)
    assert len(plain) == 1
    assert len(cut) == 2 and cut[1][0].text.startswith("しかし")


# --- デザイントークン -------------------------------------------------
@pytest.mark.parametrize("preset", ["hybrid", "digital_agency", "apple", "material3"])
def test_design_presets_are_complete(preset):
    cfg = copy.deepcopy(load_config())
    cfg.raw.setdefault("video", {})["design"] = preset
    design._load_file.cache_clear()
    t = design.tokens(cfg)
    assert t["name"] == preset
    for key in ("bg", "surface", "surface_high", "outline", "text", "accent", "positive", "negative"):
        assert key in t["colors"], key
    sizes = t["type"]
    # 序列が崩れていないこと（display > headline > body > label）
    assert sizes["display_l"] > sizes["headline_l"] > sizes["body_l"] > sizes["label"]
    assert design.radius(cfg, "l") >= design.radius(cfg, "m") >= design.radius(cfg, "s")
    assert 1 <= design.fade_frames(cfg, 30) <= 15


def test_palette_merges_tokens_then_overrides():
    from ytecon.assets import palette
    cfg = copy.deepcopy(load_config())
    cfg.raw.setdefault("video", {})["design"] = "apple"
    cfg.raw.setdefault("visuals", {})["palette"] = {"accent": "#123456"}
    design._load_file.cache_clear()
    pal = palette(cfg)
    assert pal["bg"] == "#000000" and pal["accent"] == "#123456"
    design._load_file.cache_clear()


def test_unknown_preset_falls_back_to_default():
    cfg = copy.deepcopy(load_config())
    cfg.raw.setdefault("video", {})["design"] = "nope"
    design._load_file.cache_clear()
    assert design.tokens(cfg)["name"] == design.DEFAULT_PRESET


# --- 立ち絵（YMM4 形式）の合成 ---------------------------------------
def _png(path: Path, draw):
    im = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    draw(ImageDraw.Draw(im))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def test_compose_ymm_builds_four_states(tmp_path, cfg):
    from ytecon import character
    src = tmp_path / "ずんだもん"
    _png(src / "体" / "00.png", lambda d: d.rectangle([60, 150, 140, 290], fill=(0, 200, 0, 255)))
    _png(src / "顔" / "00.png", lambda d: d.ellipse([50, 40, 150, 160], fill=(255, 220, 190, 255)))
    _png(src / "口" / "00.png", lambda d: d.line([(90, 120), (110, 120)], fill=(200, 0, 0, 255), width=3))
    _png(src / "口" / "00.0.png", lambda d: d.ellipse([92, 115, 108, 125], fill=(200, 0, 0, 255)))
    _png(src / "口" / "00.1.png", lambda d: d.ellipse([88, 110, 112, 132], fill=(200, 0, 0, 255)))
    _png(src / "目" / "00.png", lambda d: d.ellipse([70, 80, 90, 100], fill=(0, 0, 0, 255)))
    _png(src / "目" / "00.0.png", lambda d: d.line([(70, 90), (90, 90)], fill=(0, 0, 0, 255), width=2))
    cfg.raw.setdefault("character", {})["dir"] = str(src)
    found = character.find_ymm_dir(cfg)
    assert found == src
    out = character.compose_ymm(cfg, found, tmp_path / "composed")
    assert set(out) == set(character.STATES)
    sizes = {Image.open(p).size for p in out.values()}
    assert len(sizes) == 1                       # 4 枚とも同じ寸法
    # 口を開けた絵は閉じた絵と違う
    assert Image.open(out["mouth_open"]).tobytes() != Image.open(out["base"]).tobytes()
    assert Image.open(out["blink"]).tobytes() != Image.open(out["base"]).tobytes()
    # 2 回目はキャッシュ（stamp）で同じ結果
    again = character.compose_ymm(cfg, found, tmp_path / "composed")
    assert again == out


def test_variants_parse_ymm_names(tmp_path):
    from ytecon.character import _variants
    for name in ("00.png", "00.0.png", "00.1.png", "01.png", "!02.png"):
        _png(tmp_path / name, lambda d: None)
    v = _variants(tmp_path)
    assert set(v) == {"00", "01", "02"}
    assert [p.name for p in v["00"]["frames"]] == ["00.0.png", "00.1.png"]


# --- フッテージ（動く背景） -----------------------------------------
def _fake_clip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 16)


def test_footage_library_tags_from_names_and_yaml(tmp_path, cfg, monkeypatch):
    from ytecon import footage
    root = tmp_path / "footage"
    _fake_clip(root / "broll" / "tokyo_street_night_4k.mp4")
    _fake_clip(root / "abstract" / "blue_particles_loop.mov")
    (root / "tags.yaml").write_text(
        "broll/tokyo_street_night_4k.mp4:\n  tags: [crowd, commuters]\n", encoding="utf-8")
    cfg.raw.setdefault("visuals", {})["footage_dir"] = str(root)
    monkeypatch.setattr(footage, "_probe_duration", lambda p: 15.0)
    lib = footage.library(cfg)
    by = {c.name: c for c in lib}
    assert by["tokyo_street_night_4k.mp4"].kind == "broll"
    assert {"tokyo", "street", "night", "crowd", "commuters"} <= by["tokyo_street_night_4k.mp4"].tags
    assert "4k" not in by["tokyo_street_night_4k.mp4"].tags
    assert by["blue_particles_loop.mov"].kind == "abstract"


def test_footage_pick_prefers_matching_and_unused(tmp_path):
    from collections import Counter
    from ytecon.footage import Clip, pick
    a = Clip(tmp_path / "a.mp4", "broll", {"tokyo", "street"}, 10)
    b = Clip(tmp_path / "b.mp4", "broll", {"supermarket", "price"}, 10)
    used = Counter()
    assert pick([a, b], "supermarket shelves price tag", "broll", used).name == "b.mp4"
    used["b.mp4"] = 3
    # 使い過ぎたものより、語が合わなくても未使用を選ぶことがある → 語一致の重みの方が強いことを確認
    assert pick([a, b], "supermarket price", "broll", used, seed=1).name == "b.mp4"
    assert pick([a, b], "nothing matches", "abstract", used) is None


def test_picker_disabled_returns_nothing(cfg):
    from ytecon import footage
    cfg.raw.setdefault("visuals", {})["motion_backgrounds"] = False
    pk = footage.Picker(cfg)
    assert pk.abstract("x") is None and pk.broll("tokyo") is None


def test_wrap_does_not_start_a_line_with_punctuation(cfg):
    from PIL import Image, ImageDraw
    from ytecon.assets import _wrap, load_font
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    f = load_font(cfg, 92, "black")
    lines = _wrap(d, "なぜ給料は上がらないのか？", f, 92 * 12 + 10)
    assert all(not ln.startswith("？") for ln in lines)
    assert "".join(lines) == "なぜ給料は上がらないのか？"


def test_render_segment_composites_card_over_background(tmp_path, cfg):
    """透過カード + 背景ループ → 指定尺の動画になる（動く背景の経路）."""
    from ytecon import footage
    from ytecon.render import probe_duration, render_segment
    from ytecon.scenes import Scene
    from ytecon.assets import render_keyword_card

    cfg.raw.setdefault("video", {})["resolution"] = [320, 180]
    cfg.raw["video"]["fps"] = 10
    cfg.raw.setdefault("visuals", {})["motion_backgrounds"] = True
    loop = footage.generate_loop(cfg, "grid", tmp_path / "loop.mp4", seconds=1.0, size=(320, 180), fps=10)
    card = render_keyword_card(cfg, "実質賃金", "本当の給料", tmp_path / "kw.jpg")
    assert card.suffix == ".png"                         # 透過で出ている
    scene = Scene(card, 0.0, 2.5, True, "card", "t", background=loop, bg_offset=0.3)
    out = render_segment(cfg, scene, tmp_path / "seg.mp4", 0)
    assert abs(probe_duration(out) - 2.5) < 0.3
