"""日本と海外の時差を扱う部分のテスト.

ここが壊れると「先行して仕込んだのに掘り起こされない」という、
気づきにくい形で戦略が死ぬ。挙動を固定しておく。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ytecon.config import load_config
from ytecon.state import Store
from ytecon.topics import _clean_keywords, _normalize_horizon, plan_portfolio


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "s.sqlite3")


def _simulate(cfg, store, days: int, per_day: int = 2) -> dict[str, int]:
    got = {"flow": 0, "bridge": 0, "stock": 0}
    for _ in range(days):
        plan = plan_portfolio(cfg, store, per_day, has_news=True, has_seeds=True)
        assert sum(plan.values()) == per_day
        for h, n in plan.items():
            for _ in range(n):
                store.add_topic("t", "a", "news", horizon=h)
            got[h] += n
    return got


# ----------------------------------------------------------------------
def test_stock_actually_gets_produced_at_two_per_day(cfg, store):
    """1日2本でも先行仕込みが作られること。

    その日だけで 3:2:1 を割ると stock は毎日 0 本になり、
    在庫が永久に貯まらない。これを防ぐのが配分ロジックの主目的。
    """
    got = _simulate(cfg, store, days=30)
    assert got["stock"] > 0, "stock が1本も作られていない（時差戦略が死んでいる）"
    assert got["bridge"] > 0


def test_flow_never_starves(cfg, store):
    """過去に flow が偏っていても、flow が 0 本の日を作らないこと。"""
    for i in range(40):
        store.add_topic(f"p{i}", "a", "news", horizon="flow")
    for _ in range(10):
        plan = plan_portfolio(cfg, store, 2, has_news=True, has_seeds=True)
        assert plan["flow"] >= 1, "flow が枯れるとチャンネルを回す燃料が切れる"
        for h, n in plan.items():
            for _ in range(n):
                store.add_topic("t", "a", "news", horizon=h)


def test_ramp_up_is_flow_heavy(cfg, store):
    """立ち上げ期は flow に寄せる（土台が無いと stock も掘り起こされない）."""
    got = _simulate(cfg, store, days=7)
    assert got["flow"] > got["bridge"] + got["stock"]


def test_no_news_falls_back_without_crashing(cfg, store):
    plan = plan_portfolio(cfg, store, 2, has_news=False, has_seeds=True)
    assert sum(plan.values()) == 2
    assert plan["flow"] == 0


def test_horizon_inferred_when_model_omits_it():
    assert _normalize_horizon({"diffusion_stage": 0, "lag_months": 12}) == "stock"
    assert _normalize_horizon({"diffusion_stage": 2, "lag_months": 3}) == "bridge"
    assert _normalize_horizon({"diffusion_stage": 3, "lag_months": 0}) == "flow"
    # 明示された値が優先される
    assert _normalize_horizon({"horizon": "stock", "diffusion_stage": 3}) == "stock"


def test_generic_watch_keywords_are_dropped():
    """『経済』のような語を監視に入れると毎日誤検知して使い物にならない."""
    out = _clean_keywords(["経済", "日本", "ステーブルコイン", "ライドシェア解禁", "AI", "x"])
    assert out == ["ステーブルコイン", "ライドシェア解禁"]


def test_watch_keywords_deduped_and_capped():
    out = _clean_keywords(["ステーブルコイン"] * 3 + [f"キーワード{i}" for i in range(10)])
    assert out[0] == "ステーブルコイン"
    assert len(out) == len(set(out)) <= 6


# ----------------------------------------------------------------------
def test_watchlist_only_tracks_published_stock_and_bridge(store):
    ids = {}
    for horizon in ("flow", "bridge", "stock"):
        ids[horizon] = store.add_topic(
            f"{horizon}の話", "a", "news", horizon=horizon,
            watch_keywords=[f"{horizon}キーワード"],
        )
        store.create_video(f"slug-{horizon}", ids[horizon], f"{horizon}の話")
        store.update_video(f"slug-{horizon}", status="uploaded",
                           youtube_id=f"yt-{horizon}")
    # まだ公開していない stock は監視に入らない
    tid = store.add_topic("未公開", "a", "news", horizon="stock",
                          watch_keywords=["未公開キーワード"])
    store.create_video("slug-pending", tid, "未公開")

    titles = {w["title"] for w in store.watchlist()}
    assert titles == {"bridgeの話", "stockの話"}


def test_watchlist_skips_topics_without_keywords(store):
    tid = store.add_topic("語なし", "a", "news", horizon="stock", watch_keywords=[])
    store.create_video("slug-x", tid, "語なし")
    store.update_video("slug-x", status="uploaded", youtube_id="yt-x")
    assert store.watchlist() == []


def test_revival_is_not_reported_twice(store):
    tid = store.add_topic("t", "a", "news", horizon="stock", watch_keywords=["語"])
    store.create_video("slug-r", tid, "t")
    store.update_video("slug-r", status="uploaded", youtube_id="yt-r")
    vid = store.get_video("slug-r").id

    assert not store.revival_seen(vid, "語")
    rid = store.add_revival(vid, "語", "見出し", "http://x", {"urgency": "now"})
    assert store.revival_seen(vid, "語")
    assert not store.revival_seen(vid, "別の語")

    store.mark_revival_applied(rid, vid)
    assert store.watchlist()[0]["revived_at"] is not None


def test_migration_adds_columns_to_an_old_database(tmp_path: Path):
    """時差の列が無い古い DB を開いても壊れないこと."""
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, angle TEXT,
            kind TEXT, source_json TEXT, score REAL, created_at REAL NOT NULL,
            used_at REAL);
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL,
            topic_id INTEGER, title TEXT, status TEXT NOT NULL, stage_data TEXT,
            youtube_id TEXT, publish_at TEXT, error TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
    """)
    conn.execute("INSERT INTO topics(title, created_at) VALUES('旧データ', ?)",
                 (time.time(),))
    conn.commit()
    conn.close()

    store = Store(path)          # ここで ALTER TABLE が走る
    assert "旧データ" in store.recent_topic_titles(30)
    tid = store.add_topic("新", "a", "news", horizon="stock", watch_keywords=["語"])
    assert tid > 0
    assert store.horizon_counts(30)["stock"] == 1


# ----------------------------------------------------------------------
def _stocked(store: Store, title: str, keywords: list[str],
             months_ago: float = 9.0) -> int:
    tid = store.add_topic(title, "海外先行", "news", horizon="stock",
                          diffusion_stage=0, lag_months=9, watch_keywords=keywords)
    store._conn.execute("UPDATE topics SET created_at=? WHERE id=?",
                        (time.time() - 86400 * 30.4 * months_ago, tid))
    slug = f"slug-{tid}"
    store.create_video(slug, tid, title)
    store.update_video(slug, status="uploaded", youtube_id=f"yt{tid}")
    store._conn.commit()
    return tid


def test_scan_detects_only_the_topic_that_landed(cfg, store, monkeypatch):
    from ytecon import revive

    _stocked(store, "ステーブルコインで決済はどう変わるか",
             ["ステーブルコイン", "資金決済法"])
    _stocked(store, "週4日勤務は生産性を落とすのか", ["週4日勤務"])

    monkeypatch.setattr(revive, "fetch_rss", lambda _cfg: [
        {"source": "NHK 経済", "title": "ステーブルコイン発行、国内銀行が参入へ",
         "summary": "資金決済法の改正を受け", "url": "https://example.com/1",
         "published": ""},
        {"source": "Yahoo", "title": "きょうの株価は続落", "summary": "",
         "url": "https://example.com/2", "published": ""},
    ])

    hits = revive.scan(cfg, store)
    assert len(hits) == 1
    assert hits[0].keyword == "ステーブルコイン"
    assert 8 < hits[0].months_asleep < 10


def test_scan_does_not_alert_twice_for_the_same_video(cfg, store, monkeypatch):
    """1本に監視語が複数あると、同じニュースに別の語が反応して二度鳴る。
    動画単位のクールダウンでこれを抑える。"""
    from ytecon import revive

    _stocked(store, "ステーブルコイン解説", ["ステーブルコイン", "資金決済法"])
    monkeypatch.setattr(revive, "fetch_rss", lambda _cfg: [
        {"source": "NHK", "title": "ステーブルコイン発行へ",
         "summary": "資金決済法の改正", "url": "https://example.com/1", "published": ""},
    ])

    first = revive.scan(cfg, store)
    assert len(first) == 1
    store.add_revival(first[0].video_id, first[0].keyword,
                      first[0].headline, first[0].url, {})
    assert revive.scan(cfg, store) == []


def test_scan_is_quiet_when_nothing_landed(cfg, store, monkeypatch):
    from ytecon import revive

    _stocked(store, "週4日勤務の話", ["週4日勤務"])
    monkeypatch.setattr(revive, "fetch_rss", lambda _cfg: [
        {"source": "NHK", "title": "円相場、1ドル150円台", "summary": "",
         "url": "https://example.com/1", "published": ""},
    ])
    assert revive.scan(cfg, store) == []


def test_scan_survives_losing_the_news_feed(cfg, store, monkeypatch):
    from ytecon import revive

    _stocked(store, "週4日勤務の話", ["週4日勤務"])
    monkeypatch.setattr(revive, "fetch_rss", lambda _cfg: [])
    assert revive.scan(cfg, store) == []


def test_manual_schedule_is_used_before_automatic_selection(tmp_path, monkeypatch):
    """config/schedule.yaml の当日分が、自動選定より先に採用される（使った企画は履歴で飛ばす）."""
    import datetime as dt
    import copy
    from ytecon.config import load_config
    from ytecon.state import Store
    from ytecon import topics

    cfg = copy.deepcopy(load_config())
    sched = tmp_path / "schedule.yaml"
    sched.write_text(
        "queue:\n"
        "  - date: 2026-09-21\n    title: 初任給30万円は得なのか\n    angle: 手取りと昇給カーブ\n    horizon: flow\n"
        "  - date: 2026-09-22\n    title: 値上げできる会社を選べ\n    angle: 価格決定力\n"
        "  - title: 日付なしの予備\n    angle: 予備\n",
        encoding="utf-8")
    cfg.raw.setdefault("topics", {})["schedule_file"] = str(sched)
    store = Store(tmp_path / "s.sqlite3")
    got = topics.queued_topics(cfg, store, 2, today=dt.date(2026, 9, 21))
    assert [t.title for t in got] == ["初任給30万円は得なのか", "日付なしの予備"]
    assert got[0].horizon == "flow" and got[0].id
    # 翌日は 22 日ぶんが先頭。21 日ぶんは日付が違うので出ない
    got2 = topics.queued_topics(cfg, store, 1, today=dt.date(2026, 9, 22))
    assert [t.title for t in got2] == ["値上げできる会社を選べ"]
    # 自動選定は、キューで足りていれば呼ばれない
    monkeypatch.setattr(topics.llm, "complete_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM が呼ばれた")))
    monkeypatch.setattr(topics, "fetch_rss", lambda cfg: [])
    monkeypatch.setattr(topics.dt, "date", type("D", (dt.date,), {"today": classmethod(lambda cls: dt.date(2026, 9, 23))}))
    # 23 日は date 付きの企画が無く、日付なしの予備も使用済み → キューは空。ここでは queued_topics だけ確認
    assert topics.queued_topics(cfg, store, 1, today=dt.date(2026, 9, 23)) == []
