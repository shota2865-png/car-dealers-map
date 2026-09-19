"""字幕生成.

TTS が返した文ごとのタイムコードをそのまま使うので、音ズレが構造的に起きない。
長い文は文字数比で分割して複数キューにする（画面に出るのは常に最大2行）。

出力は ASS（焼き込み用）と SRT（YouTube に字幕として渡す用）の2つ。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ImageFont

from .assets import font_path, safe_text
from .config import Config
from .script import VideoScript
from .tts import VoiceTrack


def load_semantics(cfg: Config) -> dict:
    """テロップ・色・効果音の意味の定義を読む."""
    path = cfg.root / "config" / "style_semantics.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]
    style: str = "Default"      # ASS のスタイル名


@dataclass
class TelopCue:
    """意味付きテロップ。字幕とは別レイヤーに出す."""
    start: float
    end: float
    text: str
    type: str = "NORMAL"


_NO_LINE_START = "。、」』）｝】〕〉》・ーぁぃぅぇぉっゃゅょゎ々！？!?"


def _balance(text: str, per_line: int) -> list[str]:
    """1キュー分の文字列を、行の長さが揃うように割る（禁則つき）.

    単純に per_line ごとに切ると「円安なのだ」+「。」のように句点だけが
    次の行に落ちる。行数を先に決めて均等に割り、行頭に来てはいけない字は
    前の行に送る。
    """
    text = text.strip()
    if not text:
        return [""]
    n_lines = max(1, -(-len(text) // per_line))          # ceil
    width = -(-len(text) // n_lines)
    rows = [text[i:i + width] for i in range(0, len(text), width)]
    # 禁則: 行頭の句読点などを前の行の末尾へ
    for i in range(1, len(rows)):
        while rows[i] and rows[i][0] in _NO_LINE_START and rows[i - 1]:
            rows[i - 1] += rows[i][0]
            rows[i] = rows[i][1:]
    return [r for r in rows if r]


def _chunk(text: str, per_line: int, max_lines: int = 2) -> list[list[str]]:
    """テキストを『最大 max_lines 行』の塊の列に割る."""
    # 表示上は文末の句点を落とす（日本語字幕の慣習。行末の「。」だけの行も防げる）
    text = text.strip().rstrip("。")
    rows = _balance(text, per_line)
    return [rows[i:i + max_lines] for i in range(0, len(rows), max_lines)] or [[""]]


def build_cues(cfg: Config, track: VoiceTrack) -> list[Cue]:
    per_line = int(cfg.get("visuals.subtitle.max_chars_per_line", 20))
    cues: list[Cue] = []
    for line in track.lines:
        groups = _chunk(line.text, per_line)
        total_chars = sum(len("".join(g)) for g in groups) or 1
        t = line.start
        for group in groups:
            share = len("".join(group)) / total_chars
            dur = max(line.duration * share, 0.6)
            end = min(t + dur, line.end) if len(groups) > 1 else line.end
            cues.append(Cue(start=t, end=max(end, t + 0.4), lines=group))
            t = end
    return cues


def build_telops(cfg: Config, script: VideoScript,
                 track: VoiceTrack) -> list[TelopCue]:
    """台本のテロップ指定を、音声の実測時刻に貼り付ける.

    after_sentence（何文目の後か）を、その文の終了時刻に変換する。
    音声から時刻が確定しているので、ここで推定は一切要らない。
    """
    telops: list[TelopCue] = []
    default_hold = 2.6

    for i, section in enumerate(script.sections):
        block = f"s{i}"
        lines = [ln for ln in track.lines if ln.block_id == block]
        if not lines:
            continue
        for cap in section.captions:
            idx = max(0, min(cap.after_sentence, len(lines) - 1))
            start = lines[idx].end
            # 次の文の終わりまで、または既定の表示時間
            nxt = lines[idx + 1].end if idx + 1 < len(lines) else start + default_hold
            telops.append(TelopCue(
                start=start,
                end=min(nxt, start + 4.5),
                text=cap.text[:16],
                type=cap.type,
            ))

    telops.sort(key=lambda t: t.start)
    # 重なりを解消する（同時に2つ出すと読めない）
    for a, b in zip(telops, telops[1:]):
        if a.end > b.start:
            a.end = max(b.start - 0.1, a.start + 0.6)
    return telops


def _ass_time(seconds: float) -> str:
    seconds = max(seconds, 0)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _srt_time(seconds: float) -> str:
    seconds = max(seconds, 0)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_color(hex_color: str) -> str:
    """#RRGGBB → &H00BBGGRR （ASS は BGR 順）."""
    s = hex_color.lstrip("#")
    return f"&H00{s[4:6]}{s[2:4]}{s[0:2]}".upper()


def font_family(cfg: Config) -> str:
    """libass にフォントを名指しするためのファミリ名を実ファイルから取る.

    字幕も本文と同じ Black を使う。細いと動画上で潰れて読めない。
    """
    family, _style = ImageFont.truetype(font_path(cfg, "black"), 20).getname()
    return family


def _style_line(name: str, family: str, size: int, primary: str,
                outline_color: str, outline: int, alignment: int,
                margin_v: int, bold: int = -1, margin_r: int = 120) -> str:
    return (
        f"Style: {name},{family},{size},{_ass_color(primary)},&H000000FF,"
        f"{_ass_color(outline_color)},&H64000000,{bold},0,0,0,100,100,1,0,1,"
        f"{outline},2,{alignment},120,{margin_r},{margin_v},1"
    )


def write_ass(cfg: Config, cues: list[Cue], out: str | Path,
              telops: list[TelopCue] | None = None, reserve_right: int = 0) -> Path:
    """字幕とテロップを1つの ASS にまとめる.

    字幕は画面下に出しっぱなし、テロップは意味ごとに色と大きさを変えて
    上寄りに出す。両方を同じファイルに入れるのは、焼き込みが1パスで済み、
    重なり順も ASS 側で決まるため。
    """
    sem = load_semantics(cfg)
    types = sem.get("caption_types", {})
    colors = {k: v.get("hex", "#FFFFFF")
              for k, v in (sem.get("color_semantics", {}) or {}).items()}
    colors.setdefault("text", cfg.get("visuals.palette.text", "#FFFFFF"))

    base_size = int(cfg.get("visuals.subtitle.font_size", 58))
    outline = int(cfg.get("visuals.subtitle.outline", 5))
    w, h = cfg.get("video.resolution", [1920, 1080])
    family = font_family(cfg)
    stroke = "#0B1120"

    # 右下にキャラクターがいるぶん、文字は左寄りの領域に収める
    margin_r = max(120, int(reserve_right))
    styles = [
        # 字幕。画面下（alignment 2 = 下中央）
        _style_line("Default", family, base_size, colors["text"], stroke,
                    outline, 2, 72, margin_r=margin_r),
    ]
    # テロップは字幕のすぐ上（下三分の一）に置く。
    # 画面上部は背景側の見出しが使うので、そこへ出すと必ずぶつかる。
    sub_margin = 72
    telop_margin = sub_margin + base_size + 44
    for name, spec in types.items():
        color = colors.get(spec.get("color", "text"), colors["text"])
        size = int(base_size * float(spec.get("size_scale", 1.0)))
        if name == "EDITORIAL":
            # 編集者の声は隅に小さく。本人より目立たせない
            alignment, margin = 7, 150
        else:
            alignment, margin = 2, telop_margin
        styles.append(_style_line(f"T_{name}", family, size, color, stroke,
                                  outline + 1, alignment, margin, margin_r=margin_r))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
""" + "\n".join(styles) + """

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = [
        "Dialogue: 0,{},{},{},,0,0,0,,{}".format(
            _ass_time(c.start), _ass_time(c.end), c.style,
            r"\N".join(safe_text(cfg, ln) for ln in c.lines)
        )
        for c in cues
    ]
    for t in telops or []:
        style = f"T_{t.type}" if f"T_{t.type}" in {f"T_{k}" for k in types} \
            else "T_NORMAL"
        events.append(
            "Dialogue: 1,{},{},{},,0,0,0,,{}".format(
                _ass_time(t.start), _ass_time(t.end), style, safe_text(cfg, t.text)
            )
        )

    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return p


def write_srt(cues: list[Cue], out: str | Path) -> Path:
    blocks = []
    for i, c in enumerate(cues, 1):
        blocks.append(
            f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n" + "\n".join(c.lines)
        )
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return p


def build(cfg: Config, track: VoiceTrack, outdir: str | Path,
          script: VideoScript | None = None, reserve_right: int = 0) -> dict[str, Path]:
    outdir = Path(outdir)
    cues = build_cues(cfg, track)
    telops = build_telops(cfg, script, track) if script else []
    return {
        "ass": write_ass(cfg, cues, outdir / "subtitles.ass", telops=telops,
                         reserve_right=reserve_right),
        # SRT は YouTube に渡す字幕なので、テロップは入れない
        "srt": write_srt(cues, outdir / "subtitles.srt"),
    }
