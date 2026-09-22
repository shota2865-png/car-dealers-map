#!/usr/bin/env python3
"""日本語フォント（Noto Sans JP Bold / Black）を assets/fonts に用意する.

システムにある IPAGothic は線が細く、動画のテロップだと読みづらい。
Noto Sans JP の Bold(700) / Black(900) を入れる。

取得経路は npm の @fontsource/noto-sans-jp。woff2 で配布されているので
fonttools で TTF に戻す（グリフはそのままなので劣化しない）。
Google Fonts へ直接繋げない環境でも、npm さえ通れば入る。

  python scripts/install_fonts.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets" / "fonts"
PACKAGE = "@fontsource/noto-sans-jp"

# (woff2 のファイル名, 出力名, family, subfamily)
WANTED = [
    ("noto-sans-jp-japanese-700-normal.woff2", "NotoSansJP-Bold.ttf",
     "Noto Sans JP", "Bold"),
    ("noto-sans-jp-japanese-900-normal.woff2", "NotoSansJP-Black.ttf",
     "Noto Sans JP Black", "Regular"),
]

SYSTEM_FALLBACKS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "C:/Windows/Fonts/YuGothB.ttc",
]


def already_installed() -> bool:
    return all((DEST / name).exists() for _src, name, _f, _s in WANTED)


def fix_names(path: Path, family: str, subfamily: str) -> None:
    """name テーブルを直す.

    fontsource の woff2 は family が 'Noto Sans JP Thin' になっている。
    そのままだと libass（字幕の焼き込み）がフォントを引けないので、
    family / subfamily / full name / PostScript name を揃え直す。
    """
    from fontTools.ttLib import TTFont

    font = TTFont(path)
    full = family if subfamily in ("Regular", "") else f"{family} {subfamily}"
    ps = full.replace(" ", "")
    records = {1: family, 2: subfamily, 3: f"{ps};ytecon", 4: full, 6: ps,
               16: family, 17: subfamily}
    for name_id, value in records.items():
        font["name"].setName(value, name_id, 3, 1, 0x409)   # Windows/Unicode/en-US
        font["name"].setName(value, name_id, 1, 0, 0)       # Mac/Roman/en
    font.save(path)


def from_npm() -> bool:
    if not shutil.which("npm"):
        print("npm が見つかりません")
        return False
    try:
        from fontTools.ttLib import TTFont  # noqa: F401
    except ImportError:
        print("fonttools が要ります: pip install fonttools brotli")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        print(f"{PACKAGE} を取得しています…")
        proc = subprocess.run(["npm", "pack", PACKAGE], cwd=work,
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            print(f"取得に失敗: {proc.stderr.strip()[-300:]}")
            return False

        tgz = next(work.glob("*.tgz"), None)
        if not tgz:
            print("パッケージが見つかりません")
            return False
        with tarfile.open(tgz) as tar:
            tar.extractall(work)

        files = work / "package" / "files"
        DEST.mkdir(parents=True, exist_ok=True)
        from fontTools.ttLib import TTFont

        ok = 0
        for src_name, out_name, family, subfamily in WANTED:
            src = files / src_name
            if not src.exists():
                matches = sorted(files.glob(src_name.replace("normal", "*")))
                if not matches:
                    print(f"  {src_name} が見つかりません")
                    continue
                src = matches[0]
            out = DEST / out_name
            font = TTFont(src)
            font.flavor = None          # woff2 -> ttf
            font.save(out)
            fix_names(out, family, subfamily)
            print(f"  {out.name}  {out.stat().st_size // 1024}KB  family={family}")
            ok += 1
        return ok == len(WANTED)


def from_system() -> bool:
    for path in SYSTEM_FALLBACKS:
        if Path(path).exists():
            print(f"システムのフォントを使います: {path}")
            return True
    return False


def main() -> int:
    if already_installed():
        print(f"導入済みです: {DEST}")
        return 0
    if from_npm():
        print(f"\n完了: {DEST}")
        return 0
    print("\nnpm 経由で取得できませんでした。システムのフォントを探します。")
    if from_system():
        return 0
    print(
        "日本語フォントが用意できませんでした。次のいずれかで入れてください。\n"
        "  Ubuntu/Debian : sudo apt install -y fonts-noto-cjk\n"
        "  macOS         : 標準のヒラギノが使われます\n"
        "  手動          : Noto Sans JP Bold を assets/fonts/NotoSansJP-Bold.ttf に置く"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
