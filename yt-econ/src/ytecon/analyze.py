"""参照動画の「見た目」を実測する.

learn.py が文字起こしから語り口を測るのに対し、こちらは映像そのものから
カットの速さ・配色・サムネの作りを測る。

  カット頻度  : ffmpeg のシーン検出。何秒に1回画が変わっているか
  配色        : フレームを間引いて量子化し、支配色を出す
  サムネ      : 支配色・明度・彩度・文字が占める割合の推定

「〇〇チャンネルっぽく」を感覚で真似ると必ずブレるので、数値にして
presets に落とす。動画はすべて低解像度で落とすので、帯域も時間も食わない。
"""

from __future__ import annotations

import logging
import re
import shutil
import statistics
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class AnalyzeError(RuntimeError):
    pass


def _ffmpeg() -> str:
    from .render import ensure_ffmpeg
    return ensure_ffmpeg()


# ----------------------------------------------------------------------
# カット検出
# ----------------------------------------------------------------------
_PTS = re.compile(r"pts_time:([0-9.]+)")


def scene_scores(video: str | Path) -> list[tuple[float, float]]:
    """全フレームの (時刻, シーンスコア) を取り出す."""
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(video),
         "-filter:v", "select='gte(scene,0)',metadata=print:file=-",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    blob = proc.stdout + proc.stderr
    pairs = re.findall(r"pts_time:([0-9.]+).*?lavfi\.scene_score=([0-9.]+)",
                       blob, re.S)
    return [(float(t), float(v)) for t, v in pairs]


def detect_cuts(video: str | Path, min_gap: float = 0.5,
                noise_floor: float = 0.01) -> tuple[list[float], float]:
    """カット時刻の列と、採用した閾値を返す.

    固定閾値は使わない。実測すると、暗い図解中心の動画は隣接フレームの差が
    小さく 0.3 では1つも拾えず、逆に動きの多い実写で 0.02 まで下げると
    カメラの揺れやテロップの出入りまで拾ってしまう。閾値は素材ごとに違う。

    そこで全フレームのスコアを取り、**上位で最も大きな段差**を探して
    そこで切る。本物のカットはスコアが飛び抜けるので、段差が出る。
    （検証: 4カットの動画で上位4つが 0.122/0.117/0.116/0.067、
      5番目が 0.021。ここに3.2倍の段差があり、正しく4つに分かれる）
    """
    scored = [(t, v) for t, v in scene_scores(video) if v >= noise_floor]
    if not scored:
        return [], noise_floor

    ranked = sorted((v for _t, v in scored), reverse=True)
    # 上位だけ見る。全体を見ると小さな揺らぎの中の段差を拾ってしまう
    head = ranked[: max(3, min(len(ranked), 60))]

    best_ratio, split = 1.0, len(head)
    for i in range(len(head) - 1):
        ratio = head[i] / max(head[i + 1], 1e-6)
        if ratio > best_ratio:
            best_ratio, split = ratio, i + 1

    if best_ratio >= 1.8:
        threshold = head[split - 1]
    else:
        # 段差が無い＝残った候補はどれも本物のカット、という状況。
        # （色が切り替わるだけの素材だと、カットのスコアが軒並み 1.0 付近に
        #   並び、ノイズ除去で他が全部消えるためこうなる）
        # ここで平均より上に閾値を上げると、本物のカットまで捨ててしまう。
        threshold = min(head)

    cuts = sorted(t for t, v in scored if v >= threshold)
    merged: list[float] = []
    for t in cuts:
        if not merged or t - merged[-1] >= min_gap:
            merged.append(t)
    return merged, round(threshold, 4)


def cut_stats(cuts: list[float], duration: float) -> dict[str, Any]:
    """カットの速さを数値にする."""
    if duration <= 0:
        return {}
    shots = []
    prev = 0.0
    for t in cuts:
        shots.append(t - prev)
        prev = t
    shots.append(duration - prev)
    shots = [s for s in shots if s > 0]
    if not shots:
        return {"cuts": 0, "cuts_per_minute": 0.0}
    return {
        "cuts": len(cuts),
        "cuts_per_minute": round(len(cuts) / (duration / 60), 1),
        "median_shot_seconds": round(statistics.median(shots), 2),
        "mean_shot_seconds": round(statistics.mean(shots), 2),
        "longest_shot_seconds": round(max(shots), 1),
        # 長すぎるショットの割合。ここが高いと「画が持っていない」
        "shots_over_6s_ratio": round(
            sum(1 for s in shots if s > 6) / len(shots), 2),
    }


# ----------------------------------------------------------------------
# 配色
# ----------------------------------------------------------------------
def sample_frames(video: str | Path, outdir: Path, every: float = 5.0,
                  width: int = 320) -> list[Path]:
    """一定間隔でフレームを抜く."""
    outdir.mkdir(parents=True, exist_ok=True)
    pattern = outdir / "f_%04d.png"
    subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-i", str(video),
         "-vf", f"fps=1/{every},scale={width}:-1", str(pattern)],
        capture_output=True, text=True,
    )
    return sorted(outdir.glob("f_*.png"))


def palette_of(paths: list[Path], colors: int = 6) -> list[dict[str, Any]]:
    """支配色を頻度順に返す."""
    from PIL import Image

    counter: Counter[tuple[int, int, int]] = Counter()
    for path in paths:
        img = Image.open(path).convert("RGB").resize((160, 90))
        quant = img.quantize(colors=colors, method=Image.MEDIANCUT)
        pal = quant.getpalette() or []
        for count, index in quant.convert("P").getcolors(1 << 16) or []:
            rgb = tuple(pal[index * 3: index * 3 + 3])
            if len(rgb) == 3:
                # 近い色をまとめる（16段階に丸める）
                counter[tuple(v // 16 * 16 for v in rgb)] += count

    total = sum(counter.values()) or 1
    out = []
    for rgb, count in counter.most_common(colors):
        r, g, b = rgb
        out.append({
            "hex": f"#{r:02X}{g:02X}{b:02X}",
            "share": round(count / total, 3),
            "luminance": round((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255, 3),
        })
    return out


def brightness_stats(paths: list[Path]) -> dict[str, float]:
    from PIL import Image, ImageStat

    values = []
    for path in paths:
        stat = ImageStat.Stat(Image.open(path).convert("L"))
        values.append(stat.mean[0] / 255)
    if not values:
        return {}
    return {
        "mean_brightness": round(statistics.mean(values), 3),
        "dark_frame_ratio": round(sum(1 for v in values if v < 0.35) / len(values), 2),
    }


# ----------------------------------------------------------------------
# サムネイル
# ----------------------------------------------------------------------
def analyze_thumbnail(path: Path) -> dict[str, Any]:
    """サムネの作りを数値にする.

    文字の量は、彩度が低く輝度が極端な画素（白文字・黒縁）の割合で近似する。
    OCR は環境依存が大きいので使わない。
    """
    from PIL import Image

    img = Image.open(path).convert("RGB").resize((320, 180))
    pal = palette_of([_save_tmp(img)], colors=5)

    hsv = img.convert("HSV")
    pixels = list(hsv.getdata())   # 320x180 なので一括で問題ない
    n = len(pixels) or 1
    high_contrast = sum(1 for _h, s, v in pixels if s < 60 and (v > 210 or v < 40))
    saturated = sum(1 for _h, s, _v in pixels if s > 150)

    return {
        "palette": pal,
        "text_area_ratio": round(high_contrast / n, 3),
        "vivid_ratio": round(saturated / n, 3),
        "aspect": f"{img.width}x{img.height}",
    }


def _save_tmp(img) -> Path:
    import tempfile

    fd = Path(tempfile.mkstemp(suffix=".png")[1])
    img.save(fd)
    return fd


# ----------------------------------------------------------------------
# まとめ
# ----------------------------------------------------------------------
@dataclass
class VisualProfile:
    cuts: dict[str, Any] = field(default_factory=dict)
    palette: list[dict[str, Any]] = field(default_factory=list)
    brightness: dict[str, float] = field(default_factory=dict)
    thumbnail: dict[str, Any] = field(default_factory=dict)


def analyze_video(video: Path, duration: float, workdir: Path,
                  thumbnail: Path | None = None) -> VisualProfile:
    profile = VisualProfile()
    try:
        cuts, threshold = detect_cuts(video)
        profile.cuts = cut_stats(cuts, duration)
        profile.cuts["threshold_used"] = threshold
    except Exception as exc:
        log.warning("カット検出に失敗: %s", exc)
    try:
        frames = sample_frames(video, workdir / "frames")
        profile.palette = palette_of(frames)
        profile.brightness = brightness_stats(frames)
    except Exception as exc:
        log.warning("配色の解析に失敗: %s", exc)
    if thumbnail and thumbnail.exists():
        try:
            profile.thumbnail = analyze_thumbnail(thumbnail)
        except Exception as exc:
            log.warning("サムネの解析に失敗: %s", exc)
    return profile


def aggregate_visual(profiles: list[VisualProfile]) -> dict[str, Any]:
    """複数本ぶんをまとめる（中央値）."""
    def med(getter, default=0.0):
        values = [v for v in (getter(p) for p in profiles) if v]
        return round(statistics.median(values), 2) if values else default

    palette: Counter[str] = Counter()
    for p in profiles:
        for entry in p.palette:
            palette[entry["hex"]] += entry["share"]

    return {
        "videos": len(profiles),
        "cuts_per_minute": med(lambda p: p.cuts.get("cuts_per_minute")),
        "median_shot_seconds": med(lambda p: p.cuts.get("median_shot_seconds")),
        "shots_over_6s_ratio": med(lambda p: p.cuts.get("shots_over_6s_ratio")),
        "mean_brightness": med(lambda p: p.brightness.get("mean_brightness")),
        "dominant_colors": [
            {"hex": hex_, "share": round(share / max(len(profiles), 1), 3)}
            for hex_, share in palette.most_common(6)
        ],
        "thumbnail_text_area_ratio": med(
            lambda p: p.thumbnail.get("text_area_ratio")),
        "thumbnail_vivid_ratio": med(lambda p: p.thumbnail.get("vivid_ratio")),
    }
