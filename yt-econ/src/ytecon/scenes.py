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

    def _bg(self, scene: Scene) -> Scene:
        """透過カード（PNG）なら動く背景を敷く。不透明カードならそのまま."""
        if scene.image.suffix.lower() == ".png":
            bg = self.picker.abstract(self.words, seed=self.n)
            if bg is not None:
                scene.background = bg
                scene.bg_offset = (self.n * 2.7) % max(footage.LOOP_SECONDS - 1.0, 1.0)
        return scene

    def title(self, text: str, start: float, end: float) -> Scene:
        p = assets.build_title_card(self.cfg, text, self._next("title"))
        return self._bg(Scene(p, start, end, True, "title", "タイトル"))

    def outro(self, start: float, end: float) -> Scene:
        p = assets.build_outro_card(self.cfg, self._next("outro"))
        return self._bg(Scene(p, start, end, True, "outro", "アウトロ"))

    def bullets(self, heading: str, items: list[str], start: float, end: float) -> Scene:
        p = assets.render_textcard(self.cfg, heading, items, self._next("bullets"))
        return self._bg(Scene(p, start, end, True, "card", f"箇条書き: {heading[:12]}"))

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
        pool.append(lambda s, e: painter.bullets(sec.heading, sec.on_screen, s, e))

    # 2. 体言止めの文字カード（台本の cards）。数字・固有名詞のある文の直後に出す
    for card in sorted(sec.cards, key=lambda c: c.after_sentence):
        pool.append(lambda s, e, c=card: painter.quote(c.text, s, e, source=c.source))

    # 2b. テロップ由来
    for cap in sec.captions:
        if cap.type == "KEYWORD":
            pool.append(lambda s, e, c=cap: painter.keyword(c.text, sec.heading, s, e))
        elif cap.type == "DATA":
            pool.append(lambda s, e, c=cap: painter.number(c.text, sec.heading, "", s, e))
        elif cap.type in ("EMPHASIS", "PUNCHLINE"):
            pool.append(lambda s, e, c=cap: painter.quote(c.text, s, e))

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

    def block_card(block: str, j: int, ch: list[Line]):
        """導入・締めの j 番目の枠に出すカード。台本の block_cards → 無ければ体言止めに寄せた一文."""
        cards = script.block_cards.get(block) or []
        if cards:
            c = cards[j % len(cards)]
            return painter.quote(c.text, _span(ch)[0], _span(ch)[1], source=c.source)
        return painter.quote(nominalize(_key_sentence(ch)), *_span(ch))

    # --- hook: タイトル → キーワード → カード ---
    hook = chunk_lines(lines_of("hook"), target, lo, hi, pivots)
    for j, ch in enumerate(hook):
        s, e = _span(ch)
        if j == 0:
            scenes.append(painter.title(script.topic_title, s, e))
        elif j == 1:
            main = (script.thumbnail_copy or {}).get("main") or script.topic_title
            scenes.append(painter.keyword(main, "", s, e))
        else:
            scenes.append(block_card("hook", j - 2, ch))

    # --- proof: 数字があれば数字カード → カード ---
    for j, ch in enumerate(chunk_lines(lines_of("proof"), target, lo, hi, pivots)):
        s, e = _span(ch)
        nums = _numbers(script.proof)
        if j == 0 and nums:
            value = " → ".join(nums[:2]) if len(nums) >= 2 else nums[0]
            scenes.append(painter.number(value, "数字で見る", "", s, e))
        else:
            scenes.append(block_card("proof", j, ch))

    # --- promise: この動画で分かること → カード ---
    for j, ch in enumerate(chunk_lines(lines_of("promise"), target, lo, hi, pivots)):
        s, e = _span(ch)
        if j == 0:
            cards = script.block_cards.get("promise") or []
            items = [c.text for c in cards][:3] or \
                    ([plain_heading(x.rstrip("。")) for x in split_sentences(script.promise)][1:4])
            scenes.append(painter.bullets("この動画で分かること", items, s, e))
        else:
            scenes.append(block_card("promise", j - 1, ch))

    # --- 本編 ---
    for i, sec in enumerate(script.sections):
        chunks = chunk_lines(lines_of(f"s{i}"), target, lo, hi, pivots)
        if not chunks:
            continue
        painter.words = f"{sec.visual.query} {sec.visual.image_prompt} {sec.beat}"
        pool = _section_pool(cfg, script, sec, i, chunks, painter)
        main_again = pool[0]
        for j, ch in enumerate(chunks):
            s, e = _span(ch)
            if j < len(pool):
                scenes.append(pool[j](s, e))
            elif (j - len(pool)) % 2 == 0:
                if sec.cards:
                    c = sec.cards[(j - len(pool)) // 2 % len(sec.cards)]
                    scenes.append(painter.quote(c.text, s, e, source=c.source))
                else:
                    scenes.append(painter.quote(nominalize(_key_sentence(ch)), s, e))
            else:
                scenes.append(main_again(s, e))

    # --- closing: 3行まとめ → アウトロ ---
    closing = chunk_lines(lines_of("closing"), target, lo, hi, pivots)
    for j, ch in enumerate(closing):
        s, e = _span(ch)
        if j == 0:
            cards = script.block_cards.get("closing") or []
            items = [c.text for c in cards][:3] or \
                    [plain_heading(x.rstrip("。")) for x in split_sentences(script.closing)][1:4]
            scenes.append(painter.bullets("今日のまとめ", items, s, e))
        elif j == len(closing) - 1:
            scenes.append(painter.outro(s, e))
        else:
            scenes.append(block_card("closing", j - 1, ch))

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
