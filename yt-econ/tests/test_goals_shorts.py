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


# ----------------------------------------------------------------------
# Shorts（story モード: 起承転結のミニ台本）
# ----------------------------------------------------------------------
def _story() -> shorts.Story:
    return shorts.Story(hook="生成AI利用9%対46%", title="日本は安全？AI利用率の落差", section_index=2, beats=[
        shorts.Beat("起", ["【ずんだもん】[困]海外ではもうAIで仕事が消えていると聞くのだ。"], {"kind": "quote", "text": "仕事が消える？"}),
        shorts.Beat("承", ["【めたん】総務省の白書によると、生成AIを使った人は日本で約9パーセント。", "【めたん】アメリカは約46パーセントよ。"],
                    {"kind": "number", "value": "9% vs 46%", "label": "生成AIを使った人の割合", "note": "出典: 総務省"}),
        shorts.Beat("転", ["【ずんだもん】[驚]じゃあ日本は安全なのだ。", "【めたん】そこは早とちりね。", "【めたん】遅い国ほど一気に入るの。"],
                    {"kind": "compare", "title": "遅い vs 安全", "items": ["意味|来るのが遅い|来ない"]}),
        shorts.Beat("結", ["【めたん】今夜は、AIに自分の経験を一つ話してみて。"], {"kind": "steps", "title": "今夜やること", "items": ["経験を一つ話す"]}),
    ])


def test_story_always_ends_with_the_call_to_the_long_video(cfg):
    st = shorts.with_cta(cfg, _story())
    assert [b.role for b in st.beats] == ["起", "承", "転", "結", "誘導"]
    assert "本編" in st.beats[-1].lines[0]
    assert st.beats[-1].visual["kind"] == "cta"
    shorts.with_cta(cfg, st)                                   # 2 回呼んでも増えない
    assert sum(1 for b in st.beats if b.role == "誘導") == 1


def test_story_becomes_a_script_the_tts_and_subtitles_understand(cfg):
    st = shorts.with_cta(cfg, _story())
    mini = shorts.story_script(cfg, st, _script())
    blocks = dict(mini.narration_blocks)
    assert [k for k in blocks if k.startswith("s")] == ["s0", "s1", "s2", "s3", "s4"]
    assert blocks["hook"] == "" and blocks["closing"] == ""      # タイトルカードもアウトロも無い
    assert "【ずんだもん】" in blocks["s0"] and blocks["s4"].startswith("【ずんだもん】続きは本編で")
    assert mini.sections[2].diagrams[0].type == "compare"        # 転の比較は図解として描ける
    assert mini.sections[1].diagrams == []                       # 数字カードは図解ではない
    assert mini.title_candidates == [st.title]
    assert st.chars == sum(len(shorts.strip_tags(x)) for b in st.beats for x in b.lines)


def test_story_cta_card_and_hook_band_render(cfg, tmp_path):
    from PIL import Image
    sc = shorts.shorts_config(cfg)
    assets.apply_layout(sc)
    p = shorts.render_cta_card(sc, tmp_path / "cta.png")
    img = Image.open(p)
    assert img.size == (1080, 1920)
    # 背景を落とす層（半透明）は全面に掛かるので、文字（不透明）だけの範囲を見る
    bbox = img.getchannel("A").point(lambda a: 255 if a > 200 else 0).getbbox()
    assert bbox[1] >= sc.get("layout.top_band") - 40            # 見出しの帯には掛からない
    assert bbox[3] <= 1920 - sc.get("layout.sub_band") + 40     # 字幕と立ち絵の帯にも掛からない
    h = Image.open(shorts.render_hook_band(sc, "生成AI利用9%対46%", tmp_path / "hook.png"))
    hb = h.getchannel("A").getbbox()
    assert hb[3] <= sc.get("layout.top_band")                    # 見出しは帯の中に収まる
    assets.apply_layout(cfg)


def test_shorts_speak_a_little_faster_than_the_long_video(cfg):
    sc = shorts.shorts_config(cfg)
    assert sc.get("tts.voicevox.speed") > cfg.get("tts.voicevox.speed")
    assert sc.get("tts.voicevox.pause_sentence") < cfg.get("tts.voicevox.pause_sentence")
    assert cfg.get("tts.voicevox.speed") == 1.2                  # 本編は生涯賃金の回と同じ速さ
