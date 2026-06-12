"""test_admission_negative.py — 负样本准入拒收测试（承载 master SC-2）。

覆盖:
    - 新闻型负样本 100% 拒于准入段（How 倾向 fail）
    - 洗稿型负样本 100% 拒于准入段（新颖度 fail）
    - 拒收原因落账
    - 账本零提炼调用
"""

from __future__ import annotations

import json
from pathlib import Path

from src.akb2.quality.admission import run_admission


class TestNewsNegativeSample:
    """新闻型负样本：How 倾向应 fail。"""

    def test_news_content_rejected(self, news_content, tmp_ledger_path, admission_fail_news_response):
        """新闻型内容被准入段拒收（SC-3.1 / master SC-2）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_fail_news_response)

        result = run_admission(
            raw_path="raw/google-io-recap--raw--2026-06-10.md",
            raw_content=news_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        assert result.passed is False, "新闻型内容应被拒收"
        assert len(result.checks) == 3, "应有 3 条判定"

        # 主题相关性应 pass（命中 AI 领域）
        topic_check = result.checks[0]
        assert topic_check.name == "topic_relevance"
        assert topic_check.passed is True

        # How 倾向应 fail
        how_check = result.checks[1]
        assert how_check.name == "how_tendency"
        assert how_check.passed is False
        assert "新闻" in how_check.reason or "无可执行" in how_check.reason or "步骤" in how_check.reason

        # 拒收原因正确
        assert "准入未通过" in result.reason
        assert "how_tendency" in result.reason

    def test_news_rejection_logs_to_ledger(self, news_content, tmp_ledger_path, admission_fail_news_response):
        """拒收后账本有 rejection event，零提炼调用。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_fail_news_response)

        run_admission(
            raw_path="raw/google-io-recap--raw--2026-06-10.md",
            raw_content=news_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        # 验证账本文件存在且有内容
        ledger_path = Path(tmp_ledger_path)
        assert ledger_path.exists(), "账本文件应存在"

        with open(ledger_path, encoding="utf-8") as fh:
            lines = [line.strip() for line in fh if line.strip()]

        assert len(lines) >= 1, "账本至少有一条拒收记录"

        rejection = json.loads(lines[0])
        assert rejection["loop_id"] == "loop1"
        assert rejection["node"] == "admission"
        assert rejection["event"] == "admission"
        assert rejection["details"]["admission_result"] == "rejected"
        assert "how_tendency" in rejection["details"]["failed_checks"]


class TestPlagiarismNegativeSample:
    """洗稿型负样本：新颖度应 fail。"""

    def test_plagiarism_content_rejected(
        self, sample_raw_content, tmp_ledger_path, admission_fail_plagiarism_response
    ):
        """洗稿型内容被准入段拒收（新颖度 fail）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_fail_plagiarism_response)

        wiki_refs = [
            "Loop Engineering: bounded agent loops with convergence and observability",
            "Harness Engineering: agent runtime and tool definitions",
        ]

        result = run_admission(
            raw_path="raw/zynd-ai-rewrite--raw--2026-06-10.md",
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            wiki_references=wiki_refs,
            route_llm_fn=mock_fn,
        )

        assert result.passed is False, "洗稿型内容应被拒收"
        assert len(result.checks) == 3

        # 主题相关性应 pass
        assert result.checks[0].passed is True

        # How 倾向应 pass
        assert result.checks[1].passed is True

        # 新颖度应 fail
        novelty_check = result.checks[2]
        assert novelty_check.name == "novelty"
        assert novelty_check.passed is False
        assert "重叠" in novelty_check.reason or "增量" in novelty_check.reason or "重复" in novelty_check.reason

    def test_plagiarism_rejection_logs_to_ledger(
        self, sample_raw_content, tmp_ledger_path, admission_fail_plagiarism_response
    ):
        """拒收后账本有 rejection event，含失败判定详情。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_fail_plagiarism_response)

        wiki_refs = ["Loop Engineering: bounded agent loops"]

        run_admission(
            raw_path="raw/zynd-ai-rewrite--raw--2026-06-10.md",
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            wiki_references=wiki_refs,
            route_llm_fn=mock_fn,
        )

        ledger_path = Path(tmp_ledger_path)
        assert ledger_path.exists()

        with open(ledger_path, encoding="utf-8") as fh:
            lines = [line.strip() for line in fh if line.strip()]

        assert len(lines) >= 1
        rejection = json.loads(lines[0])
        assert rejection["details"]["admission_result"] == "rejected"
        assert "novelty" in rejection["details"]["failed_checks"]


class TestLedgerNoDistillCalls:
    """验证负样本拒收后账本零提炼调用（master SC-2）。"""

    def test_no_distill_event_in_ledger_for_news(
        self, news_content, tmp_ledger_path, admission_fail_news_response
    ):
        """新闻型拒收后账本中无 distill 事件。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_fail_news_response)

        run_admission(
            raw_path="raw/news--raw--2026-06-10.md",
            raw_content=news_content,
            ledger_path=tmp_ledger_path,
            route_llm_fn=mock_fn,
        )

        ledger_path = Path(tmp_ledger_path)
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                assert event.get("event") != "distill_extract", (
                    "负样本拒收后不应出现 distill_extract 事件"
                )
                assert event.get("event") != "distill_compose", (
                    "负样本拒收后不应出现 distill_compose 事件"
                )
                assert event.get("node") != "distill_extract", (
                    "负样本拒收后不应出现 distill 节点"
                )

    def test_no_distill_event_in_ledger_for_plagiarism(
        self, sample_raw_content, tmp_ledger_path, admission_fail_plagiarism_response
    ):
        """洗稿型拒收后账本中无 distill 事件。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(admission_fail_plagiarism_response)

        run_admission(
            raw_path="raw/plagiarism--raw--2026-06-10.md",
            raw_content=sample_raw_content,
            ledger_path=tmp_ledger_path,
            wiki_references=["Loop Engineering"],
            route_llm_fn=mock_fn,
        )

        ledger_path = Path(tmp_ledger_path)
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                assert "distill" not in event.get("event", ""), (
                    "负样本拒收后不应出现 distill 事件"
                )
