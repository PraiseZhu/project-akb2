#!/usr/bin/env python3
"""Deterministic GitHub Actions minute-budget check.

Reads timeout-minutes via PyYAML from every job in .github/workflows/*.yml and
the expected_minutes_per_run / frequency_per_month constants in
docs/ci-budget.md. Does not call any GitHub billing API.

Exit 0 if estimated minutes < soft threshold; exit 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
BUDGET_DOC = REPO / "docs" / "ci-budget.md"

SOFT_RE = re.compile(r"^soft_threshold_minutes:\s*(\d+)\s*$", re.MULTILINE)
HARD_RE = re.compile(r"^hard_quota_minutes:\s*(\d+)\s*$", re.MULTILINE)
MAP_ITEM_RE = re.compile(r"^  ([A-Za-z0-9._-]+\.yml):\s*(\d+)\s*$")


def parse_map(text: str, heading: str) -> dict[str, int]:
    lines = text.splitlines()
    out: dict[str, int] = {}
    in_block = False
    for line in lines:
        if line.strip() == f"{heading}:":
            in_block = True
            continue
        if in_block:
            m = MAP_ITEM_RE.match(line)
            if m:
                out[m.group(1)] = int(m.group(2))
                continue
            if line.startswith("  "):
                raise SystemExit(f"docs/ci-budget.md {heading} 含非法行: {line!r}")
            break
    if not out:
        raise SystemExit(f"docs/ci-budget.md 缺少非空 {heading} 段")
    return out


def job_timeouts(path: Path) -> dict[str, int]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path.name}: 顶层不是 mapping")
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise SystemExit(f"{path.name}: 没有 jobs")
    out: dict[str, int] = {}
    missing: list[str] = []
    for name, job in jobs.items():
        if not isinstance(job, dict):
            missing.append(name)
            continue
        timeout = job.get("timeout-minutes")
        if not isinstance(timeout, int) or timeout <= 0:
            missing.append(name)
            continue
        out[name] = timeout
    if missing:
        raise SystemExit(f"{path.name}: job 缺少正整数 timeout-minutes: {missing}")
    return out


def main() -> int:
    if not BUDGET_DOC.is_file():
        print("missing docs/ci-budget.md", file=sys.stderr)
        return 1
    if not WORKFLOWS.is_dir():
        print("missing .github/workflows", file=sys.stderr)
        return 1

    text = BUDGET_DOC.read_text(encoding="utf-8")
    soft_m = SOFT_RE.search(text)
    hard_m = HARD_RE.search(text)
    if not soft_m or not hard_m:
        print(
            "docs/ci-budget.md 缺少 soft_threshold_minutes 或 hard_quota_minutes",
            file=sys.stderr,
        )
        return 1
    soft = int(soft_m.group(1))
    hard = int(hard_m.group(1))
    if soft <= 0 or hard <= 0 or soft > hard:
        print(
            f"docs/ci-budget.md 阈值非法: soft={soft} hard={hard}",
            file=sys.stderr,
        )
        return 1
    freqs = parse_map(text, "frequency_per_month")
    expected = parse_map(text, "expected_minutes_per_run")

    workflow_files = sorted(WORKFLOWS.glob("*.yml"))
    names = {p.name for p in workflow_files}
    for label, mapping in (("frequency_per_month", freqs), ("expected_minutes_per_run", expected)):
        missing = sorted(names - set(mapping))
        extra = sorted(set(mapping) - names)
        if missing or extra:
            print(
                f"{label} 与 workflow 文件不一致: 缺 {missing} 多 {extra}",
                file=sys.stderr,
            )
            return 1
        zeros = sorted(name for name, value in mapping.items() if value <= 0)
        if zeros:
            print(f"{label} 必须为正整数，发现 ≤0: {zeros}", file=sys.stderr)
            return 1

    total = 0
    rows: list[str] = []
    for path in workflow_files:
        try:
            jobs = job_timeouts(path)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 1
        cap = sum(jobs.values())
        exp = expected[path.name]
        if exp > cap:
            print(
                f"{path.name}: expected_minutes_per_run={exp} > timeout 上界 {cap}",
                file=sys.stderr,
            )
            return 1
        month = exp * freqs[path.name]
        total += month
        rows.append(
            f"{path.name}: jobs={jobs} timeout_cap={cap} "
            f"expected={exp} freq={freqs[path.name]} month={month}"
        )

    margin = hard - total
    pct = (margin / hard) * 100
    print("AKB2 CI budget check")
    for row in rows:
        print(row)
    print(f"estimated_month_minutes={total}")
    print(f"soft_threshold_minutes={soft}")
    print(f"hard_quota_minutes={hard}")
    print(f"safety_margin_minutes={margin} ({pct:.1f}%)")
    if total >= soft:
        print(f"FAIL: 估算月耗 {total} >= 软阈值 {soft}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
