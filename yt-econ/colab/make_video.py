# ============================================================
#  動画を1本つくる（Google Colab 用）
#
#  台本(script.json)から、声・図解・テロップ・字幕をつけた mp4 を
#  作ります。パソコンには何も入りません。
#
#  使い方:
#    1. ▶ を押す
#    2. ファイルを選ぶ画面が出たら ytecon_colab.zip を選ぶ
#    3. 待つ（VOICEVOX を使う場合は初回15〜20分・2回目以降8分ほど、使わない場合は5分ほど）
#    4. できた mp4 が自動でダウンロードされる
# ============================================================

USE_VOICEVOX = True   # ずんだもんの声を使う。False にすると軽い代替音声で速く終わる
USE_DRIVE = True      # Google Drive に道具を保存し、2回目以降を速くする（初回だけ許可を求められる）

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

# ------------------------------------------------------------
# Colab は閉じると全部消える。1GB の音声エンジンを毎回落とし直すのは
# 無駄なので、Google Drive に置いておく。初回 20 分 → 2回目以降 3 分。
# ------------------------------------------------------------
CACHE = None
if USE_DRIVE:
    try:
        from google.colab import drive
        drive.mount("/content/drive", force_remount=False)
        CACHE = Path("/content/drive/MyDrive/ytecon_cache")
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"Drive に保存します: {CACHE}")
    except Exception as e:
        print("Drive は使いません:", e)
        CACHE = None


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
    print("③ VOICEVOX（ずんだもんの声）を用意します")
    import shutil
    REPO = "VOICEVOX/voicevox_engine"
    local_vv = WORK / "vv"
    parts_cache = (CACHE / "voicevox_parts") if CACHE else None   # Drive には圧縮ファイルだけ置く

    def _fail(msg, r=None):
        print("   ✗", msg)
        if r is not None and (r.stderr or "").strip():
            print("     ", (r.stderr or "").strip().splitlines()[-1][:200])

    def _resolve_version():
        """最新版の番号を取る。GitHub API は Colab から使えないことが多いので、
        まず /releases/latest のリダイレクト先から読む."""
        r = sh(f"curl -sIL -o /dev/null -w '%{{url_effective}}' https://github.com/{REPO}/releases/latest")
        tag = r.stdout.strip().rstrip("/").rsplit("/", 1)[-1]
        if tag and tag[0].isdigit():
            return tag
        r = sh(f"curl -sL https://api.github.com/repos/{REPO}/releases/latest")
        import json as _json
        try:
            return _json.loads(r.stdout)["tag_name"]
        except Exception:
            return None

    def _download_parts(tag, dest):
        """linux-cpu-x64 の 7z 分割ファイルを 001 から順に落とす（無くなるまで）."""
        dest.mkdir(parents=True, exist_ok=True)
        base = f"https://github.com/{REPO}/releases/download/{tag}/voicevox_engine-linux-cpu-x64-{tag}.7z"
        got = []
        for i in range(1, 40):
            name = f"voicevox_engine-linux-cpu-x64-{tag}.7z.{i:03d}"
            out = dest / name
            if out.exists() and out.stat().st_size > 1_000_000:
                got.append(out)
                continue
            r = sh(f"curl -fL --retry 3 --retry-delay 3 -o '{out}' '{base}.{i:03d}'")
            if r.returncode != 0 or not out.exists() or out.stat().st_size < 1_000_000:
                if out.exists():
                    out.unlink()
                if i == 1:
                    _fail(f"ダウンロードできませんでした: {base}.001", r)
                break
            got.append(out)
            print(f"   取得 {name}（{out.stat().st_size / 1e6:.0f} MB）")
        return got

    def _extract(parts, dest):
        if shutil.which("7z") is None:
            sh("apt-get install -y -qq p7zip-full")
        exe = shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
        if exe is None:
            _fail("7z が入れられませんでした")
            return False
        dest.mkdir(parents=True, exist_ok=True)
        r = sh(f"'{exe}' x '{parts[0]}' -o'{dest}' -y")
        if r.returncode != 0:
            _fail("展開に失敗しました", r)
            return False
        return True

    runs = list(local_vv.glob("**/run")) if local_vv.exists() else []
    if not runs:
        parts = []
        if parts_cache and parts_cache.exists():
            cached = sorted(parts_cache.glob("*.7z.001"))
            if cached:
                print("   Drive に保存済みの圧縮ファイルを使います（速い）")
                parts = sorted(parts_cache.glob(cached[0].name[:-4] + ".*"))
        if not parts:
            tag = _resolve_version()
            if not tag:
                _fail("最新版の番号が分かりませんでした（GitHub に届いていない可能性）")
            else:
                print(f"   初回なので取得します（版 {tag}。1.5GB ほど、3〜10分）")
                parts = _download_parts(tag, WORK / "vv_parts")
                if parts and parts_cache:
                    print("   次回のために Drive へ保存しています…")
                    parts_cache.mkdir(parents=True, exist_ok=True)
                    for pth in parts:
                        shutil.copy2(pth, parts_cache / pth.name)
        if parts:
            print("   展開しています（1〜3分）…")
            if _extract(parts, local_vv):
                runs = list(local_vv.glob("**/run"))
                if not runs:
                    _fail("展開はできましたが起動ファイル(run)が見つかりません")
    if runs:
        sh(f"chmod +x '{runs[0]}'")
        vv_log = open(WORK / "voicevox.log", "w")
        subprocess.Popen([str(runs[0]), "--host", "127.0.0.1", "--port", "50021"],
                         stdout=vv_log, stderr=subprocess.STDOUT)
        print("   起動を待っています…")
    else:
        print("   VOICEVOX を用意できませんでした（代替の音声で続けます）")
        print("   ↑ うまくいかない場合は、この上に出た ✗ の行をそのまま貼ってください")

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
        log_p = WORK / "voicevox.log"
        if log_p.exists():
            print("   起動ログ(末尾):")
            for ln in log_p.read_text(errors="ignore").splitlines()[-8:]:
                print("     ", ln[:200])
else:
    print("③ VOICEVOX は使いません（代替の音声で進めます）")

# ------------------------------------------------------------
print("④ 動画を作ります")
from ytecon.config import load_config                    # noqa: E402
from ytecon.script import VideoScript                    # noqa: E402
from ytecon import character, render, scenes, subtitles, thumbnail, tts   # noqa: E402

cfg = load_config()
# VOICEVOX が立っていればそれを、駄目なら自動で代替音声に落ちる
cfg.raw.setdefault("tts", {})["provider"] = "auto"

script_path = next(Path(".").glob("**/script.json"))
script = VideoScript.load(script_path)
print(f"   台本: {script.topic_title}（{script.total_chars}字 / 用語{len(script.terms)}個）")

out = WORK / "out"
out.mkdir(exist_ok=True)

print("   声を作っています…")
track = tts.synthesize(cfg, script, out)
print(f"   {track.duration/60:.1f}分になりました")

print("   画面を作っています（8秒ごとに切り替え）…")
sc = scenes.plan_and_render(cfg, script, track, out / "images")
print(f"   {len(sc)}シーン / 平均 {track.duration/len(sc):.1f}秒")
reserve = character.reserved_width(cfg)         # 右下のキャラのぶん字幕を左に寄せる
subs = subtitles.build(cfg, track, out, script=script, reserve_right=reserve)
thumbnail.build(cfg, script, out / "thumbnail.jpg")

print("   映像にしています…（数分。キャラクターの口パクと BGM も合成）")
video = render.render(cfg, script, track, sc, subs["ass"], out)

print(f"\n完成しました: {video.name}"
      f"  {video.stat().st_size // 1024 // 1024}MB"
      f"  {render.probe_duration(video)/60:.1f}分")

# ------------------------------------------------------------
print("\n⑤ ダウンロードします")
files.download(str(video))
files.download(str(out / "thumbnail.jpg"))
files.download(str(out / "subtitles.srt"))
