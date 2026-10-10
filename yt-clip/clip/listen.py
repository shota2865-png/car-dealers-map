"""つないだ後の音を聞き取って、字幕の文字と「話し始めの時刻」を取る（faster-whisper・CPU）.

YouTube の自動字幕の時刻は場所によって最大 1 秒ほどずれる。切り抜いた後の音を聞き取り直すと、
つないだ後の時間軸でそのまま正確な時刻が取れる。1 本（約 50 秒）で 1 分前後かかる。
"""

from __future__ import annotations

import logging
import subprocess
import wave
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_model = None


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def words(src: Path, spans: list[tuple[float, float]], wav: Path, topic: str = "") -> list[dict[str, Any]]:
    """spans（src の中の秒の区間）をつないだ音を聞き取り、render.subtitle_chunks が読める形で返す。失敗したら空."""
    try:
        import numpy as np
        f = [f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]" for i, (a, b) in enumerate(spans)]
        f.append("".join(f"[a{i}]" for i in range(len(spans))) + f"concat=n={len(spans)}:v=0:a=1[o]")
        p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", ";".join(f),
                            "-map", "[o]", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr[-300:])
        with wave.open(str(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        prompt = "マックスむらいの配信の切り抜き。" + topic[:80]
        segs, _ = _load().transcribe(audio, language="ja", word_timestamps=True, vad_filter=True, temperature=0.0,
                                     beam_size=5, initial_prompt=prompt, condition_on_previous_text=False)
        out = []
        for s in segs:
            ws = [{"t": float(x.start), "e": float(x.end), "w": x.word} for x in (s.words or []) if x.word.strip()]
            if ws:
                ws[0]["brk"] = True          # 聞き取りの 1 文ごとに字幕も改める
                out.append({"start": ws[0]["t"], "end": ws[-1]["e"], "text": s.text, "words": ws})
        return out
    except Exception as e:  # noqa: BLE001  聞き取れなくても動画は作る
        log.info("聞き取り直せませんでした（自動字幕の時刻で出します）: %s", e)
        return []


def retime(yt_words: list[dict[str, Any]], heard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """文字は YouTube の自動字幕（固有名詞に強い）、時刻は聞き取り直したもの（話し始めに正確）を使う.

    聞き取りの 1 文ごとに、同じころ（前後 1.5 秒）の自動字幕の文字と突き合わせ、一致した文字に聞き取りの時刻を移す
    （同じ言い回しが 2 回出ても、離れた場所どうしを結ばない）。一致しなかった文字は、いちばん近い一致した文字と
    同じだけ時刻をずらす。一致が少なすぎるときは空を返す（呼び出し側は聞き取りの文字をそのまま使う）。
    """
    import difflib
    import re
    a: list[list[Any]] = []                       # YouTube 側の [文字, 単語の番号, もとの時刻, 合わせた始まり, 終わり]
    for i, w in enumerate(yt_words):
        for ch in re.sub(r"\[[^\]]*\]|\s+", "", w["w"]):
            a.append([ch, i, w["t"], None, None])
    if not a or not heard:
        return []
    hit = 0
    for ln in heard:
        b: list[tuple[str, float, float]] = []    # 聞き取りの 1 文の (文字, 始まり, 終わり)
        for w in ln["words"]:
            txt = re.sub(r"\s+", "", w["w"])
            d = (w["e"] - w["t"]) / max(1, len(txt))
            for k, ch in enumerate(txt):
                b.append((ch, w["t"] + k * d, w["t"] + (k + 1) * d))
        idx = [k for k, c in enumerate(a) if ln["start"] - 1.5 <= c[2] <= ln["end"] + 1.5 and c[3] is None]
        if not b or not idx:
            continue
        sm = difflib.SequenceMatcher(None, [a[k][0] for k in idx], [c for c, _, _ in b], autojunk=False)
        for m in sm.get_matching_blocks():
            if m.size < 2:
                continue                           # 1 文字だけの一致は偶然が多いので使わない
            for k in range(m.size):
                a[idx[m.a + k]][3], a[idx[m.a + k]][4] = b[m.b + k][1], b[m.b + k][2]
                hit += 1
    if hit < len(a) * 0.4:
        return []
    # 一致しなかった文字は、前後の一致した文字の間を等分する（端は 1 文字 0.12 秒で外へ伸ばす）。
    # 自動字幕は 1 行まるごとが同じ時刻のことがあるので、もとの時刻は当てにしない
    known = [k for k, c in enumerate(a) if c[3] is not None]
    for k, c in enumerate(a):
        if c[3] is not None:
            continue
        prev = max((j for j in known if j < k), default=None)
        nxt = min((j for j in known if j > k), default=None)
        if prev is None:
            c[3] = max(0.0, a[nxt][3] - 0.12 * (nxt - k))
        elif nxt is None:
            c[3] = a[prev][4] + 0.12 * (k - prev - 1)
        else:
            gap = max(0.0, a[nxt][3] - a[prev][4])
            c[3] = a[prev][4] + min(gap, 0.25 * (nxt - prev)) * (k - prev) / (nxt - prev)
        c[4] = c[3] + 0.12
    # 1 文字ずつ返す（字幕の行は呼び出し側が文節で組み直す）。単語の頭の「ここで改める」印は最初の文字に付ける
    out = []
    seen: set[int] = set()
    for ch, wi, _, t0, t1 in a:
        out.append({"t": float(t0), "e": float(t1), "w": ch, "brk": bool(yt_words[wi].get("brk")) and wi not in seen})
        seen.add(wi)
    for x, y in zip(out, out[1:]):
        if y["t"] < x["t"]:
            y["t"] = x["t"]
        y["e"] = max(y["e"], y["t"] + 0.05)
    return out
