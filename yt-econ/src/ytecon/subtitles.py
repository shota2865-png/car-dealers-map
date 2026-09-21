"""字幕生成.

TTS が返した文ごとのタイムコードをそのまま使うので、音ズレが構造的に起きない。
長い文は文節で分割して複数キューにする（画面に出るのは**常に1行**。
2〜3行に折り返すと不自然になるので、行に収まる文節ごとに送る）。

出力は ASS（焼き込み用）と SRT（YouTube に字幕として渡す用）の2つ。
"""

from __future__ import annotations

import json
import re
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


_NO_LINE_START = "。、」』）｝】〕〉》・ーぁぃぅぇぉっゃゅょゎ々ん！？!?"
_NO_LINE_END = "「『（(〔［｛〈《【"     # 行末に置かない（開き括弧）

# 文節の切れ目とみなす助詞・接続助詞（この直後で切ってよい）
_PARTICLES = ("ので", "けど", "けれど", "から", "まで", "って", "とか", "たら", "ながら", "なら",
              "ように", "より", "こそ", "だけ", "など", "ほど", "しか", "でも",
              "は", "が", "を", "に", "で", "と", "も", "へ", "て")
_PUNCT = "、。，！？!?…"


# 切った直後にこれが来る位置では切らない（「と｜いう」「し｜て」のような不自然な割れを防ぐ）
_NO_CUT_BEFORE = ("いう", "いえ", "して", "した", "なる", "なっ", "いる", "いた", "ある", "あっ",
                  "おく", "みる", "くる", "しまう", "ください", "ほしい", "のだ", "なのだ", "する", "すれ", "され", "せる",
                  "ない", "なく", "なかっ", "ません", "です", "ます", "だっ", "だ", "か", "ね", "よ")
# 節の終わりになりやすい助詞（ここで切ると自然）
_CLAUSE_END = ("ので", "けど", "けれど", "から", "たら", "ながら", "なら", "ように", "て", "と", "ば")
# 1 字の助詞に見えて語の一部（「思う｜はず」「で｜きる」）。この並びでは切らない
_FALSE_PARTICLE = ("はず", "でき", "とき", "ところ", "がち", "がわ", "にく", "もの", "こと", "やり", "やす", "やっ",
                   "もら", "もう", "もし", "とも", "でも", "では", "には", "とは")
OVERFLOW = 1   # 語を割るくらいなら 1 字だけはみ出してよい（字幕の余白に 1 字ぶんの遊びがある）


_PARTICLE_HEADS = set("はがをにでともへやのか")   # 行頭に来させない（「で｜は」「と｜いう」を防ぐ）


def _is_kana(ch: str) -> bool:
    return "ぁ" <= ch <= "ん"


def _kanji(ch: str) -> bool:
    return "一" <= ch <= "龥" or ch == "々"


def _digit(ch: str) -> bool:
    return ch.isdigit() or ch in ".．,"


def _bad_head(tail: str) -> bool:
    """この文字列で行を始めてはいけないか（禁則・助詞・「いう」など）. 語（もらう/もの）なら助詞扱いしない."""
    if not tail or tail[0] in _NO_LINE_START:
        return True
    for w in _NO_CUT_BEFORE:
        if not tail.startswith(w):
            continue
        # 1 字のもの（だ・か・ね・よ）は終助詞のときだけ。「よかったら」「だから」は語なので切ってよい
        if len(w) > 1 or len(tail) == 1 or tail[1] in "、。，！？!?…":
            return True
    return tail[0] in _PARTICLE_HEADS and not tail.startswith(_FALSE_PARTICLE)


def _kata(ch: str) -> bool:
    return ("ァ" <= ch <= "ヶ") or ch == "ー"


def _best_cut(seg: str, max_chars: int) -> int:
    """seg を max_chars 以内で切る位置を選ぶ。自然さを点数にして一番よい所."""
    return _cut(seg, max_chars)[0]


def _cut(seg: str, max_chars: int, min_head: int = 3) -> tuple[int, bool]:
    """(切る位置, 自然な切れ目か)。自然な切れ目 = 読点・助詞・名詞のの直後."""
    best_i, best_score = -1, -1e9
    for i0 in range(min_head, min(len(seg) - 1, max_chars + OVERFLOW) + 1):
        i = i0
        if seg[i] in "、，" and i + 1 < len(seg):
            i += 1                                 # 読点は前の行にくっつける（幅は数えない）
        head, tail = seg[:i], seg[i:]
        over = max(0, i0 - max_chars)
        if _bad_head(tail) or len(head.rstrip("、，")) < min_head or head[-1] in _NO_LINE_END:
            continue
        hit = next((pt for pt in _PARTICLES if head.endswith(pt)), None)
        if hit is not None and len(hit) == 1 and (head[-1] + tail).startswith(_FALSE_PARTICLE):
            continue                               # 「思う｜はず」のような語の途中
        if head[-1] in "、，":
            score = i0 + 10                        # 読点の直後が最良
        elif hit is not None:
            score = i + (6 if hit in _CLAUSE_END else 0)
            if len(hit) == 1 and _is_kana(tail[0]):
                if hit in "てで" and not tail.startswith(_FALSE_PARTICLE):
                    continue                       # 「打て｜る」「出て｜くる」: て形の次のひらがなは動詞の続き
                score -= 3                         # 1 字の助詞の次がひらがなだと語の途中かもしれない
        elif head[-1] == "の" and not _is_kana(tail[0]):
            score = i + 2                          # 「名詞の｜名詞」は切ってよい（弱め）
        else:
            continue
        if len(tail) < 5:
            score -= 8                             # 次の行が短すぎる
        if len(head) < 6:
            score -= 6                             # この行が短すぎる
        score -= over * 5                          # はみ出しは最後の手段
        if score > best_score:
            best_i, best_score = i, score
    if best_i > 0 and best_score >= -4:
        return best_i, True
    # 自然な切れ目が無いときは、字種の変わり目（漢字→かな、かな→漢字）を選ぶ。
    # 漢字熟語・カタカナ語・数字の途中（「以｜来」「パス｜タ」「19｜90」）は避ける
    best_i, best_score = -1, -1e9
    for i in range(3, min(len(seg) - 1, max_chars + OVERFLOW) + 1):
        prev, nxt = seg[i - 1], seg[i]
        if nxt in _NO_LINE_START or seg[i:].startswith(_NO_CUT_BEFORE) or prev in _NO_LINE_END:
            continue
        score = i * 0.5 - max(0, i - max_chars) * 4   # 長い行のほうがよい（行数が減る）。はみ出しは最後の手段
        if _bad_head(seg[i:]):
            score -= 5                             # 助詞で行を始めるのは、語を割るよりはまし
        if _kanji(prev) and _kanji(nxt):
            score -= 8
        elif _kata(prev) and _kata(nxt):
            score -= 10
        elif _digit(prev) and (_digit(nxt) or nxt in "年月日円%％割人倍" or seg[i:].startswith("パーセント")):
            score -= 10
        elif _is_kana(prev) and _is_kana(nxt):
            score -= 3
        elif _is_kana(prev) and (_kanji(nxt) or _kata(nxt)):
            score += 5                             # 「〜する｜物価」語の始まり
        elif _kanji(prev) and _is_kana(nxt):
            score += 2                             # 「物価｜が」語の終わり
        if score > best_score:
            best_i, best_score = i, score
    return (best_i if best_i > 0 else max(3, min(max_chars, len(seg) - 1))), False


def _visible(text: str) -> float:
    """見た目の長さ（全角 1、読点 0.5）."""
    n = text.count("、") + text.count("，")
    return len(text) - n * 0.5


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
    while _visible(seg) > max_chars:
        i, natural = _cut(seg, max_chars)
        if not natural and _visible(seg) <= max_chars + OVERFLOW:
            break                                  # 語を割るくらいなら 1 字はみ出して 1 行にする
        pieces.append(seg[:i])
        seg = seg[i:]
    if seg:
        pieces.append(seg)
    # 短すぎる断片（「で、」「は」「この感覚」）は隣と結合する。
    # 収まらなければ合わせて切り直し、釣り合う 2 行にする（語を割ってまでは直さない）
    limit = max_chars + OVERFLOW

    def short(x: str) -> bool:
        return len(x.rstrip("、，")) <= 5

    merged: list[str] = []
    for pc in pieces:
        if merged and (short(pc) or short(merged[-1])):
            joined = merged[-1] + pc
            tiny = min(len(pc.rstrip("、，")), len(merged[-1].rstrip("、，"))) <= 3
            # 「では」「まず」のような 2〜3 字を孤立させるくらいなら、読点ぶん（0.5 字）余計にはみ出してよい
            if _visible(joined) <= limit + (0.5 if tiny else 0):
                merged[-1] = joined
                continue
            done = False
            for mc in (max_chars, max_chars - 2, max_chars - 4):
                i, natural = _cut(joined, mc, min_head=4)
                a, b = joined[:i], joined[i:]
                if natural and len(a.rstrip("、，")) >= 4 and len(b) >= 4 and _visible(b) <= limit:
                    merged[-1], done = a, True
                    merged.append(b)
                    break
            if done:
                continue
        merged.append(pc)
    # 結合で長くなりすぎた行があれば切り直す（はみ出しは 1 字＋読点まで）
    final: list[str] = []
    for pc in merged:
        while _visible(pc) > limit + 0.5:
            i = _best_cut(pc, max_chars)
            final.append(pc[:i])
            pc = pc[i:]
        final.append(pc)
    # 行末の読点は落とす（行の中の読点は残す）
    return [m.rstrip("、，") or m for m in final]


def chars_per_line(cfg: Config, reserve_right: int = 0) -> int:
    """1行に入れてよい文字数。フォントサイズとキャラの占有幅から決め、設定値を上限にする."""
    from . import design

    w, _h = cfg.get("video.resolution", [1920, 1080])
    size = int(cfg.get("visuals.subtitle.font_size", 0) or design.type_size(cfg, "subtitle", 64))
    margin_r = 120 if cfg.get("visuals.subtitle.full_width", False) else max(120, int(reserve_right))
    usable = w - margin_r * 2          # 左右対称の余白（中央揃え）
    by_width = max(7, int(usable / size + 0.25))
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
            cues.append(Cue(start=t, end=max(end, t + 0.3), lines=[piece],
                            style=f"S_{line.speaker}" if line.speaker else "Default"))
            t = end
    # 表示が短すぎる断片は次と結合して読める長さにする（1行に収まるときだけ）
    out: list[Cue] = []
    for c in cues:
        if out and (c.end - c.start) < min_show * 0.6 and c.style == out[-1].style and \
                len(out[-1].lines[0]) + len(c.lines[0]) <= per_line:
            out[-1].lines[0] += c.lines[0]
            out[-1].end = c.end
        else:
            out.append(c)
    # 前後の字幕は時間的に重ねない（重なると libass が 2 段に積み、位置がずれて見える）
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            a.end = max(b.start, a.start + 0.2)
            b.start = max(b.start, a.end)
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


def subtitle_font_path(cfg: Config) -> str:
    """字幕のフォントファイル。visuals.subtitle.font（丸ゴシックなど）があればそれ、無ければ本文と同じ Black."""
    custom = str(cfg.get("visuals.subtitle.font", "") or "").strip()
    if custom:
        p = Path(custom)
        p = p if p.is_absolute() else cfg.root / p
        if p.exists():
            return str(p)
    return font_path(cfg, "black")


def font_family(cfg: Config) -> str:
    """libass にフォントを名指しするためのファミリ名を実ファイルから取る.

    字幕は丸ゴシック（柔らかい声に合う）。細いと動画上で潰れるので太いウェイトだけ使う。
    """
    family, _style = ImageFont.truetype(subtitle_font_path(cfg), 20).getname()
    return family


# ----------------------------------------------------------------------
# 字幕の中の色分け（重要な語だけ色を付ける。同じ語はいつも同じ色）
# ----------------------------------------------------------------------
_DEFAULT_HIGHLIGHT_COLORS = {"red": "#FF6B6B", "blue": "#4CC2FF", "yellow": "#FFD93D", "green": "#B4E65A"}
# 減少・リスク・否定は赤
_NEGATIVE_WORDS = ("マイナス", "減っ", "減る", "減り", "減少", "損", "痛み", "危ない", "注意", "下が",
                   "追いつかない", "追いつけない", "止まっ", "止まる", "削られ", "苦しい")
_NUMBER_RE = re.compile(
    r"[0-9０-９][0-9０-９.,．，]*\s*(?:パーセント|％|%|円|割|倍|か月|ヶ月|カ月|年代|年|月|日|人|万|億|兆|つ|回|個|台|強|弱)?"
    r"(?:台|前後|ほど|以上|以下|近く|連続)?")


def highlight_terms(cfg: Config, script: VideoScript | None, track: VoiceTrack | None) -> list[tuple[str, str]]:
    """色を付ける語と色の対応（語が長い順）。

    数字は黄、減少・リスクは赤、その回の主張（EMPHASIS/PUNCHLINE）は赤、
    キーワード・用語は初出順に青と黄緑を交互に。同じ語には必ず同じ色。
    """
    hl = cfg.get("visuals.subtitle.highlight", {}) or {}
    if isinstance(hl, dict) and not hl.get("enabled", True):
        return []
    colors = {**_DEFAULT_HIGHLIGHT_COLORS, **((hl.get("colors") if isinstance(hl, dict) else None) or {})}
    table: dict[str, str] = {}
    for w in _NEGATIVE_WORDS:
        table[w] = colors["red"]
    if script is not None:
        keywords: list[str] = []
        for sec in script.sections:
            for cap in sec.captions:
                t = cap.text.strip()
                if not t or len(t) > 12 or _NUMBER_RE.fullmatch(t):
                    continue
                if cap.type in ("EMPHASIS", "PUNCHLINE"):
                    table.setdefault(t, colors["red"])
                elif cap.type == "KEYWORD":
                    keywords.append(t)
        for term in script.terms:
            if term.term.strip():
                keywords.append(term.term.strip())
        # 初出順（音声の並び）で青・黄緑を交互に
        corpus = "".join(ln.text for ln in track.lines) if track is not None else ""
        seen: list[str] = []
        for k in keywords:
            if k not in seen:
                seen.append(k)
        seen.sort(key=lambda k: (corpus.find(k) if corpus.find(k) >= 0 else 10 ** 9))
        for n, k in enumerate(seen):
            table.setdefault(k, colors["blue"] if n % 2 == 0 else colors["green"])
    return sorted(table.items(), key=lambda kv: -len(kv[0]))


def _tag_color(hex_color: str) -> str:
    """行内タグ用の色（&HBBGGRR&）."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&".upper()


def colorize(text: str, terms: list[tuple[str, str]], primary: str, max_spans: int = 2,
             number_color: str | None = None) -> str:
    """字幕 1 行に ASS の色タグを入れる。重なりは長い語を優先し、1 行あたり max_spans 個まで."""
    spans: list[tuple[int, int, str]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e, _ in spans)

    for term, color in terms:
        start = 0
        while True:
            i = text.find(term, start)
            if i < 0:
                break
            j = i + len(term)
            # 「減っ」「止まっ」のような語幹だけ色を変えると途中で切れて見えるので、活用語尾まで含める
            if term in _NEGATIVE_WORDS:
                while j < len(text) and j - (i + len(term)) < 4 and text[j] in "ていたるっく":
                    j += 1
            if free(i, j):
                spans.append((i, j, color))
            start = j
    if number_color:
        for m in _NUMBER_RE.finditer(text):
            if m.end() - m.start() >= 1 and free(m.start(), m.end()):
                spans.append((m.start(), m.end(), number_color))
    spans.sort()
    spans = spans[:max_spans]
    if not spans:
        return text
    out, pos = [], 0
    for a, b, color in spans:
        out.append(text[pos:a])
        out.append(f"{{\\1c{_tag_color(color)}}}{text[a:b]}{{\\1c{_tag_color(primary)}}}")
        pos = b
    out.append(text[pos:])
    return "".join(out)


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
              telops: list[TelopCue] | None = None, reserve_right: int = 0,
              highlights: list[tuple[str, str]] | None = None) -> Path:
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
    outline = int(cfg.get("visuals.subtitle.outline", 0) or max(design.stroke(cfg, "text_outline"), base_size // 14))
    w, h = cfg.get("video.resolution", [1920, 1080])
    family = font_family(cfg)
    stroke = "#0B1120"

    # 字幕は画面中央。キャラが字幕帯の上にいる（full_width）なら左右 120px だけ空ける
    margin_r = 120 if cfg.get("visuals.subtitle.full_width", False) else max(120, int(reserve_right))
    styles = [
        # 字幕。画面下（alignment 2 = 下中央）
        _style_line("Default", family, base_size, colors["text"], stroke,
                    outline, 2, 72, margin_r=margin_r),
    ]
    # 掛け合い: 話者ごとに縁の色を変える（ずんだもん動画の慣例。文字色は白のまま）
    for c in (cfg.get("cast.characters", []) or []):
        if c.get("key"):
            styles.append(_style_line(f"S_{c['key']}", family, base_size, colors["text"],
                                      str(c.get("subtitle_outline") or stroke), outline, 2, 72, margin_r=margin_r))
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

    # 字の大きさは全行おなじ（行ごとに縮めない）。長さは chars_per_line で先に区切ってある。
    # 重要な語だけ色を付ける（数字は黄、減少は赤、キーワードは青/黄緑。同じ語は同じ色）
    hl = cfg.get("visuals.subtitle.highlight", {}) or {}
    hl_on = not isinstance(hl, dict) or hl.get("enabled", True)
    terms = highlights if highlights is not None else (highlight_terms(cfg, None, None) if hl_on else [])
    max_spans = int(hl.get("max_per_line", 2)) if isinstance(hl, dict) else 2
    number_color = ({**_DEFAULT_HIGHLIGHT_COLORS, **((hl.get("colors") if isinstance(hl, dict) else None) or {})}["yellow"]
                    if hl_on else None)

    def line_text(ln: str) -> str:
        txt = safe_text(cfg, ln)
        return colorize(txt, terms, colors["text"], max_spans, number_color) if hl_on else txt

    events = [
        "Dialogue: 0,{},{},{},,0,0,0,,{}".format(
            _ass_time(c.start), _ass_time(c.end), c.style,
            r"\N".join(line_text(ln) for ln in c.lines)
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
    # 大きなテロップ（EMPHASIS など）は既定で出さない。字幕と重なって読みにくく、
    # 重要な語は字幕の色分けで示すため
    telops = build_telops(cfg, script, track) if (script and cfg.get("visuals.subtitle.telops", False)) else []
    highlights = highlight_terms(cfg, script, track)
    return {
        "ass": write_ass(cfg, cues, outdir / "subtitles.ass", telops=telops,
                         reserve_right=reserve_right, highlights=highlights),
        # SRT は YouTube に渡す字幕なので、テロップは入れない
        "srt": write_srt(cues, outdir / "subtitles.srt"),
    }
