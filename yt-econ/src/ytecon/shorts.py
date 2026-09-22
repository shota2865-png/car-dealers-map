"""本編から Shorts（縦 9:16、30〜58 秒）を切り出す.

50 万再生の目標は本編（1 日 1 本・16 分）だけでは届かない。新規チャンネルの再生の大半は
Shorts の発見面から来るので、本編 1 本から 2〜3 本の Shorts を自動で切り出し、
本編への導線（概要欄のリンク）を付けて別枠の時刻に投稿する。

2 つの作り方がある（shorts.mode）:
  story（既定）: 本編の台本を材料に、LLM が 1 本ごとに 起・承・転・結 の 45〜55 秒の掛け合いを
                 書き直し、音声も新しく合成する。切り出しではないので 1 本で話が閉じる。
                 最後は必ず本編への誘導（shorts.cta）で終わり、画面にも「続きは本編で」を出す
  cut          : 本編の音声から区間を切り出す（LLM が使えないときの予備）

やっていること:
  1. 台本と音声のタイムコードから「ずんだもんのボケ／質問 → めたんの数字入りの答え」で
     完結する 30〜58 秒の窓を探し、点数を付けて重ならないように上位を選ぶ（cut）
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
from .script import Diagram, Section, VideoScript, Visual, plain_heading, strip_tags
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
    # Shorts は本編より少しだけ速く、間を詰める（指を止めた 1 秒を無駄にしない）
    vv = raw.setdefault("tts", {}).setdefault("voicevox", {})
    vv["speed"] = float(sc.get("tts_speed", 1.25))
    vv["pause_sentence"] = float(sc.get("pause_sentence", 0.22))
    vv["pause_section"] = float(sc.get("pause_section", 0.45))
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
    # 画面下の「関連動画」リンクは API から付けられない。YouTube Studio で本編を関連動画に設定する
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    parts.append(f"寝る前に聴く、お金と就活とAIの話。毎日{times[0] if times else '夜'}に本編を更新しています。")
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


# ----------------------------------------------------------------------
# 4. story モード: 起承転結のミニ台本を書いて、音声から作る
# ----------------------------------------------------------------------
BEATS = ("起", "承", "転", "結")
# Shorts で概要欄を開く人はほぼいない。画面下の関連動画リンクへ誘導する
_DEFAULT_CTA = ["【ずんだもん】続きは本編で聞くのだ。", "【めたん】本編は毎日19時。下のリンクから飛べるわ。"]


@dataclass
class Beat:
    role: str                                # 起 / 承 / 転 / 結 / 誘導
    lines: list[str]                         # 話者タグ付きの文
    visual: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Story:
    """Shorts 1 本ぶんのミニ台本."""
    hook: str                                # 画面上部の見出し（≦ 14 字）
    title: str                               # 投稿タイトル（≦ 28 字）
    beats: list[Beat]
    section_index: int = -1
    query: str = ""                          # 背景選びの英語キーワード
    sources: list[dict[str, str]] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(strip_tags(ln)) for b in self.beats for ln in b.lines)

    def to_dict(self) -> dict[str, Any]:
        return {"hook": self.hook, "title": self.title, "section_index": self.section_index,
                "chars": self.chars,
                "beats": [{"role": b.role, "lines": b.lines, "visual": b.visual} for b in self.beats]}


_STORY_SYSTEM = """あなたは YouTube Shorts の構成作家です。経済・お金・就活・AI を扱う、2 人の掛け合いのチャンネルの
本編（15 分）から、単体で成立する 45〜55 秒の Shorts を書きます。

Shorts の視聴者は最初の 1 秒で指を止めるかを決め、退屈した瞬間に次へ送ります。
だから「途中を切り出したもの」ではなく、**1 本で 起・承・転・結 が閉じる小さな話**にしてください。
面白いか、役に立つか。どちらかが無い Shorts は最後まで見られません。両方あるのが理想です。

出演者:
{cast}

守ること:
- 文は 1 文 12〜28 字。1 文ずつ改行し、文頭に話者タグ（【めたん】【ずんだもん】）を付ける。地の文は書かない
- 読み上げ文を体言止め（「〜約9パーセント。」「〜の数字。」）で終えない。音声合成の語尾が不自然に上がる。
  必ず「〜だったの」「〜よ」「〜のだ」など述語で終える。1 文に数字は 1 つまで
- 全体で 280〜360 字（タグを除く）。少ないと薄く、これを超えると 60 秒に収まらない
- 起（1〜2 文、ずんだもん）: 視聴者が自分ごとにできる具体的な状況か、意外な数字で始める。
  「こんにちは」「今日は」は禁止。前置きなしで、いきなり本題
- 承（2〜3 文、めたん）: 事実を数字と出典つきで。出典は「総務省の白書によると」のように文中で
- 転（3〜4 文）: ずんだもんが極端な結論に飛び、めたんが「そこは違うの」「むしろ」で視点を返す。ここが山場
- 結（2 文、めたん）: 視聴者が今夜できる 1 つの小さな行動、または覚えて帰る 1 つの数字。説教にしない
- 数字は本編の台本にあるものだけを使う。新しい統計や社名を作らない。断定的な投資助言はしない
- 統計の時点が今から 1 年以上前（例: 令和6年版・2024 年）なら、そのまま「今の数字」のように言わない。
  「令和6年の時点で」と時点を言い、「今は変わっているけれど、最初のとっかかりの時点でこれだけ差があった」
  のように、古い数字を「出だしの差」として使う。今日の日付は {today}
- ずんだもんの語尾は「〜のだ」「〜なのだ」、一人称は「ぼく」。めたんは「〜よ」「〜ね」「〜わ」「〜の」で、です・ます調にしない
- 感情が動く文には文頭に表情タグ [驚] [困] [笑] [考] [指] を付けてよい（話者タグの後ろ）
- 本編への誘導の文はこちらで最後に足すので、書かない

画面（visual）は各ビートに 1 つ。次のどれか:
- {{"kind": "number", "value": "9% vs 46%", "label": "生成AIを使った人の割合", "note": "出典: 総務省"}}
- {{"kind": "compare", "title": "AIの文 vs 自分の文", "items": ["速さ|速い|遅い", "材料|一般論|自分の事実"]}}
- {{"kind": "table", "title": "…", "items": ["項目|値", "項目|値"]}}（2〜4 行）
- {{"kind": "steps", "title": "今夜やること", "items": ["…", "…"]}}（2〜3 行、各 ≦ 14 字）
- {{"kind": "quote", "text": "体言止めの一句（≦ 18 字）", "source": ""}}
起は quote か number、承は number か table、転は compare か quote、結は steps か quote が合います。
"""

_STORY_USER = """# 本編のタイトル
{title}

# 本編の台本（セクションごと。ここにある事実・数字・出典だけを使う）
{body}

# 出典
{sources}

# 依頼
この本編から、互いに別のセクションを土台にした Shorts を {n} 本書いてください。
1 本ごとに、いちばん「指が止まる」入口（数字の落差・誤解の訂正・視聴者の痛いところ）を選ぶこと。
hook は画面上部に常に出る見出しで 14 字以内（数字があれば入れる、名詞止め）。
title は投稿タイトルで 28 字以内（疑問形か数字入り。煽り語「ヤバい」「終わった」は使わない）。
"""


def _story_schema():
    from . import llm
    visual = llm.obj({"kind": llm.STR, "value": llm.STR, "label": llm.STR, "note": llm.STR,
                      "title": llm.STR, "items": llm.arr(llm.STR), "text": llm.STR, "source": llm.STR},
                     ["kind"])
    beat = llm.obj({"role": llm.STR, "lines": llm.arr(llm.STR), "visual": visual}, ["role", "lines", "visual"])
    item = llm.obj({"section_index": {"type": "integer"}, "hook": llm.STR, "title": llm.STR,
                    "beats": llm.arr(beat)}, ["section_index", "hook", "title", "beats"])
    return llm.obj({"items": llm.arr(item)}, ["items"])


def _script_body(script: VideoScript) -> str:
    parts = []
    for i, sec in enumerate(script.sections):
        extra = []
        for c in sec.cards:
            extra.append(f"  カード: {c.text}" + (f"（{c.source}）" if c.source else ""))
        for g in sec.diagrams:
            extra.append(f"  図解({g.type}): {g.title} / " + " ; ".join(g.items))
        parts.append(f"[{i}] {plain_heading(sec.heading)}\n{sec.narration.strip()}\n" + "\n".join(extra))
    return "\n\n".join(parts)


def write_stories(cfg: Config, script: VideoScript, n: int) -> list[Story]:
    """LLM に n 本ぶんのミニ台本を書かせる。失敗したら例外（呼び出し側が cut に落とす）."""
    from . import llm
    from .script import speech_style

    cast = "\n".join(
        f"- 【{c.get('tag') or c.get('name')}】{c.get('name')}: {str(c.get('persona', '')).strip()}"
        for c in (cfg.get("cast.characters", []) or [])
    ) or speech_style(cfg)
    sources = "\n".join(f"- {s.get('name', '')} {s.get('url', '')}".rstrip() for s in (script.sources or [])) or "（なし）"
    import datetime as _dt
    out = llm.complete_json(
        _STORY_SYSTEM.format(cast=cast, today=_dt.date.today().isoformat()),
        _STORY_USER.format(title=strip_tags(script.topic_title), body=_script_body(script), sources=sources, n=n),
        _story_schema(),
        model=str(cfg.get("shorts.model", cfg.get("script.model", "claude-sonnet-5"))),
        effort=str(cfg.get("shorts.effort", "medium")),
    )
    stories: list[Story] = []
    for it in (out.get("items") or [])[:n]:
        beats = []
        for b in it.get("beats") or []:
            lines = [str(x).strip() for x in (b.get("lines") or []) if str(x).strip()]
            if lines:
                beats.append(Beat(role=str(b.get("role", "")), lines=lines,
                                  visual={k: v for k, v in (b.get("visual") or {}).items() if v not in ("", [], None)}))
        if not beats:
            continue
        idx = int(it.get("section_index", -1))
        sec = script.sections[idx] if 0 <= idx < len(script.sections) else None
        st = Story(hook=strip_tags(str(it.get("hook", "")))[:20], title=strip_tags(str(it.get("title", "")))[:40],
                   beats=beats, section_index=idx,
                   query=(sec.visual.query if sec else "") or "", sources=list(script.sources or []))
        stories.append(st)
    if not stories:
        raise RuntimeError("Shorts の台本が返りませんでした")
    return stories


def with_cta(cfg: Config, story: Story) -> Story:
    """最後に本編への誘導を足す（shorts.cta。無ければ既定の 2 文）."""
    cta = [str(x) for x in (cfg.get("shorts.cta") or _DEFAULT_CTA)]
    if story.beats and story.beats[-1].role == "誘導":
        return story
    story.beats.append(Beat(role="誘導", lines=cta, visual={"kind": "cta"}))
    return story


def story_script(cfg: Config, story: Story, parent: VideoScript) -> VideoScript:
    """ミニ台本を、音声合成と字幕がそのまま扱える VideoScript にする（ビート = セクション）."""
    sections = []
    for b in story.beats:
        v = b.visual or {}
        diagrams = []
        if v.get("kind") in ("compare", "table", "steps", "flow", "balance") and v.get("items"):
            diagrams.append(Diagram(type=str(v["kind"]), title=str(v.get("title", "")),
                                    items=[str(x) for x in v["items"]][:5],
                                    note=str(v.get("note", "")), after_sentence=0))
        sections.append(Section(heading=b.role, narration=b.text, on_screen=[],
                                visual=Visual(kind="textcard", query=story.query),
                                beat=b.role, diagrams=diagrams))
    return VideoScript(
        topic_title=story.title or parent.topic_title, hook="", proof="", promise="",
        sections=sections, closing="", title_candidates=[story.title], description="",
        tags=list(parent.tags), thumbnail_copy={}, sources=list(parent.sources or []),
        terms=[], disclaimer=parent.disclaimer,
    )


def render_cta_card(cfg: Config, out: Path) -> Path:
    """最後の画面: 続きは本編で。投稿時刻は upload.publish_times_jst から."""
    from . import assets
    img, d, pal, w, h = assets._card_base(cfg, assets.card_style(cfg, "quote"))
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    when = f"本編は毎日{times[0]}" if times else "本編はチャンネルで"
    avail = min(w - 160, assets.span_width(cfg))
    f1, l1 = assets.fit_text(cfg, d, "続きは本編で", "display_l", avail, 1, min_size=72)
    f2, l2 = assets.fit_text(cfg, d, when + " ▶ 下のリンクから", "headline_m", avail, 1, min_size=40)
    lh1, lh2 = int(f1.size * 1.3), int(f2.size * 1.4)
    total = lh1 + lh2 + 20
    y = assets.TOP_BAND + (h - assets.SUB_BAND - assets.TOP_BAND - total) // 2
    assets._center_text(d, l1[0], f1, y, w, pal["text"])
    assets._center_text(d, l2[0], f2, y + lh1 + 20, w, pal["accent"])
    return assets._save(img, out)


def plan_story_scenes(cfg: Config, story: Story, mini: VideoScript, track: VoiceTrack, outdir: Path) -> list:
    """ビートごとに 1 画面（図解はハイライト付き）。誘導は専用カード."""
    from . import assets
    from . import scenes as scenes_mod

    outdir.mkdir(parents=True, exist_ok=True)
    assets.apply_layout(cfg)
    painter = scenes_mod._Painter(cfg, outdir)
    painter.words = story.query
    scenes: list = []
    for i, (b, sec) in enumerate(zip(story.beats, mini.sections)):
        lines = [ln for ln in track.lines if ln.block_id == f"s{i}"]
        if not lines:
            continue
        s, e = lines[0].start, lines[-1].end
        v = b.visual or {}
        kind = str(v.get("kind", ""))
        try:
            if kind == "cta":
                p = render_cta_card(cfg, painter._next("cta"))
                scenes.append(painter._bg(scenes_mod.Scene(p, s, e, True, "card", "本編へ")))
            elif kind == "number" and v.get("value"):
                scenes.append(painter.number(str(v["value"]), str(v.get("label", "")), str(v.get("note", "")), s, e))
            elif sec.diagrams:
                scenes.extend(painter.diagram(sec.diagrams[0], s, e, lines=lines))
            elif kind == "quote" and v.get("text"):
                scenes.append(painter.quote(str(v["text"]), s, e, source=str(v.get("source", ""))))
            elif kind == "bullets" and v.get("items"):
                scenes.extend(painter.bullets(str(v.get("title", "")), [str(x) for x in v["items"]], s, e, lines))
            else:
                scenes.append(painter.quote(scenes_mod.nominalize(strip_tags(b.lines[0])), s, e))
        except Exception as exc:                          # 画面 1 枚の失敗で止めない
            log.warning("Shorts の画面（%s）を描けなかったので一文カードにします: %s", kind, exc)
            scenes.append(painter.quote(scenes_mod.nominalize(strip_tags(b.lines[0])), s, e))
    scenes.sort(key=lambda x: x.start)
    for a, b2 in zip(scenes, scenes[1:]):
        a.end = b2.start
    if scenes:
        scenes[0].start = 0.0
        scenes[-1].end = track.duration + 0.8
    return scenes


def build_story_short(cfg: Config, parent: VideoScript, story: Story, outdir: Path, index: int,
                      parent_url: str = "", parent_minutes: float = 0.0) -> dict[str, Any]:
    """ミニ台本 → 音声合成 → 縦画面。outdir/shorts/short_NN/ に書き出す."""
    from . import render, subtitles, tts

    d = Path(outdir) / "shorts" / f"short_{index:02d}"
    d.mkdir(parents=True, exist_ok=True)
    scfg = shorts_config(cfg)
    story = with_cta(cfg, story)
    mini = story_script(cfg, story, parent)
    mini.save(d / "script.json")
    (d / "story.json").write_text(json.dumps(story.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    track = tts.synthesize(scfg, mini, d)
    max_s = float(cfg.get("shorts.max_seconds", 58))
    if track.duration > max_s + 4:
        log.warning("Shorts %02d は %.0f 秒（上限 %.0f）。台本が長すぎます", index, track.duration, max_s)
    scenes = plan_story_scenes(scfg, story, mini, track, d / "images")
    subs = subtitles.build(scfg, track, d, script=mini, reserve_right=0)
    hook_png = render_hook_band(scfg, story.hook, d / "hook.png")
    video = render.render(scfg, mini, track, scenes, subs["ass"], d, overlays=[(hook_png, "0:0")])

    w = Window(0.0, track.duration, track.lines, f"story{index}", hook=story.hook,
               reasons=[f"title:{story.title}"] if story.title else [])
    meta = shorts_metadata(cfg, parent, w, parent_url, parent_minutes=parent_minutes)
    (d / "metadata.json").write_text(json.dumps(meta.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Shorts %02d（story）: %.1f秒 / %d字 / %s / %s", index, track.duration, story.chars, story.hook, video)
    return {"dir": str(d), "video": str(video), "meta": meta, "window": w, "srt": str(subs["srt"]), "story": story}


def build_all(cfg: Config, script: VideoScript, track: VoiceTrack, outdir: Path,
              n: int | None = None, parent_url: str = "") -> list[dict[str, Any]]:
    """本編 1 本から n 本の Shorts を作る。0 本なら何もしない.

    既定は story（LLM が起承転結を書き直し、音声も合成する）。LLM が使えなければ cut に落ちる。
    """
    count = int(n if n is not None else cfg.get("shorts.per_video", 0))
    if count <= 0:
        return []
    mode = str(cfg.get("shorts.mode", "story")).lower()
    results: list[dict[str, Any]] = []
    if mode == "story":
        try:
            stories = write_stories(cfg, script, count)
        except Exception as exc:
            log.warning("Shorts の台本を書けなかったので、本編の切り出し（cut）で作ります: %s", exc)
            stories = []
        for i, st in enumerate(stories, 1):
            try:
                results.append(build_story_short(cfg, script, st, outdir, i, parent_url=parent_url,
                                                 parent_minutes=track.duration / 60))
            except Exception as exc:               # 1 本失敗しても残りは作る
                log.error("Shorts %02d の生成に失敗: %s", i, exc)
        if results:
            return results
    wins = candidates(cfg, script, track, n=count)
    if not wins:
        log.warning("Shorts にできる区間が見つかりませんでした")
        return []
    refine_hooks(cfg, script, wins)
    for i, w in enumerate(wins, 1):
        try:
            results.append(build_short(cfg, script, track, w, outdir, i, parent_url=parent_url))
        except Exception as exc:
            log.error("Shorts %02d の生成に失敗: %s", i, exc)
    return results
