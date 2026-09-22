"""Claude Code CLI を経由して LLM を呼ぶ（従量課金を避ける経路）.

`ANTHROPIC_API_KEY` を直接使うと、台本1本ごとにトークン課金が発生する。
一方 `claude -p`（headless モード）は、あなたの Claude Code が
すでに認証している枠をそのまま使う。

  - Claude のサブスクリプションで認証している場合
      → 追加の請求は発生しない（プランの利用上限の範囲内）
  - Claude Code 自体を API キーで認証している場合
      → 結局は従量課金になる。この経路にしても安くならない

つまり「サブスクで使っているなら、台本生成のぶんは追加で払わなくて済む」
という話であって、どんな認証状態でも無料になるわけではない。

呼び出しは毎回まっさらなセッションで行う。
このプロジェクトの会話履歴を引き継ぐと、コンテキストが膨らんで
利用枠を無駄に食うため。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from typing import Any

log = logging.getLogger(__name__)

# 台本生成に余計なツールを使わせない。
# ファイルを読み書きされると、生成結果以外の副作用が出て再現性が落ちる。
_DISALLOWED = ["Bash", "Edit", "Write", "Read", "Glob", "Grep",
               "WebFetch", "WebSearch", "Task", "NotebookEdit"]


class ClaudeCodeError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("claude") is not None


FALLBACK_MODEL = "sonnet"
_UNAVAILABLE_HINTS = ("not available", "not_found", "does not exist", "no access", "not supported on your plan",
                      "permission", "invalid model", "unknown model", "model_not_found", "利用できません")


def _model_unavailable(text: str) -> bool:
    t = (text or "").lower()
    return "model" in t and any(h in t for h in _UNAVAILABLE_HINTS)


def _run(prompt: str, system: str, model: str, timeout: int) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise ClaudeCodeError(
            "claude コマンドが見つかりません。\n"
            "Claude Code をインストールするか、config の llm.provider を api に。"
        )

    cmd = [
        exe, "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--system-prompt", system,
        "--max-turns", "1",
        "--session-id", str(uuid.uuid4()),   # 毎回まっさらな文脈で呼ぶ
        "--disallowedTools", *_DISALLOWED,
        "--strict-mcp-config",               # MCP を読み込ませない
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-600:]
        # プランで使えないモデル（Pro で opus など）なら、一段下のモデルで自動的にやり直す
        if _model_unavailable(tail) and model != FALLBACK_MODEL:
            log.warning("モデル %s がこのプランでは使えないようなので %s で続けます: %s",
                        model, FALLBACK_MODEL, tail[-160:])
            return _run(prompt, system, FALLBACK_MODEL, timeout)
        raise ClaudeCodeError(f"claude -p が失敗しました (exit {proc.returncode})\n{tail}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeError(
            f"claude の出力を解釈できませんでした: {exc}\n{proc.stdout[:400]}"
        ) from exc

    if payload.get("is_error"):
        msg = str(payload.get("result", ""))
        if _model_unavailable(msg) and model != FALLBACK_MODEL:
            log.warning("モデル %s がこのプランでは使えないようなので %s で続けます: %s",
                        model, FALLBACK_MODEL, msg[-160:])
            return _run(prompt, system, FALLBACK_MODEL, timeout)
        raise ClaudeCodeError(f"claude がエラーを返しました: {msg}")

    usage = payload.get("usage", {})
    log.debug("claude -p: in=%s out=%s cache_read=%s",
              usage.get("input_tokens"), usage.get("output_tokens"),
              usage.get("cache_read_input_tokens"))
    return str(payload.get("result", ""))


def complete_text(system: str, user: str, *, model: str = "sonnet",
                  timeout: int = 900, **_ignored: Any) -> str:
    return _run(user, system, model, timeout).strip()


def complete_json(system: str, user: str, schema: dict[str, Any], *,
                  model: str = "sonnet", timeout: int = 900,
                  **_ignored: Any) -> dict[str, Any]:
    """JSON を得る.

    CLI には output_config.format のようなスキーマ強制が無いので、
    スキーマをプロンプトに埋めて、返ってきた本文から JSON を取り出す。
    """
    instruction = (
        f"{user}\n\n"
        "---\n"
        "次の JSON Schema に**厳密に**従う JSON だけを出力してください。\n"
        "前置き・説明・コードフェンスを一切付けないこと。\n\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    raw = _run(instruction, system, model, timeout)
    return _extract_json(raw)


def _extract_json(text: str) -> dict[str, Any]:
    """コードフェンスや前置きが付いていても JSON を取り出す."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 最初の { から最後の } までを拾う
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ClaudeCodeError(
                f"JSON を取り出せませんでした: {exc}\n---\n{text[:500]}"
            ) from exc
    raise ClaudeCodeError(f"JSON が含まれていません\n---\n{text[:500]}")
