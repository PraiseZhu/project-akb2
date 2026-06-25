"""Subprocess entrypoint for killable repair LLM calls."""

from __future__ import annotations

import json
import sys
from typing import Any

from src.akb2.quality.llm_router import route_llm
from src.akb2.quality.repair import (
    REPAIR_DISABLE_THINKING,
    REPAIR_MAX_RETRIES,
    REPAIR_MAX_TOKENS,
    REPAIR_MODEL,
    REPAIR_TIMEOUT_S,
)


def _coerce_patch_dict(content: Any) -> dict[str, Any]:
    """把 route_llm 返回的 content 归一成 section-patch dict。

    strict_schema=False 且无 response_schema 时 route_llm 返回原始字符串,
    需在此解析为 dict(section_title -> new_body)。兼容裸 JSON 与 ```json 围栏。
    空/非法 JSON/非 dict → 返回 {},由上层 _apply_section_patches 当"无补丁"处理
    (未点名节逐字节不变),fail-safe 不崩(宪法 P4 失败可见: 空 patch 即不收敛)。
    """
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {}
    text = content.strip()
    if not text:
        return {}
    if text.startswith("```"):
        # 去掉 ```json / ``` 围栏
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _payload(
    success: bool,
    content: dict[str, Any] | None = None,
    model_used: str = "",
    calls_used: int = 0,
    error: str = "",
) -> str:
    return json.dumps(
        {
            "success": success,
            "content": content or {},
            "model_used": model_used,
            "calls_used": calls_used,
            "error": error,
        },
        ensure_ascii=False,
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")
        response = route_llm(
            model_family=REPAIR_MODEL,
            messages=messages,
            timeout_s=REPAIR_TIMEOUT_S,
            max_retries=REPAIR_MAX_RETRIES,
            reasoning_effort=None,
            strict_schema=False,
            max_tokens=REPAIR_MAX_TOKENS,
            extra_body=(
                {"enable_thinking": False} if REPAIR_DISABLE_THINKING else None
            ),
        )
        # repair 返回字符串 JSON(strict_schema=False 不解析),这里解析成 dict patch。
        # 关思维链后 content 落 patch 而非 reasoning;空/非法 JSON → 空 dict,
        # 由上层 _apply_section_patches 当无补丁处理(未点名节逐字节不变),不崩。
        content = _coerce_patch_dict(response.content)
        print(_payload(True, content, response.model_used, response.calls_used))
        return 0
    except BaseException as exc:
        print(_payload(False, error=f"repair_worker_uncaught: {exc}"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
