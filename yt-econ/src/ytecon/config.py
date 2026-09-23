"""設定の読み込み。channel.yaml + .env をひとつのオブジェクトにまとめる.

チャンネルは複数持てる。2 つ目以降は config/channels/<key>/channel.yaml に置き、
先頭の `extends: ../../channel.yaml` で本体を継承して、違うところだけ書く（深いマージ）。
選び方は `ytecon --channel <key>` か環境変数 YTECON_CHANNEL。

鍵（YOUTUBE_* など）はチャンネルごとに別の値を使えるように、`channel.env_prefix`（例 PSY_）が
あれば `PSY_YOUTUBE_REFRESH_TOKEN` を先に見て、無ければ素の名前に落ちる。
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
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
CHANNELS_DIR = PROJECT_ROOT / "config" / "channels"
# 継承の深さの上限（循環の保険）
_MAX_EXTENDS = 5


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = PROJECT_ROOT
    path: Path = field(default=DEFAULT_CONFIG)

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
    def channel_key(self) -> str:
        """チャンネルの短い識別子（既定のチャンネルは 'main'）。キャッシュ名やログに使う."""
        return str(self.get("channel.key", "") or "main")

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
        """環境変数。channel.env_prefix があれば <prefix><key> を先に見る（チャンネルごとの鍵）."""
        prefix = str(self.get("channel.env_prefix", "") or "").strip()
        if prefix:
            v = os.environ.get(prefix + key, "")
            if v:
                return v
        return os.environ.get(key, default) or default

    def env_name(self, key: str) -> str:
        """このチャンネルで実際に参照される環境変数名（doctor の表示用）."""
        prefix = str(self.get("channel.env_prefix", "") or "").strip()
        return (prefix + key) if prefix and os.environ.get(prefix + key) else key


_MISSING = object()

_cache: dict[str, Config] = {}


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """辞書は再帰的に、それ以外（リストや値）は上書きで合成する。base は変えない."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_yaml(path: Path, depth: int = 0) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"設定ファイルが見つかりません: {path}\n"
            f"config/channel.yaml を用意してください。"
        )
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    parent = raw.pop("extends", None)
    if not parent:
        return raw
    if depth >= _MAX_EXTENDS:
        raise ValueError(f"extends が深すぎます（循環していませんか）: {path}")
    pp = Path(str(parent))
    pp = pp if pp.is_absolute() else (path.parent / pp).resolve()
    return deep_merge(_read_yaml(pp, depth + 1), raw)


def channel_config_path(channel: str | None) -> Path:
    """--channel の値から設定ファイルの場所を出す。'main'/空なら既定."""
    key = (channel or "").strip()
    if not key or key == "main":
        return DEFAULT_CONFIG
    p = Path(key)
    if p.suffix in (".yaml", ".yml"):
        return p if p.is_absolute() else PROJECT_ROOT / p
    cand = CHANNELS_DIR / key / "channel.yaml"
    if cand.exists():
        return cand
    raise FileNotFoundError(
        f"チャンネル {key} の設定がありません: {cand}\n"
        f"config/channels/{key}/channel.yaml を作ってください（extends: ../../channel.yaml で本体を継承できます）"
    )


def list_channels() -> list[str]:
    """設定のあるチャンネル一覧（main + config/channels/*）."""
    keys = ["main"]
    if CHANNELS_DIR.exists():
        keys += sorted(d.name for d in CHANNELS_DIR.iterdir() if (d / "channel.yaml").exists())
    return keys


def load_config(path: str | Path | None = None, channel: str | None = None) -> Config:
    """channel.yaml と .env を読み込む（プロセス内でキャッシュ）.

    優先順: path 引数 → channel 引数 → 環境変数 YTECON_CONFIG → YTECON_CHANNEL → 既定。
    """
    if path:
        cfg_path = Path(path)
    elif channel:
        cfg_path = channel_config_path(channel)
    elif os.environ.get("YTECON_CONFIG"):
        cfg_path = Path(os.environ["YTECON_CONFIG"])
    else:
        cfg_path = channel_config_path(os.environ.get("YTECON_CHANNEL", ""))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    key = str(cfg_path)
    if key in _cache:
        return _cache[key]

    load_dotenv(PROJECT_ROOT / ".env")
    raw = _read_yaml(cfg_path)
    cfg = Config(raw=raw, path=cfg_path)
    _cache[key] = cfg
    return cfg
