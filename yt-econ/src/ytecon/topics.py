"""話題づくり.

RSS から経済ニュースを集め、過去に扱ったテーマと重複するものを外し、
「高校生〜20代に刺さるか」を Claude に採点させて当日分を選ぶ。

ニュースが取れない日でも止まらないよう、常設テーマ（evergreen）の種を
必ず混ぜる。1日2本なら「ニュース1本 + 常設1本」が回しやすい。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from . import domain, llm
from .config import Config
from .state import HORIZONS, Store

log = logging.getLogger(__name__)


@dataclass
class Topic:
    title: str                      # 動画で扱うテーマ（タイトル案ではない）
    angle: str                      # 切り口。ここが被ると動画も被る
    kind: str                       # news | evergreen
    why_now: str = ""
    audience_hook: str = ""         # なぜ視聴者が自分ごと化できるか
    key_questions: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    score: float = 0.0
    id: int | None = None

    # --- 日本と海外の時差に関する属性 ---
    horizon: str = "flow"           # flow(今刺さる) / bridge(半年以内) / stock(先行仕込み)
    diffusion_stage: int = 3        # 日本での普及段階 0..3
    lag_months: float = 0.0         # 日本で一般化するまでの推定ヶ月数
    watch_keywords: list[str] = field(default_factory=list)  # 話題化を検知する語
    japan_bridge: str = ""          # 海外の話を日本の視聴者に接続する一文


# ----------------------------------------------------------------------
# RSS 収集
# ----------------------------------------------------------------------
def fetch_rss(cfg: Config) -> list[dict[str, str]]:
    """設定された RSS を巡回して記事見出しを集める."""
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser 未導入のため RSS 収集をスキップします")
        return []

    limit = int(cfg.get("topics.fetch_limit", 60))
    items: list[dict[str, str]] = []
    for src in cfg.get("topics.rss_sources", []) or []:
        name, url = src.get("name", "?"), src.get("url")
        if not url:
            continue
        try:
            feed = feedparser.parse(url)
        except Exception as exc:  # ネットワーク断でも全体は止めない
            log.warning("RSS取得失敗 %s: %s", name, exc)
            continue
        if getattr(feed, "bozo", False) and not feed.entries:
            log.warning("RSS解析失敗 %s", name)
            continue
        for entry in feed.entries[:20]:
            items.append(
                {
                    "source": name,
                    "title": _clean(getattr(entry, "title", "")),
                    "summary": _clean(getattr(entry, "summary", ""))[:300],
                    "url": getattr(entry, "link", ""),
                    "published": str(getattr(entry, "published", "")),
                }
            )
    log.info("RSS %d件を収集", len(items))
    return items[:limit]


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return unicodedata.normalize("NFKC", text).strip()


# ----------------------------------------------------------------------
# 重複排除
# ----------------------------------------------------------------------
def _normalize(title: str) -> str:
    t = unicodedata.normalize("NFKC", title).lower()
    return re.sub(r"[\s　、。・:：\-—\[\]「」『』（）()！!？?]", "", t)


def is_duplicate(title: str, history: list[str], threshold: float) -> bool:
    """過去テーマとの表記ゆれを吸収した類似判定."""
    norm = _normalize(title)
    if not norm:
        return True
    for past in history:
        if SequenceMatcher(None, norm, _normalize(past)).ratio() >= threshold:
            return True
    return False


# ----------------------------------------------------------------------
# Claude による選定
# ----------------------------------------------------------------------
_SELECT_SYSTEM = """あなたは日本語の{field}解説YouTubeチャンネルの企画担当ディレクターです。
視聴者は{audience}。彼らは{field}の予備知識が乏しく、しかし「自分の生活や給料に
どう効くのか」には強い関心があります。

# このチャンネルの前提：日本は海外に遅れて追いつく市場

経済・技術・制度のトピックは、海外で起きてから日本で一般化するまでに
時差があります。この時差は弱点ではなく在庫です。扱い方が3つに分かれます。

- flow  : いま日本で話題。今出せば今見られる。競合は多い。チャンネルを回す燃料
- bridge: 海外で起きており、日本にも半年以内に降りてくる。先行者として最も旨い層
- stock : 海外のみで、日本での一般化はまだ先。今は数字が出ないが、
          日本で話題化した瞬間に検索で掘り起こされる資産になる

**stock を「今バズらないから駄目な企画」と判断しないでください。**
stock の評価軸は今日の再生数ではなく、「日本で話題化したとき、
この動画が最初に見つかる1本になるか」です。

# 各企画に必ず付ける時差の見立て

- diffusion_stage: 日本での普及段階
    0 = 海外のみ。日本ではまだ誰も話していない
    1 = 感度の高い一部の層が知り始めた
    2 = 日本のメディアが報じ始めた
    3 = 一般化して既出。競合が多い
- lag_months: 日本で一般化するまでの推定ヶ月数（stage 3 なら 0）
- horizon: stage 0〜1 かつ lag が6ヶ月超なら stock、
           stage 1〜2 または lag が6ヶ月以内なら bridge、stage 3 なら flow
- watch_keywords: **日本のニュース見出しに出たら「来た」と判断できる日本語の語**を
  3〜6個。固有名詞・制度名・カタカナ語を優先し、「{field}」のような一般語は入れない。
  これは後日この動画を掘り起こすトリガーとして機械的に使われます
- japan_bridge: 海外の話を日本の視聴者が自分ごと化するための接続を一文で。
  「アメリカで起きた→日本ではこう来る→だから今あなたに関係がある」の橋渡し

# 企画を選ぶときの基準（この順に重い）
1. 自分ごと化できるか — 視聴者の財布・就職・進路に接続できるか
   （stock の場合は「まだ関係ないが、先に知っておくと得をする」形でよい）
2. 8〜10分で説明しきれるか — 論点が3〜5個に収まるか
3. 一次情報があるか — 公的統計や公式発表で数字を裏づけられるか
4. 誤解が多いか — 「実は違う」を提示できるテーマは強い
5. 賞味期限 — flow は1週間以内に出す価値があるか。stock は逆に、
   1年後に見ても古びない構成にできるか

扱ってはいけない話題:
{banned}

出力の注意:
- title は「動画で扱うテーマ」。釣りタイトルではなく内容の要約にする
- angle は他の動画と被らない切り口。ここが企画の本体
- 数字を断定せず、確認すべき一次情報を sources に入れる
"""

_SELECT_SCHEMA = llm.obj(
    {
        "topics": llm.arr(
            llm.obj(
                {
                    "title": llm.STR,
                    "angle": llm.STR,
                    "kind": {"type": "string", "enum": ["news", "evergreen"]},
                    "why_now": llm.STR,
                    "audience_hook": llm.STR,
                    "key_questions": llm.arr(llm.STR),
                    "sources": llm.arr(llm.obj({"name": llm.STR, "url": llm.STR})),
                    "score": llm.NUM,
                    "horizon": {"type": "string", "enum": ["flow", "bridge", "stock"]},
                    "diffusion_stage": llm.INT,
                    "lag_months": llm.NUM,
                    "watch_keywords": llm.arr(llm.STR),
                    "japan_bridge": llm.STR,
                }
            )
        )
    }
)


# ----------------------------------------------------------------------
# ポートフォリオ配分
# ----------------------------------------------------------------------
def _normalize_horizon(item: dict[str, Any]) -> str:
    """horizon が空・不正な場合に stage と lag から埋め直す."""
    horizon = str(item.get("horizon", "") or "").lower()
    if horizon in HORIZONS:
        return horizon
    stage = int(item.get("diffusion_stage", 3) or 0)
    lag = float(item.get("lag_months", 0) or 0)
    if stage >= 3:
        return "flow"
    if stage <= 1 and lag > 6:
        return "stock"
    return "bridge"


def _clean_keywords(words: list[str], extra_generic: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """掘り起こしのトリガー語。一般語が混ざると毎日誤検知するので落とす."""
    out = []
    for w in words:
        w = unicodedata.normalize("NFKC", str(w)).strip()
        if len(w) < 3 or w in _TOO_GENERIC or w in extra_generic:
            continue
        if w not in out:
            out.append(w)
    return out[:6]


_TOO_GENERIC = {
    "経済", "日本", "世界", "市場", "企業", "政府", "金融", "投資", "景気",
    "ニュース", "お金", "価格", "円", "株", "AI", "米国", "アメリカ",
}


def plan_portfolio(cfg: Config, store: Store, count: int, *,
                   has_news: bool, has_seeds: bool) -> dict[str, int]:
    """今日の flow / bridge / stock の本数を決める.

    考え方:
      チャンネルが若いうちは flow を厚くする。数字が出ないと推薦が回らず、
      stock を置いても掘り起こされる土台ができないため。本数が積み上がると
      stock の比率が上がる。時差のある市場では、これが「先行しているのに
      刺さらない」を「先行していたから刺さる」に変える唯一の方法になる。

    配分はその日だけで閉じずに、直近の実績を見て**不足している枠から埋める**。
    1日2本で比率 3:2:1 をその場で割ると stock が毎日 0 本になり、
    先行仕込みが永久に作られないため。
    """
    weights = dict(cfg.get("topics.horizon_mix", {}) or
                   {"flow": 3, "bridge": 2, "stock": 1})

    # 立ち上げ期は flow に寄せる（config の ramp_up_videos 本まで）
    window = int(cfg.get("topics.horizon_window_days", 30))
    counts = store.horizon_counts(window)
    produced_total = sum(store.horizon_counts(3650).values())
    if produced_total < int(cfg.get("topics.ramp_up_videos", 30)):
        weights = {"flow": weights.get("flow", 3) + 3,
                   "bridge": weights.get("bridge", 2),
                   "stock": max(weights.get("stock", 1), 1)}

    available = {
        "flow": has_news,
        # 国内ニュースが無い日でも、常設テーマや先行テーマからは作れる
        "bridge": True,
        "stock": bool(cfg.get("topics.frontier_seeds") or []) or has_seeds,
    }
    weights = {h: (w if available.get(h) else 0) for h, w in weights.items()}
    total_weight = sum(weights.values())
    if total_weight <= 0:                    # 何も作れない指定になったら flow に逃がす
        return {"flow": count, "bridge": 0, "stock": 0}

    plan = {h: 0 for h in HORIZONS}

    # 下限を先に確保する。deficit だけで回すと、過去に flow が偏っていた期間に
    # flow が何日も 0 本になり、チャンネルを回す燃料が切れてしまう。
    floors = dict(cfg.get("topics.horizon_floor", {}) or {"flow": 1})
    for h, floor in floors.items():
        if h in plan and weights.get(h, 0) > 0:
            plan[h] = min(int(floor), count - sum(plan.values()))
            plan[h] = max(plan[h], 0)

    for _ in range(count - sum(plan.values())):
        assigned = sum(counts.values()) + sum(plan.values())
        # 「あるべき本数」に最も足りていない枠を選ぶ
        deficit = {
            h: weights[h] / total_weight * (assigned + 1) - (counts[h] + plan[h])
            for h in HORIZONS if weights.get(h, 0) > 0
        }
        plan[max(deficit, key=lambda h: deficit[h])] += 1
    return plan


def schedule_path(cfg: Config) -> Path:
    p = str(cfg.get("topics.schedule_file", "") or "").strip()
    q = Path(p) if p else cfg.root / "config" / "schedule.yaml"
    return q if q.is_absolute() else cfg.root / q


def queued_topics(cfg: Config, store: Store, count: int, today: dt.date | None = None) -> list[Topic]:
    """config/schedule.yaml に手で書いた企画のうち、今日ぶん（date が今日、または date 無し）を返す.

    使い終わった企画は投稿履歴との重複判定で自動的に飛ばす。DB にも登録する。
    """
    path = schedule_path(cfg)
    if not path.exists():
        return []
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    queue = data.get("queue") or []
    today = today or dt.date.today()
    history = store.recent_topic_titles(int(cfg.get("topics.dedupe_window_days", 120)))
    threshold = float(cfg.get("topics.dedupe_threshold", 0.72))
    out: list[Topic] = []
    for item in queue:
        title = str(item.get("title", "")).strip()
        if not title or is_duplicate(title, history, threshold):
            continue
        when = item.get("date")
        if when is not None:
            when = when if isinstance(when, dt.date) else dt.date.fromisoformat(str(when))
            if when != today:
                continue
        topic = Topic(
            title=title,
            angle=str(item.get("angle", "")).strip(),
            kind=str(item.get("kind", "evergreen")),
            why_now=str(item.get("why_now", "")),
            audience_hook=str(item.get("audience_hook", "")),
            key_questions=[str(q) for q in (item.get("key_questions") or [])],
            sources=[dict(s) for s in (item.get("sources") or [])],
            score=float(item.get("score", 100) or 100),
            horizon=str(item.get("horizon", "flow")),
            japan_bridge=str(item.get("japan_bridge", "")),
        )
        topic.id = store.add_topic(topic.title, topic.angle, topic.kind, topic.sources, topic.score,
                                   horizon=topic.horizon)
        history.append(title)
        out.append(topic)
        if len(out) >= count:
            break
    if out:
        log.info("週間スケジュール（%s）から %d 本: %s", path.name, len(out), " / ".join(t.title for t in out))
    return out


def select_topics(cfg: Config, store: Store, count: int) -> list[Topic]:
    """当日分の話題を選んで DB に登録して返す。手動スケジュールがあればそちらを先に使う."""
    queued = queued_topics(cfg, store, count)
    if len(queued) >= count:
        return queued
    count -= len(queued)
    history = store.recent_topic_titles(int(cfg.get("topics.dedupe_window_days", 120)))
    threshold = float(cfg.get("topics.dedupe_threshold", 0.72))

    news = fetch_rss(cfg)
    seeds = [
        s for s in (cfg.get("topics.evergreen_seeds", []) or [])
        if not is_duplicate(s, history, threshold)
    ]

    plan = plan_portfolio(cfg, store, count, has_news=bool(news), has_seeds=bool(seeds))

    system = _SELECT_SYSTEM.format(
        field=domain.field(cfg),
        audience=cfg.get("channel.audience", "20代の社会人"),
        banned="\n".join(f"- {b}" for b in cfg.get("channel.banned_topics", []) or []),
    )

    news_block = "\n".join(
        f"- [{i['source']}] {i['title']} ({i['url']})\n  {i['summary']}" for i in news
    ) or "(今日はニュースを取得できませんでした)"
    seeds_block = "\n".join(f"- {s}" for s in seeds) or "(常設テーマの在庫なし)"
    history_block = "\n".join(f"- {h}" for h in history[-60:]) or "(まだ投稿履歴なし)"
    frontier_block = "\n".join(
        f"- {t}" for t in (cfg.get("topics.frontier_seeds", []) or [])
        if not is_duplicate(t, history, threshold)
    ) or "(先行テーマの在庫なし)"

    recent = store.horizon_counts(30)
    balance_block = (
        f"直近30日の内訳: flow {recent['flow']}本 / bridge {recent['bridge']}本 / "
        f"stock {recent['stock']}本"
    )

    user = f"""今日は {dt.date.today().isoformat()} です。

# 収集した{domain.field(cfg)}ニュース見出し（日本国内。主に flow の材料）
{news_block}

# 常設テーマの種（ニュース性はないが需要が安定している）
{seeds_block}

# 海外で先行している領域の種（bridge / stock の材料）
{frontier_block}

# 過去に扱ったテーマ（これらと内容が重なる企画は出さないでください）
{history_block}

# これまでのポートフォリオ
{balance_block}

# 依頼
合計 {count} 本ぶんの企画を作ってください。内訳の目安は
**flow {plan['flow']}本 / bridge {plan['bridge']}本 / stock {plan['stock']}本** です。
題材の都合でこの内訳を1本ずらすのは構いませんが、理由が説明できる範囲にしてください。

各企画に必ず付けるもの:
- score: 0〜100。ただし **horizon ごとに評価軸を変えてください**
    flow   = 今日クリックされるか
    bridge = 3〜6ヶ月後に検索されるか
    stock  = 日本で話題化したとき「最初に見つかる1本」になれるか
- diffusion_stage / lag_months / watch_keywords / japan_bridge

採用する {count} 本だけを返してください。"""

    data = llm.complete_json(
        system,
        user,
        _SELECT_SCHEMA,
        model=cfg.get("topics.model", llm.DEFAULT_MODEL),
        effort=cfg.get("topics.effort", "medium"),
    )

    chosen: list[Topic] = []
    for item in data.get("topics", []):
        title = (item.get("title") or "").strip()
        if not title or is_duplicate(title, history, threshold):
            log.info("重複のためスキップ: %s", title)
            continue
        topic = Topic(
            title=title,
            angle=(item.get("angle") or "").strip(),
            kind=item.get("kind", "evergreen"),
            why_now=item.get("why_now", ""),
            audience_hook=item.get("audience_hook", ""),
            key_questions=item.get("key_questions", []) or [],
            sources=item.get("sources", []) or [],
            score=float(item.get("score", 0) or 0),
            horizon=_normalize_horizon(item),
            diffusion_stage=int(item.get("diffusion_stage", 3) or 0),
            lag_months=float(item.get("lag_months", 0) or 0),
            watch_keywords=_clean_keywords(item.get("watch_keywords", []) or [], domain.generic_words(cfg)),
            japan_bridge=item.get("japan_bridge", ""),
        )
        topic.id = store.add_topic(
            topic.title, topic.angle, topic.kind, topic.sources, topic.score,
            horizon=topic.horizon, diffusion_stage=topic.diffusion_stage,
            lag_months=topic.lag_months, watch_keywords=topic.watch_keywords,
        )
        history.append(topic.title)  # 同一バッチ内の共食いも防ぐ
        chosen.append(topic)
        if len(chosen) >= count:
            break

    chosen = queued + chosen
    if not chosen:
        raise RuntimeError(
            "採用できる話題がありませんでした。"
            "config の evergreen_seeds を増やすか dedupe_window_days を短くしてください。"
        )
    for t in chosen:
        log.info("採用 [%s/stage%d/lag%.0fヶ月] %s",
                 t.horizon, t.diffusion_stage, t.lag_months, t.title)
    return chosen
