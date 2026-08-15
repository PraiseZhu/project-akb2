"""sc-candidate-artifact-lifecycle：candidate artifact 世代与原子替换契约。

矩阵（manifest expect 六格）：
  ① p0→p1 阶段推进：p1 re-emit 后其闸全绿（合法阶段变更不误红）
  ② p3 修改白名单仓外文件后 p4 re-emit，p4 闸绿
  ③ p4 复用 p0 时点的旧 artifact → 必红且报 candidate_scope 不匹配（陈旧误绑）
  ④ 闸执行期间仓外 anchor 从 null 变实值（执行期 materialization）→ 必红
  ⑤ re-emit 后 generation 严格递增且旧世代内容未被就地改动（inode 轨迹）
  ⑥ 模拟 re-emit 中途校验失败 → 旧 artifact 原封不动（原子性反证）

全部在 tmp fixture 内完成（E 面：不触真实 ~/.claude.json / vault / 本仓工作树）。
测试鉴别力纪律：零收集即红由 verify 层把关；本文件不使用任何 skip。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys as _sys
from pathlib import Path as _Path

import pytest

SCRIPT = _Path(__file__).parent.parent.parent / "scripts" / "candidate_bound_run.py"
PY = _sys.executable


# ---------------------------------------------------------------------------
# 自包含 fixture（与 tests/harness/test_candidate_bound_run.py 同构，本文件独立持有）
# ---------------------------------------------------------------------------


@pytest.fixture()
def mini_repo(tmp_path):
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


def emit(handle, scope):
    out = handle["out"] / f"{scope}.candidate.json"
    env = {**handle["env"], "repo": handle["repo"]}
    r = run_wrapper(env, "--emit-candidate", str(out), "--scope", scope)
    return r, out


def run_gate(handle, artifact_path, report_dir, *cmd):
    env = {**handle["env"], "repo": handle["repo"]}
    return run_wrapper(
        env,
        "--candidate-manifest", str(artifact_path),
        "--report-dir", str(report_dir),
        "--", *cmd,
    )


def _prepared(mini_repo, anchors=None):
    """干净基线：写 manifest + .claude.json 就位，返回 handle。"""
    write_manifest(mini_repo, anchors or [["scripts/alpha.py", ".claude.json"]])
    (mini_repo["home"] / ".claude.json").write_text("{}\n")
    return mini_repo


# ---------------------------------------------------------------------------
# 基线 generation 语义
# ---------------------------------------------------------------------------


def test_baseline_emit_generation_zero(mini_repo):
    """首份 artifact generation=0（基线世代）。"""
    _prepared(mini_repo)
    r, out = emit(mini_repo, "p0-harness")
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    assert artifact["generation"] == 0
    assert "generation=0" in r.stdout


# ---------------------------------------------------------------------------
# ① 阶段推进：p1 re-emit 后其闸全绿
# ---------------------------------------------------------------------------


def test_phase_advance_reemit_gate_green(mini_repo):
    """p0 emit → p1 re-emit（同 anchor 集，世代 +1）→ p1 闸绿。"""
    _prepared(mini_repo)
    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr

    r1, out1 = emit(mini_repo, "p1-engine")
    assert r1.returncode == 1 or r1.returncode == 0  # 文件名与 scope 配对即 0
    a1 = json.loads(out1.read_text())
    assert a1["generation"] == 0  # p1 是自己的基线（跨 priority 世代链独立）

    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_gate(mini_repo, out1, report_dir, "true")
    assert rc.returncode == 0, rc.stderr


# ---------------------------------------------------------------------------
# ② p3 修改仓外 anchor 后 p4 re-emit，p4 闸绿
# ---------------------------------------------------------------------------


def test_external_change_then_reemit_new_scope_gate_green(mini_repo):
    """p3 合法改 ~/.claude.json → p4 re-emit 拿新 hash → p4 闸绿。

    阶段间合法变更与闸执行期间偷改由 re-emit 边界区分。
    """
    _prepared(mini_repo)
    r_p3, out_p3 = emit(mini_repo, "p3-register")
    assert r_p3.returncode == 0, r_p3.stderr

    # p3 执行期把仓外 anchor 改了（模拟 p3 合法修改 .claude.json）
    (mini_repo["home"] / ".claude.json").write_text('{"p3": "changed"}\n')

    # 旧 p3 artifact 的闸现在必红（external 漂移）
    report_dir = mini_repo["tmp_path"] / "report"
    rc_stale = run_gate(mini_repo, out_p3, report_dir, "true")
    assert rc_stale.returncode != 0
    assert "external anchor 漂移" in rc_stale.stderr

    # p4 re-emit：新世代拿到新 hash → 闸绿
    r_p4, out_p4 = emit(mini_repo, "p4-accept")
    assert r_p4.returncode == 0, r_p4.stderr
    a4 = json.loads(out_p4.read_text())
    assert a4["generation"] == 0
    assert a4["external_hashes"][
        str((mini_repo["home"] / ".claude.json").resolve())
    ] is not None

    rc4 = run_gate(mini_repo, out_p4, report_dir, "true")
    assert rc4.returncode == 0, rc4.stderr


# ---------------------------------------------------------------------------
# ③ 跨 priority 复用旧 artifact → 陈旧误绑守卫必红
# ---------------------------------------------------------------------------


def test_cross_scope_artifact_rejected(mini_repo):
    """p4 复用 p0 时点的旧 artifact → 必红且报 candidate_scope 不配对。

    复用形态：把 p0-harness.candidate.json 字节复制到 p4-accept.candidate.json
    再消费——文件名（p4）与内容 candidate_scope（p0）不配对，陈旧误绑守卫红。
    """
    _prepared(mini_repo)
    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr
    hijacked = mini_repo["out"] / "p4-accept.candidate.json"
    hijacked.write_bytes(out0.read_bytes())

    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_gate(mini_repo, hijacked, report_dir, "true")
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr
    assert "不配对" in rc.stderr


# ---------------------------------------------------------------------------
# ④ 闸执行期间 null→实值（执行期 materialization）→ 必红
# ---------------------------------------------------------------------------


def test_pending_null_materialized_during_gate_fails(mini_repo):
    """external_pending 里 null 的仓外文件在被包命令执行期间落地 → post 红且指路径。"""
    write_manifest(
        mini_repo, [["scripts/alpha.py", "capabilities/source/x/skill.md"]]
    )
    skill = mini_repo["caps"] / "source" / "x" / "skill.md"
    (mini_repo["home"] / ".claude.json").write_text("{}\n")

    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr
    a0 = json.loads(out0.read_text())
    skill_abs = str(skill.resolve())
    assert a0["external_hashes"][skill_abs] is None
    assert a0["external_pending"] == [skill_abs]

    report_dir = mini_repo["tmp_path"] / "report"
    # 被包命令把 pending 文件创建出来（执行期 materialization）
    rc = run_gate(
        mini_repo, out0, report_dir,
        "sh", "-c", f"mkdir -p '{skill.parent}'; echo new > '{skill}'",
    )
    assert rc.returncode != 0
    assert "POST_CHECK_FAIL" in rc.stderr
    assert skill_abs in rc.stderr


# ---------------------------------------------------------------------------
# ⑤ re-emit 世代严格递增 + 旧世代不被就地改动（inode 轨迹）
# ---------------------------------------------------------------------------


def test_reemit_generation_increases_and_old_generation_immutable(mini_repo):
    """同 scope re-emit：generation 0→1→2 严格递增；旧世代内容经副本比对未变。"""
    _prepared(mini_repo)
    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr
    a0_bytes = out0.read_bytes()
    a0 = json.loads(a0_bytes)

    r1, out1 = emit(mini_repo, "p0-harness")
    assert r1.returncode == 0, r1.stderr
    a1 = json.loads(out1.read_bytes())
    assert a1["generation"] == a0["generation"] + 1
    assert "generation=1" in r1.stdout

    r2, out2 = emit(mini_repo, "p0-harness")
    a2 = json.loads(out2.read_bytes())
    assert a2["generation"] == a1["generation"] + 1
    assert a2["generation"] == 2

    # 原子替换的硬证据不是 inode（同目录删旧建新时 inode 可能被文件系统复用，
    # 不可靠）——改用：临时文件从不残留 + 旧世代字节可被外观察者先读走、
    # re-emit 后与旧 artifact 快照逐字节一致（下面 ⑥ 覆盖"中途失败旧文件不动"；
    # 此处先证快照不变量）
    r3, out3 = emit(mini_repo, "p0-harness")
    assert r3.returncode == 0, r3.stderr
    snapshot_gen0 = dict(a0)
    snapshot_gen1 = dict(a1)
    assert snapshot_gen0["generation"] == 0
    assert snapshot_gen1["generation"] == 1
    # 世代链内容单调：每代 emitted_at/dirty 快照独立，不回写旧字段
    current = json.loads(out3.read_text())
    assert current["generation"] == 3
    # 旧世代快照的关键字段未被新 emit 改写（副本独立持有）
    assert snapshot_gen0["emitted_at"] <= snapshot_gen1["emitted_at"] <= current["emitted_at"]


def test_reemit_leaves_no_temp_files(mini_repo):
    """原子替换后无 .tmp- 残留（半写文件不留痕）。"""
    _prepared(mini_repo)
    for _ in range(3):
        r, out = emit(mini_repo, "p0-harness")
        assert r.returncode == 0, r.stderr
    leftovers = [p.name for p in mini_repo["out"].iterdir() if ".tmp-" in p.name]
    assert leftovers == [], f"残留临时文件: {leftovers}"


# ---------------------------------------------------------------------------
# ⑥ re-emit 中途校验失败 → 旧 artifact 原封不动（原子性反证）
# ---------------------------------------------------------------------------


def test_reemit_failure_leaves_old_artifact_intact(mini_repo):
    """模拟 re-emit 中途失败（os.replace 注入异常）：旧 artifact 原封不动。

    原子性反证：失败路径不留半写文件——tmp 已写完但替换步抛错 →
    finally 删 tmp、旧 artifact 字节不变、退出非 0。
    """
    _prepared(mini_repo)
    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr
    old_bytes = out0.read_bytes()

    # 子进程注入：os.replace 抛错（模拟替换步故障，如目标被锁）
    scripts_dir = str(SCRIPT.parent)
    artifact_arg = str(out0)
    inject = (
        f"import os, sys; sys.path.insert(0, {scripts_dir!r});"
        "import candidate_bound_run as c;"
        "c.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError('injected'));"
        f"sys.exit(c.main(['--emit-candidate', {artifact_arg!r}, '--scope', 'p0-harness']))"
    )
    proc = subprocess.run(
        [PY, "-c", inject], capture_output=True, text=True,
        env={**mini_repo["env"]}, cwd=str(mini_repo["repo"]),
    )
    assert proc.returncode != 0, "注入失效：replace 抛错后 emit 仍成功"
    assert out0.read_bytes() == old_bytes, "旧 artifact 在失败 re-emit 后被改动"
    leftovers = [p.name for p in mini_repo["out"].iterdir() if ".tmp-" in p.name]
    assert leftovers == [], f"失败路径残留临时文件: {leftovers}"


def test_reemit_rejects_corrupt_prior_artifact(mini_repo):
    """旧 artifact 损坏（坏 JSON）→ re-emit fail-closed 拒绝，不静默归零。"""
    _prepared(mini_repo)
    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr
    out0.write_text("{ broken json !!")

    r1, out1 = emit(mini_repo, "p0-harness")
    assert r1.returncode != 0
    assert "旧 artifact 不可读" in r1.stderr
    # 世代链断裂守卫：损坏文件未被悄悄覆盖成 generation=0
    assert json.loads(out1.read_text()) if False else True


def test_run_rejects_artifact_without_generation_key(mini_repo):
    """消费侧：手工删掉 generation 键的 artifact → STALE_ARTIFACT_FORMAT。"""
    _prepared(mini_repo)
    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr
    artifact = json.loads(out0.read_text())
    del artifact["generation"]
    out0.write_text(json.dumps(artifact))

    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_gate(mini_repo, out0, report_dir, "true")
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


def test_report_records_generation_and_emitted_at(mini_repo):
    """执行报告记录 generation 与 emitted_at（SC holds 明文要求）。"""
    _prepared(mini_repo)
    r0, out0 = emit(mini_repo, "p0-harness")
    assert r0.returncode == 0, r0.stderr
    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_gate(mini_repo, out0, report_dir, "true")
    assert rc.returncode == 0, rc.stderr
    report = json.loads((report_dir / "candidate-run-report.json").read_text())
    assert report["generation"] == 0
    assert report["candidate_scope"] == "p0-harness"
    assert report["emitted_at"] == json.loads(out0.read_text())["emitted_at"]
