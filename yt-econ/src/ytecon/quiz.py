"""参加型テストの Shorts（心理学チャンネルの型）: 2 択 → カウントダウン → 結果 → 研究 → 比較 → 今日のひとつ → 本編へ.

見た目はデジタル庁デザインシステム準拠（白地・Noto Sans JP・青 #0017C1・グレーの罫線・装飾なし）。
字幕は出さず、1 人の声（四国めたん）だけ。声の文節ごとに要素が出て、ハイライト（黄マーカー・青枠）と矢印で見せる。

流れ:
  write_quiz(cfg, topic)        LLM が場面ごとの JSON（文節と、その文節で出す要素）を書く
  build(cfg, quiz, outdir)      文節ごとに音声を合成 → 場面を段階と遅延つきで描画 → ffmpeg で 9:16 の mp4
  quiz_metadata(cfg, quiz)      タイトル・概要欄・タグ
JSON の形は _SCHEMA と _EXAMPLE のとおり。人が書いた JSON からも作れる（ytecon quiz --json）。
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

from . import domain, llm
from .assets import palette
from .config import Config
from .metadata import Metadata, _voice_credit, subscribe_line

log = logging.getLogger(__name__)

FPS = 30
TRANS = 0.4                # 要素が出るときのアニメーション秒数
SEG_GAP = 0.15             # 文節の間
SCENE_TAIL = 0.6           # 場面の最後の余韻
COUNT_TICK = 0.85          # カウントダウン 1 拍


# ----------------------------------------------------------------------
# 台本（LLM）
# ----------------------------------------------------------------------
_SYSTEM = """あなたは YouTube Shorts の構成作家です。{field}のチャンネルで、視聴者が最初の 1 秒で指を止め、3 秒後に「選ぶ」参加型テストの型を書きます。
{subs_line}
だから **画面の文字は短く（箱の中は 12 字以内）、ナレーションは 1 文節 25 字以内** にしてください。
おすすめに広く出るかは「最初の 1 秒で止まったか」と「最後まで見られたか（もう一周されたか）」でほぼ決まります。短く、密に。

# 型（この順。場面は 5〜6 個、全体で {seconds} 秒 = ナレーション合計 {chars}）
1. question  : **最初の文節は、常識をひっくり返す結論の言い切り**（例:「先延ばしは、意志の弱さではありません。」）。
               heading はその結論を 12 字以内に縮めたもの（例:「先延ばしは性格じゃない」）。「3秒で選んでください」は使わない。
               続けて「あなたはどっち？」→ 状況を 1 行 → A と B の選択肢。どちらも「自分もそうだ」と思える日常の行動にする
2. countdown : 3・2・1（ナレーションなし。自動で入る）
3. result    : 選ばれがちな方（B）に向けて、よくある思い込み（strike）を消し、本当の理由（answer）を出す
4. flow / branch / versus : 研究の核を 1〜2 場面で見せる（同じ種類を続けない）。branch は「人 → 2 つの行き先。片方を避けていた」、flow は「A → B → C」の因果、versus は対比
5. steps     : 今日その場で試せる 1 つのこと。3 手順以内、各 10 字以内。最後に「それで終わり」。
               **最後の文節は、最初の結論にもどる一言で終える**（例:「だから、先延ばしは性格ではないんです。」。もう一周見たとき、最後→最初がひと続きに聞こえる）
「続きは本編で」「チャンネル登録」などの誘導は書かない（必要ならこちらで足す）

# 言葉づかい（いちばん大事）
- 小学 5 年生が聞いて分かる言葉だけで話す。話し方（です・ます、落ち着いた口調）はそのまま
- 専門用語は使ってよいが、1 回だけ、すぐ後ろに日常の言葉で言い換える（例:「作業記憶、つまり頭の中のメモ帳」）。2 回目からは言い換えのほうを使う
- 画面の箱・見出しには専門用語を出さない。日常の言葉だけ（例: 作業記憶 → 頭のメモ帳、反すう → 何度も思い出す、認知 → 考え方、回避 → 避ける）
- 漢語より和語（「想起する」→「思い出す」、「軽減」→「へらす」、「阻害」→「じゃまする」）

# ナレーションの決まり
- narration は [文節, 段階] の並び。段階 0 から順に増やし、飛ばさない。文節は 25 字以内、読点か句点で終える
- 研究は実在するものだけ。研究者名か大学名を 1 つ言い、数字は出典にあるものだけ。断定は「〜と分かってきています」まで
- 診断しない（「あなたは◯◯障害」等は禁止）。性格を責めない。煽らない（ヤバい・終わった・知らないと損 は禁止）
- 話し言葉。です・ます調。1 文節に数字は 1 つまで

# 画面の決まり（各 kind の段階の意味は固定）
- question : 段階 0 = heading、1 = lead、2 = 選択肢 A、3 = 選択肢 B。heading_hl / lead_hl はその中の強調したい語（部分文字列）
- result   : 段階 0 = 見出しと選んだ選択肢、1 = strike（思い込み。8 字以内）、2 = answer（本当の理由。8 字以内、青のマーカー）
- flow     : boxes は 2〜3 個（上から下へ矢印でつなぐ）。段階 i で boxes[i] が出る。state は normal / active（青枠）/ dim（灰色）。up=true で赤い上向き矢印（増える）
- branch   : 段階 0 = source（左の箱）、1 = targets[0]（右上）、2 = targets[1]（右下）。targets[1] は avoided=true にすると矢印に ✕ と「避けていた」が付く
- versus   : 段階 0 = left の箱、1 = left が灰色になり ✕ と left.caption、2 = right の箱（青枠）と ✓ と right.caption。caption は 8 字以内
- steps    : 段階 0 = 見出し、1..n = items[i-1]、n+1 = final
- heading は 12 字以内。heading_hl はその一部（2〜4 字）

# 出力
JSON だけを返す。形は次の例と同じ。"""

_EXAMPLE = {
    "title": "先延ばしは意志の弱さではない？",
    "hook": "先延ばしは、意志の弱さではない",
    "scenes": [
        {"kind": "question", "heading": "先延ばしは性格じゃない", "heading_hl": "性格じゃない", "lead": "締切が5日後の仕事", "lead_hl": "5日後",
         "options": ["今日、少しだけ手をつける", "気分が乗った日に、まとめてやる"],
         "narration": [["先延ばしは、意志の弱さではありません。", 0], ["あなたはどっち？締切が5日後の仕事。", 1], ["A、今日少しだけ手をつける。", 2], ["B、気分が乗った日にまとめてやる。", 3]]},
        {"kind": "countdown"},
        {"kind": "result", "pick": "B", "option": "気分が乗った日に、まとめてやる", "strike": "意志が弱い", "answer": "仕組みの問題",
         "narration": [["Bを選んだ人。", 0], ["意志が弱いわけでは", 1], ["ありません。", 2]]},
        {"kind": "flow", "heading": "先延ばしの正体", "heading_hl": "先延ばし",
         "boxes": [{"text": "時間の管理の問題", "state": "dim"}, {"text": "気分の管理の問題", "state": "active"}],
         "narration": [["先延ばしは、時間の管理ではなく、", 0], ["気分の管理の問題だと分かってきています。", 1]]},
        {"kind": "branch", "heading": "研究が見つけたこと", "heading_hl": "研究", "note": "カールトン大学 ピチル教授", "source": "先延ばす人",
         "targets": [{"text": "課題", "avoided": False}, {"text": "嫌な気分", "avoided": True}],
         "narration": [["カールトン大学のピチル教授の研究では、", 0], ["先延ばす人ほど、課題の", 1], ["嫌な気分を避けていました。", 2]]},
        {"kind": "steps", "heading": "今日のひとつ", "heading_hl": "今日のひとつ", "items": ["ファイルを開く", "名前だけ付ける", "閉じる"], "final": "それで終わり",
         "narration": [["今日試すなら、ひとつ。", 0], ["次の仕事のファイルを開いて、", 1], ["名前だけ付けて、", 2], ["閉じる。", 3], ["それで終わり。だから、先延ばしは性格ではないんです。", 4]]},
    ],
    "sources": [{"name": "Sirois & Pychyl (2013) Procrastination and the priority of short-term mood regulation", "url": ""}],
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "scenes": {"type": "array", "items": {"type": "object"}},
        "sources": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "url": {"type": "string"}}}},
    },
    "required": ["title", "hook", "scenes"],
}


def write_quiz(cfg: Config, topic: str, angle: str = "") -> dict[str, Any]:
    """LLM に参加型テストの JSON を書かせる."""
    user = (
        f"テーマ: {topic}\n" + (f"切り口: {angle}\n" if angle else "") +
        f"視聴者: {cfg.get('channel.audience', '')}\n\n"
        "この形（例）と同じ JSON で書いてください。例の内容は使わず、テーマに合わせて全部書き換えること:\n"
        + json.dumps(_EXAMPLE, ensure_ascii=False, indent=1)
    )
    subs = bool(cfg.get("shorts.subtitles", False))
    system = _SYSTEM.format(
        field=domain.field(cfg),
        seconds=str(cfg.get("shorts.quiz_seconds", "35〜45")), chars=str(cfg.get("shorts.quiz_chars", "170〜220 字")),
        subs_line=("1 人のナレーター（落ち着いた女性の声）が話し、声は下に字幕で出ます。画面の上には短い言葉の箱・矢印・ハイライトが出ます。" if subs
                   else "字幕は出ません。1 人のナレーター（落ち着いた女性の声）が話し、画面には短い言葉の箱・矢印・ハイライトだけが出ます。"))
    data = llm.complete_json(system, user, _SCHEMA,
                             model=str(cfg.get("shorts.model", cfg.get("script.model", llm.DEFAULT_MODEL))),
                             effort=str(cfg.get("shorts.effort", "medium")))
    return normalize(data)


def normalize(q: dict[str, Any], add_cta: bool = True) -> dict[str, Any]:
    """LLM の出力を整える: countdown と cta を保証し、文節を 25 字前後に、段階を 0 からの連番に."""
    scenes = [s for s in (q.get("scenes") or []) if isinstance(s, dict) and s.get("kind")]
    # どの question の直後にも countdown を置く（本編は 1 本に何問もある）
    fixed = []
    for i, s in enumerate(scenes):
        fixed.append(s)
        if s["kind"] == "question" and (i + 1 >= len(scenes) or scenes[i + 1].get("kind") != "countdown"):
            fixed.append({"kind": "countdown"})
    scenes = fixed
    if add_cta and "cta" not in [s["kind"] for s in scenes]:
        scenes.append({"kind": "cta"})
    for s in scenes:
        nar = []
        for item in s.get("narration") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                nar.append([str(item[0]).strip(), int(item[1])])
        # 段階を 0 からの連番に詰める
        steps = sorted({st for _, st in nar})
        remap = {st: i for i, st in enumerate(steps)}
        s["narration"] = [[t, remap[st]] for t, st in nar if t]
    q["scenes"] = scenes
    q["title"] = str(q.get("title") or "").strip()[:60]
    q["hook"] = str(q.get("hook") or "").strip()
    return q


# ----------------------------------------------------------------------
# 見た目
# ----------------------------------------------------------------------
@dataclass
class Theme:
    W: int = 1080
    H: int = 1920
    M: int = 72
    bg: str = "#FFFFFF"
    text: str = "#1A1A1C"
    sec: str = "#626264"
    muted: str = "#949497"
    line: str = "#D8D8DB"
    surface: str = "#F1F1F4"
    blue: str = "#0017C1"
    blue_light: str = "#E8F1FE"
    yellow: str = "#FFE97A"
    green: str = "#197A4B"
    red: str = "#EC0000"
    on_accent: str = "#FFFFFF"   # 青の面の上の文字（A/B のタブ・ボタン）
    bad_fill: str = "#FDE8E6"    # 「よくないもの」の升の面
    brand: str = ""
    safe_top: int = 300          # Shorts の UI に隠れない縦の範囲
    safe_bottom: int = 1560
    header_y: int = 152          # ブランド名の位置と、その下の罫線
    header_line: int = 208
    body_top: int = 230          # これより下が中身（縦中央に寄せる対象）
    brand_size: int = 28
    chip: str = ""               # 右上の小さなラベル（本編の章など）
    clear: bool = False          # 地を透明にする（後ろに動く背景を敷く。研究所の宇宙の解析室）


def theme(cfg: Config) -> Theme:
    pal = palette(cfg)
    w, h = cfg.get("shorts.resolution", [1080, 1920])
    return Theme(
        W=int(w), H=int(h),
        bg=pal.get("bg", "#FFFFFF"), text=pal.get("text", "#1A1A1C"), sec=pal.get("text_secondary", "#626264"),
        muted=pal.get("muted", "#949497"), line=pal.get("outline", "#D8D8DB"), surface=pal.get("surface", "#F1F1F4"),
        blue=pal.get("accent", "#0017C1"), blue_light=pal.get("surface_high", "#E8F1FE"), yellow=pal.get("accent2", "#FFE97A"),
        green=pal.get("positive", "#197A4B"), red=pal.get("negative", "#EC0000"),
        on_accent=pal.get("on_accent", "#FFFFFF"), bad_fill=pal.get("negative_surface", "#FDE8E6"),
        brand=str(cfg.get("channel.name", "") or ""),
    )


_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


class _SafeFont(ImageFont.FreeTypeFont):
    """字体に無い記号（→ ※ ● など）を、描ける近い字に置き換えてから描く（M PLUS 1 は矢印を持たない）."""

    def getmask2(self, text, *a, **k):
        from .assets import _safe_for_font
        return super().getmask2(_safe_for_font(self, text), *a, **k)

    def getlength(self, text, *a, **k):
        from .assets import _safe_for_font
        return super().getlength(_safe_for_font(self, text), *a, **k)

    def getbbox(self, text, *a, **k):
        from .assets import _safe_for_font
        return super().getbbox(_safe_for_font(self, text), *a, **k)


def font_en(cfg: Config, size: int, weight: int = 500) -> ImageFont.FreeTypeFont:
    """英字・数字だけの飾り文字（PSYCH DATA LAB など）。video.typeface_en（Orbitron）が無ければ本文と同じ."""
    face = _typeface(cfg, "video.typeface_en")
    if not face:
        return font(cfg, size, weight)
    key = (f"{face}@{weight}", size)
    if key not in _font_cache:
        f = ImageFont.truetype(face, size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        _font_cache[key] = f
    return _font_cache[key]


def _typeface(cfg: Config, key: str = "video.typeface") -> str:
    """config で字体を指定していれば、その可変フォントのパス（心理学の研究所は細い M PLUS 1）."""
    face = str(cfg.get(key, "") or "").strip()
    if face:
        p = Path(face)
        p = p if p.is_absolute() else cfg.root / p
        if p.exists():
            return str(p)
    return ""


def _font_file(cfg: Config, weight: int) -> tuple[str, bool]:
    """(ファイル, 可変フォントか)。Bold は Noto Sans JP Bold、細い字は Medium/Regular、無ければ可変、最後は Bold."""
    face = _typeface(cfg)
    if face:
        return face, True
    fonts = cfg.root / "assets" / "fonts"
    if weight >= 700:
        for n in ("NotoSansJP-Bold.ttf", "NotoSansJP-Black.ttf"):
            if (fonts / n).exists():
                return str(fonts / n), False
    else:
        names = ["NotoSansJP-Medium.ttf", "NotoSansJP-Regular.ttf"] if weight >= 500 else ["NotoSansJP-Regular.ttf", "NotoSansJP-Medium.ttf"]
        for n in names:
            if (fonts / n).exists():
                return str(fonts / n), False
        for n in ("NotoSansJP-VF.ttf", "NotoSansJP[wght].ttf"):
            if (fonts / n).exists():
                return str(fonts / n), True
        for n in ("NotoSansJP-Bold.ttf", "NotoSansJP-Black.ttf"):
            if (fonts / n).exists():
                return str(fonts / n), False
    from .assets import font_path
    return font_path(cfg, "bold"), False


def font(cfg: Config, size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    if _typeface(cfg):
        # 細い字体にするときは、全部の太さを同じだけ細くする（見出し 900 → 600、本文 700 → 400 など）
        weight = max(100, min(900, weight + int(cfg.get("video.weight_shift", 0) or 0)))
    path, variable = _font_file(cfg, weight)
    key = (f"{path}@{weight if variable else 0}", size)
    if key not in _font_cache:
        f = _SafeFont(path, size) if _typeface(cfg) else ImageFont.truetype(path, size)
        if variable:
            try:
                f.set_variation_by_axes([weight])
            except Exception:
                pass
        _font_cache[key] = f
    return _font_cache[key]


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class Scene:
    """要素 = (出る段階, 描画関数(d, 進行度), 下からスライドするか, 遅延秒)."""

    def __init__(self, cfg: Config, th: Theme) -> None:
        self.cfg, self.th = cfg, th
        self.items: list[tuple[int, Callable[[ImageDraw.ImageDraw, float], None], bool, float]] = []
        self.extra: Callable[[ImageDraw.ImageDraw, float, int], None] | None = None   # カウントダウンの円

    def add(self, step: int, fn, slide: bool = True, delay: float = 0.0) -> None:
        self.items.append((step, fn, slide, delay))

    def max_delay(self, step: int) -> float:
        return max([dl for st, _f, _s, dl in self.items if st == step] or [0.0])

    def last_step(self) -> int:
        return max([st for st, *_ in self.items] or [0])

    def render(self, step: int, elapsed: float) -> Image.Image:
        th = self.th
        img = Image.new("RGBA", (th.W, th.H), (0, 0, 0, 0) if th.clear else th.bg)
        d = ImageDraw.Draw(img)
        if th.brand:
            d.text((th.M, th.header_y), th.brand, font=font(self.cfg, th.brand_size, 700), fill=th.text)
            d.line([(th.M, th.header_line), (th.W - th.M, th.header_line)], fill=th.line, width=2)
        if th.chip:
            fch = font(self.cfg, th.brand_size, 700)
            tw = d.textlength(th.chip, font=fch)
            d.text((th.W - th.M - tw, th.header_y), th.chip, font=fch, fill=th.blue)
        for st, fn, slide, delay in self.items:
            if st > step:
                continue
            p = 1.0 if st < step else ease((elapsed - delay) / TRANS)
            if p <= 0.0:
                continue
            layer = Image.new("RGBA", (th.W, th.H), (0, 0, 0, 0))
            fn(ImageDraw.Draw(layer), p)
            if p < 1.0:
                if slide:
                    moved = Image.new("RGBA", (th.W, th.H), (0, 0, 0, 0))
                    moved.paste(layer, (0, int((1 - p) * 28)))
                    layer = moved
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * p)))
            img = Image.alpha_composite(img, layer)
        return img if th.clear else img.convert("RGB")


# --- 部品 ---------------------------------------------------------------
class Parts:
    def __init__(self, cfg: Config, th: Theme) -> None:
        self.cfg, self.th = cfg, th

    def f(self, size: int, weight: int = 700):
        return font(self.cfg, size, weight)

    @staticmethod
    def text_mm(d, cx, cy, text, f, fill):
        d.text((cx, cy), text, font=f, fill=fill, anchor="mm")

    _BREAK_AFTER = "をのはがにでとへもて、・」）"

    def fit(self, d, text: str, max_w: int, size: int, weight: int = 700, min_size: int = 30):
        """1 行で入るなら 1 行（縮めるのは 2 割まで）。入らなければ自然な位置で 2 行に割り、両方が入る大きさにする."""
        s1 = size
        while s1 >= int(size * 0.8):
            f = self.f(s1, weight)
            if d.textlength(text, font=f) <= max_w:
                return f, [text]
            s1 -= 2
        # 割る位置: 助詞・読点・閉じ括弧の後ろ（無ければどこでも）。2 行の長さがいちばん揃うところ
        # 次の行が助詞や句読点で始まる位置では割らない（「出ないの／は」→「出ないのは／緊張の…」）
        # 「」の中はなるべく割らない（「も／し〜なら」）。番号（「3. 」）だけの行も作らない
        depth, inside = 0, set()
        for i, ch in enumerate(text):
            if ch == "「":
                depth += 1
            elif ch == "」":
                depth = max(0, depth - 1)
            if depth > 0:
                inside.add(i + 1)
        nat = [i + 1 for i, ch in enumerate(text[:-1])
               if ch in self._BREAK_AFTER and text[i + 1] not in "はがをにでともへのや、。」）"]
        nat += [i for i, ch in enumerate(text) if ch == "「" and i > 0 and not re.fullmatch(r"\s*\d+\.\s*", text[:i])]
        # 括弧の外で割れるならそちらを優先。全体が 1 つの「」なら中で割ってよい（「確認させて／ください」）
        cands = [i for i in nat if i not in inside] or nat
        if not cands:
            # 自然に割れる場所が無い語（「どう思われる？」など）は、途中で割らずに 1 行のまま縮める
            s3 = int(size * 0.8)
            while s3 >= min_size:
                f = self.f(s3, weight)
                if d.textlength(text, font=f) <= max_w:
                    return f, [text]
                s3 -= 2
            cands = list(range(1, len(text)))
        f0 = self.f(size, weight)
        best = min(cands, key=lambda i: max(d.textlength(text[:i], font=f0), d.textlength(text[i:], font=f0)))
        lines = [text[:best], text[best:]]
        s2 = size
        while s2 > min_size:
            f = self.f(s2, weight)
            if all(d.textlength(ln, font=f) <= max_w for ln in lines):
                return f, lines
            s2 -= 2
        return self.f(min_size, weight), lines

    def box(self, d, xy, text: str, state: str = "normal", size: int = 64) -> None:
        th = self.th
        x0, y0, x1, y1 = xy
        if state == "active":
            fill, outline, color, width = th.blue_light, th.blue, th.blue, 5
        elif state == "dim":
            fill, outline, color, width = th.surface, th.line, th.muted, 2
        else:
            fill, outline, color, width = th.bg, th.muted, th.text, 3
        d.rounded_rectangle(xy, radius=10, fill=fill, outline=outline, width=width)
        f, lines = self.fit(d, text, (x1 - x0) - 56, size)
        lh = int(f.size * 1.25)
        cy = (y0 + y1) / 2 - lh * (len(lines) - 1) / 2
        for ln in lines:
            self.text_mm(d, (x0 + x1) / 2, cy, ln, f, color)
            cy += lh

    def marker_text(self, d, x: int, y: int, text: str, f, color=None, p: float = 1.0) -> int:
        th = self.th
        tw = d.textlength(text, font=f)
        if p > 0:
            d.rectangle([x - 6, y + int(f.size * 0.5), x - 6 + (tw + 12) * p, y + int(f.size * 1.18)], fill=th.yellow)
        d.text((x, y), text, font=f, fill=color or th.text)
        return int(x + tw)

    def marker_line(self, d, x: int, y: int, parts: list[tuple[str, bool]], f, p: float = 1.0, color=None, hl_color=None) -> None:
        th = self.th
        for txt, hl in parts:
            if hl:
                x = self.marker_text(d, x, y, txt, f, color=hl_color or color or th.text, p=p) + 10
            else:
                d.text((x, y), txt, font=f, fill=color or th.text)
                x += int(d.textlength(txt, font=f)) + 10

    def arrow(self, d, a, b, p: float = 1.0, color=None, width: int = 18, head: int = 64) -> None:
        color = color or self.th.blue
        ax, ay = a
        bx, by = b
        bx, by = ax + (bx - ax) * max(p, 0.05), ay + (by - ay) * max(p, 0.05)
        ang = math.atan2(by - ay, bx - ax)
        ux, uy = math.cos(ang), math.sin(ang)
        ex, ey = bx - ux * head * 0.9, by - uy * head * 0.9
        d.line([(ax, ay), (ex, ey)], fill=color, width=width)
        px, py = -uy, ux
        d.polygon([(bx, by), (bx - ux * head + px * head * 0.6, by - uy * head + py * head * 0.6),
                   (bx - ux * head - px * head * 0.6, by - uy * head - py * head * 0.6)], fill=color)

    def strike(self, d, x0, x1, y, p: float = 1.0):
        d.line([(x0, y), (x0 + (x1 - x0) * p, y)], fill=self.th.red, width=10)

    def check(self, d, cx, cy, s: int = 72):
        d.line([(cx - s // 2, cy), (cx - s // 8, cy + s * 0.38), (cx + s // 2, cy - s * 0.42)], fill=self.th.green, width=16, joint="curve")

    def cross(self, d, cx, cy, s: int = 64, width: int = 16):
        d.line([(cx - s // 2, cy - s // 2), (cx + s // 2, cy + s // 2)], fill=self.th.red, width=width)
        d.line([(cx + s // 2, cy - s // 2), (cx - s // 2, cy + s // 2)], fill=self.th.red, width=width)

    def split_hl(self, text: str, hl: str) -> list[tuple[str, bool]]:
        """見出しの中の強調語をマーカーにする."""
        text = text or ""
        hl = (hl or "").strip()
        if hl and hl in text:
            a, b = text.split(hl, 1)
            return [(x, False) for x in [a] if x] + [(hl, True)] + [(x, False) for x in [b] if x]
        return [(text, False)]

    def title(self, d, parts, y: int = 300, size: int = 76, p: float = 1.0) -> int:
        th = self.th
        self.marker_line(d, th.M, y, parts, self.f(size), p=p)
        y += int(size * 1.35)
        d.rectangle([th.M, y, th.M + 72, y + 8], fill=th.blue)
        return y + 56

    def option(self, d, y: int, key: str, text: str, state: str = "normal", h: int = 220,
               x0: int | None = None, x1: int | None = None, size: int | None = None) -> int:
        th = self.th
        tab_h = 52
        sel, dim = state == "selected", state == "dim"
        x0 = th.M if x0 is None else x0
        x1 = th.W - th.M if x1 is None else x1
        d.rounded_rectangle([x0, y, x0 + 96, y + tab_h + 12], radius=8, fill=th.blue if not dim else th.muted)
        self.text_mm(d, x0 + 48, y + tab_h / 2 + 2, key, self.f(34), th.on_accent)
        by0 = y + tab_h
        d.rounded_rectangle([x0, by0, x1, by0 + h], radius=10,
                            fill=th.blue_light if sel else (th.surface if dim else th.bg),
                            outline=th.blue if sel else th.line, width=5 if sel else 3)
        f, lines = self.fit(d, text, (x1 - x0) - 72, size or (60 if sel else 56), 700 if sel else 500)
        lh = int(f.size * 1.25)
        cy = by0 + h / 2 - lh * (len(lines) - 1) / 2
        for ln in lines:
            self.text_mm(d, (x0 + x1) / 2, cy, ln, f, th.muted if dim else (th.blue if sel else th.text))
            cy += lh
        return by0 + h

    def caption_under(self, d, cx: int, y: int, text: str, f, color, p: float = 1.0, hl: bool = False) -> None:
        tw = d.textlength(text, font=f)
        th = self.th
        x = int(min(max(cx - tw / 2, th.M), th.W - th.M - tw))     # 左右の余白からはみ出さない
        if hl:
            self.marker_text(d, x, y, text, f, color=color, p=p)
        else:
            d.text((x, y), text, font=f, fill=color)


# --- 場面 ---------------------------------------------------------------
def build_scene(cfg: Config, th: Theme, sc: dict[str, Any]) -> Scene:
    P = Parts(cfg, th)
    s = Scene(cfg, th)
    kind = sc.get("kind")
    M, W = th.M, th.W
    TY = 300 + int(76 * 1.35) + 56          # 見出しの下

    if kind in ("question", "countdown"):
        heading = sc.get("heading") or "3秒で選んでください"
        parts = P.split_hl(heading, sc.get("heading_hl") or "3秒")
        s.add(0, lambda d, p: P.title(d, parts, p=p))
        lead = sc.get("lead") or ""
        lparts = P.split_hl(lead, sc.get("lead_hl") or "")
        s.add(1, lambda d, p: P.marker_line(d, M, TY, lparts, P.f(60), p=p))
        oy = TY + 130
        opts = (sc.get("options") or ["", ""])[:2]
        s.add(2, lambda d, p: P.option(d, oy, "A", opts[0]))
        s.add(3, lambda d, p: P.option(d, oy + 230 + 52 + 40, "B", opts[1] if len(opts) > 1 else ""))
        cy = oy + (230 + 52 + 40) * 2 + 40 + 190

        def count(d, p, n):
            r = 150 + int(30 * (1 - p))
            d.ellipse([W / 2 - r, cy - r, W / 2 + r, cy + r], fill=th.blue_light, outline=th.blue, width=6)
            tmp = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((250, 250), str(n), font=P.f(230), fill=th.blue, anchor="mm")
            bb = tmp.getbbox()
            glyph = tmp.crop(bb)
            d._image.paste(glyph, (int(W / 2 - glyph.width / 2), int(cy - glyph.height / 2)), glyph)
        s.extra = count
    elif kind == "result":
        pick = str(sc.get("pick") or "B").strip()[:1] or "B"
        heading = sc.get("heading") or f"{pick}を選んだ人へ"
        parts = P.split_hl(heading, sc.get("heading_hl") or pick)
        s.add(0, lambda d, p: P.title(d, parts, p=p))
        s.add(0, lambda d, p: P.option(d, TY, pick, sc.get("option") or "", state="selected"))
        y2 = TY + 52 + 230 + 90
        strike_t = sc.get("strike") or ""
        answer = sc.get("answer") or ""
        f = P.f(84)

        def weak(d, p):
            d.text((M, y2), strike_t, font=f, fill=th.muted)
            tw = int(d.textlength(strike_t, font=f))
            P.strike(d, M - 10, M + tw + 10, y2 + 58, p=p)
        s.add(1, weak, slide=False)
        s.add(2, lambda d, p: P.arrow(d, (M + 70, y2 + 140), (M + 70, y2 + 250), p=p), slide=False)
        s.add(2, lambda d, p: P.marker_text(d, M, y2 + 270, answer, P.f(96), color=th.blue, p=p), slide=False, delay=0.35)
    elif kind == "flow":
        parts = P.split_hl(sc.get("heading") or "", sc.get("heading_hl") or "")
        s.add(0, lambda d, p: P.title(d, parts, p=p))
        boxes = (sc.get("boxes") or [])[:3]
        bh = 200 if len(boxes) >= 3 else 250
        gap = 170 if len(boxes) >= 3 else 190
        y = TY
        for i, b in enumerate(boxes):
            text = str(b.get("text") or "")
            state = str(b.get("state") or "normal")
            yy = y + i * (bh + gap)
            if i > 0:
                s.add(i, (lambda yy_: (lambda d, p: P.arrow(d, (W // 2, yy_ - gap + 24), (W // 2, yy_ - 30), p=p)))(yy), slide=False)
            # 前の箱が normal で、この箱が出たら灰色にする指定（dim_after）
            s.add(i, (lambda yy_, t_, st_: (lambda d, p: P.box(d, [M, yy_, W - M, yy_ + bh], t_, st_, size=64)))(yy, text, state), delay=0.3 if i > 0 else 0.0)
            if b.get("up"):
                s.add(i, (lambda yy_: (lambda d, p: P.arrow(d, (W - M - 70, yy_ + bh - 30), (W - M - 70, yy_ + 30), p=p, color=th.red)))(yy), slide=False, delay=0.6)
    elif kind == "branch":
        parts = P.split_hl(sc.get("heading") or "", sc.get("heading_hl") or "")
        s.add(0, lambda d, p: P.title(d, parts, p=p))
        note = sc.get("note") or ""
        if note:
            s.add(0, lambda d, p: d.text((M, TY), note, font=P.f(40), fill=th.blue))
        gy = TY + 110
        bw = (W - M * 2 - 170) // 2
        lbox = [M, gy + 170, M + bw, gy + 410]
        tbox = [W - M - bw, gy, W - M, gy + 220]
        bbox_ = [W - M - bw, gy + 360, W - M, gy + 580]
        lx, ly = M + bw, gy + 290
        targets = (sc.get("targets") or [])[:2]
        t0 = str(targets[0].get("text") if targets else "")
        t1 = str(targets[1].get("text") if len(targets) > 1 else "")
        avoided = bool(targets[1].get("avoided")) if len(targets) > 1 else False
        s.add(0, lambda d, p: P.box(d, lbox, str(sc.get("source") or ""), size=58))
        s.add(1, lambda d, p: P.arrow(d, (lx + 12, ly - 30), (tbox[0] - 14, gy + 110), p=p), slide=False)
        s.add(1, lambda d, p: P.box(d, tbox, t0, size=64), delay=0.3)
        s.add(2, lambda d, p: P.box(d, tbox, t0, "dim", size=64), slide=False)
        s.add(2, lambda d, p: P.arrow(d, (lx + 12, ly - 30), (tbox[0] - 14, gy + 110), color=th.line), slide=False)
        s.add(2, lambda d, p: P.arrow(d, (lx + 12, ly + 30), (bbox_[0] - 14, gy + 470), p=p), slide=False)
        s.add(2, lambda d, p: P.box(d, bbox_, t1, "active", size=64), delay=0.3)
        if avoided:
            mx, my = (lx + 12 + bbox_[0] - 14) / 2, (ly + 30 + gy + 470) / 2
            s.add(2, lambda d, p: P.cross(d, int(mx) - 13, int(my) - 13, s=76, width=18), slide=False, delay=0.6)
            s.add(2, lambda d, p: P.caption_under(d, int(mx) - 40, int(my) + 60, str(sc.get("avoid_label") or "避けていた"), P.f(44), th.red, p=p, hl=True), slide=False, delay=0.8)
    elif kind == "versus":
        parts = P.split_hl(sc.get("heading") or "", sc.get("heading_hl") or "")
        s.add(0, lambda d, p: P.title(d, parts, p=p))
        gap = 40
        bw = (W - M * 2 - gap) // 2
        bh = 300
        lcx, rcx = M + bw // 2, W - M - bw // 2
        left = sc.get("left") or {}
        right = sc.get("right") or {}
        lt, rt = str(left.get("text") or ""), str(right.get("text") or "")
        s.add(0, lambda d, p: P.box(d, [M, TY, M + bw, TY + bh], lt, size=60))
        s.add(1, lambda d, p: P.box(d, [M, TY, M + bw, TY + bh], lt, "dim", size=60), slide=False)
        s.add(1, lambda d, p: P.cross(d, lcx, TY + bh + 80), slide=False, delay=0.2)
        if left.get("caption"):
            s.add(1, lambda d, p: P.caption_under(d, lcx, TY + bh + 140, str(left["caption"]), P.f(42, 500), th.muted), delay=0.4)
        s.add(2, lambda d, p: P.box(d, [W - M - bw, TY, W - M, TY + bh], rt, "active", size=60))
        s.add(2, lambda d, p: P.check(d, rcx, TY + bh + 80), slide=False, delay=0.4)
        if right.get("caption"):
            s.add(2, lambda d, p: P.caption_under(d, rcx, TY + bh + 140, str(right["caption"]), P.f(48), th.blue, p=p, hl=True), slide=False, delay=0.7)
    elif kind == "steps":
        heading = sc.get("heading") or "今日のひとつ"
        parts = P.split_hl(heading, sc.get("heading_hl") or heading)
        s.add(0, lambda d, p: P.title(d, parts, p=p))
        items = [str(x) for x in (sc.get("items") or [])][:3]
        bh = 180
        for i, t in enumerate(items):
            yy = TY + i * (bh + 100)
            st = i + 1
            s.add(st, (lambda yy_, t_, i_: (lambda d, p: P.box(d, [M, yy_, W - M, yy_ + bh], f"{i_ + 1}. {t_}", "active", size=60)))(yy, t, i))
            if i + 1 < len(items):
                s.add(st + 1, (lambda yy_: (lambda d, p: P.arrow(d, (W // 2, yy_ + bh + 16), (W // 2, yy_ + bh + 90), p=p)))(yy), slide=False)
                s.add(st + 1, (lambda yy_, t_, i_: (lambda d, p: P.box(d, [M, yy_, W - M, yy_ + bh], f"{i_ + 1}. {t_}", "normal", size=60)))(yy, t, i), slide=False)
        fy = TY + len(items) * (bh + 100) + 10
        final = sc.get("final") or "それで終わり"
        s.add(len(items) + 1, lambda d, p: P.check(d, M + 30, fy + 40, s=64), slide=False)
        s.add(len(items) + 1, lambda d, p: P.marker_text(d, M + 100, fy, final, P.f(76), color=th.green, p=p), slide=False)
    elif kind == "cta":
        times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
        when = times[0] if times else "20:00"
        cy0 = 300

        def head(d, p):
            f = P.f(104)
            tw = d.textlength("続きは本編で", font=f)
            P.marker_text(d, int((W - tw) / 2), cy0, "続きは本編で", f, p=p)
        s.add(0, head, slide=False)

        def when_(d, p):
            f = P.f(72)
            parts_ = [("毎日", False), (when, True), ("更新", False)]
            total = sum(d.textlength(t, font=f) for t, _ in parts_) + 20
            P.marker_line(d, int((W - total) / 2), cy0 + 190, parts_, f, p=p, hl_color=th.blue)
        s.add(1, when_, slide=False)

        def btn(d, p):
            f = P.f(64)
            tw = d.textlength("本編を見る", font=f)
            x0 = (W - tw) / 2 - 80
            d.rounded_rectangle([x0, cy0 + 360, x0 + tw + 160, cy0 + 520], radius=12, fill=th.blue)
            P.text_mm(d, W / 2, cy0 + 440, "本編を見る", f, th.on_accent)
        s.add(0, btn, delay=0.3)
        s.add(1, lambda d, p: P.arrow(d, (W // 2, cy0 + 560), (W // 2, cy0 + 700), p=p), slide=False, delay=0.4)
        s.add(1, lambda d, p: P.caption_under(d, W // 2, cy0 + 730, "下のリンクから", P.f(52), th.text), delay=0.7)
    else:
        s.add(0, lambda d, p: P.title(d, [(str(sc.get("heading") or kind), False)], p=p))
    return s


def cta_narration(cfg: Config) -> list[list]:
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    when = times[0] if times else "20:00"
    hh = when.split(":")[0].lstrip("0") or "0"
    return [["続きは本編で。", 0], [f"毎日{hh}時に更新しています。", 1]]


# ----------------------------------------------------------------------
# 音声・書き出し
# ----------------------------------------------------------------------
def _wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _content_offset(scene: Scene, th: Theme, with_extra: bool = False) -> int:
    """最終段階の内容を安全域の縦中央に置くためのずらし量（場面の途中で動かさない）."""
    from PIL import ImageChops
    img = scene.render(scene.last_step(), 99.0)
    if with_extra and scene.extra:
        scene.extra(ImageDraw.Draw(img), 1.0, 3)
    bt = th.body_top
    body = img.crop((0, bt, th.W, th.H))
    if body.mode == "RGBA":
        bbox = body.getchannel("A").getbbox()
    else:
        bbox = ImageChops.difference(body, Image.new("RGB", body.size, th.bg)).getbbox()
    if not bbox:
        return 0
    top, bottom = bbox[1] + bt, bbox[3] + bt
    target_top = max(th.safe_top, (th.safe_top + th.safe_bottom) // 2 - (bottom - top) // 2)
    return target_top - top


def _shift(img: Image.Image, dy: int, th: Theme) -> Image.Image:
    if dy == 0:
        return img
    bt = th.body_top
    body = img.crop((0, bt, th.W, th.H))
    out = img.copy()
    if img.mode == "RGBA":
        out.paste(Image.new("RGBA", body.size, (0, 0, 0, 0)), (0, bt))
        out.alpha_composite(body, (0, bt + dy)) if bt + dy >= 0 else out.paste(body, (0, bt + dy), body)
        return out
    out.paste(Image.new("RGB", body.size, th.bg), (0, bt))
    out.paste(body, (0, bt + dy))
    return out


_SCAP: dict[str, Image.Image] = {}


def _short_caption(cfg: Config, th: Theme, img: Image.Image, text: str) -> Image.Image:
    """Shorts の字幕: 経済の Shorts と同じ大きさ（shorts.subtitle_size）で、画面の下（UI に隠れない所）に 2 行まで."""
    if text not in _SCAP:
        from .subtitles import phrase_split
        size = int(cfg.get("shorts.subtitle_size", 76))
        f = font(cfg, size, 800)                       # 研究所は細い字体（weight_shift で 500）
        per = max(6, int((th.W - 120) / size))
        lines = phrase_split(text, per)[:2] or [text]
        if len(lines) > 1 and min(len(x) for x in lines) <= 3:
            # 「あるけ / れど」のような泣き別れを避ける: 少しだけはみ出すなら 1 行のまま字を小さく、そうでなければ切り直す
            if len(text) <= per + 4:
                lines = [text]
            else:
                lines = phrase_split(text, per - 3)[:2] or [text]
        d0 = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        while max(d0.textlength(x, font=f) for x in lines) > th.W - 100 and f.size > 56:
            f = font(cfg, f.size - 4, 800)                 # はみ出すときだけ少し小さく
        lh = int(size * 1.28)
        lay = Image.new("RGBA", (th.W, lh * len(lines) + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        wmax = max(d.textlength(ln, font=f) for ln in lines)
        d.rounded_rectangle([(th.W - wmax) / 2 - 34, 0, (th.W + wmax) / 2 + 34, lay.height - 1], radius=18, fill=(4, 9, 20, 205))
        for i, ln in enumerate(lines):
            d.text((th.W / 2, 20 + lh * i + lh / 2), ln, font=f, fill=th.text, anchor="mm",
                   stroke_width=3, stroke_fill=(4, 9, 20))
        if len(_SCAP) > 300:
            _SCAP.clear()
        _SCAP[text] = lay
    lay = _SCAP[text]
    out = img.convert("RGBA")
    out.alpha_composite(lay, (0, int(cfg.get("shorts.subtitle_bottom", 1550)) - lay.height))
    return out.convert("RGB")


@dataclass
class Built:
    video: Path
    seconds: float
    frames: int
    quiz: dict[str, Any] = field(default_factory=dict)
    cues: list[tuple[float, float, str]] = field(default_factory=list)       # 字幕ファイル（CC）用: (開始秒, 終了秒, 文)
    chapters: list[tuple[float, str]] = field(default_factory=list)          # 概要欄のチャプター用: (開始秒, 見出し)


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _split_cue(start: float, dur: float, text: str) -> list[tuple[float, float, str]]:
    """1 段階ぶんの文を句点で分け、字数に比例して時間を割り振る（字幕が 1 行に長く出続けないように）."""
    parts = [p for p in re.split(r"(?<=[。？！?!])", text) if p.strip()]
    if len(parts) <= 1:
        return [(start, start + dur, text)]
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        d = dur * len(p) / total
        out.append((t, t + d, p.strip()))
        t += d
    return out


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> Path:
    lines = []
    for i, (a, b, text) in enumerate(cues, 1):
        lines += [str(i), f"{_srt_time(a)} --> {_srt_time(b)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build(cfg: Config, quiz: dict[str, Any], outdir: str | Path, provider=None, wide: bool = False) -> Built:
    """JSON → mp4。文節ごとに音声を合成し、場面を段階・遅延つきで描いて繋ぐ.

    wide=True なら本編（16:9）。場面の組み方は wide.build_scene_wide。
    """
    from . import bgm as bgm_mod
    from . import tts

    quiz = normalize(dict(quiz), add_cta=not wide and bool(cfg.get("shorts.end_cta", True)))
    seg_gap, scene_tail, tick = SEG_GAP, SCENE_TAIL, COUNT_TICK
    voice_count, outro = False, 0.0
    if wide:
        from . import wide as wide_mod
        th = wide_mod.theme_wide(cfg)
        builder = wide_mod.build_scene_wide
        # 本編は寝ながら聴く人向け: ゆっくり・間を長く・カウントダウンも声に出す・最後は静かな余韻
        seg_gap = float(cfg.get("honpen.seg_gap", 0.35))
        scene_tail = float(cfg.get("honpen.scene_tail", 1.1))
        tick = float(cfg.get("honpen.count_tick", 1.3))
        voice_count = bool(cfg.get("honpen.voice_countdown", True))
        outro = float(cfg.get("honpen.outro_seconds", 45))
        if cfg.get("honpen.speed") is not None and provider is None:
            import copy as _copy
            cfg2 = _copy.deepcopy(cfg)
            cfg2.raw.setdefault("tts", {}).setdefault("voicevox", {})["speed"] = float(cfg.get("honpen.speed"))
            from . import tts as _tts
            provider = _tts.make_provider(cfg2)
    else:
        th = theme(cfg)
        builder = build_scene
    outdir = Path(outdir)
    fr = outdir / "frames"
    fr.mkdir(parents=True, exist_ok=True)
    for old in fr.glob("*"):
        old.unlink()
    provider = provider or tts.make_provider(cfg)
    ffmpeg = _ffmpeg()

    frames: list[str] = []
    wavs: list[tuple[Path, float]] = []      # (wav, 後ろに足す無音)
    total = 0.0
    n = 0
    # 研究所の解析画面（design: lab）: 本編は左のモニターに縮めて右にめたん、Shorts は地に格子と光
    from . import lab as lab_mod
    frame = None
    back = None
    cur_kind = ""
    cur_icon = ""
    cur_chapter = 0
    space = wide and lab_mod.space_enabled(cfg)          # 宇宙の解析室: 透明な重ね絵を作り、最後に動く背景の上に重ねる
    shorts_subs = (not wide) and bool(cfg.get("shorts.subtitles", False))     # Shorts の字幕（経済の Shorts と同じ大きさ）
    if shorts_subs:
        th.safe_bottom = int(cfg.get("shorts.content_bottom", 1330))          # 中身は字幕の帯より上に
    if lab_mod.enabled(cfg):
        pal = palette(cfg)
        if space:
            th.brand = ""
            ol = quiz.get("outline") or {}
            question = str(quiz.get("question") or ol.get("question") or ol.get("thumb_claim") or quiz.get("title") or "")
            frame = lab_mod.SpaceFrame(cfg, pal, question, sum(1 for x in quiz["scenes"] if x.get("kind") == "chapter") or 4)
        elif wide:
            th.brand = ""
            frame = lab_mod.Frame(cfg, pal, str(quiz.get("title") or ""))
        else:
            back = lab_mod.backdrop((th.W, th.H), pal)

    def emit(img: Image.Image, dur: float, post: bool = True, mouth: str = "base", caption: str = "") -> None:
        nonlocal n
        if not post:
            pass
        elif space:
            img = frame.compose(img, cur_kind, mouth, caption, cur_icon, cur_chapter)
        elif frame is not None:
            img = frame.compose(img, cur_kind, mouth)
        elif back is not None:
            img = lab_mod.finish(img, th.bg, back)
        if post and shorts_subs and caption:
            img = _short_caption(cfg, th, img, caption)
        p = fr / f"f{n:04d}.png"
        img.save(p, compress_level=1)
        frames.extend([f"file '{p.name}'", f"duration {dur:.4f}"])
        n += 1

    def mouth_at(talk, t: float) -> str:
        for s0, d0, st in talk or []:
            if s0 <= t < s0 + d0:
                return st
        return "base"

    def cap_at(caps, t: float) -> str:
        for s0, d0, txt in caps or []:
            if s0 <= t < s0 + d0 + 0.25:          # 文の切れ目の間も少し残す（ちらつかせない）
                return txt
        return ""

    def animate(scene: Scene, step: int, seconds: float, dy: int, extra=None, static: bool = False, talk=None,
                caps=None) -> None:
        """talk = 口の形の並び（lab.mouth_track）。解析画面のときだけ、声に合わせてめたんの口を動かす.

        caps = 字幕の並び [(始まり秒, 長さ, 文)]（宇宙の解析室のときだけ。めたんの声を細い字で下に出す）.
        """
        talk = talk if frame is not None else None
        caps = caps if (space or shorts_subs) else None
        trans = min(seconds * 0.8, (0 if static else scene.max_delay(step)) + TRANS)
        k = max(1, int(trans * FPS))
        for i in range(k):
            el = (i + 1) / k * trans
            img = scene.render(step, 99.0 if static else el)
            if extra:
                extra(img, min(1.0, el / TRANS))
            emit(_shift(img, dy, th), trans / k, mouth=mouth_at(talk, el), caption=cap_at(caps, el))
        rest = seconds - trans
        if rest > 0.01:
            img = scene.render(step, 99.0)
            if extra:
                extra(img, 1.0)
            img = _shift(img, dy, th)
            if not talk and not caps:
                emit(img, rest)
                return
            # 止まっている絵の間も、口の形・字幕が変わるところでコマを分ける（場面の絵は 1 回だけ作る）
            marks = [c for s0, d0, _ in (talk or []) for c in (s0, s0 + d0)] + [c for s0, d0, _ in (caps or []) for c in (s0, s0 + d0 + 0.25)]
            cuts = sorted({trans, seconds} | {c for c in marks if trans < c < seconds})
            pending, cur = 0.0, None
            for a, b in zip(cuts, cuts[1:]):
                mid = (a + b) / 2
                st = (mouth_at(talk, mid), cap_at(caps, mid))
                if cur is not None and st != cur:
                    emit(img, pending, mouth=cur[0], caption=cur[1])
                    pending = 0.0
                cur = st
                pending += b - a
            if pending > 0.001:
                cur = cur or ("base", "")
                emit(img, pending, mouth=cur[0], caption=cur[1])

    def silence(sec: float) -> None:
        p = fr / f"a{len(wavs):03d}.wav"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{sec:.3f}", str(p)], check=True)
        wavs.append((p, 0.0))

    cues: list[tuple[float, float, str]] = []
    chapters: list[tuple[float, str]] = []
    scenes = quiz["scenes"]
    q_scene = None                      # カウントダウンは直前の question の画面で数える
    shared_dy = None
    tess_ranges: list[tuple[float, float]] = []     # 4D のテッセラクトの背景にする時間（冒頭・章の扉）
    for sc in scenes:
        kind = sc.get("kind")
        cur_kind = str(kind or "")
        sc_start = total
        if kind == "chapter":
            cur_chapter += 1
            cur_icon = str(sc.get("icon") or "")
        elif kind not in ("countdown",) and sc.get("icon"):
            cur_icon = str(sc.get("icon") or "")
        elif kind in ("opening", "ending", "steps"):
            cur_icon = ""
        if kind == "question":
            q_scene = sc
        if kind == "countdown":
            base = builder(cfg, th, dict(q_scene or {}, kind="countdown"))
            if shared_dy is None:
                shared_dy = _content_offset(base, th, with_extra=True)
            for num in (3, 2, 1):
                def extra(img, t, num=num, base=base):
                    base.extra(ImageDraw.Draw(img), t, num)
                dur = tick
                if voice_count:             # 画面を見ていない人にも数が聞こえるように
                    data = provider.synth({3: "さん。", 2: "に。", 1: "いち。"}[num])
                    p = fr / f"a{len(wavs):03d}.wav"
                    p.write_bytes(data)
                    v = _wav_seconds(data)
                    dur = max(tick, v + 0.3)
                    wavs.append((p, dur - v))
                else:
                    silence(dur)
                animate(base, 3, dur, shared_dy, extra=extra, static=True)
                total += dur
            continue
        scene = builder(cfg, th, sc)
        nar = sc.get("narration") or (cta_narration(cfg) if kind == "cta" else [])
        if kind == "opening":
            chapters.append((total, "はじめに"))
        elif kind == "chapter":
            chapters.append((total, f"{sc.get('label', '')} {sc.get('heading', '')}".strip()))
        elif kind == "steps" and "まとめ" in str(sc.get("heading") or ""):
            chapters.append((total, "今日のまとめ"))
        if kind == "question":
            cd = builder(cfg, th, dict(sc, kind="countdown"))
            shared_dy = _content_offset(cd, th, with_extra=True)
            dy = shared_dy
        elif space and kind in lab_mod.FULL_KINDS:
            dy = 0                                   # 空間にじかに置く場面は、決めた位置のまま（床・字幕とそろえる）
        else:
            dy = _content_offset(scene, th)
        steps: dict[int, list[str]] = {}
        for t, st in nar:
            steps.setdefault(int(st), []).append(str(t))
        ordered = sorted(steps) or [0]
        for i, st in enumerate(ordered):
            text = "".join(steps.get(st, []))
            if text.strip():
                data = provider.synth(text)
                p = fr / f"a{len(wavs):03d}.wav"
                p.write_bytes(data)
                voice = _wav_seconds(data)
                cues.extend(_split_cue(total, voice, text))
            else:
                p = None
                voice = 1.2
            tail = scene_tail if i == len(ordered) - 1 else seg_gap
            if p is not None:
                wavs.append((p, tail))
            else:
                silence(voice + tail)
            talk = lab_mod.mouth_track(data, cfg, seed=len(wavs)) if (frame is not None and p is not None) else None
            caps = (lab_mod.caption_chunks(0.0, voice, text, maxc=26 if space else 24)
                    if ((space or shorts_subs) and p is not None) else None)
            animate(scene, st, voice + tail, dy, talk=talk, caps=caps)
            total += voice + tail
        # 台本にない段階（要素だけの段階）が残っていれば最後にまとめて出す
        last = scene.last_step()
        if last > max(ordered):
            for st in range(max(ordered) + 1, last + 1):
                silence(1.0)
                animate(scene, st, 1.0, dy)
                total += 1.0
        if kind in ("opening", "chapter"):
            tess_ranges.append((sc_start, total))

    if outro > 0 and frames:
        # 静かな余韻: 最後の画面をゆっくり暗くして、音楽だけを流す（寝落ちした人の耳に急な無音や明るさを残さない）
        last = Image.open(fr / frames[-2].split("'")[1]).convert("RGBA" if space else "RGB")
        dark = Image.new(last.mode, last.size, "#101014")
        fade = min(8.0, outro)
        k = int(fade * 6)
        for i in range(k):
            emit(Image.blend(last, dark, (i + 1) / k), fade / k, post=False)     # last は枠つきで書き出し済み
        if outro > fade:
            emit(dark, outro - fade, post=False)
        silence(outro)
        total += outro
    frames.append(frames[-2])
    (fr / "frames.txt").write_text("\n".join(frames), encoding="utf-8")
    padded = []
    for p, tail in wavs:
        q = p.with_name(p.stem + "_p.wav")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(p), "-af", f"apad=pad_dur={tail:.3f}", "-ar", "48000", "-ac", "2", str(q)], check=True)
        padded.append(q)
    (fr / "audio.txt").write_text("\n".join(f"file '{p.name}'" for p in padded), encoding="utf-8")
    voice_wav = fr / "voice.wav"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(fr / "audio.txt"), str(voice_wav)], check=True)

    dst = outdir / "video.mp4"
    bgm = bgm_mod.resolve(cfg)
    if not wide and cfg.get("shorts.bgm_file"):          # Shorts だけ別の曲（心理学: 本編はニュース調、Shorts は今までどおり）
        sb = cfg.root / "assets" / "bgm" / str(cfg.get("shorts.bgm_file"))
        if sb.exists():
            bgm = sb
    vol = float(cfg.get("honpen.bgm_db", -20) if wide else cfg.get("shorts.bgm_db", -22))
    cmd = [ffmpeg, "-y", "-loglevel", "error"]
    vin, ain = 0, 1                       # 入力の番号（映像・声）
    loop_b = None
    if space:
        # 宇宙の解析室: ショーリールと同じ 3D の背景（星空のドームと光る床。継ぎ目のないループ）の上に、透明な重ね絵を重ねる。
        # 冒頭と章の扉の間だけ、4D のテッセラクトが浮かぶ版に切り替える
        from . import space as space_mod
        loop, loop_b = space_mod.loop_paths(cfg, ffmpeg=ffmpeg)
        cmd += ["-stream_loop", "-1", "-i", str(loop)]
        vin, ain = 1, 2
        if loop_b is not None and tess_ranges:
            cmd += ["-stream_loop", "-1", "-i", str(loop_b)]
            vin, ain = 2, 3
        else:
            loop_b = None
    cmd += ["-f", "concat", "-safe", "0", "-i", str(fr / "frames.txt"), "-i", str(voice_wav)]
    vf = f"[{vin}:v]fps={FPS},format=yuv420p[v]"
    if space:
        bg = f"[0:v]fps={FPS}[bgv]"
        if loop_b is not None:
            when = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in tess_ranges)
            bg = f"[0:v]fps={FPS}[bga];[1:v]fps={FPS}[bgb];[bga][bgb]overlay=0:0:enable='{when}'[bgv]"
        vf = f"{bg};[{vin}:v]fps={FPS},format=rgba[fg];[bgv][fg]overlay=0:0:format=auto:shortest=1,format=yuv420p[v]"
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", vf + f";[{ain + 1}:a]volume={vol}dB,afade=t=in:d=1.5,afade=t=out:st={max(0.0, total-(12 if wide else 3)):.2f}:d={12 if wide else 3}[bg];[{ain}:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
                "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-filter_complex", vf, "-map", "[v]", "-map", f"{ain}:a"]
    cmd += ["-c:v", "libx264", "-preset", "faster" if space else "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)
    (outdir / "quiz.json").write_text(json.dumps(quiz, ensure_ascii=False, indent=1), encoding="utf-8")
    write_srt(cues, outdir / "subtitles.srt")
    (outdir / "chapters.json").write_text(json.dumps(chapters, ensure_ascii=False), encoding="utf-8")
    for old in fr.glob("*.png"):                     # コマ画像は大きいので消す（frames.txt と音声は残す）
        old.unlink()
    log.info("%s: %s (%.1f 秒, %d コマ)", "本編" if wide else "参加型テスト Shorts", dst, total, n)
    return Built(video=dst, seconds=total, frames=n, quiz=quiz, cues=cues, chapters=chapters)


# ----------------------------------------------------------------------
# メタデータ
# ----------------------------------------------------------------------
def quiz_metadata(cfg: Config, quiz: dict[str, Any], parent_url: str = "") -> Metadata:
    title = (quiz.get("title") or "").strip()[:88] + " #Shorts"
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    parts = [quiz.get("hook", "").strip() or title]
    q = next((s for s in quiz.get("scenes", []) if s.get("kind") == "question"), None)
    if q and q.get("options"):
        parts.append("A. " + str(q["options"][0]) + "\nB. " + str(q["options"][1] if len(q["options"]) > 1 else ""))
    srcs = [s for s in (quiz.get("sources") or []) if s.get("name")]
    if srcs:
        parts.append("■ 出典\n" + "\n".join(f"・{s['name']} {s.get('url', '')}".rstrip() for s in srcs))
    if parent_url:
        parts.append(f"▶ 本編はこちら\n{parent_url}")
    parts.append(f"{domain.pitch(cfg)}毎日{times[0] if times else '20:00'}に本編を更新しています。")
    parts.append(subscribe_line(cfg))
    parts.append("■ 音声\n" + _voice_credit(cfg))
    parts.append("■ ご注意\n" + domain.disclaimer(cfg))
    tags_h = ["#Shorts"] + domain.hashtags(cfg)
    parts.append(" ".join(tags_h))
    tags = ["Shorts"] + [t.lstrip("#") for t in domain.hashtags(cfg)] + [w for w in re.split(r"[、。 ]", quiz.get("hook", "")) if 2 <= len(w) <= 10][:5]
    return Metadata(title=title[:100], description="\n\n".join(p for p in parts if p)[:5000], tags=tags[:15],
                    category_id=str(cfg.get("upload.category_id", "27")), language=str(cfg.get("upload.language", "ja")))
