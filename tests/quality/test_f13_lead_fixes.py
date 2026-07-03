"""test_f13_lead_fixes.py — F13 lead 直改四刀的行为绑定测试。

覆盖（lead 裁决,2026-07-03）:
    - F13-1 admission 强密度确定性短路（causal≥5 && steps≥5 → PASS 不落 LLM 仲裁;
      idx8 lilian 实证 causal=14/steps=8/comp=0 被 LLM 仲裁误拒的盲区）
    - F13-1b 阈值下不触发短路（仍走 LLM 仲裁,不放宽）+ 新闻 recap 信号远低于双阈值
    - F13-2 rubric 1.3.0 冷启动校准落地（图谱可调用性/新颖增量 note 带冷启动条款;
      concept 7 维名逐字不变——维度集不动,只动判据措辞）
    - F13-3 calibration 失败正样本有界重试（默认 0 零行为变化 / retry=1 救回记账 /
      重试仍败保留首因 + retry 痕迹 / 负样本与已过样本不重试）

断言绑可观测行为,不是"能跑通"。
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality.admission import (
    MECHANISM_CAUSAL_DENSE_MIN,
    MECHANISM_STEPS_DENSE_MIN,
    _check_mechanism_signal,
    _scan_mechanism_signals,
)
from src.akb2.quality.grader import _load_rubric_spec
from src.akb2.quality.llm_router import LLMResponse

# ---------------------------------------------------------------------------
# F13-1 admission 强密度短路
# ---------------------------------------------------------------------------

# 推理机制文形态:无命名组件(编号标题),但因果连接词与编号步双高(idx8 lilian 形态)
_DENSE_BODY = "\n".join(
    [
        "Reasoning at inference time leads to better answers because of test-time compute.",
        "1. Sampling multiple chains produces diverse candidates.",
        "2. Self-consistency voting results in robust selection.",
        "3. Verification enables error pruning and therefore higher accuracy.",
        "4. Reflection triggers revision when the verifier implies a fault.",
        "5. Budget control prevents runaway loops and produces bounded latency.",
        "6. Reranking causes the final answer to improve.",
    ]
)


def _llm_must_not_be_called(*_a: Any, **_k: Any) -> LLMResponse:
    raise AssertionError("强密度短路应确定性 PASS,不得调用 LLM 仲裁")


def _llm_says_no(*_a: Any, **_k: Any) -> LLMResponse:
    return LLMResponse(
        content='{"pass": false, "reason": "no mechanism"}', model_used="mock"
    )


def test_dense_causal_steps_deterministic_pass() -> None:
    """causal≥5 && steps≥5 → 确定性 PASS,LLM 不被调用(盲区短路)。"""
    sig = _scan_mechanism_signals(_DENSE_BODY)
    assert sig["causal"] >= MECHANISM_CAUSAL_DENSE_MIN
    assert sig["numbered_steps"] >= MECHANISM_STEPS_DENSE_MIN
    assert sig["components"] == 0  # 无命名组件——正是旧短路的盲区
    check = _check_mechanism_signal(_DENSE_BODY, _llm_must_not_be_called)
    assert check.passed is True
    assert "强密度短路" in check.reason


def test_dense_shortcut_below_threshold_falls_to_llm() -> None:
    """causal 达标但 steps 不足 → 不触发短路,仍走 LLM 仲裁(不放宽)。"""
    body = "\n".join(
        [
            "Caching leads to speedups because of locality and therefore lower cost.",
            "It results in fewer misses and enables prefetching, producing gains.",
            "1. Measure baseline.",
            "2. Enable cache.",
        ]
    )
    sig = _scan_mechanism_signals(body)
    assert sig["causal"] >= MECHANISM_CAUSAL_DENSE_MIN
    assert sig["numbered_steps"] < MECHANISM_STEPS_DENSE_MIN
    check = _check_mechanism_signal(body, _llm_says_no)
    assert check.passed is False  # LLM 仲裁被走到且判否 → 证明短路未触发


def test_news_recap_far_below_dense_thresholds() -> None:
    """负样本安全:新闻 recap 的 causal/steps 计数远低于双阈值,短路不可能误放。"""
    news = (
        "Google I/O 2026 wrapped up today. The company announced new products. "
        "Executives said the launch was exciting. Shares rose after the event. "
        "Attendees enjoyed the keynote and the demos."
    )
    sig = _scan_mechanism_signals(news)
    assert sig["causal"] < MECHANISM_CAUSAL_DENSE_MIN
    assert sig["numbered_steps"] < MECHANISM_STEPS_DENSE_MIN


# ---------------------------------------------------------------------------
# F13-2 rubric 1.3.0 冷启动校准
# ---------------------------------------------------------------------------


def test_rubric_130_cold_start_notes_and_dims_frozen() -> None:
    """rubric 1.3.0:两维 note 带冷启动条款;concept 7 维名逐字不变(维度集不动)。"""
    spec = _load_rubric_spec()
    assert str(spec.get("rubric_version")) == "1.3.0"
    concept = (spec.get("lanes") or {}).get("concept") or {}
    dims = {str(d["name"]): d for d in concept.get("dimensions", [])}
    # 维度集逐字冻结(只动 note 判据措辞,不动名与权重结构)
    assert sorted(dims) == sorted(
        [
            "概念机制清晰度(H3)",
            "实现机制说明(H2)",
            "图谱可调用性",
            "适用边界",
            "TLDR密度",
            "Future Questions质量",
            "新颖增量",
        ]
    )
    assert "冷启动校准" in str(dims["图谱可调用性"].get("note"))
    assert "resolve 未命中" in str(dims["图谱可调用性"].get("note"))
    assert "冷启动校准" in str(dims["新颖增量"].get("note"))
    assert "缺少基线" in str(dims["新颖增量"].get("note"))
    # 阈值与权重不动(标准冻结)
    assert concept.get("threshold") == 6.0
    assert dims["图谱可调用性"]["weight"] == 2.0
    assert dims["新颖增量"]["weight"] == 0.5


# ---------------------------------------------------------------------------
# F13-3 calibration 失败正样本有界重试
# ---------------------------------------------------------------------------


def _fake_entry_result(idx: int, passed: bool, expected: str) -> dict[str, Any]:
    return {
        "index": idx,
        "url": f"fixture:mock-{idx}",
        "expected": expected,
        "passed": passed,
        "final_status": "proposal_passed" if passed else "failed",
        "failure_class": None if passed else "unknown",
        "repair_rounds": 0,
    }


def _run_with_retry(monkeypatch: Any, tmp_path: Any, retry_n: int,
                    second_attempt_passes: bool) -> dict[str, Any]:
    from src.akb2.quality import calibration as cal

    calls: dict[int, int] = {}

    def fake_process(entry: dict, idx: int, _config: dict) -> dict[str, Any]:
        calls[idx] = calls.get(idx, 0) + 1
        expected = entry["expected"]
        if expected == "reject":
            r = _fake_entry_result(idx, False, expected)
            r["passed"] = True  # 负样本正确拒收计 passed
            r["final_status"] = "rejected_correctly"
            r["failure_class"] = None
            return r
        # 正样本:首轮必败;重试轮按参数
        passed = calls[idx] >= 2 and second_attempt_passes
        return _fake_entry_result(idx, passed, expected)

    def fake_load(_path: str) -> list[dict[str, Any]]:
        return [
            {"url": "fixture:mock-0", "expected": "ingest", "domain": "T"},
            {"url": "fixture:mock-1", "expected": "reject", "domain": "T"},
        ]

    def fake_stats(report: dict[str, Any]) -> dict[str, Any]:
        report.update(
            positive_pass_rate=0.0,
            negative_reject_rate=1.0,
            positive_live_total=0,
            positive_live_passed=0,
            positive_live_pass_rate=0.0,
            live_positive_intake_raw_failures=0,
            effective_live_positive_pass_floor=0.9,
            live_positive_pass_floor_reason="test",
        )
        return report

    monkeypatch.setattr(cal, "_process_corpus_entry", fake_process)
    monkeypatch.setattr(cal, "_load_corpus", fake_load)
    monkeypatch.setattr(cal, "_compute_statistics", fake_stats)
    report = cal.run_calibration(
        corpus_path="mock.yaml",
        report_out=str(tmp_path / "r.json"),
        config={
            "staging_dir": str(tmp_path),
            "ledger_path": str(tmp_path / "l.jsonl"),
            "retry_failed_positives": retry_n,
        },
        persist_raw=False,
    )
    report["_calls"] = calls
    return report


def test_retry_default_zero_no_behavior_change(monkeypatch: Any, tmp_path: Any) -> None:
    """默认 0:不重试、无 retry_policy 字段——旧行为逐字节一致。"""
    report = _run_with_retry(monkeypatch, tmp_path, retry_n=0, second_attempt_passes=True)
    assert report["_calls"] == {0: 1, 1: 1}
    assert "retry_policy" not in report


def test_retry_rescues_failed_positive_and_records(monkeypatch: Any, tmp_path: Any) -> None:
    """retry=1:失败正样本重跑一次救回;负样本不重试;记账 fail-visible。"""
    report = _run_with_retry(monkeypatch, tmp_path, retry_n=1, second_attempt_passes=True)
    assert report["_calls"][0] == 2  # 正样本重试一次
    assert report["_calls"][1] == 1  # 负样本(已 passed)不重试
    e0 = report["entries"][0]
    assert e0["passed"] is True
    assert e0["retry_attempted"] is True
    assert e0["first_attempt_failure_class"] == "unknown"
    assert report["retry_policy"] == {
        "retry_failed_positives": 1,
        "retried_attempts": 1,
        "rescued": 1,
    }


def test_retry_still_failing_keeps_first_attempt(monkeypatch: Any, tmp_path: Any) -> None:
    """重试仍败:保留首轮结果(首因归类稳定),记 retry 痕迹,rescued=0。"""
    report = _run_with_retry(monkeypatch, tmp_path, retry_n=1, second_attempt_passes=False)
    e0 = report["entries"][0]
    assert e0["passed"] is False
    assert e0["retry_attempted"] is True
    assert e0["retry_failure_class"] == "unknown"
    assert report["retry_policy"]["rescued"] == 0
