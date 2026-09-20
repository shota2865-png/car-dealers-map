# ============================================================
#  動画を1本つくる（Mac 用）
#
#  「動画をつくる.command」から呼ばれます。直接動かすときは:
#      python3 make_video.py [台本.json]
#  台本を省くと、同じフォルダの script.json を使います。
#  VOICEVOX アプリが起動していればずんだもんの声、無ければ代替の声。
# ============================================================
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
import os
os.chdir(HERE)


def mac_open(*args: str) -> None:
    """Finder / アプリを開く（Mac 以外では何もしない）."""
    try:
        subprocess.run(["open", *args], capture_output=True)
    except FileNotFoundError:
        pass


def voicevox_ready(timeout_s: int = 0) -> bool:
    import requests
    deadline = time.time() + timeout_s
    while True:
        try:
            requests.get("http://127.0.0.1:50021/version", timeout=2)
            return True
        except Exception:
            if time.time() >= deadline:
                return False
            time.sleep(3)


print("① VOICEVOX（ずんだもんの声）を確認します")
if voicevox_ready():
    print("   起動しています。ずんだもんの声を使います")
else:
    print("   起動していないので、アプリを開いてみます…")
    mac_open("-a", "VOICEVOX")
    if voicevox_ready(timeout_s=90):
        print("   起動しました。ずんだもんの声を使います")
    else:
        print("   見つかりませんでした → 代替の声で続けます")
        print("   （ずんだもんにしたいときは VOICEVOX アプリを先に開いてから、もう一度実行）")

print("② 動画を作ります")
from ytecon.config import load_config                    # noqa: E402
from ytecon.script import VideoScript                    # noqa: E402
from ytecon import character, render, scenes, subtitles, thumbnail, tts   # noqa: E402

cfg = load_config()
cfg.raw.setdefault("tts", {})["provider"] = "auto"   # VOICEVOX が無ければ自動で代替へ

script_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "script.json"
if not script_path.exists():
    sys.exit(f"台本が見つかりません: {script_path}")
script = VideoScript.load(script_path)
print(f"   台本: {script.topic_title}（{script.total_chars}字 / 用語{len(script.terms)}個）")

out = HERE / "out" / time.strftime("%Y%m%d_%H%M")
out.mkdir(parents=True, exist_ok=True)

print("   声を作っています…")
track = tts.synthesize(cfg, script, out)
print(f"   {track.duration/60:.1f}分になりました")

print("   画面を作っています（8秒ごとに切り替え）…")
sc = scenes.plan_and_render(cfg, script, track, out / "images")
print(f"   {len(sc)}シーン / 平均 {track.duration/len(sc):.1f}秒")
reserve = character.reserved_width(cfg)
subs = subtitles.build(cfg, track, out, script=script, reserve_right=reserve)
thumbnail.build(cfg, script, out / "thumbnail.jpg")

print("   映像にしています…（数分。キャラクターの口パクと BGM も合成）")
video = render.render(cfg, script, track, sc, subs["ass"], out)

print(f"\n完成しました: {video}"
      f"\n  {video.stat().st_size // 1024 // 1024}MB / {render.probe_duration(video)/60:.1f}分"
      f"\n  サムネ: {out / 'thumbnail.jpg'}")
mac_open(str(out))   # Finder でフォルダを開く
