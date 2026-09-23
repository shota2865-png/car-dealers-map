"""目標（config/goals.yaml）に対する進捗・必要ペース・打ち手を出す（ytecon goal）.

数字の取り方（上から順に試す）:
  1. YouTube Analytics API v2 — 日別の再生・総再生時間・登録者の増減、動画別の平均視聴率
     （refresh token に yt-analytics.readonly のスコープが要る。scripts/auth_youtube.py を再実行）
  2. YouTube Data API v3 — チャンネルと動画の公開統計（既存のトークンで取れる。再生時間は取れない）
  3. どちらも無ければ、計画だけを表示する（--offline）

毎回の数字は output/goal/snapshots.jsonl に追記し、前回との差分から日次の伸びを出す。
「何をすべきか」は rules（1 本ごとの合否ライン）と gates（週の関門）から機械的に決める。
人が見る数字（インプレッションのクリック率・視聴者維持率グラフ）は API に無いので、
それは YouTube Studio で週 1 回見る（docs/目標_月50万再生.md の 4 節）。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import Config

log = logging.getLogger(__name__)

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


# ----------------------------------------------------------------------
@dataclass
class Goal:
    name: str
    start: dt.date
    days: int
    views: int
    mix: dict[str, float]
    per_day: dict[str, int]
    ypp: dict[str, int]
    gates: list[dict[str, int]]
    rules: dict[str, dict[str, float]]

    @property
    def end(self) -> dt.date:
        return self.start + dt.timedelta(days=self.days - 1)

    def day_index(self, today: dt.date) -> int:
        """今日が何日目か（1 始まり。開始前は 0、終了後は days）."""
        d = (today - self.start).days + 1
        return max(0, min(d, self.days))


def goals_path(cfg: Config) -> Path:
    p = str(cfg.get("goal.file", "") or "").strip()
    q = Path(p) if p else cfg.root / "config" / "goals.yaml"
    return q if q.is_absolute() else cfg.root / q


def load_goal(cfg: Config, path: Path | None = None) -> Goal:
    raw = yaml.safe_load((path or goals_path(cfg)).read_text(encoding="utf-8")) or {}
    g = raw.get("goal") or {}
    start = g.get("start")
    if isinstance(start, str):
        start = dt.date.fromisoformat(start)
    if isinstance(start, dt.datetime):
        start = start.date()
    return Goal(
        name=str(g.get("name", "目標")),
        start=start or dt.date.today(),
        days=int(g.get("days", 30)),
        views=int(g.get("views", 500_000)),
        mix={k: float(v) for k, v in (g.get("mix") or {"shorts": 0.85, "long": 0.15}).items()},
        per_day={k: int(v) for k, v in (g.get("per_day") or {"long": 1, "shorts": 3}).items()},
        ypp={k: int(v) for k, v in (raw.get("ypp") or {}).items()},
        gates=[{k: int(v) for k, v in x.items()} for x in (raw.get("gates") or [])],
        rules={k: {kk: float(vv) for kk, vv in (v or {}).items()} for k, v in (raw.get("rules") or {}).items()},
    )


# ----------------------------------------------------------------------
@dataclass
class Progress:
    """ある日の時点の数字。取れなかったものは None."""
    date: dt.date
    views: int = 0                        # 目標期間の累計再生
    subscribers: int = 0                  # 現在の登録者数
    watch_hours: float | None = None      # 目標期間の総再生時間（長尺 + Shorts。Analytics のみ）
    daily_views: list[int] = field(default_factory=list)   # 開始日からの日別再生（Analytics のみ）
    videos: list[dict[str, Any]] = field(default_factory=list)  # 動画別（id, title, kind, views, avg_pct, age_days）
    source: str = "offline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(), "views": self.views, "subscribers": self.subscribers,
            "watch_hours": self.watch_hours, "daily_views": self.daily_views,
            "videos": self.videos, "source": self.source,
        }


def pace(goal: Goal, p: Progress, today: dt.date | None = None) -> dict[str, Any]:
    """必要ペースと予測。今日を含めた残り日数で割る."""
    today = today or p.date
    day = goal.day_index(today)
    remaining = goal.days if day == 0 else goal.days - day + 1      # 今日を含めた残り日数
    left = max(goal.views - p.views, 0)
    required = left / remaining if remaining else float(left)
    expected_today = goal.views * day / goal.days

    recent = p.daily_views[-7:] if p.daily_views else []
    recent_avg = sum(recent) / len(recent) if recent else (p.views / day if day else 0.0)
    projected = p.views + recent_avg * remaining

    gate = next((g for g in goal.gates if g.get("day", 0) >= day), None)
    passed_gates = [g for g in goal.gates if g.get("day", 0) <= day]
    last_gate = passed_gates[-1] if passed_gates else None
    gate_ok = None
    if last_gate:
        gate_ok = p.views >= last_gate.get("views", 0) and p.subscribers >= last_gate.get("subscribers", 0)

    status = "ahead" if p.views >= expected_today * 1.1 else ("on_track" if p.views >= expected_today * 0.8 else "behind")
    if day == 0:
        status = "not_started"
    return {
        "day": day, "days": goal.days, "remaining_days": remaining,
        "views": p.views, "target": goal.views, "left": left,
        "expected_today": int(expected_today), "required_per_day": int(required),
        "recent_avg_per_day": int(recent_avg), "projected_total": int(projected),
        "status": status, "next_gate": gate, "last_gate": last_gate, "last_gate_ok": gate_ok,
        "ypp": ypp_status(goal, p),
    }


def ypp_status(goal: Goal, p: Progress) -> dict[str, Any]:
    subs_need = goal.ypp.get("subscribers", 1000)
    hours_need = goal.ypp.get("watch_hours_12m", 4000)
    out: dict[str, Any] = {
        "subscribers": p.subscribers, "subscribers_need": subs_need,
        "subscribers_pct": round(100 * min(p.subscribers / subs_need, 1.0), 1) if subs_need else None,
    }
    if p.watch_hours is not None:
        out["watch_hours"] = round(p.watch_hours, 1)
        out["watch_hours_need"] = hours_need
        out["watch_hours_pct"] = round(100 * min(p.watch_hours / hours_need, 1.0), 1) if hours_need else None
    return out


def recommend(goal: Goal, p: Progress, pc: dict[str, Any]) -> list[str]:
    """数字から打ち手を決める。曖昧な助言は出さず、設定のどこを動かすかまで書く."""
    acts: list[str] = []
    if pc["status"] == "not_started":
        acts.append("まだ開始前。初日は本編 1 本 + Shorts 3 本を予約し、翌日この画面で数字が取れるか確認する")
        return acts

    if pc["status"] == "behind":
        acts.append(f"目標ペースの {pc['views'] / max(pc['expected_today'], 1):.0%}。"
                    f"残り {pc['remaining_days']} 日で 1 日 {pc['required_per_day']:,} 再生が必要")
        shorts_share = goal.mix.get("shorts", 0.85)
        acts.append(f"再生の {shorts_share:.0%} は Shorts の想定。まず shorts.per_video を +1（1 日 {goal.per_day.get('shorts', 3) + 1} 本）にし、"
                    "本編は本数を増やさない（尺と品質を守るほうが総再生時間に効く）")
    if pc.get("last_gate") and pc.get("last_gate_ok") is False:
        g = pc["last_gate"]
        acts.append(f"{g['day']} 日目の関門（{g['views']:,} 再生 / 登録 {g['subscribers']:,}）を下回った。"
                    "同じ型を続けない: 直近 7 日で平均視聴率が最も高かった Shorts の型（区間の長さ・出だし）に寄せる")

    rules_l = goal.rules.get("long", {})
    rules_s = goal.rules.get("shorts", {})
    weak_long = [v for v in p.videos if v.get("kind") == "long" and v.get("age_days", 0) >= 7
                 and (v.get("views", 0) < rules_l.get("views_7d_min", 300)
                      or (v.get("avg_pct") is not None and v["avg_pct"] < rules_l.get("avg_view_pct_min", 0.30)))]
    for v in weak_long[:3]:
        why = []
        if v.get("views", 0) < rules_l.get("views_7d_min", 300):
            why.append(f"7 日で {v.get('views', 0):,} 再生")
        if v.get("avg_pct") is not None and v["avg_pct"] < rules_l.get("avg_view_pct_min", 0.30):
            why.append(f"平均視聴率 {v['avg_pct']:.0%}")
        acts.append(f"本編「{v.get('title', v.get('id'))[:28]}」: {' / '.join(why)} → "
                    "サムネとタイトルを作り直す（ytecon revive --apply の対象にする）。"
                    "視聴率が低いなら次回から hook を 1 文短く")
    weak_shorts = [v for v in p.videos if v.get("kind") == "short" and v.get("age_days", 0) >= 3
                   and v.get("avg_pct") is not None and v["avg_pct"] < rules_s.get("avg_view_pct_min", 0.75)]
    if weak_shorts:
        acts.append(f"平均視聴率が {rules_s.get('avg_view_pct_min', 0.75):.0%} 未満の Shorts が {len(weak_shorts)} 本。"
                    "shorts.max_seconds を 45 に下げ、出だしを聞き役の疑問文に限定する（shorts.py の点数の重み）")
    good_shorts = sorted([v for v in p.videos if v.get("kind") == "short"], key=lambda v: -v.get("views", 0))[:1]
    if good_shorts and good_shorts[0].get("views", 0) >= 5 * max(1, pc["recent_avg_per_day"] / max(goal.per_day.get("shorts", 3), 1)):
        v = good_shorts[0]
        acts.append(f"Shorts「{v.get('title', '')[:28]}」が突出（{v.get('views', 0):,} 再生）。"
                    "同じテーマの派生を schedule.yaml に 2 本入れる（当たった型は 1 週間以内に重ねる）")

    y = pc["ypp"]
    if y.get("subscribers_pct") is not None and y["subscribers_pct"] < 100 and pc["day"] >= 14:
        acts.append(f"登録者 {y['subscribers']:,} / {y['subscribers_need']:,}。"
                    "Shorts の最後の 1 文に「本編は 19 時」を入れる（shorts の締めの誘導）。"
                    "登録は本編視聴者から来るので、本編の概要欄 1 行目を「毎日 19:00」に固定")
    if not acts:
        acts.append("ペースどおり。設定は変えず、同じ型で続ける。週 1 回は Studio でクリック率と維持率だけ確認")
    return acts


# ----------------------------------------------------------------------
# 数字の取得
# ----------------------------------------------------------------------
def _analytics_service(cfg: Config):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build as gbuild
    from .youtube import TOKEN_URI

    from . import youtube as _yt
    _yt.check_channel_keys(cfg)
    creds = Credentials(
        token=None, refresh_token=cfg.env("YOUTUBE_REFRESH_TOKEN"),
        client_id=cfg.env("YOUTUBE_CLIENT_ID"), client_secret=cfg.env("YOUTUBE_CLIENT_SECRET"),
        token_uri=TOKEN_URI, scopes=[ANALYTICS_SCOPE],
    )
    return gbuild("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def _known_videos(store) -> list[dict[str, Any]]:
    """DB にある投稿済み動画（本編 / Shorts）。"""
    out = []
    for r in store.videos_by_status("uploaded"):
        if not r.youtube_id:
            continue
        kind = (r.stage or {}).get("kind", "long")
        out.append({"id": r.youtube_id, "title": r.title or r.slug, "kind": kind,
                    "publish_at": r.publish_at, "slug": r.slug})
    return out


def _age_days(publish_at: str | None, today: dt.date) -> int:
    if not publish_at:
        return 0
    try:
        d = dt.datetime.fromisoformat(publish_at.replace("Z", "+00:00")).date()
    except ValueError:
        return 0
    return max((today - d).days, 0)


def fetch_analytics(cfg: Config, store, goal: Goal, today: dt.date) -> Progress:
    """YouTube Analytics API。スコープが無ければ例外（呼び出し側が Data API に落とす）."""
    svc = _analytics_service(cfg)
    start = goal.start.isoformat()
    end = min(today, goal.end).isoformat()
    daily = svc.reports().query(
        ids="channel==MINE", startDate=start, endDate=end,
        metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost", dimensions="day", sort="day",
    ).execute()
    rows = daily.get("rows") or []
    daily_views = [int(r[1]) for r in rows]
    minutes = sum(float(r[2]) for r in rows)

    p = Progress(date=today, views=sum(daily_views), watch_hours=minutes / 60, daily_views=daily_views,
                 source="analytics")
    known = {v["id"]: v for v in _known_videos(store)}
    if known:
        ids = list(known)[:200]
        per = svc.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics="views,averageViewPercentage,estimatedMinutesWatched", dimensions="video",
            filters="video==" + ",".join(ids), sort="-views", maxResults=200,
        ).execute()
        for r in per.get("rows") or []:
            vid = r[0]
            k = known.get(vid, {})
            p.videos.append({"id": vid, "title": k.get("title", vid), "kind": k.get("kind", "long"),
                             "views": int(r[1]), "avg_pct": float(r[2]) / 100.0,
                             "watch_minutes": float(r[3]), "age_days": _age_days(k.get("publish_at"), today)})
    # 登録者の現在値は Data API（Analytics は増減しか返さない）
    try:
        from .youtube import build_service
        ch = build_service(cfg).channels().list(part="statistics", mine=True).execute()
        p.subscribers = int(ch["items"][0]["statistics"].get("subscriberCount", 0))
    except Exception as exc:
        log.warning("登録者数が取れませんでした: %s", exc)
    return p


def fetch_public_stats(cfg: Config, store, goal: Goal, today: dt.date) -> Progress:
    """Data API の公開統計だけで組み立てる（再生時間・視聴率は取れない）.

    期間の累計再生 = チャンネルの総再生数 − 開始時点の総再生数（初回に snapshots に保存した基準値）。
    """
    from .youtube import build_service
    svc = build_service(cfg)
    ch = svc.channels().list(part="statistics", mine=True).execute()
    st = ch["items"][0]["statistics"]
    lifetime = int(st.get("viewCount", 0))
    subs = int(st.get("subscriberCount", 0))

    base = baseline(cfg)
    if base is None:
        base = lifetime
        save_baseline(cfg, today, lifetime)
    p = Progress(date=today, views=max(lifetime - base, 0), subscribers=subs, source="data_api")

    known = _known_videos(store)
    for i in range(0, len(known), 50):
        chunk = known[i:i + 50]
        res = svc.videos().list(part="statistics", id=",".join(v["id"] for v in chunk)).execute()
        stats = {it["id"]: it.get("statistics", {}) for it in res.get("items", [])}
        for v in chunk:
            s = stats.get(v["id"], {})
            p.videos.append({"id": v["id"], "title": v["title"], "kind": v["kind"],
                             "views": int(s.get("viewCount", 0)), "avg_pct": None,
                             "age_days": _age_days(v.get("publish_at"), today)})
    return p


def fetch_progress(cfg: Config, store, goal: Goal, today: dt.date | None = None) -> Progress:
    today = today or dt.date.today()
    try:
        return fetch_analytics(cfg, store, goal, today)
    except Exception as exc:
        log.info("Analytics API は使えないので公開統計に切り替えます（%s）", str(exc).splitlines()[0][:120])
    return fetch_public_stats(cfg, store, goal, today)


# ----------------------------------------------------------------------
# 記録
# ----------------------------------------------------------------------
def goal_dir(cfg: Config) -> Path:
    d = cfg.workdir / "goal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def baseline(cfg: Config) -> int | None:
    p = goal_dir(cfg) / "baseline.json"
    if not p.exists():
        return None
    try:
        return int(json.loads(p.read_text(encoding="utf-8")).get("lifetime_views", 0))
    except (ValueError, json.JSONDecodeError):
        return None


def save_baseline(cfg: Config, day: dt.date, lifetime_views: int) -> None:
    (goal_dir(cfg) / "baseline.json").write_text(
        json.dumps({"date": day.isoformat(), "lifetime_views": lifetime_views}, ensure_ascii=False), encoding="utf-8")


def append_snapshot(cfg: Config, p: Progress, pc: dict[str, Any]) -> Path:
    path = goal_dir(cfg) / "snapshots.jsonl"
    rec = {**p.to_dict(), "pace": {k: v for k, v in pc.items() if k not in ("ypp",)}}
    rec.pop("videos", None)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def load_snapshots(cfg: Config) -> list[dict[str, Any]]:
    path = goal_dir(cfg) / "snapshots.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def daily_from_snapshots(cfg: Config, p: Progress) -> None:
    """Data API しか無いとき、前回の記録との差分から日別の伸びを復元する（Analytics があれば不要）."""
    if p.daily_views:
        return
    snaps = load_snapshots(cfg)
    by_day: dict[str, int] = {}
    for s in snaps:
        by_day[s.get("date", "")] = int(s.get("views", 0))
    by_day[p.date.isoformat()] = p.views
    days = sorted(by_day)
    series = []
    prev = 0
    for d in days:
        series.append(max(by_day[d] - prev, 0))
        prev = by_day[d]
    p.daily_views = series


# ----------------------------------------------------------------------
# 表示
# ----------------------------------------------------------------------
def _bar(ratio: float, width: int = 24) -> str:
    n = int(round(max(0.0, min(ratio, 1.0)) * width))
    return "█" * n + "░" * (width - n)


def report(goal: Goal, p: Progress, pc: dict[str, Any], actions: list[str]) -> str:
    lines = []
    lines.append(f"■ {goal.name}  （{goal.start} 〜 {goal.end}）  {pc['day']}/{pc['days']} 日目  [{p.source}]")
    lines.append(f"  再生   {p.views:>9,} / {goal.views:,}   {_bar(p.views / max(goal.views, 1))}  "
                 f"{100 * p.views / max(goal.views, 1):.1f}%")
    lines.append(f"  今日までの目標 {pc['expected_today']:,}  →  {_status_ja(pc['status'])}")
    lines.append(f"  必要ペース  1 日 {pc['required_per_day']:,}（残り {pc['remaining_days']} 日）"
                 f"   直近の実績 1 日 {pc['recent_avg_per_day']:,}   このままだと {pc['projected_total']:,}")
    y = pc["ypp"]
    lines.append(f"  収益化   登録者 {y['subscribers']:,} / {y['subscribers_need']:,}"
                 + (f"   総再生時間 {y['watch_hours']:,.0f} / {y['watch_hours_need']:,} h" if "watch_hours" in y else
                    "   総再生時間: Analytics のスコープを付けると出ます"))
    if pc.get("next_gate"):
        g = pc["next_gate"]
        lines.append(f"  次の関門  {g['day']} 日目: {g['views']:,} 再生 / 登録 {g['subscribers']:,}")
    if p.videos:
        lines.append("  動画別（上位）:")
        for v in sorted(p.videos, key=lambda v: -v.get("views", 0))[:8]:
            pct = f"{v['avg_pct']:.0%}" if v.get("avg_pct") is not None else "  -"
            lines.append(f"    {v.get('kind', 'long'):<5} {v.get('views', 0):>8,}  視聴率 {pct:>4}  {str(v.get('title', ''))[:34]}")
    lines.append("  打ち手:")
    for a in actions:
        lines.append(f"    - {a}")
    return "\n".join(lines)


def _status_ja(status: str) -> str:
    return {"ahead": "前倒し", "on_track": "予定どおり", "behind": "遅れ", "not_started": "開始前"}.get(status, status)


def plan_text(goal: Goal) -> str:
    """数字が取れないときに出す、目標の分解（--offline）."""
    per_day = goal.views / goal.days
    s_share = goal.mix.get("shorts", 0.85)
    n_s = max(goal.per_day.get("shorts", 3), 1)
    n_l = max(goal.per_day.get("long", 1), 1)
    lines = [
        f"■ {goal.name}  （{goal.start} 〜 {goal.end}）",
        f"  1 日あたり {per_day:,.0f} 再生が必要",
        f"  内訳の想定: Shorts {s_share:.0%} = 1 日 {per_day * s_share:,.0f}（{n_s} 本なら 1 本 {per_day * s_share / n_s:,.0f}）"
        f" / 本編 {1 - s_share:.0%} = 1 日 {per_day * (1 - s_share):,.0f}（{n_l} 本なら 1 本 {per_day * (1 - s_share) / n_l:,.0f}）",
        f"  収益化の条件: 登録者 {goal.ypp.get('subscribers', 1000):,} + 長尺の総再生時間 {goal.ypp.get('watch_hours_12m', 4000):,} 時間"
        f"（または Shorts 90 日 {goal.ypp.get('shorts_views_90d', 10_000_000):,} 再生）",
        "  関門:",
    ]
    for g in goal.gates:
        lines.append(f"    {g['day']:>2} 日目  {g['views']:>8,} 再生  登録 {g['subscribers']:>5,}")
    return "\n".join(lines)
