"""週 1 回の総集編（ytecon compile）."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess

import pytest

from ytecon import compilation
from ytecon.config import load_config


@pytest.fixture
def cfg():
    import copy
    return copy.deepcopy(load_config())


def _clip(path, seconds=2, color="blue"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d={seconds}:r=30",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


def _episode(root, slug, title, color="blue"):
    d = root / slug
    _clip(d / "video.mp4", color=color)
    (d / "metadata.json").write_text(json.dumps({"title": title}, ensure_ascii=False), encoding="utf-8")
    _clip(d / "shorts" / "short_01" / "video.mp4")                       # Shorts は入れない
    (d / "shorts" / "short_01" / "metadata.json").write_text("{}", encoding="utf-8")
    return d


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg が要る")
def test_compile_joins_week_of_long_videos_with_chapters(cfg, tmp_path):
    src = tmp_path / "dl"
    _episode(src / "run1", "20260926-060848-ai-9931", "生涯賃金とは？会社員の一生は億単位【就職して足りる？】【ずんだもん&めたん解説】")
    _episode(src / "run2", "20260927-061000-yoru", "不安が夜に大きくなる理由｜損失回避と睡眠不足【ずんだもん&めたん解説】", "red")
    _episode(src / "run3", "20260928-062000-sumaho", "スマホ代はなぜ5年でまた上がる？【値下げは政策】【ずんだもん&めたん解説】", "green")
    _episode(src / "run3b", "20260928-062000-sumaho", "重複", "green")                     # 同じ回は 1 回だけ
    _episode(src / "old", "20260910-060000-old", "古い回")                                  # 期間外
    eps = compilation.find_episodes(src, days=7, today=dt.date(2026, 9, 28), min_seconds=0)
    assert [e.day.day for e in eps] == [26, 27, 28]
    ch = compilation.chapters(eps)
    assert ch[0].startswith("0:00 生涯賃金とは？") and "【" not in "".join(ch)
    assert ch[1].startswith("0:02 ") and ch[2].startswith("0:04 ")
    meta = compilation.build_metadata(cfg, eps)
    assert meta.title.startswith("【睡眠用・作業用】おやすみ経済学 1週間まとめ") and "生涯賃金" in meta.title and len(meta.title) <= 100
    assert "■ もくじ\n0:00 生涯賃金とは？" in meta.description and "sub_confirmation=1" in meta.description
    res = compilation.make(cfg, src, tmp_path / "out", days=7, today=dt.date(2026, 9, 28), min_seconds=0)
    assert res["episodes"] == 3 and abs(compilation.probe_seconds(tmp_path / "out" / "compilation.mp4") - 6.0) < 0.6


def test_compile_skips_thin_weeks(cfg, tmp_path):
    assert compilation.make(cfg, tmp_path, tmp_path / "out")["skipped"]
