"""BGM の用意.

assets/bgm/ に音源があればそれを使う。無ければ、リラックス系のパッド音を
その場で合成する（権利関係がゼロで、とりあえず鳴る）。合成音は控えめな
「仮の BGM」なので、本番はフリー音源に差し替えることを勧める
（入手先は docs/BGMの用意.md）。
"""

from __future__ import annotations

import logging
import math
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


def generate_pad(out: Path, seconds: float = 64.0, seed: int = 3) -> Path:
    """ゆっくりしたコード進行のパッド。ニュース解説の後ろで邪魔をしない音."""
    import numpy as np

    rnd = np.random.default_rng(seed)
    # C major で落ち着く進行（Imaj7 → vi7 → IVmaj7 → V7）を 8 秒ずつ
    chords = [
        [261.63, 329.63, 392.00, 493.88],   # Cmaj7
        [220.00, 261.63, 329.63, 392.00],   # Am7
        [174.61, 220.00, 261.63, 329.63],   # Fmaj7
        [196.00, 246.94, 293.66, 349.23],   # G7
    ]
    bar = 8.0
    total = int(seconds * RATE)
    mix = np.zeros(total)
    t = 0.0
    i = 0
    while t < seconds:
        chord = chords[i % len(chords)]
        n = int(min(bar + 1.5, seconds - t) * RATE)      # 1.5 秒重ねてつなぎ目を消す
        start = int(t * RATE)
        seg = np.zeros(n)
        for k, f in enumerate(chord):
            f_low = f / 2 if k == 0 else f
            seg += _tone(f_low, n, RATE, phase=float(rnd.uniform(0, 6.28))) * (0.9 if k == 0 else 0.6)
        seg *= _env(n, RATE, attack=1.6, release=2.0)
        end = min(start + n, total)
        mix[start:end] += seg[: end - start]
        t += bar
        i += 1

    # ゆっくりした揺らぎ（トレモロ）とローパスで奥に引っ込める
    tt = np.arange(total) / RATE
    mix *= 1 + 0.06 * np.sin(2 * math.pi * 0.18 * tt)
    alpha = 0.06
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
    pcm = (lp * 32767).astype("<i2")

    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return out


def resolve(cfg: Config) -> Path | None:
    """使う BGM のパスを返す。無効なら None."""
    if not cfg.get("render.bgm.enabled", False):
        return None
    name = str(cfg.get("render.bgm.file", "") or "").strip()
    if name:
        p = cfg.root / "assets" / "bgm" / name
        if p.exists():
            return p
        log.warning("BGM が見つかりません: %s（合成音に切り替えます）", p)
    # ディレクトリに何か置いてあればそれを使う
    bgm_dir = cfg.root / "assets" / "bgm"
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.ogg"):
        found = sorted(bgm_dir.glob(ext))
        if found:
            return found[0]
    generated = cfg.workdir / "bgm_pad.wav"
    if not generated.exists():
        log.info("BGM が無いので、リラックス系のパッド音を合成します（仮）")
        generate_pad(generated)
    return generated
