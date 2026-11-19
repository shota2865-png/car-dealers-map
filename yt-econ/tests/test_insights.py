"""伸びた動画のジャンル分け・伸びた本編の続編・週 1 のまとめ（API と AI は差し替えて確かめる）."""
from __future__ import annotations

import copy
import datetime as dt

import pytest

from ytecon import insights, topics
from ytecon.config import load_config
from ytecon.state import Store


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(load_config())
    c.raw["pipeline"]["workdir"] = str(tmp_path / "out")
    return c


def _v(i, views, age, kind="long", fmt=None):
    return {"id": f"v{i}", "title": f"動画{i}", "kind": kind, "views": views, "lifetime_views": views,
            "age_days": age, "format": fmt}


VIDEOS = [_v(1, 1500, 20), _v(2, 120, 9), _v(3, 100, 10), _v(4, 90, 11, fmt="docu"), _v(5, 80, 12),
          _v(6, 3000, 5, "short"), _v(7, 50, 2)]
LABELS = {"v1": {"genre": "税金・手取り", "angle": "数字"}, "v2": {"genre": "税金・手取り", "angle": "仕組み"},
          "v3": {"genre": "物価・節約", "angle": "数字"}, "v4": {"genre": "物価・節約", "angle": "ニュース"},
          "v5": {"genre": "物価・節約", "angle": "ニュース"}, "v6": {"genre": "税金・手取り", "angle": "数字"}}


def test_genre_table_and_spin_off():
    table = insights.genre_table(VIDEOS, LABELS)
    assert [r["name"] for r in table] == ["税金・手取り", "物価・節約"]
    tax = table[0]
    assert tax["long_n"] == 2 and tax["long_avg"] == 810 and tax["short_n"] == 1 and tax["short_avg"] == 3000
    assert insights.spin_off_candidates(table, channel_avg=378, min_n=2) == ["税金・手取り"]
    assert insights.spin_off_candidates(table, channel_avg=378) == []          # 3 本そろうまでは候補にしない
    assert next(r["name"] for r in insights.genre_table(VIDEOS, LABELS, key="angle")) == "数字"


def test_classify_only_new_videos_and_keeps_old_labels(cfg, monkeypatch):
    insights._write(insights._goal_dir(cfg) / "genres.json", {"v1": {"genre": "税金・手取り", "angle": "数字"}})
    seen = {}

    def fake(system, user, schema, **kw):
        seen["user"] = user
        return {"items": [{"id": "v2", "genre": "税金・手取り", "angle": "仕組み"}, {"id": "v1", "genre": "変えない", "angle": ""},
                          {"id": "zzz", "genre": "知らない", "angle": ""}]}
    monkeypatch.setattr("ytecon.llm.complete_json", fake)
    labels = insights.classify(cfg, VIDEOS[:2])
    assert "- 税金・手取り" in seen["user"] and "v1 /" not in seen["user"] and "v2 / 本編 / 動画2" in seen["user"]
    assert labels == {"v1": {"genre": "税金・手取り", "angle": "数字"}, "v2": {"genre": "税金・手取り", "angle": "仕組み"}}
    assert insights.load_genres(cfg) == labels


def test_pick_sequel_threshold_gap_and_once(cfg):
    insights.save_videos(cfg, dt.date(2026, 11, 25), VIDEOS)
    today = dt.date(2026, 11, 25)
    seq = insights.pick_sequel(cfg, today)
    assert seq["id"] == "v1" and seq["views"] == 1500 and seq["median"] == 100     # 中央値 100 の 2 倍・200 回以上
    insights.mark_sequel(cfg, seq, "続きの企画", today)
    assert insights.pick_sequel(cfg, today + dt.timedelta(days=1)) is None          # 3 日あける
    assert insights.pick_sequel(cfg, today + dt.timedelta(days=5)) is None          # 同じ本編は 1 回だけ
    cfg.raw["topics"]["sequels"]["enabled"] = False
    insights._write(insights._sequels_path(cfg), [])
    assert insights.pick_sequel(cfg, today) is None


def test_select_topics_puts_sequel_first_and_records_it(cfg, tmp_path, monkeypatch):
    insights.save_videos(cfg, insights._today(), VIDEOS)
    insights._write(insights._goal_dir(cfg) / "genres.json", LABELS)
    monkeypatch.setattr(topics, "fetch_rss", lambda c: [])
    cfg.raw["topics"]["search_first"] = False
    seen = {}

    def fake(system, user, schema, **kw):
        seen["user"] = user
        return {"topics": [{"title": "手取りが増えない本当の理由", "angle": "a", "kind": "evergreen", "horizon": "stock",
                            "diffusion_stage": 3, "lag_months": 0, "score": 80}]}
    monkeypatch.setattr("ytecon.llm.complete_json", fake)
    got = topics.select_topics(cfg, Store(tmp_path / "s.sqlite3"), 1)
    assert "伸びた本編の続編" in seen["user"] and "「動画1」が 1,500 回" in seen["user"]
    assert "ジャンルごとの本編の見られ方" in seen["user"] and "税金・手取り: 本編の平均 810 回" in seen["user"]
    done = insights._read(insights._sequels_path(cfg), [])
    assert got[0].title == "手取りが増えない本当の理由" and done[0]["parent_id"] == "v1" and done[0]["child_title"] == got[0].title


def test_digest_reads_like_a_phone_summary(cfg, monkeypatch):
    insights.save_videos(cfg, insights._today(), VIDEOS)
    insights._write(insights._goal_dir(cfg) / "genres.json", LABELS)
    monkeypatch.setattr("ytecon.llm.complete_json",
                        lambda s, u, schema, **k: {"why": ["税金の回が平均 810 回で、ほかの 9 倍"], "next": ["税金の企画を週 3 本に"]})
    text = insights.digest(cfg, target=500)
    assert "**本編の平均 378 回** / 目標 500 回" in text
    assert "1. 1,500 回 動画1（税金・手取り）" in text
    assert "- 税金・手取り: 810 回（2 本） / 3,000 回" in text
    assert "docu 90 回（1 本） / 今まで" in text
    assert "**なぜ伸びたか**" in text and "税金の企画を週 3 本に" in text
    assert "動画の数字がまだありません" in insights.digest(_empty(cfg), use_llm=False)


def _empty(cfg):
    c = copy.deepcopy(cfg)
    c.raw["pipeline"]["workdir"] = str(insights._goal_dir(cfg).parent / "empty")
    return c
