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
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .script import Section, VideoScript

log = logging.getLogger(__name__)

# 日本語フォントの探索順（assets/fonts に置いたものを最優先）
_FONT_CANDIDATES = [
    "assets/fonts/NotoSansJP-Bold.otf",
    "assets/fonts/NotoSansJP-Bold.ttf",
    "assets/fonts/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/YuGothB.ttc",
]

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


class AssetError(RuntimeError):
    pass


def font_path(cfg: Config) -> str:
    """使える日本語フォントのパスを返す."""
    for cand in _FONT_CANDIDATES:
        p = Path(cand)
        if not p.is_absolute():
            p = cfg.root / cand
        if p.exists():
            return str(p)
    raise AssetError(
        "日本語フォントが見つかりません。\n"
        "  scripts/install_fonts.sh を実行するか、\n"
        "  assets/fonts/NotoSansJP-Bold.otf を配置してください。"
    )


def load_font(cfg: Config, size: int) -> ImageFont.FreeTypeFont:
    key = (font_path(cfg), size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(key[0], size)
    return _font_cache[key]


def palette(cfg: Config) -> dict[str, str]:
    default = {
        "bg": "#0E1525", "surface": "#18223A", "text": "#F2F5FA",
        "accent": "#4CC2FF", "accent2": "#FFC857",
        "positive": "#5BD99A", "negative": "#FF6B6B",
    }
    default.update(cfg.get("visuals.palette", {}) or {})
    return default


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
          max_width: int) -> list[str]:
    """日本語は単語境界がないので1文字ずつ詰めて折り返す."""
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        if draw.textlength(trial, font=font) > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


# ----------------------------------------------------------------------
# textcard
# ----------------------------------------------------------------------
def render_textcard(cfg: Config, heading: str, bullets: list[str],
                    out: Path) -> Path:
    pal = palette(cfg)
    w, h = cfg.get("video.resolution", [1920, 1080])
    img = gradient((w, h), pal["bg"], pal["surface"])
    d = ImageDraw.Draw(img)

    # 見出し
    f_head = load_font(cfg, 76)
    head_lines = _wrap(d, heading, f_head, w - 320)[:2]
    y = 230
    d.rectangle([150, y - 24, 150 + 10, y + 96 * len(head_lines) - 24], fill=pal["accent"])
    for line in head_lines:
        d.text((196, y), line, font=f_head, fill=pal["text"])
        y += 96

    # 箇条書き
    f_body = load_font(cfg, 54)
    y = max(y + 70, 480)
    for bullet in bullets[:4]:
        d.ellipse([200, y + 20, 222, y + 42], fill=pal["accent2"])
        for i, line in enumerate(_wrap(d, bullet, f_body, w - 480)[:2]):
            d.text((256, y), line, font=f_body, fill=pal["text"])
            y += 68
        y += 26

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return out


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
                 color=pal["text"], pad=30)

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
                xytext=(0, 14), textcoords="offset points",
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

    note = spec.get("note") or ""
    if note:
        # 左下ギリギリに置くと、後段のズームや YouTube の UI で隠れるため内側に寄せる
        fig.text(0.08, 0.085, note, fontproperties=fp, fontsize=22, color="#9AA7BE")

    # 上下左右に安全余白を取る。下 1/4 は字幕が乗るので特に広く空ける
    fig.subplots_adjust(left=0.12, right=0.94, top=0.84, bottom=0.32)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=pal["bg"])
    plt.close(fig)
    return out


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
    f_head = load_font(cfg, 68)
    lines = _wrap(d, heading, f_head, img.width - 360)[:2]
    y = 170
    d.rectangle([150, y - 16, 160, y + 88 * len(lines) - 16], fill=pal["accent"])
    for line in lines:
        d.text((196, y), line, font=f_head, fill=pal["text"],
               stroke_width=3, stroke_fill="#00000099")
        y += 88
    f_b = load_font(cfg, 44)
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
    out.parent.mkdir(parents=True, exist_ok=True)
    pal = palette(cfg)
    w, h = cfg.get("video.resolution", [1920, 1080])
    img = gradient((w, h), pal["surface"], pal["bg"])
    d = ImageDraw.Draw(img)
    f = load_font(cfg, 92)
    lines = _wrap(d, title, f, w - 300)[:3]
    total = len(lines) * 120
    y = (h - total) // 2
    for line in lines:
        tw = d.textlength(line, font=f)
        d.text(((w - tw) / 2, y), line, font=f, fill=pal["text"])
        y += 120
    f_small = load_font(cfg, 40)
    name = cfg.get("channel.name", "")
    tw = d.textlength(name, font=f_small)
    d.text(((w - tw) / 2, y + 40), name, font=f_small, fill=pal["accent"])
    img.save(out, quality=95)
    return out


def build_outro_card(cfg: Config, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    pal = palette(cfg)
    w, h = cfg.get("video.resolution", [1920, 1080])
    img = gradient((w, h), pal["bg"], pal["surface"])
    d = ImageDraw.Draw(img)
    f = load_font(cfg, 78)
    for i, line in enumerate(["毎日 朝と夜に更新", "チャンネル登録で見逃しなく"]):
        tw = d.textlength(line, font=f)
        d.text(((w - tw) / 2, h / 2 - 110 + i * 120), line, font=f,
               fill=pal["text"] if i == 0 else pal["accent"])
    img.save(out, quality=95)
    return out


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
