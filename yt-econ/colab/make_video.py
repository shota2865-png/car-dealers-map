# ============================================================
#  動画を1本つくる（Google Colab 用）
#
#  台本(script.json)から、声・図解・テロップ・字幕をつけた mp4 を
#  作ります。パソコンには何も入りません。
#
#  使い方:
#    1. ▶ を押す
#    2. ファイルを選ぶ画面が出たら ytecon_colab.zip を選ぶ
#    3. 待つ（VOICEVOX を使う場合は15〜25分、使わない場合は5分ほど）
#    4. できた mp4 が自動でダウンロードされる
# ============================================================

USE_VOICEVOX = True   # ずんだもんの声を使う。False にすると軽い代替音声で速く終わる

import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

WORK = Path("/content/ytecon")
if WORK.exists():
    import shutil
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str),
                          capture_output=True, text=True, **kw)


# ------------------------------------------------------------
print("① ファイルを受け取ります")
from google.colab import files  # noqa: E402

uploaded = files.upload()
zips = [n for n in uploaded if n.endswith(".zip")]
if not zips:
    raise SystemExit("ytecon_colab.zip を選んでください")
with zipfile.ZipFile(zips[0]) as z:
    z.extractall(WORK)
os.chdir(WORK)
sys.path.insert(0, str(WORK / "src"))
print("   展開しました")

# ------------------------------------------------------------
print("② 部品を入れます（2〜3分）")
sh([sys.executable, "-m", "pip", "install", "-q",
    "pyyaml", "pillow", "matplotlib", "requests", "anthropic", "gtts"])

# ------------------------------------------------------------
if USE_VOICEVOX:
    print("③ VOICEVOX（ずんだもんの声）を用意します（10〜15分）")
    sh("apt-get install -y -qq p7zip-full")
    r = sh("curl -sL https://api.github.com/repos/VOICEVOX/voicevox_engine/releases/latest"
           " | grep browser_download_url | grep linux-cpu-x64 | cut -d'\"' -f4")
    urls = sorted(u.strip() for u in r.stdout.splitlines() if u.strip())
    if urls:
        print(f"   {len(urls)} 個のファイルを取得します")
        for u in urls:
            sh(f"curl -fsSL -O '{u}'")
        first = sorted(Path(".").glob("*.7z.001"))
        if first:
            sh(f"7z x '{first[0]}' -o./vv -y")
        else:
            for z in Path(".").glob("*.zip"):
                sh(f"unzip -qo '{z}' -d ./vv")
    runs = list(Path("./vv").glob("**/run")) if Path("./vv").exists() else []
    if runs:
        sh(f"chmod +x '{runs[0]}'")
        subprocess.Popen([str(runs[0]), "--host", "127.0.0.1", "--port", "50021"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("   起動を待っています…")
    else:
        print("   VOICEVOX を用意できませんでした（代替の音声で続けます）")

    import requests
    for _ in range(60):
        try:
            requests.get("http://127.0.0.1:50021/version", timeout=2)
            print("   ずんだもんの声が使えます")
            break
        except Exception:
            time.sleep(5)
    else:
        print("   起動しませんでした → 代替の音声に切り替えます")
else:
    print("③ VOICEVOX は使いません（代替の音声で進めます）")

# ------------------------------------------------------------
print("④ 動画を作ります")
from ytecon.config import load_config          # noqa: E402
from ytecon.script import VideoScript          # noqa: E402
from ytecon import assets, render, subtitles, thumbnail, tts   # noqa: E402

cfg = load_config()
# VOICEVOX が立っていればそれを、駄目なら自動で代替音声に落ちる
cfg.raw.setdefault("tts", {})["provider"] = "auto"

script_path = next(Path(".").glob("**/script.json"))
script = VideoScript.load(script_path)
print(f"   台本: {script.topic_title}（{script.total_chars}字）")

out = WORK / "out"
out.mkdir(exist_ok=True)

print("   声を作っています…")
track = tts.synthesize(cfg, script, out)
print(f"   {track.duration/60:.1f}分になりました")

print("   画面を作っています…")
images = assets.build_all(cfg, script, out / "images")
subs = subtitles.build(cfg, track, out, script=script)
thumbnail.build(cfg, script, out / "thumbnail.jpg")

print("   映像にしています…（数分）")
video = render.render(cfg, script, track, images, subs["ass"], out)

print(f"\n完成しました: {video.name}"
      f"  {video.stat().st_size // 1024 // 1024}MB"
      f"  {render.probe_duration(video)/60:.1f}分")

# ------------------------------------------------------------
print("\n⑤ ダウンロードします")
files.download(str(video))
files.download(str(out / "thumbnail.jpg"))
files.download(str(out / "subtitles.srt"))
