"""Smoke validator tests for isolated AKB2 main-pipeline runs."""

from __future__ import annotations

import json
import os
import plistlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.akb2.quality.smoke_validator import (
    SmokeValidationError,
    validate_distill_failure,
    validate_env_parity,
    validate_happy_path,
    validate_launchd_static,
    validate_root_isolation,
    validate_source_ledger,
)


def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _row(event: str, node: str, details: dict) -> dict:
    return {
        "loop_id": "loop1",
        "node": node,
        "event": event,
        "content_hash": "hash",
        "ts": "2026-06-20T00:00:00Z",
        "details": details,
    }


def _valid_candidate_text() -> str:
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/a--raw--2026.md
gate_reason: 待评审
future_questions:
  - Loop 工程如何适配实时系统？
  - 多 agent 协作时如何隔离失败？
related_concepts:
  - harness-engineering
  - agent-collaboration
artifact_type: wiki
pipeline_version: v1
---

## TLDR

Loop Engineering 是一种设计有界、收敛、可观测 AI Agent 循环的工程实践 [raw §A]。

## 关键发现

1. [raw §A] (source_grounded) 每个 loop 必须有显式停止条件
2. [raw §C] (source_grounded) 定点修复优于整体重跑

## 入选理由

[raw §B] (source_grounded) 本文提供了完整的 loop 设计模板，填补了 AKB 在 agent 工程方法论的空白。

## 证据地图

| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| Loop 五组件 | §B | strong | (source_grounded) 实时系统场景 |

## 核心论点

1. [raw §A] (source_grounded) 有界循环设计：每个 loop 必须有显式停止条件
2. [raw §B] (source_grounded) 五组件模板：trigger, selector, executor, gate, ledger
3. [raw §C] (source_grounded) 收敛优先：定点修复成本远低于整体重跑

## 论证与解释

**[raw §B]** (source_grounded) 来源事实：Loop engineering 的核心洞察是先写账本再执行，崩溃恢复只需读最后一条账本记录。

### 操作步骤

1. [raw §B] (akb_interpretation) Configure trigger type `trigger={type:'schedule',cron:'0 * * * *'}`
2. [raw §B] (akb_interpretation) Select the next work item `work_item=ledger.next(status='pending',limit=1)`
3. [raw §B] (akb_interpretation) Run the quality gate `assert run_quality_gate(result,grader='gpt-5.4').defects == []`

## 适用边界

- [raw §A] (source_grounded) 来源事实：原文把任务从 prompt 上移为 loop/system 设计。
- [raw §B] (source_grounded) 来源事实：原文列出 trigger、selector、executor、gate、ledger。
- [raw §A] (source_grounded) 来源事实：Loop engineering 是有界、收敛、可观测的工程实践。
- [raw §C] (akb_interpretation) 不适用场景：简单 CRUD 脚本。
- [raw §B] (akb_interpretation) 约束条件：需要显式账本、质量门和人工复核入口。

## 图谱关系

| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| harness-engineering | related | weak | source_grounded | [raw §B] (source_grounded) 原文明示 loop 包含 trigger、selector、executor、gate、ledger。 |
| agent-collaboration | related | weak | source_grounded | [raw §B] (source_grounded) 原文列出 trigger、selector、executor、gate、ledger。 |

## Future Questions

1. Loop 工程如何适配实时系统？
2. 多 agent 协作时如何隔离失败？

## 维护记录

- 2026-06-20: 初始创建（待评审）
"""


def _valid_raw_text() -> str:
    return """---
artifact_type: raw
source_slug: a
source_url: https://example.com/a
source_title: A
source_authors: Test
source_publisher: Test
source_published_at: 2026-06-20
fetched_at: 2026-06-20T00:00:00Z
fetched_via: fixture
---

# §A Loop Engineering

Loop engineering is bounded, convergent, and observable.

# §B Complete Loop Shape

A loop has trigger, selector, executor, gate, and ledger.

原文操作示例（AKB 操作化命令字面量，供 SG-command-recheck 回查锚定）：

```text
trigger={type:'schedule',cron:'0 * * * *'}
work_item=ledger.next(status='pending',limit=1)
assert run_quality_gate(result,grader='gpt-5.4').defects == []
```

# §C Convergence

Fix defects instead of restarting the entire pipeline.
"""


def _write_candidate_fixture(vault: Path) -> tuple[Path, Path]:
    raw = vault / "raw" / "a--raw--2026.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(_valid_raw_text(), encoding="utf-8")
    candidate = vault / "staging" / "candidate" / "batch" / "a.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(_valid_candidate_text(), encoding="utf-8")
    return raw, candidate


def _write_manifest(vault: Path, candidate: Path) -> None:
    manifest = vault / "manifests" / "ingest" / "2026-06-20.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "candidate_path": str(candidate.relative_to(vault)),
        "proposed_gate_decision": "active",
        "decision_confidence": "high",
        "graph_readiness": "real_link",
        "not_active_promotion": True,
    }
    _write_ledger(manifest, [record])


def _write_grader_pass(vault: Path, candidate: Path) -> Path:
    grader = vault / "staging" / "grader-results" / f"{candidate.stem}.grader.json"
    grader.parent.mkdir(parents=True, exist_ok=True)
    grader.write_text(
        json.dumps(
            {
                "status": "PASS",
                "dimensions_checked": 10,
                "findings": [],
                "model_used": "gpt-5.4",
                "primary_model": "gpt-5.4",
                "fallback_used": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return grader


def _write_plist(
    path: Path,
    script: Path,
    workdir: Path,
    log_path: Path,
    *,
    auto_promote: str = "0",
    amendment: str = "0",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": "com.akb2.main-pipeline",
        "ProgramArguments": ["/bin/bash", str(script)],
        "WorkingDirectory": str(workdir),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {
            "AKB2_AUTO_PROMOTE": auto_promote,
            "AKB2_AMENDMENT_APPLIED": amendment,
        },
    }
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)


def _write_source_config(path: Path) -> None:
    path.write_text(
        """sources:
  - slug: s1
    feed_url: https://example.com/feed.xml
    listing_url: https://example.com
  - slug: s2
    feed_url: ""
    listing_url: https://example.org
""",
        encoding="utf-8",
    )


def _source_rows() -> list[dict]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for i in range(82):
        rows.append(
            {
                "slug": f"s{i}",
                "url": f"https://source{i}.example.com",
                "feed": f"https://source{i}.example.com/feed.xml",
                "method": "live",
                "run_id": "run-1",
                "reachable": True,
                "trial_fetch": f"https://source{i}.example.com/post",
                "latest_post": f"https://source{i}.example.com/post",
                "validated_at": now,
                "failure_class": "",
                "error": "",
            }
        )
    rows[0]["slug"] = "s1"
    rows[0]["feed"] = "https://example.com/feed.xml"
    rows[0]["url"] = "https://example.com"
    rows[1]["slug"] = "s2"
    rows[1]["feed"] = ""
    rows[1]["url"] = "https://example.org"
    return rows


def _write_source_config_for_rows(path: Path, rows: list[dict]) -> None:
    lines = ["sources:"]
    for row in rows:
        if row.get("feed"):
            lines.extend(
                [
                    f"  - slug: {row['slug']}",
                    f"    feed_url: {row['feed']}",
                    f"    listing_url: {row['url']}",
                ]
            )
        else:
            lines.extend(
                [
                    f"  - slug: {row['slug']}",
                    '    feed_url: ""',
                    f"    listing_url: {row['url']}",
                ]
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_validate_distill_failure_gate(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    _write_ledger(
        ledger,
        [
            _row("intake_raw", "intake_raw", {"raw_path": str(vault / "raw/a--raw--2026.md")}),
            _row(
                "distill_extract",
                "distill_extract",
                {
                    "status": "start",
                    "model_family": "deepseek-v4-pro",
                    "timeout_s": 60,
                    "max_retries": 0,
                },
            ),
            _row(
                "abort",
                "distill_extract",
                {
                    "error": "timeout",
                    "error_class": "network_timeout",
                    "model_family": "deepseek-v4-pro",
                    "timeout_s": 60,
                    "max_retries": 0,
                    "retries_used": 3,
                },
            ),
        ],
    )

    result = validate_distill_failure(ledger, vault)

    assert result["passed"] is True
    assert result["abort_error_class"] == "network_timeout"


def test_validate_distill_failure_rejects_downstream_grade(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    _write_ledger(
        ledger,
        [
            _row("distill_extract", "distill_extract", {"status": "start"}),
            _row(
                "abort",
                "distill_extract",
                {
                    "error_class": "network_timeout",
                    "model_family": "deepseek-v4-pro",
                    "timeout_s": 60,
                    "max_retries": 0,
                    "retries_used": 3,
                },
            ),
            _row("grade_llm", "grade_llm", {"model_used": "gpt-5.4"}),
        ],
    )

    with pytest.raises(SmokeValidationError, match="downstream"):
        validate_distill_failure(ledger, vault)


def test_validate_happy_path_gate(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    raw, candidate = _write_candidate_fixture(vault)
    _write_manifest(vault, candidate)
    _write_grader_pass(vault, candidate)
    _write_ledger(
        ledger,
        [
            _row("intake_raw", "intake_raw", {"raw_path": str(raw)}),
            _row(
                "distill_compose",
                "distill_compose",
                {"candidate_path": str(candidate)},
            ),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 0,
                    "suspended": False,
                },
            ),
            _row("promote", "promote", {"mode": "proposal"}),
            _row("finalize", "finalize", {"mode": "proposal"}),
        ],
    )

    result = validate_happy_path(
        ledger,
        vault,
        require_candidate_quality=True,
        require_proposal_manifest=True,
    )

    assert result["passed"] is True
    assert result["promotion_mode"] == "proposal"
    assert result["structural_defects"] == 0
    assert result["proposal_decision"] == "active"
    assert result["grader_result_status"] == "PASS"


def _happy_ledger(vault: Path) -> Path:
    """构造一条最小 happy-path ledger（含 grader PASS + manifest），返回 ledger 路径。"""
    ledger = vault / "staging" / "quality-ledger.jsonl"
    raw, candidate = _write_candidate_fixture(vault)
    _write_manifest(vault, candidate)
    _write_grader_pass(vault, candidate)
    _write_ledger(
        ledger,
        [
            _row("intake_raw", "intake_raw", {"raw_path": str(raw)}),
            _row("distill_compose", "distill_compose", {"candidate_path": str(candidate)}),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 0,
                    "suspended": False,
                },
            ),
            _row("promote", "promote", {"mode": "proposal"}),
            _row("finalize", "finalize", {"mode": "proposal"}),
        ],
    )
    return ledger


def test_validate_happy_path_rejects_changed_active_tree(tmp_path: Path) -> None:
    """proposal smoke 最该保证「绝不碰 active 库」：active tree hash 变了必须 fail。

    回归前：所有 happy-path 测试都不传 active_tree_before/after → 早退分支永不执行，
    比较逻辑（smoke_validator._assert_active_tree_unchanged）坏了也无人发现。本测试
    传不同 hash 证伪：相同输入下，hash 不同必须抛 SmokeValidationError。
    """
    vault = tmp_path / "AKB2.0"
    ledger = _happy_ledger(vault)
    before = tmp_path / "active-before.txt"
    after = tmp_path / "active-after.txt"
    before.write_text("hash_AAAA\n", encoding="utf-8")
    after.write_text("hash_BBBB\n", encoding="utf-8")  # active 库被改动 → hash 变

    with pytest.raises(SmokeValidationError, match="active tree hash changed"):
        validate_happy_path(
            ledger,
            vault,
            require_candidate_quality=True,
            require_proposal_manifest=True,
            active_tree_before=before,
            active_tree_after=after,
        )


def test_validate_happy_path_accepts_unchanged_active_tree(tmp_path: Path) -> None:
    """active tree hash 未变（before==after）时 happy-path 正常通过，不误报。"""
    vault = tmp_path / "AKB2.0"
    ledger = _happy_ledger(vault)
    before = tmp_path / "active-before.txt"
    after = tmp_path / "active-after.txt"
    before.write_text("hash_SAME\n", encoding="utf-8")
    after.write_text("hash_SAME\n", encoding="utf-8")

    result = validate_happy_path(
        ledger,
        vault,
        require_candidate_quality=True,
        require_proposal_manifest=True,
        active_tree_before=before,
        active_tree_after=after,
    )
    assert result["passed"] is True


def test_validate_happy_path_normalizes_tmp_symlink_root(tmp_path: Path) -> None:
    """macOS /tmp -> /private/tmp should not break manifest matching."""
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    raw, candidate = _write_candidate_fixture(vault)
    _write_manifest(vault, candidate)
    _write_grader_pass(vault, candidate)
    real_candidate = candidate.resolve()
    _write_ledger(
        ledger,
        [
            _row("intake_raw", "intake_raw", {"raw_path": str(raw.resolve())}),
            _row(
                "distill_compose",
                "distill_compose",
                {"candidate_path": str(real_candidate)},
            ),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 0,
                    "suspended": False,
                },
            ),
            _row("promote", "promote", {"mode": "proposal"}),
            _row("finalize", "finalize", {"mode": "proposal"}),
        ],
    )

    result = validate_happy_path(
        ledger,
        vault,
        require_candidate_quality=True,
        require_proposal_manifest=True,
    )

    assert result["passed"] is True


def test_validate_happy_path_accepts_repaired_final_pass(tmp_path: Path) -> None:
    """Initial grader defects are allowed only when final grader result is PASS."""
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    raw, candidate = _write_candidate_fixture(vault)
    _write_manifest(vault, candidate)
    _write_grader_pass(vault, candidate)
    _write_ledger(
        ledger,
        [
            _row("intake_raw", "intake_raw", {"raw_path": str(raw)}),
            _row("distill_compose", "distill_compose", {"candidate_path": str(candidate)}),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 0,
                    "initial_defect_count": 3,
                    "suspended": False,
                },
            ),
            _row("repair_loop", "repair_loop", {"converged": True, "repair_rounds": 2}),
            _row("promote", "promote", {"mode": "proposal"}),
        ],
    )

    result = validate_happy_path(
        ledger,
        vault,
        require_candidate_quality=True,
        require_proposal_manifest=True,
    )

    assert result["initial_defect_count"] == 3
    assert result["grader_result_status"] == "PASS"


def test_validate_happy_path_rejects_final_llm_defects(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    _raw, candidate = _write_candidate_fixture(vault)
    _write_grader_pass(vault, candidate)
    _write_ledger(
        ledger,
        [
            _row("distill_compose", "distill_compose", {"candidate_path": str(candidate)}),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 1,
                    "suspended": False,
                },
            ),
            _row("promote", "promote", {"mode": "proposal"}),
        ],
    )

    with pytest.raises(SmokeValidationError, match="defect_count"):
        validate_happy_path(ledger, vault)


def test_validate_happy_path_rejects_grader_fallback(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    _raw, candidate = _write_candidate_fixture(vault)
    _write_grader_pass(vault, candidate)
    _write_ledger(
        ledger,
        [
            _row(
                "distill_compose",
                "distill_compose",
                {"candidate_path": str(candidate)},
            ),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gemini-3.5-flash",
                    "fallback_used": True,
                    "defect_count": 0,
                    "suspended": False,
                },
            ),
        ],
    )

    with pytest.raises(SmokeValidationError, match="gpt-5.4"):
        validate_happy_path(ledger, vault)


def test_validate_happy_path_rejects_missing_candidate_quality(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    raw, candidate = _write_candidate_fixture(vault)
    _write_grader_pass(vault, candidate)
    candidate.write_text("## TLDR\nmissing contract [raw §A]\n", encoding="utf-8")
    _write_ledger(
        ledger,
        [
            _row("intake_raw", "intake_raw", {"raw_path": str(raw)}),
            _row("distill_compose", "distill_compose", {"candidate_path": str(candidate)}),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 0,
                    "suspended": False,
                },
            ),
            _row("promote", "promote", {"mode": "proposal"}),
        ],
    )

    with pytest.raises(SmokeValidationError, match="candidate self-gate"):
        validate_happy_path(ledger, vault, require_candidate_quality=True)


def test_validate_happy_path_rejects_non_proposal_mode(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    _raw, candidate = _write_candidate_fixture(vault)
    _write_grader_pass(vault, candidate)
    _write_ledger(
        ledger,
        [
            _row("distill_compose", "distill_compose", {"candidate_path": str(candidate)}),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 0,
                    "suspended": False,
                },
            ),
            _row("promote", "promote", {"mode": "active"}),
        ],
    )

    with pytest.raises(SmokeValidationError, match="proposal mode"):
        validate_happy_path(ledger, vault)


def test_validate_rejects_real_vault_paths(tmp_path: Path) -> None:
    vault = tmp_path / "AKB2.0"
    ledger = vault / "staging" / "quality-ledger.jsonl"
    _write_ledger(
        ledger,
        [
            _row(
                "distill_compose",
                "distill_compose",
                {
                    "candidate_path": (
                        "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/"
                        "Praise Space/AKB2.0/staging/candidate/a.md"
                    )
                },
            ),
            _row(
                "grade_llm",
                "grade_llm",
                {
                    "model_used": "gpt-5.4",
                    "fallback_used": False,
                    "defect_count": 0,
                    "suspended": False,
                },
            ),
        ],
    )

    with pytest.raises(SmokeValidationError, match="real vault"):
        validate_happy_path(ledger, vault)


def test_validate_source_ledger_gate(tmp_path: Path) -> None:
    rows = _source_rows()
    ledger = tmp_path / "sources-ledger.jsonl"
    config = tmp_path / "main_sources.yaml"
    _write_ledger(ledger, rows)
    _write_source_config_for_rows(config, rows)

    result = validate_source_ledger(ledger, config)

    assert result["rows"] == 82
    assert result["reachable"] == 82
    assert result["rich_count"] == 82


def test_validate_source_ledger_rejects_generic_failed_error(tmp_path: Path) -> None:
    rows = _source_rows()
    rows[-1]["reachable"] = False
    rows[-1]["failure_class"] = "network_timeout"
    rows[-1]["error"] = "error"
    ledger = tmp_path / "sources-ledger.jsonl"
    config = tmp_path / "main_sources.yaml"
    _write_ledger(ledger, rows)
    _write_source_config_for_rows(config, rows)

    with pytest.raises(SmokeValidationError, match="generic source error"):
        validate_source_ledger(ledger, config)


def test_validate_launchd_static_proposal(tmp_path: Path) -> None:
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    _write_plist(plist, script, tmp_path, tmp_path / "logs" / "runner.log")

    result = validate_launchd_static(plist, expected_mode="proposal")

    assert result["passed"] is True
    assert result["mode"] == "proposal"


def test_validate_launchd_static_rejects_mode_mismatch(tmp_path: Path) -> None:
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    _write_plist(
        plist,
        script,
        tmp_path,
        tmp_path / "logs" / "runner.log",
        auto_promote="1",
        amendment="1",
    )

    with pytest.raises(SmokeValidationError, match="mode active"):
        validate_launchd_static(plist, expected_mode="proposal")


def test_validate_env_parity_requires_active_ready(tmp_path: Path) -> None:
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    _write_plist(
        plist,
        script,
        tmp_path,
        tmp_path / "logs" / "runner.log",
        auto_promote="1",
        amendment="1",
    )

    with pytest.raises(SmokeValidationError, match="active/rollback"):
        validate_env_parity(plist, smoke_auto_promote="0", active_ready=False)

    result = validate_env_parity(plist, smoke_auto_promote="0", active_ready=True)
    assert result["plist_mode"] == "active"
    assert result["smoke_mode"] == "proposal"


def test_validate_root_isolation_metadata(tmp_path: Path) -> None:
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    payload = {"logs/runner.log": {"mtime": 1, "size": 2}}
    before.write_text(json.dumps(payload), encoding="utf-8")
    after.write_text(json.dumps(payload), encoding="utf-8")

    assert validate_root_isolation(before, after)["root_metadata_unchanged"] is True

    after.write_text(json.dumps({"changed": True}), encoding="utf-8")
    with pytest.raises(SmokeValidationError, match="metadata changed"):
        validate_root_isolation(before, after)
