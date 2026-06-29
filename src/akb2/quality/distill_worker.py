"""Subprocess entrypoint for killable DeepSeek distill extraction.

stdin 协议（A3-1 抽取子进程过界，step-1）:
    主路径为 JSON payload `{"raw_content": str, "content_lane": str}` —— 携 content_lane
    穿过抽取子进程边界，转交 `extract_claims(raw_content, content_lane=...)`。
    向后兼容：若 stdin 是被 JSON 序列化的裸字符串（`json.dumps(raw_text)`，payload 解析为
    str），视为 raw_content、content_lane=""。非 JSON 的裸文本 → fail-closed 返回错误
    payload（stdin 单次读取无法重读裸文本；生产 runner 已升级为传 JSON payload，
    无裸文本调用方——见 runner._run_extract_claims_worker）。
"""

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


def _decode_payload(payload: Any) -> tuple[str, str]:
    """从已解析的 JSON payload 提取 (raw_content, content_lane)。

    - dict 含 raw_content → 新协议，取 raw_content + content_lane。
    - str → 被 JSON 序列化的裸内容，content_lane=""（向后兼容）。
    - 其他形态 → ("", "")，由调用方按空内容处理（extract 会显式失败）。
    """
    if isinstance(payload, dict) and "raw_content" in payload:
        return str(payload.get("raw_content", "")), str(payload.get("content_lane", ""))
    if isinstance(payload, str):
        return payload, ""
    return "", ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as exc:
        # 非 JSON 裸文本 stdin → fail-closed（无法重读裸文本；生产已传 JSON payload）
        print(_payload(False, {}, 0, f"extract_worker_invalid_stdin: {exc}"))
        return 0

    raw_content, content_lane = _decode_payload(payload)
    try:
        result = extract_claims(raw_content, content_lane=content_lane)
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
