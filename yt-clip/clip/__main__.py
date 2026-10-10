"""毎日の切り抜き: 場面を探す → 範囲とタイトルを決める → 縦型にする → 予約投稿.

    python -m clip                 # 今日の分（config の per_day 本）を作って予約投稿
    python -m clip --no-upload     # 作るだけ（out/ に mp4 が残る）
    python -m clip --count 1       # 本数を指定
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import random
import shutil
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from . import find, pick, render, sources, upload
from .state import State

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("clip")


def publish_times(cfg: dict[str, Any], day: dt.date, n: int) -> list[dt.datetime]:
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    times = cfg["schedule"]["publish_times"] or ["18:00"]
    out = []
    for i in range(n):
        hh, mm = map(int, times[min(i, len(times) - 1)].split(":"))
        out.append(dt.datetime.combine(day, dt.time(hh, mm), tz))
    return out


def gather_fresh(cfg: dict[str, Any], st: State, work: Path, need: int) -> list[dict[str, Any]]:
    got: list[dict[str, Any]] = []
    if need <= 0:
        return got
    excl = cfg["sources"].get("exclude_title_words", [])
    for src in cfg["sources"]["fresh"]:
        try:
            vids = sources.fresh_videos(src, excl)
        except Exception as e:  # noqa: BLE001
            log.warning("RSS を読めませんでした（%s）: %s", src["name"], e)
            continue
        for v in vids:
            already = len(st.used_ranges(v["id"]))
            want = v["per_video"] - already
            if want <= 0:
                continue
            try:
                cands = find.candidates(v, work / v["id"], cfg, want)
            except Exception as e:  # noqa: BLE001
                log.warning("場面を探せませんでした（%s）: %s", v["title"][:40], e)
                continue
            taken = 0
            for c in cands:
                if taken >= want or len(got) >= need * 2:
                    break
                if st.overlaps(v["id"], c["start"], c["end"], cfg["clip"]["gap_sec"] / 2):
                    continue
                d = pick.decide(cfg, c)
                if d and d["rating"] >= 4:
                    got.append(d)
                    taken += 1
    return got


def gather_archive(cfg: dict[str, Any], st: State, work: Path, need: int) -> list[dict[str, Any]]:
    a = cfg["sources"]["archive"]
    pool = st.data.get("archive_pool") or []
    built = st.data.get("archive_pool_at", "")
    if not pool or built < (dt.date.today() - dt.timedelta(days=7)).isoformat():
        try:
            pool = sources.archive_pool(a["channel_handle"], int(a["pool_size"]))
            st.data["archive_pool"], st.data["archive_pool_at"] = pool, dt.date.today().isoformat()
        except Exception as e:  # noqa: BLE001
            log.warning("アーカイブの一覧を取れませんでした: %s", e)
    done = set(st.data.get("archive_done", []))
    rest = [v for v in pool if v["id"] not in done
            and not any(w in v["title"] for w in cfg["sources"].get("exclude_title_words", []))]
    random.shuffle(rest)
    got: list[dict[str, Any]] = []
    for v in rest:
        if len(got) >= need:
            break
        v = {**v, "kind": "archive", "source_name": "マックスむらい", "per_video": 1, "show": "マックスむらい【切り抜き】"}
        try:
            cands = find.candidates(v, work / v["id"], cfg, 1)
        except Exception as e:  # noqa: BLE001
            log.warning("場面を探せませんでした（%s）: %s", v["title"][:40], e)
            st.data["archive_done"].append(v["id"])
            continue
        st.data["archive_done"].append(v["id"])      # アーカイブは 1 本につき 1 場面だけ
        for c in cands:
            d = pick.decide(cfg, c)
            if d and d["rating"] >= 4:
                got.append(d)
                break
    return got


def dress(clip: dict[str, Any]) -> dict[str, Any]:
    """画面に出す飾り（番組名・配信日・切り抜き元）を足す."""
    d = clip.get("upload_date") or ""
    label = ""
    if len(d) == 8:
        y, mo, da = int(d[:4]), int(d[4:6]), int(d[6:])
        this_year = dt.date.today().year
        label = f"配信日:{mo}月{da}日" if y == this_year else f"{y}年{mo}月の動画"
    return {**clip, "show": clip["video"].get("show"), "source_name": clip["video"].get("source_name"),
            "date_label": label}


def description(cfg: dict[str, Any], clip: dict[str, Any]) -> str:
    u = cfg["upload"]
    src = f"https://www.youtube.com/watch?v={clip['video']['id']}&t={int(clip['start'])}s"
    parts = [clip["hook"] or clip["title"], ""]
    if clip.get("is_investment"):
        parts += [u["investment_note"], ""]
    parts += [u["credit"].format(source_url=src).strip(), "", "#マックスむらい #切り抜き #shorts"]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--count", type=int)
    ap.add_argument("--state", default=str(ROOT / "state" / "state.json"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    st = State(Path(args.state))
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = dt.datetime.now(tz)
    # 18 時を過ぎてから動いた場合は翌日の分として予約する
    day = now.date() if now.time() < dt.time(17, 30) else now.date() + dt.timedelta(days=1)
    per_day = args.count or int(cfg["schedule"]["per_day"])
    need = per_day - (0 if args.no_upload else st.uploaded_on(day.isoformat()))
    if need <= 0:
        log.info("%s の分（%d 本）は予約済みです", day, per_day)
        return 0

    out = Path(args.out)
    work = out / "work"
    fonts = {"gothic": (ROOT / cfg["render"]["font_gothic"]).resolve()}

    token = None
    if not args.no_upload:
        prefix = cfg["channel"]["env_prefix"]
        token = upload.access_token(prefix)
        mine = upload.my_channel_id(token)
        if mine != cfg["channel"]["id"]:
            log.error("鍵のチャンネル（%s）が切り抜きチャンネル（%s）と違うので止めます", mine, cfg["channel"]["id"])
            return 2

    n_archive = min(need, int(cfg["sources"]["archive_per_day"]))
    fresh = gather_fresh(cfg, st, work, need - n_archive)
    fresh.sort(key=lambda c: (-c["rating"], -c["score"]))
    # 作れなかったときの予備も含めて並べ、必要な本数に届いたところで止める
    n_fresh = min(len(fresh), need - n_archive)
    archive = gather_archive(cfg, st, work, need - n_fresh + 2)
    chosen = fresh[:n_fresh] + archive + fresh[n_fresh:]
    st.save()
    log.info("今日の候補: 新しい配信 %d 本 + アーカイブ %d 本（必要 %d 本）", len(fresh), len(archive), need)
    if len(chosen) < need:
        log.warning("候補が %d 本しかありません（必要 %d 本）", len(chosen), need)

    times = publish_times(cfg, day, per_day)
    done_before = per_day - need
    made = 0
    for clip in chosen:
        if made >= need:
            break
        k = made
        name = f"{day.isoformat()}_{done_before + k + 1:02d}"
        try:
            src = work / clip["video"]["id"] / f"sec_{int(clip['start'])}.mp4"
            # 余白を付けて落とす（区間の頭と終わりが切れないように）。render には落とした頭の時刻を渡す
            dl0 = max(0.0, clip["start"] - 0.5)
            render.download_section(clip["video"]["url"], dl0, clip["end"] + 0.5, src, cfg["render"]["max_height"])
            clip = {**clip, "segments": clip.get("segments"), "dl_start": dl0}
            mp4 = render.render_short(dress(clip), src, out / f"{name}.mp4", fonts, cfg["render"]["fps"],
                                      proof=lambda lines, heard, title: pick.proofread(cfg, lines, heard, title))
        except Exception as e:  # noqa: BLE001
            log.warning("作れませんでした（%s）: %s", clip["title"], e)
            continue
        rec = {"day": day.isoformat(), "source": clip["video"]["id"], "start": clip["start"], "end": clip["end"],
               "title": clip["title"], "how": clip["how"], "kind": clip["video"].get("kind"), "llm": clip["llm"]}
        if token:
            meta = {"title": clip["title"], "description": description(cfg, clip), **cfg["upload"]}
            try:
                vid = upload.upload(token, mp4, meta, times[done_before + k])
                rec["youtube_id"] = vid
                log.info("予約しました %s %s → https://youtu.be/%s", times[done_before + k].strftime("%m/%d %H:%M"), clip["title"], vid)
            except Exception as e:  # noqa: BLE001
                log.warning("投稿できませんでした（%s）: %s", clip["title"], e)
                continue
        else:
            log.info("作りました %s ← %s %d〜%d秒（%d 区間・%d 秒）", mp4.name, clip["video"]["title"][:30], clip["start"], clip["end"],
                     len(clip.get("segments") or [1]), clip.get("length", clip["end"] - clip["start"]))
        st.add_clip(rec)
        st.save()
        made += 1
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    log.info("完了: %d / %d 本", made, need)
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
