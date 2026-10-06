"""ショーリールの音: BGM（声の間は下げる）＋効果音＋めたんの声 → audio/mix.wav."""
import json
import os as _os0
import subprocess

M = _os0.environ.get("MOTION_DIR", _os0.path.dirname(_os0.path.abspath(__file__)))  # 作業フォルダ（frames2/ audio/ out/）
A = _os0.path.join(_os0.path.dirname(_os0.path.abspath(__file__)), "..", "assets")
lines = json.load(open(f"{M}/audio/lines2.json"))
sfx = [
    ("RISER.mp3", 10.6 - 2.6, -9),
    ("WHOOSH.mp3", 10.3, -9), ("WHOOSH.mp3", 21.1, -8), ("WHOOSH.mp3", 33.1, -8), ("WHOOSH.mp3", 44.1, -8), ("WHOOSH.mp3", 52.7, -8),
    ("TRANSITION.mp3", 14.6, -11), ("TRANSITION.mp3", 16.8, -12),
    ("POP.mp3", 22.6, -12), ("POP.mp3", 25.2, -12), ("POP.mp3", 27.8, -12), ("POP.mp3", 35.8, -12), ("POP.mp3", 37.0, -10),
    ("RISER.mp3", 37.3 - 2.0, -12), ("IMPACT.mp3", 37.3, -8), ("IMPACT.mp3", 55.0, -5),
]
inputs = ["-i", f"{A}/bgm/Yair Cohen - Rise Within.mp3"]
for ln in lines:
    inputs += ["-i", ln["file"]]
for f, _, _ in sfx:
    inputs += ["-i", f"{A}/sfx/{f}"]
parts = []
nv = len(lines)
# 声をまとめた道（BGM を下げるきっかけにも使う）
for i, ln in enumerate(lines):
    ms = int(ln["t"] * 1000)
    parts.append(f"[{i + 1}:a]aresample=48000,aformat=channel_layouts=stereo,volume=2.2dB,adelay={ms}|{ms}[v{i}]")
parts.append("".join(f"[v{i}]" for i in range(nv)) + f"amix=inputs={nv}:normalize=0:duration=longest,apad=whole_dur=60[voice]")
parts.append("[voice]asplit=2[voice1][vkey]")
parts.append("[0:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:60,volume=-11dB,afade=t=in:d=1.2,afade=t=out:st=57.6:d=2.4[bgm0]")
parts.append("[bgm0][vkey]sidechaincompress=threshold=0.03:ratio=6:attack=40:release=500[bgm]")
for k, (_, t, db) in enumerate(sfx):
    ms = int(t * 1000)
    parts.append(f"[{nv + 1 + k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={db}dB,adelay={ms}|{ms}[s{k}]")
parts.append("[bgm][voice1]" + "".join(f"[s{k}]" for k in range(len(sfx))) + f"amix=inputs={2 + len(sfx)}:normalize=0:duration=first,"
             "alimiter=limit=0.89,atrim=0:60[out]")
cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts), "-map", "[out]", "-ar", "48000", f"{M}/audio/mix2.wav"]
subprocess.run(cmd, check=True)
print("ok")
