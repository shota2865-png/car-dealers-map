# ============================================================
#  参考チャンネルの実測ツール（Google Colab 用・貼るだけ）
#
#  使い方:
#    1. https://colab.research.google.com/ を開く
#    2. 「ノートブックを新規作成」
#    3. このファイルの中身を全部コピーして貼る
#    4. 左の ▶ を押す
#
#  何もインストールしなくて大丈夫です。最後に出る結果をコピーして
#  Claude に貼ってください。
# ============================================================

# ------------------------------------------------------------
# ここだけ変えれば別のチャンネルも測れます
# ------------------------------------------------------------
CHANNELS = [
    "https://www.youtube.com/@kangaesugiruashi",
    "https://www.youtube.com/@pivot00",
    "https://www.youtube.com/@world-zunda",
    "https://www.youtube.com/@weareyutoriman",
]

VIDEOS_PER_CHANNEL = 3      # 1チャンネルあたり何本測るか（増やすと時間がかかる）
DEEP = True                 # 映像も落としてカット頻度と配色を測る


# ============================================================
#  ここから下は触らなくて大丈夫です
# ============================================================
import json
import os
import re
import statistics
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

print("準備しています…（1〜2分）")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "yt-dlp", "pillow"],
               check=False)
print("準備できました\n")

WORK = Path("/content/measure") if Path("/content").exists() else Path("./measure")
WORK.mkdir(parents=True, exist_ok=True)


def run(cmd, timeout=1800):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ytdlp(*args, timeout=1800):
    return run([sys.executable, "-m", "yt_dlp", *args], timeout=timeout)


# ------------------------------------------------------------
# チャンネル -> 動画URL
# ------------------------------------------------------------
def list_videos(channel_url, limit):
    target = channel_url.rstrip("/")
    if not target.endswith("/videos"):
        target += "/videos"
    r = ytdlp("--flat-playlist", "--print", "%(url)s",
              "--playlist-end", str(limit), target, timeout=300)
    if r.returncode != 0:
        print(f"  取得できませんでした: {r.stderr.strip()[-200:]}")
        return []
    return [u.strip() for u in r.stdout.splitlines() if u.strip()]


# ------------------------------------------------------------
# 字幕（VTT）の解析
# ------------------------------------------------------------
_TIMING = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")


def _sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _strip_overlap(prev, cur):
    if not prev or not cur:
        return cur
    if cur in prev:
        return ""
    for size in range(min(len(prev), len(cur)), 2, -1):
        if prev.endswith(cur[:size]):
            return cur[size:].strip()
    return cur


def parse_vtt(text):
    """自動生成字幕は前行の末尾を繰り返すので、重複を落としてから返す."""
    cues, acc, lines, i = [], "", text.splitlines(), 0
    while i < len(lines):
        m = _TIMING.search(lines[i])
        if not m:
            i += 1
            continue
        g = m.groups()
        start, end = _sec(*g[:4]), _sec(*g[4:])
        i += 1
        body = []
        while i < len(lines) and lines[i].strip() and not _TIMING.search(lines[i]):
            body.append(lines[i])
            i += 1
        raw = re.sub(r"<[^>]+>", "", " ".join(body))
        raw = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw)).strip()
        if not raw:
            continue
        if acc:
            raw = _strip_overlap(acc[-300:], raw)
            if not raw:
                continue
        acc += raw
        cues.append((start, end, raw))
    return cues


# ------------------------------------------------------------
# カット検出（全フレームのスコアの段差で決める）
# ------------------------------------------------------------
_PTS = re.compile(r"pts_time:([0-9.]+)")


def detect_cuts(video, min_gap=0.5, noise_floor=0.01):
    r = run(["ffmpeg", "-hide_banner", "-i", str(video),
             "-filter:v", "select='gte(scene,0)',metadata=print:file=-",
             "-an", "-f", "null", "-"], timeout=900)
    blob = r.stdout + r.stderr
    pairs = re.findall(r"pts_time:([0-9.]+).*?lavfi\.scene_score=([0-9.]+)",
                       blob, re.S)
    scored = [(float(t), float(v)) for t, v in pairs if float(v) >= noise_floor]
    if not scored:
        return []
    ranked = sorted((v for _t, v in scored), reverse=True)
    head = ranked[:max(3, min(len(ranked), 60))]
    best, split = 1.0, len(head)
    for i in range(len(head) - 1):
        ratio = head[i] / max(head[i + 1], 1e-6)
        if ratio > best:
            best, split = ratio, i + 1
    threshold = head[split - 1] if best >= 1.8 else min(head)
    merged = []
    for t in sorted(t for t, v in scored if v >= threshold):
        if not merged or t - merged[-1] >= min_gap:
            merged.append(t)
    return merged


def palette_of(paths, colors=6):
    from PIL import Image
    counter = Counter()
    for p in paths:
        img = Image.open(p).convert("RGB").resize((160, 90))
        q = img.quantize(colors=colors)
        pal = q.getpalette() or []
        for count, idx in (q.convert("P").getcolors(1 << 16) or []):
            rgb = tuple(pal[idx * 3: idx * 3 + 3])
            if len(rgb) == 3:
                counter[tuple(v // 16 * 16 for v in rgb)] += count
    total = sum(counter.values()) or 1
    return [{"hex": "#%02X%02X%02X" % rgb, "share": round(c / total, 3)}
            for rgb, c in counter.most_common(colors)]


# ------------------------------------------------------------
# 1本ぶんの計測
# ------------------------------------------------------------
def measure_video(url, index):
    d = WORK / f"v{index}"
    d.mkdir(parents=True, exist_ok=True)
    cmd = ["--no-playlist", "--retries", "3", "--write-info-json",
           "--write-subs", "--write-auto-subs", "--sub-langs", "ja,ja-orig,ja.*",
           "--sub-format", "vtt/best", "--convert-subs", "vtt",
           "--write-thumbnail", "--convert-thumbnails", "jpg",
           "-o", str(d / "ref.%(ext)s")]
    cmd += (["-f", "worst[height>=360]/worst"] if DEEP else ["--skip-download"])
    r = ytdlp(*cmd, url)
    if r.returncode != 0:
        print(f"    取得失敗: {r.stderr.strip().splitlines()[-1][:120]}")
        return None

    infos = list(d.glob("*.info.json"))
    if not infos:
        return None
    info = json.loads(infos[0].read_text(encoding="utf-8"))
    duration = float(info.get("duration") or 0)
    out = {"title": info.get("title", "")[:40], "duration_min": round(duration / 60, 1),
           "chapters": len(info.get("chapters") or [])}

    vtts = sorted(d.glob("*.vtt"))
    if vtts:
        cues = parse_vtt(vtts[0].read_text(encoding="utf-8"))
        text = "".join(t for _s, _e, t in cues)
        chars = len(re.sub(r"\s", "", text))
        sents = [s for s in re.split(r"[。！？!?]", text) if s.strip()]
        out["chars_per_min"] = round(chars / (duration / 60)) if duration else 0
        out["avg_sentence_chars"] = (
            round(statistics.mean(len(re.sub(r"\s", "", s)) for s in sents))
            if sents else 0)
        out["sample"] = text[:120]
    else:
        print("    字幕なし（映像だけ測ります）")

    if DEEP:
        vids = [f for f in d.iterdir() if f.suffix in (".mp4", ".webm", ".mkv")]
        if vids and duration:
            cuts = detect_cuts(vids[0])
            shots, prev = [], 0.0
            for t in cuts:
                shots.append(t - prev)
                prev = t
            shots.append(duration - prev)
            shots = [s for s in shots if s > 0]
            out["cuts_per_min"] = round(len(cuts) / (duration / 60), 1)
            out["median_shot_sec"] = round(statistics.median(shots), 1) if shots else 0
            out["shots_over_6s_ratio"] = (
                round(sum(1 for s in shots if s > 6) / len(shots), 2) if shots else 0)
            frames = WORK / f"f{index}"
            frames.mkdir(exist_ok=True)
            run(["ffmpeg", "-y", "-hide_banner", "-i", str(vids[0]),
                 "-vf", "fps=1/10,scale=320:-1", str(frames / "f_%03d.png")],
                timeout=600)
            fs = sorted(frames.glob("*.png"))
            if fs:
                out["colors"] = palette_of(fs)
            vids[0].unlink(missing_ok=True)      # 容量を空ける
    return out


# ------------------------------------------------------------
# 実行
# ------------------------------------------------------------
results = {}
counter = 0
for ch in CHANNELS:
    name = ch.rstrip("/").split("/")[-1]
    print(f"\n=== {name} ===")
    urls = list_videos(ch, VIDEOS_PER_CHANNEL)
    if not urls:
        continue
    rows = []
    for u in urls:
        counter += 1
        print(f"  [{counter}] {u}")
        try:
            m = measure_video(u, counter)
        except Exception as e:
            print(f"    エラー: {e}")
            m = None
        if m:
            rows.append(m)
            print(f"      {m.get('duration_min')}分 / 話速{m.get('chars_per_min','-')} / "
                  f"カット{m.get('cuts_per_min','-')}回per分 / "
                  f"1ショット{m.get('median_shot_sec','-')}秒")
    if rows:
        results[name] = rows


# ------------------------------------------------------------
# まとめ
# ------------------------------------------------------------
def med(rows, key):
    vals = [r[key] for r in rows if r.get(key)]
    return round(statistics.median(vals), 1) if vals else None


print("\n\n" + "=" * 56)
print(" 結果（ここから下を全部コピーして Claude に貼ってください）")
print("=" * 56)

summary = {}
for name, rows in results.items():
    colors = Counter()
    for r in rows:
        for c in r.get("colors", []):
            colors[c["hex"]] += c["share"]
    summary[name] = {
        "本数": len(rows),
        "尺_分": med(rows, "duration_min"),
        "話速_文字per分": med(rows, "chars_per_min"),
        "1文の長さ_字": med(rows, "avg_sentence_chars"),
        "カット_回per分": med(rows, "cuts_per_min"),
        "1ショット_秒": med(rows, "median_shot_sec"),
        "6秒超の割合": med(rows, "shots_over_6s_ratio"),
        "チャプター数": med(rows, "chapters"),
        "支配色": [h for h, _ in colors.most_common(5)],
        "字幕サンプル": (rows[0].get("sample", "") or "")[:80],
    }

print(json.dumps(summary, ensure_ascii=False, indent=2))
print("=" * 56)
