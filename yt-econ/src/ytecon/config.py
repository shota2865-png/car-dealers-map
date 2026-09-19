"""設定の読み込み。channel.yaml + .env をひとつのオブジェクトにまとめる."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:  # .env は無くても動く
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a, **_kw):  # type: ignore
        return False


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "channel.yaml"


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = PROJECT_ROOT

    # --- 辞書アクセスの糖衣 ---
    def get(self, path: str, default: Any = None) -> Any:
        """'tts.voicevox.speaker' のようなドット区切りで引く."""
        node: Any = self.raw
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def __getitem__(self, path: str) -> Any:
        value = self.get(path, _MISSING)
        if value is _MISSING:
            raise KeyError(f"config に {path} がありません")
        return value

    # --- よく使う派生値 ---
    @property
    def workdir(self) -> Path:
        d = self.root / self.get("pipeline.workdir", "output")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def target_chars(self) -> tuple[int, int]:
        """目標尺(分)と話速から、台本の目標文字数レンジを出す."""
        cpm = float(self.get("video.chars_per_minute", 340))
        lo = float(self.get("video.target_minutes_min", 8.0))
        hi = float(self.get("video.target_minutes_max", 10.0))
        return int(cpm * lo), int(cpm * hi)

    @property
    def target_seconds(self) -> tuple[float, float]:
        return (
            float(self.get("video.target_minutes_min", 8.0)) * 60,
            float(self.get("video.target_minutes_max", 10.0)) * 60,
        )

    def env(self, key: str, default: str = "") -> str:
        return os.environ.get(key, default) or default


_MISSING = object()

_cache: dict[str, Config] = {}


def load_config(path: str | Path | None = None) -> Config:
    """channel.yaml と .env を読み込む（プロセス内でキャッシュ）."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    key = str(cfg_path)
    if key in _cache:
        return _cache[key]

    load_dotenv(PROJECT_ROOT / ".env")
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"設定ファイルが見つかりません: {cfg_path}\n"
            f"config/channel.yaml を用意してください。"
        )
    with cfg_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = Config(raw=raw)
    _cache[key] = cfg
    return cfg
