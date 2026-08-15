"""test_f13_lead_fixes.py — F13 lead 直改四刀的行为绑定测试。

覆盖（lead 裁决,2026-07-03;F13-fix2 采纳 reviewer 两轮 blocking 定论）:
    - F13-1（fix2 版）:**无密度类确定性 PASS 短路**——密度信号降级为仲裁证据
      （信号计数注入 mechanism 仲裁 prompt,由 LLM 判）;D11 reject-recheck 降级样本
      force_llm_adjudication 禁用全部确定性 PASS（撤销 reject ≠ 确定性准入）
    - reviewer 两个反例回归:营销 numbered-benefits（词面双高）/ 分节营销文
      （3 普通 H2 + benefits）/ `## 1.` 编号标题营销文（会被计成命名组件）
      → 全部保留 mechanism LLM 否决权,整体拒收
    - F13-2 rubric 1.3.0 冷启动校准落地（图谱可调用性/新颖增量 note 带冷启动条款;
      concept 7 维名逐字不变——维度集不动,只动判据措辞）
    - F13-3 calibration 失败正样本有界重试（默认 0 零行为变化 / retry=1 救回记账 /
      重试仍败保留首因 + retry 痕迹 / 负样本与已过样本不重试）

断言绑可观测行为,不是"能跑通"。
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality.admission import (
    _check_mechanism_signal,
    _scan_mechanism_signals,
    run_admission,
)
from src.akb2.quality.grader import _load_rubric_spec
from src.akb2.quality.llm_router import LLMResponse

# ---------------------------------------------------------------------------
# F13-1 admission 强密度短路
# ---------------------------------------------------------------------------

# 推理机制文形态:无命名组件(编号标题),但因果连接词/编号步双高 + 小节标题结构
# (idx8 lilian 形态: causal=14/steps=8/weak_comp=13/comp=0)
_DENSE_BODY = "\n".join(
    [
        "## Test-Time Compute",
        "Reasoning at inference time leads to better answers because of test-time compute.",
        "1. Sampling multiple chains produces diverse candidates.",
        "2. Self-consistency voting results in robust selection.",
        "## Verification",
        "3. Verification enables error pruning and therefore higher accuracy.",
        "4. Reflection triggers revision when the verifier implies a fault.",
        "## Budget Control",
        "5. Budget control prevents runaway loops and produces bounded latency.",
        "6. Reranking causes the final answer to improve.",
    ]
)

# 营销 numbered-benefits 形态(reviewer blocking P1 反例):词面 causal/steps 双高,
# 但零结构标题、零机制——短路不得放行
_MARKETING_BODY = "\n".join(
    [
        "Our new product launch results in huge gains and enables your team to win.",
        "1. Faster onboarding leads to happier customers and therefore more revenue.",
        "2. Smart sync enables seamless flows and produces instant results.",
        "3. One-click setup prevents errors because of our magic engine.",
        "4. Premium support triggers faster resolution and results in loyalty.",
        "5. New pricing enables savings and therefore produces value.",
    ]
)


# 分节营销文(reviewer 第二轮反例):3 个普通 H2 + numbered benefits——曾骗过 weak_comp≥3 护栏
_MARKETING_SECTIONED = "\n".join(
    [
        "## Faster onboarding",
        "Our launch results in huge gains and enables your team to win faster.",
        "1. Faster onboarding leads to happier customers and therefore more revenue.",
        "2. Smart sync enables seamless flows and produces instant results.",
        "## Lower pilot cost",
        "3. One-click setup prevents errors because of our magic engine.",
        "4. Premium support triggers faster resolution and results in loyalty.",
        "## Ecosystem reach",
        "5. New pricing enables savings and therefore produces value.",
    ]
)

# `## 1. X` 编号标题营销文(reviewer 第二轮反例):编号 H2 被 _MECHANISM_COMPONENT_RE
# 计成"命名组件"——D11 recheck 后若不禁全部确定性 PASS,comp>=3 分支直接放行
_MARKETING_NUMBERED_HEADINGS = "\n".join(
    [
        "## 1. Faster onboarding",
        "Our launch results in huge gains and enables your team to win faster.",
        "It leads to happier customers and therefore more revenue this quarter.",
        "## 2. Smart sync",
        "Smart sync enables seamless flows and produces instant results because of AI.",
        "It triggers faster resolution and results in loyalty and enables savings.",
        "## 3. Premium support",
        "Premium support prevents churn and produces value and causes growth.",
    ]
)


def _llm_says_no(*_a: Any, **_k: Any) -> LLMResponse:
    return LLMResponse(
        content='{"pass": false, "reason": "no mechanism"}', model_used="mock"
    )


def _make_capturing_no(captured: list) -> Any:
    def _fn(_model: Any, messages: list, **_k: Any) -> LLMResponse:
        captured.append(messages)
        return LLMResponse(
            content='{"pass": false, "reason": "no mechanism"}', model_used="mock"
        )

    return _fn


def test_dense_body_goes_to_llm_with_signal_context() -> None:
    """[fix2/fix4] 密度不再是通行证:lilian 形态(causal/steps/标题三高)也走 LLM 仲裁,
    且仲裁 prompt 带信号计数 + 文本证据样本(标题原文/因果句,fix4 证据升级)。"""
    sig = _scan_mechanism_signals(_DENSE_BODY)
    assert sig["components"] == 0 and sig["causal"] >= 5 and sig["numbered_steps"] >= 5
    captured: list = []
    check = _check_mechanism_signal(_DENSE_BODY, _make_capturing_no(captured))
    assert check.passed is False  # LLM 否决生效 = 确实走了仲裁
    assert captured, "必须调用 LLM 仲裁"
    user_msg = str(captured[0][-1]["content"])
    assert "确定性信号计数" in user_msg
    assert f"因果连接词={sig['causal']}" in user_msg
    # fix4:文本证据——标题原文与因果句样本进 prompt(LLM 据名称分辨机制 vs 营销)
    assert "小节标题原文" in user_msg
    assert "Test-Time Compute" in user_msg
    assert "因果句样本" in user_msg


def test_marketing_numbered_benefits_llm_veto() -> None:
    """[reviewer 反例1] 营销编号清单(词面双高):走 LLM 仲裁且可被否决,无确定性放行。"""
    check = _check_mechanism_signal(_MARKETING_BODY, _llm_says_no)
    assert check.passed is False
    assert not check.reason.startswith("确定性机制信号")


def test_marketing_sectioned_llm_veto() -> None:
    """[reviewer 反例2] 分节营销文(3 普通 H2 + benefits):同样无确定性放行。"""
    sig = _scan_mechanism_signals(_MARKETING_SECTIONED)
    assert sig["weak_components"] >= 3  # 曾骗过旧 weak_comp 护栏的形态
    check = _check_mechanism_signal(_MARKETING_SECTIONED, _llm_says_no)
    assert check.passed is False
    assert not check.reason.startswith("确定性机制信号")


def test_numbered_heading_marketing_no_deterministic_pass() -> None:
    """[fix3 核心] mechanism 门无任何确定性 PASS:`## 1. X` 编号标题营销文
    (被计成 3 命名组件)也必须走 LLM 仲裁且可被否决——非 recheck 路径同样成立。"""
    sig = _scan_mechanism_signals(_MARKETING_NUMBERED_HEADINGS)
    assert sig["components"] >= 3  # reviewer 反例:编号 H2 被计成命名组件
    captured: list = []
    check = _check_mechanism_signal(
        _MARKETING_NUMBERED_HEADINGS, _make_capturing_no(captured)
    )
    assert captured, "无确定性 PASS——必须调用 LLM 仲裁"
    assert check.passed is False
    assert not check.reason.startswith("确定性机制信号")


def test_run_admission_numbered_heading_lane_concept_still_vetoed(
    tmp_path: Any,
) -> None:
    """[reviewer 三轮验收·全路径] `## 1/2/3` 编号标题营销文 + mock lane=concept
    (非 recheck 路径,lane LLM 方差误判):mechanism **必须被调用**且否决 →
    整体 rejected,reason 非确定性。"""
    calls: list = []

    def mock_llm(_model: Any, messages: list, **kwargs: Any) -> LLMResponse:
        schema = kwargs.get("response_schema") or {}
        if "content_lane" in (schema.get("properties") or {}):
            calls.append("lane")
            return LLMResponse(
                content='{"content_lane": "concept", "reason": "误判"}',
                model_used="mock",
            )
        system = str(messages[0].get("content", "")) if messages else ""
        if "机制" in system:
            calls.append("mechanism")
            return LLMResponse(
                content='{"pass": false, "reason": "无可复用机制"}', model_used="mock"
            )
        calls.append("other")
        return LLMResponse(
            content='{"pass": true, "reason": "mock pass"}', model_used="mock"
        )

    result = run_admission(
        raw_path="mock.md",
        raw_content=_MARKETING_NUMBERED_HEADINGS,
        ledger_path=str(tmp_path / "ledger.jsonl"),
        wiki_references=None,
        route_llm_fn=mock_llm,
    )
    assert result.passed is False
    assert "mechanism" in calls, "非 recheck 路径 mechanism 也必须被真实仲裁"
    mech = [c for c in result.checks if c.name == "mechanism_signal"]
    assert mech and mech[0].passed is False
    assert not mech[0].reason.startswith("确定性机制信号")


def _mock_reject_lane_no_mechanism(calls: list) -> Any:
    def mock_llm(_model: Any, messages: list, **kwargs: Any) -> LLMResponse:
        schema = kwargs.get("response_schema") or {}
        if "content_lane" in (schema.get("properties") or {}):
            calls.append("lane")
            return LLMResponse(
                content='{"content_lane": "reject", "reason": "营销软文"}',
                model_used="mock",
            )
        system = str(messages[0].get("content", "")) if messages else ""
        if "机制" in system:
            calls.append("mechanism")
            return LLMResponse(
                content='{"pass": false, "reason": "无可复用机制"}', model_used="mock"
            )
        calls.append("other")
        return LLMResponse(
            content='{"pass": true, "reason": "mock pass"}', model_used="mock"
        )

    return mock_llm


def test_run_admission_numbered_heading_marketing_not_resurrected(
    tmp_path: Any,
) -> None:
    """[reviewer 验收·全路径] `## 1/2/3` 编号标题营销文:mock lane=reject →
    D11 降级 → mechanism **必须被调用**且否决 → 整体 rejected,reason 非确定性。"""
    calls: list = []
    result = run_admission(
        raw_path="mock.md",
        raw_content=_MARKETING_NUMBERED_HEADINGS,
        ledger_path=str(tmp_path / "ledger.jsonl"),
        wiki_references=None,
        route_llm_fn=_mock_reject_lane_no_mechanism(calls),
    )
    assert result.passed is False
    if result.lane_reject_recheck:  # D11 降级发生时,mechanism 否决权必须行使
        assert "mechanism" in calls
        mech = [c for c in result.checks if c.name == "mechanism_signal"]
        assert mech and mech[0].passed is False
        assert not mech[0].reason.startswith("确定性机制信号")


def test_run_admission_marketing_reject_not_resurrected(tmp_path: Any) -> None:
    """[reviewer 反例1 全路径] lane 判 reject 的营销文经 D11 降级后,
    mechanism LLM 保留否决权 → 整体拒收(后门闭合)。"""
    calls: list = []
    result = run_admission(
        raw_path="mock.md",
        raw_content=_MARKETING_BODY,
        ledger_path=str(tmp_path / "ledger.jsonl"),
        wiki_references=None,
        route_llm_fn=_mock_reject_lane_no_mechanism(calls),
    )
    assert result.passed is False
    mech = [c for c in result.checks if c.name == "mechanism_signal"]
    if mech:  # 若 D11 降级发生,mechanism 必须被真实仲裁且否决,不得确定性 PASS
        assert not mech[0].reason.startswith("确定性机制信号")
        assert mech[0].passed is False


# ---------------------------------------------------------------------------
# F13-2 rubric 1.3.0 冷启动校准
# ---------------------------------------------------------------------------


def test_rubric_130_cold_start_notes_and_dims_frozen() -> None:
    """rubric concept 5 维名、权重与阈值逐字匹配当前 YAML（维度集冻结）。"""
    spec = _load_rubric_spec()
    assert str(spec.get("rubric_version")) == "1.5.0"
    concept = (spec.get("lanes") or {}).get("concept") or {}
    dims = {str(d["name"]): d for d in concept.get("dimensions", [])}
    # 当前 5 维逐字冻结；advisory 维已从生产加权集退役。
    assert sorted(dims) == sorted(
        [
            "概念机制清晰度(H3)",
            "实现机制说明(H2)",
            "适用边界",
            "TLDR密度",
            "Future Questions质量",
        ]
    )
    assert concept.get("threshold") == 6.0
    assert {name: dims[name]["weight"] for name in dims} == {
        "概念机制清晰度(H3)": 4.0,
        "实现机制说明(H2)": 1.0,
        "适用边界": 2.0,
        "TLDR密度": 1.5,
        "Future Questions质量": 1.5,
    }


# ---------------------------------------------------------------------------
# F13-3 calibration 失败正样本有界重试
# ---------------------------------------------------------------------------


def _fake_entry_result(idx: int, passed: bool, expected: str) -> dict[str, Any]:
    return {
        "index": idx,
        "url": f"fixture:mock-{idx}",
        "expected": expected,
        "passed": passed,
        "final_status": "promoted" if passed else "failed",
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
