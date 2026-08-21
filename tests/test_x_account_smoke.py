"""x7 x_account_smoke：候选全集、_post_filter 原创判定、--check fail-closed。

twitter-cli 一律 mock；不触网。限流必须记 rate_limited 而不是 suspended。
全局 ClientTransaction / 未登录不得摊成每个账号不合格。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "x_account_smoke.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("x_account_smoke", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


smoke = _load_mod()


def _tweet(tid: str, *, text: str = "hello", iso: str = "2026-08-10T08:00:00+00:00", is_rt: bool = False) -> dict:
    return {
        "id": tid,
        "text": text,
        "author": {"screenName": "acct"},
        "createdAtISO": iso,
        "isRetweet": is_rt,
    }


def _ok_payload(raw: list[dict]) -> str:
    return json.dumps({"ok": True, "schema_version": "1", "data": raw})


def _err_payload(code: str, message: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "schema_version": "1",
            "error": {"code": code, "message": message},
        }
    )


def _tiny_curator(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "fetch_tier": {
                    "en": ["OpenAI", "karpathy"],
                    "zh": ["dotey"],
                }
            }
        )
    )
    return path


def _row(handle: str, **kwargs) -> dict:
    base = {
        "handle": handle,
        "ok": True,
        "reason": "ok",
        "raw_count": 2,
        "original_count": 2,
        "latest_original_age_days": 3,
    }
    base.update(kwargs)
    return base


def _report(handles: list[str], *, first_batch: list[str] | None = None, extra_rows: list[dict] | None = None) -> dict:
    rows = [_row(h) for h in handles]
    if extra_rows:
        rows.extend(extra_rows)
    return {
        "generated_at": "2026-08-20T00:00:00Z",
        "candidates": rows,
        "first_batch": first_batch if first_batch is not None else handles[:],
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 候选全集
# ---------------------------------------------------------------------------


def test_candidates_union_fetch_tier_and_design_accounts():
    handles = smoke.load_candidates(REPO_ROOT / "config" / "curator-accounts.yaml")
    assert "karpathy" in handles
    assert "dotey" in handles
    for name in smoke.DESIGN_FIRST_BATCH:
        assert name in handles
    assert len(handles) == len(set(handles))
    assert len(handles) == 33  # fetch_tier 30 + MengTo/rileybrown/nickfloats


def test_tiny_curator_merges_design_first_batch(tmp_path: Path):
    cfg = _tiny_curator(tmp_path / "curator.yaml")
    handles = smoke.load_candidates(cfg)
    assert handles[:3] == ["OpenAI", "karpathy", "dotey"]
    assert handles[-3:] == list(smoke.DESIGN_FIRST_BATCH)


# ---------------------------------------------------------------------------
# 原创过滤复用 _post_filter
# ---------------------------------------------------------------------------


def test_original_filter_drops_retweets_and_replies():
    raw = [
        _tweet("1"),
        _tweet("2", is_rt=True),
        _tweet("3", text="@someone thanks"),
        _tweet("4", iso="2026-07-01T00:00:00+00:00"),
    ]
    row = smoke.classify_originals(
        "acct", raw, today=date(2026, 8, 20), max_idle_days=30
    )
    assert row["raw_count"] == 4
    assert row["original_count"] == 2
    assert row["ok"] is True
    assert row["reason"] == "ok"
    assert row["latest_original_age_days"] == 10


def test_empty_history_is_filter_zero_not_raw_count():
    raw = [_tweet("1", is_rt=True), _tweet("2", text="@x hi")]
    row = smoke.classify_originals(
        "acct", raw, today=date(2026, 8, 20), max_idle_days=30
    )
    assert row["raw_count"] == 2
    assert row["original_count"] == 0
    assert row["ok"] is True
    assert row["reason"] == "empty_history"
    assert not smoke.is_eligible(row, 30)


def test_idle_beyond_max_days_not_eligible():
    raw = [_tweet("1", iso="2026-06-01T00:00:00+00:00")]
    row = smoke.classify_originals(
        "acct", raw, today=date(2026, 8, 20), max_idle_days=30
    )
    assert row["reason"] == "idle"
    assert row["latest_original_age_days"] == 80
    assert not smoke.is_eligible(row, 30)


# ---------------------------------------------------------------------------
# 探测分类：429 ≠ suspended；全局故障不摊账号
# ---------------------------------------------------------------------------


def test_probe_uses_userposts_not_search():
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout=_ok_payload([_tweet("1")]), stderr="")

    smoke.probe_handle(
        "karpathy",
        today=date(2026, 8, 20),
        max_idle_days=30,
        twitter_bin="twitter",
        runner=_run,
    )
    assert cmds[0][1] == "user-posts"
    assert cmds[0][2] == "karpathy"
    assert cmds[0][cmds[0].index("-n") + 1] == "2"
    assert "--json" in cmds[0]
    assert "search" not in cmds[0]


def test_not_found_with_clienttransaction_warning_is_per_account():
    def _run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=_err_payload("not_found", "User @yangyi_io not found"),
            stderr=(
                "WARNING twitter_cli.client: Failed to init ClientTransaction: "
                "'NoneType' object has no attribute 'group'"
            ),
        )

    row = smoke.probe_handle(
        "yangyi_io",
        today=date(2026, 8, 20),
        max_idle_days=30,
        twitter_bin="twitter",
        runner=_run,
    )
    assert row["ok"] is False
    assert row["reason"] == "not_found"


def test_rate_limited_with_clienttransaction_warning_stays_rate_limited():
    def _run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=_err_payload("rate_limited", "Rate limited after 3 retries"),
            stderr=(
                "WARNING twitter_cli.client: Failed to init ClientTransaction: "
                "'NoneType' object has no attribute 'group'\n"
                "WARNING twitter_cli.client: Rate limited (429), retrying in 5.2s"
            ),
        )

    row = smoke.probe_handle(
        "acct",
        today=date(2026, 8, 20),
        max_idle_days=30,
        twitter_bin="twitter",
        runner=_run,
    )
    assert row["reason"] == "rate_limited"


def test_rate_limited_is_not_suspended():
    def _run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=_err_payload("rate_limited", "Rate limited after 3 retries"),
            stderr="WARNING twitter_cli.client: Rate limited (429), retrying in 5.2s",
        )

    row = smoke.probe_handle(
        "acct",
        today=date(2026, 8, 20),
        max_idle_days=30,
        twitter_bin="twitter",
        runner=_run,
    )
    assert row["ok"] is False
    assert row["reason"] == "rate_limited"
    assert row["reason"] != "suspended"


def test_client_transaction_failure_is_global_not_per_account():
    def _run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=_err_payload("api_error", "GraphQL 404"),
            stderr=(
                "WARNING twitter_cli.client: Failed to init ClientTransaction: "
                "'NoneType' object has no attribute 'group'"
            ),
        )

    with pytest.raises(smoke.GlobalProbeError) as exc:
        smoke.probe_handle(
            "acct",
            today=date(2026, 8, 20),
            max_idle_days=30,
            twitter_bin="twitter",
            runner=_run,
        )
    assert exc.value.kind == "client_transaction"
    assert "单账号不合格" in str(exc.value)


def test_client_transaction_warning_on_success_is_not_global():
    def _run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=_ok_payload([_tweet("1")]),
            stderr=(
                "WARNING twitter_cli.client: Failed to init ClientTransaction: "
                "'NoneType' object has no attribute 'group'"
            ),
        )

    row = smoke.probe_handle(
        "acct",
        today=date(2026, 8, 20),
        max_idle_days=30,
        twitter_bin="twitter",
        runner=_run,
    )
    assert row["ok"] is True
    assert row["reason"] == "ok"


def test_unauthenticated_is_global():
    def _run(cmd, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=_err_payload("not_authenticated", "Not authenticated"),
            stderr="",
        )

    with pytest.raises(smoke.GlobalProbeError) as exc:
        smoke.probe_handle(
            "acct",
            today=date(2026, 8, 20),
            max_idle_days=30,
            twitter_bin="twitter",
            runner=_run,
        )
    assert exc.value.kind == "not_authenticated"


def test_run_probe_global_fail_does_not_mark_remaining_suspended(monkeypatch):
    handles = ["a", "b", "c"]
    calls: list[str] = []
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "/usr/local/bin/twitter")

    def _run(cmd, **kwargs):
        if cmd[1] == "status":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"authenticated": True}}),
                stderr="",
            )
        calls.append(cmd[2])
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Failed to init ClientTransaction: boom",
        )

    with pytest.raises(smoke.GlobalProbeError):
        smoke.run_probe(
            handles,
            max_idle_days=30,
            pacing_sec=0.0,
            today=date(2026, 8, 20),
            twitter_bin="twitter",
            runner=_run,
        )
    assert calls == ["a"]


def test_pacing_sleeps_at_least_default_between_accounts(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(smoke.shutil, "which", lambda name: "/usr/local/bin/twitter")

    def _run(cmd, **kwargs):
        if cmd[1] == "status":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"authenticated": True}}),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout=_ok_payload([_tweet("1")]), stderr="")

    smoke.run_probe(
        ["a", "b"],
        max_idle_days=30,
        pacing_sec=smoke.DEFAULT_PACING_SEC,
        today=date(2026, 8, 20),
        twitter_bin="twitter",
        sleeper=lambda s: slept.append(s),
        runner=_run,
    )
    assert slept == [pytest.approx(3.0)]
    assert smoke.DEFAULT_PACING_SEC >= 3.0


def test_cli_missing_is_global(tmp_path: Path, monkeypatch):
    missing = str(tmp_path / "no-twitter")
    monkeypatch.setattr(smoke.shutil, "which", lambda name: None)
    with pytest.raises(smoke.GlobalProbeError) as exc:
        smoke.assert_cli_ready(missing, runner=lambda *a, **k: SimpleNamespace())
    assert exc.value.kind == "cli_missing"


# ---------------------------------------------------------------------------
# --check 只读闸门
# ---------------------------------------------------------------------------


def _run_check(report: Path, curator: Path, max_idle_days: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--check",
            "--report",
            str(report),
            "--curator-config",
            str(curator),
            "--max-idle-days",
            str(max_idle_days),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_check_missing_report_exit_2(tmp_path: Path):
    curator = _tiny_curator(tmp_path / "curator.yaml")
    result = _run_check(tmp_path / "missing.json", curator)
    assert result.returncode == 2


def test_check_incomplete_coverage_exit_2(tmp_path: Path):
    curator = _tiny_curator(tmp_path / "curator.yaml")
    expected = smoke.load_candidates(curator)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(expected[:-1])))
    result = _run_check(report, curator)
    assert result.returncode == 2
    assert "未覆盖" in result.stderr


def test_check_empty_first_batch_exit_2(tmp_path: Path):
    curator = _tiny_curator(tmp_path / "curator.yaml")
    expected = smoke.load_candidates(curator)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(expected, first_batch=[])))
    result = _run_check(report, curator)
    assert result.returncode == 2
    assert "first_batch 为空" in result.stderr


def test_check_unqualified_first_batch_member_exit_2(tmp_path: Path):
    curator = _tiny_curator(tmp_path / "curator.yaml")
    expected = smoke.load_candidates(curator)
    payload = _report(expected, first_batch=[expected[0]])
    payload["candidates"][0]["ok"] = False
    payload["candidates"][0]["reason"] = "suspended"
    payload["candidates"][0]["original_count"] = 0
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload))
    result = _run_check(report, curator)
    assert result.returncode == 2
    assert "不合格" in result.stderr


def test_check_candidate_failure_does_not_fail_when_first_batch_ok(tmp_path: Path):
    curator = _tiny_curator(tmp_path / "curator.yaml")
    expected = smoke.load_candidates(curator)
    payload = _report(expected, first_batch=[expected[0]])
    payload["candidates"][-1] = _row(
        expected[-1],
        ok=False,
        reason="suspended",
        raw_count=0,
        original_count=0,
        latest_original_age_days=None,
    )
    payload["errors"] = [{"handle": expected[-1], "reason": "suspended"}]
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload))
    result = _run_check(report, curator)
    assert result.returncode == 0, result.stderr
    assert "X_ACCOUNT_SMOKE_CHECK_OK" in result.stdout


def test_check_happy_path_exit_0(tmp_path: Path):
    curator = _tiny_curator(tmp_path / "curator.yaml")
    expected = smoke.load_candidates(curator)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(expected)))
    result = _run_check(report, curator)
    assert result.returncode == 0, result.stderr


def test_check_does_not_invoke_twitter(tmp_path: Path, monkeypatch):
    curator = _tiny_curator(tmp_path / "curator.yaml")
    expected = smoke.load_candidates(curator)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(expected)))

    def _boom(*args, **kwargs):
        raise AssertionError(" --check 不得打网")

    monkeypatch.setattr(smoke.subprocess, "run", _boom)
    rc = smoke.main(
        [
            "--check",
            "--report",
            str(report),
            "--curator-config",
            str(curator),
            "--max-idle-days",
            "30",
        ]
    )
    assert rc == 0
