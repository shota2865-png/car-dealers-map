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
    assert "現代人のための心理学" in first and "声だけ" in first and "ながら" not in first or "家事" in first
    assert OUTLINE["title"] in first and "長い説明文" not in first and "眠" not in first
    last = " ".join(t for t, _ in data["scenes"][-1]["narration"])
    assert "一言を書いて寝る" in last and "三日坊主" in last and "20:00" in last and "静かな音楽" not in last


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
    assert len(shorts) == 3 and all(s["after"] is not None for s in shorts)     # Shorts は本編の公開後の枠
    assert store.get_video("ep1").stage.get("kind") == "long"
    # 同じ slug で回し直しても、台本も投稿もやり直さない
    pipe.produce(topic, upload=True, slug="ep1")
    assert calls["write"] == 1 and len(calls["publish"]) == 4


def test_caption_cues_split_by_sentence_and_keep_timing():
    cues = quiz._split_cue(10.0, 6.0, "ひとつめです。ふたつめの文は長めです。")
    assert [c[2] for c in cues] == ["ひとつめです。", "ふたつめの文は長めです。"]
    assert cues[0][0] == 10.0 and abs(cues[-1][1] - 16.0) < 1e-6 and cues[0][1] == cues[1][0]
