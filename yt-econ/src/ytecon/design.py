"""デザイントークン（config/design_tokens.yaml）の読み込み.

デジタル庁デザインシステム / Apple HIG / Material 3 を下敷きにした
「寸法・色・角丸・余白・動き」の決まりを、カード・図表・字幕・レンダリングが
ここを通して参照する。数値をコードに直接書かないための層。
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

import yaml

from .config import Config

log = logging.getLogger(__name__)

DEFAULT_PRESET = "hybrid"


@functools.lru_cache(maxsize=4)
def _load_file(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        log.warning("design_tokens.yaml が見つかりません: %s", p)
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def preset_name(cfg: Config) -> str:
    return str(cfg.get("video.design", DEFAULT_PRESET) or DEFAULT_PRESET)


def tokens(cfg: Config) -> dict[str, Any]:
    """選ばれたプリセットのトークン（common を含む）を返す."""
    data = _load_file(str(cfg.root / "config" / "design_tokens.yaml"))
    presets = data.get("presets") or {}
    name = preset_name(cfg)
    if name not in presets:
        if presets:
            log.warning("デザインプリセット %s は無いので %s を使います", name, DEFAULT_PRESET)
        name = DEFAULT_PRESET
    t = dict(presets.get(name) or {})
    t["common"] = data.get("common") or {}
    t["name"] = name
    return t


def colors(cfg: Config) -> dict[str, str]:
    return dict(tokens(cfg).get("colors") or {})


def type_size(cfg: Config, role: str, fallback: int = 48) -> int:
    """文字の役割（display_l / headline_m / body_l / label …）→ px.

    layout.type_scale（縦画面の Shorts は 1.2）で全体を拡大する。字幕（subtitle）は
    config の visuals.subtitle.font_size で別に決めるので掛けない。
    """
    size = int((tokens(cfg).get("type") or {}).get(role, fallback))
    scale = float(cfg.get("layout.type_scale", 1.0) or 1.0)
    if role != "subtitle" and abs(scale - 1.0) > 1e-6:
        size = int(round(size * scale))
    return size


def radius(cfg: Config, size: str = "m") -> int:
    return int((tokens(cfg).get("radius") or {}).get(size, {"s": 12, "m": 16, "l": 28}[size]))


def stroke(cfg: Config, kind: str) -> int:
    return int((tokens(cfg).get("stroke") or {}).get(kind, {"card": 3, "text_outline": 5}.get(kind, 3)))


def grid(cfg: Config) -> int:
    return int((tokens(cfg).get("common") or {}).get("grid", 8))


def space(cfg: Config, n: float) -> int:
    """8px グリッドの n 倍."""
    return int(round(grid(cfg) * n))


def safe_margin(cfg: Config) -> int:
    return int((tokens(cfg).get("common") or {}).get("safe_margin", 96))


def line_height(cfg: Config) -> float:
    return float((tokens(cfg).get("common") or {}).get("line_height", 1.5))


def fade_frames(cfg: Config, fps: int) -> int:
    """シーン頭のフェイン（motion.fade_ms）をフレーム数に."""
    ms = float((tokens(cfg).get("motion") or {}).get("fade_ms", 300))
    return max(1, int(round(ms / 1000 * fps)))


def summary(cfg: Config) -> str:
    t = tokens(cfg)
    return (f"design={t.get('name')} ({t.get('label', '')}) "
            f"radius={t.get('radius')} fade={((t.get('motion') or {}).get('fade_ms'))}ms")
