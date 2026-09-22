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


# ----------------------------------------------------------------------
# BGM の混ぜ方（render.audio_chain）を実際に ffmpeg で通して測る
# ----------------------------------------------------------------------
def _rms_db(path: Path, seconds: float = 6.0) -> float:
    import re

    exe = ensure_ffmpeg()
    r = subprocess.run(
        [exe, "-hide_banner", "-t", str(seconds), "-i", str(path), "-vn",
         "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -99.0


def _mix(tmp_path: Path, voice_expr: str, with_bgm: bool, cfg) -> Path:
    from ytecon.bgm import generate_pad
    from ytecon.render import audio_chain

    exe = ensure_ffmpeg()
    voice = tmp_path / "voice.wav"
    r0 = subprocess.run([exe, "-y", "-f", "lavfi", "-i", voice_expr, "-t", "6",
                         "-ac", "1", "-ar", "24000", str(voice)], capture_output=True, text=True)
    assert r0.returncode == 0, r0.stderr[-400:]
    video = tmp_path / "v.mp4"
    subprocess.run([exe, "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=6:r=10",
                    "-pix_fmt", "yuv420p", str(video)], capture_output=True, check=True)
    args = [exe, "-y", "-i", str(video), "-i", str(voice)]
    bgm_idx = None
    if with_bgm:
        pad = generate_pad(tmp_path / "pad.wav", seconds=8)
        bgm_idx = 2
        args += ["-stream_loop", "-1", "-i", str(pad)]
    chain = ";".join(["[0:v]copy[v]"] + audio_chain(cfg, bgm_idx, 6.0))
    out = tmp_path / "out.mp4"
    args += ["-filter_complex", chain, "-map", "[v]", "-map", "[a]", "-t", "6",
             "-c:v", "libx264", "-c:a", "aac", str(out)]
    r = subprocess.run(args, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-600:]
    return out


def test_bgm_is_audible_when_voice_is_silent(tmp_path):
    """声が無い区間では BGM が聞こえること（前回は -45dBFS で実質無音だった）."""
    from ytecon.config import load_config

    cfg = load_config()
    out = _mix(tmp_path, "anullsrc=r=24000:cl=mono", with_bgm=True, cfg=cfg)
    assert _rms_db(out) > -35, "BGM が小さすぎる／混ざっていない"


def test_voice_stays_dominant_over_bgm(tmp_path):
    """声がある区間では、声が BGM より十分大きいこと（3割程度の目安）."""
    from ytecon.config import load_config

    cfg = load_config()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with_voice = _mix(tmp_path / "a", "sine=frequency=220:sample_rate=24000", True, cfg)
    voice_only = _mix(tmp_path / "b", "sine=frequency=220:sample_rate=24000", False, cfg)
    # BGM を足しても全体の音量はほぼ変わらない（＝声が主役のまま）
    assert abs(_rms_db(with_voice) - _rms_db(voice_only)) < 3.0


def test_voice_gain_normalizes_before_mix(tmp_path):
    """声の大きさが違っても、ミックス前に -16 LUFS 付近へ揃うこと."""
    from ytecon.render import VOICE_LUFS, audio_chain, measure_loudness
    from ytecon.config import load_config

    exe = ensure_ffmpeg()
    quiet = tmp_path / "quiet.wav"
    subprocess.run([exe, "-y", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=24000",
                    "-t", "4", "-af", "volume=-6dB", str(quiet)], capture_output=True, check=True)
    lufs = measure_loudness(quiet)
    assert -32 < lufs < -20, lufs   # sine は既定 -18dBFS。-6dB で -27 LUFS 前後
    gain = VOICE_LUFS - lufs
    chain = audio_chain(load_config(), None, 4.0, gain)
    assert f"volume={gain:.2f}dB" in chain[0]
