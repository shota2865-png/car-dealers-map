"""毎日の流れで docu（実話ドキュメンタリー型）を作る: 台本の直し・本編と Shorts の投稿・失敗時に今までの形へ戻す."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ytecon import docu_script, pipeline as pipeline_mod, topics, youtube
from ytecon.config import load_config
from ytecon.script import VideoScript
from ytecon.state import Store

SPEC = {"title": "t", "scenes": [
    {"type": "kinetic", "text": "[3,048万口座]", "lines": ["新NISAの口座は3,048万"]},
    {"type": "chapter", "index": 9, "total": 9, "title": "何人が始めた？", "lines": ["まずは口座の数"]},
    {"type": "menu", "items": ["x"], "lines": ["今日は2つ", "何人", "何を"]},
    {"type": "bars3d", "values": ["3,765,430", "5307976"], "labels": ["20代"], "tops": ["377万", "531万"], "at": [0, 5], "lines": ["20代は377万", "30代は531万"]},
    {"type": "phone", "url": "https://www.tiktok.com/@x/video/1", "handle": "@x", "quote": "q", "lines": ["引用"]},
    {"type": "anim", "preset": "dance", "lines": ["知らないアニメ"]},
    {"type": "number", "value": "13兆8,214億円", "count": True, "lines": ["13兆8,214億円 → 増えた"]},
    {"type": "chapter", "title": "注意", "lines": ["注意"]},
    {"type": "radio", "question": "q", "options": ["a", "b", "c"], "choose": 7, "answer": 1, "lines": ["1", "2", "3"]},
    {"type": "cta", "text": "?", "lines": ["コメント", "登録"]},
]}


def test_validate_fixes_and_drops():
    s = docu_script.validate(copy.deepcopy(SPEC), allowed_urls=set())
    kinds = [sc["type"] for sc in s["scenes"]]
    assert "phone" not in kinds and "anim" not in kinds                    # 候補に無い投稿・知らないアニメは落とす
    ch = [sc for sc in s["scenes"] if sc["type"] == "chapter"]
    assert [(c["index"], c["total"]) for c in ch] == [(1, 2), (2, 2)]
    menu = next(sc for sc in s["scenes"] if sc["type"] == "menu")
    assert menu["items"] == ["何人が始めた？", "注意"]
    bars = next(sc for sc in s["scenes"] if sc["type"] == "bars3d")
    assert bars["values"] == [3765430.0, 5307976.0] and bars["labels"] == ["20代", ""] and bars["at"] == [0, 1]
    num = next(sc for sc in s["scenes"] if sc["type"] == "number")
    assert num["count"] is False and "→" not in num["lines"][0]
    assert next(sc for sc in s["scenes"] if sc["type"] == "radio")["choose"] == 2
    kept = docu_script.validate(copy.deepcopy(SPEC), allowed_urls={"https://www.tiktok.com/@x/video/1"})
    assert "phone" in [sc["type"] for sc in kept["scenes"]]


def test_parse_json_strips_fences():
    assert docu_script._parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def _topic():
    return topics.Topic(title="新NISA 2026年", angle="a", kind="evergreen", horizon="flow")


def _script():
    return VideoScript.from_dict({"topic_title": "新NISA 2026年", "hook": "答え", "sections": [{"heading": "h", "narration": "n"}],
                                  "closing": "c", "title_candidates": ["新NISA 2026年"], "description": "説明", "tags": ["新NISA"],
                                  "thumbnail_copy": {}, "sources": [{"name": "金融庁", "url": "https://www.fsa.go.jp/"}],
                                  "search_keyword": "新nisa 2026年"})


@pytest.fixture
def pl(tmp_path, monkeypatch):
    cfg = copy.deepcopy(load_config())
    cfg.raw["pipeline"]["workdir"] = str(tmp_path / "out")
    cfg.raw["shorts"]["per_video"] = 2
    p = pipeline_mod.Pipeline(cfg, Store(tmp_path / "s.sqlite3"))
    monkeypatch.setattr("ytecon.script.generate", lambda cfg, topic: _script())
    monkeypatch.setattr(docu_script, "tiktok_candidates", lambda *a, **k: [])
    monkeypatch.setattr(docu_script, "make_spec", lambda cfg, material, **k: docu_script.validate(copy.deepcopy(SPEC)))
    monkeypatch.setattr(docu_script, "make_shorts", lambda cfg, spec, n, **k: [{"title": f"S{i}", "scenes": SPEC["scenes"][:3]} for i in range(n)])

    def fake_render(self, spec, out, work, vertical=False):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\0" * 2000)
        if not vertical:
            (out.parent / "chapters.json").write_text(json.dumps([[0, "冒頭"], [30, "何人"], [90, "注意"]]), encoding="utf-8")
            (out.parent / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
        return out
    monkeypatch.setattr(pipeline_mod.Pipeline, "_docu_render", fake_render)
    monkeypatch.setattr("ytecon.metadata.choose_title", lambda cfg, s, horizon="flow": ("新NISA 2026年【6割は投資信託】", {}))
    monkeypatch.setattr("ytecon.thumbnail.build", lambda cfg, s, out: Path(out).write_bytes(b"jpg"))
    monkeypatch.setattr(pipeline_mod.Pipeline, "_publish_day", lambda self, i=0: __import__("datetime").date(2026, 10, 30))
    return p


def test_produce_docu_uploads_main_and_shorts(pl, monkeypatch):
    calls = []

    def fake_publish(cfg, store, video, meta, **kw):
        calls.append((Path(video).name, meta, kw))
        n = len(calls)
        return {"video_id": f"V{n}", "publish_at": "2026-10-30T10:00:00+00:00", "url": f"https://youtu.be/V{n}"}
    monkeypatch.setattr(youtube, "publish", fake_publish)
    res = pl.produce(_topic(), slot_index=0, upload=True, slug="nisa")
    assert res["video_id"] == "V1" and len(res["shorts"]) == 2
    main_meta = calls[0][1]
    assert main_meta.title.startswith("新NISA 2026年")
    assert "VOICEVOX：玄野武宏" in main_meta.description and "■ もくじ" in main_meta.description
    assert "0:30 何人" in main_meta.description and "Mixkit" in main_meta.description
    assert calls[0][2]["srt"].name == "subtitles.srt"
    short_meta = calls[1][1]
    assert short_meta.title.endswith("#Shorts") and "https://youtu.be/V1" in short_meta.description
    assert calls[1][2]["publish_times"] and calls[1][2]["after"] is not None
    rec = pl.store.get_video("nisa")
    assert rec.status == "uploaded" and (rec.stage or {}).get("format") == "docu"


def test_docu_failure_falls_back_to_classic(pl, monkeypatch):
    monkeypatch.setattr(docu_script, "make_spec", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("AI が落ちた")))
    called = {}

    def classic_script(self, slug, topic):
        called["classic"] = True
        raise RuntimeError("stop here")
    monkeypatch.setattr(pipeline_mod.Pipeline, "stage_voice", lambda self, slug, s: classic_script(self, slug, None))
    with pytest.raises(RuntimeError, match="stop here"):
        pl.produce(_topic(), slot_index=0, upload=False, slug="nisa2")
    assert called.get("classic")
