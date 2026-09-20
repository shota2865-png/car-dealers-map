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
