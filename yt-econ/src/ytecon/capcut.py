"""CapCut 連携.

CapCut には公開APIが無いため、「完全無人で書き出しまで」は原理的にできない。
そこでこのモジュールは2つのものを出力する:

  1. draft_content.json / draft_meta_info.json を含む **ドラフトフォルダ**
     → CapCut を起動するとプロジェクト一覧に現れ、タイムラインが組まれた
       状態で開く。あとは書き出しボタンだけ。
  2. **インポートキット**（素材一式 + 秒単位の編集指示書）
     → ドラフト形式は CapCut のバージョンで変わるため、(1) が開けなかった
       場合の確実な退避路。素材をドラッグして指示書の秒数に合わせれば同じ絵になる。

無人運転したい場合は render.backend を ffmpeg にしてください。
CapCut は「最後に人の手で微調整したい」ときの経路です。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Config
from .scenes import Scene
from .script import VideoScript
from .tts import VoiceTrack

log = logging.getLogger(__name__)

US = 1_000_000  # CapCut の時間単位はマイクロ秒


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def default_draft_dir() -> Path:
    """OS ごとの CapCut ドラフト保存先."""
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return local / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    # Linux には CapCut デスクトップが無いので、出力先はプロジェクト配下にする
    return Path("capcut_drafts")


# ----------------------------------------------------------------------
# draft_content.json の部品
# ----------------------------------------------------------------------
def _video_material(path: Path, width: int, height: int, duration_us: int,
                    kind: str = "photo") -> dict[str, Any]:
    return {
        "id": _uid(),
        "type": kind,                      # photo | video
        "path": str(path),
        "material_name": path.name,
        "width": width,
        "height": height,
        "duration": duration_us,
        "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0,
                 "lower_right_y": 1.0, "upper_left_x": 0.0, "upper_left_y": 0.0,
                 "upper_right_x": 1.0, "upper_right_y": 0.0},
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "has_audio": False,
        "intensifies_audio_path": "",
        "local_material_id": "",
        "reverse_intensifies_path": "",
        "reverse_path": "",
        "source_platform": 0,
        "extra_type_option": 0,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
    }


def _audio_material(path: Path, duration_us: int) -> dict[str, Any]:
    return {
        "id": _uid(),
        "type": "extract_music",
        "path": str(path),
        "name": path.name,
        "duration": duration_us,
        "category_id": "",
        "category_name": "local",
        "check_flag": 1,
        "music_id": "",
        "source_platform": 0,
        "wave_points": [],
    }


def _segment(material_id: str, start_us: int, duration_us: int,
             render_index: int, source_start: int = 0,
             volume: float = 1.0) -> dict[str, Any]:
    return {
        "id": _uid(),
        "material_id": material_id,
        "cartoon": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "common_keyframes": [],
        "enable_adjust": True,
        "enable_color_curves": True,
        "enable_color_wheels": True,
        "enable_lut": True,
        "extra_material_refs": [],
        "group_id": "",
        "intensifies_audio": False,
        "is_placeholder": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "render_index": render_index,
        "reverse": False,
        "source_timerange": {"duration": duration_us, "start": source_start},
        "target_timerange": {"duration": duration_us, "start": start_us},
        "speed": 1.0,
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": volume,
    }


def _track(track_type: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "attribute": 0,
        "flag": 0,
        "id": _uid(),
        "is_default_name": True,
        "name": "",
        "segments": segments,
        "type": track_type,
    }


def _empty_materials() -> dict[str, Any]:
    keys = [
        "audio_balances", "audio_effects", "audio_fades", "audio_track_indexes",
        "beats", "canvases", "chromas", "color_curves", "digital_humans", "drafts",
        "effects", "filters", "handwrites", "hsl", "images", "log_color_wheels",
        "loudnesses", "manual_deformations", "masks", "material_animations",
        "material_colors", "multi_language_refs", "placeholders", "plugin_effects",
        "primary_color_wheels", "realtime_denoises", "shapes", "smart_crops",
        "smart_relights", "sound_channel_mappings", "speeds", "stickers", "tail_leaders",
        "text_templates", "time_marks", "transitions", "video_effects", "video_trackings",
        "vocal_beautifys", "vocal_separations",
    ]
    return {k: [] for k in keys}


# ----------------------------------------------------------------------
def build_draft(
    cfg: Config,
    script: VideoScript,
    track_audio: VoiceTrack,
    images: list[Scene],
    subtitle_srt: Path,
    outdir: str | Path,
    draft_name: str,
) -> Path:
    """CapCut のドラフトフォルダを作って、そのパスを返す."""
    w, h = cfg.get("video.resolution", [1920, 1080])
    fps = int(cfg.get("video.fps", 30))

    base = cfg.get("render.capcut.draft_dir", "") or ""
    draft_root = Path(base).expanduser() if base else default_draft_dir()
    if not draft_root.is_absolute():
        draft_root = cfg.root / draft_root
    draft_dir = draft_root / draft_name
    mat_dir = draft_dir / "materials"
    mat_dir.mkdir(parents=True, exist_ok=True)

    # 素材をドラフト配下にコピー（元を消しても CapCut が壊れないように）
    scenes = list(images)
    copied: list[tuple[Path, Scene]] = []
    for i, scene in enumerate(scenes):
        dest = mat_dir / f"scene_{i:02d}{scene.image.suffix}"
        shutil.copy2(scene.image, dest)
        copied.append((dest, scene))
    audio_dest = mat_dir / track_audio.wav_path.name
    shutil.copy2(track_audio.wav_path, audio_dest)
    srt_dest = mat_dir / subtitle_srt.name
    shutil.copy2(subtitle_srt, srt_dest)

    total_us = int((track_audio.duration + 0.8) * US)

    materials = _empty_materials()
    materials["videos"] = []
    materials["audios"] = []
    materials["texts"] = []

    video_segments: list[dict[str, Any]] = []
    for i, (path, scene) in enumerate(copied):
        dur_us = int(scene.duration * US)
        # 静止画素材の duration は CapCut 側では上限値の意味を持つ
        mat = _video_material(path, w, h, max(dur_us, 10 * US))
        materials["videos"].append(mat)
        video_segments.append(
            _segment(mat["id"], int(scene.start * US), dur_us, render_index=i)
        )

    audio_mat = _audio_material(audio_dest, total_us)
    materials["audios"].append(audio_mat)
    audio_segments = [_segment(audio_mat["id"], 0, int(track_audio.duration * US),
                               render_index=0)]

    content = {
        "canvas_config": {"width": w, "height": h, "ratio": "original"},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1, "attachment_info": [], "combination_max_index": 1,
            "export_range": None, "extract_audio_last_index": 1, "lyrics_recognition_id": "",
            "lyrics_sync": True, "lyrics_taskinfo": [], "maintrack_adsorb": True,
            "material_save_mode": 0, "multi_language_current": "none",
            "multi_language_list": [], "multi_language_main": "none",
            "multi_language_mode": "none", "original_sound_last_index": 1,
            "record_audio_last_index": 1, "sticker_max_index": 1,
            "subtitle_keywords_config": None, "subtitle_recognition_id": "",
            "subtitle_sync": True, "subtitle_taskinfo": [], "system_font_list": [],
            "video_mute": False, "zoom_info_params": None,
        },
        "cover": None,
        "create_time": 0,
        "duration": total_us,
        "extra_info": None,
        "fps": float(fps),
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": _uid(),
        "keyframe_graph_list": [],
        "keyframes": {k: [] for k in
                      ["adjusts", "audios", "effects", "filters", "handwrites",
                       "stickers", "texts", "videos"]},
        "last_modified_platform": {
            "app_id": 3704, "app_source": "cc", "app_version": "5.0.0",
            "device_id": "", "hard_disk_id": "", "mac_address": "", "os": "mac",
            "os_version": "",
        },
        "materials": materials,
        "mutable_config": None,
        "name": draft_name,
        "new_version": "110.0.0",
        "platform": {
            "app_id": 3704, "app_source": "cc", "app_version": "5.0.0",
            "device_id": "", "hard_disk_id": "", "mac_address": "", "os": "mac",
            "os_version": "",
        },
        "relationships": [],
        "render_index_track_mode_on": True,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": [_track("video", video_segments), _track("audio", audio_segments)],
        "update_time": 0,
        "version": 360000,
    }

    (draft_dir / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8"
    )

    now_us = int(time.time() * US)
    meta = {
        "cloud_package_completed_time": "",
        "draft_cloud_last_action_download": False,
        "draft_cover": "",
        "draft_deeplink_url": "",
        "draft_fold_path": str(draft_dir),
        "draft_id": _uid(),
        "draft_is_ai_shorts": False,
        "draft_materials": [],
        "draft_name": draft_name,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": str(draft_root),
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": total_us,
    }
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )

    write_import_kit(cfg, script, track_audio, copied, audio_dest, srt_dest, draft_dir)
    log.info("CapCut ドラフトを作成: %s", draft_dir)
    return draft_dir


# ----------------------------------------------------------------------
def write_import_kit(
    cfg: Config,
    script: VideoScript,
    track_audio: VoiceTrack,
    scenes: list[tuple[Path, Scene]],
    audio_path: Path,
    srt_path: Path,
    outdir: Path,
) -> Path:
    """ドラフトが開けなかったとき用の、手で並べ直せる指示書."""
    def ts(sec: float) -> str:
        return f"{int(sec // 60):02d}:{sec % 60:05.2f}"

    rows = [
        "# CapCut 編集指示書",
        "",
        f"- タイトル: {script.topic_title}",
        f"- 総尺: {ts(track_audio.duration + 0.8)}",
        f"- 解像度: {cfg.get('video.resolution')} / {cfg.get('video.fps')}fps",
        "",
        "## 手順",
        "",
        "1. CapCut で新規プロジェクトを作り、解像度を 1920x1080 / 30fps にする",
        f"2. `{audio_path.name}` をオーディオトラックの 00:00.00 に置く",
        "3. 下表のとおりに画像を並べる（画像の長さ＝表の尺）",
        f"4. `{srt_path.name}` を字幕としてインポートする",
        "   （CapCut: テキスト → 字幕の読み込み → ローカル字幕）",
        "5. 必要なら BGM を足し、音量を -26dB 前後まで下げる",
        "6. 1080p / 30fps で書き出す",
        "",
        "## タイムライン",
        "",
        "| # | 開始 | 終了 | 尺 | 画像 | 内容 |",
        "|---|------|------|-----|------|------|",
    ]
    headings = ["導入"] + [s.heading for s in script.sections] + ["まとめ"]
    for i, (path, scene) in enumerate(scenes):
        heading = headings[i] if i < len(headings) else ""
        rows.append(
            f"| {i+1} | {ts(scene.start)} | {ts(scene.end)} | "
            f"{scene.duration:.1f}s | `{path.name}` | {heading} |"
        )

    rows += ["", "## 読み上げ原稿（確認用）", ""]
    for line in track_audio.lines:
        rows.append(f"- `{ts(line.start)}` {line.text}")

    out = outdir / "編集指示.md"
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return out
