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


# ---------------------------------------------------------------------------
# §0.2/D8：proposed_node_only 冷启动分叉（allow_proposed_node_only；P2 后无二次拒）
# ---------------------------------------------------------------------------


def _cold_start_vault(tmp_path: Path) -> Path:
    """创建冷启动 vault：candidate 用 proposed_graph_nodes（无 real_link）。"""
    vault = tmp_path / "vault_cold"
    vault.mkdir()
    (vault / "staging" / "candidate" / "test-batch").mkdir(parents=True)
    (vault / "wiki" / "sources").mkdir(parents=True)
    (vault / "manifests" / "ingest").mkdir(parents=True)
    (vault / ".akb-runtime").mkdir(parents=True)
    cand = vault / "staging" / "candidate" / "test-batch" / "test-gates.md"
    cand.write_text("""---
page_status: candidate
source_type: blog
template_type: source
source_refs: [raw/test-gates--raw.md]
future_questions:
  - Q1: Test gate one?
  - Q2: Test gate two?
related_concepts: []
proposed_graph_nodes:
  - slug: cold-concept
    label: cold-concept
    reason: cold start pending adjudication
    source_anchor: §A
graph_pending: true
gate_decision: null
slug: test-gates
title: Cold Start Article
source_url: https://example.com/cold
date: 2026-06-13
evidence_provenance: source_grounded
graph_role: leaf
graph_status: pending
key_findings:
  - Cold start finding one
  - Cold start finding two
  - Cold start finding three
---

## TLDR

Cold start candidate with proposed_graph_nodes.

## 关键发现

Finding one. Finding two. Finding three.

## 入选理由

Cold start inclusion.

## 证据地图

| 证据 | 来源 | 强度 |
|------|------|------|
| Test | [raw] | strong |

## 核心论点

Cold start argument.

## 论证与解释

Detailed explanation.

## 适用边界

Cold start scenarios.

## 图谱关系

（无图谱边，待 adjudication）

## Future Questions

Q1: Test one?
Q2: Test two?

## 维护记录

| 2026-06-13 | Initial candidate | gate: null |""")
    return vault


class TestD8ProposedNodeOnly:
    """§0.2/D8：proposed_node_only 冷启动分叉（P2 双模式拆除后语义）。

    - evaluate_five_gates(allow_proposed_node_only=True) → 过（medium + proposed_node_only）
    - evaluate_five_gates(allow=False) → Gate-3 fail（real_link required）
    - run_promote（不读环境变量）+ proposed_node_only → 写 active + graph_pending 落账
    - run_promote + AKB2_AUTO_PROMOTE=0 → **仍**写 active（env 零影响）
    """

    def test_graph_readiness_is_proposed_node_only(self, tmp_path):
        """冷启动 candidate → gate CLI graph_readiness=proposed_node_only + confidence=medium。"""
        from src.akb2.quality.promote import _run_gate_check

        vault = _cold_start_vault(tmp_path)
        cand = vault / "staging" / "candidate" / "test-batch" / "test-gates.md"
        gate_ok, detail, parsed = _run_gate_check(cand, vault)
        # gate CLI 读 proposed_graph_nodes → proposed_node_only + medium
        assert parsed.get("graph_readiness") == "proposed_node_only", (
            f"冷启动应 proposed_node_only: {detail}"
        )

    def test_evaluate_five_gates_allow_proposed_passes(self, tmp_path, monkeypatch):
        """allow_proposed_node_only=True → 过闸（medium + proposed_node_only 接受）。"""
        from src.akb2.quality.promote import _run_gate_check, evaluate_five_gates

        vault = _cold_start_vault(tmp_path)
        cand = vault / "staging" / "candidate" / "test-batch" / "test-gates.md"
        # 确认 gate CLI 真返 proposed_node_only（否则测试无意义）
        _, _, parsed = _run_gate_check(cand, vault)
        assert parsed.get("graph_readiness") == "proposed_node_only"

        # mock lint + grader 为 pass（monkeypatch auto-stop，不泄漏到后续测试）
        monkeypatch.setattr(
            "src.akb2.quality.promote._run_lint_check",
            lambda c, v: (True, "mock lint pass"),
        )
        monkeypatch.setattr(
            "src.akb2.quality.promote._read_grader_result",
            lambda p: (True, "mock grader pass"),
        )
        passed, failed, reason = evaluate_five_gates(
            cand, vault, None, allow_proposed_node_only=True
        )
        # Gate-2/3 接受 proposed_node_only → 不在 Gate-2/3 fail
        assert failed not in ("Gate-2 (decision_confidence)", "Gate-3 (graph_readiness)"), (
            f"allow=True 时 Gate-2/3 应接受 proposed_node_only: {failed} {reason}"
        )

    def test_evaluate_five_gates_default_rejects_proposed(self, tmp_path):
        """默认 allow=False → Gate-2/3 fail（proposed_node_only 不够，要 high+real_link）。"""
        from src.akb2.quality.promote import evaluate_five_gates

        vault = _cold_start_vault(tmp_path)
        cand = vault / "staging" / "candidate" / "test-batch" / "test-gates.md"
        passed, failed, reason = evaluate_five_gates(
            cand, vault, None, allow_proposed_node_only=False
        )
        assert passed is False
        # 默认模式 proposed_node_only 被 Gate-2（medium confidence）或 Gate-3（非 real_link）拒
        assert failed in ("Gate-2 (decision_confidence)", "Gate-3 (graph_readiness)"), (
            f"默认模式 proposed_node_only 应被 Gate-2/3 拒: {failed}"
        )

    def test_run_promote_accepts_proposed_node_only_env_one(self, tmp_path, monkeypatch):
        """[修宪 2026-07-06 + P2 拆双模式] proposed_node_only → **允许**写 active
        (旧二次拒废除,冷启动死锁打破;环境开关已拆除,env=1 作回归锚点)。

        质量底线不变:五闸仍须全过(本测试 mock lint/grader pass,gate CLI 真跑);
        graph_pending 落账可见(fail-visible);缺失概念进 pending 队列。"""
        from src.akb2.quality.promote import _resolve_ledger_path, run_promote

        vault = _cold_start_vault(tmp_path)
        cand = vault / "staging" / "candidate" / "test-batch" / "test-gates.md"
        monkeypatch.setenv("AKB2_AUTO_PROMOTE", "1")
        monkeypatch.setattr(
            "src.akb2.quality.promote._run_lint_check",
            lambda c, v: (True, "mock lint pass"),
        )
        monkeypatch.setattr(
            "src.akb2.quality.promote._read_grader_result",
            lambda p: (True, "mock grader pass"),
        )
        success, detail, snapshot_id = run_promote(cand, vault, None)
        assert success is True, f"修宪后 proposed_node_only 应可 auto-active: {detail}"
        # active 已写入(第一篇冷启动条目)
        active = vault / "wiki" / "sources" / "test-gates.md"
        assert active.exists(), "修宪后 proposed_node_only 应写 active"
        # 账本可见 graph_pending 升格事件(fail-visible,非静默)。
        # 账本在机器本地 state root(2026-07-28 迁移),按 promote 同一解析取路径。
        ledger = _resolve_ledger_path(vault, "quality-ledger.jsonl")
        assert ledger.exists(), (
            f"graph_pending 升格必须落账(fail-visible): {ledger}"
        )
        content = ledger.read_text(encoding="utf-8")
        assert "auto_active_graph_pending" in content

    def test_run_promote_env_zero_writes_active_proposed_node_only(
        self, tmp_path, monkeypatch
    ):
        """[P2 sc-p2-env-zero-mutation] AKB2_AUTO_PROMOTE=0 + proposed_node_only
        六闸全过 → **仍**写 active（旧 proposal 模式已拆除，env 零影响）。"""
        from src.akb2.quality.promote import run_promote

        vault = _cold_start_vault(tmp_path)
        cand = vault / "staging" / "candidate" / "test-batch" / "test-gates.md"
        monkeypatch.setenv("AKB2_AUTO_PROMOTE", "0")
        monkeypatch.setattr(
            "src.akb2.quality.promote._run_lint_check",
            lambda c, v: (True, "mock lint pass"),
        )
        monkeypatch.setattr(
            "src.akb2.quality.promote._read_grader_result",
            lambda p: (True, "mock grader pass"),
        )
        success, detail, snapshot_id = run_promote(cand, vault, None)
        assert success is True, f"env=0 不应影响升格: {detail}"
        assert snapshot_id is not None, "env=0 六闸全过必须走快照+active 写入"
        active = vault / "wiki" / "sources" / "test-gates.md"
        assert active.exists(), "env=0 六闸全过必须写 active（proposal 路径已拆除）"


class TestGate0Provenance:
    """F52: 升格前溯源硬校验——candidate frontmatter source_url 空 → 拒升格。

    实锤事故: zed-blog--ead0a0400895 / smashing-magazine--fa0b3ac1fdf6 以
    source_url='' 过五闸写 active（全链无任何一道闸检查溯源字段非空），
    wiki 页无法回链原文。
    """

    def _make_all_pass_mocks(self, monkeypatch):
        import src.akb2.quality.promote as promote_mod

        def mock_gate(*args, **kwargs):
            return True, "mock gate detail", {
                "proposed_gate_decision": "active",
                "decision_confidence": "high",
                "graph_readiness": "real_link",
                "all_10_pass": True,
            }

        monkeypatch.setattr(promote_mod, "_run_gate_check", mock_gate)
        monkeypatch.setattr(
            promote_mod, "_run_lint_check",
            lambda *a, **k: (True, "mock lint: 0 findings"),
        )
        monkeypatch.setattr(
            promote_mod, "_read_grader_result",
            lambda *a, **k: (True, "mock grader: PASS"),
        )

    def _write_grader_pass(self, vault: Path) -> Path:
        p = vault / "grader_result.json"
        p.write_text(json.dumps(
            {"status": "PASS", "dimensions_checked": 5, "findings": []}
        ))
        return p

    def test_empty_source_url_fails_gate0(self, gates_vault_root, monkeypatch):
        """source_url: '' → Gate-0 拒绝,候选留 staging。"""
        from src.akb2.quality.promote import evaluate_five_gates

        self._make_all_pass_mocks(monkeypatch)
        candidate = (
            gates_vault_root / "staging" / "candidate" / "test-batch" / "test-gates.md"
        )
        text = candidate.read_text(encoding="utf-8")
        candidate.write_text(
            text.replace(
                "source_url: https://example.com/gates-test", "source_url: ''"
            ),
            encoding="utf-8",
        )
        grader_path = self._write_grader_pass(gates_vault_root)

        passed, failed_name, reason = evaluate_five_gates(
            candidate, gates_vault_root, grader_path
        )
        assert passed is False
        assert failed_name == "Gate-0 (source provenance)"
        assert "source_url" in reason
        assert candidate.exists(), "候选必须留 staging"

    def test_missing_source_url_fails_gate0(self, gates_vault_root, monkeypatch):
        """frontmatter 无 source_url 行 → Gate-0 拒绝。"""
        from src.akb2.quality.promote import evaluate_five_gates

        self._make_all_pass_mocks(monkeypatch)
        candidate = (
            gates_vault_root / "staging" / "candidate" / "test-batch" / "test-gates.md"
        )
        text = candidate.read_text(encoding="utf-8")
        candidate.write_text(
            text.replace("source_url: https://example.com/gates-test\n", ""),
            encoding="utf-8",
        )
        grader_path = self._write_grader_pass(gates_vault_root)

        passed, failed_name, reason = evaluate_five_gates(
            candidate, gates_vault_root, grader_path
        )
        assert passed is False
        assert failed_name == "Gate-0 (source provenance)"

    def test_valid_source_url_passes_gate0(self, gates_vault_root, monkeypatch):
        """source_url 正常 → Gate-0 放行,五闸全过。"""
        from src.akb2.quality.promote import evaluate_five_gates

        self._make_all_pass_mocks(monkeypatch)
        candidate = (
            gates_vault_root / "staging" / "candidate" / "test-batch" / "test-gates.md"
        )
        grader_path = self._write_grader_pass(gates_vault_root)

        passed, failed_name, reason = evaluate_five_gates(
            candidate, gates_vault_root, grader_path
        )
        assert passed is True, f"应全过,实际 fail 于 {failed_name}: {reason}"
