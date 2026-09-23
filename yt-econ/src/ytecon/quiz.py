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
from .metadata import Metadata, _voice_credit

log = logging.getLogger(__name__)

FPS = 30
TRANS = 0.4                # 要素が出るときのアニメーション秒数
SEG_GAP = 0.15             # 文節の間
SCENE_TAIL = 0.6           # 場面の最後の余韻
COUNT_TICK = 0.85          # カウントダウン 1 拍


# ----------------------------------------------------------------------
# 台本（LLM）
# ----------------------------------------------------------------------
_SYSTEM = """あなたは YouTube Shorts の構成作家です。{field}のチャンネルで、視聴者が最初の 3 秒で「選ぶ」参加型テストの型を書きます。
字幕は出ません。1 人のナレーター（落ち着いた女性の声）が話し、画面には短い言葉の箱・矢印・ハイライトだけが出ます。
だから **画面の文字は短く（箱の中は 12 字以内）、ナレーションは 1 文節 25 字以内** にしてください。

# 型（この順。場面は 7〜8 個、全体で 55〜70 秒 = ナレーション合計 260〜320 字）
1. question  : 「3秒で選んでください」→ 状況を 1 行 → A と B の選択肢。どちらも「自分もそうだ」と思える日常の行動にする
2. countdown : 3・2・1（ナレーションなし。自動で入る）
3. result    : 選ばれがちな方（B）に向けて、よくある思い込み（strike）を消し、本当の理由（answer）を出す
4. flow または branch : 研究の核を矢印で見せる。branch は「人 → 2 つの行き先。片方を避けていた」の形、flow は「A → B → C」の因果
5. flow / versus : 「だからこうなる」の因果、または「うまくいく人 vs いかない人」の対比
6. versus / flow : もう 1 つの見せ方（上と違う種類にする）
7. steps     : 今日その場で試せる 1 つのこと。3 手順以内、各 10 字以内。最後に「それで終わり」
8. cta       : 固定（自動で入る。書かなくてよい）

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
    "hook": "締切が5日後の仕事、あなたはどっち？",
    "scenes": [
        {"kind": "question", "heading": "3秒で選んでください", "heading_hl": "3秒", "lead": "締切が5日後の仕事", "lead_hl": "5日後",
         "options": ["今日、少しだけ手をつける", "気分が乗った日に、まとめてやる"],
         "narration": [["3秒で選んでください。", 0], ["締切が5日後の仕事。", 1], ["A、今日少しだけ手をつける。", 2], ["B、気分が乗った日にまとめてやる。", 3]]},
        {"kind": "countdown"},
        {"kind": "result", "pick": "B", "option": "気分が乗った日に、まとめてやる", "strike": "意志が弱い", "answer": "仕組みの問題",
         "narration": [["Bを選んだ人。", 0], ["意志が弱いわけでは", 1], ["ありません。", 2]]},
        {"kind": "flow", "heading": "先延ばしの正体", "heading_hl": "先延ばし",
         "boxes": [{"text": "時間の管理の問題", "state": "dim"}, {"text": "気分の管理の問題", "state": "active"}],
         "narration": [["先延ばしは、時間の管理ではなく、", 0], ["気分の管理の問題だと分かってきています。", 1]]},
        {"kind": "branch", "heading": "研究が見つけたこと", "heading_hl": "研究", "note": "カールトン大学 ピチル教授", "source": "先延ばす人",
         "targets": [{"text": "課題", "avoided": False}, {"text": "嫌な気分", "avoided": True}],
         "narration": [["カールトン大学のピチル教授の研究では、", 0], ["先延ばす人ほど、課題の", 1], ["嫌な気分を避けていました。", 2]]},
        {"kind": "flow", "heading": "やる気を待つと", "heading_hl": "やる気",
         "boxes": [{"text": "やる気を待つ", "state": "normal"}, {"text": "嫌な気分が大きくなる", "state": "active", "up": True}, {"text": "もっと先延ばす", "state": "dim"}],
         "narration": [["だから、やる気を待つほど、", 0], ["嫌な気分は大きくなります。", 1], ["そして、もっと先延ばします。", 2]]},
        {"kind": "versus", "heading": "Aの人がしていること", "heading_hl": "A",
         "left": {"text": "気分で決める", "caption": "来ない日が多い"}, "right": {"text": "最初の2分を決める", "caption": "毎日できる"},
         "narration": [["Aの人がしているのは、", 0], ["気分ではなく、", 1], ["最初の2分だけを決めることです。", 2]]},
        {"kind": "steps", "heading": "今日のひとつ", "heading_hl": "今日のひとつ", "items": ["ファイルを開く", "名前だけ付ける", "閉じる"], "final": "それで終わり",
         "narration": [["今日試すなら、ひとつ。", 0], ["次の仕事のファイルを開いて、", 1], ["名前だけ付けて、", 2], ["閉じる。", 3], ["それで終わりです。", 4]]},
        {"kind": "cta"},
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
    data = llm.complete_json(_SYSTEM.format(field=domain.field(cfg)), user, _SCHEMA,
                             model=str(cfg.get("shorts.model", cfg.get("script.model", llm.DEFAULT_MODEL))),
                             effort=str(cfg.get("shorts.effort", "medium")))
    return normalize(data)


def normalize(q: dict[str, Any], add_cta: bool = True) -> dict[str, Any]:
    """LLM の出力を整える: countdown と cta を保証し、文節を 25 字前後に、段階を 0 からの連番に."""
    scenes = [s for s in (q.get("scenes") or []) if isinstance(s, dict) and s.get("kind")]
    kinds = [s["kind"] for s in scenes]
    if "question" in kinds and "countdown" not in kinds:
        scenes.insert(kinds.index("question") + 1, {"kind": "countdown"})
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
    brand: str = ""
    safe_top: int = 300          # Shorts の UI に隠れない縦の範囲
    safe_bottom: int = 1560
    header_y: int = 152          # ブランド名の位置と、その下の罫線
    header_line: int = 208
    body_top: int = 230          # これより下が中身（縦中央に寄せる対象）
    brand_size: int = 28
    chip: str = ""               # 右上の小さなラベル（本編の章など）


def theme(cfg: Config) -> Theme:
    pal = palette(cfg)
    w, h = cfg.get("shorts.resolution", [1080, 1920])
    return Theme(
        W=int(w), H=int(h),
        bg=pal.get("bg", "#FFFFFF"), text=pal.get("text", "#1A1A1C"), sec=pal.get("text_secondary", "#626264"),
        muted=pal.get("muted", "#949497"), line=pal.get("outline", "#D8D8DB"), surface=pal.get("surface", "#F1F1F4"),
        blue=pal.get("accent", "#0017C1"), blue_light=pal.get("surface_high", "#E8F1FE"), yellow=pal.get("accent2", "#FFE97A"),
        green=pal.get("positive", "#197A4B"), red=pal.get("negative", "#EC0000"),
        brand=str(cfg.get("channel.name", "") or ""),
    )


_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font_file(cfg: Config, weight: int) -> tuple[str, bool]:
    """(ファイル, 可変フォントか)。Bold は Noto Sans JP Bold、細い字は Medium/Regular、無ければ可変、最後は Bold."""
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
    path, variable = _font_file(cfg, weight)
    key = (f"{path}@{weight if variable else 0}", size)
    if key not in _font_cache:
        f = ImageFont.truetype(path, size)
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
        img = Image.new("RGBA", (th.W, th.H), th.bg)
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
        return img.convert("RGB")


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
        self.text_mm(d, x0 + 48, y + tab_h / 2 + 2, key, self.f(34), "#FFFFFF")
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
            P.text_mm(d, W / 2, cy0 + 440, "本編を見る", f, "#FFFFFF")
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
    out.paste(Image.new("RGB", body.size, th.bg), (0, bt))
    out.paste(body, (0, bt + dy))
    return out


@dataclass
class Built:
    video: Path
    seconds: float
    frames: int
    quiz: dict[str, Any] = field(default_factory=dict)


def build(cfg: Config, quiz: dict[str, Any], outdir: str | Path, provider=None, wide: bool = False) -> Built:
    """JSON → mp4。文節ごとに音声を合成し、場面を段階・遅延つきで描いて繋ぐ.

    wide=True なら本編（16:9）。場面の組み方は wide.build_scene_wide。
    """
    from . import bgm as bgm_mod
    from . import tts

    quiz = normalize(dict(quiz), add_cta=not wide)
    if wide:
        from . import wide as wide_mod
        th = wide_mod.theme_wide(cfg)
        builder = wide_mod.build_scene_wide
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

    def emit(img: Image.Image, dur: float) -> None:
        nonlocal n
        p = fr / f"f{n:04d}.png"
        img.save(p, compress_level=1)
        frames.extend([f"file '{p.name}'", f"duration {dur:.4f}"])
        n += 1

    def animate(scene: Scene, step: int, seconds: float, dy: int, extra=None, static: bool = False) -> None:
        trans = min(seconds * 0.8, (0 if static else scene.max_delay(step)) + TRANS)
        k = max(1, int(trans * FPS))
        for i in range(k):
            el = (i + 1) / k * trans
            img = scene.render(step, 99.0 if static else el)
            if extra:
                extra(img, min(1.0, el / TRANS))
            emit(_shift(img, dy, th), trans / k)
        rest = seconds - trans
        if rest > 0.01:
            img = scene.render(step, 99.0)
            if extra:
                extra(img, 1.0)
            emit(_shift(img, dy, th), rest)

    def silence(sec: float) -> None:
        p = fr / f"a{len(wavs):03d}.wav"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{sec:.3f}", str(p)], check=True)
        wavs.append((p, 0.0))

    scenes = quiz["scenes"]
    q_scene = next((sc for sc in scenes if sc.get("kind") == "question"), None)
    shared_dy = None
    for sc in scenes:
        kind = sc.get("kind")
        if kind == "countdown":
            base = builder(cfg, th, dict(q_scene or {}, kind="countdown"))
            if shared_dy is None:
                shared_dy = _content_offset(base, th, with_extra=True)
            for num in (3, 2, 1):
                def extra(img, t, num=num, base=base):
                    base.extra(ImageDraw.Draw(img), t, num)
                animate(base, 3, COUNT_TICK, shared_dy, extra=extra, static=True)
                silence(COUNT_TICK)
                total += COUNT_TICK
            continue
        scene = builder(cfg, th, sc)
        nar = sc.get("narration") or (cta_narration(cfg) if kind == "cta" else [])
        if kind == "question":
            cd = builder(cfg, th, dict(sc, kind="countdown"))
            shared_dy = _content_offset(cd, th, with_extra=True)
            dy = shared_dy
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
            else:
                p = None
                voice = 1.2
            tail = SCENE_TAIL if i == len(ordered) - 1 else SEG_GAP
            if p is not None:
                wavs.append((p, tail))
            else:
                silence(voice + tail)
            animate(scene, st, voice + tail, dy)
            total += voice + tail
        # 台本にない段階（要素だけの段階）が残っていれば最後にまとめて出す
        last = scene.last_step()
        if last > max(ordered):
            for st in range(max(ordered) + 1, last + 1):
                silence(1.0)
                animate(scene, st, 1.0, dy)
                total += 1.0

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
    vol = float(cfg.get("shorts.bgm_db", -22))
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(fr / "frames.txt"), "-i", str(voice_wav)]
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", f"[2:a]volume={vol}dB,afade=t=in:d=1.5,afade=t=out:st={max(0.0, total-3):.2f}:d=3[bg];[1:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)
    (outdir / "quiz.json").write_text(json.dumps(quiz, ensure_ascii=False, indent=1), encoding="utf-8")
    for old in fr.glob("*.png"):                     # コマ画像は大きいので消す（frames.txt と音声は残す）
        old.unlink()
    log.info("参加型テスト Shorts: %s (%.1f 秒, %d コマ)", dst, total, n)
    return Built(video=dst, seconds=total, frames=n, quiz=quiz)


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
    parts.append("■ 音声\n" + _voice_credit(cfg))
    parts.append("■ ご注意\n" + domain.disclaimer(cfg))
    tags_h = ["#Shorts"] + domain.hashtags(cfg)
    parts.append(" ".join(tags_h))
    tags = ["Shorts"] + [t.lstrip("#") for t in domain.hashtags(cfg)] + [w for w in re.split(r"[、。 ]", quiz.get("hook", "")) if 2 <= len(w) <= 10][:5]
    return Metadata(title=title[:100], description="\n\n".join(parts)[:5000], tags=tags[:15],
                    category_id=str(cfg.get("upload.category_id", "27")), language=str(cfg.get("upload.language", "ja")))
