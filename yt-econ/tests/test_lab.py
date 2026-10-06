from PIL import Image

from ytecon import honpen, lab, llm, wide
from ytecon.assets import palette
from ytecon.config import load_config


def _psych():
    return load_config(channel="psych")


def test_psych_uses_lab_design():
    cfg = _psych()
    assert lab.enabled(cfg) and cfg.get("thumbnail.style") == "lab" and cfg.get("presenter.key") == "metan"
    assert palette(cfg)["bg"] == "#060A12"


def test_frame_puts_scene_in_monitor(tmp_path):
    cfg = _psych()
    fr = lab.Frame(cfg, palette(cfg), "選択肢が多いと選べない？")
    scene = Image.new("RGB", (1920, 1080), palette(cfg)["bg"])
    out = fr.compose(scene, "data")
    assert out.size == (1920, 1080)


def test_data_and_verdict_scenes_render():
    cfg = _psych()
    th = wide.theme_wide(cfg)
    for sc in (
        {"kind": "data", "heading": "買った人の割合", "source": "大学（2000年）",
         "bars": [{"label": "24種類", "value": 3, "unit": "%"}, {"label": "6種類", "value": 30, "unit": "%", "hl": True}], "note": "少ないほうが売れた"},
        {"kind": "verdict", "claim": "選択肢が多いと選べない", "result": "半分本当", "reason": "いつもではない"},
    ):
        s = wide.build_scene_wide(cfg, th, sc)
        assert s.last_step() >= 2
        img = s.render(s.last_step(), 99.0)
        assert img.size == (1920, 1080) and img.getbbox()


def test_lab_thumbnail(tmp_path):
    cfg = _psych()
    out = honpen.thumbnail(cfg, {"title": "t", "outline": {"thumb_claim": "怒りは6秒で消える？", "thumb_hl": "6秒"}}, tmp_path / "t.jpg")
    assert Image.open(out).size == (1280, 720)


def test_lab_prompts_ask_for_verification(monkeypatch):
    cfg = _psych()
    seen = []

    def fake(system, user, schema, **kw):
        seen.append((system, user))
        if "parts" in (schema.get("properties") or {}):
            return {"title": "t", "theme": "t", "parts": [{"heading": "h"}] * 4, "recap": ["a"], "today_one": "x"}
        return {"scenes": [{"kind": "chapter", "heading": "h", "narration": [["a", 0]]}]}
    monkeypatch.setattr(llm, "complete_json", fake)
    ol = honpen.outline(cfg, {"title": "t"})
    sc = honpen.write_part(cfg, ol, 0)
    assert "検証" in seen[0][0] and "verdict" in seen[1][0] and "data" in seen[1][0]
    assert sc[0]["label"] == "検証 01"


def test_mouth_track_follows_voice():
    import io
    import math
    import wave
    buf = io.BytesIO()
    sr = 24000
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(sr * 2):                       # 前半 1 秒は声、後半 1 秒は無音
            v = int(12000 * math.sin(i / sr * 2 * math.pi * 220)) if i < sr else 0
            frames += v.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    tr = lab.mouth_track(buf.getvalue(), fps=12)
    states = [st for _, _, st in tr]
    assert "mouth_open" in states and states[-1] in ("base", "blink")
    assert abs(sum(d for _, d, _ in tr) - 2.0) < 0.2


def test_frame_mouth_states_differ_when_presenter_exists():
    cfg = _psych()
    if lab.presenter(cfg, "通常", 520, 760) is None:          # 立ち絵が無い環境では確かめられない
        return
    fr = lab.Frame(cfg, palette(cfg), "t")
    scene = Image.new("RGB", (1920, 1080), palette(cfg)["bg"])
    a = fr.compose(scene, "data", "base")
    b = fr.compose(scene, "data", "mouth_open")
    assert list(a.crop((1430, 300, 1920, 1080)).getdata()) != list(b.crop((1430, 300, 1920, 1080)).getdata())
