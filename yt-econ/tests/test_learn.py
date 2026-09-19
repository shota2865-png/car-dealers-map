"""参照動画の分析部分のテスト.

字幕の取得はネットワークが要るのでここでは扱わない。
代わりに、YouTube が実際に返す形の VTT を食わせて、解析と実測を固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ytecon.config import load_config
from ytecon.learn import (
    Reference, _strip_overlap, aggregate, load_style, measure, parse_vtt,
    render_for_prompt,
)
from ytecon.script import target_chars

# YouTube の自動生成字幕は、前のキューの末尾を次のキューが繰り返す
AUTO_VTT = """WEBVTT
Kind: captions
Language: ja

00:00:00.320 --> 00:00:03.100 align:start position:0%
スーパーで同じ買い物をしているのに

00:00:03.100 --> 00:00:06.480 align:start position:0%
スーパーで同じ買い物をしているのに<00:00:03.500><c> 去年より</c>

00:00:06.480 --> 00:00:09.900 align:start position:0%
去年より千円多く払っていませんか。

00:00:09.900 --> 00:00:13.200 align:start position:0%
今日は円安の仕組みが分かります。
"""

MANUAL_VTT = """WEBVTT

1
00:00:00.000 --> 00:00:04.000
円安とは、円の価値が下がることです。

2
00:00:04.000 --> 00:00:10.000
日本銀行によると、金利差が為替に影響します。
"""


# ----------------------------------------------------------------------
def test_parse_manual_vtt():
    cues = parse_vtt(MANUAL_VTT)
    assert len(cues) == 2
    assert cues[0][0] == 0.0 and cues[0][1] == 4.0
    assert cues[1][2] == "日本銀行によると、金利差が為替に影響します。"


def test_parse_auto_vtt_removes_rollup_duplication():
    """自動字幕の繰り返しを落とさないと、話速が倍近く水増しされる."""
    cues = parse_vtt(AUTO_VTT)
    joined = "".join(c[2] for c in cues)
    assert joined.count("スーパーで同じ買い物をしているのに") == 1
    assert joined.count("去年より") == 1
    assert "千円多く払っていませんか" in joined


def test_parse_strips_inline_timing_tags():
    cues = parse_vtt(AUTO_VTT)
    assert all("<" not in c[2] for c in cues)


def test_strip_overlap():
    assert _strip_overlap("あいうえお", "うえおかきく") == "かきく"
    assert _strip_overlap("あいうえお", "あいうえお") == ""
    assert _strip_overlap("あいうえお", "まったく別") == "まったく別"


def test_parse_empty_input():
    assert parse_vtt("WEBVTT\n\n") == []


# ----------------------------------------------------------------------
def _ref() -> Reference:
    ref = Reference(url="u", title="t", duration=600.0, chapters=[
        {"title": "導入", "start_time": 0, "end_time": 25},
        {"title": "本編1", "start_time": 25, "end_time": 130},
        {"title": "本編2", "start_time": 130, "end_time": 240},
        {"title": "まとめ", "start_time": 240, "end_time": 600},
    ])
    ref.cues = parse_vtt(MANUAL_VTT)
    ref.transcript = "".join(c[2] for c in ref.cues)
    return ref


def test_measure_reports_usable_numbers():
    m = measure(_ref())
    assert m["duration_minutes"] == 10.0
    assert m["chapters"] == 4
    assert m["hook_seconds"] == 25
    assert m["sentences"] == 2
    assert m["avg_sentence_chars"] > 0
    assert m["chars_per_minute"] > 0


def test_measure_handles_video_without_chapters():
    ref = _ref()
    ref.chapters = []
    m = measure(ref)
    assert m["chapters"] == 0
    assert m["hook_seconds"] == 0
    assert m["duration_minutes"] == 10.0


def test_aggregate_uses_median_not_mean():
    """1本だけ極端に速い動画に引っ張られないこと."""
    stats = aggregate([
        {"chars_per_minute": 300, "duration_minutes": 9, "avg_sentence_chars": 40,
         "chapters": 6, "median_chapter_seconds": 90, "hook_seconds": 20},
        {"chars_per_minute": 320, "duration_minutes": 10, "avg_sentence_chars": 42,
         "chapters": 6, "median_chapter_seconds": 95, "hook_seconds": 22},
        {"chars_per_minute": 900, "duration_minutes": 11, "avg_sentence_chars": 44,
         "chapters": 7, "median_chapter_seconds": 99, "hook_seconds": 24},
    ])
    assert stats["chars_per_minute"] == 320
    assert stats["videos"] == 3


# ----------------------------------------------------------------------
def test_measured_speed_overrides_the_guessed_config(tmp_path, monkeypatch):
    """learn 後は、当て推量の chars_per_minute ではなく実測値で尺を決める."""
    cfg = load_config()
    assert cfg.get("video.chars_per_minute") == 340
    assert target_chars(cfg) == (int(340 * 8), int(340 * 10))

    style_path = cfg.root / "config" / "style.yaml"
    assert not style_path.exists(), "テストが既存の style.yaml を壊さないこと"
    style_path.write_text(
        yaml.safe_dump({"measured": {"chars_per_minute": 420}}, allow_unicode=True),
        encoding="utf-8",
    )
    try:
        assert target_chars(cfg) == (int(420 * 8), int(420 * 10))
    finally:
        style_path.unlink()


def test_no_style_file_is_fine():
    cfg = load_config()
    assert load_style(cfg) is None
    assert target_chars(cfg) == cfg.target_chars


def test_render_for_prompt_is_instructional():
    text = render_for_prompt({
        "sources": [{"title": "円安の話"}],
        "measured": {"chars_per_minute": 372, "avg_sentence_chars": 41,
                     "hook_seconds": 22, "chapters": 6,
                     "median_chapter_seconds": 95},
        "voice": {
            "summary": "身近な違和感から入る",
            "opening_pattern": "〈日常の違和感〉→〈結論の予告〉",
            "signature_phrases": ["〜なんですね", "ここが大事で"],
            "avoid": ["ヤバい", "絶対に"],
            "what_not_to_copy": "本人の経歴に依存した語り",
        },
    })
    assert "372文字/分" in text
    assert "〜なんですね" in text
    assert "使わないもの" in text and "ヤバい" in text
    assert "真似しないこと" in text


def test_render_for_prompt_tolerates_a_sparse_profile():
    assert isinstance(render_for_prompt({}), str)
    assert isinstance(render_for_prompt({"voice": {}, "measured": {}}), str)


# ----------------------------------------------------------------------
def _rollup_vtt(truth: str, step: int = 9, tail: int = 7) -> str:
    """YouTube の自動生成字幕（ロールアップ）を忠実に再現する.

    各キューは「前キューの末尾」＋「新しい語」という形で届く。
    """
    lines, t, prev_tail, pos = ["WEBVTT", ""], 0, "", 0
    while pos < len(truth):
        body = prev_tail + truth[pos:pos + step]
        lines += [f"00:00:{t:02d}.000 --> 00:00:{t + 3:02d}.000", body, ""]
        prev_tail = body[-tail:]
        pos += step
        t += 3
    return "\n".join(lines)


TRUTH = (
    "スーパーで同じ買い物をしているのに去年より千円多く払っていませんか"
    "その原因のひとつが円安です円安とは円の価値が下がることを指します"
    "日本銀行によると政策金利の差が為替を動かします"
)


def test_rollup_transcript_is_reconstructed_exactly():
    """ここが崩れると話速の実測が狂い、尺の見積りごとずれる."""
    cues = parse_vtt(_rollup_vtt(TRUTH))
    assert "".join(c[2] for c in cues) == TRUTH


def test_rollup_dedup_matters_a_lot():
    """重複を残すと文字数が1.5倍以上に膨らむ（=話速を過大評価する）."""
    vtt = _rollup_vtt(TRUTH)
    naive = sum(len(l) for l in vtt.splitlines()
                if l and "-->" not in l and l != "WEBVTT")
    clean = len("".join(c[2] for c in parse_vtt(vtt)))
    assert naive > clean * 1.4
    assert clean == len(TRUTH)


@pytest.mark.parametrize("step,tail", [(5, 4), (9, 7), (14, 11), (20, 3)])
def test_reconstruction_holds_across_cue_shapes(step, tail):
    cues = parse_vtt(_rollup_vtt(TRUTH, step=step, tail=tail))
    assert "".join(c[2] for c in cues) == TRUTH


def test_overlap_spanning_two_cues_back_is_caught():
    """繰り返しが2つ前のキューに跨るケース。直前だけ見ていると取りこぼす."""
    vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
円安とは円の価値が

00:00:03.000 --> 00:00:06.000
円安とは円の価値が下がること

00:00:06.000 --> 00:00:09.000
円安とは円の価値が下がることです
"""
    assert "".join(c[2] for c in parse_vtt(vtt)) == "円安とは円の価値が下がることです"


# ----------------------------------------------------------------------
def test_local_files_do_not_require_ytdlp(monkeypatch, tmp_path):
    """ローカル動画だけを渡したとき、yt-dlp が無くても動くこと.

    本番前に手元の1本で配管を確かめられるようにするための経路。
    """
    from ytecon import learn as learn_mod

    def boom():
        raise learn_mod.LearnError("yt-dlp が必要です")

    monkeypatch.setattr(learn_mod, "ensure_ytdlp", boom)
    assert learn_mod.expand_channels(["/tmp/a.mp4", "https://youtu.be/x"]) == \
        ["/tmp/a.mp4", "https://youtu.be/x"]


def test_channel_urls_are_recognised(tmp_path):
    from ytecon.learn import _is_channel

    assert _is_channel("https://www.youtube.com/@kangaesugiruashi")
    assert _is_channel("https://www.youtube.com/channel/UCxxxx")
    assert not _is_channel("https://youtu.be/XeTAlZiIWHE")
    # 実在するローカルパスはチャンネル扱いしない
    f = tmp_path / "@weird.mp4"
    f.write_bytes(b"")
    assert not _is_channel(str(f))


def test_stable_id_is_stable():
    """再実行で同じ名前になること（hash() はプロセスごとに変わる）."""
    from ytecon.learn import _stable_id

    assert _stable_id("https://youtu.be/abc") == _stable_id("https://youtu.be/abc")
    assert _stable_id("https://youtu.be/abc") != _stable_id("https://youtu.be/xyz")


def test_measure_survives_a_video_without_subtitles():
    """字幕が取れなくても落ちないこと（映像の実測だけでも価値がある）."""
    from ytecon.learn import Reference, measure

    ref = Reference(url="u", title="t", duration=600.0)
    m = measure(ref)
    assert m["chars_per_minute"] == 0
    assert m["duration_minutes"] == 10.0


def test_measure_with_zero_duration():
    from ytecon.learn import Reference, measure

    assert measure(Reference(url="u", title="t"))["chars_per_minute"] == 0
