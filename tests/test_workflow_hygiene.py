"""每个 workflow job 必须有 timeout-minutes，每个 workflow 必须有 concurrency。

漏配会让单个挂死 job 或同分支连推叠跑，把 Free 仓 2000 分钟/月烧穿。
本测试解析 YAML 断言这两件事；删掉任一 job 的 timeout-minutes 后本文件必须红。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
TESTS_JOB_TIMEOUT_MAX = 30
OTHER_JOB_TIMEOUT_MAX = 10


@pytest.fixture(scope="module")
def loaded_workflows() -> dict[str, dict]:
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files, f"未找到任何 workflow: {WORKFLOWS}"
    out: dict[str, dict] = {}
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} 顶层不是 mapping"
        out[path.name] = data
    return out


def test_every_workflow_has_concurrency(loaded_workflows: dict[str, dict]) -> None:
    missing = [name for name, data in loaded_workflows.items() if "concurrency" not in data]
    assert not missing, f"以下 workflow 缺少顶层 concurrency: {missing}"


def test_every_job_has_timeout_minutes(loaded_workflows: dict[str, dict]) -> None:
    missing: list[str] = []
    for wf_name, data in loaded_workflows.items():
        jobs = data.get("jobs")
        assert isinstance(jobs, dict) and jobs, f"{wf_name} 没有 jobs"
        for name, job in jobs.items():
            assert isinstance(job, dict), f"{wf_name} job {name} 不是 mapping"
            timeout = job.get("timeout-minutes")
            if not isinstance(timeout, int) or timeout <= 0:
                missing.append(f"{wf_name}:{name}")
    assert not missing, f"以下 job 缺少正整数 timeout-minutes: {missing}"


def test_job_timeout_caps(loaded_workflows: dict[str, dict]) -> None:
    over: list[str] = []
    for wf_name, data in loaded_workflows.items():
        for name, job in data["jobs"].items():
            timeout = job.get("timeout-minutes")
            assert isinstance(timeout, int), f"{wf_name}:{name} 缺少 timeout-minutes"
            cap = (
                TESTS_JOB_TIMEOUT_MAX
                if wf_name == "ci.yml" and name == "tests"
                else OTHER_JOB_TIMEOUT_MAX
            )
            if timeout > cap:
                over.append(f"{wf_name}:{name}={timeout}>{cap}")
    assert not over, f"timeout-minutes 超过上限: {over}"


def test_budget_script_accepts_current_workflows() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_ci_budget.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "estimated_month_minutes=" in result.stdout
