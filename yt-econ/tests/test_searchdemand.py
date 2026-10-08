"""経済の本編を「検索されている言葉」から作る（searchdemand / topics.search_first / 台本・タイトル）."""
from __future__ import annotations

import copy

from ytecon import metadata, searchdemand, topics
from ytecon.config import load_config
from ytecon.script import VideoScript
from ytecon.state import Store

FAKE = {
    "手取り": ["手取り20万", "手取り", "手取り15万"],
    "住民税": ["住民税非課税世帯", "住民税 計算", "住民税の"],
    "生涯賃金": ["生涯賃金", "生涯賃金 両学長", "生涯賃金 大卒"],
    "住民税 計算": ["住民税 計算", "住民税 計算方法"],
    "日銀1.25%": [],
}


def _cfg(**topics_cfg):
    cfg = copy.deepcopy(load_config())
    cfg.raw.setdefault("topics", {}).update({"search_bases": ["手取り", "住民税", "生涯賃金"], "search_per_base": 3,
                                             "search_exclude": ["両学長"], **topics_cfg})
    return cfg


def test_pool_takes_real_searches_skips_covered_names_and_fragments(monkeypatch):
    monkeypatch.setattr(searchdemand, "suggest", lambda q, timeout=10.0: FAKE.get(q, []))
    p = searchdemand.pool(_cfg(), history=["手取り20万円で一人暮らしはできる？"])
    assert "手取り20万" not in p                       # もう扱った
    assert "生涯賃金 両学長" not in p                   # 人名つき
    assert "住民税の" not in p                          # 途中で切れた候補
    assert p[:3] == ["手取り15万", "住民税非課税世帯", "生涯賃金"]     # 種ごとに 1 つずつ（多い順。「手取り」はもう扱った）
    assert searchdemand.has_demand("住民税 計算") and not searchdemand.has_demand("日銀1.25%")


def test_select_topics_builds_on_a_searched_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr(searchdemand, "suggest", lambda q, timeout=10.0: FAKE.get(q, []))
    monkeypatch.setattr(topics, "fetch_rss", lambda cfg: [])
    seen = {}

    def fake_llm(system, user, schema, **kw):
        seen["user"] = user
        return {"topics": [
            {"title": "住民税はいくら？手取り20万円の人の6月", "angle": "a", "kind": "evergreen", "search_keyword": "住民税 計算",
             "horizon": "flow", "diffusion_stage": 3, "lag_months": 0, "score": 80},
        ]}
    monkeypatch.setattr("ytecon.llm.complete_json", fake_llm)
    cfg = _cfg(search_first=True)
    store = Store(tmp_path / "s.sqlite3")
    got = topics.select_topics(cfg, store, 1)
    assert "検索されている言葉" in seen["user"] and "住民税非課税世帯" in seen["user"]
    assert got[0].search_keyword == "住民税 計算"


def test_unsearched_keyword_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(searchdemand, "suggest", lambda q, timeout=10.0: FAKE.get(q, []))
    monkeypatch.setattr(topics, "fetch_rss", lambda cfg: [])
    monkeypatch.setattr("ytecon.llm.complete_json", lambda *a, **k: {"topics": [
        {"title": "日銀の利上げで円安？", "angle": "a", "kind": "news", "search_keyword": "日銀1.25%",
         "horizon": "flow", "diffusion_stage": 3, "lag_months": 0, "score": 50}]})
    got = topics.select_topics(_cfg(search_first=True), Store(tmp_path / "s.sqlite3"), 1)
    assert got[0].search_keyword == ""                 # 検索候補に出ない言葉はタイトルの頭に置かない


def test_title_starts_with_the_searched_phrase(monkeypatch):
    s = VideoScript.from_dict({"topic_title": "住民税", "hook": "h", "sections": [], "closing": "c",
                               "title_candidates": [], "description": "", "tags": [], "thumbnail_copy": {}, "sources": [],
                               "search_keyword": "住民税 計算"})
    assert VideoScript.from_dict(s.to_dict()).search_keyword == "住民税 計算"
    monkeypatch.setattr("ytecon.llm.complete_json", lambda *a, **k: {
        "title": "6月に手取りが減る理由", "keyword": "住民税", "hook_tag": "", "thumbnail_main": "", "thumbnail_sub": "", "thumbnail_bubble": ""})
    title, _ = metadata.choose_title(load_config(), s)
    assert title.startswith("住民税 計算")


def test_psych_long_is_news_style_but_shorts_stay_as_before():
    p = load_config(channel="psych")
    assert p.get("honpen.speed") >= 1.2 and p.get("tts.voicevox.speed") == 1.02
    assert "Delayed Flight" in p.get("render.bgm.file") and "Hush Move" in p.get("shorts.bgm_file")
    assert not p.get("topics.search_first")            # 検索から作るのは経済の本編だけ
    assert load_config().get("topics.search_first") is True


def _store_with_longs(tmp_path):
    st = Store(tmp_path / "s.sqlite3")
    rows = [("old-low", "L1", "日銀の利上げでなぜ円安？1.25%が奨学金に届く順番", "2026-10-01T10:00:00+00:00", {"kind": "long"}),
            ("old-high", "L2", "生涯賃金とは？会社員の一生は億単位", "2026-10-01T10:00:00+00:00", {"kind": "long"}),
            ("new-low", "L3", "住民税の話", "2026-10-07T10:00:00+00:00", {"kind": "long"}),
            ("short", "S1", "ショート #Shorts", "2026-10-01T10:00:00+00:00", {"kind": "short"}),
            ("done", "L4", "前に付け直した回", "2026-10-01T10:00:00+00:00", {"kind": "long", "retitled_at": "x"})]
    for slug, vid, title, pub, stage in rows:
        st.create_video(slug, None, title)
        st.update_video(slug, status="uploaded", youtube_id=vid, publish_at=pub, stage=stage)
    return st


def test_retitle_picks_only_old_low_long_videos(tmp_path):
    from ytecon import retitle
    st = _store_with_longs(tmp_path)
    views = {"L1": 70, "L2": 1245, "L3": 5, "S1": 10, "L4": 3}
    got = retitle.candidates(load_config(), st, views, today=__import__("datetime").date(2026, 10, 8))
    assert [r.slug for r in got] == ["old-low"]          # 新しすぎる・伸びた・Shorts・付け直し済みは外す


def test_retitle_puts_a_searched_phrase_first_and_keeps_old_title(tmp_path, monkeypatch):
    from ytecon import retitle, youtube
    st = _store_with_longs(tmp_path)
    monkeypatch.setattr(searchdemand, "suggest", lambda q, timeout=10.0: {"円安": ["円安", "円安 なぜ", "円安 生活"],
                                                                          "奨学金": ["奨学金 返済"]}.get(q, []))
    answers = iter([{"bases": ["円安", "奨学金"]},
                    {"keyword": "円安 なぜ", "title": "円安 なぜ止まらない？利上げしても下がる理由", "hook_tag": "金利上げたのに"}])
    monkeypatch.setattr("ytecon.llm.complete_json", lambda *a, **k: next(answers))

    class Svc:
        def videos(self):
            return self

        def list(self, **kw):
            return self

        def execute(self):
            return {"items": [{"id": i, "statistics": {"viewCount": v}} for i, v in
                              {"L1": "70", "L2": "1245", "L3": "5", "L4": "3"}.items()]}
    monkeypatch.setattr(youtube, "build_service", lambda cfg: Svc())
    updated = []
    monkeypatch.setattr(youtube, "update_video_metadata", lambda cfg, store, vid, title=None, **k: updated.append((vid, title)))
    import datetime as _dt
    monkeypatch.setattr(retitle.dt, "date", type("D", (_dt.date,), {"today": staticmethod(lambda: _dt.date(2026, 10, 8))}))
    rows = retitle.run(load_config(), st, apply=True)
    assert updated and updated[0][0] == "L1" and updated[0][1].startswith("円安 なぜ")
    rec = st.get_video("old-low")
    assert rec.stage["old_title"].startswith("日銀の利上げ") and rec.stage["retitle_keyword"] == "円安 なぜ"
    assert "円安 なぜ" in retitle.format_report(rows, True)


def test_description_starts_with_search_phrase_and_answer_and_tags_lead_with_it():
    s = VideoScript.from_dict({"topic_title": "住民税", "hook": "ずんだもん：手取り20万円なら、住民税は月およそ1万円なのだ。でも、なぜかは知られていないのだ。",
                               "sections": [], "closing": "c", "title_candidates": [], "description": "説明", "tags": ["税金", "住民税"],
                               "thumbnail_copy": {}, "sources": [], "search_keyword": "住民税 計算"})
    lead = metadata.search_lead(s)
    assert lead.startswith("【住民税 計算】") and "月およそ1万円" in lead and "ずんだもん" not in lead
    from pathlib import Path

    from ytecon.tts import VoiceTrack
    meta = metadata.build(load_config(), s, VoiceTrack(wav_path=Path("x.wav")))
    assert meta.description.startswith("【住民税 計算】")
    assert meta.tags[:3] == ["住民税 計算", "住民税", "計算"]


def test_topic_playlists_follow_the_search_phrase():
    from ytecon.pipeline import topic_playlists
    cfg = load_config()
    assert topic_playlists(cfg, "住民税 計算", "") == ["手取り・税金・社会保険のしくみ"]
    assert "借りるお金（奨学金・ローン・カード）" in topic_playlists(cfg, "", "奨学金 返済はいつから？")
    assert topic_playlists(load_config(channel="psych"), "住民税", "") == []
