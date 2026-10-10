"""縦型 Shorts を作る.

構図は「株ライブ＠田端大学」の Shorts を手本にしている:
  黒帯（番組名）→ 水色の帯（白い太字のタイトル）→ 映像 → 濃紺の背景（切り抜き元）
  - 生配信（宇宙株LIVE）: 顔カメラのアップ＋画面全体の 2 段。字幕あり（色つきの箱・1 行 12 字まで・文節で区切る）
  - 生配信でない動画    : 中央を 4:3 に切り出した 1 段。字幕は付けず、タイトルと小見出しだけ

字は角ゴシックの極太（Noto Sans JP Black）。字幕は自動字幕の単語の時刻から作り、Pillow で PNG に描いて重ねる
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
BAR_H = 100            # いちばん上の黒帯（番組名）
BAND_H = 236           # 水色の帯（タイトル 2 行）
TOP = BAR_H + BAND_H   # 映像の上端
BAND = "#2FB4F2"
BOX = {"normal": "#1F3BFF", "strong": "#E3122D", "ask": "#8A1FB8"}
SUB_MAX = 12           # 字幕 1 行の最大文字数


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


def _fit(text: str, path: Path, size: int, max_w: int, min_size: int = 40) -> ImageFont.FreeTypeFont:
    """1 行に収まるまで字を小さくする."""
    f = _font(path, size)
    while f.getlength(text) > max_w and size > min_size:
        size -= 4
        f = _font(path, size)
    return f


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


def _center(d: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.FreeTypeFont, fill: str,
            stroke: str | None = None, sw: int = 0) -> None:
    x = (W - font.getlength(text)) / 2
    d.text((x, y), text, font=font, fill=fill, stroke_width=sw, stroke_fill=stroke)


def background(out: Path) -> Path:
    """濃紺のグラデーション＋上下の赤い細線（映像の無いところに見える）."""
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    top, bot = (16, 24, 58), (4, 6, 16)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    for y in (H - 150, H - 142):
        d.line([(60, y), (W - 60, y)], fill=(200, 24, 48), width=3)
    im.save(out)
    return out


def frame_layer(clip: dict[str, Any], layout: dict[str, Any], fonts: dict[str, Path], out: Path) -> Path:
    """動かない部分（黒帯・水色の帯・タイトル・小見出し・配信日・切り抜き元）を 1 枚の透明 PNG に描く."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    font = fonts["gothic"]
    # 黒帯（番組名）
    d.rectangle([0, 0, W, BAR_H], fill="#000000")
    show = clip.get("show") or "マックスむらい【切り抜き】"
    f = _fit(show, font, 48, W - 60)
    _center(d, (BAR_H - sum(f.getmetrics())) // 2, show, f, "#FFFFFF")
    # 水色の帯（タイトル）。生配信は 2 行（話題＋一言）、それ以外は話題だけ（一言は映像の下に小見出しで出す）
    d.rectangle([0, BAR_H, W, TOP], fill=BAND)
    l1, l2 = clip.get("band1") or "", clip.get("band2") or ""
    rows = [t for t in ((l1, l2) if layout["subs"] else (l1,)) if t]
    if len(rows) == 1:
        rows = _wrap(rows[0], _font(font, 80), W - 80, 2)
    rh = BAND_H // max(1, len(rows))
    for k, t in enumerate(rows[:2]):
        f = _fit(t, font, 88 if len(rows) == 1 else 78, W - 70)
        asc, desc = f.getmetrics()
        _center(d, BAR_H + k * rh + (rh - asc - desc) // 2, t, f, "#FFFFFF", "#0B4E86", 5)
    # 配信日（映像の左上）
    if clip.get("date_label"):
        f = _font(font, 38)
        tw = f.getlength(clip["date_label"])
        d.rectangle([0, TOP, tw + 36, TOP + 62], fill=(0, 0, 0, 200))
        d.text((18, TOP + 4), clip["date_label"], font=f, fill="#FFFFFF")
    # 小見出し（生配信でない動画だけ。映像のすぐ下に黄色の大きな字）
    y_end = layout["end_y"]
    if not layout["subs"] and l2:
        for k, t in enumerate(_wrap(l2, _font(font, 96), W - 100, 2)):
            f = _fit(t, font, 96, W - 100)
            _center(d, y_end + 70 + k * 130, t, f, "#FFE600", "#000000", 10)
    # 切り抜き元（いちばん下）
    credit = f"切り抜き元：{clip.get('source_name') or 'マックスむらい'}"
    f = _fit(credit, font, 38, W - 80, 28)
    _center(d, H - 118, credit, f, "#FFFFFF", "#000000", 4)
    im.save(out)
    return out


def _box_kind(text: str) -> str:
    if re.search(r"[?？]", text):
        return "ask"
    if re.search(r"[!！]|やば|マジ|嘘|うそ|すご|えぐ|最悪|最高|爆|暴落|急騰", text):
        return "strong"
    return "normal"


def subtitle_png(text: str, font_path: Path, out: Path) -> Path:
    """色つきの箱に白い太字 1 行."""
    font = _fit(text, font_path, 70, W - 150, 48)
    asc, desc = font.getmetrics()
    pad_x, pad_y = 26, 8
    w = int(font.getlength(text)) + pad_x * 2
    h = asc + desc + pad_y * 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, h], fill=BOX[_box_kind(text)])
    d.text((pad_x, pad_y), text, font=font, fill="#FFFFFF")
    im.save(out)
    return out


_parser = None


def phrases(text: str) -> list[str]:
    """文節に分ける（BudouX。入っていなければ句読点だけで分ける）."""
    global _parser
    if _parser is None:
        try:
            import budoux
            _parser = budoux.load_default_japanese_parser()
        except Exception:  # noqa: BLE001
            _parser = False
    if _parser:
        return _parser.parse(text)
    return [p for p in re.split(r"(?<=[。、！？!?])", text) if p]


def subtitle_chunks(lines: list[dict[str, Any]], start: float, end: float, max_chars: int = SUB_MAX) -> list[dict[str, Any]]:
    """字幕を「1 行・最大 max_chars 字・文節の切れ目」で区切る.

    自動字幕の単語の時刻を 1 文字ずつに割り当て、文節（BudouX）をつないで行にする。
    句読点・間（0.7 秒以上）・[笑い] などの印のところでも行を改める。
    """
    chars: list[tuple[str, float]] = []          # (文字, その単語の時刻)
    breaks: set[int] = set()                     # この位置（文字数）の前で必ず区切る
    last = None
    for ln in lines:
        for w in ln["words"]:
            if not start <= w["t"] < end:
                continue
            txt = re.sub(r"\[[^\]]*\]", "", w["w"])
            txt = re.sub(r"\s+", "", txt)
            if last is not None and w["t"] - last > 0.7:
                breaks.add(len(chars))
            for ch in txt:
                chars.append((ch, w["t"]))
            if txt:
                last = w["t"]
    if not chars:
        return []
    text = "".join(c for c, _ in chars)
    out: list[dict[str, Any]] = []
    cur, cur_i, pos = "", 0, 0

    def flush(next_pos: int) -> None:
        nonlocal cur, cur_i
        # 行の頭・終わりに残った「え、」「、え」などの言いよどみは出さない
        shown = re.sub(r"^(え|えー|あの|まあ?)、", "", cur.strip("、。 "))
        shown = re.sub(r"、(え|えー|あの)$", "", shown).strip("、。 ")
        if shown:
            out.append({"text": shown, "t0": chars[cur_i][1], "i": cur_i})
        cur, cur_i = "", next_pos

    for ph in phrases(text):
        # 文節が長すぎるときは max_chars ごとに割る
        parts = [ph[k:k + max_chars] for k in range(0, len(ph), max_chars)]
        for part in parts:
            forced = any(pos < b <= pos + len(part) for b in breaks) or pos in breaks
            if cur and (len(cur) + len(part) > max_chars or forced):
                flush(pos)
            if not cur:
                cur_i = pos
            cur += part
            pos += len(part)
            if re.search(r"[。！？!?]$", part):
                flush(pos)
    flush(pos)
    # 1 字だけの行は前の行に足す（12 字を 1 字はみ出すより、1 字だけ出るほうが読みにくい）
    merged: list[dict[str, Any]] = []
    for c in out:
        if merged and len(c["text"]) <= 1:
            merged[-1]["text"] += c["text"]
        else:
            merged.append(c)
    # 0.5 秒未満しか出ない行は、12 字に収まるなら次の行の頭に付ける（一瞬だけ光るのを防ぐ）
    calm: list[dict[str, Any]] = []
    k = 0
    while k < len(merged):
        c = merged[k]
        if k + 1 < len(merged) and merged[k + 1]["t0"] - c["t0"] < 0.5 \
                and len(c["text"]) + len(merged[k + 1]["text"]) <= max_chars:
            merged[k + 1] = {**merged[k + 1], "text": c["text"] + merged[k + 1]["text"], "t0": c["t0"]}
        else:
            calm.append(c)
        k += 1
    merged = calm
    for a, b in zip(merged, merged[1:]):
        a["t1"] = b["t0"]
    if merged:
        merged[-1]["t1"] = min(end, merged[-1]["t0"] + 2.5)
    for c in merged:
        c["t0"] -= start
        c["t1"] = min(c["t1"], c["t0"] + start + 4.0) - start      # 黙っている間は 4 秒で消す
    return [c for c in merged if c["t1"] - c["t0"] > 0.15 and len(c["text"]) >= 2]


def plan_layout(face: list[float] | None, is_live: bool, src_w: int, src_h: int) -> dict[str, Any]:
    """映像の置き方を決める（ffmpeg のフィルタと、字幕を出す高さ・映像の下端）."""
    if face:
        # 2 段: 顔カメラのアップ ＋ 画面全体
        x, y, w, h = face
        cw, ch = int(src_w * w) // 2 * 2, int(src_h * h) // 2 * 2
        cx, cy = int(src_w * x), int(src_h * y)
        face_h = int(W * ch / cw) // 2 * 2
        full_h = int(W * src_h / src_w) // 2 * 2
        return {"filters": [f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={W}:{face_h}:flags=lanczos[a]",
                            f"[0:v]scale={W}:{full_h}:flags=lanczos[b]",
                            f"[2:v][a]overlay=0:{TOP}:shortest=1[t1]",
                            f"[t1][b]overlay=0:{TOP + face_h}[base]"],
                "sub_bottom": TOP + face_h - 24, "end_y": TOP + face_h + full_h, "subs": is_live}
    # 1 段: 中央を 4:3 に切り出して大きく見せる
    cw = min(src_w, int(src_h * 4 / 3) // 2 * 2)
    vid_h = int(W * src_h / cw) // 2 * 2
    return {"filters": [f"[0:v]crop={cw}:{src_h}:(iw-{cw})/2:0,scale={W}:{vid_h}:flags=lanczos[a]",
                        f"[2:v][a]overlay=0:{TOP}:shortest=1[base]"],
            "sub_bottom": TOP + vid_h - 24, "end_y": TOP + vid_h, "subs": is_live}


def probe_size(src: Path) -> tuple[int, int]:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    w, h = p.stdout.strip().split(",")[:2]
    return int(w), int(h)


def render_short(clip: dict[str, Any], src: Path, out: Path, fonts: dict[str, Path], fps: int = 30) -> Path:
    work = out.parent / (out.stem + "_parts")
    work.mkdir(parents=True, exist_ok=True)
    sw_, sh_ = probe_size(src)
    lay = plan_layout(clip["video"].get("face"), bool(clip.get("is_live")), sw_, sh_)
    frame = frame_layer(clip, lay, fonts, work / "frame.png")
    bg = background(work / "bg.png")
    subs = subtitle_chunks(clip["lines"], clip["start"], clip["end"]) if lay["subs"] else []
    pngs = [subtitle_png(s["text"], fonts["gothic"], work / f"s{i:03d}.png") for i, s in enumerate(subs)]
    dur = clip["end"] - clip["start"]

    inputs = ["-i", str(src), "-loop", "1", "-t", f"{dur:.2f}", "-i", str(frame),
              "-loop", "1", "-t", f"{dur:.2f}", "-i", str(bg)]
    for p in pngs:
        inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(p)]
    f = [*lay["filters"], f"[base][1:v]overlay=0:0,fps={fps}[v0]"]
    last = "v0"
    for i, s in enumerate(subs):
        nxt = f"v{i + 1}"
        f.append(f"[{last}][{i + 3}:v]overlay=(W-w)/2:{lay['sub_bottom']}-h:"
                 f"enable='between(t,{s['t0']:.2f},{s['t1']:.2f})'[{nxt}]")
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
