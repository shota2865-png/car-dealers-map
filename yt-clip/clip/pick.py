"""山の前後の字幕を読んで、切り抜く範囲・タイトル・冒頭の一言を決める.

GitHub Models（Actions の GITHUB_TOKEN で使える無料枠）に聞く。
使えない・返事がおかしいときは、字幕から機械的に決める（止まらないことを優先）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

log = logging.getLogger(__name__)

PROMPT = """あなたは YouTube Shorts の切り抜き編集者です。マックスむらいさん（元AppBank、パズドラ実況・米国株/宇宙株の配信）の配信の一部を、番号つきの字幕で渡します。
自動字幕なので誤字があります。意味を補って読んでください。

やること:
1. この中から、前置きなしで見て意味が分かり、オチ・驚き・笑い・役に立つ一言のどれかで終わる {min_sec}〜{max_sec} 秒の範囲を 1 つ選ぶ（start_line から end_line まで。各行の秒数を見て長さを合わせる）
2. Shorts のタイトル（全角 28 字以内）。冒頭で興味を引く言い方。誇張・ウソ・他人を貶める表現は禁止。「マックスむらい」を入れる
3. 画面の上の帯に出す見出し 2 行。band1 は何の話か（全角 13 字以内。例「TOPIX除外銘柄は買い?」「ビール大手4社カルテル疑惑」）、band2 はむらいさんの結論や一言（全角 11 字以内。例「バカなの!?」「今後どうなる!?」「皆んな気にしすぎ」）
4. 切り抜きとしての面白さ 1〜10（内輪の話・挨拶・雑音だけなら 1〜3）
5. 投資の話なら is_investment を true

JSON だけを返す: {{"start_line": 数, "end_line": 数, "title": "...", "band1": "...", "band2": "...", "score": 数, "is_investment": true/false}}

元の動画: {video_title}
字幕:
{lines}"""


def _fmt_lines(lines: list[dict[str, Any]]) -> str:
    return "\n".join(f"{i}: [{ln['start']:.0f}s] {ln['text']}" for i, ln in enumerate(lines))


def ask_llm(cfg: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any] | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_MODELS_TOKEN")
    if not token or cfg["llm"].get("provider") != "github_models":
        return None
    c = cfg["clip"]
    prompt = PROMPT.format(min_sec=c["min_sec"], max_sec=c["max_sec"], video_title=cand["info_title"],
                           lines=_fmt_lines(cand["lines"]))
    try:
        r = requests.post("https://models.github.ai/inference/chat/completions", timeout=90,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                          json={"model": cfg["llm"]["model"], "temperature": 0.4,
                                "response_format": {"type": "json_object"},
                                "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            log.warning("GitHub Models %s: %s", r.status_code, r.text[:200])
            return None
        txt = r.json()["choices"][0]["message"]["content"]
        return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception as e:  # noqa: BLE001  切り抜きは止めない
        log.warning("GitHub Models に聞けませんでした: %s", e)
        return None


def _clean(s: str, n: int) -> str:
    s = re.sub(r"\[[^\]]*\]", "", s).strip(" 。、")
    return s[:n]


def fallback(cfg: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """LLM 無しの決め方: 山の範囲を字幕の行の頭に合わせ、いちばん目立つ一言をタイトルにする."""
    lines = cand["lines"]
    i = next((k for k, ln in enumerate(lines) if ln["start"] >= cand["start"]), 0)
    j = max((k for k, ln in enumerate(lines) if ln["end"] <= cand["end"]), default=len(lines) - 1)
    body = lines[i:j + 1]

    def punch(ln: dict[str, Any]) -> float:
        t = ln["text"]
        return 3 * t.count("[笑い]") + ("？" in t or "?" in t) + bool(re.search(r"\d", t)) + min(len(t), 30) / 30

    best = max(body, key=punch) if body else lines[0]
    # 自動字幕の一言はタイトルにすると崩れやすいので、タイトルは元動画の題名から作る
    base = re.sub(r"【[^】]*】|\[[^\]]*\]", "", cand["info_title"]).strip()[:26]
    return {"start_line": i, "end_line": j, "title": f"{base}【マックスむらい切り抜き】",
            "band1": base[:13], "band2": _clean(best["text"], 11), "score": 5,
            "is_investment": "株" in cand["info_title"]}


def decide(cfg: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any] | None:
    c = cfg["clip"]
    lines = cand["lines"]
    ans = ask_llm(cfg, cand)
    used_llm = ans is not None
    if ans is None:
        ans = fallback(cfg, cand)
    try:
        i, j = int(ans["start_line"]), int(ans["end_line"])
        i, j = max(0, min(i, len(lines) - 1)), max(0, min(j, len(lines) - 1))
        if j < i:
            i, j = j, i
        start, end = lines[i]["start"] - 0.2, lines[j]["end"] + 0.4
        # 長さを範囲に収める（短ければ後ろへ伸ばす・長ければ後ろを削る）
        while end - start < c["min_sec"] and j + 1 < len(lines):
            j += 1
            end = lines[j]["end"] + 0.4
        if end - start > c["max_sec"]:
            end = start + c["max_sec"]
        if end - start < c["min_sec"]:
            return None
        title = str(ans.get("title", "")).strip()[:40] or fallback(cfg, cand)["title"]
        if "#shorts" not in title.lower():
            title = f"{title} #shorts"
        return {**cand, "start": max(0.0, start), "end": end, "title": title,
                "band1": str(ans.get("band1", "")).strip()[:16], "band2": str(ans.get("band2", "")).strip()[:14],
                "hook": " ".join(x for x in (str(ans.get("band1", "")).strip(), str(ans.get("band2", "")).strip()) if x),
                "rating": float(ans.get("score", 5)),
                "is_investment": bool(ans.get("is_investment")), "llm": used_llm}
    except (KeyError, ValueError, TypeError) as e:
        log.warning("範囲を決められませんでした: %s", e)
        return None
