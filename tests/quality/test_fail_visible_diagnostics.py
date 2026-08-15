"""test_fail_visible_diagnostics.py — F-C fail-visible 诊断字段测试。

覆盖:
    - _classify_distill_failure_reason: schema错/超时/重试耗尽/provider/other 分类
    - residual_defects / distill_extract_failure_reason 字段透传(合成 pipeline_result)
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality.calibration import _classify_distill_failure_reason


class TestClassifyDistillFailureReason:
    def test_retry_exhausted(self):
        assert _classify_distill_failure_reason("抽取重试耗尽（3）", "extract_failed") == "retry_exhausted"
        assert _classify_distill_failure_reason("retries exhausted", "extract_failed") == "retry_exhausted"

    def test_timeout(self):
        assert _classify_distill_failure_reason("抽取调用超时 (DeadlineError)", "extract_failed") == "timeout"
        assert _classify_distill_failure_reason("distill timeout after 60s", "") == "timeout"

    def test_schema_invalid(self):
        assert _classify_distill_failure_reason(
            "抽取校验失败（3/3）: schema 校验失败: 'anchor' is required", "schema_validation_failed"
        ) == "schema_invalid"
        assert _classify_distill_failure_reason("json parse error", "invalid_json") == "schema_invalid"

    def test_provider_error(self):
        assert _classify_distill_failure_reason("抽取调用失败（1/3）: API 503", "provider_error") == "provider_error"

    def test_other(self):
        assert _classify_distill_failure_reason("unknown glitch", "extract_failed") == "other"

    def test_retry_takes_priority_over_schema(self):
        """重试耗尽消息(不含 schema 词)优先于 schema 分类。"""
        assert _classify_distill_failure_reason("抽取重试耗尽（3）", "extract_failed") == "retry_exhausted"


# ---------------------------------------------------------------------------
# 字段透传:用合成 pipeline_result 跑 _process_corpus_entry 的诊断透传段
# ---------------------------------------------------------------------------


class TestFieldSurfacing:
    """验证 residual_defects / distill_extract_failure_reason 在 repair 不收敛 /
    distill 失败时被透传到 entry。用 monkeypatch 把 _run_pipeline_segments 替成合成返回。
    """

    def _make_entry_result(self, monkeypatch, pipeline_result: dict[str, Any]) -> dict[str, Any]:
        """跑 _process_corpus_entry 用合成 pipeline_result,返回 result dict。"""
        from src.akb2.quality import calibration as calib

        def _fake_run_pipeline_segments(raw_path, raw_content, config):
            return pipeline_result

        monkeypatch.setattr(calib, "_run_pipeline_segments", _fake_run_pipeline_segments, raising=False)
        # _process_corpus_entry 内 late-import runner._run_pipeline_segments → patch runner 模块
        import src.akb2.quality.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_run_pipeline_segments", _fake_run_pipeline_segments, raising=False)

        config = calib._default_config()
        config["raw_store_dir"] = None  # 不写 raw-store
        entry = {
            "url": "fixture:ag2-captain-agent-concept",
            "domain": "AI Agent",
            "expected": "ingest",
        }
        return calib._process_corpus_entry(entry, 0, config)

    def test_residual_defects_surfaced_on_repair_non_converge(self, monkeypatch, tmp_path):
        """repair 不收敛 + final_defects_summary 非空 → residual_defects(维度名列表)。"""
        pr = {
            "converged": False,
            "rounds": 2,
            "final_status": "failed",
            "error": "质量不达标: distill_no_converge — 修复不收敛: 3 项缺陷残留",
            "failed_node": "repair_loop",
            "error_class": "distill_no_converge",
            "content_lane": "concept",
            "final_defects_summary": [
                {"dimension": "SG-raw-anchor", "location": "正文", "defect": "x", "suggestion": "y"},
                {"dimension": "F4-eligible-edge", "location": "图谱", "defect": "z", "suggestion": "w"},
                {"dimension": "A3-锚点真实", "location": "正文", "defect": "a", "suggestion": "b"},
            ],
        }
        result = self._make_entry_result(monkeypatch, pr)
        assert result["residual_defects"] == ["SG-raw-anchor", "F4-eligible-edge", "A3-锚点真实"]
        assert result["failed_node"] == "repair_loop"

    def test_distill_extract_failure_reason_surfaced(self, monkeypatch):
        """distill_extract 失败 → distill_extract_failure_reason 分类。"""
        pr = {
            "converged": False,
            "rounds": 0,
            "final_status": "failed",
            "error": "抽取校验失败（3/3）: schema 校验失败: 'anchor' is required",
            "failed_node": "distill_extract",
            "error_class": "schema_validation_failed",
            "content_lane": "concept",
        }
        result = self._make_entry_result(monkeypatch, pr)
        assert result["distill_extract_failure_reason"] == "schema_invalid"

    def test_no_residual_defects_when_converged(self, monkeypatch):
        """repair 收敛(无 final_defects_summary)→ 无 residual_defects 字段。"""
        pr = {
            "converged": True,
            "rounds": 1,
            "final_status": "promoted",
            "promotion_outcome": "active_promoted",
            "failed_node": None,
            "error_class": None,
            "content_lane": "concept",
        }
        result = self._make_entry_result(monkeypatch, pr)
        assert "residual_defects" not in result
