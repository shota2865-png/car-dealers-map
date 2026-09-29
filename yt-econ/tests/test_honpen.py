"""現代人のための心理学の本編（ながら聴き 15〜20 分）: 台本の組み立て・概要欄・毎日の流れ（投稿と Shorts の予約）."""
from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from ytecon import honpen, quiz
from ytecon.config import load_config


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config(channel="psych"))


OUTLINE = {
    "title": "言い返せなかった夜の心理学", "theme": "長い説明文", "recap": ["出ないのは緊張", "夜は考えない", "一言を書く"],
    "today_one": "一言を書いて寝る", "next": "三日坊主",
    "parts": [{"heading": f"第{i}の話", "quiz": {"lead": "状況", "options": ["A案", "B案"]}, "points": ["要点1", "要点2"]} for i in range(1, 6)],
}


def test_assemble_adds_greeting_and_closing_for_listeners(cfg):
    parts = [[{"kind": "chapter", "heading": f"章{i}", "narration": [["はなし", 0]]}] for i in range(4)]
    data = honpen.assemble(cfg, OUTLINE, parts)
    kinds = [s["kind"] for s in data["scenes"]]
    assert kinds[0] == "opening" and kinds[-2:] == ["steps", "ending"]
    first = " ".join(t for t, _ in data["scenes"][0]["narration"])
    assert "現代人のための心理学" in first and "声だけ" in first
    assert first.startswith("今日のテーマは、" + OUTLINE["title"])          # 何の話かを最初の一言で
    assert "長い説明文" not in first and "眠" not in first
    last = " ".join(t for t, _ in data["scenes"][-1]["narration"])
    assert "一言を書いて寝る" in last and "三日坊主" in last and "20:00" in last and "静かな音楽" not in last
    assert "チャンネル登録" in last


def test_sleep_style_can_be_restored_from_config(cfg):
    cfg.raw["honpen"]["greeting"] = [["こんばんは。{name}です。", 0], ["{theme}のお話です。", 1]]
    cfg.raw["honpen"]["closing"] = [["{one}。ゆっくり休んでくださいね。", 0]]
    data = honpen.assemble(cfg, dict(OUTLINE, next=""), [])
    assert data["scenes"][0]["narration"][0][0] == "こんばんは。現代人のための心理学です。"
    assert "休んで" in data["scenes"][-1]["narration"][0][0]


def test_every_question_gets_its_own_countdown():
    q = quiz.normalize({"scenes": [{"kind": "question", "options": ["a", "b"]}, {"kind": "result"},
                                   {"kind": "question", "options": ["c", "d"]}, {"kind": "flow"}]}, add_cta=False)
    assert [s["kind"] for s in q["scenes"]] == ["question", "countdown", "result", "question", "countdown", "flow"]


def test_metadata_has_chapters_from_zero_and_sleep_tags(cfg):
    data = {"title": "テスト回", "outline": OUTLINE}
    m = honpen.honpen_metadata(cfg, data, [[3.2, "はじめに"], [40, "第1章 a"], [400, "第2章 b"], [1600, "今日のまとめ"]])
    assert m.title == "テスト回【現代人のための心理学】"
    assert "0:00 はじめに" in m.description and "26:40 今日のまとめ" in m.description
    assert "聞き流し" in m.tags and "睡眠用" not in m.tags and len(m.tags) == len(set(m.tags))
    assert "UCv-h9prBycfOLwu2ieAUcMw?sub_confirmation=1" in m.description      # 登録リンク
    m2 = honpen.honpen_metadata(cfg, {"title": "SNS疲れの正体", "outline": {**OUTLINE, "keyword": "SNS疲れ"}}, [])
    assert m2.tags[0] == "SNS疲れ"


def test_outline_title_starts_with_search_keyword(cfg, monkeypatch):
    from ytecon import llm
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {**OUTLINE, "title": "見るほど沈むのはなぜ？", "keyword": "SNS疲れ"})
    ol = honpen.outline(cfg, {"title": "SNS"})
    assert ol["title"] == "SNS疲れ｜見るほど沈むのはなぜ？"


def test_quiz_shorts_have_no_cta_scene_when_end_cta_is_off(cfg):
    assert cfg.get("shorts.end_cta") is False
    q = quiz.normalize({"scenes": [{"kind": "question", "options": ["a", "b"]}, {"kind": "result"}]},
                       add_cta=bool(cfg.get("shorts.end_cta", True)))
    assert "cta" not in [s["kind"] for s in q["scenes"]]
    m = quiz.quiz_metadata(cfg, {"title": "t", "hook": "h", "scenes": []})
    assert "sub_confirmation=1" in m.description


def test_short_angles_spread_over_chapters():
    got = honpen.short_angles({"title": "t", "outline": OUTLINE}, 3)
    assert [g[0] for g in got] == ["第1の話", "第3の話", "第5の話"]      # 5 章なら 1・3・5 章
    assert "A: A案 / B: B案" in got[0][1]


def test_daily_flow_uploads_long_then_shorts_after_it_and_resumes(cfg, tmp_path, monkeypatch):
    from ytecon import topics as topics_mod
    from ytecon.pipeline import Pipeline
    from ytecon.state import Store
    cfg.raw["pipeline"]["workdir"] = str(tmp_path)
    cfg.raw.setdefault("upload", {})["finals_dir"] = str(tmp_path / "finals")
    store = Store(tmp_path / "s.sqlite3")
    pipe = Pipeline(cfg, store=store)
    monkeypatch.setattr(pipe, "_publish_day", lambda slot_index=0: dt.date(2026, 9, 29))
    topic = topics_mod.Topic(title="言い返せなかった夜", angle="", kind="evergreen")
    calls = {"write": 0, "build": 0, "publish": []}

    def fake_write(c, t):
        calls["write"] += 1
        return honpen.assemble(c, OUTLINE, [[{"kind": "chapter", "label": f"第{i + 1}章", "heading": "h", "narration": [["x", 0]]}] for i in range(5)])

    def fake_build(c, data, outdir, provider=None, wide=False):
        calls["build"] += 1
        from pathlib import Path
        out = Path(outdir)
        (out / "video.mp4").write_bytes(b"0")
        (out / "chapters.json").write_text(json.dumps([[0, "はじめに"], [30, "第1章 h"], [60, "第2章 h"]]))
        (out / "quiz.json").write_text(json.dumps(data, ensure_ascii=False))
        (out / "subtitles.srt").write_text("")
        return quiz.Built(video=out / "video.mp4", seconds=1.0, frames=1, quiz=data)

    def fake_publish(c, st, video, meta, thumbnail=None, srt=None, slot_index=0, publish_times=None, playlist=True, after=None):
        calls["publish"].append({"title": meta.title, "after": after, "slot": slot_index})
        vid = f"v{len(calls['publish'])}"
        at = "2026-09-29T11:00:00+00:00" if after is None else f"2026-09-29T1{2 + slot_index}:30:00+00:00"
        return {"video_id": vid, "url": f"https://youtu.be/{vid}", "publish_at": at}

    monkeypatch.setattr("ytecon.honpen.write_honpen", fake_write)
    monkeypatch.setattr("ytecon.quiz.build", fake_build)
    monkeypatch.setattr("ytecon.quiz.write_quiz", lambda c, t, a="": {"title": t, "hook": "h", "scenes": []})
    monkeypatch.setattr("ytecon.youtube.publish", fake_publish)
    monkeypatch.setattr("ytecon.finals.keep", lambda *a, **k: {"video": "x"})

    res = pipe.produce(topic, upload=True, slug="ep1")
    assert calls["write"] == 1 and res["video_id"] == "v1"
    long_, shorts = calls["publish"][0], calls["publish"][1:]
    assert "現代人のための心理学" in long_["title"] and long_["after"] is None
    assert len(shorts) == 5 and all(s["after"] is not None for s in shorts)     # Shorts は本編の公開後の枠
    assert store.get_video("ep1").stage.get("kind") == "long"
    # 同じ slug で回し直しても、台本も投稿もやり直さない
    pipe.produce(topic, upload=True, slug="ep1")
    assert calls["write"] == 1 and len(calls["publish"]) == 6


def test_caption_cues_split_by_sentence_and_keep_timing():
    cues = quiz._split_cue(10.0, 6.0, "ひとつめです。ふたつめの文は長めです。")
    assert [c[2] for c in cues] == ["ひとつめです。", "ふたつめの文は長めです。"]
    assert cues[0][0] == 10.0 and abs(cues[-1][1] - 16.0) < 1e-6 and cues[0][1] == cues[1][0]


def test_nothing_is_made_before_the_start_date(cfg, tmp_path, monkeypatch):
    from ytecon.pipeline import Pipeline
    from ytecon.state import Store
    cfg.raw["pipeline"]["start_date"] = "2026-09-29"
    pipe = Pipeline(cfg, store=Store(tmp_path / "s.sqlite3"))
    called = []
    monkeypatch.setattr("ytecon.topics.select_topics", lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(pipe, "_publish_day", lambda slot_index=0: dt.date(2026, 9, 28))
    assert pipe.run_daily(count=1, upload=True)[0]["skipped"] and not called
    monkeypatch.setattr(pipe, "_publish_day", lambda slot_index=0: dt.date(2026, 9, 29))
    assert pipe.run_daily(count=1, upload=True) == [] and called


def test_next_title_comes_from_the_schedule(cfg):
    import yaml
    q = yaml.safe_load((cfg.root / cfg.get("topics.schedule_file")).read_text(encoding="utf-8"))["queue"]
    d0 = dt.date.fromisoformat(str(q[0]["date"]))
    assert honpen.next_title(cfg, d0) == q[1]["title"]
    assert honpen.next_title(cfg, dt.date(2030, 1, 1)) == ""


def test_summary_comments_go_on_public_videos_once_without_links(cfg, tmp_path, monkeypatch):
    from ytecon import comments
    from ytecon.pipeline import Pipeline
    from ytecon.state import Store
    store = Store(tmp_path / "s.sqlite3")
    pipe = Pipeline(cfg, store=store)
    rows = [("ep", "L1", "2026-09-26T11:00:00+00:00", "long"), ("ep-s0", "S0", "2026-09-26T12:30:00+00:00", "short"),
            ("ep-s1", "S1", "2026-09-27T12:30:00+00:00", "short")]
    for slug, vid, at, kind in rows:
        store.create_video(slug, None, slug)
        store.update_video(slug, status="uploaded", youtube_id=vid, publish_at=at, stage={"kind": kind})

    class FakeList:
        def __init__(self, ids): self.ids = ids
        def execute(self): return {"items": [{"id": i, "snippet": {"title": f"t{i}", "description": "d"}, "status": {"privacyStatus": "public"}} for i in self.ids]}

    class FakeYT:
        def videos(self): return self
        def list(self, part, id): return FakeList(id.split(","))
    monkeypatch.setattr("ytecon.youtube.build_service", lambda c: FakeYT())
    monkeypatch.setattr(comments, "_own_channel_id", lambda c: "ME")
    existing = {"S0": [{"id": "old", "text": "▶ 本編（17分）はこちら\nhttps://youtu.be/L1"}]}
    monkeypatch.setattr(comments, "own_comments", lambda c, vid, ch: existing.get(vid, []))
    deleted, posted = [], []
    monkeypatch.setattr(comments, "delete_comment", lambda c, cid: deleted.append(cid) or True)
    monkeypatch.setattr(comments, "summary_text", lambda c, t, d="": f"{t}って、なぜだと思いますか？\nあなたはどうですか？")
    monkeypatch.setattr("ytecon.youtube.post_comment", lambda c, st, vid, text: posted.append((vid, text)) or f"c-{vid}")
    now = dt.datetime(2026, 9, 26, 13, 0, tzinfo=dt.timezone.utc)
    assert pipe.post_pending_comments(now) == 2                       # 本編と、公開済みの Shorts 1 本
    assert deleted == ["old"]                                         # リンクのコメントは消す
    assert {v for v, _ in posted} == {"L1", "S0"} and all("http" not in t for _, t in posted)
    assert pipe.post_pending_comments(now) == 0                       # 二度付けない
    assert pipe.post_pending_comments(now + dt.timedelta(days=2)) == 1 and posted[-1][0] == "S1"


def test_weekly_report_renders_tables():
    from ytecon import report
    data = {"channel": "現代人のための心理学", "start": "2026-09-19", "end": "2026-09-25",
            "now": {"views": 4468, "minutes": 776, "subs": 1}, "prev": {"views": 1000, "minutes": 100, "subs": 0},
            "sources": [["SHORTS", 4304], ["YT_SEARCH", 70]],
            "long": [{"title": "本編", "views": 17, "avg_seconds": 176, "avg_percent": 18.4, "kept_30s": 0.53, "from_shorts": 2}],
            "short": [{"title": "S", "views": 1113, "avg_percent": 34.5}],
            "targets": {"deadline": "2026-10-31", "day": 1, "days": 37, "rows": [
                {"key": "subscribers", "label": "登録者", "unit": "人", "now": 2.0, "target": 100.0, "expected": 2.7, "status": "on_track", "n": None},
                {"key": "shorts_avg_views", "label": "Shorts の平均再生", "unit": "回", "now": 1050.0, "target": 10000.0, "expected": 10000.0, "status": "behind", "n": 3}]}}
    text = report.render(data)
    assert "## 目標まで（期限 2026-10-31、1/37 日目）" in text and "登録者 2 / 100 人" in text and "Shorts の平均再生 1,050 / 10,000 回（直近 3 本）  → 遅れ" in text
    assert "+347%" in text and "Shorts のフィード: 4,304（98%）" in text and "2分56秒" in text and "53%" in text
    assert report._iso_seconds("PT17M1S") == 1021 and report._iso_seconds("PT45S") == 45


def test_five_shorts_from_four_chapters_and_upload_guard(cfg, tmp_path):
    ol = {**OUTLINE, "parts": OUTLINE["parts"][:4]}
    got = honpen.short_angles({"title": "t", "outline": ol}, 5)
    assert len(got) == 5 and "別の話題" in got[4][1]                    # 章より多いときは同じ章から別の問い
    assert cfg.get("shorts.per_video") == 5 and len(cfg.get("shorts.publish_times_jst")) == 5
    from ytecon import youtube
    from ytecon.state import Store
    guard = youtube.QuotaGuard(Store(tmp_path / "q.db"))
    for _ in range(12):                                                  # 2 チャンネル × (本編 1 + Shorts 5)
        guard.check(youtube.COST_UPLOAD + 50 + 400 + 50)
        guard.check_upload()
        guard.spend(youtube.COST_UPLOAD)
        guard.spend_upload()
