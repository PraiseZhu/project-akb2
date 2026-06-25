"""B 层评分器测试 — 10 维量规 + 定位化缺陷清单。

全 fixture LLM 响应，不打真 API。
"""

from __future__ import annotations

import pytest

from src.akb2.quality.grader import (
    RUBRIC_DIMENSIONS,
    _build_grader_schema,
    _load_rubric_text,
    _parse_grader_output,
    run_grader_with_fixture,
)
from src.akb2.quality.llm_router import LLMResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_candidate_page() -> str:
    """完整的候选页 fixture。"""
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/addy-osmani-loop-engineering--raw--2026-06-11.md
gate_reason: 待评审
future_questions:
  - Loop 工程在实时系统中如何适配延迟约束？
  - 多 agent 协作场景下 loop 的故障隔离如何设计？
related_concepts:
  - harness-engineering
  - agent-collaboration
artifact_type: wiki
pipeline_version: v1
---

## TLDR

Loop Engineering 是一种设计有界、收敛、可观测 AI Agent 循环的工程实践 [raw §A]。
它提出了五组件模板（trigger, selector, executor, gate, ledger）[raw §B]。
核心原则：收敛优先于重跑 [raw §C]。

## 关键发现

1. 每个 loop 必须有显式停止条件 [raw §A]
2. 定点修复优于整体重跑 [raw §C]
3. Maker/Checker 必须异族分离 [raw §E]

## 入选理由

本文提供了完整的 loop 设计模板和可执行组件，填补了 AKB 在 agent 工程方法论的空白。

## 证据地图

| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| Loop 五组件 | §B | strong | 实时系统场景 |
| 收敛优先 | §C | strong | 大规模系统 |
| Maker/Checker | §E | strong | 非 LLM 裁决 |

## 核心论点

1. 有界循环设计 [raw §A]: 每个 loop 必须有显式停止条件（source_grounded）
2. 五组件模板 [raw §B]: trigger, selector, executor, gate, ledger（source_grounded）
3. 收敛优先 [raw §C]: 定点修复成本远低于整体重跑（source_grounded）
4. 预算约束 [raw §D]: 预算必须是具名常量（source_grounded）
5. Maker/Checker 分离 [raw §E]: 生产与裁决使用不同模型家族（source_grounded）

## 论证与解释

Loop engineering 的核心洞察是: 先写账本再执行，崩溃恢复只需读最后一条账本记录 [raw §B]。
每个 loop 的 budget 必须声明在可见处（具名常量），不得使用内联魔数 [raw §D]。

## 适用边界

适用: Agent pipeline 自动化、内容质量门控、定时任务编排
不适用: 简单 CRUD 脚本、一次性数据迁移、无外部依赖的纯计算任务

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| harness-engineering | 上游概念 | strong | 原文明示 "楼上一层" |
| agent-collaboration | 相关领域 | medium | 场景应用 |
| context-engineering | 下游应用 | medium | loop 设计决策依赖 context 工程 |

## Future Questions

1. Loop 工程在实时系统中如何适配延迟约束？
2. 多 agent 协作场景下 loop 的故障隔离如何设计？
3. 跨语言/跨运行时环境下 loop ledger 的一致性如何保证？

## 维护记录

- 2026-06-13: 初始创建（待评审）
"""


@pytest.fixture
def all_pass_grader_response() -> dict:
    """B 层评分全 pass 的 fixture 响应。"""
    return {
        "gpt-5.4": LLMResponse(
            content={
                "dimensions": [
                    {"dimension": dim, "pass": True, "defects": []}
                    for dim in RUBRIC_DIMENSIONS
                ]
            },
            model_used="gpt-5.4",
            calls_used=1,
        ),
    }


@pytest.fixture
def some_fail_grader_response() -> dict:
    """B 层评分部分 fail 的 fixture 响应（含定位化缺陷）。"""
    return {
        "gpt-5.4": LLMResponse(
            content={
                "dimensions": [
                    {"dimension": "论断锚定率", "pass": True, "defects": []},
                    {"dimension": "How 密度", "pass": True, "defects": []},
                    {"dimension": "How 可执行性", "pass": True, "defects": []},
                    {
                        "dimension": "证据诚实",
                        "pass": False,
                        "defects": [
                            {
                                "location": "证据地图",
                                "defect": "证据地图缺少未覆盖列",
                                "suggestion": "为每行添加未覆盖列",
                            }
                        ],
                    },
                    {"dimension": "解读分离", "pass": True, "defects": []},
                    {
                        "dimension": "TLDR 密度",
                        "pass": False,
                        "defects": [
                            {
                                "location": "TLDR",
                                "defect": "TLDR 超过 3 句话",
                                "suggestion": "精简到 3 句以内",
                            }
                        ],
                    },
                    {"dimension": "新颖增量", "pass": True, "defects": []},
                    {"dimension": "边强度匹配", "pass": True, "defects": []},
                    {"dimension": "Future Questions 质量", "pass": True, "defects": []},
                    {"dimension": "适用边界", "pass": True, "defects": []},
                ]
            },
            model_used="gpt-5.4",
            calls_used=1,
        ),
    }


@pytest.fixture
def missing_location_response() -> dict:
    """FR-006 测试: 缺陷缺 location。"""
    return {
        "gpt-5.4": LLMResponse(
            content={
                "dimensions": [
                    {
                        "dimension": "论断锚定率",
                        "pass": False,
                        "defects": [
                            {
                                "location": "",
                                "defect": "some defect",
                                "suggestion": "fix it",
                            }
                        ],
                    },
                ]
            },
            model_used="gpt-5.4",
            calls_used=1,
        ),
    }


# ---------------------------------------------------------------------------
# 量规加载
# ---------------------------------------------------------------------------


class TestRubricLoading:
    def test_rubric_file_exists(self):
        """量规文件存在且可加载。"""
        text = _load_rubric_text()
        assert len(text) > 0
        assert "版本" in text or "version" in text.lower()

    def test_rubric_has_10_dimensions(self):
        """量规含 10 维。"""
        text = _load_rubric_text()
        for dim in RUBRIC_DIMENSIONS:
            assert dim in text, f"Missing rubric dimension: {dim}"


# ---------------------------------------------------------------------------
# 解析逻辑
# ---------------------------------------------------------------------------


class TestParseGraderOutput:
    def test_parse_all_pass(self, all_pass_grader_response):
        """解析全 pass 输出 → 空缺陷列表。"""
        response = all_pass_grader_response["gpt-5.4"]
        defects, ok = _parse_grader_output(response.content)
        assert ok
        assert len(defects) == 0

    def test_parse_some_fail(self, some_fail_grader_response):
        """解析部分 fail 输出 → 非空缺陷列表。"""
        response = some_fail_grader_response["gpt-5.4"]
        defects, ok = _parse_grader_output(response.content)
        assert ok
        assert len(defects) > 0
        # 每条缺陷必须有 location
        for d in defects:
            assert d.location, f"Defect missing location: {d}"

    def test_parse_accepts_canonical_name_with_english_suffix(self):
        """gpt-5.4 常把维度名写成中文 canonical + 英文括注，应归一化后接收。"""
        english_suffixes = [
            "Claim Anchor Rate",
            "How Density",
            "How Actionability",
            "Evidence Honesty",
            "Interpretation Separation",
            "TLDR Density",
            "Novelty Increment",
            "Edge Strength Matching",
            "Future Questions Quality",
            "Applicability Boundary",
        ]
        dims = []
        for dim, suffix in zip(RUBRIC_DIMENSIONS, english_suffixes, strict=True):
            dims.append({"dimension": f"{dim} ({suffix})", "pass": True, "defects": []})
        dims[0] = {
            "dimension": "论断锚定率 (Claim Anchor Rate)",
            "pass": False,
            "defects": [
                {
                    "location": "入选理由",
                    "defect": "入选理由缺 raw 锚点",
                    "suggestion": "补充 raw § 引用",
                }
            ],
        }

        defects, ok = _parse_grader_output({"dimensions": dims})

        assert ok
        assert len(defects) == 1
        assert defects[0].dimension == "论断锚定率"

    def test_parse_rejects_unknown_dimension_even_with_suffix(self):
        """只接受已知 canonical 维度名的括注变体。"""
        dims = [
            {"dimension": dim, "pass": True, "defects": []}
            for dim in RUBRIC_DIMENSIONS
        ]
        dims[0] = {"dimension": "论断覆盖率 (Claim Anchor Rate)", "pass": True, "defects": []}

        defects, ok = _parse_grader_output({"dimensions": dims})

        assert not ok
        assert defects == []

    def test_parse_missing_location_rejected(self, missing_location_response):
        """FR-006: 缺 location 的条目整批拒收。"""
        response = missing_location_response["gpt-5.4"]
        defects, ok = _parse_grader_output(response.content)
        assert not ok, "Should reject batch with missing location"

    def test_parse_invalid_json(self):
        """解析非法 JSON → 失败。"""
        defects, ok = _parse_grader_output("not valid json at all")
        assert not ok

    def test_parse_string_content(self):
        """解析字符串形式的 dict → 成功。"""
        dims = [
            {"dimension": dim, "pass": True, "defects": []}
            for dim in RUBRIC_DIMENSIONS
        ]
        import json

        content = json.dumps({"dimensions": dims}, ensure_ascii=False)
        defects, ok = _parse_grader_output(content)
        assert ok

    def test_parse_malformed_dimensions(self):
        """dimensions 不是 list → 失败。"""
        defects, ok = _parse_grader_output({"dimensions": "not_a_list"})
        assert not ok

    def test_parse_rejects_partial_qwen_like_shape(self):
        """网关模型只返回半结构化对象时必须拒收，不许当 0 缺陷通过。"""
        content = {
            "dimensions": [
                {"id": 1, "pass": True, "defects": []},
                {
                    "id": 2,
                    "pass": False,
                    "defects": [{"location": "## Future Questions"}],
                },
            ]
        }
        defects, ok = _parse_grader_output(content)
        assert not ok
        assert defects == []

    def test_parse_requires_all_10_dimensions(self):
        """缺维度时拒收，避免单维 pass 被误作整体验收。"""
        content = {
            "dimensions": [
                {"dimension": "论断锚定率", "pass": True, "defects": []},
            ]
        }
        defects, ok = _parse_grader_output(content)
        assert not ok
        assert defects == []

    def test_parse_missing_pass_field_rejected(self):
        """10 维都缺 pass 时拒收，避免 defects=[] 被静默当全 pass。"""
        content = {
            "dimensions": [
                {"dimension": dim, "defects": []}
                for dim in RUBRIC_DIMENSIONS
            ]
        }
        defects, ok = _parse_grader_output(content)
        assert not ok
        assert defects == []


class TestGraderSchema:
    def test_dimension_schema_is_canonical_enum(self):
        """结构化输出 schema 固定 10 个 canonical 中文维度名。"""
        schema = _build_grader_schema()
        dim_schema = schema["properties"]["dimensions"]["items"]["properties"]["dimension"]
        assert dim_schema["enum"] == RUBRIC_DIMENSIONS


# ---------------------------------------------------------------------------
# 完整评分流程（fixture LLM）
# ---------------------------------------------------------------------------


class TestGraderWithFixture:
    def test_all_pass(self, valid_candidate_page, all_pass_grader_response):
        """全 pass 评分 → 空缺陷列表。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm(all_pass_grader_response)
        defects, model_used, suspended = run_grader_with_fixture(
            valid_candidate_page, mock_llm
        )
        assert not suspended
        assert len(defects) == 0
        assert model_used == "gpt-5.4"

    def test_some_fail(self, valid_candidate_page, some_fail_grader_response):
        """部分 fail 评分 → 非空缺陷列表，全部可定位。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm(some_fail_grader_response)
        defects, model_used, suspended = run_grader_with_fixture(
            valid_candidate_page, mock_llm
        )
        assert not suspended
        assert len(defects) > 0
        assert model_used == "gpt-5.4"
        for d in defects:
            assert d.location, f"Defect missing location: {d}"
            assert d.dimension
            assert d.defect
            assert d.suggestion

    def test_defects_are_localized(self, valid_candidate_page, some_fail_grader_response):
        """缺陷清单每条含 dimension + location + defect + suggestion。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm(some_fail_grader_response)
        defects, _, _ = run_grader_with_fixture(valid_candidate_page, mock_llm)
        for d in defects:
            assert isinstance(d.dimension, str) and d.dimension
            assert isinstance(d.location, str) and d.location
            assert isinstance(d.defect, str) and d.defect
            assert isinstance(d.suggestion, str) and d.suggestion


# ---------------------------------------------------------------------------
# 10 维覆盖验证
# ---------------------------------------------------------------------------


class TestRubricCoverage:
    def test_all_10_dimensions_in_response(self, all_pass_grader_response):
        """fixture 响应覆盖全部 10 维。"""
        response = all_pass_grader_response["gpt-5.4"]
        data = response.content
        dims = [d["dimension"] for d in data["dimensions"]]
        for expected in RUBRIC_DIMENSIONS:
            assert expected in dims, f"Missing dimension in response: {expected}"


# ---------------------------------------------------------------------------
# SC-6.1 重试 + SC-6.4 异族分离（step-6: grader degraded fail-closed 护栏）
# ---------------------------------------------------------------------------


class TestGraderRetryAndIsolation:
    """重试循环（解析失败时按 MAX_GRADER_RETRIES 重试）+ Maker/Checker 异族守护。"""

    def test_run_grader_detailed_retry_exhausted_to_degraded(
        self, monkeypatch, valid_candidate_page
    ):
        """SC-6.1: primary 持续返回坏 JSON → 重试（≤MAX_GRADER_RETRIES），耗尽走兜底标 degraded。"""
        from src.akb2.quality import grader as grader_mod
        from src.akb2.quality.grader import MAX_GRADER_RETRIES, run_grader_detailed
        from src.akb2.quality.llm_router import LLMResponse
        from tests.quality.conftest import make_mock_route_llm

        bad = LLMResponse(content="not valid json", model_used="gpt-5.4", calls_used=1)
        good_dims = [
            {"dimension": dim, "pass": True, "defects": []} for dim in RUBRIC_DIMENSIONS
        ]
        good_gemini = LLMResponse(
            content={"dimensions": good_dims}, model_used="gemini-flash", calls_used=1
        )
        mock_llm = make_mock_route_llm({"gpt-5.4": bad, "gemini-flash": good_gemini})
        monkeypatch.setattr(grader_mod, "route_llm", mock_llm)

        result = run_grader_detailed(valid_candidate_page)

        gpt_calls = mock_llm.call_counts.get("gpt-5.4", 0)
        # 重试发生：至少 2 次（初试 + 重试）
        assert gpt_calls >= 2, f"应触发重试，实际 gpt-5.4 调用 {gpt_calls} 次"
        # 重试上限 ≤ MAX_GRADER_RETRIES
        assert gpt_calls <= MAX_GRADER_RETRIES, (
            f"重试次数超上限：{gpt_calls} > {MAX_GRADER_RETRIES}"
        )
        # 耗尽后走 gemini 兜底 → degraded（fallback_used=True）
        assert result.fallback_used is True
        assert result.suspended is False
        assert len(result.defects) == 0

    def test_run_grader_detailed_retry_then_succeed(
        self, monkeypatch, valid_candidate_page
    ):
        """SC-6.1: 首次坏 JSON、重试后成功 → 不走兜底，未 degraded。"""
        from src.akb2.quality import grader as grader_mod
        from src.akb2.quality.grader import run_grader_detailed
        from src.akb2.quality.llm_router import LLMResponse
        from tests.quality.conftest import make_mock_route_llm

        bad = LLMResponse(content="not valid json", model_used="gpt-5.4", calls_used=1)
        good_dims = [
            {"dimension": dim, "pass": True, "defects": []} for dim in RUBRIC_DIMENSIONS
        ]
        good = LLMResponse(
            content={"dimensions": good_dims}, model_used="gpt-5.4", calls_used=1
        )
        # 首次（call_index 0）坏，第二次（call_index 1）好
        mock_llm = make_mock_route_llm({"gpt-5.4:0": bad, "gpt-5.4:1": good})
        monkeypatch.setattr(grader_mod, "route_llm", mock_llm)

        result = run_grader_detailed(valid_candidate_page)

        assert mock_llm.call_counts.get("gpt-5.4", 0) == 2  # 初试 + 1 重试
        assert result.fallback_used is False
        assert result.suspended is False
        assert len(result.defects) == 0
        assert result.model_used == "gpt-5.4"

    def test_run_grader_detailed_both_models_unavailable_suspends(
        self, monkeypatch, valid_candidate_page
    ):
        """FR-008 fail-closed: primary+fallback 都挂 → suspended=True + 空缺陷。

        这是「评分器双不可用 → 稿件挂起，绝不当通过」的核心闸门（grader.py 双挂出口）。
        现有 test_both_unavailable_suspends 走的是 fixture 版函数(run_grader_with_fixture)，
        真实路径 run_grader_detailed 的双挂出口零覆盖：少这个检查就会把「两模型全挂」
        误判成「0 缺陷通过」，账本骗人。让 route_llm 对两家都抛 RuntimeError 证伪。
        """
        from src.akb2.quality import grader as grader_mod
        from src.akb2.quality.grader import run_grader_detailed

        calls: dict[str, int] = {}

        def boom_route_llm(model_family, messages, response_schema=None, **kwargs):
            calls[model_family] = calls.get(model_family, 0) + 1
            raise RuntimeError(f"{model_family} gateway 401/unreachable")

        monkeypatch.setattr(grader_mod, "route_llm", boom_route_llm)

        result = run_grader_detailed(valid_candidate_page)

        # 双不可用 → fail-closed 挂起，不可信缺陷列表必须为空（不得当通过）。
        assert result.suspended is True, "双模型全挂必须 suspended=True，不得放行"
        assert result.defects == [], "挂起态缺陷列表必须为空，不得伪造 0 缺陷通过"
        # primary 和 fallback 都被尝试过（确实走到双挂出口而非提前返回）。
        assert calls.get("gpt-5.4", 0) >= 1, "primary gpt-5.4 必须被尝试"
        assert calls.get("gemini-flash", 0) >= 1, "fallback gemini 必须被尝试"
        assert result.primary_failed is True
        assert result.fallback_failed is True

    def test_grader_distill_model_family_isolation(self):
        """SC-6.4: Maker/Checker 异族分离 — distill(deepseek) != grader(gpt-5.4)。"""
        from src.akb2.quality.distill import DISTILL_MODEL_FAMILY
        from src.akb2.quality.grader import PRIMARY_GRADER_MODEL

        assert DISTILL_MODEL_FAMILY != PRIMARY_GRADER_MODEL, (
            "Maker/Checker 必须异族分离，distill 与 grader 不得同模型"
        )
        assert PRIMARY_GRADER_MODEL == "gpt-5.4"
        assert DISTILL_MODEL_FAMILY == "deepseek-v4-pro"
