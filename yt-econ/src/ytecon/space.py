"""心理学チャンネル（研究所）の「宇宙の解析室」: 動く 3D の背景と、立体のグラフ・ホログラムの絵.

ショーリール（motion/reel3.html、three.js）と同じ見た目を、毎日の自動投稿でも速く作れるように numpy と PIL だけで描く。
  - camera / project       背景と立体グラフで同じカメラを使う（床の上にきちんと棒が立つ）
  - background_loop        星空のドームと光る床の輪がゆっくり回る、継ぎ目のないループ動画（本編の後ろに敷く）
  - bars3d                 研究の数字を、床に立つ光る角柱で比べる（2 本なら「約 N 倍」を自動で出す）
  - holo_icon              絵文字を、青く光るホログラムの絵にする（例え話の店・職場などの「イラスト」）
"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

log = logging.getLogger(__name__)

W, H = 1920, 1080
CYAN, VIO, MAG, GOLD = (34, 211, 238), (124, 92, 255), (255, 61, 154), (255, 200, 87)
WHITE, SUB, INK = (234, 246, 255), (150, 186, 210), (3, 6, 12)
LOOP_VERSION = 2


# ----------------------------------------------------------------------
# カメラ
# ----------------------------------------------------------------------
class Camera:
    """右にめたんが立つので、床の真ん中（世界の原点）が画面の左寄り（x≈760）に来るように置く."""

    def __init__(self, pos=(1.9, 3.1, 10.0), target=(1.9, 1.45, 0.0), fov=44.0, size=(W, H)) -> None:
        self.w, self.h = size
        self.pos = np.array(pos, dtype=np.float64)
        f = np.array(target, dtype=np.float64) - self.pos
        self.f = f / np.linalg.norm(f)
        r = np.cross(self.f, np.array([0.0, 1.0, 0.0]))
        self.r = r / np.linalg.norm(r)
        self.u = np.cross(self.r, self.f)
        self.fpx = (self.h / 2) / math.tan(math.radians(fov) / 2)

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(N, 3) の点 → 画面の (N, 2) と奥行き（カメラより後ろは負）."""
        v = np.asarray(pts, dtype=np.float64) - self.pos
        x, y, z = v @ self.r, v @ self.u, v @ self.f
        zz = np.where(np.abs(z) < 1e-6, 1e-6, z)
        sx = self.w / 2 + x / zz * self.fpx
        sy = self.h / 2 - y / zz * self.fpx
        return np.stack([sx, sy], axis=1), z

    def p(self, x: float, y: float, z: float) -> tuple[float, float]:
        s, _ = self.project(np.array([[x, y, z]]))
        return float(s[0, 0]), float(s[0, 1])


CAM = Camera()


# ----------------------------------------------------------------------
# 動く背景（星空のドーム＋光る床の輪）
# ----------------------------------------------------------------------
def _stars(n: int = 26000, fold: int = 5, seed: int = 7):
    """fold 回の回転対称にした星（1/fold 回転でぴったり元に戻る → ループの継ぎ目が出ない）."""
    rng = np.random.default_rng(seed)
    m = n // fold
    az = rng.uniform(0, 2 * math.pi / fold, m)
    el = np.arcsin(rng.uniform(-0.35, 1.0, m))                 # 上半分を多めに（見上げるドーム）
    r = rng.uniform(26, 46, m)
    pal = np.array([CYAN, CYAN, CYAN, VIO, MAG, WHITE], dtype=np.float32)
    col = pal[rng.integers(0, len(pal), m)] * rng.uniform(0.25, 1.0, (m, 1)).astype(np.float32)
    big = rng.uniform(0, 1, m) < 0.06
    pts, cols, bigs = [], [], []
    for k in range(fold):
        a = az + k * 2 * math.pi / fold
        pts.append(np.stack([r * np.cos(el) * np.cos(a), r * np.sin(el), r * np.cos(el) * np.sin(a)], axis=1))
        cols.append(col)
        bigs.append(big)
    return np.concatenate(pts), np.concatenate(cols), np.concatenate(bigs)


def _floor_layer(cam: Camera = CAM) -> np.ndarray:
    """床の輪・同心円・放射線（動かない部分）。float32 (H, W, 3)."""
    lay = Image.new("RGB", (cam.w, cam.h), (0, 0, 0))
    d = ImageDraw.Draw(lay)
    th = np.linspace(0, 2 * math.pi, 361)
    for rad, a, wd in ((6.4, 1.0, 3), (4.6, 0.35, 2), (2.8, 0.22, 2), (8.6, 0.18, 2)):
        s, z = cam.project(np.stack([np.cos(th) * rad, np.zeros_like(th), np.sin(th) * rad], axis=1))
        ok = z > 0.5
        pts = [tuple(p) for p, k in zip(s, ok) if k]
        if len(pts) > 2:
            d.line(pts, fill=tuple(int(c * a) for c in CYAN), width=wd)
    for k in range(24):
        a = k * 2 * math.pi / 24
        p0, z0 = cam.project(np.array([[math.cos(a) * 1.2, 0, math.sin(a) * 1.2]]))
        p1, z1 = cam.project(np.array([[math.cos(a) * 8.6, 0, math.sin(a) * 8.6]]))
        if z0[0] > 0.5 and z1[0] > 0.5:
            d.line([tuple(p0[0]), tuple(p1[0])], fill=tuple(int(c * 0.12) for c in CYAN), width=1)
    arr = np.asarray(lay, dtype=np.float32)
    glow = np.asarray(lay.resize((cam.w // 4, cam.h // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3))
                      .resize((cam.w, cam.h), Image.BILINEAR), dtype=np.float32)
    return arr + glow * 2.2


def _nebula(cam: Camera = CAM) -> np.ndarray:
    yy, xx = np.mgrid[0:cam.h, 0:cam.w].astype(np.float32)
    out = np.zeros((cam.h, cam.w, 3), np.float32)
    for (cx, cy, r, col, a) in ((0.15, 0.1, 0.55, CYAN, 26), (0.95, 0.85, 0.5, VIO, 22), (0.55, 0.62, 0.35, CYAN, 14)):
        g = np.exp(-(((xx / cam.w - cx) ** 2 + (yy / cam.h - cy) ** 2) / (r * r)) * 2.2)
        out += g[..., None] * (np.array(col, np.float32) / 255 * a)
    vig = np.clip(1.1 - 0.45 * (((xx - cam.w / 2) / (cam.w / 2)) ** 2 + ((yy - cam.h / 2) / (cam.h / 2)) ** 2), 0.5, 1.0)
    return out, vig[..., None]


def _static_base(floor: np.ndarray, neb) -> Image.Image:
    neb_rgb, vig = neb
    return Image.fromarray(np.clip((floor + neb_rgb) * vig, 0, 255).astype(np.uint8))


def background_frame(t: float, period: float, stars, base: Image.Image, cam: Camera = CAM, fold: int = 5) -> Image.Image:
    """1 コマ。base = 床と光（_static_base）。重い計算は PIL の C の処理に任せる（1 コマ 0.05 秒ほど）."""
    from PIL import ImageChops
    pts, cols, big = stars
    a = (t / period) * 2 * math.pi / fold                          # 1 周期で 1/fold 回転 → 継ぎ目なし
    c, s = math.cos(a), math.sin(a)
    rot = np.stack([pts[:, 0] * c - pts[:, 2] * s, pts[:, 1], pts[:, 0] * s + pts[:, 2] * c], axis=1)
    sc, z = cam.project(rot)
    ok = (z > 1) & (sc[:, 0] >= 1) & (sc[:, 0] < cam.w - 2) & (sc[:, 1] >= 1) & (sc[:, 1] < cam.h - 2)
    img = np.zeros((cam.h, cam.w, 3), np.uint16)
    xi, yi = sc[ok, 0].astype(np.int32), sc[ok, 1].astype(np.int32)
    cc = (cols[ok] * 1.7).astype(np.uint16)
    np.add.at(img, (yi, xi), cc)
    b = big[ok]
    for dx, dy in ((1, 0), (0, 1), (1, 1)):
        np.add.at(img, (yi[b] + dy, xi[b] + dx), (cc[b] * 0.8).astype(np.uint16))
    # 床の輪の上を流れる光の粒（周期で元の位置に戻る）
    th = np.array([k * 2 * math.pi / 7 for k in range(7)]) + (t / period) * 2 * math.pi
    for rad in (6.4, 4.6):
        q, zq = cam.project(np.stack([np.cos(th) * rad, np.zeros_like(th), np.sin(th) * rad], axis=1))
        for (x, y), zz in zip(q, zq):
            if zz > 0.5 and 2 <= x < cam.w - 3 and 2 <= y < cam.h - 3:
                img[int(y) - 1:int(y) + 2, int(x) - 2:int(x) + 3] += np.array(CYAN, np.uint16)
    star = Image.fromarray(np.minimum(img, 255).astype(np.uint8))
    glow = star.reduce(4).filter(ImageFilter.GaussianBlur(2)).resize((cam.w, cam.h), Image.BILINEAR)
    out = ImageChops.add(base, star)
    out = ImageChops.add(out, glow)
    return ImageChops.add(out, glow)


def loop_paths(cfg, ffmpeg: str = "ffmpeg") -> tuple[Path, Path | None]:
    """本編の後ろに敷くループ動画（ふだん用, 章の扉・冒頭用）.

    assets/space/loop_a.mp4（星空ドームと光る床）と loop_b.mp4（＋4D のテッセラクト）は、ショーリールと同じ three.js の絵
    （motion/space_loop.html を 40 秒ぶん書き出したもの）。無いときだけ、ここで numpy の簡易版を作る.
    """
    d = cfg.root / "assets" / "space"
    a, b = d / "loop_a.mp4", d / "loop_b.mp4"
    if a.exists():
        return a, (b if b.exists() else None)
    cache = cfg.root / str(cfg.get("pipeline.workdir", "output")) / "cache"
    return background_loop(cache / f"space_loop_v{LOOP_VERSION}.mp4", ffmpeg=ffmpeg), None


def background_loop(out: str | Path, seconds: float = 60.0, fps: int = 30, ffmpeg: str = "ffmpeg",
                    cam: Camera = CAM) -> Path:
    """継ぎ目のない背景ループ（mp4）。同じ版のものがあれば作り直さない."""
    out = Path(out)
    if out.exists() and out.stat().st_size > 1000:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    stars = _stars()
    base = _static_base(_floor_layer(cam), _nebula(cam))
    tmp = out.with_suffix(".tmp.mp4")
    proc = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{cam.w}x{cam.h}",
                             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                             "-pix_fmt", "yuv420p", str(tmp)], stdin=subprocess.PIPE)
    n = int(round(seconds * fps))
    assert proc.stdin is not None
    for i in range(n):
        proc.stdin.write(background_frame(i / fps, seconds, stars, base, cam).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("背景ループの書き出しに失敗しました")
    tmp.replace(out)
    log.info("背景ループを作りました: %s（%.0f 秒）", out, seconds)
    return out


# ----------------------------------------------------------------------
# 立体のグラフ（床に立つ光る角柱）
# ----------------------------------------------------------------------
def _box(cx: float, cz: float, wdt: float, h: float, yaw: float) -> np.ndarray:
    c, s = math.cos(yaw), math.sin(yaw)
    out = []
    for y in (0.0, h):
        for dx, dz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x, z = dx * wdt / 2, dz * wdt / 2
            out.append((cx + x * c - z * s, y, cz + x * s + z * c))
    return np.array(out)


_FACES = [(4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]       # 上・奥・右・手前・左


def bar_positions(n: int) -> list[float]:
    gap = 3.6 if n <= 2 else (2.6 if n == 3 else 2.05)
    return [(i - (n - 1) / 2) * gap for i in range(n)]


def draw_bars3d(canvas: Image.Image, values: list[float], hl: list[bool], p: float = 1.0, cam: Camera = CAM,
                max_h: float = 3.0, only: int | None = None) -> list[dict]:
    """canvas（RGBA, 1920x1080）に角柱を描き、各棒の画面上の位置（伸びきったときの上端・足もと）を返す.

    only = その 1 本だけを描く（p = 伸びる途中 0〜1）。-1 なら描かずに位置だけ。None なら全部（p は全部に効く）.
    """
    n = len(values)
    vmax = max([v for v in values if v > 0] + [1e-9])
    xs = bar_positions(n)
    wdt = 1.25 if n <= 2 else (1.05 if n == 3 else 0.9)
    lay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    edge = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d, de = ImageDraw.Draw(lay), ImageDraw.Draw(edge)
    info = []
    order = sorted(range(n), key=lambda i: -abs(xs[i] - cam.pos[0]))           # 遠い棒から
    for i in order:
        k = p if (only is None or only == i) else 0.0
        h_full = max(0.02, max_h * max(0.0, values[i]) / vmax)
        h = max(0.02, h_full * k)
        col = (GOLD if hl[i] else CYAN) if any(hl) else ([CYAN, VIO, MAG, GOLD][i % 4])
        if any(hl) and not hl[i]:
            col = (96, 140, 190)
        v = _box(xs[i], 0.0, wdt, h, math.radians(-16))
        s, z = cam.project(v)
        top = cam.p(xs[i], h, 0.0)
        base = cam.p(xs[i], 0.0, wdt * 0.75)
        info.append({"i": i, "top": top, "top_full": cam.p(xs[i], h_full, 0.0), "base": base, "x": xs[i], "h": h, "shown": k > 0})
        if k <= 0:
            continue
        center = np.array([xs[i], h / 2, 0.0])
        faces = []
        for f in _FACES:
            q = v[list(f)]
            nrm = np.cross(q[1] - q[0], q[2] - q[0])
            fc = q.mean(axis=0)
            if np.dot(nrm, fc - center) < 0:
                nrm = -nrm
            if np.dot(nrm, cam.pos - fc) > 0:                     # カメラから見える面だけ
                faces.append((float(np.linalg.norm(cam.pos - fc)), f))
        for _dist, f in sorted(faces, reverse=True):
            poly = [tuple(s[j]) for j in f]
            shade = 1.0 if f == _FACES[0] else (0.7 if f == _FACES[3] else 0.5)
            a = 235 if f == _FACES[0] else 170
            d.polygon(poly, fill=tuple(int(c * shade) for c in col) + (a,))
            de.line(poly + [poly[0]], fill=col + (255,), width=2)
    glow = edge.resize((canvas.width // 4, canvas.height // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3)).resize(canvas.size, Image.BILINEAR)
    canvas.alpha_composite(glow)
    canvas.alpha_composite(glow)
    canvas.alpha_composite(lay)
    canvas.alpha_composite(edge)
    return sorted(info, key=lambda x: x["i"])


def ratio_text(values: list[float]) -> str:
    """2 つの数字の差を、倍で言う（1.3 倍未満なら言わない）。例: 3 と 30 → 「約10倍」."""
    vs = [v for v in values if v > 0]
    if len(vs) != 2:
        return ""
    r = max(vs) / min(vs)
    if r < 1.3:
        return ""
    return f"約{r:.1f}倍".replace(".0倍", "倍") if r < 3 else f"約{int(round(r))}倍"


# ----------------------------------------------------------------------
# ホログラムの絵（例え話のイラスト）
# ----------------------------------------------------------------------
def holo_icon(ch: str, size: int, color=CYAN, base: bool = True) -> Image.Image | None:
    """絵文字 → 青く光るホログラム（色を少し残し、走査線と光のにじみ、足もとに投影の光）."""
    from .thumbpanel import emoji_image
    e = emoji_image(ch, size) if ch else None
    if e is None:
        return None
    arr = np.asarray(e.convert("RGBA"), dtype=np.float32)
    rgb, a = arr[..., :3], arr[..., 3:] / 255
    lum = (rgb @ np.array([0.3, 0.59, 0.11], np.float32))[..., None] / 255
    tint = np.array(color, np.float32) * (0.35 + 0.95 * lum)
    out = rgb * 0.42 + tint * 0.58
    a = a * 0.9
    a[::4] *= 0.55                                                    # 走査線
    icon = Image.fromarray(np.concatenate([np.clip(out, 0, 255), a * 255], axis=2).astype(np.uint8), "RGBA")
    pad = size // 4
    beam = size // 3 if base else 0
    cv = Image.new("RGBA", (size + pad * 2, icon.height + pad * 2 + beam), (0, 0, 0, 0))
    glow = Image.new("RGBA", cv.size, color + (0,))
    m = Image.new("L", cv.size, 0)
    m.paste(icon.getchannel("A"), (pad, pad))
    glow.putalpha(m.filter(ImageFilter.GaussianBlur(size / 14)).point(lambda v: int(v * 0.8)))
    if base:
        d = ImageDraw.Draw(cv)
        cx, by = cv.width / 2, pad + icon.height + beam * 0.35
        for k in range(14):                                          # 下から当たる投影の光
            t = k / 14
            d.polygon([(cx - size * 0.18 - t * size * 0.2, by), (cx + size * 0.18 + t * size * 0.2, by),
                       (cx + size * 0.5, pad + icon.height * 0.55), (cx - size * 0.5, pad + icon.height * 0.55)],
                      fill=color + (5,))
        d.ellipse([cx - size * 0.42, by - size * 0.07, cx + size * 0.42, by + size * 0.07], outline=color + (220,), width=3)
        d.ellipse([cx - size * 0.3, by - size * 0.045, cx + size * 0.3, by + size * 0.045], outline=color + (120,), width=2)
    cv.alpha_composite(glow)
    cv.alpha_composite(icon, (pad, pad))
    return cv
