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
    import copy
    return copy.deepcopy(load_config())


_GOAL_YAML = """
goal: {name: "30日で50万再生", start: 2026-09-22, days: 30, views: 500000, mix: {shorts: 0.85, long: 0.15}, per_day: {long: 1, shorts: 3}}
ypp: {subscribers: 1000, watch_hours_12m: 4000, shorts_views_90d: 10000000}
gates:
  - {day: 7,  views: 20000,  subscribers: 100}
  - {day: 14, views: 80000,  subscribers: 300}
  - {day: 21, views: 220000, subscribers: 600}
  - {day: 30, views: 500000, subscribers: 1000}
rules:
  long: {avg_view_pct_min: 0.30, views_7d_min: 300}
  shorts: {avg_view_pct_min: 0.75, views_3d_min: 500}
"""


@pytest.fixture
def goal(cfg, tmp_path):
    """計算のテストは、設定ファイルの目標（運用で変わる）ではなく固定の目標で行う."""
    f = tmp_path / "goals.yaml"
    f.write_text(_GOAL_YAML, encoding="utf-8")
    return goals.load_goal(cfg, f)


# ----------------------------------------------------------------------
# 目標
# ----------------------------------------------------------------------
@pytest.mark.parametrize("channel", ["", "psych"])
def test_goal_files_are_consistent(channel):
    import datetime as _dt
    g = goals.load_goal(load_config(channel=channel) if channel else load_config())
    assert g.end == _dt.date(2026, 10, 31)                 # 10 月末が期限
    assert g.targets == {"subscribers": 100, "long_watch_hours": 500, "shorts_avg_views": 10000}
    assert g.gates[-1]["day"] == g.days and g.gates[-1]["views"] == g.views
    assert g.gates[-1]["subscribers"] == g.targets["subscribers"] and g.gates[-1]["long_hours"] == g.targets["long_watch_hours"]
    assert g.gates == sorted(g.gates, key=lambda x: x["day"])
    assert abs(sum(g.mix.values()) - 1.0) < 1e-6


def test_targets_status_and_lines(goal):
    goal.targets = {"subscribers": 100, "long_watch_hours": 500, "shorts_avg_views": 10000}
    vids = [{"id": "l1", "kind": "long", "views": 300, "watch_minutes": 900.0, "age_days": 5},
            {"id": "l2", "kind": "long", "views": 100, "watch_minutes": 300.0, "age_days": 2}]
    vids += [{"id": f"s{i}", "kind": "short", "views": 500 * (i + 1), "age_days": i} for i in range(12)]
    p = goals.Progress(date=goal.start + dt.timedelta(days=14), subscribers=60, views=50_000, videos=vids)
    rows = {r["key"]: r for r in goals.pace(goal, p)["targets"]}
    assert rows["subscribers"]["status"] == "on_track"            # 15/30 日で 60 人（目安 50）
    assert rows["long_watch_hours"]["now"] == 20.0 and rows["long_watch_hours"]["status"] == "behind"
    # 公開 3 日未満の Shorts は平均に入れない。3〜11 日目の 9 本（2,000〜6,000）の平均
    assert rows["shorts_avg_views"]["now"] == 4000 and rows["shorts_avg_views"]["n"] == 9
    text = "\n".join(goals.targets_lines(list(rows.values())))
    assert "登録者 60 / 100 人" in text and "本編の総再生時間 20 / 500 時間" in text and "直近 9 本" in text
    acts = "\n".join(goals.recommend(goal, p, goals.pace(goal, p)))
    assert "本編の総再生時間" in acts and "Shorts の平均" in acts
    # 公開統計（Data API）では本編の再生時間が取れない → 数字なし
    p2 = goals.Progress(date=p.date, subscribers=60, videos=[{"id": "l1", "kind": "long", "views": 3, "age_days": 5}])
    assert {r["key"]: r for r in goals.pace(goal, p2)["targets"]}["long_watch_hours"]["status"] == "unknown"


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
    assert short_at.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%H:%M") == "12:00"
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


def test_story_ends_without_call_when_end_cta_is_off(cfg):
    assert cfg.get("shorts.end_cta") is False                  # 既定の運用: 誘導なし（ループで終える）
    st = shorts.with_cta(cfg, _story())
    assert [b.role for b in st.beats] == ["起", "承", "転", "結"]


def test_story_always_ends_with_the_call_to_the_long_video(cfg):
    cfg.raw["shorts"]["end_cta"] = True
    st = shorts.with_cta(cfg, _story())
    assert [b.role for b in st.beats] == ["起", "承", "転", "結", "誘導"]
    assert "本編" in st.beats[-1].lines[0]
    assert st.beats[-1].visual["kind"] == "cta"
    shorts.with_cta(cfg, st)                                   # 2 回呼んでも増えない
    assert sum(1 for b in st.beats if b.role == "誘導") == 1


def test_story_becomes_a_script_the_tts_and_subtitles_understand(cfg):
    cfg.raw["shorts"]["end_cta"] = True
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


# ----------------------------------------------------------------------
# 手で作ったサムネイルと投稿時刻
# ----------------------------------------------------------------------
def test_manual_thumbnail_is_found_by_slug_or_date_and_resized(cfg, tmp_path, monkeypatch):
    from PIL import Image
    from ytecon import thumbnail
    monkeypatch.setattr(thumbnail, "manual_dir", lambda _cfg: tmp_path)
    Image.new("RGB", (1024, 1024), "red").save(tmp_path / "2026-09-23.png")        # 正方形（ChatGPT の既定）
    Image.new("RGB", (1920, 1080), "blue").save(tmp_path / "20260924.jpg")
    Image.new("RGB", (800, 450), "green").save(tmp_path / "my-slug.jpg")
    assert thumbnail.pick_manual(cfg, "my-slug", dt.date(2026, 9, 23)).name == "my-slug.jpg"   # slug が最優先
    assert thumbnail.pick_manual(cfg, "other", dt.date(2026, 9, 23)).name == "2026-09-23.png"
    assert thumbnail.pick_manual(cfg, "other", dt.date(2026, 9, 24)).name == "20260924.jpg"
    assert thumbnail.pick_manual(cfg, "other", dt.date(2026, 9, 25)) is None                    # 無ければ自動生成へ
    out = thumbnail.prepare(tmp_path / "2026-09-23.png", tmp_path / "out" / "thumbnail.jpg")
    img = Image.open(out)
    assert img.size == (1280, 720) and img.format == "JPEG"
    assert out.stat().st_size <= 2_000_000
    assert thumbnail.name_for(dt.date(2026, 9, 23)) == "2026-09-23.jpg"
    assert thumbnail.name_for(None, "slug-x") == "slug-x.jpg"


def test_five_shorts_go_out_after_the_long_video_before_the_next_one(cfg):
    from ytecon.youtube import next_publish_time
    jst = dt.timezone(dt.timedelta(hours=9))
    base = dt.datetime(2026, 9, 23, 15, 0, tzinfo=jst)           # Actions は JST 15:00 ごろに走る
    long_at = next_publish_time(cfg, 0, base=base)
    assert long_at.astimezone(jst).strftime("%m-%d %H:%M") == "09-23 19:00"
    times = cfg.get("shorts.publish_times_jst")
    assert len(times) == cfg.get("shorts.per_video") == 5
    got = [next_publish_time(cfg, i, base=long_at + dt.timedelta(minutes=1), times=times).astimezone(jst) for i in range(5)]
    assert [g.strftime("%m-%d %H:%M") for g in got] == ["09-23 21:30", "09-24 00:00", "09-24 07:00", "09-24 12:00", "09-24 18:00"]


# ----------------------------------------------------------------------
# 完成品の名前（yt_001_20260922）と手で仕上げた動画の投稿
# ----------------------------------------------------------------------
def test_final_names_count_up_from_001_and_follow_the_publish_day(cfg, tmp_path, monkeypatch):
    from ytecon import finals
    from ytecon.state import Store
    monkeypatch.setattr(finals, "finals_dir", lambda _cfg: tmp_path / "finals")
    monkeypatch.setattr(finals, "output_dir", lambda _cfg: tmp_path / "out")
    (tmp_path / "finals").mkdir(); (tmp_path / "out").mkdir()
    store = Store(tmp_path / "s.sqlite3")
    assert finals.name_for(1, dt.date(2026, 9, 22)) == "yt_001_20260922"
    assert finals.parse_name("yt_012_20261001.mp4") == (12, dt.date(2026, 10, 1))
    assert finals.parse_name("video.mp4") is None
    assert finals.assign(cfg, store, dt.date(2026, 9, 23)) == "yt_001_20260923"
    (tmp_path / "finals" / "yt_001_20260922.json").write_text("{}")          # 手で仕上げた 1 本目の記録
    store.create_video("s", None, "t"); store.update_video("s", stage={"final_name": "yt_002_20260923"})
    assert finals.assign(cfg, store, dt.date(2026, 9, 24)) == "yt_003_20260924"   # 両方を見て次の番号
    kept = finals.keep(cfg, "yt_003_20260924", Path(__file__))
    assert Path(kept["video"]).name == "yt_003_20260924.mp4"


def test_manual_final_metadata_and_thumbnail_are_found_by_name(cfg, tmp_path, monkeypatch):
    from PIL import Image
    from ytecon import finals, thumbnail
    meta = finals.load_meta(cfg, "yt_001_20260922")                        # リポジトリに入れた記録
    assert meta is not None and "なぜ給料が上がっても" in meta.title and meta.title.endswith("【ずんだもん&めたん解説】")
    assert "もくじ" not in meta.description                                 # CapCut でカット済みなのでタイムコードは無い
    assert "VOICEVOX" in meta.description and meta.tags
    monkeypatch.setattr(thumbnail, "manual_dir", lambda _cfg: tmp_path)
    Image.new("RGB", (1280, 720), "red").save(tmp_path / "yt_001_20260922.png")
    assert finals.find_thumbnail(cfg, "yt_001_20260922").name == "yt_001_20260922.png"
    assert thumbnail.pick_manual(cfg, "slug", dt.date(2026, 9, 22), extra=["yt_001_20260922"]).name == "yt_001_20260922.png"
    assert finals.parse_jst("2026-09-23 19:00").hour == 19
    assert finals.parse_jst("").__class__ is type(None)


def test_title_format_wraps_hook_and_channel_suffix(cfg):
    from ytecon.metadata import format_title, MAX_TITLE
    t = format_title(cfg, "なぜ給料が上がっても生活は楽にならないのか", "給料どこいった")
    assert t == "なぜ給料が上がっても生活は楽にならないのか【給料どこいった】【ずんだもん&めたん解説】"   # 頭は本題（検索される語）
    cfg.raw["upload"]["title_hook_position"] = "front"
    assert format_title(cfg, "本題", "引き").startswith("【引き】本題")
    cfg.raw["upload"]["title_hook_position"] = "back"
    assert format_title(cfg, "【本題】", "") == "本題【ずんだもん&めたん解説】"     # 引きが無ければ前は付けない
    from ytecon.metadata import keyword_first
    assert keyword_first("給料が上がっても楽にならない理由", "手取り") == "手取り｜給料が上がっても楽にならない理由"
    assert keyword_first("手取りが増えない理由", "手取り") == "手取りが増えない理由"          # 既に頭にあれば触らない
    assert keyword_first("タイトル", "") == "タイトル"
    long = format_title(cfg, "あ" * 120, "数字の落差")
    assert len(long) <= MAX_TITLE and long.endswith("【ずんだもん&めたん解説】") and "…" in long


def test_publish_at_in_jst_is_sent_to_youtube_as_utc(cfg, monkeypatch, tmp_path):
    """JST の予約時刻を渡しても、YouTube には UTC（Z）で送られる（21:00 JST → 12:00Z）."""
    from ytecon import youtube, finals
    from ytecon.metadata import Metadata
    from ytecon.state import Store
    sent = {}
    class _Req:
        def __init__(self, body): self.body = body
        def next_chunk(self):
            sent.update(self.body); return None, {"id": "vid"}
    class _Videos:
        def insert(self, part, body, media_body): return _Req(body)
    class _Svc:
        def videos(self): return _Videos()
    monkeypatch.setattr(youtube, "build_service", lambda _cfg: _Svc())
    import googleapiclient.http
    monkeypatch.setattr(googleapiclient.http, "MediaFileUpload", lambda *a, **k: None)
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    store = Store(tmp_path / "s.sqlite3")
    youtube.upload_video(cfg, store, video, Metadata(title="t", description="d"),
                         publish_at=finals.parse_jst("2026-09-22 21:00"))
    assert sent["status"]["publishAt"] == "2026-09-22T12:00:00Z"


def test_cast_thumbnail_renders_characters_and_big_text(cfg, tmp_path):
    from PIL import Image
    from ytecon import thumbnail
    out = thumbnail.render_cast(cfg, "会社員の一生／給料は2億円／ない", "生涯賃金とは？", tmp_path / "t.jpg",
                                bubble="足りるのだ？", seed="x")
    img = Image.open(out)
    assert img.size == (1280, 720) and out.stat().st_size < 2_000_000
    # 数字は赤で出る（赤い画素がある）
    px = img.convert("RGB").resize((320, 180)).getdata()
    assert any(r > 220 and g < 60 and b < 60 for r, g, b in px)
    # 「／」入りの文言を bar / framed に渡しても落ちない（「／」は消して 1 行として組む）
    thumbnail.render_framed(cfg, "一行目／二行目", "", tmp_path / "f.jpg")


def test_panel_thumbnail_limits_people_and_renders_offline(cfg, tmp_path, monkeypatch):
    from PIL import Image, ImageDraw
    from ytecon import thumbpanel as T
    assert cfg.get("thumbnail.style") == "panel"
    monkeypatch.setattr(T, "search_photos", lambda *a, **k: [])          # ネットに出ない
    # 白い背景に人の形（切り抜ける）と、背景の写真
    person = Image.new("RGB", (400, 600), "white")
    ImageDraw.Draw(person).ellipse([120, 60, 280, 220], fill="#333333")
    ImageDraw.Draw(person).rectangle([80, 220, 320, 600], fill="#222244")
    person.save(tmp_path / "p.jpg")
    Image.new("RGB", (800, 600), "#88AA44").save(tmp_path / "bg.jpg")
    spec = {"layout": "flow", "headline": [["昇給しても", "red"], ["貧乏な謎", "white"]], "labels": ["賃上げ5%", "でも値上げ", "実質マイナス"],
            "panels": [{"mood": "gold", "bg_file": str(tmp_path / "bg.jpg"), "person_file": str(tmp_path / "p.jpg"), "zunda": "喜", "line": "やったのだ！"},
                       {"mood": "red", "person_file": str(tmp_path / "p.jpg"), "arrows": "up", "stamp": "値上げ", "zunda": "驚愕", "line": "高すぎるのだ！"},
                       {"mood": "dark", "person_file": str(tmp_path / "p.jpg"), "zunda": "絶望", "line": "なんでなのだ…"}]}
    limited = T._limit_people(spec["panels"])
    assert sum(1 for p in limited if p.get("person_file") or p.get("person_query")) == T.MAX_PEOPLE == 2
    out = T.render(cfg, spec, tmp_path / "flow.jpg")
    assert Image.open(out).size == (1280, 720) and out.stat().st_size < 2_000_000
    vs = {"layout": "versus", "headline": [["利上げなのに", "white"], ["円安", "red"]],
          "panels": [{"label": "日銀 1.25%", "mood": "blue", "arrows": "up", "zunda": "指", "line": "上げたのだ"},
                     {"label": "1ドル158円", "mood": "glitch", "person_file": str(tmp_path / "p.jpg"), "zunda": "驚愕", "line": "逆なのだ！？"}]}
    assert Image.open(T.render(cfg, vs, tmp_path / "vs.jpg")).size == (1280, 720)
    # 切り抜けない写真（背景が白くない）は使わない
    assert T.cutout_white(Image.open(tmp_path / "bg.jpg")) is None
    # 全身は上半身だけにする
    tall = Image.new("RGBA", (100, 400))
    assert T.bust(tall).size == (100, 135)


def test_panel_person_never_crosses_zundamon_and_font_is_square_gothic(cfg):
    from PIL import Image
    from ytecon import thumbpanel as T
    assert T._font(cfg, 40).path.endswith("NotoSansJP-Black.ttf")        # 動画の丸ゴシックではなく、角ゴシックの極太
    wide = Image.new("RGBA", (900, 700), (40, 40, 40, 255))               # 腕を広げた横長の人
    bg = Image.new("RGBA", (426, 624), (0, 0, 0, 0))
    T.place_person(bg, wide, right=240, top=90, max_h=470, min_h=400)
    box = bg.getbbox()
    assert box is not None and box[2] <= 240                              # ずんだもんの左端より右に出ない
    bg2 = Image.new("RGBA", (640, 570), (0, 0, 0, 0))
    T.place_person(bg2, wide, left=260, top=90, max_h=440)
    assert bg2.getbbox()[0] >= 260


def test_title_with_leading_bracket_hook_is_not_broken(cfg):
    from ytecon.metadata import format_title
    t = format_title(cfg, "【値下げは政策】スマホ代はなぜ5年でまた上がる？", "値下げは政策")
    assert t == "スマホ代はなぜ5年でまた上がる？【値下げは政策】【ずんだもん&めたん解説】"
    t = format_title(cfg, "【値下げは政策】スマホ代はなぜ上がる？", "")
    assert t == "スマホ代はなぜ上がる？【値下げは政策】【ずんだもん&めたん解説】"
