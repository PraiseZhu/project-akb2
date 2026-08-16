"""量规压缩后的维度契约测试。

intake-rebuild p4/p1 已移除 advisory 加分维与 repair 驻留路径：
候选只消费 4–5 个加权维，完整性地板负责硬门，blocking 缺陷直接终态。
"""

from __future__ import annotations

from src.akb2.quality.grader import _load_rubric_spec


def test_rubric_has_no_advisory_dimensions() -> None:
    """量规不再声明 advisory 维，避免测试把已退役路径当成绿路径。"""
    spec = _load_rubric_spec()
    all_dims = list(spec.get("dimensions", []))
    for lane in (spec.get("lanes", {}) or {}).values():
        all_dims.extend(lane.get("dimensions", []))
    assert not [d for d in all_dims if d.get("advisory") is True]


def test_global_dimensions_are_compressed_weighted_set() -> None:
    """全局加权维保持 4–5 项且不包含 advisory 加分项。"""
    spec = _load_rubric_spec()
    dimensions = spec["dimensions"]
    assert 4 <= len(dimensions) <= 5
    assert {d["name"] for d in dimensions} == {
        "How 密度",
        "How 可执行性",
        "TLDR 密度",
        "适用边界",
        "Future Questions 质量",
    }


def test_lanes_keep_integrity_floor_separate() -> None:
    """每车道加权维与四项完整性地板分离，硬门不可由加权分补偿。"""
    spec = _load_rubric_spec()
    floor_names = {item["name"] for item in spec["integrity_floor"]}
    for lane in (spec.get("lanes", {}) or {}).values():
        weighted_names = {item["name"] for item in lane["dimensions"]}
        assert 4 <= len(weighted_names) <= 5
        assert weighted_names.isdisjoint(floor_names)
        assert all(item.get("hard") is True for item in lane["integrity_floor"])


def test_concept_lane_has_no_advisory_dimensions() -> None:
    """概念车道只保留当前五个加权质量维。"""
    spec = _load_rubric_spec()
    concept = spec["lanes"]["concept"]
    assert {item["name"] for item in concept["dimensions"]} == {
        "概念机制清晰度(H3)",
        "实现机制说明(H2)",
        "适用边界",
        "TLDR密度",
        "Future Questions质量",
    }
