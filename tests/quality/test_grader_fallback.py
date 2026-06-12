"""兜底切换与双不可用挂起测试。

FR-008: GPT-5.4 不可用→gemini-flash 兜底；两者皆不可用→挂起(suspended)不放行。
"""

from __future__ import annotations

import pytest

from src.akb2.quality.llm_router import LLMResponse, RouterConfigError
from src.akb2.quality.grader import (
    run_grader_with_fixture,
    PRIMARY_GRADER_MODEL,
    FALLBACK_GRADER_MODEL,
)


@pytest.fixture
def candidate_page() -> str:
    """简单候选页 fixture。"""
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: test
future_questions:
  - test q1?
  - test q2?
related_concepts:
  - test-concept
  - another-concept
---

## TLDR
test [raw §A]

## 关键发现
test

## 入选理由
test

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| test | §A | strong | none |

## 核心论点
test [raw §A]: description (source_grounded)

## 论证与解释
test

## 适用边界
适用: test
不适用: not test

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| test-concept | related | strong | text evidence |

## Future Questions
test q1?

## 维护记录
- 2026-06-13: test
"""


@pytest.fixture
def gpt54_pass_response() -> dict:
    """GPT-5.4 全 pass 响应。"""
    return {
        "gpt-5.4": LLMResponse(
            content={
                "dimensions": [
                    {
                        "dimension": "论断锚定率",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "How 密度",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "How 可执行性",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "证据诚实",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "解读分离",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "TLDR 密度",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "新颖增量",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "边强度匹配",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "Future Questions 质量",
                        "pass": True,
                        "defects": [],
                    },
                    {
                        "dimension": "适用边界",
                        "pass": True,
                        "defects": [],
                    },
                ]
            },
            model_used="gpt-5.4",
            calls_used=1,
        ),
    }


@pytest.fixture
def gemini_flash_pass_response() -> dict:
    """Gemini Flash 全 pass 兜底响应。"""
    return {
        "gemini-flash": LLMResponse(
            content={
                "dimensions": [
                    {"dimension": dim, "pass": True, "defects": []}
                    for dim in [
                        "论断锚定率", "How 密度", "How 可执行性", "证据诚实",
                        "解读分离", "TLDR 密度", "新颖增量", "边强度匹配",
                        "Future Questions 质量", "适用边界",
                    ]
                ]
            },
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
    }


class TestGraderFallback:
    """FR-008: GPT-5.4 不可用切 gemini-flash 兜底。"""

    def test_primary_succeeds(self, candidate_page, gpt54_pass_response):
        """GPT-5.4 可用 → 直接使用，不切兜底。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_llm = make_mock_route_llm(gpt54_pass_response)
        defects, model_used, suspended = run_grader_with_fixture(
            candidate_page, mock_llm
        )

        assert not suspended
        assert model_used == "gpt-5.4"
        assert len(defects) == 0

    def test_fallback_to_gemini(self, candidate_page, gemini_flash_pass_response):
        """GPT-5.4 不可用 → 切 gemini-flash 兜底。"""
        from tests.quality.conftest import make_mock_route_llm

        # GPT-5.4 抛出异常，gemini-flash 返回正常
        def mock_with_fallback(model_family, messages, response_schema=None):
            from src.akb2.quality.llm_router import LLMResponse

            if model_family == PRIMARY_GRADER_MODEL:
                raise RuntimeError("GPT-5.4 unavailable")
            if model_family == FALLBACK_GRADER_MODEL:
                return gemini_flash_pass_response["gemini-flash"]
            return LLMResponse(content={}, model_used=model_family)

        defects, model_used, suspended = run_grader_with_fixture(
            candidate_page, mock_with_fallback
        )

        assert not suspended
        assert model_used == "gemini-2.5-flash"
        assert len(defects) == 0

    def test_both_unavailable_suspends(self, candidate_page):
        """GPT-5.4 和 gemini-flash 皆不可用 → 挂起。"""
        from tests.quality.conftest import make_mock_route_llm

        # 两者都抛异常
        def mock_both_down(model_family, messages, response_schema=None):
            raise RuntimeError(f"{model_family} unavailable")

        defects, model_used, suspended = run_grader_with_fixture(
            candidate_page, mock_both_down
        )

        assert suspended, "Should suspend when both graders are unavailable"
        assert model_used == "none"

    def test_primary_parse_failure_falls_back(
        self, candidate_page, gemini_flash_pass_response
    ):
        """GPT-5.4 返回但解析失败 → 切 gemini-flash 兜底。"""
        from tests.quality.conftest import make_mock_route_llm

        def mock_parse_fail_then_fallback(model_family, messages, response_schema=None):
            from src.akb2.quality.llm_router import LLMResponse

            if model_family == PRIMARY_GRADER_MODEL:
                return LLMResponse(
                    content="not valid json at all {{{",
                    model_used="gpt-5.4",
                    calls_used=1,
                )
            if model_family == FALLBACK_GRADER_MODEL:
                return gemini_flash_pass_response["gemini-flash"]
            return LLMResponse(content={}, model_used=model_family)

        defects, model_used, suspended = run_grader_with_fixture(
            candidate_page, mock_parse_fail_then_fallback
        )

        assert not suspended
        assert model_used == "gemini-2.5-flash"

    def test_primary_config_error_falls_back(
        self, candidate_page, gemini_flash_pass_response
    ):
        """GPT-5.4 配置错误（缺 key）→ 切 gemini-flash 兜底。"""
        from tests.quality.conftest import make_mock_route_llm

        def mock_config_error_then_fallback(
            model_family, messages, response_schema=None
        ):
            from src.akb2.quality.llm_router import LLMResponse

            if model_family == PRIMARY_GRADER_MODEL:
                raise RouterConfigError("OPENAI_API_KEY missing")
            if model_family == FALLBACK_GRADER_MODEL:
                return gemini_flash_pass_response["gemini-flash"]
            return LLMResponse(content={}, model_used=model_family)

        defects, model_used, suspended = run_grader_with_fixture(
            candidate_page, mock_config_error_then_fallback
        )

        assert not suspended
        assert model_used == "gemini-2.5-flash"