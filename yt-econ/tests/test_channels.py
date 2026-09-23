"""多チャンネル化: extends / 鍵の接頭辞 / 分野語 / 完成品の接頭辞 / 心理学チャンネルの設定 / framed サムネ."""
from __future__ import annotations

import copy
import datetime as dt
import os
import textwrap
from pathlib import Path

import pytest
import yaml

from ytecon import config as cfgmod
from ytecon.config import Config, deep_merge, load_config


# --- extends と深いマージ -------------------------------------------------
def test_deep_merge_keeps_nested_and_replaces_lists():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 1}
    over = {"a": {"y": [9]}, "c": 3}
    out = deep_merge(base, over)
    assert out == {"a": {"x": 1, "y": [9]}, "b": 1, "c": 3}
    assert base["a"]["y"] == [1, 2]            # 元は変えない


def test_extends_merges_parent_relative_to_child(tmp_path):
    (tmp_path / "channel.yaml").write_text(textwrap.dedent("""
        channel: {name: 本体, field: 経済}
        upload: {publish_times_jst: ["19:00"], playlist_title: 経済}
        tts: {voicevox: {speed: 1.2}}
    """), encoding="utf-8")
    sub = tmp_path / "channels" / "x"
    sub.mkdir(parents=True)
    (sub / "channel.yaml").write_text(textwrap.dedent("""
        extends: ../../channel.yaml
        channel: {key: x, name: 別, env_prefix: X_}
        upload: {publish_times_jst: ["20:00"]}
    """), encoding="utf-8")
    cfg = load_config(sub / "channel.yaml")
    assert cfg.channel_key == "x"
    assert cfg.get("channel.name") == "別"
    assert cfg.get("channel.field") == "経済"                 # 親から継承
    assert cfg.get("upload.publish_times_jst") == ["20:00"]  # リストは置き換え
    assert cfg.get("upload.playlist_title") == "経済"        # 同じ節の他のキーは残る
    assert cfg.get("tts.voicevox.speed") == 1.2
    assert "extends" not in cfg.raw


def test_extends_cycle_is_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(tmp_path / "a.yaml")


def test_channel_config_path_and_listing():
    assert cfgmod.channel_config_path("") == cfgmod.DEFAULT_CONFIG
    assert cfgmod.channel_config_path("main") == cfgmod.DEFAULT_CONFIG
    assert cfgmod.channel_config_path("psych").name == "channel.yaml"
    assert "psych" in cfgmod.list_channels() and cfgmod.list_channels()[0] == "main"
    with pytest.raises(FileNotFoundError):
        cfgmod.channel_config_path("no_such_channel")


# --- 鍵の接頭辞 -------------------------------------------------------------
def test_env_prefix_is_tried_first_then_falls_back(monkeypatch):
    cfg = Config(raw={"channel": {"env_prefix": "PSY_"}})
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "main")
    monkeypatch.delenv("PSY_YOUTUBE_REFRESH_TOKEN", raising=False)
    assert cfg.env("YOUTUBE_REFRESH_TOKEN") == "main"
    assert cfg.env_name("YOUTUBE_REFRESH_TOKEN") == "YOUTUBE_REFRESH_TOKEN"
    monkeypatch.setenv("PSY_YOUTUBE_REFRESH_TOKEN", "psy")
    assert cfg.env("YOUTUBE_REFRESH_TOKEN") == "psy"
    assert cfg.env_name("YOUTUBE_REFRESH_TOKEN") == "PSY_YOUTUBE_REFRESH_TOKEN"
    plain = Config(raw={})
    assert plain.env("YOUTUBE_REFRESH_TOKEN") == "main"


def test_second_channel_never_uploads_with_main_keys(monkeypatch):
    from ytecon import youtube
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "main")
    monkeypatch.delenv("PSY_YOUTUBE_REFRESH_TOKEN", raising=False)
    cfg = Config(raw={"channel": {"key": "psych", "env_prefix": "PSY_"}})
    with pytest.raises(youtube.UploadError):
        youtube.check_channel_keys(cfg)
    # 意図して同じアカウントに上げる設定なら通る
    shared = Config(raw={"channel": {"env_prefix": "PSY_"}, "upload": {"share_main_account": True}})
    youtube.check_channel_keys(shared)
    monkeypatch.setenv("PSY_YOUTUBE_REFRESH_TOKEN", "psy")
    youtube.check_channel_keys(cfg)
    youtube.check_channel_keys(Config(raw={}))   # 本体は接頭辞なし


# --- 分野の言葉 -------------------------------------------------------------
def test_domain_defaults_match_legacy_wording():
    from ytecon import domain
    cfg = Config(raw={})
    assert domain.field(cfg) == "経済" and domain.lens(cfg) == "経済学"
    assert domain.pitch(cfg) == "寝る前に聴く、お金と就活とAIの話。"
    assert domain.hashtags(cfg) == ["#経済", "#就活"]
    assert "金融商品" in domain.disclaimer(cfg)
    assert domain.hook_examples(cfg)["question"].startswith("給料どこいった")


def test_domain_reads_channel_overrides():
    from ytecon import domain
    cfg = Config(raw={"channel": {"field": "心理学", "hashtags": ["心理学", "#メンタル"],
                                  "hook_examples": {"gap": "決意9割、続くのは1割"}}})
    assert domain.field(cfg) == "心理学"
    assert domain.hashtags(cfg) == ["#心理学", "#メンタル"]
    ex = domain.hook_examples(cfg)
    assert ex["gap"] == "決意9割、続くのは1割" and ex["question"].startswith("給料")   # 無いものは既定


def test_prompts_take_the_field_from_config():
    from ytecon import domain, script, topics, shorts, metadata
    assert "{field}" in script._SYSTEM and "{lens}" in script._SYSTEM and "{term_examples}" in script._SYSTEM
    assert "{field}" in script._RESEARCH_SYSTEM and "{field}" in script._FACT_SYSTEM
    assert "{field}" in topics._SELECT_SYSTEM
    assert "{topic_words}" in shorts._STORY_SYSTEM
    assert "{question}" in metadata._HOOK_TAG_GUIDE
    cfg = Config(raw={"channel": {"field": "心理学"}})
    assert "心理学メディア" in script._FACT_SYSTEM.replace("{field}", domain.field(cfg))
    assert "経済" not in metadata._HOOK_TAG_GUIDE.format(**domain.hook_examples(Config(raw={"channel": {"hook_examples": {
        "question": "a", "gap": "b", "claim": "c"}}})))


def test_listening_modes():
    from ytecon import script
    assert script.listening_block(Config(raw={"channel": {"listening_mode": "sleep"}})).startswith("# 聴かれ方: 寝る前")
    assert "今日その場で試せる" in script.listening_block(Config(raw={"channel": {"listening_mode": "daytime"}}))
    assert script.listening_block(Config(raw={})) == ""


# --- 完成品の接頭辞 ---------------------------------------------------------
def test_finals_prefix_per_channel(tmp_path):
    from ytecon import finals
    day = dt.date(2026, 9, 29)
    assert finals.name_for(1, day) == "yt_001_20260929"
    assert finals.name_for(3, day, "ps") == "ps_003_20260929"
    assert finals.parse_name("ps_003_20260929") == (3, day)
    assert finals.parse_name("ps_003_20260929", "yt") is None
    assert finals.parse_name("yt_001_20260929", "yt") == (1, day)
    assert finals.parse_name("PS_001_20260929") is None
    cfg = Config(raw={"upload": {"finals_dir": str(tmp_path / "f"), "finals_prefix": "ps"}, "pipeline": {"workdir": "out"}},
                 root=tmp_path)
    assert finals.prefix(cfg) == "ps"
    assert finals.prefix(Config(raw={"upload": {"finals_prefix": "BAD PREFIX"}})) == "yt"
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "ps_004_20260929.json").write_text("{}", encoding="utf-8")
    (tmp_path / "f" / "yt_009_20260929.json").write_text("{}", encoding="utf-8")   # 本体の番号は数えない
    assert finals.next_number(cfg, None) == 5
    assert finals.assign(cfg, None, day) == "ps_005_20260929"


# --- 心理学チャンネルの設定 ---------------------------------------------------
@pytest.fixture
def psych():
    return copy.deepcopy(load_config(channel="psych"))


def test_psych_profile_overrides_and_inherits(psych):
    from ytecon import domain, finals, goals, topics, design, script
    assert psych.channel_key == "psych" and psych.get("channel.env_prefix") == "PSY_"
    assert "おやすみ" not in psych.get("channel.name")
    assert domain.field(psych) == "心理学" and "心理学" in psych.get("upload.title_suffix")
    assert psych.get("channel.listening_mode") == "daytime"
    assert psych.get("video.design") == "dads" and design.tokens(psych)["name"] == "dads"
    assert psych.get("render.bgm.file") != load_config().get("render.bgm.file")
    assert psych.get("upload.publish_times_jst") == ["20:00"]
    assert len(psych.get("shorts.publish_times_jst")) == 3
    assert psych.get("thumbnail.style") == "framed"
    assert finals.prefix(psych) == "ps"
    # 置き場が本体と混ざらない
    main = load_config()
    assert psych.workdir != main.workdir
    assert topics.schedule_path(psych) != topics.schedule_path(main) and topics.schedule_path(psych).exists()
    assert goals.goals_path(psych) != goals.goals_path(main) and goals.load_goal(psych).views > 0
    assert Path(psych.get("upload.thumbnail_dir")) != Path(main.get("upload.thumbnail_dir"))
    assert Path(psych.get("upload.finals_dir")) != Path(main.get("upload.finals_dir"))
    # 継承されているもの
    assert psych.get("tts.provider") == main.get("tts.provider")
    assert psych.get("tts.voicevox.speaker") == 2                # めたんの声だけ
    assert psych.get("topics.horizon_mix") == main.get("topics.horizon_mix")
    # 立ち絵なし・1 人語り
    assert script.cast_tags(psych) == {}
    # 分野の言葉に経済が残っていない
    for key in ("field", "lens", "topic_words", "pitch", "term_examples"):
        assert "経済" not in psych.get(f"channel.{key}")
    assert all("経済" not in t for t in domain.hashtags(psych))


def test_psych_schedule_is_well_formed(psych):
    from ytecon import topics
    data = yaml.safe_load(topics.schedule_path(psych).read_text(encoding="utf-8"))
    q = data["queue"]
    assert len(q) >= 7
    for item in q:
        assert item["title"] and item["angle"] and item["horizon"] in ("flow", "bridge", "stock")
        assert len(item["key_questions"]) >= 2 and item["sources"]
    dates = [item["date"] for item in q]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)


def test_workflow_matrix_matches_channels():
    root = Path(__file__).resolve().parents[2]
    wf = yaml.safe_load((root / ".github" / "workflows" / "daily-upload.yml").read_text(encoding="utf-8"))
    rows = wf["jobs"]["produce"]["strategy"]["matrix"]["include"]
    by = {r["channel"]: r for r in rows}
    assert set(by) == set(cfgmod.list_channels())
    # 状態キャッシュの名前が互いの接頭辞にならない（restore-keys の前方一致で混ざらない）
    keys = [r["state_key"] for r in rows]
    for a in keys:
        for b in keys:
            assert a == b or not b.startswith(a)
    for key, cfg in (("main", load_config()), ("psych", load_config(channel="psych"))):
        assert by[key]["workdir"] == cfg.get("pipeline.workdir")
    assert by["psych"]["token_env"] == "PSY_YOUTUBE_REFRESH_TOKEN"
    pf = yaml.safe_load((root / ".github" / "workflows" / "publish-file.yml").read_text(encoding="utf-8"))
    assert pf[True]["workflow_dispatch"]["inputs"]["channel"]["options"] == ["main", "psych"]


# --- framed サムネ -------------------------------------------------------------
def test_framed_thumbnail_renders_1280x720(tmp_path, psych):
    from PIL import Image
    from ytecon import thumbnail
    out = thumbnail.render_framed(psych, "言い返せなかった夜、名文句が浮かぶ理由", "反すうを止める1分の手順",
                                  tmp_path / "t.jpg", photo=None)
    img = Image.open(out)
    assert img.size == (1280, 720) and out.stat().st_size < 2_000_000
    # 枠の線が引かれている（inset 34 の位置が、すぐ内側と違う色）
    rgb = img.convert("RGB")
    line, inside = rgb.getpixel((640, 34)), rgb.getpixel((640, 60))
    assert sum(abs(x - y) for x, y in zip(line, inside)) > 60
    # 写真を渡しても落ちない
    Image.new("RGB", (400, 900), "#446688").save(tmp_path / "p.jpg")
    out2 = thumbnail.render_framed(psych, "三日坊主は意志が弱いからではない", "", tmp_path / "t2.jpg", photo=tmp_path / "p.jpg")
    assert Image.open(out2).size == (1280, 720)


def test_thumbnail_style_switch(monkeypatch, tmp_path):
    from ytecon import thumbnail
    calls = {}
    monkeypatch.setattr(thumbnail, "render_framed", lambda cfg, m, s, out, **kw: calls.setdefault("framed", (m, s)) or Path(out))
    monkeypatch.setattr(thumbnail, "_build_bar", lambda cfg, sc, out, m, s: calls.setdefault("bar", (m, s)) or Path(out))
    monkeypatch.setattr(thumbnail, "find_photo", lambda cfg, q, d: None)

    class _V:  # 最小の VideoScript もどき
        thumbnail_copy = {"main": "見出し", "sub": "補足"}
        topic_title = "題"
        sections = []

    thumbnail.build(Config(raw={"thumbnail": {"style": "framed"}}), _V(), tmp_path / "a.jpg")
    thumbnail.build(Config(raw={}), _V(), tmp_path / "b.jpg")
    assert calls == {"framed": ("見出し", "補足"), "bar": ("見出し", "補足")}


# --- 表のセルは枠からはみ出さない ------------------------------------------------
def _needed_height(d, font, lines, spacing=1.15):
    bb = d.textbbox((0, 0), "".join(lines), font=font)
    return int(font.size * spacing) * (len(lines) - 1) + (bb[3] - bb[1])


@pytest.mark.parametrize("channel", ["main", "psych"])
def test_table_cells_fit_inside_box(channel):
    from PIL import Image, ImageDraw
    from ytecon import assets, shorts
    cfg = load_config(channel=channel)
    for c in (cfg, shorts.shorts_config(cfg)):      # 本編と Shorts（文字が 1.2 倍）の両方
        d = ImageDraw.Draw(Image.new("RGB", (100, 100)))
        for text in ("中央値66日（18〜254日）", "美容整形外科医マクスウェル・マルツ", "96人の日常行動を12週間追った実測の中央値", "21日"):
            for box_w, box_h in ((360, 110), (240, 90), (520, 60)):
                f, lines = assets._fit_cell(c, d, text, "title", box_w, box_h, max_lines=2, min_size=24)
                assert lines and all(d.textlength(ln, font=f) <= box_w for ln in lines), (text, box_w)
                assert _needed_height(d, f, lines) <= box_h + 2, (text, box_w, box_h, f.size, lines)


def test_compare_and_table_render_in_both_layouts(tmp_path):
    from PIL import Image
    from ytecon import assets, shorts
    items = ["出所|1960年の観察|2010年の実測", "日数|21日|中央値66日（18〜254日）", "対象|手術後の患者|96人の日常行動を12週間"]
    for c in (load_config(), shorts.shorts_config(load_config(channel="psych"))):
        assets.apply_layout(c)
        try:
            p1 = assets.render_compare(c, "21日説 vs 実測", items, "出典", tmp_path / "c.png", active=1)
            p2 = assets.render_table(c, "21日説の正体", ["出どころ|1960年の著書", "書いた人|美容整形外科医マクスウェル・マルツ"], "",
                                     tmp_path / "t.png", active=0)
            w, h = c.get("video.resolution", [1920, 1080])
            assert Image.open(p1).size == (w, h) and Image.open(p2).size == (w, h)
        finally:
            assets.apply_layout(load_config())
