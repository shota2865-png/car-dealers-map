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
from pathlib import Path

from .config import Config
from .script import VideoScript
from .tts import VoiceTrack

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    pass


_ffmpeg_path: str | None = None


from .scenes import Scene  # noqa: E402  (Scene の定義は scenes.py に移した)


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

    # 切り替わった感を出す短いフェードイン（カットの手触りが硬すぎない程度）
    fade_frames = int(cfg.get("visuals.fade_in_frames", 6))
    if fade_frames > 0 and frames > fade_frames * 2:
        vf += f",fade=t=in:st=0:d={fade_frames / fps:.3f}"

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
    scenes: list[Scene],
    subtitle_ass: Path,
    outdir: str | Path,
) -> Path:
    """完成した mp4 のパスを返す."""
    from . import bgm as bgm_mod
    from . import character

    ffmpeg = ensure_ffmpeg()
    outdir = Path(outdir)
    seg_dir = outdir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    if not scenes:
        raise RenderError("シーンがありません")

    segments = [
        render_segment(cfg, scene, seg_dir / f"seg_{i:03d}.mp4", i)
        for i, scene in enumerate(scenes)
    ]

    list_file = seg_dir / "concat.txt"
    list_file.write_text("".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
    silent = outdir / "silent.mp4"
    _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c", "copy", str(silent)], "シーン連結")

    total = track.duration + 0.8
    w, h = cfg.get("video.resolution", [1920, 1080])

    # --- 入力を組み立てる（任意のものは有る時だけ） ---
    inputs = [silent, track.wav_path]
    args = [ffmpeg, "-y", "-i", str(silent), "-i", str(track.wav_path)]

    bgm_path = bgm_mod.resolve(cfg)
    bgm_idx = None
    if bgm_path:
        bgm_idx = len(inputs)
        inputs.append(bgm_path)
        args += ["-stream_loop", "-1", "-i", str(bgm_path)]

    char_path = character.build_track(cfg, track.wav_path, total, outdir)
    char_idx = None
    if char_path:
        char_idx = len(inputs)
        inputs.append(char_path)
        args += ["-i", str(char_path)]

    # --- 映像: 字幕を焼く → キャラクターを右下に重ねる ---
    fonts_dir = cfg.root / "assets" / "fonts"
    sub_filter = f"ass='{_escape_filter_path(subtitle_ass)}'"
    if fonts_dir.exists():
        sub_filter += f":fontsdir='{_escape_filter_path(fonts_dir)}'"
    chain = [f"[0:v]{sub_filter}[v0]"]
    vout = "[v0]"
    if char_idx is not None:
        ch_h = int(h * float(cfg.get("character.height_ratio", 0.42)))
        mr = int(cfg.get("character.margin_right", 24))
        mb = int(cfg.get("character.margin_bottom", 0))
        chain.append(f"[{char_idx}:v]scale=-2:{ch_h}[ch]")
        chain.append(f"{vout}[ch]overlay=W-w-{mr}:H-h-{mb}:format=auto:eof_action=repeat[v]")
        vout = "[v]"

    # --- 音声: 声を基準に BGM を下げ、話している間はさらに下げる ---
    if bgm_idx is not None:
        vol = float(cfg.get("render.bgm.volume_db", -10))
        chain.append("[1:a]apad=pad_dur=0.8,asplit=2[voice][sc]")
        chain.append(
            f"[{bgm_idx}:a]volume={vol}dB,afade=t=in:st=0:d=2,"
            f"afade=t=out:st={max(total - 3, 0):.2f}:d=3[bgm0]"
        )
        if cfg.get("render.bgm.ducking", True):
            chain.append("[bgm0][sc]sidechaincompress=threshold=0.03:ratio=6:"
                         "attack=15:release=350[bgm]")
        else:
            chain.append("[sc]anullsink;[bgm0]acopy[bgm]")
        chain.append("[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0,"
                     "loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        chain.append("[1:a]apad=pad_dur=0.8,loudnorm=I=-14:TP=-1.5:LRA=11[a]")

    final = outdir / "video.mp4"
    args += [
        "-filter_complex", ";".join(chain),
        "-map", vout, "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-t", f"{total:.3f}", str(final),
    ]
    _run(args, "最終合成")
    log.info("動画を出力しました: %s (%.1f分 / %dシーン)", final, total / 60, len(scenes))
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
