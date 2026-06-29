"""SC-9c 验收锚 — raw 门（v2）。

承载 master-plan SC-9c（= multi-source-distill spec SC-3）：
raw 门只挡不可用，不误杀"暂时单薄但领域相关"的源（进池等攒）；对任何概念零贡献者拒。

诚实边界：v2 模块 `akb2.quality.raw_gate` 是 **P0 之后 v2 实现期**才创建的交付物。
每个测试函数体首行 `importorskip` 该模块——模块未建则 selector 命中并 skip（exit 0），
模块建好后自动转真。skip 不等于 pass；这是验收契约的先行声明。
具体 API 以 v2-1 实现为准。
"""

from __future__ import annotations

import pytest

_V2_REASON = "v2 raw 门模块（akb2.quality.raw_gate）为 P0 之后 v2 实现期交付；SC-9c 验收锚，模块建好后本测试转真"


def test_raw_gate_thin_but_valid_source_admitted():
    """薄但对某概念 ≥1 合格知识贡献的源 → 收进 raw 池等攒（passed=True）。"""
    rg = pytest.importorskip("akb2.quality.raw_gate", reason=_V2_REASON)
    result = rg.evaluate_raw(_thin_but_one_valid_contribution())
    assert result.passed is True
    assert result.contribution_evidence  # 命中的概念/贡献非空


def test_raw_gate_zero_contribution_rejected():
    """对任何概念零贡献（新闻/营销/纯观点/噪音）→ 拒，与 wiki 负样本同口径。"""
    rg = pytest.importorskip("akb2.quality.raw_gate", reason=_V2_REASON)
    for sample in _zero_contribution_samples():
        result = rg.evaluate_raw(sample)
        assert result.passed is False


def test_raw_gate_does_not_judge_novelty_or_lane():
    """raw 门不判 how 密度/车道/novelty（成页时才判）——薄源不因 novelty/lane 在 raw 门被拒。"""
    rg = pytest.importorskip("akb2.quality.raw_gate", reason=_V2_REASON)
    result = rg.evaluate_raw(_thin_but_one_valid_contribution())
    # raw 门返回结构不含 novelty/lane 拒因（这些是 page_admission 的职责）
    assert getattr(result, "novelty_judged", False) is False
    assert getattr(result, "lane_judged", False) is False


# --- 实现期填充的 fixture 助手（占位；v2-1/v2-4 落地时接真实样本）--- #

def _thin_but_one_valid_contribution():
    raise NotImplementedError("v2-4 eval harness 提供薄但有效源 fixture")


def _zero_contribution_samples():
    raise NotImplementedError("v2-4 eval harness 提供零贡献负样本 fixture")
