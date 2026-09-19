"""字幕生成.

TTS が返した文ごとのタイムコードをそのまま使うので、音ズレが構造的に起きない。
長い文は文字数比で分割して複数キューにする（画面に出るのは常に最大2行）。

出力は ASS（焼き込み用）と SRT（YouTube に字幕として渡す用）の2つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from .assets import font_path
from .config import Config
from .tts import VoiceTrack


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]


def _chunk(text: str, per_line: int, max_lines: int = 2) -> list[list[str]]:
    """テキストを『最大 max_lines 行』の塊の列に割る."""
    rows = [text[i:i + per_line] for i in range(0, len(text), per_line)] or [""]
    return [rows[i:i + max_lines] for i in range(0, len(rows), max_lines)]


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
    """libass にフォントを名指しするためのファミリ名を実ファイルから取る."""
    family, _style = ImageFont.truetype(font_path(cfg), 20).getname()
    return family


def write_ass(cfg: Config, cues: list[Cue], out: str | Path) -> Path:
    pal = {"text": "#FFFFFF", "outline": "#0B1120"}
    pal.update({"text": cfg.get("visuals.palette.text", "#FFFFFF")})
    size = int(cfg.get("visuals.subtitle.font_size", 58))
    outline = int(cfg.get("visuals.subtitle.outline", 5))
    w, h = cfg.get("video.resolution", [1920, 1080])

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_family(cfg)},{size},{_ass_color(pal['text'])},&H000000FF,{_ass_color(pal['outline'])},&H64000000,-1,0,0,0,100,100,1,0,1,{outline},2,2,120,120,72,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = "\n".join(
        "Dialogue: 0,{},{},Default,,0,0,0,,{}".format(
            _ass_time(c.start), _ass_time(c.end), r"\N".join(c.lines)
        )
        for c in cues
    )
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + body + "\n", encoding="utf-8")
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


def build(cfg: Config, track: VoiceTrack, outdir: str | Path) -> dict[str, Path]:
    outdir = Path(outdir)
    cues = build_cues(cfg, track)
    return {
        "ass": write_ass(cfg, cues, outdir / "subtitles.ass"),
        "srt": write_srt(cues, outdir / "subtitles.srt"),
    }
