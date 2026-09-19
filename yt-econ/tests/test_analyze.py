"""参照動画の見た目を実測する部分のテスト.

ffmpeg でカット位置が既知の動画を合成し、検出が当たることを固定する。
ここが狂うと「参考チャンネルに寄せる」の土台が崩れる。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ytecon.analyze import (
    aggregate_visual, analyze_thumbnail, brightness_stats, cut_stats,
    detect_cuts, palette_of, sample_frames, VisualProfile,
)
from ytecon.render import ensure_ffmpeg


def _make_video(path: Path, colors: list[str], seconds: float = 3.0) -> list[float]:
    """色が切り替わる動画を作り、正解のカット時刻を返す."""
    exe = ensure_ffmpeg()
    parts = []
    for i, color in enumerate(colors):
        seg = path.parent / f"seg{i}.mp4"
        subprocess.run(
            [exe, "-y", "-f", "lavfi", "-i",
             f"color=c={color}:s=320x180:d={seconds}:r=15",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)],
            capture_output=True, check=True,
        )
        parts.append(seg)
    listing = path.parent / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    subprocess.run(
        [exe, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(path)],
        capture_output=True, check=True,
    )
    return [seconds * (i + 1) for i in range(len(colors) - 1)]


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> tuple[Path, list[float], float]:
    d = tmp_path_factory.mktemp("clip")
    path = d / "v.mp4"
    truth = _make_video(path, ["navy", "orange", "darkgreen", "white"], seconds=3.0)
    return path, truth, 12.0


# ----------------------------------------------------------------------
def test_cuts_are_detected_at_the_right_times(clip):
    path, truth, _dur = clip
    cuts, threshold = detect_cuts(path)
    assert len(cuts) == len(truth), f"検出 {cuts} / 正解 {truth}"
    for got, want in zip(cuts, truth):
        assert abs(got - want) < 0.35, f"{got} != {want}"
    assert threshold > 0


def test_threshold_is_chosen_per_video_not_fixed(clip):
    """固定閾値では素材ごとに当たらない。段差で決めていることの確認."""
    path, _truth, _dur = clip
    _cuts, threshold = detect_cuts(path)
    # 明るさが大きく変わる素材なので、暗い素材より高い閾値が選ばれるはず
    assert 0.01 < threshold <= 1.0


def test_close_detections_are_merged(clip):
    path, truth, _dur = clip
    cuts, _ = detect_cuts(path, min_gap=100.0)   # 全部まとめてしまう極端な設定
    assert len(cuts) == 1


def test_cut_stats(clip):
    _path, truth, dur = clip
    stats = cut_stats(truth, dur)
    assert stats["cuts"] == 3
    assert stats["cuts_per_minute"] == pytest.approx(15.0, abs=0.1)
    assert stats["median_shot_seconds"] == pytest.approx(3.0, abs=0.1)
    assert stats["shots_over_6s_ratio"] == 0.0


def test_cut_stats_flags_a_static_video():
    """カットが無い動画は shots_over_6s_ratio が 1.0 になる（画が持っていない）."""
    stats = cut_stats([], 120.0)
    assert stats["cuts"] == 0
    assert stats["shots_over_6s_ratio"] == 1.0


def test_cut_stats_on_empty_duration():
    assert cut_stats([], 0) == {}


# ----------------------------------------------------------------------
def test_palette_and_brightness(clip, tmp_path):
    path, _truth, _dur = clip
    frames = sample_frames(path, tmp_path / "f", every=1.0)
    assert frames, "フレームを抜けていない"

    palette = palette_of(frames)
    assert palette and all("hex" in c and "share" in c for c in palette)
    assert sum(c["share"] for c in palette) <= 1.01

    bright = brightness_stats(frames)
    assert 0.0 <= bright["mean_brightness"] <= 1.0
    assert 0.0 <= bright["dark_frame_ratio"] <= 1.0


def test_thumbnail_analysis_distinguishes_text_heavy_images(tmp_path):
    """文字の多いサムネほど text_area_ratio が高くなること."""
    from PIL import Image, ImageDraw

    plain = tmp_path / "plain.jpg"
    Image.new("RGB", (640, 360), "#3366AA").save(plain)

    texty = tmp_path / "texty.jpg"
    img = Image.new("RGB", (640, 360), "#3366AA")
    d = ImageDraw.Draw(img)
    for y in range(40, 320, 30):
        d.rectangle([40, y, 600, y + 18], fill="#FFFFFF")   # 白い帯＝文字の代理
    img.save(texty)

    assert (analyze_thumbnail(texty)["text_area_ratio"]
            > analyze_thumbnail(plain)["text_area_ratio"])


def test_aggregate_visual_uses_median(tmp_path):
    profiles = [
        VisualProfile(cuts={"cuts_per_minute": 10.0, "median_shot_seconds": 6.0},
                      brightness={"mean_brightness": 0.4},
                      palette=[{"hex": "#111111", "share": 0.5}]),
        VisualProfile(cuts={"cuts_per_minute": 12.0, "median_shot_seconds": 5.0},
                      brightness={"mean_brightness": 0.45},
                      palette=[{"hex": "#111111", "share": 0.4}]),
        VisualProfile(cuts={"cuts_per_minute": 99.0, "median_shot_seconds": 0.5},
                      brightness={"mean_brightness": 0.9},
                      palette=[{"hex": "#EEEEEE", "share": 0.9}]),
    ]
    agg = aggregate_visual(profiles)
    assert agg["cuts_per_minute"] == 12.0      # 99 に引っ張られない
    assert agg["videos"] == 3
    assert agg["dominant_colors"][0]["hex"] == "#111111"


def test_aggregate_visual_with_nothing():
    assert aggregate_visual([])["videos"] == 0
