"""シーン計画 —「8秒ごとに画を変える」の実装.

これまでは1セクション＝1枚の絵（90秒近く同じ画面）だった。
ここでは音声の実測タイムコードを使い、各ブロックを約8秒ずつの
シーンに割って、シーンごとに違う見せ方を割り当てる:

    セクションの本体（図表 / カード）
    → キーワードカード（KEYWORD テロップから）
    → 数字カード（DATA テロップから）
    → 写真 or AI画像 or 幾何パターン
    → 用語カード（ビジネス用語）
    → 出典カード（参考記事の様式）
    → いま読んでいる一文のカード
    → 本体をもう一度 …

still（静止させる）は**実際に描けた種類**で決める。写真が取れずカードに
落ちたのにズームがかかる、という前回の不具合はここで潰す。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import assets, footage
from .config import Config
from .script import Section, VideoScript, nominalize, plain_heading, split_sentences
from .tts import Line, VoiceTrack

log = logging.getLogger(__name__)


@dataclass
class Scene:
    image: Path
    start: float
    end: float
    still: bool = True          # True なら動かさない（カード・図表）
    kind: str = "card"          # 実際に描けた種類
    label: str = ""             # ログ用
    background: Path | None = None   # 後ろで流す動画（透過カードはこの上に重なる）
    bg_offset: float = 0.0           # 背景動画の再生開始位置（同じ素材でも違う所から）
    fade_in: bool = True             # False なら切り替えのフェードなし（同じ画面の続き = ハイライトの段階）
    bg_duration: float = 0.0         # 背景素材の尺（足りなければゆっくり再生して繰り返しを避ける）

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.5)


# ----------------------------------------------------------------------
# 音声の行を「約8秒のかたまり」に割る
# ----------------------------------------------------------------------
def chunk_lines(lines: list[Line], target: float, lo: float, hi: float,
                pivots: tuple[str, ...] = ()) -> list[list[Line]]:
    """約 target 秒ごとに切る。pivots（でも／しかし／つまり…）で始まる文の直前は、
    lo 秒以上たまっていれば目標前でも切る（Semantic Cut: 意味が変わる所で画を変える）."""
    chunks: list[list[Line]] = []
    cur: list[Line] = []
    for ln in lines:
        if cur:
            span = ln.end - cur[0].start
            cur_span = cur[-1].end - cur[0].start
            pivot = bool(pivots) and ln.text.lstrip("「 　").startswith(pivots)
            # 目標を超えたら切る。上限を超えそうなときも切る。転換語の前でも切る
            if cur_span >= target or span > hi or (pivot and cur_span >= lo):
                chunks.append(cur)
                cur = []
        cur.append(ln)
    if cur:
        chunks.append(cur)
    # 最後が短すぎたら前に吸収する
    if len(chunks) >= 2 and (chunks[-1][-1].end - chunks[-1][0].start) < lo:
        chunks[-2].extend(chunks.pop())
    return chunks


def _span(chunk: list[Line]) -> tuple[float, float]:
    return chunk[0].start, chunk[-1].end


def _key_sentence(chunk: list[Line]) -> str:
    """そのかたまりで一番情報量のありそうな文（長めの文）を返す."""
    return max((ln.text for ln in chunk), key=len, default="")


_NUM = re.compile(r"(\d[\d,\.]*\s*(?:兆|億|万)?\s*(?:円|ドル|%|パーセント|倍|人|年|ポイント|割))")


def _numbers(text: str) -> list[str]:
    return [m.replace("パーセント", "%") for m in _NUM.findall(text)]


# ----------------------------------------------------------------------
# 見せ方の候補（セクションごと）
# ----------------------------------------------------------------------
class _Painter:
    """カードを描いて Scene を返す小道具。ファイル名の連番を管理する."""

    def __init__(self, cfg: Config, outdir: Path):
        self.cfg = cfg
        self.outdir = outdir
        self.n = 0
        self.seed = 0
        self.picker = footage.Picker(cfg)
        self.words = ""             # いまのセクションの英語キーワード（背景選びに使う）

    def _next(self, stem: str) -> Path:
        self.n += 1
        return self.outdir / f"scene_{self.n:03d}_{stem}.jpg"

    def _bg(self, scene: Scene, needed: float | None = None) -> Scene:
        """透過カード（PNG）なら動く背景を敷く。不透明カードならそのまま.

        needed 秒（その画面が続く長さ）以上の素材を優先し、同じ映像が途中で頭から
        繰り返されるのを避ける。
        """
        if scene.image.suffix.lower() == ".png":
            need = needed if needed is not None else scene.duration
            clip = self.picker.abstract_clip(self.words, seed=self.n, needed=need)
            if clip is not None:
                scene.background = clip.path
                scene.bg_duration = clip.duration
                spare = max(clip.duration - need, 0.0)
                scene.bg_offset = (self.n * 2.7) % spare if spare > 1.0 else 0.0
        return scene

    def title(self, text: str, start: float, end: float) -> Scene:
        p = assets.build_title_card(self.cfg, text, self._next("title"))
        return self._bg(Scene(p, start, end, True, "title", "タイトル"))

    def outro(self, start: float, end: float) -> Scene:
        p = assets.build_outro_card(self.cfg, self._next("outro"))
        return self._bg(Scene(p, start, end, True, "outro", "アウトロ"))

    def bullets(self, heading: str, items: list[str], start: float, end: float,
                lines: list[Line] | None = None) -> list[Scene]:
        """箇条書き。話が進むにつれて、いま話している項目を順にハイライトする（段階ごとに 1 枚）."""
        items = [x for x in items if x]
        stages = _stages(start, end, [assets.row_fragments([x]) for x in items], lines,
                         min_seconds=float(self.cfg.get("visuals.highlight_min_seconds", 1.6)))
        scenes: list[Scene] = []
        for k, (active, s, e) in enumerate(stages):
            p = assets.render_textcard(self.cfg, heading, items, self._next("bullets"), active=active)
            scenes.append(Scene(p, s, e, True, "card", f"箇条書き: {heading[:12]}"))
        return self._bg_seq(scenes)

    def _bg_seq(self, scenes: list[Scene]) -> list[Scene]:
        """同じ画面の段階（ハイライトが進むだけ）には同じ背景を続きから敷き、フェードも入れない."""
        if not scenes:
            return scenes
        first = self._bg(scenes[0], needed=scenes[-1].end - scenes[0].start)
        for sc in scenes[1:]:
            sc.background = first.background
            sc.bg_duration = first.bg_duration
            sc.bg_offset = first.bg_offset + (sc.start - first.start)
            sc.fade_in = False
        return scenes

    def chart(self, sec: Section, start: float, end: float) -> Scene:
        p = self._next("chart")
        try:
            p = assets.render_chart(self.cfg, sec.visual.chart or {}, p)
            return self._bg(Scene(p, start, end, True, "chart", f"図表: {sec.heading[:12]}"))
        except Exception as exc:
            log.warning("図表を描けなかったのでカードにします: %s", exc)
            return self.bullets(sec.heading, sec.on_screen, start, end)

    def keyword(self, word: str, sub: str, start: float, end: float) -> Scene:
        p = assets.render_keyword_card(self.cfg, word, sub, self._next("kw"))
        return self._bg(Scene(p, start, end, True, "card", f"キーワード: {word[:10]}"))

    def number(self, value: str, label: str, note: str, start: float, end: float) -> Scene:
        p = assets.render_number_card(self.cfg, value, label, note, self._next("num"))
        return self._bg(Scene(p, start, end, True, "card", f"数字: {value}"))

    def quote(self, sentence: str, start: float, end: float, source: str = "") -> Scene:
        # 台本の cards（体言止め）が渡されればそのまま、生の文なら語尾を落として体言止めに寄せる
        text = sentence if source or not sentence.endswith(("のだ", "です", "ます", "だ", "。")) else nominalize(sentence)
        p = assets.render_quote_card(self.cfg, text, self._next("quote"), source=source)
        return self._bg(Scene(p, start, end, True, "card", f"カード: {text[:10]}"))

    def term(self, t, start: float, end: float) -> Scene:
        p = assets.render_term_card(self.cfg, t.term, t.meaning, t.example, self._next("term"))
        return self._bg(Scene(p, start, end, True, "card", f"用語: {t.term}"))

    def reference(self, name: str, url: str, note: str, start: float, end: float) -> Scene:
        p = assets.render_reference_card(self.cfg, name, url, note, self._next("ref"))
        return self._bg(Scene(p, start, end, True, "card", f"出典: {name[:10]}"))

    def diagram(self, g, start: float, end: float, lines: list[Line] | None = None) -> list[Scene]:
        """図解。行（箱）を話の進みに合わせて順にハイライトする（段階ごとに 1 枚、背景は続き）."""
        stages = _stages(start, end, assets.diagram_row_texts(g.type, g.items), lines,
                         min_seconds=float(self.cfg.get("visuals.highlight_min_seconds", 1.6)))
        scenes: list[Scene] = []
        for active, s, e in stages:
            p = assets.render_diagram(self.cfg, g.type, g.title, g.items, g.note,
                                      self._next(f"dg_{g.type}"), active=active)
            scenes.append(Scene(p, s, e, True, "diagram", f"図解({g.type}): {g.title[:10]}"))
        return self._bg_seq(scenes)

    def photo(self, query: str, prompt: str, heading: str, bullets: list[str],
              start: float, end: float) -> Scene:
        """写真系。順に: 実写フッテージ（Artlist など）→ 写真 → AI 画像 → 動く抽象背景.

        prompt は台本の image_prompt（概念の視覚化）。無ければ検索語で代用。
        """
        self.seed += 1
        clip = self.picker.broll(f"{query} {prompt}", seed=self.seed)
        if clip is not None:
            p = assets.render_heading_overlay(self.cfg, heading, bullets, self._next("broll"))
            return Scene(p, start, end, True, "broll", f"実写: {clip.name[:18]}",
                         background=clip, bg_offset=(self.seed * 3.1) % 6.0)
        p, kind = assets.build_photo_scene(self.cfg, query, prompt, heading, bullets,
                                           self.seed, self._next("photo"), allow_pattern=False)
        if p is not None:
            # 写真は Ken Burns で動かす
            return Scene(p, start, end, False, kind, f"写真: {query[:14]}")
        # 何も取れない → 動く抽象背景の上に見出しだけ
        p = assets.render_heading_overlay(self.cfg, heading, bullets, self._next("motion"))
        scene = Scene(p, start, end, True, "motion", f"動く背景: {heading[:12]}")
        scene = self._bg(scene)
        if scene.background is None:      # 動く背景も無効なら従来のパターン画
            p, kind = assets.build_photo_scene(self.cfg, query, prompt, heading, bullets,
                                               self.seed, self._next("photo"))
            return Scene(p, start, end, kind != "photo", kind, f"写真: {query[:14]}")
        return scene


def _norm(text: str) -> str:
    """照合用に表記をそろえる（全角数字→半角、パーセント→%、空白・記号を除く）."""
    t = text.translate(str.maketrans("０１２３４５６７８９％＋－", "0123456789%+-"))
    t = t.replace("パーセント", "%").replace("パ-セント", "%")
    return re.sub(r"[\s、。，・「」『』（）()＝=→…]", "", t)


def _mentioned_row(rows: list[list[str]], text: str) -> int | None:
    """ナレーションの一文が、どの行の言葉を含んでいるか（一番長く一致した行）. 無ければ None."""
    t = _norm(text)
    best, best_len = None, 0
    for i, cells in enumerate(rows):
        for cell in cells:
            c = _norm(cell)
            if len(c) >= 2 and c in t and len(c) > best_len:
                best, best_len = i, len(c)
    return best


def _stages(start: float, end: float, rows: list[list[str]], lines: list[Line] | None,
            min_seconds: float = 1.6) -> list[tuple[int | None, float, float]]:
    """ハイライトの段階 [(行番号 or None, 開始, 終了), ...] を決める.

    ずんだもんが話している文に、図や表の行の言葉が出てきたときだけ、その行を光らせる。
    出てこない間は全体を見せる（None）。短すぎる段階は前の段階に吸収する。
    """
    n = len(rows)
    if n == 0 or not lines or end - start < min_seconds:
        return [(None, start, end)]
    raw: list[tuple[int | None, float]] = [(None, start)]
    for ln in lines:
        if ln.start < start or ln.start >= end:
            continue
        row = _mentioned_row(rows, ln.text)
        if row != raw[-1][0]:
            raw.append((row, max(ln.start, start)))
    # 段階にする（短すぎるものは前に吸収）
    stages: list[tuple[int | None, float, float]] = []
    for k, (row, s) in enumerate(raw):
        e = raw[k + 1][1] if k + 1 < len(raw) else end
        if stages and (e - s) < min_seconds * 0.6:
            prev = stages[-1]
            stages[-1] = (prev[0], prev[1], e)
            continue
        if stages and stages[-1][0] == row:
            stages[-1] = (row, stages[-1][1], e)
            continue
        if stages and (stages[-1][2] - stages[-1][1]) < min_seconds * 0.6:
            stages[-1] = (row, stages[-1][1], e)       # 直前の段階が短すぎたら差し替える
            continue
        stages.append((row, s, e))
    if not stages:
        return [(None, start, end)]
    stages[0] = (stages[0][0], start, stages[0][2])
    stages[-1] = (stages[-1][0], stages[-1][1], end)
    return stages


def _call(fn, s: float, e: float, ch):
    """プールの描き手を呼ぶ。話している文（ch）を受け取れるもの（引数 ch がある）には渡す."""
    import inspect
    try:
        takes_ch = "ch" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        takes_ch = False
    return fn(s, e, ch) if takes_ch else fn(s, e)


def _extend(scenes: list[Scene], item) -> None:
    if isinstance(item, list):
        scenes.extend(item)
    else:
        scenes.append(item)


def _section_pool(cfg: Config, script: VideoScript, sec: Section, index: int,
                  chunks: list[list[Line]], painter: _Painter):
    """セクション内の各かたまりに割り当てる『描き方』の列を作る（遅延実行）."""
    pool = []
    kind = sec.visual.kind

    # 1. 本体
    if kind == "chart" and sec.visual.chart:
        pool.append(lambda s, e: painter.chart(sec, s, e))
    elif kind == "stock":
        pool.append(lambda s, e: painter.photo(sec.visual.query or sec.heading,
                                               sec.visual.image_prompt or sec.visual.query,
                                               sec.heading, sec.on_screen, s, e))
    else:
        pool.append(lambda s, e, ch=None: painter.bullets(sec.heading, sec.on_screen, s, e, ch))

    # 2. テロップ由来。「タイトルだけ」のカードはほぼ要らないので、数字（DATA）だけ残す
    for cap in sec.captions:
        if cap.type == "DATA":
            pool.append(lambda s, e, c=cap: painter.number(c.text, sec.heading, "", s, e))

    # 3. 写真を1枚は挟む（本体が写真でなければ）
    if kind != "stock":
        query = sec.visual.query or sec.heading
        prompt = sec.visual.image_prompt or query
        pool.insert(min(2, len(pool)),
                    lambda s, e: painter.photo(query, prompt, sec.heading, sec.on_screen[:2], s, e))

    # 4. 用語カード（このセクションが初出のもの。範囲外は順繰りに）
    terms = [t for t in script.terms if t.section in (index, index + 1)]
    if not terms and script.terms:
        terms = [script.terms[index % len(script.terms)]]
    for t in terms[:1]:
        pool.append(lambda s, e, t=t: painter.term(t, s, e))

    # 5. 出典カード
    if script.sources:
        src = script.sources[index % len(script.sources)]
        pool.append(lambda s, e, src=src: painter.reference(
            src.get("name", "出典"), src.get("url", ""), sec.heading, s, e))

    # 6. 足りないぶんは「いま読んでいる一文」と本体の再掲を交互に
    return pool


# ----------------------------------------------------------------------
def plan_and_render(cfg: Config, script: VideoScript, track: VoiceTrack,
                    outdir: str | Path) -> list[Scene]:
    """全ブロックをシーンに割り、画像を描いて、時間順の Scene 列を返す."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    painter = _Painter(cfg, outdir)

    target = float(cfg.get("visuals.scene_seconds", 8.0))
    lo = float(cfg.get("visuals.scene_seconds_min", 4.0))
    hi = float(cfg.get("visuals.scene_seconds_max", 14.0))
    from . import bible
    pivots = bible.semantic_cut_words(cfg)

    scenes: list[Scene] = []

    def lines_of(block: str) -> list[Line]:
        return [ln for ln in track.lines if ln.block_id == block]

    def pick_card(cards, ch: list[Line]):
        """いま話している文（ch の index 範囲）に対応するカードを選ぶ。無ければ一番近いもの."""
        if not cards:
            return None
        lo_i, hi_i = ch[0].index, ch[-1].index
        inside = [c for c in cards if lo_i <= c.after_sentence <= hi_i]
        if inside:
            return inside[0]
        return min(cards, key=lambda c: min(abs(c.after_sentence - lo_i), abs(c.after_sentence - hi_i)))

    used_block: set[int] = set()

    def block_card(block: str, j: int, ch: list[Line]):
        """導入・締めの枠。話している文に合う図解 → 文字カード → 体言止めに寄せた一文."""
        lo_i, hi_i = ch[0].index, ch[-1].index
        for g in script.block_diagrams.get(block) or []:
            if lo_i <= g.after_sentence <= hi_i and id(g) not in used_block:
                used_block.add(id(g))
                return painter.diagram(g, *_span(ch), lines=ch)
        c = pick_card(script.block_cards.get(block) or [], ch)
        if c is not None:
            return painter.quote(c.text, _span(ch)[0], _span(ch)[1], source=c.source)
        rest = [g for g in script.block_diagrams.get(block) or [] if id(g) not in used_block]
        if rest:
            used_block.add(id(rest[0]))
            return painter.diagram(rest[0], *_span(ch), lines=ch)
        return painter.quote(nominalize(_key_sentence(ch)), *_span(ch))

    # --- hook: タイトル → キーワード → カード ---
    hook = chunk_lines(lines_of("hook"), target, lo, hi, pivots)
    for j, ch in enumerate(hook):
        s, e = _span(ch)
        if j == 0:
            scenes.append(painter.title(script.topic_title, s, e))
        else:
            # サムネ文言だけのキーワードカード（「痛みは毎週」のような短句）は文脈が無いと謎になるので出さない
            _extend(scenes, block_card("hook", j - 1, ch))

    # --- proof: 数字があれば数字カード → カード ---
    for j, ch in enumerate(chunk_lines(lines_of("proof"), target, lo, hi, pivots)):
        s, e = _span(ch)
        nums = _numbers(script.proof)
        if j == 0 and nums:
            value = " → ".join(nums[:2]) if len(nums) >= 2 else nums[0]
            scenes.append(painter.number(value, "", "", s, e))
        else:
            _extend(scenes, block_card("proof", j, ch))

    # --- promise: この動画で分かること → カード ---
    for j, ch in enumerate(chunk_lines(lines_of("promise"), target, lo, hi, pivots)):
        s, e = _span(ch)
        if j == 0:
            cards = script.block_cards.get("promise") or []
            items = [c.text for c in cards][:3] or \
                    ([plain_heading(x.rstrip("。")) for x in split_sentences(script.promise)][1:4])
            _extend(scenes, painter.bullets("この動画で分かること", items, s, e, ch))
        else:
            _extend(scenes, block_card("promise", j - 1, ch))

    # --- 本編 ---
    for i, sec in enumerate(script.sections):
        chunks = chunk_lines(lines_of(f"s{i}"), target, lo, hi, pivots)
        if not chunks:
            continue
        painter.words = f"{sec.visual.query} {sec.visual.image_prompt} {sec.beat}"
        pool = _section_pool(cfg, script, sec, i, chunks, painter)
        main_again = pool[0]
        used_cards: set[int] = set()
        k = 0                                   # 汎用プール（本体・写真・用語・出典）の消費位置
        for j, ch in enumerate(chunks):
            s, e = _span(ch)
            lo_i, hi_i = ch[0].index, ch[-1].index
            dg = next((g for g in sec.diagrams
                       if lo_i <= g.after_sentence <= hi_i and id(g) not in used_cards), None)
            hit = next((c for c in sec.cards
                        if lo_i <= c.after_sentence <= hi_i and id(c) not in used_cards), None)
            if j == 0 and pool:
                _extend(scenes, _call(pool[0], s, e, ch)); k = 1   # 最初は必ず本体（図表 / 見出し）
            elif dg is not None:                             # 図解が最優先（言葉だけで説明しない）
                used_cards.add(id(dg))
                _extend(scenes, painter.diagram(dg, s, e, lines=ch))
            elif hit is not None:
                used_cards.add(id(hit))
                scenes.append(painter.quote(hit.text, s, e, source=hit.source))
            elif k < len(pool):
                _extend(scenes, _call(pool[k], s, e, ch)); k += 1
            else:
                rest_g = [g for g in sec.diagrams if id(g) not in used_cards]
                rest = [c for c in sec.cards if id(c) not in used_cards]
                if rest_g:
                    used_cards.add(id(rest_g[0]))
                    _extend(scenes, painter.diagram(rest_g[0], s, e, lines=ch))
                elif rest:
                    used_cards.add(id(rest[0]))
                    scenes.append(painter.quote(rest[0].text, s, e, source=rest[0].source))
                elif (j % 2) == 0:
                    scenes.append(painter.quote(nominalize(_key_sentence(ch)), s, e))
                else:
                    _extend(scenes, _call(main_again, s, e, ch))

    # --- closing: 3行まとめ → アウトロ ---
    closing = chunk_lines(lines_of("closing"), target, lo, hi, pivots)
    for j, ch in enumerate(closing):
        s, e = _span(ch)
        if j == 0:
            cards = script.block_cards.get("closing") or []
            items = [c.text for c in cards][:3] or \
                    [plain_heading(x.rstrip("。")) for x in split_sentences(script.closing)][1:4]
            _extend(scenes, painter.bullets("今日のまとめ", items, s, e, ch))
        elif j == len(closing) - 1:
            scenes.append(painter.outro(s, e))
        else:
            _extend(scenes, block_card("closing", j - 1, ch))

    scenes.sort(key=lambda x: x.start)
    # 隣接シーンの隙間を埋める（無音区間で画が消えないように）
    for a, b in zip(scenes, scenes[1:]):
        a.end = b.start
    if scenes:
        scenes[-1].end = track.duration + 0.8

    stills = sum(1 for x in scenes if x.still)
    moving = sum(1 for x in scenes if x.background is not None or not x.still)
    log.info("シーン %d 枚（平均 %.1f秒 / 動きのある画面 %d 枚 / 動く写真 %d 枚）",
             len(scenes), (track.duration / max(len(scenes), 1)), moving, len(scenes) - stills)
    return scenes
