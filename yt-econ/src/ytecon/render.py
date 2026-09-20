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

    背景動画（scene.background）があるときは、それをループ再生した上に
    透過カード（PNG）を重ねる。カードは動かさず、背景が動く。

    zoompan には**静止画を1フレームだけ**渡すこと。`-loop 1` で連番入力に
    すると、入力フレームごとに d フレームずつ吐いてしまい、尺もファイル
    サイズも爆発する（6秒の想定が数十MBになる）。
    入力は1枚、出力枚数は `-frames:v` で決める、が正しい組み合わせ。
    """
    ffmpeg = ensure_ffmpeg()
    w, h = cfg.get("video.resolution", [1920, 1080])
    fps = int(cfg.get("video.fps", 30))
    frames = max(int(round(scene.duration * fps)), 1)
    from . import design
    # 切り替えの長さはデザイントークン（motion.fade_ms）。config で明示したらそちら
    fade_frames = int(cfg.get("visuals.fade_in_frames", 0) or design.fade_frames(cfg, fps))
    fade = (f",fade=t=in:st=0:d={fade_frames / fps:.3f}"
            if fade_frames > 0 and frames > fade_frames * 2 else "")

    if scene.background is not None:
        # 背景動画（ループ）＋ 透過カードの重ね合わせ。
        # 背景は「内容に集中できる」ようにぼかして彩度を落とす（実写 B-roll は弱め）
        strong = scene.kind not in ("broll",)
        blur = float(cfg.get("visuals.background_blur", 8)) * (1.0 if strong else 0.35)
        sat = float(cfg.get("visuals.background_saturation", 0.6)) if strong else 0.85
        calm = (f"boxblur=lr={blur:.1f}:lp=2," if blur >= 0.5 else "") + f"eq=saturation={sat:.2f}:brightness=-0.03,"
        fc = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"{calm}fps={fps},format=rgba[bg];"
            f"[1:v]format=rgba[fg];"
            f"[bg][fg]overlay=0:0:format=auto,format=yuv420p{fade}[v]"
        )
        _run(
            [ffmpeg, "-y",
             "-stream_loop", "-1", "-ss", f"{scene.bg_offset:.2f}", "-i", str(scene.background),
             "-i", str(scene.image),
             "-filter_complex", fc, "-map", "[v]", "-frames:v", str(frames),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-r", str(fps), "-an", str(out)],
            f"シーン{index}のレンダリング（動く背景）",
        )
        return out

    if cfg.get("visuals.ken_burns", True) and not scene.still:
        # 拡大時の粗さを防ぐぶんだけ上に取る。2倍まで上げても画質は変わらず遅くなるだけ
        sw, sh = int(w * OVERSAMPLE), int(h * OVERSAMPLE)
        step = (MAX_ZOOM - 1.0) / frames
        # 寄り → 引き → 横移動 を順に回す（E02〜E04。同じ動きが続くと単調に見える）
        from . import bible
        motions = bible.motions(cfg) or ["push_in", "pull_out"]
        motion = motions[index % len(motions)]
        xexpr, yexpr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        if motion == "pull_out":
            zexpr = f"max({MAX_ZOOM}-{step:.8f}*on,1.0)"
        elif motion == "pan":
            # 少しだけ寄った状態で、左→右（奇数回は右→左）へゆっくり流す
            zexpr = f"{1 + (MAX_ZOOM - 1.0) * 0.6:.4f}"
            span = f"(iw-iw/zoom)"
            xexpr = (f"{span}*on/{frames}" if (index // len(motions)) % 2 == 0
                     else f"{span}*(1-on/{frames})")
        else:
            zexpr = f"min(zoom+{step:.8f},{MAX_ZOOM})"
        vf = (
            f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
            f"crop={sw}:{sh},"
            f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}'"
            f":d={frames}:s={w}x{h}:fps={fps}"
        )
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"loop=loop={frames}:size=1:start=0,fps={fps}")
    vf += ",format=yuv420p" + fade

    _run(
        [ffmpeg, "-y", "-i", str(scene.image),
         "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(fps), "-an", str(out)],
        f"シーン{index}のレンダリング",
    )
    return out


def measure_loudness(wav: Path) -> float:
    """声ファイルの統合ラウドネス(LUFS)を1回だけ測る。失敗時は -18 とみなす."""
    exe = ensure_ffmpeg()
    r = subprocess.run(
        [exe, "-nostats", "-i", str(wav), "-af", "ebur128=peak=none", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    if not m:
        return -18.0
    val = float(m[-1])
    return val if val > -60 else -18.0


VOICE_LUFS = -16.0   # ミックス前に声を揃える基準
BGM_LUFS = -20.0     # BGM を揃える基準（ここから volume_db ぶん下げる）


def audio_chain(
    cfg: Config, bgm_idx: int | None, total: float, voice_gain_db: float = 0.0,
    sfx: list[tuple[int, float]] | None = None,
) -> list[str]:
    """音声の filter_complex を組む（BGM の混ぜ方はここだけで決まる）.

    考え方:
      1. 声を先に一定のラウドネス(-16 LUFS)へ。voice_gain_db は
         measure_loudness() で測った値から出す固定ゲイン（動的処理はしない）
      2. BGM も一定のラウドネス(-20 LUFS)に揃え、そこから volume_db だけ下げる。
         既定 -6dB → 声の間(無音区間)で声より 10dB ほど小さい＝「3割」の体感
      3. 声が乗っている間だけ軽く下げる(ratio 2)。強く掛けると BGM が
         「ある気配」すら消えて、無い動画と区別がつかなくなる。
         release を短めにして、文と文の 0.3〜0.6 秒の間でも BGM が戻るようにする
      4. 最後に全体を YouTube 基準(-14 LUFS)へ

    入力: [1:a] が声、[{bgm_idx}:a] が BGM。出力ラベルは [a]。
    """
    vg = f"volume={voice_gain_db:.2f}dB," if abs(voice_gain_db) > 0.05 else ""
    sfx = list(sfx or [])
    sfx_vol = float(cfg.get("render.sfx.volume_db", -10))

    def sfx_bus(chain: list[str]) -> str:
        """効果音を1本のバス [sfx] にまとめて、そのラベルを返す（無ければ空文字）."""
        if not sfx:
            return ""
        labels = []
        for k, (idx, at) in enumerate(sfx):
            ms = int(max(at, 0) * 1000)
            chain.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=mono,atrim=0:4,"
                         f"adelay={ms}|{ms},volume={sfx_vol}dB[sx{k}]")
            labels.append(f"[sx{k}]")
        if len(labels) == 1:
            chain.append(f"{labels[0]}acopy[sfx]")
        else:
            chain.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:"
                         "dropout_transition=0:normalize=0[sfx]")
        return "[sfx]"

    if bgm_idx is None:
        chain = [f"[1:a]{vg}apad=pad_dur=0.8[voice]"]
        bus = sfx_bus(chain)
        if bus:
            chain.append(f"[voice]{bus}amix=inputs=2:duration=first:dropout_transition=0:"
                         "normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
        else:
            chain.append("[voice]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
        return chain

    vol = float(cfg.get("render.bgm.volume_db", -6))
    fade_out_at = max(total - 3, 0)
    chain = [
        f"[1:a]{vg}apad=pad_dur=0.8,asplit=2[voice][sc]",
        f"[{bgm_idx}:a]aresample=48000,loudnorm=I={BGM_LUFS:.0f}:TP=-2:LRA=7,volume={vol}dB,"
        f"afade=t=in:st=0:d=2,afade=t=out:st={fade_out_at:.2f}:d=3[bgm0]",
    ]
    if cfg.get("render.bgm.ducking", True):
        # threshold 0.1 ≒ -20dBFS。声のピークがこれを超えた分の半分だけ BGM を下げる
        chain.append("[bgm0][sc]sidechaincompress=threshold=0.1:ratio=2:"
                     "attack=30:release=250:makeup=1[bgm]")
    else:
        chain.append("[sc]anullsink;[bgm0]acopy[bgm]")
    bus = sfx_bus(chain)
    chain.append(f"[voice][bgm]{bus}amix=inputs={3 if bus else 2}:duration=first:dropout_transition=0:"
                 "normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    return chain


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

    bgm_path = bgm_mod.resolve(cfg, script=script, track=track, outdir=outdir)
    bgm_idx = None
    if bgm_path:
        bgm_idx = len(inputs)
        inputs.append(bgm_path)
        args += ["-stream_loop", "-1", "-i", str(bgm_path)]

    from . import sfx as sfx_mod
    sfx_inputs: list[tuple[int, float]] = []
    for kind, at, path in sfx_mod.plan(cfg, script, track):
        sfx_inputs.append((len(inputs), at))
        inputs.append(path)
        args += ["-i", str(path)]

    char_path = character.build_track(cfg, track.wav_path, total, outdir, track=track)
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

    voice_gain = VOICE_LUFS - measure_loudness(track.wav_path)
    voice_gain = max(-20.0, min(20.0, voice_gain))
    log.info("声のゲイン補正 %+.1f dB（-16 LUFS に揃える）", voice_gain)
    chain += audio_chain(cfg, bgm_idx, total, voice_gain, sfx=sfx_inputs)

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
