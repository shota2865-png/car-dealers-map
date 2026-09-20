"""動く背景（フッテージ）の用意.

「背景がずっと同じでつまらない」への答え。画面の下地を静止画から**動画**にする。

    1. assets/footage/ に置いた素材（Artlist などから落とした mp4/mov）を使う。
       フォルダ名・ファイル名・tags.yaml のタグで、場面に合うものを選ぶ
    2. 何も無ければ、ゆっくり動く抽象背景（粒子・ぼけ・格子・波・光線・
       グラデーションの揺らぎ）をその場で合成して使う。配色はデザイントークンに従う

Artlist の素材はサブスク会員が自分で落として置く（API が無く、規約上も
自分のアカウントで取得したものを使う）。置き方は docs/Artlistの使い方.md。
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

SUPPORTED = (".mp4", ".mov", ".webm", ".m4v", ".mkv")
KINDS = ("broll", "abstract", "texture")       # 実写 / 抽象ループ / 質感（紙・布・光）
LOOP_SECONDS = 12.0
LOOP_SIZE = (1280, 720)
LOOP_FPS = 30
STYLES = ("bokeh", "particles", "grid", "waves", "rays", "drift")

_STOP = {"and", "the", "of", "in", "a", "an", "with", "on", "at", "to", "for", "4k", "hd",
         "artlist", "footage", "stock", "video", "clip", "mp4", "mov", "final", "copy"}


@dataclass
class Clip:
    path: Path
    kind: str = "broll"
    tags: set[str] = field(default_factory=set)
    duration: float = 0.0

    @property
    def name(self) -> str:
        return self.path.name


# ----------------------------------------------------------------------
# 素材の棚卸し
# ----------------------------------------------------------------------
def footage_dir(cfg: Config) -> Path:
    d = str(cfg.get("visuals.footage_dir", "") or "").strip()
    return (Path(d) if d and Path(d).is_absolute() else cfg.root / (d or "assets/footage"))


def _tokens(text: str) -> set[str]:
    words = re.split(r"[^a-z0-9ぁ-んァ-ン一-龥]+", text.lower())
    return {w for w in words if len(w) >= 2 and w not in _STOP and not w.isdigit()}


def _probe_duration(path: Path) -> float:
    try:
        from .render import ensure_ffmpeg
        r = subprocess.run([ensure_ffmpeg(), "-i", str(path)], capture_output=True, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr)
        if m:
            return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    except Exception:
        pass
    return 0.0


def library(cfg: Config) -> list[Clip]:
    """assets/footage/ 以下の動画を読み、タグを付けて返す（尺はキャッシュ）."""
    root = footage_dir(cfg)
    if not root.exists():
        return []
    manual: dict = {}
    tags_file = root / "tags.yaml"
    if tags_file.exists():
        import yaml
        manual = yaml.safe_load(tags_file.read_text(encoding="utf-8")) or {}
    cache_file = cfg.workdir / "footage_cache.json"
    cache: dict = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except Exception:
            cache = {}
    clips: list[Clip] = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in SUPPORTED or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        parts = [x.lower() for x in p.relative_to(root).parts[:-1]]
        kind = next((k for k in KINDS if k in parts), "broll")
        tags = _tokens(p.stem) | {t for part in parts for t in _tokens(part)}
        spec = manual.get(rel) or manual.get(p.name) or {}
        if isinstance(spec, list):
            spec = {"tags": spec}
        tags |= {str(t).lower() for t in (spec.get("tags") or [])}
        kind = str(spec.get("kind") or kind)
        key = f"{rel}:{p.stat().st_mtime_ns}"
        dur = float(cache.get(key) or 0.0)
        if not dur:
            dur = _probe_duration(p)
            cache[key] = dur
        clips.append(Clip(p, kind, tags, dur))
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    except Exception:
        pass
    return clips


def pick(clips: list[Clip], words: str | set[str], kind: str | None, used: Counter,
         seed: int = 0, min_seconds: float = 3.0, reuse_penalty: float = 1.5,
         exclude: set[str] | None = None) -> Clip | None:
    """語の重なりが多く、まだ使っていない素材を選ぶ（reuse_penalty が大きいほど使い回しを嫌う）.

    exclude に入っている名前（直前に使ったもの）は候補から外す（同じ背景が続かないように）。
    """
    want = _tokens(words) if isinstance(words, str) else set(words)
    cands = [c for c in clips if (kind is None or c.kind == kind) and c.duration >= min_seconds]
    if exclude:
        rest = [c for c in cands if c.name not in exclude]
        cands = rest or cands
    if not cands:
        return None
    rnd = random.Random(seed)
    best, best_score = None, -1e9
    for c in cands:
        score = 3.0 * len(want & c.tags) - reuse_penalty * used[c.name] + rnd.random() * 2.0
        if score > best_score:
            best, best_score = c, score
    return best


# ----------------------------------------------------------------------
# 抽象背景の合成（素材が無いときの下地。配色はデザイントークン）
# ----------------------------------------------------------------------
def _hex(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _blob(r: int):
    import numpy as np
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    g = np.exp(-(x * x + y * y) / (2 * (r / 2.2) ** 2))
    return g / g.max()


def _stamp(img, blob, cx: float, cy: float, color, alpha: float) -> None:
    """img(HxWx3) に blob(kxk) を色付きで加算する（はみ出しは切る）."""
    import numpy as np
    h, w = img.shape[:2]
    k = blob.shape[0]
    r = k // 2
    x0, y0 = int(round(cx)) - r, int(round(cy)) - r
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(w, x0 + k), min(h, y0 + k)
    if xa >= xb or ya >= yb:
        return
    part = blob[ya - y0:yb - y0, xa - x0:xb - x0][..., None] * alpha
    img[ya:yb, xa:xb] += part * np.asarray(color)[None, None, :]


def _frame(style: str, t: float, size, pal, rnd_state) -> "np.ndarray":
    """1 フレームぶんの背景（0〜1 の float）。t は 0〜1 の位相（1 周でループ）."""
    import numpy as np
    w, h = size
    bg = np.asarray(_hex(pal["bg"]))
    surf = np.asarray(_hex(pal["surface"]))
    acc = np.asarray(_hex(pal["accent"]))
    acc2 = np.asarray(_hex(pal.get("accent2", pal["accent"])))
    ph = 2 * math.pi * t
    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    # 下地: 斜めのグラデーション（角度がゆっくり揺れる）
    ang = 0.6 + 0.25 * math.sin(ph)
    g = (xx * math.cos(ang) + yy * math.sin(ang)) / (w * math.cos(ang) + h * math.sin(ang))
    img = bg[None, None, :] * (1 - g[..., None]) + surf[None, None, :] * g[..., None]
    img = img * 1.15 + 0.02
    items = rnd_state
    if style == "bokeh":
        for i, (px, py, r, sp, tint) in enumerate(items):
            cy = (py - t * sp) % 1.2 - 0.1
            cx = px + 0.02 * math.sin(ph * 2 + i)
            _stamp(img, _blob(int(r * h)), cx * w, cy * h, acc if tint < 0.7 else acc2, 0.22 + 0.10 * math.sin(ph + i))
    elif style == "particles":
        blob = _blob(3)
        for i, (px, py, r, sp, tint) in enumerate(items):
            cy = (py - t * sp * 2) % 1.1 - 0.05
            cx = px + 0.015 * math.sin(ph * 3 + i * 0.7)
            _stamp(img, blob, cx * w, cy * h, acc, 0.7 + 0.3 * math.sin(ph * 4 + i))
    elif style == "grid":
        step = w / 12
        off = (t * step) % step
        line = np.zeros((h, w))
        for x in np.arange(-off, w, step):
            xi = int(x)
            if 0 <= xi < w:
                line[:, xi:xi + 2] = 1
        for y in np.arange(-off * 0.6, h, step):
            yi = int(y)
            if 0 <= yi < h:
                line[yi:yi + 2, :] = 1
        vign = np.exp(-(((xx / w - 0.5) ** 2) + ((yy / h - 0.5) ** 2)) * 3.5)
        img += (line * vign)[..., None] * acc[None, None, :] * 0.30
        _stamp(img, _blob(int(h * 0.35)), w * (0.65 + 0.1 * math.sin(ph)), h * (0.4 + 0.1 * math.cos(ph)), acc, 0.20)
    elif style == "waves":
        for k in range(6):
            amp = h * (0.03 + 0.01 * k)
            yc = h * (0.35 + 0.09 * k) + amp * np.sin(xx / w * 2 * math.pi * (1.5 + 0.3 * k) + ph * (1 + k * 0.2) + k)
            d = np.abs(yy - yc)
            img += np.exp(-(d / 2.4) ** 2)[..., None] * (acc if k % 2 == 0 else acc2)[None, None, :] * 0.34
    elif style == "rays":
        for i, (px, py, r, sp, tint) in enumerate(items[:6]):
            # 斜めの光の帯がゆっくり横に流れる
            c = (px + t * sp * 0.5) % 1.4 - 0.2
            d = (xx / w - c) * math.cos(0.9) + (yy / h) * math.sin(0.9)
            img += np.exp(-(d / (0.02 + 0.02 * r)) ** 2)[..., None] * acc[None, None, :] * (0.14 + 0.06 * tint)
    else:  # drift: 大きな光のかたまりが漂う
        for i, (px, py, r, sp, tint) in enumerate(items[:4]):
            cx = px + 0.12 * math.sin(ph + i * 1.7)
            cy = py + 0.10 * math.cos(ph * 0.8 + i)
            _stamp(img, _blob(int(h * (0.28 + 0.1 * r))), cx * w, cy * h, acc if tint < 0.6 else acc2, 0.20)
    return np.clip(img, 0, 1)


def generate_loop(cfg: Config, style: str, out: Path, seconds: float = LOOP_SECONDS,
                  size=LOOP_SIZE, fps: int = LOOP_FPS, seed: int = 11) -> Path:
    """style の抽象背景をループ動画にする（位相 0→1 で一周するので継ぎ目が無い）."""
    import numpy as np
    from PIL import Image

    from .assets import palette
    from .render import ensure_ffmpeg

    pal = palette(cfg)
    rnd = random.Random(seed + STYLES.index(style) if style in STYLES else seed)
    items = [(rnd.random(), rnd.random(), rnd.uniform(0.04, 0.16), rnd.uniform(0.5, 1.0), rnd.random())
             for _ in range(28)]
    w, h = size
    calc = (w // 2, h // 2)                     # 半分の解像度で計算して拡大（十分なめらか）
    n = int(seconds * fps)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [ensure_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    for i in range(n):
        fr = _frame(style, i / n, calc, pal, items)
        im = Image.fromarray((fr * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"背景ループの生成に失敗: {style}")
    return out


def generated_loops(cfg: Config) -> list[Clip]:
    """6 種類の抽象背景を用意して返す（プリセットごとにキャッシュ）."""
    from . import design
    tag = design.preset_name(cfg)
    d = cfg.workdir / "motion_bg"
    clips: list[Clip] = []
    for style in STYLES:
        p = d / f"{tag}_{style}_v2.mp4"
        if not p.exists():
            log.info("動く背景を合成しています: %s", style)
            generate_loop(cfg, style, p)
        clips.append(Clip(p, "abstract", {style, "abstract", "generated"}, LOOP_SECONDS))
    return clips


# ----------------------------------------------------------------------
# 場面 → 背景
# ----------------------------------------------------------------------
class Picker:
    """1 本の動画の中で背景を配る係。同じ素材が続かないように数える."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = bool(cfg.get("visuals.motion_backgrounds", True))
        self.used: Counter = Counter()
        self.last = ""
        self.recent: list[str] = []      # 直近に使った素材（これらは続けて使わない）
        self.lib = library(cfg) if self.enabled else []
        self.loops = generated_loops(cfg) if self.enabled else []
        self.n = 0
        if self.enabled:
            log.info("フッテージ: 素材 %d 本（実写 %d / 抽象 %d / 質感 %d）+ 合成ループ %d 本",
                     len(self.lib), *(sum(1 for c in self.lib if c.kind == k) for k in KINDS),
                     len(self.loops))

    def abstract(self, words: str = "", seed: int = 0, needed: float = 0.0) -> Path | None:
        clip = self.abstract_clip(words, seed=seed, needed=needed)
        return clip.path if clip is not None else None

    def abstract_clip(self, words: str = "", seed: int = 0, needed: float = 0.0) -> Clip | None:
        """カードの後ろに敷く背景。実写も抽象も全部候補にし、場面の語に合うもの・まだ使っていない
        ものを優先する（ぼかして敷くので実写でも文字の邪魔にならない）。合成ループは最後の手段.

        needed 秒以上の素材があればそちらから選ぶ（途中で頭から繰り返さないため）。
        """
        if not self.enabled:
            return None
        self.n += 1
        # 実写は場面の語に合うものだけ（関係ない絵を映さない）。抽象・質感・合成ループはいつでも候補
        want = _tokens(words)
        pool = [c for c in self.lib if c.kind != "broll" or (want & c.tags)] + list(self.loops)
        if not pool:
            pool = list(self.lib) or self.loops
        if not pool:
            return None
        exclude = set(self.recent[-3:])
        long_enough = [c for c in pool if c.duration >= needed and c.name not in exclude]
        clip = None
        if long_enough:
            clip = pick(long_enough, words, None, self.used, seed=seed + self.n, reuse_penalty=4.0)
        if clip is None:
            clip = pick(pool, words, None, self.used, seed=seed + self.n, reuse_penalty=4.0, exclude=exclude)
        if clip is None:
            return None
        self._remember(clip)
        return clip

    def _remember(self, clip: Clip) -> None:
        self.used[clip.name] += 1
        self.last = clip.name
        self.recent.append(clip.name)

    def broll(self, words: str, seed: int = 0) -> Path | None:
        """実写の B-roll。語が合う素材があるときだけ返す（無ければ None → 写真/AI に落ちる）."""
        if not self.enabled or not self.lib:
            return None
        clip = pick(self.lib, words, "broll", self.used, seed=seed, min_seconds=4.0,
                    exclude=set(self.recent[-3:]))
        if clip is None or not (_tokens(words) & clip.tags):
            # 語が一つも合わない実写は「関係ない絵」になるので使わない
            return None
        self._remember(clip)
        return clip.path
