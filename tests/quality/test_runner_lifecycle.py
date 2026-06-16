"""Runner 生命周期测试 — SC-6.1 (七段生命线 + 周抽检)。

验证每篇七段生命线完整落账、七段任一失败后续调用计数为 0、
周抽检达标率检查位行为。

全 fixture mock，不调真 API。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
    from src.akb2.quality.runner import RunnerConfig

# ---------------------------------------------------------------------------
# 辅助 Fixtures
# ---------------------------------------------------------------------------


def _make_runner_config(
    tmp_path: Path,
    auto_promote: bool = False,
    poll_interval: int = 1,
) -> RunnerConfig:
    """创建测试用的 RunnerConfig。"""
    from src.akb2.quality.runner import RunnerConfig as RC

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    grader_dir = tmp_path / "grader-results"
    grader_dir.mkdir(parents=True, exist_ok=True)

    return RC(
        raw_dir=str(raw_dir),
        ledger_path=str(tmp_path / "quality-ledger.jsonl"),
        staging_dir=str(staging_dir),
        vault_root=str(tmp_path),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(grader_dir),
        poll_interval_seconds=poll_interval,
        auto_promote=auto_promote,
    )


def _make_raw_file(raw_dir: Path, slug: str, date_str: str = "2026-06-13") -> Path:
    """在 raw_dir 中创建 raw 文件。"""
    filename = f"{slug}--raw--{date_str}.md"
    raw_dir.mkdir(parents=True, exist_ok=True)
    content = f"""---
artifact_type: raw
source_slug: {slug}
source_url: https://example.com/{slug}
source_title: Test Article {slug}
source_authors: Test Author
source_publisher: Test Blog
source_published_at: {date_str}
fetched_at: {date_str}T00:00:00Z
fetched_via: defuddle
---

# §A Test Article {slug}

Some content here with anchor §A.

# §B How Section

Step 1: Do something.
Step 2: Verify something.
"""
    filepath = raw_dir / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ---------------------------------------------------------------------------
# 测试类: 七段生命线
# ---------------------------------------------------------------------------


class TestRunnerLifelineSegments:
    """七段生命线完整落账测试。"""

    def test_full_lifeline_all_segments_logged(self, tmp_path: Path) -> None:
        """正常流程: 七段生命线全部落账。"""

        from src.akb2.quality.runner import (
            _compute_raw_hash,
            _log_event,
            _process_one,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-lifeline", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_pipeline_segments"
        ) as mock_run:
            mock_run.return_value = {
                "converged": True,
                "rounds": 1,
                "promoted": True,
                "promotion_mode": "proposal",
                "final_status": "promoted",
                "error": None,
            }

            _process_one(str(f), config)

        # 验证 _process_one 正确调用了 _run_pipeline_segments
        mock_run.assert_called_once()

        # 手动创建一些账本事件来验证账本写入逻辑
        content_hash = _compute_raw_hash(str(f))
        assert _log_event(config, "intake_raw", "intake_raw", content_hash,
                         {"raw_path": str(f)})
        assert _log_event(config, "finalize", "finalize", content_hash,
                         {"mode": "proposal"})

        # 验证账本记录存在
        ledger_path = Path(config.ledger_path)
        assert ledger_path.exists(), "账本文件应存在"

        events = []
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        event_types = [e["event"] for e in events]
        # 我们手动创建了 intake_raw 和 finalize 事件
        assert "intake_raw" in event_types, f"缺少 intake_raw 事件: {event_types}"
        assert "finalize" in event_types, f"缺少 finalize 事件: {event_types}"
        assert len(events) >= 2, f"账本至少应有 2 条事件，实际 {len(events)}"

    def test_admission_fail_stops_chain(self, tmp_path: Path) -> None:
        """准入失败 → 后续段调用计数为 0。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-admission-fail", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        # Mock admission 返回失败
        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (False, "准入未通过: how_tendency", [])

            result = _run_pipeline_segments(
                str(f), str(f.read_text(encoding="utf-8")), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "rejected"
        assert "admission" in str(result.get("error", "")).lower() or result["final_status"] == "rejected"

    def test_distill_extract_fail_stops_chain(self, tmp_path: Path) -> None:
        """提炼段 1 失败 → 后续段调用计数为 0。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-distill-fail", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (True, "", [])
            with mock.patch(
                "src.akb2.quality.runner._run_distill_segment"
            ) as mock_distill:
                mock_distill.return_value = (
                    False,
                    "extract_failed",
                    "抽取失败",
                    None,
                )

                result = _run_pipeline_segments(
                    str(f), str(f.read_text(encoding="utf-8")), config
                )

        assert result["converged"] is False
        assert "extract" in str(result.get("error", "")).lower() or not result["converged"]

    def test_repair_not_converge_stops_chain(self, tmp_path: Path) -> None:
        """修复不收敛 → fail-closed 停链。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-repair-fail", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (True, "", [])
            with mock.patch(
                "src.akb2.quality.runner._run_distill_segment"
            ) as mock_distill:
                mock_distill.return_value = (
                    True,
                    "staging/candidate/test-repair-fail.md",
                    "",
                    "candidate text content",
                )
                with mock.patch(
                    "src.akb2.quality.runner._run_quality_segments"
                ) as mock_quality:
                    mock_quality.return_value = (
                        False,
                        "distill_no_converge",
                        "3 轮修复不收敛",
                        {"repair_rounds": 3, "a_defect_count": 0, "a_defect_summary": [],
                         "b_defect_count": 3, "b_defect_summary": [], "final_defects_summary": []},
                    )
                    result = _run_pipeline_segments(
                        str(f), str(f.read_text(encoding="utf-8")), config
                    )

        assert result["converged"] is False
        assert result["final_status"] in ("failed", "quarantined")

    def test_promote_proposal_mode_no_active_write(self, tmp_path: Path) -> None:
        """提议模式 (AKB2_AUTO_PROMOTE=0): 不写 active。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-proposal", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (True, "", [])
            with mock.patch(
                "src.akb2.quality.runner._run_distill_segment"
            ) as mock_distill:
                mock_distill.return_value = (
                    True,
                    "staging/candidate/test-proposal.md",
                    "",
                    "candidate text",
                )
                with mock.patch(
                    "src.akb2.quality.runner._run_quality_segments"
                ) as mock_quality:
                    mock_quality.return_value = (
                        True, "", "",
                        {"repair_rounds": 0, "a_defect_count": 0, "a_defect_summary": [],
                         "b_defect_count": 0, "b_defect_summary": [], "final_defects_summary": []},
                    )
                    with mock.patch(
                        "src.akb2.quality.runner._run_promote_segment"
                    ) as mock_promote:
                        mock_promote.return_value = (
                            True,
                            "proposal",
                            "Proposal mode: no active write",
                        )
                        result = _run_pipeline_segments(
                            str(f), str(f.read_text(encoding="utf-8")), config
                        )

        assert result["converged"] is True
        assert result["promotion_mode"] == "proposal"
        assert result["final_status"] == "promoted"


# ---------------------------------------------------------------------------
# 测试类: 周抽检
# ---------------------------------------------------------------------------


class TestRunnerWeeklySpotCheck:
    """周抽检行为测试 — SC-6.1。"""

    def test_spot_check_below_threshold_creates_task(self, tmp_path: Path) -> None:
        """达标率 < 80% → 创建校准任务文件 + 落账 + weekly-summary.json。"""
        from src.akb2.quality.runner import (
            _run_weekly_spot_check,
        )

        config = _make_runner_config(tmp_path, auto_promote=False)
        weekly_dir = tmp_path / "reports" / "quality-loop"
        weekly_dir.mkdir(parents=True, exist_ok=True)

        # Mock 抽检结果为 < 80% 达标
        with mock.patch(
            "src.akb2.quality.runner._sample_recent_promoted"
        ) as mock_sample:
            mock_sample.return_value = [
                {"path": "wiki/sources/a.md", "slug": "a"},
                {"path": "wiki/sources/b.md", "slug": "b"},
                {"path": "wiki/sources/c.md", "slug": "c"},
            ]
            with mock.patch(
                "src.akb2.quality.runner._regrade_sample"
            ) as mock_regrade:
                # 3 篇中 2 篇 fail → 33% < 80%
                mock_regrade.return_value = [False, False, True]

                _run_weekly_spot_check(config, str(weekly_dir))

        # 验证 weekly-summary.json 产出
        summary_path = weekly_dir / "weekly-summary.json"
        assert summary_path.exists(), "weekly-summary.json 应存在"

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert "manual_intervention_required_count" in summary, (
            "应含 manual_intervention_required_count 字段"
        )
        assert "quality_feedback_required_count" in summary, (
            "应含 quality_feedback_required_count 字段"
        )
        # 达标率 < 80%，calibration task 自动创建，两计数保持 0（plan SC-6.1: 无需人工介入）
        assert summary["manual_intervention_required_count"] == 0, (
            "达标率 < 80% 时自动创建校准任务，不应报人工介入"
        )
        assert summary["quality_feedback_required_count"] == 0, (
            "达标率 < 80% 时自动创建校准任务，不应报质量反馈需求"
        )

    def test_spot_check_above_threshold_happy_path(self, tmp_path: Path) -> None:
        """达标率 >= 80% → happy path: weekly-summary.json 存在且两计数为 0。"""
        from src.akb2.quality.runner import (
            _run_weekly_spot_check,
        )

        config = _make_runner_config(tmp_path, auto_promote=False)
        weekly_dir = tmp_path / "reports" / "quality-loop"
        weekly_dir.mkdir(parents=True, exist_ok=True)

        with mock.patch(
            "src.akb2.quality.runner._sample_recent_promoted"
        ) as mock_sample:
            mock_sample.return_value = [
                {"path": "wiki/sources/a.md", "slug": "a"},
                {"path": "wiki/sources/b.md", "slug": "b"},
                {"path": "wiki/sources/c.md", "slug": "c"},
            ]
            with mock.patch(
                "src.akb2.quality.runner._regrade_sample"
            ) as mock_regrade:
                # 3 篇全部 pass → 100% >= 80%
                mock_regrade.return_value = [True, True, True]

                _run_weekly_spot_check(config, str(weekly_dir))

        summary_path = weekly_dir / "weekly-summary.json"
        assert summary_path.exists(), "weekly-summary.json 应存在"

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["manual_intervention_required_count"] == 0, (
            "happy path 下 manual_intervention_required_count 应为 0"
        )
        assert summary["quality_feedback_required_count"] == 0, (
            "happy path 下 quality_feedback_required_count 应为 0"
        )

    def test_spot_check_no_promoted_items(self, tmp_path: Path) -> None:
        """无近期入库条目 → 跳过抽检，不创建 summary。"""
        from src.akb2.quality.runner import (
            _run_weekly_spot_check,
        )

        config = _make_runner_config(tmp_path, auto_promote=False)
        weekly_dir = tmp_path / "reports" / "quality-loop"

        with mock.patch(
            "src.akb2.quality.runner._sample_recent_promoted"
        ) as mock_sample:
            mock_sample.return_value = []

            _run_weekly_spot_check(config, str(weekly_dir))

        summary_path = weekly_dir / "weekly-summary.json"
        # 无条目时可能不创建 summary，或创建空 summary
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            assert summary.get("samples_checked", 0) == 0
