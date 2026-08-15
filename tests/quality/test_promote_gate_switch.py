"""sc-p2-drop-dual-mode / sc-p2-env-zero-mutation: promote 双模式拆除后，
环境变量 AKB2_AUTO_PROMOTE 无论何值（0/未设置/垃圾值/1），六闸全过一律写 active。

[P2 重构] 旧「开关 0 → proposal 模式零 active 写入」语义已废除——promote.py
不再读取该环境变量，proposal 写路径（gate manifest only）已删除。
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

    # 先有 active 文件（用于验证不被误改）
    active_file = vault / "wiki" / "sources" / "existing-active.md"
    active_file.write_text("""---
page_status: active
slug: existing-active
---

# Existing Active Page

This page should be unchanged.""")

    return vault


class TestPromoteGateSwitch:
    """环境变量任何形态下六闸全过都写 active（p2-env-zero-mutation）。"""

    def _mock_gates_pass(self, monkeypatch):
        """六闸全 mock 为过（gate CLI 10/10 + lint 0 + grader PASS）。"""
        import src.akb2.quality.promote as promote_mod

        def mock_run_gate(*args, **kwargs):
            return True, "mock gate: 10/10 pass", {
                "proposed_gate_decision": "active",
                "decision_confidence": "high",
                "graph_readiness": "real_link",
                "all_10_pass": True,
            }

        def mock_run_lint(*args, **kwargs):
            return True, "mock lint: 0 findings"

        def mock_grader(*args, **kwargs):
            return True, "mock grader: PASS"

        monkeypatch.setattr(promote_mod, "_run_gate_check", mock_run_gate)
        monkeypatch.setattr(promote_mod, "_run_lint_check", mock_run_lint)
        monkeypatch.setattr(promote_mod, "_read_grader_result", mock_grader)

    def _run_promote_env(self, switch_vault_root: Path, monkeypatch, env_value):
        """在指定环境变量形态下跑 run_promote，返回结果三元组。"""
        from src.akb2.quality.promote import run_promote

        if env_value is _UNSET:
            monkeypatch.delenv("AKB2_AUTO_PROMOTE", raising=False)
        else:
            os.environ["AKB2_AUTO_PROMOTE"] = env_value

        candidate = (
            switch_vault_root / "staging" / "candidate" / "test-batch" / "test-switch.md"
        )
        grader_path = switch_vault_root / "grader_result.json"
        grader_path.write_text(
            '{"status": "PASS", "dimensions_checked": 5, "findings": []}'
        )
        try:
            return run_promote(candidate, switch_vault_root, grader_path)
        finally:
            os.environ.pop("AKB2_AUTO_PROMOTE", None)

    def _assert_active_written(self, switch_vault_root: Path, result):
        """断言升格成功 + active 已写 + 既有 active 页未被动。"""
        success, detail, snapshot_id = result
        active_path = switch_vault_root / "wiki" / "sources" / "test-switch.md"
        assert success is True, f"六闸全过应升格成功: {detail}"
        assert snapshot_id is not None, "双模式拆除后必须走快照+active 写入路径"
        assert active_path.exists(), (
            f"env 值不得影响写 active——active 页缺失: {detail}"
        )
        active_text = active_path.read_text(encoding="utf-8")
        assert "page_status: active" in active_text
        assert "status: active" in active_text
        # 既有 active 页不被误改（fixture 写入的原文仍完整）
        existing = switch_vault_root / "wiki" / "sources" / "existing-active.md"
        assert "This page should be unchanged." in existing.read_text(encoding="utf-8")

    def test_env_zero_still_writes_active(self, switch_vault_root, monkeypatch):
        """AKB2_AUTO_PROMOTE=0 + 六闸全过 → 仍写 active（不再走 proposal）。"""
        self._mock_gates_pass(monkeypatch)
        result = self._run_promote_env(switch_vault_root, monkeypatch, "0")
        self._assert_active_written(switch_vault_root, result)

    def test_env_unset_still_writes_active(self, switch_vault_root, monkeypatch):
        """未设置 AKB2_AUTO_PROMOTE + 六闸全过 → 仍写 active。"""
        self._mock_gates_pass(monkeypatch)
        result = self._run_promote_env(switch_vault_root, monkeypatch, _UNSET)
        self._assert_active_written(switch_vault_root, result)

    def test_env_invalid_values_still_write_active(self, switch_vault_root, monkeypatch):
        """垃圾值（true/yes/2/on/enabled/空串）逐个 + 六闸全过 → 每次都写 active。"""
        from src.akb2.quality.promote import run_promote

        self._mock_gates_pass(monkeypatch)
        for bad_value in ["true", "yes", "2", "on", "enabled", ""]:
            candidate = (
                switch_vault_root
                / "staging" / "candidate" / "test-batch" / "test-switch.md"
            )
            grader_path = switch_vault_root / "grader_result.json"
            grader_path.write_text(
                '{"status": "PASS", "dimensions_checked": 5, "findings": []}'
            )
            # 每轮删掉上一轮写入的 active，证明下一轮是重新写入而非残留
            active_path = switch_vault_root / "wiki" / "sources" / "test-switch.md"
            if active_path.exists():
                active_path.unlink()

            os.environ["AKB2_AUTO_PROMOTE"] = bad_value
            try:
                success, detail, snapshot_id = run_promote(
                    candidate, switch_vault_root, grader_path
                )
            finally:
                os.environ.pop("AKB2_AUTO_PROMOTE", None)
            assert success is True, f"env={bad_value!r} 不应影响六闸判定: {detail}"
            assert active_path.exists(), (
                f"env={bad_value!r} 下六闸全过必须写 active（proposal 路径已拆除）"
            )

    def test_env_one_still_writes_active(self, switch_vault_root, monkeypatch):
        """AKB2_AUTO_PROMOTE=1 + 六闸全过 → 写 active（对齐旧 auto 行为，回归锚点）。"""
        self._mock_gates_pass(monkeypatch)
        result = self._run_promote_env(switch_vault_root, monkeypatch, "1")
        self._assert_active_written(switch_vault_root, result)


# 哨兵：_run_promote_env 用它表示「删除环境变量」
_UNSET = object()
