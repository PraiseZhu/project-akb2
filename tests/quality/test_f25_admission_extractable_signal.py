"""test_f25_admission_extractable_signal.py — F25 准入仲裁职责校准测试。

双盲审计(F25-A 数据面/F25-B 机制面)会师结论:mechanism_signal 仲裁在用
「文章是否以机制文体自足展开」的文体尺(与后链 F4 的结构尺不同),把综述体
里散布真机制的文章(addy Long-running Agents 等)随机拦在准入,且同文多跑
结局翻转(16 次准入 11 过 5 拒)。lead 裁决(2026-07-06):改仲裁提问口径为
「正文是否存在可提炼的机制信号」——组件散布在正文任意处/综述对比文均算;
**wiki 入库标准零改动**(F4/A4/H3/entailment/五闸原样),垃圾仍由否决线+
后链拦。本文件锁:新口径在 prompt 里 + 否决线未丢 + 判定结构未变。

真营销/新闻反例回归由既有 test_f13_lead_fixes.py 全量守护(必须保持绿)。
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality.admission import _check_mechanism_signal
from src.akb2.quality.llm_router import LLMResponse

# 综述体机制文样本:组件散布在段落句子里(addy 形态缩影)
_SURVEY_WITH_MECHANISM = "\n".join(
    [
        "## The state of long-running agents",
        "This is a survey of how vendors approach the problem.",
        "The argument is that an agent has three components that should be "
        "independently replaceable. The Brain is the model and the harness loop "
        "that calls it. The Hands are sandboxed execution environments where "
        "tools actually run. The Session is an append-only event log.",
        "## Another vendor",
        "Cursor splits the work across planners, workers, and judges. Planners "
        "produce task specs, workers execute them in parallel, and judges "
        "evaluate the results before merging.",
    ]
)


def _capture_prompt(captured: list) -> Any:
    def _fn(_model: Any, messages: list, **_k: Any) -> LLMResponse:
        captured.append(messages)
        return LLMResponse(
            content='{"pass": true, "reason": "存在可提炼机制信号"}', model_used="mock"
        )

    return _fn


def _system_prompt_of(captured: list) -> str:
    return str(captured[0][0]["content"])


class TestArbitrationQuestionRecalibrated:
    """F25 核心:仲裁问「信号是否存在」,不问「文体是否自足展开」。"""

    def test_prompt_asks_extractable_signal_not_style(self) -> None:
        captured: list = []
        _check_mechanism_signal(_SURVEY_WITH_MECHANISM, _capture_prompt(captured))
        sp = _system_prompt_of(captured)
        assert "可提炼的机制信号" in sp, "核心问题应为信号存在性"
        assert "提炼器" in sp, "应指明结构化转写是提炼器的工作"
        # 旧文体尺措辞不再是核心问题(「阐述了一个可复用的机制」为旧问法)
        assert "是否阐述了一个可复用" not in sp

    def test_prompt_covers_survey_and_scattered_components(self) -> None:
        """综述/对比文条款 + 组件散布条款必须在(addy 死因的直接解药)。"""
        captured: list = []
        _check_mechanism_signal(_SURVEY_WITH_MECHANISM, _capture_prompt(captured))
        sp = _system_prompt_of(captured)
        assert "综述" in sp and "对比" in sp
        assert "不得以「文体是综述/观点散文」为由判 false" in sp
        assert "散布" in sp or "任意处" in sp

    def test_prompt_keeps_marketing_and_news_veto(self) -> None:
        """否决线一字不丢:营销收益清单/纯政策新闻观点仍判 false。"""
        captured: list = []
        _check_mechanism_signal(_SURVEY_WITH_MECHANISM, _capture_prompt(captured))
        sp = _system_prompt_of(captured)
        assert "营销" in sp and "收益清单" in sp
        assert "应判 false" in sp
        assert "纯政策/新闻/观点" in sp

    def test_prompt_component_examples_align_with_extractor(self) -> None:
        """仲裁的组件示例与 F24 提炼器同口径(Brain/Hands/Session)——同一把尺。"""
        captured: list = []
        _check_mechanism_signal(_SURVEY_WITH_MECHANISM, _capture_prompt(captured))
        sp = _system_prompt_of(captured)
        assert "Brain/Hands/Session" in sp


class TestAdjudicationStructureUnchanged:
    """判定结构零改动:LLM 仍唯一放行人、可否决;fail-closed 保持。"""

    def test_llm_veto_still_effective(self) -> None:
        """LLM 判 false → 仍拒(新口径没有制造任何确定性放行)。"""

        def _llm_says_no(*_a: Any, **_k: Any) -> LLMResponse:
            return LLMResponse(
                content='{"pass": false, "reason": "无机制"}', model_used="mock"
            )

        check = _check_mechanism_signal(_SURVEY_WITH_MECHANISM, _llm_says_no)
        assert check.passed is False
        assert not check.reason.startswith("确定性机制信号")

    def test_strong_negative_deterministic_fail_untouched(self) -> None:
        """全零信号 + 短正文 → 仍确定性 fail,不进 LLM(强反信号路径不变)。"""

        def _must_not_call(*_a: Any, **_k: Any) -> LLMResponse:
            raise AssertionError("强反信号不应调用 LLM")

        check = _check_mechanism_signal("short opinion text.", _must_not_call)
        assert check.passed is False
