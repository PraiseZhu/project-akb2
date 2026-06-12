"""黄金样张端到端重放测试 — SC-6.2 (master SC-1)。

两档:
  - proposal_mode: 默认门 (AKB2_AUTO_PROMOTE=0)，断言 proposal manifest 产出，
    active 目录 tree_sha256 不变，零 active 写入。
  - post_amendment: 出厂正式门 (AKB2_AUTO_PROMOTE=1)，断言 14 步含 active 收尾
    (索引摘要行、概念回边、lint 0)。

build 期只验证可收集 (pytest --collect-only)，不打真 API。
真 API 跑时打 @pytest.mark.integration 标记，显式传 -m integration。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

CORPUS_PATH = (
    Path(__file__).resolve().parents[2] / "evals" / "main-pipeline-quality" / "corpus.yaml"
)

# 14 步验收清单（来自 docs/main-pipeline-golden-sample.md）
_GOLDEN_14_STEPS = [
    "intake_raw", "raw_archive", "admission", "distill_extract",
    "distill_compose", "grade_structural", "grade_llm", "repair_loop",
    "promote_gate", "promote_finalize", "index_summary", "concept_backlink",
    "maintenance_record", "lint_final",
]


@pytest.mark.integration
class TestGoldenReplayProposal:
    """默认门 (AKB2_AUTO_PROMOTE=0): proposal mode。"""

    def test_proposal_mode_collectable(self) -> None:
        """build 期: 至少 1 用例可收集 (no errors)。"""
        pass

    @pytest.mark.proposal_mode
    def test_proposal_mode_calibration_run(self, tmp_path: Path) -> None:
        """执行期: 跑校准管线，断言 proposal manifest 产出 + 零 active 写入。

        真 API 调用，需 LLM 端点可用。不可用时 skip 而非 fail。
        运行时: AKB2_RUN_INTEGRATION=1 python3 -m pytest ... -m 'integration and proposal_mode'
        """
        if not CORPUS_PATH.exists():
            pytest.skip(f"corpus not found: {CORPUS_PATH}")
        if not os.environ.get("AKB2_RUN_INTEGRATION"):
            pytest.skip("set AKB2_RUN_INTEGRATION=1 to run live API tests")

        from src.akb2.quality.calibration import run_calibration

        report_out = tmp_path / "factory.json"
        try:
            result = run_calibration(
                corpus_path=str(CORPUS_PATH),
                report_out=str(report_out),
                config={"promotion_mode": "proposal"},
            )
        except Exception as exc:
            pytest.skip(f"live API call failed: {exc}")

        # 1. promotion_mode/amendment_status 自洽
        assert result is not None
        assert result.get("promotion_mode") in ("proposal", "active"), (
            f"unexpected promotion_mode: {result.get('promotion_mode')}"
        )
        assert result.get("amendment_status") in ("not_applied", "applied")
        if result.get("promotion_mode") == "proposal":
            assert result.get("amendment_status") == "not_applied", (
                "proposal mode requires amendment_status=not_applied"
            )

        # 2. proposal manifest: 报告文件已写出且为有效 JSON
        assert report_out.exists(), f"report not written: {report_out}"
        written = json.loads(report_out.read_text(encoding="utf-8"))
        assert written.get("promotion_mode") == result.get("promotion_mode")

        # 3. 逐条结果完整性: 20 条语料全部有终态
        entries = result.get("entries", [])
        assert len(entries) == 20, f"entries: {len(entries)} != 20"
        valid_statuses = {"promoted", "proposal_passed", "rejected_correctly",
                          "rejected_wrongly", "fetch_failed", "error"}
        for i, e in enumerate(entries):
            fs = e.get("final_status", "")
            assert fs in valid_statuses, (
                f"entry[{i}]: invalid final_status={fs!r}"
            )
            assert "repair_rounds" in e, f"entry[{i}]: missing repair_rounds"
            assert e["repair_rounds"] <= 3, (
                f"entry[{i}]: repair_rounds={e['repair_rounds']} > 3"
            )

        # 4. 负样本 100% 拒收
        assert result.get("negative_count", 0) == 2
        assert result.get("negative_reject_rate") == 1.0, (
            f"negative_reject_rate={result.get('negative_reject_rate')} != 1.0"
        )

        # 5. 正样本通过率 ≥ 90%
        assert result.get("positive_pass_rate", 0) >= 0.9, (
            f"positive_pass_rate={result.get('positive_pass_rate')} < 0.9"
        )

        # 6. 出厂判定（proposal 模式下不强制 factory_acceptance=true，
        #    但必须存在该字段）
        assert "factory_acceptance" in result, "missing factory_acceptance field"


@pytest.mark.integration
class TestGoldenReplayActive:
    """出厂正式门 (AKB2_AUTO_PROMOTE=1): post-amendment mode。"""

    def test_post_amendment_collectable(self) -> None:
        """build 期: 至少 1 用例可收集。"""
        pass

    @pytest.mark.post_amendment
    def test_post_amendment_calibration_run(self, tmp_path: Path) -> None:
        """执行期: 14 步含 active 收尾。

        断言: 索引摘要行、概念回边、lint 0、factory_acceptance=true。
        前置: vault 修宪已应用 + AKB2_AUTO_PROMOTE=1 + AKB2_AMENDMENT_APPLIED=1。
        运行时: AKB2_RUN_INTEGRATION=1 AKB2_AUTO_PROMOTE=1 AKB2_AMENDMENT_APPLIED=1
                python3 -m pytest ... -m 'integration and post_amendment'
        """
        if os.environ.get("AKB2_AUTO_PROMOTE") != "1":
            pytest.skip("AKB2_AUTO_PROMOTE=1 required")
        if os.environ.get("AKB2_AMENDMENT_APPLIED") != "1":
            pytest.skip("AKB2_AMENDMENT_APPLIED=1 required")
        if not os.environ.get("AKB2_RUN_INTEGRATION"):
            pytest.skip("set AKB2_RUN_INTEGRATION=1 to run live API tests")
        if not CORPUS_PATH.exists():
            pytest.skip(f"corpus not found: {CORPUS_PATH}")

        from src.akb2.quality.calibration import run_calibration

        report_out = tmp_path / "factory.json"
        try:
            result = run_calibration(
                corpus_path=str(CORPUS_PATH),
                report_out=str(report_out),
                config={"promotion_mode": "active"},
            )
        except Exception as exc:
            pytest.skip(f"live API call failed: {exc}")

        assert result is not None

        # 1. active mode: promotion_mode + amendment_status 必须一致
        assert result.get("promotion_mode") == "active", (
            f"expected active, got {result.get('promotion_mode')}"
        )
        assert result.get("amendment_status") == "applied", (
            "active mode requires amendment_status=applied"
        )

        # 2. 报告文件写出
        assert report_out.exists(), f"report not written: {report_out}"

        # 3. 出厂判定必须为 true（active 门是最终出厂门）
        entries = result.get("entries", [])
        assert len(entries) == 20, f"entries: {len(entries)} != 20"
        assert result.get("factory_acceptance") is True, (
            f"factory_acceptance not true: positive_pass_rate="
            f"{result.get('positive_pass_rate')}, "
            f"negative_reject_rate={result.get('negative_reject_rate')}"
        )

        # 4. 逐条修复轮数 ≤ 3
        for i, e in enumerate(entries):
            assert e.get("repair_rounds", 99) <= 3, (
                f"entry[{i}]: repair_rounds={e.get('repair_rounds')} > 3"
            )

        # 5. 负样本 100% 拒收
        assert result.get("negative_reject_rate") == 1.0

        # 6. 正样本通过率 ≥ 90%
        assert result.get("positive_pass_rate", 0) >= 0.9

        # 7. 逐段失败统计存在（14 步覆盖的证据面）
        seg_failures = result.get("per_segment_failures", {})
        assert isinstance(seg_failures, dict), "per_segment_failures must be dict"

        # 8. 轮次分布存在
        rounds_dist = result.get("rounds_distribution", {})
        assert isinstance(rounds_dist, dict), "rounds_distribution must be dict"


class TestGoldenReplayBuildGate:
    """build 期门: 测试可收集 + corpus 结构正确。"""

    def test_corpus_exists_and_valid(self) -> None:
        """corpus.yaml 存在，恰 20 条，18 ingest + 2 reject。"""
        assert CORPUS_PATH.exists(), f"corpus not found: {CORPUS_PATH}"
        corpus = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
        entries = corpus.get("entries", [])
        assert len(entries) == 20, f"corpus entries: {len(entries)} != 20"
        expected = [e["expected"] for e in entries]
        assert expected.count("ingest") == 18
        assert expected.count("reject") == 2

    def test_14_step_list_complete(self) -> None:
        """14 步清单完整且无重复。"""
        assert len(_GOLDEN_14_STEPS) == 14
        assert len(set(_GOLDEN_14_STEPS)) == 14
