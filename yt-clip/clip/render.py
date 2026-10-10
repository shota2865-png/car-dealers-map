"""縦型 Shorts を作る.

構図は「株ライブ＠田端大学」の Shorts を手本にしている:
  黒帯（番組名）→ 水色の帯（白い太字のタイトル）→ 映像 → 濃紺の背景（切り抜き元）
  - 生配信（宇宙株LIVE）: 基本は顔カメラ（横長のまま）。話している人のほうへ画角を寄せる。
                          チャートや画面の話をしている区間だけ画面全体に切り替える。
                          字幕あり（色つきの箱・1 行 12 字まで・文節で区切る）。区間はジャンプカットでつなぐ
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

from . import listen, speaker, ytdlp

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
    for y in (96, 104, H - 104, H - 96):
        d.line([(60, y), (W - 60, y)], fill=(200, 24, 48), width=3)
    im.save(out)
    return out


def frame_layer(clip: dict[str, Any], layout: dict[str, Any], fonts: dict[str, Path], out: Path) -> Path:
    """動かない部分（黒帯・水色の帯・タイトル・小見出し・配信日・切り抜き元）を 1 枚の透明 PNG に描く."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    font = fonts["gothic"]
    # 黒帯（番組名）
    y0 = layout["y0"]                 # かたまり全体（黒帯〜切り抜き元）の上端。上下の余白が同じになる位置
    top = y0 + TOP
    d.rectangle([0, y0, W, y0 + BAR_H], fill="#000000")
    show = clip.get("show") or "マックスむらい【切り抜き】"
    f = _fit(show, font, 48, W - 60)
    _center(d, y0 + (BAR_H - sum(f.getmetrics())) // 2, show, f, "#FFFFFF")
    # 水色の帯（タイトル）。生配信は 2 行（話題＋一言）、それ以外は話題だけ（一言は映像の下に小見出しで出す）
    d.rectangle([0, y0 + BAR_H, W, top], fill=BAND)
    l1, l2 = clip.get("band1") or "", clip.get("band2") or ""
    rows = [t for t in ((l1, l2) if layout["subs"] else (l1,)) if t]
    if len(rows) == 1:
        rows = _wrap(rows[0], _font(font, 80), W - 80, 2)
    rh = BAND_H // max(1, len(rows))
    for k, t in enumerate(rows[:2]):
        f = _fit(t, font, 88 if len(rows) == 1 else 78, W - 70)
        asc, desc = f.getmetrics()
        _center(d, y0 + BAR_H + k * rh + (rh - asc - desc) // 2, t, f, "#FFFFFF", "#0B4E86", 5)
    # 配信日（映像の左上）
    if clip.get("date_label"):
        f = _font(font, 38)
        tw = f.getlength(clip["date_label"])
        d.rectangle([0, top, tw + 36, top + 62], fill=(0, 0, 0, 200))
        d.text((18, top + 4), clip["date_label"], font=f, fill="#FFFFFF")
    # 小見出し（生配信でない動画だけ。映像のすぐ下に黄色の大きな字）
    y_end = layout["end_y"]
    if not layout["subs"] and l2:
        # 1 行に収まるなら 1 行（字を少し小さくしてでも）。長いときだけ文節の切れ目で 2 行にする
        rows2 = [l2]
        if _font(font, 68).getlength(l2) > W - 100:
            ph, half = phrases(l2), len(l2) / 2
            cut = min(range(1, len(ph)), key=lambda i: abs(len("".join(ph[:i])) - half)) if len(ph) > 1 else 0
            rows2 = ["".join(ph[:cut]), "".join(ph[cut:])] if cut else _wrap(l2, _font(font, 90), W - 100, 2)
        for k, t in enumerate(rows2):
            f = _fit(t, font, 96, W - 100, 60)
            _center(d, y_end + 40 + k * 130, t, f, "#FFE600", "#000000", 10)
    # 切り抜き元（いちばん下）
    credit = f"切り抜き元：{clip.get('source_name') or 'マックスむらい'}"
    f = _fit(credit, font, 38, W - 80, 28)
    _center(d, layout["credit_y"], credit, f, "#FFFFFF", "#000000", 4)
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
    chars: list[tuple[str, float, float | None]] = []     # (文字, その単語の始まり, 終わり（分かるときだけ）)
    breaks: set[int] = set()                     # この位置（文字数）の前で必ず区切る
    last = None
    for ln in lines:
        for w in ln["words"]:
            if not start <= w["t"] < end:
                continue
            txt = re.sub(r"\[[^\]]*\]", "", w["w"])
            txt = re.sub(r"\s+", "", txt)
            if w.get("brk"):
                breaks.add(len(chars))
            for ch in txt:
                chars.append((ch, w["t"], w.get("e")))
            if txt:
                last = w["t"]
    if not chars:
        return []
    text = "".join(c[0] for c in chars)
    out: list[dict[str, Any]] = []
    cur, cur_i, pos = "", 0, 0

    def flush(next_pos: int) -> None:
        nonlocal cur, cur_i
        # 行の頭・終わりに残った「え、」「、え」などの言いよどみは出さない
        shown = re.sub(r"^(え|えー|あの|まあ?)、", "", cur.strip("、。 "))
        shown = re.sub(r"、(え|えー|あの)$", "", shown).strip("、。 ")
        if shown:
            out.append({"text": shown, "t0": chars[cur_i][1], "e": chars[max(cur_i, next_pos - 1)][2]})
        cur, cur_i = "", next_pos

    # 文の切れ目（聞き取りの 1 文・0.7 秒以上の間）で先に分け、その中を文節でつないでいく。
    # 切れ目をまたいで 1 行にしない（「世界本当かよ」のように別の文がくっつくのを防ぐ）
    cuts = sorted(b for b in breaks if 0 < b < len(text)) + [len(text)]
    a = 0
    for b in cuts:
        for ph in phrases(text[a:b]):
            # 文節が長すぎるときは max_chars ごとに割る
            for part in [ph[k:k + max_chars] for k in range(0, len(ph), max_chars)]:
                # 行を改めるのは文節の頭だけ: 12 字を超えるとき、または 0.7 秒以上の間があいたとき
                paused = pos > 0 and chars[pos][1] - chars[pos - 1][1] > 0.7
                if cur and (len(cur) + len(part) > max_chars or paused):
                    flush(pos)
                if not cur:
                    cur_i = pos
                cur += part
                pos += len(part)
                if re.search(r"[。！？!?]$", part):
                    flush(pos)
                    if out:
                        out[-1]["eos"] = True
        flush(pos)
        if out:
            out[-1]["eos"] = True
        a = b
    flush(pos)
    # 1 字だけの行は、12 字に収まるなら前の行に足す
    merged: list[dict[str, Any]] = []
    for c in out:
        if merged and len(c["text"]) <= 1 and len(merged[-1]["text"]) < max_chars:
            merged[-1]["text"] += c["text"]
            merged[-1]["e"] = c["e"]
        else:
            merged.append(c)
    # 0.5 秒未満しか出ない行は、12 字に収まるなら次の行の頭に付ける（一瞬だけ光るのを防ぐ）
    calm: list[dict[str, Any]] = []
    k = 0
    while k < len(merged):
        c = merged[k]
        if k + 1 < len(merged) and merged[k + 1]["t0"] - c["t0"] < 0.5 and not c.get("eos") \
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
        if c.get("e") is not None:
            c["t1"] = max(c["t0"] + 0.4, min(c["t1"], c["e"] + 0.45))   # 言い終わったら少し残して消す
        c["t0"] -= start
        c["t1"] = min(c["t1"], c["t0"] + start + 4.0) - start      # 黙っている間は 4 秒で消す
    # 次の行と重ならないようにし、一瞬（0.3 秒未満）しか出せない行は出さない
    for a, b in zip(merged, merged[1:]):
        a["t1"] = min(a["t1"], b["t0"])
    return [c for c in merged if c["t1"] - c["t0"] >= 0.3 and len(c["text"]) >= 2]


PAD = "0x0A1028"       # 画面全体を映すときの上下の余白の色（背景の濃紺に合わせる）
ZOOM_TWO = 1.35        # 2 人以上映っているとき、話し手に寄る倍率
ZOOM_ONE = 1.2         # 1 人のとき、カットごとに「引き／寄り」を入れ替える寄りの倍率


SUB_ZONE = 150         # 映像の下の、字幕（または小見出し 1 行ぶん）を出す帯の高さ
CREDIT_H = 70


def _place(lay: dict[str, Any]) -> dict[str, Any]:
    """かたまり（黒帯＋水色の帯＋映像＋字幕／小見出し＋切り抜き元）を、上下の余白が同じになる高さに置く."""
    zone = SUB_ZONE if lay["subs"] else 300          # 字幕なし（生配信以外）は小見出しを 2 行まで出す
    block = TOP + lay["vid_h"] + zone + CREDIT_H
    y0 = max(0, (H - block) // 2)
    end_y = y0 + TOP + lay["vid_h"]
    return {**lay, "y0": y0, "end_y": end_y, "sub_y": end_y + 26, "credit_y": end_y + zone + 6}


def plan_layout(face: list[float] | None, is_live: bool, src_w: int, src_h: int) -> dict[str, Any]:
    """映像の置き方を決める。view は 1 カットぶんの見せ方（顔カメラ／画面全体）を ffmpeg のフィルタにして返す."""
    if face:
        x, y, w, h = face
        fx, fy, fw, fh = int(src_w * x), int(src_h * y), int(src_w * w), int(src_h * h)
        box_h = int(W * fh / fw) // 2 * 2             # 顔カメラをそのままの横長で映す
        full_h = int(W * src_h / src_w) // 2 * 2

        def view(i: int, src: str, screen: bool, shot: dict[str, Any] | None = None) -> list[str]:
            if screen:
                return [f"{src}scale={W}:{full_h}:flags=lanczos,pad={W}:{box_h}:0:(oh-ih)/2:color={PAD},setsar=1[v{i}]"]
            z = float(shot["zoom"]) if shot else 1.0
            cw, ch = int(fw / z) // 2 * 2, int(fh / z) // 2 * 2
            cx = fx + (shot["cx"] if shot else 0.5) * fw
            cy = fy + (shot["cy"] if shot else 0.5) * fh
            x0 = int(min(max(fx, cx - cw / 2), fx + fw - cw))
            y0 = int(min(max(fy, cy - ch / 2), fy + fh - ch))
            return [f"{src}crop={cw}:{ch}:{x0}:{y0},scale={W}:{box_h}:flags=lanczos,setsar=1[v{i}]"]

        return _place({"view": view, "face_crop": (fx, fy, fw // 2 * 2, fh // 2 * 2), "vid_h": box_h, "subs": is_live})
    # 顔カメラの場所が決まっていない動画: 中央を 4:3 に切り出して大きく見せる
    cw = min(src_w, int(src_h * 4 / 3) // 2 * 2)
    vid_h = int(W * src_h / cw) // 2 * 2

    def view(i: int, src: str, screen: bool, shot: dict[str, Any] | None = None) -> list[str]:
        return [f"{src}crop={cw}:{src_h}:(iw-{cw})/2:0,scale={W}:{vid_h}:flags=lanczos,setsar=1[v{i}]"]

    return _place({"view": view, "face_crop": None, "vid_h": vid_h, "subs": is_live})


def probe(src: Path) -> tuple[int, int, float]:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True)
    rows = [ln for ln in p.stdout.strip().splitlines() if ln]
    w, h = rows[0].split(",")[:2]
    return int(w), int(h), float(rows[-1].split(",")[0])


def shots_for(src: Path, a: float, b: float, lay: dict[str, Any], flip: int) -> list[dict[str, Any]]:
    """区間 a〜b のカット割り（どこからどこまで・どこへ寄るか）。顔が見つからなければ寄らない 1 カット."""
    if not lay["face_crop"]:
        return [{"start": a, "end": b, "shot": None}]
    found = speaker.follow(src, a, b, lay["face_crop"])
    if not found:
        return [{"start": a, "end": b, "shot": None}]
    out = []
    for k, f in enumerate(found):
        if f["people"] >= 2:
            shot = {"cx": f["cx"], "cy": f["cy"], "zoom": ZOOM_TWO}
        else:
            # 1 人のときは、カットごとに引きと寄りを入れ替える（ジャンプカットが自然に見える）
            shot = {"cx": f["cx"], "cy": f["cy"], "zoom": ZOOM_ONE} if (flip + k) % 2 else None
        out.append({"start": f["start"], "end": f["end"], "shot": shot})
    return out


def render_short(clip: dict[str, Any], src: Path, out: Path, fonts: dict[str, Path], fps: int = 30,
                 proof: Any = None) -> Path:
    """src は clip["start"]〜clip["end"] を落としたもの。その中の segments をジャンプカットでつなぐ."""
    work = out.parent / (out.stem + "_parts")
    work.mkdir(parents=True, exist_ok=True)
    sw_, sh_, src_dur = probe(src)
    lay = plan_layout(clip["video"].get("face"), bool(clip.get("is_live")), sw_, sh_)
    frame = frame_layer(clip, lay, fonts, work / "frame.png")
    bg = background(work / "bg.png")
    segs = clip.get("segments") or [{"start": clip["start"], "end": clip["end"], "screen": False}]
    base = clip.get("dl_start", clip["start"])      # 落とした動画の頭の時刻

    # 区間ごとに切り出してつなぐ。映像は「区間 × 寄りの切り替え」で細かく刻み、音は区間ごとに 1 本。
    # 字幕は区間ごとに作って、つないだ後の時刻にずらす
    f: list[str] = []
    subs: list[dict[str, Any]] = []
    t = 0.0
    nv = na = 0
    spans: list[tuple[float, float]] = []
    yt_lines: list[dict[str, Any]] = []
    for seg in segs:
        a, b = max(0.0, seg["start"] - base), min(src_dur, seg["end"] - base)
        if b - a < 0.5:
            continue
        screen = bool(seg.get("screen"))
        cuts = [{"start": a, "end": b, "shot": None}] if screen else shots_for(src, a, b, lay, na)
        for cut in cuts:
            if cut["end"] - cut["start"] < 0.05:
                continue
            f += lay["view"](nv, f"[0:v]trim=start={cut['start']:.3f}:end={cut['end']:.3f},setpts=PTS-STARTPTS,fps={fps},",
                             screen, cut["shot"])
            nv += 1
        f.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                 f"afade=t=in:d=0.03,afade=t=out:st={max(0.0, b - a - 0.03):.3f}:d=0.03[a{na}]")
        na += 1
        if lay["subs"]:
            for s in subtitle_chunks(clip["lines"], seg["start"], seg["end"]):
                subs.append({"text": s["text"], "t0": t + s["t0"], "t1": min(t + s["t1"], t + (b - a))})
        # 自動字幕の行（1 行＝ 1 つの発話）を、つないだ後の時間軸に並べておく。相づちだけの行（「うん」「はい」）は字幕にしない
        for ln in clip["lines"]:
            ws = [w for w in ln["words"] if seg["start"] <= w["t"] < seg["end"]]
            txt = re.sub(r"\[[^\]]*\]|\s+", "", "".join(w["w"] for w in ws))
            if not txt or re.fullmatch(r"(うん|はい|ええ|へえ|ああ|え|あ|そう|おお)[。、]*", txt):
                continue
            yt_lines.append({"t": t + ws[0]["t"] - seg["start"], "e": t + min(seg["end"], ln["end"]) - seg["start"],
                             "text": txt})
        spans.append((a, b))
        t += b - a
    if not na or not nv:
        raise RuntimeError("つなぐ区間がありません")
    dur = t
    if lay["subs"]:
        # YouTube の自動字幕は時刻が最大 1 秒ほどずれるので、つないだ音を聞き取り直して話し始めに合わせる。
        # 聞き取れなかったとき（Whisper が無いなど）だけ、自動字幕の時刻のまま出す
        log.info("字幕の時刻を合わせています")
        # 文字: 自動字幕と聞き取りの 2 つを見比べて誤字を直す（proof。使えないときは自動字幕のまま）。
        # 時刻: 聞き取りに合わせる。突き合わせられないときは聞き取りの文字と時刻をそのまま使う
        heard = listen.words(src, spans, work / "cut.wav", clip.get("info_title", ""))
        if heard:
            texts = [ln["text"] for ln in yt_lines]
            if proof:
                texts = proof(texts, "".join(h["text"] for h in heard), clip.get("info_title", "")) or texts
            yt_words: list[dict[str, Any]] = []
            for ln, txt in zip(yt_lines, texts):
                # 1 行の中の文字のだいたいの時刻（突き合わせる範囲を絞るのに使うだけ。行の長さに沿って等分）
                span = max(0.3, ln["e"] - ln["t"])
                for k, ch in enumerate(txt):
                    yt_words.append({"t": ln["t"] + span * k / max(1, len(txt)), "w": ch, "brk": k == 0})
            fixed = listen.retime(yt_words, heard)
            subs = subtitle_chunks([{"words": fixed}] if fixed else heard, 0.0, dur)
    f.append("".join(f"[v{i}]" for i in range(nv)) + f"concat=n={nv}:v=1:a=0[vc]")
    f.append("".join(f"[a{i}]" for i in range(na)) + f"concat=n={na}:v=0:a=1[ac]")
    f.append(f"[2:v][vc]overlay=0:{lay['y0'] + TOP}:shortest=1[base]")
    f.append("[base][1:v]overlay=0:0[v_0]")
    pngs = [subtitle_png(s["text"], fonts["gothic"], work / f"s{i:03d}.png") for i, s in enumerate(subs)]
    inputs = ["-i", str(src), "-loop", "1", "-t", f"{dur:.2f}", "-i", str(frame),
              "-loop", "1", "-t", f"{dur:.2f}", "-i", str(bg)]
    for p in pngs:
        inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(p)]
    last = "v_0"
    for i, s in enumerate(subs):
        nxt = f"v_{i + 1}"
        f.append(f"[{last}][{i + 3}:v]overlay=(W-w)/2:{lay['sub_y']}:"
                 f"enable='between(t,{s['t0']:.2f},{s['t1']:.2f})'[{nxt}]")
        last = nxt
    f.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(f), "-map", f"[{last}]", "-map", "[aout]",
           "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"動画を作れませんでした: {p.stderr[-1200:]}")
    return out
