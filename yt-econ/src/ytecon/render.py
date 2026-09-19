"""動画レンダリング（ffmpeg）.

シーンごとに短いセグメントを作って concat し、最後の1パスで
字幕焼き込み・ナレーション・BGM をまとめて合成する。
セグメント方式にしているのは、1本の巨大な filter_complex にすると
どこで壊れたのか分からなくなるため。落ちたシーンだけ見に行ける。

必要なもの: ffmpeg / ffprobe（libass 付き。通常のビルドなら入っている）
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .script import VideoScript
from .tts import VoiceTrack

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    pass


_ffmpeg_path: str | None = None


@dataclass
class Scene:
    image: Path
    start: float
    end: float
    # 図表やテキストカードはズームさせない。文字が滲むうえ、
    # 端に置いた出典キャプションがズームで切れてしまうため。
    still: bool = False

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.5)


# ----------------------------------------------------------------------
def ensure_ffmpeg() -> str:
    """ffmpeg の実行パス。システムに無ければ imageio-ffmpeg の同梱版を使う.

    同梱版は libass 入りなので字幕焼き込みも通る。sudo が使えない環境
    （共用サーバ・CI・Windows）でも `pip install imageio-ffmpeg` だけで動く。
    """
    global _ffmpeg_path
    if _ffmpeg_path:
        return _ffmpeg_path
    exe = shutil.which("ffmpeg")
    if exe:
        _ffmpeg_path = exe
        return exe
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        log.info("システムの ffmpeg が無いので同梱版を使います: %s", bundled)
        _ffmpeg_path = bundled
        return bundled
    except Exception:
        pass
    raise RenderError(
        "ffmpeg が見つかりません。次のいずれかで導入してください。\n"
        "  どのOSでも  : pip install imageio-ffmpeg\n"
        "  Ubuntu/Debian: sudo apt install -y ffmpeg\n"
        "  macOS        : brew install ffmpeg\n"
        "  Windows      : winget install Gyan.FFmpeg"
    )


def _run(args: list[str], label: str) -> None:
    log.debug("ffmpeg: %s", " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise RenderError(f"{label} に失敗しました (exit {proc.returncode})\n{tail}")


def _escape_filter_path(path: Path) -> str:
    """filter 引数に埋めるパスのエスケープ（Windows のドライブレターとバックスラッシュ対策）."""
    s = str(path).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'")


# ----------------------------------------------------------------------
def _is_still(script: VideoScript, block: str) -> bool:
    """そのブロックの画面が『動かしてはいけない』ものかどうか."""
    if block in ("hook", "closing"):
        return True                      # タイトル/アウトロのカード
    try:
        index = int(block[1:])
        return script.sections[index].visual.kind in ("chart", "textcard")
    except (ValueError, IndexError):
        return False


def plan_scenes(script: VideoScript, track: VoiceTrack,
                images: dict[str, Path]) -> list[Scene]:
    """ブロックごとの音声区間に画像を割り当てる."""
    scenes: list[Scene] = []
    order = ["hook"] + [f"s{i}" for i in range(len(script.sections))] + ["closing"]
    image_key = {"hook": "title", "closing": "outro"}
    for block in order:
        start, end = track.block_span(block)
        if end <= start:
            continue
        key = image_key.get(block, block)
        img = images.get(key)
        if img is None:
            log.warning("ブロック %s に対応する画像がありません", block)
            continue
        scenes.append(Scene(image=img, start=start, end=end,
                            still=_is_still(script, block)))
    if not scenes:
        raise RenderError("シーンを1つも構成できませんでした")
    # 最後のシーンは音声の終わりまで伸ばす（0.8秒の余韻）
    scenes[-1].end = track.duration + 0.8
    return scenes


# Ken Burns の最大ズーム倍率。入力はこれより少しだけ大きく作れば足りる
MAX_ZOOM = 1.12
OVERSAMPLE = 1.25


def render_segment(cfg: Config, scene: Scene, out: Path, index: int) -> Path:
    """1シーン = 静止画にゆっくりズームをかけた無音の動画.

    zoompan には**静止画を1フレームだけ**渡すこと。`-loop 1` で連番入力に
    すると、入力フレームごとに d フレームずつ吐いてしまい、尺もファイル
    サイズも爆発する（6秒の想定が数十MBになる）。
    入力は1枚、出力枚数は `-frames:v` で決める、が正しい組み合わせ。
    """
    ffmpeg = ensure_ffmpeg()
    w, h = cfg.get("video.resolution", [1920, 1080])
    fps = int(cfg.get("video.fps", 30))
    frames = max(int(round(scene.duration * fps)), 1)

    if cfg.get("visuals.ken_burns", True) and not scene.still:
        # 拡大時の粗さを防ぐぶんだけ上に取る。2倍まで上げても画質は変わらず遅くなるだけ
        sw, sh = int(w * OVERSAMPLE), int(h * OVERSAMPLE)
        step = (MAX_ZOOM - 1.0) / frames
        if index % 2 == 0:   # 偶数シーンは寄り、奇数シーンは引き。単調さを避ける
            zexpr = f"min(zoom+{step:.8f},{MAX_ZOOM})"
        else:
            zexpr = f"max({MAX_ZOOM}-{step:.8f}*on,1.0)"
        vf = (
            f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
            f"crop={sw}:{sh},"
            f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={w}x{h}:fps={fps}"
        )
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"loop=loop={frames}:size=1:start=0,fps={fps}")

    _run(
        [ffmpeg, "-y", "-i", str(scene.image),
         "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(fps), "-an", str(out)],
        f"シーン{index}のレンダリング",
    )
    return out


def render(
    cfg: Config,
    script: VideoScript,
    track: VoiceTrack,
    images: dict[str, Path],
    subtitle_ass: Path,
    outdir: str | Path,
) -> Path:
    """完成した mp4 のパスを返す."""
    ffmpeg = ensure_ffmpeg()
    outdir = Path(outdir)
    seg_dir = outdir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    scenes = plan_scenes(script, track, images)
    segments = [
        render_segment(cfg, scene, seg_dir / f"seg_{i:02d}.mp4", i)
        for i, scene in enumerate(scenes)
    ]

    # concat demuxer 用のリスト
    list_file = seg_dir / "concat.txt"
    list_file.write_text(
        "".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8"
    )
    silent = outdir / "silent.mp4"
    _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c", "copy", str(silent)], "シーン連結")

    # --- 最終合成 ---
    fonts_dir = cfg.root / "assets" / "fonts"
    sub_filter = f"ass='{_escape_filter_path(subtitle_ass)}'"
    if fonts_dir.exists():
        sub_filter += f":fontsdir='{_escape_filter_path(fonts_dir)}'"

    args = [ffmpeg, "-y", "-i", str(silent), "-i", str(track.wav_path)]

    bgm_file = ""
    if cfg.get("render.bgm.enabled", False):
        name = cfg.get("render.bgm.file", "") or ""
        if name:
            candidate = cfg.root / "assets" / "bgm" / name
            if candidate.exists():
                bgm_file = str(candidate)
            else:
                log.warning("BGM ファイルが見つかりません: %s（BGM なしで続行）", candidate)

    total = track.duration + 0.8
    if bgm_file:
        args += ["-stream_loop", "-1", "-i", bgm_file]
        vol = cfg.get("render.bgm.volume_db", -26)
        filter_complex = (
            f"[0:v]{sub_filter}[v];"
            f"[2:a]volume={vol}dB,afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(total-3,0):.2f}:d=3[bgm];"
            f"[1:a]apad=pad_dur=0.8[voice];"
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
    else:
        filter_complex = (
            f"[0:v]{sub_filter}[v];"
            f"[1:a]apad=pad_dur=0.8,loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )

    final = outdir / "video.mp4"
    args += [
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest", str(final),
    ]
    _run(args, "最終合成")

    log.info("動画を出力しました: %s (%.1f分)", final, total / 60)
    return final


def probe_duration(path: str | Path) -> float:
    """動画の実尺（秒）. ffprobe が無ければ ffmpeg の出力から読み取る."""
    exe = shutil.which("ffprobe")
    if exe:
        proc = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        )
        try:
            return float(proc.stdout.strip())
        except ValueError:
            pass

    # 同梱 ffmpeg には ffprobe が付いてこないので、-i の標準エラーから拾う
    proc = subprocess.run([ensure_ffmpeg(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr)
    if not match:
        return 0.0
    h, m, sec = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(sec)
