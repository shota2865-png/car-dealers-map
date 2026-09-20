#!/usr/bin/env python3
"""Colab に持っていく zip を作る.

yt-econ の本体・設定・フォント・台本を1つにまとめる。
Colab 側はこれを展開するだけで、本番と同じコードで動画を作れる。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(script_json: Path, out: Path | None = None, mac: bool = False) -> Path:
    """Colab 用 zip を作る。mac=True なら Mac でダブルクリックして動かす一式にする."""
    out = out or (ROOT / ("mac" if mac else "colab") / ("ytecon_mac.zip" if mac else "ytecon_colab.zip"))
    out.parent.mkdir(parents=True, exist_ok=True)

    prefix = "ytecon_mac/" if mac else ""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if mac:
            for name in ("make_video.py", "動画をつくる.command", "import_assets.py",
                         "artlist_fetch.py", "素材をそろえる.command"):
                z.write(ROOT / "mac" / name, f"ytecon_mac/{name}")
        for py in (ROOT / "src" / "ytecon").glob("*.py"):
            z.write(py, f"{prefix}src/ytecon/{py.name}")
        for cfg in (ROOT / "config").iterdir():
            if cfg.suffix in (".yaml", ".json"):
                z.write(cfg, f"{prefix}config/{cfg.name}")
        for font in (ROOT / "assets" / "fonts").glob("*.ttf"):
            z.write(font, f"{prefix}assets/fonts/{font.name}")
        lic = ROOT / "assets" / "fonts" / "LICENSE.txt"
        if lic.exists():
            z.write(lic, f"{prefix}assets/fonts/LICENSE.txt")
        # 右下のキャラクター（置いてあれば本物、無ければ仮キャラ）
        for png in (ROOT / "assets" / "character").glob("*.png"):
            z.write(png, f"{prefix}assets/character/{png.name}")
        # BGM（置いてあれば。無ければ実行時に合成音を作る）
        for ext in ("*.mp3", "*.wav", "*.m4a", "*.ogg"):
            for m in (ROOT / "assets" / "bgm").glob(ext):
                z.write(m, f"{prefix}assets/bgm/{m.name}")
        z.write(script_json, f"{prefix}script.json")

    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--mac"]
    src = Path(args[0]) if args else ROOT / "output" / "demo" / "script.json"
    if not src.exists():
        raise SystemExit(f"台本が見つかりません: {src}")
    path = build(src, mac="--mac" in sys.argv)
    size = path.stat().st_size / 1024 / 1024
    print(f"{path}  {size:.1f}MB")
    with zipfile.ZipFile(path) as z:
        print(f"  {len(z.namelist())} ファイル")
        for n in sorted(z.namelist())[:6]:
            print("   ", n)
