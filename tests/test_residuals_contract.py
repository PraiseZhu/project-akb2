"""Contract tests for explicitly accepted residual risks."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESIDUALS = PROJECT_ROOT / "docs" / "allowed-residuals.md"


def _section(marker: str) -> str:
    text = RESIDUALS.read_text(encoding="utf-8")
    heading = f"### {marker}"
    assert heading in text, f"missing residual heading: {heading}"
    return text.split(heading, 1)[1].split("\n### ", 1)[0]


def _assert_terms(section: str, terms: tuple[str, ...]) -> None:
    missing = [term for term in terms if term not in section]
    assert not missing, f"residual omits required terms: {missing}"


def test_main_backstop_shared_lock_residual_registered():
    section = _section("A7-b — main/backstop 不共锁")
    _assert_terms(
        section,
        (
            "daily_briefing.sh",
            "daily_github_briefing.sh",
            "pgrep -f",
            "main×backstop",
            "双花额度",
            "锁生命周期",
        ),
    )


def test_lock_sync_risk_residual_registered():
    section = _section("A11 — 同步锁根只告警不拒跑")
    _assert_terms(
        section,
        (
            "STATE_ROOT=~/Library/Application Support/akb2",
            "is_synced=NO",
            "AKB2_STATE_ROOT",
            "AKB2_BACKSTOP_LOCK_FILE",
            "非默认 env",
            "kill -0",
            "共享租约",
        ),
    )


def test_f14_sdk_retry_residual_registered():
    section = _section("F14 — llm_router 的 SDK 层仍自动重试 429")
    _assert_terms(
        section,
        ("429", "SDK", "自动重试", "额外消耗额度", "共享写闸", "consumer", "有界"),
    )


def test_missing_policy_residual_registered():
    section = _section("s5 — missing_policy 静默回落")
    _assert_terms(
        section,
        (
            "config.py",
            "非法或缺失",
            "宪法 P4",
            "默认行为",
            "无告警",
            "本轮不修",
            "较宽松的那个",
            "partial_ok_recoverable",
            "src/akb2/feishu_sync/config.py:21,61-65",
            "fail_all",
            "下一个 PR",
            "届时删除本条",
        ),
    )
