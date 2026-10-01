from ytecon import geo


def test_wrap_sub_keeps_words_together():
    s = geo._wrap_sub("このように、本体から離れた場所にある土地を、飛び地と呼びます。")
    assert s.count(r"\N") == 1
    assert "土地\\Nを" not in s


def test_credit_follows_scene_kind():
    assert "国土地理院" in geo.credit_of({"kind": "map", "base": "satellite", "tiles": "gsi"})
    assert "EOX" in geo.credit_of({"kind": "map", "base": "satellite", "tiles": "eox"})
    assert geo.credit_of({"kind": "map"}) == ""
    assert "ぱくたそ" in geo.credit_of({"kind": "photo"})


def test_tile_xy_origin():
    x, y = geo._tile_xy(0.0, 0.0, 1)
    assert abs(x - 1) < 1e-9 and abs(y - 1) < 1e-9


def test_synth_falls_back_to_voicevox_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    seen = {}

    def fake(text, speaker, speed, url):
        seen["speaker"] = speaker
        return b"wav"

    monkeypatch.setattr(geo, "synth_voicevox", fake)

    class Cfg:
        def get(self, key, default=None):
            return default

    assert geo.synth(Cfg(), {"tts": "gemini", "voice": "Charon", "voicevox_voice": 30}, "こんにちは") == b"wav"
    assert seen["speaker"] == 30
