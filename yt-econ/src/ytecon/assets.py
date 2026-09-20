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
}

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


class AssetError(RuntimeError):
    pass


def font_path(cfg: Config, weight: str = "black") -> str:
    """使える日本語フォントのパスを返す.

    既定は black（900）。動画のテロップは遠目でも読める太さが要る。
    無ければ bold に落ちる。
    """
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
          max_width: int, _balance_ok: bool = True) -> list[str]:
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
    # 2 行に収まる長さなら、割る位置を総当たりで選ぶ（「価値／が下がる」「逆算す／る」を避ける）
    if _balance_ok and len(lines) == 2:
        best = _best_two_line_split(draw, text, font, max_width)
        if best:
            return best
    return lines


_PARTICLE_CHARS = set("がをにはでともへやのか")
_BREAK_BONUS = set(" 　、。，・／→はがをにでともへのか")


def _best_two_line_split(draw, text: str, font, max_width: int) -> list[str] | None:
    """2 行の割り位置を点数で選ぶ: 幅に収まる／行頭に助詞・句読点を置かない／長さが釣り合う."""
    n = len(text)
    best, best_score = None, -1e9
    for i in range(2, n - 1):
        a, b = text[:i].rstrip(" 　"), text[i:].lstrip(" 　")
        if not a or not b:
            continue
        if draw.textlength(a, font=font) > max_width or draw.textlength(b, font=font) > max_width:
            continue
        if b[0] in _NO_LINE_START or b[0] in _PARTICLE_CHARS:
            continue
        score = -abs(len(a) - len(b)) * 2.0
        prev, nxt = text[i - 1], text[i]
        # 助詞の直後は切りやすい。ただし次がひらがな（「上が｜らない」）なら語の途中の可能性が高い
        if prev in _PARTICLE_CHARS and not ("ぁ" <= nxt <= "ん"):
            score += 5
        if prev in " 　、。，・／→":
            score += 9
        if score > best_score:
            best, best_score = [a, b], score
    return best


_NO_LINE_START = set("、。，．・：；？！?!」』）〕］｝〉》〟ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶー～")
_BREAK_AFTER = (" ", "　", "、", "。", "，", "・", "／", "→")


def fit_text(cfg: Config, d: ImageDraw.ImageDraw, text: str, role: str, max_width: int,
             max_lines: int, weight: str = "black", min_size: int = 28
             ) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """文字が枠に収まるまでフォントを小さくして、(フォント, 行) を返す.

    まず役割サイズで折り返し、行数が上限を超えたら 8% ずつ縮める。
    それでも入らなければ最終行を「…」で切る（枠からはみ出させない）。
    """
    size = ts(cfg, role)
    while True:
        font = load_font(cfg, size, weight)
        lines = _wrap(d, text, font, max_width)
        if len(lines) <= max_lines or size <= min_size:
            break
        size = max(min_size, int(size * 0.92))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and d.textlength(lines[-1] + "…", font=font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return font, lines


# ----------------------------------------------------------------------
# textcard
# ----------------------------------------------------------------------
def render_textcard(cfg: Config, heading: str, bullets: list[str],
                    out: Path) -> Path:
    img, d, pal, _cw, h = _card_base(cfg, card_style(cfg, "bullets"))
    w = img.width

    # 見出し
    cw = _cw
    f_head, head_lines = fit_text(cfg, d, heading, "headline_l", cw - 320, 2)
    y = 200
    lh = int(f_head.size * 1.28)
    d.rectangle([150, y - 24, 150 + 10, y + lh * len(head_lines) - 24], fill=pal["accent"])
    for line in head_lines:
        d.text((196, y), line, font=f_head, fill=pal["text"])
        y += lh

    # 箇条書き（4項目 × 最大2行が枠に収まるよう、本文は少し小さくしてもよい）
    y = max(y + 60, 440)
    bottom = h - 120
    for bullet in bullets[:4]:
        f_body, blines = fit_text(cfg, d, bullet, "body_l", cw - 480, 2)
        bh = int(f_body.size * 1.3)
        if y + bh * len(blines) > bottom:
            break
        d.ellipse([200, y + 20, 222, y + 42], fill=pal["accent2"])
        for line in blines:
            d.text((256, y), line, font=f_body, fill=pal["text"])
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
    for i, line in enumerate(["毎日 朝と夜に更新", "チャンネル登録で見逃しなく"]):
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
GLASS_KINDS = ("bullets", "term", "reference", "chart", "outro")
CLEAR_KINDS = ("keyword", "number", "quote", "title")


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
    # 背景を少し落として文字を立たせる（動画側にフィルタを掛けずに済む）
    d.rectangle([0, 0, w, h], fill=(0, 0, 0, int(255 * float(cfg.get("visuals.backdrop_dim", 0.30)))))
    if style == "glass":
        m = design.safe_margin(cfg) - 24
        cw = content_width(cfg)
        r, g, b = _rgb(pal["surface"])
        orr, og, ob = _rgb(pal["outline"])
        d.rounded_rectangle([m, 64, cw - 8, h - 64], radius=design.radius(cfg, "l"),
                            fill=(r, g, b, int(255 * 0.80)), outline=(orr, og, ob, 160),
                            width=max(design.stroke(cfg, "card"), 2))
    return img, d, pal, content_width(cfg), h


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
        _shadow(img).save(out)
        return out
    img.save(out, quality=94)
    return out


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
    f, lines = fit_text(cfg, d, keyword, "display_xl", w - 300, 2, min_size=80)
    total = len(lines) * (f.size + 18)
    y = (h - total) // 2 - (50 if sub else 0)
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
    f_val, vl = fit_text(cfg, d, value, "numeral_xl", w - 240, 1, min_size=96)
    f_lab, ll = fit_text(cfg, d, label, "title", w - 300, 1, min_size=36)
    f_note, nl = fit_text(cfg, d, note, "label", w - 300, 1, weight="bold", min_size=26) if note else (None, [])
    y = h // 2 - 200
    y = _center_text(d, ll[0], f_lab, y, w, pal["accent"])
    y = _center_text(d, vl[0], f_val, y + 10, w, pal["positive"])
    if note:
        _center_text(d, nl[0], f_note, y + 20, w, "#9AA7BE")
    return _save(img, out)


def render_quote_card(cfg: Config, sentence: str, out: Path) -> Path:
    """いま読み上げている一文をそのまま大きく出す。「文字で分かりやすく」の主力."""
    img, d, pal, w, h = _card_base(cfg, card_style(cfg, "quote"))
    f, lines = fit_text(cfg, d, sentence, "headline_l", w - 360, 3, min_size=56)
    total = len(lines) * (f.size + 24)
    y = (h - total) // 2
    # 引用符
    fq = load_font(cfg, 160)
    d.text((120, y - 120), "“", font=fq, fill=pal["accent"])
    for line in lines:
        d.text((200, y), line, font=f, fill=pal["text"])
        y += f.size + 24
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

    img, d, pal, cw, h = _card_base(cfg, "clear")
    m = design.safe_margin(cfg)
    f_head, lines = fit_text(cfg, d, heading, "headline_m", cw - m * 2, 2) if heading else (load_font(cfg, 68), [])
    y = h - 300 - 96 * len(lines) - (len(bullets[:2]) * 64 if bullets else 0)
    y = max(y, 120)
    if lines:
        d.rectangle([m, y - 8, m + 10, y + 96 * len(lines) - 24], fill=pal["accent"])
    for line in lines:
        d.text((m + 36, y), line, font=f_head, fill=pal["text"],
               stroke_width=design.stroke(cfg, "text_outline"), stroke_fill="#06090F")
        y += 96
    f_b = load_font(cfg, ts(cfg, "body_m", 44))
    for b in (bullets or [])[:2]:
        d.text((m + 36, y + 10), "・" + b, font=f_b, fill=pal["text_secondary"],
               stroke_width=3, stroke_fill="#06090F")
        y += 64
    return _save(img, out)
