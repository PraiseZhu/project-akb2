"""Subprocess entrypoint for killable DeepSeek distill extraction.

stdin 协议(A3-1 抽取子进程过界,step-1):
    主路径为 JSON payload `{"raw_content": str, "content_lane": str}` —— 携 content_lane
    穿过抽取子进程边界,转交 `extract_claims(raw_content, content_lane=...)`。
    向后兼容:若 stdin 是被 JSON 序列化的裸字符串(`json.dumps(raw_text)`,payload 解析为
    str),视为 raw_content、content_lane=""。非 JSON 的裸文本 → fail-closed 返回错误
    payload(stdin 单次读取无法重读裸文本;生产 runner 已升级为传 JSON payload,
    无裸文本调用方——见 runner._run_extract_claims_worker)。

F2-B traceability retake mode:
    payload `{"mode": "traceability_retake", "extract_data": dict, "raw_content": str,
    "content_lane": str}` → 调 `distill.retake_traceability`(返回更新后的 extract_data 或 None)。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.akb2.quality.distill import extract_claims, retake_traceability


def _payload(
    success: bool,
    data: dict[str, Any],
    retries_used: int,
    error: str,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """F3-A fail-visible:payload 带 diagnostics(interp_count/llm_called/items_returned/
    accepted_count/reject_reason),供 ledger 可见。"""
    out = {
        "success": success,
        "data": data,
        "retries_used": retries_used,
        "error": error,
    }
    if diagnostics is not None:
        out["diagnostics"] = diagnostics
    return json.dumps(out, ensure_ascii=False)


def _decode_payload(payload: Any) -> tuple[str, str]:
    """从已解析的 JSON payload 提取 (raw_content, content_lane)。

    - dict 含 raw_content → 新协议,取 raw_content + content_lane。
    - str → 被 JSON 序列化的裸内容,content_lane=""(向后兼容)。
    - 其他形态 → ("", ""),由调用方按空内容处理(extract 会显式失败)。
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
        # 非 JSON 裸文本 stdin → fail-closed(无法重读裸文本;生产已传 JSON payload)
        print(_payload(False, {}, 0, f"extract_worker_invalid_stdin: {exc}"))
        return 0

    # F2-B: traceability retake mode
    if (
        isinstance(payload, dict)
        and payload.get("mode") == "traceability_retake"
        and isinstance(payload.get("extract_data"), dict)
    ):
        raw_content = str(payload.get("raw_content", ""))
        content_lane = str(payload.get("content_lane", ""))
        extract_data = payload["extract_data"]
        try:
            # F3-A: retake_traceability 返回 {"changed", "data", "diagnostics"}
            result = retake_traceability(extract_data, raw_content, content_lane)
        except BaseException as exc:
            print(_payload(False, {}, 0, f"retake_worker_uncaught: {exc}"))
            return 0
        if not isinstance(result, dict):
            print(_payload(False, {}, 0, "retake_invalid_result"))
            return 0
        diag = result.get("diagnostics", {}) if isinstance(result.get("diagnostics"), dict) else {}
        if result.get("changed"):
            print(_payload(True, result.get("data", {}), 0, "", diagnostics=diag))
        else:
            print(_payload(False, {}, 0, "retake_no_change", diagnostics=diag))
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
