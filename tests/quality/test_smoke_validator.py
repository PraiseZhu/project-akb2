"""Smoke validator tests for isolated AKB2 main-pipeline runs."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from akb2.common.plist_parse import parse_plist_file
from src.akb2.quality.smoke_validator import (
    EXPECTED_GRADER_MODEL,
    SmokeValidationError,
    _is_expected_grader_model,
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

# Loop Engineering

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


def test_expected_grader_model_accepts_codex_alias() -> None:
    assert _is_expected_grader_model("gpt-5.6-luna")
    assert _is_expected_grader_model("codex/gpt-5.6-luna")
    assert not _is_expected_grader_model("gpt-5.4")


def _write_grader_pass(vault: Path, candidate: Path) -> Path:
    grader = vault / "staging" / "grader-results" / f"{candidate.stem}.grader.json"
    grader.parent.mkdir(parents=True, exist_ok=True)
    grader.write_text(
        json.dumps(
            {
                "status": "PASS",
                "dimensions_checked": 10,
                "findings": [],
                "model_used": EXPECTED_GRADER_MODEL,
                "primary_model": EXPECTED_GRADER_MODEL,
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


def _source_rows(n: int = 82) -> list[dict]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for i in range(n):
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
            _row("grade_llm", "grade_llm", {"model_used": EXPECTED_GRADER_MODEL}),
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
                    "model_used": EXPECTED_GRADER_MODEL,
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
                    "model_used": EXPECTED_GRADER_MODEL,
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
                    "model_used": EXPECTED_GRADER_MODEL,
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
                    "model_used": EXPECTED_GRADER_MODEL,
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
                    "model_used": EXPECTED_GRADER_MODEL,
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

    with pytest.raises(SmokeValidationError, match=EXPECTED_GRADER_MODEL):
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
                    "model_used": EXPECTED_GRADER_MODEL,
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
                    "model_used": EXPECTED_GRADER_MODEL,
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
                    "model_used": EXPECTED_GRADER_MODEL,
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


def test_default_arguments_do_not_raise(tmp_path: Path) -> None:
    """SC-R2-12：以全默认参数校验一个"运行态形态"的 plist 必须不抛。

    owner 决策①「保持自动、配置对齐实际」的同一处漏项：`launchd/com.akb2.main-pipeline.plist`
    与运行态 `~/Library/LaunchAgents/com.akb2.main-pipeline.plist` 都是
    `AKB2_AUTO_PROMOTE=1`（= active），而 `--expected-mode` / `validate_launchd_static`
    的默认值仍写 `proposal` —— 于是照文档跑全默认参数必定
    `SmokeValidationError: launchd mode active != expected proposal`：
    一个与管线好坏无关、纯配置错位的失败，运维读不出任何信息。

    三条一起钉，缺一条都能让默认值再次漂走：
      ① 函数默认（不传 expected_mode）不抛，且判出 active；
      ② CLI 的 --expected-mode 默认值与函数默认值一致（两处默认不得各自漂移）；
      ③ 前提自证：仓库模板确实是 AUTO_PROMOTE=1 —— 哪天模板改回 proposal 部署，
         这条会打红，提醒把默认值一起改回去，而不是让默认值继续与现实脱钩。
    """
    import inspect

    from src.akb2.quality import smoke_validator as sv

    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    # 运行态形态：AKB2_AUTO_PROMOTE=1（与模板、与 ~/Library/LaunchAgents 实测一致）
    _write_plist(
        plist, script, tmp_path, tmp_path / "logs" / "runner.log", auto_promote="1"
    )

    # ① 函数默认
    result = validate_launchd_static(plist)
    assert result["passed"] is True
    assert result["mode"] == "active"

    # ② CLI 默认（只覆盖 --plist；--skip-live-parity 因 CI/本机未加载 job，
    #    live 三方腿另有专测，本条只守 expected-mode 默认值不漂移）
    assert (
        sv.main(
            ["--mode", "launchd-static", "--plist", str(plist), "--skip-live-parity"]
        )
        == 0
    )
    assert (
        inspect.signature(validate_launchd_static).parameters["expected_mode"].default
        == sv.DEFAULT_EXPECTED_LAUNCHD_MODE
    ), "函数默认与具名常量脱钩了"

    # ③ 前提自证：仓库模板的部署模式就是 active
    repo_template = Path(__file__).resolve().parents[2] / "launchd" / plist.name
    template_env = parse_plist_file(repo_template).get("EnvironmentVariables") or {}
    assert str(template_env.get("AKB2_AUTO_PROMOTE")) == "1", (
        "仓库模板已不是 AUTO_PROMOTE=1 —— 默认 expected-mode 必须跟着改回 proposal，"
        f"实际 env={dict(template_env)}"
    )
    assert sv.DEFAULT_EXPECTED_LAUNCHD_MODE == "active"

    # 反向守卫：显式 proposal 仍必须拒 active plist（别把默认值改成"什么都放过"）
    with pytest.raises(SmokeValidationError, match="mode active"):
        validate_launchd_static(plist, expected_mode="proposal")


def test_launchd_mode_helper_has_no_unused_context_param() -> None:
    """SC-R2-12 后半：`_launchd_mode_from_env` 的 `context` 参数从未被使用，已移除。

    未使用的必填关键字参数会让每个调用点都得编一个字符串出来 —— 读者以为它进了
    错误消息（它没有），下一个人照抄时还会继续编。
    """
    import inspect

    from src.akb2.quality.smoke_validator import _launchd_mode_from_env

    params = inspect.signature(_launchd_mode_from_env).parameters
    assert "context" not in params, f"context 参数又回来了: {list(params)}"
    assert list(params) == ["env"], list(params)


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


# ---- live (launchctl print) three-way parity ---------------------------------
# F0 事故形态：installed plist 已是 AKB2_AUTO_PROMOTE=1，但 2026-08-06 拉起的
# daemon 运行态仍是 0 —— 只改磁盘 plist、没 bootout+bootstrap。旧 env-parity
# 只比 repo/installed 两份文件，比不出这种漂移。以下用例把 live 腿钉进判定。


def _live_reader_returning(env: dict[str, str]):
    def reader(label: str) -> dict[str, str]:
        assert label == "com.akb2.main-pipeline"
        return dict(env)

    return reader


_LAUNCHCTL_SAMPLE = """\
com.akb2.main-pipeline = {
\tactive count = 1
\tinherited environment = {
\t\tPATH => /usr/bin:/bin
\t\tAKB2_AUTO_PROMOTE => 9
\t}

\tdefault environment = {
\t\tPATH => /usr/bin:/bin
\t}

\tenvironment = {
\t\tAKB2_AUTO_PROMOTE => 1
\t\tAKB2_AMENDMENT_APPLIED => 0
\t\tPATH => /some/path
\t}

\tdomain = gui/501 [100023]
};
"""


def test_parse_launchctl_env_live_block_only() -> None:
    """解析器只认 job-set environment 块，不受 inherited/default 干扰。"""
    from src.akb2.quality.smoke_validator import parse_launchctl_env

    env = parse_launchctl_env(_LAUNCHCTL_SAMPLE)

    assert env["AKB2_AUTO_PROMOTE"] == "1"
    assert env["AKB2_AMENDMENT_APPLIED"] == "0"
    assert env["PATH"] == "/some/path"


def test_validate_env_parity_live_drift_installed_one_live_zero_fails(tmp_path: Path) -> None:
    """F0 复刻：installed plist=1、live job=0 → 必红，并点名两侧与值。

    这正是「只改磁盘 plist、不 bootout+bootstrap」的形态：旧实现（无 live 腿）
    对此报 passed —— 本用例是 sc-p0-live-check-mutation 的锚点，删掉 live 比对
    （_assert_live_env_matches_installed 或其调用）后本用例必须转红。
    """
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    repo_plist = tmp_path / "repo.plist"
    # repo=installed=1（两方对齐——旧实现到此即绿），live job 仍是 0
    _write_plist(plist, script, tmp_path, tmp_path / "logs" / "runner.log", auto_promote="1")
    _write_plist(
        repo_plist, script, tmp_path, tmp_path / "logs" / "repo.log", auto_promote="1"
    )

    live_reader = _live_reader_returning({"AKB2_AUTO_PROMOTE": "0"})

    with pytest.raises(SmokeValidationError, match="installed=1 live=0") as exc_info:
        validate_env_parity(
            plist,
            smoke_auto_promote="1",
            repo_plist_path=repo_plist,
            live_env_reader=live_reader,
        )

    message = str(exc_info.value)
    assert "bootout+bootstrap" in message
    assert "com.akb2.main-pipeline" in message


def test_validate_env_parity_live_green_when_three_way_matches(tmp_path: Path) -> None:
    """repo=installed=live 三方一致 → passed，live 腿不误伤正常部署。"""
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    repo_plist = tmp_path / "repo.plist"
    _write_plist(plist, script, tmp_path, tmp_path / "logs" / "runner.log", auto_promote="1")
    _write_plist(
        repo_plist, script, tmp_path, tmp_path / "logs" / "repo.log", auto_promote="1"
    )

    result = validate_env_parity(
        plist,
        smoke_auto_promote="1",
        repo_plist_path=repo_plist,
        live_env_reader=_live_reader_returning(
            {"AKB2_AUTO_PROMOTE": "1", "AKB2_AMENDMENT_APPLIED": "0"}
        ),
    )

    assert result["passed"] is True
    assert result["plist_mode"] == "active"


def test_validate_env_parity_live_missing_reader_fails_closed(tmp_path: Path) -> None:
    """live 读取失败（job 未加载/launchctl rc!=0）→ fail-closed，不得当绿。

    缺 live 句柄 = 无法完成三方判定；跳过等于把 F0 那扇门重新打开。
    """
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    _write_plist(plist, script, tmp_path, tmp_path / "logs" / "runner.log", auto_promote="1")

    from src.akb2.quality.smoke_validator import SmokeValidationError as SVE

    def broken_reader(label: str) -> dict[str, str]:
        raise SVE(f"cannot read live launchd job {label}: launchctl print rc=113")

    with pytest.raises(SmokeValidationError, match="rc=113"):
        validate_env_parity(
            plist, smoke_auto_promote="1", live_env_reader=broken_reader
        )


def test_env_parity_cli_defaults_to_live_reader_no_job_loaded_fails_closed(
    tmp_path: Path,
) -> None:
    """CLI env-parity 注入缺 live 句柄的假 reader 时必须 fail-closed。

    不依赖运行主机是否提供 launchctl：macOS 真机仍由默认 reader 读取 live
    job；Linux/CI 通过显式假 reader 保留同一缺句柄断言，避免测试被 skip。
    """
    from src.akb2.quality import smoke_validator as sv

    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    _write_plist(plist, script, tmp_path, tmp_path / "logs" / "runner.log", auto_promote="1")

    def missing_live_reader(label: str) -> dict[str, str]:
        raise SmokeValidationError(
            f"cannot read live launchd job {label}: launchctl print rc=113"
        )

    if shutil.which("launchctl") is None:
        # Linux/CI has no launchctl binary; inject the equivalent missing-job
        # reader so the production fail-closed branch is still exercised.
        reader = missing_live_reader
    else:
        reader = None
        job_loaded = (
            subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/com.akb2.main-pipeline"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
        )
        if job_loaded:
            pytest.skip("live launchd job loaded; missing-job case is not applicable")

    rc = sv.main(
        ["--mode", "env-parity", "--plist", str(plist), "--smoke-auto-promote", "1"],
        live_env_reader=reader,
    )
    assert rc == 1, "缺 live 句柄时 CLI env-parity 必须 fail-closed rc=1"


def test_validate_launchd_static_live_drift_also_fails(tmp_path: Path) -> None:
    """launchd-static 与 env-parity 同一 owner 判定：live 漂移在两模式都红。"""
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    _write_plist(plist, script, tmp_path, tmp_path / "logs" / "runner.log", auto_promote="1")

    with pytest.raises(SmokeValidationError, match="installed=1 live=0"):
        validate_launchd_static(
            plist,
            expected_mode="active",
            live_env_reader=_live_reader_returning({"AKB2_AUTO_PROMOTE": "0"}),
        )

    # live 一致时同一入口正常通过
    result = validate_launchd_static(
        plist,
        expected_mode="active",
        live_env_reader=_live_reader_returning(
            {"AKB2_AUTO_PROMOTE": "1", "AKB2_AMENDMENT_APPLIED": "0"}
        ),
    )
    assert result["passed"] is True


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


def test_source_ledger_count_follows_config_not_hardcoded(tmp_path: Path) -> None:
    """源数门跟随 config 条目数，不写死 82：扩到 90 源、ledger 90 行也应通过。"""
    rows = _source_rows(90)
    ledger = tmp_path / "sources-ledger.jsonl"
    config = tmp_path / "main_sources.yaml"
    _write_ledger(ledger, rows)
    _write_source_config_for_rows(config, rows)
    result = validate_source_ledger(ledger, config)
    assert result["rows"] == 90


def test_source_ledger_count_mismatch_config_rejected(tmp_path: Path) -> None:
    """ledger 行数与 config 源数不一致 → 拒（防 ledger/config 漂移）。"""
    rows = _source_rows(90)
    ledger = tmp_path / "sources-ledger.jsonl"
    config = tmp_path / "main_sources.yaml"
    _write_ledger(ledger, rows)
    _write_source_config_for_rows(config, rows[:88])
    with pytest.raises(SmokeValidationError, match="config source count"):
        validate_source_ledger(ledger, config)
