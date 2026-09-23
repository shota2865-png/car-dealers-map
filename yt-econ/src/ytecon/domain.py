"""チャンネルの「分野」の言葉。プロンプトや概要欄に埋め込む語をひとまとめにする.

本体（経済）と 2 つ目以降（心理学など）で違うのはここの値だけで、
台本・企画・Shorts・概要欄のロジックは共通。channel.yaml の `channel:` で上書きする。
既定値は本体（おやすみ経済学）の従来の文言そのもの。
"""

from __future__ import annotations

from .config import Config

_DEFAULTS = {
    # 「◯◯解説YouTube」の◯◯。台本・企画・校閲のプロンプトに入る
    "field": "経済",
    # 「◯◯のレンズで日常を説明する」の◯◯（学問名）
    "lens": "経済学",
    # Shorts の構成作家に伝える扱う領域
    "topic_words": "経済・お金・就活・AI",
    # 台本で毎回 2〜4 個扱う「その分野でしか使わない言葉」の例
    "term_examples": "実質賃金 / 政策金利 / 貿易収支 / 購買力平価 / 名目と実質 / 為替介入",
    # Shorts の概要欄 1 行（「毎日◯時に本編を更新」の前）
    "pitch": "寝る前に聴く、お金と就活とAIの話。",
    # 概要欄の「ご注意」。分野ごとの免責
    "disclaimer": (
        "この動画は経済の仕組みを解説するもので、特定の金融商品の購入を\n"
        "推奨するものではありません。投資の判断はご自身の責任でお願いします。"
    ),
    # タイトル先頭の【引き】の例（型を示すためのもの）
    "hook_examples": {
        "question": "給料どこいった / 昇給、実感ある？",
        "gap": "物価8%、給料5%",
        "claim": "値上げの方が速い / 給料は最後尾",
    },
}
_DEFAULT_HASHTAGS = ["#経済", "#就活"]
# 企画のキーワードから外す一般語（分野ごとに足せる）
_DEFAULT_GENERIC = ["経済", "お金", "金融", "投資", "景気", "市場", "株", "円"]


def _s(cfg: Config, key: str) -> str:
    v = cfg.get(f"channel.{key}", None)
    return str(v).strip() if v not in (None, "") else str(_DEFAULTS[key])


def field(cfg: Config) -> str:
    return _s(cfg, "field")


def lens(cfg: Config) -> str:
    return _s(cfg, "lens")


def topic_words(cfg: Config) -> str:
    return _s(cfg, "topic_words")


def term_examples(cfg: Config) -> str:
    return _s(cfg, "term_examples")


def pitch(cfg: Config) -> str:
    return _s(cfg, "pitch")


def disclaimer(cfg: Config) -> str:
    return _s(cfg, "disclaimer")


def hook_examples(cfg: Config) -> dict[str, str]:
    base = dict(_DEFAULTS["hook_examples"])  # type: ignore[arg-type]
    for k, v in (cfg.get("channel.hook_examples", {}) or {}).items():
        if v:
            base[str(k)] = str(v)
    return base


def hashtags(cfg: Config) -> list[str]:
    """分野のハッシュタグ（# 付き）。出演者の分は呼び出し側で足す."""
    raw = cfg.get("channel.hashtags", None)
    tags = [str(t).strip() for t in (raw if isinstance(raw, list) else _DEFAULT_HASHTAGS) if str(t).strip()]
    return [t if t.startswith("#") else "#" + t for t in tags]


def generic_words(cfg: Config) -> set[str]:
    raw = cfg.get("channel.generic_words", None)
    words = raw if isinstance(raw, list) else _DEFAULT_GENERIC
    return {str(w).strip() for w in words if str(w).strip()}


def cast_names(cfg: Config) -> list[str]:
    """出演者の表示名（ハッシュタグ・タグ用）。掛け合いでなければ空."""
    cast = cfg.get("cast", {}) or {}
    if str(cast.get("mode", "solo")) != "dialogue":
        return []
    return [str(c.get("name") or c.get("key")) for c in (cast.get("characters", []) or []) if c.get("key")]
