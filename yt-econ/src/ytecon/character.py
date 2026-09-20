"""画面右下の解説キャラクター（口パク・瞬き）.

音声の音量の山に合わせて口の開き具合を切り替え、数秒おきに瞬きする。
画像は4枚あれば足りる:

    assets/character/base.png        口を閉じている・目を開けている
    assets/character/mouth_half.png  口を半分開けている
    assets/character/mouth_open.png  口を開けている
    assets/character/blink.png       目を閉じている

すべて同じサイズ・透過PNGであること。

**ゆっくりMovieMaker4 の「動く立ち絵」形式もそのまま使える。**
ずんだもんの立ち絵（坂本アヒル様の配布素材など）は、次のようなフォルダで
配られている。これを assets/character/ の下にフォルダごと置けばよい:

    assets/character/ずんだもん立ち絵/
        体/00.png            体（服）
        顔/00.png            顔の下地
        口/00.png            閉じた口          ← 00.0.png, 00.1.png, 00.2.png が開いていく途中
        目/00.png            開いた目          ← 00.0.png, 00.1.png … が閉じていく途中（まばたき）
        眉/00.png  髪/00.png  他/00.png        （あるものだけ）

命名規則は YMM4 と同じ: `NN.png` が基本、`NN.K.png` がアニメーションの K コマ目。
ここから base / mouth_half / mouth_open / blink の 4 枚を合成して使う。
使う差分（目 01 番、眉 02 番など）は config の character.parts で選べる。
配布元のガイドライン（クレジット表記など）に必ず従うこと（character.credit）。
画像が無いときは、汎用の仮キャラを自動生成する（本番前に差し替える前提）。

仕組み:
    音声 → 20fps の音量包絡 → 口の状態列 → 状態が変わる区間ごとに PNG を
    並べた concat リスト → アルファ付き動画（ProRes 4444）→ 本編に overlay
"""

from __future__ import annotations

import logging
import random
import subprocess
import wave
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

STATES = ("base", "mouth_half", "mouth_open", "blink")
FPS = 20


# ----------------------------------------------------------------------
# 画像の用意
# ----------------------------------------------------------------------
def character_dir(cfg: Config) -> Path:
    return cfg.root / "assets" / "character"


def find_assets(cfg: Config) -> dict[str, Path] | None:
    """使う画像を決める。本物の立ち絵（フォルダ形式 / PSD）→ 4 枚の PNG → 無し."""
    d = character_dir(cfg)
    ymm = find_ymm_dir(cfg)
    if ymm is not None:
        try:
            return compose_ymm(cfg, ymm, cfg.workdir / "character_composed")
        except Exception as exc:
            log.warning("立ち絵フォルダ %s を合成できませんでした（仮キャラにします）: %s", ymm, exc)
    psd = find_psd(cfg)
    if psd is not None:
        try:
            return compose_psd(cfg, psd, cfg.workdir / "character_composed")
        except Exception as exc:
            log.warning("立ち絵 PSD %s を合成できませんでした（仮キャラにします）: %s", psd, exc)
    found = {s: d / f"{s}.png" for s in STATES}
    if all(p.exists() for p in found.values()):
        return found
    if (d / "base.png").exists():
        # 足りない表情は base で代用する
        base = d / "base.png"
        return {s: (p if p.exists() else base) for s, p in found.items()}
    return None


# ----------------------------------------------------------------------
# ゆっくりMovieMaker4「動く立ち絵」形式（ずんだもん立ち絵など）
# ----------------------------------------------------------------------
# 重ね順（下 → 上）。YMM4 の既定に合わせる
PARTS_ORDER = ("後", "体", "服", "顔", "口", "目", "眉", "髪", "他")


def find_ymm_dir(cfg: Config) -> Path | None:
    """立ち絵フォルダを探す。config の character.dir → assets/character/ 直下のフォルダの順."""
    cands: list[Path] = []
    explicit = str(cfg.get("character.dir", "") or "").strip()
    if explicit:
        p = Path(explicit)
        cands.append(p if p.is_absolute() else cfg.root / p)
    root = character_dir(cfg)
    if root.exists():
        cands += sorted(x for x in root.iterdir() if x.is_dir())
    for c in cands:
        if (c / "口").is_dir() and ((c / "体").is_dir() or (c / "顔").is_dir()):
            return c
        # 1 段深く入っている配布 zip 対策
        for sub in sorted(x for x in c.iterdir() if x.is_dir()) if c.exists() else []:
            if (sub / "口").is_dir() and ((sub / "体").is_dir() or (sub / "顔").is_dir()):
                return sub
    return None


def _variants(folder: Path) -> dict[str, dict]:
    """フォルダ内の PNG を「NN → {base, frames[]}」にまとめる（NN.K.png がコマ）."""
    out: dict[str, dict] = {}
    for p in sorted(folder.glob("*.png")):
        stem = p.name[:-4]
        head, _, tail = stem.partition(".")
        head = head.lstrip("!")          # YMM4 の「!」付き（既定にする印）も同じ扱い
        v = out.setdefault(head, {"base": None, "frames": []})
        if tail == "":
            v["base"] = p
        else:
            try:
                v["frames"].append((float(tail), p))
            except ValueError:
                v["frames"].append((len(v["frames"]), p))
    for v in out.values():
        v["frames"] = [p for _, p in sorted(v["frames"])]
        if v["base"] is None and v["frames"]:
            v["base"] = v["frames"].pop(0)
    return {k: v for k, v in out.items() if v["base"] is not None}


def _pick(folder: Path, wanted: str | None) -> dict | None:
    vs = _variants(folder)
    if not vs:
        return None
    key = str(wanted) if wanted is not None and str(wanted) in vs else sorted(vs)[0]
    return vs[key]


def compose_ymm(cfg: Config, src: Path, out_dir: Path) -> dict[str, Path]:
    """立ち絵の部品を重ねて、base / mouth_half / mouth_open / blink の 4 枚を作る."""
    from PIL import Image

    parts_cfg = cfg.get("character.parts", {}) or {}
    flip = bool(cfg.get("character.flip", False))
    layers: list[tuple[str, dict]] = []
    for part in PARTS_ORDER:
        folder = src / part
        if folder.is_dir():
            v = _pick(folder, parts_cfg.get(part))
            if v:
                layers.append((part, v))
    if not layers:
        raise ValueError("部品フォルダが見つかりません")

    # 更新チェック（部品が変わっていなければ前回の合成を使う）
    stamp = "|".join(f"{p}:{v['base'].stat().st_mtime_ns}" for p, v in layers) + \
            f"|{parts_cfg}|{flip}|{cfg.get('character.crop_bottom', 0)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp_file = out_dir / "stamp.txt"
    result = {s: out_dir / f"{s}.png" for s in STATES}
    if stamp_file.exists() and stamp_file.read_text() == stamp and all(p.exists() for p in result.values()):
        return result

    def pick_image(part: str, v: dict, state: str) -> Path:
        frames = v["frames"]
        if part == "口" and frames:
            if state == "mouth_open":
                return frames[-1]
            if state == "mouth_half":
                return frames[len(frames) // 2] if len(frames) > 1 else frames[0]
        if part == "目" and frames and state == "blink":
            return frames[-1]
        return v["base"]

    size = Image.open(layers[0][1]["base"]).size
    canvases: dict[str, Image.Image] = {}
    for state in STATES:
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        for part, v in layers:
            im = Image.open(pick_image(part, v, state)).convert("RGBA")
            if im.size != size:
                im = im.resize(size, Image.LANCZOS)
            canvas.alpha_composite(im)
        canvases[state] = canvas

    _finish(cfg, canvases, result)
    stamp_file.write_text(stamp)
    mouth_frames = next((len(v["frames"]) for p, v in layers if p == "口"), 0)
    eye_frames = next((len(v["frames"]) for p, v in layers if p == "目"), 0)
    log.info("立ち絵を合成しました: %s（部品 %s / 口コマ %d / 目コマ %d）",
             src.name, "".join(p for p, _ in layers), mouth_frames, eye_frames)
    if mouth_frames == 0:
        log.warning("口の開きコマ（口/00.0.png など）が無いので口パクしません")
    return result


def make_placeholder(cfg: Config, size: int = 640) -> dict[str, Path]:
    """汎用の仮キャラ（丸い顔）を4状態ぶん描く。本物に差し替えるまでのつなぎ."""
    from PIL import Image, ImageDraw

    d = character_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for state in STATES:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        # 体
        dr.ellipse([size * 0.18, size * 0.55, size * 0.82, size * 1.15], fill=(76, 194, 255, 255))
        # 顔
        dr.ellipse([size * 0.15, size * 0.08, size * 0.85, size * 0.78], fill=(255, 224, 189, 255))
        # 目
        ey = size * 0.40
        for ex in (size * 0.37, size * 0.63):
            if state == "blink":
                dr.line([(ex - size * 0.05, ey), (ex + size * 0.05, ey)], fill=(40, 40, 60, 255),
                        width=int(size * 0.02))
            else:
                dr.ellipse([ex - size * 0.045, ey - size * 0.06, ex + size * 0.045, ey + size * 0.06],
                           fill=(40, 40, 60, 255))
        # 口
        mx, my = size * 0.5, size * 0.60
        if state == "mouth_open":
            dr.ellipse([mx - size * 0.09, my - size * 0.06, mx + size * 0.09, my + size * 0.08],
                       fill=(160, 60, 70, 255))
        elif state == "mouth_half":
            dr.ellipse([mx - size * 0.07, my - size * 0.02, mx + size * 0.07, my + size * 0.04],
                       fill=(160, 60, 70, 255))
        else:
            dr.arc([mx - size * 0.08, my - size * 0.06, mx + size * 0.08, my + size * 0.04],
                   start=10, end=170, fill=(120, 60, 70, 255), width=int(size * 0.015))
        p = d / f"{state}.png"
        img.save(p)
        out[state] = p
    log.warning("キャラクター画像が無いので仮キャラを生成しました: %s（本番前に差し替えてください）", d)
    return out


def reserved_width(cfg: Config) -> int:
    """キャラクターが占める横幅（px）。字幕をその左に収めるために使う."""
    if not cfg.get("character.enabled", False):
        return 0
    from PIL import Image

    assets = find_assets(cfg) or make_placeholder(cfg)
    w, h = Image.open(assets["base"]).size
    _rw, rh = cfg.get("video.resolution", [1920, 1080])
    ch_h = rh * float(cfg.get("character.height_ratio", 0.42))
    return int(w * ch_h / h) + int(cfg.get("character.margin_right", 24)) + 30


# ----------------------------------------------------------------------
# 音量包絡 → 口の状態
# ----------------------------------------------------------------------
def envelope(wav_path: Path, fps: int = FPS) -> list[float]:
    """フレームごとの音量（0〜1）。"""
    import array
    import math

    with wave.open(str(wav_path), "rb") as w:
        rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError("16bit PCM の WAV を想定しています")
    samples = array.array("h", raw)
    if ch > 1:
        samples = samples[::ch]
    hop = max(1, rate // fps)
    out: list[float] = []
    for i in range(0, len(samples), hop):
        chunk = samples[i:i + hop]
        if not chunk:
            break
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0
        out.append(rms)
    peak = max(out) if out else 1.0
    if peak > 0:
        out = [min(1.0, v / peak * 1.4) for v in out]   # ピークが 0.7 くらいに来るよう正規化
    # 少しなめらかにする（口がバタつかないように）
    smooth = []
    for i, v in enumerate(out):
        prev = out[i - 1] if i > 0 else v
        smooth.append(max(v, prev * 0.6))
    return smooth


def mouth_states(cfg: Config, env: list[float]) -> list[str]:
    half = float(cfg.get("character.mouth_half_threshold", 0.08))
    opn = float(cfg.get("character.mouth_open_threshold", 0.22))
    return ["mouth_open" if v >= opn else "mouth_half" if v >= half else "base" for v in env]


def apply_blinks(cfg: Config, states: list[str], fps: int = FPS, seed: int = 7) -> list[str]:
    """数秒おきに 3 フレーム（150ms）だけ目を閉じる。話していても瞬きはする."""
    rnd = random.Random(seed)
    lo = float(cfg.get("character.blink_every_min", 2.5))
    hi = float(cfg.get("character.blink_every_max", 5.5))
    out = list(states)
    t = rnd.uniform(lo, hi)
    while int(t * fps) < len(out):
        i = int(t * fps)
        for k in range(i, min(i + 3, len(out))):
            out[k] = "blink"
        t += rnd.uniform(lo, hi)
    return out


# ----------------------------------------------------------------------
# アルファ付きの動画にする
# ----------------------------------------------------------------------
def build_track(cfg: Config, wav_path: Path, total_seconds: float, outdir: Path) -> Path | None:
    """キャラクターのレイヤー（透過動画）を作って返す。無効なら None."""
    if not cfg.get("character.enabled", False):
        return None
    from .render import ensure_ffmpeg

    assets = find_assets(cfg) or make_placeholder(cfg)
    env = envelope(wav_path)
    states = apply_blinks(cfg, mouth_states(cfg, env))

    # 音声が終わったあとの余韻ぶんは口を閉じて待つ
    need = int(total_seconds * FPS) + 1
    if len(states) < need:
        states += ["base"] * (need - len(states))

    # 同じ状態が続く区間をまとめて concat リストにする
    outdir.mkdir(parents=True, exist_ok=True)
    listing = outdir / "character_frames.txt"
    lines: list[str] = []
    run_state, run_len = states[0], 0
    for s in states:
        if s == run_state:
            run_len += 1
            continue
        lines += [f"file '{assets[run_state].as_posix()}'", f"duration {run_len / FPS:.4f}"]
        run_state, run_len = s, 1
    lines += [f"file '{assets[run_state].as_posix()}'", f"duration {run_len / FPS:.4f}",
              f"file '{assets[run_state].as_posix()}'"]     # concat の仕様で最後をもう一度
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = outdir / "character.mov"
    proc = subprocess.run(
        [ensure_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(listing),
         "-vf", f"fps={FPS},format=rgba",
         # qtrle: 透過を保ったまま、平坦な絵なら ProRes 4444 の 1/60 の容量で済む
         # （実測 106秒: ProRes 243MB / qtrle 4MB）。復号に特別な指定も要らない
         "-c:v", "qtrle", "-pix_fmt", "argb",
         str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        log.warning("キャラクターレイヤーの生成に失敗（無しで続けます）: %s", proc.stderr[-300:])
        return None
    log.info("キャラクターレイヤー: %d 状態区間 / %.1f秒", len(lines) // 2, total_seconds)
    return out


def detect_credit(cfg: Config) -> str:
    """立ち絵フォルダの readme から配布元を推定してクレジット文を作る（config に無いとき用）.

    坂本アヒル様のずんだもん立ち絵は「立ち絵：坂本アヒル 様」の表記が慣例。
    readme に名前が見つからなければフォルダ名だけを書く。
    """
    explicit = str(cfg.get("character.credit", "") or "").strip()
    if explicit:
        return explicit
    ymm = find_ymm_dir(cfg)
    if ymm is None:
        psd = find_psd(cfg)
        if psd is None:
            return ""
        ymm = psd.parent
    text = ""
    for p in list(ymm.glob("*.txt")) + list(ymm.parent.glob("*.txt")):
        try:
            text += p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            try:
                text += p.read_text(encoding="cp932", errors="ignore")
            except Exception:
                pass
    if "坂本アヒル" in text or "坂本アヒル" in ymm.name or "坂本アヒル" in ymm.parent.name:
        return "立ち絵：坂本アヒル 様"
    return f"立ち絵：{ymm.name}"


def _finish(cfg: Config, canvases: dict, result: dict[str, Path]) -> None:
    """4 枚に共通の余白を落とし、下を切ってバストアップにし、必要なら反転して保存する."""
    from PIL import Image

    flip = bool(cfg.get("character.flip", False))
    crop_bottom = float(cfg.get("character.crop_bottom", 0.0))   # 下から何割を切るか（脚を画面外へ）
    bbox = None
    for c in canvases.values():
        b = c.getbbox()
        if b:
            bbox = b if bbox is None else (min(bbox[0], b[0]), min(bbox[1], b[1]),
                                           max(bbox[2], b[2]), max(bbox[3], b[3]))
    for st, c in canvases.items():
        if bbox:
            c = c.crop(bbox)
        if 0 < crop_bottom < 0.9:
            c = c.crop((0, 0, c.width, int(c.height * (1 - crop_bottom))))
        if flip:
            c = c.transpose(Image.FLIP_LEFT_RIGHT)
        c.save(result[st])


# ----------------------------------------------------------------------
# PSD 形式の立ち絵（坂本アヒル様の「ずんだもん立ち絵素材」など、PSDTool 対応のもの）
# ----------------------------------------------------------------------
# レイヤー名の約束（PSDTool 流儀）:
#   グループ名の先頭 "!" = 必ず表示 / 子のうち "*" 付きはラジオボタン（どれか1つを表示）
#   ここでは「各グループから1枚選ぶ」を基本に、口と目だけ状態ごとに差し替える
_PSD_DEFAULTS = {
    "口": "むふ", "黒目": "普通目", "目セット": "普通白目", "眉": "普通眉", "顔色": "ほっぺ",
    "服装1": "いつもの服", "右腕": "基本", "左腕": "基本", "枝豆": "枝豆通常",
}
_PSD_MOUTH = {"base": "むふ", "mouth_half": "ほあ", "mouth_open": "ほあー"}
_PSD_BLINK = "UU"
# これらのグループは既定で丸ごと使わない（服装の別バージョン・記号類）
_PSD_SKIP_GROUPS = ("服装2", "記号など")


def find_psd(cfg: Config) -> Path | None:
    explicit = str(cfg.get("character.psd", "") or "").strip()
    if explicit:
        p = Path(explicit)
        p = p if p.is_absolute() else cfg.root / p
        return p if p.exists() else None
    root = character_dir(cfg)
    if not root.exists():
        return None
    found = sorted(root.rglob("*.psd"))
    return found[0] if found else None


def _clean(name: str) -> str:
    return name.lstrip("*!").strip()


def compose_psd(cfg: Config, psd_path: Path, out_dir: Path) -> dict[str, Path]:
    """PSD のレイヤーを選んで重ね、base / mouth_half / mouth_open / blink の 4 枚を作る."""
    from PIL import Image

    try:
        from psd_tools import PSDImage
    except ImportError as exc:
        raise RuntimeError("psd-tools が要ります: pip install psd-tools") from exc

    parts_cfg = {str(k): str(v) for k, v in (cfg.get("character.parts", {}) or {}).items()}
    mouth_cfg = {**_PSD_MOUTH, **{str(k): str(v) for k, v in (cfg.get("character.mouth", {}) or {}).items()}}
    blink_name = str(cfg.get("character.blink", "") or _PSD_BLINK)
    skip = tuple(cfg.get("character.skip_groups", []) or _PSD_SKIP_GROUPS)
    flip = bool(cfg.get("character.flip", False))

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (f"{psd_path}:{psd_path.stat().st_mtime_ns}|{parts_cfg}|{mouth_cfg}|{blink_name}|{skip}|{flip}"
             f"|{cfg.get('character.crop_bottom', 0)}")
    stamp_file = out_dir / "stamp.txt"
    result = {s: out_dir / f"{s}.png" for s in STATES}
    if stamp_file.exists() and stamp_file.read_text() == stamp and all(p.exists() for p in result.values()):
        return result

    psd = PSDImage.open(str(psd_path))
    size = psd.size
    used: list[str] = []

    def pick_child_from(kids, wanted: str | None):
        """候補から1つ選ぶ。config/既定の名前 → 表示中のもの → 最後の候補."""
        if wanted:
            for k in kids:
                if _clean(k.name) == wanted:
                    return k
        vis = [k for k in kids if k.visible and _clean(k.name) != "(非表示)"]
        return vis[-1] if vis else (kids[-1] if kids else None)

    def pick_child(group, wanted: str | None):
        return pick_child_from(list(group), wanted)

    def choices_for(state: str) -> dict[str, str]:
        ch = {**_PSD_DEFAULTS, **parts_cfg}
        if "目" in ch:                       # 「目: 普通目2」のように書かれたら黒目の選択とみなす
            ch.setdefault("黒目", ch["目"])
            ch["黒目"] = ch["目"]
        ch["口"] = mouth_cfg.get(state, mouth_cfg["base"])
        if state == "blink":
            ch["目"] = blink_name
        return ch

    def render(state: str) -> Image.Image:
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        ch = choices_for(state)

        def paint(layer) -> None:
            if layer.is_group():
                name = _clean(layer.name)
                if name in skip:
                    return
                if name == "目" and state == "blink":
                    # まばたきは「目」グループ直下の閉じ目レイヤー1枚に差し替える
                    k = pick_child(layer, ch["目"])
                    if k is not None and not k.is_group():
                        paint(k)
                        return
                kids = list(layer)
                radio = [k for k in kids if k.name.startswith("*")]
                always = [k for k in kids if k.name.startswith("!")]
                plain = [k for k in kids if not k.name.startswith(("*", "!"))]
                # "!" 付きは必ず描く。"*" 付きはラジオボタン（1つだけ）。無印は表示中のものだけ
                chosen = pick_child_from(radio, ch.get(name)) if radio else None
                for k in kids:                       # 重ね順は元の並びを保つ
                    if k in always or k is chosen or (k in plain and k.visible):
                        paint(k)
                return
            if _clean(layer.name) == "(非表示)":
                return
            im = layer.topil()
            if im is None:
                return
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            canvas.alpha_composite(im, (max(layer.left, 0), max(layer.top, 0)))
            if state == "base":
                used.append(_clean(layer.name))

        for top in psd:
            if top.is_group() or top.visible:
                paint(top)
        return canvas

    canvases = {st: render(st) for st in STATES}
    _finish(cfg, canvases, result)
    stamp_file.write_text(stamp)
    log.info("立ち絵(PSD)を合成しました: %s（使ったレイヤー: %s）", psd_path.name, " / ".join(used))
    return result
