"""本編から Shorts（縦 9:16、30〜58 秒）を切り出す.

50 万再生の目標は本編（1 日 1 本・16 分）だけでは届かない。新規チャンネルの再生の大半は
Shorts の発見面から来るので、本編 1 本から 2〜3 本の Shorts を自動で切り出し、
本編への導線（概要欄のリンク）を付けて別枠の時刻に投稿する。

やっていること:
  1. 台本と音声のタイムコードから「ずんだもんのボケ／質問 → めたんの数字入りの答え」で
     完結する 30〜58 秒の窓を探し、点数を付けて重ならないように上位を選ぶ
  2. その区間の音声を切り出し、本編と同じ部品（シーン計画・字幕・立ち絵・BGM）を
     縦画面の設定で回す。上にフック見出し、下に 2 人、そのすぐ上に字幕
  3. 本編のリンク入りのメタデータを書く（Shorts はサムネ不要・字幕は焼き込み）

画面の部品はすべて `video.resolution` と `layout.*` を見て描くので、本編と同じコードで
縦画面が出る。ここでは設定を組み替えるだけで、描画の分岐は持たない。
"""

from __future__ import annotations

import copy
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config import Config
from .script import VideoScript, plain_heading, strip_tags
from .tts import Line, VoiceTrack

log = logging.getLogger(__name__)

_NUM = re.compile(r"\d|[０-９]|パーセント|万円|億|兆|割")
_QUESTION = re.compile(r"[？?]|なぜ|どうして|どうすれば|なの[だか]？|いいのだ|のか[。？]")
# 文脈が無いと意味が取れない出だし（前の発言を受けている）
_CONTINUATION = ("でも", "それで", "つまり", "そして", "だから", "ということは", "じゃあ", "なら",
                 "それは", "それが", "その", "これは", "そこは", "そういう", "ええ", "うん", "そう")
# めたんが視点を切り替える言い方（ここから面白くなる）
_FLIP = ("実は", "ところが", "むしろ", "逆に", "本当は", "でもね", "違うの", "早とちり")
_SURPRISE = ("驚", "困", "笑", "怒")
# 直前の発言が無いと意味が取れないリアクション（「5倍くらい違うのだ」「同じ国とは思えないのだ」）
_REACTION = re.compile(r"(違う|思えない|すごい|多い|少ない|高い|安い|本当|そんなに|くらい)")


@dataclass
class Window:
    """Shorts 1 本ぶんの区間."""
    start: float
    end: float
    lines: list[Line]
    block_id: str
    score: float = 0.0
    hook: str = ""                      # 画面上部の見出し（≦ 18 字目安）
    reasons: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3), "end": round(self.end, 3),
            "duration": round(self.duration, 3), "block_id": self.block_id,
            "score": round(self.score, 2), "hook": self.hook, "reasons": self.reasons,
            "first_line": self.lines[0].text if self.lines else "",
            "last_line": self.lines[-1].text if self.lines else "",
        }


# ----------------------------------------------------------------------
# 1. 区間を選ぶ
# ----------------------------------------------------------------------
def _student_key(cfg: Config) -> str:
    for c in (cfg.get("cast.characters", []) or []):
        if str(c.get("role", "")) == "student":
            return str(c.get("key", ""))
    return ""


def _teacher_key(cfg: Config) -> str:
    for c in (cfg.get("cast.characters", []) or []):
        if str(c.get("role", "")) == "teacher":
            return str(c.get("key", ""))
    return ""


def _turn_ends(lines: list[Line], i: int, teacher: str) -> bool:
    """i 番目の文で「答え」が一区切りつくか（次が別の話者、または区間の終わり）."""
    if i >= len(lines) - 1:
        return True
    if not teacher:
        return True
    return lines[i].speaker == teacher and lines[i + 1].speaker != teacher


def score_window(lines: list[Line], student: str, teacher: str, ideal: float = 45.0,
                 block_start: bool = False) -> tuple[float, list[str]]:
    """区間の面白さ・完結度を点数にする。理由も返す（ログと調整用）.

    block_start はセクションの最初の文から始まる窓。「でも海外では…」のような接続詞も
    セクションの頭なら話題の切り替えなので減点しない。
    """
    score = 0.0
    why: list[str] = []
    first = lines[0].text
    dur = lines[-1].end - lines[0].start

    if student and lines[0].speaker == student:
        score += 2.0; why.append("聞き役の発言から始まる")
    if _QUESTION.search(first):
        score += 2.5; why.append("疑問で始まる")
    if block_start:
        score += 1.0; why.append("セクションの頭から")
    elif any(first.startswith(c) for c in _CONTINUATION):
        score -= 3.0; why.append("出だしが前の発言を受けている")
    if not block_start and lines[0].expression in _SURPRISE and _REACTION.search(first):
        score -= 4.0; why.append("出だしが直前の数字へのリアクション")

    surprises = sum(1 for ln in lines if ln.expression in _SURPRISE and (not student or ln.speaker == student))
    score += min(surprises, 3) * 1.5
    if surprises:
        why.append(f"聞き役のリアクション {surprises}")

    numbers = sum(1 for ln in lines if _NUM.search(ln.text))
    score += min(numbers, 4) * 1.0
    if numbers:
        why.append(f"数字入りの文 {numbers}")

    flips = sum(1 for ln in lines if any(f in ln.text for f in _FLIP))
    score += min(flips, 2) * 1.5
    if flips:
        why.append("視点の切り替えがある")

    if student and teacher:
        turns = sum(1 for a, b in zip(lines, lines[1:]) if a.speaker != b.speaker)
        if turns >= 2:
            score += 1.5; why.append(f"掛け合い {turns} 往復")
        if lines[-1].speaker != teacher:
            score -= 1.0                       # 答えで終わっていない

    score -= abs(dur - ideal) / 12.0           # 45 秒前後がいちばん見られる
    return score, why


def candidates(cfg: Config, script: VideoScript, track: VoiceTrack, n: int = 3,
               min_seconds: float | None = None, max_seconds: float | None = None) -> list[Window]:
    """重ならない上位 n 本の区間。同じブロックからは 1 本まで（内容が散るように）."""
    lo = float(min_seconds or cfg.get("shorts.min_seconds", 30))
    hi = float(max_seconds or cfg.get("shorts.max_seconds", 58))
    student, teacher = _student_key(cfg), _teacher_key(cfg)
    exclude = set(cfg.get("shorts.exclude_blocks", ["closing"]) or [])

    blocks: dict[str, list[Line]] = {}
    for ln in track.lines:
        blocks.setdefault(ln.block_id, []).append(ln)

    found: list[Window] = []
    for block, lines in blocks.items():
        if block in exclude:
            continue
        for i, start_ln in enumerate(lines):
            if student and start_ln.speaker != student and i != 0:
                continue                       # 縦動画は聞き役の一言から入る
            best: Window | None = None
            for j in range(i, len(lines)):
                dur = lines[j].end - start_ln.start
                if dur > hi:
                    break
                if dur < lo or not _turn_ends(lines, j, teacher):
                    continue
                seg = lines[i:j + 1]
                sc, why = score_window(seg, student, teacher, block_start=(i == 0))
                if best is None or sc > best.score:
                    best = Window(start_ln.start, lines[j].end, seg, block, sc, reasons=why)
            if best is not None:
                found.append(best)

    found.sort(key=lambda w: w.score, reverse=True)
    chosen: list[Window] = []
    used_blocks: set[str] = set()
    for w in found:
        if len(chosen) >= n:
            break
        if w.block_id in used_blocks:
            continue
        if any(not (w.end <= c.start or w.start >= c.end) for c in chosen):
            continue
        chosen.append(w)
        used_blocks.add(w.block_id)
    if len(chosen) < n:                        # ブロックが足りなければ同じブロックの別区間も許す
        for w in found:
            if len(chosen) >= n:
                break
            if w in chosen or any(not (w.end <= c.start or w.start >= c.end) for c in chosen):
                continue
            chosen.append(w)
    chosen.sort(key=lambda w: w.start)
    for w in chosen:
        w.hook = hook_for(script, w)
    return chosen


def hook_for(script: VideoScript, w: Window) -> str:
    """画面上部の見出し。セクションの見出しを標準語で。導入なら動画タイトルの前半."""
    m = re.fullmatch(r"s(\d+)", w.block_id)
    if m and int(m.group(1)) < len(script.sections):
        return plain_heading(script.sections[int(m.group(1))].heading)
    title = strip_tags(script.topic_title)
    return re.split(r"[。、，,]", title)[0][:20]


def refine_hooks(cfg: Config, script: VideoScript, windows: list[Window]) -> None:
    """LLM で見出しとタイトルを磨く（shorts.llm_hooks が真のときだけ。失敗したら既定のまま）."""
    if not windows or not cfg.get("shorts.llm_hooks", False):
        return
    try:
        from . import llm
        schema = llm.obj({"items": llm.arr(llm.obj({"hook": llm.STR, "title": llm.STR}, ["hook", "title"]))},
                         ["items"])
        body = "\n\n".join(
            f"[{i}] " + "\n".join(f"{ln.speaker or '-'}: {ln.text}" for ln in w.lines)
            for i, w in enumerate(windows)
        )
        out = llm.complete_json(
            "あなたは YouTube Shorts の編集者です。寝る前に聴く落ち着いた経済チャンネルなので、煽らない。",
            "次の各会話に、画面上部に出す見出し（14 字以内、名詞止め、数字があれば入れる）と、"
            "Shorts のタイトル（28 字以内、疑問形か数字入り）を付けてください。順番どおりに返すこと。\n\n" + body,
            schema, model=str(cfg.get("shorts.model", cfg.get("topics.model", "claude-sonnet-5"))), effort="low",
        )
        items = out.get("items") or []
        for w, it in zip(windows, items):
            if it.get("hook"):
                w.hook = strip_tags(str(it["hook"]))[:20]
            if it.get("title"):
                w.reasons.append("title:" + str(it["title"])[:40])
    except Exception as exc:                   # 見出しは既定で十分。ここでは止めない
        log.warning("Shorts の見出しを LLM で磨けませんでした（既定のまま）: %s", exc)


# ----------------------------------------------------------------------
# 2. 縦画面の設定
# ----------------------------------------------------------------------
def shorts_config(cfg: Config) -> Config:
    """本編の設定を縦画面用に組み替える（元の Config は変えない）."""
    raw = copy.deepcopy(cfg.raw)
    sc = raw.get("shorts") or {}
    w, h = sc.get("resolution", [1080, 1920])
    ratio = float(sc.get("cast_height_ratio", 0.23))
    char_h = int(h * ratio)
    sub_size = int(sc.get("subtitle_size", 76))
    sub_margin = char_h + int(sc.get("subtitle_gap", 24))
    top = int(sc.get("top_band", 420))

    raw.setdefault("video", {})["resolution"] = [w, h]
    v = raw.setdefault("visuals", {})
    v["scene_seconds"] = float(sc.get("scene_seconds", 6.0))
    v["scene_seconds_min"] = 3.0
    v["scene_seconds_max"] = 10.0
    sub = v.setdefault("subtitle", {})
    sub["font_size"] = sub_size
    sub["margin_v"] = sub_margin
    sub["telops"] = False
    raw["layout"] = {
        "top_band": top,
        "sub_band": sub_margin + int(sub_size * 1.25) + 24,
        "characters_in_band": True,
        "side_margin": int(sc.get("side_margin", 40)),
        "no_title_outro": True,
        "type_scale": float(sc.get("type_scale", 1.2)),   # 横幅が半分なので文字を少し大きく
    }
    cast = raw.setdefault("cast", {})
    cast["height_ratio"] = ratio
    for c in cast.get("characters", []) or []:
        c["margin_bottom"] = 0
        c["margin_left"] = int(sc.get("character_margin", 0))
        c["margin_right"] = int(sc.get("character_margin", 0))
    ch = raw.setdefault("character", {})
    ch["height_ratio"] = ratio
    ch["margin_bottom"] = 0
    return Config(raw=raw, root=cfg.root)


# ----------------------------------------------------------------------
# 3. 書き出し
# ----------------------------------------------------------------------
def _cut_wav(src: Path, out: Path, start: float, end: float) -> Path:
    from .render import ensure_ffmpeg
    proc = subprocess.run(
        [ensure_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-c:a", "pcm_s16le", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"音声の切り出しに失敗: {proc.stderr[-300:]}")
    return out


def sub_track(track: VoiceTrack, w: Window, wav: Path, head: float, tail: float) -> tuple[VoiceTrack, float, float]:
    """区間の音声を切り出し、タイムコードを 0 始まりにずらした VoiceTrack を返す."""
    a = max(w.start - head, 0.0)
    b = min(w.end + tail, track.duration + tail)
    _cut_wav(track.wav_path, wav, a, b)
    lines = [replace(ln, start=ln.start - a, end=ln.end - a) for ln in w.lines]
    return VoiceTrack(wav_path=wav, lines=lines, sample_rate=track.sample_rate), a, b


def render_hook_band(cfg: Config, hook: str, out: Path) -> Path:
    """画面上部のフック見出し（透過 PNG、全編に重ねる）."""
    from PIL import Image, ImageDraw
    from . import assets

    w, h = cfg.get("video.resolution", [1080, 1920])
    pal = assets.palette(cfg)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    top = int(cfg.get("shorts.hook_top", 150))
    f_ch = assets.load_font(cfg, 34, "bold")
    name = str(cfg.get("channel.name", ""))
    if name:
        bb = d.textbbox((0, 0), name, font=f_ch)
        d.text(((w - (bb[2] - bb[0])) // 2, top), name, font=f_ch, fill=pal["accent"],
               stroke_width=4, stroke_fill="#0B1120")
        top += 58
    f, lines = assets.fit_text(cfg, d, hook, "display_m", w - 120, 2, min_size=60)
    lh = int(f.size * 1.25)
    y = top
    for line in lines[:2]:
        bb = d.textbbox((0, 0), line, font=f, stroke_width=10)
        x = (w - (bb[2] - bb[0])) // 2 - bb[0]
        d.text((x, y), line, font=f, fill="#FFFFFF", stroke_width=10, stroke_fill="#0B1120")
        y += lh
    img.save(out)
    return out


def shorts_metadata(cfg: Config, script: VideoScript, w: Window, parent_url: str = "",
                    parent_minutes: float = 0.0):
    """Shorts のタイトル・概要欄・タグ。本編への導線を必ず入れる."""
    from . import metadata as md
    from .character import detect_credits

    title_hint = next((r[6:] for r in w.reasons if r.startswith("title:")), "")
    base_title = strip_tags(script.topic_title)
    title = title_hint or f"{w.hook}｜{base_title}"
    title = title[:88] + " #Shorts"

    lead = "\n".join(f"{ln.text}" for ln in w.lines[:2])
    parts = [lead]
    if parent_url:
        mins = f"{parent_minutes:.0f}分" if parent_minutes else ""
        parts.append(f"▶ 本編{('（' + mins + '）') if mins else ''}はこちら\n{parent_url}")
    else:
        parts.append("▶ 本編はチャンネルの最新動画から")
    parts.append("寝る前に聴く、お金と就活とAIの話。毎日19:00に本編を更新しています。")
    parts.append("■ 音声\n" + md._voice_credit(cfg))
    credits = [c for c in detect_credits(cfg) + [str(cfg.get("render.bgm.credit", "") or "").strip()] if c]
    if credits:
        parts.append("■ 素材\n" + "\n".join(credits))
    hashtags = ["#Shorts", "#ずんだもん", "#四国めたん", "#経済", "#就活"]
    parts.append(" ".join(hashtags))
    tags = ["Shorts", "ずんだもん", "四国めたん"] + [t for t in script.tags if t][:10]
    return md.Metadata(
        title=title, description="\n\n".join(parts)[:5000], tags=tags,
        category_id=str(cfg.get("upload.category_id", "25")),
        language=str(cfg.get("upload.language", "ja")),
    )


def build_short(cfg: Config, script: VideoScript, track: VoiceTrack, w: Window, outdir: Path,
                index: int, parent_url: str = "") -> dict[str, Any]:
    """1 本の Shorts を outdir/shorts/short_NN/ に書き出す。video.mp4 と metadata.json を返す."""
    from . import render, scenes as scenes_mod, subtitles

    d = Path(outdir) / "shorts" / f"short_{index:02d}"
    d.mkdir(parents=True, exist_ok=True)
    scfg = shorts_config(cfg)
    head = float(cfg.get("shorts.pad_head", 0.15))
    tail = float(cfg.get("shorts.pad_tail", 0.6))
    sub, a, b = sub_track(track, w, d / "narration.wav", head, tail)
    sub.save_manifest(d / "narration.json")

    scenes = scenes_mod.plan_and_render(scfg, script, sub, d / "images")
    subs = subtitles.build(scfg, sub, d, script=script, reserve_right=0)
    hook_png = render_hook_band(scfg, w.hook, d / "hook.png")
    video = render.render(scfg, script, sub, scenes, subs["ass"], d, overlays=[(hook_png, "0:0")])

    meta = shorts_metadata(cfg, script, w, parent_url, parent_minutes=track.duration / 60)
    (d / "metadata.json").write_text(json.dumps(meta.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "window.json").write_text(json.dumps({**w.to_dict(), "cut_from": round(a, 3), "cut_to": round(b, 3)},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Shorts %02d: %.1f秒 / %s / %s", index, sub.duration, w.hook, video)
    return {"dir": str(d), "video": str(video), "meta": meta, "window": w, "srt": str(subs["srt"])}


def build_all(cfg: Config, script: VideoScript, track: VoiceTrack, outdir: Path,
              n: int | None = None, parent_url: str = "") -> list[dict[str, Any]]:
    """本編 1 本から n 本の Shorts を作る。0 本なら何もしない."""
    count = int(n if n is not None else cfg.get("shorts.per_video", 0))
    if count <= 0:
        return []
    wins = candidates(cfg, script, track, n=count)
    if not wins:
        log.warning("Shorts にできる区間が見つかりませんでした")
        return []
    refine_hooks(cfg, script, wins)
    results = []
    for i, w in enumerate(wins, 1):
        try:
            results.append(build_short(cfg, script, track, w, outdir, i, parent_url=parent_url))
        except Exception as exc:               # 1 本失敗しても残りは作る
            log.error("Shorts %02d の生成に失敗: %s", i, exc)
    return results
