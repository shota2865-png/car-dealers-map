"""スタイルバイブル（config/style_bible.yaml）の読み込みと、各工程向けの取り出し口.

「考えすぎる葦」型の分解（S01〜S09 / V01〜V05 / E01〜E05 / A01〜A05 / R01〜R06）を
データとして持ち、台本生成・シーン割り・BGM・サムネがここを通して参照する。
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

import yaml

from .config import Config

log = logging.getLogger(__name__)

# 旧い台本（beat 名が CONTEXT/STORY など）を読むときの対応表
_LEGACY_BEATS = {
    "CONTEXT": "FAMILIAR_SCENE",
    "QUESTION": "QUESTION_LOCK",
    "STORY": "ACADEMIC_LENS",
    "REVEAL": "MECHANISM_REVEAL",
    "PAYOFF": "HUMAN_RETURN",
    "COMEDY": "PERSPECTIVE_FLIP",
    "CONCLUSION": "REFLECTIVE_ENDING",
}


@functools.lru_cache(maxsize=4)
def _load_file(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        log.warning("style_bible.yaml が見つかりません: %s", p)
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load(cfg: Config) -> dict[str, Any]:
    return _load_file(str(cfg.root / "config" / "style_bible.yaml"))


# ----------------------------------------------------------------------
# ビート（台本の 9 ブロック）
# ----------------------------------------------------------------------
def beats(cfg: Config) -> list[dict[str, Any]]:
    return list(load(cfg).get("beats") or [])


def section_beats(cfg: Config) -> list[str]:
    """本編セクションに割り当てる beat 名を、台本上の順で返す."""
    return [b["id"] for b in beats(cfg) if b.get("slot") == "section"]


def beat_ids(cfg: Config) -> list[str]:
    ids = [b["id"] for b in beats(cfg)]
    return ids or list(_LEGACY_BEATS.values())


def normalize_beat(name: str) -> str:
    return _LEGACY_BEATS.get(name, name)


def beat_for_section(cfg: Config, index: int, n_sections: int) -> str:
    """セクション数が 6 でなくても、順序が崩れないように比例で割り当てる."""
    order = section_beats(cfg)
    if not order:
        return "ACADEMIC_LENS"
    if n_sections <= 0:
        return order[0]
    pos = int(index * len(order) / n_sections)
    return order[min(pos, len(order) - 1)]


def beat_for_block(cfg: Config, block_id: str, n_sections: int) -> str:
    """音声ブロック ID（hook / proof / promise / s0.. / closing）→ beat."""
    slot_map = {b.get("slot"): b["id"] for b in beats(cfg) if b.get("slot") != "section"}
    if block_id.startswith("s") and block_id[1:].isdigit():
        return beat_for_section(cfg, int(block_id[1:]), n_sections)
    return slot_map.get(block_id, "ACADEMIC_LENS")


# ----------------------------------------------------------------------
# 編集・音・画像
# ----------------------------------------------------------------------
def semantic_cut_words(cfg: Config) -> tuple[str, ...]:
    words = (load(cfg).get("editing") or {}).get("semantic_cut_words") or []
    return tuple(words)


def motions(cfg: Config) -> list[str]:
    return list((load(cfg).get("editing") or {}).get("motions") or ["push_in", "pull_out"])


def bgm_mood_for_beat(cfg: Config, beat: str) -> str:
    moods = (load(cfg).get("audio") or {}).get("bgm_moods") or {}
    for mood, spec in moods.items():
        if beat in (spec.get("beats") or []):
            return mood
    return "ambient"


def bgm_crossfade(cfg: Config) -> float:
    return float((load(cfg).get("audio") or {}).get("crossfade_seconds", 3.0))


def image_style(cfg: Config) -> str:
    return str(load(cfg).get("image_style") or "").strip()


# ----------------------------------------------------------------------
# タイトル・サムネ
# ----------------------------------------------------------------------
def title_matches(cfg: Config, title: str) -> bool:
    """Knowledge Gap 型（なぜ／〜ほど／意外…）の合図が1つでも入っているか."""
    signals = load(cfg).get("title_signals") or ["なぜ"]
    return any(s in title for s in signals)


def thumbnail_overlaps_title(cfg: Config, main: str, title: str) -> bool:
    """サムネ主コピーがタイトルの語をなぞっていないか（2文字以上の連続一致を数える）."""
    spec = load(cfg).get("thumbnail") or {}
    if not spec.get("forbid_same_words_as_title", True) or not main or not title:
        return False
    hits = sum(1 for i in range(len(main) - 1) if main[i:i + 2] in title)
    return hits >= max(2, (len(main) - 1) // 2)


# ----------------------------------------------------------------------
# プロンプトへの埋め込み
# ----------------------------------------------------------------------
def render_for_prompt(cfg: Config, n_sections: int) -> str:
    """台本生成のシステムプロンプトに差し込む、バイブルの要約."""
    b = load(cfg)
    if not b:
        return ""
    lines: list[str] = []
    ident = b.get("identity") or {}
    lines.append(f"ジャンル: {ident.get('genre', '')}")
    lines.append(f"1本のループ: {ident.get('loop', '')}")
    lines.append("")
    lines.append("## 9ブロックの順番と役割（この順を崩さない）")
    slot_label = {"hook": "hook", "proof": "proof", "promise": "promise",
                  "closing": "closing"}
    sec_i = 0
    order = section_beats(cfg)
    for bt in b.get("beats") or []:
        s0, s1 = bt.get("seconds", [0, 0])
        if bt.get("slot") == "section":
            where = f"sections[{sec_i}]"
            sec_i += 1
        else:
            where = slot_label.get(bt.get("slot"), bt.get("slot"))
        lines.append(f"- {bt['code']} {bt['id']}（{bt['name']}）→ {where}"
                     f" / {s0 // 60}:{s0 % 60:02d}〜{s1 // 60}:{s1 % 60:02d}: {bt['rule']}")
    if n_sections != len(order):
        lines.append(f"  ※ 本編は {n_sections} セクション。{len(order)} ブロックを順序を保って"
                     "比例配分すること（前の beat を飛ばさない）")
    lines.append("")
    lines.append("## 接続詞で切る（Semantic Cut）")
    lines.append("画面は「話題」ではなく「意味の単位」が変わるところで切り替える。"
                 "次の語で文を始めると、そこがカット点になる。転換点では必ず使うこと: "
                 + "、".join(semantic_cut_words(cfg)))
    lines.append("")
    lines.append("## タイトル（Knowledge Gap 型）")
    for k, t in (b.get("title_templates") or {}).items():
        lines.append(f"- {k}: {t}")
    for r in b.get("title_rules") or []:
        lines.append(f"- {r}")
    lines.append("")
    th = b.get("thumbnail") or {}
    lines.append("## サムネイル文言（thumbnail_copy）")
    lines.append(f"- {th.get('rule', '')}")
    lines.append(f"- main は {th.get('main_max_chars', 10)} 字以内の感情語・象徴語。"
                 "タイトルと同じ言葉を使わない（タイトル＝問い、サムネ＝感情）")
    lines.append("")
    lines.append("## 画像プロンプト（visual.image_prompt。英語）")
    lines.append(f"- 式: {b.get('image_prompt_formula', '')}")
    lines.append("- 「綺麗な絵」ではなく「概念の視覚化」。"
                 "例) SNSで比較する → 暗い部屋でスマホを見つめる人物、周囲に明るい投稿が浮かび本人だけ影")
    for ex in b.get("image_prompt_examples") or []:
        lines.append(f"  例: {ex}")
    lines.append("- 画風の指定（照明・粒子など）はこちらで足すので書かない。被写体と状況と感情だけ")
    lines.append("")
    nar = b.get("narration") or {}
    lines.append("## 語りの温度")
    lines.append(f"- {nar.get('temperature', '')}")
    lines.append("- 使わない: " + " / ".join(nar.get("avoid") or []))
    lines.append("- こういう文を使う: " + " / ".join(nar.get("prefer") or []))
    return "\n".join(lines)


def research_prompt_block(cfg: Config) -> str:
    """リサーチの木（台本前の視点集め）で使うレンズ一覧."""
    b = load(cfg)
    lenses = b.get("research_lenses") or {}
    pick = b.get("research_pick") or [2, 3]
    lines = [f"- {k} {v['name']}: {v['ask']}" for k, v in lenses.items()]
    lines.append(f"\n上のレンズをすべて当てて仮説を書き、面白い {pick[0]}〜{pick[1]} 個を採用する。")
    return "\n".join(lines)
