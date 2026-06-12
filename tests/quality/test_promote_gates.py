"""SC-5.2: 五闸逐闸 fail 留 staging — 参数化 5+1 用例。

五闸(gate 10/10 / decision_confidence: high / graph_readiness: real_link /
lint 0 / 评分器 PASS)任一闸 fail -> 候选留 staging,不升格、不写 active。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def gates_vault_root(tmp_path: Path) -> Path:
    """创建 fixture vault 用于五闸测试。"""
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "staging" / "candidate" / "test-batch").mkdir(parents=True)
    (vault / "wiki" / "sources").mkdir(parents=True)
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "manifests" / "ingest").mkdir(parents=True)
    (vault / ".akb-runtime").mkdir(parents=True)

    # 候选页（通过所有闸的基线）
    candidate = vault / "staging" / "candidate" / "test-batch" / "test-gates.md"
    candidate.write_text("""---
page_status: candidate
source_type: blog
template_type: source
source_refs: [raw/test-gates--raw.md]
future_questions:
  - Q1: Test gate one?
  - Q2: Test gate two?
related_concepts: [concept-test-gates]
gate_decision: null
slug: test-gates
title: Test Gates Article
source_url: https://example.com/gates-test
date: 2026-06-13
evidence_provenance: source_grounded
graph_role: leaf
graph_status: healthy
key_findings:
  - Gate test finding one
  - Gate test finding two
  - Gate test finding three
---

## TLDR

Test article for five-gate validation.

## 关键发现

Finding one. Finding two. Finding three.

## 入选理由

Test inclusion.

## 证据地图

| 证据 | 来源 | 强度 |
|------|------|------|
| Test | [raw] | strong |

## 核心论点

Core argument for gate test.

## 论证与解释

Detailed explanation.

## 适用边界

Test scenarios.

## 图谱关系

| 目标 | 关系 | 强度 | 证据 |
|------|------|------|------|
| concept-test-gates | supports | medium | Test |

## Future Questions

Q1: Test one?
Q2: Test two?

## 维护记录

| 2026-06-13 | Initial candidate | gate: null |""")

    return vault


class TestFiveGates:
    """参数化测试：五闸逐一 fail → 留 staging。"""

    def _make_mocks(self, monkeypatch, gate_10_10=True, confidence="high",
                    graph="real_link", lint_ok=True, grader_pass=True):
        """创建 mock 函数注入 promote 模块。"""
        import src.akb2.quality.promote as promote_mod

        def mock_gate(*args, **kwargs):
            return gate_10_10, "mock gate detail", {
                "proposed_gate_decision": "active" if gate_10_10 else "needs_repair",
                "decision_confidence": confidence,
                "graph_readiness": graph,
                "all_10_pass": gate_10_10,
            }

        def mock_lint(*args, **kwargs):
            return lint_ok, "mock lint: 0 findings" if lint_ok else "mock lint: 3 P1 findings"

        monkeypatch.setattr(promote_mod, "_run_gate_check", mock_gate)
        monkeypatch.setattr(promote_mod, "_run_lint_check", mock_lint)

        # For grader, we need to mock _read_grader_result
        def mock_grader(*args, **kwargs):
            if grader_pass:
                return True, "mock grader: PASS"
            return False, "mock grader: FAIL (1 finding)"

        monkeypatch.setattr(promote_mod, "_read_grader_result", mock_grader)

    @pytest.mark.parametrize("fail_gate,expected_fail_name", [
        ("gate_10_10", "Gate-1 (10/10 hard checks)"),
        ("confidence", "Gate-2 (decision_confidence)"),
        ("graph", "Gate-3 (graph_readiness)"),
        ("lint", "Gate-4 (lint 0 findings)"),
        ("grader", "Gate-5 (grader PASS)"),
    ])
    def test_each_gate_fail_keeps_staging(
        self, gates_vault_root, monkeypatch, fail_gate, expected_fail_name
    ):
        """每个闸单独 fail 时返回 (False, 闸名, 原因)，候选留 staging。"""
        from src.akb2.quality.promote import evaluate_five_gates

        kwargs = {
            "gate_10_10": fail_gate != "gate_10_10",
            "confidence": "medium" if fail_gate == "confidence" else "high",
            "graph": "proposed_node_only" if fail_gate == "graph" else "real_link",
            "lint_ok": fail_gate != "lint",
            "grader_pass": fail_gate != "grader",
        }
        self._make_mocks(monkeypatch, **kwargs)

        candidate = gates_vault_root / "staging" / "candidate" / "test-batch" / "test-gates.md"
        grader_path = gates_vault_root / "grader_result.json"
        grader_path.write_text(json.dumps({
            "status": "PASS" if fail_gate != "grader" else "FAIL",
            "dimensions_checked": 5,
            "findings": [] if fail_gate != "grader" else [{"detail": "test failure"}],
        }))

        passed, failed_name, reason = evaluate_five_gates(
            candidate, gates_vault_root, grader_path
        )

        assert passed is False, f"Gate should fail for {fail_gate}"
        assert failed_name == expected_fail_name, (
            f"Expected fail gate '{expected_fail_name}', got '{failed_name}'"
        )
        assert reason, "Reason should be non-empty"

        # 验证候选仍留在 staging（未被移动或删除）
        assert candidate.exists(), "Candidate should remain in staging after gate failure"

        # 验证没有创建 active 文件
        active_path = gates_vault_root / "wiki" / "sources" / "test-gates.md"
        assert not active_path.exists(), (
            f"Active file should NOT be created when gate {fail_gate} fails"
        )

    def test_all_gates_pass(self, gates_vault_root, monkeypatch):
        """五闸全过时返回 (True, 'ALL', reason)。"""
        from src.akb2.quality.promote import evaluate_five_gates

        self._make_mocks(monkeypatch)  # all defaults = pass

        candidate = gates_vault_root / "staging" / "candidate" / "test-batch" / "test-gates.md"
        grader_path = gates_vault_root / "grader_result.json"
        grader_path.write_text('{"status": "PASS", "dimensions_checked": 5, "findings": []}')

        passed, failed_name, reason = evaluate_five_gates(
            candidate, gates_vault_root, grader_path
        )

        assert passed is True, f"All gates should pass, got: {failed_name}: {reason}"
        assert failed_name == "ALL"
        assert "Five gates passed" in reason

    def test_gate_check_result_dataclass(self):
        """GateCheckResult 数据类行为。"""
        from src.akb2.quality.promote import GateCheckResult

        r = GateCheckResult(passed=True, gate_name="Gate-1", reason="all pass")
        assert r.passed is True
        assert r.gate_name == "Gate-1"

        r2 = GateCheckResult(passed=False, gate_name="Gate-4", reason="lint findings")
        assert r2.passed is False
        assert "lint" in r2.reason

    def test_empty_grader_path_returns_fail(self, gates_vault_root, monkeypatch):
        """grader_result_path 为 None 时闸 5 应 fail。"""
        import src.akb2.quality.promote as promote_mod

        def mock_gate(*args, **kwargs):
            return True, "mock", {
                "proposed_gate_decision": "active",
                "decision_confidence": "high",
                "graph_readiness": "real_link",
                "all_10_pass": True,
            }

        def mock_lint(*args, **kwargs):
            return True, "mock lint: 0 findings"

        monkeypatch.setattr(promote_mod, "_run_gate_check", mock_gate)
        monkeypatch.setattr(promote_mod, "_run_lint_check", mock_lint)

        from src.akb2.quality.promote import evaluate_five_gates

        candidate = gates_vault_root / "staging" / "candidate" / "test-batch" / "test-gates.md"
        passed, failed_name, reason = evaluate_five_gates(
            candidate, gates_vault_root, None
        )

        assert passed is False
        assert "Gate-5" in failed_name
