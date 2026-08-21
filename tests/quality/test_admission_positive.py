"""test_admission_positive.py — 正样本准入通过测试。

覆盖:
    - 黄金样张 raw fixture 三硬判定全 pass
    - 输出为 schema 化布尔+理由，无连续分数字段
    - AdmissionResult 结构完整性
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.admission import run_admission


class TestPositiveAdmission:
    """正样本准入全 pass 测试。"""

    def test_positive_sample_all_three_pass(
        self, sample_raw_content, sample_raw_path, tmp_ledger_path, admission_pass_responses
    ):
        """黄金样张 raw fixture 三硬判定全 pass（SC-3.2）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_pass_responses)

        result = run_admission(
            raw_path=sample_raw_path,
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        assert result.passed is True, f"正样本应通过准入: {result.reason}"
        assert result.reason == "", "通过时 reason 应为空字符串"
        # WP-2: concept 候选追加 mechanism_signal 门（黄金样张 5 编号步+code → 确定性 pass）
        assert len(result.checks) == 4, "concept 候选应有 4 条判定（含 mechanism_signal）"

    def test_positive_sample_checks_have_required_fields(
        self, sample_raw_content, sample_raw_path, tmp_ledger_path, admission_pass_responses
    ):
        """每条判定含 name / passed / reason 三字段。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_pass_responses)

        result = run_admission(
            raw_path=sample_raw_path,
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        for check in result.checks:
            assert hasattr(check, "name"), "每条判定应有 name"
            assert isinstance(check.name, str) and len(check.name) > 0
            assert hasattr(check, "passed"), "每条判定应有 passed"
            assert isinstance(check.passed, bool)
            assert hasattr(check, "reason"), "每条判定应有 reason"
            assert isinstance(check.reason, str) and len(check.reason) > 0

    def test_positive_sample_topic_relevance_check(
        self, sample_raw_content, sample_raw_path, tmp_ledger_path, admission_pass_responses
    ):
        """主题相关性判定通过（命中领域 taxonomy 锚点）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_pass_responses)

        result = run_admission(
            raw_path=sample_raw_path,
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        topic_check = result.checks[0]
        assert topic_check.name == "topic_relevance"
        assert topic_check.passed is True
        assert "Loop Engineering" in topic_check.reason or "AI Agent" in topic_check.reason

    def test_positive_sample_how_tendency_check(
        self, sample_raw_content, sample_raw_path, tmp_ledger_path, admission_pass_responses
    ):
        """How 倾向判定通过（含可执行知识）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_pass_responses)

        result = run_admission(
            raw_path=sample_raw_path,
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        how_check = result.checks[1]
        assert how_check.name == "how_tendency"
        assert how_check.passed is True

    def test_positive_sample_novelty_check_default_pass(
        self, sample_raw_content, sample_raw_path, tmp_ledger_path, admission_pass_responses
    ):
        """新颖度判定默认通过（无既有 wiki 引用时）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_pass_responses)

        result = run_admission(
            raw_path=sample_raw_path,
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            wiki_references=None,  # 无既有引用
            route_llm_fn=mock_fn,
        )

        novelty_check = result.checks[2]
        assert novelty_check.name == "novelty"
        # 无 wiki references 时 route_llm 不会被调用（直接返回 pass）
        # 但我们的 mock 已预设了 pass 响应

    def test_no_score_field_in_admission_result(
        self, sample_raw_content, sample_raw_path, tmp_ledger_path, admission_pass_responses
    ):
        """准入结果无连续分数字段（宪法 P8）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_pass_responses)

        result = run_admission(
            raw_path=sample_raw_path,
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        # AdmissionResult 只有 passed/checks/reason/topic_borderline，无 score/quality 等连续字段
        from src.akb2.quality.admission import AdmissionResult

        assert "topic_borderline" in AdmissionResult.__dataclass_fields__
        assert result.topic_borderline is False
        result_dict = {
            "passed": result.passed,
            "checks": [{"name": c.name, "passed": c.passed, "reason": c.reason} for c in result.checks],
            "reason": result.reason,
            "topic_borderline": result.topic_borderline,
        }

        # 确保没有 score/rating/quality 等连续值字段
        forbidden_keys = {"score", "rating", "quality", "level", "grade", "rank"}
        for key in forbidden_keys:
            assert key not in result_dict, f"准入结果不应含 {key} 连续分数字段（宪法 P8）"

    def test_positive_sample_no_ledger_write(
        self, sample_raw_content, sample_raw_path, tmp_ledger_path, admission_pass_responses
    ):
        """正样本通过时不写 rejection 到账本。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_pass_responses)

        run_admission(
            raw_path=sample_raw_path,
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        ledger_path = Path(tmp_ledger_path)
        # 通过时不落账，文件应为空（或只有空行）
        with open(ledger_path, encoding="utf-8") as fh:
            content = fh.read().strip()
        assert content == "", f"正样本通过不应写 rejection 到账本，但账本有内容: {content[:200]}"
