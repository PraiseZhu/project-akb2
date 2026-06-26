"""test_runner_lane_threading.py — step-1 content_lane 贯穿全链到 ledger。

覆盖 SC-6:
    - lane_visible_in_ledger: lane 贯穿全链——grader/repair/runner 入口可见，
      并向 ledger 写出（账本事件含 details.content_lane）。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.akb2.quality import grader, repair, runner
from src.akb2.quality.admission import (
    LANE_PROCEDURAL,
    AdmissionCheck,
    _log_rejection,
)


class TestLaneVisibleInLedger:
    def test_lane_visible_in_ledger(self, tmp_path: Path) -> None:
        """admission 拒收落账时 details 含 content_lane 键（lane 穿到 ledger）。"""
        ledger_path = tmp_path / "ledger.jsonl"
        checks = [
            AdmissionCheck(name="topic_relevance", passed=True, reason="ok"),
            AdmissionCheck(name="how_tendency", passed=True, reason="ok"),
            AdmissionCheck(name="novelty", passed=False, reason="洗稿，零增量"),
        ]
        _log_rejection(
            "raw/proc--raw--2026-06-26.md",
            checks,
            str(ledger_path),
            content_lane=LANE_PROCEDURAL,
        )
        lines = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert lines, "拒收应落账"
        details = lines[0]["details"]
        assert "content_lane" in details, "账本事件 details 必须含 content_lane 键"
        assert details["content_lane"] == LANE_PROCEDURAL

    def test_runner_admission_segment_returns_lane(self) -> None:
        """runner._run_admission_segment 返回 4 元组（末位 content_lane），lane 在 runner 入口可见。"""
        sig = inspect.signature(runner._run_admission_segment)
        assert "wiki_references" in sig.parameters, "admission 段应接 wiki_references"
        # 返回值携 lane 由 pipeline 容错解包消费；此处验形参与 distill 段穿线
        dsig = inspect.signature(runner._run_distill_segment)
        assert "content_lane" in dsig.parameters
        assert "known_concepts" in dsig.parameters
        assert "pending_queue" in dsig.parameters

    def test_grader_repair_quality_entrypoints_see_lane(self) -> None:
        """grader / repair / runner quality 段入口均接 content_lane 形参。"""
        assert "content_lane" in inspect.signature(grader.run_grader_detailed).parameters
        assert "content_lane" in inspect.signature(repair.run_repair_loop).parameters
        assert (
            "content_lane"
            in inspect.signature(runner._run_quality_segments).parameters
        )

    def test_pipeline_admission_event_carries_lane(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pipeline 准入通过事件向 ledger 写 content_lane（端到端穿线，mock 下游段）。"""
        config = runner.RunnerConfig(
            raw_dir=str(tmp_path / "raw"),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path / "vault"),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader"),
        )
        # loader 注入受控索引（first_run_no_index：空索引允许通过）
        monkeypatch.setattr(
            runner,
            "_load_vault_concept_index",
            lambda cfg, exclude_slug="": {
                "status": runner.LANE_INDEX_STATUS_FIRST_RUN,
                "wiki_references": [],
                "known_concepts": [],
                "pending_queue": [],
                "counts": {},
                "source_paths": [],
            },
        )
        # admission 返回 4 元组携 lane（procedural 通过）
        monkeypatch.setattr(
            runner,
            "_run_admission_segment",
            lambda *a, **k: (True, "", [], LANE_PROCEDURAL),
        )
        # 下游 distill 直接失败停链（聚焦验证 admission 事件已写 lane）
        monkeypatch.setattr(
            runner,
            "_run_distill_segment",
            lambda *a, **k: (False, "extract_failed: stop", "stop", None, {}),
        )
        monkeypatch.setattr(runner, "_existing_source_page_for_raw", lambda *a, **k: None)

        runner._run_pipeline_segments(
            "raw/proc--raw--2026-06-26.md", "RAW BODY", config
        )

        lines = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        admission_events = [
            e for e in lines if e["event"] == "admission" and e["details"].get("passed")
        ]
        assert admission_events, "应有准入通过事件"
        assert admission_events[0]["details"].get("content_lane") == LANE_PROCEDURAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
