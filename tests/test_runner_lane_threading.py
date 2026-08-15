"""test_runner_lane_threading.py — content_lane 贯穿全链到 ledger（intake-rebuild p5 契约）。

覆盖 SC-6 + sc-p5-add-pattern-design:
    - lane_visible_in_ledger: lane 贯穿全链——grader/repair/runner 入口可见，
      并向 ledger 写出（账本事件含 details.content_lane）。
    - p5: admission 输出侧的 tutorial canonical（procedural 别名已归一）流经
      runner 侧 payload/账本；pattern/design 三车道在 runner 入口的 attempt
      预算与穿线不回归。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.akb2.quality import grader, repair, runner
from src.akb2.quality.admission import (
    LANE_CONCEPT,
    LANE_DESIGN,
    LANE_PATTERN,
    LANE_TUTORIAL,
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
            content_lane=LANE_TUTORIAL,
        )
        lines = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert lines, "拒收应落账"
        details = lines[0]["details"]
        assert "content_lane" in details, "账本事件 details 必须含 content_lane 键"
        assert details["content_lane"] == LANE_TUTORIAL

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

    @pytest.mark.parametrize(
        "lane", [LANE_CONCEPT, LANE_TUTORIAL, LANE_PATTERN, LANE_DESIGN]
    )
    def test_pipeline_admission_event_carries_lane(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane: str
    ) -> None:
        """pipeline 准入通过事件向 ledger 写 content_lane（四车道端到端穿线，mock 下游段）。"""
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
        # admission 返回 4 元组携 lane（该车道通过）
        monkeypatch.setattr(
            runner,
            "_run_admission_segment",
            lambda *a, **k: (True, "", [], lane),
        )
        # 下游 distill 直接失败停链（聚焦验证 admission 事件已写 lane）
        monkeypatch.setattr(
            runner,
            "_run_distill_segment",
            lambda *a, **k: (False, "extract_failed: stop", "stop", None, {}),
        )
        monkeypatch.setattr(runner, "_existing_source_page_for_raw", lambda *a, **k: None)

        runner._run_pipeline_segments(
            f"raw/{lane}--raw--2026-06-26.md", "RAW BODY", config
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
        assert admission_events[0]["details"].get("content_lane") == lane

    def test_pipeline_extract_max_attempts_per_lane(self) -> None:
        """p5：attempt 预算按车道——concept=4（F32 质量重试 +1），tutorial/pattern/design=3。

        admission 输出侧 procedural 已归一 tutorial；runner._extract_max_attempts
        对三新车道与旧操作型同预算（无 F32），账本 retries_used ≤ max 自洽。
        """
        assert runner._extract_max_attempts(LANE_CONCEPT) == 4
        assert runner._extract_max_attempts(LANE_TUTORIAL) == 3
        assert runner._extract_max_attempts(LANE_PATTERN) == 3
        assert runner._extract_max_attempts(LANE_DESIGN) == 3

    def test_worker_payload_carries_tutorial_canonical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """p5：runner worker payload 携 admission canonical lane（tutorial）穿子进程。"""
        captured: dict = {}

        class _FakeProc:
            returncode = 0
            pid = 99999

            def communicate(self, input=None, timeout=None):
                captured["input"] = input
                return (
                    json.dumps(
                        {"success": True, "data": {}, "retries_used": 0, "error": ""}
                    ),
                    "",
                )

            def poll(self):
                return 0

        def _fake_popen(cmd, **kwargs):
            return _FakeProc()

        monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen)
        runner._run_extract_claims_worker("RAW", content_lane=LANE_TUTORIAL)
        payload = json.loads(captured["input"])
        assert payload["content_lane"] == LANE_TUTORIAL


class TestVaultConceptIndexLoader:
    """SC-2b-b1：loader 把 wiki/sources/*.md slug 折进 known_concepts。"""

    def test_loader_folds_source_slugs_into_known_concepts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """源 wikilink（指向 wiki/sources/ 已存在页）不应判死链。

        满仓观测：[[anthropic-effective-context-engineering]] 在 wiki/sources/ 存在，
        但旧 loader 只 glob wiki/concepts/，源 slug 永不进 known_concepts → 误判死链
        （生产也中招）。resolver docstring 已明示 known_concepts=概念或源。
        """
        from src.akb2.loops import paths as paths_mod
        from src.akb2.quality import runner

        vault = tmp_path / "vault"
        (vault / "wiki" / "concepts").mkdir(parents=True)
        (vault / "wiki" / "sources").mkdir(parents=True)
        (vault / "staging" / "pending").mkdir(parents=True)
        (vault / "wiki" / "concepts" / "concept-context-engineering.md").write_text(
            "# c\n", encoding="utf-8"
        )
        (vault / "wiki" / "sources" / "anthropic-effective-context-engineering.md").write_text(
            "# s\n", encoding="utf-8"
        )

        monkeypatch.setattr(paths_mod, "get_akb2_root", lambda: vault)
        monkeypatch.setattr(paths_mod, "get_staging_root", lambda: vault / "staging")

        config = runner.RunnerConfig(
            raw_dir=str(tmp_path / "raw"),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            staging_dir=str(vault / "staging"),
            vault_root=str(vault),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader"),
        )
        idx = runner._load_vault_concept_index(config)

        assert "concept-context-engineering" in idx["known_concepts"]
        assert "anthropic-effective-context-engineering" in idx["known_concepts"], (
            "loader 须把 wiki/sources/*.md slug 折进 known_concepts（SC-2b-b1）"
        )
        assert idx["status"] == runner.LANE_INDEX_STATUS_OK


class TestAdmissionReasonNoDoublePrefix:
    """SC-6：admission 失败的 pipeline error 不重复拼"准入未通过:"前缀。"""

    def test_admission_failure_error_no_double_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """admission reason 已带前缀 → pipeline error 幂等不双前缀。

        满仓观测：factory.json failure_reason = "准入未通过: 准入未通过: how_tendency"，
        runner 把 admission 已带前缀的 reason 又包了一层。
        """
        from src.akb2.quality import runner

        config = runner.RunnerConfig(
            raw_dir=str(tmp_path / "raw"),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            staging_dir=str(tmp_path / "staging"),
            vault_root=str(tmp_path / "vault"),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader"),
        )
        monkeypatch.setattr(runner, "_load_vault_concept_index", lambda cfg, exclude_slug="": {
            "status": runner.LANE_INDEX_STATUS_FIRST_RUN,
            "wiki_references": [],
            "known_concepts": [],
            "pending_queue": [],
            "counts": {},
            "source_paths": [],
        })
        monkeypatch.setattr(runner, "_existing_source_page_for_raw", lambda *a, **k: None)
        monkeypatch.setattr(
            runner,
            "_run_admission_segment",
            lambda *a, **k: (False, "准入未通过: how_tendency", [], ""),
        )
        result = runner._run_pipeline_segments(
            "raw/concept--raw--2026-06-27.md", "RAW BODY", config
        )
        assert result["final_status"] == "rejected"
        assert result["error"] == "准入未通过: how_tendency", (
            "error 不应重复'准入未通过:'前缀（SC-6）"
        )
        assert "准入未通过: 准入未通过" not in result["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
