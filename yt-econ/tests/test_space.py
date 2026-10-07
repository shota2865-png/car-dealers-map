"""宇宙の解析室（心理学の本編）: 動く背景・立体のグラフ・今日の問いの軸・字幕・20 分の上限・Shorts の字幕."""
from __future__ import annotations

import copy

import numpy as np
from PIL import Image

from ytecon import honpen, lab, quiz, space, wide
from ytecon.assets import palette
from ytecon.config import load_config


def _psych():
    return copy.deepcopy(load_config(channel="psych"))


def test_background_loop_is_seamless():
    stars = space._stars(n=2000)
    base = space._static_base(space._floor_layer(), space._nebula())
    a = np.asarray(space.background_frame(0.0, 60.0, stars, base), dtype=np.int16)
    b = np.asarray(space.background_frame(60.0, 60.0, stars, base), dtype=np.int16)
    assert np.abs(a - b).mean() < 0.5                 # 1 周期後は最初と同じ絵 → ループの継ぎ目が出ない
    c = np.asarray(space.background_frame(5.0, 60.0, stars, base), dtype=np.int16)
    assert np.abs(a - c).mean() > 0.01                # でも途中は動いている


def test_bars_stand_on_the_floor_and_compare():
    img = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    info = space.draw_bars3d(img, [3, 30], [False, True])
    assert info[1]["top"][1] < info[0]["top"][1]       # 大きい数字の棒が高い
    assert all(60 < it["base"][0] < 1400 for it in info)   # めたんのいる右側にかからない
    assert img.getchannel("A").getbbox()
    assert space.ratio_text([3, 30]) == "約10倍" and space.ratio_text([40, 60]) == "約1.5倍"
    assert space.ratio_text([50, 52]) == "" and space.ratio_text([1, 2, 3]) == ""


def test_space_scenes_are_transparent_and_keep_left_of_metan():
    cfg = _psych()
    assert lab.space_enabled(cfg)
    th = wide.theme_wide(cfg)
    assert th.clear
    for sc in (
        {"kind": "opening", "question": "選択肢が多いと、人は選べなくなる？", "lines": ["a", "b"]},
        {"kind": "chapter", "label": "検証 01", "heading": "本当に選べなくなる？", "sub": "まず確かめる", "index": 1,
         "roadmap": [{"heading": "本当に？", "result": "半分本当"}, {"heading": "なぜ？"}, {"heading": "いつ？"}, {"heading": "どうする？"}]},
        {"kind": "data", "heading": "買った人の割合", "source": "大学（2000年）", "note": "少ないほうが売れた",
         "bars": [{"label": "24種類", "value": 3, "unit": "%"}, {"label": "6種類", "value": 30, "unit": "%", "hl": True}]},
        {"kind": "verdict", "claim": "選択肢が多いと選べない", "result": "半分本当", "reason": "いつもではない"},
    ):
        s = wide.build_scene_wide(cfg, th, sc)
        img = s.render(s.last_step(), 99.0)
        assert img.mode == "RGBA" and img.getpixel((5, 5))[3] == 0      # 地は透明（後ろに動く背景）
        bb = img.getchannel("A").getbbox()
        assert bb and bb[2] <= 1400, (sc["kind"], bb)


def test_frame_is_clean_only_metan_and_caption():
    """上の帯（今日の問い・現在地）・例え話の絵・字幕の左の棒は出さない。めたんと字幕だけ."""
    cfg = _psych()
    fr = lab.SpaceFrame(cfg, palette(cfg), "選択肢が多いと、人は選べなくなる？", 4)
    scene = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    plain = fr.compose(scene, "data", "base", "", "🍓", 2)
    assert plain.mode == "RGBA" and plain.size == (1920, 1080)
    bb = plain.getchannel("A").getbbox()
    assert bb is None or (bb[1] > 200 and bb[0] > 1300)          # 上と左は空（右下のめたんだけ）
    out = fr.compose(scene, "data", "mouth_open", "24種類の売り場では、買った人は3%", "🍓", 2)
    diff = np.abs(np.asarray(out, dtype=np.int16) - np.asarray(plain, dtype=np.int16))[940:1030, 100:1300].sum()
    assert diff > 0                                                  # 下に字幕
    cap = fr.caption("テスト")
    assert cap.getpixel((1, 38))[:3] != fr.acc                       # 左の棒は無い


def test_caption_chunks_are_short():
    cs = lab.caption_chunks(0.0, 6.0, "24種類のジャムを並べた売り場では、立ち止まった人のうち、買った人は、およそ3%でした。")
    assert all(len(t) <= 32 for _, _, t in cs)
    assert abs(sum(d for _, d, _ in cs) - 6.0) < 1e-6


def test_assemble_keeps_one_axis_and_trims_to_twenty_minutes():
    cfg = _psych()
    ol = {"title": "選択肢が多いと選べない？", "question": "選択肢が多いと、人は選べなくなる？", "recap": ["a", "b", "c"], "today_one": "x",
          "parts": [{"heading": f"h{i}", "icon": "🍓"} for i in range(4)]}
    parts = []
    for i in range(4):
        parts.append([
            {"kind": "chapter", "heading": f"検証{i}", "narration": [["あ" * 20, 0]]},
            {"kind": "point", "heading": "説明", "narration": [["い" * 900, 0]]},
            {"kind": "verdict", "result": ["本当", "半分本当", "ウソ", "本当"][i], "narration": [["う" * 20, 0]]},
        ])
    data = honpen.assemble(cfg, ol, parts)
    assert data["question"] == ol["question"]
    assert data["scenes"][0]["question"] == ol["question"] and data["scenes"][0]["icon"] == "🍓"
    assert data["scenes"][0]["narration"][0][0].startswith("今日の問いは、選択肢が多いと、人は選べなくなるのか")
    chs = [s for s in data["scenes"] if s["kind"] == "chapter"]
    assert [c["index"] for c in chs] == [0, 1, 2, 3]
    assert chs[2]["roadmap"][0]["result"] == "本当" and chs[2]["roadmap"][2]["result"] == ""   # 済んだ部だけ判定を出す
    trimmed = honpen.trim_to(data, 2500)
    assert honpen.narration_chars(trimmed) <= 2500
    kinds = [s["kind"] for s in trimmed["scenes"]]
    assert kinds.count("chapter") == 4 and kinds.count("verdict") == 4     # 軸（章の扉と判定）は残る
    assert cfg.get("honpen.max_chars") and int(cfg.get("honpen.max_chars")) <= 8600      # 速さ 1.22 で 1 分 ≒ 430 字 → 20 分未満


def test_psych_shorts_have_subtitles_like_economy_and_no_presenter():
    cfg = _psych()
    main = load_config()
    assert cfg.get("shorts.subtitles") is True
    assert cfg.get("shorts.subtitle_size") == main.get("shorts.subtitle_size")
    assert "45" in str(cfg.get("shorts.quiz_seconds")) or "47" in str(cfg.get("shorts.quiz_seconds"))
    th = quiz.theme(cfg)
    img = Image.new("RGB", (th.W, th.H), th.bg)
    out = quiz._short_caption(cfg, th, img, "先延ばしは、意志の弱さではありません")
    bb = Image.fromarray((np.abs(np.asarray(out, dtype=np.int16) - np.asarray(img, dtype=np.int16)).sum(axis=2) > 0).astype(np.uint8) * 255).getbbox()
    assert bb and bb[1] > 1250 and bb[3] <= 1560                    # 中身の下、Shorts の UI に隠れない所


def test_failed_shorts_are_kept_and_uploaded_next_time(tmp_path, monkeypatch):
    """1 日の上限などで上げられなかった Shorts は残しておき、次の実行で本編の公開より後の枠に上げる（二度は上げない）."""
    import json

    from ytecon.pipeline import Pipeline
    from ytecon.state import Store
    cfg = _psych()
    cfg.raw["pipeline"]["workdir"] = str(tmp_path / "w")
    cfg.root = tmp_path                                       # リポジトリ側の pending_shorts も tmp に
    store = Store(tmp_path / "s.sqlite3")
    pipe = Pipeline(cfg, store=store)
    store.create_video("p1", None, "本編")
    store.update_video("p1", status="uploaded", youtube_id="P", publish_at="2026-10-07T10:00:00+00:00")
    q = tmp_path / "q"
    q.mkdir()
    (q / "video.mp4").write_bytes(b"x")
    (q / "metadata.json").write_text(json.dumps({"title": "t #Shorts", "description": "d", "tags": [], "category_id": "27", "language": "ja"}))
    pipe._keep_pending_short("p1-short3", "p1", 2, q)
    calls = []

    def fake_publish(cfg_, store_, video, meta, **kw):
        calls.append((meta.title, kw["slot_index"], kw["after"]))
        return {"video_id": f"S{len(calls)}", "url": "u", "publish_at": "2026-10-07T22:00:00+00:00"}
    monkeypatch.setattr("ytecon.youtube.publish", fake_publish)
    assert pipe.upload_pending_shorts() == 1
    assert calls[0][1] == 2 and calls[0][2].isoformat().startswith("2026-10-07T10:00")
    assert store.get_video("p1-short3").youtube_id == "S1"
    assert not (cfg.workdir / "pending_shorts" / "p1-short3").exists()
    assert pipe.upload_pending_shorts() == 0                  # 二度は上げない
