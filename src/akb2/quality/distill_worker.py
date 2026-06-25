"""Subprocess entrypoint for killable DeepSeek distill extraction."""

from __future__ import annotations

import json
import sys
from typing import Any

from src.akb2.quality.distill import extract_claims


def _payload(success: bool, data: dict[str, Any], retries_used: int, error: str) -> str:
    return json.dumps(
        {
            "success": success,
            "data": data,
            "retries_used": retries_used,
            "error": error,
        },
        ensure_ascii=False,
    )


def main() -> int:
    raw_content = sys.stdin.read()
    try:
        result = extract_claims(raw_content)
    except BaseException as exc:
        print(_payload(False, {}, 0, f"extract_worker_uncaught: {exc}"))
        return 0
    print(
        _payload(
            result.success,
            result.data if isinstance(result.data, dict) else {},
            result.retries_used,
            result.error,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
