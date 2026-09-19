"""参照動画から文体を学ぶ.

「こういう感じにして」を言葉で伝えるのは難しい。なので参照動画の
**字幕と構成を実測して数値にする**。手で書いたトーン指示より、
実物から測った値のほうが確実に寄る。

やること:
  1. yt-dlp で字幕・メタデータだけ取る（動画本体はダウンロードしない。速い）
  2. 話速・1文の長さ・セクション長・チャプター数を機械的に測る
  3. 文字起こしを Claude に読ませ、語り口の型を言語化させる
  4. config/style.yaml に書き出す

以降 `ytecon run` は、このプロファイルに寄せて台本を書く。
実測した話速は chars_per_minute に反映されるので、尺の精度も上がる。

    python -m ytecon learn https://youtu.be/xxxx https://youtu.be/yyyy
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import statistics
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import llm
from .config import Config

log = logging.getLogger(__name__)


class LearnError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# 字幕の取得
# ----------------------------------------------------------------------
@dataclass
class Reference:
    url: str
    title: str = ""
    channel: str = ""
    duration: float = 0.0            # 秒
    chapters: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    transcript: str = ""
    cues: list[tuple[float, float, str]] = field(default_factory=list)
    visual: Any = None              # analyze.VisualProfile（deep のときだけ）
    video_path: Path | None = None
    thumbnail_path: Path | None = None


def ensure_ytdlp() -> str:
    exe = shutil.which("yt-dlp")
    if exe:
        return exe
    raise LearnError(
        "yt-dlp が必要です（参照動画の字幕を取るために使います）。\n"
        "  pip install yt-dlp\n"
        "動画本体はダウンロードしません。字幕とメタデータだけ取得します。"
    )


def expand_channels(urls: list[str], per_channel: int = 5) -> list[str]:
    """チャンネルURLが混ざっていたら、最新動画のURLに展開する.

    @handle / /channel/ / /c/ / /videos を渡せる。個別動画URLはそのまま通す。
    """
    exe = ensure_ytdlp()
    out: list[str] = []
    for url in urls:
        if not _is_channel(url):
            out.append(url)
            continue
        target = url.rstrip("/")
        if not target.endswith("/videos"):
            target += "/videos"
        proc = subprocess.run(
            [exe, "--flat-playlist", "--print", "%(url)s",
             "--playlist-end", str(per_channel), target],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            log.warning("チャンネルを展開できませんでした %s: %s",
                        url, proc.stderr.strip()[-200:])
            continue
        found = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        log.info("%s から %d本", url, len(found))
        out.extend(found)
    return out


def _is_channel(url: str) -> bool:
    return any(token in url for token in ("/@", "/channel/", "/c/", "/user/"))


def fetch(url: str, lang: str = "ja", deep: bool = False,
          workdir: Path | None = None) -> Reference:
    """字幕とメタデータだけ取得する（動画本体は落とさない）."""
    exe = ensure_ytdlp()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        cmd = [
            exe,
            "--write-info-json",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", f"{lang},{lang}-orig,{lang}.*",
            "--sub-format", "vtt/best",
            "--convert-subs", "vtt",
            "--write-thumbnail", "--convert-thumbnails", "jpg",
            "-o", str(out / "ref.%(ext)s"),
        ]
        if deep:
            # 解析にしか使わないので最低画質で十分。帯域と時間を節約する
            cmd += ["-f", "worstvideo[height>=360]+worstaudio/worst"]
        else:
            cmd += ["--skip-download"]
        cmd.append(url)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-6:])
            raise LearnError(f"字幕の取得に失敗しました ({url})\n{tail}")

        info_files = list(out.glob("*.info.json"))
        if not info_files:
            raise LearnError(f"メタデータを取得できませんでした: {url}")
        info = json.loads(info_files[0].read_text(encoding="utf-8"))

        ref = Reference(
            url=url,
            title=info.get("title", ""),
            channel=info.get("uploader", "") or info.get("channel", ""),
            duration=float(info.get("duration") or 0),
            chapters=info.get("chapters") or [],
            description=info.get("description", "") or "",
            tags=info.get("tags") or [],
        )

        vtts = sorted(out.glob("*.vtt"))
        if not vtts:
            raise LearnError(
                f"字幕が見つかりませんでした: {url}\n"
                "自動生成字幕もオフの動画は分析できません。別の動画を指定してください。"
            )
        ref.cues = parse_vtt(vtts[0].read_text(encoding="utf-8"))
        ref.transcript = "".join(text for _s, _e, text in ref.cues)

        thumbs = sorted(out.glob("*.jpg"))
        if thumbs and workdir:
            workdir.mkdir(parents=True, exist_ok=True)
            dest = workdir / f"thumb_{abs(hash(url)) % 100000}.jpg"
            shutil.copy2(thumbs[0], dest)
            ref.thumbnail_path = dest

        if deep and workdir:
            from . import analyze

            videos = [f for f in out.iterdir()
                      if f.suffix in (".mp4", ".webm", ".mkv")]
            if videos:
                ref.visual = analyze.analyze_video(
                    videos[0], ref.duration, workdir / "work",
                    thumbnail=ref.thumbnail_path,
                )
            else:
                log.warning("映像を取得できなかったので見た目の解析をスキップ: %s", url)
        elif ref.thumbnail_path:
            from . import analyze

            ref.visual = analyze.VisualProfile(
                thumbnail=analyze.analyze_thumbnail(ref.thumbnail_path))
        return ref


# ----------------------------------------------------------------------
# VTT の解析（ここは純粋関数なのでテストできる）
# ----------------------------------------------------------------------
_TIMING = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    """VTT を (開始秒, 終了秒, 本文) の列にする.

    YouTube の自動生成字幕は同じ語を次のキューに持ち越して重複させるので、
    直前のキューの末尾と重なる部分を落としてから返す。
    """
    cues: list[tuple[float, float, str]] = []
    accumulated = ""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _TIMING.search(lines[i])
        if not match:
            i += 1
            continue
        g = match.groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip() and not _TIMING.search(lines[i]):
            body.append(lines[i])
            i += 1
        raw = " ".join(body)
        raw = re.sub(r"<[^>]+>", "", raw)              # <c> や <00:00:01.000> を除去
        raw = unicodedata.normalize("NFKC", raw).strip()
        raw = re.sub(r"\s+", " ", raw)
        if not raw:
            continue
        if accumulated:
            # 直前のキューだけでなく、積み上げた本文の末尾と突き合わせる。
            # 繰り返しが2つ前のキューに跨ることがあり、直前だけ見ると取りこぼす
            raw = _strip_overlap(accumulated[-_OVERLAP_LOOKBACK:], raw)
            if not raw:
                continue
        accumulated += raw
        cues.append((start, end, raw))
    return cues


# 重複を探しに行く範囲。長すぎると偶然の一致を拾うので、数キュー分に留める
_OVERLAP_LOOKBACK = 300


def _strip_overlap(previous: str, current: str) -> str:
    """自動字幕のロールアップ重複を落とす.

    YouTube の自動生成字幕は、直前の行の末尾を次の行が繰り返す。
    これを残したまま文字数を数えると、話速を3割ほど多く見積もってしまう。
    """
    if not previous or not current:
        return current
    if current in previous:
        return ""
    limit = min(len(previous), len(current))
    # 3文字まで見る。2文字以下まで下げると助詞の偶然一致で本文を削ってしまう
    for size in range(limit, 2, -1):
        if previous.endswith(current[:size]):
            return current[size:].strip()
    return current


# ----------------------------------------------------------------------
# 実測
# ----------------------------------------------------------------------
_SENT_END = re.compile(r"[。！？!?]")


def measure(ref: Reference) -> dict[str, Any]:
    """字幕から、真似できる数値を取り出す."""
    body = re.sub(r"\s", "", ref.transcript)
    chars = len(body)
    minutes = (ref.duration or (ref.cues[-1][1] if ref.cues else 0)) / 60

    sentences = [s for s in _SENT_END.split(ref.transcript) if s.strip()]
    sent_lengths = [len(re.sub(r"\s", "", s)) for s in sentences if s.strip()]

    chapters = ref.chapters or []
    chapter_spans = [
        float(c.get("end_time", 0)) - float(c.get("start_time", 0))
        for c in chapters
        if c.get("end_time") is not None
    ]

    hook_seconds = 0.0
    if chapters:
        hook_seconds = float(chapters[0].get("end_time", 0) or 0)

    return {
        "duration_minutes": round(minutes, 1),
        "total_chars": chars,
        "chars_per_minute": round(chars / minutes) if minutes else 0,
        "sentences": len(sent_lengths),
        "avg_sentence_chars": round(statistics.mean(sent_lengths)) if sent_lengths else 0,
        "max_sentence_chars": max(sent_lengths) if sent_lengths else 0,
        "chapters": len(chapters),
        "median_chapter_seconds": (
            round(statistics.median(chapter_spans)) if chapter_spans else 0
        ),
        "hook_seconds": round(hook_seconds),
        "chapter_titles": [c.get("title", "") for c in chapters][:12],
        "description_chars": len(ref.description),
        "tags": len(ref.tags),
    }


def aggregate(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """複数本の実測をまとめる（中央値。1本の外れ値に引っ張られないように）."""
    def med(key: str) -> float:
        values = [m[key] for m in measurements if m.get(key)]
        return round(statistics.median(values), 1) if values else 0

    return {
        "videos": len(measurements),
        "duration_minutes": med("duration_minutes"),
        "chars_per_minute": int(med("chars_per_minute")),
        "avg_sentence_chars": int(med("avg_sentence_chars")),
        "chapters": int(med("chapters")),
        "median_chapter_seconds": int(med("median_chapter_seconds")),
        "hook_seconds": int(med("hook_seconds")),
    }


# ----------------------------------------------------------------------
# 語り口の言語化
# ----------------------------------------------------------------------
_STYLE_SCHEMA = llm.obj(
    {
        "summary": llm.STR,
        "person": llm.STR,
        "stance": llm.STR,
        "opening_pattern": llm.STR,
        "transition_pattern": llm.STR,
        "closing_pattern": llm.STR,
        "jargon_policy": llm.STR,
        "number_policy": llm.STR,
        "sentence_rhythm": llm.STR,
        "humor": llm.STR,
        "signature_phrases": llm.arr(llm.STR),
        "avoid": llm.arr(llm.STR),
        "section_shape": llm.STR,
        "title_patterns": llm.arr(llm.STR),
        "what_makes_it_work": llm.STR,
        "what_not_to_copy": llm.STR,
    }
)

_STYLE_SYSTEM = """あなたは動画台本の文体分析者です。
参照動画の文字起こしとメタデータを読み、**別の人が同じ語り口で書けるように**
型を言語化してください。

守ること:
- 感想を書かない。再現できる指示に落とす。
  悪い例:「親しみやすい話し方」
  良い例:「1文を40字前後で切り、2文に1回『〜なんですね』で受け止める」
- 実際に出てくる言い回しを signature_phrases に原文のまま拾う（5個まで）
- opening / transition / closing は、**テンプレートとして使える形**で書く
  例:「〈視聴者の日常の違和感〉から入り、〈結論の予告〉を1文で置く」
- avoid には、この書き手が**使っていない**ものを挙げる（煽り語、断定、専門用語の
  投げっぱなしなど）。真似るときに足さないためのリスト
- what_not_to_copy には、この動画固有で真似ると事故になる要素を書く
  （個人の経歴に依存する語り、特定回だけの企画性、他者への言及など）

複数本ある場合は、**共通している型**を抽出してください。1本だけの特徴は
その旨を添えてください。
"""


def profile(cfg: Config, refs: list[Reference],
            measurements: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = []
    for ref, m in zip(refs, measurements):
        # 文字起こしは長いので冒頭と中盤を抜く（全部入れても型は変わらない）
        head = ref.transcript[:2500]
        mid = ref.transcript[len(ref.transcript) // 2:][:1500]
        blocks.append(
            f"""## {ref.title}
チャンネル: {ref.channel}
尺: {m['duration_minutes']}分 / 話速 {m['chars_per_minute']}文字per分 /
1文平均 {m['avg_sentence_chars']}字 / チャプター {m['chapters']}個
チャプター名: {' / '.join(m['chapter_titles']) or '(なし)'}

### 文字起こし（冒頭）
{head}

### 文字起こし（中盤）
{mid}

### 概要欄（冒頭500字）
{ref.description[:500]}
"""
        )

    visual_note = ""
    withvis = [r for r in refs if r.visual and r.visual.cuts]
    if withvis:
        lines = ["", "## 映像の実測値（参考）"]
        for r in withvis:
            c = r.visual.cuts
            lines.append(
                f"- {r.title[:24]}: カット {c.get('cuts_per_minute')}回/分 / "
                f"中央ショット {c.get('median_shot_seconds')}秒 / "
                f"6秒超のショット比率 {c.get('shots_over_6s_ratio')}"
            )
        lines.append("この数値から、話の区切りと画の切り替えの関係も推測してください。")
        visual_note = "\n".join(lines)

    user = (
        "次の参照動画を分析し、同じ語り口で書くための型を出してください。\n\n"
        + "\n\n".join(blocks) + visual_note
    )
    return llm.complete_json(
        _STYLE_SYSTEM, user, _STYLE_SCHEMA,
        model=cfg.get("script.model", llm.DEFAULT_MODEL), effort="high",
    )


# ----------------------------------------------------------------------
def learn(cfg: Config, urls: list[str], out: Path | None = None,
          lang: str = "ja", deep: bool = False, per_channel: int = 5) -> Path:
    """参照動画（またはチャンネル）を分析して config/style.yaml を書き出す."""
    from . import analyze

    urls = expand_channels(urls, per_channel=per_channel)
    if not urls:
        raise LearnError("分析対象の動画がありません")

    workdir = cfg.workdir / "reference"
    workdir.mkdir(parents=True, exist_ok=True)

    refs, measurements, visuals = [], [], []
    for url in urls:
        log.info("取得中: %s", url)
        try:
            ref = fetch(url, lang=lang, deep=deep, workdir=workdir)
        except LearnError as exc:
            log.warning("スキップ: %s", exc)
            continue
        m = measure(ref)
        log.info("  「%s」%.1f分 / %d文字per分 / 1文%d字 / チャプター%d個",
                 ref.title[:30], m["duration_minutes"], m["chars_per_minute"],
                 m["avg_sentence_chars"], m["chapters"])
        if ref.visual and ref.visual.cuts:
            log.info("    カット %.1f回/分 / 中央ショット %.1f秒",
                     ref.visual.cuts.get("cuts_per_minute", 0),
                     ref.visual.cuts.get("median_shot_seconds", 0))
        refs.append(ref)
        measurements.append(m)
        if ref.visual:
            visuals.append(ref.visual)

    if not refs:
        raise LearnError("分析できる動画がありませんでした")

    stats = aggregate(measurements)
    visual_stats = analyze.aggregate_visual(visuals) if visuals else {}

    log.info("文体を言語化しています…")
    style = profile(cfg, refs, measurements)

    doc = {
        "_note": (
            "ytecon learn が参照動画から自動生成したファイルです。"
            "手で編集しても構いませんが、learn を再実行すると上書きされます。"
        ),
        "sources": [{"url": r.url, "title": r.title, "channel": r.channel}
                    for r in refs],
        "measured": stats,
        "visual": visual_stats,
        "per_video": measurements,
        "voice": style,
    }

    out = out or (cfg.root / "config" / "style.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    log.info("書き出しました: %s", out)
    return out


# ----------------------------------------------------------------------
def load_style(cfg: Config) -> dict[str, Any] | None:
    path = cfg.root / "config" / "style.yaml"
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or None
    except Exception as exc:
        log.warning("style.yaml を読めませんでした: %s", exc)
        return None


def render_for_prompt(style: dict[str, Any]) -> str:
    """style.yaml を台本生成プロンプトに差し込める文章にする."""
    voice = style.get("voice", {}) or {}
    measured = style.get("measured", {}) or {}
    sources = style.get("sources", []) or []

    rows = ["# 参照動画から抽出した語り口（これに寄せてください）", ""]
    if sources:
        rows.append("参照: " + "、".join(
            f"「{s.get('title','')[:30]}」" for s in sources[:3]))
        rows.append("")

    for label, key in [
        ("全体", "summary"), ("人称・文体", "person"), ("立ち位置", "stance"),
        ("導入の型", "opening_pattern"), ("つなぎの型", "transition_pattern"),
        ("締めの型", "closing_pattern"), ("専門用語の扱い", "jargon_policy"),
        ("数字の扱い", "number_policy"), ("文のリズム", "sentence_rhythm"),
        ("セクションの形", "section_shape"),
    ]:
        value = (voice.get(key) or "").strip()
        if value:
            rows.append(f"- {label}: {value}")

    phrases = voice.get("signature_phrases") or []
    if phrases:
        rows.append(f"- よく使う言い回し: {'、'.join(phrases[:5])}")

    avoid = voice.get("avoid") or []
    if avoid:
        rows.append(f"- 使わないもの: {'、'.join(avoid[:6])}")

    if voice.get("what_not_to_copy"):
        rows.append(f"- ただし真似しないこと: {voice['what_not_to_copy']}")

    visual = style.get("visual", {}) or {}
    if visual:
        rows += [
            "",
            "参照動画の映像の作り（中央値）:",
            f"- カット {visual.get('cuts_per_minute', '?')}回/分",
            f"- 1ショット {visual.get('median_shot_seconds', '?')}秒",
        ]
        if visual.get("dominant_colors"):
            hexes = "、".join(c["hex"] for c in visual["dominant_colors"][:4])
            rows.append(f"- 支配色 {hexes}")
        rows.append(
            "画の切り替わりがこの頻度で起きる前提で、セクションを設計してください。"
        )

    if measured:
        rows += [
            "",
            "実測値（参照動画の中央値）:",
            f"- 話速 {measured.get('chars_per_minute', '?')}文字/分",
            f"- 1文あたり平均 {measured.get('avg_sentence_chars', '?')}字",
            f"- 導入 {measured.get('hook_seconds', '?')}秒",
            f"- チャプター {measured.get('chapters', '?')}個 / "
            f"1章あたり {measured.get('median_chapter_seconds', '?')}秒",
        ]
    return "\n".join(rows)
