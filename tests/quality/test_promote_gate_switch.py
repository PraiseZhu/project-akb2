"""SC-5.1 (master SC-7): AKB2_AUTO_PROMOTE=0 下 active 目录 tree_sha256 不变。

开关 0（默认/未设置/非 "1" 值一律按 0）时 promote 只走 gate --write-manifest
提议路径，对 wiki/ active 区零写入。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def switch_vault_root(tmp_path: Path) -> Path:
    """创建 fixture vault 用于开关测试。"""
    vault = tmp_path / "vault"
    vault.mkdir()

    # 目录结构
    (vault / "staging" / "candidate" / "test-batch").mkdir(parents=True)
    (vault / "wiki" / "sources").mkdir(parents=True)
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "manifests" / "ingest").mkdir(parents=True)
    (vault / ".akb-runtime").mkdir(parents=True)

    # 候选页
    candidate = vault / "staging" / "candidate" / "test-batch" / "test-switch.md"
    candidate.write_text("""---
page_status: candidate
source_type: blog
template_type: source
source_refs: [raw/test-switch--raw.md]
future_questions:
  - Q1: Test question one?
  - Q2: Test question two?
related_concepts: [concept-test]
gate_decision: null
slug: test-switch
title: Test Switch Article
source_url: https://example.com/test
date: 2026-06-13
evidence_provenance: source_grounded
graph_role: leaf
graph_status: healthy
key_findings:
  - Finding one for testing
  - Finding two for testing
  - Finding three for testing
---

## TLDR

Test article for switch behavior validation.

## 关键发现

Finding one. Finding two. Finding three.

## 入选理由

Test inclusion rationale.

## 证据地图

| 证据 | 来源 | 强度 |
|------|------|------|
| Test evidence | [raw] | strong |

## 核心论点

Core argument one. Core argument two.

## 论证与解释

Detailed explanation of the core arguments.

## 适用边界

Applicable in test scenarios.

## 图谱关系

| 目标 | 关系 | 强度 | 证据 |
|------|------|------|------|
| concept-test | supports | medium | Test evidence |

## Future Questions

Q1: Test question one?
Q2: Test question two?

## 维护记录

| 2026-06-13 | Initial candidate | gate: null |""")

    # 先有 active 文件（用于验证不变）
    active_file = vault / "wiki" / "sources" / "existing-active.md"
    active_file.write_text("""---
page_status: active
slug: existing-active
---

# Existing Active Page

This page should be unchanged.""")

    return vault


def tree_sha256(directory: Path) -> str:
    """计算目录下所有文件的聚合 SHA-256（同 conftest.py）。"""
    import hashlib
    h = hashlib.sha256()
    files = sorted(
        p for p in directory.rglob("*")
        if p.is_file()
        and p.name != ".DS_Store"
        and p.suffix != ".pyc"
        and "__pycache__" not in p.parts
    )
    for p in files:
        h.update(str(p.relative_to(directory)).encode())
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


class TestPromoteGateSwitch:
    """开关 0 下 active 目录不变。"""

    def test_switch_0_keeps_active_unchanged(self, switch_vault_root: Path, monkeypatch):
        """AKB2_AUTO_PROMOTE=0 时 evaluate_five_gates 不触发 active write。"""
        # Mock subprocess 调用避免依赖真实 CLI
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import evaluate_five_gates

        def mock_run_gate(*args, **kwargs):
            return True, "mock gate: 10/10 pass", {
                "proposed_gate_decision": "active",
                "decision_confidence": "high",
                "graph_readiness": "real_link",
                "all_10_pass": True,
            }

        def mock_run_lint(*args, **kwargs):
            return True, "mock lint: 0 findings"

        monkeypatch.setattr(promote_mod, "_run_gate_check", mock_run_gate)
        monkeypatch.setattr(promote_mod, "_run_lint_check", mock_run_lint)

        active_dir = switch_vault_root / "wiki"
        hash_before = tree_sha256(active_dir)

        candidate = switch_vault_root / "staging" / "candidate" / "test-batch" / "test-switch.md"
        grader_path = switch_vault_root / "grader_result.json"
        grader_path.write_text('{"status": "PASS", "dimensions_checked": 5, "findings": []}')

        # 开关 0: 只评估五闸，不应写 active
        os.environ["AKB2_AUTO_PROMOTE"] = "0"
        try:
            gates_ok, failed_gate, detail = evaluate_five_gates(
                candidate, switch_vault_root, grader_path
            )
        finally:
            os.environ.pop("AKB2_AUTO_PROMOTE", None)

        assert gates_ok is True, f"Gates should pass, got: {failed_gate}: {detail}"

        hash_after = tree_sha256(active_dir)
        assert hash_before == hash_after, (
            f"Active directory tree_sha256 changed under AKB2_AUTO_PROMOTE=0: "
            f"before={hash_before[:16]}..., after={hash_after[:16]}..."
        )

    def test_switch_unset_defaults_to_0(self, switch_vault_root: Path, monkeypatch):
        """未设置 AKB2_AUTO_PROMOTE 时默认行为等价于 0。"""
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import evaluate_five_gates

        def mock_run_gate(*args, **kwargs):
            return True, "mock", {
                "proposed_gate_decision": "active",
                "decision_confidence": "high",
                "graph_readiness": "real_link",
                "all_10_pass": True,
            }

        def mock_run_lint(*args, **kwargs):
            return True, "mock lint: 0 findings"

        monkeypatch.setattr(promote_mod, "_run_gate_check", mock_run_gate)
        monkeypatch.setattr(promote_mod, "_run_lint_check", mock_run_lint)

        active_dir = switch_vault_root / "wiki"
        hash_before = tree_sha256(active_dir)

        candidate = switch_vault_root / "staging" / "candidate" / "test-batch" / "test-switch.md"
        grader_path = switch_vault_root / "grader_result.json"
        grader_path.write_text('{"status": "PASS", "dimensions_checked": 5, "findings": []}')

        # 确保环境变量未设置
        monkeypatch.delenv("AKB2_AUTO_PROMOTE", raising=False)

        gates_ok, failed_gate, detail = evaluate_five_gates(
            candidate, switch_vault_root, grader_path
        )
        assert gates_ok is True

        hash_after = tree_sha256(active_dir)
        assert hash_before == hash_after, "Active directory should be unchanged when env var unset"

    def test_switch_invalid_value_treated_as_0(self, switch_vault_root: Path, monkeypatch):
        """非 '1' 值一律按 0 处理。"""
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import evaluate_five_gates

        def mock_run_gate(*args, **kwargs):
            return True, "mock", {
                "proposed_gate_decision": "active",
                "decision_confidence": "high",
                "graph_readiness": "real_link",
                "all_10_pass": True,
            }

        def mock_run_lint(*args, **kwargs):
            return True, "mock lint: 0 findings"

        monkeypatch.setattr(promote_mod, "_run_gate_check", mock_run_gate)
        monkeypatch.setattr(promote_mod, "_run_lint_check", mock_run_lint)

        active_dir = switch_vault_root / "wiki"
        hash_before = tree_sha256(active_dir)

        candidate = switch_vault_root / "staging" / "candidate" / "test-batch" / "test-switch.md"
        grader_path = switch_vault_root / "grader_result.json"
        grader_path.write_text('{"status": "PASS", "dimensions_checked": 5, "findings": []}')

        for bad_value in ["true", "yes", "2", "on", "enabled", ""]:
            os.environ["AKB2_AUTO_PROMOTE"] = bad_value
            try:
                gates_ok, _, _ = evaluate_five_gates(
                    candidate, switch_vault_root, grader_path
                )
                assert gates_ok is True
            finally:
                os.environ.pop("AKB2_AUTO_PROMOTE", None)

        hash_after = tree_sha256(active_dir)
        assert hash_before == hash_after, (
            "Active directory changed with invalid AKB2_AUTO_PROMOTE value"
        )
