"""目標トラッカー（ytecon goal）と Shorts 切り出しの、API を叩かずに確かめられる部分."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from ytecon import assets, goals, shorts
from ytecon.config import load_config
from ytecon.script import Section, VideoScript, Visual
from ytecon.tts import Line, VoiceTrack


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def goal(cfg):
    return goals.load_goal(cfg)


# ----------------------------------------------------------------------
# 目標
# ----------------------------------------------------------------------
def test_goal_file_is_consistent(goal):
    assert goal.views == 500_000 and goal.days == 30
    assert goal.gates[-1]["views"] == goal.views          # 最後の関門 = 目標
    assert goal.gates == sorted(goal.gates, key=lambda g: g["day"])
    assert abs(sum(goal.mix.values()) - 1.0) < 1e-6


def test_pace_before_start_and_required_per_day(goal):
    p = goals.Progress(date=goal.start - dt.timedelta(days=1))
    pc = goals.pace(goal, p)
    assert pc["status"] == "not_started" and pc["day"] == 0
    assert pc["required_per_day"] == int(goal.views / goal.days)

    # 10 日目に 4 万再生: 目標ペース（16.7 万）を大きく下回る → 遅れ
    p = goals.Progress(date=goal.start + dt.timedelta(days=9), views=40_000, subscribers=50,
                       daily_views=[4000] * 10)
    pc = goals.pace(goal, p)
    assert pc["day"] == 10 and pc["status"] == "behind"
    assert pc["remaining_days"] == 21
    assert pc["required_per_day"] == int((goal.views - 40_000) / 21)
    assert pc["projected_total"] == 40_000 + 4000 * 21
    assert pc["last_gate"]["day"] == 7 and pc["last_gate_ok"] is False   # 7 日目の関門も未達
    assert pc["next_gate"]["day"] == 14


def test_pace_on_track_and_ahead(goal):
    on = goals.Progress(date=goal.start + dt.timedelta(days=14), views=290_000, subscribers=400)
    assert goals.pace(goal, on)["status"] == "ahead"
    ok = goals.Progress(date=goal.start + dt.timedelta(days=14), views=240_000, subscribers=400)
    assert goals.pace(goal, ok)["status"] == "on_track"


def test_recommend_names_concrete_levers(goal):
    p = goals.Progress(
        date=goal.start + dt.timedelta(days=9), views=40_000, subscribers=50, daily_views=[4000] * 10,
        videos=[
            {"id": "a", "title": "本編A", "kind": "long", "views": 120, "avg_pct": 0.2, "age_days": 8},
            {"id": "b", "title": "短いやつ", "kind": "short", "views": 30_000, "avg_pct": 0.9, "age_days": 5},
            {"id": "c", "title": "弱い短いやつ", "kind": "short", "views": 200, "avg_pct": 0.5, "age_days": 4},
        ],
    )
    pc = goals.pace(goal, p)
    acts = goals.recommend(goal, p, pc)
    text = "\n".join(acts)
    assert "shorts.per_video" in text                  # 遅れ → Shorts の本数
    assert "本編A" in text and "サムネ" in text          # 弱い本編 → サムネとタイトル
    assert "shorts.max_seconds" in text                 # 視聴率の低い Shorts → 尺
    assert "短いやつ" in text and "派生" in text          # 当たった Shorts → 派生


def test_recommend_when_on_track_says_keep_going(goal):
    p = goals.Progress(date=goal.start + dt.timedelta(days=6), views=120_000, subscribers=200,
                       daily_views=[17_000] * 7)
    acts = goals.recommend(goal, p, goals.pace(goal, p))
    assert len(acts) == 1 and "同じ型" in acts[0]


def test_snapshots_rebuild_daily_series(cfg, tmp_path, monkeypatch, goal):
    monkeypatch.setattr(goals, "goal_dir", lambda _cfg: tmp_path)
    d0 = goal.start
    for i, v in enumerate([1000, 2500, 6000]):
        p = goals.Progress(date=d0 + dt.timedelta(days=i), views=v, source="data_api")
        goals.append_snapshot(cfg, p, goals.pace(goal, p))
    p = goals.Progress(date=d0 + dt.timedelta(days=3), views=10_000, source="data_api")
    goals.daily_from_snapshots(cfg, p)
    assert p.daily_views == [1000, 1500, 3500, 4000]


def test_report_and_plan_render(goal):
    p = goals.Progress(date=goal.start + dt.timedelta(days=2), views=9000, subscribers=30, watch_hours=120)
    pc = goals.pace(goal, p)
    text = goals.report(goal, p, pc, goals.recommend(goal, p, pc))
    assert "500,000" in text and "収益化" in text and "4,000" in text
    assert "1 日あたり 16,667" in goals.plan_text(goal)


# ----------------------------------------------------------------------
# Shorts
# ----------------------------------------------------------------------
def _dialogue(block: str, start: float, texts: list[tuple[str, str, str]], each: float = 3.5) -> list[Line]:
    out = []
    t = start
    for i, (spk, expr, text) in enumerate(texts):
        out.append(Line(block, i, text, t, t + each - 0.4, expression=expr, speaker=spk))
        t += each
    return out


def _track() -> VoiceTrack:
    lines = _dialogue("hook", 0.0, [
        ("zundamon", "困", "先輩、いま深夜1時なのだ。"),
        ("metan", "", "今夜はそこを解くわね。"),
    ])
    lines += _dialogue("s0", 10.0, [
        ("zundamon", "", "でも海外では、もう仕事が消えていると聞くのだ。"),
        ("metan", "", "そこは数字で確かめましょう。"),
        ("metan", "", "総務省の白書の国際比較よ。"),
        ("metan", "", "日本はおよそ9パーセント、アメリカは46パーセントなの。"),
        ("zundamon", "驚", "5倍くらい違うのだ。"),
        ("metan", "", "ええ、企業の調査でも差が出ていたわ。"),
        ("metan", "", "日本はおよそ4割台、アメリカは8割を超えていたの。"),
        ("zundamon", "", "じゃあ日本は安全なのだ。"),
        ("metan", "", "そこは早とちりね。遅いのは安全という意味ではないの。"),
        ("metan", "", "むしろ遅い国ほど一気に入ることがあるのよ。"),
        ("metan", "", "試す段階を飛ばして一度に入れるからよ。"),
        ("zundamon", "考", "ぼくは今、待ち時間の中にいるのだ。"),
        ("metan", "", "そういうこと。だから焦らなくていいの。"),
        ("metan", "", "ここまでで分かったのは、日本の広がりは入口の水準だということ。"),
    ])
    lines += _dialogue("closing", 70.0, [
        ("metan", "", "今夜はここまでで十分よ。"),
        ("zundamon", "", "おやすみなのだ。"),
    ] * 6)
    return VoiceTrack(wav_path=Path("x.wav"), lines=lines)


def _script() -> VideoScript:
    return VideoScript(topic_title="AIに仕事を取られる前に", hook="h", proof="p", promise="q",
                       sections=[Section(heading="日本と海外の広がり方なのだ", narration="", on_screen=[],
                                         visual=Visual(kind="textcard"))],
                       closing="c", title_candidates=[], description="", tags=["AI", "就活"],
                       thumbnail_copy={}, sources=[])


def test_shorts_candidates_start_with_the_student_and_end_on_an_answer(cfg):
    wins = shorts.candidates(cfg, _script(), _track(), n=3)
    assert wins, "候補が 1 本も無い"
    for w in wins:
        assert 30 <= w.duration <= 58
        assert w.lines[0].speaker == "zundamon"                 # 聞き役の一言から入る
        assert w.lines[-1].speaker == "metan"                   # 答えで終わる
        assert w.block_id != "closing"                          # 締めは切り出さない
    assert wins[0].hook == "日本と海外の広がり方"                  # 見出しは標準語
    assert wins[0].lines[0].text.startswith("でも海外では")        # セクションの頭から


def test_shorts_scoring_penalises_context_dependent_openers():
    student, teacher = "zundamon", "metan"
    good = _dialogue("s0", 0, [("zundamon", "", "なぜ求人は減らないのだ？"),
                               ("metan", "", "理由は三つあるの。"),
                               ("metan", "", "一つ目は採用の慣性よ。")] * 4, each=4.0)[:11]
    bad = [Line("s0", 0, "5倍くらい違うのだ。", 0, 3, expression="驚", speaker="zundamon")] + good[1:]
    sg, _ = shorts.score_window(good, student, teacher)
    sb, why = shorts.score_window(bad, student, teacher)
    assert sg > sb
    assert any("リアクション" in w for w in why)


def test_shorts_candidates_do_not_overlap_and_prefer_distinct_blocks(cfg):
    t = _track()
    t.lines += [Line("s1", i, f"{'質問なのだ？' if i % 4 == 0 else '答えよ。'}", 120 + i * 3.5, 123 + i * 3.5,
                     speaker="zundamon" if i % 4 == 0 else "metan") for i in range(16)]
    wins = shorts.candidates(cfg, _script(), t, n=2)
    assert len(wins) == 2
    assert {w.block_id for w in wins} == {"s0", "s1"}
    a, b = wins
    assert a.end <= b.start


def test_shorts_config_is_vertical_and_keeps_the_original(cfg):
    sc = shorts.shorts_config(cfg)
    assert sc.get("video.resolution") == [1080, 1920]
    assert cfg.get("video.resolution") == [1920, 1080]           # 元の設定は変えない
    assert sc.get("layout.characters_in_band") is True
    assert sc.get("layout.no_title_outro") is True
    # 字幕は立ち絵の上、図の帯は字幕の上
    char_h = int(1920 * float(sc.get("cast.height_ratio")))
    assert sc.get("visuals.subtitle.margin_v") > char_h
    assert sc.get("layout.sub_band") > sc.get("visuals.subtitle.margin_v")
    assert sc.get("layout.top_band") >= 400


def test_layout_bands_switch_and_reset(cfg):
    sc = shorts.shorts_config(cfg)
    top, sub = assets.apply_layout(sc)
    assert (top, sub) == (sc.get("layout.top_band"), sc.get("layout.sub_band"))
    assert assets.content_span(sc) == (40, 1040)               # 立ち絵は下の帯 → 横幅は全部使える
    assert assets.content_width(sc) == 1080
    assert assets.apply_layout(cfg) == assets.DEFAULT_BANDS     # 本編に戻すと既定
    assert assets.TOP_BAND == 110 and assets.SUB_BAND == 230


def test_shorts_metadata_links_back_to_the_long_video(cfg):
    wins = shorts.candidates(cfg, _script(), _track(), n=1)
    meta = shorts.shorts_metadata(cfg, _script(), wins[0], parent_url="https://youtu.be/abc", parent_minutes=16.2)
    assert meta.title.endswith("#Shorts") and len(meta.title) <= 100
    assert "https://youtu.be/abc" in meta.description
    assert "16分" in meta.description
    assert "VOICEVOX：四国めたん" in meta.description and "VOICEVOX：ずんだもん" in meta.description
    assert "Shorts" in meta.tags


def test_shorts_publish_times_are_separate_from_the_long_video(cfg):
    from ytecon.youtube import next_publish_time
    base = dt.datetime(2026, 9, 22, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    long_at = next_publish_time(cfg, 0, base=base)
    short_at = next_publish_time(cfg, 0, base=base, times=cfg.get("shorts.publish_times_jst"))
    assert long_at.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%H:%M") == "19:00"
    assert short_at.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%H:%M") == "12:15"
    assert next_publish_time(cfg, 2, base=base, times=cfg.get("shorts.publish_times_jst")) > short_at
