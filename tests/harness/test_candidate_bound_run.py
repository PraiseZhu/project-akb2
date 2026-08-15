"""sc-harness-candidate-bind 验收测试：候选绑定 wrapper 的执行语义。

核心守卫：
  - 执行前后双校验（HEAD == head_sha ∧ dirty=0 ∧ external_hashes 逐一相等），
    两次都过才透传被包命令退出码（关闭 check→use TOCTOU 窗口）
  - 被包命令非 0 退出码原样透传，不吞不改写
  - wrapper 输出与执行报告只写仓外 --report-dir，不新增仓内写入面
  - 读入 artifact 先做格式世代校验：缺 candidate_scope / 含废弃键
    anchor_hashes、pending_paths / external_hashes 混入仓内路径
    → STALE_ARTIFACT_FORMAT 非 0（消费侧旧口径拒收）
  - dirty 观察面固定为不带 --ignored 的 porcelain：.gitignore 覆盖的缓存
    不触发红（12 处仓内 pytest 场景的诚实声称）
  - wrapper 不改被包命令 argv（透传原样）

T1 残余（已声明，不冒充覆盖）：仓内文件一次调用内改后逐字节恢复、
porcelain 仍干净 → dirty 与 external_hashes 均检测不到；消除需只读
快照，不在本轮。测试只断言观察面语义，不伪造 T2 声称。

测试鉴别力纪律：零收集即红由 verify 层把关；本文件不使用任何 skip。
"""

from __future__ import annotations

import json

import pytest

ANCHORS = ["scripts/alpha.py", ".claude.json"]


def _prepared(mini_repo):
    """emit 一个干净 artifact（.claude.json 已就位），返回 (artifact_path, run_env, report_dir)。"""
    write_manifest(mini_repo, [ANCHORS])
    (mini_repo["home"] / ".claude.json").write_text("{}\n")
    r, out = emit(mini_repo)
    assert r.returncode == 0, r.stderr
    run_env = {**mini_repo["env"], "repo": mini_repo["repo"]}
    report_dir = mini_repo["tmp_path"] / "report"
    return out, run_env, report_dir


def _run(run_env, out, report_dir, *cmd: str):
    return run_wrapper(
        run_env,
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", *cmd,
    )


# ---------------------------------------------------------------------------
# 快乐路径与退出码透传
# ---------------------------------------------------------------------------


def test_clean_run_passes_and_propagates_zero(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode == 0, rc.stderr


def test_nonzero_cmd_exit_propagated_verbatim(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    rc = _run(run_env, out, report_dir, "sh", "-c", "exit 7")
    assert rc.returncode == 7


def test_cmd_argv_passed_through_unchanged(mini_repo):
    # 被包命令的 argv 原样透传：echo 收到的参数与传入完全一致
    out, run_env, report_dir = _prepared(mini_repo)
    marker = mini_repo["tmp_path"] / "argv.json"
    argv = ["--scope", "x", "--emit-candidate", "/etc/evil"]
    rc = _run(run_env, out, report_dir, "sh", "-c",
              'echo "$#" > "$1"; printf "%s\\n" "$@" >> "$1"',
              "sh", str(marker), *argv)
    assert rc.returncode == 0, rc.stderr
    lines = marker.read_text().splitlines()
    assert lines[0] == str(1 + len(argv))  # $@ = marker + argv（sh -c 的 script 占 $0）
    # wrapper 未吞/未改被包命令的任何参数
    assert lines[-len(argv):] == argv


# ---------------------------------------------------------------------------
# 执行前校验（PRE_CHECK_FAIL）
# ---------------------------------------------------------------------------


def test_pre_check_fails_on_head_drift(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    (mini_repo["repo"] / "extra.txt").write_text("x\n")
    mini_repo["git"]("add", "-A")
    mini_repo["git"]("commit", "-qm", "advance HEAD")
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    assert "PRE_CHECK_FAIL" in rc.stderr
    assert "head_sha" in rc.stderr


def test_pre_check_fails_on_dirty_worktree(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    (mini_repo["repo"] / "dirty.txt").write_text("uncommitted\n")
    (mini_repo["repo"] / "dirty2.txt").write_text("uncommitted\n")
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    assert "PRE_CHECK_FAIL" in rc.stderr
    assert "dirty" in rc.stderr
    # 失败信息必须带 expected/actual（可诊断，与 head/external 同口径）
    assert "expected dirty=0" in rc.stderr
    assert "actual=2" in rc.stderr


def test_pre_check_fails_on_external_hash_mismatch(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    (mini_repo["home"] / ".claude.json").write_text('{"changed": 1}\n')
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    assert str((mini_repo["home"] / ".claude.json").resolve()) in rc.stderr


# ---------------------------------------------------------------------------
# 执行后校验（POST_CHECK_FAIL）——TOCTOU 关闭的核心证据
# ---------------------------------------------------------------------------


def test_post_check_fails_when_cmd_dirties_worktree(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    # 被包命令在执行期间往仓内写 git 可见文件 → 执行后校验必红
    rc = _run(run_env, out, report_dir, "sh", "-c",
              f"echo x > '{mini_repo['repo']}/during.txt'")
    assert rc.returncode != 0
    assert "POST_CHECK_FAIL" in rc.stderr
    assert "dirty" in rc.stderr
    # POST 侧同样必须带 expected/actual（一个 git 可见改动）
    assert "expected dirty=0" in rc.stderr
    assert "actual=1" in rc.stderr


def test_post_check_fails_when_cmd_modifies_external_anchor(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    claude_json = mini_repo["home"] / ".claude.json"
    # 被包命令篡改仓外 anchor → 执行后 external 比对必红并指出路径
    rc = _run(run_env, out, report_dir, "sh", "-c",
              f"echo '{{\"hijack\": 1}}' > '{claude_json}'")
    assert rc.returncode != 0
    assert "POST_CHECK_FAIL" in rc.stderr
    assert str(claude_json.resolve()) in rc.stderr


def test_post_check_detects_transient_modify_then_restore_differs(mini_repo):
    """改后恢复但内容不同 → 端点相等不足以放行，文件级 hash 必须抓到。"""
    out, run_env, report_dir = _prepared(mini_repo)
    claude_json = mini_repo["home"] / ".claude.json"
    rc = _run(run_env, out, report_dir, "sh", "-c",
              f"printf '{{\"n\": 1}}' > '{claude_json}'; sleep 0.1; "
              f"printf '{{\"n\": 2}}' > '{claude_json}'")
    # .claude.json 终态内容与 artifact 记录的 hash 不同 → 红（即使有中途改写）
    assert rc.returncode != 0


# ---------------------------------------------------------------------------
# dirty 观察面：ignored 缓存不误红（T1 诚实边界）
# ---------------------------------------------------------------------------


def test_ignored_writes_do_not_trip_dirty(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    # 预置 .gitignore 覆盖 __pycache__/ 并 commit（保持 emit 后 HEAD 不变）
    gitignore = mini_repo["repo"] / ".gitignore"
    gitignore.write_text("__pycache__/\n")
    mini_repo["git"]("add", "-A")
    mini_repo["git"]("commit", "-qm", "add gitignore")

    # manifest 未变但 artifact 的 head_sha 已漂移 → 先 re-emit 保持合法
    r2, out2 = emit(mini_repo)
    assert r2.returncode == 0, r2.stderr
    # 被包命令往仓内写 ignored 缓存 → 不构成 git 可见改动 → 放行
    rc = _run(run_env, out2, report_dir, "sh", "-c",
              f"mkdir -p '{mini_repo['repo']}/__pycache__'; "
              f"echo cache > '{mini_repo['repo']}/__pycache__/x.pyc'")
    assert rc.returncode == 0, rc.stderr


def test_untracked_non_ignored_write_still_trips(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    rc = _run(run_env, out, report_dir, "sh", "-c",
              f"echo log > '{mini_repo['repo']}/run.log'")
    assert rc.returncode != 0
    assert "POST_CHECK_FAIL" in rc.stderr


# ---------------------------------------------------------------------------
# 格式世代（消费侧旧口径拒收）
# ---------------------------------------------------------------------------


def test_stale_format_missing_candidate_scope(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    artifact = json.loads(out.read_text())
    del artifact["candidate_scope"]
    out.write_text(json.dumps(artifact))
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


def test_stale_format_deprecated_anchor_hashes(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    artifact = json.loads(out.read_text())
    artifact["anchor_hashes"] = {}
    out.write_text(json.dumps(artifact))
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


def test_stale_format_deprecated_pending_paths(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    artifact = json.loads(out.read_text())
    artifact["pending_paths"] = []
    out.write_text(json.dumps(artifact))
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


def test_stale_format_external_hashes_repo_path(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    artifact = json.loads(out.read_text())
    artifact["external_hashes"]["scripts/alpha.py"] = "0" * 64
    out.write_text(json.dumps(artifact))
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


def test_stale_format_bare_candidate_json_name(mini_repo):
    # 裸 candidate.json 不得成为实际消费路径（文件名须与 scope 配对）
    out, run_env, report_dir = _prepared(mini_repo)
    bare = mini_repo["out"] / "candidate.json"
    bare.write_text(out.read_text())
    rc = _run(run_env, bare, report_dir, "true")
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


# ---------------------------------------------------------------------------
# 报告与写入面
# ---------------------------------------------------------------------------


def test_report_written_outside_repo_only(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode == 0, rc.stderr
    report = report_dir / "candidate-run-report.json"
    assert report.exists()
    data = json.loads(report.read_text())
    assert data["cmd_rc"] == 0
    assert data["wrapper_exit"] == 0
    assert data["pre"]["head_ok"] and data["pre"]["dirty_ok"]
    assert data["post"]["head_ok"] and data["post"]["dirty_ok"]
    # 报告目录在仓外（按路径组件判，不做字符串前缀——/repo 与 /report 前 4 字符同）
    try:
        report.relative_to(mini_repo["repo"].resolve())
        inside = True
    except ValueError:
        inside = False
    assert not inside


def test_report_written_on_pre_check_fail(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    (mini_repo["repo"] / "dirty.txt").write_text("x\n")
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode != 0
    report = report_dir / "candidate-run-report.json"
    data = json.loads(report.read_text())
    assert data["wrapper_exit"] != 0
    assert data["cmd_rc"] is None  # 被包命令根本没跑


def test_report_dir_inside_repo_rejected(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    inside = mini_repo["repo"] / "reports"
    rc = _run(run_env, out, inside, "true")
    assert rc.returncode != 0
    assert "仓外" in rc.stderr or "report" in rc.stderr
    assert not inside.exists()


def test_report_dir_created_if_missing(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    nested = mini_repo["tmp_path"] / "deep" / "nested" / "report"
    rc = _run(run_env, out, nested, "true")
    assert rc.returncode == 0, rc.stderr
    assert (nested / "candidate-run-report.json").exists()


# ---------------------------------------------------------------------------
# manifest 漂移（run 侧同样按当前 manifest 重算）
# ---------------------------------------------------------------------------


def test_run_detects_manifest_change_without_reemit(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    rc = _run(run_env, out, report_dir, "true")
    assert rc.returncode == 0, rc.stderr
    write_manifest(mini_repo, [["scripts/alpha.py", "scripts/another.py",
                                ".claude.json"]])
    rc2 = _run(run_env, out, report_dir, "true")
    assert rc2.returncode != 0
    assert "MANIFEST_DRIFT" in rc2.stderr


# ---------------------------------------------------------------------------
# 用法错误
# ---------------------------------------------------------------------------


def test_missing_cmd_usage_error(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    rc = run_wrapper(
        run_env,
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
    )
    assert rc.returncode != 0


def test_mutually_exclusive_modes_rejected(mini_repo):
    out, run_env, report_dir = _prepared(mini_repo)
    rc = run_wrapper(
        run_env,
        "--emit-candidate", str(mini_repo["out"] / "x.candidate.json"),
        "--scope", "x",
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode != 0


# ---------------------------------------------------------------------------
# 自包含黑盒环境（本仓 tests/harness 无 conftest，fixture 与 helper 内联）
# ---------------------------------------------------------------------------

import os  # noqa: E402
import subprocess  # noqa: E402
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

SCRIPT = _Path(__file__).parent.parent.parent / "scripts" / "candidate_bound_run.py"


@pytest.fixture()
def mini_repo(tmp_path):
    """自建迷你 git 仓库 + 白名单仓外树 + 测试 manifest。

    AKB2_CBR_* 是 wrapper 的正规注入协议，均经 env 传入子进程，
    不读真实 ~/.claude.json 与 ~/AI-Agent/Claude/capabilities。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    caps = tmp_path / "caps"
    out = tmp_path / "out"
    (home / "sub").mkdir(parents=True)
    caps.mkdir()

    def git(*args, cwd=repo):
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=cwd
        )

    assert git("init", "-q").returncode == 0
    assert git("config", "user.email", "t@example.com").returncode == 0
    assert git("config", "user.name", "t").returncode == 0
    (repo / "README.md").write_text("seed\n")
    assert git("add", "-A").returncode == 0
    assert git("commit", "-qm", "seed").returncode == 0

    env = {
        "AKB2_CBR_HOME": str(home),
        "AKB2_CBR_CAPABILITIES_ROOT": str(caps),
        "AKB2_CBR_TASK_MANIFEST": str(tmp_path / "task-manifest.json"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    return {"repo": repo, "home": home, "caps": caps, "out": out,
            "tmp_path": tmp_path, "env": env, "git": git}


def run_wrapper(env, *args, cwd=None):
    """以注入 env 子进程跑 wrapper（env 内可带 repo 键作 cwd）。"""
    full_env = dict(env)
    repo = full_env.pop("repo", None)
    return subprocess.run(
        [_sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=full_env,
        cwd=str(cwd or repo or _Path.cwd()),
    )


def write_manifest(handle, anchors_by_sc):
    """按 SC 分组写测试 task-manifest（派生规则取全部 SC 的并集）。"""
    manifest = {
        "schema_version": "1.0.0",
        "scs": [
            {"id": f"sc-{i}", "anchor_paths": anchors}
            for i, anchors in enumerate(anchors_by_sc)
        ],
    }
    path = _Path(handle["env"]["AKB2_CBR_TASK_MANIFEST"])
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def emit(handle, scope="p0-harness"):
    """emit 一个候选 artifact 到仓外 out/ 目录。"""
    out = handle["out"] / f"{scope}.candidate.json"
    env = {**handle["env"], "repo": handle["repo"]}
    r = run_wrapper(env, "--emit-candidate", str(out), "--scope", scope)
    return r, out
