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


def build(script_json: Path, out: Path | None = None) -> Path:
    out = out or (ROOT / "colab" / "ytecon_colab.zip")
    out.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for py in (ROOT / "src" / "ytecon").glob("*.py"):
            z.write(py, f"src/ytecon/{py.name}")
        for cfg in (ROOT / "config").iterdir():
            if cfg.suffix in (".yaml", ".json"):
                z.write(cfg, f"config/{cfg.name}")
        for font in (ROOT / "assets" / "fonts").glob("*.ttf"):
            z.write(font, f"assets/fonts/{font.name}")
        lic = ROOT / "assets" / "fonts" / "LICENSE.txt"
        if lic.exists():
            z.write(lic, "assets/fonts/LICENSE.txt")
        # 右下のキャラクター（置いてあれば本物、無ければ仮キャラ）
        for png in (ROOT / "assets" / "character").glob("*.png"):
            z.write(png, f"assets/character/{png.name}")
        # BGM（置いてあれば。無ければ実行時に合成音を作る）
        for ext in ("*.mp3", "*.wav", "*.m4a", "*.ogg"):
            for m in (ROOT / "assets" / "bgm").glob(ext):
                z.write(m, f"assets/bgm/{m.name}")
        z.write(script_json, "script.json")

    return out


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output" / "demo" / "script.json"
    if not src.exists():
        raise SystemExit(f"台本が見つかりません: {src}")
    path = build(src)
    size = path.stat().st_size / 1024 / 1024
    print(f"{path}  {size:.1f}MB")
    with zipfile.ZipFile(path) as z:
        print(f"  {len(z.namelist())} ファイル")
        for n in sorted(z.namelist())[:6]:
            print("   ", n)
