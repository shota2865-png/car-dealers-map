"""シーン計画（どの画像を何秒映すか）の検証.

「8秒ごとに画を変える」「写真だけ動かし、カードは動かさない」を固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ytecon.config import load_config
from ytecon.render import _escape_filter_path
from ytecon.scenes import Scene, chunk_lines, plan_and_render
from ytecon.script import Caption, Section, Term, VideoScript, Visual
from ytecon.tts import Line, VoiceTrack


@pytest.fixture
def cfg():
    c = load_config()
    c.raw.setdefault("visuals", {})["ai_image_provider"] = ""   # ネットに出ない
    c.raw["visuals"]["motion_backgrounds"] = False            # 背景ループの合成（数十秒）は別テストで
    return c


def _lines(block: str, n: int, start: float, each: float = 3.0) -> list[Line]:
    return [Line(block, i, f"{block}の{i}文目です。", start + i * each, start + (i + 1) * each - 0.3)
            for i in range(n)]


def _script(n_sections: int = 2) -> VideoScript:
    return VideoScript(
        topic_title="円安の話", hook="つかみ。", proof="数字は110円から151円。", promise="今日は三つ。",
        sections=[
            Section(heading=f"見出し{i}", narration="n", on_screen=["a", "b"],
                    visual=Visual(kind="textcard"),
                    captions=[Caption("金利差", "KEYWORD", 0), Caption("月1万円", "DATA", 1)])
            for i in range(n_sections)
        ],
        closing="まとめ。一つ。二つ。三つ。", title_candidates=[], description="", tags=[],
        thumbnail_copy={"main": "円安"}, sources=[{"name": "日本銀行", "url": "https://www.boj.or.jp/"}],
        terms=[Term("実質賃金", "物価を差し引いた買う力", "2023年はマイナス2.5%", 0)],
    )


def _track(script: VideoScript) -> VoiceTrack:
    lines, t = [], 0.0
    for block in ["hook", "proof", "promise"] + [f"s{i}" for i in range(len(script.sections))] + ["closing"]:
        n = 10 if block.startswith("s") else 3
        ls = _lines(block, n, t)
        lines += ls
        t = ls[-1].end + 0.5
    return VoiceTrack(wav_path=Path("x.wav"), lines=lines)


# ----------------------------------------------------------------------
def test_chunk_lines_targets_about_eight_seconds():
    lines = _lines("s0", 12, 0.0, each=3.0)          # 36秒ぶん
    chunks = chunk_lines(lines, target=8.0, lo=4.0, hi=14.0)
    spans = [c[-1].end - c[0].start for c in chunks]
    assert len(chunks) >= 3
    assert all(s <= 14.0 for s in spans)
    assert all(s >= 4.0 for s in spans[:-1])


def test_chunk_lines_merges_a_tiny_tail():
    lines = _lines("s0", 3, 0.0, each=3.0) + [Line("s0", 3, "短い。", 9.0, 9.5)]
    chunks = chunk_lines(lines, target=8.0, lo=4.0, hi=14.0)
    assert (chunks[-1][-1].end - chunks[-1][0].start) >= 4.0 or len(chunks) == 1


def test_scenes_cover_audio_without_gaps(cfg, tmp_path):
    script = _script()
    track = _track(script)
    sc = plan_and_render(cfg, script, track, tmp_path)
    assert sc[0].start == 0.0
    for a, b in zip(sc, sc[1:]):
        assert a.end == pytest.approx(b.start)
    assert sc[-1].end > track.duration


def test_scenes_change_every_few_seconds(cfg, tmp_path):
    """1セクション1枚だった頃に戻っていないこと."""
    script = _script()
    track = _track(script)
    sc = plan_and_render(cfg, script, track, tmp_path)
    assert track.duration / len(sc) < 10.0
    assert max(x.duration for x in sc) <= 14.6


def test_cards_are_still_and_only_photos_move(cfg, tmp_path):
    """写真が取れずカードに落ちた場合も、ズームがかからないこと（前回の不具合）."""
    script = _script()
    script.sections[0].visual = Visual(kind="stock", query="tokyo")   # 写真指定だが取れない
    track = _track(script)
    sc = plan_and_render(cfg, script, track, tmp_path)
    for x in sc:
        if x.kind in ("card", "chart", "title", "outro", "pattern"):
            assert x.still, f"{x.label} が動いてしまう"
    # 写真が取れない環境では動くシーンは 0
    assert all(x.still for x in sc)


def test_scene_variety_within_a_section(cfg, tmp_path):
    script = _script(n_sections=1)
    track = _track(script)
    sc = plan_and_render(cfg, script, track, tmp_path)
    # 隣接シーンの隙間は次のシーンの開始まで埋めるので、開始時刻で所属を判定する
    s0, e0 = track.block_span("s0")
    labels = [x.label.split(":")[0] for x in sc if s0 <= x.start < e0]
    assert len(set(labels)) >= 3, labels


def test_filter_path_escaping():
    assert _escape_filter_path(Path("C:/a/b.ass")) == r"C\:/a/b.ass"


def test_scene_minimum_duration():
    assert Scene(Path("a"), 1.0, 1.0).duration == 0.5


# ----------------------------------------------------------------------
# 画面の細部（ユーザー指摘の再発防止）
# ----------------------------------------------------------------------
def test_segments_share_colour_metadata_so_overlay_never_drops():
    """シーンごとに色の付帯情報が違うと、連結後にフィルタが組み直されて立ち絵が数フレーム消える."""
    from ytecon import render
    assert "colorspace=bt709" in render.COLOR_PARAMS
    assert "-colorspace" in render.COLOR_FLAGS
    import inspect
    src = inspect.getsource(render.render_segment)
    assert src.count("COLOR_FLAGS") >= 2 and src.count("COLOR_PARAMS") >= 2
    assert '"-reinit_filter", "0"' in inspect.getsource(render.render)


def test_subtitle_size_is_uniform(cfg, tmp_path):
    from ytecon import subtitles
    cues = [subtitles.Cue(0.0, 1.0, ["短い行"]), subtitles.Cue(1.0, 2.0, ["とても長い長い長い長い長い長い行"])]
    out = subtitles.write_ass(cfg, cues, tmp_path / "s.ass", reserve_right=400)
    body = out.read_text(encoding="utf-8")
    assert "\\fs" not in body            # 行ごとに縮めない
    assert subtitles.chars_per_line(cfg, 400) <= 12   # 代わりに 1 行の長さを大きさから決める


def test_script_sanitize_fixes_words_notes_and_placeholders():
    from ytecon.script import Card, Diagram, clean_note, fix_words
    assert fix_words("釣りが減る。お釣りは同じ") == "お釣りが減る。お釣りは同じ"
    assert clean_note("台本の給与明細の記述より") == ""
    assert clean_note("台本の例示（仮の数値）") == ""
    assert clean_note("毎月勤労統計調査（厚生労働省）") == "毎月勤労統計調査（厚生労働省）"
    sec = Section(heading="同じ買い物なのに、釣りが減る", narration="釣りが減ったのだ。",
                  visual=Visual(kind="stock"),
                  captions=[Caption(text="数十円の値上がり（例）", type="DATA"), Caption(text="約5.1%", type="DATA")],
                  cards=[Card(text="内容量は1割ほど減(例)")],
                  diagrams=[Diagram(type="flow", title="釣りが減る感覚", items=["釣りが減る"], note="台本の売り場の描写より")])
    script = VideoScript(topic_title="t", hook="h", sections=[sec], closing="c", title_candidates=[],
                         description="", tags=[], thumbnail_copy={}, sources=[])
    from ytecon.script import _sanitize
    _sanitize(script)
    assert sec.heading == "同じ買い物なのに、お釣りが減る"
    assert [c.text for c in sec.captions] == ["約5.1%"]
    assert sec.cards == []
    assert sec.diagrams[0].note == "" and sec.diagrams[0].items == ["お釣りが減る"]


def test_glass_panel_fits_content_and_bar_aligns(cfg, tmp_path):
    """すりガラスの面は中身の大きさ、見出しの縦線は文字の上下に合う、黒縁は付かない."""
    import copy
    from PIL import Image
    from ytecon import assets
    cfg = copy.deepcopy(cfg)
    cfg.raw["visuals"]["motion_backgrounds"] = True     # 透過カード（すりガラス）を作る経路
    p = assets.render_diagram(cfg, "compare", "行動経済学 vs マクロ経済",
                              ["見るもの|感じ方のクセ|お金の流れ", "焦点|受け取り方|順番"], "", tmp_path / "c.png")
    im = Image.open(p)
    w, h = im.size
    a = im.getchannel("A")
    # 画面の四隅は「暗くするだけ」の薄い透過（面が画面いっぱいではない）
    dim = a.getpixel((20, 20))
    assert 0 < dim < 160
    assert a.getpixel((w // 2, h // 2)) > dim          # 中央には面がある
    assert a.getpixel((w // 2, h - 40)) == dim         # 字幕帯には面が掛からない
    # 見出しつきの図: 縦線の上下が見出し文字の上下と一致する
    p2 = assets.render_diagram(cfg, "steps", "給与明細で起きたこと", ["額面", "手取り"], "", tmp_path / "s.png")
    im2 = Image.open(p2).convert("RGBA")
    pal = assets.palette(cfg)
    accent = assets._rgb(pal["accent"])
    from ytecon import design
    m = design.safe_margin(cfg)
    ys = [y for y in range(im2.height) if im2.getpixel((m + 5, y))[:3] == accent and im2.getpixel((m + 5, y))[3] > 200]
    assert ys, "縦線が無い"
    bar_top, bar_bottom = min(ys), max(ys)
    # 文字（白）の上下
    text = assets._rgb(pal["text"])
    tys = [y for y in range(bar_top - 30, bar_bottom + 30)
           for x in range(m + 30, m + 400, 2) if im2.getpixel((x, y))[:3] == text and im2.getpixel((x, y))[3] > 200]
    assert abs(min(tys) - bar_top) <= 6 and abs(max(tys) - bar_bottom) <= 6


def test_heading_overlay_has_no_black_outline(cfg, tmp_path):
    import copy
    from PIL import Image
    from ytecon import assets
    cfg = copy.deepcopy(cfg)
    cfg.raw["visuals"]["motion_backgrounds"] = True
    p = assets.render_heading_overlay(cfg, "同じ買い物なのに、お釣りが減る", ["値札は週に何十回も見る"], tmp_path / "h.png")
    im = Image.open(p).convert("RGBA")
    # 文字のまわりに濃い黒（縁取り）のピクセルが無い（面の色は紺、文字は白）
    px = [im.getpixel((x, y)) for x in range(0, im.width, 3) for y in range(0, im.height, 3)]
    outline_like = [p for p in px if p[3] > 200 and max(p[:3]) < 20]
    assert not outline_like


@pytest.mark.parametrize("text,expected", [
    ("普通ならこう思うはずなのだ。", ["普通なら", "こう思うはずなのだ"]),                 # 「思うは｜ず」にしない
    ("この差で、次に打てる手が変わるのだ。", ["この差で", "次に打てる手が", "変わるのだ"]),  # 「打て｜る」にしない
    ("価格は据え置きで、中身だけを減らすやり方なのだ。", ["価格は据え置きで", "中身だけを", "減らすやり方なのだ"]),
    ("2025年もおよそ5.2パーセントだった。", ["2025年もおよそ", "5.2パーセントだった"]),     # 数字と単位を割らない
    ("ここから少しややこしくなるのだ。", ["ここから", "少しややこしくなるのだ"]),           # 語を割るより 1 字はみ出す
    ("さらに、問題はもう一段ある。", ["さらに、問題は", "もう一段ある"]),                   # 2〜3 字を孤立させない
    ("ところが給与明細の額面は、去年とほとんど同じ。", ["ところが給与明細の", "額面は、去年と", "ほとんど同じ"]),
])
def test_phrase_split_keeps_words_whole(text, expected):
    from ytecon.subtitles import phrase_split
    assert phrase_split(text, 10) == expected


def test_highlight_stages_follow_the_speech():
    from ytecon.scenes import _stages
    lines = [Line("s0", i, f"文{i}", 10 + i * 2.5, 12 + i * 2.5) for i in range(4)]
    st = _stages(10, 20, 3, lines)
    assert [a for a, _, _ in st] == [None, 0, 1, 2]          # 全体 → 1 行ずつ
    assert st[0][1] == 10 and st[-1][2] == 20                 # 隙間なく覆う
    assert all(b[1] == a[2] for a, b in zip(st, st[1:]))
    assert _stages(10, 13, 3, lines) == [(None, 10, 13)]      # 短い場面は段階を作らない


def test_diagram_highlight_changes_only_the_active_row(cfg, tmp_path):
    import copy
    from PIL import Image, ImageChops
    from ytecon import assets
    cfg = copy.deepcopy(cfg)
    cfg.raw["visuals"]["motion_backgrounds"] = True
    items = ["価格は据え置き", "中身だけ1割減", "実質的な値上げ"]
    a = Image.open(assets.render_diagram(cfg, "steps", "t", items, "", tmp_path / "a.png", active=0)).convert("RGB")
    b = Image.open(assets.render_diagram(cfg, "steps", "t", items, "", tmp_path / "b.png", active=1)).convert("RGB")
    plain = Image.open(assets.render_diagram(cfg, "steps", "t", items, "", tmp_path / "c.png")).convert("RGB")
    assert ImageChops.difference(a, b).getbbox() is not None
    assert ImageChops.difference(a, plain).getbbox() is not None
    assert assets.diagram_rows("balance", items) == 3 and assets.diagram_rows("table", items[:2]) == 2


def test_diagram_scenes_share_background_and_skip_fade(cfg, tmp_path):
    import copy
    from ytecon.scenes import _Painter
    from ytecon.script import Diagram
    cfg = copy.deepcopy(cfg)
    cfg.raw["visuals"]["motion_backgrounds"] = True
    painter = _Painter(cfg, tmp_path)
    lines = [Line("s0", i, f"文{i}", 10 + i * 2.5, 12 + i * 2.5) for i in range(4)]
    sc = painter.diagram(Diagram(type="steps", title="t", items=["a", "b", "c"]), 10, 20, lines=lines)
    assert len(sc) == 4
    assert sc[0].fade_in and not any(s.fade_in for s in sc[1:])
    if sc[0].background is not None:
        assert all(s.background == sc[0].background for s in sc)
        assert sc[1].bg_offset > sc[0].bg_offset


def test_backgrounds_do_not_repeat_back_to_back(cfg):
    import copy
    from ytecon import footage
    cfg = copy.deepcopy(cfg)
    cfg.raw["visuals"]["motion_backgrounds"] = True
    picker = footage.Picker(cfg)
    if len(picker.lib) + len(picker.loops) < 2:
        pytest.skip("素材が足りない")
    seq = [picker.abstract("economy office", seed=i) for i in range(12)]
    assert all(a != b for a, b in zip(seq, seq[1:]))


def test_outro_text_uses_publish_time(cfg, tmp_path):
    from ytecon import assets
    p = assets.build_outro_card(cfg, tmp_path / "o.png")
    assert p.exists()
    assert cfg.get("upload.publish_times_jst") == ["19:00"] and cfg.get("pipeline.videos_per_day") == 1


def test_subtitle_cues_never_overlap(cfg):
    from ytecon.subtitles import build_cues
    track = VoiceTrack(wav_path=Path("x.wav"), lines=[
        Line("s0", 0, "これをシュリンクフレーションと呼ぶ。", 0.0, 3.0),
        Line("s0", 1, "実質的な値上げ、という意味の言葉。", 2.98, 6.0)])   # 音声の実測は少し重なることがある
    cues = build_cues(cfg, track, reserve_right=400)
    assert all(a.end <= b.start + 1e-6 for a, b in zip(cues, cues[1:]))
