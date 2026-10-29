"""実話ドキュメンタリー型（docu）: テロップの記法・字幕の折り返し・時刻の組み立て・1 コマの描画."""
from __future__ import annotations

import io
import json
import wave
from pathlib import Path

from ytecon import docu

REF = Path(__file__).resolve().parents[1] / "reference"


def _wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * int(24000 * seconds))
    return buf.getvalue()


def test_markup_runs_and_plain():
    assert docu.runs("AIは[[知りません]]") == [("AIは", "w"), ("知りません", "box")]
    assert docu.runs("[100枚]と{黄}") == [("100枚", "red"), ("と", "w"), ("黄", "lime")]
    assert docu.plain("封筒[100枚]に[[毎日]]") == "封筒100枚に毎日"


def test_wrap_keeps_numbers_whole():
    f = docu.font(64, "bold")
    rows = docu.wrap("100円単位なら、100日で50万5,000円", f, 600)
    assert all("5,0" not in r or "50万5,000円" in r for r in rows)
    assert "".join(rows) == "100円単位なら、100日で50万5,000円"


def test_say_text_reads_dollars_and_handles():
    s = docu.say_text("たった100日で、$5,050。@budgeting.money さん", {})
    assert "5050ドル" in s and "@" not in s


def test_plan_places_lines_in_order_and_sfx(tmp_path):
    spec = {"scenes": [
        {"type": "chapter", "index": 1, "total": 2, "title": "t", "lines": ["一"]},
        {"type": "statement", "text": "AIは[[知りません]]", "lines": ["二", "三"]},
    ]}
    scenes = docu.synth_lines(spec, tmp_path, lambda text: _wav(0.5))
    pl = docu.plan(scenes)
    starts = [ln.start for sc in pl.scenes for ln in sc.lines]
    assert starts == sorted(starts) and pl.duration > 1.5
    kinds = {name for _, name, _ in pl.sfx}
    assert {"WHOOSH", "IMPACT"} <= kinds


def test_every_scene_of_the_sample_renders(tmp_path):
    spec = json.loads((REF / "docu_sample_100envelope.json").read_text())
    scenes = docu.synth_lines(spec, tmp_path, lambda text: _wav(0.3))
    pl = docu.plan(scenes)
    for vertical in (False, True):
        r = docu.Renderer(*((540, 960) if vertical else (960, 540)))
        for sc in pl.scenes:
            im = r.frame(pl, (sc.start + sc.end) / 2)
            assert im.size == (r.W, r.H)
