"""P0-c lane reject 门 + NUMBERED 真命令检测直测。

reject 门：LLM 判 reject（新闻/营销/纯观点等非知识）→ admission 直接拒，
content_lane 不下传 reject（不混进通过态，w9）。堵"新闻被判 concept → how_tendency
豁免 → 溜过准入"后门（W5/w7 实证）。
NUMBERED 修复：编号行反引号须是真 CLI 命令才算强 procedural 信号；编号机制点里的
API 调用 / 模型名 / 概念术语不触发短路（W7/w9 实测旧正则误判）。
"""

from __future__ import annotations


def test_numbered_backtick_api_or_model_name_not_strong_procedural():
    """编号行含反引号 API 调用/模型名（非真命令）不触发强 procedural 短路。"""
    from src.akb2.quality.admission import _has_strong_procedural_signal

    body = (
        "# Agent Architecture Mechanism\n\n"
        "The loop has these named components and their interactions:\n"
        "1. The `agent.plan()` method decomposes the goal into subtasks\n"
        "2. The `ledger.append()` call records each state transition\n"
        "3. The `DeepSeek-V3` model performs the synthesis step\n"
    )
    assert _has_strong_procedural_signal(body) is False


def test_numbered_real_shell_commands_is_strong_procedural():
    """编号行含 ≥3 真 shell 命令仍触发强 procedural（不放松对真教程的识别）。"""
    from src.akb2.quality.admission import _has_strong_procedural_signal

    body = (
        "# Deployment Walkthrough\n\n"
        "1. Run `npm install` to fetch dependencies\n"
        "2. Run `git clone https://example.com/repo.git`\n"
        "3. Run `docker build -t app .`\n"
    )
    assert _has_strong_procedural_signal(body) is True


def test_reject_lane_rejects_admission_without_relying_on_how(tmp_path):
    """LLM 判 reject → admission 拒；reason 含 non_knowledge；content_lane 不下传。"""
    from src.akb2.quality.admission import run_admission
    from src.akb2.quality.llm_router import LLMResponse
    from tests.quality.conftest import make_mock_route_llm

    news = (
        "---\nartifact_type: raw\nsource_slug: x\nsource_url: https://e.com/x\n"
        "source_title: T\nsource_authors: A\nsource_publisher: P\n"
        "source_published_at: 2026-06-13\nfetched_at: 2026-06-13T00:00:00Z\n"
        "fetched_via: defuddle\n---\n\n# Google I/O Recap\n\n"
        "Google announced new models today. The keynote covered product updates "
        "and release dates without explaining mechanisms or teaching steps.\n"
    )
    mock_llm = make_mock_route_llm({
        "gemini-flash:0": LLMResponse(
            content={"pass": True, "reason": "topic"}, model_used="m", calls_used=1
        ),
        "gemini-flash:1": LLMResponse(
            content={"pass": False, "reason": "no how"}, model_used="m", calls_used=1
        ),
        "gemini-flash:2": LLMResponse(
            content={"pass": True, "reason": "novel"}, model_used="m", calls_used=1
        ),
        "lane": LLMResponse(
            content={"content_lane": "reject", "reason": "纯新闻播报"},
            model_used="m", calls_used=1,
        ),
    })
    result = run_admission(
        raw_path="raw/x--raw.md",
        raw_content=news,
        ledger_path=str(tmp_path / "l.jsonl"),
        route_llm_fn=mock_llm,
    )
    assert result.passed is False
    assert "non_knowledge" in result.reason
    assert result.content_lane == ""
