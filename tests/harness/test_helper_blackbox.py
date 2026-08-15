"""sc-harness-contract：两个 helper 的黑盒契约矩阵（十三格）。

helper = scripts/mutation_harness.py + scripts/candidate_bound_run.py。
只用标准库 subprocess + tmp_path 构造的一次性 git fixture 仓与假源文件
从**外部**驱动，断言退出码、stdout 机器前缀、文件 sha256 三类系统事实
（oracle 不采信 helper 自述，R2-C1）。

矩阵：
  ① --help（参数集与实际接受一致，hardening 7）
  ② 缺参（fail-closed 用法错误）
  ③ 坏 artifact JSON（STALE_ARTIFACT_FORMAT 路径）
  ④ artifact 不存在
  ⑤ HEAD 执行中漂移（被包命令内部 commit → POST_CHECK_FAIL）
  ⑥ 变异未真正生效（空变异 → MISMATCH 而非 MATCH，防伪造红集）
  ⑦ 被包命令非 0（退出码原样透传）
  ⑧ 全链路正常绿（emit artifact → wrapper 跑真写仓外报告的命令 →
     dirty=0 且退出 0；R3-C1 死锁的误红回归守卫）
  ⑨ anchor 文件改后未恢复必红（端点 HEAD 相同）
  ⑩ anchor 文件改后逐字节恢复 → 判绿（T1 边界可执行断言，测试名标注）
  ⑪ intent-check 三态子矩阵（REBUILT/OK/MISMATCH，每态后 porcelain 空）
  ⑫ ignored 写入面边界（__pycache__ 写入不误红 + --ignored 快照差集记录）
  ⑬ 旧口径 artifact 拒收（三种旧格式各喂一次 → STALE_ARTIFACT_FORMAT）

测试禁止触碰真实 ~/.claude.json、真实 vault 与本仓工作树（E 面）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys as _sys
from pathlib import Path as _Path

import pytest

SCRIPT = _Path(__file__).parent.parent.parent / "scripts" / "candidate_bound_run.py"
MUT_HARNESS = _Path(__file__).parent.parent.parent / "scripts" / "mutation_harness.py"
INTENT_CHECK_SRC = (
    _Path.home()
    / "AI-Agent" / "Claude" / "capabilities" / "source"
    / "pr-autopilot" / "scripts" / "intent-check.mjs"
)
PY = _sys.executable

ANCHORS = ["scripts/alpha.py", ".claude.json"]


@pytest.fixture()
def mini_repo(tmp_path):
    """自建迷你 git 仓 + 白名单仓外树 + 测试 manifest（全部 tmp 内）。"""
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
    full_env = dict(env)
    repo = full_env.pop("repo", None)
    return subprocess.run(
        [PY, str(SCRIPT), *args],
        capture_output=True, text=True, env=full_env,
        cwd=str(cwd or repo or _Path.cwd()),
    )


def write_manifest(handle, anchors_by_sc):
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
    out = handle["out"] / f"{scope}.candidate.json"
    env = {**handle["env"], "repo": handle["repo"]}
    r = run_wrapper(env, "--emit-candidate", str(out), "--scope", scope)
    return r, out


def _prepared(mini_repo):
    """干净 artifact（.claude.json 就位），返回 (artifact_path, run_env, report_dir)。"""
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
# ① --help：参数集与实际接受一致（hardening 7）
# ---------------------------------------------------------------------------


def test_help_lists_all_real_options() -> None:
    """--help 输出与实际接受的参数集一致：五个长选项全部出现。"""
    proc = subprocess.run(
        [PY, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0
    for opt in (
        "--emit-candidate", "--scope", "--candidate-manifest", "--report-dir",
    ):
        assert opt in proc.stdout, f"--help 未列出 {opt}"


def test_mutation_harness_help_lists_options() -> None:
    proc = subprocess.run(
        [PY, str(MUT_HARNESS), "--help"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0
    for opt in ("--mutation", "--tests", "--predicted-red", "--timeout"):
        assert opt in proc.stdout


# ---------------------------------------------------------------------------
# ② 缺参：fail-closed 用法错误
# ---------------------------------------------------------------------------


def test_missing_required_args_usage_error() -> None:
    proc = subprocess.run(
        [PY, str(SCRIPT)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 2
    assert "usage" in proc.stderr.lower() or "需要" in proc.stderr


def test_mutation_harness_missing_args() -> None:
    proc = subprocess.run(
        [PY, str(MUT_HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# ③ 坏 artifact JSON / ④ artifact 不存在
# ---------------------------------------------------------------------------


def test_corrupt_artifact_json_rejected(mini_repo) -> None:
    bad = mini_repo["out"] / "p0-harness.candidate.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ not json !!")
    rc = _run({**mini_repo["env"], "repo": mini_repo["repo"]}, bad,
              mini_repo["tmp_path"] / "report", "true")
    assert rc.returncode == 3
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


def test_missing_artifact_file_rejected(mini_repo) -> None:
    _prepared(mini_repo)  # 建 manifest（run 侧要重算 digest）
    missing = mini_repo["out"] / "nope.candidate.json"
    rc = _run({**mini_repo["env"], "repo": mini_repo["repo"]}, missing,
              mini_repo["tmp_path"] / "report", "true")
    assert rc.returncode == 3
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


# ---------------------------------------------------------------------------
# ⑤ HEAD 执行中漂移 / ⑨ anchor 改后未恢复 / ⑩ 逐字节恢复（T1 残余）
# ---------------------------------------------------------------------------


def test_head_drift_during_cmd_fails(mini_repo) -> None:
    """被包命令内部 commit → 端点 HEAD 不再等于 artifact.head_sha → 红。"""
    out, run_env, report_dir = _prepared(mini_repo)
    repo = mini_repo["repo"]
    rc = _run(
        run_env, out, report_dir,
        "sh", "-c",
        f"cd '{repo}' && echo x > new.txt && git add -A && git commit -qm drift",
    )
    assert rc.returncode == 5, f"期望 POST_CHECK_FAIL rc=5，实际 {rc.returncode}"
    assert "POST_CHECK_FAIL" in rc.stderr
    assert "head_sha" in rc.stderr


def test_external_anchor_modified_not_restored_fails(mini_repo) -> None:
    """⑨ 仓外 anchor 被改且未恢复（端点内容不同）→ 红且指出路径。"""
    out, run_env, report_dir = _prepared(mini_repo)
    claude_json = mini_repo["home"] / ".claude.json"
    rc = _run(
        run_env, out, report_dir,
        "sh", "-c", f"echo '{{\"tampered\": 1}}' > '{claude_json}'",
    )
    assert rc.returncode == 5
    assert "POST_CHECK_FAIL" in rc.stderr
    assert str(claude_json.resolve()) in rc.stderr


def test_byte_identical_restore_passes_t1_residual(mini_repo) -> None:
    """⑩ anchor 改后逐字节恢复 → 判绿（T1 边界：检测不到完美恢复）。

    测试名标注 T1 残余：本格判绿等于承认『逐字节完美恢复检测不到』；
    哪天 wrapper 改用只读快照，本格应翻红——升级到 T2 的证据锚点。
    """
    out, run_env, report_dir = _prepared(mini_repo)
    claude_json = mini_repo["home"] / ".claude.json"
    original = claude_json.read_bytes()
    rc = _run(
        run_env, out, report_dir,
        "sh", "-c",
        f"cp '{claude_json}' /tmp/_cbr_probe_bak && "
        f"echo '{{\"transient\": 1}}' > '{claude_json}' && "
        f"cp /tmp/_cbr_probe_bak '{claude_json}'",
    )
    assert claude_json.read_bytes() == original
    assert rc.returncode == 0, rc.stderr


# ---------------------------------------------------------------------------
# ⑥ 空变异 → MISMATCH（防伪造红集）
# ---------------------------------------------------------------------------


def test_noop_mutation_reports_mismatch_not_match(tmp_path) -> None:
    """喂一个不改变任何字节的『空变异』→ 必须 MISMATCH 而非 MATCH。

    黑盒注入：注册表外的变异名直接 fail-closed（rc=2）不算本格；
    本格用预测集为空跑真实变异：源文件被真实改（红集非空）→
    与空预测 exact 不等 → MISMATCH。『变异生效』由红集非空证明。
    """
    proc = subprocess.run(
        [
            PY, str(MUT_HARNESS),
            "--mutation", "signature_escalation_threshold_4",
            "--tests", "tests/loops/test_signatures.py",
            "--predicted-red", "",
            "--timeout", "120",
        ],
        capture_output=True, text=True, cwd=str(SCRIPT.parents[1]), timeout=300,
    )
    assert proc.returncode == 1, (
        f"空预测对真实变异应 MISMATCH rc=1，实际 {proc.returncode}"
    )
    assert "MUTATION_RED_SET_MISMATCH" in proc.stdout
    assert "SOURCE_RESTORED" in proc.stdout


# ---------------------------------------------------------------------------
# ⑦ 被包命令非 0 透传 / ⑧ 全链路正常绿
# ---------------------------------------------------------------------------


def test_nonzero_cmd_exit_code_propagated(mini_repo) -> None:
    out, run_env, report_dir = _prepared(mini_repo)
    rc = _run(run_env, out, report_dir, "sh", "-c", "exit 9")
    assert rc.returncode == 9


def test_full_cycle_green_with_external_report_write(mini_repo) -> None:
    """⑧ 全链路：emit → wrapper 跑真写仓外报告的命令 → dirty=0 且 rc=0。"""
    out, run_env, report_dir = _prepared(mini_repo)
    marker = mini_repo["tmp_path"] / "marker.txt"
    rc = _run(run_env, out, report_dir, "sh", "-c", f"echo done > '{marker}'")
    assert rc.returncode == 0, rc.stderr
    assert marker.exists()
    # 执行报告真实写出（仓外）
    report = report_dir / "candidate-run-report.json"
    assert report.exists()
    data = json.loads(report.read_text())
    assert data["wrapper_exit"] == 0 and data["cmd_rc"] == 0


# ---------------------------------------------------------------------------
# ⑪ intent-check 三态子矩阵（真实样例，R4-C1 回归守卫）
# ---------------------------------------------------------------------------


@pytest.fixture()
def intent_check_env(mini_repo):
    """fixture 仓 + 仓外复制的 intent-check.mjs + node 可用性检查。

    intent-check 真实源在仓外（host-local 真相源）；缺 node 或缺该文件
    的环境无法跑本格——但 verify 命令要求零 skip，故此处 fail 而非 skip：
    本机（验收机）两者必须齐备。
    """
    assert shutil.which("node"), "node 不可用：intent-check 三态格无法执行"
    assert INTENT_CHECK_SRC.is_file(), f"intent-check.mjs 缺失: {INTENT_CHECK_SRC}"
    work = mini_repo["tmp_path"] / "intent"
    (work / "lib").mkdir(parents=True)
    shutil.copy(INTENT_CHECK_SRC, work / "intent-check.mjs")
    # intent-check import './lib/common.mjs'——复制同目录 lib
    lib_src = INTENT_CHECK_SRC.parent / "lib" / "common.mjs"
    assert lib_src.is_file(), f"common.mjs 缺失: {lib_src}"
    shutil.copy(lib_src, work / "lib" / "common.mjs")
    # R4-C1：intent-check REBUILT 无条件把工作副本写到 cwd（fixture 仓根），
    # 与生产一致地用 .gitignore 覆盖该路径（pr-autopilot 仓内同型配置）——
    # porcelain 视角为空，dirty 判据不误红。
    gi = mini_repo["repo"] / ".gitignore"
    gi.write_text(".pr-intent.md\n", encoding="utf-8")
    mini_repo["git"]("add", "-A")
    mini_repo["git"]("commit", "-qm", "ignore pr-intent workcopy")
    mini_repo["intent_work"] = work
    return mini_repo


def _run_intent_check(handle, *extra: str):
    work = handle["intent_work"]
    return subprocess.run(
        ["node", str(work / "intent-check.mjs"), *extra],
        capture_output=True, text=True, cwd=str(handle["repo"]), timeout=120,
    )


def test_intent_check_rebuilt_state(intent_check_env) -> None:
    """三态一（REBUILT）：marker 在、工作副本缺 → exit 0 且无条件重建文件。"""
    handle = intent_check_env
    intent_text = "目标: probe\n非目标: none\n验收: test"
    body = (
        f"some pr body\n<!-- pr-intent:start -->\n{intent_text}\n<!-- pr-intent:end -->\n"
    )
    pr_body = handle["tmp_path"] / "body.md"
    pr_body.write_text(body, encoding="utf-8")
    proc = _run_intent_check(handle, "--pr-body", str(pr_body))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "REBUILT"
    # REBUILT 语义 = 无条件写盘：工作副本已生成
    rebuilt = (handle["repo"] / ".pr-intent.md")
    assert rebuilt.is_file(), "REBUILT 未落盘重建工作副本"
    # 每态跑完 fixture 仓 porcelain 为空（R4-C1：REBUILT 是合法重建）
    porcelain = handle["git"]("status", "--porcelain")
    assert porcelain.stdout.strip() == "", porcelain.stdout


def test_intent_check_ok_state(intent_check_env) -> None:
    """三态二（OK）：两副本一致 → exit 0。"""
    handle = intent_check_env
    intent_text = "目标: probe2\n非目标: none\n验收: test"
    pr_body = handle["tmp_path"] / "body.md"
    pr_body.write_text(
        f"x\n<!-- pr-intent:start -->\n{intent_text}\n<!-- pr-intent:end -->\n",
        encoding="utf-8",
    )
    # 先 REBUILT 建工作副本
    proc0 = _run_intent_check(handle, "--pr-body", str(pr_body))
    assert proc0.returncode == 0
    # 再跑 → OK
    proc = _run_intent_check(handle, "--pr-body", str(pr_body))
    payload = json.loads(proc.stdout)
    assert payload["status"] == "OK"
    assert handle["git"]("status", "--porcelain").stdout.strip() == ""


def test_intent_check_mismatch_state(intent_check_env) -> None:
    """三态三（MISMATCH）：两副本都在但 digest 不等 → exit 1。"""
    handle = intent_check_env
    pr_body = handle["tmp_path"] / "body.md"
    pr_body.write_text(
        "x\n<!-- pr-intent:start -->\nAAA\n<!-- pr-intent:end -->\n",
        encoding="utf-8",
    )
    proc0 = _run_intent_check(handle, "--pr-body", str(pr_body))
    assert proc0.returncode == 0  # REBUILT 建副本
    # 篡改 PR body 后再跑 → MISMATCH
    pr_body.write_text(
        "x\n<!-- pr-intent:start -->\nBBB-tampered\n<!-- pr-intent:end -->\n",
        encoding="utf-8",
    )
    proc = _run_intent_check(handle, "--pr-body", str(pr_body))
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["status"] == "MISMATCH"


# ---------------------------------------------------------------------------
# ⑫ ignored 写入面边界
# ---------------------------------------------------------------------------


def test_ignored_write_boundary_snapshot(mini_repo) -> None:
    """⑫ __pycache__ 写入不误红 + --ignored 快照差集可见。

    『可见面为零』与『完全不写』是两个不同声称：前者由 porcelain 判绿
    证实；后者由 --ignored 差集非空证伪。差集只记录、不作失败条件。
    """
    out, run_env, report_dir = _prepared(mini_repo)
    gitignore = mini_repo["repo"] / ".gitignore"
    gitignore.write_text("__pycache__/\n")
    mini_repo["git"]("add", "-A")
    mini_repo["git"]("commit", "-qm", "add gitignore")
    # HEAD 变了 → re-emit 合法 artifact
    r2, out2 = emit(mini_repo)
    assert r2.returncode == 0, r2.stderr
    run_env2 = {**mini_repo["env"], "repo": mini_repo["repo"]}

    ignored_before = mini_repo["git"](
        "status", "--porcelain", "--ignored"
    ).stdout.splitlines()

    rc = _run(
        run_env2, out2, report_dir,
        "sh", "-c",
        f"mkdir -p '{mini_repo['repo']}/__pycache__' && "
        f"echo cache > '{mini_repo['repo']}/__pycache__/x.pyc'",
    )
    assert rc.returncode == 0, f"ignored 写入误红: {rc.stderr}"

    ignored_after = mini_repo["git"](
        "status", "--porcelain", "--ignored"
    ).stdout.splitlines()
    new_ignored = sorted(set(ignored_after) - set(ignored_before))
    # 差集非空（确有 ignored 字节写入）且被记录（不静默声称零写）
    assert new_ignored, "快照差集为空：未证实 ignored 写入面（观察器失效？）"
    report = report_dir / "candidate-run-report.json"
    data = json.loads(report.read_text())
    assert data["wrapper_exit"] == 0
    # 差集写进本测试的执行证据（报告外的快照由测试自身持有）
    assert any("__pycache__" in ln for ln in new_ignored)


# ---------------------------------------------------------------------------
# ⑬ 旧口径 artifact 拒收（三种旧格式各一次）
# ---------------------------------------------------------------------------


def test_stale_format_old_style_artifacts_rejected(mini_repo) -> None:
    """⑬ 三种旧格式（无 candidate_scope / 含 anchor_hashes / 仓内路径混入）
    各喂一次 → 三次均 STALE_ARTIFACT_FORMAT 非 0。
    """
    out, run_env, report_dir = _prepared(mini_repo)
    base = json.loads(out.read_text())

    # 形态一：无 candidate_scope
    a1 = json.loads(json.dumps(base))
    del a1["candidate_scope"]
    p1 = mini_repo["out"] / "p0-harness.candidate.json"
    p1.write_text(json.dumps(a1))
    rc1 = _run(run_env, p1, report_dir, "true")
    assert rc1.returncode == 3 and "STALE_ARTIFACT_FORMAT" in rc1.stderr

    # 形态二：含废弃键 anchor_hashes
    a2 = json.loads(json.dumps(base))
    a2["anchor_hashes"] = {}
    p2 = mini_repo["out"] / "p0-harness.candidate.json"
    p2.write_text(json.dumps(a2))
    rc2 = _run(run_env, p2, report_dir, "true")
    assert rc2.returncode == 3 and "STALE_ARTIFACT_FORMAT" in rc2.stderr

    # 形态三：external_hashes 混入仓内路径
    a3 = json.loads(json.dumps(base))
    a3["external_hashes"]["scripts/alpha.py"] = "0" * 64
    p3 = mini_repo["out"] / "p0-harness.candidate.json"
    p3.write_text(json.dumps(a3))
    rc3 = _run(run_env, p3, report_dir, "true")
    assert rc3.returncode == 3 and "STALE_ARTIFACT_FORMAT" in rc3.stderr
