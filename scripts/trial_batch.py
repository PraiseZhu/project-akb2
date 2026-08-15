#!/usr/bin/env python3
"""试投批次 manifest 与残留面处置协议校验。

本脚本校验 trial-batch.json 的结构完整性和 payload 的 schema+车道合规性。
只校验、不生成 payload。payload 由人工写入的完整 harvest JSON v1 组成。

依赖: 复用 akb2.harvest.schema 的 validate + run_lane_check，不另写第二套；
顶层 session_id 身份闸复用 akb2.harvest.assembler.validate_harvest_run_id
（与 schema 侧同一判定函数对象，单一真相源，不另写正则）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

# assembler 内部带 `from src.akb2...` 前缀 import（全仓惯例），要求 repo root
# 在 sys.path。脚本直跑时 sys.path[0] 是 scripts/、测试只注入 src+scripts，
# 都不含 repo root——先幂等注入再 import。
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from akb2.harvest.assembler import validate_harvest_run_id  # noqa: E402
from akb2.harvest.schema import run_lane_check  # noqa: E402
from akb2.harvest.schema import validate as validate_harvest  # noqa: E402

# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

BATCH_VERSION = "1"
PLANNED_COUNT = 3

# attempted 阶段枚举（四阶段）
ATTEMPT_PHASES = ("intent", "in_flight", "settled", "failed")

# attempted status 枚举
ATTEMPT_STATUSES = ("pending", "running", "completed", "failed")

# failed 阶段终态枚举
FAILED_FINAL_STATES = ("submit_error", "duplicate_content")

# reconciliation_state 枚举
RECONCILIATION_STATES = ("not_required", "committed", "blocked")

# CLI exit codes（fail-closed）
CLI_EXIT_OK = 0
CLI_EXIT_INVALID = 2

# ---------------------------------------------------------------------------
# 纯函数校验器
# ---------------------------------------------------------------------------


def _nonempty_str(value: Any, label: str) -> list[str]:
    """单值非空字符串检查；返回违规消息列表。"""
    if not isinstance(value, str):
        return [f"{label}: 期望字符串,实际 {type(value).__name__}"]
    if not value.strip():
        return [f"{label}: 期望非空字符串,实际为空"]
    return []


def _field_type(value: Any, expected: type, label: str) -> list[str]:
    """单值类型检查；返回违规消息列表。"""
    if not isinstance(value, expected):
        return [f"{label}: 期望 {expected.__name__},实际 {type(value).__name__}"]
    return []


def _percent_decode_variants(raw_path: str) -> list[str]:
    """逐层解百分号编码（最多两层，防 %252e 双重编码），返回含原串的变体清单。"""
    variants = [raw_path]
    current = raw_path
    for _ in range(2):
        decoded = unquote(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return variants


def _has_path_traversal(raw_path: str) -> bool:
    """路径逃逸检测：按 '/' 逐段比对是否存在 '..' 段。

    不依赖单一子串匹配（`/../` 或 `endswith('/..')`）——那类检查对
    `%2e%2e`（百分号编码）或未被 '/' 包围的边界形态（如裸 `..` 段）
    存在漏判空间。这里对原串与逐层百分号解码后的变体都做逐段扫描。
    """
    for candidate in _percent_decode_variants(raw_path):
        if any(seg == ".." for seg in candidate.split("/")):
            return True
    return False


def _validate_session_id_identity(
    sid: Any, payload_sid: Any, label: str
) -> list[str]:
    """顶层 session_id 身份闸：小写 canonical UUID4 + 与内嵌 payload 逐字同源。

    UUID4 判定复用 assembler.validate_harvest_run_id（与 schema 侧同一函数
    对象，单一真相源，不另写正则）。违规进校验清单（fail-closed 同级）。
    payload_sid 为 None 表示该条目无内嵌 payload（attempted 条目），
    同源检查自然跳过，UUID4 闸仍生效。
    """
    violations: list[str] = []
    if isinstance(sid, str) and not validate_harvest_run_id(sid):
        violations.append(
            f"{label}.session_id: 期望小写 canonical UUID4,实际 {sid!r}"
        )
    if payload_sid is not None and sid != payload_sid:
        violations.append(
            f"{label}.session_id({sid!r}): 与内嵌 payload session_id"
            f"({payload_sid!r}) 不同源"
        )
    return violations


def _validate_planned_item(item: Any, idx: int) -> list[str]:
    """校验单个 planned 条目：session_id 身份闸+title 非空 + payload 走 schema+车道校验。"""
    violations: list[str] = []
    label = f"planned[{idx}]"
    if not isinstance(item, dict):
        return [f"{label}: 期望对象,实际 {type(item).__name__}"]

    violations.extend(_nonempty_str(item.get("session_id"), f"{label}.session_id"))
    violations.extend(_nonempty_str(item.get("title"), f"{label}.title"))

    payload = item.get("payload")
    if payload is None:
        violations.append(f"{label}.payload: 期望 harvest JSON v1,实际缺失")
        violations.extend(
            _validate_session_id_identity(
                item.get("session_id"), None, label
            )
        )
    elif not isinstance(payload, dict):
        violations.append(f"{label}.payload: 期望对象,实际 {type(payload).__name__}")
        violations.extend(
            _validate_session_id_identity(
                item.get("session_id"), None, label
            )
        )
    else:
        payload_sid = payload.get("session_id")
        violations.extend(
            _validate_session_id_identity(
                item.get("session_id"), payload_sid, label
            )
        )
        # 复用 akb2.harvest.schema 校验器（硬不变量 7）
        violations.extend(validate_harvest(payload))
        if not violations:
            ok, _code, msg = run_lane_check(payload)
            if not ok:
                violations.append(f"{label}.payload 车道校验: {msg}")

    return violations


def _validate_attempted_item(item: Any, idx: int) -> list[str]:
    """校验单个 attempted 条目（含阶段特定字段）。

    四阶段共享字段：harvest_run_id / status / session_id / expected_slug / intent_timestamp。
    阶段特定字段：
    - intent: raw_path/hash/终态必须为 null
    - in_flight: 补 raw_path(绝对,无逃逸)/hash[:16]/投递时刻/deadline/reconciliation_state
    - settled: 必填终态 + reconciliation_state
    - failed: 必填终态(∈ {submit_error, duplicate_content}) + reconciliation_state
    """
    violations: list[str] = []
    label = f"attempted[{idx}]"
    if not isinstance(item, dict):
        return [f"{label}: 期望对象,实际 {type(item).__name__}"]

    # phase 枚举
    phase = item.get("phase")
    violations.extend(_nonempty_str(phase, f"{label}.phase"))
    if isinstance(phase, str) and phase not in ATTEMPT_PHASES:
        expected = "|".join(ATTEMPT_PHASES)
        violations.append(f"{label}.phase: 期望值 ∈ {{{expected}}},实际 {phase!r}")

    # status 校验
    status = item.get("status")
    violations.extend(_nonempty_str(status, f"{label}.status"))
    if isinstance(status, str) and status not in ATTEMPT_STATUSES:
        expected = "|".join(ATTEMPT_STATUSES)
        violations.append(f"{label}.status: 期望值 ∈ {{{expected}}},实际 {status!r}")

    # 所有阶段共享字段
    violations.extend(
        _nonempty_str(item.get("harvest_run_id"), f"{label}.harvest_run_id")
    )
    violations.extend(_nonempty_str(item.get("session_id"), f"{label}.session_id"))
    # 身份闸：顶层 session_id 必须小写 canonical UUID4（与 planned 同级）。
    # attempted 条目无内嵌 payload，同源检查不适用（payload_sid=None）。
    violations.extend(
        _validate_session_id_identity(item.get("session_id"), None, label)
    )
    violations.extend(
        _nonempty_str(item.get("expected_slug"), f"{label}.expected_slug")
    )
    violations.extend(
        _nonempty_str(item.get("intent_timestamp"), f"{label}.intent_timestamp")
    )

    # 阶段特定字段校验
    if phase == "intent":
        # intent: raw_path / hash / 终态必须为 null
        if item.get("raw_path") is not None:
            violations.append(
                f"{label}.raw_path: intent 阶段期望 null,实际 {item['raw_path']!r}"
            )
        if item.get("pipeline_content_hash") is not None:
            violations.append(
                f"{label}.pipeline_content_hash: intent 阶段期望 null,"
                f"实际 {item['pipeline_content_hash']!r}"
            )
        if item.get("final_state") is not None:
            violations.append(
                f"{label}.final_state: intent 阶段期望 null,"
                f"实际 {item['final_state']!r}"
            )

    elif phase in ("in_flight", "settled", "failed"):
        # in_flight 及以上阶段：raw_path / hash / 投递时刻 / deadline 必须填
        raw_path = item.get("raw_path")
        violations.extend(_nonempty_str(raw_path, f"{label}.raw_path"))
        if isinstance(raw_path, str):
            if not raw_path.startswith("/"):
                violations.append(
                    f"{label}.raw_path: 期望绝对路径,实际 {raw_path!r}"
                )
            if _has_path_traversal(raw_path):
                violations.append(
                    f"{label}.raw_path: 存在路径逃逸({raw_path!r}),不允许使用 '..'"
                )

        content_hash = item.get("pipeline_content_hash")
        violations.extend(
            _nonempty_str(content_hash, f"{label}.pipeline_content_hash")
        )
        if isinstance(content_hash, str) and len(content_hash) != 16:
            violations.append(
                f"{label}.pipeline_content_hash: 期望 16 字符,"
                f"实际 {len(content_hash)}"
            )

        violations.extend(
            _nonempty_str(
                item.get("delivery_timestamp"), f"{label}.delivery_timestamp"
            )
        )
        violations.extend(
            _nonempty_str(item.get("deadline"), f"{label}.deadline")
        )

        # reconciliation_state
        rc = item.get("reconciliation_state")
        violations.extend(
            _nonempty_str(rc, f"{label}.reconciliation_state")
        )
        if isinstance(rc, str) and rc not in RECONCILIATION_STATES:
            expected = "|".join(RECONCILIATION_STATES)
            violations.append(
                f"{label}.reconciliation_state: 期望值 ∈ {{{expected}}},"
                f"实际 {rc!r}"
            )

        # settled / failed 必填终态
        if phase in ("settled", "failed"):
            fs = item.get("final_state")
            violations.extend(_nonempty_str(fs, f"{label}.final_state"))

            # failed 阶段终态枚举
            if phase == "failed" and isinstance(fs, str):
                if fs not in FAILED_FINAL_STATES:
                    expected = "|".join(FAILED_FINAL_STATES)
                    violations.append(
                        f"{label}.final_state: failed 阶段期望值 ∈ {{{expected}}},"
                        f"实际 {fs!r}"
                    )
                # duplicate_content 必须带首投时间与旧 run_id
                if fs == "duplicate_content":
                    violations.extend(
                        _nonempty_str(
                            item.get("first_submitted_at"),
                            f"{label}.first_submitted_at",
                        )
                    )
                    violations.extend(
                        _nonempty_str(
                            item.get("previous_run_id"),
                            f"{label}.previous_run_id",
                        )
                    )

    return violations


def validate_trial_batch(batch: Any) -> list[str]:
    """校验 trial batch manifest 的结构完整性 + payload 合规性。

    Args:
        batch: trial batch manifest（dict）或任意值。

    Returns:
        违规清单（list[str]）；空列表 = 合规。
    """
    violations: list[str] = []
    if not isinstance(batch, dict):
        return [f"batch: 期望对象,实际 {type(batch).__name__}"]

    # version
    version = batch.get("version")
    if version != BATCH_VERSION:
        violations.append(
            f"version: 期望值 {BATCH_VERSION!r},实际 {version!r}"
        )

    # planned — 恰好 3 篇
    planned = batch.get("planned")
    if not isinstance(planned, list):
        violations.append(f"planned: 期望列表,实际 {type(planned).__name__}")
    else:
        if len(planned) != PLANNED_COUNT:
            violations.append(
                f"planned: 期望恰好 {PLANNED_COUNT} 篇,实际 {len(planned)} 篇"
            )
        for idx, item in enumerate(planned):
            violations.extend(_validate_planned_item(item, idx))

    # attempted — 不限制条目数
    attempted = batch.get("attempted")
    if not isinstance(attempted, list):
        violations.append(
            f"attempted: 期望列表,实际 {type(attempted).__name__}"
        )
    else:
        # 收集 planned session_id 用于 ⊆ 检查
        planned_session_ids: set[str] = set()
        if isinstance(planned, list):
            for item in planned:
                if isinstance(item, dict):
                    sid = item.get("session_id")
                    if isinstance(sid, str):
                        planned_session_ids.add(sid)

        # 去重追踪
        seen_run_ids: set[str] = set()
        seen_hashes: set[str] = set()

        for idx, item in enumerate(attempted):
            violations.extend(_validate_attempted_item(item, idx))

            if not isinstance(item, dict):
                continue

            # attempted ⊆ planned
            sid = item.get("session_id")
            if isinstance(sid, str) and sid not in planned_session_ids:
                violations.append(
                    f"attempted[{idx}].session_id({sid!r}): 不在 planned 中"
                )

            # harvest_run_id 全局唯一
            rid = item.get("harvest_run_id")
            if isinstance(rid, str):
                if rid in seen_run_ids:
                    violations.append(
                        f"attempted[{idx}].harvest_run_id({rid!r}): 重复,"
                        f"与其他 attempted 冲突"
                    )
                seen_run_ids.add(rid)

            # pipeline_content_hash 全局唯一（非 null）
            h = item.get("pipeline_content_hash")
            if isinstance(h, str):
                if h in seen_hashes:
                    violations.append(
                        f"attempted[{idx}].pipeline_content_hash({h!r}): 重复,"
                        f"与其他 attempted 冲突"
                    )
                seen_hashes.add(h)

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读 trial-batch.json → 校验结构+payload → fail-closed。

    Returns:
        exit code: 0 = 通过；2 = 任一校验失败。
    """
    parser = argparse.ArgumentParser(
        prog="python3 scripts/trial_batch.py",
        description="试投批次 manifest 校验（fail-closed，零产出）",
    )
    parser.add_argument("file", help="trial-batch.json 文件路径")
    args = parser.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            batch = json.load(fh)
    except FileNotFoundError:
        print(f"trial_batch: 文件不可读: {args.file}", file=sys.stderr)
        return CLI_EXIT_INVALID
    except json.JSONDecodeError as exc:
        print(f"trial_batch: JSON 非法: {exc}", file=sys.stderr)
        return CLI_EXIT_INVALID

    violations = validate_trial_batch(batch)
    if violations:
        for v in violations:
            print(f"trial_batch: {v}", file=sys.stderr)
        print("trial_batch: 校验失败,零文件产出", file=sys.stderr)
        return CLI_EXIT_INVALID

    print("trial_batch: 校验通过", file=sys.stderr)
    return CLI_EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
