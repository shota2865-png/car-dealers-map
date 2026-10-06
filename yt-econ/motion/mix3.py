"""サンプル v3 の音: BGM（声の間は下げる）＋効果音（切り替え・数字が出る瞬間）＋めたんの声 → audio3/mix3.wav."""
import json
import os
import subprocess

M = os.path.dirname(os.path.abspath(__file__))
A = "/home/user/car-dealers-map/yt-econ/assets"
END = 78.0
lines = json.load(open(f"{M}/audio3/lines3.json"))
cuts = [11, 23, 33, 44, 54, 64, 71.6]
sfx = [("WHOOSH.mp3", c - 0.3, -10) for c in cuts]
sfx += [
    ("TRANSITION.mp3", 5.1, -13),                       # 検証テーマの文字
    ("POP.mp3", 14.6, -13), ("POP.mp3", 18.6, -13), ("TRANSITION.mp3", 21.2, -13),   # 60% / 40% / 1.5倍
    ("POP.mp3", 25.6, -13), ("RISER.mp3", 27.4 - 2.0, -14), ("IMPACT.mp3", 29.6, -10),  # 3% / 30% / 約10倍
    ("POP.mp3", 35.9, -14), ("POP.mp3", 38.6, -12),      # 立ち止まる / 買う
    ("TRANSITION.mp3", 45.2, -14), ("RISER.mp3", 50.4 - 2.0, -14), ("POP.mp3", 50.4, -11),   # 点が降る / 平均≈0
    ("POP.mp3", 57.0, -14), ("POP.mp3", 58.1, -14), ("POP.mp3", 59.2, -14), ("POP.mp3", 60.3, -14),
    ("RISER.mp3", 67.8 - 2.0, -12), ("IMPACT.mp3", 67.8, -6),   # 判定スタンプ
    ("IMPACT.mp3", 72.6, -8),                            # ロゴ
]
inputs = ["-i", f"{A}/bgm/" + os.environ.get("BGM", "Yair Cohen - Rise Within.mp3")]
for ln in lines:
    inputs += ["-i", os.path.join(M, ln["file"])]
for f, _, _ in sfx:
    inputs += ["-i", f"{A}/sfx/{f}"]
parts = []
nv = len(lines)
for i, ln in enumerate(lines):
    ms = int(ln["t"] * 1000)
    parts.append(f"[{i + 1}:a]aresample=48000,aformat=channel_layouts=stereo,volume=2.2dB,adelay={ms}|{ms}[v{i}]")
parts.append("".join(f"[v{i}]" for i in range(nv)) + f"amix=inputs={nv}:normalize=0:duration=longest,apad=whole_dur={END}[voice]")
parts.append("[voice]asplit=2[voice1][vkey]")
parts.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,aloop=loop=1:size=2e9,atrim=0:{END},volume=-11dB,"
             f"afade=t=in:d=1.2,afade=t=out:st={END - 2.6}:d=2.6[bgm0]")
parts.append("[bgm0][vkey]sidechaincompress=threshold=0.03:ratio=6:attack=40:release=500[bgm]")
for k, (_, t, db) in enumerate(sfx):
    ms = int(t * 1000)
    parts.append(f"[{nv + 1 + k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={db}dB,adelay={ms}|{ms}[s{k}]")
parts.append("[bgm][voice1]" + "".join(f"[s{k}]" for k in range(len(sfx))) + f"amix=inputs={2 + len(sfx)}:normalize=0:duration=first,"
             f"alimiter=limit=0.89,atrim=0:{END}[out]")
cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts), "-map", "[out]", "-ar", "48000", f"{M}/audio3/mix3.wav"]
subprocess.run(cmd, check=True)
print("ok")
