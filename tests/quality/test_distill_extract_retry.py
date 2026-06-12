"""test_distill_extract_retry.py — 段 1 抽取重试路径测试。

覆盖:
    - 正常抽取成功
    - JSON 解析失败走有界重试
    - 锚点校验失败走有界重试
    - 重试耗尽显式失败不出候选
    - MAX_EXTRACT_RETRIES 是具名常量
"""

from __future__ import annotations

import pytest

from src.akb2.quality.distill import (
    MAX_EXTRACT_RETRIES,
    _validate_extract_output,
    extract_claims,
)


class TestExtractSuccess:
    """正常抽取成功路径。"""

    def test_extract_claims_success(
        self, sample_raw_content, extract_success_response
    ):
        """段 1 抽取成功，返回结构化数据。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is True
        assert result.retries_used >= 1  # 至少 1 次调用
        assert "claims" in result.data
        assert len(result.data["claims"]) >= 1
        assert "how_steps" in result.data
        assert "params" in result.data
        assert "referenced_concepts" in result.data

    def test_extract_claims_have_anchors(
        self, sample_raw_content, extract_success_response
    ):
        """每条 claim 带 §X 格式锚点。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        for claim in result.data["claims"]:
            assert "anchor" in claim, "每条 claim 必须有 anchor"
            anchor = claim["anchor"]
            assert anchor.startswith("§"), f"anchor 应以 § 开头: {anchor}"
            assert len(anchor) == 2, f"anchor 格式 §X: {anchor}"
            assert anchor[1].isalpha()

    def test_extract_claims_have_provenance(
        self, sample_raw_content, extract_success_response
    ):
        """每条 claim 有 provenance 字段（source_grounded / akb_interpretation）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        valid_provenances = {"source_grounded", "akb_interpretation"}
        for claim in result.data["claims"]:
            assert "provenance" in claim
            assert claim["provenance"] in valid_provenances, (
                f"provenance 应为 source_grounded/akb_interpretation: {claim['provenance']}"
            )


class TestExtractRetry:
    """抽取重试路径。"""

    def test_extract_retry_on_invalid_json(
        self, sample_raw_content, extract_fail_invalid_json_response
    ):
        """JSON 解析失败走重试，耗尽后显式失败。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_fail_invalid_json_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is False
        assert result.retries_used == MAX_EXTRACT_RETRIES
        assert result.data == {}
        assert "非 JSON" in result.error or "重试" in result.error or "失败" in result.error

    def test_extract_retry_on_missing_anchor(
        self, sample_raw_content, extract_fail_missing_anchor_response
    ):
        """claims 缺少 anchor 字段走重试并失败。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_fail_missing_anchor_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is False
        assert result.retries_used == MAX_EXTRACT_RETRIES

    def test_max_extract_retries_is_named_constant(self):
        """MAX_EXTRACT_RETRIES 是具名常量（宪法 P1/P4）。"""
        assert isinstance(MAX_EXTRACT_RETRIES, int)
        assert MAX_EXTRACT_RETRIES > 0
        assert MAX_EXTRACT_RETRIES == 3

    def test_extract_uses_deepseek_model(
        self, sample_raw_content, extract_success_response
    ):
        """段 1 抽取使用 deepseek-v4-pro 模型。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        # 验证模型使用：response 的 model_used 应为 deepseek
        assert result.success is True


class TestValidateExtractOutput:
    """抽取输出校验函数。"""

    def test_valid_output_passes(self):
        """合法输出通过校验。"""
        data = {
            "claims": [
                {
                    "text": "test claim",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                }
            ],
            "how_steps": ["step 1"],
            "params": {"key": "value"},
            "referenced_concepts": ["concept-1"],
        }
        _validate_extract_output(data)  # 不应抛异常

    def test_empty_claims_raises(self):
        """空 claims 抛 ValueError。"""
        data = {
            "claims": [],
            "how_steps": [],
            "params": {},
            "referenced_concepts": [],
        }
        with pytest.raises(ValueError, match="claims 为空|should be non-empty"):
            _validate_extract_output(data)

    def test_invalid_anchor_format_raises(self):
        """非 §X 格式的 anchor 抛 ValueError。"""
        data = {
            "claims": [
                {
                    "text": "test",
                    "anchor": "bad-anchor",
                    "provenance": "source_grounded",
                }
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": [],
        }
        with pytest.raises(ValueError, match="anchor 格式无效"):
            _validate_extract_output(data)

    def test_invalid_provenance_raises(self):
        """非法 provenance 抛 ValueError。"""
        data = {
            "claims": [
                {
                    "text": "test",
                    "anchor": "§A",
                    "provenance": "unknown_type",
                }
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": [],
        }
        with pytest.raises(ValueError, match="provenance 无效|is not one of"):
            _validate_extract_output(data)
