"""週 1 回の振り返りレポート（YouTube Analytics）.

  ytecon report [--days 7] [--no-advice]

見るもの:
  - 全体: 再生・総再生時間・登録者の増減（前の週との比較）
  - どこから見られたか（トラフィックソース）。Shorts のフィードからどれだけ来ているか
  - 本編ごと: 再生・平均視聴時間・平均視聴率・30 秒時点で残っている割合・Shorts からの流入
  - Shorts ごと: 再生・平均視聴率（よかった順）
  - 次の打ち手（LLM。数字を根拠に 3〜5 個）
結果は <workdir>/goal/reports/weekly_<日付>.md にも残す（Actions の成果物に入る）。
Analytics の数字は 2〜3 日遅れて入るので、公開したばかりの動画は空欄になる。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

from . import domain, goals, llm, youtube
from .config import Config

log = logging.getLogger(__name__)

LONG_SECONDS = 180          # これより長いものを本編として扱う

_SOURCE_JA = {
    "SHORTS": "Shorts のフィード", "YT_SEARCH": "YouTube 検索", "YT_OTHER_PAGE": "YouTube のその他のページ",
    "YT_CHANNEL": "チャンネルページ", "RELATED_VIDEO": "関連動画", "SUBSCRIBER": "登録者（ホーム・通知）",
    "BROWSE": "ホーム・ブラウジング", "PLAYLIST": "再生リスト", "EXT_URL": "外部サイト", "NO_LINK_OTHER": "直接・不明",
    "NOTIFICATION": "通知", "END_SCREEN": "終了画面", "ANNOTATION": "カード", "SOUND_PAGE": "サウンドページ",
    "HASHTAGS": "ハッシュタグ", "YT_PLAYLIST_PAGE": "再生リストのページ",
}


def _iso_seconds(d: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


def _q(svc, start: dt.date, end: dt.date, **kw) -> list[list]:
    try:
        r = svc.reports().query(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(), **kw).execute()
        return r.get("rows") or []
    except Exception as exc:                         # 指標の組み合わせが使えない等。レポート全体は止めない
        log.warning("Analytics の取得に失敗: %s", str(exc)[:200])
        return []


def _videos_meta(cfg: Config, ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not ids:
        return out
    yt = youtube.build_service(cfg)
    for i in range(0, len(ids), 50):
        res = yt.videos().list(part="snippet,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in res.get("items", []):
            out[v["id"]] = {"title": v["snippet"]["title"], "seconds": _iso_seconds(v["contentDetails"].get("duration", "")),
                            "published": v["snippet"].get("publishedAt", "")[:10]}
    return out


def collect(cfg: Config, days: int = 7, today: dt.date | None = None) -> dict[str, Any]:
    """レポートの材料（数字だけ）を集める."""
    today = today or dt.date.today()
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    pstart, pend = start - dt.timedelta(days=days), start - dt.timedelta(days=1)
    svc = goals._analytics_service(cfg)

    def totals(a: dt.date, b: dt.date) -> dict[str, float]:
        rows = _q(svc, a, b, metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost")
        v = rows[0] if rows else [0, 0, 0, 0]
        return {"views": v[0], "minutes": v[1], "subs": v[2] - v[3]}

    per = _q(svc, start, end, metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
             dimensions="video", sort="-views", maxResults=50)
    meta = _videos_meta(cfg, [r[0] for r in per])
    long_, short = [], []
    for vid, views, minutes, avd, avp in per:
        m = meta.get(vid, {})
        row = {"id": vid, "title": m.get("title", vid), "seconds": m.get("seconds", 0), "published": m.get("published", ""),
               "views": views, "minutes": minutes, "avg_seconds": avd, "avg_percent": avp}
        (long_ if row["seconds"] > LONG_SECONDS else short).append(row)
    for row in long_:
        src = _q(svc, start, end, metrics="views", dimensions="insightTrafficSourceType", filters=f"video=={row['id']}")
        row["from_shorts"] = sum(v for k, v in src if k == "SHORTS")
        row["sources"] = {k: v for k, v in src}
        ret = _q(svc, start, end, metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
                 filters=f"video=={row['id']};audienceType==ORGANIC")
        if ret and row["seconds"]:
            target = min(1.0, 30 / row["seconds"])
            row["kept_30s"] = min(ret, key=lambda r: abs(r[0] - target))[1]
    sources = _q(svc, start, end, metrics="views", dimensions="insightTrafficSourceType", sort="-views")
    return {"channel": str(cfg.get("channel.name", "")), "start": start.isoformat(), "end": end.isoformat(),
            "now": totals(start, end), "prev": totals(pstart, pend), "sources": sources,
            "long": long_, "short": short}


def _pct(a: float, b: float) -> str:
    if not b:
        return "—"
    return f"{(a - b) / b * 100:+.0f}%"


def render(data: dict[str, Any]) -> str:
    n, p = data["now"], data["prev"]
    lines = [f"# 週次レポート: {data['channel']}（{data['start']}〜{data['end']}）", "",
             "## 全体", "",
             "| | この週 | 前の週 | 変化 |", "|---|---:|---:|---:|",
             f"| 再生 | {n['views']:,} | {p['views']:,} | {_pct(n['views'], p['views'])} |",
             f"| 総再生時間（分） | {n['minutes']:,} | {p['minutes']:,} | {_pct(n['minutes'], p['minutes'])} |",
             f"| 登録者の増減 | {n['subs']:+,} | {p['subs']:+,} | |", ""]
    total = sum(v for _, v in data["sources"]) or 1
    lines += ["## どこから見られたか", ""]
    for k, v in data["sources"][:6]:
        lines.append(f"- {_SOURCE_JA.get(k, k)}: {v:,}（{v / total * 100:.0f}%）")
    lines += ["", "## 本編", ""]
    if data["long"]:
        lines += ["| タイトル | 再生 | 平均視聴 | 平均視聴率 | 30 秒で残った割合 | Shorts から |", "|---|---:|---:|---:|---:|---:|"]
        for r in data["long"]:
            kept = f"{r['kept_30s'] * 100:.0f}%" if r.get("kept_30s") is not None else "—"
            lines.append(f"| {r['title'][:36]} | {r['views']:,} | {r['avg_seconds'] // 60}分{r['avg_seconds'] % 60:02d}秒 | "
                         f"{r['avg_percent']:.0f}% | {kept} | {r.get('from_shorts', 0):,} |")
    else:
        lines.append("（この週に再生された本編はありません）")
    lines += ["", "## Shorts（よかった順）", ""]
    if data["short"]:
        lines += ["| タイトル | 再生 | 平均視聴率 |", "|---|---:|---:|"]
        for r in sorted(data["short"], key=lambda r: -r["views"])[:10]:
            lines.append(f"| {r['title'][:40]} | {r['views']:,} | {r['avg_percent']:.0f}% |")
    else:
        lines.append("（この週に再生された Shorts はありません）")
    return "\n".join(lines) + "\n"


_ADVICE_SYSTEM = """あなたは YouTube チャンネル「{name}」（{field}）の運用担当です。
週次の数字を見て、次の週に試すことを 3〜5 個、優先順に書きます。

- 数字を根拠にする（「◯◯が△％なので」）。数字に無いことは推測だと断る
- いちばんの課題は、Shorts は見られているのに本編に人が来ないこと。Shorts から本編への導線、本編の最初の 30 秒、タイトルとサムネを優先して見る
- 自動で作っている前提なので、台本の型・タイトルの付け方・Shorts の選び方など「仕組みで変えられること」を書く
- API では自動化できないもの（Shorts の「関連動画」の設定、コメントの固定、終了画面、サムネの A/B テスト）は「手作業」と明記する
- Shorts には本編へのリンクのコメントを自動で付けている（固定表示はしていない）
- 小学 5 年生にも分かる言葉で、1 項目 2 文以内。箇条書きだけを返す"""


def advice(cfg: Config, data: dict[str, Any]) -> str:
    slim = {k: data[k] for k in ("now", "prev", "sources")}
    slim["long"] = [{k: r.get(k) for k in ("title", "seconds", "views", "avg_seconds", "avg_percent", "kept_30s", "from_shorts")} for r in data["long"]]
    slim["short"] = [{k: r.get(k) for k in ("title", "views", "avg_percent")} for r in sorted(data["short"], key=lambda r: -r["views"])[:10]]
    system = _ADVICE_SYSTEM.format(name=data["channel"], field=domain.field(cfg))
    return llm.complete_text(system, json.dumps(slim, ensure_ascii=False),
                             model=str(cfg.get("script.model", llm.DEFAULT_MODEL)), effort="medium").strip()


def weekly(cfg: Config, days: int = 7, today: dt.date | None = None, with_advice: bool = True) -> tuple[str, Path]:
    data = collect(cfg, days=days, today=today)
    text = render(data)
    if with_advice:
        try:
            text += "\n## 次の週に試すこと\n\n" + advice(cfg, data) + "\n"
        except Exception as exc:
            log.warning("打ち手の生成に失敗: %s", exc)
    out = goals.goal_dir(cfg) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"weekly_{data['end']}.md"
    path.write_text(text, encoding="utf-8")
    (out / f"weekly_{data['end']}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return text, path
