"""音声合成.

文単位で個別に合成してから連結する。遠回りに見えるが、これをやると
**各文の開始・終了秒がサンプル精度で分かる**ので、字幕もカット割りも
後工程で一切推測せずに作れる。尺の実測値もここで確定する。

対応プロバイダ:
  voicevox : ローカルの VOICEVOX ENGINE を叩く（無料・日本語特化・商用可）
  google   : Google Cloud Text-to-Speech（従量課金・声の自然さが高い）
"""

from __future__ import annotations

import io
import json
import logging
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from .config import Config
from .script import VideoScript, split_sentences

log = logging.getLogger(__name__)


@dataclass
class Line:
    """1文ぶんの音声."""
    block_id: str          # hook / s0 / s1 ... / closing
    index: int             # そのブロック内の通し番号
    text: str
    start: float = 0.0
    end: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class VoiceTrack:
    wav_path: Path
    lines: list[Line] = field(default_factory=list)
    sample_rate: int = 24000

    @property
    def duration(self) -> float:
        return self.lines[-1].end if self.lines else 0.0

    def block_span(self, block_id: str) -> tuple[float, float]:
        """あるブロック（セクション）の開始・終了秒."""
        items = [ln for ln in self.lines if ln.block_id == block_id]
        if not items:
            return (0.0, 0.0)
        return (items[0].start, items[-1].end)

    def save_manifest(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(
            json.dumps(
                {
                    "wav": str(self.wav_path),
                    "duration": self.duration,
                    "sample_rate": self.sample_rate,
                    "lines": [
                        {
                            "block_id": l.block_id, "index": l.index, "text": l.text,
                            "start": round(l.start, 3), "end": round(l.end, 3),
                        }
                        for l in self.lines
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return p

    @classmethod
    def load_manifest(cls, path: str | Path) -> "VoiceTrack":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        track = cls(wav_path=Path(d["wav"]), sample_rate=d.get("sample_rate", 24000))
        track.lines = [
            Line(block_id=l["block_id"], index=l["index"], text=l["text"],
                 start=l["start"], end=l["end"])
            for l in d["lines"]
        ]
        return track


class TTSError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# プロバイダ
# ----------------------------------------------------------------------
class Provider:
    sample_rate = 24000

    def synth(self, text: str) -> bytes:
        """WAV バイト列を返す."""
        raise NotImplementedError


class VoiceVox(Provider):
    """ローカル起動した VOICEVOX ENGINE を使う.

    起動例:
        docker run --rm -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
    """

    def __init__(self, cfg: Config):
        self.base = cfg.env("VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/")
        self.speaker = int(cfg.get("tts.voicevox.speaker", 3))
        self.speed = float(cfg.get("tts.voicevox.speed", 1.0))
        self.pitch = float(cfg.get("tts.voicevox.pitch", 0.0))
        self.intonation = float(cfg.get("tts.voicevox.intonation", 1.0))
        self.session = requests.Session()
        self._check()

    def _check(self) -> None:
        try:
            self.session.get(f"{self.base}/version", timeout=5).raise_for_status()
        except Exception as exc:
            raise TTSError(
                f"VOICEVOX ENGINE に接続できません ({self.base})。\n"
                "  docker run --rm -p 50021:50021 "
                "voicevox/voicevox_engine:cpu-ubuntu20.04-latest\n"
                "で起動してから再実行してください。"
            ) from exc

    def speakers(self) -> list[dict[str, Any]]:
        r = self.session.get(f"{self.base}/speakers", timeout=15)
        r.raise_for_status()
        return r.json()

    def synth(self, text: str) -> bytes:
        q = self.session.post(
            f"{self.base}/audio_query",
            params={"text": text, "speaker": self.speaker},
            timeout=60,
        )
        q.raise_for_status()
        query = q.json()
        query["speedScale"] = self.speed
        query["pitchScale"] = self.pitch
        query["intonationScale"] = self.intonation
        query["prePhonemeLength"] = 0.03
        query["postPhonemeLength"] = 0.05
        query["outputSamplingRate"] = self.sample_rate
        query["outputStereo"] = False

        s = self.session.post(
            f"{self.base}/synthesis",
            params={"speaker": self.speaker},
            json=query,
            timeout=180,
        )
        s.raise_for_status()
        return s.content


class GoogleTTS(Provider):
    """Google Cloud Text-to-Speech（LINEAR16 で受け取る）."""

    def __init__(self, cfg: Config):
        if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            raise TTSError("GOOGLE_APPLICATION_CREDENTIALS が未設定です")
        try:
            from google.cloud import texttospeech  # type: ignore
        except ImportError as exc:
            raise TTSError(
                "google-cloud-texttospeech が必要です: pip install google-cloud-texttospeech"
            ) from exc
        self._tts = texttospeech
        self.client = texttospeech.TextToSpeechClient()
        self.voice_name = cfg.get("tts.google.voice_name", "ja-JP-Neural2-B")
        self.rate = float(cfg.get("tts.google.speaking_rate", 1.1))

    def synth(self, text: str) -> bytes:
        t = self._tts
        resp = self.client.synthesize_speech(
            input=t.SynthesisInput(text=text),
            voice=t.VoiceSelectionParams(language_code="ja-JP", name=self.voice_name),
            audio_config=t.AudioConfig(
                audio_encoding=t.AudioEncoding.LINEAR16,
                speaking_rate=self.rate,
                sample_rate_hertz=self.sample_rate,
            ),
        )
        return resp.audio_content


class GoogleTranslateTTS(Provider):
    """gTTS（無料・インストールが軽い）を使う予備の経路.

    VOICEVOX が用意できない環境（Colab など）でも、とりあえず動画が
    完成するようにするためのもの。声はずんだもんではなく機械的な読み上げ
    になるので、**本番用ではありません。** 絵と流れを確認する用途。
    """

    def __init__(self, cfg: Config):
        try:
            from gtts import gTTS  # noqa: F401
        except ImportError as exc:
            raise TTSError("gTTS が要ります: pip install gtts") from exc
        self.speed_up = float(cfg.get("tts.gtts.speed", 1.15))

    def synth(self, text: str) -> bytes:
        import io
        import subprocess

        from gtts import gTTS

        from .render import ensure_ffmpeg

        buf = io.BytesIO()
        gTTS(text=text, lang="ja").write_to_fp(buf)
        buf.seek(0)

        # mp3 で返ってくるので、WAV（16bit モノラル）に揃える。
        # あわせて再生速度を上げる（gTTS は既定が遅い）
        proc = subprocess.run(
            [ensure_ffmpeg(), "-hide_banner", "-loglevel", "error",
             "-f", "mp3", "-i", "pipe:0",
             "-filter:a", f"atempo={self.speed_up:.2f}",
             "-ac", "1", "-ar", str(self.sample_rate),
             "-f", "wav", "pipe:1"],
            input=buf.read(), capture_output=True,
        )
        if proc.returncode != 0 or not proc.stdout:
            raise TTSError(f"gTTS の変換に失敗しました: {proc.stderr[-300:]!r}")
        return proc.stdout


def make_provider(cfg: Config) -> Provider:
    name = str(cfg.get("tts.provider", "voicevox")).lower()
    if name == "voicevox":
        return VoiceVox(cfg)
    if name == "google":
        return GoogleTTS(cfg)
    if name in ("gtts", "fallback"):
        return GoogleTranslateTTS(cfg)
    if name == "auto":
        # VOICEVOX が立っていればそれを使い、駄目なら gTTS に落ちる
        try:
            return VoiceVox(cfg)
        except TTSError as exc:
            log.warning("VOICEVOX が使えないので gTTS に切り替えます: %s",
                        str(exc).splitlines()[0])
            return GoogleTranslateTTS(cfg)
    raise TTSError(f"未対応の TTS プロバイダです: {name}")


# ----------------------------------------------------------------------
# WAV 連結（標準ライブラリだけで完結させる）
# ----------------------------------------------------------------------
def _read_wav(data: bytes) -> tuple[bytes, int, int, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        return (w.readframes(w.getnframes()), w.getnchannels(),
                w.getsampwidth(), w.getframerate())


def _silence(seconds: float, channels: int, width: int, rate: int) -> bytes:
    return b"\x00" * int(seconds * rate) * channels * width


def synthesize(cfg: Config, script: VideoScript, outdir: str | Path) -> VoiceTrack:
    """台本を音声化し、全文のタイムコード付きトラックを返す."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    provider = make_provider(cfg)

    pause_sentence = float(cfg.get("tts.voicevox.pause_sentence", 0.25))
    pause_section = float(cfg.get("tts.voicevox.pause_section", 0.6))

    frames: list[bytes] = []
    lines: list[Line] = []
    channels = width = rate = 0
    cursor = 0.0

    blocks = script.narration_blocks
    for b_i, (block_id, text) in enumerate(blocks):
        sentences = split_sentences(text)
        for s_i, sentence in enumerate(sentences):
            audio = provider.synth(sentence)
            pcm, ch, wd, rt = _read_wav(audio)
            if not channels:
                channels, width, rate = ch, wd, rt
            elif (ch, wd, rt) != (channels, width, rate):
                raise TTSError("音声フォーマットが途中で変わりました。プロバイダ設定を確認してください")

            dur = len(pcm) / (rate * channels * width)
            lines.append(Line(block_id=block_id, index=s_i, text=sentence,
                              start=cursor, end=cursor + dur))
            frames.append(pcm)
            cursor += dur

            last_in_block = s_i == len(sentences) - 1
            gap = pause_section if last_in_block and b_i < len(blocks) - 1 else pause_sentence
            if not (last_in_block and b_i == len(blocks) - 1):
                frames.append(_silence(gap, channels, width, rate))
                cursor += gap

    if not frames:
        raise TTSError("読み上げるテキストがありません")

    wav_path = outdir / "narration.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))

    track = VoiceTrack(wav_path=wav_path, lines=lines, sample_rate=rate)
    track.save_manifest(outdir / "narration.json")

    lo, hi = cfg.target_seconds
    mins = track.duration / 60
    if track.duration < lo:
        log.warning("尺が短すぎます: %.1f分（目標 %.1f〜%.1f分）",
                    mins, lo / 60, hi / 60)
    elif track.duration > hi:
        log.warning("尺が長すぎます: %.1f分（目標 %.1f〜%.1f分）",
                    mins, lo / 60, hi / 60)
    else:
        log.info("音声合成完了: %.1f分 / %d文", mins, len(lines))

    # 実測から話速を逆算しておくと次回以降の文字数見積りが当たるようになる
    chars = sum(len(l.text) for l in lines)
    if track.duration > 0:
        log.info("実測話速: %.0f文字/分（config の chars_per_minute の調整材料）",
                 chars / (track.duration / 60))
    return track
