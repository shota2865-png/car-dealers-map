"""実話ドキュメンタリー型（docu）の台本を AI に書かせる.

  make_spec(cfg, material, ...)   調べ終わった材料（経済の台本 / 心理学の本編 JSON）→ docu の台本（場面の並び）
  make_shorts(cfg, spec, n, ...)  本編の台本 → Shorts の台本 n 本
  validate(spec, ...)             AI が書いた台本の形を直す（知らない場面・足りない値・章の番号・使えない字）
  tiktok_candidates(cfg, title)   引用に使える実在の投稿（投稿文・再生数つき）。取れなければ空

数字は材料にあるものだけを使わせる（材料に無い数字・投稿は書かない）。引用する投稿は候補の URL に限る。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from . import llm
from .config import Config

log = logging.getLogger(__name__)

KINDS = {"statement", "kinetic", "number", "compare", "steps", "grid", "bars3d", "hbars", "calendar", "phone", "carousel",
         "article", "points", "accordion", "checklist", "chapter", "opening", "menu", "modal", "radio", "pulldown", "anim",
         "cta", "footage"}
PRESETS = {"jar_fill", "coin_stacks", "flow", "walk", "risk", "think"}

SCENE_GUIDE = """# 場面の種類（type と必要な値）。どの場面も "lines"（語り。1 行 12〜35 字、話し言葉）が要る。i 番目の部品は i 行目の語りで出る
テロップの記法: [赤字]  [[赤い帯]]  {黄緑}。矢印「→」は使わない（字が無い）。改行は \\n
- kinetic: text（1〜2 行の強い一言。1 字ずつ跳ねて出る）, size(80〜120)。冒頭のつかみ・章の締め・最後の問いに
- statement: text, 任意で small（上の小さな説明）, text2（i=1 で出る下の行）, size, size2
- number: value（"13兆8,214億円" "約3.7倍" など。円や倍まで書く）, label, note（出典や内訳）, count（数え上げるなら true、"万""億"を含む数字は false）
- compare: items:[{label, value, note?}]（2〜3 個。i 番目は i 行目で出る）, size
- hbars: items:[{label, value(数値), text(表示), hl?}]（割合・内訳）
- bars3d: title, labels[], values[数値], tops[表示する数字], hl[強調する番号], at[何行目で伸びるか], side（任意の一言）。立体の棒グラフ。3〜6 本
- grid: n(100), cols(10), icon("person" か "envelope"), from, to（何個埋めるか）, label（"{n}" が数に置きかわる）, side
- radio: question, options[3], choose（視聴者がつい選びがちな答え）, answer（正解）, at_choose(1), at_answer(2)。クイズ。lines は 3 行
- pulldown: label, options[], choose, result, at_choose, at_result
- steps: items:[{head, text, note?}]（ルールや手順を 1 つずつ出し替える）
- accordion: head, items:[{title, body}]（i+1 行目で i 番目が開く。注意点 3 つなど）。lines は items の数＋1
- checklist: head, items[], hl[]（学べること）
- modal: title, body, button, behind（閉じた後に見える一言）。冒頭の「でも、ちょっと待って」に
- anim: preset と値。キャラクターと物が動く 2D/3D アニメ
    jar_fill: values[行ごとの金額], jar_label, talk(true)   / coin_stacks: values[], labels[], tops[], hl[], at[]
    flow: nodes:[{label, icon("note"|"coin"|"envelope"|"person"), amount, hl?}]（3 個）, telop
    walk: marks:[{label, amount, pos(0〜1)}]   / risk: hazards["fire","thief","lost"]（lines は 4 行）
    think: thoughts:[{text, mood("smile"|"worry"|"happy"|"surprise"|"tired")}], talk(true)
- calendar: top, day, side
- article: title, text（記事・発表の引用。白いカードに黄色のマーカー）, telop
- phone: url, handle, quote（投稿の日本語訳のひと言）, meta（"220万回再生" など）, telops[]。※ 候補にある投稿だけ
- carousel: items:[{url, handle, quote, meta, telop, at}]。※ 候補にある投稿だけ
- chapter: index, total, title（章の扉。語りは 1 行）
- opening: label("データで検証" など), title（2 行、[赤字] で要の語）, sub
- menu: title("今日の目次"), items[章の題], offset(1)。lines は「今日はこの N つの順番で…」＋章ごとに 1 行
- cta: text（コメントを誘う問い）, at_click(1)。最後の場面。lines は 2 行
どの場面にも任意で source（"出典：金融庁（2026年6月末）" のような出典。数字を出す場面には必ず）, bg("red" は冒頭の強い場面だけ), cam を付けられる
"""

_SYSTEM = """あなたは日本語 YouTube の解説動画の構成作家です。黒緑の背景・白の極太テロップ・赤の強調・毎行の字幕で進む
「データで確かめるドキュメンタリー」の台本を、場面の並び（JSON）で書きます。語り手は「僕」の一人語り（落ち着いた男性）。

# 絶対の決まり
- 数字・固有名詞・出来事は「材料」にあるものだけを使う。材料に無い数字を作らない。丸めた数字を出すときは語りも同じ丸め方にする
- 引用する投稿（phone / carousel）は「投稿の候補」にある URL・再生数だけ。候補が無ければ使わない
- 冒頭 2 文で答えを言い切る。冒頭は kinetic → number か statement → modal（疑問）→ opening → menu の順
- 章は 4〜5 個。章ごとに「扉（chapter）→ 中身 4〜8 場面」。最後の 2 章は「光と影：3つの注意」（accordion を使う）と「この話から学べること」（checklist）
- 立体の棒グラフ（bars3d）・2D/3D アニメ（anim）・クイズ（radio）を、それぞれ 2 回以上使う。同じ種類を 3 回続けない
- 煽り語（ヤバい・終わった・知らないと損）は使わない。専門語は一言で言いかえる
- 最後は cta。チャンネル名の自己紹介はしない
{guide}
# 出力
JSON だけを出す（説明やコードの囲みは書かない）: {{"title": "...", "scenes": [...]}}
"""

_SHORTS_SYSTEM = """あなたは日本語 YouTube Shorts の構成作家です。本編の台本から、縦の Shorts を {n} 本作ります。
1 本は {seconds} 秒（語りの合計 {chars} 字ほど）、場面は 5〜7 個。
- 1 場面目は kinetic（bg "red"）で、数字か問いのつかみ。最後の場面は kinetic で視聴者への問い（「続きは本編で」とは言わない。ループする終わり方）
- 使ってよい場面: kinetic, statement, number, compare, hbars, bars3d, radio, anim（coin_stacks / think / jar_fill / flow）, phone（本編の台本にある url だけ）
- 各場面に "lead": 0.05 を付ける。数字は本編の台本にあるものだけ。数字を出す場面には source を付ける
- {n} 本は、それぞれ別の切り口（別の数字・別のクイズ）にする
{guide}
# 出力
JSON だけ: {{"shorts": [{{"title": "Shorts のタイトル（30 字以内）", "scenes": [...]}}, ...]}}
"""


def _parse_json(text: str) -> dict[str, Any]:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("JSON が見つかりません")
    return json.loads(t[a:b + 1])


def _model(cfg: Config) -> str:
    return str(cfg.get("docu.model", cfg.get("script.model", llm.DEFAULT_MODEL)))


def _clean_text(v: Any) -> Any:
    if isinstance(v, str):
        return v.replace("→", "から").replace("⇒", "から")
    if isinstance(v, list):
        return [_clean_text(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean_text(x) for k, x in v.items()}
    return v


def validate(spec: dict[str, Any], allowed_urls: set[str] | None = None, short: bool = False) -> dict[str, Any]:
    """AI の台本を、描ける形に直す。直せない場面は落とす."""
    spec = _clean_text(spec)
    allowed_urls = allowed_urls or set()
    out = []
    for sc in spec.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        kind = str(sc.get("type", ""))
        lines = [str(x).strip() for x in (sc.get("lines") or []) if str(x).strip()]
        if kind not in KINDS or not lines:
            continue
        sc["lines"] = lines
        if kind == "anim" and str(sc.get("preset")) not in PRESETS:
            continue
        if kind in ("phone", "footage") and sc.get("url") and sc["url"] not in allowed_urls:
            if kind == "footage" and sc.get("telop"):
                sc.pop("url", None)                  # 映像が無くてもテロップの場面として残す
            else:
                continue
        if kind == "footage" and not sc.get("url") and not sc.get("prompt") and not sc.get("telop"):
            continue
        if kind == "carousel":
            items = [it for it in (sc.get("items") or []) if it.get("url") in allowed_urls]
            if not items:
                continue
            for i, it in enumerate(items):
                it["at"] = min(int(it.get("at", i)), len(lines) - 1)
            sc["items"] = items
        if kind == "bars3d" or (kind == "anim" and sc.get("preset") == "coin_stacks"):
            try:
                vals = [float(str(v).replace(",", "")) for v in sc.get("values") or []]
            except ValueError:
                continue
            if not vals or max(vals) <= 0:
                continue
            sc["values"] = vals
            n = len(vals)
            sc["labels"] = (list(sc.get("labels") or []) + [""] * n)[:n]
            if sc.get("tops"):
                sc["tops"] = (list(sc["tops"]) + [""] * n)[:n]
            if sc.get("at"):
                sc["at"] = [min(int(a), len(lines) - 1) for a in (list(sc["at"]) + [len(lines) - 1] * n)[:n]]
        if kind == "radio":
            opts = sc.get("options") or []
            if len(opts) < 2:
                continue
            for k in ("choose", "answer"):
                if sc.get(k) is not None:
                    sc[k] = min(max(0, int(sc[k])), len(opts) - 1)
        if kind == "hbars":
            items = []
            for it in sc.get("items") or []:
                try:
                    it["value"] = float(str(it.get("value", 0)).replace(",", "").rstrip("%"))
                    items.append(it)
                except ValueError:
                    pass
            if not items:
                continue
            sc["items"] = items
        if kind == "number" and re.search(r"[万億千]", str(sc.get("value", ""))):
            sc["count"] = False
        if short:
            sc.setdefault("lead", 0.05)
        out.append(sc)
    # 章の番号と目次をそろえる
    chapters = [sc for sc in out if sc["type"] == "chapter"]
    for i, sc in enumerate(chapters):
        sc["index"], sc["total"] = i + 1, len(chapters)
    for sc in out:
        if sc["type"] == "menu" and chapters:
            sc["items"] = [str(c.get("title", "")) for c in chapters]
            sc["offset"] = 1
    spec["scenes"] = out
    return spec


def make_spec(cfg: Config, material: dict[str, Any], *, kind: str = "経済", minutes: float = 7.0,
              candidates: list[dict[str, Any]] | None = None, title: str = "") -> dict[str, Any]:
    """材料（調べ終わった台本や数字）から docu の台本を書く。2 回まで書き直す."""
    chars = int(minutes * 60 * float(cfg.get("docu.chars_per_sec", 7.2)))
    cand = candidates or []
    user = (f"# チャンネル\n{cfg.get('channel.name', '')}（{kind}）。視聴者: {cfg.get('channel.audience', '')}\n\n"
            f"# この回の題（タイトルの元）\n{title}\n\n"
            f"# 長さ\n語りの合計 {chars} 字ほど（約 {minutes:.0f} 分）。場面は {int(chars / 50)}〜{int(chars / 40)} 個\n\n"
            f"# 材料（ここにある事実・数字・出典だけを使う）\n{json.dumps(material, ensure_ascii=False)[:60000]}\n\n"
            "# 投稿の候補（引用してよいのはこれだけ。quote は desc の内容に忠実な日本語訳。meta は再生数）\n"
            + (json.dumps(cand, ensure_ascii=False) if cand else "（なし。phone / carousel は使わない）"))
    system = _SYSTEM.format(guide=SCENE_GUIDE)
    allowed = {c["url"] for c in cand if c.get("url")}
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            text = llm.complete_text(system, user + (f"\n\n# 前回の失敗\n{last_err}" if last_err else ""), model=_model(cfg), effort="high")
            spec = validate(_parse_json(text), allowed)
            n = len(spec["scenes"])
            got = sum(len(x) for sc in spec["scenes"] for x in sc["lines"])
            if n < 15 or got < chars * 0.55:
                raise ValueError(f"場面 {n} 個・{got} 字で短すぎます（{chars} 字ほど必要）")
            log.info("docu の台本: %d 場面 / %d 字", n, got)
            return spec
        except Exception as exc:
            last_err = exc
            log.warning("docu の台本を書き直します (%d/3): %s", attempt + 1, exc)
    raise RuntimeError(f"docu の台本を作れませんでした: {last_err}")


def make_shorts(cfg: Config, spec: dict[str, Any], n: int, *, seconds: str = "30〜40") -> list[dict[str, Any]]:
    """本編の台本から Shorts の台本を n 本."""
    lo, hi = [int(x) for x in re.findall(r"\d+", seconds)[:2]] or [30, 40]
    rate = float(cfg.get("docu.chars_per_sec", 7.2))
    system = _SHORTS_SYSTEM.format(n=n, seconds=seconds, chars=f"{int(lo * rate)}〜{int(hi * rate)}", guide=SCENE_GUIDE)
    allowed = {it.get("url") for sc in spec.get("scenes") or [] for it in ([sc] + list(sc.get("items") or [])) if isinstance(it, dict) and it.get("url")}
    user = "# 本編の台本\n" + json.dumps(spec, ensure_ascii=False)[:60000]
    last: Exception | None = None
    for attempt in range(2):
        try:
            data = _parse_json(llm.complete_text(system, user, model=_model(cfg), effort="medium"))
            out = []
            for s in data.get("shorts") or []:
                s = validate(s, allowed, short=True)
                if len(s["scenes"]) >= 3:
                    out.append(s)
            if out:
                return out[:n]
            raise ValueError("Shorts の台本が空です")
        except Exception as exc:
            last = exc
            log.warning("Shorts の台本を書き直します (%d/2): %s", attempt + 1, exc)
    raise RuntimeError(f"Shorts の台本を作れませんでした: {last}")


# ----------------------------------------------------------------------
# 引用に使える実在の投稿
# ----------------------------------------------------------------------
_TAGS_SCHEMA = llm.obj({"hashtags": llm.arr(llm.STR)})


def _node(args: list[str], timeout: float) -> str:
    import os

    from .docu_media import _node_path
    script = Path(__file__).resolve().parents[2] / "scripts" / "tiktok_info.cjs"
    env = {**os.environ, "NODE_PATH": os.environ.get("NODE_PATH") or _node_path()}
    return subprocess.run(["node", str(script), *args], capture_output=True, text=True, timeout=timeout, env=env, check=False).stdout


def tiktok_candidates(cfg: Config, title: str, material_hint: str = "", per_tag: int = 6) -> list[dict[str, Any]]:
    """題に合うハッシュタグを AI に 2 つ選ばせ、そのページの人気の投稿（投稿文・再生数）を集める。失敗したら空."""
    if not cfg.get("docu.tiktok", True):
        return []
    try:
        tags = llm.complete_json(
            "TikTok で、この話題の投稿が実際に集まっているハッシュタグを 2 つ選ぶ。英語圏の大きなタグを優先し、日本のタグでもよい。# は付けない。",
            f"話題: {title}\n{material_hint[:800]}", _TAGS_SCHEMA, model=_model(cfg), effort="low").get("hashtags") or []
    except Exception as exc:
        log.info("ハッシュタグを選べませんでした: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    min_plays = int(cfg.get("docu.tiktok_min_plays", 50000))
    for tag in [re.sub(r"[^\w]", "", str(t)) for t in tags][:2]:
        if not tag:
            continue
        try:
            urls = json.loads((_node(["--tag", f"https://www.tiktok.com/tag/{tag}?lang=en"], 150).strip().splitlines() or ["[]"])[-1])
            for line in _node(urls[:per_tag], 300).splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("desc") and int(d.get("plays") or 0) >= min_plays:
                    out.append({k: d.get(k) for k in ("url", "author", "desc", "plays", "likes", "date")})
        except Exception as exc:
            log.info("TikTok の投稿を集められませんでした（#%s）: %s", tag, exc)
    out.sort(key=lambda d: -int(d.get("plays") or 0))
    log.info("引用の候補: %d 件", len(out))
    return out[:8]
