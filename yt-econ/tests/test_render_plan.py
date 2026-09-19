"""レンダリング計画（どの画像を何秒映すか）の検証."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytecon.render import Scene, plan_scenes, _escape_filter_path
from ytecon.script import Section, VideoScript, Visual
from ytecon.tts import Line, VoiceTrack


def _script(n: int) -> VideoScript:
    return VideoScript(
        topic_title="t", hook="h",
        sections=[Section(heading=f"s{i}", narration="n", visual=Visual())
                  for i in range(n)],
        closing="c", title_candidates=[], description="", tags=[],
        thumbnail_copy={}, sources=[],
    )


def _track(n: int) -> VoiceTrack:
    lines = [Line("hook", 0, "h", 0.0, 10.0)]
    t = 10.0
    for i in range(n):
        lines.append(Line(f"s{i}", 0, "n", t, t + 60.0))
        t += 60.0
    lines.append(Line("closing", 0, "c", t, t + 20.0))
    return VoiceTrack(wav_path=Path("x.wav"), lines=lines)


def test_scenes_cover_the_whole_audio():
    n = 5
    scenes = plan_scenes(_script(n), _track(n), {
        "title": Path("t.jpg"), "outro": Path("o.jpg"),
        **{f"s{i}": Path(f"{i}.jpg") for i in range(n)},
    })
    assert len(scenes) == n + 2
    assert scenes[0].start == 0.0
    # 隣接シーンに隙間が無い
    for a, b in zip(scenes, scenes[1:]):
        assert a.end == pytest.approx(b.start)
    # 最後は音声の終わりより後ろまで伸びる（切れ落ち防止）
    assert scenes[-1].end > _track(n).duration


def test_missing_image_is_skipped_not_fatal():
    scenes = plan_scenes(_script(2), _track(2),
                         {"title": Path("t.jpg"), "s0": Path("0.jpg"),
                          "outro": Path("o.jpg")})
    assert len(scenes) == 3   # s1 の画像が無い分だけ減る


def test_no_scenes_raises():
    from ytecon.render import RenderError
    with pytest.raises(RenderError):
        plan_scenes(_script(1), _track(1), {})


def test_filter_path_escaping():
    assert _escape_filter_path(Path("C:/a/b.ass")) == r"C\:/a/b.ass"


def test_scene_minimum_duration():
    assert Scene(Path("a"), 1.0, 1.0).duration == 0.5


def test_charts_and_cards_are_not_zoomed():
    """図表とテキストカードは静止させる。ズームすると端の出典が切れる。"""
    script = VideoScript(
        topic_title="t", hook="h",
        sections=[
            Section(heading="a", narration="n", visual=Visual(kind="chart")),
            Section(heading="b", narration="n", visual=Visual(kind="textcard")),
            Section(heading="c", narration="n", visual=Visual(kind="stock")),
        ],
        closing="c", title_candidates=[], description="", tags=[],
        thumbnail_copy={}, sources=[],
    )
    scenes = plan_scenes(script, _track(3), {
        "title": Path("t.jpg"), "outro": Path("o.jpg"),
        **{f"s{i}": Path(f"{i}.jpg") for i in range(3)},
    })
    stills = [s.still for s in scenes]
    #      タイトル, chart, textcard, stock, アウトロ
    assert stills == [True, True, True, False, True]
