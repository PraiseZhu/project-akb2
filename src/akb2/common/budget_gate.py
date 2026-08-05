"""Shared budget gate for cross-process Gateway budget exhaustion short-circuiting.

The state lives under ``AKB2_STATE_ROOT`` and is intentionally machine-local so
multiple daemons on the same machine can coordinate without writing into vault.

## 状态机（R1 审查后重写；此前是「单槽 last_error」，三条缺陷同源）

两个槽，语义正交，别再合并回一个：

``active_block``
    **当前有效的预算封锁**。只由 ``error_type=budget_exceeded`` 且带具名 scope 的
    事件建立，只在具名边界（utc_daily → 下一个 UTC 午夜；total → cooldown 到期）
    自然过期。后到的 rate_limit / other 事件**不得**清除它 —— 预算耗尽的 cooldown
    是时间上不可逆的态，被普通限流重放清掉就等于闸门失效（R1 实测：先记预算耗尽
    再记一次普通 429，判定即回到 allowed=True）。

``last_error``
    **最近一次诊断事件**（任意类型）。纯排查素材，不参与短路判定。

query 与 record 的降级方向刻意不同：

- query 遇到损坏状态 → fail-open（状态损坏不该变成新的全局单点故障）。
- record 遇到损坏状态 → **锁内隔离损坏证据 + 重建默认结构 + 落盘新事件**。
  否则一次写坏就永久 degraded：此后每个确定性预算事件都进不去，闸门永久失效。

落盘一律 0600（provider 原文即便脱敏也不该全机可读），原文经
``http_retry.redact_provider_text`` 统一脱敏后才进状态文件。
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
# 损坏状态的隔离副本后缀（固定后缀而非带时间戳：证据要留，但不能无界增长）
CORRUPT_STATE_SUFFIX = ".corrupt"
# v1 = 单槽 last_error；v2 = active_block（单调封锁） + last_error（最近诊断）
STATE_VERSION = 2
UTC_DAILY_SCOPE = "utc_daily"
TOTAL_SCOPE = "total"
BUDGET_EXCEEDED = "budget_exceeded"
RATE_LIMIT = "rate_limit"
OTHER_ERROR = "other"
PROVIDER_CODE_429 = "429"
TOTAL_BUDGET_COOLDOWN_SECONDS = 3600
CLI_BLOCKED_EXIT_CODE = 10
# 能建立封锁的 scope 集合 —— exact 而非 subset：不在集合内的 budget_exceeded
# 事件算不出 cooldown，落盘就是个静默失效的闸门，必须 fail-loud 拒写。
NAMED_BUDGET_SCOPES: frozenset[str] = frozenset({UTC_DAILY_SCOPE, TOTAL_SCOPE})
# 落盘权限（状态文件/锁文件 0600，目录 0700）
STATE_FILE_MODE = 0o600
STATE_DIR_MODE = 0o700
_DEFAULT_CONSUMER = "unknown"

try:  # pragma: no cover - runtime path
    from src.akb2.common.http_retry import redact_provider_text
    from src.akb2.common.state_paths import get_lock_dir, state_file_path
except ImportError:  # pragma: no cover - script runtime path
    from akb2.common.http_retry import redact_provider_text
    from akb2.common.state_paths import get_lock_dir, state_file_path


class UnscopedBudgetEventError(ValueError):
    """``budget_exceeded`` 事件缺具名 scope —— 拒绝写入（fail-loud）。

    为什么不「宽容接受」：scope 为空 → ``_cooldown_for`` 返回空 →
    ``_active_short_circuit`` 落到兜底 allowed=True。也就是说这种事件写进去之后，
    状态文件里**看起来**有一条预算耗尽记录，闸门却一次都不会短路。R1 审查实测
    形态 A（`over budget. Spend=…, Budget=…`）正是这样被放行的。
    归类不出 scope = 上游分类器坏了，那是必须响亮暴露的编程错误，不是运行时降级点。
    """


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
        # 当前有效封锁（单调，只在具名边界过期）
        "active_block": None,
        # 最近一次诊断事件（任意类型，纯排查素材）
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


def _mkdir_private(directory: Path) -> None:
    """建目录并尽力收到 0700（已存在的目录不强改，避免动别人的状态根）。"""
    existed = directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if not existed:
        try:
            directory.chmod(STATE_DIR_MODE)
        except OSError as exc:  # pragma: no cover - 异常文件系统
            logger.warning("failed to tighten state dir mode %s: %s", directory, exc)


def _write_state_unlocked(path: Path, state: dict[str, Any]) -> None:
    _mkdir_private(path.parent)
    tmp = path.with_name(f"{path.name}.tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # 用 O_CREAT+0600 打开而不是 write_text：write_text 走默认 umask，
    # R1 实测生产落成 0644（provider 原文全机可读）。os.replace 保留 tmp 的模式，
    # 所以在 tmp 上定权限即可覆盖最终文件。
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, STATE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    # tmp 可能是上一次遗留的旧文件（模式已存在，O_CREAT 不改）→ 显式收紧一次
    os.chmod(tmp, STATE_FILE_MODE)
    os.replace(tmp, path)


def _quarantine_corrupt_state(path: Path, reason: str) -> Path | None:
    """把损坏状态另存一份再重建 —— 证据要留（宪法 P4），但不能挡住新事件落盘。"""
    if not path.exists():
        return None
    quarantine = path.with_name(f"{path.name}{CORRUPT_STATE_SUFFIX}")
    try:
        os.replace(path, quarantine)
    except OSError as exc:  # pragma: no cover - 异常文件系统
        logger.warning("failed to quarantine corrupt budget state %s: %s", path, exc)
        return None
    logger.warning(
        "budget gate state was corrupt (%s); quarantined to %s and rebuilt defaults",
        reason,
        quarantine,
    )
    return quarantine


def _read_or_rebuild_unlocked(path: Path) -> tuple[dict[str, Any], str]:
    """锁内读状态；损坏则隔离证据并返回默认结构（附 rebuild 原因）。"""
    try:
        return _read_state_unlocked(path), ""
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        reason = type(exc).__name__
        _quarantine_corrupt_state(path, reason)
        state = _default_state()
        state["rebuilt_from_corrupt"] = reason
        return state, reason


def _with_lock(mutator):
    state_path, lock_path = _lock_and_path()
    _mkdir_private(lock_path.parent)
    if not lock_path.exists():
        os.close(os.open(lock_path, os.O_WRONLY | os.O_CREAT, STATE_FILE_MODE))
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


def _normalize_diagnostics(
    diagnostics: BudgetDiagnostics, now: dt.datetime
) -> BudgetDiagnostics:
    """补 utc_daily 的 budget_date + 统一脱敏原文（幂等，可对已脱敏文本重复调用）。"""
    budget_date = diagnostics.budget_date
    if diagnostics.budget_scope == UTC_DAILY_SCOPE and not budget_date:
        budget_date = now.date().isoformat()
    return BudgetDiagnostics(
        error_type=diagnostics.error_type,
        budget_scope=diagnostics.budget_scope,
        spend=diagnostics.spend,
        cap=diagnostics.cap,
        budget_date=budget_date,
        provider_code=diagnostics.provider_code,
        # 兜底脱敏：调用方即使忘了脱敏，原文也不会以裸形态进状态文件
        raw_message=redact_provider_text(diagnostics.raw_message),
    )


def _assert_scoped_budget_event(diagnostics: BudgetDiagnostics) -> None:
    """budget_exceeded 必须带具名 scope，否则拒写（见 UnscopedBudgetEventError）。"""
    if diagnostics.error_type != BUDGET_EXCEEDED:
        return
    if diagnostics.budget_scope in NAMED_BUDGET_SCOPES:
        return
    raise UnscopedBudgetEventError(
        f"refusing to persist {BUDGET_EXCEEDED} event without a named budget scope "
        f"(got budget_scope={diagnostics.budget_scope!r}; "
        f"expected one of {sorted(NAMED_BUDGET_SCOPES)}) —— "
        "空 scope 算不出 cooldown，写进去等于装了个永不短路的闸门；"
        "上游必须用 http_retry.classify_provider_error 归出 scope。"
    )


def _event_payload(
    diagnostics: BudgetDiagnostics, now: dt.datetime, consumer: str
) -> dict[str, Any]:
    return {
        **asdict(diagnostics),
        "consumer": consumer,
        "recorded_at": _format_ts(now),
        "cooldown_until": _cooldown_for(diagnostics, now),
    }


def _block_is_active(block: dict[str, Any] | None, now: dt.datetime) -> bool:
    """该封锁此刻是否仍在具名边界之内。"""
    if not isinstance(block, dict):
        return False
    if block.get("error_type") != BUDGET_EXCEEDED:
        return False
    scope = str(block.get("budget_scope", "") or "")
    if scope not in NAMED_BUDGET_SCOPES:
        return False
    cooldown_until = _parse_ts(str(block.get("cooldown_until", "") or ""))
    if scope == UTC_DAILY_SCOPE:
        budget_date = str(block.get("budget_date", "") or "")
        if not budget_date or budget_date != now.date().isoformat():
            return False
        return cooldown_until is None or now < cooldown_until
    return cooldown_until is not None and now < cooldown_until


def _merge_active_block(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any],
    now: dt.datetime,
) -> dict[str, Any]:
    """单调合并：保留边界更晚的那个封锁。

    「后到的预算事件直接覆盖」也是错的：total scope 的 cooldown 只有 1 小时，
    若它覆盖掉一条到 UTC 午夜才解的 utc_daily 封锁，就等于**缩短**了封锁 ——
    同样是「有效封锁被提前清除」，只是清除者换成了另一个预算事件。
    """
    if not _block_is_active(existing, now):
        return candidate
    existing_until = _parse_ts(str((existing or {}).get("cooldown_until", "") or ""))
    candidate_until = _parse_ts(str(candidate.get("cooldown_until", "") or ""))
    if existing_until is None:
        return existing or candidate
    if candidate_until is None or candidate_until <= existing_until:
        return existing or candidate
    return candidate


def record_budget_event(
    diagnostics: BudgetDiagnostics,
    *,
    recorded_at: dt.datetime | None = None,
    consumer: str = _DEFAULT_CONSUMER,
) -> dict[str, Any]:
    """Persist the latest provider budget/rate-limit diagnosis.

    单调性（SC-R1-03/04）：处于未过期预算封锁时，rate_limit / other 事件只更新
    ``last_error``（排查素材），**不动** ``active_block``。

    损坏恢复（SC-R1-05）：状态文件损坏时在锁内隔离证据 + 重建默认结构 + 落盘本事件，
    不再永久 degraded。

    Raises:
        UnscopedBudgetEventError: ``budget_exceeded`` 且 scope 不具名（fail-loud，
            这是上游分类器的编程错误，不是可降级的运行时状况）。

    其它文件系统/路径/JSON 问题仍降级为可见 warning 并返回 best-effort payload，
    让调用方保持原有的错误语义。
    """

    now = _ensure_utc(recorded_at)
    diagnostics = _normalize_diagnostics(diagnostics, now)
    _assert_scoped_budget_event(diagnostics)
    event = _event_payload(diagnostics, now, consumer)

    def _mutate(path: Path) -> dict[str, Any]:
        state, rebuilt_from = _read_or_rebuild_unlocked(path)
        state["version"] = STATE_VERSION
        state["write_count"] = int(state.get("write_count", 0)) + 1
        state["last_updated_at"] = _format_ts(now)
        state["last_error"] = event
        if rebuilt_from:
            state["rebuilt_from_corrupt"] = rebuilt_from
        existing_block = _legacy_aware_active_block(state)
        if diagnostics.error_type == BUDGET_EXCEEDED:
            state["active_block"] = _merge_active_block(existing_block, event, now)
        elif _block_is_active(existing_block, now):
            # 单调：低级别事件不得清除仍有效的预算封锁
            state["active_block"] = existing_block
            logger.warning(
                "shared budget gate keeps active block scope=%s cooldown_until=%s"
                " despite newer error_type=%s consumer=%s",
                existing_block.get("budget_scope"),
                existing_block.get("cooldown_until"),
                diagnostics.error_type,
                consumer,
            )
        else:
            state["active_block"] = None
        _write_state_unlocked(path, state)
        return state

    try:
        return _with_lock(_mutate)
    except UnscopedBudgetEventError:  # pragma: no cover - 已在锁外校验
        raise
    except Exception as exc:  # noqa: BLE001 - fail-open boundary
        logger.warning(
            "shared budget gate record degraded to fail-open: %s", exc
        )
        return {
            **_default_state(),
            "last_error": event,
            "active_block": event if diagnostics.error_type == BUDGET_EXCEEDED else None,
        }


def _legacy_aware_active_block(state: dict[str, Any]) -> dict[str, Any] | None:
    """取当前封锁槽；v1 状态文件（只有 last_error）走一次性向后兼容读。

    生产磁盘上存在 v1 状态文件，升级后第一次 query 不能因为少了 active_block 键
    就把有效封锁当成不存在 —— 那等于用一次代码升级清掉了 cooldown。
    """
    block = state.get("active_block")
    if isinstance(block, dict):
        return block
    if block is not None:
        return None
    legacy = state.get("last_error")
    if isinstance(legacy, dict) and legacy.get("error_type") == BUDGET_EXCEEDED:
        return legacy
    return None


def _active_short_circuit(
    active_block: dict[str, Any] | None,
    *,
    now: dt.datetime,
    consumer: str,
) -> BudgetGateDecision:
    if not isinstance(active_block, dict):
        return BudgetGateDecision(
            allowed=True,
            reason="no_recorded_budget_failure",
            consumer=consumer,
        )
    if active_block.get("error_type") != BUDGET_EXCEEDED:
        return BudgetGateDecision(
            allowed=True,
            reason="last_error_not_budget_exceeded",
            consumer=consumer,
        )

    scope = str(active_block.get("budget_scope", "") or "")
    cooldown_until_raw = str(active_block.get("cooldown_until", "") or "")
    cooldown_until = _parse_ts(cooldown_until_raw)
    budget_date = str(active_block.get("budget_date", "") or "")

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
        _legacy_aware_active_block(state),
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
