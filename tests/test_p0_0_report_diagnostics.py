"""P0-0 报告诊断字段直测。

runner 失败返回携机读 content_lane/failed_node/error_class，calibration 据此：
    - failure_segment 取真实 failed_node（非 final_status 粗映射 rejected→admission）；
    - grader 环境失败（grader_fallback_not_allowed/grader_all_down）单列 grader_unavailable
      / environment_blocked，与真实代码缺陷分桶；
    - 负样本 rejected_correctly 保留 underlying_* 底层拒因（不抹掉"靠哪道门拒"）；
    - content_lane 透传进 entry。

mock 边界：_run_pipeline_segments（runner 诊断 dict）+ _load_fixture_content（绕过真抓/真 fixture 文件）。
断言绑可观测行为，不是字段形状。
"""

from __future__ import annotations

from typing import Any


def _config(tmp_path) -> dict[str, Any]:
    return {
        "vault_root": "/tmp/akb2-p00-vault",
        "staging_dir": "/tmp/akb2-p00-staging",
        "raw_dir": str(tmp_path),
        "ledger_path": "/tmp/akb2-p00-ledger.jsonl",
        "failures_path": "",
        "grader_result_dir": "/tmp/akb2-p00-grader",
        "auto_promote": False,
        "fixtures_dir": "/tmp/akb2-p00-fixtures",
        "preflight_lookup": {},
    }


def _patch(monkeypatch, pipeline_ret: dict[str, Any]) -> None:
    from src.akb2.quality import calibration, runner

    monkeypatch.setattr(
        calibration,
        "_load_fixture_content",
        lambda name, config: "# Concept Title\n\n" + ("body text here. " * 80),
    )
    monkeypatch.setattr(
        runner,
        "_run_pipeline_segments",
        lambda raw_path, raw_content, config: pipeline_ret,
    )


def test_floor_failure_maps_to_integrity_floor_not_admission(monkeypatch, tmp_path):
    """floor reject 旧逻辑粗映射成 admission；P0-0 用真实 failed_node=integrity_floor。"""
    from src.akb2.quality import calibration

    _patch(monkeypatch, {
        "converged": False, "rounds": 0, "promoted": False, "promotion_mode": "none",
        "final_status": "rejected",
        "error": "完整性地板未过（不可补偿）: concept F4：机制组件 < 3",
        "content_lane": "concept", "failed_node": "integrity_floor",
        "error_class": "integrity_floor_failed",
    })
    entry = {"url": "fixture:x", "domain": "AI Agent", "expected": "ingest"}
    r = calibration._process_corpus_entry(entry, 0, _config(tmp_path))
    assert r["passed"] is False
    assert r["failure_segment"] == "integrity_floor"  # 不是 admission
    assert r["content_lane"] == "concept"
    assert r["error_class"] == "integrity_floor_failed"


def test_grader_unavailable_bucketed_as_environment(monkeypatch, tmp_path):
    """grader 环境不可用单列 grader_unavailable/environment_blocked，不混 distill_compose。"""
    from src.akb2.quality import calibration

    _patch(monkeypatch, {
        "converged": False, "rounds": 0, "promoted": False, "promotion_mode": "none",
        "final_status": "failed",
        "error": "质量不达标: grader_fallback_not_allowed — GPT-5.4 裁判不可用",
        "content_lane": "concept", "failed_node": "grader",
        "error_class": "grader_fallback_not_allowed",
    })
    entry = {"url": "fixture:x", "domain": "AI Agent", "expected": "ingest"}
    r = calibration._process_corpus_entry(entry, 0, _config(tmp_path))
    assert r["failure_segment"] == "grader_unavailable"
    assert r["failure_attribution"] == "environment_blocked"


def test_negative_rejected_keeps_underlying_cause(monkeypatch, tmp_path):
    """负样本 rejected_correctly 仍保留底层真实拒因（content_lane/failed_node/error_class）。"""
    from src.akb2.quality import calibration

    _patch(monkeypatch, {
        "converged": False, "rounds": 0, "promoted": False, "promotion_mode": "none",
        "final_status": "rejected",
        "error": "准入未通过: how_tendency",
        "content_lane": "concept", "failed_node": "admission",
        "error_class": "how_tendency",
    })
    entry = {"url": "fixture:x", "domain": "News", "expected": "reject"}
    r = calibration._process_corpus_entry(entry, 0, _config(tmp_path))
    assert r["passed"] is True  # 负样本拒收 = pass
    assert r["final_status"] == "rejected_correctly"
    assert r["underlying_error"] == "准入未通过: how_tendency"
    assert r["underlying_failed_node"] == "admission"
    assert r["underlying_error_class"] == "how_tendency"
