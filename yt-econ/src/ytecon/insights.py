"""伸びた動画の分析（ジャンル分け）・伸びた本編の続編・週 1 のまとめ（ytecon digest）.

- 毎日の ytecon goal が、動画ごとの数字を output/<ch>/goal/videos.json に残す（save_videos）
- 週 1 回、AI が動画をジャンルと切り口に分け（goal/genres.json に貯める）、ジャンルごとの平均を出す
- 伸びた本編は、次の企画選びで「続編」として 1 本ぶん差し込む（topics.select_topics が pick_sequel を呼ぶ）
- まとめはスマホで読める短い Markdown（ytecon digest）。Actions が GitHub の Issue に出して通知する
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import statistics
from pathlib import Path
from typing import Any

from . import llm
from .config import Config

log = logging.getLogger(__name__)

JST = dt.timezone(dt.timedelta(hours=9))


def _today() -> dt.date:
    return dt.datetime.now(JST).date()


# ----------------------------------------------------------------------
# 動画ごとの数字（ytecon goal が毎日書く）
# ----------------------------------------------------------------------
def _goal_dir(cfg: Config) -> Path:
    d = cfg.workdir / "goal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def save_videos(cfg: Config, day: dt.date, videos: list[dict[str, Any]]) -> Path:
    path = _goal_dir(cfg) / "videos.json"
    _write(path, {"date": day.isoformat(), "videos": videos})
    return path


def load_videos(cfg: Config) -> list[dict[str, Any]]:
    return list(_read(_goal_dir(cfg) / "videos.json", {}).get("videos") or [])


def views_of(v: dict[str, Any]) -> int:
    """本編は公開からの通算、無ければ期間内の再生."""
    return int(v.get("lifetime_views", v.get("views", 0)) or 0)


def _longs(videos: list[dict[str, Any]], min_age: int = 7, max_age: int | None = None) -> list[dict[str, Any]]:
    return [v for v in videos if v.get("kind", "long") == "long" and v.get("age_days", 0) >= min_age
            and (max_age is None or v.get("age_days", 0) <= max_age)]


# ----------------------------------------------------------------------
# ジャンル分け
# ----------------------------------------------------------------------
_GENRE_SYSTEM = """あなたは YouTube チャンネル「{name}」の分析担当です。
動画のタイトルを見て、1 本ずつ「ジャンル」と「切り口」を付けます。

- ジャンル = 何の話か（例: 税金・手取り / 給料・働き方 / 投資・NISA / 物価・節約 / 奨学金・学生 / 年金・保険 / 人間関係 / 不安・ストレス）。
  6〜10 字ほど。同じ話は必ず同じ名前にする。すでにあるジャンル名に当てはまるときは、その名前をそのまま使う
- ジャンルは全部で 10 個まで。細かく分けすぎない（「住民税」と「所得税」は同じ「税金・手取り」）
- 切り口 = どう見せたか（例: 数字で驚かせる / 損得を比べる / 制度の仕組み / ニュース解説 / あるある・共感 / やり方の手順）。8 字ほど
- タイトルだけで決める。分からないときも一番近いものを選ぶ"""

_GENRE_SCHEMA = llm.obj({"items": llm.arr(llm.obj({"id": llm.STR, "genre": llm.STR, "angle": llm.STR}))})


def load_genres(cfg: Config) -> dict[str, dict[str, str]]:
    return dict(_read(_goal_dir(cfg) / "genres.json", {}))


def classify(cfg: Config, videos: list[dict[str, Any]], *, limit: int = 120) -> dict[str, dict[str, str]]:
    """まだ分けていない動画だけ AI に分けてもらい、genres.json に足す（一度付けたジャンルは変えない）."""
    labels = load_genres(cfg)
    todo = [v for v in videos if v.get("id") and v["id"] not in labels and v.get("title")]
    todo.sort(key=lambda v: (v.get("kind", "long") != "long", -views_of(v)))      # 本編と伸びた動画から
    todo = todo[:limit]
    if not todo:
        return labels
    known = sorted({x["genre"] for x in labels.values() if x.get("genre")})
    user = ("# すでにあるジャンル\n" + ("\n".join(f"- {g}" for g in known) or "(まだ無い)")
            + "\n\n# 分ける動画（id / 種類 / タイトル）\n"
            + "\n".join(f"- {v['id']} / {'本編' if v.get('kind', 'long') == 'long' else 'Shorts'} / {v['title']}" for v in todo))
    data = llm.complete_json(_GENRE_SYSTEM.format(name=cfg.get("channel.name", "")), user, _GENRE_SCHEMA,
                             model=cfg.get("insights.model", llm.DEFAULT_MODEL), effort="low", max_tokens=8000)
    ids = {v["id"] for v in todo}
    for it in data.get("items") or []:
        if it.get("id") in ids and str(it.get("genre", "")).strip():
            labels[it["id"]] = {"genre": str(it["genre"]).strip()[:16], "angle": str(it.get("angle", "")).strip()[:16]}
    _write(_goal_dir(cfg) / "genres.json", labels)
    return labels


def genre_table(videos: list[dict[str, Any]], labels: dict[str, dict[str, str]], *,
                key: str = "genre", min_age: int = 7) -> list[dict[str, Any]]:
    """ジャンル（または切り口）ごとの本編の平均と Shorts の平均。本編の平均が多い順."""
    rows: dict[str, dict[str, list[int]]] = {}
    for v in videos:
        name = (labels.get(v.get("id", "")) or {}).get(key)
        if not name or v.get("age_days", 0) < (min_age if v.get("kind", "long") == "long" else 3):
            continue
        r = rows.setdefault(name, {"long": [], "short": []})
        r["long" if v.get("kind", "long") == "long" else "short"].append(views_of(v))
    out = [{"name": k, "long_n": len(r["long"]), "long_avg": statistics.mean(r["long"]) if r["long"] else None,
            "short_n": len(r["short"]), "short_avg": statistics.mean(r["short"]) if r["short"] else None}
           for k, r in rows.items()]
    out.sort(key=lambda r: (r["long_avg"] is None, -(r["long_avg"] or 0), -(r["short_avg"] or 0)))
    return out


def spin_off_candidates(table: list[dict[str, Any]], channel_avg: float, *, min_n: int = 3, ratio: float = 2.0) -> list[str]:
    """チャンネルの平均の ratio 倍以上見られていて、本数もあるジャンル（別のチャンネルに分ける候補）."""
    return [r["name"] for r in table
            if r["long_n"] >= min_n and r["long_avg"] is not None and channel_avg > 0 and r["long_avg"] >= ratio * channel_avg]


# ----------------------------------------------------------------------
# 伸びた本編の続編
# ----------------------------------------------------------------------
def _sequels_path(cfg: Config) -> Path:
    return _goal_dir(cfg) / "sequels.json"


def pick_sequel(cfg: Config, today: dt.date | None = None) -> dict[str, Any] | None:
    """続編を作る本編を 1 本選ぶ。無ければ None.

    公開 min_age_days〜max_age_days 日の本編のうち、通算再生が min_views 以上かつ
    本編の中央値の ratio 倍以上のもの。続編を出してから min_gap_days 日は次を出さない。同じ本編は 1 回だけ。
    """
    if not bool(cfg.get("topics.sequels.enabled", False)):
        return None
    today = today or _today()
    done = _read(_sequels_path(cfg), [])
    if done:
        last = max(dt.date.fromisoformat(d["date"]) for d in done)
        if (today - last).days < int(cfg.get("topics.sequels.min_gap_days", 3)):
            return None
    videos = load_videos(cfg)
    base = [views_of(v) for v in _longs(videos)]
    if not base:
        return None
    median = statistics.median(base)
    floor = max(float(cfg.get("topics.sequels.min_views", 200)), float(cfg.get("topics.sequels.ratio", 2.0)) * median)
    used = {d["parent_id"] for d in done}
    cands = [v for v in _longs(videos, int(cfg.get("topics.sequels.min_age_days", 7)), int(cfg.get("topics.sequels.max_age_days", 60)))
             if v.get("id") not in used and views_of(v) >= floor]
    if not cands:
        return None
    best = max(cands, key=views_of)
    genre = (load_genres(cfg).get(best["id"]) or {}).get("genre", "")
    return {"id": best["id"], "title": best.get("title", ""), "views": views_of(best), "median": int(median), "genre": genre}


def sequel_block(seq: dict[str, Any], count: int) -> str:
    """企画選びのプロンプトに足す文."""
    return f"""
# 伸びた本編の続編（{count} 本のうち 1 本目は必ずこれにする）
前に出した本編「{seq['title']}」が {seq['views']:,} 回見られた（このチャンネルの本編のふつうは {seq['median']:,} 回）。
- この回を見た人が「次に知りたくなる疑問」に答える続編を作る。前の回と同じ説明を繰り返さない
- タイトルは前の回とは別の言葉で始める（同じ言葉だと重複として落ちる）。「続編」「第2弾」とは書かない
- 1 本目の企画として返す
"""


def mark_sequel(cfg: Config, seq: dict[str, Any], child_title: str, today: dt.date | None = None) -> None:
    done = _read(_sequels_path(cfg), [])
    done.append({"date": (today or _today()).isoformat(), "parent_id": seq["id"],
                 "parent_title": seq.get("title", ""), "parent_views": seq.get("views", 0), "child_title": child_title})
    _write(_sequels_path(cfg), done)
    log.info("伸びた本編の続編を入れました: 「%s」（%s 回）の続き →「%s」", seq.get("title"), seq.get("views"), child_title)


def genre_block(cfg: Config) -> str:
    """企画選びのプロンプトに足す「伸びているジャンル」（分けた結果がまだ無ければ空）."""
    table = [r for r in genre_table(load_videos(cfg), load_genres(cfg)) if r["long_n"] >= 2]
    if len(table) < 2:
        return ""
    top = "\n".join(f"- {r['name']}: 本編の平均 {r['long_avg']:,.0f} 回（{r['long_n']} 本）" for r in table[:3])
    low = "\n".join(f"- {r['name']}: 本編の平均 {r['long_avg']:,.0f} 回（{r['long_n']} 本）" for r in table[-2:])
    return f"""
# ジャンルごとの本編の見られ方（自分のチャンネルの実績）
よく見られている:
{top}
あまり見られていない:
{low}
- 企画は、よく見られているジャンルを多めにする（全部を同じにはしない）
"""


# ----------------------------------------------------------------------
# 週 1 のまとめ（スマホで読む）
# ----------------------------------------------------------------------
_WHY_SYSTEM = """あなたは YouTube チャンネル「{name}」の分析担当です。数字を見て、
「なぜ伸びたか」を 3 つ、「来週やること」を 2 つ書きます。

- 数字を根拠にする（「◯◯のジャンルは平均△回で、ほかの 3 倍」）。数字に無いことは「たぶん」と書く
- 小学 5 年生にも分かる言葉で、1 つ 1 文。専門用語は使わない
- 「来週やること」は、自動で作っている前提で仕組みで変えられること（企画のジャンル・タイトルの付け方・切り口）"""

_WHY_SCHEMA = llm.obj({"why": llm.arr(llm.STR), "next": llm.arr(llm.STR)})


def digest(cfg: Config, *, target: float = 500, min_age: int = 7, use_llm: bool = True) -> str:
    """スマホで読む週 1 のまとめ（Markdown）."""
    videos = load_videos(cfg)
    name = cfg.get("channel.name", "")
    if not videos:
        return f"## {name}\n\n動画の数字がまだありません（毎日の ytecon goal が動くと出ます）。"
    labels = load_genres(cfg)
    if use_llm:
        try:
            labels = classify(cfg, videos)
        except Exception as exc:  # noqa: BLE001  AI が落ちてもまとめは出す
            log.warning("ジャンル分けができませんでした: %s", str(exc).splitlines()[0][:160])
    longs = sorted(_longs(videos, min_age), key=lambda v: v.get("age_days", 0))
    recent = [views_of(v) for v in longs[:7]]
    avg = statistics.mean(recent) if recent else 0.0
    lines = [f"## {name}（{_today():%m/%d}）", ""]
    lines.append(f"**本編の平均 {avg:,.0f} 回** / 目標 {target:,.0f} 回（公開 {min_age} 日以上の新しい {len(recent)} 本）")
    lines.append("")
    top = sorted(_longs(videos, min_age, 60), key=lambda v: -views_of(v))[:3]
    if top:
        lines.append("**伸びた本編（直近 2 か月）**")
        for i, v in enumerate(top, 1):
            g = (labels.get(v["id"]) or {}).get("genre", "")
            lines.append(f"{i}. {views_of(v):,} 回 {v.get('title', '')[:40]}" + (f"（{g}）" if g else ""))
        lines.append("")
    table = genre_table(videos, labels, min_age=min_age)
    if table:
        lines.append("**ジャンル別（本編の平均 / Shorts の平均）**")
        for r in table[:8]:
            la = f"{r['long_avg']:,.0f} 回（{r['long_n']} 本）" if r["long_avg"] is not None else "—"
            sa = f"{r['short_avg']:,.0f} 回" if r["short_avg"] is not None else "—"
            lines.append(f"- {r['name']}: {la} / {sa}")
        all_long = [views_of(v) for v in _longs(videos, min_age)]
        spin = spin_off_candidates(table, statistics.mean(all_long) if all_long else 0)
        if spin:
            lines.append(f"- 別のチャンネルに分けられそう: {'、'.join(spin)}（本編がチャンネルの平均の 2 倍以上・3 本以上）")
        lines.append("")
    by_fmt: dict[str, list[int]] = {}
    for v in _longs(videos, min_age, 45):
        by_fmt.setdefault(v.get("format") or "今まで", []).append(views_of(v))
    if len(by_fmt) >= 2:
        lines.append("**作り方の比較（本編の平均）**: " + " / ".join(
            f"{k} {statistics.mean(x):,.0f} 回（{len(x)} 本）" for k, x in sorted(by_fmt.items())))
        lines.append("")
    seqs = _read(_sequels_path(cfg), [])
    if seqs:
        s = seqs[-1]
        lines.append(f"**最近入れた続編**: 「{s['parent_title'][:30]}」（{s['parent_views']:,} 回）の続き →「{s['child_title'][:30]}」（{s['date']}）")
        lines.append("")
    if use_llm and table:
        try:
            facts = "\n".join(lines)
            data = llm.complete_json(_WHY_SYSTEM.format(name=name), facts, _WHY_SCHEMA,
                                     model=cfg.get("insights.model", llm.DEFAULT_MODEL), effort="low", max_tokens=4000)
            if data.get("why"):
                lines += ["**なぜ伸びたか**"] + [f"- {x}" for x in data["why"][:3]] + [""]
            if data.get("next"):
                lines += ["**来週やること**"] + [f"- {x}" for x in data["next"][:2]] + [""]
        except Exception as exc:  # noqa: BLE001  AI が落ちてもまとめは出す
            log.warning("まとめの文章が作れませんでした: %s", str(exc).splitlines()[0][:160])
    return "\n".join(lines).rstrip() + "\n"
