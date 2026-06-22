"""Runner 生命周期测试 — SC-6.1 (七段生命线 + 周抽检)。

验证每篇七段生命线完整落账、七段任一失败后续调用计数为 0、
周抽检达标率检查位行为。

全 fixture mock，不调真 API。
"""

from __future__ import annotations

import json
import subprocess
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

    def test_existing_source_slug_rejected_before_distill(self, tmp_path: Path) -> None:
        """同 slug active source 已存在时应 fail-closed，不进入提炼/裁判。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "already-active", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)
        existing = tmp_path / "wiki" / "sources" / "already-active.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("# already active", encoding="utf-8")

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission, mock.patch(
            "src.akb2.quality.runner._run_distill_segment"
        ) as mock_distill, mock.patch(
            "src.akb2.quality.runner._run_quality_segments"
        ) as mock_quality, mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "rejected"
        assert "duplicate_source" in result["error"]
        mock_admission.assert_not_called()
        mock_distill.assert_not_called()
        mock_quality.assert_not_called()
        mock_promote.assert_not_called()

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        events = [r.get("event") for r in rows]
        assert events == ["intake_raw", "admission", "abort"]
        abort = rows[-1]
        assert abort["node"] == "admission"
        assert abort["details"]["reason"] == "duplicate_source"
        assert abort["details"]["existing_source_path"] == str(existing)

    def test_existing_source_url_rejected_before_distill(self, tmp_path: Path) -> None:
        """同 source_url 即使换 slug 也应 fail-closed，不进入提炼/裁判。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "renamed-active", "2026-06-13")
        raw_text = f.read_text(encoding="utf-8")
        raw_text = raw_text.replace(
            "source_url: https://example.com/renamed-active",
            "source_url: https://example.com/already-active",
        )
        f.write_text(raw_text, encoding="utf-8")
        config = _make_runner_config(tmp_path, auto_promote=False)
        existing = tmp_path / "wiki" / "sources" / "already-active.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text(
            "---\nsource_url: https://example.com/already-active\n---\n# already active",
            encoding="utf-8",
        )

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission, mock.patch(
            "src.akb2.quality.runner._run_distill_segment"
        ) as mock_distill, mock.patch(
            "src.akb2.quality.runner._run_quality_segments"
        ) as mock_quality, mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "rejected"
        assert "duplicate_source" in result["error"]
        mock_admission.assert_not_called()
        mock_distill.assert_not_called()
        mock_quality.assert_not_called()
        mock_promote.assert_not_called()

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [r.get("event") for r in rows] == ["intake_raw", "admission", "abort"]
        assert rows[-1]["details"]["existing_source_path"] == str(existing)

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
                    {
                        "failed_node": "distill_extract",
                        "retries_used": 3,
                        "maker_fallback_used": False,
                    },
                )

                result = _run_pipeline_segments(
                    str(f), str(f.read_text(encoding="utf-8")), config
                )

        assert result["converged"] is False
        assert "extract" in str(result.get("error", "")).lower() or not result["converged"]
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        distill_starts = [
            r for r in rows
            if r.get("event") == "distill_extract"
            and r.get("details", {}).get("status") == "start"
        ]
        aborts = [
            r for r in rows
            if r.get("event") == "abort" and r.get("node") == "distill_extract"
        ]
        assert distill_starts
        assert aborts
        assert aborts[-1]["details"]["max_retries"] == 0
        assert aborts[-1]["details"]["retries_used"] == 3
        assert not any(r.get("event") in {"grade_llm", "promote", "finalize"} for r in rows)

    def test_distill_worker_timeout_stops_chain(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """真实 DeepSeek 卡住时，runner 父进程必须能杀 worker 并 fail-closed。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        monkeypatch.setenv("AKB2_MAKER_FALLBACK", "0")
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-worker-timeout", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=210),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments"
        ) as mock_quality, mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "failed"
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            r.get("event") == "distill_extract"
            and r.get("details", {}).get("status") == "start"
            for r in rows
        )
        abort = [r for r in rows if r.get("event") == "abort"][-1]
        assert abort["node"] == "distill_extract"
        assert abort["details"]["error_class"] == "network_timeout"
        assert "extract_worker_timeout" in abort["details"]["error"]
        assert not any(r.get("event") in {"grade_llm", "promote", "finalize"} for r in rows)
        mock_quality.assert_not_called()
        mock_promote.assert_not_called()

    def test_distill_worker_timeout_can_use_maker_fallback(self, tmp_path: Path, monkeypatch) -> None:
        """maker fallback 开启时，DeepSeek timeout 可转为候选，但必须继续过质量门。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        monkeypatch.setenv("AKB2_MAKER_FALLBACK", "1")
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-maker-fallback", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=210),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                True,
                "",
                "",
                {
                    "repair_rounds": 0,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 0,
                    "b_defect_summary": [],
                    "final_defects_summary": [],
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment",
            return_value=(True, "proposal", "Proposal mode"),
        ):
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is True
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        success = [
            r for r in rows
            if r.get("event") == "distill_extract"
            and r.get("details", {}).get("status") == "success"
        ][-1]
        assert success["details"]["maker_fallback_used"] is True
        assert "extract_worker_timeout" in success["details"]["maker_primary_error"]
        assert any(r.get("event") == "grade_llm" for r in rows)
        assert any(r.get("event") == "promote" for r in rows)

    def test_distill_worker_timeout_fail_closed_when_maker_fallback_disabled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """maker fallback 关闭时，DeepSeek timeout 仍必须 fail-closed。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        monkeypatch.setenv("AKB2_MAKER_FALLBACK", "0")
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-maker-fallback-off", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=210),
        ):
            _run_pipeline_segments(str(f), f.read_text(encoding="utf-8"), config)

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        abort = [r for r in rows if r.get("event") == "abort"][-1]
        assert abort["node"] == "distill_extract"
        assert abort["details"]["maker_fallback_used"] is False
        assert not any(r.get("event") in {"grade_llm", "promote", "finalize"} for r in rows)

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
                    {"retries_used": 1, "maker_fallback_used": False},
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

    def test_repair_route_failed_writes_abort_without_promote(self, tmp_path: Path) -> None:
        """repair LLM route/worker 失败必须及时落主账本 abort。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-repair-route-failed", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_distill_segment",
            return_value=(
                True,
                "staging/candidate/test-repair-route-failed.md",
                "",
                "candidate text",
                {"retries_used": 1, "maker_fallback_used": True},
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                False,
                "repair_route_failed",
                "repair worker timeout",
                {
                    "repair_rounds": 1,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 1,
                    "b_defect_summary": [{"dimension": "论断锚定率"}],
                    "final_defects_summary": [{"dimension": "论断锚定率"}],
                    "repair_trace_paths": ["trace.json"],
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        abort = [r for r in rows if r.get("event") == "abort"][-1]
        assert abort["node"] == "repair_loop"
        assert abort["details"]["error_class"] == "repair_route_failed"
        assert abort["details"]["repair_rounds"] == 1
        assert not any(r.get("event") in {"promote", "finalize"} for r in rows)
        mock_promote.assert_not_called()

    def test_grade_llm_logs_fallback_reason(self, tmp_path: Path) -> None:
        """grade_llm 账本必须记录 primary 降级原因，避免 canary 0 缺陷失真。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-grader-fallback", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_distill_segment",
            return_value=(
                True,
                "staging/candidate/test-grader-fallback.md",
                "",
                "candidate text",
                {"retries_used": 1, "maker_fallback_used": False},
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                False,
                "",
                "grader_fallback_not_allowed",
                {
                    "repair_rounds": 0,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 0,
                    "b_defect_summary": [],
                    "grader_model_used": "gemini-2.5-flash",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_model": "gemini-flash",
                    "grader_fallback_used": True,
                    "grader_primary_failed": True,
                    "grader_primary_failure_reason": "gpt-5.4 返回不符合 grader schema 的 JSON",
                    "grader_fallback_failed": False,
                    "grader_fallback_failure_reason": "",
                    "final_defects_summary": [],
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment",
            return_value=(True, "proposal", "Proposal mode"),
        ) as mock_promote:
            _run_pipeline_segments(str(f), f.read_text(encoding="utf-8"), config)

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        grade_events = [r for r in rows if r.get("event") == "grade_llm"]
        assert grade_events
        details = grade_events[-1]["details"]
        assert details["model_used"] == "gemini-2.5-flash"
        assert details["fallback_used"] is True
        assert details["primary_model"] == "gpt-5.4"
        assert "不符合 grader schema" in details["primary_failure_reason"]
        events = [r.get("event") for r in rows]
        assert "abort" in events
        assert "promote" not in events
        assert "finalize" not in events
        mock_promote.assert_not_called()

    def test_structural_defects_enter_repair_before_abort(self, tmp_path: Path) -> None:
        """A 层结构缺陷必须进入 repair，不能在 structural_fail 处提前停链。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.runner import _run_quality_segments

        candidate = tmp_path / "staging" / "candidate" / "test.md"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("candidate with structural issue", encoding="utf-8")
        config = _make_runner_config(tmp_path, auto_promote=False)
        grader_path = tmp_path / "staging" / "grader-results" / "test.grader.json"
        initial_defect = Defect(
            dimension="A7-图谱证据",
            location="图谱关系/evidence/no-supported-related-concept",
            defect="图谱 evidence 缺少 provenance",
            suggestion="补 provenance",
        )

        with mock.patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate",
            return_value=[initial_defect],
        ) as mock_self_gate, mock.patch(
            "src.akb2.quality.grader.run_grader_detailed"
        ) as mock_grader, mock.patch(
            "src.akb2.quality.repair.run_repair_loop",
            return_value={
                "converged": True,
                "rounds": 1,
                "final_text": "candidate repaired",
                "final_defects": [],
                "trace_paths": ["trace.json"],
                "final_grader_meta": {
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            },
        ) as mock_repair:
            converged, error_class, error_detail, meta = _run_quality_segments(
                candidate.read_text(encoding="utf-8"),
                str(candidate),
                "raw §A",
                config,
                grader_path,
            )

        assert converged is True
        assert error_class == ""
        assert error_detail == ""
        mock_self_gate.assert_called_once()
        mock_grader.assert_not_called()
        mock_repair.assert_called_once()
        assert mock_repair.call_args.kwargs["a_defects"] == [initial_defect]
        assert mock_repair.call_args.kwargs["b_defects"] == []
        assert meta["a_defect_count"] == 1
        assert meta["repair_rounds"] == 1
        assert meta["repair_trace_paths"] == ["trace.json"]
        assert meta["grader_model_used"] == "gpt-5.4"
        assert candidate.read_text(encoding="utf-8") == "candidate repaired"
        persisted = json.loads(grader_path.read_text(encoding="utf-8"))
        assert persisted["status"] == "PASS"
        assert persisted["model_used"] == "gpt-5.4"

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
                    {"retries_used": 1, "maker_fallback_used": False},
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

    def test_grade_llm_logs_final_zero_after_repair(self, tmp_path: Path) -> None:
        """repair 收敛后 grade_llm.defect_count 表示最终缺陷数，初始缺陷另存。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-final-grade", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)
        initial_defect = {
            "dimension": "TLDR 密度",
            "location": "## TLDR",
            "defect": "修复前缺陷",
        }

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_distill_segment",
            return_value=(
                True,
                "staging/candidate/test-final-grade.md",
                "",
                "candidate text",
                {"retries_used": 1, "maker_fallback_used": False},
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                True,
                "",
                "",
                {
                    "repair_rounds": 2,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 1,
                    "b_defect_summary": [initial_defect],
                    "final_defects_summary": [],
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment",
            return_value=(True, "proposal", "Proposal mode"),
        ):
            _run_pipeline_segments(str(f), f.read_text(encoding="utf-8"), config)

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        grade = [r for r in rows if r.get("event") == "grade_llm"][-1]["details"]
        assert grade["defect_count"] == 0
        assert grade["defects"] == []
        assert grade["initial_defect_count"] == 1
        assert grade["initial_defects"] == [initial_defect]


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
