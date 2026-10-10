"""顔カメラの中で「いま話している人」を探す（寄りの画角を決めるため）.

やり方: 顔を検出（YuNet）→ 顔ごとに口のあたりの動きの量を数える → 数秒ごとに、いちばん口が動いている顔を話し手とみなす。
音声は見ていないので完璧ではない（笑っている聞き手を拾うことがある）。寄りは控えめにして、外しても破綻しないようにしている。
OpenCV が無い・顔が見つからないときは何も返さない（寄らずに全体を映す）。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MODEL = Path(__file__).resolve().parents[1] / "assets" / "face_detection_yunet_2023mar.onnx"
AW, FPS = 480, 6          # 解析用に縮めた幅と、1 秒あたりのコマ数


def _frames(src: Path, a: float, b: float, crop: tuple[int, int, int, int]):
    import numpy as np
    x, y, w, h = crop
    ah = int(AW * h / w) // 2 * 2
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src),
           "-vf", f"crop={w}:{h}:{x}:{y},fps={FPS},scale={AW}:{ah}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (AW * ah * 3)
    if n < 2:
        return None
    return np.frombuffer(raw[:n * AW * ah * 3], dtype=np.uint8).reshape(n, ah, AW, 3)


def _faces(frames) -> list[tuple[float, float, float, float]]:
    """何コマかで顔を検出し、だいたい同じ場所のものを 1 人にまとめる（x, y, w, h は解析用の画素）."""
    import cv2
    import numpy as np
    n, ah, aw = frames.shape[:3]
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (aw, ah), 0.6, 0.3, 50)
    found: list[list[tuple[float, float, float, float]]] = []
    for k in np.linspace(0, n - 1, min(n, 12)).astype(int):
        _, res = det.detect(np.ascontiguousarray(frames[k]))
        for r in (res if res is not None else []):
            x, y, w, h = map(float, r[:4])
            if w < aw * 0.06:
                continue                      # 棚のぬいぐるみ・ポスターなどの小さい顔は無視
            cx = x + w / 2
            for grp in found:
                gx = sum(f[0] + f[2] / 2 for f in grp) / len(grp)
                if abs(gx - cx) < max(w, grp[0][2]) * 0.8:
                    grp.append((x, y, w, h))
                    break
            else:
                found.append([(x, y, w, h)])
    people = []
    for grp in found:
        if len(grp) < 6:
            continue                          # 12 コマ中 6 コマ未満しか映らない顔には寄らない（見切れ・誤検出）
        people.append(tuple(float(np.median([f[i] for f in grp])) for i in range(4)))
    return sorted(people, key=lambda f: f[0])


def follow(src: Path, a: float, b: float, crop: tuple[int, int, int, int], window: float = 3.0) -> list[dict[str, Any]]:
    """区間 a〜b（src の中の秒）を数秒ごとに見て、寄る先（顔カメラの中での割合 cx, cy）を返す.

    返り値: [{"start", "end", "cx", "cy", "people"}]。顔が見つからなければ空。
    """
    try:
        import numpy as np
        frames = _frames(src, a, b, crop)
        if frames is None:
            return []
        people = _faces(frames)
    except Exception as e:  # noqa: BLE001  寄りは無くても動画は作れる
        log.info("話し手を探せませんでした: %s", e)
        return []
    if not people:
        return []
    n, ah, aw = frames.shape[:3]
    gray = frames.mean(axis=3)
    diff = np.abs(np.diff(gray, axis=0))                     # コマ間の動き
    energy = []
    for x, y, w, h in people:
        # 口のあたり（顔の下 45%。少し下にはみ出して顎の動きも拾う）
        x0, x1 = int(max(0, x + w * 0.15)), int(min(aw, x + w * 0.85))
        y0, y1 = int(max(0, y + h * 0.55)), int(min(ah, y + h * 1.1))
        e = diff[:, y0:y1, x0:x1].mean(axis=(1, 2)) if x1 > x0 and y1 > y0 else np.zeros(n - 1)
        # 顔全体が動いた分（うなずき・姿勢）は引く。口だけが動いているときに高くなる
        fx0, fx1, fy0, fy1 = int(max(0, x)), int(min(aw, x + w)), int(max(0, y)), int(min(ah, y + h * 0.5))
        whole = diff[:, fy0:fy1, fx0:fx1].mean(axis=(1, 2)) if fx1 > fx0 and fy1 > fy0 else 0
        energy.append(np.maximum(e - 0.6 * whole, 0))
    energy = np.array(energy)                                # (人, コマ)
    step = max(1, int(window * FPS))
    out: list[dict[str, Any]] = []
    cur = None
    for k in range(0, n - 1, step):
        sc = energy[:, k:k + step].mean(axis=1)
        best = int(sc.argmax())
        # 僅差なら話し手を変えない（画角がパタパタしないように）
        if cur is not None and best != cur and sc[best] < sc[cur] * 1.35:
            best = cur
        cur = best
        x, y, w, h = people[best]
        t0, t1 = a + k / FPS, min(b, a + (k + step) / FPS)
        cx, cy = (x + w / 2) / aw, (y + h * 0.62) / ah       # 顔の少し下（胸元まで入るように）
        if out and out[-1]["who"] == best:
            out[-1]["end"] = t1
        else:
            out.append({"start": t0, "end": t1, "cx": cx, "cy": cy, "who": best, "people": len(people)})
    if out:
        out[-1]["end"] = b
    # 1 秒未満の切り替えは前の画角に吸収する
    calm: list[dict[str, Any]] = []
    for o in out:
        if calm and o["end"] - o["start"] < 1.0:
            calm[-1]["end"] = o["end"]
        else:
            calm.append(o)
    return calm


def game_panel(src: Path) -> tuple[str, float] | None:
    """画面の左か右に「スマホのゲーム画面」が貼ってある動画なら、その境目を返す（("left", 0.315) など）.

    パズドラ・モンストの動画は「片側にゲーム画面、残りにカメラ」の決まった配置。境目は全部のコマで動かない縦線になるので、
    横方向の明るさの差が「どのコマ・どの行でも大きい列」を探す。はっきりした線が無ければ None。
    """
    try:
        import numpy as np
        w, h = 480, 270
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", f"fps=1,scale={w}:{h}",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        n = len(raw) // (w * h)
        if n < 4:
            return None
        f = np.frombuffer(raw[:n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
        col = (np.abs(np.diff(f, axis=2)) > 14).mean(axis=(0, 1))      # 列ごとの「縦線らしさ」
        best = None
        for side, lo, hi in (("left", 0.18, 0.42), ("right", 0.58, 0.82)):
            a, b = int(lo * w), int(hi * w)
            k = a + int(col[a:b].argmax())
            if col[k] >= 0.75 and (best is None or col[k] > best[2]):
                best = (side, (k + 1) / w, float(col[k]))
        return (best[0], best[1]) if best else None
    except Exception as e:  # noqa: BLE001
        log.info("ゲーム画面を探せませんでした: %s", e)
        return None
