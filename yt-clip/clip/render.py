"""縦型 Shorts を作る（ぼかした背景＋中央に元動画＋上に一言＋下に字幕）.

字幕は自動字幕の単語の時刻から作り、Pillow で PNG に描いて重ねる
（ffmpeg に libass / drawtext が無い環境でも同じ見た目になる）。
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import ytdlp

log = logging.getLogger(__name__)

W, H = 1080, 1920
VIDEO_Y = 610          # 元動画（1080x608）を置く高さ
SUB_Y = 1290           # 字幕の上端


def download_section(url: str, start: float, end: float, out: Path, max_height: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    err = ""
    # 区間だけ取るとき、YouTube がたまに 403 を返す。取り方（クライアント）を変えて取り直す
    for client in (None, "tv", "web_safari", "mweb"):
        out.unlink(missing_ok=True)
        cmd = [*ytdlp.base(), "-f", fmt, "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
               "--merge-output-format", "mp4", "-o", str(out)]
        if client:
            cmd += ["--extractor-args", f"youtube:player_client={client}"]
        p = subprocess.run([*cmd, url], capture_output=True, text=True)
        if p.returncode == 0 and out.exists() and out.stat().st_size > 100_000:
            return
        err = p.stderr[-600:]
        log.info("取り直します（%s）: %s", client or "既定", err.strip().splitlines()[-1:] or "")
    raise RuntimeError(f"切り抜き元を取れませんでした: {err}")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _text_png(text: str, font: ImageFont.FreeTypeFont, fill: str, stroke: str, sw: int,
              max_w: int, max_lines: int, out: Path, line_gap: int = 10) -> Path:
    lines = _wrap(text, font, max_w, max_lines)
    asc, desc = font.getmetrics()
    lh = asc + desc + line_gap
    w = int(max(font.getlength(ln) for ln in lines)) + sw * 2 + 8
    h = lh * len(lines) + sw * 2
    im = Image.new("RGBA", (max(w, 2), max(h, 2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for k, ln in enumerate(lines):
        x = (w - font.getlength(ln)) / 2
        d.text((x, sw + k * lh), ln, font=font, fill=fill, stroke_width=sw, stroke_fill=stroke)
    im.save(out)
    return out


def top_layer(hook: str, credit: str, fonts: dict[str, Path], out: Path) -> Path:
    """上の一言と、下の「切り抜き元」表示を 1 枚の透明 PNG に描く."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    big = _font(fonts["black"], 92)
    lines = _wrap(hook, big, W - 120, 2)
    asc, desc = big.getmetrics()
    lh = asc + desc + 12
    y0 = VIDEO_Y - 60 - lh * len(lines)
    for k, ln in enumerate(lines):
        x = (W - big.getlength(ln)) / 2
        d.text((x, y0 + k * lh), ln, font=big, fill="#FFE600", stroke_width=10, stroke_fill="#000000")
    small = _font(fonts["bold"], 38)
    x = (W - small.getlength(credit)) / 2
    d.text((x, 1700), credit, font=small, fill="#FFFFFF", stroke_width=5, stroke_fill="#000000")
    im.save(out)
    return out


def subtitle_chunks(lines: list[dict[str, Any]], start: float, end: float, max_chars: int = 15) -> list[dict[str, Any]]:
    """単語の時刻をまとめて、画面に出す字幕のかたまり（最大 max_chars 字）を作る."""
    words = [w for ln in lines for w in ln["words"] if start <= w["t"] < end]
    chunks: list[dict[str, Any]] = []
    cur, t0, last = "", None, None
    for w in words:
        txt = re.sub(r"\[[^\]]*\]", "", w["w"]).strip()
        if not txt:
            continue
        brk = cur and (len(cur) + len(txt) > max_chars or (last is not None and w["t"] - last > 0.7))
        if brk:
            chunks.append({"text": cur, "t0": t0, "t1": w["t"]})
            cur, t0 = "", None
        if t0 is None:
            t0 = w["t"]
        cur += txt
        last = w["t"]
    if cur:
        chunks.append({"text": cur, "t0": t0, "t1": min(end, (last or t0) + 1.5)})
    # 「N」「ほら」のような 1〜2 字だけの字幕は前のかたまりにくっつける
    merged: list[dict[str, Any]] = []
    for ch in chunks:
        if merged and len(ch["text"]) <= 2 and len(merged[-1]["text"]) + len(ch["text"]) <= max_chars + 4:
            merged[-1]["text"] += ch["text"]
            merged[-1]["t1"] = ch["t1"]
        else:
            merged.append(ch)
    chunks = merged
    for a, b in zip(chunks, chunks[1:]):
        a["t1"] = min(a["t1"], b["t0"])
    for ch in chunks:
        ch["t0"] -= start
        ch["t1"] -= start
    return [c for c in chunks if c["t1"] - c["t0"] > 0.15]


def render_short(clip: dict[str, Any], src: Path, out: Path, fonts: dict[str, Path], fps: int = 30) -> Path:
    work = out.parent / (out.stem + "_parts")
    work.mkdir(parents=True, exist_ok=True)
    top = top_layer(clip["hook"] or clip["title"], "切り抜き元：マックスむらい", fonts, work / "top.png")
    subfont = _font(fonts["black"], 70)
    subs = subtitle_chunks(clip["lines"], clip["start"], clip["end"])
    pngs = [_text_png(s["text"], subfont, "#FFFFFF", "#000000", 9, W - 100, 2, work / f"s{i:03d}.png")
            for i, s in enumerate(subs)]
    dur = clip["end"] - clip["start"]

    inputs = ["-i", str(src), "-loop", "1", "-t", f"{dur:.2f}", "-i", str(top)]
    for p in pngs:
        inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(p)]
    f = [f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=38,eq=brightness=-0.18,fps={fps}[bg]",
         f"[0:v]scale={W}:-2,fps={fps}[fg]",
         f"[bg][fg]overlay=0:{VIDEO_Y}[b0]",
         "[b0][1:v]overlay=0:0[v0]"]
    last = "v0"
    for i, s in enumerate(subs):
        nxt = f"v{i + 1}"
        f.append(f"[{last}][{i + 2}:v]overlay=(W-w)/2:{SUB_Y}:enable='between(t,{s['t0']:.2f},{s['t1']:.2f})'[{nxt}]")
        last = nxt
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(f), "-map", f"[{last}]", "-map", "0:a?",
           "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
           "-movflags", "+faststart", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"動画を作れませんでした: {p.stderr[-1200:]}")
    return out
