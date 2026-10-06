"""心理学チャンネルの本編（16:9・15〜20 分・ながら聴き向け）の台本を LLM に書かせる.

画面は wide.py の場面（Shorts と同じ部品を横に並べたもの）。字幕は出さず、めたんの声だけ。
通勤・家事・作業をしながら聴く人（画面を見ない人）がいる前提で、画面に出るものは全部ナレーションでも言う。
聴かれ方・あいさつ・長さは config の honpen.*（listener / greeting / closing / minutes / parts）で変えられる。

流れ:
  outline(cfg, topic)        5 部の骨組み（各部の 2 択クイズ・話す要点・研究）と、まとめ・今日のひとつ
  write_part(cfg, ...)       1 部ずつ場面の JSON を書く（1 回で全部を書かせると質と長さが崩れるため）
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

PARTS = 4          # 既定の章の数（config の honpen.parts で変える）

# 聴かれ方の既定（ながら聴き）。config の honpen.listener / honpen.tone で差し替えられる
LISTENER = "通勤・家事・作業をしながら、画面を見ずに聴いている人が多い"
TONE = "落ち着いて、でもテンポよく。責めない。煽らない（ヤバい・終わった・知らないと損 は禁止）。診断しない"

_RULES = """# 聴く人
- {listener}。**画面を見なくても、声だけで全部わかる**ように話す
- 画面に出す言葉・選択肢・図の中身は、必ずナレーションでも言う。「画面を見てください」「この図」「ここに」「こちら」は使わない
- {tone}

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

# 研究所の解析画面（video.design: lab）のときに足す決まり。番組の型は「よく聞く話を、研究のデータで検証する」
_KINDS_LAB = """- data     : 研究のデータを横棒グラフで。heading（14 字以内）, heading_hl, source（研究者名か大学名と年 26 字以内）,
             bars（2〜4 本。{"label": 12 字以内, "value": 数, "unit": "%" や "人" など, "hl": true で強調}）, note（読み取りの一言 20 字以内）
             段階 0 = 見出しと出典、1..k = 棒が 1 本ずつ、k+1 = note。**数字は研究で報告された値だけ**。正確な値に自信がなければ data を使わず flow / versus にする
             ナレーションで「〜の研究では」と出典を言い、棒の中身（何が何%か）を全部声で言う
- verdict  : 検証結果。claim（この部で確かめた「よく聞く話」20 字以内）, result（"本当" か "半分本当" か "ウソ"）, reason（ひとことの理由 24 字以内）
             段階 0 = claim、1 = 判定のスタンプ、2 = reason。ナレーションは「検証結果です。〜は、半分本当でした。」のように、判定を声ではっきり言う"""

_OUTLINE_LAB = """
# 番組の型（研究所の解析画面）
- 「よく聞く心理学の話」を、解析担当のめたんが研究のデータで検証する番組
- **動画全体の軸は 1 本**。question に「今日の問い」を 1 つ決める（20 字以内の疑問形。例:「選択肢が多いと、人は選べなくなる？」）。
  画面の上にずっと出ていて、全部の部がこの問いに答えるための一歩になる
- 部はこの順番で、今日の問いに少しずつ答えていく（前の部の結論をひっくり返したり、前の話に戻ったりしない）:
  1. 本当に起きる？（有名な実験やデータで確かめる）
  2. なぜ起きる？（頭や心のしくみ）
  3. どんなときに強く・弱くなる？（条件。あとの研究で分かったこと）
  4. じゃあ、どうする？（今日から試せること）
- 部ごとに、検証する話（claim）を 1 つ決め、研究で分かったことから判定（verdict: "本当" / "半分本当" / "ウソ"）を出す。全部が「本当」にならないようにする
- 部ごとに、説明に使う身近な例え（店・職場・スマホ・家など）を 1 つ決め、その部の中では同じ例えを使い続ける。
  icon にその例えを表す絵文字を 1 つ（例: ジャムの売り場 → 🍓、職場 → 💼、スマホ → 📱、買い物 → 🛒）
- data_hint に、その部で見せられる研究の数字（研究者か大学・年・何と何を比べて何%か）を書く。確かな数字が無ければ空にする
- thumb_claim はサムネの大きな問い（14 字以内、疑問形）。初心者が一瞬で分かる言葉にし、率や専門語は使わない
  （例:「怒りは6秒で消える？」「スマホを置くと集中できる？」）。thumb_hl はその中のいちばん大事な語"""

_PART_LAB = """
# 研究所の解析画面のとき（話の軸をぶらさない）
- 今日の問いは「{question}」。この部は、そのうちの「{role}」を確かめる部
- 場面の順番は固定: chapter → question → result → 説明の場面（1〜3 個）→ data（研究の数字があるとき）→ verdict。前の場面の話に戻らない
- chapter: heading はこの部で確かめる話を問いの形で（例:「本当に選べなくなる？」）。sub は今日の問いとのつながり
  （例:「まず、本当に起きるのかを確かめる」）。icon はこの部の例えの絵文字「{icon}」
- この部の例え（{icon}）を最初から最後まで使う。途中で別の例えに乗りかえない。例えを話す場面には icon「{icon}」を付ける
- 数字は data で見せる（比べる 2〜3 本の棒。「何の何%か」が見出しと棒の名前だけで分かるように）
- 部の最後の場面は verdict。最後の文節は「ここまでで分かったのは、〜。」と今日の問いへの答えの一部を言い、
  次の部があれば「次は、〜を確かめます。」でつなぐ
- 「データ」「検証」「研究」の言葉を自然に使い、科学番組のような落ち着いた口調で"""

_ROLES = ["本当に起きるのか", "なぜ起きるのか", "どんなときに強く・弱くなるのか", "じゃあ、どうするか"]

_OUTLINE_SYSTEM = """あなたは YouTube の{field}チャンネルの構成作家です。約 {minutes} 分の本編の骨組みを作ります。
全体は {parts} 部。各部は「2 択クイズ → 答え合わせ → 研究と説明」で進み、最後に全体のまとめと「今日のひとつ」を置きます。

{rules}

# 骨組みの決まり
- 部ごとに、違う問いと違う研究を扱う。前の部の話を繰り返さない。部の順番は、聴いている人の疑問が自然に進む順に
- 各部のクイズは、どちらも「自分もそうだ」と思える日常の行動にする（どちらかが明らかに正解、にしない）
- 最後の部は「じゃあ、どうすればいいか」（今日・明日から試せること）にする
- recap は 3 つ（各 10 字以内）。today_one は 14 字以内の一言
- next は次回のテーマ（12 字以内）

# タイトル（title / keyword）
- keyword は、このテーマで人が YouTube の検索窓に打ち込む言葉（2〜8 字。例:「SNS疲れ」「先延ばし」「サンクコスト」「HSP」）
- title は 32 字以内で、**keyword から始める**。後ろに「なぜ」「〜する人の心理」「やめ方」など、検索で付けられやすい言葉を続ける
  （例:「SNS疲れの正体｜見るほど気分が沈むのはなぜ？」「先延ばしする人の心理｜意志の弱さではなかった」）
- 煽らない。内容と一致させる

JSON だけを返す。"""

_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "keyword": {"type": "string"},
        "theme": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "heading": {"type": "string"}, "heading_hl": {"type": "string"}, "sub": {"type": "string"},
            "quiz": {"type": "object", "properties": {"lead": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
                                                       "pick": {"type": "string"}, "strike": {"type": "string"}, "answer": {"type": "string"}}},
            "points": {"type": "array", "items": {"type": "string"}},
            "research": {"type": "array", "items": {"type": "string"}},
            "claim": {"type": "string"}, "verdict": {"type": "string"}, "data_hint": {"type": "string"},
            "icon": {"type": "string"},
        }}},
        "question": {"type": "string"},
        "thumb_claim": {"type": "string"},
        "thumb_hl": {"type": "string"},
        "recap": {"type": "array", "items": {"type": "string"}},
        "today_one": {"type": "string"},
        "next": {"type": "string"},
    },
    "required": ["title", "theme", "parts", "recap", "today_one"],
}

_PART_SYSTEM = """あなたは YouTube の{field}チャンネルの構成作家です。約 {minutes} 分の本編のうち、1 つの部（約 {part_minutes} 分）の場面を書きます。
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


_EXAMPLE_PART_LAB = {"scenes": [
    {"kind": "chapter", "label": "検証 01", "heading": "本当に選べなくなる？", "heading_hl": "選べなくなる", "sub": "まず、本当に起きるのかを確かめる", "icon": "🍓",
     "narration": [["最初の検証は、選択肢が多いと、人は選べなくなる、という話です。", 0], ["有名な、ジャムの実験から、確かめていきます。", 1]]},
    {"kind": "data", "heading": "ジャムを買った人の割合", "heading_hl": "買った人", "source": "コロンビア大学 アイエンガー教授ら（2000年）",
     "bars": [{"label": "24種類の売り場", "value": 3, "unit": "%"}, {"label": "6種類の売り場", "value": 30, "unit": "%", "hl": True}],
     "note": "少ないほうが、よく買われた", "icon": "🍓",
     "narration": [["コロンビア大学の、アイエンガー教授たちの研究です。", 0], ["24種類のジャムを並べた売り場では、買った人は、およそ3%。", 1],
                   ["6種類だけの売り場では、およそ30%でした。", 2], ["少ないほうが、ずっとよく買われたんです。", 3]]},
    {"kind": "verdict", "claim": "選択肢が多いと、人は選べなくなる", "result": "半分本当", "reason": "あとの研究では、いつも起きるとは限らなかった",
     "narration": [["では、検証結果です。", 0], ["選択肢が多いと選べなくなる、という話は、半分本当でした。", 1],
                   ["ここまでで分かったのは、選択肢が多いと選べなくなることは、ある、ということです。", 2],
                   ["次は、なぜ迷ってしまうのかを確かめます。", 2]]},
]}


def _model(cfg: Config) -> str:
    return str(cfg.get("honpen.model", cfg.get("script.model", llm.DEFAULT_MODEL)))


def n_parts(cfg: Config) -> int:
    return int(cfg.get("honpen.parts", PARTS))


def _minutes(cfg: Config) -> int:
    return int(cfg.get("honpen.minutes", 18))


def _rules(cfg: Config) -> str:
    return _RULES.format(listener=str(cfg.get("honpen.listener", LISTENER)), tone=str(cfg.get("honpen.tone", TONE)))


def outline(cfg: Config, topic: dict[str, Any]) -> dict[str, Any]:
    user = (
        f"テーマ: {topic.get('title', '')}\n"
        f"切り口: {topic.get('angle', '')}\n"
        f"視聴者: {cfg.get('channel.audience', '')}\n"
        + ("聴く人の疑問:\n" + "\n".join(f"- {q}" for q in topic.get("key_questions") or []) + "\n" if topic.get("key_questions") else "")
        + ("参考にできる出典:\n" + "\n".join(f"- {s.get('name', '')}" for s in topic.get("sources") or []) + "\n" if topic.get("sources") else "")
        + (f"次回のテーマは決まっている: 「{topic['next_title']}」。next にはこれを 12 字以内に縮めて書く。\n" if topic.get("next_title") else "")
        + f"\n{n_parts(cfg)} 部の骨組みを JSON で。"
    )
    system = _OUTLINE_SYSTEM.format(field=domain.field(cfg), parts=n_parts(cfg), rules=_rules(cfg), minutes=_minutes(cfg))
    if _lab(cfg):
        system = system.replace("\nJSON だけを返す。", _OUTLINE_LAB + "\n\nJSON だけを返す。")
    data = llm.complete_json(system, user, _OUTLINE_SCHEMA, model=_model(cfg), effort=str(cfg.get("honpen.effort", "high")))
    data["parts"] = (data.get("parts") or [])[:n_parts(cfg)]
    from .metadata import keyword_first
    data["title"] = keyword_first(str(data.get("title") or topic.get("title") or ""), str(data.get("keyword") or ""))
    return data


def next_title(cfg: Config, day) -> str:
    """schedule.yaml で day の翌日に予定しているテーマ（締めの「次回は」を実際の予定と合わせる）."""
    import datetime as _dt
    import yaml
    path = str(cfg.get("topics.schedule_file", "") or "")
    if not path:
        return ""
    try:
        q = (yaml.safe_load((cfg.root / path).read_text(encoding="utf-8")) or {}).get("queue") or []
    except (OSError, yaml.YAMLError):
        return ""
    want = day + _dt.timedelta(days=1)
    for item in q:
        d = item.get("date")
        if d and str(d) == want.isoformat():
            return str(item.get("title") or "")
    return ""


def write_part(cfg: Config, ol: dict[str, Any], k: int, chars: tuple[int, int] = (1600, 1900)) -> list[dict[str, Any]]:
    part = ol["parts"][k]
    others = [f"第{i + 1}章 {p.get('heading', '')}: " + " / ".join(p.get("points") or []) for i, p in enumerate(ol["parts"]) if i != k]
    user = (
        f"本編のタイトル: {ol.get('title', '')}\n"
        f"視聴者: {cfg.get('channel.audience', '')}\n\n"
        f"# この部（第{k + 1}章）\n{json.dumps(part, ensure_ascii=False, indent=1)}\n\n"
        "# ほかの部で話すこと（ここでは繰り返さない）\n" + "\n".join(f"- {o}" for o in others) + "\n\n"
        + ("この部が最後なので、今日・明日から試せることを中心に。\n" if k == len(ol["parts"]) - 1 else "")
        + "形の例（内容は使わない。形だけ真似る）:\n" + json.dumps(_EXAMPLE_PART, ensure_ascii=False, indent=1)
        + ("\n研究所の解析画面の場面の例（形だけ）:\n" + json.dumps(_EXAMPLE_PART_LAB, ensure_ascii=False, indent=1) if _lab(cfg) else "")
    )
    kinds = _KINDS + ("\n" + _KINDS_LAB if _lab(cfg) else "")
    system = _PART_SYSTEM.format(field=domain.field(cfg), rules=_rules(cfg), kinds=kinds, chars_min=chars[0], chars_max=chars[1],
                                 minutes=_minutes(cfg), part_minutes=max(3, round(_minutes(cfg) / n_parts(cfg))))
    if _lab(cfg):
        system = system.replace("画面は白地に短い言葉の箱・矢印・黄色いマーカーだけ。",
                                "画面は暗い研究所の解析画面（短い言葉の箱・矢印・グラフ・判定のスタンプ）。右に解析担当のめたんが立っている。")
        lab_rules = _PART_LAB.format(question=_question(ol), role=_ROLES[-1] if k == len(ol["parts"]) - 1 else _ROLES[min(k, 2)],
                                     icon=str(part.get("icon") or "🔬"))
        system = system.replace("\nJSON だけを返す。", lab_rules + "\n\nJSON だけを返す。")
    data = llm.complete_json(system, user, _PART_SCHEMA, model=_model(cfg), effort=str(cfg.get("honpen.effort", "high")))
    scenes = [s for s in (data.get("scenes") or []) if isinstance(s, dict) and s.get("kind")]
    for s in scenes:
        if s["kind"] == "chapter":
            s["label"] = f"検証 {k + 1:02d}" if _lab(cfg) else f"第{k + 1}章"
            if _lab(cfg) and not s.get("icon") and part.get("icon"):
                s["icon"] = str(part["icon"])
    return scenes


def _question(ol: dict[str, Any]) -> str:
    """動画全体の軸（今日の問い）。無ければサムネの問い・タイトル."""
    return str(ol.get("question") or ol.get("thumb_claim") or ol.get("title") or "")


def _lab(cfg: Config) -> bool:
    return str(cfg.get("video.design", "") or "") == "lab"


# 冒頭と締めのあいさつ（既定はながら聴き）。{name} {theme} {one} {next} {when} を差し込める
# 最初の一言はテーマ（最初の 30 秒で離れる人がいちばん多いので、何の話かを先に言う）
GREETING = [
    ["今日のテーマは、{theme}、です。", 0],
    ["{name}へ、ようこそ。", 1],
    ["画面を見なくても、声だけでわかるように、お話ししますね。", 1],
    ["ながら聴きで大丈夫です。さっそく、始めていきましょう。", 2],
]
GREETING_LINES = ["声だけで、わかるようにお話しします", "ながら聴きで大丈夫です"]
CLOSING = [
    ["今日のひとつは、{one}、でした。", 0],
    ["次回は、{next}のお話です。毎日{when}に更新しています。", 1],
    ["チャンネル登録をしておくと、次のお話も、聴きのがさずに届きます。", 1],
    ["ここまで聴いてくださって、ありがとうございました。", 2],
    ["気になったところだけ、明日ひとつ試してみてください。", 2],
]


def _fill(lines: list, **kw) -> list[list]:
    out = []
    for t, st in lines:
        text = str(t)
        if "{next}" in text and not kw.get("next"):
            text = text.split("次回は")[0] + (f"毎日{kw.get('when', '')}に更新しています。" if "{when}" in text else "")
        try:
            text = text.format(**kw)
        except (KeyError, IndexError):
            pass
        if text.strip():
            out.append([text, int(st)])
    return out


def opening(cfg: Config, ol: dict[str, Any]) -> dict[str, Any]:
    name = str(cfg.get("channel.name", ""))
    theme = str(ol.get("title") or ol.get("theme") or "")      # theme は長い説明文になりがちなので、声と画面はタイトルで
    lines = cfg.get("honpen.greeting") or GREETING
    q = _question(ol)
    if _lab(cfg) and q:
        theme = q.rstrip("？?")                                 # 声も「今日の問いは、〜のか、です。」で軸から始める
        if q.endswith(("？", "?")) and not theme.endswith(("か", "の")):
            theme += "のか"
    parts = ol.get("parts") or []
    return {"kind": "opening", "lines": list(cfg.get("honpen.greeting_lines") or GREETING_LINES), "theme": theme,
            "question": q, "icon": str((parts[0] if parts else {}).get("icon") or ""),
            "narration": _fill(lines, name=name, theme=theme)}


def closing(cfg: Config, ol: dict[str, Any]) -> list[dict[str, Any]]:
    recap = [str(x) for x in (ol.get("recap") or [])][:3]
    one = str(ol.get("today_one") or "")
    nxt = str(ol.get("next") or "")
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    when = times[0] if times else "20:00"
    rec_nar = [["最後に、今日のお話を振り返りますね。", 0]]
    rec_nar += [[f"{['ひとつめ', 'ふたつめ', 'みっつめ'][i]}は、{t}。", i + 1] for i, t in enumerate(recap)]
    end_nar = _fill(cfg.get("honpen.closing") or CLOSING, one=one, next=nxt, when=when)
    return [
        {"kind": "steps", "heading": "今日のまとめ", "heading_hl": "まとめ", "items": recap,
         "final": str(cfg.get("honpen.recap_final", "今日はここまで")), "narration": rec_nar},
        {"kind": "ending", "one": one, "next": nxt, "rest": str(cfg.get("honpen.ending_rest", "")), "narration": end_nar},
    ]


def assemble(cfg: Config, ol: dict[str, Any], parts: list[list[dict[str, Any]]]) -> dict[str, Any]:
    scenes = [opening(cfg, ol)]
    # 章の扉に「今日の検証」の道のり（4 つの見出しと、済んだ部の判定）を持たせる → どこを話しているか迷わない
    road = []
    for k, p in enumerate(parts):
        ch = next((s for s in p if s.get("kind") == "chapter"), {})
        vd = next((s for s in reversed(p) if s.get("kind") == "verdict"), {})
        road.append({"heading": str(ch.get("heading") or (ol.get("parts") or [{}] * (k + 1))[k].get("heading") or ""),
                     "result": str(vd.get("result") or "")})
    for k, p in enumerate(parts):
        for s in p:
            if s.get("kind") == "chapter":
                s["index"] = k
                s["roadmap"] = [{"heading": r["heading"], "result": r["result"] if i < k else ""} for i, r in enumerate(road)]
        scenes += p
    scenes += closing(cfg, ol)
    return {"title": str(ol.get("title") or ""), "question": _question(ol), "hook": "", "scenes": scenes, "outline": ol}


def trim_to(data: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """ナレーションが長すぎる（20 分を超えそうな）とき、説明の場面を後ろの部から 1 つずつ外す.

    章の扉・クイズ・答え合わせ・データ・判定・冒頭・締めは残す（話の軸が切れないように）.
    """
    keep = {"opening", "chapter", "question", "countdown", "result", "data", "verdict", "steps", "ending"}
    scenes = list(data.get("scenes") or [])
    while narration_chars({"scenes": scenes}) > max_chars:
        cand = [i for i, s in enumerate(scenes) if s.get("kind") not in keep]
        if not cand:
            break
        # 同じ部に説明が多いところから（後ろの部ほど先に）
        i = max(cand, key=lambda j: (sum(len(str(t)) for t, _ in scenes[j].get("narration") or []), j))
        log.info("本編が長いので、説明の場面を 1 つ外します: %s", scenes[i].get("heading") or scenes[i].get("kind"))
        scenes.pop(i)
    return dict(data, scenes=scenes)


def narration_chars(data: dict[str, Any]) -> int:
    return sum(len(str(t)) for sc in data.get("scenes") or [] for t, _ in (sc.get("narration") or []))


def write_honpen(cfg: Config, topic: dict[str, Any]) -> dict[str, Any]:
    """本編 JSON（quiz.build(..., wide=True) にそのまま渡せる形）."""
    ol = outline(cfg, topic)
    log.info("本編の骨組み: %s / %d 部", ol.get("title"), len(ol.get("parts") or []))
    lo, hi = int(cfg.get("honpen.part_chars_min", 1600)), int(cfg.get("honpen.part_chars_max", 1900))
    parts = []
    for k in range(len(ol["parts"])):
        sc = write_part(cfg, ol, k, (lo, hi))
        log.info("第%d章: 場面 %d / %d 字", k + 1, len(sc), sum(len(str(t)) for s in sc for t, _ in (s.get("narration") or [])))
        parts.append(sc)
    data = assemble(cfg, ol, parts)
    cap = int(cfg.get("honpen.max_chars", 0) or 0)            # 20 分以内に収める上限（声の速さから決めた字数）
    if cap and narration_chars(data) > cap:
        data = trim_to(data, cap)
    log.info("本編の台本: %d 場面 / ナレーション %d 字", len(data["scenes"]), narration_chars(data))
    return data


# ----------------------------------------------------------------------
# 概要欄・Shorts の切り口・自動サムネ
# ----------------------------------------------------------------------
def _stamp(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def honpen_metadata(cfg: Config, data: dict[str, Any], chapters: list, topic: dict[str, Any] | None = None):
    """本編のタイトル・概要欄（チャプター付き）・タグ."""
    from .metadata import Metadata, _voice_credit, subscribe_line
    ol = data.get("outline") or {}
    prefix = str(cfg.get("honpen.title_prefix", ""))
    suffix = str(cfg.get("upload.title_suffix", ""))
    title = f"{prefix}{data.get('title', '')}{suffix}"[:100]
    times = [str(t) for t in (cfg.get("upload.publish_times_jst", []) or [])]
    parts = [str(ol.get("theme") or data.get("title") or ""),
             str(cfg.get("honpen.desc_note", "通勤や家事をしながらでも聴けるように、画面を見なくても声だけでわかるようにお話ししています。")),
             subscribe_line(cfg)]
    # YouTube のチャプターは 0:00 から始まり、3 つ以上・各 10 秒以上で有効になる
    ch = [(float(t), str(n)) for t, n in chapters if n]
    if len(ch) >= 3:
        ch[0] = (0.0, ch[0][1])
        parts.append("■ もくじ\n" + "\n".join(f"{_stamp(t)} {n}" for t, n in ch))
    if ol.get("today_one"):
        parts.append(f"■ 今日のひとつ\n{ol['today_one']}")
    srcs = [s for s in ((topic or {}).get("sources") or []) if s.get("name")]
    if srcs:
        parts.append("■ 参考\n" + "\n".join(f"・{s['name']} {s.get('url', '')}".rstrip() for s in srcs))
    parts.append(f"{domain.pitch(cfg)}毎日{times[0] if times else '20:00'}に更新しています。")
    parts.append("■ 音声\n" + _voice_credit(cfg))
    parts.append("■ ご注意\n" + domain.disclaimer(cfg))
    tags_h = domain.hashtags(cfg) + [str(t) for t in (cfg.get("honpen.extra_hashtags", ["#聞き流し"]) or [])]
    parts.append(" ".join(tags_h))
    tags = ([str(ol["keyword"])] if ol.get("keyword") else []) + [t.lstrip("#") for t in tags_h] + [str(t) for t in (cfg.get("honpen.extra_tags", ["聞き流し", "作業用", "ながら聴き"]) or [])] + [str(cfg.get("channel.name", ""))]
    return Metadata(title=title, description="\n\n".join(p for p in parts if p)[:5000], tags=list(dict.fromkeys(t for t in tags if t))[:15],
                    category_id=str(cfg.get("upload.category_id", "27")), language=str(cfg.get("upload.language", "ja")))


def short_angles(data: dict[str, Any], n: int = 3) -> list[tuple[str, str]]:
    """本編の章から Shorts の（テーマ, 切り口）を n 個。章が 5 つなら 1・3・5 章のように散らす."""
    ol = data.get("outline") or {}
    parts = ol.get("parts") or []
    if not parts:
        return [(str(data.get("title") or ""), "")] * n
    k = min(n, len(parts))
    idx = sorted({round(i * (len(parts) - 1) / max(1, k - 1)) for i in range(k)}) if k > 1 else [0]
    out = []
    for i in idx[:n]:
        p = parts[i]
        qz = p.get("quiz") or {}
        opts = qz.get("options") or []
        angle = (f"{p.get('heading', '')}。状況: {qz.get('lead', '')}。"
                 + (f"A: {opts[0]} / B: {opts[1]}。" if len(opts) >= 2 else "")
                 + "要点: " + " ".join((p.get("points") or [])[:4]))
        out.append((str(p.get("heading") or data.get("title") or ""), angle))
    # 章より多く作るときは、同じ章の「研究・後半の要点」から別の問いを立てる（前の Shorts とクイズを変える）
    j = 0
    while len(out) < n:
        p = parts[j % len(parts)]
        rest = (p.get("research") or []) + (p.get("points") or [])[2:]
        angle = (f"{p.get('heading', '')}。この章の、前の Shorts とは別の話題で問いを立てる（同じクイズは使わない）。"
                 + ("材料: " + " ".join(str(x) for x in rest[:4]) if rest else "日常の別の場面で、同じ心のくせを確かめる問いにする"))
        out.append((str(p.get("heading") or data.get("title") or ""), angle))
        j += 1
    return out


def thumbnail(cfg: Config, data: dict[str, Any], out) -> Any:
    """手で作ったサムネが無い日の自動サムネ（白地・黒の太字・黄マーカー・青のチャンネル名）。thumbnail.style: lab なら解析画面."""
    if str(cfg.get("thumbnail.style", "") or "") == "lab":
        from . import lab
        ol = data.get("outline") or {}
        return lab.thumbnail(cfg, {**ol, "title": data.get("title") or ol.get("title")}, out)
    from pathlib import Path
    from PIL import Image, ImageDraw
    from . import quiz as quiz_mod
    th = quiz_mod.theme(cfg)
    W, H, M = 1280, 720, 72
    img = Image.new("RGB", (W, H), th.bg)
    d = ImageDraw.Draw(img)
    P = quiz_mod.Parts(cfg, th)
    name = str(cfg.get("channel.name", ""))
    d.text((M, 56), name, font=quiz_mod.font(cfg, 40, 700), fill=th.blue)
    d.rectangle([M, 116, W - M, 120], fill=th.line)
    title = str(data.get("title") or "")
    f, lines = P.fit(d, title, W - M * 2, 96, 700, min_size=56)
    y = 220 if len(lines) > 1 else 280
    for ln in lines:
        P.marker_text(d, M, y, ln, f, p=1.0)
        y += int(f.size * 1.4)
    d.text((M, H - 110), str(cfg.get("honpen.thumb_note", "ながら聴きOK・声だけでわかる")), font=quiz_mod.font(cfg, 40, 500), fill=th.sec)
    out = Path(out)
    img.save(out, quality=92)
    return out
