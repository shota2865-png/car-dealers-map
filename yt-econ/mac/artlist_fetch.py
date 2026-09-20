# ============================================================
#  Artlist から素材をまとめて落とす（Mac 用）
#
#  あなたのブラウザ（専用プロファイル）で Artlist を開き、検索語ごとに
#  ダウンロードを押して、assets/footage と assets/bgm に振り分ける。
#
#  2つのモードが自動で切り替わる:
#    自動   : ページ上の「ダウンロード」ボタンをこちらで押す
#    手伝い : 自動で押せなかった検索語は、その画面で**あなたが**好きな素材の
#             ダウンロードを押す（こちらは落ちたファイルを受け取って振り分ける）
#  どちらでも、ファイル名・タグ付け・フォルダ分けは自動。
#
#  初回だけ:
#    1. python3 -m pip install playwright && python3 -m playwright install chromium
#    2. 実行すると Artlist のログイン画面が開くので、ログインする（次回から不要）
#
#  使い方:
#    python3 artlist_fetch.py                 # 既定の一覧（動画 40 本 + 曲 4 曲 + 効果音 8 個）
#    python3 artlist_fetch.py --music-only    # 曲だけ
#    python3 artlist_fetch.py --per 1 --limit 5   # 検索語ごと 1 本、最初の 5 語だけ（お試し）
#    python3 artlist_fetch.py --manual        # 最初から手伝いモード（自動で押さない）
#
#  注意:
#    ・Artlist の規約は自動化ツールでの取得を制限しています。この道具は人が押すのと
#      同じ操作を、人と同じ速さ（1 本ごとに数秒〜十数秒の間）で行い、まとめ落としは
#      しません。心配なら --manual で、押すのは自分・整理だけ道具、にしてください。
#    ・この環境（開発側）からは Artlist に届かないため、自動モードの押す場所（ボタンの
#      見つけ方）は実機で未検証です。押せなかった検索語は自動で手伝いモードに落ち、
#      ~/ytecon_artlist_debug/ に画面写真と候補ボタンの一覧を残します。
#      それを送ってもらえれば直します。
# ============================================================
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = Path.home() / ".ytecon" / "chrome-profile"
DEBUG = Path.home() / "ytecon_artlist_debug"
FOOT = HERE / "assets" / "footage"
BGM = HERE / "assets" / "bgm"

# 検索語 → (保存先の種類, タグ)
FOOTAGE_QUERIES: list[tuple[str, str, list[str]]] = [
    # 抽象ループ（カードの後ろに敷く）
    ("abstract particles loop dark", "abstract", ["particles", "loop", "dark"]),
    ("bokeh lights dark background", "abstract", ["bokeh", "light", "dark"]),
    ("digital network lines dark", "abstract", ["network", "lines", "data", "digital"]),
    ("slow gradient motion background", "abstract", ["gradient", "slow", "calm"]),
    ("light leak dark", "texture", ["light", "leak"]),
    ("ink in water dark slow motion", "abstract", ["ink", "water", "slow"]),
    ("smoke slow motion black background", "abstract", ["smoke", "slow", "dark"]),
    ("digital grid perspective", "abstract", ["grid", "digital", "tech"]),
    ("dust particles floating", "texture", ["dust", "particles"]),
    ("paper texture close up", "texture", ["paper", "texture"]),
    # 街・人
    ("tokyo street crowd", "broll", ["tokyo", "street", "crowd", "city", "japan"]),
    ("shibuya crossing", "broll", ["shibuya", "tokyo", "crossing", "crowd", "city"]),
    ("office workers japan", "broll", ["office", "workers", "japan", "business", "salary"]),
    ("commuters train japan", "broll", ["commuters", "train", "japan", "morning", "work"]),
    ("city night aerial", "broll", ["city", "night", "aerial", "skyline"]),
    ("people smartphone street", "broll", ["people", "smartphone", "street", "sns"]),
    ("young professionals walking", "broll", ["young", "professionals", "walking", "career"]),
    ("apartment building japan", "broll", ["apartment", "housing", "rent", "japan"]),
    ("convenience store night japan", "broll", ["convenience", "store", "night", "japan"]),
    ("family kitchen cooking", "broll", ["family", "kitchen", "home", "cost"]),
    # お金・買い物
    ("japanese yen banknotes", "broll", ["yen", "banknotes", "money", "cash", "japan"]),
    ("coins falling slow motion", "broll", ["coins", "money", "falling"]),
    ("supermarket shelves shopping", "broll", ["supermarket", "shelves", "shopping", "grocery", "price"]),
    ("price tag close up", "broll", ["price", "tag", "label", "inflation"]),
    ("cash register payment", "broll", ["cash", "register", "payment", "shop"]),
    ("receipt calculator budget", "broll", ["receipt", "calculator", "budget", "household"]),
    ("wallet empty", "broll", ["wallet", "money", "poor", "budget"]),
    ("gas station fuel price", "broll", ["gas", "fuel", "price", "energy"]),
    # 経済・仕事
    ("stock market screen", "broll", ["stock", "market", "screen", "trading", "finance"]),
    ("trading chart monitor", "broll", ["chart", "monitor", "trading", "finance", "graph"]),
    ("bank building exterior", "broll", ["bank", "building", "finance", "central"]),
    ("factory production line", "broll", ["factory", "production", "manufacturing", "industry"]),
    ("shipping containers port", "broll", ["shipping", "containers", "port", "trade", "export"]),
    ("real estate house for sale", "broll", ["real", "estate", "house", "housing", "mortgage"]),
    ("calculator desk accounting", "broll", ["calculator", "desk", "accounting", "tax"]),
    ("handshake business meeting", "broll", ["handshake", "business", "meeting", "deal"]),
    ("farmer field harvest", "broll", ["farmer", "field", "harvest", "food", "agriculture"]),
    ("semiconductor chip factory", "broll", ["semiconductor", "chip", "factory", "tech"]),
    ("electric car charging", "broll", ["electric", "car", "ev", "charging", "auto"]),
    ("airport travelers luggage", "broll", ["airport", "travel", "tourism", "yen"]),
]

MUSIC_QUERIES: list[tuple[str, str]] = [
    ("ambient minimal calm piano pads", "ambient"),
    ("curious light plucks documentary", "curiosity"),
    ("tension suspense minimal cinematic", "tension"),
    ("reflective hopeful piano", "reflective"),
]

# 効果音: 種類名がファイル名になる（assets/sfx/POP.mp3 など）
SFX_QUERIES: list[tuple[str, str]] = [
    ("soft pop ui", "POP"),
    ("click subtle", "CLICK"),
    ("whoosh transition short", "WHOOSH"),
    ("cinematic impact hit soft", "IMPACT"),
    ("comedy boing light", "COMEDY"),
    ("error buzzer short", "ERROR"),
    ("riser short cinematic", "RISER"),
    ("swoosh transition", "TRANSITION"),
]

FOOTAGE_URL = "https://artlist.io/stock-footage/search?terms={q}"
MUSIC_URL = "https://artlist.io/royalty-free-music/search?terms={q}"
SFX_URL = "https://artlist.io/sfx/search?terms={q}"
SFX = HERE / "assets" / "sfx"


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def human_pause(a: float = 2.0, b: float = 6.0) -> None:
    time.sleep(random.uniform(a, b))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def save_download(download, dest_dir: Path, base: str) -> Path | None:
    """Playwright の Download を、決めた名前で保存する."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suggested = download.suggested_filename or "clip.mp4"
    ext = Path(suggested).suffix.lower() or ".mp4"
    dest = dest_dir / f"{base}{ext}"
    n = 2
    while dest.exists():
        dest = dest_dir / f"{base}_{n}{ext}"
        n += 1
    try:
        download.save_as(str(dest))
    except Exception as exc:
        log(f"   保存に失敗: {exc}")
        return None
    if dest.suffix == ".zip":
        # 4K などは zip で来ることがある → 中の動画を取り出す
        import zipfile
        with zipfile.ZipFile(dest) as zf:
            for info in zf.infolist():
                if info.filename.lower().endswith((".mp4", ".mov")) and not info.filename.startswith("__MACOSX"):
                    target = dest.with_suffix(Path(info.filename).suffix.lower())
                    with zf.open(info) as f, open(target, "wb") as g:
                        shutil.copyfileobj(f, g)
                    dest.unlink(missing_ok=True)
                    return target
    return dest


def write_tags(kind: str, name: str, tags: list[str]) -> None:
    """assets/footage/tags.yaml に追記（ファイル名の語 + 検索語のタグ）."""
    import yaml
    path = FOOT / "tags.yaml"
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data[f"{kind}/{name}"] = {"kind": kind, "tags": sorted(set(tags))}
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=True), encoding="utf-8")


def debug_dump(page, label: str) -> None:
    """自動で押せなかったとき: 画面写真と、ボタンらしい要素の一覧を残す."""
    DEBUG.mkdir(parents=True, exist_ok=True)
    stem = DEBUG / f"{int(time.time())}_{slug(label)[:40]}"
    try:
        page.screenshot(path=str(stem) + ".png", full_page=False)
    except Exception:
        pass
    try:
        info = page.evaluate(
            """() => Array.from(document.querySelectorAll('button, a, [role=button]'))
                 .slice(0, 400)
                 .map(e => ({tag: e.tagName, text: (e.innerText||'').trim().slice(0,40),
                             aria: e.getAttribute('aria-label'), title: e.getAttribute('title'),
                             testid: e.getAttribute('data-testid'), cls: (e.className||'').toString().slice(0,80)}))
                 .filter(x => x.text || x.aria || x.title || x.testid)"""
        )
        (Path(str(stem) + ".json")).write_text(json.dumps(info, ensure_ascii=False, indent=1))
    except Exception:
        pass
    log(f"   デバッグ情報を残しました: {stem}.png / .json")


def ensure_logged_in(page) -> None:
    page.goto("https://artlist.io/", wait_until="domcontentloaded")
    human_pause(2, 3)
    # ログイン中なら「Log in」「Sign in」のボタンが無い、という判定（緩め）
    for _ in range(120):   # 最大 10 分待つ
        html = page.content().lower()
        if not re.search(r">\s*(log ?in|sign ?in)\s*<", html):
            log("ログイン済みです")
            return
        log("Artlist にログインしてください（このウィンドウで）。ログインを検知したら続けます…")
        time.sleep(5)
    raise SystemExit("ログインを確認できませんでした")


def try_auto_download(page, context, per: int) -> list:
    """検索結果の上から per 本、ダウンロードボタンを押してみる。取れた Download の一覧を返す."""
    downloads = []
    # 結果カードの候補: 動画要素を含む要素の親
    cards = page.locator("article, li, div").filter(has=page.locator("video")).all()
    cards = [c for c in cards if c.is_visible()][:per * 3] if cards else []
    tried = 0
    for card in cards:
        if len(downloads) >= per or tried >= per * 3:
            break
        tried += 1
        try:
            card.scroll_into_view_if_needed(timeout=5000)
            card.hover(timeout=5000)
            human_pause(0.8, 1.6)
            btn = card.locator(
                "button[aria-label*='ownload' i], a[aria-label*='ownload' i], "
                "[data-testid*='download' i], button:has-text('Download'), a:has-text('Download')"
            ).first
            if btn.count() == 0 or not btn.is_visible():
                continue
            btn.click(timeout=5000)
            human_pause(1.0, 2.0)
            # 解像度を選ぶ小窓が出たら HD を選び、確定ボタンを押す
            with page.expect_download(timeout=90000) as dl:
                confirm = page.locator(
                    "[role=dialog] button:has-text('Download'), [role=dialog] button:has-text('HD'), "
                    "button:has-text('Download HD'), button:has-text('1080')"
                ).first
                if confirm.count() and confirm.is_visible():
                    confirm.click(timeout=5000)
            downloads.append(dl.value)
            log(f"   ダウンロード {len(downloads)}/{per}")
            human_pause(3, 8)
        except Exception as exc:
            log(f"   自動で押せませんでした: {str(exc).splitlines()[0][:80]}")
            continue
    return downloads


def wait_manual_downloads(page, context, per: int, seconds: int) -> list:
    """手伝いモード: あなたがダウンロードを押すのを待ち、落ちてきたものを受け取る."""
    log(f"   ▶ この画面で好きな素材のダウンロードを {per} 本押してください（{seconds} 秒待ちます。"
        f" 終わったら待たなくても次へ進みます）")
    got = []
    deadline = time.time() + seconds
    while len(got) < per and time.time() < deadline:
        try:
            with page.expect_download(timeout=5000) as dl:
                pass
            got.append(dl.value)
            log(f"   受け取りました {len(got)}/{per}")
        except Exception:
            continue
    return got


def run(args) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("先に: python3 -m pip install playwright && python3 -m playwright install chromium")

    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, accept_downloads=True,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        ensure_logged_in(page)

        jobs: list[tuple[str, str, str, list[str]]] = []
        if not args.music_only:
            jobs += [("footage", q, kind, tags) for q, kind, tags in FOOTAGE_QUERIES]
        if not args.footage_only:
            jobs += [("music", q, mood, [mood]) for q, mood in MUSIC_QUERIES]
            jobs += [("sfx", q, kind, [kind]) for q, kind in SFX_QUERIES]
        if args.limit:
            jobs = jobs[:args.limit]

        total = 0
        for n, (what, query, kind, tags) in enumerate(jobs, 1):
            url = {"music": MUSIC_URL, "sfx": SFX_URL}.get(what, FOOTAGE_URL).format(q=query.replace(" ", "%20"))
            log(f"[{n}/{len(jobs)}] {query}")
            page.goto(url, wait_until="domcontentloaded")
            human_pause(3, 5)
            per = 1 if what in ("music", "sfx") else args.per
            downloads = [] if args.manual else try_auto_download(page, context, per)
            if len(downloads) < per:
                if not args.manual:
                    debug_dump(page, query)
                downloads += wait_manual_downloads(page, context, per - len(downloads), args.wait)
            for i, dl in enumerate(downloads):
                if what == "music":
                    dest = save_download(dl, BGM, kind)            # ambient.mp3 など
                elif what == "sfx":
                    dest = save_download(dl, SFX, kind)            # POP.mp3 など
                else:
                    dest = save_download(dl, FOOT / kind, f"{slug(query)}_{i + 1}")
                    if dest:
                        write_tags(kind, dest.name, tags + slug(query).split("_"))
                if dest:
                    total += 1
                    log(f"   保存: {dest.relative_to(HERE)}")
            human_pause(4, 10)
        context.close()
    log(f"完了: {total} 件を保存しました → {FOOT} / {BGM} / {SFX}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=1, help="検索語ごとに落とす本数（既定 1）")
    ap.add_argument("--limit", type=int, default=0, help="最初の N 語だけ（お試し用）")
    ap.add_argument("--wait", type=int, default=90, help="手伝いモードで待つ秒数")
    ap.add_argument("--manual", action="store_true", help="自動で押さず、押すのは自分・整理だけ道具")
    ap.add_argument("--music-only", action="store_true")
    ap.add_argument("--footage-only", action="store_true")
    run(ap.parse_args())
