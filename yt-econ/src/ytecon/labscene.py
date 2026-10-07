"""宇宙の解析室（lab_style: space）で、ガラスの板を使わず空間にじかに置く場面: 冒頭・章の扉・データ・検証結果.

どれも地は透明（後ろに space.background_loop の動く背景が来る）。右はめたんが立つので、中身は x=80〜1380 に収める。
  opening  今日の問い（動画全体の軸）を大きく
  chapter  「検証 02」と見出し。下に 4 つの検証の道のり（済んだものは判定つき）。右の宙には背景の 4D テッセラクト
  data     床に立つ光る角柱で数字を比べる。2 本なら「約 N 倍」を自動で出す
  verdict  主張・判定メーター（ウソ ← 半分本当 → 本当）の針・スタンプ・理由
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageFont

from . import space
from .config import Config
from .quiz import Parts, Scene, Theme

X0, X1 = 90, 1380


def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


_NUM: dict[tuple[int, int], ImageFont.FreeTypeFont] = {}


def num_font(cfg: Config, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """数字の字体（Oxanium。0 に斜線が無い）。無ければ本文の字体."""
    key = (size, weight)
    if key not in _NUM:
        p = cfg.root / "assets" / "fonts" / "Oxanium[wght].ttf"
        if p.exists():
            f = ImageFont.truetype(str(p), size)
            try:
                f.set_variation_by_axes([weight])
            except Exception:
                pass
        else:
            from .quiz import font
            f = font(cfg, size, 700)
        _NUM[key] = f
    return _NUM[key]


def _en(cfg: Config, size: int, weight: int = 500):
    from .quiz import font_en
    return font_en(cfg, size, weight)


def _hl_line(d, P: Parts, x: float, y: float, text: str, hl: str, f, color, hl_color) -> float:
    """hl の語だけ色を変えて 1 行を描く。描いた幅を返す."""
    cx = x
    for t, is_hl in (P.split_hl(text, hl) if hl and hl in text else [(text, False)]):
        d.text((cx, y), t, font=f, fill=hl_color if is_hl else color)
        cx += d.textlength(t, font=f)
    return cx - x


def build(cfg: Config, th: Theme, sc: dict[str, Any]) -> Scene:
    P = Parts(cfg, th)
    s = Scene(cfg, th)
    kind = sc.get("kind")
    acc, text, sub = _rgb(th.blue), _rgb(th.text), _rgb(th.sec)
    red, green = _rgb(th.red), _rgb(th.green)
    gold = (255, 200, 87)

    if kind == "opening":
        q = str(sc.get("question") or sc.get("theme") or "")
        lines = [str(x) for x in (sc.get("lines") or [])][:2]

        def head(d, p):
            d.text((X0, 250), "TODAY'S QUESTION", font=_en(cfg, 26, 600), fill=acc)
            d.text((X0 + 330, 248), "今日の問い", font=P.f(30, 700), fill=sub)
            f, ls = P.fit(d, q, 900, 96, 600, min_size=56)           # 右の宙は 4D のテッセラクト
            y = 310
            for ln in ls[:3]:
                d.text((X0, y), ln, font=f, fill=text)
                y += int(f.size * 1.3)
        s.add(0, head)
        if lines:
            s.add(1, lambda d, p: d.text((X0, 690), lines[0], font=P.f(46, 700), fill=sub))
        if len(lines) > 1:
            s.add(2, lambda d, p: d.text((X0, 760), lines[1], font=P.f(40, 700), fill=sub))

    elif kind == "chapter":
        label = str(sc.get("label") or "")
        heading, hl = str(sc.get("heading") or ""), str(sc.get("heading_hl") or "")
        subt = str(sc.get("sub") or "")
        road = [r for r in (sc.get("roadmap") or []) if isinstance(r, dict)]
        idx = int(sc.get("index") or 0)
        tw = 860                                   # 右寄りの宙に 4D のテッセラクト（背景）が浮かぶので、見出しは左に

        def head(d, p):
            d.text((X0, 190), label, font=P.f(40, 700), fill=acc)
            d.line([(X0, 250), (X0 + 420, 250)], fill=acc + (200,), width=2)
            f, ls = P.fit(d, heading, tw, 96, 600, min_size=60)
            y = 275
            for ln in ls[:2]:
                _hl_line(d, P, X0, y, ln, hl, f, text, acc)
                y += int(f.size * 1.28)
        s.add(0, head)
        if subt:
            s.add(1, lambda d, p: d.text((X0, 545), subt, font=P.f(42, 700), fill=sub))
        if road:
            def roadmap(d, p):
                y0 = 640
                d.text((X0, y0 - 6), "今日の検証", font=P.f(26, 700), fill=sub)
                for i, r in enumerate(road[:5]):
                    y = y0 + 40 + i * 50
                    cur, done = i == idx, i < idx
                    col = acc if cur else (text if done else sub)
                    a = 255 if (cur or done) else 130
                    d.text((X0, y), f"{i + 1:02d}", font=num_font(cfg, 30, 500 if cur else 300), fill=col + (a,))
                    f = P.f(30, 800 if cur else 600)
                    h = str(r.get("heading") or "")
                    while d.textlength(h, font=f) > 760 and len(h) > 4:
                        h = h[:-2] + "…"
                    d.text((X0 + 70, y - 2), h, font=f, fill=col + (a,))
                    res = str(r.get("result") or "")
                    if done and res:
                        from . import lab as lab_mod
                        from .assets import palette
                        vc = _rgb(lab_mod.verdict_color(palette(cfg), res))
                        x = X0 + 70 + d.textlength(h, font=f) + 24
                        d.rounded_rectangle([x, y + 2, x + d.textlength(res, font=P.f(24, 700)) + 28, y + 38], radius=8, outline=vc, width=2)
                        d.text((x + 14, y + 6), res, font=P.f(24, 700), fill=vc)
                    if cur:
                        d.polygon([(X0 - 30, y + 8), (X0 - 30, y + 30), (X0 - 12, y + 19)], fill=acc)
            s.add(0, roadmap, slide=False, delay=0.4)

    elif kind == "data":
        heading, hl = str(sc.get("heading") or ""), str(sc.get("heading_hl") or "")
        source = str(sc.get("source") or "")
        bars = [b for b in (sc.get("bars") or []) if isinstance(b, dict)][:4]
        vals = []
        for b in bars:
            try:
                vals.append(float(b.get("value") or 0))
            except (TypeError, ValueError):
                vals.append(0.0)
        hls = [bool(b.get("hl")) for b in bars]
        n = len(bars)

        def head(d, p):
            f, ls = P.fit(d, heading, X1 - X0, 64, 600, min_size=44)
            _hl_line(d, P, X0, 165, ls[0] if ls else "", hl, f, text, acc)
            if source:
                d.text((X0 + 2, 165 + int(f.size * 1.35)), "出典：" + source, font=P.f(28, 700), fill=acc)
        s.add(0, head)

        def num(v: float, unit: str, final: float) -> str:
            """数え上げの途中も、最後の数と同じ桁数で（3 → 0,1,2,3 / 2.5 → 0.0〜2.5）."""
            dec = 0 if abs(final - round(final)) < 1e-9 else len(f"{final:g}".split(".")[-1])
            return (f"{v:,.{dec}f}" if dec else f"{int(round(v)):,}") + unit

        for i, b in enumerate(bars):
            def bar(d, p, i=i, b=b):
                info = space.draw_bars3d(d._image, vals, hls, p=p, only=i)
                it = info[i]
                tx, ty = it["top"]
                bx, by = it["base"]
                col = gold if hls[i] else (acc if any(hls) else text)
                if p > 0.05:
                    fn = num_font(cfg, 76 if hls[i] else 64, 400)
                    unit = str(b.get("unit") or "")
                    t = num(vals[i] * min(1.0, p), "", vals[i])
                    # 数字は Oxanium、「%」以外の単位（人・点・倍・円…）は日本語の字体で
                    fu = num_font(cfg, int(fn.size * 0.55), 400) if unit.isascii() else P.f(int(fn.size * 0.5), 700)
                    w_ = d.textlength(t, font=fn) + d.textlength(unit, font=fu)
                    a_ = int(255 * min(1.0, p * 1.5))
                    d.text((tx - w_ / 2, ty - 18), t, font=fn, fill=col + (a_,), anchor="ls")
                    d.text((tx - w_ / 2 + d.textlength(t, font=fn), ty - 18), unit, font=fu, fill=col + (a_,), anchor="ls")
                lab_ = str(b.get("label") or "")
                fl = P.f(34 if n <= 2 else 28, 700)
                while d.textlength(lab_, font=fl) > (520 if n <= 2 else 360) and fl.size > 22:
                    fl = P.f(fl.size - 2, 700)
                d.text((bx, by + 22), lab_, font=fl, fill=text, anchor="ma")
            s.add(i + 1, bar, slide=False)
        note = str(sc.get("note") or "")
        ratio = space.ratio_text(vals)
        if note or ratio:
            def nt(d, p):
                if ratio and n == 2:
                    info = space.draw_bars3d(Image.new("RGBA", (8, 8)), vals, hls, p=1.0, only=-1)
                    (x0_, y0_), (x1_, y1_) = info[0]["top_full"], info[1]["top_full"]
                    bx, by = (x0_ + x1_) / 2, (min(y0_, y1_) + max(y0_, y1_)) / 2 - 40
                    f = num_font(cfg, 60, 500)
                    w_ = d.textlength(ratio[1:-1], font=f) + 150
                    d.rounded_rectangle([bx - w_ / 2, by - 52, bx + w_ / 2, by + 52], radius=52, fill=(30, 22, 4, 190),
                                        outline=gold + (255,), width=3)
                    d.text((bx - w_ / 2 + 30, by + 2), "約", font=P.f(36, 700), fill=gold, anchor="lm")
                    d.text((bx, by + 4), ratio[1:-1], font=f, fill=gold, anchor="mm")
                    d.text((bx + w_ / 2 - 30, by + 2), "倍", font=P.f(36, 700), fill=gold, anchor="rm")
                if note:
                    f, ls = P.fit(d, note, X1 - X0, 46, 700, min_size=34)
                    P.marker_text(d, X0, 862, ls[0] if ls else note, f, p=p)
            s.add(n + 1, nt, slide=False)

    elif kind == "verdict":
        from . import lab as lab_mod
        from .assets import palette
        claim = str(sc.get("claim") or "")
        result = str(sc.get("result") or "半分本当")
        reason = str(sc.get("reason") or "")
        vcol = _rgb(lab_mod.verdict_color(palette(cfg), result))
        pos = {"ウソ": 0.0, "根拠うすい": 0.15, "半分本当": 0.5, "条件つき": 0.5, "本当": 1.0}.get(result, 0.5)
        mx0, mx1, my = 120, 860, 560

        def head(d, p):
            d.text((X0, 175), "RESULT", font=_en(cfg, 30, 600), fill=acc)
            d.text((X0 + 170, 172), "検証結果", font=P.f(32, 700), fill=sub)
            f, ls = P.fit(d, f"「{claim}」", X1 - X0, 70, 600, min_size=46)
            y = 235
            for ln in ls[:2]:
                d.text((X0 - 10, y), ln, font=f, fill=text)
                y += int(f.size * 1.3)
            # 判定メーター（ウソ ← 半分本当 → 本当）
            for i in range(mx1 - mx0):
                t = i / (mx1 - mx0)
                ca, cb = (red, gold) if t < 0.5 else (gold, green)
                k = t * 2 if t < 0.5 else (t - 0.5) * 2
                c = tuple(int(a + (b - a) * k) for a, b in zip(ca, cb))
                d.line([(mx0 + i, my - 5), (mx0 + i, my + 5)], fill=c + (200,))
            for t, name in ((0.0, "ウソ"), (0.5, "半分本当"), (1.0, "本当")):
                x = mx0 + (mx1 - mx0) * t
                d.line([(x, my - 18), (x, my + 18)], fill=text + (200,), width=2)
                d.text((x, my + 34), name, font=P.f(30, 700), fill=sub, anchor="ma")
        s.add(0, head, slide=False)

        def needle(d, p):
            t = 0.5 + (pos - 0.5) * p
            x = mx0 + (mx1 - mx0) * t
            d.polygon([(x - 18, my - 52), (x + 18, my - 52), (x, my - 14)], fill=vcol + (255,))
            d.line([(x, my - 14), (x, my + 14)], fill=vcol + (255,), width=4)
            st = lab_mod.stamp(cfg, result, "#%02X%02X%02X" % vcol, size=96)
            k = 1.3 - 0.3 * p
            st = st.resize((max(1, int(st.width * k)), max(1, int(st.height * k))))
            st.putalpha(st.getchannel("A").point(lambda v: int(v * min(1.0, p * 1.4))))
            d._image.alpha_composite(st, (int(1140 - st.width / 2), int(560 - st.height / 2)))
        s.add(1, needle, slide=False)
        if reason:
            def rs(d, p):
                f, ls = P.fit(d, reason, X1 - X0, 50, 700, min_size=34)
                y = 700
                for ln in ls[:2]:
                    P.marker_text(d, X0, y, ln, f, p=p)
                    y += int(f.size * 1.4)
            s.add(2, rs, slide=False)
    return s
