"""心理学チャンネルの本編（16:9・約 30 分・寝ながら聴く人向け）の台本を LLM に書かせる.

画面は wide.py の場面（Shorts と同じ部品を横に並べたもの）。字幕は出さず、めたんの声だけ。
寝落ちしながら聴く人・画面を見ない人がいる前提で、画面に出るものは全部ナレーションでも言う。

流れ:
  outline(cfg, topic)        5 部の骨組み（各部の 2 択クイズ・話す要点・研究）と、まとめ・今日のひとつ
  write_part(cfg, ...)       1 部ずつ場面の JSON を書く（1 回で 30 分ぶんを書かせると質と長さが崩れるため）
  assemble(cfg, ...)         冒頭のあいさつ + 5 部 + まとめ + 締めのあいさつ をつなぐ
  write_honpen(cfg, topic)   上の 3 つをまとめて呼ぶ。quiz.build(cfg, data, out, wide=True) でそのまま動画になる
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import domain, llm
from .config import Config

log = logging.getLogger(__name__)

PARTS = 5

_RULES = """# 聴く人
- 寝る前に、目を閉じて聴いている人が多い。**画面を見なくても、声だけで全部わかる**ように話す
- 画面に出す言葉・選択肢・図の中身は、必ずナレーションでも言う。「画面を見てください」「この図」「ここに」「こちら」は使わない
- ゆっくり、やさしく、責めない。煽らない（ヤバい・終わった・知らないと損 は禁止）。診断しない

# 言葉づかい（いちばん大事）
- 小学 5 年生が聞いて分かる言葉で話す。です・ます調の、落ち着いた話し言葉
- 専門用語は 1 回だけ、すぐ後ろに日常の言葉で言い換える（例:「作業記憶、つまり頭の中のメモ帳」）。2 回目からは言い換えのほうを使う
- 画面の箱・見出しには専門用語を出さない（point 場面の term だけは例外）。漢語より和語
- 文節は 30 字以内で、読点か句点で終える。1 文節に数字は 1 つまで

# 研究
- 実在する研究だけ。研究者名か大学名を 1 つ言う。数字は出典にあるものだけ。断定は「〜と分かってきています」まで
- 有名でも再現されていない実験は、そう断る"""

_KINDS = """# 場面の種類（段階 = narration の 2 つ目の数字。その文節で画面に出るもの）
- chapter  : 部の扉。label（「第1章」）, heading（14 字以内）, heading_hl（その一部）, sub（20 字以内）。段階 0 = 見出し、1 = sub
- question : 2 択クイズ。heading は「3秒で選んでください」固定、heading_hl「3秒」、lead（状況 16 字以内）, lead_hl, options（2 つ、各 14 字以内）
             段階 0 = heading、1 = lead、2 = 選択肢 A、3 = 選択肢 B。
             **ナレーションで状況と A・B の両方を全文読み上げ**、最後に「3つ数えますね。心の中で選んでみてください。」で終える（このあと声のカウントダウンが自動で入る）
- result   : 答え合わせ。pick（多くの人が選ぶほう "A" か "B"）, option（その選択肢の文）, strike（よくある思いこみ 14 字以内）, answer（本当のこと 20 字以内）
             段階 0 = 選んだ肢、1 = 思いこみに打ち消し線、2 = 本当のこと。**ナレーションで「Bの、〜を選んだ人」と選択肢の中身を言い直し**、思いこみと本当のことを言葉で言う
- point    : 用語の言い換え。heading, heading_hl, term（専門用語）, plain（日常の言葉 12 字以内）, note（補足 24 字以内）。段階 0 = term、1 = plain、2 = note
- meter    : 升目（容量）。heading, heading_hl, caption（20 字以内）, slots（4〜6）, fill（[{"text": 8 字以内, "bad": true/false}]、slots より少なく）, label_left（残りの升の説明）
             段階 0 = 空の升目、1..k = fill が 1 つずつ、k+1 = label_left
- flow     : 因果の流れ。heading, heading_hl, boxes（2〜3 個。{"text": 10 字以内, "state": "normal|active|dim", "up": true で赤い上向き矢印, "note": 箱の下の補足 12 字以内}）。段階 i = boxes[i]
- branch   : 分かれ道。heading, heading_hl, note（出典など 24 字以内）, source（左の箱）, targets（2 つ。{"text": 10 字以内, "avoided": true/false}）。段階 0 = source、1 = targets[0]、2 = targets[1]
- versus   : 対比。heading, heading_hl, left/right（{"text": 14 字以内, "caption": 10 字以内}）。段階 0 = left、1 = left に ✕ と caption、2 = right に ✓ と caption
- steps    : 手順。heading（12 字以内）, heading_hl, items（3 つ、各 10 字以内）, final（10 字以内）。段階 0 = 見出し、1..3 = items、4 = final"""

_OUTLINE_SYSTEM = """あなたは YouTube の{field}チャンネルの構成作家です。寝る前に聴く、約 30 分の本編の骨組みを作ります。
全体は {parts} 部。各部は「2 択クイズ → 答え合わせ → 研究と説明」で進み、最後に全体のまとめと「今日のひとつ」を置きます。

{rules}

# 骨組みの決まり
- 部ごとに、違う問いと違う研究を扱う。前の部の話を繰り返さない。部の順番は、聴いている人の疑問が自然に進む順に
- 各部のクイズは、どちらも「自分もそうだ」と思える日常の行動にする（どちらかが明らかに正解、にしない）
- 最後の部は「じゃあ、どうすればいいか」（今夜・明日から試せること）にする
- recap は 3 つ（各 10 字以内）。today_one は 14 字以内の一言
- next は次回のテーマ（12 字以内）

JSON だけを返す。"""

_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "theme": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "heading": {"type": "string"}, "heading_hl": {"type": "string"}, "sub": {"type": "string"},
            "quiz": {"type": "object", "properties": {"lead": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
                                                       "pick": {"type": "string"}, "strike": {"type": "string"}, "answer": {"type": "string"}}},
            "points": {"type": "array", "items": {"type": "string"}},
            "research": {"type": "array", "items": {"type": "string"}},
        }}},
        "recap": {"type": "array", "items": {"type": "string"}},
        "today_one": {"type": "string"},
        "next": {"type": "string"},
    },
    "required": ["title", "theme", "parts", "recap", "today_one"],
}

_PART_SYSTEM = """あなたは YouTube の{field}チャンネルの構成作家です。寝る前に聴く約 30 分の本編のうち、1 つの部（約 5〜6 分）の場面を書きます。
画面は白地に短い言葉の箱・矢印・黄色いマーカーだけ。字幕は出ません。1 人のナレーター（落ち着いた女性の声）が話します。

{rules}

{kinds}

# この部の決まり
- 場面は 10〜14 個。順番は chapter → question → result → 説明の場面（point / meter / flow / branch / versus / steps を混ぜる。同じ種類を 3 回続けない）
- ナレーションの合計は {chars_min}〜{chars_max} 字。説明の場面は 1 つにつき 4〜8 文節。短すぎる場面を並べない
- 各段階の文節は、その段階で出る要素のことを話す。段階は 0 から順に増やし、飛ばさない
- 部の最後の場面は、この部で分かったことを 1〜2 文でまとめる（次の部へのつなぎの一言を添えてよい）

JSON だけを返す。形: {{"scenes": [ ... ]}}"""

_PART_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {"type": "object"}}},
    "required": ["scenes"],
}

_EXAMPLE_PART = {"scenes": [
    {"kind": "chapter", "label": "第1章", "heading": "その場で言葉が出ない理由", "heading_hl": "言葉が出ない", "sub": "頭の回転の速さとは、関係がありません",
     "narration": [["まずは、その場で言葉が出てこない理由からです。", 0], ["先に言っておくと、これは頭の回転の速さとは、関係がありません。", 1]]},
    {"kind": "question", "heading": "3秒で選んでください", "heading_hl": "3秒", "lead": "会議で言い返せなかった夜", "lead_hl": "夜",
     "options": ["動画を見て気をそらす", "布団で返す言葉を考える"],
     "narration": [["ここで、ひとつ質問です。", 0], ["会議で、言い返せなかった日の夜。", 1], ["A、動画を見て、気をそらす。", 2], ["B、布団の中で、返す言葉を考える。", 3],
                   ["3つ数えますね。心の中で選んでみてください。", 3]]},
    {"kind": "result", "pick": "B", "option": "布団で返す言葉を考える", "strike": "考えれば、すっきりする", "answer": "夜に考えるほど、気分は重くなりやすい",
     "narration": [["Bの、布団の中で返す言葉を考える、を選んだ人。実はとても多いんです。", 0], ["考えれば、すっきりする。そう思いますよね。", 1],
                   ["でも研究では、夜に考えるほど、気分は重くなりやすいと分かってきています。", 2]]},
    {"kind": "point", "heading": "作業記憶ってなに？", "heading_hl": "作業記憶", "term": "作業記憶", "plain": "頭の中のメモ帳", "note": "今この瞬間に考えるための、小さな置き場",
     "narration": [["作業記憶という言葉があります。", 0], ["わかりやすく言うと、頭の中のメモ帳です。", 1], ["今この瞬間に考えるための、小さな置き場なんです。", 2]]},
]}


def _model(cfg: Config) -> str:
    return str(cfg.get("honpen.model", cfg.get("script.model", llm.DEFAULT_MODEL)))


def outline(cfg: Config, topic: dict[str, Any]) -> dict[str, Any]:
    user = (
        f"テーマ: {topic.get('title', '')}\n"
        f"切り口: {topic.get('angle', '')}\n"
        f"視聴者: {cfg.get('channel.audience', '')}\n"
        + ("聴く人の疑問:\n" + "\n".join(f"- {q}" for q in topic.get("key_questions") or []) + "\n" if topic.get("key_questions") else "")
        + ("参考にできる出典:\n" + "\n".join(f"- {s.get('name', '')}" for s in topic.get("sources") or []) + "\n" if topic.get("sources") else "")
        + f"\n{PARTS} 部の骨組みを JSON で。"
    )
    system = _OUTLINE_SYSTEM.format(field=domain.field(cfg), parts=PARTS, rules=_RULES)
    data = llm.complete_json(system, user, _OUTLINE_SCHEMA, model=_model(cfg), effort=str(cfg.get("honpen.effort", "high")))
    data["parts"] = (data.get("parts") or [])[:PARTS]
    return data


def write_part(cfg: Config, ol: dict[str, Any], k: int, chars: tuple[int, int] = (1600, 1900)) -> list[dict[str, Any]]:
    part = ol["parts"][k]
    others = [f"第{i + 1}章 {p.get('heading', '')}: " + " / ".join(p.get("points") or []) for i, p in enumerate(ol["parts"]) if i != k]
    user = (
        f"本編のタイトル: {ol.get('title', '')}\n"
        f"視聴者: {cfg.get('channel.audience', '')}\n\n"
        f"# この部（第{k + 1}章）\n{json.dumps(part, ensure_ascii=False, indent=1)}\n\n"
        "# ほかの部で話すこと（ここでは繰り返さない）\n" + "\n".join(f"- {o}" for o in others) + "\n\n"
        + ("この部が最後なので、今夜・明日から試せることを中心に。\n" if k == len(ol["parts"]) - 1 else "")
        + "形の例（内容は使わない。形だけ真似る）:\n" + json.dumps(_EXAMPLE_PART, ensure_ascii=False, indent=1)
    )
    system = _PART_SYSTEM.format(field=domain.field(cfg), rules=_RULES, kinds=_KINDS, chars_min=chars[0], chars_max=chars[1])
    data = llm.complete_json(system, user, _PART_SCHEMA, model=_model(cfg), effort=str(cfg.get("honpen.effort", "high")))
    scenes = [s for s in (data.get("scenes") or []) if isinstance(s, dict) and s.get("kind")]
    for s in scenes:
        if s["kind"] == "chapter":
            s["label"] = f"第{k + 1}章"
    return scenes


def opening(cfg: Config, ol: dict[str, Any]) -> dict[str, Any]:
    name = str(cfg.get("channel.name", ""))
    theme = str(ol.get("theme") or ol.get("title") or "")
    return {"kind": "opening", "lines": ["声だけで、わかるようにお話しします", "眠くなったら、目を閉じたままで大丈夫です"], "theme": theme,
            "narration": [[f"こんばんは。{name}です。", 0],
                          ["この動画は、寝る前に、目を閉じたまま聴けるように作っています。", 0],
                          ["画面を見なくても、声だけでわかるように、お話ししますね。", 0],
                          ["途中で眠くなったら、そのまま眠ってしまって大丈夫です。", 1],
                          ["部屋の明かりを少し落として、楽な姿勢になってください。", 1],
                          [f"今日のテーマは、{theme}、です。", 2],
                          ["ゆっくり、始めていきましょう。", 2]]}


def closing(cfg: Config, ol: dict[str, Any]) -> list[dict[str, Any]]:
    recap = [str(x) for x in (ol.get("recap") or [])][:3]
    one = str(ol.get("today_one") or "")
    nxt = str(ol.get("next") or "")
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    when = times[0] if times else "20:00"
    rec_nar = [["最後に、今日のお話を、ゆっくり振り返りますね。", 0]]
    rec_nar += [[f"{['ひとつめ', 'ふたつめ', 'みっつめ'][i]}は、{t}。", i + 1] for i, t in enumerate(recap)]
    rec_nar += [["ここまで覚えていなくても、大丈夫です。", len(recap) + 1]]
    end_nar = [[f"今日のひとつは、{one}、でした。", 0]]
    end_nar += [[f"次回は、{nxt}のお話です。毎日{when}に更新しています。", 1]] if nxt else [[f"毎日{when}に更新しています。", 1]]
    end_nar += [["ここまで聴いてくださって、ありがとうございます。", 2],
                ["このあとは、静かな音楽だけが、少しのあいだ流れます。", 2],
                ["今日も一日、おつかれさまでした。ゆっくり休んでくださいね。", 2]]
    return [
        {"kind": "steps", "heading": "今日のまとめ", "heading_hl": "まとめ", "items": recap, "final": "今日はここまで", "narration": rec_nar},
        {"kind": "ending", "one": one, "next": nxt, "rest": "このあとは、静かな音楽だけが流れます", "narration": end_nar},
    ]


def assemble(cfg: Config, ol: dict[str, Any], parts: list[list[dict[str, Any]]]) -> dict[str, Any]:
    scenes = [opening(cfg, ol)]
    for p in parts:
        scenes += p
    scenes += closing(cfg, ol)
    return {"title": str(ol.get("title") or ""), "hook": "", "scenes": scenes, "outline": ol}


def narration_chars(data: dict[str, Any]) -> int:
    return sum(len(str(t)) for sc in data.get("scenes") or [] for t, _ in (sc.get("narration") or []))


def write_honpen(cfg: Config, topic: dict[str, Any]) -> dict[str, Any]:
    """約 30 分の本編 JSON（quiz.build(..., wide=True) にそのまま渡せる形）."""
    ol = outline(cfg, topic)
    log.info("本編の骨組み: %s / %d 部", ol.get("title"), len(ol.get("parts") or []))
    lo, hi = int(cfg.get("honpen.part_chars_min", 1600)), int(cfg.get("honpen.part_chars_max", 1900))
    parts = []
    for k in range(len(ol["parts"])):
        sc = write_part(cfg, ol, k, (lo, hi))
        log.info("第%d章: 場面 %d / %d 字", k + 1, len(sc), sum(len(str(t)) for s in sc for t, _ in (s.get("narration") or [])))
        parts.append(sc)
    data = assemble(cfg, ol, parts)
    log.info("本編の台本: %d 場面 / ナレーション %d 字", len(data["scenes"]), narration_chars(data))
    return data
