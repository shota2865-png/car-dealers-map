"""Claude API ラッパー.

このプロジェクトで Claude に頼む仕事は3種類しかない:
  1. 話題を選ぶ（JSON）
  2. 台本を書く（JSON）
  3. メタデータ/ファクトチェックを書く（JSON or テキスト）

どれも「長い入力・長い出力」になりうるのでストリーミングを既定にし、
構造化出力は output_config.format(json_schema) でスキーマを強制する。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# どの経路で Claude を呼ぶか
#   claude_code : claude -p を使う。すでに認証している枠を使うので
#                 サブスクリプションなら台本生成に追加の請求が出ない
#   api         : ANTHROPIC_API_KEY で直接叩く。トークン従量課金
#   auto        : claude コマンドがあれば claude_code、無ければ api
PROVIDER_ENV = "YTECON_LLM_PROVIDER"


def _provider() -> str:
    choice = os.environ.get(PROVIDER_ENV, "auto").lower()
    if choice == "auto":
        from . import llm_claudecode
        return "claude_code" if llm_claudecode.available() else "api"
    return choice

# サーバサイド fallback が使えない環境（プロキシ/旧デプロイ）では
# 1度 400 を食らった時点で以降は標準エンドポイントに切り替える
_use_fallbacks = True


class LLMError(RuntimeError):
    pass


def _anthropic():
    import anthropic
    return anthropic


def _client():
    anthropic = _anthropic()
    # ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ant auth profile のいずれかを
    # SDK が自動解決する。明示的にキーを渡す必要はない。
    return anthropic.Anthropic()


def _system_blocks(system: str) -> list[dict[str, Any]]:
    """システムプロンプトはリクエスト間で不変なのでキャッシュさせる."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _final_message(client: anthropic.Anthropic, **kwargs: Any):
    """fallbacks 付き beta ストリーミング → 失敗したら標準ストリーミング."""
    global _use_fallbacks
    if _use_fallbacks:
        try:
            with client.beta.messages.stream(
                betas=[REFUSAL_FALLBACK_BETA], fallbacks="default", **kwargs
            ) as stream:
                return stream.get_final_message()
        except _anthropic().BadRequestError as exc:
            if "fallback" not in str(exc).lower() and "beta" not in str(exc).lower():
                raise
            log.warning("サーバサイドfallbackが使えないため標準経路に切替: %s", exc)
            _use_fallbacks = False
    with client.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


def _check(message: Any) -> None:
    if message.stop_reason == "refusal":
        detail = getattr(message, "stop_details", None)
        raise LLMError(
            "Claude がこのリクエストを拒否しました"
            f"(category={getattr(detail, 'category', None)})。"
            "話題の切り口を変えて再試行してください。"
        )
    if message.stop_reason == "max_tokens":
        raise LLMError("出力が max_tokens に達して途切れました。max_tokens を増やしてください。")


def complete_text(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    max_tokens: int = 32000,
) -> str:
    """自由記述のテキストを1回で得る."""
    if _provider() == "claude_code":
        from . import llm_claudecode
        return llm_claudecode.complete_text(system, user, model=_cli_model(model))
    client = _client()
    message = _final_message(
        client,
        model=model,
        max_tokens=max_tokens,
        system=_system_blocks(system),
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
    )
    _check(message)
    return "".join(b.text for b in message.content if b.type == "text").strip()


def complete_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    max_tokens: int = 32000,
) -> dict[str, Any]:
    """JSON スキーマを強制して構造化データを得る."""
    if _provider() == "claude_code":
        from . import llm_claudecode
        return llm_claudecode.complete_json(system, user, schema,
                                            model=_cli_model(model))
    client = _client()
    message = _final_message(
        client,
        model=model,
        max_tokens=max_tokens,
        system=_system_blocks(system),
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    )
    _check(message)
    text = next((b.text for b in message.content if b.type == "text"), "")
    if not text:
        raise LLMError("空のレスポンスが返りました")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # スキーマ強制下では基本起きない
        raise LLMError(f"JSON を解釈できませんでした: {exc}\n---\n{text[:500]}") from exc


# CLI は API のモデルIDではなく別名を取る
_CLI_MODEL_ALIAS = {
    "claude-opus-5": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-haiku-4-5": "haiku",
}


def _cli_model(model: str) -> str:
    return _CLI_MODEL_ALIAS.get(model, model)


def obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """json_schema の object を組み立てる小道具（additionalProperties:false 必須）."""
    return {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        "additionalProperties": False,
    }


def arr(items: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"type": "array", "items": items, **kw}


STR = {"type": "string"}
NUM = {"type": "number"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}
