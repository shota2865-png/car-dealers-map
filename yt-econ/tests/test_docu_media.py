"""docu の素材集め: Veo（Gemini API）の呼び方・引用の映像が取れないときの表紙・BGM のつなぎ."""
from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import numpy as np

from ytecon import docu, docu_media


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_veo_without_key_makes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert docu_media.veo("a cat", tmp_path / "x.mp4") is None


def test_veo_polls_and_downloads(tmp_path, monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        calls.append((req.get_method(), url))
        if url.endswith(":predictLongRunning"):
            body = json.loads(req.data)
            assert body["parameters"]["aspectRatio"] == "9:16" and body["instances"][0]["prompt"] == "envelopes"
            return _Resp(json.dumps({"name": "operations/abc"}).encode())
        if url.endswith("operations/abc"):
            return _Resp(json.dumps({"done": True, "response": {"generateVideoResponse": {
                "generatedSamples": [{"video": {"uri": "https://example.com/v.mp4"}}]}}}).encode())
        assert req.headers.get("X-goog-api-key") == "k"
        return _Resp(b"\0" * 2000)

    monkeypatch.setattr(docu_media.urllib.request, "urlopen", fake_urlopen)
    out = docu_media.veo("envelopes", tmp_path / "v.mp4", api_key="k", aspect="9:16")
    assert out and out.stat().st_size == 2000
    assert calls[0][0] == "POST" and "veo-3.1" in calls[0][1]


def test_prepare_falls_back_to_cover_image(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        raise RuntimeError("blocked")

    monkeypatch.setattr(docu_media.subprocess, "run", fake_run)
    monkeypatch.setattr(docu_media, "_oembed", lambda url: {"thumbnail_url": "https://example.com/t.jpg"})
    monkeypatch.setattr(docu_media, "_get", lambda url, timeout=30, headers=None: b"\xff" * 3000)
    spec = {"scenes": [{"type": "phone", "url": "https://www.tiktok.com/@a/video/1", "lines": []},
                       {"type": "carousel", "items": [{"url": "https://www.tiktok.com/@b/video/2"}], "lines": []},
                       {"type": "footage", "prompt": "x", "lines": []}]}
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    got = docu_media.prepare(spec, tmp_path)
    assert got["scenes"][0]["media"].endswith(".jpg") and got["scenes"][1]["items"][0]["media"].endswith(".jpg")
    assert "clip" not in got["scenes"][2] and "media" not in spec["scenes"][0]       # 元の台本は変えない


def test_loop_bgm_crossfades_to_length(tmp_path):
    sr = 8000
    src = tmp_path / "s.wav"
    x = (np.sin(np.arange(sr * 5) / sr * 2 * np.pi * 220) * 0.3 * 32767).astype(np.int16)
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    out = docu.loop_bgm("ffmpeg", src, 12.0, tmp_path / "o.wav", xfade=1.0, sr=sr)
    with wave.open(str(out)) as w:
        assert abs(w.getnframes() / w.getframerate() - 12.0) < 0.01 and w.getnchannels() == 2


def test_bars3d_and_footage_render_without_media():
    spec = {"scenes": [
        {"type": "bars3d", "values": [1, 3], "labels": ["a", "b"], "tops": ["1", "3"], "hl": [1], "lines": ["x"]},
        {"type": "footage", "telop": "[映像]なし", "lines": ["y"]},
    ]}
    scenes = [docu.Scene(spec=s, lines=[docu.Line(text="x", say="x", start=0.3 + i * 2, end=1.5 + i * 2)]) for i, s in enumerate(spec["scenes"])]
    for i, sc in enumerate(scenes):
        sc.start, sc.end = i * 2.0, i * 2.0 + 2.0
    pl = docu.Plan(scenes=scenes, duration=4.0)
    r = docu.Renderer(960, 540, fps=30)
    for t in (1.0, 3.0):
        assert r.frame(pl, t).size == (960, 540)
    assert Path(docu._ROOT / "assets" / "space" / "loop_a.mp4").exists()
