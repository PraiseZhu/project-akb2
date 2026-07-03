"""test_f14b_mechanism_double_arbitration.py — F14-B mechanism 仲裁双次取或测试。

v5 admission memory/crewai/material no_mechanism_signal(deepseek 仲裁稳定说不,重试不救
=系统性偏严)。F14-B:仲裁失败时再仲裁一次(MECHANISM_ARBITRATION_MAX_VOTES=2,任一 pass
即 pass;两 fail 才 fail)。LLM 仍唯一放行人。强反信号(全0+短正文)不可绕过。
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality.admission import (
    MECHANISM_ARBITRATION_MAX_VOTES,
    _check_mechanism_signal,
)


class _MockResp:
    def __init__(self, content: Any):
        self.content = content


def _make_route_fn(responses: list[Any]):
    """返回一个 mock route_llm_fn,每次调用返 responses 的下一个(末个重复)。"""
    state = {"n": 0}

    def _fn(*a, **kw):
        i = state["n"]
        state["n"] += 1
        if i < len(responses):
            return _MockResp(responses[i])
        return _MockResp(responses[-1])
    return _fn, state


class TestF14BDoubleArbitration:
    def test_max_votes_is_2(self):
        assert MECHANISM_ARBITRATION_MAX_VOTES == 2

    def test_first_fail_second_pass_passes(self, monkeypatch):
        """首次 fail 二次 pass → pass + reason 记 2 票(v1=fail/v2=pass)。"""
        body = "## Overview\n\nThe Captain dispatches to the Executor Pool which feeds the Task Graph."
        fn, state = _make_route_fn([
            {"pass": False, "reason": "no mechanism v1"},
            {"pass": True, "reason": "mechanism v2"},
        ])
        result = _check_mechanism_signal(body, fn)
        assert result.passed is True
        assert "v1=fail" in result.reason
        assert "v2=pass" in result.reason
        assert state["n"] == 2  # 调了 2 次(首次 fail → 第二次)

    def test_both_fail_fails(self, monkeypatch):
        """两次 fail → fail + reason 记 2 票全 fail。"""
        body = "## Overview\n\nThe Captain dispatches to the Executor Pool which feeds the Task Graph."
        fn, state = _make_route_fn([
            {"pass": False, "reason": "no mechanism v1"},
            {"pass": False, "reason": "no mechanism v2"},
        ])
        result = _check_mechanism_signal(body, fn)
        assert result.passed is False
        assert "v1=fail" in result.reason
        assert "v2=fail" in result.reason
        assert result.reason.startswith("no_mechanism_signal")  # runner 映射 error_class
        assert state["n"] == 2

    def test_first_pass_no_second_call(self, monkeypatch):
        """首次 pass → 不发第二次(省调用)。"""
        body = "## Overview\n\nThe Captain dispatches to the Executor Pool which feeds the Task Graph."
        fn, state = _make_route_fn([{"pass": True, "reason": "mechanism v1"}])
        result = _check_mechanism_signal(body, fn)
        assert result.passed is True
        assert "v1=pass" in result.reason
        assert state["n"] == 1  # 只调 1 次(首次 pass 不再调)

    def test_strong_anti_signal_not_bypassed(self, monkeypatch):
        """强反信号(全0+短正文)不调 LLM,deterministic fail,不可绕过。"""
        body = "short prose no mechanism signal at all here."
        fn, state = _make_route_fn([{"pass": True, "reason": "would bypass"}])
        result = _check_mechanism_signal(body, fn)
        assert result.passed is False
        assert result.reason.startswith("no_mechanism_signal")
        assert state["n"] == 0  # 没调 LLM(强反信号 deterministic fail)

    def test_router_config_error_fail_closed(self, monkeypatch):
        """RouterConfigError → fail-closed(不重试配置错误)。"""
        body = "## Overview\n\nThe Captain dispatches to the Executor Pool which feeds the Task Graph."
        import src.akb2.quality.llm_router as lr

        def _raise(*a, **kw):
            raise lr.RouterConfigError("config error")
        result = _check_mechanism_signal(body, _raise)
        assert result.passed is False
        assert "fail-closed" in result.reason
