"""コマンドラインインターフェース.

    python -m ytecon doctor          環境の点検（最初にこれ）
    python -m ytecon topics -n 2     話題だけ出す
    python -m ytecon run             当日分を作って投稿まで
    python -m ytecon run --no-upload 投稿せずローカルに mp4 だけ作る
    python -m ytecon resume <slug>   途中で落ちた回を再開
    python -m ytecon speakers        VOICEVOX の話者一覧
"""

from __future__ import annotations

import argparse
import os
import json
import logging
import shutil
import sys
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-16s %(message)s",
        datefmt="%H:%M:%S",
    )
    # 外部ライブラリの情報ログは自分のログを埋めるだけなので落とす
    for noisy in ("googleapiclient", "urllib3", "httpx", "httpx2",
                  "matplotlib", "matplotlib.font_manager", "PIL", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ----------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    """必要なものが揃っているかを一気に確認する."""
    from .config import load_config

    ok = True
    print("== yt-econ 環境チェック ==\n")

    try:
        cfg = load_config(args.config, channel=args.channel)
        print(f"[ok] 設定ファイル: {cfg.path}（チャンネル: {cfg.channel_key} / {cfg.get('channel.name', '')}）")
        lo, hi = cfg.target_chars
        print(f"     目標尺 {cfg.get('video.target_minutes_min')}〜"
              f"{cfg.get('video.target_minutes_max')}分 = 約{lo}〜{hi}文字")
    except Exception as exc:
        print(f"[NG] 設定の読み込みに失敗: {exc}")
        return 1

    # Python パッケージ
    for mod, why in [
        ("anthropic", "台本生成"), ("yaml", "設定"), ("requests", "HTTP"),
        ("PIL", "画像生成"), ("matplotlib", "図表"), ("feedparser", "RSS収集"),
        ("googleapiclient", "YouTubeアップロード"),
    ]:
        try:
            __import__(mod)
            print(f"[ok] {mod:16s} ({why})")
        except ImportError:
            ok = False
            print(f"[NG] {mod:16s} ({why}) → pip install -r requirements.txt")

    # ffmpeg（システム版が無ければ imageio-ffmpeg の同梱版に落ちる）
    try:
        from .render import ensure_ffmpeg

        path = ensure_ffmpeg()
        kind = "システム" if shutil.which("ffmpeg") else "同梱版"
        print(f"[ok] {'ffmpeg':16s} {path} ({kind})")
    except Exception as exc:
        if str(cfg.get("render.backend")) == "ffmpeg":
            ok = False
            print(f"[NG] {'ffmpeg':16s} {exc}")
        else:
            print(f"[--] {'ffmpeg':16s} (CapCut バックエンドなので任意)")

    if shutil.which("yt-dlp"):
        print(f"[ok] {'yt-dlp':16s} {shutil.which('yt-dlp')} (ytecon learn 用)")
    else:
        print(f"[--] {'yt-dlp':16s} 参照動画から語り口を学ぶなら pip install yt-dlp")

    if shutil.which("ffprobe"):
        print(f"[ok] {'ffprobe':16s} {shutil.which('ffprobe')}")
    else:
        print(f"[--] {'ffprobe':16s} 無くても ffmpeg から尺を読むので問題ありません")

    # フォント
    try:
        from .assets import font_path
        print(f"[ok] 日本語フォント     {font_path(cfg)}")
    except Exception as exc:
        ok = False
        print(f"[NG] 日本語フォント     {exc}")

    # LLM の経路（ここが費用を左右する）
    from .llm import _provider

    provider = _provider()
    if provider == "claude_code":
        print(f"[ok] {'LLM 経路':16s} claude -p（Claude Code の枠を使う）")
        print("     サブスク認証なら台本生成に追加の請求は出ません")
    else:
        print(f"[--] {'LLM 経路':16s} ANTHROPIC_API_KEY で直接（トークン従量課金）")
        print("     claude コマンドを入れると課金経路を避けられます")

    # 鍵類
    checks = [
        ("ANTHROPIC_API_KEY", False, "api 経路を使う場合のみ必要"),
        ("PEXELS_API_KEY", False, "無くても背景はグラデーションで出ます"),
        ("YOUTUBE_CLIENT_ID", False, "自動投稿するなら必要"),
        ("YOUTUBE_CLIENT_SECRET", False, "自動投稿するなら必要"),
        ("YOUTUBE_REFRESH_TOKEN", False, "scripts/auth_youtube.py で取得"),
    ]
    for key, required, note in checks:
        val = cfg.env(key)
        if val:
            print(f"[ok] {cfg.env_name(key):22s} 設定済み")
        elif required:
            ok = False
            print(f"[NG] {key:22s} 未設定 — {note}")
        else:
            print(f"[--] {key:22s} 未設定 — {note}")

    # TTS
    if str(cfg.get("tts.provider")) == "voicevox":
        import requests
        url = cfg.env("VOICEVOX_URL", "http://127.0.0.1:50021")
        try:
            r = requests.get(f"{url.rstrip('/')}/version", timeout=3)
            from .metadata import _VOICEVOX_SPEAKERS
            sid = int(cfg.get("tts.voicevox.speaker", 3))
            name = _VOICEVOX_SPEAKERS.get(sid, f"話者ID {sid}")
            print(f"[ok] VOICEVOX           {url} (v{r.text.strip()}) 話者: {name}")
            style = cfg.get("channel.speech_style", "plain")
            if sid in (1, 3, 5, 7, 22, 38) and style != "zundamon":
                print("     [注意] ずんだもんの声なのに speech_style が "
                      f"'{style}' です。台本が です・ます調 になり違和感が出ます")
        except Exception:
            ok = False
            print(f"[NG] VOICEVOX           {url} に繋がりません\n"
                  "     docker run --rm -p 50021:50021 "
                  "voicevox/voicevox_engine:cpu-ubuntu20.04-latest")

    print("\n" + ("すべて揃っています。`python -m ytecon run --no-upload` から試してください。"
                  if ok else "NG の項目を解消してから実行してください。"))
    return 0 if ok else 1


def cmd_topics(args: argparse.Namespace) -> int:
    from .config import load_config
    from .state import Store
    from .topics import select_topics

    cfg = load_config(args.config, channel=args.channel)
    store = Store(cfg.workdir / "state.sqlite3")
    for t in select_topics(cfg, store, args.number):
        print(f"\n■ {t.title}  (score {t.score:.0f} / {t.kind})")
        print(f"  切り口 : {t.angle}")
        if t.why_now:
            print(f"  今やる理由: {t.why_now}")
        for q in t.key_questions:
            print(f"  - {q}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .config import load_config
    from .pipeline import Pipeline

    cfg = load_config(args.config, channel=args.channel)
    pipe = Pipeline(cfg)
    results = pipe.run_daily(count=args.number, upload=not args.no_upload, force=args.force)
    print("\n== 結果 ==")
    failed = 0
    for r in results:
        if r.get("skipped"):
            print(f"  [省略] {r.get('title')}（{r.get('slug')} {r.get('url', '')}）")
        elif "error" in r:
            failed += 1
            print(f"  [失敗] {r.get('title','?')}: {r['error']}")
        else:
            print(f"  [完了] {r.get('title', r.get('slug'))}")
            if r.get("url"):
                print(f"         {r['url']}  公開予定 {r.get('publish_at')}")
            else:
                print(f"         {r.get('dir')}")
    return 1 if failed else 0


def cmd_resume(args: argparse.Namespace) -> int:
    from .config import load_config
    from .pipeline import Pipeline

    pipe = Pipeline(load_config(args.config, channel=args.channel))
    print(json.dumps(pipe.resume(args.slug, upload=not args.no_upload),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .config import load_config
    from .state import STATUSES, Store

    cfg = load_config(args.config, channel=args.channel)
    store = Store(cfg.workdir / "state.sqlite3")
    rows = store.videos_by_status(*STATUSES)
    if not rows:
        print("まだ1本も作っていません。")
        return 0
    for r in rows[-args.number:]:
        mark = {"uploaded": "✓", "failed": "×"}.get(r.status, "…")
        print(f"{mark} {r.slug:<40} {r.status:<10} {r.title}")
        if r.error:
            print(f"    {r.error.splitlines()[0]}")
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    """参照動画の字幕を実測して、語り口のプロファイルを作る."""
    from .config import load_config
    from .learn import learn, load_style

    cfg = load_config(args.config, channel=args.channel)
    out = learn(cfg, args.urls, out=Path(args.out) if args.out else None,
                lang=args.lang, deep=args.deep, per_channel=args.per_channel,
                measure_only=args.measure_only)

    # 書き出した先をそのまま読み直す（-o で既定以外に出した場合に備える）
    import yaml

    style = yaml.safe_load(out.read_text(encoding="utf-8")) or {}
    measured = style.get("measured", {}) or {}
    visual = style.get("visual", {}) or {}
    voice = style.get("voice", {}) or {}

    print(f"\n== 参照動画 {measured.get('videos', 0)} 本から実測しました ==\n")
    if measured.get("chars_per_minute"):
        print(f"  話速       : {measured['chars_per_minute']}文字/分"
              f"  (config の {cfg.get('video.chars_per_minute')} を上書きします)")
        print(f"  1文の長さ  : 平均 {measured.get('avg_sentence_chars')}字")
        print(f"  尺         : {measured.get('duration_minutes')}分")
        print(f"  導入       : {measured.get('hook_seconds')}秒")
        print(f"  チャプター : {measured.get('chapters')}個"
              f" / 1章 {measured.get('median_chapter_seconds')}秒")
    else:
        print("  （字幕が取れなかったので話速は測れていません）")

    if visual:
        print(f"\n  カット     : {visual.get('cuts_per_minute')}回/分"
              f"  1ショット {visual.get('median_shot_seconds')}秒")
        over = visual.get("shots_over_6s_ratio")
        if over is not None:
            print(f"  6秒超の割合: {over}"
                  + ("   ← 高いほど画が持っていない" if over and over > 0.5 else ""))
        colors = visual.get("dominant_colors") or []
        if colors:
            print("  支配色     : " + "  ".join(
                f"{c['hex']}({c['share']*100:.0f}%)" for c in colors[:5]))
        if visual.get("thumbnail_text_area_ratio") is not None:
            print(f"  サムネ文字量: {visual['thumbnail_text_area_ratio']}"
                  f"  鮮やかさ {visual.get('thumbnail_vivid_ratio')}")

    if voice.get("summary"):
        print(f"\n  語り口     : {voice['summary']}")
    if voice.get("opening_pattern"):
        print(f"  導入の型   : {voice['opening_pattern']}")
    if voice.get("what_not_to_copy"):
        print(f"\n  真似しないほうがいい点: {voice['what_not_to_copy']}")
    if not voice:
        print("\n  （文体の言語化は行っていません。--measure-only を外すと出ます）")

    print(f"\n  → {out}")
    if out.resolve() == (cfg.root / "config" / "style.yaml").resolve():
        print("  以降 `ytecon run` はこのプロファイルに寄せて台本を書きます。")
    else:
        print("  ※ 既定の場所ではないので、台本生成には反映されません。")
        print(f"     反映するには config/style.yaml に置いてください。")
    return 0


def cmd_thumbnail(args: argparse.Namespace) -> int:
    """手で作ったサムネを thumbnails/ に置き、投稿済みなら YouTube にも反映する."""
    import datetime as dt
    from pathlib import Path as _P
    from .config import load_config
    from .pipeline import Pipeline

    img = _P(args.image)
    if not img.exists():
        print(f"{img} がありません")
        return 1
    day = dt.date.fromisoformat(args.date) if args.date else None
    pipe = Pipeline(load_config(args.config, channel=args.channel))
    if args.no_upload:
        from . import thumbnail
        dest = thumbnail.manual_dir(pipe.cfg) / thumbnail.name_for(day, args.slug or "")
        thumbnail.prepare(img, dest)
        print(json.dumps({"saved": str(dest), "applied": False}, ensure_ascii=False))
        return 0
    print(json.dumps(pipe.set_thumbnail_later(img, slug=args.slug or "", day=day, video_id=args.video_id or ""),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_publish_file(args: argparse.Namespace) -> int:
    """手で仕上げた完成ファイル（yt_001_20260922.mp4 など）を投稿する。既定は非公開."""
    from .config import load_config
    from .state import Store
    from . import finals

    cfg = load_config(args.config, channel=args.channel)
    store = Store(cfg.workdir / "state.sqlite3")
    name = args.name or Path(args.source).stem
    result = finals.publish_file(
        cfg, store, args.source, name, privacy=args.privacy,
        publish_at=finals.parse_jst(args.publish_at), title=args.title,
        thumbnail=args.thumbnail, dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_visibility(args: argparse.Namespace) -> int:
    """非公開で上げた動画を公開（または予約公開）にする."""
    from .config import load_config
    from .state import Store
    from . import finals

    cfg = load_config(args.config, channel=args.channel)
    store = Store(cfg.workdir / "state.sqlite3")
    print(json.dumps(finals.set_visibility(cfg, store, args.target, args.privacy,
                                           publish_at=finals.parse_jst(args.publish_at)),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_revive(args: argparse.Namespace) -> int:
    """寝かせた動画のテーマが日本で話題化していないか照合する."""
    from .config import load_config
    from .revive import format_report, run
    from .state import Store

    cfg = load_config(args.config, channel=args.channel)
    if not cfg.get("revive.enabled", True):
        print("revive.enabled が false です")
        return 0
    store = Store(cfg.workdir / "state.sqlite3")
    apply = args.apply or bool(cfg.get("revive.auto_apply", False))
    hits = run(cfg, store, apply=apply)
    print(format_report(hits))
    if hits and not apply:
        print("\n反映するには --apply を付けて実行してください。")
    return 0


def cmd_portfolio(args: argparse.Namespace) -> int:
    """flow / bridge / stock の偏りと、掘り起こし待ちの在庫を見る."""
    from .config import load_config
    from .state import Store

    cfg = load_config(args.config, channel=args.channel)
    store = Store(cfg.workdir / "state.sqlite3")

    window = int(cfg.get("topics.horizon_window_days", 30))
    recent = store.horizon_counts(window)
    lifetime = store.horizon_counts(3650)
    total = sum(lifetime.values())

    label = {"flow": "flow   いま刺さる", "bridge": "bridge 半年以内に来る",
             "stock": "stock  先行仕込み"}
    print(f"== 企画の内訳 ==\n")
    print(f"{'':22s} 直近{window}日   累計")
    for h in ("flow", "bridge", "stock"):
        share = f"{lifetime[h] / total * 100:.0f}%" if total else "-"
        print(f"{label[h]:22s} {recent[h]:>5d}本 {lifetime[h]:>6d}本 ({share})")

    watch = store.watchlist()
    print(f"\n== 掘り起こし待ち {len(watch)}本 ==")
    if not watch:
        print("  まだありません（stock/bridge を公開すると貯まります）")
    for item in watch[:20]:
        mark = "済" if item["revived_at"] else "  "
        print(f"  {mark} {item['title'][:34]:<34} 監視語: {'、'.join(item['keywords'][:3])}")
    return 0


def cmd_goal(args: argparse.Namespace) -> int:
    from .config import load_config
    from . import goals
    from .state import Store

    cfg = load_config(args.config, channel=args.channel)
    goal = goals.load_goal(cfg)
    if args.offline:
        print(goals.plan_text(goal))
        return 0
    store = Store(cfg.workdir / "state.sqlite3")
    try:
        p = goals.fetch_progress(cfg, store, goal)
    except Exception as exc:
        print(goals.plan_text(goal))
        print(f"\n数字が取れませんでした（{str(exc).splitlines()[0][:160]}）。"
              "\nYOUTUBE_* を設定し、scripts/auth_youtube.py を再実行すると Analytics まで取れます。")
        return 1
    goals.daily_from_snapshots(cfg, p)
    pc = goals.pace(goal, p)
    acts = goals.recommend(goal, p, pc)
    goals.append_snapshot(cfg, p, pc)
    if args.json:
        print(json.dumps({"progress": p.to_dict(), "pace": pc, "actions": acts}, ensure_ascii=False, indent=2))
    else:
        print(goals.report(goal, p, pc, acts))
    return 0


def cmd_shorts(args: argparse.Namespace) -> int:
    """本編の成果物フォルダ（script.json / narration.json / narration.wav）から Shorts を作る."""
    from pathlib import Path as _P
    from .config import load_config
    from .script import VideoScript
    from .tts import VoiceTrack
    from . import shorts

    cfg = load_config(args.config, channel=args.channel)
    d = _P(args.target)
    if not d.is_dir():
        d = cfg.workdir / args.target
    if not (d / "script.json").exists():
        print(f"{d} に script.json がありません")
        return 1
    s = VideoScript.load(d / "script.json")
    t = VoiceTrack.load_manifest(d / "narration.json")
    if not _P(t.wav_path).exists():
        t.wav_path = d / "narration.wav"
    if args.list:
        for w in shorts.candidates(cfg, s, t, n=args.number):
            print(f"{w.start:7.1f}s  {w.duration:4.0f}s  {w.score:5.1f}  {w.hook}")
            print(f"           {w.lines[0].text} … {w.lines[-1].text}")
        return 0
    results = shorts.build_all(cfg, s, t, d, n=args.number, parent_url=args.parent_url or "")
    for r in results:
        print(f"[完了] {r['video']}  ({r['window'].duration:.0f}s / {r['window'].hook})")
    if args.upload and results:
        from .pipeline import Pipeline
        pipe = Pipeline(cfg)
        for k, r in enumerate(results):
            print(json.dumps(pipe.upload_short(d.name, r, slot_index=k), ensure_ascii=False))
    return 0 if results else 1


def cmd_speakers(args: argparse.Namespace) -> int:
    from .config import load_config
    from .tts import VoiceVox

    cfg = load_config(args.config, channel=args.channel)
    for sp in VoiceVox(cfg).speakers():
        for style in sp.get("styles", []):
            print(f"{style['id']:>4}  {sp['name']} / {style['name']}")
    return 0


def cmd_script(args: argparse.Namespace) -> int:
    """話題を1つ指定して台本だけ作る（試し書き用）."""
    from .config import load_config
    from .script import generate
    from .topics import Topic

    cfg = load_config(args.config, channel=args.channel)
    topic = Topic(title=args.title, angle=args.angle or "", kind="evergreen")
    s = generate(cfg, topic)
    out = Path(args.out) if args.out else cfg.workdir / "draft_script.json"
    s.save(out)
    print(f"{s.total_chars}文字 / {len(s.sections)}セクション → {out}")
    for i, sec in enumerate(s.sections):
        print(f"  {i+1}. {sec.heading} ({sec.char_count}字, visual={sec.visual.kind})")
    return 0


# ----------------------------------------------------------------------
def cmd_quiz(args: argparse.Namespace) -> int:
    """参加型テストの Shorts を 1 本作る（心理学チャンネルの型）。--json で人が書いた台本からも作れる."""
    import json as _json
    from .config import load_config
    from . import quiz as quiz_mod
    from .pipeline import slugify

    cfg = load_config(args.config, channel=args.channel)
    if args.json:
        q = _json.loads(Path(args.json).read_text(encoding="utf-8"))
        topic = q.get("title") or Path(args.json).stem
    else:
        if not args.topic:
            print("テーマ（または --json）を指定してください")
            return 1
        q = quiz_mod.write_quiz(cfg, args.topic, args.angle or "")
        topic = args.topic
    outdir = Path(args.out) if args.out else cfg.workdir / "quiz" / slugify(topic)
    outdir.mkdir(parents=True, exist_ok=True)
    built = quiz_mod.build(cfg, q, outdir)
    meta = quiz_mod.quiz_metadata(cfg, built.quiz, parent_url=args.parent_url or "")
    (outdir / "metadata.json").write_text(_json.dumps(meta.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[完了] {built.video}  ({built.seconds:.0f} 秒)")
    print(f"タイトル: {meta.title}")
    if args.upload:
        from . import youtube
        from .state import Store
        store = Store(cfg.workdir / "state.sqlite3")
        times = cfg.get("shorts.publish_times_jst") or None
        res = youtube.publish(cfg, store, built.video, meta, thumbnail=None, srt=None,
                              slot_index=args.slot, publish_times=times, playlist=False)
        print(f"投稿: {res['url']}  公開 {res['publish_at']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytecon", description="経済解説YouTubeチャンネルの自動運用")
    parser.add_argument("-c", "--config", help="設定ファイルのパス")
    parser.add_argument("--channel", default=os.environ.get("YTECON_CHANNEL", ""),
                        help="チャンネルの識別子（config/channels/<key>/channel.yaml）。省略で本体。環境変数 YTECON_CHANNEL でも可")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="環境チェック")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("topics", help="話題を選ぶだけ")
    p.add_argument("-n", "--number", type=int, default=2)
    p.set_defaults(func=cmd_topics)

    p = sub.add_parser("script", help="台本を1本だけ書く")
    p.add_argument("title", help="扱うテーマ")
    p.add_argument("-a", "--angle", help="切り口")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_script)

    p = sub.add_parser("learn", help="参照動画の語り口を実測して取り込む")
    p.add_argument("urls", nargs="+", help="参考にしたい動画のURL（複数可）")
    p.add_argument("-o", "--out", help="出力先（既定 config/style.yaml）")
    p.add_argument("--lang", default="ja", help="字幕の言語（既定 ja）")
    p.add_argument("--deep", action="store_true",
                   help="映像も落としてカット頻度・配色まで測る（低画質・時間がかかる）")
    p.add_argument("--per-channel", type=int, default=5,
                   help="チャンネルURLを渡したとき、何本さかのぼるか（既定5）")
    p.add_argument("--measure-only", action="store_true",
                   help="実測だけ行い、文体の言語化（LLM）は行わない。まず数字を見たいとき")
    p.set_defaults(func=cmd_learn)

    p = sub.add_parser("run", help="当日分を作って投稿する")
    p.add_argument("-n", "--number", type=int, default=None)
    p.add_argument("--no-upload", action="store_true", help="投稿せず mp4 まで")
    p.add_argument("--force", action="store_true", help="同じ日の本編が予約済みでも作る")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="途中で落ちた回を再開")
    p.add_argument("slug")
    p.add_argument("--no-upload", action="store_true")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("status", help="進行状況一覧")
    p.add_argument("-n", "--number", type=int, default=20)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("revive", help="寝かせた動画が日本で話題化したか照合する")
    p.add_argument("--apply", action="store_true",
                   help="タイトル・サムネ・概要欄を YouTube に反映する")
    p.set_defaults(func=cmd_revive)

    p = sub.add_parser("portfolio", help="flow/bridge/stock の偏りと在庫を見る")
    p.set_defaults(func=cmd_portfolio)

    p = sub.add_parser("quiz", help="参加型テストの Shorts を 1 本作る（--channel psych）")
    p.add_argument("topic", nargs="?", help="テーマ")
    p.add_argument("-a", "--angle", help="切り口")
    p.add_argument("--json", help="人が書いた台本 JSON から作る")
    p.add_argument("-o", "--out", help="出力先フォルダ")
    p.add_argument("--parent-url", help="概要欄に入れる本編の URL")
    p.add_argument("--upload", action="store_true", help="YouTube に予約投稿する")
    p.add_argument("--slot", type=int, default=0, help="投稿枠（shorts.publish_times_jst の何番目か）")
    p.set_defaults(func=cmd_quiz)

    p = sub.add_parser("speakers", help="VOICEVOX の話者一覧")
    p.set_defaults(func=cmd_speakers)

    p = sub.add_parser("thumbnail", help="手で作ったサムネを thumbnails/ に置く（投稿済みなら YouTube にも反映）")
    p.add_argument("image", help="画像ファイル（png/jpg/webp。1280x720 に自動で整える）")
    p.add_argument("--date", help="その本編の公開日（YYYY-MM-DD）。ファイル名になり、投稿済みならその日の本編に付く")
    p.add_argument("--slug", help="output/<slug> を直接指定")
    p.add_argument("--video-id", help="YouTube の動画 ID を直接指定")
    p.add_argument("--no-upload", action="store_true", help="thumbnails/ に置くだけ（YouTube には触らない）")
    p.set_defaults(func=cmd_thumbnail)

    p = sub.add_parser("publish-file", help="手で仕上げた完成ファイル（yt_001_20260922.mp4）を投稿する。既定は非公開")
    p.add_argument("source", help="mp4 のパスか URL")
    p.add_argument("--name", help="yt_001_20260922 の形の名前（省略時はファイル名）。finals/<名前>.json のメタデータを使う")
    p.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    p.add_argument("--publish-at", help="予約公開の日時（JST、例 2026-09-23 19:00）。--privacy public のときだけ")
    p.add_argument("--title", help="タイトルを上書き")
    p.add_argument("--thumbnail", help="サムネ画像（省略時は thumbnails/<名前>.jpg）")
    p.add_argument("--dry-run", action="store_true", help="アップロードせず、何を上げるかだけ表示")
    p.set_defaults(func=cmd_publish_file)

    p = sub.add_parser("visibility", help="投稿済みの動画を公開 / 非公開 / 限定公開にする")
    p.add_argument("target", help="yt_001_20260922 か YouTube の動画 ID")
    p.add_argument("privacy", choices=["public", "private", "unlisted"])
    p.add_argument("--publish-at", help="この日時に公開予約（JST、例 2026-09-23 19:00）")
    p.set_defaults(func=cmd_visibility)

    p = sub.add_parser("goal", help="目標（config/goals.yaml）への進捗と打ち手")
    p.add_argument("--offline", action="store_true", help="API を叩かず、目標の分解だけ表示")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_goal)

    p = sub.add_parser("shorts", help="本編の成果物から Shorts（縦 9:16）を切り出す")
    p.add_argument("target", help="output/<slug> か、script.json のあるフォルダ")
    p.add_argument("-n", "--number", type=int, default=None, help="本数（既定 shorts.per_video）")
    p.add_argument("--list", action="store_true", help="候補区間を表示するだけ")
    p.add_argument("--upload", action="store_true", help="作った Shorts を予約投稿する")
    p.add_argument("--parent-url", help="概要欄に入れる本編の URL")
    p.set_defaults(func=cmd_shorts)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n中断しました", file=sys.stderr)
        return 130
    except Exception as exc:
        logging.getLogger("ytecon").error("%s", exc)
        if args.verbose:
            raise
        print("\n詳しい原因を見るには -v を付けて再実行してください。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
