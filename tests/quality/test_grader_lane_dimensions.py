"""test_grader_lane_dimensions.py — intake-rebuild p4 车道维度集验收。

sc-p4-compress-dims 主验收：
  - 加权维每车道 4–5 项（intake-rebuild p4 压缩契约）；维名全部从 rubric.yaml 动态派生。
  - 完整性地板 4 项（论断锚定率/证据诚实/解读分离/车道核心价值）对全车道生效，
    且地板维名不进任何车道的加权维度集（同判据不重复计分）。
  - 旧 10 维 / 旧 concept 7 维不再是生产维度集（退役断言）。
  - .py 不手写维名（AST 负断言）。
  - spec 缺字段/坏形状 → fail-closed（RuntimeError），不给空标准跑空判定。
  - 车道派生通用化：rubric.yaml lanes 每键各成一集（p5 新增 pattern/design 零代码改动）。

另承接原 test_grader.py 的 procedural 解析契约（FR-006 缺 location 整批拒 /
全维到齐 / 维度名归一容差 / fixture 评分回路）——原 10 维 fixture 已随维度压缩退役，
本文件按派生维度集重写等价断言。
"""

from __future__ import annotations

import ast
import copy
import pathlib

import pytest
import yaml

from src.akb2.quality.grader import (
    RUBRIC_DIMENSIONS,
    RUBRIC_DIMENSIONS_BY_LANE,
    _dimensions_for_lane,
    _load_rubric_spec,
    _rubric_dimension_names_by_lane,
)

# 地板四成员的中文名（rubric.yaml integrity_floor；地板维不得出现在任何车道加权集）
_FLOOR_DIM_NAMES = {
    "论断锚定率(raw-anchor)",
    "证据诚实",
    "解读分离(F3溯源)",
    "车道核心价值",
}
_FLOOR_DIM_BASES = {"论断锚定率", "证据诚实", "解读分离", "车道核心价值"}

# 旧生产维度集中被地板/advisory/结构层吸收、不得再作为加权维的名字
_RETIRED_WEIGHTED = {
    "论断锚定率",  # 地板①
    "证据诚实",  # 地板②
    "解读分离",  # 地板③
    "新颖增量",  # advisory（admission novelty 门承担）
    "边强度匹配",  # A 层 A4 结构缺陷承担
    "图谱可调用性",  # advisory（门控侧 D8 兜底承担）
}

# p4 后仍保留的加权维（可出现在某车道集中）
_KEPT_NAMES = {
    "How 密度", "How 可执行性", "TLDR 密度", "适用边界", "Future Questions 质量",
    "概念机制清晰度(H3)", "实现机制说明(H2)", "TLDR密度", "Future Questions质量",
}


class TestLaneDimensionSets:
    """每车道 4–5 项加权维；地板维不进加权集；旧 10/7 维退役。"""

    def test_every_lane_has_4_to_5_weighted_dims(self):
        """p4 压缩契约：每个车道加权维恰 4–5 项（旧 10/7 维不再是生产维度集）。"""
        for lane, dims in RUBRIC_DIMENSIONS_BY_LANE.items():
            assert 4 <= len(dims) <= 5, (
                f"车道 {lane} 加权维 {len(dims)} 项，不在 4–5（intake-rebuild p4 契约）"
            )

    def test_procedural_dims_are_5_not_old_10(self):
        """procedural（= 全局默认集）从 10 维压缩为 5 维，且不再含旧集全部成员。"""
        assert len(RUBRIC_DIMENSIONS) == 5
        assert RUBRIC_DIMENSIONS_BY_LANE["procedural"] == RUBRIC_DIMENSIONS
        # 旧 10 维集整体不再存在：5 维 ≠ 旧 10 维
        assert len(RUBRIC_DIMENSIONS) != 10

    def test_concept_dims_are_5_not_old_7(self):
        """concept 从 7 维压缩为 5 维。"""
        assert len(RUBRIC_DIMENSIONS_BY_LANE["concept"]) == 5

    def test_no_floor_dim_in_any_weighted_set(self):
        """地板判据不得重复进加权集（同一判据既当硬门又当加权维 = 高分补偿错觉）。"""
        for lane, dims in RUBRIC_DIMENSIONS_BY_LANE.items():
            leaked = {d for d in dims if d in _FLOOR_DIM_NAMES or d in _FLOOR_DIM_BASES}
            assert not leaked, f"车道 {lane} 加权集混入地板维: {leaked}"

    def test_retired_dims_absent_from_weighted_sets(self):
        """被地板/advisory/结构层吸收的旧维名不得再作为加权维。"""
        for lane, dims in RUBRIC_DIMENSIONS_BY_LANE.items():
            leaked = set(dims) & _RETIRED_WEIGHTED
            assert not leaked, f"车道 {lane} 加权集含已退役维: {leaked}"

    def test_weighted_dims_all_in_kept_catalog(self):
        """加权维名 ⊆ p4 保留目录（新维名入集需先扩目录并过校验）。"""
        for lane, dims in RUBRIC_DIMENSIONS_BY_LANE.items():
            unknown = set(dims) - _KEPT_NAMES
            assert not unknown, f"车道 {lane} 含未登记维名: {unknown}"

    def test_concept_excludes_how_dims(self):
        """concept 不含 How 两维（How 是教程型车道核心维）。"""
        concept = set(RUBRIC_DIMENSIONS_BY_LANE["concept"])
        how_dims = {"How 密度", "How 可执行性"}
        assert not (concept & how_dims), f"concept 含 How 维: {concept & how_dims}"

    def test_lane_weights_sum_and_threshold_present(self):
        """每车道权重和==10 且 threshold 数值在 spec（过阈算术自洽的输入）。"""
        spec = _load_rubric_spec()
        for lane, cfg in spec["lanes"].items():
            weights = [d["weight"] for d in cfg["dimensions"]]
            assert abs(sum(weights) - 10.0) < 1e-6, (
                f"车道 {lane} 权重和 {sum(weights)} != 10（满分过阈算术锚点）"
            )
            assert isinstance(cfg["threshold"], (int, float))

    def test_dimensions_for_lane_defaults_to_procedural(self):
        """未知/缺省 lane → procedural 集回落（零回归语义不变）。"""
        assert _dimensions_for_lane("") == RUBRIC_DIMENSIONS
        assert _dimensions_for_lane("unknown") == RUBRIC_DIMENSIONS
        assert _dimensions_for_lane("concept") == RUBRIC_DIMENSIONS_BY_LANE["concept"]


class TestFloorFourMembersAllLanes:
    """rubric.yaml 侧：地板 4 项对全车道生效（每车道恰 4 项、成员一致、hard=true）。"""

    def test_every_lane_floor_has_exactly_four_members(self):
        spec = _load_rubric_spec()
        for lane, cfg in spec["lanes"].items():
            names = [f["name"] for f in cfg["integrity_floor"]]
            assert len(names) == 4, f"车道 {lane} 地板 {len(names)} 项 != 4"
            assert all(f.get("hard") is True for f in cfg["integrity_floor"]), (
                f"车道 {lane} 地板含非 hard 成员（宪法 8(a)）"
            )

    def test_floor_member_names_identical_across_lanes(self):
        """全车道地板成员名一致（④车道核心价值按车道分支判据，但成员名相同）。"""
        spec = _load_rubric_spec()
        member_sets = {
            lane: {f["name"] for f in cfg["integrity_floor"]}
            for lane, cfg in spec["lanes"].items()
        }
        reference = member_sets["procedural"]
        for lane, members in member_sets.items():
            assert members == reference, f"车道 {lane} 地板成员偏离: {members ^ reference}"
        assert reference == _FLOOR_DIM_NAMES

    def test_global_floor_matches_lanes_floor(self):
        """全局 integrity_floor 与车道地板同一成员集（默认回落零漂移）。"""
        spec = _load_rubric_spec()
        global_floor = {f["name"] for f in spec["integrity_floor"]}
        assert global_floor == _FLOOR_DIM_NAMES


class TestDimsDerivedFromSpec:
    """维名仍从 spec 派生（A3-6）：不在 .py 手写，改 YAML 即改维度集。"""

    def test_grader_py_no_hardcoded_lane_dim_constants(self):
        """AST 负断言：grader.py 字符串常量不含任何车道维名字面量（防硬编码回流）。"""
        grader_path = pathlib.Path(
            __import__("src.akb2.quality.grader", fromlist=["x"]).__file__
        )
        tree = ast.parse(grader_path.read_text(encoding="utf-8"))
        str_consts = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        all_dims = set()
        for dims in RUBRIC_DIMENSIONS_BY_LANE.values():
            all_dims.update(dims)
        leaked = all_dims & str_consts
        assert not leaked, f"grader.py 含维名硬编码字面量: {sorted(leaked)}"

    def test_tmp_rubric_rename_changes_derived_dims(self):
        """tmp rubric 改名 → 派生集随之变化（动态派生不硬编码）。"""
        spec = _load_rubric_spec()
        tmp = copy.deepcopy(spec)
        tmp["lanes"]["concept"]["dimensions"][0]["name"] = "改名的H3维度(X)"
        by_lane = _rubric_dimension_names_by_lane(tmp)
        assert "改名的H3维度(X)" in by_lane["concept"]
        assert "概念机制清晰度(H3)" not in by_lane["concept"]
        assert by_lane["procedural"] == RUBRIC_DIMENSIONS

    def test_new_lane_in_spec_appears_in_derived_map(self):
        """通用派生：spec 里新增 lane 键（如 pattern）→ 派生图自动含该车道
        （p5 新增 pattern/design 车道零 .py 改动的前置证明）。"""
        spec = _load_rubric_spec()
        tmp = copy.deepcopy(spec)
        tmp["lanes"]["pattern"] = copy.deepcopy(tmp["lanes"]["concept"])
        tmp["lanes"]["pattern"]["dimensions"] = [
            {"name": f"模式维{i}", "weight": 2.0} for i in range(1, 6)
        ]
        by_lane = _rubric_dimension_names_by_lane(tmp)
        assert "pattern" in by_lane
        assert by_lane["pattern"] == [f"模式维{i}" for i in range(1, 6)]


class TestSpecFailClosed:
    """spec 缺字段/坏形状 → fail-closed（RuntimeError，不给空标准跑空判定）。"""

    def _tmp_spec_dir(self, tmp_path: pathlib.Path, spec_obj: dict) -> pathlib.Path:
        d = tmp_path / "rubric"
        d.mkdir()
        (d / "rubric.yaml").write_text(
            yaml.safe_dump(spec_obj, allow_unicode=True), encoding="utf-8"
        )
        # 配套 md：_load_rubric_text 版本断言用（parse 层测试触发 _load_rubric_spec 即够，
        # md 仅在 _build_grader_prompt 路径需要，此处不触达）
        return d

    _BASE_SPEC_VALID = None  # lazy：用例内从生产 spec deepcopy

    def test_missing_lane_dimensions_key_rejected(self, tmp_path, monkeypatch):
        from src.akb2.quality import grader as grader_mod

        spec = copy.deepcopy(_load_rubric_spec())
        del spec["lanes"]["concept"]["dimensions"]
        d = self._tmp_spec_dir(tmp_path, spec)
        monkeypatch.setattr(grader_mod, "RUBRIC_DIR", d)
        with pytest.raises(RuntimeError, match="dimensions"):
            grader_mod._load_rubric_spec()

    def test_dim_missing_weight_rejected(self, tmp_path, monkeypatch):
        from src.akb2.quality import grader as grader_mod

        spec = copy.deepcopy(_load_rubric_spec())
        del spec["lanes"]["concept"]["dimensions"][0]["weight"]
        d = self._tmp_spec_dir(tmp_path, spec)
        monkeypatch.setattr(grader_mod, "RUBRIC_DIR", d)
        with pytest.raises(RuntimeError, match="weight"):
            grader_mod._load_rubric_spec()

    def test_lane_dims_out_of_4_to_5_range_rejected(self, tmp_path, monkeypatch):
        """6 项维度（超 p4 压缩契约上界）→ fail-closed。"""
        from src.akb2.quality import grader as grader_mod

        spec = copy.deepcopy(_load_rubric_spec())
        dims = spec["lanes"]["concept"]["dimensions"]
        dims.append({"name": "第六维", "weight": 1.0})
        # 权重和破坏不在此校验（算术校验在 weighted_score），只验维度数
        d = self._tmp_spec_dir(tmp_path, spec)
        monkeypatch.setattr(grader_mod, "RUBRIC_DIR", d)
        with pytest.raises(RuntimeError, match="4–5"):
            grader_mod._load_rubric_spec()

    def test_lane_floor_not_four_members_rejected(self, tmp_path, monkeypatch):
        """车道地板 3 项（缺成员）→ fail-closed。"""
        from src.akb2.quality import grader as grader_mod

        spec = copy.deepcopy(_load_rubric_spec())
        spec["lanes"]["concept"]["integrity_floor"] = spec["lanes"]["concept"][
            "integrity_floor"
        ][:3]
        d = self._tmp_spec_dir(tmp_path, spec)
        monkeypatch.setattr(grader_mod, "RUBRIC_DIR", d)
        with pytest.raises(RuntimeError, match="4 项"):
            grader_mod._load_rubric_spec()

    def test_floor_member_not_hard_rejected(self, tmp_path, monkeypatch):
        """地板成员 hard: false → fail-closed（不可补偿性入校验）。"""
        from src.akb2.quality import grader as grader_mod

        spec = copy.deepcopy(_load_rubric_spec())
        spec["lanes"]["concept"]["integrity_floor"][0]["hard"] = False
        d = self._tmp_spec_dir(tmp_path, spec)
        monkeypatch.setattr(grader_mod, "RUBRIC_DIR", d)
        with pytest.raises(RuntimeError, match="hard"):
            grader_mod._load_rubric_spec()

    def test_dim_missing_name_rejected(self, tmp_path, monkeypatch):
        from src.akb2.quality import grader as grader_mod

        spec = copy.deepcopy(_load_rubric_spec())
        spec["dimensions"][0] = {"weight": 1.0}
        d = self._tmp_spec_dir(tmp_path, spec)
        monkeypatch.setattr(grader_mod, "RUBRIC_DIR", d)
        with pytest.raises(RuntimeError, match="name"):
            grader_mod._load_rubric_spec()

    def test_lane_missing_threshold_rejected(self, tmp_path, monkeypatch):
        from src.akb2.quality import grader as grader_mod

        spec = copy.deepcopy(_load_rubric_spec())
        del spec["lanes"]["concept"]["threshold"]
        d = self._tmp_spec_dir(tmp_path, spec)
        monkeypatch.setattr(grader_mod, "RUBRIC_DIR", d)
        with pytest.raises(RuntimeError, match="threshold"):
            grader_mod._load_rubric_spec()


# ---------------------------------------------------------------------------
# §0.2/D6：grader concept prompt 含 inferred_from_structure / edge evidence / H3 fail
# ---------------------------------------------------------------------------


class TestD6GraderConceptPromptH3:
    """§0.2/D6：concept grader prompt 显式含 H3 fail 条件 + inferred + edge evidence。"""

    def test_concept_prompt_contains_inferred_from_structure(self):
        from src.akb2.quality.grader import _build_grader_prompt, _load_rubric_text

        msgs = _build_grader_prompt("候选页正文", _load_rubric_text(), content_lane="concept")
        system = msgs[0]["content"]
        assert "inferred_from_structure" in system, (
            "concept grader prompt 须含 inferred_from_structure（H3 fail 条件）"
        )

    def test_concept_prompt_contains_edge_evidence_and_h3_fail(self):
        from src.akb2.quality.grader import _build_grader_prompt, _load_rubric_text

        msgs = _build_grader_prompt("候选页正文", _load_rubric_text(), content_lane="concept")
        system = msgs[0]["content"]
        assert "evidence" in system and "evidence_anchor" in system, (
            "concept prompt 须含 edge evidence / evidence_anchor"
        )
        assert "H3 fail" in system or "H3（概念机制清晰度）fail" in system, (
            "concept prompt 须显式含 H3 fail 条件"
        )
        assert "机制组件与因果边证据" in system, (
            "concept prompt 须指向 ## 机制组件与因果边证据 两表"
        )

    def test_procedural_prompt_has_no_h3_fail_note(self):
        """procedural 不注 H3 fail 条件（H3 是 concept 维度）。"""
        from src.akb2.quality.grader import _build_grader_prompt, _load_rubric_text

        msgs = _build_grader_prompt("候选页正文", _load_rubric_text(), content_lane="procedural")
        system = msgs[0]["content"]
        assert "H3 fail" not in system, "procedural prompt 不应含 concept H3 fail 条件"

    def test_rubric_yaml_h3_note_contains_d6_keywords(self):
        """rubric.yaml concept H3 note 含 inferred_from_structure + eligible edge。"""
        spec = _load_rubric_spec()
        concept_dims = spec["lanes"]["concept"]["dimensions"]
        h3 = next(d for d in concept_dims if "H3" in d["name"])
        note = h3["note"]
        assert "inferred_from_structure" in note
        assert "evidence_anchor" in note
        assert "eligible" in note or "source_causal" in note


# ---------------------------------------------------------------------------
# procedural 解析契约（承接原 test_grader.py，按 5 维派生集重写）
# ---------------------------------------------------------------------------


def _all_pass_dims() -> list[dict]:
    return [
        {"dimension": d, "pass": True, "defects": []} for d in RUBRIC_DIMENSIONS
    ]


class TestProceduralParseContract:
    """_parse_grader_output 在 procedural（默认集）上的硬约束。"""

    def test_parse_all_pass(self):
        from src.akb2.quality.grader import _parse_grader_output

        defects, ok = _parse_grader_output({"dimensions": _all_pass_dims()})
        assert ok
        assert defects == []

    def test_parse_rejects_missing_dimension(self):
        """缺任一维 → 拒收（seen != 全维集，防单维 pass 被当整体验收）。"""
        from src.akb2.quality.grader import _parse_grader_output

        dims = _all_pass_dims()[:-1]
        defects, ok = _parse_grader_output({"dimensions": dims})
        assert not ok
        assert defects == []

    def test_parse_missing_location_rejected(self):
        """FR-006: 缺 location 的缺陷条目整批拒收。"""
        from src.akb2.quality.grader import _parse_grader_output

        dims = _all_pass_dims()
        dims[0] = {
            "dimension": RUBRIC_DIMENSIONS[0],
            "pass": False,
            "defects": [{"location": "", "defect": "x", "suggestion": "y"}],
        }
        defects, ok = _parse_grader_output({"dimensions": dims})
        assert not ok, "缺 location 应整批拒"

    def test_parse_accepts_canonical_name_with_english_suffix(self):
        """canonical 名 + 英文括注 → 归一化接收。"""
        from src.akb2.quality.grader import _parse_grader_output

        suffixes = [
            "How Density",
            "How Actionability",
            "TLDR Density",
            "Applicability Boundary",
            "Future Questions Quality",
        ]
        dims = [
            {"dimension": f"{d} ({s})", "pass": True, "defects": []}
            for d, s in zip(RUBRIC_DIMENSIONS, suffixes, strict=True)
        ]
        defects, ok = _parse_grader_output({"dimensions": dims})
        assert ok
        assert defects == []

    def test_parse_rejects_retired_dimension(self):
        """旧 10 维中退役的名字（如『论断锚定率』）→ 拒收（不再是生产维度）。"""
        from src.akb2.quality.grader import _parse_grader_output

        dims = _all_pass_dims()
        dims[0] = {"dimension": "论断锚定率", "pass": True, "defects": []}
        defects, ok = _parse_grader_output({"dimensions": dims})
        assert not ok, "退役维名应 parse fail（红=维度退役生效）"
        assert defects == []

    def test_parse_invalid_json(self):
        from src.akb2.quality.grader import _parse_grader_output

        defects, ok = _parse_grader_output("not valid json at all")
        assert not ok

    def test_parse_string_content(self):
        import json

        from src.akb2.quality.grader import _parse_grader_output

        content = json.dumps({"dimensions": _all_pass_dims()}, ensure_ascii=False)
        defects, ok = _parse_grader_output(content)
        assert ok

    def test_fixture_grader_loop_some_fail_localized(self):
        """fixture LLM 回路：部分维 fail → 可定位缺陷清单（dimension/location 齐全）。"""
        from src.akb2.quality.grader import run_grader_with_fixture
        from src.akb2.quality.llm_router import LLMResponse
        from tests.quality.conftest import make_mock_route_llm

        dims = _all_pass_dims()
        dims[0] = {
            "dimension": RUBRIC_DIMENSIONS[0],
            "pass": False,
            "defects": [
                {
                    "location": "正文步骤节",
                    "defect": "全文无可执行步骤或代码示例",
                    "suggestion": "添加具体操作步骤、命令或配置参数",
                }
            ],
        }
        from src.akb2.quality.grader import PRIMARY_GRADER_MODEL

        mock = make_mock_route_llm(
            {
                PRIMARY_GRADER_MODEL: LLMResponse(
                    content={"dimensions": dims},
                    model_used=PRIMARY_GRADER_MODEL,
                    calls_used=1,
                )
            }
        )
        defects, model_used, suspended = run_grader_with_fixture("候选页", mock)
        assert not suspended
        assert model_used == PRIMARY_GRADER_MODEL
        assert len(defects) == 1
        assert defects[0].dimension == RUBRIC_DIMENSIONS[0]
        assert defects[0].location
        assert defects[0].defect
        assert defects[0].suggestion


class TestMakerCheckerIsolation:
    """SC-6.4（承接原 test_grader.py）：Maker/Checker 异族分离常量锁。"""

    def test_grader_distill_model_family_isolation(self):
        from src.akb2.quality.distill import DISTILL_MODEL_FAMILY
        from src.akb2.quality.grader import PRIMARY_GRADER_MODEL

        assert DISTILL_MODEL_FAMILY != PRIMARY_GRADER_MODEL, (
            "Maker/Checker 必须异族分离，distill 与 grader 不得同模型"
        )
