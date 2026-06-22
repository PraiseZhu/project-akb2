"""Subprocess entrypoint for killable repair LLM calls."""

from __future__ import annotations

import json
import sys
from typing import Any

from src.akb2.quality.llm_router import route_llm
from src.akb2.quality.repair import (
    REPAIR_MAX_RETRIES,
    REPAIR_MODEL,
    REPAIR_TIMEOUT_S,
)


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
        )
        content = response.content if isinstance(response.content, dict) else {}
        print(_payload(True, content, response.model_used, response.calls_used))
        return 0
    except BaseException as exc:
        print(_payload(False, error=f"repair_worker_uncaught: {exc}"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
