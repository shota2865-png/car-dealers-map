"""API を叩かずに検証できる部分のテスト."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from ytecon.config import Config, load_config
from ytecon.metadata import build_chapters
from ytecon.script import Section, VideoScript, Visual, split_sentences, tts_text
from ytecon.state import Store
from ytecon.subtitles import _chunk, build_cues
from ytecon.topics import is_duplicate
from ytecon.tts import Line, VoiceTrack


@pytest.fixture
def cfg() -> Config:
    return load_config()


# ----------------------------------------------------------------------
def test_config_target_chars(cfg: Config):
    """目標文字数 = 話速 × 尺。値そのものは設定なのでハードコードしない."""
    lo, hi = cfg.target_chars
    cpm = cfg.get("video.chars_per_minute")
    assert lo == int(cpm * cfg.get("video.target_minutes_min"))
    assert hi == int(cpm * cfg.get("video.target_minutes_max"))
    assert lo < hi


def test_config_dotted_lookup(cfg: Config):
    assert cfg.get("tts.provider") == "voicevox"
    assert cfg.get("nope.nothing", "default") == "default"
    with pytest.raises(KeyError):
        cfg["nope.nothing"]


# ----------------------------------------------------------------------
def test_store_roundtrip(tmp_path: Path):
    store = Store(tmp_path / "s.sqlite3")
    tid = store.add_topic("円安の話", "生活への影響", "news", [{"name": "日銀"}], 80)
    assert "円安の話" in store.recent_topic_titles(30)

    store.create_video("slug-1", tid, "円安の話")
    store.update_video("slug-1", status="scripted", stage={"script": "a.json"})
    store.update_video("slug-1", stage={"duration": 512})
    rec = store.get_video("slug-1")
    assert rec is not None
    assert rec.status == "scripted"
    # stage はマージされる（上書きで消えない）
    assert rec.stage == {"script": "a.json", "duration": 512}

    assert store.quota_used("2026-01-01") == 0
    store.add_quota("2026-01-01", 1600)
    store.add_quota("2026-01-01", 50)
    assert store.quota_used("2026-01-01") == 1650


# ----------------------------------------------------------------------
def test_dedupe_catches_variants():
    history = ["円安はなぜ起きる？ 生活にどう効いてくるのか"]
    assert is_duplicate("円安はなぜ起きる?生活にどう効いてくるのか", history, 0.72)
    assert not is_duplicate("半導体が経済の主役になった理由", history, 0.72)


def test_dedupe_rejects_empty():
    assert is_duplicate("   ", [], 0.72)


# ----------------------------------------------------------------------
def test_tts_text_strips_unreadable():
    out = tts_text("成長率は3%です※詳細は https://example.com 参照（速報値）")
    assert "%" not in out and "パーセント" in out
    assert "http" not in out
    assert "※" not in out


def test_split_sentences():
    assert split_sentences("あ。い！う？ え。") == ["あ。", "い！", "う？", "え。"]


def test_script_char_count_ignores_whitespace():
    s = VideoScript(
        topic_title="t", hook="あい うえ", sections=[
            Section(heading="h", narration="かきくけこ", visual=Visual())
        ],
        closing="さし", title_candidates=[], description="", tags=[],
        thumbnail_copy={}, sources=[],
    )
    assert s.total_chars == 4 + 5 + 2


def test_script_json_roundtrip(tmp_path: Path):
    s = VideoScript(
        topic_title="円安", hook="つかみ",
        sections=[Section(heading="なぜ", narration="本文", on_screen=["ポイント"],
                          visual=Visual(kind="chart", chart={"type": "bar"}))],
        closing="まとめ", title_candidates=["A"], description="d", tags=["経済"],
        thumbnail_copy={"main": "円安"}, sources=[{"name": "日銀", "url": "x"}],
    )
    p = s.save(tmp_path / "script.json")
    back = VideoScript.load(p)
    assert back.sections[0].visual.chart == {"type": "bar"}
    assert back.total_chars == s.total_chars


# ----------------------------------------------------------------------
def _track() -> VoiceTrack:
    lines = [
        Line("hook", 0, "つかみです。", 0.0, 3.0),
        Line("s0", 0, "あ" * 45 + "。", 3.5, 15.0),
        Line("closing", 0, "まとめです。", 20.0, 24.0),
    ]
    return VoiceTrack(wav_path=Path("x.wav"), lines=lines)


def test_block_span():
    t = _track()
    assert t.block_span("s0") == (3.5, 15.0)
    assert t.block_span("nope") == (0.0, 0.0)
    assert t.duration == 24.0


def test_chunk_two_lines_max():
    groups = _chunk("あ" * 45, per_line=20)
    assert [len("".join(g)) for g in groups] == [40, 5]
    assert all(len(g) <= 2 for g in groups)


def test_cues_stay_inside_their_line(cfg: Config):
    cues = build_cues(cfg, _track())
    assert cues[0].start == 0.0
    long_cues = [c for c in cues if c.start >= 3.5 and c.end <= 15.0]
    assert len(long_cues) >= 2
    for c in cues:
        assert c.end > c.start


def _one_section_script() -> VideoScript:
    return VideoScript(
        topic_title="t", hook="h",
        sections=[Section(heading="第1章", narration="n", visual=Visual())],
        closing="c", title_candidates=[], description="", tags=[],
        thumbnail_copy={}, sources=[],
    )


def test_chapters_are_built_from_measured_audio():
    track = VoiceTrack(wav_path=Path("x.wav"), lines=[
        Line("hook", 0, "つかみ", 0.0, 20.0),
        Line("s0", 0, "本文", 20.0, 200.0),
        Line("closing", 0, "まとめ", 200.0, 240.0),
    ])
    chapters = build_chapters(_one_section_script(), track)
    assert chapters == ["0:00 今日の話", "0:20 第1章", "3:20 まとめ"]


def test_chapters_dropped_when_too_close_together():
    """YouTube は『3つ以上・10秒以上間隔』でないとチャプターを認識しない。
    条件を満たせないときは半端に出さず、空で返すのが正しい挙動。"""
    assert build_chapters(_one_section_script(), _track()) == []


# ----------------------------------------------------------------------
def test_voice_manifest_roundtrip(tmp_path: Path):
    t = _track()
    t.wav_path = tmp_path / "n.wav"
    p = t.save_manifest(tmp_path / "n.json")
    back = VoiceTrack.load_manifest(p)
    assert back.duration == t.duration
    assert back.lines[1].text == t.lines[1].text


def test_wav_concat_math(tmp_path: Path):
    """tts が使う『秒＝フレーム数/レート』の前提を固定しておく."""
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000)
    with wave.open(str(path), "rb") as w:
        assert w.getnframes() / w.getframerate() == 1.0


# ----------------------------------------------------------------------
def test_telops_are_placed_on_measured_sentence_ends(cfg: Config):
    """テロップは after_sentence を、その文の実測終了時刻に変換して置く."""
    from ytecon.script import Caption
    from ytecon.subtitles import build_telops

    script = VideoScript(
        topic_title="t", hook="h",
        sections=[Section(heading="h", narration="n", visual=Visual(),
                          captions=[Caption("最初", "KEYWORD", 0),
                                    Caption("2文目の後", "DATA", 1)])],
        closing="c", title_candidates=[], description="", tags=[],
        thumbnail_copy={}, sources=[],
    )
    track = VoiceTrack(wav_path=Path("x.wav"), lines=[
        Line("s0", 0, "一文目。", 0.0, 3.0),
        Line("s0", 1, "二文目。", 3.2, 6.0),
        Line("s0", 2, "三文目。", 6.2, 9.0),
    ])
    telops = build_telops(cfg, script, track)
    assert [t.text for t in telops] == ["最初", "2文目の後"]
    assert telops[0].start == 3.0          # 1文目の終わり
    assert telops[1].start == 6.0          # 2文目の終わり


def test_telops_never_overlap(cfg: Config):
    """同時に2つ出すと読めないので、重なりは自動で解消する."""
    from ytecon.script import Caption
    from ytecon.subtitles import build_telops

    script = VideoScript(
        topic_title="t", hook="h",
        sections=[Section(heading="h", narration="n", visual=Visual(),
                          captions=[Caption(f"c{i}", "NORMAL", i) for i in range(4)])],
        closing="c", title_candidates=[], description="", tags=[],
        thumbnail_copy={}, sources=[],
    )
    track = VoiceTrack(wav_path=Path("x.wav"), lines=[
        Line("s0", i, f"文{i}。", i * 0.8, i * 0.8 + 0.7) for i in range(5)
    ])
    telops = build_telops(cfg, script, track)
    for a, b in zip(telops, telops[1:]):
        assert a.end <= b.start, f"{a.text} と {b.text} が重なっている"
        assert a.end > a.start


def test_each_caption_type_gets_its_own_ass_style(cfg: Config, tmp_path: Path):
    """種類ごとに色と大きさが変わること（全部同じ見た目なら意味がない）."""
    from ytecon.subtitles import TelopCue, write_ass

    telops = [TelopCue(0.0, 2.0, "あ", t) for t in
              ("NORMAL", "KEYWORD", "EMPHASIS", "PUNCHLINE", "EDITORIAL", "DATA")]
    path = write_ass(cfg, [], tmp_path / "s.ass", telops=telops)
    text = path.read_text(encoding="utf-8")

    styles = {}
    for line in text.splitlines():
        if line.startswith("Style: T_"):
            parts = line[7:].split(",")
            styles[parts[0]] = (parts[1], parts[2], parts[3])   # family,size,color

    assert len(styles) == 6
    sizes = {name: int(v[1]) for name, v in styles.items()}
    colors = {name: v[2] for name, v in styles.items()}
    # 強調ほど大きい
    assert sizes["T_PUNCHLINE"] > sizes["T_EMPHASIS"] > sizes["T_NORMAL"]
    # 編集者の声はいちばん小さい
    assert sizes["T_EDITORIAL"] < sizes["T_NORMAL"]
    # 色が全部同じではない
    assert len(set(colors.values())) >= 4


def test_srt_has_no_telops(cfg: Config, tmp_path: Path):
    """YouTube に渡す字幕にテロップを混ぜない（二重に出てしまう）."""
    from ytecon.script import Caption
    from ytecon.subtitles import build

    script = VideoScript(
        topic_title="t", hook="h",
        sections=[Section(heading="h", narration="n", visual=Visual(),
                          captions=[Caption("テロップだけの文言", "PUNCHLINE", 0)])],
        closing="c", title_candidates=[], description="", tags=[],
        thumbnail_copy={}, sources=[],
    )
    track = VoiceTrack(wav_path=Path("x.wav"), lines=[
        Line("s0", 0, "喋った内容。", 0.0, 3.0)])
    out = build(cfg, track, tmp_path, script=script)
    srt = out["srt"].read_text(encoding="utf-8")
    ass = out["ass"].read_text(encoding="utf-8")
    assert "テロップだけの文言" not in srt
    assert "テロップだけの文言" in ass



def test_missing_glyphs_are_substituted(cfg: Config):
    """同梱フォントに無い記号（→ ※ など）が豆腐にならず、近い字に置き換わること."""
    from ytecon.assets import safe_text

    out = safe_text(cfg, "110円 → 151円 ※注 2021〜2024")
    assert "→" not in out and "※" not in out
    assert "〜" in out                    # ある字はそのまま
    assert "110円" in out and "151円" in out
