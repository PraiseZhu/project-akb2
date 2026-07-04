"""test_f11b_must_cover_repair_reinject.py — F11-B repair 循环 must_cover re-inject 测试。

probe-p9b B 臂 sub-agents ×3 SG-must-cover 死因:compose 阶段 must_cover 4/4 在场,
repair 循环 r3 重写 body 时丢了 source_operation 项,旧版 _apply_must_cover_repairs
只 re-inject source_conclusion,不修 source_operation/source_boundary → SG-must-cover 报缺。
F11-B:扩展全 3 kinds,repair 重写后 re-inject → SG-must-cover 不再报缺。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.checks_structural import (
    Defect,
    _how_section_text,
    _normalize_must_cover_text,
    run_candidate_self_gate,
)
from src.akb2.quality.repair import _apply_must_cover_repairs
from src.akb2.quality.source_ops_scanners import scan_all

_WT = Path(__file__).resolve().parents[2]
_SUB_AGENTS_RAW = (
    _WT / "reports/akb2-quality-repair/_tmp/raw-store/"
    "46593545d630be280052b766e08c4a8e65e4b94ee0aa4151ce2d786fa6c14020.md"
)


def _sg_must_cover_defects(raw: str) -> list[Defect]:
    """构造 SG-must-cover defect(模拟 repair 后 SG 报缺)。"""
    return [Defect(dimension="SG-must-cover", location="### 操作步骤",
                   defect="must_cover 字面量未在对应节渲染", suggestion="补上")]


class TestF11BReinjectSourceOperation:
    def test_dropped_source_operation_reinjected(self):
        """repair 重写丢了 source_operation 项 → re-inject 后 SG-must-cover 过。

        用 sub-agents raw(4 项 source_operation model 配置)。body 模拟 repair 重写
        后操作步骤节为空 → _apply_must_cover_repairs 应 re-inject 缺失项。
        """
        if not _SUB_AGENTS_RAW.exists():
            import pytest
            pytest.skip("sub-agents raw fixture 不存在")
        raw = _SUB_AGENTS_RAW.read_text()
        items = scan_all(raw)
        op_items = [it for it in items if it.kind == "source_operation"]
        assert op_items, "sub-agents 应有 source_operation 项"

        # 模拟 repair 重写后的 body:操作步骤节为空(项被丢)
        body_dropped = """\
## TLDR

Subagents config.

## 核心论点

1. [raw §A] (source_grounded) subagents.

## 论证与解释

### 操作步骤

(操作步骤被 repair 重写清空)

## 适用边界

boundary.

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-07-03 | 初始 | x |"""
        defects = _sg_must_cover_defects(raw)
        repaired, actions = _apply_must_cover_repairs(body_dropped, defects, raw)
        # 应有 operation re-inject action
        assert any(a.get("must_cover_operation_count") for a in actions), (
            f"未 re-inject source_operation: {actions}"
        )
        # re-inject 后 how_section 含缺失项(归一化口径)
        how = _how_section_text(repaired)
        norm_how = _normalize_must_cover_text(how)
        reinjected = 0
        for it in op_items:
            if _normalize_must_cover_text(it.literal) in norm_how:
                reinjected += 1
        assert reinjected >= 3, f"re-inject 后仍有 source_operation 缺失(只 {reinjected}/{len(op_items)} 在场)"

    def test_present_source_operation_not_reinjected(self):
        """已在场的 source_operation 项不重复注入(归一化口径)。"""
        if not _SUB_AGENTS_RAW.exists():
            import pytest
            pytest.skip("sub-agents raw fixture 不存在")
        raw = _SUB_AGENTS_RAW.read_text()
        items = scan_all(raw)
        op_items = [it for it in items if it.kind == "source_operation"]
        # body 含全部 op 项(用 literal 原文)
        ops_text = "\n".join(f"- [raw §A] (source_grounded) {it.literal}" for it in op_items)
        body_complete = f"""\
## TLDR

x.

## 核心论点

1. [raw §A] (source_grounded) x.

## 论证与解释

### 操作步骤

{ops_text}

## 适用边界

b.

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-07-03 | 初始 | x |"""
        defects = _sg_must_cover_defects(raw)
        repaired, actions = _apply_must_cover_repairs(body_complete, defects, raw)
        # 已在场 → 不 re-inject operation
        assert not any(a.get("must_cover_operation_count") for a in actions), (
            f"在场项被误重复注入: {actions}"
        )

    def test_reinjected_literal_no_backtick_command(self):
        """re-inject 的 literal 替 `→' → 不触发 SG-command-recheck 误报。"""
        if not _SUB_AGENTS_RAW.exists():
            import pytest
            pytest.skip("sub-agents raw fixture 不存在")
        raw = _SUB_AGENTS_RAW.read_text()
        body_dropped = """\
## TLDR

x.

## 核心论点

1. [raw §A] (source_grounded) x.

## 论证与解释

### 操作步骤

(empty)

## 适用边界

b.

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-07-03 | 初始 | x |"""
        defects = _sg_must_cover_defects(raw)
        repaired, _ = _apply_must_cover_repairs(body_dropped, defects, raw)
        # re-inject 的行不含反引号(替成 ')
        reinjected_lines = [ln for ln in repaired.split("\n") if "(source_grounded)" in ln and "model" in ln.lower()]
        for ln in reinjected_lines:
            assert "`" not in ln, f"re-inject 行含反引号(SG-command 风险): {ln}"


class TestF11BReinjectSourceBoundary:
    def test_dropped_source_boundary_reinjected(self):
        """source_boundary 项被丢 → re-inject 进适用边界节。"""
        raw = (
            "§A_CONTENT_START\n\n"
            "Boundary: do not use in production without testing.\n"
        )
        # 构造一个有 source_boundary 的 raw(scan_all 应抽出)
        items = scan_all(raw)
        bound_items = [it for it in items if it.kind == "source_boundary"]
        if not bound_items:
            import pytest
            pytest.skip("synthetic raw 无 source_boundary 项")
        body_dropped = """\
## TLDR

x.

## 核心论点

1. [raw §A] (source_grounded) x.

## 论证与解释

### 操作步骤

step.

## 适用边界

(empty)

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-07-03 | 初始 | x |"""
        defects = [Defect(dimension="SG-must-cover", location="## 适用边界",
                          defect="missing", suggestion="补")]
        repaired, actions = _apply_must_cover_repairs(body_dropped, defects, raw)
        assert any(a.get("must_cover_boundary_count") for a in actions), (
            f"未 re-inject source_boundary: {actions}"
        )


class TestF11BSGMustCoverPassesAfterReinject:
    def test_full_sg_must_cover_passes_after_reinject(self):
        """repair 重写丢项 → re-inject → run_candidate_self_gate SG-must-cover 不报缺。"""
        if not _SUB_AGENTS_RAW.exists():
            import pytest
            pytest.skip("sub-agents raw fixture 不存在")
        raw = _SUB_AGENTS_RAW.read_text()
        body_dropped = """\
## TLDR

Subagents config.

## 核心论点

1. [raw §A] (source_grounded) subagents.

## 论证与解释

### 操作步骤

(操作步骤被 repair 重写清空)

## 适用边界

boundary.

## 维护记录

| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-07-03 | 初始 | x |"""
        # 先跑 SG 看报缺
        defects_before = run_candidate_self_gate(body_dropped, raw_content=raw, content_lane="concept")
        mc_before = [d for d in defects_before if d.dimension == "SG-must-cover"]
        assert mc_before, "丢项后应有 SG-must-cover 缺陷"
        # re-inject
        repaired, _ = _apply_must_cover_repairs(body_dropped, mc_before, raw)
        # 再跑 SG → must_cover 缺陷应清零(或大幅下降)
        defects_after = run_candidate_self_gate(repaired, raw_content=raw, content_lane="concept")
        mc_after = [d for d in defects_after if d.dimension == "SG-must-cover"]
        assert len(mc_after) < len(mc_before), (
            f"re-inject 后 SG-must-cover 缺陷未降: {len(mc_before)}→{len(mc_after)}"
        )
