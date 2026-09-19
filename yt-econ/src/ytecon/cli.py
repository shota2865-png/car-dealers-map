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
        cfg = load_config(args.config)
        print(f"[ok] 設定ファイル: {cfg.root / 'config' / 'channel.yaml'}")
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

    # 鍵類
    checks = [
        ("ANTHROPIC_API_KEY", True, "台本生成に必須"),
        ("PEXELS_API_KEY", False, "無くても背景はグラデーションで出ます"),
        ("YOUTUBE_CLIENT_ID", False, "自動投稿するなら必要"),
        ("YOUTUBE_CLIENT_SECRET", False, "自動投稿するなら必要"),
        ("YOUTUBE_REFRESH_TOKEN", False, "scripts/auth_youtube.py で取得"),
    ]
    for key, required, note in checks:
        val = cfg.env(key)
        if val:
            print(f"[ok] {key:22s} 設定済み")
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
            print(f"[ok] VOICEVOX           {url} (v{r.text.strip()})")
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

    cfg = load_config(args.config)
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

    cfg = load_config(args.config)
    pipe = Pipeline(cfg)
    results = pipe.run_daily(count=args.number, upload=not args.no_upload)
    print("\n== 結果 ==")
    failed = 0
    for r in results:
        if "error" in r:
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

    pipe = Pipeline(load_config(args.config))
    print(json.dumps(pipe.resume(args.slug, upload=not args.no_upload),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .config import load_config
    from .state import STATUSES, Store

    cfg = load_config(args.config)
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


def cmd_revive(args: argparse.Namespace) -> int:
    """寝かせた動画のテーマが日本で話題化していないか照合する."""
    from .config import load_config
    from .revive import format_report, run
    from .state import Store

    cfg = load_config(args.config)
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

    cfg = load_config(args.config)
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


def cmd_speakers(args: argparse.Namespace) -> int:
    from .config import load_config
    from .tts import VoiceVox

    cfg = load_config(args.config)
    for sp in VoiceVox(cfg).speakers():
        for style in sp.get("styles", []):
            print(f"{style['id']:>4}  {sp['name']} / {style['name']}")
    return 0


def cmd_script(args: argparse.Namespace) -> int:
    """話題を1つ指定して台本だけ作る（試し書き用）."""
    from .config import load_config
    from .script import generate
    from .topics import Topic

    cfg = load_config(args.config)
    topic = Topic(title=args.title, angle=args.angle or "", kind="evergreen")
    s = generate(cfg, topic)
    out = Path(args.out) if args.out else cfg.workdir / "draft_script.json"
    s.save(out)
    print(f"{s.total_chars}文字 / {len(s.sections)}セクション → {out}")
    for i, sec in enumerate(s.sections):
        print(f"  {i+1}. {sec.heading} ({sec.char_count}字, visual={sec.visual.kind})")
    return 0


# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytecon", description="経済解説YouTubeチャンネルの自動運用")
    parser.add_argument("-c", "--config", help="設定ファイルのパス")
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

    p = sub.add_parser("run", help="当日分を作って投稿する")
    p.add_argument("-n", "--number", type=int, default=None)
    p.add_argument("--no-upload", action="store_true", help="投稿せず mp4 まで")
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

    p = sub.add_parser("speakers", help="VOICEVOX の話者一覧")
    p.set_defaults(func=cmd_speakers)

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
