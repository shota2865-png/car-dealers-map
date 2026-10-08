"""YouTube の検索候補（オートコンプリート）から「実際に検索されている言葉」を集める.

本編は Shorts のフィードから来ないので、検索と関連動画で見つけてもらうしかない（週次レポート: 検索 2%）。
伸びた本編（生涯賃金・スマホ代）は人が検索する言葉の回で、ニュースの速報的な回（日銀1.25%・消費税1%）は伸びなかった。
そこで、本編の企画は「検索候補に出る言葉」を 1 つ選び、その疑問に正面から答える形にする（topics.select_topics）。

  suggest(q)            YouTube の検索候補（日本・日本語）。取れなければ []
  pool(cfg, history)    config の topics.search_bases を種に、まだ扱っていない検索語を人気順に
  has_demand(keyword)   その言葉が検索候補に出るか（タイトルの頭に置く語の確認）
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from .config import Config

log = logging.getLogger(__name__)

_URL = "https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=ja&gl=jp&q={q}"
_CACHE: dict[str, list[str]] = {}


def suggest(q: str, timeout: float = 10.0) -> list[str]:
    """YouTube の検索窓に q を打ったときに出る候補（多い順）。通信できなければ空."""
    q = q.strip()
    if not q:
        return []
    if q in _CACHE:
        return _CACHE[q]
    out: list[str] = []
    try:
        req = urllib.request.Request(_URL.format(q=urllib.parse.quote(q)), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="ignore"))
        out = [str(s).strip() for s in (data[1] if len(data) > 1 else []) if str(s).strip()]
    except Exception as exc:                         # 検索候補が取れなくても企画は作る
        log.warning("検索候補を取れませんでした（%s）: %s", q, exc)
    _CACHE[q] = out
    return out


def _covered(phrase: str, history: list[str]) -> bool:
    """最近の回で、同じ言葉（空白を除いて）をもう扱ったか."""
    key = phrase.replace(" ", "").replace("　", "")
    words = [w for w in phrase.split() if w]
    for h in history:
        hh = h.replace(" ", "").replace("　", "")
        if key and key in hh:
            return True
        if len(words) >= 2 and all(w in hh for w in words):
            return True
    return False


def pool(cfg: Config, history: list[str], limit: int = 40) -> list[str]:
    """種の言葉ごとに検索候補を集め、まだ扱っていないものを人気順（種の順 × 候補の順）に並べる.

    候補は種の言葉で始まるものだけ（「手取フィッシュランド」のような別物を外す）。
    """
    bases = [str(b).strip() for b in (cfg.get("topics.search_bases") or []) if str(b).strip()]
    per = int(cfg.get("topics.search_per_base", 4))
    exclude = [str(x) for x in (cfg.get("topics.search_exclude") or [])]
    lists = []
    for b in bases:
        got = [s for s in suggest(b) if s.replace(" ", "").startswith(b.replace(" ", ""))
               and not any(x in s for x in exclude)                 # 人名・番組名つきの候補は外す
               and not s.rstrip().endswith(("の", "が", "は", "を", "に", "と", "で"))]   # 「金利の」のような途中の候補
        lists.append([s for s in got if not _covered(s, history)][:per])
    # 種ごとに 1 つずつ順に取る（同じ種ばかりにならない）
    out: list[str] = []
    for rank in range(per):
        for got in lists:
            if rank < len(got) and got[rank] not in out:
                out.append(got[rank])
    return out[:limit]


def has_demand(keyword: str) -> bool:
    """その言葉が YouTube の検索候補に出るか（タイトルの頭に置く価値があるか）."""
    k = keyword.strip()
    if not k:
        return False
    got = suggest(k)
    norm = k.replace(" ", "")
    return any(s.replace(" ", "").startswith(norm) for s in got)
