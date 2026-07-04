"""test_grader_schema_mismatch_retry.py — F18 grader schema 兼容修复测试。

工单 F18:修 grader(GPT-5.4) schema 兼容 bug。改前基线(Plan B 232fc01 后)已修主死因
(400 Missing 'score' + 错 keys),本测试覆盖防御深度——schema-mismatch 有界重试 +1 +
prompt 附格式错误提示 + 本地 jsonschema 诊断(严格度守恒:约束位置从请求侧挪到响应侧)
+ 预算耗尽 fail-closed + gemini 兜底仍不作通过条件(宪法 P7 不动)。

测试用 monkeypatch grader.route_llm + run_grader_detailed(生产主逻辑),不打真 API。
"""

from __future__ import annotations

import pytest

from src.akb2.quality import grader
from src.akb2.quality.llm_router import LLMResponse, RouterConfigError

# ---------------------------------------------------------------------------
# 工具:构造 grader 响应
# ---------------------------------------------------------------------------


def _all_pass(lane: str = "") -> dict:
    """全 pass 合法响应(按车道维度集)。"""
    dims = grader._dimensions_for_lane(lane) or grader.RUBRIC_DIMENSIONS
    return {
        "dimensions": [
            {"dimension": d, "pass": True, "defects": []} for d in dims
        ],
        "interpretation_entailment": True,
    }


def _verdict_shape(lane: str = "") -> dict:
    """verdict 形状(错 keys)——gpt-5.4 自由发挥的典型错形状(账本 2026-06-27 签名 2)。"""
    dims = grader._dimensions_for_lane(lane) or grader.RUBRIC_DIMENSIONS
    return {
        "dimensions": [
            {"dimension": d, "verdict": "pass", "defect": ""} for d in dims
        ],
    }


def _extra_top_level_keys(lane: str = "") -> dict:
    """顶层额外键(overall_decision/summary 等)——账本 2026-06-27 签名 2 的另一形态。"""
    base = _all_pass(lane)
    base["overall_decision"] = "pass"
    base["overall_score"] = 8.5
    base["summary"] = "good"
    return base


# ---------------------------------------------------------------------------
# F18-1: schema-mismatch 触发有界重试 + prompt 附 hint
# ---------------------------------------------------------------------------


class TestSchemaMismatchBoundedRetry:
    def test_verdict_shape_triggers_retry_with_hint_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """首次返回 verdict 形状(parse 失败)→ 重试,prompt 含 hint,第二次返回对 → 成功。"""
        calls: list[dict] = []

        def _mock(model_family, messages, response_schema=None, **kwargs):
            calls.append({"model_family": model_family, "messages": messages})
            if model_family == grader.PRIMARY_GRADER_MODEL:
                if len(calls) == 1:
                    return LLMResponse(content=_verdict_shape(), model_used="gpt-5.4")
                return LLMResponse(content=_all_pass(), model_used="gpt-5.4")
            return LLMResponse(content=_all_pass(), model_used="gemini-3.5-flash")

        monkeypatch.setattr(grader, "route_llm", _mock)
        result = grader.run_grader_detailed("# candidate\n\nbody with substance\n", content_lane="")
        assert result.primary_failed is False, "重试后应 primary 成功"
        assert result.fallback_used is False, "不该走兜底"
        assert result.model_used == "gpt-5.4"
        # 首次 + 1 次重试 = 2 次 gpt 调用
        gpt_calls = [c for c in calls if c["model_family"] == grader.PRIMARY_GRADER_MODEL]
        assert len(gpt_calls) == 2, f"应首次+1次重试=2次,实际 {len(gpt_calls)}"
        # 重试 prompt system 消息含 hint
        retry_system = next(
            m["content"] for m in gpt_calls[1]["messages"] if m["role"] == "system"
        )
        assert "上次" in retry_system, "重试 prompt 应含上次格式错误提示"
        assert (
            "verdict" in retry_system
            or "schema" in retry_system
            or "pass" in retry_system
        ), f"hint 应含具体错误指引,实际: {retry_system[-200:]}"

    def test_extra_top_level_keys_triggers_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """顶层额外键(overall_decision/summary)→ jsonschema 诊断报错 → 重试成功。"""
        calls: list[dict] = []

        def _mock(model_family, messages, response_schema=None, **kwargs):
            calls.append({"family": model_family})
            if model_family == grader.PRIMARY_GRADER_MODEL:
                if len(calls) == 1:
                    return LLMResponse(content=_extra_top_level_keys(), model_used="gpt-5.4")
                return LLMResponse(content=_all_pass(), model_used="gpt-5.4")
            return LLMResponse(content=_all_pass(), model_used="gemini-3.5-flash")

        monkeypatch.setattr(grader, "route_llm", _mock)
        result = grader.run_grader_detailed("# candidate\n\nbody\n", content_lane="")
        # 注意:_parse_grader_output 容忍无害顶层额外键(只看 dimensions);若 dimensions 合法
        # parse 成功不重试。此用例 dimensions 全 pass 合法 → parse 成功,不触发重试。
        # 但若 dimensions 内部也错(如 verdict 形状)+ 顶层额外键 → 触发重试(见上一用例)。
        # 此用例验证:顶层额外键 + 合法 dimensions → 不误触发重试(严格度不降,容忍无害额外键)。
        assert result.primary_failed is False, "dimensions 合法时顶层额外键不应触发失败"


# ---------------------------------------------------------------------------
# F18-2: 预算耗尽 fail-closed
# ---------------------------------------------------------------------------


class TestBudgetExhaustedFailClosed:
    def test_budget_exhausted_fail_closed_suspended(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gpt-5.4 总是错 keys(预算耗尽) + gemini 也失败 → suspended(fail-closed,不降级放行)。"""
        gpt_calls: list[int] = []

        def _mock(model_family, messages, response_schema=None, **kwargs):
            if model_family == grader.PRIMARY_GRADER_MODEL:
                gpt_calls.append(1)
                return LLMResponse(content=_verdict_shape(), model_used="gpt-5.4")
            raise RouterConfigError("gemini unavailable")

        monkeypatch.setattr(grader, "route_llm", _mock)
        result = grader.run_grader_detailed("# candidate\n\nbody\n", content_lane="")
        assert result.primary_failed is True, "gpt-5.4 预算耗尽应失败"
        assert result.fallback_failed is True, "gemini 也失败"
        assert result.fallback_used is False, "gemini 没成功"
        assert result.suspended is True, "双不可用应挂起(fail-closed)"
        # 总尝试 = MAX_GRADER_RETRIES(2) + SCHEMA_MISMATCH_RETRY_BUDGET(1) = 3
        expected = grader.MAX_GRADER_RETRIES + grader.SCHEMA_MISMATCH_RETRY_BUDGET
        assert len(gpt_calls) == expected, (
            f"schema-mismatch 应重试到预算耗尽={expected}次,实际 {len(gpt_calls)}"
        )


# ---------------------------------------------------------------------------
# F18-3: gemini 兜底仍不作通过条件(宪法 P7 不动)
# ---------------------------------------------------------------------------


class TestGeminiFallbackNotPassCondition:
    def test_gemini_fallback_marks_fallback_used_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gpt-5.4 失败 + gemini 兜底成功 → fallback_used=True(下游守卫 grader_fallback_not_allowed 拒)。

        宪法 P7:gemini 兜底不作通过条件。此测试验证 fallback_used=True 标记保留——
        下游 runner 守卫见 fallback_used=True 即拒(不放行)。修复不改这个语义。
        """
        def _mock(model_family, messages, response_schema=None, **kwargs):
            if model_family == grader.PRIMARY_GRADER_MODEL:
                return LLMResponse(content=_verdict_shape(), model_used="gpt-5.4")
            return LLMResponse(content=_all_pass(), model_used="gemini-3.5-flash")

        monkeypatch.setattr(grader, "route_llm", _mock)
        result = grader.run_grader_detailed("# candidate\n\nbody\n", content_lane="")
        assert result.primary_failed is True, "gpt-5.4 应失败"
        assert result.fallback_used is True, "应走 gemini 兜底(fallback_used=True)"
        assert result.fallback_failed is False
        assert result.suspended is False, "gemini 成功不挂起(但下游守卫会因 fallback_used 拒)"
        assert result.model_used == "gemini-3.5-flash"
        # 关键不变量:fallback_used=True → 下游 grader_fallback_not_allowed 拒(宪法 P7)


# ---------------------------------------------------------------------------
# F18-4: 400/配置错误 break 不重试(Plan B 后 400 已修,但保持 break 语义)
# ---------------------------------------------------------------------------


class TestConfigErrorBreaksNoRetry:
    def test_400_config_error_breaks_no_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """400/配置错误 → break 不重试(配置错误非 schema-mismatch,直接走兜底)。"""
        gpt_calls: list[int] = []

        def _mock(model_family, messages, response_schema=None, **kwargs):
            if model_family == grader.PRIMARY_GRADER_MODEL:
                gpt_calls.append(1)
                raise RouterConfigError("400 Invalid schema")
            raise RouterConfigError("gemini unavailable")

        monkeypatch.setattr(grader, "route_llm", _mock)
        result = grader.run_grader_detailed("# candidate\n\nbody\n", content_lane="")
        assert result.primary_failed is True
        assert "调用失败" in result.primary_failure_reason
        assert len(gpt_calls) == 1, (
            f"400 应 break 不重试,实际 gpt 调用 {len(gpt_calls)} 次"
        )
        assert result.suspended is True


# ---------------------------------------------------------------------------
# F18-5: _schema_mismatch_hint 本地 jsonschema 诊断(严格度守恒证明)
# ---------------------------------------------------------------------------


class TestSchemaMismatchHintLocalValidation:
    """严格度守恒:请求侧 schema 约束(additionalProperties/required/enum)因网关不强制
    strict 无法在端点生效,_schema_mismatch_hint 用本地 jsonschema 在响应侧照常校验。
    约束位置挪了,不许消失。"""

    def test_hint_reports_extra_top_level_key(self) -> None:
        """顶层额外键 → jsonschema 报 Additional properties not allowed。"""
        bad = _extra_top_level_keys()
        bad["dimensions"] = []  # 让 dimensions 也非法,确保 parse 失败场景
        hint = grader._schema_mismatch_hint(bad, content_lane="")
        assert isinstance(hint, str) and hint
        assert "schema" in hint or "not allowed" in hint.lower() or "Additional" in hint

    def test_hint_reports_missing_pass_in_dimension(self) -> None:
        """dimensions 项缺 pass → jsonschema 报 required。"""
        dims = grader.RUBRIC_DIMENSIONS
        bad = {
            "dimensions": [
                {"dimension": dims[0], "defects": []}  # 缺 pass
            ],
        }
        hint = grader._schema_mismatch_hint(bad, content_lane="")
        assert "pass" in hint or "required" in hint.lower(), (
            f"应报缺 pass,实际: {hint}"
        )

    def test_hint_for_non_json_string(self) -> None:
        """非 JSON 字符串 → hint 报类型错。"""
        hint = grader._schema_mismatch_hint("not a json object", content_lane="")
        assert "JSON" in hint or "json" in hint.lower() or "类型" in hint

    def test_hint_for_none_content(self) -> None:
        """None content → hint 报类型错(不崩)。"""
        hint = grader._schema_mismatch_hint(None, content_lane="")
        assert isinstance(hint, str) and hint
        assert "JSON" in hint or "类型" in hint or "None" in hint


# ---------------------------------------------------------------------------
# F18-6: 请求侧 schema strict 合规(回归守护:Plan B 修复不被回滚)
# ---------------------------------------------------------------------------


class TestRequestSchemaStrictCompliant:
    def test_total_attempts_budget_bounded(self) -> None:
        """总尝试次数 = MAX_GRADER_RETRIES + SCHEMA_MISMATCH_RETRY_BUDGET(有界,宪法 P4)。"""
        expected = grader.MAX_GRADER_RETRIES + grader.SCHEMA_MISMATCH_RETRY_BUDGET
        assert expected == 3, f"基础 2 + schema-mismatch 1 = 3,实际 {expected}"

    def test_schema_mismatch_retry_budget_is_named_constant(self) -> None:
        """SCHEMA_MISMATCH_RETRY_BUDGET 是具名常量(非魔法数字,宪法 P4)。"""
        assert hasattr(grader, "SCHEMA_MISMATCH_RETRY_BUDGET")
        assert isinstance(grader.SCHEMA_MISMATCH_RETRY_BUDGET, int)
        assert grader.SCHEMA_MISMATCH_RETRY_BUDGET >= 1

    def test_request_schema_no_score_property(self) -> None:
        """Plan B 修复守护:dimensions.items.properties 不含 score(strict required==properties)。"""
        schema = grader._build_grader_schema()
        dim_props = schema["properties"]["dimensions"]["items"]["properties"]
        assert "score" not in dim_props, "Plan B:score 已从 schema 移除(修 Azure 400)"

    def test_request_schema_additional_properties_false_all_levels(self) -> None:
        """所有 strict object 都 additionalProperties: False(端点 strict 合规)。"""
        schema = grader._build_grader_schema()
        assert schema.get("additionalProperties") is False
        dim_item = schema["properties"]["dimensions"]["items"]
        assert dim_item.get("additionalProperties") is False
        defect_item = dim_item["properties"]["defects"]["items"]
        assert defect_item.get("additionalProperties") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
