"""効果音（SE）の配置.

台本の sounds（POP / CLICK / WHOOSH / IMPACT / COMEDY / ERROR / RISER / TRANSITION）を、
音声の実測時刻に貼り付けて、render の音声チェーンに渡す。
音源は assets/sfx/<TYPE>.mp3|wav（大文字小文字は問わない。Artlist の SFX などを置く）。
無い種類は鳴らさない。合成音は使わない。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Config
from .script import VideoScript
from .tts import VoiceTrack

log = logging.getLogger(__name__)

TYPES = ("POP", "CLICK", "WHOOSH", "IMPACT", "COMEDY", "ERROR", "RISER", "TRANSITION")
EXTS = (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac")


def available(cfg: Config) -> dict[str, Path]:
    """種類 → ファイル。名前に種類名を含むファイルを拾う（pop_soft.mp3 → POP）."""
    d = cfg.root / "assets" / "sfx"
    out: dict[str, Path] = {}
    if not d.exists():
        return out
    for p in sorted(d.iterdir()):
        if p.suffix.lower() not in EXTS or p.name.startswith("."):
            continue
        stem = p.stem.upper()
        for t in TYPES:
            if stem == t or stem.startswith(t + "_") or stem.startswith(t + "-") or t in stem.split("_"):
                out.setdefault(t, p)
    return out


def _limits(cfg: Config) -> tuple[float, int]:
    path = cfg.root / "config" / "style_semantics.json"
    try:
        sem = json.loads(path.read_text(encoding="utf-8")).get("se_density", {})
    except Exception:
        sem = {}
    return float(sem.get("min_interval_seconds", 5.0)), int(sem.get("max_per_minute", 8))


def plan(cfg: Config, script: VideoScript, track: VoiceTrack) -> list[tuple[str, float, Path]]:
    """(種類, 秒, ファイル) の列。間隔と密度の上限を守る."""
    files = available(cfg)
    if not files or not cfg.get("render.sfx.enabled", True):
        return []
    min_gap, per_min = _limits(cfg)
    cues: list[tuple[str, float]] = []
    for i, sec in enumerate(script.sections):
        lines = [ln for ln in track.lines if ln.block_id == f"s{i}"]
        if not lines:
            continue
        # 章の頭に TRANSITION（あれば）
        if "TRANSITION" in files and i > 0:
            cues.append(("TRANSITION", max(lines[0].start - 0.15, 0.0)))
        for snd in sec.sounds:
            idx = max(0, min(int(snd.after_sentence), len(lines) - 1))
            cues.append((snd.type, lines[idx].end))
    cues = [(t, at) for t, at in cues if t in files]
    cues.sort(key=lambda x: x[1])
    out: list[tuple[str, float, Path]] = []
    last = -1e9
    window: list[float] = []
    for t, at in cues:
        if at - last < min_gap:
            continue
        window = [w for w in window if at - w < 60.0]
        if len(window) >= per_min:
            continue
        out.append((t, at, files[t]))
        window.append(at)
        last = at
    if out:
        log.info("効果音 %d 個（種類: %s）", len(out), ", ".join(sorted({t for t, _, _ in out})))
    return out[: int(cfg.get("render.sfx.max_total", 80))]
