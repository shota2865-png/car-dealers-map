"""本編（16:9）の場面。Shorts（quiz.py）と同じ部品を横に並べ、ハイライトを多めに使う.

場面の種類:
  question / countdown  2 択を横に並べる（円のカウントダウンは下）
  chapter               章の扉。「第1章」と見出し（マーカー）
  flow                  箱を左から右へ矢印でつなぐ。up=true の箱に赤い上向き矢印
  branch                左の箱から右上・右下へ斜めの矢印。avoided=true で ✕ と「避けていた」
  versus                左右の対比。左が灰色になって ✕、右が青枠で ✓
  steps                 手順カードを左から右へ。最後に ✓ と締めの一言
  meter                 「頭のメモ帳」のような容量を、升目が順に埋まっていくことで見せる
  point                 専門用語 = 日常の言葉 の言い換えを、大きく
各場面の narration の段階の意味は quiz.py と同じ考え方（段階 i で i 番目の要素が出る）。
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from .config import Config
from .quiz import Parts, Scene, Theme, theme


def theme_wide(cfg: Config) -> Theme:
    th = theme(cfg)
    th.W, th.H, th.M = 1920, 1080, 120
    th.header_y, th.header_line, th.body_top, th.brand_size = 56, 110, 130, 30
    th.safe_top, th.safe_bottom = 170, 1010
    return th


def build_scene_wide(cfg: Config, th: Theme, sc: dict[str, Any]) -> Scene:
    P = Parts(cfg, th)
    s = Scene(cfg, th)
    kind = sc.get("kind")
    M, W = th.M, th.W
    CW = W - M * 2
    TITLE = 80
    TY = 170 + int(TITLE * 1.35) + 48

    def title(d, p):
        parts = P.split_hl(sc.get("heading") or "", sc.get("heading_hl") or "")
        P.title(d, parts, y=170, size=TITLE, p=p)

    if kind in ("question", "countdown"):
        heading = sc.get("heading") or "3秒で選んでください"
        parts = P.split_hl(heading, sc.get("heading_hl") or "3秒")
        s.add(0, lambda d, p: P.title(d, parts, y=170, size=TITLE, p=p))
        lparts = P.split_hl(sc.get("lead") or "", sc.get("lead_hl") or "")
        s.add(1, lambda d, p: P.marker_line(d, M, TY, lparts, P.f(54), p=p))
        oy = TY + 110
        gap = 60
        ow = (CW - gap) // 2
        opts = (sc.get("options") or ["", ""])[:2] + [""]
        s.add(2, lambda d, p: P.option(d, oy, "A", opts[0], h=220, x0=M, x1=M + ow, size=54))
        s.add(3, lambda d, p: P.option(d, oy, "B", opts[1], h=220, x0=M + ow + gap, x1=W - M, size=54))
        cy = oy + 52 + 220 + 40 + 120

        def count(d, p, n):
            r = 120 + int(24 * (1 - p))
            d.ellipse([W / 2 - r, cy - r, W / 2 + r, cy + r], fill=th.blue_light, outline=th.blue, width=6)
            tmp = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
            ImageDraw.Draw(tmp).text((200, 200), str(n), font=P.f(180), fill=th.blue, anchor="mm")
            glyph = tmp.crop(tmp.getbbox())
            d._image.paste(glyph, (int(W / 2 - glyph.width / 2), int(cy - glyph.height / 2)), glyph)
        s.extra = count

    elif kind == "chapter":
        label = str(sc.get("label") or "")
        head = str(sc.get("heading") or "")
        hl = str(sc.get("heading_hl") or "")

        def draw(d, p):
            f_l = P.f(44)
            d.text((M, 360), label, font=f_l, fill=th.blue)
            f = P.f(104)
            P.marker_line(d, M, 440, P.split_hl(head, hl), f, p=p)
            d.rectangle([M, 600, M + 120 * p, 610], fill=th.blue)
        s.add(0, draw, slide=False)
        sub = str(sc.get("sub") or "")
        if sub:
            s.add(1, lambda d, p: d.text((M, 660), sub, font=P.f(46, 500), fill=th.sec))

    elif kind == "flow":
        s.add(0, title)
        boxes = (sc.get("boxes") or [])[:3]
        n = max(1, len(boxes))
        gap = 150
        bw = (CW - gap * (n - 1)) // n
        bh = 300
        y0 = TY + 150
        for i, b in enumerate(boxes):
            x0 = M + i * (bw + gap)
            text, state = str(b.get("text") or ""), str(b.get("state") or "normal")
            if i > 0:
                s.add(i, (lambda x_: (lambda d, p: P.arrow(d, (x_ - gap + 20, y0 + bh / 2), (x_ - 20, y0 + bh / 2), p=p)))(x0), slide=False)
            s.add(i, (lambda x_, t_, st_: (lambda d, p: P.box(d, [x_, y0, x_ + bw, y0 + bh], t_, st_, size=62)))(x0, text, state), delay=0.3 if i else 0.0)
            if b.get("up"):   # 箱の上に赤い上向き矢印（文字に重ねない）
                s.add(i, (lambda x_: (lambda d, p: P.arrow(d, (x_ + bw / 2, y0 - 16), (x_ + bw / 2, y0 - 130), p=p, color=th.red)))(x0), slide=False, delay=0.6)
            if b.get("note"):
                s.add(i, (lambda x_, nt: (lambda d, p: P.caption_under(d, x_ + bw // 2, y0 + bh + 40, nt, P.f(44), th.blue, p=p, hl=True)))(x0, str(b["note"])), slide=False, delay=0.6)

    elif kind == "branch":
        s.add(0, title)
        note = sc.get("note") or ""
        if note:
            s.add(0, lambda d, p: d.text((M, TY), note, font=P.f(40), fill=th.blue))
        gy = TY + 90
        bw = 620
        lbox = [M, gy + 150, M + bw, gy + 370]
        tbox = [W - M - bw, gy, W - M, gy + 200]
        bbox_ = [W - M - bw, gy + 320, W - M, gy + 520]
        lx, ly = M + bw, gy + 260
        targets = (sc.get("targets") or [])[:2] + [{}, {}]
        t0, t1 = str(targets[0].get("text") or ""), str(targets[1].get("text") or "")
        avoided = bool(targets[1].get("avoided"))
        a0 = ((lx + 20, ly - 30), (tbox[0] - 20, gy + 100))
        a1 = ((lx + 20, ly + 30), (bbox_[0] - 20, gy + 420))
        s.add(0, lambda d, p: P.box(d, lbox, str(sc.get("source") or ""), size=62))
        s.add(1, lambda d, p: P.arrow(d, *a0, p=p), slide=False)
        s.add(1, lambda d, p: P.box(d, tbox, t0, size=64), delay=0.3)
        s.add(2, lambda d, p: P.box(d, tbox, t0, "dim", size=64), slide=False)
        s.add(2, lambda d, p: P.arrow(d, *a0, color=th.line), slide=False)
        s.add(2, lambda d, p: P.arrow(d, *a1, p=p), slide=False)
        s.add(2, lambda d, p: P.box(d, bbox_, t1, "active", size=64), delay=0.3)
        if avoided:
            mx, my = (a1[0][0] + a1[1][0]) / 2, (a1[0][1] + a1[1][1]) / 2
            s.add(2, lambda d, p: P.cross(d, int(mx) - 13, int(my) - 13, s=76, width=18), slide=False, delay=0.6)
            s.add(2, lambda d, p: P.caption_under(d, int(mx), int(my) + 70, str(sc.get("avoid_label") or "避けていた"), P.f(46), th.red, p=p, hl=True), slide=False, delay=0.8)

    elif kind == "versus":
        s.add(0, title)
        gap = 120
        bw = (CW - gap) // 2
        bh = 280
        y0 = TY + 40
        lcx, rcx = M + bw // 2, W - M - bw // 2
        left, right = sc.get("left") or {}, sc.get("right") or {}
        lt, rt = str(left.get("text") or ""), str(right.get("text") or "")
        s.add(0, lambda d, p: P.box(d, [M, y0, M + bw, y0 + bh], lt, size=64))
        s.add(1, lambda d, p: P.box(d, [M, y0, M + bw, y0 + bh], lt, "dim", size=64), slide=False)
        s.add(1, lambda d, p: P.cross(d, lcx, y0 + bh + 80, s=72), slide=False, delay=0.2)
        if left.get("caption"):
            s.add(1, lambda d, p: P.caption_under(d, lcx, y0 + bh + 140, str(left["caption"]), P.f(46, 500), th.muted), delay=0.4)
        s.add(2, lambda d, p: P.box(d, [W - M - bw, y0, W - M, y0 + bh], rt, "active", size=64))
        s.add(2, lambda d, p: P.check(d, rcx, y0 + bh + 80, s=80), slide=False, delay=0.4)
        if right.get("caption"):
            s.add(2, lambda d, p: P.caption_under(d, rcx, y0 + bh + 140, str(right["caption"]), P.f(52), th.blue, p=p, hl=True), slide=False, delay=0.7)

    elif kind == "steps":
        heading = sc.get("heading") or "今日のひとつ"
        parts = P.split_hl(heading, sc.get("heading_hl") or heading)
        s.add(0, lambda d, p: P.title(d, parts, y=170, size=TITLE, p=p))
        items = [str(x) for x in (sc.get("items") or [])][:3]
        n = max(1, len(items))
        gap = 110
        bw = (CW - gap * (n - 1)) // n
        bh = 240
        y0 = TY + 60
        for i, t in enumerate(items):
            x0 = M + i * (bw + gap)
            st = i + 1
            s.add(st, (lambda x_, t_, i_: (lambda d, p: P.box(d, [x_, y0, x_ + bw, y0 + bh], f"{i_ + 1}. {t_}", "active", size=56)))(x0, t, i))
            if i + 1 < n:
                s.add(st + 1, (lambda x_: (lambda d, p: P.arrow(d, (x_ + bw + 16, y0 + bh / 2), (x_ + bw + gap - 16, y0 + bh / 2), p=p)))(x0), slide=False)
                s.add(st + 1, (lambda x_, t_, i_: (lambda d, p: P.box(d, [x_, y0, x_ + bw, y0 + bh], f"{i_ + 1}. {t_}", "normal", size=56)))(x0, t, i), slide=False)
        final = sc.get("final") or "それで終わり"
        fy = y0 + bh + 90
        s.add(n + 1, lambda d, p: P.check(d, M + 36, fy + 44, s=72), slide=False)
        s.add(n + 1, lambda d, p: P.marker_text(d, M + 110, fy, final, P.f(80), color=th.green, p=p), slide=False)

    elif kind == "meter":
        # 容量の升目。段階 0 = 見出しと空の升目、1..k = fill[i-1] が 1 升ずつ埋まる、最後に残りの升に label_left
        s.add(0, title)
        slots = int(sc.get("slots") or 5)
        fill = [x for x in (sc.get("fill") or [])][:slots]
        gap = 24
        sw = (CW - gap * (slots - 1)) // slots
        sh = 240
        y0 = TY + 90
        cap = str(sc.get("caption") or "")
        if cap:
            s.add(0, lambda d, p: d.text((M, TY), cap, font=P.f(44, 500), fill=th.sec))
        for i in range(slots):
            x0 = M + i * (sw + gap)
            s.add(0, (lambda x_: (lambda d, p: d.rounded_rectangle([x_, y0, x_ + sw, y0 + sh], radius=10, fill=th.bg, outline=th.muted, width=3)))(x0))
        for i, item in enumerate(fill):
            text = str(item.get("text") if isinstance(item, dict) else item)
            bad = bool(item.get("bad", True)) if isinstance(item, dict) else True
            x0 = M + i * (sw + gap)
            color = "#FDE8E6" if bad else th.blue_light
            outline = th.red if bad else th.blue
            tcolor = th.red if bad else th.blue

            def slot(d, p, x_=x0, t_=text, c_=color, o_=outline, tc_=tcolor):
                h = int(sh * p)
                d.rounded_rectangle([x_, y0 + sh - max(h, 12), x_ + sw, y0 + sh], radius=10, fill=c_, outline=o_, width=4)
                if p > 0.6:
                    f, lines = P.fit(d, t_, sw - 36, 60)
                    lh = int(f.size * 1.2)
                    cy = y0 + sh / 2 - lh * (len(lines) - 1) / 2
                    for ln in lines:
                        P.text_mm(d, x_ + sw / 2, cy, ln, f, tc_)
                        cy += lh
            s.add(i + 1, slot, slide=False)
        left = str(sc.get("label_left") or "")
        if left and len(fill) < slots:
            k = len(fill)
            xs = M + k * (sw + gap)
            xe = M + (slots - 1) * (sw + gap) + sw
            for j in range(k, slots):          # 残りの升を青枠で光らせる
                xj = M + j * (sw + gap)
                s.add(len(fill) + 1, (lambda x_: (lambda d, p: d.rounded_rectangle([x_, y0, x_ + sw, y0 + sh], radius=10, fill=th.blue_light, outline=th.blue, width=6)))(xj), slide=False)
            s.add(len(fill) + 1, lambda d, p: P.caption_under(d, (xs + xe) // 2, y0 + sh + 50, left, P.f(56), th.blue, p=p, hl=True), slide=False, delay=0.2)

    elif kind == "point":
        # 専門用語 = 日常の言葉。段階 0 = 用語（灰色の箱）、1 = ＝ と言い換え（青枠）、2 = 補足（マーカー）
        s.add(0, title)
        y0 = TY + 60
        bw, bh = 700, 260
        term, plain = str(sc.get("term") or ""), str(sc.get("plain") or "")
        s.add(0, lambda d, p: P.box(d, [M, y0, M + bw, y0 + bh], term, size=72))
        s.add(1, lambda d, p: P.box(d, [M, y0, M + bw, y0 + bh], term, "dim", size=72), slide=False)
        s.add(1, lambda d, p: P.text_mm(d, W / 2, y0 + bh / 2, "＝", P.f(120), th.blue), slide=False)
        s.add(1, lambda d, p: P.box(d, [W - M - bw, y0, W - M, y0 + bh], plain, "active", size=76), delay=0.25)
        note = str(sc.get("note") or "")
        if note:
            s.add(2, lambda d, p: P.caption_under(d, W // 2, y0 + bh + 90, note, P.f(56), th.text, p=p, hl=True), slide=False)

    elif kind == "result":
        # 答え合わせ。段階 0 = 選んだ肢（左・選択状態）、1 = 思いこみに打ち消し線、2 = 矢印と本当の答え（右）
        pick = str(sc.get("pick") or "B").strip()[:1] or "B"
        heading = sc.get("heading") or f"{pick}を選んだ人へ"
        parts = P.split_hl(heading, sc.get("heading_hl") or pick)
        s.add(0, lambda d, p: P.title(d, parts, y=170, size=TITLE, p=p))
        oy = TY + 20
        ow = 720
        s.add(0, lambda d, p: P.option(d, oy, pick, str(sc.get("option") or ""), state="selected", h=220, x0=M, x1=M + ow, size=58))
        rx = M + ow + 200
        strike_t = str(sc.get("strike") or "")
        answer = str(sc.get("answer") or "")

        def weak(d, p):
            f, lines = P.fit(d, strike_t, W - M - rx, 64)
            y = oy + 70
            for ln in lines:
                d.text((rx, y), ln, font=f, fill=th.muted)
                tw = int(d.textlength(ln, font=f))
                P.strike(d, rx - 8, rx + tw + 8, y + int(f.size * 0.62), p=p)
                y += int(f.size * 1.3)
        if strike_t:
            s.add(1, weak, slide=False)
        s.add(2, lambda d, p: P.arrow(d, (M + ow + 40, oy + 52 + 110), (rx - 30, oy + 52 + 110), p=p), slide=False)
        ay = oy + 52 + 220 + 90

        def ans(d, p):
            f, lines = P.fit(d, answer, CW, 84)
            y = ay
            for ln in lines:
                P.marker_text(d, M, y, ln, f, color=th.blue, p=p)
                y += int(f.size * 1.35)
        s.add(2, ans, slide=False, delay=0.35)

    elif kind == "opening":
        # 冒頭のあいさつ（寝ながら聴く人へ）。段階 0 = 大きな一言、1 = 補足、2 = 今日のテーマ
        lines = [str(x) for x in (sc.get("lines") or [])][:2]
        theme_t = str(sc.get("theme") or "")
        cy0 = 260

        def big(d, p):
            f, ls = P.fit(d, lines[0] if lines else "", CW, 88)
            y = cy0
            for ln in ls:
                tw = d.textlength(ln, font=f)
                P.marker_text(d, int((W - tw) / 2), y, ln, f, p=p)
                y += int(f.size * 1.35)
        s.add(0, big, slide=False)
        if len(lines) > 1:
            s.add(1, lambda d, p: P.caption_under(d, W // 2, cy0 + 200, lines[1], P.f(52, 500), th.sec))
        if theme_t:
            def th_(d, p):
                f = P.f(44)
                tw = d.textlength("今日のテーマ", font=f)
                d.text(((W - tw) / 2, cy0 + 330), "今日のテーマ", font=f, fill=th.blue)
                f2, ls = P.fit(d, theme_t, CW, 64)
                y = cy0 + 400
                for ln in ls:
                    P.text_mm(d, W / 2, y + f2.size * 0.6, ln, f2, th.text)
                    y += int(f2.size * 1.3)
            s.add(2, th_)

    elif kind == "ending":
        # 締め。真ん中に大きく「今日のひとつ」、その下に次回と更新時刻
        times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
        when = times[0] if times else "20:00"
        one = str(sc.get("one") or "")
        cy0 = 250

        def label(d, p):
            f = P.f(48)
            tw = d.textlength("今日のひとつ", font=f)
            d.text(((W - tw) / 2, cy0), "今日のひとつ", font=f, fill=th.blue)
        s.add(0, label, slide=False)

        def big(d, p):
            f, lines = P.fit(d, one, CW, 100)
            y = cy0 + 100
            for ln in lines:
                tw = d.textlength(ln, font=f)
                P.marker_text(d, int((W - tw) / 2), y, ln, f, p=p)
                y += int(f.size * 1.35)
        s.add(0, big, slide=False, delay=0.2)
        nxt = str(sc.get("next") or "")

        def foot(d, p):
            f = P.f(56)
            parts_ = ([("次回は", False), (nxt, True)] if nxt else []) + [("　毎日", False), (when, True), ("更新", False)]
            total = sum(d.textlength(t, font=f) for t, _ in parts_) + 20
            P.marker_line(d, int((W - total) / 2), cy0 + 480, parts_, f, p=p, hl_color=th.blue)
        s.add(1, foot, slide=False)
        rest = str(sc.get("rest") or "")
        if rest:
            s.add(2, lambda d, p: P.caption_under(d, W // 2, cy0 + 600, rest, P.f(46, 500), th.sec))

    else:
        s.add(0, title)
    return s
