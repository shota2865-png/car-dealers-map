"""全工程のオーケストレーション.

    話題選定 → 台本 → 音声 → 素材 → 字幕 → レンダリング
      → サムネ → メタデータ → アップロード

各工程の成果物は output/<slug>/ に残り、DB に「どこまで進んだか」が入る。
途中で落ちても同じコマンドを叩き直せば、終わった工程はスキップして続きから走る。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import traceback
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import assets, capcut, metadata, render, script as script_mod, subtitles, thumbnail
from . import topics as topics_mod
from . import tts, youtube
from .config import Config, load_config
from .state import Store

log = logging.getLogger(__name__)

STAGE_ORDER = ["planned", "scripted", "voiced", "rendered", "uploaded"]


def slugify(text: str, when: dt.datetime | None = None) -> str:
    when = when or dt.datetime.now()
    norm = unicodedata.normalize("NFKC", text)
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()[:24]
    digest = abs(hash(norm)) % 10000
    return f"{when:%Y%m%d-%H%M%S}-{ascii_part or 'topic'}-{digest:04d}"


@dataclass
class Artifacts:
    dir: Path

    @property
    def script(self) -> Path: return self.dir / "script.json"
    @property
    def narration(self) -> Path: return self.dir / "narration.json"
    @property
    def video(self) -> Path: return self.dir / "video.mp4"
    @property
    def thumb(self) -> Path: return self.dir / "thumbnail.jpg"
    @property
    def srt(self) -> Path: return self.dir / "subtitles.srt"
    @property
    def ass(self) -> Path: return self.dir / "subtitles.ass"
    @property
    def images(self) -> Path: return self.dir / "images"
    @property
    def meta(self) -> Path: return self.dir / "metadata.json"


class Pipeline:
    def __init__(self, cfg: Config | None = None, store: Store | None = None):
        self.cfg = cfg or load_config()
        self.store = store or Store(self.cfg.workdir / "state.sqlite3")

    # ------------------------------------------------------------------
    def art(self, slug: str) -> Artifacts:
        d = self.cfg.workdir / slug
        d.mkdir(parents=True, exist_ok=True)
        return Artifacts(dir=d)

    # --- 個別工程 ------------------------------------------------------
    def stage_script(self, slug: str, topic: topics_mod.Topic) -> script_mod.VideoScript:
        art = self.art(slug)
        if art.script.exists():
            log.info("[%s] 台本は生成済み。読み込みます", slug)
            return script_mod.VideoScript.load(art.script)
        s = script_mod.generate(self.cfg, topic)
        s.save(art.script)
        self.store.update_video(slug, status="scripted",
                                stage={"script": str(art.script),
                                       "chars": s.total_chars})
        return s

    def stage_voice(self, slug: str, s: script_mod.VideoScript) -> tts.VoiceTrack:
        art = self.art(slug)
        if art.narration.exists():
            log.info("[%s] 音声は生成済み。読み込みます", slug)
            return tts.VoiceTrack.load_manifest(art.narration)
        track = tts.synthesize(self.cfg, s, art.dir)
        self.store.update_video(slug, status="voiced",
                                stage={"narration": str(art.narration),
                                       "duration": round(track.duration, 1)})
        return track

    def stage_visuals(self, slug: str, s: script_mod.VideoScript,
                      track: tts.VoiceTrack) -> tuple[dict[str, Path], dict[str, Path]]:
        art = self.art(slug)
        images = assets.build_all(self.cfg, s, art.images)
        subs = subtitles.build(self.cfg, track, art.dir)
        return images, subs

    def stage_render(self, slug: str, s: script_mod.VideoScript,
                     track: tts.VoiceTrack, images: dict[str, Path],
                     subs: dict[str, Path]) -> Path:
        art = self.art(slug)
        backend = str(self.cfg.get("render.backend", "ffmpeg")).lower()

        if backend == "capcut":
            draft = capcut.build_draft(
                self.cfg, s, track, images, subs["srt"], art.dir,
                draft_name=f"{slug}",
            )
            self.store.update_video(slug, status="rendered",
                                    stage={"capcut_draft": str(draft)})
            log.info(
                "[%s] CapCut ドラフトを作成しました。CapCut で開いて書き出してください:\n  %s",
                slug, draft,
            )
            return draft

        if art.video.exists():
            log.info("[%s] 動画は生成済み", slug)
        else:
            render.render(self.cfg, s, track, images, subs["ass"], art.dir)
        self.store.update_video(slug, status="rendered",
                                stage={"video": str(art.video)})
        return art.video

    def stage_publish(self, slug: str, s: script_mod.VideoScript,
                      track: tts.VoiceTrack, slot_index: int) -> dict[str, Any]:
        art = self.art(slug)
        if not art.video.exists():
            raise RuntimeError(
                f"{art.video} がありません。CapCut バックエンドの場合は、"
                "書き出した mp4 をこのパスに置いてから upload を実行してください。"
            )
        title, thumb_copy = metadata.choose_title(self.cfg, s)
        if thumb_copy.get("main"):
            s.thumbnail_copy = thumb_copy
            s.save(art.script)
        thumbnail.build(self.cfg, s, art.thumb)

        meta = metadata.build(self.cfg, s, track, title=title)
        art.meta.write_text(
            json.dumps(meta.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = youtube.publish(
            self.cfg, self.store, art.video, meta,
            thumbnail=art.thumb, srt=art.srt, slot_index=slot_index,
        )
        self.store.update_video(
            slug, status="uploaded", title=meta.title,
            youtube_id=result["video_id"], publish_at=result["publish_at"],
            stage={"url": result["url"]},
        )
        return result

    # --- 1本ぶんの通し ---------------------------------------------------
    def produce(self, topic: topics_mod.Topic, slot_index: int = 0,
                upload: bool = True) -> dict[str, Any]:
        slug = slugify(topic.title)
        self.store.create_video(slug, topic.id, topic.title)
        if topic.id:
            self.store.mark_topic_used(topic.id)
        log.info("=== [%s] %s ===", slug, topic.title)

        try:
            s = self.stage_script(slug, topic)
            track = self.stage_voice(slug, s)
            images, subs = self.stage_visuals(slug, s, track)
            self.stage_render(slug, s, track, images, subs)

            result: dict[str, Any] = {"slug": slug, "title": s.topic_title,
                                      "dir": str(self.art(slug).dir)}
            if upload and str(self.cfg.get("render.backend")) == "ffmpeg":
                result.update(self.stage_publish(slug, s, track, slot_index))
            else:
                log.info("[%s] アップロードはスキップしました", slug)
            return result
        except Exception as exc:
            self.store.update_video(slug, status="failed",
                                    error=f"{exc}\n{traceback.format_exc()[-1500:]}")
            raise

    # --- 当日分をまとめて ------------------------------------------------
    def run_daily(self, count: int | None = None, upload: bool = True) -> list[dict[str, Any]]:
        count = count or int(self.cfg.get("pipeline.videos_per_day", 2))
        chosen = topics_mod.select_topics(self.cfg, self.store, count)
        results = []
        retries = int(self.cfg.get("pipeline.retries", 2))
        for i, topic in enumerate(chosen):
            for attempt in range(retries + 1):
                try:
                    results.append(self.produce(topic, slot_index=i, upload=upload))
                    break
                except Exception as exc:
                    if attempt >= retries:
                        log.error("「%s」は %d 回試して失敗しました: %s",
                                  topic.title, attempt + 1, exc)
                        results.append({"title": topic.title, "error": str(exc)})
                    else:
                        log.warning("失敗したので再試行します (%d/%d): %s",
                                    attempt + 1, retries, exc)
        return results

    # --- 再開 ------------------------------------------------------------
    def resume(self, slug: str, upload: bool = True) -> dict[str, Any]:
        rec = self.store.get_video(slug)
        if not rec:
            raise RuntimeError(f"{slug} は見つかりませんでした")
        art = self.art(slug)
        if not art.script.exists():
            raise RuntimeError(f"{art.script} がないので最初からやり直してください")
        s = script_mod.VideoScript.load(art.script)
        track = self.stage_voice(slug, s)
        images, subs = self.stage_visuals(slug, s, track)
        self.stage_render(slug, s, track, images, subs)
        if upload:
            return self.stage_publish(slug, s, track, slot_index=0)
        return {"slug": slug, "status": "rendered"}
