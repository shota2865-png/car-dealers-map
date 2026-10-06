"""週 1 回の総集編（睡眠用・作業用）.

毎日の本編（Actions の成果物 <slug>/video.mp4 と <slug>/metadata.json）を 1 本につなげ、
章（チャプター）つきで予約投稿する。新しい台本は作らない（自分の動画のまとめなので、規約上も問題ない）。

  ytecon compile <成果物を落としたフォルダ> [--days 7] [--upload]

つなぎ方は、同じ設定で書き出した動画どうしなので、まず再エンコードなし（-c copy）でつなぐ。
うまくいかなければ（解像度・音声の形式が違う回がある等）、速い設定で作り直す。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Episode:
    video: Path
    title: str
    day: dt.date
    seconds: float
    thumbnail: Path | None = None

    @property
    def short_title(self) -> str:
        """章の名前: 引きの【】やチャンネル名を外した本題."""
        t = re.sub(r"【[^】]*】", "", self.title).strip()
        return t[:60]


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def probe_seconds(path: Path) -> float:
    """ffprobe が無い環境でも動くよう、ffmpeg -i の出力から長さを読む."""
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _slug_day(name: str) -> dt.date | None:
    m = re.match(r"(\d{8})-", name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def find_episodes(src: str | Path, days: int = 7, today: dt.date | None = None, min_seconds: float = 60) -> list[Episode]:
    """src の下から本編（<slug>/video.mp4 + <slug>/metadata.json、Shorts は除く）を集め、古い順に並べる."""
    today = today or dt.date.today()
    since = today - dt.timedelta(days=days)
    seen: dict[str, Episode] = {}
    for meta in sorted(Path(src).rglob("metadata.json")):
        d = meta.parent
        if "shorts" in d.parts:
            continue
        video = d / "video.mp4"
        day = _slug_day(d.name)
        if not video.exists() or day is None or not (since <= day <= today):
            continue
        try:
            title = str(json.loads(meta.read_text(encoding="utf-8")).get("title") or d.name)
        except Exception:
            title = d.name
        if d.name in seen:                     # 同じ回が 2 つの成果物に入っていても 1 回だけ
            continue
        thumb = d / "thumbnail.jpg"
        seen[d.name] = Episode(video=video, title=title, day=day, seconds=probe_seconds(video),
                               thumbnail=thumb if thumb.exists() else None)
    eps = sorted(seen.values(), key=lambda e: (e.day, str(e.video)))
    return [e for e in eps if e.seconds > min_seconds]


def _stamp(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters(eps: list[Episode]) -> list[str]:
    t, out = 0.0, []
    for e in eps:
        out.append(f"{_stamp(t)} {e.short_title}")
        t += e.seconds
    return out


def _keyword(title: str) -> str:
    """タイトルの頭の、検索される語（「｜」「？」「とは」の前）。【】の残りや句読点は落とし、10 字で切れるなら出さない."""
    t = re.sub(r"【[^】]*】", "", title)
    t = re.sub(r"^[^【]*】", "", t)              # 片方だけ残った「…】」（古いタイトルの崩れ）は手前ごと落とす
    t = re.sub(r"[【】#＃]", "", t).strip()
    for sep in ("｜", "|", "？", "?", "とは", "、", "。", "！", "!", " ", "　"):
        if sep in t:
            t = t.split(sep)[0]
    t = t.strip("・,，.。、 ")
    if t.startswith("なぜ"):
        t = t[2:]
    for sep in ("なのに", "なぜ", "でも"):
        if sep in t and t.index(sep) >= 2:
            t = t.split(sep)[0]
    if len(t) > 10:                               # まだ長ければ、最初の「は」「が」の前（例: 不安は夜に… → 不安）
        for p_ in ("は", "が"):
            i = t.find(p_)
            if 2 <= i <= 10:
                t = t[:i]
                break
    t = t.rstrip("はがのをにで")                   # 「スマホ代は」→「スマホ代」
    return t if 2 <= len(t) <= 10 else ""


def build_metadata(cfg: Config, eps: list[Episode]):
    from . import domain
    from .metadata import Metadata, _voice_credit, subscribe_line
    total = sum(e.seconds for e in eps)
    hours = total / 3600
    length = f"{hours:.1f}時間" if hours >= 1 else f"{int(total // 60)}分"
    name = str(cfg.get("channel.name", ""))
    kws = list(dict.fromkeys(k for k in (_keyword(e.title) for e in eps) if k))
    prefix = str(cfg.get("compilation.title_prefix", "【睡眠用・作業用】"))
    title = f"{prefix}{name} 1週間まとめ {length}"
    # 中身は語の途中で切らない。入るだけ（最大 4 語）並べる
    picked: list[str] = []
    for k in kws[:4]:
        if len(title) + len("｜" + "・".join(picked + [k])) > 100:
            break
        picked.append(k)
    if picked:
        title += "｜" + "・".join(picked)
    lines = [
        str(cfg.get("compilation.lead", f"この 1 週間に公開した {len(eps)} 本を、1 本にまとめました（約 {length}）。"
                                         "眠る前や作業中に、流しっぱなしでどうぞ。")),
        subscribe_line(cfg),
        "■ もくじ\n" + "\n".join(chapters(eps)),
        f"{domain.pitch(cfg)}毎日{(cfg.get('upload.publish_times_jst') or ['19:00'])[0]}に新しい動画を公開しています。",
        "■ 音声\n" + _voice_credit(cfg),
    ]
    credits = [str(cfg.get("render.bgm.credit", "") or "").strip()]
    if str(cfg.get("thumbnail.style", "")) == "panel":
        credits.append(str(cfg.get("thumbnail.credit", "") or "").strip())
    credits = [c for c in credits if c]
    if credits:
        lines.append("■ 素材\n" + "\n".join(credits))
    lines.append("■ ご注意\n" + domain.disclaimer(cfg).rstrip())
    tags = ["睡眠用", "作業用", "聞き流し", "総集編", name] + [_keyword(e.title) for e in eps]
    return Metadata(title=title[:100], description="\n\n".join(x for x in lines if x)[:5000],
                    tags=list(dict.fromkeys(t for t in tags if t))[:15],
                    category_id=str(cfg.get("upload.category_id", "27")), language=str(cfg.get("upload.language", "ja")))


def join(eps: list[Episode], out: Path) -> Path:
    """つなぐ。まず再エンコードなし、失敗したら作り直す."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.parent / "concat.txt"
    lst.write_text("".join(f"file '{e.video.resolve()}'\n" for e in eps), encoding="utf-8")
    r = subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    want = sum(e.seconds for e in eps)
    if r.returncode == 0 and abs(probe_seconds(out) - want) < max(5.0, want * 0.01):
        return out
    log.warning("そのままではつなげなかったので作り直します: %s", (r.stderr or "")[-300:])
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


def thumbnail(cfg: Config, eps: list[Episode], out: Path) -> Path | None:
    """夜空の背景に「睡眠用 1週間まとめ」、その週の回のサムネを並べ、眠そうなずんだもんと「約◯時間」."""
    import random
    from PIL import Image, ImageDraw
    from .thumbpanel import big_text, emoji_image, label, sticker, zunda
    W, H = 1280, 720
    g = Image.linear_gradient("L").resize((W, H))
    img = Image.composite(Image.new("RGB", (W, H), "#1B1446"), Image.new("RGB", (W, H), "#050A1F"), g).convert("RGBA")
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    for _ in range(140):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.choice([1, 1, 2, 2, 3])
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 230, rnd.randint(120, 255)))
    moon = emoji_image("🌙", 150)
    if moon is not None:
        img.alpha_composite(moon, (W - 190, 150))
    # その週の回のサムネ（最大 6 枚、2 段 × 3 列）
    thumbs = [e.thumbnail for e in eps if e.thumbnail][-6:]
    tw, th = 300, 169
    x0, y0 = 40, 190
    for i, t in enumerate(thumbs):
        try:
            im = Image.open(t).convert("RGB").resize((tw, th))
        except Exception:
            continue
        x, y = x0 + (i % 3) * (tw + 18), y0 + (i // 3) * (th + 18)
        d.rectangle([x - 5, y - 5, x + tw + 5, y + th + 5], fill="white")
        img.paste(im, (x, y))
    z = zunda(cfg, "眠", 330)
    if z is not None:
        img.alpha_composite(z, (W - z.width + 30, H - z.height + 60))
    head = big_text(cfg, [("睡眠用", "white"), ("1週間まとめ", "yellow")], 130, inner=8, outer=16, skew=0.0)
    k = min(1.0, (W - 60) / head.width)
    head = head.resize((int(head.width * k), int(head.height * k)))
    img.alpha_composite(head, ((W - head.width) // 2, 14))
    hours = sum(e.seconds for e in eps) / 3600
    tag = label(cfg, f"約{hours:.1f}時間" if hours >= 1 else f"約{int(hours * 60)}分", 72, fill="#E00000", fg="white")
    img.alpha_composite(tag, (40, H - tag.height - 30))
    n = label(cfg, f"{len(eps)}本まとめ", 56, fill="#FFE600", fg="black")
    img.alpha_composite(n, (60 + tag.width, H - n.height - 36))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=90)
    return out


def make(cfg: Config, src: str | Path, outdir: str | Path, days: int = 7, upload: bool = False,
         today: dt.date | None = None, min_seconds: float = 60) -> dict[str, Any]:
    eps = find_episodes(src, days=days, today=today, min_seconds=min_seconds)
    min_n = int(cfg.get("compilation.min_episodes", 3))
    if len(eps) < min_n:
        log.warning("総集編にできる本編が %d 本しかありません（%d 本以上で作る）", len(eps), min_n)
        return {"skipped": True, "episodes": len(eps)}
    outdir = Path(outdir)
    video = join(eps, outdir / "compilation.mp4")
    meta = build_metadata(cfg, eps)
    thumb = thumbnail(cfg, eps, outdir / "thumbnail.jpg")
    (outdir / "metadata.json").write_text(json.dumps(meta.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    res: dict[str, Any] = {"video": str(video), "title": meta.title, "episodes": len(eps),
                           "hours": round(sum(e.seconds for e in eps) / 3600, 2)}
    if upload:
        from . import youtube
        from .state import Store
        store = Store(outdir / "compilation.sqlite3")
        times = [str(t) for t in (cfg.get("compilation.publish_times_jst") or ["22:00"])]
        pub = youtube.publish(cfg, store, video, meta, thumbnail=thumb, srt=None, publish_times=times, playlist=False)
        title = str(cfg.get("compilation.playlist_title", "") or "")
        if title:
            pid = youtube.ensure_playlist(cfg, store, title)
            if pid:
                youtube.add_to_playlist(cfg, store, pid, pub["video_id"])
        res.update(pub)
    log.info("総集編: %d 本 / %.1f 時間 / %s", len(eps), res["hours"], meta.title)
    return res
