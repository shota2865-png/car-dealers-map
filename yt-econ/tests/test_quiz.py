"""参加型テストの Shorts（quiz.py）: 台本の整形・場面の描画・文字の収まり・メタデータ."""
from __future__ import annotations

import copy

import pytest
from PIL import Image, ImageDraw

from ytecon import quiz
from ytecon.config import load_config


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config(channel="psych"))


def test_normalize_adds_countdown_and_cta_and_renumbers_steps():
    q = quiz.normalize({"title": "t", "hook": "h", "scenes": [
        {"kind": "question", "options": ["a", "b"], "narration": [["x", 0], ["y", 2]]},
        {"kind": "flow", "boxes": [{"text": "1"}], "narration": [["z", 5]]},
    ]})
    kinds = [s["kind"] for s in q["scenes"]]
    assert kinds == ["question", "countdown", "flow", "cta"]
    assert [st for _, st in q["scenes"][0]["narration"]] == [0, 1]
    assert [st for _, st in q["scenes"][2]["narration"]] == [0]


def test_example_scenes_render_and_stay_in_safe_area(cfg):
    th = quiz.theme(cfg)
    assert th.bg.upper() == "#FFFFFF" and th.blue.upper() == "#0017C1"      # dads プリセット
    q = quiz.normalize(copy.deepcopy(quiz._EXAMPLE))
    for sc in q["scenes"]:
        s = quiz.build_scene(cfg, th, sc if sc["kind"] != "countdown" else dict(q["scenes"][0], kind="countdown"))
        img = s.render(s.last_step(), 99.0)
        assert img.size == (1080, 1920)
        dy = quiz._content_offset(s, th, with_extra=sc["kind"] == "countdown")
        moved = quiz._shift(img, dy, th)
        from PIL import ImageChops
        body = moved.crop((0, 230, 1080, 1920))
        bb = ImageChops.difference(body, Image.new("RGB", body.size, th.bg)).getbbox()
        assert bb and bb[1] + 230 >= th.safe_top - 2 and bb[3] + 230 <= th.safe_bottom + 60, (sc["kind"], bb)


def test_box_text_fits_and_breaks_naturally(cfg):
    th = quiz.theme(cfg)
    P = quiz.Parts(cfg, th)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for text in ("「確認させてください」", "気分が乗った日に、まとめてやる", "作業記憶が狭まる", "短い"):
        for w in (400, 600, 900):
            f, lines = P.fit(d, text, w, 64)
            assert "".join(lines) == text
            assert all(d.textlength(ln, font=f) <= w for ln in lines), (text, w, lines)
            assert len(lines) <= 2
    f, lines = P.fit(d, "「確認させてください」", 400, 64)
    assert lines == ["「確認させて", "ください」"]


def test_metadata_has_choices_sources_credit_and_disclaimer(cfg):
    m = quiz.quiz_metadata(cfg, quiz.normalize(copy.deepcopy(quiz._EXAMPLE)))
    assert m.title.endswith("#Shorts") and len(m.title) <= 100
    assert "A. 今日、少しだけ手をつける" in m.description and "B. " in m.description
    assert "Sirois" in m.description and "VOICEVOX：四国めたん" in m.description
    assert "診断や治療の代わり" in m.description and "#心理学" in m.description
    assert m.category_id == "27"


def test_psych_is_voice_only(cfg):
    assert cfg.get("cast.mode") == "solo" and not cfg.get("character.enabled")
    assert int(cfg.get("tts.voicevox.speaker")) == 2
    assert cfg.get("shorts.mode") == "quiz" and cfg.get("video.design") == "dads"


def test_run_daily_skips_when_the_day_already_has_an_episode(tmp_path, monkeypatch):
    import datetime as dt
    from ytecon.pipeline import Pipeline
    from ytecon.state import Store
    cfg = copy.deepcopy(load_config())
    store = Store(tmp_path / "s.sqlite3")
    pipe = Pipeline(cfg, store=store)
    day = dt.date(2026, 9, 23)
    monkeypatch.setattr(pipe, "_publish_day", lambda slot_index=0: day)
    called = []
    monkeypatch.setattr("ytecon.topics.select_topics", lambda *a, **k: called.append(1) or [])
    store.create_video("ep1", None, "t")
    store.update_video("ep1", status="uploaded", youtube_id="x", publish_at="2026-09-23T10:00:00+00:00", stage={"kind": "long"})
    store.create_video("ep1-short1", None, "s")
    store.update_video("ep1-short1", status="uploaded", youtube_id="y", publish_at="2026-09-24T03:00:00+00:00", stage={"kind": "short"})
    res = pipe.run_daily(count=1, upload=True)
    assert res[0]["skipped"] and not called
    assert pipe.run_daily(count=1, upload=True, force=True) == [] and called      # --force なら作る
    monkeypatch.setattr(pipe, "_publish_day", lambda slot_index=0: day + dt.timedelta(days=1))
    assert pipe.run_daily(count=1, upload=True) == []                              # 翌日は空いている（Shorts は数えない）


def test_wide_scenes_render_in_16x9_and_stay_in_safe_area(cfg):
    from PIL import ImageChops
    from ytecon import wide
    th = wide.theme_wide(cfg)
    assert (th.W, th.H) == (1920, 1080)
    scenes = [
        {"kind": "question", "options": ["動画を見て気をそらす", "布団で返す言葉を考える"], "lead": "会議で言い返せなかった夜"},
        {"kind": "chapter", "label": "第1章", "heading": "その場で言葉が出ない理由", "heading_hl": "言葉が出ない", "sub": "頭の回転とは関係がない"},
        {"kind": "point", "heading": "作業記憶ってなに？", "term": "作業記憶", "plain": "頭の中のメモ帳", "note": "考えるための小さな置き場"},
        {"kind": "meter", "heading": "緊張したときのメモ帳", "slots": 5, "fill": [{"text": "どう思われる？"}, {"text": "失敗したら？"}, {"text": "早く言わなきゃ"}, {"text": "上司の顔"}], "label_left": "言葉を考える場所は、これだけ"},
        {"kind": "flow", "heading": "流れ", "boxes": [{"text": "緊張する"}, {"text": "メモ帳がうまる", "state": "active", "up": True}, {"text": "言葉が出ない", "note": "頭のせいじゃない"}]},
        {"kind": "branch", "heading": "研究", "source": "先延ばす人", "targets": [{"text": "課題"}, {"text": "嫌な気分", "avoided": True}]},
        {"kind": "versus", "heading": "一言目", "left": {"text": "完璧な言い返しを探す", "caption": "間に合わない"}, "right": {"text": "「確認させてください」", "caption": "時間ができる"}},
        {"kind": "steps", "items": ["紙を出す", "書く", "閉じる"]},
    ]
    for sc in scenes:
        s = wide.build_scene_wide(cfg, th, sc)
        img = s.render(s.last_step(), 99.0)
        if sc["kind"] == "question":
            s.extra(ImageDraw.Draw(img), 1.0, 3)
        assert img.size == (1920, 1080)
        dy = quiz._content_offset(s, th, with_extra=sc["kind"] == "question")
        body = quiz._shift(img, dy, th).crop((0, th.body_top, 1920, 1080))
        bb = ImageChops.difference(body, Image.new("RGB", body.size, th.bg)).getbbox()
        assert bb and bb[3] + th.body_top <= 1080 - 20 and bb[0] >= th.M - 12 and bb[2] <= 1920 - th.M + 12, (sc["kind"], bb)


def test_unbreakable_labels_stay_on_one_line(cfg):
    P = quiz.Parts(cfg, quiz.theme(cfg))
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    f, lines = P.fit(d, "どう思われる？", 280, 60)
    assert lines == ["どう思われる？"] and d.textlength(lines[0], font=f) <= 280


def test_retry_resumes_same_slug_and_never_reuploads(tmp_path, monkeypatch):
    """失敗して再試行しても最初から作り直さない（同じ slug で続きから）。投稿済みの本編は二度上げない."""
    import datetime as dt
    from ytecon import topics as topics_mod
    from ytecon.pipeline import Pipeline
    from ytecon.state import Store
    cfg = copy.deepcopy(load_config())
    cfg.raw["pipeline"]["workdir"] = str(tmp_path)
    cfg.raw.setdefault("shorts", {})["per_video"] = 0
    store = Store(tmp_path / "s.sqlite3")
    pipe = Pipeline(cfg, store=store)
    monkeypatch.setattr(pipe, "_publish_day", lambda slot_index=0: dt.date(2026, 9, 24))
    topic = topics_mod.Topic(title="テスト回", angle="", kind="evergreen")
    monkeypatch.setattr("ytecon.topics.select_topics", lambda *a, **k: [topic])
    slugs, published = [], []
    monkeypatch.setattr(pipe, "stage_script", lambda slug, t: slugs.append(slug) or type("S", (), {"topic_title": "テスト回"})())
    monkeypatch.setattr(pipe, "stage_voice", lambda slug, s: None)
    monkeypatch.setattr(pipe, "stage_visuals", lambda slug, s, tr: ([], {}))
    monkeypatch.setattr(pipe, "stage_render", lambda *a: None)
    calls = {"n": 0}

    def publish(slug, s, track, slot_index, horizon="flow"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("一時的な失敗")
        published.append(slug)
        store.update_video(slug, status="uploaded", youtube_id="vid1", stage={"url": "u"})
        return {"video_id": "vid1", "url": "u"}
    monkeypatch.setattr(pipe, "stage_publish", publish)
    res = pipe.run_daily(count=1, upload=True, force=True)
    assert len(set(slugs)) == 1 and len(slugs) == 2        # 再試行も同じ slug（作り直さない）
    assert res[0]["video_id"] == "vid1" and published == [slugs[0]]
    # 同じ slug でもう一度回しても、投稿済みの本編は上げ直さない
    again = pipe.produce(topic, upload=True, slug=slugs[0])
    assert again["video_id"] == "vid1" and calls["n"] == 2
