"""Shared budget gate for cross-process Gateway budget exhaustion short-circuiting.

The state lives under ``AKB2_STATE_ROOT`` and is intentionally machine-local so
multiple daemons on the same machine can coordinate without writing into vault.
State corruption must never become a new global outage: unreadable state
degrades to fail-open with a visible warning.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_FILE_NAME = "shared-budget-gate.json"
LOCK_FILE_NAME = "shared-budget-gate.lock"
STATE_VERSION = 1
UTC_DAILY_SCOPE = "utc_daily"
TOTAL_SCOPE = "total"
BUDGET_EXCEEDED = "budget_exceeded"
RATE_LIMIT = "rate_limit"
OTHER_ERROR = "other"
PROVIDER_CODE_429 = "429"
TOTAL_BUDGET_COOLDOWN_SECONDS = 3600
CLI_BLOCKED_EXIT_CODE = 10
_DEFAULT_CONSUMER = "unknown"

try:  # pragma: no cover - runtime path
    from src.akb2.common.state_paths import get_lock_dir, state_file_path
except ImportError:  # pragma: no cover - script runtime path
    from akb2.common.state_paths import get_lock_dir, state_file_path


@dataclass(frozen=True)
class BudgetDiagnostics:
    error_type: str = OTHER_ERROR
    budget_scope: str = ""
    spend: float | None = None
    cap: float | None = None
    budget_date: str = ""
    provider_code: str = ""
    raw_message: str = ""


@dataclass(frozen=True)
class BudgetGateDecision:
    allowed: bool
    reason: str
    budget_scope: str = ""
    cooldown_until: str = ""
    error_type: str = ""
    budget_date: str = ""
    degraded: bool = False
    consumer: str = _DEFAULT_CONSUMER

    def shell_message(self) -> str:
        if self.allowed:
            return (
                f"shared_budget_gate allow reason={self.reason} consumer={self.consumer}"
            )
        return (
            "blocked_by_shared_budget"
            f" scope={self.budget_scope or 'unknown'}"
            f" cooldown_until={self.cooldown_until or 'unknown'}"
            f" consumer={self.consumer}"
        )


def _utc_now() -> dt.datetime:
    injected = _parse_ts(os.environ.get("AKB2_BUDGET_GATE_NOW", "").strip())
    if injected is not None:
        return injected
    return dt.datetime.now(dt.UTC)


def _ensure_utc(moment: dt.datetime | None) -> dt.datetime:
    if moment is None:
        return _utc_now()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.UTC)
    return moment.astimezone(dt.UTC)


def _format_ts(moment: dt.datetime) -> str:
    return _ensure_utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return _ensure_utc(parsed)


def _next_utc_midnight(moment: dt.datetime) -> dt.datetime:
    utc = _ensure_utc(moment)
    next_day = utc.date() + dt.timedelta(days=1)
    return dt.datetime.combine(next_day, dt.time(0, 0), tzinfo=dt.UTC)


def _state_path() -> Path:
    return state_file_path(STATE_FILE_NAME)


def _lock_path() -> Path:
    return get_lock_dir() / LOCK_FILE_NAME


def _lock_and_path() -> tuple[Path, Path]:
    return _state_path(), _lock_path()


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "write_count": 0,
        "last_updated_at": "",
        "last_error": None,
    }


def _read_state_unlocked(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_state()
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("budget gate state must be a JSON object")
    state = _default_state()
    state.update(data)
    return state


def _write_state_unlocked(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _with_lock(mutator):
    state_path, lock_path = _lock_and_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            return mutator(state_path)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _safe_state_read() -> tuple[dict[str, Any], str | None]:
    try:
        state = _with_lock(_read_state_unlocked)
    except Exception as exc:  # noqa: BLE001 - fail-open boundary
        warning = f"shared budget gate degraded to fail-open: {exc}"
        logger.warning(warning)
        return _default_state(), warning
    return state, None


def _cooldown_for(
    diagnostics: BudgetDiagnostics, recorded_at: dt.datetime
) -> str:
    if diagnostics.error_type != BUDGET_EXCEEDED:
        return ""
    if diagnostics.budget_scope == UTC_DAILY_SCOPE:
        return _format_ts(_next_utc_midnight(recorded_at))
    if diagnostics.budget_scope == TOTAL_SCOPE:
        return _format_ts(
            recorded_at + dt.timedelta(seconds=TOTAL_BUDGET_COOLDOWN_SECONDS)
        )
    return ""


def record_budget_event(
    diagnostics: BudgetDiagnostics,
    *,
    recorded_at: dt.datetime | None = None,
    consumer: str = _DEFAULT_CONSUMER,
) -> dict[str, Any]:
    """Persist the latest provider budget/rate-limit diagnosis.

    Any filesystem/path/JSON issue degrades to a visible warning and returns a
    best-effort payload without raising, so callers keep their original error
    semantics.
    """

    now = _ensure_utc(recorded_at)
    if diagnostics.budget_scope == UTC_DAILY_SCOPE and not diagnostics.budget_date:
        diagnostics = BudgetDiagnostics(
            error_type=diagnostics.error_type,
            budget_scope=diagnostics.budget_scope,
            spend=diagnostics.spend,
            cap=diagnostics.cap,
            budget_date=now.date().isoformat(),
            provider_code=diagnostics.provider_code,
            raw_message=diagnostics.raw_message,
        )

    def _mutate(path: Path) -> dict[str, Any]:
        state = _read_state_unlocked(path)
        state["write_count"] = int(state.get("write_count", 0)) + 1
        state["last_updated_at"] = _format_ts(now)
        state["last_error"] = {
            **asdict(diagnostics),
            "consumer": consumer,
            "recorded_at": _format_ts(now),
            "cooldown_until": _cooldown_for(diagnostics, now),
        }
        _write_state_unlocked(path, state)
        return state

    try:
        return _with_lock(_mutate)
    except Exception as exc:  # noqa: BLE001 - fail-open boundary
        logger.warning(
            "shared budget gate record degraded to fail-open: %s", exc
        )
        return {
            **_default_state(),
            "last_error": {
                **asdict(diagnostics),
                "consumer": consumer,
                "recorded_at": _format_ts(now),
                "cooldown_until": _cooldown_for(diagnostics, now),
            },
        }


def _active_short_circuit(
    last_error: dict[str, Any] | None,
    *,
    now: dt.datetime,
    consumer: str,
) -> BudgetGateDecision:
    if not isinstance(last_error, dict):
        return BudgetGateDecision(
            allowed=True,
            reason="no_recorded_budget_failure",
            consumer=consumer,
        )
    if last_error.get("error_type") != BUDGET_EXCEEDED:
        return BudgetGateDecision(
            allowed=True,
            reason="last_error_not_budget_exceeded",
            consumer=consumer,
        )

    scope = str(last_error.get("budget_scope", "") or "")
    cooldown_until_raw = str(last_error.get("cooldown_until", "") or "")
    cooldown_until = _parse_ts(cooldown_until_raw)
    budget_date = str(last_error.get("budget_date", "") or "")

    if scope == UTC_DAILY_SCOPE:
        if budget_date and budget_date == now.date().isoformat():
            if cooldown_until is None or now < cooldown_until:
                return BudgetGateDecision(
                    allowed=False,
                    reason="utc_daily_budget_exhausted",
                    budget_scope=scope,
                    cooldown_until=cooldown_until_raw or "unknown",
                    error_type=BUDGET_EXCEEDED,
                    budget_date=budget_date,
                    consumer=consumer,
                )
        return BudgetGateDecision(
            allowed=True,
            reason="utc_daily_budget_reset",
            consumer=consumer,
        )

    if scope == TOTAL_SCOPE and cooldown_until is not None and now < cooldown_until:
        return BudgetGateDecision(
            allowed=False,
            reason="total_budget_exhausted",
            budget_scope=scope,
            cooldown_until=cooldown_until_raw,
            error_type=BUDGET_EXCEEDED,
            consumer=consumer,
        )

    return BudgetGateDecision(
        allowed=True,
        reason="budget_cooldown_expired",
        consumer=consumer,
    )


def query_budget_gate(
    *,
    now: dt.datetime | None = None,
    consumer: str = _DEFAULT_CONSUMER,
) -> BudgetGateDecision:
    utc_now = _ensure_utc(now)
    state, warning = _safe_state_read()
    if warning is not None:
        return BudgetGateDecision(
            allowed=True,
            reason="degraded_fail_open",
            degraded=True,
            consumer=consumer,
        )

    decision = _active_short_circuit(
        state.get("last_error"),
        now=utc_now,
        consumer=consumer,
    )
    if not decision.allowed:
        logger.warning(
            "shared budget gate short-circuit consumer=%s scope=%s cooldown_until=%s",
            consumer,
            decision.budget_scope,
            decision.cooldown_until,
        )
    return decision


def load_state_snapshot() -> dict[str, Any]:
    """Best-effort state snapshot for tests/debugging."""
    state, _warning = _safe_state_read()
    return state


def _cli_now() -> dt.datetime | None:
    return _parse_ts(os.environ.get("AKB2_BUDGET_GATE_NOW", "").strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AKB2 shared budget gate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="check shared budget gate")
    preflight.add_argument("--consumer", default=_DEFAULT_CONSUMER)

    args = parser.parse_args(argv)
    if args.command == "preflight":
        decision = query_budget_gate(now=_cli_now(), consumer=args.consumer)
        message = decision.shell_message()
        stream = sys.stderr if not decision.allowed else sys.stdout
        print(message, file=stream)
        return 0 if decision.allowed else CLI_BLOCKED_EXIT_CODE
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    import sys

    raise SystemExit(main(sys.argv[1:]))
