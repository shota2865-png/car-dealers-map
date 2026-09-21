"""画面素材の用意.

台本の visual 指定に従って、セクションごとに1枚の背景画像(1920x1080)を作る。
  chart    → matplotlib で図表を描く
  textcard → 見出し + 箇条書きのカードを描く
  stock    → Pexels から写真を取り、暗くして文字が乗るようにする

APIキーが無い/取得に失敗した場合は必ずグラデーション背景に落ちる。
素材の都合でパイプライン全体が止まらないようにするのが方針。
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .script import Section, VideoScript

log = logging.getLogger(__name__)

# 日本語フォントの探索順（assets/fonts に置いたものを最優先）。
# IPAGothic は線が細く、動画のテロップだと潰れて読めない。
# 必ず Bold 以上を使うこと。scripts/install_fonts.py で用意できる。
_FONT_CANDIDATES = {
    # 予備。black が無い環境ではこちらに落ちる
    "bold": [
        "assets/fonts/NotoSansJP-Bold.ttf",
        "assets/fonts/NotoSansJP-Bold.otf",
        "assets/fonts/NotoSansCJKjp-Bold.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "C:/Windows/Fonts/YuGothB.ttc",
        "C:/Windows/Fonts/meiryob.ttc",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",   # 最後の手段
    ],
    # 既定。テロップ・サムネ・見出し・図表すべてこれを使う
    "black": [
        "assets/fonts/NotoSansJP-Black.ttf",
        "assets/fonts/NotoSansJP-Black.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
    ],
    # 図解・表・カードの本文。地上波のテロップに近い UD ゴシック（BIZ UDPGothic、OFL）。無ければ black
    "body": [
        "assets/fonts/MPLUSRounded1c-Black.ttf",
        "assets/fonts/NotoSansJP-Black.ttf",
    ],
}
# 大きな見出し・キーワードは black（太くて遠目に効く）、それ以外は body
_DISPLAY_ROLES = ("display_xl", "display_l", "display_s", "numeral_xl", "headline_l")


def weight_for(role: str) -> str:
    return "black" if role in _DISPLAY_ROLES else "body"

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


class AssetError(RuntimeError):
    pass


def font_path(cfg: Config, weight: str = "black") -> str:
    """使える日本語フォントのパスを返す.

    既定は black（900）。動画のテロップは遠目でも読める太さが要る。
    無ければ bold に落ちる。
    """
    # visuals.font が指定されていれば、画面の文字は全部それ（字幕と同じ丸ゴシックにそろえる、など）
    one = str(cfg.get("visuals.font", "") or "").strip()
    if one:
        q = Path(one)
        q = q if q.is_absolute() else cfg.root / q
        if q.exists():
            return str(q)
    for candidates in (_FONT_CANDIDATES.get(weight, []),
                       _FONT_CANDIDATES["bold"] if weight != "bold" else []):
        for cand in candidates:
            p = Path(cand)
            if not p.is_absolute():
                p = cfg.root / cand
            if p.exists():
                return str(p)
    raise AssetError(
        "日本語フォントが見つかりません。\n"
        "  python scripts/install_fonts.py\n"
        "を実行するか、assets/fonts/NotoSansJP-Bold.ttf を配置してください。"
    )


# フォントに無い記号の置き換え候補（左から順に、描ける最初のものを使う）
_GLYPH_FALLBACKS = {
    "\u2192": ["\u25b6", "\u25ba", "\u00bb", ">"],   # → ▶ ► » >
    "\u2190": ["\u25c0", "\u00ab", "<"],
    "\u21d2": ["\u25b6", ">"],                        # ⇒
    "\u301c": ["\uff5e", "-"],                        # 〜 → ～
    "\u2212": ["\uff0d", "-"],                        # −
    "\u2013": ["\uff0d", "-"],
    "\u2014": ["\uff0d", "-"],
    "\u2022": ["\u30fb", "-"],                        # •
    "\u2713": ["\u25cb", "o"],                        # ✓
    "\u203b": ["\uff0a", "*"],                        # ※（この字も無い）
    "\uff5e": ["\u301c", "-"],                        # ～ → 〜
}
_cmap_cache: dict[str, set[int]] = {}


def _cmap(path: str) -> set[int]:
    """フォントが持つコードポイントの集合.

    PIL の getmask は .notdef（豆腐）にも bbox を返すので、有無の判定に使えない。
    実際に cmap を読む。
    """
    if path not in _cmap_cache:
        try:
            from fontTools.ttLib import TTFont

            _cmap_cache[path] = set(TTFont(path).getBestCmap().keys())
        except Exception:
            _cmap_cache[path] = set()      # 読めなければ置換しない
    return _cmap_cache[path]


def _safe_for_font(font: ImageFont.FreeTypeFont, text: str) -> str:
    """フォントに無い字を、描ける近い字に置き換える（すべてのカードが通る）."""
    have = _cmap(getattr(font, "path", "") or "")
    if not have or not text:
        return text
    out = []
    for ch in text:
        if ch.isspace() or ord(ch) in have:
            out.append(ch)
            continue
        repl = "-"
        for cand in _GLYPH_FALLBACKS.get(ch, []):
            if ord(cand) in have:
                repl = cand
                break
        out.append(repl)
    return "".join(out)


def safe_text(cfg: Config, text: str, weight: str = "black") -> str:
    return _safe_for_font(ImageFont.truetype(font_path(cfg, weight), 40), text)


def load_font(cfg: Config, size: int, weight: str = "black") -> ImageFont.FreeTypeFont:
    key = (font_path(cfg, weight), size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(key[0], size)
    return _font_cache[key]


def content_width(cfg: Config) -> int:
    """右下のキャラクターに隠れない、描画に使ってよい横幅（px）.

    カード類の中央揃えや図表の右端はこれを基準にする。
    """
    w, _h = cfg.get("video.resolution", [1920, 1080])
    if not cfg.get("character.enabled", False):
        return w
    from .character import reserved_width

    return max(int(w * 0.55), w - reserved_width(cfg))


def palette(cfg: Config) -> dict[str, str]:
    """配色。デザイントークン（video.design のプリセット）→ visuals.palette の上書き の順."""
    from . import design

    default = {
        "bg": "#0E1525", "surface": "#18223A", "surface_high": "#22304C",
        "outline": "#33405C", "text": "#F2F5FA", "text_secondary": "#C7D0DE",
        "muted": "#9AA7BE", "accent": "#4CC2FF", "accent2": "#FFC857",
        "positive": "#5BD99A", "negative": "#FF6B6B", "warning": "#FFB84C",
    }
    default.update(design.colors(cfg))
    default.update(cfg.get("visuals.palette", {}) or {})
    return default


def ts(cfg: Config, role: str, fallback: int = 48) -> int:
    """文字サイズを役割で引く（display_l / headline_m / body_l / label …）."""
    from . import design

    return design.type_size(cfg, role, fallback)


def rad(cfg: Config, size: str = "m") -> int:
    from . import design

    return design.radius(cfg, size)


# ----------------------------------------------------------------------
# 下地
# ----------------------------------------------------------------------
def gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (1, h))
    d = ImageDraw.Draw(base)
    t = _rgb(top)
    b = _rgb(bottom)
    for y in range(h):
        k = y / max(h - 1, 1)
        d.point((0, y), tuple(int(t[i] + (b[i] - t[i]) * k) for i in range(3)))
    return base.resize((w, h), Image.BILINEAR)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          max_width: int, _balance_ok: bool = True, strict: bool = False) -> list[str] | None:
    """折り返す。strict=True のときは、自然な位置で割れなければ None を返す（fit_text が縮める）."""
    """日本語は単語境界がないので1文字ずつ詰めて折り返す."""
    text = _safe_for_font(font, text)
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        # 禁則: 「？」「。」「、」などは行頭に来させない（幅を少し超えてもぶら下げる）
        if draw.textlength(trial, font=font) > max_width and current and ch not in _NO_LINE_START:
            # 行の後ろ 40% に空白・読点があれば、そこで折る（「スー/パー」のような割れを避ける）
            cut = max(current.rfind(sep) for sep in _BREAK_AFTER)
            if cut >= int(len(current) * 0.6):
                lines.append(current[:cut + 1].rstrip())
                current = current[cut + 1:] + ch
            else:
                lines.append(current)
                current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    lines = [ln for ln in lines if ln]
    # 2〜3 行なら、割る位置を総当たりで選ぶ（「価値／が下がる」「お／よそ」を避ける）
    if _balance_ok and len(lines) in (2, 3):
        # strict のときは「自然な切れ目」（読点・助詞の直後など、点数 -3 以上）だけを認める。
        # 語の途中でしか割れない大きさなら None を返し、fit_text が少し縮めて試し直す
        best = _best_split(draw, text, font, max_width, len(lines), min_score=-3.0 if strict else None)
        if best:
            return best
        if strict:
            return None
    return lines


_NO_LINE_START = set("、。，．・：；？！?!」』）〕］｝〉》〟ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶー～")
_BREAK_AFTER = (" ", "　", "、", "。", "，", "・", "／", "→")
_PARTICLE_CHARS = set("がをにはでともへやのか")


def fit_text(cfg: Config, d: ImageDraw.ImageDraw, text: str, role: str, max_width: int,
             max_lines: int, weight: str | None = None, min_size: int = 28
             ) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """文字が枠に収まるまでフォントを小さくして、(フォント, 行) を返す.

    まず役割サイズで折り返し、行数が上限を超えたら 8% ずつ縮める。
    それでも入らなければ最終行を「…」で切る（枠からはみ出させない）。
    """
    size = ts(cfg, role)
    weight = weight or weight_for(role)
    # 2 行にするより、2 割までなら縮めて 1 行に収めるほうが読みやすい（表のセルなど）
    if max_lines >= 2:
        s1 = size
        while s1 >= max(min_size, int(size * 0.8)):
            f1 = load_font(cfg, s1, weight)
            if d.textlength(_safe_for_font(f1, text), font=f1) <= max_width:
                return f1, [_safe_for_font(f1, text)]
            s1 = int(s1 * 0.95)
    while True:
        font = load_font(cfg, size, weight)
        # 自然な位置で割れない（語の途中で切れる）ときも縮めて試す
        lines = _wrap(d, text, font, max_width, strict=True)
        if lines is not None and len(lines) <= max_lines:
            break
        if size <= min_size:
            lines = _wrap(d, text, font, max_width)
            break
        size = max(min_size, int(size * 0.92))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and d.textlength(lines[-1] + "…", font=font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return font, lines


def _cut_score(text: str, i: int) -> float | None:
    """位置 i で切るときの点数（None = 切ってはいけない）."""
    b = text[i:].lstrip(" 　")
    if not b or b[0] in _NO_LINE_START or b[0] in _PARTICLE_CHARS:
        return None
    prev, nxt = text[i - 1], b[0]
    score = 0.0
    kata = lambda c: ("ァ" <= c <= "ヶ") or c == "ー"
    kanji = lambda c: "一" <= c <= "龥"
    # 助詞の直後は切りやすい。ただし次がひらがな（「上が｜らない」）なら語の途中の可能性が高い
    if prev in _PARTICLE_CHARS and not ("ぁ" <= nxt <= "ん"):
        score += 5
    if prev in " 　、。，・／→＝「」":
        score += 12                         # 読点・記号の直後が最良
    # ひらがなの途中で切る（「お｜よそ」）のは避ける
    if ("ぁ" <= prev <= "ん") and ("ぁ" <= nxt <= "ん") and prev not in _PARTICLE_CHARS:
        score -= 6
    # カタカナ語・数字・漢字熟語の途中（「パスタソー｜ス」「マイ｜ナス」「行動経｜済学」）は避ける
    if kata(prev) and kata(nxt):
        score -= 14
    if prev.isdigit() and nxt.isdigit():
        score -= 14
    if (prev.isdigit() or prev in "%％.．,，") and nxt in "円%％割分倍人年月日時か個台点円兆億万千百":
        score -= 16                         # 「1000｜円札」「5.1｜%」のように数字と単位を割らない
    if kanji(prev) and kanji(nxt):
        score -= 5
    # 漢字の直後のひらがなが助詞でない（「苦｜しい」「上｜がる」の送り仮名）ときは語の途中の可能性が高い
    if kanji(prev) and ("ぁ" <= nxt <= "ん") and nxt not in _PARTICLE_CHARS:
        score -= 4
    return score


def _best_split(draw, text: str, font, max_width: int, k: int,
                min_score: float | None = None) -> list[str] | None:
    """k 行（2 or 3）の割り位置を点数で選ぶ: 幅に収まる／行頭に助詞・句読点を置かない／長さが釣り合う.

    min_score を渡すと、切れ目の自然さ（釣り合いの減点を除く）がそれ未満の候補は選ばない。
    """
    n = len(text)

    def fits(seg: str) -> bool:
        return draw.textlength(seg, font=font) <= max_width

    best, best_score = None, -1e9
    if k == 2:
        for i in range(2, n - 1):
            a, b = text[:i].rstrip(" 　"), text[i:].lstrip(" 　")
            if not a or not b or not fits(a) or not fits(b):
                continue
            sc = _cut_score(text, i)
            if sc is None or (min_score is not None and sc < min_score):
                continue
            sc -= abs(len(a) - len(b)) * 2.0
            if sc > best_score:
                best, best_score = [a, b], sc
        return best
    for i in range(2, n - 3):
        a = text[:i].rstrip(" 　")
        if not a or not fits(a):
            continue
        sa = _cut_score(text, i)
        if sa is None or (min_score is not None and sa < min_score):
            continue
        for j in range(i + 2, n - 1):
            b, c = text[i:j].strip(" 　"), text[j:].lstrip(" 　")
            if not b or not c or not fits(b) or not fits(c):
                continue
            sb = _cut_score(text, j)
            if sb is None or (min_score is not None and sb < min_score):
                continue
            avg = n / 3
            sc = sa + sb - (abs(len(a) - avg) + abs(len(b) - avg) + abs(len(c) - avg)) * 1.5
            if sc > best_score:
                best, best_score = [a, b, c], sc
    return best


# ----------------------------------------------------------------------
# textcard
# ----------------------------------------------------------------------
def render_textcard(cfg: Config, heading: str, bullets: list[str],
                    out: Path, active: int | None = None) -> Path:
    img, d, pal, _cw, h = _card_base(cfg, card_style(cfg, "bullets"))
    w = img.width
    img.info["vcenter"] = (TOP_BAND, h - SUB_BAND)

    # 見出し
    cw = _cw
    f_head, head_lines = fit_text(cfg, d, heading, "headline_l", min(cw - 320, span_width(cfg) - 60), 2)
    y = 200
    lh = int(f_head.size * 1.28)
    _accent_bar(d, 150, y, head_lines[0], f_head, pal["accent"], lines=len(head_lines), lh=lh) if head_lines else None
    for line in head_lines:
        d.text((196, y), line, font=f_head, fill=pal["text"])
        y += lh

    # 箇条書き（4項目 × 最大2行が枠に収まるよう、本文は少し小さくしてもよい）
    y = max(y + 60, 440)
    bottom = h - SUB_BAND - 20
    for i, bullet in enumerate(bullets[:4]):
        f_body, blines = fit_text(cfg, d, bullet, "body_l", min(cw - 480, span_width(cfg) - 120), 2)
        bh = int(f_body.size * 1.3)
        if y + bh * len(blines) > bottom:
            break
        _reserve_marker(d, 196, y + 31)
        if active is not None and i == active:
            _marker(d, 196, y + 31, pal["accent"])
        else:
            d.ellipse([200, y + 20, 222, y + 42], fill=pal["accent2"])
        for line in blines:
            d.text((256, y), line, font=f_body, fill=pal["text"] if (active is None or i == active) else pal["text"])
            y += bh
        y += 26

    return _save(img, out)


# ----------------------------------------------------------------------
# chart
# ----------------------------------------------------------------------
def render_chart(cfg: Config, spec: dict[str, Any], out: Path) -> Path:
    """matplotlib で図表を描く。データが不正なら textcard にフォールバック."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    pal = palette(cfg)
    w, h = cfg.get("video.resolution", [1920, 1080])
    fp = font_manager.FontProperties(fname=font_path(cfg))

    kind = spec.get("type", "bar")
    labels = spec.get("labels") or []
    series = [s for s in (spec.get("series") or []) if s.get("values")]
    if kind == "none" or not labels or not series:
        raise AssetError("図表データが不足しています")

    dpi = 100
    fig, ax = plt.subplots(figsize=(w / dpi, h / dpi), dpi=dpi)
    fig.patch.set_facecolor(pal["bg"])
    ax.set_facecolor(pal["bg"])

    colors = [pal["accent"], pal["accent2"], pal["positive"], pal["negative"], "#B58CFF"]

    if kind == "line":
        for i, s in enumerate(series):
            ax.plot(labels[: len(s["values"])], s["values"], marker="o", linewidth=4,
                    markersize=10, color=colors[i % len(colors)], label=s.get("name", ""))
    elif kind == "pie":
        values = series[0]["values"]
        texts = ax.pie(values, labels=labels[: len(values)], autopct="%1.0f%%",
                       colors=colors,
                       textprops={"fontproperties": fp, "color": pal["text"]})
        for group in texts[1:]:
            for t in group:
                t.set_fontsize(28)
        ax.axis("equal")
    elif kind == "stacked_bar":
        bottom = [0.0] * len(labels)
        for i, s in enumerate(series):
            vals = (s["values"] + [0] * len(labels))[: len(labels)]
            ax.bar(labels, vals, bottom=bottom, color=colors[i % len(colors)],
                   label=s.get("name", ""))
            bottom = [b + v for b, v in zip(bottom, vals)]
    else:  # bar
        n = len(series)
        width = 0.8 / max(n, 1)
        xs = range(len(labels))
        for i, s in enumerate(series):
            vals = (s["values"] + [0] * len(labels))[: len(labels)]
            ax.bar([x + i * width - 0.4 + width / 2 for x in xs], vals, width=width,
                   color=colors[i % len(colors)], label=s.get("name", ""))
        ax.set_xticks(list(xs))
        ax.set_xticklabels(labels)

    ax.set_title(spec.get("title", ""), fontproperties=fp, fontsize=44,
                 color=pal["text"], pad=46)

    if kind != "pie":
        ax.set_xlabel(spec.get("x_label", ""), fontproperties=fp, fontsize=26,
                      color=pal["text"], labelpad=12)
        # 日本語の縦書きラベルは1文字ずつ回って読めなくなるので、
        # 回転させず軸の左上に単位として置く
        y_label = spec.get("y_label", "")
        if y_label:
            ax.set_ylabel("")
            ax.annotate(
                f"（{y_label}）", xy=(0, 1), xycoords="axes fraction",
                xytext=(0, 6), textcoords="offset points",
                fontproperties=fp, fontsize=24, color="#9AA7BE",
                ha="left", va="bottom",
            )
        ax.tick_params(colors=pal["text"], labelsize=24)
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            # FontProperties は自前の既定サイズ(12)を持っているので、
            # 割り当てたあとに必ずサイズを上書きし直す
            label.set_fontproperties(fp)
            label.set_fontsize(24)
        for spine in ax.spines.values():
            spine.set_color("#33405C")
        ax.grid(axis="y", color="#26324C", linewidth=1.2)
        ax.set_axisbelow(True)
        if len(series) > 1 or series[0].get("name"):
            leg = ax.legend(prop=fp, fontsize=26, facecolor=pal["surface"],
                            edgecolor="#33405C", labelcolor=pal["text"],
                            loc="best", framealpha=0.92)
            for text in leg.get_texts():
                text.set_fontsize(26)

    # 図表の右端はキャラクターの手前まで。字幕の帯（下 1/3）には何も置かない
    right = min(0.94, content_width(cfg) / w - 0.02)
    fig.subplots_adjust(left=0.12, right=right, top=0.82, bottom=0.34)

    note = spec.get("note") or ""
    if note:
        # 図の外に置くとタイトルか字幕のどちらかと必ずぶつかる。
        # 図の中・右上に置く。データと重ならないよう、縦軸の上に 28% の余白を足す
        if kind != "pie":
            lo, hi = ax.get_ylim()
            ax.set_ylim(lo, hi + (hi - lo) * 0.28)
        ax.text(0.985, 0.965, note, transform=ax.transAxes, fontproperties=fp,
                fontsize=19, color="#C7D0DE", ha="right", va="top",
                bbox={"boxstyle": "round,pad=0.35", "facecolor": pal["surface"],
                      "edgecolor": "#33405C", "alpha": 0.88})
    out.parent.mkdir(parents=True, exist_ok=True)
    style = card_style(cfg, "chart")
    if style == "solid":
        fig.savefig(out, facecolor=pal["bg"])
        plt.close(fig)
        return out
    # 動く背景の上に置く: 図は透過で描き、すりガラスの面に載せる
    tmp = out.with_suffix(".chart.png")
    fig.savefig(tmp, transparent=True)
    plt.close(fig)
    base, _d, _pal, _cw, _h = _card_base(cfg, "glass")
    chart_img = Image.open(tmp).convert("RGBA")
    if chart_img.size != base.size:
        chart_img = chart_img.resize(base.size, Image.LANCZOS)
    base.alpha_composite(chart_img)
    tmp.unlink(missing_ok=True)
    return _save(base, out)


# ----------------------------------------------------------------------
# stock 写真
# ----------------------------------------------------------------------
def fetch_stock(cfg: Config, query: str, out: Path) -> Path | None:
    key = cfg.env("PEXELS_API_KEY")
    if not key or not query:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": 5, "orientation": "landscape",
                    "size": "large"},
            timeout=20,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        if not photos:
            return None
        # クエリごとに安定して同じ写真を選ぶ（再実行で絵が変わらない）
        idx = int(hashlib.md5(query.encode()).hexdigest(), 16) % len(photos)
        url = photos[idx]["src"]["large2x"]
        img_bytes = requests.get(url, timeout=30).content
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(img_bytes)
        return out
    except Exception as exc:
        log.warning("Pexels 取得失敗 (%s): %s", query, exc)
        return None


def _prepare_photo(cfg: Config, src: Path, out: Path) -> Path:
    """写真を 16:9 にトリムし、暗くして文字が読めるようにする."""
    pal = palette(cfg)
    w, h = cfg.get("video.resolution", [1920, 1080])
    img = Image.open(src).convert("RGB")
    # cover トリミング
    scale = max(w / img.width, h / img.height)
    img = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)),
                     Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))
    # 暗幕 + 下部を濃くする
    overlay = Image.new("RGB", (w, h), _rgb(pal["bg"]))
    img = Image.blend(img, overlay, 0.45)
    shade = gradient((w, h), pal["bg"], pal["bg"])
    mask = Image.linear_gradient("L").rotate(180).resize((w, h))
    img = Image.composite(Image.blend(img, shade, 0.85), img, mask.point(
        lambda v: int(max(0, v - 120) * 1.6)))
    img.save(out, quality=92)
    return out


def _overlay_heading(cfg: Config, image_path: Path, heading: str,
                     bullets: list[str]) -> None:
    """写真の上にセクション見出しを焼き込む."""
    pal = palette(cfg)
    img = Image.open(image_path).convert("RGB")
    d = ImageDraw.Draw(img)
    f_head = load_font(cfg, ts(cfg, "headline_m", 68), "black")
    lines = _wrap(d, heading, f_head, img.width - 360)[:2]
    y = 170
    d.rectangle([150, y - 16, 160, y + 88 * len(lines) - 16], fill=pal["accent"])
    for line in lines:
        d.text((196, y), line, font=f_head, fill=pal["text"],
               stroke_width=3, stroke_fill="#00000099")
        y += 88
    f_b = load_font(cfg, ts(cfg, "body_m", 44))
    y += 36
    for bullet in bullets[:3]:
        d.text((200, y), "・" + bullet, font=f_b, fill=pal["text"],
               stroke_width=3, stroke_fill="#00000099")
        y += 62
    img.save(image_path, quality=92)


# ----------------------------------------------------------------------
# セクション単位のディスパッチ
# ----------------------------------------------------------------------
def build_section_image(cfg: Config, section: Section, index: int,
                        outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"scene_{index:02d}.jpg"
    visual = section.visual

    if visual.kind == "chart" and visual.chart:
        try:
            render_chart(cfg, visual.chart, out)
            return out
        except Exception as exc:
            log.warning("図表描画に失敗したのでテキストカードに切替: %s", exc)

    if visual.kind == "stock":
        raw = fetch_stock(cfg, visual.query, outdir / f"_raw_{index:02d}.jpg")
        if raw:
            _prepare_photo(cfg, raw, out)
            _overlay_heading(cfg, out, section.heading, section.on_screen)
            raw.unlink(missing_ok=True)
            return out

    render_textcard(cfg, section.heading, section.on_screen, out)
    return out


def build_title_card(cfg: Config, title: str, out: Path) -> Path:
    """冒頭のタイトルカード."""
    img, d, pal, cw, h = _card_base(cfg, card_style(cfg, "title"))
    f, lines = fit_text(cfg, d, title, "display_m", cw - 240, 2, min_size=64)
    total = len(lines) * int(f.size * 1.3)
    y = (h - total) // 2
    for line in lines:
        y = _center_text(d, line, f, y, cw, pal["text"]) + (int(f.size * 1.3) - f.size - 18)
    f_small = load_font(cfg, ts(cfg, "label", 40))
    _center_text(d, cfg.get("channel.name", ""), f_small, y + 40, cw, pal["accent"])
    return _save(img, out)


def build_outro_card(cfg: Config, out: Path) -> Path:
    img, d, pal, cw, h = _card_base(cfg, card_style(cfg, "outro"))
    f = load_font(cfg, ts(cfg, "headline_l", 78), "black")
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    when = f"毎日{times[0]}に更新" if len(times) == 1 else "毎日 朝と夜に更新"
    for i, line in enumerate([when, "チャンネル登録で見逃しなく"]):
        _center_text(d, line, f, int(h / 2 - 110 + i * 120), cw,
                     pal["text"] if i == 0 else pal["accent"])
    return _save(img, out)


def build_all(cfg: Config, script: VideoScript, outdir: str | Path) -> dict[str, Path]:
    """1本ぶんの画像をすべて用意する."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    result["title"] = build_title_card(cfg, script.topic_title, outdir / "scene_title.jpg")
    for i, sec in enumerate(script.sections):
        result[f"s{i}"] = build_section_image(cfg, sec, i, outdir)
    result["outro"] = build_outro_card(cfg, outdir / "scene_outro.jpg")
    log.info("画面素材 %d 枚を生成", len(result))
    return result


# ======================================================================
# 追加のカード類。1シーン8秒で画を切り替えるために、同じ内容を
# いろいろな見せ方で出せるようにする。
# ======================================================================
# カードの下地の描き方。動く背景の上に重ねる前提なので、透過で作る
#   solid : 従来どおり不透明なグラデーション（動く背景を使わないとき）
#   glass : 透過 + 半透明の面（すりガラス）。箇条書き・用語・出典・図表など文字が多いもの
#   clear : 透過のみ。キーワード・数字・一文など短い文字を背景の上に直接置く（影で読ませる）
GLASS_KINDS = ("bullets", "term", "chart", "outro")
CLEAR_KINDS = ("keyword", "number", "quote", "title", "reference")


def card_style(cfg: Config, kind: str) -> str:
    mode = str(cfg.get("visuals.card_style", "mixed") or "mixed")
    if not cfg.get("visuals.motion_backgrounds", True) or mode == "solid":
        return "solid"
    if mode in ("glass", "clear"):
        return mode
    return "clear" if kind in CLEAR_KINDS else "glass"


def _card_base(cfg: Config, style: str = "solid"
               ) -> tuple[Image.Image, ImageDraw.ImageDraw, dict[str, str], int, int]:
    """返す w は『使ってよい幅』（キャラがいればその手前まで）。画像自体は全幅."""
    from . import design

    pal = palette(cfg)
    w, h = cfg.get("video.resolution", [1920, 1080])
    if style == "solid":
        img = gradient((w, h), pal["bg"], pal["surface"])
        return img, ImageDraw.Draw(img), pal, content_width(cfg), h
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    dim = int(255 * float(cfg.get("visuals.backdrop_dim", 0.30)))
    try:
        x0, x1 = content_span(cfg)
        if x0 > 0:                       # 左にも立ち絵がいる（掛け合い）ときだけ位置を直す
            img.info["span"] = (x0, x1)
    except Exception:
        pass
    # 背景を少し落とす層（backdrop）と、すりガラスの面は _save で中身の下に敷く。
    # ここでは中身だけを透明レイヤーに描く（中身の位置・大きさを後から測って直せるように）
    img.info["dim"] = dim
    if style == "glass":
        r, g, b = _rgb(pal["surface"])
        orr, og, ob = _rgb(pal["outline"])
        img.info["glass"] = {
            "fill": (r, g, b, int(255 * 0.82)),
            "outline": (orr, og, ob, 160),
            "width": max(design.stroke(cfg, "card"), 2),
            "radius": design.radius(cfg, "l"),
        }
    return img, d, pal, content_width(cfg), h


GLASS_PAD = (56, 40)   # すりガラスの面が中身の外側に取る余白（左右, 上下）


def _fit_glass(img: Image.Image) -> Image.Image:
    """中身（透明レイヤーに描いた文字・図）の下に、背景を落とす層と（glass なら）外接矩形の面を敷く."""
    spec = img.info.get("glass")
    dim = img.info.get("dim")
    if not spec and dim is None:
        return img
    w, h = img.size
    bbox = img.getchannel("A").getbbox()
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rectangle([0, 0, w, h], fill=(0, 0, 0, int(dim or 0)))
    if spec and bbox:
        px, py = GLASS_PAD
        box = [max(bbox[0] - px, 16), max(bbox[1] - py, 16),
               min(bbox[2] + px, w - 16), min(bbox[3] + py, h - 16)]
        d.rounded_rectangle(box, radius=spec["radius"], fill=spec["fill"],
                            outline=spec["outline"], width=spec["width"])
    base.alpha_composite(img)
    return base


def content_span(cfg: Config) -> tuple[int, int]:
    """左右の立ち絵に掛からない x の範囲 (x0, x1)."""
    w, _h = cfg.get("video.resolution", [1920, 1080])
    from .character import reserved_widths
    left, right = reserved_widths(cfg)
    return left, w - right


def span_width(cfg: Config) -> int:
    """左右の立ち絵のあいだで、文字を置いてよい幅（両端の余白を引いたもの）."""
    w, _h = cfg.get("video.resolution", [1920, 1080])
    try:
        x0, x1 = content_span(cfg)
    except Exception:
        return w - 300
    if x0 <= 0:
        return w - 300
    return max(int(w * 0.45), (x1 - x0) - 2 * (GLASS_PAD[0] + 24))


def _fit_h(img: Image.Image, x0: int, x1: int) -> Image.Image:
    """中身を (x0, x1) の間に収める。はみ出すなら縦横同じ比率で縮め、左右はその範囲の中央に."""
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        return img
    px, _py = GLASS_PAD
    lo, hi = x0 + px + 8, x1 - px - 8
    bw = bbox[2] - bbox[0]
    scale = min(1.0, (hi - lo) / max(bw, 1))
    cx_want = (lo + hi) / 2
    cx_have = (bbox[0] + bbox[2]) / 2
    if scale >= 0.999 and abs(cx_want - cx_have) < 2:
        return img
    content = img.crop(bbox)
    if scale < 0.999:
        content = content.resize((max(int(content.width * scale), 1), max(int(content.height * scale), 1)),
                                 Image.LANCZOS)
    cy = (bbox[1] + bbox[3]) / 2
    moved = Image.new("RGBA", img.size, (0, 0, 0, 0))
    moved.alpha_composite(content, (int(cx_want - content.width / 2), int(cy - content.height / 2)))
    moved.info.update(img.info)
    return moved


def _vcenter(img: Image.Image) -> Image.Image:
    """中身全体（見出し＋図＋出典）を、上の余白と字幕帯のあいだで上下中央に寄せる."""
    band = img.info.pop("vcenter", None)
    if not band:
        return img
    top, bottom = band
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        return img
    want = (top + bottom) / 2
    have = (bbox[1] + bbox[3]) / 2
    dy = int(round(want - have))
    dy = max(min(dy, bottom - bbox[3]), top - bbox[1])   # 帯からはみ出す方向には動かさない
    if abs(dy) < 2:
        return img
    moved = Image.new("RGBA", img.size, (0, 0, 0, 0))
    moved.alpha_composite(img, (0, dy))
    moved.info.update(img.info)
    return moved


def _draw_note(img: Image.Image) -> None:
    """図解の出典行を、中身のすぐ下（字幕帯より上）に描く。_diagram_base が予約した情報を使う."""
    spec = img.info.pop("note", None)
    if not spec:
        return
    text, font, fill, x, max_w, y_max = spec
    bbox = img.getchannel("A").getbbox()
    y = min((bbox[3] + 26) if bbox else y_max, y_max)
    d = ImageDraw.Draw(img)
    note_txt = "— " + _safe_for_font(font, text)
    cut = False
    while len(note_txt) > 4 and d.textlength(note_txt + "…", font=font) > max_w:
        note_txt = note_txt[:-1]
        cut = True
    d.text((x, y), note_txt + ("…" if cut else ""), font=font, fill=fill)


def _shadow(img: Image.Image) -> Image.Image:
    """透過カードに柔らかい影を付ける（文字を背景から浮かせる）."""
    from PIL import ImageFilter

    alpha = img.getchannel("A")
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(10)).point(lambda a: int(a * 0.75)))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (4, 6))
    out.alpha_composite(img)
    return out


def _save(img: Image.Image, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if img.mode == "RGBA":
        out = out.with_suffix(".png")
        _draw_note(img)
        span = img.info.pop("span", None)
        if span:
            img = _fit_h(img, *span)
        _shadow(_fit_glass(_vcenter(img))).save(out)
        return out
    img.save(out, quality=94)
    return out


def _text_block(d: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont,
                box: list[float] | tuple[float, float, float, float], fill,
                align: str = "center", spacing: float = 1.2) -> None:
    """箱の中に文字を上下左右とも中央で置く（字形の実寸で測るので、箱と文字の高さが合う）."""
    lines = [_safe_for_font(font, ln) for ln in lines if ln is not None]
    if not lines:
        return
    x0, y0, x1, y1 = box
    lh = int(font.size * spacing)
    # 字形の実寸（この字体の上端オフセットと高さ）
    bb = d.textbbox((0, 0), "".join(lines) or "国", font=font)
    glyph_h = bb[3] - bb[1]
    total = lh * (len(lines) - 1) + glyph_h
    y = y0 + ((y1 - y0) - total) / 2 - bb[1]
    for ln in lines:
        tw = d.textlength(ln, font=font)
        if align == "left":
            x = x0
        elif align == "right":
            x = x1 - tw
        else:
            x = x0 + ((x1 - x0) - tw) / 2
        d.text((x, y), ln, font=font, fill=fill)
        y += lh


def _center_text(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                 y: int, w: int, fill: str, stroke: int = 0, stroke_fill: str = "#000") -> int:
    """w は『使ってよい幅』。キャラがいるときは呼び出し側が content_width を渡す."""
    text = _safe_for_font(font, text)
    tw = d.textlength(text, font=font)
    d.text(((w - tw) / 2, y), text, font=font, fill=fill,
           stroke_width=stroke, stroke_fill=stroke_fill)
    return y + font.size + 18


def render_keyword_card(cfg: Config, keyword: str, sub: str, out: Path) -> Path:
    """キーワード1語をドンと置くカード。話題の切り替わりに使う."""
    img, d, pal, w, h = _card_base(cfg, card_style(cfg, "keyword"))
    f, lines = fit_text(cfg, d, keyword, "display_xl", min(w - 300, span_width(cfg)), 2, min_size=80)
    total = len(lines) * (f.size + 18) + (int(ts(cfg, "body_l") * 1.6) if sub else 0)
    # 字幕の帯（下 SUB_BAND px）には掛けない。その上の範囲で中央に
    y = TOP_BAND + (h - SUB_BAND - TOP_BAND - total) // 2
    # 左右のアクセント線
    d.rectangle([w // 2 - 260, y - 40, w // 2 + 260, y - 30], fill=pal["accent"])
    for line in lines:
        y = _center_text(d, line, f, y, w, pal["text"])
    if sub:
        f2, sl = fit_text(cfg, d, sub, "body_l", w - 300, 1, min_size=36)
        _center_text(d, sl[0], f2, y + 30, w, pal["accent2"])
    return _save(img, out)


def render_number_card(cfg: Config, value: str, label: str, note: str, out: Path) -> Path:
    """数字を主役にするカード。DATA テロップの内容を大きく見せる."""
    img, d, pal, w, h = _card_base(cfg, card_style(cfg, "number"))
    f_val, vl = fit_text(cfg, d, value, "numeral_xl", min(w - 240, span_width(cfg)), 1, min_size=96)
    f_lab, ll = fit_text(cfg, d, label, "title", w - 300, 1, min_size=36) if label else (None, [])
    f_note, nl = fit_text(cfg, d, note, "label", w - 300, 1, weight="bold", min_size=26) if note else (None, [])
    lab_h = int(f_lab.size * 1.4) if label else 0
    val_bb = d.textbbox((0, 0), vl[0], font=f_val)
    val_h = val_bb[3] - val_bb[1] + 24
    note_h = int(f_note.size * 1.6) if note else 0
    total = lab_h + val_h + note_h
    y = TOP_BAND + (h - SUB_BAND - TOP_BAND - total) // 2
    if label:
        _text_block(d, ll[:1], f_lab, (0, y, w, y + lab_h), pal["accent"])
    _text_block(d, vl[:1], f_val, (0, y + lab_h, w, y + lab_h + val_h), pal["positive"])
    if note:
        _text_block(d, nl[:1], f_note, (0, y + lab_h + val_h, w, y + total), "#9AA7BE")
    return _save(img, out)


def render_quote_card(cfg: Config, sentence: str, out: Path, source: str = "") -> Path:
    """体言止めの文字カード。要点を大きく、出典は小さく別行に（「文章」は出さない）.

    例) 実質賃金がマイナスの月が26か月連続 / — 毎月勤労統計調査（厚生労働省）
        連合「1990年代前半以来の水準」
    """
    img, d, pal, w, h = _card_base(cfg, card_style(cfg, "quote"))
    text = sentence.strip().rstrip("。")
    # 体言止めの短い句を、大きく・中央に・堂々と置く（見出しの縦線は付けない）。
    # まず 1 行に収まる大きさを探し（途中で折らない）、長ければ 2 行
    avail = min(w - 300, span_width(cfg))
    f, lines = fit_text(cfg, d, text, "display_l", avail, 1, min_size=96)
    if len(lines) > 1 or lines[-1].endswith("…"):
        f, lines = fit_text(cfg, d, text, "display_l", avail, 2, min_size=84)
    if len(lines) > 2 or lines[-1].endswith("…"):
        f, lines = fit_text(cfg, d, text, "display_s", avail, 2, min_size=66)
    lh = int(f.size * 1.25)
    total = len(lines) * lh + (int(ts(cfg, "label") * 2.0) if source else 0)
    y = TOP_BAND + (h - SUB_BAND - TOP_BAND - total) // 2
    for line in lines:
        y = _center_text(d, line, f, y, w, pal["text"]) - 18 + (lh - f.size)
    if source:
        f_src = load_font(cfg, ts(cfg, "label", 36), "bold")
        _center_text(d, "— " + _safe_for_font(f_src, source), f_src, y + 16, w, pal["text_secondary"])
    return _save(img, out)


def render_term_card(cfg: Config, term: str, meaning: str, example: str, out: Path) -> Path:
    """ビジネス用語カード。用語 → 一文の意味 → 数字つきの例."""
    img, d, pal, w, h = _card_base(cfg, card_style(cfg, "term"))
    # 見出しタグ
    f_tag = load_font(cfg, ts(cfg, "label", 40))
    d.rectangle([150, 150, 150 + 330, 150 + 64], fill=pal["accent2"])
    d.text((174, 158), "ビジネス用語", font=f_tag, fill="#101010")

    f_term, tl = fit_text(cfg, d, term, "display_l", w - 300, 1, min_size=64)
    d.text((150, 240), tl[0], font=f_term, fill=pal["text"])

    f_mean, ml = fit_text(cfg, d, meaning, "body_l", w - 340, 2)
    y = 250 + int(f_term.size * 1.35)
    for line in ml:
        d.text((170, y), line, font=f_mean, fill=pal["text"])
        y += int(f_mean.size * 1.36)

    if example:
        y += 30
        f_ex, el = fit_text(cfg, d, example, "body_m", w - 420, 3, weight="bold")
        eh = int(f_ex.size * 1.34)
        # 字幕の帯には掛けない（入らない行は落とす）
        while len(el) > 1 and y + eh * len(el) > h - SUB_BAND - 20:
            el = el[:-1]
        d.rectangle([170, y, 182, y + eh * len(el) - 10], fill=pal["positive"])
        for line in el:
            d.text((214, y), line, font=f_ex, fill="#CFE3D8")
            y += eh
    return _save(img, out)


def render_reference_card(cfg: Config, name: str, url: str, note: str, out: Path) -> Path:
    """出典・参考カード。記事のスクリーンショットの代わりに、こちらの様式で出す.

    他社サイトの画面をそのまま貼ると著作権の問題が出るので、
    見出し・媒体名・URL を自分の様式で組む。
    """
    img, d, pal, w, h = _card_base(cfg, card_style(cfg, "reference"))
    # 疑似ウィンドウ
    x0, y0, x1, y1 = 200, 200, w - 200, h - 220
    d.rounded_rectangle([x0, y0, x1, y1], radius=rad(cfg, "l"), fill=pal["surface_high"], outline=pal["outline"], width=3)
    d.rounded_rectangle([x0, y0, x1, y0 + 64], radius=rad(cfg, "l"), fill=pal["outline"])
    for i, c in enumerate(("#FF6B6B", "#FFC857", "#5BD99A")):
        d.ellipse([x0 + 28 + i * 34, y0 + 20, x0 + 52 + i * 34, y0 + 44], fill=c)
    f_url = load_font(cfg, ts(cfg, "label_s", 30), "bold")
    domain = re.sub(r"^https?://", "", url or "").split("/")[0]
    d.text((x0 + 150, y0 + 16), domain[:60], font=f_url, fill="#9AA7BE")

    f_tag = load_font(cfg, ts(cfg, "label", 36))
    d.text((x0 + 60, y0 + 110), "参考・出典", font=f_tag, fill=pal["accent"])
    f_name = load_font(cfg, ts(cfg, "display_s", 92))
    y = y0 + 170
    for line in _wrap(d, name, f_name, x1 - x0 - 120)[:2]:
        d.text((x0 + 60, y), line, font=f_name, fill=pal["text"])
        y += 112
    if note:
        f_note = load_font(cfg, ts(cfg, "body_m", 50), "bold")
        y += 20
        for line in _wrap(d, note, f_note, x1 - x0 - 120)[:3]:
            d.text((x0 + 60, y), line, font=f_note, fill="#CBD5E1")
            y += 66
    return _save(img, out)


def render_pattern_background(cfg: Config, seed: int, heading: str,
                              bullets: list[str], out: Path) -> Path:
    """写真が取れなかったときの幾何パターン背景。無地より画に変化が出る."""
    import random

    img, d, pal, w, h = _card_base(cfg)
    rnd = random.Random(seed)
    accent = _rgb(pal["accent"])
    # 薄い斜めライン or ドット
    if rnd.random() < 0.5:
        for x in range(-h, w, 90):
            d.line([(x, h), (x + h, 0)], fill=tuple(list(accent) + [0]) if False else
                   (accent[0] // 5 + 10, accent[1] // 5 + 18, accent[2] // 5 + 30), width=2)
    else:
        for x in range(60, w, 70):
            for y in range(60, h, 70):
                d.ellipse([x, y, x + 4, y + 4],
                          fill=(accent[0] // 4 + 12, accent[1] // 4 + 20, accent[2] // 4 + 34))
    # 大きな円を1つ置いて重心を作る
    cx, cy, r = rnd.randint(w // 2, w - 200), rnd.randint(150, h - 300), rnd.randint(220, 380)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=pal["accent"], width=6)
    _save(img, out)
    if heading:
        _overlay_heading(cfg, out, heading, bullets)
    return out


def fetch_ai_image(cfg: Config, prompt: str, out: Path, seed: int = 0) -> Path | None:
    """鍵不要の無料生成（pollinations.ai）。使えない環境では None を返す.

    ※ このプロジェクトの検証環境からは外部に出られないため、この経路は
      実機で未検証。失敗しても必ず None で返し、後段が別の絵に落とす。
    """
    provider = str(cfg.get("visuals.ai_image_provider", "") or "").lower()
    if provider != "pollinations" or not prompt:
        return None
    from . import bible
    # 画風はスタイルバイブルで一元管理（概念を映画のように。文字・ロゴは入れない）
    style = bible.image_style(cfg) or (
        "cinematic documentary photography, muted color grading, "
        "shallow depth of field, subtle film grain, no text, no letters")
    from urllib.parse import quote

    url = (f"https://image.pollinations.ai/prompt/{quote(prompt + ', ' + style)}"
           f"?width=1920&height=1080&nologo=true&seed={seed}")
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image/"):
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        Image.open(out).verify()
        return out
    except Exception as exc:
        log.warning("AI画像の生成に失敗（別の絵で続けます）: %s", str(exc)[:120])
        return None


def build_photo_scene(cfg: Config, query: str, ai_prompt: str, heading: str,
                      bullets: list[str], seed: int, out: Path,
                      allow_pattern: bool = True) -> tuple[Path | None, str]:
    """写真系の1シーンを作る。取れた手段を kind として返す（still 判定に使う）.

    順に試す: Pexels の写真 → AI 生成画像 → 幾何パターン背景
    allow_pattern=False のときは、写真も AI も取れなければ (None, "") を返す
    """
    raw = fetch_stock(cfg, query, out.parent / f"_raw_{out.stem}.jpg")
    if raw:
        _prepare_photo(cfg, raw, out)
        _overlay_heading(cfg, out, heading, bullets)
        raw.unlink(missing_ok=True)
        return out, "photo"
    ai = fetch_ai_image(cfg, ai_prompt or query, out.parent / f"_ai_{out.stem}.png", seed)
    if ai:
        _prepare_photo(cfg, ai, out)
        _overlay_heading(cfg, out, heading, bullets)
        ai.unlink(missing_ok=True)
        return out, "photo"
    if not allow_pattern:
        return None, ""
    render_pattern_background(cfg, seed, heading, bullets, out)
    return out, "pattern"


def render_heading_overlay(cfg: Config, heading: str, bullets: list[str], out: Path) -> Path:
    """動く背景の上に載せる、見出し＋短い箇条書きだけの透過レイヤー.

    写真に焼き込む _overlay_heading の透過版。背景は動画側が担当する。
    """
    from . import design

    # 図解と同じ見た目（黒い縁取りは付けない。すりガラスの面を中身の大きさで敷く）
    img, d, pal, cw, h = _card_base(cfg, card_style(cfg, "chart"))
    m = design.safe_margin(cfg)
    f_head, lines = fit_text(cfg, d, heading, "headline_m", cw - m * 2 - 60, 2) if heading else (load_font(cfg, 68), [])
    lh = int(f_head.size * 1.3)
    bullets = [b for b in (bullets or []) if b][:2]
    f_b = load_font(cfg, ts(cfg, "body_m", 44))
    bh = int(f_b.size * 1.45)
    total = lh * len(lines) + (bh * len(bullets) + 10 if bullets else 0)
    y = min(h - SUB_BAND - 80 - total, h - 360 - total)
    y = max(y, TOP_BAND)
    if lines:
        _accent_bar(d, m, y, lines[0], f_head, pal["accent"], lines=len(lines), lh=lh)
    for line in lines:
        d.text((m + 36, y), line, font=f_head, fill=pal["text"])
        y += lh
    y += 10 if bullets else 0
    for b in bullets:
        d.text((m + 36, y), "・" + _safe_for_font(f_b, b), font=f_b, fill=pal["text_secondary"])
        y += bh
    return _save(img, out)


# ----------------------------------------------------------------------
# 図解（flow / compare / steps / balance / table）
# 「タイトルだけ出て言葉で説明される」を無くすための絵。すりガラスの面に描く
# ----------------------------------------------------------------------
SUB_BAND = 230   # 画面下の字幕帯の高さ（ここには図解の中身も出典も置かない。字幕 110px + 余白）
TOP_BAND = 110   # 画面上の余白（ここより上には置かない）


def _accent_bar(d, x: int, y: int, sample: str, font, fill, lines: int = 1, lh: int | None = None) -> None:
    """見出しの左に置く縦線。字形の実寸（上端〜下端）に合わせるので、文字と上下が揃う."""
    bb = d.textbbox((x + 30, y), _safe_for_font(font, sample) or "国", font=font)
    top, bottom = bb[1], bb[3]
    if lines > 1 and lh:
        bottom += lh * (lines - 1)
    d.rectangle([x, top, x + 10, bottom], fill=fill)


def _diagram_base(cfg: Config, title: str, note: str):
    from . import design

    img, d, pal, cw, h = _card_base(cfg, card_style(cfg, "chart"))
    m = design.safe_margin(cfg)
    try:
        x0, x1 = content_span(cfg)
        if x0 > 0:                      # 掛け合い: 2 人のあいだに最初から収める
            m = x0 + GLASS_PAD[0] + 24
            cw = x1 - GLASS_PAD[0] - 24 + m
    except Exception:
        pass
    y = TOP_BAND + 20
    if title:
        f_t, tl = fit_text(cfg, d, title, "headline_m", cw - m * 2 - 60, 1, min_size=44)
        _accent_bar(d, m, y, tl[0], f_t, pal["accent"])
        d.text((m + 30, y), tl[0], font=f_t, fill=pal["text"])
        y += int(f_t.size * 1.6)
    if note:
        # 出典は中身のすぐ下に出す（_save の時点で中身の下端が分かる）。字幕帯には入れない
        f_n = load_font(cfg, ts(cfg, "label_s", 30), "bold")
        img.info["note"] = (note, f_n, pal["text_secondary"], m + 30, cw - m * 2 - 60, h - SUB_BAND - 44)
    img.info["vcenter"] = (TOP_BAND, h - SUB_BAND)   # 見出し＋図をまとめて上下中央に
    # 中身は字幕の帯（下 SUB_BAND px）と出典行より上に収める → 呼び出し側は h - SUB_BAND - 60 を下限に使う
    return img, d, pal, cw, h - SUB_BAND + 128 - 60, m, y


def _rounded(d, box, fill, outline=None, r=16, width=3):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def _mix(a: str, b: str, t: float) -> str:
    """2 色を混ぜる（t=0 で a、1 で b）."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return "#%02X%02X%02X" % (int(ra + (rb - ra) * t), int(ga + (gb - ga) * t), int(ba + (bb - ba) * t))


def _row_style(pal: dict[str, str], i: int, active: int | None,
               fill: str | None = None, text: str | None = None) -> tuple[str, str, int, str]:
    """行・箱の (塗り, 枠色, 枠の太さ, 文字色)。active の行は明るく縁取り、それ以外は少し沈める.

    「いま話している行」を目立たせるためのもの。active が None なら通常表示。
    """
    fill = fill or pal["surface_high"]
    text = text or pal["text"]
    if active is None or i != active:
        return fill, pal["outline"], 3, text
    return _mix(fill, pal["accent"], 0.22), pal["accent"], 6, text


def _marker(d, x: int, cy: int, color: str) -> None:
    """行の左に置く ▶ 印（いま話している行）."""
    d.polygon([(x, cy - 16), (x, cy + 16), (x + 22, cy)], fill=color)


def _reserve_marker(d, x: int, cy: int) -> None:
    """▶ 印のぶんの場所を、印が無い行にも確保する（ほぼ透明な点を打つ）.

    すりガラスの面は中身の外接矩形に合わせて敷くので、これが無いと
    ハイライトの有無で面の大きさが変わってしまう。
    """
    d.rectangle([x, cy - 16, x + 22, cy + 16], fill=(0, 0, 0, 1))


def render_flow(cfg: Config, title: str, items: list[str], note: str, out: Path,
                active: int | None = None) -> Path:
    """A → B → C。因果・順番を箱と矢印で."""
    from . import design

    img, d, pal, cw, h, m, y0 = _diagram_base(cfg, title, note)
    items = [x for x in items if x][:4] or ["…"]
    n = len(items)
    gap = 56
    box_w = int((cw - m * 2 - gap * (n - 1)) / n)
    box_h = 220
    y = y0 + 30
    x = m
    r = design.radius(cfg, "m")
    for i, txt in enumerate(items):
        base_fill = pal["accent"] if i == n - 1 else pal["surface_high"]
        base_color = "#0B1120" if i == n - 1 else pal["text"]
        fill, outline, width, color = _row_style(pal, i, active, base_fill, base_color)
        if i == active:
            outline = pal["accent2"] if i == n - 1 else pal["accent"]
        _rounded(d, [x, y, x + box_w, y + box_h], fill, outline, r, width)
        f, lines = fit_text(cfg, d, txt, "title", box_w - 40, 2, min_size=34)
        _text_block(d, lines, f, (x, y, x + box_w, y + box_h), color, spacing=1.25)
        if i < n - 1:
            ax = x + box_w + 8
            cy = y + box_h // 2
            d.line([(ax, cy), (ax + gap - 16, cy)], fill=pal["accent2"], width=8)
            d.polygon([(ax + gap - 16, cy - 16), (ax + gap - 16, cy + 16), (ax + gap - 2, cy)], fill=pal["accent2"])
        x += box_w + gap
    return _save(img, out)


def render_compare(cfg: Config, title: str, items: list[str], note: str, out: Path,
                   active: int | None = None) -> Path:
    """A vs B。行ごとに 見出し | 左 | 右."""
    from . import design

    left_name, right_name = "", ""
    if title and ("vs" in title.lower() or "対" in title or "と" in title):
        parts = re.split(r"\s*(?:vs\.?|VS|対|と)\s*", title, maxsplit=1)
        if len(parts) == 2 and all(parts):
            left_name, right_name = parts[0].strip(), parts[1].strip()
    # 「A vs B」の形でなければ、見出しとして出して列の名札（A/B）は付けない
    img, d, pal, cw, h, m, y0 = _diagram_base(cfg, "" if left_name else title, note)
    rows = []
    for it in items[:4]:
        cells = [c.strip() for c in it.split("|")]
        if len(cells) == 3:
            rows.append(cells)
        elif len(cells) == 2:
            rows.append(["", cells[0], cells[1]])
    if not rows:
        rows = [["", title, ""]]
    label_w = min(300, int((cw - m * 2) * 0.22))
    col_w = (cw - m * 2 - label_w - 40) // 2
    y = y0 + 10
    head_h = 96
    r = design.radius(cfg, "m")
    if left_name:
        for k, (name, fill) in enumerate(((left_name, pal["accent"]), (right_name, pal["accent2"]))):
            x = m + label_w + 20 + k * (col_w + 20)
            _rounded(d, [x, y, x + col_w, y + head_h], fill, None, r)
            fh, hl = fit_text(cfg, d, name, "title", col_w - 30, 1, min_size=36)
            _text_block(d, hl[:1], fh, (x, y, x + col_w, y + head_h), "#0B1120")
        y += head_h + 20
    row_h = min(150, (h - 128 - y - 20) // max(len(rows), 1))
    for i, (label, lval, rval) in enumerate(rows):
        fill, outline, width, color = _row_style(pal, i, active)
        _rounded(d, [m, y, cw - m, y + row_h - 14], fill, outline, r, width)
        _reserve_marker(d, m - 34, y + (row_h - 14) // 2)
        if i == active:
            _marker(d, m - 34, y + (row_h - 14) // 2, pal["accent"])
        if label:
            fl, ll = fit_text(cfg, d, label, "body_m", label_w - 30, 1, min_size=30, weight="bold")
            _text_block(d, ll[:1], fl, (m + 24, y, m + label_w, y + row_h - 14),
                        pal["text"] if i == active else pal["text_secondary"], align="left")
        for k, val in enumerate((lval, rval)):
            x = m + label_w + 20 + k * (col_w + 20)
            fv, vl = fit_text(cfg, d, val, "title", col_w - 30, 2, min_size=32)
            _text_block(d, vl, fv, (x, y, x + col_w, y + row_h - 14), color)
        y += row_h
    return _save(img, out)


def render_steps(cfg: Config, title: str, items: list[str], note: str, out: Path,
                 active: int | None = None) -> Path:
    """番号つきの手順・条件."""
    from . import design

    img, d, pal, cw, h, m, y0 = _diagram_base(cfg, title, note)
    items = [x for x in items if x][:4] or ["…"]
    avail = h - 128 - y0 - 20
    row_h = min(170, avail // len(items))
    y = y0 + 10
    r = design.radius(cfg, "m")
    f_num = load_font(cfg, ts(cfg, "headline_m", 66), "body")
    for i, txt in enumerate(items):
        fill, outline, width, color = _row_style(pal, i, active)
        _rounded(d, [m, y, cw - m, y + row_h - 16], fill, outline, r, width)
        _reserve_marker(d, m - 34, y + (row_h - 16) // 2)
        if i == active:
            _marker(d, m - 34, y + (row_h - 16) // 2, pal["accent"])
        cx = m + 70
        dot = pal["accent"]
        d.ellipse([cx - 44, y + (row_h - 16) // 2 - 44, cx + 44, y + (row_h - 16) // 2 + 44], fill=dot)
        _text_block(d, [str(i + 1)], f_num, (cx - 44, y + (row_h - 16) // 2 - 44, cx + 44, y + (row_h - 16) // 2 + 44),
                    "#0B1120")
        f, lines = fit_text(cfg, d, txt, "title", cw - m * 2 - 190, 2, min_size=34)
        _text_block(d, lines, f, (m + 150, y, cw - m - 30, y + row_h - 16), color, align="left")
        y += row_h
    return _save(img, out)


def render_balance(cfg: Config, title: str, items: list[str], note: str, out: Path,
                   active: int | None = None) -> Path:
    """数字の差し引き: A − B = 結果（最後の要素が結果）."""
    from . import design

    img, d, pal, cw, h, m, y0 = _diagram_base(cfg, title, note)
    items = [x for x in items if x][:3]
    while len(items) < 3:
        items.append("…")
    a, b, res = items
    box_w = (cw - m * 2 - 200) // 3
    box_h = 230
    y = y0 + 30
    r = design.radius(cfg, "m")
    f_op = load_font(cfg, ts(cfg, "display_s", 84), "body")
    x = m
    for i, (txt, base_fill, base_color) in enumerate(((a, pal["surface_high"], pal["text"]),
                                                      (b, pal["surface_high"], pal["text"]),
                                                      (res, pal["accent"], "#0B1120"))):
        fill, outline, width, color = _row_style(pal, i, active, base_fill, base_color)
        if i == active:
            outline = pal["accent2"] if i == 2 else pal["accent"]
        _rounded(d, [x, y, x + box_w, y + box_h], fill, outline, r, width)
        # 「名目賃金 +5.1%」→ 上に名前、下に数字
        parts = txt.rsplit(" ", 1) if " " in txt else [txt]
        if len(parts) == 2:
            fl, ll = fit_text(cfg, d, parts[0], "body_m", box_w - 30, 1, min_size=30, weight="bold")
            _text_block(d, ll[:1], fl, (x, y + 20, x + box_w, y + 20 + int(fl.size * 1.4)),
                        color if i == 2 else pal["text_secondary"])
            fv, vl = fit_text(cfg, d, parts[1], "display_s", box_w - 30, 1, min_size=48)
            if len(vl) > 1 or vl[-1].endswith("…"):
                fv, vl = fit_text(cfg, d, parts[1], "title", box_w - 30, 2, min_size=34)
            _text_block(d, vl[:2], fv, (x, y + 20 + int(fl.size * 1.4), x + box_w, y + box_h - 16),
                        color if i == 2 else pal["positive"], spacing=1.15)
        else:
            fv, vl = fit_text(cfg, d, txt, "title", box_w - 30, 2, min_size=34)
            _text_block(d, vl, fv, (x, y, x + box_w, y + box_h), color)
        if i < 2:
            op = "−" if i == 0 else "＝"
            _text_block(d, [op], f_op, (x + box_w, y, x + box_w + 100, y + box_h), pal["accent2"])
        x += box_w + 100
    return _save(img, out)


def render_table(cfg: Config, title: str, items: list[str], note: str, out: Path,
                 active: int | None = None) -> Path:
    """2 列の表（項目 | 値）."""
    from . import design

    img, d, pal, cw, h, m, y0 = _diagram_base(cfg, title, note)
    rows = [[c.strip() for c in it.split("|", 1)] for it in items[:5] if it]
    rows = [r if len(r) == 2 else [r[0], ""] for r in rows] or [["…", ""]]
    avail = h - 128 - y0 - 20
    row_h = min(120, avail // len(rows))
    y = y0 + 10
    r = design.radius(cfg, "s")
    for i, (k, v) in enumerate(rows):
        base = pal["surface_high"] if i % 2 == 0 else pal["surface"]
        fill, outline, width, color = _row_style(pal, i, active, base)
        _rounded(d, [m, y, cw - m, y + row_h - 10], fill, outline if i == active else None, r, width)
        _reserve_marker(d, m - 34, y + (row_h - 10) // 2)
        if i == active:
            _marker(d, m - 34, y + (row_h - 10) // 2, pal["accent"])
        fk, kl = fit_text(cfg, d, k, "title", (cw - m * 2) * 0.55, 1, min_size=34)
        _text_block(d, kl[:1], fk, (m + 36, y, cw - m, y + row_h - 10), color, align="left")
        fv, vl = fit_text(cfg, d, v, "title", (cw - m * 2) * 0.35, 1, min_size=34)
        _text_block(d, vl[:1], fv, (m, y, cw - m - 36, y + row_h - 10), pal["positive"], align="right")
        y += row_h
    return _save(img, out)


def render_diagram(cfg: Config, kind: str, title: str, items: list[str], note: str, out: Path,
                   active: int | None = None) -> Path:
    """active は「いま話している行」の番号（0 始まり）。None なら全行ふつうに描く."""
    fn = {"flow": render_flow, "compare": render_compare, "steps": render_steps,
          "balance": render_balance, "table": render_table}.get(kind, render_steps)
    return fn(cfg, title, items, note, out, active=active)


def row_fragments(cells: list[str]) -> list[str]:
    """照合に使う語の候補。セルそのものに加え、「・」「：」「＝」などで区切った断片（3 字以上）も含める.

    「レンズ1・人の感じ方のクセ」→ ["レンズ1・人の感じ方のクセ", "レンズ1", "人の感じ方のクセ"]
    """
    out: list[str] = []
    for c in cells:
        c = str(c).strip()
        if not c:
            continue
        out.append(c)
        for frag in re.split(r"[・：:＝=／/（）()「」【】、,]", c):
            frag = frag.strip()
            if len(frag) >= 3 and frag not in out:
                out.append(frag)
    return out


def diagram_row_texts(kind: str, items: list[str]) -> list[list[str]]:
    """行ごとの「言葉」（ナレーションと照合する語の候補）."""
    items = [x for x in items if x]
    if kind == "balance":
        items = (items + ["…", "…", "…"])[:3]
        return [row_fragments(it.rsplit(" ", 1)) for it in items]
    if kind == "compare":
        rows = [[c.strip() for c in it.split("|")] for it in items[:4]]
        rows = [r for r in rows if len(r) in (2, 3)] or [[items[0] if items else "…"]]
        return [row_fragments(r) for r in rows]
    if kind == "table":
        return [row_fragments([c.strip() for c in it.split("|", 1)]) for it in items[:5]] or [["…"]]
    return [row_fragments([it]) for it in items[:4]] or [["…"]]


def diagram_rows(kind: str, items: list[str]) -> int:
    """図解の行（箱）の数。ハイライトを順に当てる回数になる."""
    items = [x for x in items if x]
    if kind == "balance":
        return 3
    if kind == "compare":
        return len([it for it in items[:4] if len([c for c in it.split("|")]) in (2, 3)]) or 1
    if kind == "table":
        return min(len(items), 5) or 1
    return min(len(items), 4) or 1
