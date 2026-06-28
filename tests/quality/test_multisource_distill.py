"""SC-9 验收锚 — 多源提炼（v2）。

承载 master-plan SC-9a/9b/9d/9e（= multi-source-distill spec SC-1/2/4/5）。

诚实边界：v2 模块 `akb2.quality.multisource_distill` 是 **P0 之后 v2 实现期**才创建的交付物。
本文件的每个测试在函数体首行 `importorskip` 该模块——
- 模块未建（plan-time / P0 期）→ 测试被 **selector 命中并 skip**（`pytest -k synthesis` 返回 exit 0 "1 skipped"，不再 0-selected/exit 5）；
- v2 实现把模块建好后 → 同一测试**自动转真**（按下方断言校验合成行为）。

这是"先写验收契约、实现期转真"的标准做法，不是 fake pass（skip 不等于 pass）。
具体 API 名以 v2-3 实现为准；实现时若签名不同，应同步更新本测试断言。
"""

from __future__ import annotations

import pytest

_V2_REASON = "v2 多源提炼模块（akb2.quality.multisource_distill）为 P0 之后 v2 实现期交付；SC-9 验收锚，模块建好后本测试转真"


def test_multisource_synthesis_thin_sources_pass_gate():
    """SC-9a / spec SC-1：单源 floor fail 的薄源 ×K 合成后过门（F4 + 加权 ≥ 阈值）。"""
    md = pytest.importorskip("akb2.quality.multisource_distill", reason=_V2_REASON)
    # 实现期转真：构造 K 个"单独 1:1 会 floor fail"的同概念薄源 → 合成 → 断言过门。
    result = md.synthesize_concept_page(sources=_thin_same_concept_sources(), concept="loop-engineering")
    assert result.integrity_floor_passed is True
    assert result.weighted_score >= result.threshold
    assert result.created is True  # 仅 create 新页


def test_multisource_incoherent_mix_rejected_by_f4():
    """SC-9b / spec SC-2：不相干混料 → F4 因果边不连通被拒，未入库。"""
    md = pytest.importorskip("akb2.quality.multisource_distill", reason=_V2_REASON)
    result = md.synthesize_concept_page(sources=_incoherent_cross_concept_sources(), concept="loop-engineering")
    assert result.integrity_floor_passed is False
    assert "core_value_f4" in result.failed_floors
    assert result.ingested is False


def test_multisource_fast_path_1to1_thick_source():
    """SC-9d / spec SC-4：单篇够厚（机制组件候选 ≥3）走 1:1 快路径，不进 pending 槽。"""
    md = pytest.importorskip("akb2.quality.multisource_distill", reason=_V2_REASON)
    route = md.route_raw(_thick_source_with_3plus_components())
    assert route.kind == "fast_1to1"
    assert route.pending_slot is None


def test_multisource_no_wiki_mutation_v1_create_only():
    """SC-9e / spec SC-5：v1 合成全程不改写任何已入库 wiki 页（只 create + 写 staging/pending）。"""
    md = pytest.importorskip("akb2.quality.multisource_distill", reason=_V2_REASON)
    before = _snapshot_existing_wiki_pages()
    md.synthesize_concept_page(sources=_thin_same_concept_sources(), concept="new-concept-no-existing-page")
    after = _snapshot_existing_wiki_pages()
    assert before == after  # 既有 wiki 页 mtime/content 快照不变


# --- 实现期填充的 fixture 助手（占位；v2-3/v2-4 落地时接 evals/main-pipeline-quality fixtures）--- #

def _thin_same_concept_sources():
    raise NotImplementedError("v2-4 eval harness 提供同概念薄源 fixture")


def _incoherent_cross_concept_sources():
    raise NotImplementedError("v2-4 eval harness 提供跨概念不相干 fixture")


def _thick_source_with_3plus_components():
    raise NotImplementedError("v2-4 eval harness 提供 ≥3 机制组件的厚源 fixture")


def _snapshot_existing_wiki_pages():
    raise NotImplementedError("v2-4 提供 wiki 既有页快照助手")
