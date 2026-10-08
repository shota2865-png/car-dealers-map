"""伸びなかった本編のタイトルを、YouTube で検索されている言葉から付け直す.

本編は Shorts のフィードからはほぼ来ず、検索と関連動画で見つけてもらうしかない（週次レポート: 検索 2%）。
公開から数日たっても再生が少ない本編は、タイトルの頭が「人が検索窓に打たない言葉」（「日銀1.25%」など）のことが多い。
そこで、動画の中身に合う検索語を YouTube の検索候補から選び直し、タイトルの頭に置き直す。

  - 対象: 公開から retitle.min_age_days 日以上で、再生が retitle.max_views 回未満の本編（Shorts・総集編は除く）
  - 1 回の実行で retitle.per_run 本まで（変えた効果を見分けられるように少しずつ）。同じ動画は 1 回だけ
  - 元のタイトルは状態 DB（stage.old_title）に残す。戻すときはそれを使う
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from . import llm, metadata, searchdemand
from .config import Config
from .state import Store

log = logging.getLogger(__name__)

_SYSTEM = """あなたは日本語 YouTube のタイトル設計者です。公開済みの解説動画のタイトルを、検索で見つかるように付け直します。
視聴者は{audience}。

# 決まり
- まず、この動画の中身を表す「暮らしのお金の言葉」を 1〜3 個、bases に出す（例:「手取り」「住民税」「スマホ代」）。
  人が検索窓に打ち込む、そのままの表記にする。率（1.25%）や専門語（実質・料率）は入れない
- 次に、検索候補（YouTube で実際に検索されている言葉）の一覧が渡されたら、その中から**動画の中身にいちばん合う 1 つ**を keyword に選ぶ。
  合うものが無ければ keyword は空にする（中身と違う言葉で人を呼ばない）
- title は keyword から始め、40 字以内。中身と一致させ、煽り語（ヤバい・終わった・知らないと損）は使わない。【】は入れない
- hook_tag は 5〜10 字の引き（【】の中身だけ）。title と同じ語を繰り返さない
"""

_BASES_SCHEMA = llm.obj({"bases": llm.arr(llm.STR)})
_TITLE_SCHEMA = llm.obj({"keyword": llm.STR, "title": llm.STR, "hook_tag": llm.STR})


def candidates(cfg: Config, store: Store, views: dict[str, int], today: dt.date | None = None) -> list[Any]:
    """付け直す本編（再生の少ない順）。views = {動画 ID: 再生回数}."""
    today = today or dt.date.today()
    min_age = int(cfg.get("retitle.min_age_days", 3))
    max_views = int(cfg.get("retitle.max_views", 100))
    out = []
    for r in store.videos_by_status("uploaded"):
        st = r.stage or {}
        if not r.youtube_id or st.get("kind", "long") != "long" or st.get("retitled_at"):
            continue
        if not r.publish_at:
            continue
        try:
            pub = dt.datetime.fromisoformat(str(r.publish_at).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if (today - pub).days < min_age or r.youtube_id not in views:
            continue
        if views[r.youtube_id] >= max_views:
            continue
        out.append(r)
    return sorted(out, key=lambda r: views.get(r.youtube_id, 0))


def plan(cfg: Config, title: str) -> dict[str, str]:
    """新しいタイトル案。検索候補に合う言葉が無ければ空の dict（付け直さない）."""
    system = _SYSTEM.format(audience=cfg.get("channel.audience", ""))
    model = cfg.get("script.model", llm.DEFAULT_MODEL)
    bases = llm.complete_json(system, f"今のタイトル: {title}\n\nbases だけを出してください。", _BASES_SCHEMA,
                              model=model, effort="low").get("bases") or []
    exclude = [str(x) for x in (cfg.get("topics.search_exclude") or [])]
    found: list[str] = []
    for b in [str(x).strip() for x in bases][:3]:
        for s in searchdemand.suggest(b):
            if s.replace(" ", "").startswith(b.replace(" ", "")) and not any(x in s for x in exclude) and s not in found:
                found.append(s)
    if not found:
        log.info("検索候補が見つからないので付け直しません: %s", title)
        return {}
    data = llm.complete_json(
        system,
        f"今のタイトル: {title}\n\n検索候補（上ほど多く検索されている）:\n" + "\n".join(f"- {s}" for s in found[:20])
        + "\n\nkeyword・title・hook_tag を出してください。",
        _TITLE_SCHEMA, model=model, effort="medium")
    kw = str(data.get("keyword") or "").strip()
    if not kw or kw not in found:
        log.info("中身に合う検索語が無いので付け直しません: %s", title)
        return {}
    body = metadata.keyword_first(str(data.get("title") or ""), kw)
    new = metadata.format_title(cfg, body, str(data.get("hook_tag") or ""))
    if not new.startswith(kw) or new == title:
        return {}
    return {"keyword": kw, "title": new}


def run(cfg: Config, store: Store, apply: bool = False) -> list[dict[str, str]]:
    """付け直す本編を選んで案を作り、apply なら YouTube のタイトルを書き換える."""
    from . import youtube
    recs = store.videos_by_status("uploaded")
    ids = [r.youtube_id for r in recs if r.youtube_id and (r.stage or {}).get("kind", "long") == "long"]
    if not ids:
        return []
    views: dict[str, int] = {}
    svc = youtube.build_service(cfg)
    for i in range(0, len(ids), 50):
        res = svc.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for it in res.get("items", []):
            views[it["id"]] = int(it.get("statistics", {}).get("viewCount", 0))
    done = []
    for r in candidates(cfg, store, views)[: int(cfg.get("retitle.per_run", 1))]:
        p = plan(cfg, r.title or r.slug)
        if not p:
            continue
        row = {"slug": r.slug, "id": r.youtube_id, "views": str(views.get(r.youtube_id, 0)),
               "old": r.title or "", "new": p["title"], "keyword": p["keyword"]}
        if apply:
            try:
                youtube.update_video_metadata(cfg, store, r.youtube_id, title=p["title"])
            except Exception as exc:
                log.warning("タイトルの付け直しに失敗: %s %s", r.youtube_id, exc)
                continue
            store.update_video(r.slug, title=p["title"], stage={
                "retitled_at": dt.datetime.now(dt.timezone.utc).isoformat(), "old_title": r.title or "",
                "retitle_keyword": p["keyword"]})
        done.append(row)
    return done


def format_report(rows: list[dict[str, str]], applied: bool) -> str:
    if not rows:
        return "付け直す本編はありません（再生の少ない本編が無いか、検索候補に合う言葉が無かった）"
    head = "タイトルを付け直しました" if applied else "タイトルの付け直し案（--apply で反映）"
    lines = [head]
    for r in rows:
        lines.append(f"- https://youtu.be/{r['id']}（{r['views']} 回）\n    前: {r['old']}\n    後: {r['new']}  ［検索語: {r['keyword']}］")
    return "\n".join(lines)
