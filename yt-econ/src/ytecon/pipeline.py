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

from . import capcut, character, metadata, render, subtitles, thumbnail, tts, youtube
from . import scenes as scenes_mod
from . import script as script_mod
from . import topics as topics_mod
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


def topic_playlists(cfg: Config, keyword: str, title: str) -> list[str]:
    """検索語（無ければタイトル）に合うテーマ別の再生リスト名（upload.topic_playlists: {再生リスト名: [言葉, ...]}）."""
    table = cfg.get("upload.topic_playlists") or {}
    text = keyword or title
    out = []
    for name, words in table.items():
        if any(str(w) and str(w) in text for w in (words or [])):
            out.append(str(name))
    return out[:2]


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
                      track: tts.VoiceTrack) -> tuple[list, dict[str, Path]]:
        art = self.art(slug)
        scenes = scenes_mod.plan_and_render(self.cfg, s, track, art.images)
        # 右下にキャラクターを置くぶん、字幕とテロップを左に寄せる
        reserve = character.reserved_width(self.cfg)
        subs = subtitles.build(self.cfg, track, art.dir, script=s, reserve_right=reserve)
        return scenes, subs

    def stage_render(self, slug: str, s: script_mod.VideoScript,
                     track: tts.VoiceTrack, images: list,
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
                      track: tts.VoiceTrack, slot_index: int,
                      horizon: str = "flow") -> dict[str, Any]:
        art = self.art(slug)
        if not art.video.exists():
            raise RuntimeError(
                f"{art.video} がありません。CapCut バックエンドの場合は、"
                "書き出した mp4 をこのパスに置いてから upload を実行してください。"
            )
        title, thumb_copy = metadata.choose_title(self.cfg, s, horizon=horizon)
        if thumb_copy.get("main"):
            s.thumbnail_copy = thumb_copy
            s.save(art.script)
        # 完成品の名前（yt_001_20260922）。番号は 001 から昇順、日付は公開日
        from . import finals
        day = self._publish_day(slot_index)
        final_name = (self.store.get_video(slug).stage or {}).get("final_name") if self.store.get_video(slug) else None
        if not final_name:
            final_name = finals.assign(self.cfg, self.store, day)
            self.store.update_video(slug, stage={"final_name": final_name})
        # 手で用意したサムネ（thumbnails/yt_001_20260922.jpg / <公開日>.jpg / <slug>.jpg）があればそれを使い、無ければ自動生成
        manual = thumbnail.pick_manual(self.cfg, slug, day, extra=[final_name])
        if manual is not None:
            log.info("[%s] 手で用意したサムネイルを使います: %s", slug, manual.name)
            thumbnail.prepare(manual, art.thumb)
            self.store.update_video(slug, stage={"thumbnail": "manual", "thumbnail_file": manual.name})
        else:
            thumbnail.build(self.cfg, s, art.thumb)
            self.store.update_video(slug, stage={"thumbnail": "auto"})

        meta = metadata.build(self.cfg, s, track, title=title)
        art.meta.write_text(
            json.dumps(meta.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = youtube.publish(
            self.cfg, self.store, art.video, meta,
            thumbnail=art.thumb, srt=art.srt, slot_index=slot_index,
            extra_playlists=topic_playlists(self.cfg, s.search_keyword or "", meta.title),
        )
        self.store.update_video(
            slug, status="uploaded", title=meta.title,
            youtube_id=result["video_id"], publish_at=result["publish_at"],
            stage={"url": result["url"]},
        )
        kept = finals.keep(self.cfg, final_name, art.video, art.thumb)
        log.info("[%s] 完成品を残しました: %s", slug, kept["video"])
        result.update({"final_name": final_name, "final_video": kept["video"]})
        return result

    def _publish_day(self, slot_index: int = 0) -> dt.date:
        """この本編が公開される日（JST）。サムネの日付名の照合に使う."""
        jst = dt.timezone(dt.timedelta(hours=9))
        try:
            return youtube.next_publish_time(self.cfg, slot_index).astimezone(jst).date()
        except Exception:
            return dt.datetime.now(jst).date()

    def set_thumbnail_later(self, image: Path, slug: str = "", day: dt.date | None = None,
                            video_id: str = "") -> dict[str, Any]:
        """あとから届いたサムネを、投稿済みの本編に付ける（未投稿なら thumbnails/ に置くだけ）."""
        rec = None
        if slug:
            rec = self.store.get_video(slug)
        elif video_id:
            rec = next((r for r in self.store.videos_by_status("uploaded") if r.youtube_id == video_id), None)
        elif day is not None:
            jst = dt.timezone(dt.timedelta(hours=9))
            for r in self.store.videos_by_status("uploaded"):
                if not r.publish_at or (r.stage or {}).get("kind") == "short":
                    continue
                try:
                    when = dt.datetime.fromisoformat(r.publish_at.replace("Z", "+00:00")).astimezone(jst).date()
                except ValueError:
                    continue
                if when == day:
                    rec = r
                    break
        dest = thumbnail.manual_dir(self.cfg) / thumbnail.name_for(day, rec.slug if rec else (slug or video_id))
        thumbnail.prepare(image, dest)
        out: dict[str, Any] = {"saved": str(dest)}
        if rec is None and video_id:
            # 状態 DB に無い動画（手で上げた回・古い回）でも、動画 ID が分かっていればそのまま付ける
            youtube.set_thumbnail(self.cfg, self.store, video_id, dest)
            out.update({"video_id": video_id, "applied": True})
        elif rec is not None and rec.youtube_id:
            art = self.art(rec.slug)
            thumbnail.prepare(image, art.thumb)
            youtube.set_thumbnail(self.cfg, self.store, rec.youtube_id, art.thumb)
            self.store.update_video(rec.slug, stage={"thumbnail": "manual", "thumbnail_file": dest.name})
            out.update({"slug": rec.slug, "video_id": rec.youtube_id, "applied": True})
        else:
            out["applied"] = False
        return out

    # --- Shorts -------------------------------------------------------------
    def stage_shorts(self, slug: str, s: script_mod.VideoScript, track: tts.VoiceTrack,
                     parent_url: str = "", upload: bool = True) -> list[dict[str, Any]]:
        """本編から Shorts を切り出し、（upload なら）Shorts 用の時刻で予約投稿する."""
        from . import shorts as shorts_mod
        art = self.art(slug)
        made = shorts_mod.build_all(self.cfg, s, track, art.dir, parent_url=parent_url)
        out = []
        for k, r in enumerate(made):
            if upload:
                try:
                    out.append(self.upload_short(slug, r, slot_index=k))
                except Exception as exc:          # Shorts の失敗で本編の結果は壊さない
                    log.error("Shorts %d の投稿に失敗: %s", k + 1, exc)
                    out.append({"video": r["video"], "error": str(exc)})
            else:
                out.append({"video": r["video"], "hook": r["window"].hook})
        return out

    def upload_short(self, parent_slug: str, made: dict[str, Any], slot_index: int = 0) -> dict[str, Any]:
        """Shorts 1 本を予約投稿し、DB に kind=short で記録する."""
        parent = self.store.get_video(parent_slug)
        n = int(Path(made["dir"]).name.split("_")[-1])
        slug = f"{parent_slug}-short{n}"
        if not self.store.get_video(slug):
            self.store.create_video(slug, parent.topic_id if parent else None, made["meta"].title)
        self.store.update_video(slug, stage={"kind": "short", "parent": parent_slug, "hook": made["window"].hook,
                                             "parent_url": (parent.stage or {}).get("url", "") if parent else ""})
        times = self.cfg.get("shorts.publish_times_jst") or None
        # 本編の公開より後の枠に入れる（先に出すと、概要欄の本編リンクが「非公開」になる）
        after = None
        if parent and parent.publish_at:
            try:
                after = dt.datetime.fromisoformat(str(parent.publish_at).replace("Z", "+00:00"))
            except ValueError:
                after = None
        result = youtube.publish(
            self.cfg, self.store, made["video"], made["meta"], thumbnail=None, srt=None,
            slot_index=slot_index, publish_times=times, playlist=False, after=after,
        )
        self.store.update_video(slug, status="uploaded", youtube_id=result["video_id"],
                                publish_at=result["publish_at"], stage={"url": result["url"]})
        return {"slug": slug, **result}

    # --- 1本ぶんの通し ---------------------------------------------------
    def produce(self, topic: topics_mod.Topic, slot_index: int = 0,
                upload: bool = True, slug: str | None = None) -> dict[str, Any]:
        # 再試行では同じ slug を渡す。台本・音声・動画は出来ている所から続きをやる（毎回最初から作り直さない）
        slug = slug or slugify(topic.title)
        fmt = str(self.cfg.get("pipeline.format", ""))
        if fmt == "docu":
            try:
                return self.produce_docu(topic, slot_index=slot_index, upload=upload, slug=slug)
            except Exception as exc:
                rec = self.store.get_video(slug)
                if (rec and rec.youtube_id) or not self.cfg.get("docu.fallback", True):
                    raise
                fmt = str(self.cfg.get("docu.fallback_format", "classic"))
                log.error("[%s] 新しい形（docu）で作れなかったので、今までの形（%s）で作ります: %s", slug, fmt, exc)
        if fmt == "honpen":
            return self.produce_honpen(topic, slot_index=slot_index, upload=upload, slug=slug)
        if self.store.get_video(slug) is None:
            self.store.create_video(slug, topic.id, topic.title)
        else:
            log.info("=== [%s] 前回の続きから再開します ===", slug)
        self.store.update_video(slug, horizon=topic.horizon)
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
            rec = self.store.get_video(slug)
            if rec and rec.youtube_id:
                # 本編はもう上がっている（後段の Shorts で失敗して再試行した）→ 二重に上げない
                log.info("[%s] 本編は投稿済み: %s", slug, rec.youtube_id)
                result.update({"video_id": rec.youtube_id, "publish_at": rec.publish_at,
                               "url": (rec.stage or {}).get("url", "")})
            elif upload and str(self.cfg.get("render.backend")) == "ffmpeg":
                result.update(self.stage_publish(slug, s, track, slot_index,
                                                 horizon=topic.horizon))
            else:
                log.info("[%s] アップロードはスキップしました", slug)
            if int(self.cfg.get("shorts.per_video", 0)) > 0:
                result["shorts"] = self.stage_shorts(slug, s, track, parent_url=result.get("url", ""),
                                                     upload=upload and str(self.cfg.get("render.backend")) == "ffmpeg")
            return result
        except Exception as exc:
            self.store.update_video(slug, status="failed",
                                    error=f"{exc}\n{traceback.format_exc()[-1500:]}")
            raise

    # --- 実話ドキュメンタリー型（docu）: 調べた材料 → AI が場面の台本 → 60fps の本編 → Shorts -----------------
    def _docu_synth(self):
        """docu の語り（VOICEVOX。話者と速さは docu.voice の設定）."""
        v = tts.VoiceVox(self.cfg)
        v.speaker = int(self.cfg.get("docu.voice.speaker", 11))
        v.speed = float(self.cfg.get("docu.voice.speed", 1.25))
        v.intonation = float(self.cfg.get("docu.voice.intonation", 1.2))
        return v.synth

    def _docu_render(self, spec: dict[str, Any], out: Path, work: Path, vertical: bool = False) -> Path:
        from . import docu, docu_media
        media = docu_media.prepare(spec, work.parent / "media", vertical=vertical,
                                   max_clips=int(self.cfg.get("docu.veo_max", 0)))
        docu_media.sfx_pack(self.cfg.root)
        bgm = docu_media.bgm(self.cfg.root, str(self.cfg.get("docu.bgm.file", "Mixkit - Close Up.mp3")),
                             str(self.cfg.get("docu.bgm.url", "https://assets.mixkit.co/music/1167/1167.mp3")))
        return docu.render(media, out, work, self._docu_synth(), vertical=vertical, bgm=bgm,
                           bgm_db=float(self.cfg.get("docu.bgm.db", -22)), fps=int(self.cfg.get("docu.fps", 60)))

    def _docu_material(self, slug: str, topic: topics_mod.Topic) -> tuple[dict[str, Any], str, Any, Any]:
        """(材料, 題, 経済の VideoScript か None, 心理学の本編 JSON か None)."""
        import dataclasses
        src = str(self.cfg.get("docu.source", "script"))
        art = self.art(slug)
        if src == "honpen":
            from . import honpen as honpen_mod
            hp = art.dir / "honpen.json"
            if hp.exists():
                data = json.loads(hp.read_text(encoding="utf-8"))
            else:
                tdict = dataclasses.asdict(topic) if dataclasses.is_dataclass(topic) else dict(topic)
                data = honpen_mod.write_honpen(self.cfg, tdict)
                hp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            ol = data.get("outline") or {}
            material = {"title": data.get("title"), "question": data.get("question"), "outline": ol,
                        "narration": [" ".join(str(t) for t, _ in (sc.get("narration") or [])) for sc in data.get("scenes") or []]}
            return material, str(data.get("title") or topic.title), None, data
        sc = self.stage_script(slug, topic)
        material = {"title": sc.topic_title, "search_keyword": sc.search_keyword, "hook": sc.hook, "proof": sc.proof,
                    "sections": [{"heading": x.heading, "narration": x.narration, "on_screen": x.on_screen} for x in sc.sections],
                    "closing": sc.closing, "research": sc.research, "sources": sc.sources,
                    "terms": [dataclasses.asdict(t) if dataclasses.is_dataclass(t) else t for t in sc.terms]}
        return material, sc.topic_title, sc, None

    def _docu_description(self, meta: Any, spec: dict[str, Any], chapters: list, with_chapters: bool) -> Any:
        """概要欄を docu 用に直す: もくじ・出典・引用した投稿・声と素材のクレジット."""
        import itertools

        from .metadata import MAX_DESCRIPTION, _voice_credit
        desc = meta.description
        old_voice = _voice_credit(self.cfg)
        sp = int(self.cfg.get("docu.voice.speaker", 11))
        names = {11: "玄野武宏", 13: "青山龍星", 2: "四国めたん（ノーマル）", 3: "ずんだもん（ノーマル）"}
        new_voice = ("この動画のナレーションは音声合成ソフト VOICEVOX を使用しています。\n"
                     f"VOICEVOX：{names.get(sp, f'話者ID {sp}')}\nhttps://voicevox.hiroshiba.jp/")
        desc = desc.replace(old_voice, new_voice)
        old_bgm = str(self.cfg.get("render.bgm.credit", "") or "").strip()
        if old_bgm:
            desc = desc.replace(old_bgm, "")
        parts = [desc.strip()]
        ch = [(float(t), str(n)) for t, n in chapters if n]
        if with_chapters and len(ch) >= 3 and all(b[0] - a[0] >= 10 for a, b in itertools.pairwise(ch)):
            parts.append("■ もくじ\n" + "\n".join(f"{int(t // 60)}:{int(t % 60):02d} {n}" for t, n in ch))
        srcs = list(dict.fromkeys(str(sc.get("source")).replace("出典：", "").replace("引用：", "")
                                  for sc in spec.get("scenes") or [] if sc.get("source")))
        if srcs:
            parts.append("■ この動画で使った数字・引用の出典\n" + "\n".join(f"・{x}" for x in srcs))
        quotes = []
        for sc in spec.get("scenes") or []:
            for it in [sc] + list(sc.get("items") or []):
                if isinstance(it, dict) and it.get("url") and it["url"] not in quotes:
                    quotes.append(it["url"])
        if quotes:
            parts.append("■ 引用した投稿\n" + "\n".join(quotes))
        parts.append("■ 素材\nBGM・効果音：Mixkit（https://mixkit.co/）")
        meta.description = "\n\n".join(p for p in parts if p.strip())[:MAX_DESCRIPTION]
        return meta

    def produce_docu(self, topic: topics_mod.Topic, slot_index: int = 0,
                     upload: bool = True, slug: str | None = None) -> dict[str, Any]:
        """材料 → docu の台本 → 本編（60fps）→ 投稿 → Shorts。どの段階も出来ていれば続きから."""
        from . import docu_script, finals
        slug = slug or slugify(topic.title)
        if self.store.get_video(slug) is None:
            self.store.create_video(slug, topic.id, topic.title)
        self.store.update_video(slug, horizon=topic.horizon, stage={"kind": "long", "format": "docu"})
        if topic.id:
            self.store.mark_topic_used(topic.id)
        art = self.art(slug)
        ddir = art.dir / "docu"
        ddir.mkdir(parents=True, exist_ok=True)
        log.info("=== [%s] %s（docu）===", slug, topic.title)
        try:
            material, title, s, honpen_data = self._docu_material(slug, topic)
            sp = ddir / "spec.json"
            if sp.exists():
                spec = json.loads(sp.read_text(encoding="utf-8"))
            else:
                cp = ddir / "candidates.json"
                if cp.exists():
                    cand = json.loads(cp.read_text(encoding="utf-8"))
                else:
                    cand = docu_script.tiktok_candidates(self.cfg, title, json.dumps(material, ensure_ascii=False)[:800])
                    cp.write_text(json.dumps(cand, ensure_ascii=False, indent=1), encoding="utf-8")
                spec = docu_script.make_spec(self.cfg, material, kind=str(self.cfg.get("docu.kind", "経済")),
                                             minutes=float(self.cfg.get("docu.minutes", 7)), candidates=cand, title=title)
                sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
            self.store.update_video(slug, status="scripted", stage={"docu_spec": str(sp)})
            if not art.video.exists():
                self._docu_render(spec, art.video, ddir / "work")
            chapters = json.loads((art.dir / "chapters.json").read_text(encoding="utf-8")) if (art.dir / "chapters.json").exists() else []
            self.store.update_video(slug, status="rendered", stage={"video": str(art.video)})
            result: dict[str, Any] = {"slug": slug, "title": title, "dir": str(art.dir)}

            rec = self.store.get_video(slug)
            if rec and rec.youtube_id:
                log.info("[%s] 本編は投稿済み: %s", slug, rec.youtube_id)
                result.update({"video_id": rec.youtube_id, "publish_at": rec.publish_at, "url": (rec.stage or {}).get("url", "")})
            elif upload:
                day = self._publish_day(slot_index)
                final_name = (rec.stage or {}).get("final_name") if rec else None
                if not final_name:
                    final_name = finals.assign(self.cfg, self.store, day)
                    self.store.update_video(slug, stage={"final_name": final_name})
                manual = thumbnail.pick_manual(self.cfg, slug, day, extra=[final_name])
                if s is not None:
                    vtitle, thumb_copy = metadata.choose_title(self.cfg, s, horizon=topic.horizon)
                    if thumb_copy.get("main"):
                        s.thumbnail_copy = thumb_copy
                        s.save(art.script)
                    if manual is not None:
                        thumbnail.prepare(manual, art.thumb)
                    else:
                        thumbnail.build(self.cfg, s, art.thumb)

                    class _NoBlocks:                       # docu はもくじを自分で作る（台本のブロックの時刻は使わない）
                        def block_span(self, _bid: str) -> tuple[float, float]:
                            return (0.0, 0.0)
                    meta = metadata.build(self.cfg, s, _NoBlocks(), title=vtitle)  # type: ignore[arg-type]
                    meta = self._docu_description(meta, spec, chapters, with_chapters=True)
                    playlists = topic_playlists(self.cfg, s.search_keyword or "", meta.title)
                else:
                    import dataclasses

                    from . import honpen as honpen_mod
                    if manual is not None:
                        thumbnail.prepare(manual, art.thumb)
                    else:
                        honpen_mod.thumbnail(self.cfg, honpen_data, art.thumb)
                    tdict = dataclasses.asdict(topic) if dataclasses.is_dataclass(topic) else dict(topic)
                    meta = honpen_mod.honpen_metadata(self.cfg, honpen_data, chapters, tdict)
                    meta = self._docu_description(meta, spec, chapters, with_chapters=False)
                    playlists = []
                art.meta.write_text(json.dumps(meta.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
                res = youtube.publish(self.cfg, self.store, art.video, meta, thumbnail=art.thumb,
                                      srt=art.dir / "subtitles.srt", slot_index=slot_index, extra_playlists=playlists)
                self.store.update_video(slug, status="uploaded", title=meta.title, youtube_id=res["video_id"],
                                        publish_at=res["publish_at"], stage={"url": res["url"]})
                kept = finals.keep(self.cfg, final_name, art.video, art.thumb)
                result.update(res)
                result.update({"final_name": final_name, "final_video": kept["video"]})
            else:
                log.info("[%s] アップロードはスキップしました", slug)

            n = int(self.cfg.get("shorts.per_video", 0))
            if n > 0:
                result["shorts"] = self._docu_shorts(slug, spec, n, parent_url=result.get("url", ""), upload=upload)
            return result
        except Exception as exc:
            self.store.update_video(slug, status="failed", error=f"{exc}\n{traceback.format_exc()[-1500:]}")
            raise

    def _docu_short_meta(self, ss: dict[str, Any], parent_url: str) -> Any:
        from . import domain
        from .metadata import Metadata, subscribe_line
        t = str(ss.get("title") or "").strip()
        title = (t if "#Shorts" in t else f"{t} #Shorts")[:100]
        first = next((ln for sc in ss.get("scenes") or [] for ln in sc.get("lines") or []), "")
        parts = [first]
        if parent_url:
            parts.append(f"▶ くわしい解説（本編）\n{parent_url}")
        parts.append(subscribe_line(self.cfg))
        srcs = list(dict.fromkeys(str(sc.get("source")).replace("出典：", "").replace("引用：", "")
                                  for sc in ss.get("scenes") or [] if sc.get("source")))
        if srcs:
            parts.append("■ 出典\n" + "\n".join(f"・{x}" for x in srcs))
        sp = int(self.cfg.get("docu.voice.speaker", 11))
        parts.append(f"VOICEVOX：{'玄野武宏' if sp == 11 else f'話者ID {sp}'} / BGM・効果音：Mixkit")
        parts.append(" ".join(domain.hashtags(self.cfg) + ["#Shorts"]))
        tags = [x.lstrip("#") for x in domain.hashtags(self.cfg)] + ["Shorts"]
        return Metadata(title=title, description="\n\n".join(p for p in parts if p)[:5000], tags=tags[:15],
                        category_id=str(self.cfg.get("upload.category_id", "25")), language=str(self.cfg.get("upload.language", "ja")))

    def _docu_shorts(self, parent_slug: str, spec: dict[str, Any], n: int,
                     parent_url: str = "", upload: bool = True) -> list[dict[str, Any]]:
        from . import docu_script
        art = self.art(parent_slug)
        parent = self.store.get_video(parent_slug)
        after = None
        if parent and parent.publish_at:
            try:
                after = dt.datetime.fromisoformat(str(parent.publish_at).replace("Z", "+00:00"))
            except ValueError:
                after = None
        times = self.cfg.get("shorts.publish_times_jst") or None
        sp = art.dir / "docu" / "shorts.json"
        try:
            if sp.exists():
                specs = json.loads(sp.read_text(encoding="utf-8"))
            else:
                specs = docu_script.make_shorts(self.cfg, spec, n, seconds=str(self.cfg.get("docu.shorts_seconds", "30〜40")))
                sp.write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:
            log.error("Shorts の台本を作れませんでした: %s", exc)
            return [{"error": str(exc)}]
        out = []
        for k, ss in enumerate(specs[:n]):
            qdir = art.dir / "shorts" / f"short_{k + 1:02d}"
            qdir.mkdir(parents=True, exist_ok=True)
            sslug = f"{parent_slug}-short{k + 1}"
            try:
                srec = self.store.get_video(sslug)
                if srec and srec.youtube_id:
                    out.append({"slug": sslug, "video_id": srec.youtube_id, "publish_at": srec.publish_at})
                    continue
                video = qdir / "video.mp4"
                if not video.exists():
                    self._docu_render(ss, video, qdir / "work", vertical=True)
                meta = self._docu_short_meta(ss, parent_url)
                (qdir / "metadata.json").write_text(json.dumps(meta.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
                if not upload:
                    out.append({"video": str(video), "title": meta.title})
                    continue
                if not srec:
                    self.store.create_video(sslug, parent.topic_id if parent else None, meta.title)
                self.store.update_video(sslug, stage={"kind": "short", "parent": parent_slug, "parent_url": parent_url, "format": "docu"})
                res = youtube.publish(self.cfg, self.store, video, meta, thumbnail=None, srt=None,
                                      slot_index=k, publish_times=times, playlist=False, after=after)
                self.store.update_video(sslug, status="uploaded", youtube_id=res["video_id"],
                                        publish_at=res["publish_at"], stage={"url": res["url"]})
                out.append({"slug": sslug, **res})
            except Exception as exc:                  # Shorts の失敗で本編の結果は壊さない
                log.error("Shorts %d の作成・投稿に失敗: %s", k + 1, exc)
                out.append({"error": str(exc)})
                if upload and (qdir / "video.mp4").exists():
                    self._keep_pending_short(sslug, parent_slug, k, qdir)
        return out

    # --- 心理学まくら: 寝落ち向け 30 分の本編 + 参加型テストの Shorts ---------------
    def produce_honpen(self, topic: topics_mod.Topic, slot_index: int = 0,
                       upload: bool = True, slug: str | None = None) -> dict[str, Any]:
        """台本（honpen.py）→ 16:9 の本編（quiz.build wide）→ 投稿 → 章から Shorts 3 本 → 本編の公開後に予約.

        どの段階も出来ていれば読み込んで続きから（再試行で作り直さない）。
        """
        import dataclasses

        from . import finals
        from . import honpen as honpen_mod
        from . import quiz as quiz_mod

        slug = slug or slugify(topic.title)
        if self.store.get_video(slug) is None:
            self.store.create_video(slug, topic.id, topic.title)
        self.store.update_video(slug, horizon=topic.horizon, stage={"kind": "long"})
        if topic.id:
            self.store.mark_topic_used(topic.id)
        log.info("=== [%s] %s（本編）===", slug, topic.title)
        art = self.art(slug)
        tdict = dataclasses.asdict(topic) if dataclasses.is_dataclass(topic) else dict(topic)
        try:
            hp = art.dir / "honpen.json"
            if hp.exists():
                data = json.loads(hp.read_text(encoding="utf-8"))
            else:
                tdict.setdefault("next_title", honpen_mod.next_title(self.cfg, self._publish_day(slot_index)))
                data = honpen_mod.write_honpen(self.cfg, tdict)
                hp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self.store.update_video(slug, status="scripted", title=data.get("title") or topic.title)

            chp = art.dir / "chapters.json"
            if not (art.video.exists() and chp.exists()):
                quiz_mod.build(self.cfg, data, art.dir, wide=True)
            chapters = json.loads(chp.read_text(encoding="utf-8"))
            self.store.update_video(slug, status="rendered", stage={"video": str(art.video)})
            result: dict[str, Any] = {"slug": slug, "title": data.get("title", ""), "dir": str(art.dir)}

            rec = self.store.get_video(slug)
            if rec and rec.youtube_id:
                log.info("[%s] 本編は投稿済み: %s", slug, rec.youtube_id)
                result.update({"video_id": rec.youtube_id, "publish_at": rec.publish_at, "url": (rec.stage or {}).get("url", "")})
            elif upload:
                day = self._publish_day(slot_index)
                final_name = (rec.stage or {}).get("final_name") if rec else None
                if not final_name:
                    final_name = finals.assign(self.cfg, self.store, day)
                    self.store.update_video(slug, stage={"final_name": final_name})
                manual = thumbnail.pick_manual(self.cfg, slug, day, extra=[final_name])
                if manual is not None:
                    log.info("[%s] 手で用意したサムネイルを使います: %s", slug, manual.name)
                    thumbnail.prepare(manual, art.thumb)
                else:
                    honpen_mod.thumbnail(self.cfg, data, art.thumb)
                meta = honpen_mod.honpen_metadata(self.cfg, data, chapters, tdict)
                art.meta.write_text(json.dumps(meta.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
                res = youtube.publish(self.cfg, self.store, art.video, meta, thumbnail=art.thumb,
                                      srt=art.dir / "subtitles.srt", slot_index=slot_index)
                self.store.update_video(slug, status="uploaded", title=meta.title, youtube_id=res["video_id"],
                                        publish_at=res["publish_at"], stage={"url": res["url"]})
                kept = finals.keep(self.cfg, final_name, art.video, art.thumb)
                result.update(res)
                result.update({"final_name": final_name, "final_video": kept["video"]})
            else:
                log.info("[%s] アップロードはスキップしました", slug)

            n = int(self.cfg.get("shorts.per_video", 0))
            if n > 0:
                result["shorts"] = self._honpen_shorts(slug, data, n, parent_url=result.get("url", ""), upload=upload)
            return result
        except Exception as exc:
            self.store.update_video(slug, status="failed", error=f"{exc}\n{traceback.format_exc()[-1500:]}")
            raise

    def _honpen_shorts(self, parent_slug: str, data: dict[str, Any], n: int,
                       parent_url: str = "", upload: bool = True) -> list[dict[str, Any]]:
        from . import honpen as honpen_mod
        from . import quiz as quiz_mod
        art = self.art(parent_slug)
        parent = self.store.get_video(parent_slug)
        after = None
        if parent and parent.publish_at:
            try:
                after = dt.datetime.fromisoformat(str(parent.publish_at).replace("Z", "+00:00"))
            except ValueError:
                after = None
        times = self.cfg.get("shorts.publish_times_jst") or None
        out = []
        for k, (theme, angle) in enumerate(honpen_mod.short_angles(data, n)):
            qdir = art.dir / "shorts" / f"short_{k + 1:02d}"
            qdir.mkdir(parents=True, exist_ok=True)
            sslug = f"{parent_slug}-short{k + 1}"
            try:
                srec = self.store.get_video(sslug)
                if srec and srec.youtube_id:
                    out.append({"slug": sslug, "video_id": srec.youtube_id, "publish_at": srec.publish_at})
                    continue
                qp = qdir / "quiz.json"
                if (qdir / "video.mp4").exists() and qp.exists():
                    q = json.loads(qp.read_text(encoding="utf-8"))
                else:
                    q = quiz_mod.write_quiz(self.cfg, theme, angle)
                    q = quiz_mod.build(self.cfg, q, qdir).quiz
                meta = quiz_mod.quiz_metadata(self.cfg, q, parent_url=parent_url)
                (qdir / "metadata.json").write_text(json.dumps(meta.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
                if not upload:
                    out.append({"video": str(qdir / "video.mp4"), "title": meta.title})
                    continue
                if not srec:
                    self.store.create_video(sslug, parent.topic_id if parent else None, meta.title)
                self.store.update_video(sslug, stage={"kind": "short", "parent": parent_slug, "parent_url": parent_url})
                res = youtube.publish(self.cfg, self.store, qdir / "video.mp4", meta, thumbnail=None,
                                      srt=None, slot_index=k, publish_times=times, playlist=False, after=after)
                self.store.update_video(sslug, status="uploaded", youtube_id=res["video_id"],
                                        publish_at=res["publish_at"], stage={"url": res["url"]})
                out.append({"slug": sslug, **res})
            except Exception as exc:              # Shorts の失敗で本編の結果は壊さない
                log.error("Shorts %d の作成・投稿に失敗: %s", k + 1, exc)
                out.append({"error": str(exc)})
                if upload and (qdir / "video.mp4").exists():
                    self._keep_pending_short(sslug, parent_slug, k, qdir)
        return out

    # --- 上げられなかった Shorts（1 日の上限など）を次の実行で上げ直す -----------------
    def _pending_dirs(self) -> list[Path]:
        """作業場所の pending_shorts（状態と一緒にキャッシュされる）と、リポジトリに置いた pending_shorts/<チャンネル>."""
        return [self.cfg.workdir / "pending_shorts", self.cfg.root / "pending_shorts" / self.cfg.channel_key]

    def _keep_pending_short(self, sslug: str, parent_slug: str, k: int, qdir: Path) -> None:
        import shutil
        d = self._pending_dirs()[0] / sslug
        try:
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(qdir / "video.mp4", d / "video.mp4")
            if (qdir / "metadata.json").exists():
                shutil.copy2(qdir / "metadata.json", d / "metadata.json")
            (d / "info.json").write_text(json.dumps({"slug": sslug, "parent": parent_slug, "slot": k}, ensure_ascii=False),
                                         encoding="utf-8")
            log.info("Shorts %s は次の実行で上げ直します（%s）", sslug, d)
        except OSError as exc:
            log.warning("上げ直し用に Shorts を残せませんでした: %s", exc)

    def upload_pending_shorts(self) -> int:
        """前の実行で上げられなかった Shorts を、本編の公開より後の枠で予約投稿する。上げた本数を返す."""
        import shutil

        from .metadata import Metadata
        times = self.cfg.get("shorts.publish_times_jst") or None
        n = 0
        for base in self._pending_dirs():
            for info_p in sorted(base.glob("*/info.json")):
                d = info_p.parent
                try:
                    info = json.loads(info_p.read_text(encoding="utf-8"))
                    sslug, parent_slug, k = str(info["slug"]), str(info["parent"]), int(info.get("slot", 0))
                    rec = self.store.get_video(sslug)
                    if rec and rec.youtube_id:
                        if base == self._pending_dirs()[0]:
                            shutil.rmtree(d, ignore_errors=True)
                        continue
                    parent = self.store.get_video(parent_slug)
                    after = None
                    if parent and parent.publish_at:
                        after = dt.datetime.fromisoformat(str(parent.publish_at).replace("Z", "+00:00"))
                    meta = Metadata(**json.loads((d / "metadata.json").read_text(encoding="utf-8")))
                    res = youtube.publish(self.cfg, self.store, d / "video.mp4", meta, thumbnail=None, srt=None,
                                          slot_index=k, publish_times=times, playlist=False, after=after)
                    if not rec:
                        self.store.create_video(sslug, parent.topic_id if parent else None, meta.title)
                    self.store.update_video(sslug, status="uploaded", youtube_id=res["video_id"], publish_at=res["publish_at"],
                                            stage={"kind": "short", "parent": parent_slug, "url": res["url"]})
                    log.info("上げ直した Shorts: %s %s（公開予定 %s）", sslug, res["url"], res["publish_at"])
                    n += 1
                    if base == self._pending_dirs()[0]:
                        shutil.rmtree(d, ignore_errors=True)
                except Exception as exc:                # 上限がまだなら次の実行でもう一度
                    log.warning("Shorts の上げ直しに失敗（次の実行で再挑戦）: %s %s", d.name, exc)
        return n

    # --- 当日分をまとめて ------------------------------------------------
    def scheduled_long_on(self, day: dt.date) -> list[Any]:
        """その日（JST）に公開予定・公開済みの本編（Shorts を除く）."""
        jst = dt.timezone(dt.timedelta(hours=9))
        out = []
        for r in self.store.videos_by_status("uploaded"):
            if not r.publish_at or (r.stage or {}).get("kind") == "short":
                continue
            try:
                when = dt.datetime.fromisoformat(r.publish_at.replace("Z", "+00:00")).astimezone(jst).date()
            except ValueError:
                continue
            if when == day:
                out.append(r)
        return out

    # --- 公開した動画に、要約を問いかけの形で 2 行のコメント ---------------------
    def post_pending_comments(self, now: dt.datetime | None = None) -> int:
        """公開済みで、まだ要約のコメントが無い動画（本編・Shorts）に付ける（comments.py）.

        予約中の動画にはコメントできないので、毎日の実行のはじめに拾い直す。以前のリンクのコメントは消す。
        """
        if not self.cfg.get("comments.summary", True):
            return 0
        from . import comments
        return comments.post_summaries(self.cfg, self.store, self.store.videos_by_status("uploaded"), now=now)

    def run_daily(self, count: int | None = None, upload: bool = True, force: bool = False) -> list[dict[str, Any]]:
        if upload:
            try:
                self.post_pending_comments()
            except Exception as exc:                 # コメントで本編づくりを止めない
                log.warning("コメントの投稿でエラー: %s", exc)
            try:
                self.upload_pending_shorts()
            except Exception as exc:
                log.warning("Shorts の上げ直しでエラー: %s", exc)
        count = count or int(self.cfg.get("pipeline.videos_per_day", 2))
        # 開始日より前は作らない（鍵を先に登録しておいても、初回の日までは投稿しない）
        start = str(self.cfg.get("pipeline.start_date", "") or "").strip()
        if upload and start:
            day = self._publish_day(0)
            if day < dt.date.fromisoformat(start):
                log.warning("%s は開始日 %s より前なので作りません（pipeline.start_date）", day, start)
                return [{"skipped": True, "title": f"開始日 {start} より前", "slug": "", "url": "", "publish_at": ""}]
        # 同じ日の本編がもう予約済みなら作らない（予約実行が遅れて手動実行と重なったときの二重投稿を防ぐ）
        if upload and not force:
            day = self._publish_day(0)
            already = self.scheduled_long_on(day)
            if already:
                log.warning("%s の本編はもう予約済みなので今回は作りません: %s（作るなら --force）",
                            day, ", ".join(r.slug for r in already))
                return [{"skipped": True, "title": f"{day} は予約済み", "slug": already[0].slug,
                         "url": (already[0].stage or {}).get("url", ""), "publish_at": already[0].publish_at}]
        chosen = topics_mod.select_topics(self.cfg, self.store, count)
        results = []
        retries = int(self.cfg.get("pipeline.retries", 2))
        for i, topic in enumerate(chosen):
            slug = slugify(topic.title)
            for attempt in range(retries + 1):
                try:
                    results.append(self.produce(topic, slot_index=i, upload=upload, slug=slug))
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
