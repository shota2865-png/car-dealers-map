"""山の前後の字幕を読んで、どこをつなぐか・タイトル・見出しを決める.

無料枠の LLM（既定は Google の Gemini API）に聞く。
1 本の切り抜きは「いくつかの区間をジャンプカットでつないだもの」。話の前置き → 展開 → オチの順に、
要らない言いよどみ・脱線・間を飛ばして 1 分以内にまとめる。
使えない・返事がおかしいときは、山の周りを間（ま）で刻んでつなぐ（止まらないことを優先）。
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
1. 起承転結のある {min_sec}〜{max_sec} 秒の Shorts になるように、使う行の範囲を順番にいくつか選ぶ（cuts）。
   - 起: 何の話かが一瞬で分かる行から始める（挨拶・前置き・「えー」は飛ばす）
   - 承・転: 話が進む行だけ残す。脱線・言い直し・コメント読みの寄り道・長い間は飛ばす（ジャンプカット）
   - 結: オチ・驚き・笑い・結論の一言で終わる。尻切れにしない
   - 範囲は 2〜8 個。時間の順に並べ、重ねない。各行の頭の秒数を見て、合計が {min_sec}〜{max_sec} 秒になるようにする
   - screen: その範囲でチャート・株価・画面に映っているものの話をしているなら true（画面を映す）。それ以外は false（顔を映す）
2. Shorts のタイトル（全角 28 字以内）。実際に話している中身をそのまま言う。「マックスむらい」を入れる。
   煽り言葉（ヤバすぎ・大惨事・黒歴史・鬼畜・驚愕・衝撃・絶句・炎上 など）、起きていないこと、他人を貶める言い方は使わない（切り抜きの許可の条件）
3. 見出し 2 つ。
   - band1: 何の話か（全角 13 字以内。例「TOPIX除外銘柄は買い?」「ビール大手4社カルテル疑惑」）
   - band2: 続きが気になる小見出し。単語ではなく文章にする。中身に無いことは書かない（全角 8〜13 字。例「果たしてバレるのか！？」「この後まさかの展開に…」「むらいが出した答えは？」）
4. 切り抜きとしての面白さ 1〜10（内輪の話・挨拶・雑音だけなら 1〜3）
5. 投資の話なら is_investment を true

JSON だけを返す:
{{"cuts": [{{"from": 行番号, "to": 行番号, "screen": true/false}}], "title": "...", "band1": "...", "band2": "...", "score": 数, "is_investment": true/false}}

元の動画: {video_title}
いちばん盛り上がったのは {peak:.0f}s 付近（ここを必ず含める）
字幕:
{lines}"""

SCREEN_WORDS = re.compile(r"チャート|画面|グラフ|ローソク|出来高|移動平均|この線|見てください|これ見|見ると|板が|株価が")


def _fmt_lines(lines: list[dict[str, Any]]) -> str:
    return "\n".join(f"{i}: [{ln['start']:.0f}s] {ln['text']}" for i, ln in enumerate(lines))


def chat(cfg: dict[str, Any], prompt: str, temperature: float = 0.4) -> dict[str, Any] | None:
    """無料枠の LLM に聞いて JSON を受け取る（OpenAI 互換の API。既定は Google の Gemini API）.

    config の llm.models を上から順に試す（無料枠の回数を使い切った・そのモデルが無くなった、に備える）。
    鍵が無い・全部だめなら None（呼び出し側は機械的な決め方に落とす）。
    """
    llm = cfg.get("llm") or {}
    key = os.environ.get(llm.get("key_env", "GEMINI_API_KEY"))
    if not key:
        return None
    for model in llm.get("models") or []:
        try:
            r = requests.post(llm["base_url"].rstrip("/") + "/chat/completions", timeout=60,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json={"model": model, "temperature": temperature,
                                    "response_format": {"type": "json_object"},
                                    "messages": [{"role": "user", "content": prompt}]})
            if r.status_code != 200:
                log.warning("LLM %s: %s %s", model, r.status_code, r.text[:160].replace("\n", " "))
                continue
            txt = r.json()["choices"][0]["message"]["content"]
            return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        except Exception as e:  # noqa: BLE001  切り抜きは止めない
            log.warning("LLM %s に聞けませんでした: %s", model, e)
    return None


def ask_llm(cfg: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any] | None:
    c = cfg["clip"]
    return chat(cfg, PROMPT.format(min_sec=c["min_sec"], max_sec=c["max_sec"], video_title=cand["info_title"],
                                   peak=cand.get("peak", cand["start"]), lines=_fmt_lines(cand["lines"])))


def _clean(s: str, n: int) -> str:
    s = re.sub(r"\[[^\]]*\]", "", s).strip(" 。、")
    return s[:n]


def fallback(cfg: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """LLM 無しの決め方: 山の範囲を、0.8 秒以上の間（ま）で刻んでつなぐ。見出しは元動画の題名から."""
    lines = cand["lines"]
    i = next((k for k, ln in enumerate(lines) if ln["start"] >= cand["start"]), 0)
    j = max((k for k, ln in enumerate(lines) if ln["end"] <= cand["end"] + 8), default=len(lines) - 1)
    j = max(i, j)
    cuts, a = [], i
    for k in range(i, j):
        if lines[k + 1]["start"] - lines[k]["end"] > 0.8:
            cuts.append((a, k))
            a = k + 1
    cuts.append((a, j))
    base = re.sub(r"【[^】]*】|\[[^\]]*\]", "", cand["info_title"]).strip()
    return {"cuts": [{"from": x, "to": y,
                      "screen": bool(SCREEN_WORDS.search("".join(ln["text"] for ln in lines[x:y + 1])))}
                     for x, y in cuts],
            "title": f"{base[:26]}【マックスむらい切り抜き】", "band1": base[:13],
            "band2": "この後どうなる！？", "score": 5, "is_investment": "株" in cand["info_title"]}


def to_segments(cfg: dict[str, Any], lines: list[dict[str, Any]], cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """行の範囲を、秒の区間（重なりなし・時間順・合計 max_sec 以内）にする."""
    c = cfg["clip"]
    n = len(lines)
    rng = []
    for cut in cuts:
        a, b = int(cut["from"]), int(cut["to"])
        a, b = max(0, min(a, n - 1)), max(0, min(b, n - 1))
        if b < a:
            a, b = b, a
        rng.append((a, b, bool(cut.get("screen"))))
    rng.sort()
    segs: list[dict[str, Any]] = []
    last_b = -1
    for a, b, screen in rng:
        a = max(a, last_b + 1)
        if a > b:
            continue
        last_b = b
        s, e = lines[a]["start"] - 0.12, lines[b]["end"] + 0.18
        if segs and s - segs[-1]["end"] < 0.35 and segs[-1]["screen"] == screen:
            segs[-1]["end"] = e                    # ほぼ続いている区間はつなげる（無駄なカットを作らない）
        else:
            if segs:
                s = max(s, segs[-1]["end"])
            segs.append({"start": max(0.0, s), "end": e, "screen": screen})
    segs = [x for x in segs if x["end"] - x["start"] >= 0.8]

    def total() -> float:
        return sum(x["end"] - x["start"] for x in segs)

    # 長すぎるときは、頭（起）と終わり（結）を残して真ん中の短い区間から落とす。それでも長ければ最後の手前を削る
    while total() > c["max_sec"] and len(segs) > 2:
        k = min(range(1, len(segs) - 1), key=lambda i: segs[i]["end"] - segs[i]["start"])
        segs.pop(k)
    if total() > c["max_sec"]:
        over = total() - c["max_sec"]
        k = 0 if len(segs) == 1 else max(range(len(segs)), key=lambda i: segs[i]["end"] - segs[i]["start"])
        segs[k]["end"] -= over
    return segs


def decide(cfg: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any] | None:
    c = cfg["clip"]
    lines = cand["lines"]
    ans = ask_llm(cfg, cand)
    used_llm = ans is not None
    try:
        segs = to_segments(cfg, lines, ans["cuts"]) if ans else []
    except (KeyError, ValueError, TypeError) as e:
        log.warning("返事を読めませんでした: %s", e)
        segs = []
    if sum(x["end"] - x["start"] for x in segs) < c["min_sec"]:
        if used_llm:
            log.info("つないだ長さが足りないので、機械的な決め方にします")
        fb = fallback(cfg, cand)
        segs = to_segments(cfg, lines, fb["cuts"])
        ans = {**fb, **{k: v for k, v in (ans or {}).items() if k in ("title", "band1", "band2", "score", "is_investment") and v}}
    total = sum(x["end"] - x["start"] for x in segs)
    if total < min(c["min_sec"], 25) or not segs:
        return None
    title = str(ans.get("title", "")).strip()[:40]
    if "#shorts" not in title.lower():
        title = f"{title} #shorts"
    b1, b2 = str(ans.get("band1", "")).strip()[:16], str(ans.get("band2", "")).strip()[:18]
    return {**cand, "start": segs[0]["start"], "end": segs[-1]["end"], "segments": segs, "length": total,
            "title": title, "band1": b1, "band2": b2, "hook": " ".join(x for x in (b1, b2) if x),
            "rating": float(ans.get("score", 5)), "is_investment": bool(ans.get("is_investment")), "llm": used_llm}


PROOF = """YouTube Shorts の字幕を校正します。同じ音声を 2 つの方法で文字起こししたものを渡します。どちらにも誤字があります。
A は行ごとに番号が付いています。B は同じ音声の別の文字起こし（つながった文章）です。

やること: A の各行を、実際に話されたはずの言葉に直す。
- A と B を見比べ、文脈に合うほうを採る（例: A「証券講座」B「正権講座」→ 文脈から「証券口座」）
- 同音の誤変換・聞き間違い・固有名詞を直す（銘柄・会社・ゲームの名前は元の動画の題名も手がかりにする）
- 言い回しは変えない。要約・言い換え・付け足しはしない。話していないことは書かない
- 「えー」「あのー」などの言いよどみと、同じ言葉の言い直しの重複は消してよい
- A にあって B にまったく無い言葉は、編集で音声が切られた部分の可能性が高い。前後がつながらないなら消す
- 行の数と順番は A と同じにする（消したい行は空文字にする）。句読点は付けなくてよい

JSON だけを返す: {{"lines": ["1 行目", "2 行目", ...]}}（ちょうど {n} 行）

元の動画の題名: {title}

A:
{a}

B:
{b}"""


def apply_fixes(cfg: dict[str, Any], text: str) -> str:
    """config の subtitle_fixes（よくある誤字の置き換え表）を当てる."""
    for bad, good in (cfg.get("subtitle_fixes") or {}).items():
        text = text.replace(bad, good)
    return text


def proofread(cfg: dict[str, Any], lines: list[str], heard: str, title: str) -> list[str]:
    """字幕の誤字を直す。LLM が使えないとき・返事の行数が合わないときは、置き換え表だけ当てて返す."""
    base = [apply_fixes(cfg, x) for x in lines]
    if not lines:
        return base
    ans = chat(cfg, PROOF.format(n=len(lines), title=title, b=heard,
                                 a="\n".join(f"{i + 1}: {x}" for i, x in enumerate(lines))), temperature=0.1)
    got = (ans or {}).get("lines")
    if not isinstance(got, list):
        return base
    if len(got) != len(lines):
        log.warning("校正の行数が合わないので使いません（%d → %d）", len(lines), len(got))
        return base
    out = []
    for old, new in zip(base, got):
        new = re.sub(r"\s+", "", str(new))
        # 直しすぎ（長さが大きく変わる＝言い換え・付け足し）は採らない
        out.append(apply_fixes(cfg, new) if 0.5 * len(old) <= len(new) <= 1.3 * len(old) + 2 or not new else old)
    return out
