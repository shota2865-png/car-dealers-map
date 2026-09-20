"""BGM の用意.

assets/bgm/ に置いた曲（Artlist など）だけを使う。気分名（ambient / curiosity /
tension / reflective）のファイルがあればブロックごとに切り替え、無ければ置いてある
曲を順繰りに割り当てる。曲が無ければ BGM なし。
合成のパッド音（generate_pad）はテスト用に残してあるが、本番では使わない。
"""

from __future__ import annotations

import logging
import math
import subprocess
import wave
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

RATE = 44100


def _tone(freq: float, n: int, rate: int, phase: float = 0.0):
    import numpy as np

    t = np.arange(n) / rate
    # 基音 + 弱い倍音で、丸い電子ピアノ風の音色にする
    return (np.sin(2 * math.pi * freq * t + phase)
            + 0.28 * np.sin(2 * math.pi * freq * 2 * t)
            + 0.10 * np.sin(2 * math.pi * freq * 3 * t))


def _env(n: int, rate: int, attack: float, release: float):
    import numpy as np

    e = np.ones(n)
    a = min(n, int(attack * rate))
    r = min(n, int(release * rate))
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


# 気分ごとの設計。A01〜A04（スタイルバイブル）に対応
#   chords: 周波数の組 / bar: 1コードの秒数 / octave: 音域(1で1オクターブ下)
#   pulse: 8分音符のパルスを乗せる（Curiosity）/ arp: 分散和音（Reflective）
#   lp: ローパスの強さ（小さいほどこもる）/ detune: 不協和のうなり（Tension）
_MOODS = {
    "ambient": dict(
        chords=[[261.63, 329.63, 392.00, 493.88],   # Cmaj7
                [220.00, 261.63, 329.63, 392.00],   # Am7
                [174.61, 220.00, 261.63, 329.63],   # Fmaj7
                [196.00, 246.94, 293.66, 349.23]],  # G7
        bar=8.0, octave=1, pulse=False, arp=False, lp=0.06, detune=0.0),
    "curiosity": dict(
        chords=[[220.00, 261.63, 329.63, 392.00],   # Am7
                [174.61, 220.00, 261.63, 329.63],   # Fmaj7
                [261.63, 329.63, 392.00, 493.88],   # Cmaj7
                [196.00, 246.94, 293.66, 392.00]],  # G
        bar=6.0, octave=1, pulse=True, arp=False, lp=0.09, detune=0.0),
    "tension": dict(
        chords=[[146.83, 174.61, 220.00, 261.63],   # Dm7
                [116.54, 146.83, 174.61, 220.00],   # Bbmaj7
                [196.00, 233.08, 293.66, 349.23],   # Gm7
                [110.00, 138.59, 164.81, 196.00]],  # A7
        bar=8.0, octave=1, pulse=False, arp=False, lp=0.045, detune=1.8),
    "reflective": dict(
        chords=[[261.63, 329.63, 392.00, 493.88],   # Cmaj7
                [329.63, 392.00, 493.88, 587.33],   # Em7
                [174.61, 220.00, 261.63, 329.63],   # Fmaj7
                [196.00, 246.94, 293.66, 349.23]],  # G7
        bar=8.0, octave=0, pulse=False, arp=True, lp=0.12, detune=0.0),
}


def generate_pad(out: Path, seconds: float = 64.0, seed: int = 3, mood: str = "ambient") -> Path:
    """ゆっくりしたコード進行のパッド。ニュース解説の後ろで邪魔をしない音.

    mood で雰囲気を変える（ambient / curiosity / tension / reflective）。
    """
    import numpy as np

    spec = _MOODS.get(mood, _MOODS["ambient"])
    rnd = np.random.default_rng(seed)
    chords = spec["chords"]
    bar = float(spec["bar"])
    total = int(seconds * RATE)
    mix = np.zeros(total)
    t = 0.0
    i = 0
    while t < seconds:
        chord = chords[i % len(chords)]
        n = int(min(bar + 1.5, seconds - t) * RATE)      # 1.5 秒重ねてつなぎ目を消す
        start = int(t * RATE)
        seg = np.zeros(n)
        if spec["arp"]:
            # 分散和音: 1音ずつ順に鳴らす（ピアノ風に短い減衰）
            step = bar / 8
            for k in range(8):
                f = chord[k % len(chord)] * (2 if k >= 4 else 1)
                s0 = int(k * step * RATE)
                m = min(int(step * 1.6 * RATE), n - s0)
                if m <= 0:
                    continue
                note = _tone(f, m, RATE, phase=0.0) * np.exp(-np.arange(m) / (RATE * 0.9))
                seg[s0:s0 + m] += note * 0.55
        else:
            for k, f in enumerate(chord):
                f_use = f / (2 ** spec["octave"]) if k == 0 else f
                seg += _tone(f_use, n, RATE, phase=float(rnd.uniform(0, 6.28))) * (0.9 if k == 0 else 0.6)
                if spec["detune"]:
                    # ほんの少しずらした音を重ねて、ゆっくりしたうなりを作る
                    seg += _tone(f_use + spec["detune"], n, RATE, phase=float(rnd.uniform(0, 6.28))) * 0.25
        if spec["pulse"]:
            # 8分音符のパルス（ルート音の短い粒）
            step = bar / 16
            root = chord[0]
            for k in range(16):
                s0 = int(k * step * RATE)
                m = min(int(0.18 * RATE), n - s0)
                if m <= 0:
                    continue
                vel = 0.5 if k % 4 == 0 else 0.3
                seg[s0:s0 + m] += _tone(root * 2, m, RATE) * np.exp(-np.arange(m) / (RATE * 0.05)) * vel
        seg *= _env(n, RATE, attack=1.6 if not spec["arp"] else 0.2, release=2.0)
        end = min(start + n, total)
        mix[start:end] += seg[: end - start]
        t += bar
        i += 1

    # ゆっくりした揺らぎ（トレモロ）とローパスで奥に引っ込める
    tt = np.arange(total) / RATE
    mix *= 1 + 0.06 * np.sin(2 * math.pi * 0.18 * tt)
    alpha = float(spec["lp"])
    lp = np.zeros_like(mix)
    acc = 0.0
    for idx, v in enumerate(mix):
        acc += alpha * (v - acc)
        lp[idx] = acc
    lp += rnd.normal(0, 0.0025, total)          # ごく薄いノイズの床

    # 全体を 0.5 秒フェードで囲み、-6 dBFS ピークに揃える（最終的な音量は render 側で決める）
    lp *= _env(total, RATE, attack=0.5, release=0.8)
    peak = float(np.max(np.abs(lp))) or 1.0
    lp = lp / peak * 0.5
    _write_wav(out, lp)
    return out


def _write_wav(out: Path, samples) -> Path:
    import numpy as np

    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return out


def _read_mono(path: Path):
    """wav/mp3 などを 44.1kHz mono の float 配列で読む（wav 以外は ffmpeg で変換）."""
    import numpy as np

    src = path
    if path.suffix.lower() != ".wav":
        from .render import ensure_ffmpeg
        tmp = path.with_suffix(".tmp44.wav")
        subprocess.run([ensure_ffmpeg(), "-y", "-loglevel", "error", "-i", str(path),
                        "-ac", "1", "-ar", str(RATE), str(tmp)], check=True)
        src = tmp
    with wave.open(str(src), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32767
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if rate != RATE:
        idx = np.linspace(0, len(data) - 1, int(len(data) * RATE / rate))
        data = np.interp(idx, np.arange(len(data)), data)
    if src is not path:
        src.unlink(missing_ok=True)
    return data


def available_files(cfg: Config) -> list[Path]:
    bgm_dir = cfg.root / "assets" / "bgm"
    out: list[Path] = []
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.aac", "*.flac"):
        out += sorted(bgm_dir.glob(ext))
    return [p for p in out if not p.name.startswith(".")]


def _mood_source(cfg: Config, mood: str, files: list[Path] | None = None) -> Path | None:
    """気分ごとの音源。assets/bgm/<mood>.* → 名前に気分を含むもの → 他の曲を順繰り.

    合成音は使わない（電子音は不評）。曲が1つも無ければ None（＝BGM なし）。
    """
    files = available_files(cfg) if files is None else files
    if not files:
        return None
    for p in files:
        if p.stem.lower() == mood:
            return p
    for p in files:
        if mood in p.stem.lower():
            return p
    # 気分名のファイルが無い: 置いてある曲を気分の順番で割り当てる（曲数が足りなければ循環）
    order = list((__import__("ytecon.bible", fromlist=["x"]).load(cfg).get("audio") or {}).get("bgm_moods") or
                 {"ambient": 0, "curiosity": 0, "tension": 0, "reflective": 0})
    idx = order.index(mood) if mood in order else 0
    return files[idx % len(files)]


def build_timeline(cfg: Config, script, track, outdir: Path) -> Path:
    """ブロック（hook / s0.. / closing）ごとに気分を切り替えた 1 本の BGM を作る.

    A01 Ambient（導入）→ A02 Curiosity（探究）→ A03 Tension（意外な事実）→
    A04 Reflective（結論）。切り替えはクロスフェードで、盛り上げではなく空気を変える。
    """
    import numpy as np

    from . import bible

    n_sections = len(script.sections)
    xf = bible.bgm_crossfade(cfg)
    # ブロックの並びを気分の連続区間にまとめる
    spans: list[tuple[str, float, float]] = []
    for block_id, _ in script.narration_blocks:
        try:
            s0, s1 = track.block_span(block_id)
        except Exception:
            continue
        if s1 <= s0:                      # 音声の無いブロック（尺を切った検証など）は飛ばす
            continue
        mood = bible.bgm_mood_for_beat(cfg, bible.beat_for_block(cfg, block_id, n_sections))
        if spans and spans[-1][0] == mood:
            spans[-1] = (mood, spans[-1][1], s1)
        else:
            spans.append((mood, s0, s1))
    total_sec = (track.duration if track.lines else 0.0) + 1.0
    if spans:
        spans[-1] = (spans[-1][0], spans[-1][1], total_sec)
        spans[0] = (spans[0][0], 0.0, spans[0][2])

    total = int(total_sec * RATE) + 1
    mix = np.zeros(total)
    cache: dict[str, object] = {}
    files = available_files(cfg)
    if not files:
        raise FileNotFoundError("assets/bgm に曲がありません")
    for mood, s0, s1 in spans:
        if mood not in cache:
            src_path = _mood_source(cfg, mood, files)
            cache[mood] = _read_mono(src_path)
        src = cache[mood]
        a = max(int((s0 - xf / 2) * RATE), 0)
        b = min(int((s1 + xf / 2) * RATE), total)
        n = b - a
        if n <= 0 or len(src) == 0:
            continue
        reps = int(np.ceil(n / len(src)))
        seg = np.tile(src, reps)[:n]
        seg = seg * _env(n, RATE, attack=xf, release=xf)
        mix[a:b] += seg
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = mix / peak * 0.5
    out = Path(outdir) / "bgm_timeline.wav"
    _write_wav(out, mix)
    log.info("BGM を %d 区間でつなぎました: %s", len(spans),
             " → ".join(f"{m}({s0:.0f}-{s1:.0f}s)" for m, s0, s1 in spans))
    return out


def resolve(cfg: Config, script=None, track=None, outdir: Path | None = None) -> Path | None:
    """使う BGM のパスを返す。無効・曲なしなら None（BGM なしで作る）.

    優先順位:
      1. render.bgm.file で指定された 1 曲（全編それ）
      2. script/track が渡され mood_timeline が有効なら、ブロックごとに気分を
         切り替えたタイムライン（assets/bgm/<mood>.* があればそれ、無ければ置いてある曲を順繰り）
      3. assets/bgm/ にある最初の曲
    合成音（パッド）は使わない。曲が無いときは警告して BGM なしにする。
    """
    if not cfg.get("render.bgm.enabled", False):
        return None
    name = str(cfg.get("render.bgm.file", "") or "").strip()
    if name:
        p = cfg.root / "assets" / "bgm" / name
        if p.exists():
            return p
        log.warning("BGM が見つかりません: %s", p)
    files = available_files(cfg)
    if not files:
        log.warning("assets/bgm に曲が無いので BGM なしで作ります（Artlist などの曲を置いてください。"
                    "docs/Artlistの使い方.md）")
        return None
    if script is not None and track is not None and outdir is not None \
            and cfg.get("render.bgm.mood_timeline", True):
        try:
            return build_timeline(cfg, script, track, Path(outdir))
        except Exception as exc:
            log.warning("気分つき BGM を作れなかったので 1 曲で通します: %s", exc)
    return files[0]
