"""盛り上がった場面を探す.

Claude を使わずに、YouTube 側にあるデータだけで山を見つける:
  - 生配信のアーカイブ → チャットの勢い（「ｗ」「草」やスパチャは重めに数える）
  - 再生の多い通常動画 → 「よく見返された場所」（ヒートマップ）
  - どちらも無い動画   → 自動字幕の [笑い] の多いところ
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from . import ytdlp

log = logging.getLogger(__name__)

LAUGH = re.compile(r"[wｗ]{2,}|草|笑|ﾜﾛ|爆笑|！！|!!")


def ytdlp_info(url: str, workdir: Path, chat: bool) -> dict[str, Any]:
    """情報・自動字幕（json3）・チャットのリプレイを取る（動画本体は落とさない）."""
    workdir.mkdir(parents=True, exist_ok=True)
    langs = "ja,live_chat" if chat else "ja"
    cmd = [*ytdlp.base(), "--skip-download", "--write-info-json", "--write-auto-subs", "--write-subs",
           "--sub-langs", langs, "--sub-format", "json3/best", "-o", str(workdir / "v.%(ext)s"), url]
    p = subprocess.run(cmd, capture_output=True, text=True)
    info_path = workdir / "v.info.json"
    if not info_path.exists():
        raise RuntimeError(f"情報を取れませんでした: {url}\n{p.stderr[-800:]}")
    return json.loads(info_path.read_text(encoding="utf-8"))


# --- 字幕 ---------------------------------------------------------------

def caption_lines(workdir: Path) -> list[dict[str, Any]]:
    """自動字幕を「行（開始・終了・文字・単語ごとの時刻）」にする."""
    p = workdir / "v.ja.json3"
    if not p.exists():
        return []
    ev = json.loads(p.read_text(encoding="utf-8")).get("events", [])
    lines: list[dict[str, Any]] = []
    for e in ev:
        segs = e.get("segs")
        if not segs:
            continue
        t0 = e["tStartMs"] / 1000
        words = []
        for s in segs:
            w = s.get("utf8", "")
            if not w.strip():
                continue
            words.append({"t": t0 + s.get("tOffsetMs", 0) / 1000, "w": w.replace("\n", "")})
        if not words:
            continue
        text = "".join(w["w"] for w in words).strip()
        dur = e.get("dDurationMs", 2000) / 1000
        lines.append({"start": t0, "end": t0 + dur, "text": text, "words": words})
    # 自動字幕は行が重なるので、次の行の頭で切る
    for a, b in zip(lines, lines[1:]):
        a["end"] = min(a["end"], b["start"])
    return lines


# --- 山を探す -------------------------------------------------------------

def chat_scores(workdir: Path, duration: int) -> list[float] | None:
    p = workdir / "v.live_chat.json"
    if not p.exists():
        return None
    sc = [0.0] * (duration + 1)
    n = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        rep = d.get("replayChatItemAction", {})
        t = int(int(rep.get("videoOffsetTimeMsec", 0)) / 1000)
        if not 0 <= t <= duration:
            continue
        for a in rep.get("actions", []):
            it = a.get("addChatItemAction", {}).get("item", {})
            r = it.get("liveChatTextMessageRenderer")
            paid = it.get("liveChatPaidMessageRenderer")
            if not (r or paid):
                continue
            msg = "".join(x.get("text", "") for x in (r or paid).get("message", {}).get("runs", []))
            sc[t] += 1 + (1.5 if LAUGH.search(msg) else 0) + (3 if paid else 0)
            n += 1
    return sc if n >= 30 else None


def heatmap_scores(info: dict[str, Any], duration: int) -> list[float] | None:
    hm = info.get("heatmap") or []
    if len(hm) < 20:
        return None
    sc = [0.0] * (duration + 1)
    for h in hm:
        for t in range(int(h["start_time"]), min(duration, int(h["end_time"])) + 1):
            sc[t] = max(sc[t], float(h["value"]))
    return sc


def caption_scores(lines: list[dict[str, Any]], duration: int) -> list[float]:
    sc = [0.0] * (duration + 1)
    for ln in lines:
        t = min(duration, int(ln["start"]))
        sc[t] += 1 + 4 * ln["text"].count("[笑い]") + (1 if "？" in ln["text"] or "?" in ln["text"] else 0)
    return sc


def smooth(sc: list[float], win: int) -> list[float]:
    out, acc = [0.0] * len(sc), 0.0
    for i, v in enumerate(sc):
        acc += v
        if i >= win:
            acc -= sc[i - win]
        out[i] = acc
    return out


def peaks(sc: list[float], k: int, gap: int, skip_head: int, skip_tail: int) -> list[int]:
    s = smooth(sc, 20)
    n = len(s)
    allowed = [skip_head <= i <= n - 1 - skip_tail for i in range(n)]
    got: list[int] = []
    order = sorted(range(n), key=lambda i: -s[i])
    for i in order:
        if len(got) >= k or s[i] <= 0:
            break
        if not allowed[i] or any(abs(i - g) < gap for g in got):
            continue
        got.append(i)
    return got


def candidates(video: dict[str, Any], workdir: Path, cfg: dict[str, Any], want: int) -> list[dict[str, Any]]:
    c = cfg["clip"]
    info = ytdlp_info(video["url"], workdir, chat=True)
    if info.get("availability") not in (None, "public") or info.get("live_status") == "is_live":
        log.info("対象外（%s / %s）: %s", info.get("availability"), info.get("live_status"), video["title"])
        return []
    if video.get("live_only") and info.get("live_status") != "was_live":
        return []
    dur = int(info.get("duration") or 0)
    if dur < c["max_sec"] + 30:
        return []
    lines = caption_lines(workdir)
    if not lines:
        log.info("字幕が無いので飛ばす: %s", video["title"])
        return []
    sc, how = chat_scores(workdir, dur), "chat"
    if sc is None:
        sc, how = heatmap_scores(info, dur), "heatmap"
    if sc is None:
        sc, how = caption_scores(lines, dur), "captions"
    head = 180 if how == "chat" else 20      # 配信の冒頭の挨拶ラッシュ・終わりの「おつ」ラッシュを避ける
    tail = 150 if how == "chat" else 5
    lead = c["lead_sec"] if how == "chat" else 25
    tl = c["tail_sec"] if how == "chat" else 25
    out = []
    for p in peaks(sc, want * 2, c["gap_sec"], head, tail):
        start, end = max(0, p - lead), min(dur, p + tl)
        # 生配信は前後を広めに渡す（話の前置きとオチを拾って、ジャンプカットでつなぐため）
        before, after = (150, 45) if how == "chat" else (20, 20)
        near = [ln for ln in lines if start - before <= ln["start"] <= end + after]
        if len(near) < 5:
            continue
        out.append({"video": video, "info_title": info.get("title", video["title"]), "duration": dur,
                    "upload_date": info.get("upload_date") or "", "is_live": info.get("live_status") == "was_live",
                    "start": float(start), "end": float(end), "peak": p, "how": how,
                    "score": sum(sc[max(0, start):end]) / max(1, end - start), "lines": near})
    log.info("%s: 山 %d 個（%s）", video["title"][:40], len(out), how)
    return out
