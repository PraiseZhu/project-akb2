"""tests/quality/test_distill_mechanism_depth.py — F24 primary 机制抽取深度 prompt 测试。

工单 F24:修 primary 机制抽取深度(addy 入库前最后一关)。deepseek 只扒表层
(抽 Call the agent/Go back to step 1 步骤碎片,没抽 brain/hands/session 命名组件),
根因:how_params prompt 没给"命名组件"定义 + 没要求"逐节读正文挖"。

测试覆盖 prompt 构建处行为断言(工单要求):
  - 命名组件定义(专有名词/加粗/大写首字母,不是泛词/步骤)
  - 逐节读正文挖(不停在标题/首句)
  - 逐字 evidence_sentence(反造假:不得意译/改写)
  - 反造假 + fail-closed(原文真没有 ≥3 不凑数)
  - 原文风格示例(Brain/Hands/Session,不是 AKB work_selector)

只测 prompt 文本断言,不打真 LLM;tmp_path 不写 vault。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))

from src.akb2.quality.distill import _build_extract_segment_messages  # noqa: E402


def _how_params_system_prompt() -> str:
    """返回 how_params 段的 system content。"""
    msgs = _build_extract_segment_messages("how_params", "原文内容")
    return next(m["content"] for m in msgs if m["role"] == "system")


# ---------------------------------------------------------------------------
# 命名组件定义 + 逐节挖正文(根因修复)
# ---------------------------------------------------------------------------


class TestNamedComponentDefinition:
    def test_prompt_defines_named_component(self) -> None:
        """prompt 给'命名组件'定义(专有名词/加粗/大写首字母/引号命名)。"""
        sp = _how_params_system_prompt()
        assert "命名组件" in sp, "prompt 应定义命名组件"
        assert "专有名词" in sp or "加粗" in sp or "大写首字母" in sp

    def test_prompt_original_style_examples(self) -> None:
        """prompt 举例原文风格组件(Brain/Hands/Session),不是 AKB work_selector。"""
        sp = _how_params_system_prompt()
        assert "Brain" in sp or "Hands" in sp or "Session" in sp, (
            "应举例原文命名组件(Brain/Hands/Session),引导 deepseek 抽原文真组件"
        )
        # 旧 AKB 风格示例应移除(避免 deepseek 找 AKB 风格不找原文)
        assert "work_selector" not in sp, "应移除 AKB 风格示例 work_selector"

    def test_prompt_excludes_steps_as_components(self) -> None:
        """prompt 明确'操作步骤(Call the agent)不是命名组件'。"""
        sp = _how_params_system_prompt()
        assert "Call the agent" in sp or "操作步骤" in sp
        assert "不是" in sp and "步骤" in sp

    def test_prompt_requires_digging_into_body(self) -> None:
        """prompt 要求'逐节读正文挖'(不停在标题/首句碎片)。"""
        sp = _how_params_system_prompt()
        assert "逐节" in sp or "正文" in sp
        assert "标题" in sp or "首句" in sp


# ---------------------------------------------------------------------------
# 反造假守恒(逐字 evidence_sentence + fail-closed 不凑数)
# ---------------------------------------------------------------------------


class TestAntiFabrication:
    def test_prompt_requires_verbatim_evidence_sentence(self) -> None:
        """prompt 要求 evidence_sentence 逐字复制自 raw(不得意译/改写)。"""
        sp = _how_params_system_prompt()
        assert "逐字" in sp, "应要求 evidence_sentence 逐字复制"
        assert "意译" in sp or "改写" in sp, "应禁止意译/改写"

    def test_prompt_anti_fabrication_fail_closed(self) -> None:
        """prompt 反造假:原文真没有 ≥3 命名组件时不凑数(fail-closed,F4 会拦)。"""
        sp = _how_params_system_prompt()
        assert "反造假" in sp or "不凑数" in sp or "不编造" in sp
        assert "fail-closed" in sp or "F4" in sp

    def test_prompt_keeps_evidence_anchor_requirement(self) -> None:
        """prompt 仍要求 evidence_anchor(§X 指向原文出处)——反造假护栏不变。"""
        sp = _how_params_system_prompt()
        assert "evidence_anchor" in sp
        assert "§X" in sp or "§" in sp


# ---------------------------------------------------------------------------
# 既有契约不破(component_id 后处理 + causal_edges 宁少勿强)
# ---------------------------------------------------------------------------


class TestExistingContract:
    def test_prompt_component_id_postprocess(self) -> None:
        """prompt 仍声明 component_id 由后处理生成(不需 LLM 产)。"""
        sp = _how_params_system_prompt()
        assert "component_id" in sp
        assert "后处理" in sp

    def test_prompt_causal_edges_anti_fabrication(self) -> None:
        """causal_edges 仍'宁少勿强'(无原文支撑不编造)——反造假护栏不变。"""
        sp = _how_params_system_prompt()
        assert "causal_edges" in sp
        assert "宁少勿强" in sp or "不编造" in sp

    def test_prompt_returns_valid_messages_structure(self) -> None:
        """_build_extract_segment_messages 返回 system+user 双消息(契约不变)。"""
        msgs = _build_extract_segment_messages("how_params", "原文")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "原文" in msgs[1]["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
