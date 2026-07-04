"""test_rubric_advisory_dims.py — rubric 1.4.0 advisory 加分维语义(用户裁决 2026-07-04)。

裁决:「新颖增量」是加分项——有增量加分;缺失/未证不作为入库卡点。
机器化:rubric advisory: true → 其缺陷不计 blocking、不阻断 repair 收敛;
仍照常评分与记录(fail-visible,宪法 P4)。真重复由 admission novelty 门拦。
"""

from __future__ import annotations

from src.akb2.quality.checks_structural import Defect
from src.akb2.quality.grader import ADVISORY_DIMENSIONS, _load_rubric_spec
from src.akb2.quality.repair import _blocking_defects


def _d(dim: str) -> Defect:
    return Defect(dimension=dim, location="x", defect="d", suggestion="s")


def test_advisory_dims_derived_from_spec() -> None:
    """advisory 集从 rubric.yaml 动态派生:含「新颖增量」(concept+procedural+全局)。"""
    assert "新颖增量" in ADVISORY_DIMENSIONS
    # 非 advisory 核心维绝不在集内(标准不放宽)
    for core in ("概念机制清晰度(H3)", "论断锚定率", "证据诚实", "图谱可调用性"):
        assert core not in ADVISORY_DIMENSIONS
    spec = _load_rubric_spec()
    assert str(spec.get("rubric_version")) == "1.4.0"


def test_blocking_filter_drops_only_advisory() -> None:
    """_blocking_defects:只滤 advisory 维缺陷,其余(含 A 层/核心 B 维)全保留。"""
    defects = [
        _d("新颖增量"),
        _d("概念机制清晰度(H3)"),
        _d("A4-图谱resolve"),
        _d("TLDR密度"),
    ]
    blocking = _blocking_defects(defects)
    dims = [d.dimension for d in blocking]
    assert "新颖增量" not in dims
    assert dims == ["概念机制清晰度(H3)", "A4-图谱resolve", "TLDR密度"]


def test_advisory_only_list_is_nonblocking() -> None:
    """全 advisory 缺陷 → blocking 空(= repair 收敛条件成立);空列表幂等。"""
    assert _blocking_defects([_d("新颖增量"), _d("新颖增量")]) == []
    assert _blocking_defects([]) == []


def test_repair_loop_advisory_only_converges_without_llm(tmp_path) -> None:
    """[F15-fix,e2e v7 idx17 死因回归] 初始缺陷仅 advisory → 轮首短路收敛,
    不进 LLM repair(否则修不动 advisory 触发空转早停把样本判死)。"""
    from src.akb2.quality.repair import run_repair_loop

    def _llm_must_not_run(*_a, **_k):
        raise AssertionError("advisory-only 不应调用 repair LLM")

    result = run_repair_loop(
        candidate_text="# t\n\n## 图谱关系\n\nx\n",
        a_defects=[],
        b_defects=[_d("新颖增量")],
        failures_path=str(tmp_path / "f.jsonl"),
        mock_route_llm=_llm_must_not_run,
        content_lane="concept",
    )
    assert result["converged"] is True
    assert result["rounds"] == 0
    assert [d.dimension for d in result["final_defects"]] == ["新颖增量"]


def test_repair_loop_blocking_defect_still_enters_loop(tmp_path) -> None:
    """blocking 缺陷仍进 repair 循环(不因 advisory 机制放宽):LLM 被调用即证明。"""
    from src.akb2.quality.repair import run_repair_loop

    calls: list = []

    def _llm_probe(*_a, **_k):
        calls.append(1)
        raise RuntimeError("probe stop")  # 进入循环即停,不跑全程

    result = run_repair_loop(
        candidate_text="# t\n\n## 论证与解释\n\nx\n",
        a_defects=[],
        b_defects=[_d("概念机制清晰度(H3)"), _d("新颖增量")],
        failures_path=str(tmp_path / "f.jsonl"),
        mock_route_llm=_llm_probe,
        content_lane="concept",
    )
    assert result["converged"] is False  # probe 打断 → 未收敛(fail-closed 保持)
    assert calls, "blocking 缺陷必须进入 repair LLM 路径"


def test_advisory_weight_and_dims_unchanged() -> None:
    """1.4.0 只加 advisory 标记:concept 维度集/权重/threshold 不动(标准冻结)。"""
    spec = _load_rubric_spec()
    concept = (spec.get("lanes") or {}).get("concept") or {}
    dims = {str(d["name"]): d for d in concept.get("dimensions", [])}
    assert len(dims) == 7
    assert dims["新颖增量"]["weight"] == 0.5
    assert dims["新颖增量"].get("advisory") is True
    assert concept.get("threshold") == 6.0
