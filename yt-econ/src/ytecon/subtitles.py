"""字幕生成.

TTS が返した文ごとのタイムコードをそのまま使うので、音ズレが構造的に起きない。
長い文は文節で分割して複数キューにする（画面に出るのは**常に1行**。
2〜3行に折り返すと不自然になるので、行に収まる文節ごとに送る）。

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

# 文節の切れ目とみなす助詞・接続助詞（この直後で切ってよい）
_PARTICLES = ("ので", "けど", "けれど", "から", "まで", "って", "とか", "たら", "ながら",
              "ように", "は", "が", "を", "に", "で", "と", "も", "へ", "や", "て")
_PUNCT = "、。，！？!?…"


# 切った直後にこれが来る位置では切らない（「と｜いう」「し｜て」のような不自然な割れを防ぐ）
_NO_CUT_BEFORE = ("いう", "いえ", "して", "した", "なる", "なっ", "いる", "いた", "ある", "あっ",
                  "おく", "みる", "くる", "しまう", "ください", "ほしい", "のだ", "なのだ",
                  "です", "ます", "だっ", "だ", "か", "ね", "よ")
# 節の終わりになりやすい助詞（ここで切ると自然）
_CLAUSE_END = ("ので", "けど", "けれど", "から", "たら", "ながら", "ように", "て", "と", "ば")


_PARTICLE_HEADS = set("はがをにでともへやのか")   # 行頭に来させない（「で｜は」「と｜いう」を防ぐ）


def _is_kana(ch: str) -> bool:
    return "ぁ" <= ch <= "ん"


def _best_cut(seg: str, max_chars: int) -> int:
    """seg を max_chars 以内で切る位置を選ぶ。自然さを点数にして一番よい所."""
    best_i, best_score = -1, -1e9
    for i in range(3, min(len(seg) - 1, max_chars) + 1):
        head, tail = seg[:i], seg[i:]
        if tail[0] in _NO_LINE_START or tail[0] in _PARTICLE_HEADS or tail.startswith(_NO_CUT_BEFORE):
            continue
        hit = next((pt for pt in _PARTICLES if head.endswith(pt)), None)
        if head[-1] in "、，":
            score = i + 10                         # 読点の直後が最良
        elif hit is not None:
            score = i + (6 if hit in _CLAUSE_END else 0)
        elif head[-1] == "の" and not _is_kana(tail[0]):
            score = i + 2                          # 「名詞の｜名詞」は切ってよい（弱め）
        else:
            continue
        if len(tail) < 5:
            score -= 8                             # 次の行が短すぎる
        if len(head) < 6:
            score -= 6                             # この行が短すぎる
        if score > best_score:
            best_i, best_score = i, score
    if best_i > 0:
        return best_i
    i = min(max_chars, len(seg) - 1)
    while i > 1 and seg[i] in _NO_LINE_START:
        i -= 1
    return i


def phrase_split(text: str, max_chars: int) -> list[str]:
    """1文を『1行に収まる文節のかたまり』に割る（常に1行で出すための分割）.

    読点の直後 → 節の終わりの助詞 → 格助詞 → 機械的、の順に自然な所で切る。
    「話していることは1行で。分かりやすい文節で区切る」がここの仕事。
    """
    text = text.strip().rstrip("。")
    if not text:
        return []
    pieces: list[str] = []
    seg = text
    while len(seg) > max_chars:
        i = _best_cut(seg, max_chars)
        pieces.append(seg[:i])
        seg = seg[i:]
    if seg:
        pieces.append(seg)
    # 短すぎる断片（「で、」「は」「この感覚」）は隣と結合する。
    # 収まらなければ前の行と合わせて切り直す。孤立させるくらいなら 1 字だけはみ出してよい
    merged: list[str] = []
    for pc in pieces:
        if merged and (len(pc.rstrip("、，")) <= 5 or len(merged[-1].rstrip("、，")) <= 5):
            joined = merged[-1] + pc
            # 読点は幅が狭いので数えない。3 字以下の断片（値段は／呼ぶ／いる）を孤立させる
            # くらいなら 2 字までのはみ出しを許す
            shortest = min(len(pc.rstrip("、，")), len(merged[-1].rstrip("、，")))
            visible = len(joined.replace("、", "").replace("，", ""))
            if visible <= max_chars + (2 if shortest <= 4 else 1):
                merged[-1] = joined
                continue
            if len(pc) <= 5:
                i = _best_cut(joined, max_chars)
                if 5 <= i <= len(joined) - 5:
                    merged[-1] = joined[:i]
                    merged.append(joined[i:])
                    continue
        merged.append(pc)
    # 行末の読点は落とす（行の中の読点は残す）
    return [m.rstrip("、，") or m for m in merged]


def chars_per_line(cfg: Config, reserve_right: int = 0) -> int:
    """1行に入れてよい文字数。フォントサイズとキャラの占有幅から決め、設定値を上限にする."""
    from . import design

    w, _h = cfg.get("video.resolution", [1920, 1080])
    size = int(cfg.get("visuals.subtitle.font_size", 0) or design.type_size(cfg, "subtitle", 64))
    margin_r = max(120, int(reserve_right))
    usable = w - margin_r * 2          # 左右対称の余白（中央揃え）
    by_width = max(8, int(usable / (size * 1.02)))
    limit = int(cfg.get("visuals.subtitle.max_chars_per_line", 20))
    return min(by_width, limit)


def build_cues(cfg: Config, track: VoiceTrack, reserve_right: int = 0) -> list[Cue]:
    """文ごとの実測時刻を、1行ずつの文節に配る。表示は常に1行."""
    per_line = chars_per_line(cfg, reserve_right)
    min_show = float(cfg.get("visuals.subtitle.min_seconds", 0.7))
    cues: list[Cue] = []
    for line in track.lines:
        pieces = phrase_split(line.text, per_line)
        if not pieces:
            continue
        total_chars = sum(len(p) for p in pieces) or 1
        t = line.start
        for k, piece in enumerate(pieces):
            if k == len(pieces) - 1:
                end = line.end
            else:
                end = t + max(line.duration * len(piece) / total_chars, min_show)
                end = min(end, line.end)
            cues.append(Cue(start=t, end=max(end, t + 0.3), lines=[piece]))
            t = end
    # 表示が短すぎる断片は次と結合して読める長さにする（1行に収まるときだけ）
    out: list[Cue] = []
    for c in cues:
        if out and (c.end - c.start) < min_show * 0.6 and \
                len(out[-1].lines[0]) + len(c.lines[0]) <= per_line:
            out[-1].lines[0] += c.lines[0]
            out[-1].end = c.end
        else:
            out.append(c)
    return out


def _balance(text: str, per_line: int) -> list[str]:
    """（互換用）1行に収める。長ければ文節で割った先頭だけ返す."""
    pieces = phrase_split(text, per_line)
    return pieces[:1] or [""]


def _chunk(text: str, per_line: int, max_lines: int = 1) -> list[list[str]]:
    """（互換用）常に1行ずつ."""
    return [[p] for p in phrase_split(text, per_line)] or [[""]]


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
                margin_v: int, bold: int = -1, margin_r: int = 120,
                margin_l: int | None = None) -> str:
    # 左右の余白を同じにして、字幕を**画面の中央**に置く。キャラのぶん右を空ける
    # 必要があるときは、左も同じだけ空けて中央を保つ（左に寄って見えるのを防ぐ）
    margin_l = margin_r if margin_l is None else margin_l
    return (
        f"Style: {name},{family},{size},{_ass_color(primary)},&H000000FF,"
        f"{_ass_color(outline_color)},&H64000000,{bold},0,0,0,100,100,1,0,1,"
        f"{outline},2,{alignment},{margin_l},{margin_r},{margin_v},1"
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

    from . import design
    # 字幕の大きさは本文トークン（body_l）が既定。config で明示したらそちら
    base_size = int(cfg.get("visuals.subtitle.font_size", 0) or design.type_size(cfg, "subtitle", 64))
    outline = int(cfg.get("visuals.subtitle.outline", 0) or design.stroke(cfg, "text_outline"))
    w, h = cfg.get("video.resolution", [1920, 1080])
    family = font_family(cfg)
    stroke = "#0B1120"

    # 右下にキャラクターがいるぶん右を空けるが、左も同じだけ空けて字幕は画面中央に置く
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
            # 編集者の声は左上の隅に小さく。本人より目立たせない
            alignment, margin, ml = 7, 150, 120
        else:
            alignment, margin, ml = 2, telop_margin, None
        styles.append(_style_line(f"T_{name}", family, size, color, stroke,
                                  outline + 1, alignment, margin, margin_r=margin_r, margin_l=ml))

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
    cues = build_cues(cfg, track, reserve_right=reserve_right)
    telops = build_telops(cfg, script, track) if script else []
    return {
        "ass": write_ass(cfg, cues, outdir / "subtitles.ass", telops=telops,
                         reserve_right=reserve_right),
        # SRT は YouTube に渡す字幕なので、テロップは入れない
        "srt": write_srt(cues, outdir / "subtitles.srt"),
    }
