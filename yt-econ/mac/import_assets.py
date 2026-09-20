# ============================================================
#  素材を取り込む（Mac 用）
#
#  ~/Downloads に落ちている
#    ・ずんだもんの立ち絵 zip（「立ち絵」「ずんだもん」を含む zip）
#    ・Artlist の動画（mp4/mov）と音楽（mp3/wav/aac）
#  を、このフォルダの assets/ に振り分ける。何度実行しても同じファイルは二重に入れない。
#
#  使い方:  python3 import_assets.py            （~/Downloads を見る）
#           python3 import_assets.py ~/Desktop  （別のフォルダを見る）
# ============================================================
import re
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "Downloads"
CHAR = HERE / "assets" / "character"
FOOT = HERE / "assets" / "footage"
BGM = HERE / "assets" / "bgm"
SFX = HERE / "assets" / "sfx"
SFX_WORDS = {"pop": "POP", "click": "CLICK", "whoosh": "WHOOSH", "swoosh": "TRANSITION", "impact": "IMPACT",
             "hit": "IMPACT", "boing": "COMEDY", "comedy": "COMEDY", "buzzer": "ERROR", "error": "ERROR",
             "riser": "RISER", "transition": "TRANSITION"}
for d in (CHAR, FOOT / "abstract", FOOT / "broll", FOOT / "texture", BGM, SFX):
    d.mkdir(parents=True, exist_ok=True)

MOODS = ("ambient", "curiosity", "tension", "reflective")
ABSTRACT_WORDS = ("abstract", "particle", "bokeh", "loop", "gradient", "grid", "light", "leak",
                  "smoke", "ink", "texture", "grain", "dust", "network", "digital", "motion")


def slug(name: str) -> str:
    base = re.sub(r"\s*[-_]\s*artlist.*$", "", Path(name).stem, flags=re.I)
    base = re.sub(r"[^A-Za-z0-9ぁ-んァ-ン一-龥]+", "_", base).strip("_").lower()
    return base or "clip"


def newest(path: Path) -> bool:
    return path.is_file() and not path.name.startswith(".")


moved: dict[str, list[str]] = {"立ち絵": [], "動画": [], "音楽": [], "効果音": []}

# 1) 立ち絵 zip
for z in SRC.glob("*.zip"):
    if not any(k in z.name for k in ("立ち絵", "ずんだもん", "zundamon")):
        continue
    dest = CHAR / re.sub(r"\.zip$", "", z.name)
    if dest.exists():
        continue
    with zipfile.ZipFile(z) as zf:
        # macOS の zip は cp437 で名前が化けるので cp932 で読み直す
        for info in zf.infolist():
            name = info.filename
            try:
                name = name.encode("cp437").decode("cp932")
            except Exception:
                pass
            if name.startswith("__MACOSX") or name.endswith("/"):
                continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as f, open(target, "wb") as g:
                shutil.copyfileobj(f, g)
    moved["立ち絵"].append(dest.name)

# 2) 動画（Artlist）
for v in sorted(SRC.iterdir()):
    if not newest(v) or v.suffix.lower() not in (".mp4", ".mov", ".webm", ".m4v"):
        continue
    kind = "abstract" if any(w in v.name.lower() for w in ABSTRACT_WORDS) else "broll"
    dest = FOOT / kind / f"{slug(v.name)}{v.suffix.lower()}"
    if dest.exists() and dest.stat().st_size == v.stat().st_size:
        continue
    shutil.move(str(v), dest)
    moved["動画"].append(f"{kind}/{dest.name}")

# 3) 効果音（Artlist SFX）: 名前に pop / whoosh / click … が入っていれば assets/sfx へ
for a in sorted(SRC.iterdir()):
    if not newest(a) or a.suffix.lower() not in (".mp3", ".wav", ".aac", ".m4a", ".flac"):
        continue
    kind = next((k for w, k in SFX_WORDS.items() if w in a.name.lower()), None)
    if kind is None:
        continue
    dest = SFX / f"{kind}{a.suffix.lower()}"
    if dest.exists():
        dest = SFX / f"{kind}_{slug(a.name)}{a.suffix.lower()}"
    shutil.move(str(a), dest)
    moved["効果音"].append(dest.name)

# 4) 音楽（Artlist）: 名前に気分が入っていればその名前に、無ければ順に空いている気分へ
for a in sorted(SRC.iterdir()):
    if not newest(a) or a.suffix.lower() not in (".mp3", ".wav", ".aac", ".m4a", ".flac"):
        continue
    mood = next((m for m in MOODS if m in a.name.lower()), None)
    if mood is None:
        taken = {p.stem for p in BGM.iterdir() if p.is_file()}
        mood = next((m for m in MOODS if m not in taken), None)
    dest = BGM / (f"{mood}{a.suffix.lower()}" if mood else f"{slug(a.name)}{a.suffix.lower()}")
    if dest.exists():
        dest = BGM / f"{slug(a.name)}{a.suffix.lower()}"
    if dest.exists() and dest.stat().st_size == a.stat().st_size:
        continue
    shutil.move(str(a), dest)
    moved["音楽"].append(dest.name)

for k, v in moved.items():
    print(f"{k}: {len(v)} 件" + (" → " + ", ".join(v[:8]) + ("…" if len(v) > 8 else "") if v else ""))
n_foot = sum(1 for p in FOOT.rglob("*") if p.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v"))
n_bgm = sum(1 for p in BGM.iterdir() if p.is_file() and not p.name.startswith("."))
print(f"いま: 動画 {n_foot} 本 / 曲 {n_bgm} 曲 / 立ち絵 {sum(1 for p in CHAR.iterdir() if p.is_dir())} 組")
