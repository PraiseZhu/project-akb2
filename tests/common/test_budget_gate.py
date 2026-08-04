from __future__ import annotations

import datetime as dt
import json
import os
import stat
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from src.akb2.common import budget_gate
from src.akb2.common.state_paths import get_lock_dir, state_file_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
UTC = dt.UTC
UTC_DAILY_EXHAUSTED_AT = dt.datetime(2026, 8, 1, 21, 14, tzinfo=UTC)


def _record_daily_budget_exhausted(state_root: Path) -> None:
    os.environ["AKB2_STATE_ROOT"] = str(state_root)
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.UTC_DAILY_SCOPE,
            spend=2701.23,
            cap=2700.0,
            budget_date="2026-08-01",
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message=(
                "ExceededBudget: exceeded UTC daily cap. "
                "Date=2026-08-01, Spend=2701.23, DailyCap=2700.0"
            ),
        ),
        recorded_at=UTC_DAILY_EXHAUSTED_AT,
        consumer="seed",
    )


def _concurrent_record_and_query(state_root: str, consumer: str) -> dict[str, object]:
    os.environ["AKB2_STATE_ROOT"] = state_root
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.UTC_DAILY_SCOPE,
            spend=2701.23,
            cap=2700.0,
            budget_date="2026-08-01",
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message="ExceededBudget: exceeded UTC daily cap.",
        ),
        recorded_at=UTC_DAILY_EXHAUSTED_AT,
        consumer=consumer,
    )
    decision = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 21, 30, tzinfo=UTC),
        consumer=consumer,
    )
    state = budget_gate.load_state_snapshot()
    return {
        "allowed": decision.allowed,
        "write_count": state["write_count"],
        "state": state,
    }


def _make_stub(path: Path, marker: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"echo stub >> {str(marker)!r}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_utc_daily_boundary_short_circuits_until_next_utc_midnight(monkeypatch, tmp_path):
    monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
    _record_daily_budget_exhausted(tmp_path / "state")

    at_2130 = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 21, 30, tzinfo=UTC),
        consumer="github.synthesize",
    )
    at_2330 = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 23, 30, tzinfo=UTC),
        consumer="quality.admission",
    )
    at_0405 = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 2, 4, 5, tzinfo=UTC),
        consumer="quality.distill",
    )

    assert at_2130.allowed is False
    assert at_2330.allowed is False
    assert at_0405.allowed is True


def test_corrupt_or_unreadable_state_degrades_fail_open(monkeypatch, tmp_path, caplog):
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    caplog.set_level("WARNING")
    state_path = state_file_path(budget_gate.STATE_FILE_NAME)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"broken": ', encoding="utf-8")

    corrupt_decision = budget_gate.query_budget_gate(consumer="corrupt")
    assert corrupt_decision.allowed is True
    assert corrupt_decision.degraded is True

    state_path.write_text("{}", encoding="utf-8")
    state_path.chmod(0)
    try:
        unreadable_decision = budget_gate.query_budget_gate(consumer="chmod000")
    finally:
        state_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert unreadable_decision.allowed is True
    assert unreadable_decision.degraded is True
    assert "shared budget gate degraded to fail-open" in caplog.text


def test_concurrent_record_keeps_json_valid_and_counts_all_updates(monkeypatch, tmp_path):
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    with ProcessPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                _concurrent_record_and_query,
                [str(state_root)] * 8,
                [f"consumer-{index}" for index in range(8)],
            )
        )

    assert all(result["allowed"] is False for result in results)
    snapshot = budget_gate.load_state_snapshot()
    assert snapshot["write_count"] == 8

    parsed = json.loads(
        state_file_path(budget_gate.STATE_FILE_NAME).read_text(encoding="utf-8")
    )
    assert isinstance(parsed, dict)
    assert parsed["write_count"] == 8


def test_daily_scripts_fail_visible_before_pipeline_when_budget_is_exhausted(tmp_path):
    state_root = tmp_path / "state"
    _record_daily_budget_exhausted(state_root)
    marker = tmp_path / "blocked.marker"
    github_stub = _make_stub(tmp_path / "github-stub.sh", marker)
    social_stub = _make_stub(tmp_path / "social-stub.sh", marker)

    common_env = {
        **os.environ,
        "AKB2_STATE_ROOT": str(state_root),
        "AKB2_DISABLE_DEADLINE": "1",
        "VAULT_ROOT": str(tmp_path / "vault"),
        "AKB2_GITHUB_BRIEFING_PIPELINE_BIN": str(github_stub),
        "AKB2_SOCIAL_BRIEFING_PIPELINE_BIN": str(social_stub),
        "AKB2_BUDGET_GATE_NOW": "2026-08-01T21:30:00Z",
    }

    github = subprocess.run(
        ["/bin/bash", str(SCRIPTS_DIR / "daily_github_briefing.sh")],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )
    social = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPTS_DIR / "daily_briefing.sh"),
            "--vault-root",
            str(tmp_path / "vault"),
        ],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )

    for result in (github, social):
        combined = f"{result.stdout}\n{result.stderr}"
        assert result.returncode != 0
        assert result.returncode != 124
        assert "blocked_by_shared_budget scope=utc_daily" in combined
    assert not marker.exists()


def test_daily_scripts_allow_pipeline_when_budget_gate_is_clear(tmp_path):
    state_root = tmp_path / "state"
    marker = tmp_path / "allowed.marker"
    github_stub = _make_stub(tmp_path / "github-stub.sh", marker)
    social_stub = _make_stub(tmp_path / "social-stub.sh", marker)

    common_env = {
        **os.environ,
        "AKB2_STATE_ROOT": str(state_root),
        "AKB2_DISABLE_DEADLINE": "1",
        "VAULT_ROOT": str(tmp_path / "vault"),
        "AKB2_GITHUB_BRIEFING_PIPELINE_BIN": str(github_stub),
        "AKB2_SOCIAL_BRIEFING_PIPELINE_BIN": str(social_stub),
    }

    github = subprocess.run(
        ["/bin/bash", str(SCRIPTS_DIR / "daily_github_briefing.sh")],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )
    social = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPTS_DIR / "daily_briefing.sh"),
            "--vault-root",
            str(tmp_path / "vault"),
        ],
        cwd=str(PROJECT_ROOT),
        env=common_env,
        capture_output=True,
        text=True,
    )

    assert github.returncode == 0, github.stderr
    assert social.returncode == 0, social.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == ["stub", "stub"]


# ---------------------------------------------------------------------------
# SC-R1-02 —— budget_exceeded 必须带具名 scope，否则拒写（fail-loud）
# ---------------------------------------------------------------------------


def test_record_rejects_unscoped_budget_event(monkeypatch, tmp_path):
    """空 scope 的预算事件写进去 = 装了个永不短路的闸门 → 必须拒写且状态文件不脏。

    R1 实测的缺陷形态：形态 A 的 429 归出 scope=''，落盘后状态文件里**看起来**
    有一条预算耗尽记录，`query_budget_gate` 却一路 allowed=True。
    """
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    with pytest.raises(budget_gate.UnscopedBudgetEventError) as excinfo:
        budget_gate.record_budget_event(
            budget_gate.BudgetDiagnostics(
                error_type=budget_gate.BUDGET_EXCEEDED,
                budget_scope="",
                spend=18000.6,
                cap=18000.0,
                provider_code=budget_gate.PROVIDER_CODE_429,
                raw_message="ExceededBudget: over budget. Spend=18000.6, Budget=18000.0",
            ),
            recorded_at=UTC_DAILY_EXHAUSTED_AT,
            consumer="unscoped-probe",
        )
    assert "named budget scope" in str(excinfo.value)

    # 状态文件里不得出现空 scope 的预算态（拒写发生在落盘之前）
    state_path = state_file_path(budget_gate.STATE_FILE_NAME)
    assert not state_path.exists(), "拒写的事件不该建出状态文件"

    # 非预算事件（rate_limit）不受这条约束影响 —— 它本来就没有 scope
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.RATE_LIMIT,
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message="429 Too Many Requests",
        ),
        recorded_at=UTC_DAILY_EXHAUSTED_AT,
        consumer="rate-limit-probe",
    )
    snapshot = budget_gate.load_state_snapshot()
    assert snapshot["last_error"]["error_type"] == budget_gate.RATE_LIMIT
    assert snapshot["active_block"] is None


# ---------------------------------------------------------------------------
# SC-R1-03 / SC-R1-04 —— 预算封锁在冷却期内单调保持
# ---------------------------------------------------------------------------


def test_rate_limit_does_not_clear_budget_cooldown(monkeypatch, tmp_path):
    """先记预算耗尽，再记一次普通 rate_limit 429 → 仍必须 blocked。

    旧实现只有一个 `last_error` 槽且判定的第一步是
    `last_error.error_type != budget_exceeded → allowed=True`，
    于是 24/7 quality runner 的任意一次普通限流就把封锁清了。
    """
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    _record_daily_budget_exhausted(state_root)

    at_2130 = dt.datetime(2026, 8, 1, 21, 30, tzinfo=UTC)
    assert budget_gate.query_budget_gate(now=at_2130, consumer="before").allowed is False

    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.RATE_LIMIT,
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message="429 Too Many Requests (transient)",
        ),
        recorded_at=at_2130,
        consumer="quality.runner",
    )

    after = budget_gate.query_budget_gate(now=at_2130, consumer="after")
    assert after.allowed is False, "普通限流事件不得清除仍有效的预算封锁"
    assert after.reason == "utc_daily_budget_exhausted"
    assert after.budget_scope == budget_gate.UTC_DAILY_SCOPE

    snapshot = budget_gate.load_state_snapshot()
    # 两个槽语义正交：最近诊断是 rate_limit，有效封锁仍是预算耗尽
    assert snapshot["last_error"]["error_type"] == budget_gate.RATE_LIMIT
    assert snapshot["active_block"]["error_type"] == budget_gate.BUDGET_EXCEEDED

    # 具名边界之后才解封（UTC 午夜）
    assert budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 2, 0, 1, tzinfo=UTC), consumer="next-day"
    ).allowed is True


def test_budget_state_monotonic_within_cooldown(monkeypatch, tmp_path):
    """冷却期内任意顺序注入低级别事件，判定不变；且 total 短封锁不得缩短日封锁。"""
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    _record_daily_budget_exhausted(state_root)

    at_2130 = dt.datetime(2026, 8, 1, 21, 30, tzinfo=UTC)
    lower_level_events = [
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.RATE_LIMIT,
            provider_code=budget_gate.PROVIDER_CODE_429,
        ),
        budget_gate.BudgetDiagnostics(error_type=budget_gate.OTHER_ERROR),
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.RATE_LIMIT,
            provider_code=budget_gate.PROVIDER_CODE_429,
        ),
        budget_gate.BudgetDiagnostics(error_type=budget_gate.OTHER_ERROR),
    ]
    for index, event in enumerate(lower_level_events):
        budget_gate.record_budget_event(
            event, recorded_at=at_2130, consumer=f"noise-{index}"
        )
        decision = budget_gate.query_budget_gate(now=at_2130, consumer=f"check-{index}")
        assert decision.allowed is False, f"第 {index} 个低级别事件清除了封锁"
        assert decision.reason == "utc_daily_budget_exhausted"

    # total scope 的 cooldown 只有 1h，绝不能覆盖掉到 UTC 午夜的日封锁
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.TOTAL_SCOPE,
            cap=18000.0,
            provider_code=budget_gate.PROVIDER_CODE_429,
        ),
        recorded_at=at_2130,
        consumer="total-shorter",
    )
    at_2300 = dt.datetime(2026, 8, 1, 23, 0, tzinfo=UTC)  # > total cooldown(22:30)
    still_blocked = budget_gate.query_budget_gate(now=at_2300, consumer="after-total")
    assert still_blocked.allowed is False, "更短的 total 封锁不得缩短更晚的日封锁边界"
    assert still_blocked.budget_scope == budget_gate.UTC_DAILY_SCOPE


# ---------------------------------------------------------------------------
# SC-R1-05 —— 损坏状态遇到新的确定性预算事件必须在锁内重建
# ---------------------------------------------------------------------------


def test_corrupt_state_rebuilds_on_next_budget_event(monkeypatch, tmp_path, caplog):
    """写坏状态 → record 一个预算事件 → 状态恢复合法 JSON 且后续 query 短路。

    旧行为：`_read_state_unlocked` 抛错 → record 落到 fail-open 分支且**不重建**，
    此后每次调用都 degraded，闸门永久失效（唯一信号是一行 warning）。
    """
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))
    caplog.set_level("WARNING")

    state_path = state_file_path(budget_gate.STATE_FILE_NAME)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"broken": ', encoding="utf-8")

    # query 仍 fail-open（状态损坏不该变成新的全局单点故障）
    degraded = budget_gate.query_budget_gate(consumer="corrupt-read")
    assert degraded.allowed is True
    assert degraded.degraded is True

    _record_daily_budget_exhausted(state_root)

    rebuilt = json.loads(state_path.read_text(encoding="utf-8"))
    assert rebuilt["version"] == budget_gate.STATE_VERSION
    assert rebuilt["rebuilt_from_corrupt"] == "JSONDecodeError"
    assert rebuilt["active_block"]["budget_scope"] == budget_gate.UTC_DAILY_SCOPE

    # 损坏证据被隔离保留，不是静默丢弃
    quarantined = state_path.with_name(
        state_path.name + budget_gate.CORRUPT_STATE_SUFFIX
    )
    assert quarantined.read_text(encoding="utf-8") == '{"broken": '

    decision = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 21, 30, tzinfo=UTC), consumer="after-rebuild"
    )
    assert decision.allowed is False
    assert decision.degraded is False


# ---------------------------------------------------------------------------
# SC-R1-10 —— 0600 落盘 + 原文脱敏
# ---------------------------------------------------------------------------


def test_state_files_0600_and_redacted(monkeypatch, tmp_path):
    """新建状态/锁文件权限必须是 0600，且落盘内容不含 provider 原文中的敏感串。"""
    state_root = tmp_path / "state"
    monkeypatch.setenv("AKB2_STATE_ROOT", str(state_root))

    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.UTC_DAILY_SCOPE,
            spend=2701.23,
            cap=2700.0,
            budget_date="2026-08-01",
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message=(
                "ExceededBudget: User=zhuzan exceeded UTC daily cap. "
                "Date=2026-08-01, Spend=2701.23, DailyCap=2700.0 "
                "(Authorization: Bearer sk-probe-secret-2026)"
            ),
        ),
        recorded_at=UTC_DAILY_EXHAUSTED_AT,
        consumer="redaction-probe",
    )

    state_path = state_file_path(budget_gate.STATE_FILE_NAME)
    assert stat.S_IMODE(state_path.stat().st_mode) == budget_gate.STATE_FILE_MODE

    lock_path = get_lock_dir() / budget_gate.LOCK_FILE_NAME
    assert stat.S_IMODE(lock_path.stat().st_mode) == budget_gate.STATE_FILE_MODE

    persisted = state_path.read_text(encoding="utf-8")
    assert "sk-probe-secret-2026" not in persisted
    assert "User=zhuzan" not in persisted
    assert "<redacted>" in persisted
    # 分类字段仍在（脱敏不得连诊断价值一起删）
    block = json.loads(persisted)["active_block"]
    assert block["budget_scope"] == budget_gate.UTC_DAILY_SCOPE
    assert block["cap"] == 2700.0
    assert block["budget_date"] == "2026-08-01"
