"""Step-7: 语义型 How 抖动兜底测试。

覆盖：
- SC-7.1: how_params segment 产 SEMANTIC_RETAKE_MAX 次取交集（具名常量=2）
- SC-7.2: 交集为空/连续不一致达阈值 → fail-closed（有界，不无限重试）
- SC-7.3: must_cover kind 仍三类；DISTILL_MODEL_FAMILY / PRIMARY_GRADER_MODEL 未变
- 回归护栏: 默认关（不设 env）时 extract_claims 仍只产 2 次 LLM 调用

retake 仅作用于 LLM 自由文本 how_steps（语义 segment），不碰 must_cover 代码直出。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.akb2.quality.distill import (
    DISTILL_MODEL_FAMILY,
    MAX_EXTRACT_RETRIES,
    SEMANTIC_RETAKE_MAX,
    SemanticJitterError,
    extract_claims,
)
from src.akb2.quality.grader import PRIMARY_GRADER_MODEL
from src.akb2.quality.llm_router import LLMResponse

# 期望的 must_cover kind 三类（step-2 代码直出，step-7 不新增）
_MUST_COVER_KINDS = {"source_operation", "source_boundary", "source_conclusion"}


def _llm(content: dict) -> LLMResponse:
    """构造一个 content 为 dict 的 LLMResponse（_coerce_json_dict 直接收 dict）。"""
    return LLMResponse(content=content, model_used=DISTILL_MODEL_FAMILY, calls_used=1)


def _claims_response() -> LLMResponse:
    """合法 claims 段响应（3 条 source_grounded claims + tldr≥20 字）。"""
    return _llm(
        {
            "claims": [
                {
                    "text": "Loop engineering designs bounded convergent observable AI agent loops",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供 bounded/convergent 的可度量判据",
                },
                {
                    "text": "A well-designed loop has five components: trigger selector executor gate ledger",
                    "anchor": "§B",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较五组件在不同负载下的取舍",
                },
                {
                    "text": "Fix specific defects rather than restarting the entire pipeline convergence over restart",
                    "anchor": "§C",
                    "provenance": "source_grounded",
                    "evidence_gap": "未验证：原文未提供定点修复 vs 整体重跑的实测成本",
                },
            ],
            "tldr": (
                "Loop engineering is designing bounded, convergent, observable AI agent loops "
                "instead of prompting agents directly."
            ),
        }
    )


def _how_params_response(commands: list[str]) -> LLMResponse:
    """合法 how_params 段响应：每个 command 一条 executable how_step + 固定 param_entries。"""
    return _llm(
        {
            "how_steps": [
                {
                    "instruction": f"Run {cmd}",
                    "command": cmd,
                    "executable": True,
                }
                for cmd in commands
            ],
            "param_entries": [
                {"key": "max_rounds", "value": "3"},
                {"key": "budget_seconds", "value": "300"},
            ],
        }
    )


# 共享命令（两次产出都出现 → 进交集；≥3 条 executable 通过 _validate_extract_output）
_SHARED_COMMANDS = ["tools/list", "tools/call", "resources/read"]


class TestSemanticRetakeConstant:
    def test_semantic_retake_max_is_named_constant(self):
        """SC-7.1: SEMANTIC_RETAKE_MAX 是具名常量且有界（=2）。"""
        assert isinstance(SEMANTIC_RETAKE_MAX, int)
        assert SEMANTIC_RETAKE_MAX == 2
        assert SEMANTIC_RETAKE_MAX > 0

    def test_semantic_jitter_error_is_distinct_exception(self):
        """SemanticJitterError 是独立异常类型，fail-closed 语义可被调用方捕获。"""
        assert issubclass(SemanticJitterError, Exception)
        with pytest.raises(SemanticJitterError):
            raise SemanticJitterError("test")


class TestSemanticRetakeDefaultOff:
    """回归护栏：默认不设 env 时 retake 关闭，extract_claims 行为与改前一致（2 次调用）。"""

    def test_default_off_keeps_two_calls(
        self, monkeypatch, sample_raw_content, extract_success_response
    ):
        monkeypatch.delenv("AKB2_DISTILL_SEMANTIC_RETAKE", raising=False)
        from tests.quality.conftest import make_mock_route_llm

        calls = []
        base = make_mock_route_llm(extract_success_response)

        def tracking(model_family, messages, response_schema=None, **kwargs):
            calls.append(response_schema["required"][0])
            return base(model_family, messages, response_schema, **kwargs)

        result = extract_claims(sample_raw_content, route_llm_fn=tracking)

        assert result.success is True
        # 默认关：仍只 claims + how_params 两次调用，无 retake 第 3 次。
        assert calls == ["claims", "how_steps"]
        assert len(calls) == 2


class TestSemanticRetakeIntersection:
    """SC-7.1: env 开启后 how_params 产 2 次取交集。"""

    def test_retake_takes_intersection_keeps_shared_commands(
        self, monkeypatch, sample_raw_content
    ):
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "1")
        from tests.quality.conftest import make_mock_route_llm

        # 调用序：idx0=claims, idx1=how_params 样本A, idx2=how_params 样本B
        sample_a = _SHARED_COMMANDS + ["extra-only-a"]
        sample_b = _SHARED_COMMANDS + ["extra-only-b"]
        responses = {
            f"{DISTILL_MODEL_FAMILY}:0": _claims_response(),
            f"{DISTILL_MODEL_FAMILY}:1": _how_params_response(sample_a),
            f"{DISTILL_MODEL_FAMILY}:2": _how_params_response(sample_b),
        }
        mock_fn = make_mock_route_llm(responses)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is True, result.error
        commands = [s["command"] for s in result.data["how_steps"]]
        # 交集 = 两次都产出的共享命令；extra-only-* 被舍弃；保留样本A 首次顺序。
        assert commands == _SHARED_COMMANDS
        assert "extra-only-a" not in commands
        assert "extra-only-b" not in commands

    def test_retake_produces_three_calls_when_enabled(
        self, monkeypatch, sample_raw_content
    ):
        """retake 开启后 how_params 多取一次 → 总调用 3 次（有界 SEMANTIC_RETAKE_MAX）。"""
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "1")
        from tests.quality.conftest import make_mock_route_llm

        responses = {
            f"{DISTILL_MODEL_FAMILY}:0": _claims_response(),
            f"{DISTILL_MODEL_FAMILY}:1": _how_params_response(_SHARED_COMMANDS),
            f"{DISTILL_MODEL_FAMILY}:2": _how_params_response(_SHARED_COMMANDS),
        }
        mock_fn = make_mock_route_llm(responses)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is True
        # 1 claims + 2 how_params = 3（SEMANTIC_RETAKE_MAX 有界，不无限多取）
        assert mock_fn.call_counts[DISTILL_MODEL_FAMILY] == 1 + SEMANTIC_RETAKE_MAX

    def test_retake_identical_samples_keeps_all(self, monkeypatch, sample_raw_content):
        """两次产出完全一致 → 交集 = 全集（不丢步骤）。"""
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "1")
        from tests.quality.conftest import make_mock_route_llm

        responses = {
            f"{DISTILL_MODEL_FAMILY}:0": _claims_response(),
            f"{DISTILL_MODEL_FAMILY}:1": _how_params_response(_SHARED_COMMANDS),
            f"{DISTILL_MODEL_FAMILY}:2": _how_params_response(_SHARED_COMMANDS),
        }
        mock_fn = make_mock_route_llm(responses)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is True
        assert [s["command"] for s in result.data["how_steps"]] == _SHARED_COMMANDS


class TestSemanticRetakeFailClosed:
    """SC-7.2: 交集为空/连续不一致达阈值 → fail-closed（不无限重试）。"""

    def test_empty_intersection_fail_closed(
        self, monkeypatch, sample_raw_content
    ):
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "1")
        from tests.quality.conftest import make_mock_route_llm

        # 两次产出 command 完全不相交 → 交集为空 → fail-closed
        disjoint_a = ["tools/list", "tools/call", "resources/read"]
        disjoint_b = ["prompts/list", "prompts/get", "initialize"]
        responses = {
            f"{DISTILL_MODEL_FAMILY}:0": _claims_response(),
            f"{DISTILL_MODEL_FAMILY}:1": _how_params_response(disjoint_a),
            f"{DISTILL_MODEL_FAMILY}:2": _how_params_response(disjoint_b),
        }
        mock_fn = make_mock_route_llm(responses)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is False
        assert "抖动" in result.error or "fail-closed" in result.error
        assert "交集" in result.error

    def test_fail_closed_does_not_retry_infinitely(
        self, monkeypatch, sample_raw_content
    ):
        """SC-7.2 边界: fail-closed 立即返回，retries_used 受 MAX_EXTRACT_RETRIES 上界。"""
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "1")
        from tests.quality.conftest import make_mock_route_llm

        disjoint_a = ["tools/list", "tools/call", "resources/read"]
        disjoint_b = ["prompts/list", "prompts/get", "initialize"]
        responses = {
            f"{DISTILL_MODEL_FAMILY}:0": _claims_response(),
            f"{DISTILL_MODEL_FAMILY}:1": _how_params_response(disjoint_a),
            f"{DISTILL_MODEL_FAMILY}:2": _how_params_response(disjoint_b),
        }
        mock_fn = make_mock_route_llm(responses)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is False
        # 立即 fail-closed：第 1 次 attempt 即返回，不耗尽 MAX_EXTRACT_RETRIES 重试。
        assert result.retries_used <= MAX_EXTRACT_RETRIES
        # retake 调用有界：1 claims + 2 how_params（仅 1 轮 attempt，未反复重试）
        assert mock_fn.call_counts[DISTILL_MODEL_FAMILY] == 1 + SEMANTIC_RETAKE_MAX

    def test_intersect_empty_samples_helper(self):
        """_intersect_how_steps 单元：完全不相交 → 空交集（调用方据此 fail-closed）。"""
        from src.akb2.quality.distill import _intersect_how_steps

        samples = [
            _how_params_response(["tools/list"]).content,
            _how_params_response(["prompts/list"]).content,
        ]
        # _intersect_how_steps 接收 normalized segment dict（含 how_steps/param_entries）
        # 这里直接喂 _how_params_response 的 content dict（结构等价）
        result = _intersect_how_steps(samples)
        assert result["how_steps"] == []
        assert result["param_entries"]  # 取首次


class TestBoundaryGuardrails:
    """SC-7.3: must_cover kind 仍三类；两模型常量未变。"""

    def test_must_cover_kinds_remain_three_classes(self):
        """distill.py 中 must_cover kind 字面量集合仍恰为三类（未新增第 4 类）。"""
        distill_path = Path(__file__).resolve().parents[2] / "src" / "akb2" / "quality" / "distill.py"
        source = distill_path.read_text(encoding="utf-8")
        # 收集所有 `kind == "source_xxx"` 字面量（must_cover 代码直出渲染处）
        found = set(re.findall(r'\.kind\s*==\s*"(source_[a-z_]+)"', source))
        assert found == _MUST_COVER_KINDS, (
            f"must_cover kind 集合已变: 期望 {_MUST_COVER_KINDS}, 实际 {found}"
        )
        # 确认三类各自仍被代码直出渲染引用（source_operation/boundary/conclusion）
        for kind in _MUST_COVER_KINDS:
            assert kind in source

    def test_distill_model_family_unchanged(self):
        """SC-7.3: DISTILL_MODEL_FAMILY 仍为 deepseek-v4-pro（maker 未改）。"""
        assert DISTILL_MODEL_FAMILY == "deepseek-v4-pro"

    def test_primary_grader_model_unchanged(self):
        """SC-7.3: PRIMARY_GRADER_MODEL 仍为 gpt-5.4（checker 未改）。"""
        assert PRIMARY_GRADER_MODEL == "gpt-5.4"
