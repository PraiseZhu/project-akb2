#!/usr/bin/env python3
"""Deterministic GitHub Actions minute-budget check.

Reads timeout-minutes from every job in .github/workflows/*.yml and the
expected_minutes_per_run / frequency_per_month constants in docs/ci-budget.md.
Does not call any GitHub billing API.

Exit 0 if estimated minutes < soft threshold; exit 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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
    jobs: dict[str, int] = {}
    current: str | None = None
    in_jobs = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        jm = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if jm:
            current = jm.group(1)
            continue
        tm = re.match(r"^    timeout-minutes:\s*(\d+)\s*$", line)
        if tm and current:
            jobs[current] = int(tm.group(1))
    return jobs


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

    total = 0
    rows: list[str] = []
    for path in workflow_files:
        jobs = job_timeouts(path)
        if not jobs:
            print(f"{path.name}: 没有任何 timeout-minutes", file=sys.stderr)
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
    pct = (margin / hard) * 100 if hard else 0.0
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
