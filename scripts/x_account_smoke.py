#!/usr/bin/env python3
"""x7 账号冒烟：探测 curator 候选 handle 是否仍能产出原创。

对候选（config/curator-accounts.yaml 的 fetch_tier ∪ MengTo/rileybrown/nickfloats）
逐个跑::

    twitter user-posts <handle> -n 2 --json

user-posts 没有 --exclude/--since；原创判定复用
``account_collector.AccountCollector._post_filter``（剔 RT、以 @ 开头的回复）。
空历史 = filter 后 0 条，不用 raw 条数冒充原创。

限流：账号间停顿默认 3s（实测 2.5s 跑到约 34 个会 429）。429 记
``rate_limited``，不得记 ``suspended``。

全局 ClientTransaction / 未登录 / CLI 不可用：整次 fail-closed（非 0 退出），
不要把每个 handle 写成账号不合格。

``--check`` 只读报告、不打网。报告缺失 / 缺字段 / 候选覆盖不全 /
first_batch 空或其中有不合格 → exit 2。候选失败只记入报告，不因此失败。
eligible / first_batch 为空 → exit 2（禁止诚实空集当成功）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from akb2.briefing.intake.account_collector import AccountCollector  # noqa: E402
from akb2.briefing.intake.l0_collector import (  # noqa: E402
    TWITTER_BIN,
    TWITTER_TIMEOUT_SEC,
    match_upstream_fatal,
)

# 计划首批 design 账号（priority-plan 原文），与 fetch_tier 去重合并为候选全集。
DESIGN_FIRST_BATCH: tuple[str, ...] = ("MengTo", "rileybrown", "nickfloats")
FETCH_N = 2
# 账号间停顿。历史实测 pacing 2.5s 跑到约 34 个账号触发 429；限流 ≠ suspended。
DEFAULT_PACING_SEC = 3.0
DEFAULT_MAX_IDLE_DAYS = 30
DEFAULT_REPORT = REPO_ROOT / "reports" / "x-account-smoke.json"
DEFAULT_CURATOR = REPO_ROOT / "config" / "curator-accounts.yaml"

EXIT_OK = 0
EXIT_PROBE_FAIL = 1
EXIT_CHECK_FAIL = 2

REQUIRED_REPORT_KEYS = ("generated_at", "candidates", "first_batch", "errors")
REQUIRED_CANDIDATE_KEYS = (
    "handle",
    "ok",
    "reason",
    "raw_count",
    "original_count",
    "latest_original_age_days",
)


class GlobalProbeError(RuntimeError):
    """CLI / 登录 / ClientTransaction 一类整轮故障，不得摊到每个 handle。"""

    def __init__(self, kind: str, message: str, handle: str | None = None) -> None:
        self.kind = kind
        self.handle = handle
        super().__init__(message)


def load_candidates(curator_path: Path) -> list[str]:
    """fetch_tier.en ∪ fetch_tier.zh ∪ DESIGN_FIRST_BATCH，保序去重。"""
    with open(curator_path) as f:
        cfg = yaml.safe_load(f) or {}
    seen: set[str] = set()
    out: list[str] = []
    fetch = cfg.get("fetch_tier") or {}
    for tier in ("en", "zh"):
        for raw in fetch.get(tier) or []:
            handle = str(raw).strip()
            if handle and handle not in seen:
                seen.add(handle)
                out.append(handle)
    for handle in DESIGN_FIRST_BATCH:
        if handle not in seen:
            seen.add(handle)
            out.append(handle)
    return out


def _twitter_bin() -> str:
    return shutil.which("twitter") or TWITTER_BIN


def _age_days(iso: str, today: date) -> int | None:
    day = str(iso)[:10]
    if not day:
        return None
    try:
        created = date.fromisoformat(day)
    except ValueError:
        return None
    return (today - created).days


def _is_rate_limited(text: str) -> bool:
    lowered = text.lower()
    return (
        "rate limited" in lowered
        or "rate_limited" in lowered
        or "429" in lowered
        or "code 88" in lowered
    )


def _is_suspended(text: str) -> bool:
    lowered = text.lower()
    return "suspended" in lowered or "userunavailable" in lowered


def _is_not_found(text: str) -> bool:
    return "not found" in text.lower()


def _is_not_authenticated(text: str) -> bool:
    lowered = text.lower()
    return "not_authenticated" in lowered or "not authenticated" in lowered


def _row(
    handle: str,
    *,
    ok: bool,
    reason: str,
    raw_count: int = 0,
    original_count: int = 0,
    latest_original_age_days: int | None = None,
) -> dict[str, Any]:
    return {
        "handle": handle,
        "ok": ok,
        "reason": reason,
        "raw_count": raw_count,
        "original_count": original_count,
        "latest_original_age_days": latest_original_age_days,
    }


def _originals(handle: str, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collector = AccountCollector(per_account_cap=FETCH_N, pacing_sec=0.0)
    return collector._post_filter(handle, raw, None, None)


def classify_originals(
    handle: str,
    raw: list[dict[str, Any]],
    *,
    today: date,
    max_idle_days: int,
) -> dict[str, Any]:
    originals = _originals(handle, raw)
    ages = [
        a
        for t in originals
        if (a := _age_days(str(t.get("createdAtISO", "")), today)) is not None
    ]
    latest = min(ages) if ages else None
    if not originals:
        return _row(
            handle,
            ok=True,
            reason="empty_history",
            raw_count=len(raw),
            original_count=0,
            latest_original_age_days=None,
        )
    if latest is None:
        return _row(
            handle,
            ok=True,
            reason="empty_history",
            raw_count=len(raw),
            original_count=len(originals),
            latest_original_age_days=None,
        )
    active_age = max(latest, 0)
    reason = "ok" if active_age <= max_idle_days else "idle"
    return _row(
        handle,
        ok=True,
        reason=reason,
        raw_count=len(raw),
        original_count=len(originals),
        latest_original_age_days=active_age,
    )


def is_eligible(row: dict[str, Any], max_idle_days: int) -> bool:
    age = row.get("latest_original_age_days")
    return bool(
        row.get("ok")
        and int(row.get("original_count") or 0) > 0
        and age is not None
        and int(age) <= max_idle_days
    )


def _parse_payload(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _error_blob(payload: dict[str, Any] | None, stderr: str, stdout: str) -> str:
    parts = [stderr or "", stdout or ""]
    if payload:
        err = payload.get("error") or {}
        if isinstance(err, dict):
            parts.append(str(err.get("code") or ""))
            parts.append(str(err.get("message") or ""))
        parts.append(json.dumps(payload, ensure_ascii=False)[:500])
    return "\n".join(p for p in parts if p)


def _error_code(payload: dict[str, Any] | None) -> str:
    if payload and isinstance(payload.get("error"), dict):
        return str(payload["error"].get("code") or "")
    return ""


def _per_account_reason(blob: str, payload: dict[str, Any] | None) -> str | None:
    """账号级原因优先于 stderr 里伴随的 ClientTransaction 警告。

    本机 twitter-cli 成功请求也会打 Failed to init ClientTransaction；
    真正的全局故障是「没有更具体的账号级错误码、且签名就是失败原因」。
    """
    err_code = _error_code(payload)
    if err_code == "rate_limited" or _is_rate_limited(blob):
        return "rate_limited"
    if err_code == "not_found" or _is_not_found(blob):
        return "not_found"
    if _is_suspended(blob):
        return "suspended"
    return None


def _raise_if_global(handle: str, blob: str, payload: dict[str, Any] | None) -> None:
    if _per_account_reason(blob, payload):
        return
    err_code = _error_code(payload)
    if match_upstream_fatal(blob):
        raise GlobalProbeError(
            "client_transaction",
            f"@{handle} 命中全局 ClientTransaction 故障，中止整轮（不得记成单账号不合格）",
            handle=handle,
        )
    if err_code == "not_authenticated" or _is_not_authenticated(blob):
        raise GlobalProbeError(
            "not_authenticated",
            f"@{handle} 探测到未登录，中止整轮",
            handle=handle,
        )


def _per_account_failure(handle: str, blob: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    reason = _per_account_reason(blob, payload) or "error"
    return _row(handle, ok=False, reason=reason)


def probe_handle(
    handle: str,
    *,
    today: date,
    max_idle_days: int,
    twitter_bin: str,
    timeout_sec: int = TWITTER_TIMEOUT_SEC,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    cmd = [twitter_bin, "user-posts", handle, "-n", str(FETCH_N), "--json"]
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        blob = f"timeout after {timeout_sec}s"
        _raise_if_global(handle, blob, None)
        row = _row(handle, ok=False, reason="timeout")
        row["detail"] = str(exc)
        return row

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    payload = _parse_payload(stdout)
    blob = _error_blob(payload, stderr, stdout)
    success = (
        result.returncode == 0
        and payload is not None
        and payload.get("ok") is True
        and isinstance(payload.get("data"), list)
    )
    if not success:
        _raise_if_global(handle, blob, payload)
        return _per_account_failure(handle, blob, payload)

    raw = payload.get("data") or []
    return classify_originals(handle, raw, today=today, max_idle_days=max_idle_days)


def assert_cli_ready(
    twitter_bin: str,
    *,
    runner: Any = subprocess.run,
    timeout_sec: int = 20,
) -> None:
    resolved = Path(twitter_bin)
    if not resolved.exists() and shutil.which(twitter_bin) is None:
        raise GlobalProbeError(
            "cli_missing",
            f"twitter CLI 不可用: {twitter_bin}",
        )
    result = runner(
        [twitter_bin, "status", "--json"],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    payload = _parse_payload(result.stdout or "")
    blob = _error_blob(payload, result.stderr or "", result.stdout or "")
    authed = bool(((payload or {}).get("data") or {}).get("authenticated"))
    status_ok = payload is not None and payload.get("ok") is True and authed
    if status_ok:
        return
    _raise_if_global("status", blob, payload)
    if payload is not None and not authed:
        raise GlobalProbeError("not_authenticated", "twitter status 显示未登录，中止整轮")
    if result.returncode != 0:
        raise GlobalProbeError(
            "cli_missing",
            f"twitter status 失败 (exit {result.returncode})，中止整轮",
        )


def first_batch_of(candidates: list[dict[str, Any]], max_idle_days: int) -> list[str]:
    return [row["handle"] for row in candidates if is_eligible(row, max_idle_days)]


def build_report(
    candidate_rows: list[dict[str, Any]],
    *,
    max_idle_days: int,
    errors: list[dict[str, Any]],
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at
        or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_idle_days": max_idle_days,
        "candidates": candidate_rows,
        "first_batch": first_batch_of(candidate_rows, max_idle_days),
        "errors": errors,
    }


def check_report(
    report: dict[str, Any],
    expected_handles: list[str],
    max_idle_days: int,
) -> list[str]:
    problems: list[str] = []
    for key in REQUIRED_REPORT_KEYS:
        if key not in report:
            problems.append(f"报告缺字段: {key}")
    if problems:
        return problems

    rows = report.get("candidates")
    if not isinstance(rows, list):
        return ["candidates 不是列表"]
    batch = report.get("first_batch")
    if not isinstance(batch, list):
        return ["first_batch 不是列表"]
    if not isinstance(report.get("errors"), list):
        return ["errors 不是列表"]

    by_handle: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"candidates[{i}] 不是对象")
            continue
        missing = [k for k in REQUIRED_CANDIDATE_KEYS if k not in row]
        if missing:
            problems.append(f"candidates[{i}] 缺字段: {','.join(missing)}")
            continue
        handle = str(row.get("handle") or "")
        if handle:
            by_handle[handle] = row

    missing_handles = [h for h in expected_handles if h not in by_handle]
    if missing_handles:
        problems.append(f"报告未覆盖候选: {','.join(missing_handles)}")

    if not batch:
        problems.append("first_batch 为空（fail-closed，禁止诚实空集）")
        return problems

    for handle in batch:
        row = by_handle.get(str(handle))
        if row is None:
            problems.append(f"first_batch 含未知 handle: {handle}")
            continue
        if not is_eligible(row, max_idle_days):
            problems.append(
                f"first_batch 成员不合格: {handle} "
                f"(ok={row.get('ok')} original_count={row.get('original_count')} "
                f"age={row.get('latest_original_age_days')} reason={row.get('reason')})"
            )
    return problems


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def run_probe(
    handles: list[str],
    *,
    max_idle_days: int,
    pacing_sec: float,
    today: date,
    twitter_bin: str,
    sleeper: Any = None,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    import time as time_mod

    sleep = sleeper or time_mod.sleep
    errors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    assert_cli_ready(twitter_bin, runner=runner)
    for i, handle in enumerate(handles):
        if i > 0 and pacing_sec > 0:
            sleep(pacing_sec)
        row = probe_handle(
            handle,
            today=today,
            max_idle_days=max_idle_days,
            twitter_bin=twitter_bin,
            runner=runner,
        )
        rows.append({k: v for k, v in row.items() if k != "detail"})
        if not row.get("ok"):
            errors.append(
                {
                    "handle": handle,
                    "reason": row.get("reason"),
                    "message": row.get("detail") or row.get("reason"),
                }
            )
    return build_report(rows, max_idle_days=max_idle_days, errors=errors)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"报告不可读: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"报告不是 JSON 对象: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x_account_smoke")
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="探测报告路径（默认 reports/x-account-smoke.json）",
    )
    parser.add_argument(
        "--curator-config",
        default=str(DEFAULT_CURATOR),
        help="curator-accounts.yaml 路径",
    )
    parser.add_argument(
        "--max-idle-days",
        type=int,
        default=DEFAULT_MAX_IDLE_DAYS,
        help="原创距今超过该天数则不入 first_batch",
    )
    parser.add_argument(
        "--pacing-sec",
        type=float,
        default=DEFAULT_PACING_SEC,
        help="账号间隔秒数（默认 3；2.5s@34 会 429）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只读校验报告，不打网",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = REPO_ROOT / report_path
    curator_path = Path(args.curator_config)
    if not curator_path.is_absolute():
        curator_path = REPO_ROOT / curator_path

    try:
        expected = load_candidates(curator_path)
    except OSError as exc:
        sys.stderr.write(f"ERROR: 无法读取候选配置 {curator_path}: {exc}\n")
        return EXIT_CHECK_FAIL if args.check else EXIT_PROBE_FAIL

    if not expected:
        sys.stderr.write("ERROR: 候选列表为空\n")
        return EXIT_CHECK_FAIL if args.check else EXIT_PROBE_FAIL

    if args.check:
        if not report_path.is_file():
            sys.stderr.write(f"ERROR: 报告不存在: {report_path}\n")
            return EXIT_CHECK_FAIL
        try:
            report = _load_json(report_path)
        except ValueError as exc:
            sys.stderr.write(f"ERROR: {exc}\n")
            return EXIT_CHECK_FAIL
        problems = check_report(report, expected, args.max_idle_days)
        if problems:
            for item in problems:
                sys.stderr.write(f"ERROR: {item}\n")
            return EXIT_CHECK_FAIL
        batch = report.get("first_batch") or []
        print(
            f"X_ACCOUNT_SMOKE_CHECK_OK candidates={len(expected)} "
            f"first_batch={len(batch)} report={report_path}"
        )
        return EXIT_OK

    twitter_bin = _twitter_bin()
    try:
        report = run_probe(
            expected,
            max_idle_days=args.max_idle_days,
            pacing_sec=args.pacing_sec,
            today=datetime.now(UTC).date(),
            twitter_bin=twitter_bin,
        )
    except GlobalProbeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        failed = build_report(
            [],
            max_idle_days=args.max_idle_days,
            errors=[{"kind": exc.kind, "handle": exc.handle, "message": str(exc)}],
        )
        write_report(report_path, failed)
        return EXIT_PROBE_FAIL
    except subprocess.TimeoutExpired as exc:
        sys.stderr.write(f"ERROR: CLI 超时: {exc}\n")
        return EXIT_PROBE_FAIL

    write_report(report_path, report)
    print(
        f"X_ACCOUNT_SMOKE_OK candidates={len(report['candidates'])} "
        f"first_batch={len(report['first_batch'])} report={report_path}"
    )
    if not report["first_batch"]:
        sys.stderr.write("ERROR: first_batch 为空（探测完成但无人合格）\n")
        return EXIT_CHECK_FAIL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
