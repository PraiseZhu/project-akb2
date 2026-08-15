"""每个 workflow job 必须有 timeout-minutes，每个 workflow 必须有 concurrency。

漏配会让单个挂死 job 或同分支连推叠跑，把 Free 仓 2000 分钟/月烧穿。
本测试解析 YAML 断言这两件事；删掉任一 job 的 timeout-minutes 后本文件必须红。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"


def _workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files, f"未找到任何 workflow: {WORKFLOWS}"
    return files


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} 顶层不是 mapping"
    return data


def test_every_workflow_has_concurrency() -> None:
    missing = []
    for path in _workflow_files():
        data = _load(path)
        if "concurrency" not in data:
            missing.append(path.name)
    assert not missing, f"以下 workflow 缺少顶层 concurrency: {missing}"


def test_every_job_has_timeout_minutes() -> None:
    missing: list[str] = []
    for path in _workflow_files():
        data = _load(path)
        jobs = data.get("jobs")
        assert isinstance(jobs, dict) and jobs, f"{path.name} 没有 jobs"
        for name, job in jobs.items():
            assert isinstance(job, dict), f"{path.name} job {name} 不是 mapping"
            timeout = job.get("timeout-minutes")
            if not isinstance(timeout, int) or timeout <= 0:
                missing.append(f"{path.name}:{name}")
    assert not missing, f"以下 job 缺少正整数 timeout-minutes: {missing}"


def test_tests_job_timeout_at_most_30() -> None:
    ci = _load(WORKFLOWS / "ci.yml")
    tests = ci["jobs"].get("tests")
    assert isinstance(tests, dict), "ci.yml 缺少 tests job"
    timeout = tests.get("timeout-minutes")
    assert isinstance(timeout, int), "ci.yml tests 缺少 timeout-minutes"
    assert timeout <= 30, f"ci.yml tests timeout-minutes={timeout} 超过 30"


def test_budget_script_accepts_current_workflows() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_ci_budget.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "estimated_month_minutes=" in result.stdout


def test_non_tests_jobs_timeout_at_most_10() -> None:
    over: list[str] = []
    for path in _workflow_files():
        data = _load(path)
        for name, job in data["jobs"].items():
            if path.name == "ci.yml" and name == "tests":
                continue
            timeout = job["timeout-minutes"]
            if timeout > 10:
                over.append(f"{path.name}:{name}={timeout}")
    assert not over, f"非 tests job 的 timeout-minutes 超过 10: {over}"
