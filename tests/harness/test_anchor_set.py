"""sc-anchor-set-contract 验收测试：anchor 集合派生规则与职责边界。

覆盖 expect 矩阵七格：
  ① scope_paths == manifest 全部 anchor_paths 的仓内子集（顺序无关、去重）
  ② external_hashes 键集恰为白名单内仓外路径，不含任何仓内路径
  ③ manifest 改动 → 按当前 manifest 现算 scope_set_digest 与 artifact 不等 → 红
  ④ 篡改仓外 anchor 内容 → external 比对必红并指出路径
  ⑤ 仓内待建文件在两次校验间创建 → 不因 external_hashes 误红（由 dirty=0 拦）
  ⑥ 术语扫描表 (a)-(d)：十二键完整、参数配对、旧名只许出现在负断言、
     路径归属措辞
  ⑦ 路径越界 → 拒绝产出 artifact 且非 0

测试鉴别力纪律：零收集即红由 verify 层把关；本文件不使用任何 skip。
"""

from __future__ import annotations

import json
import re

import pytest

# 三个典型样张：仓内相对 ×2 + 仓外白名单 ×2（.claude.json 与 capabilities 脚本）
ANCHORS_SC1 = ["scripts/alpha.py", "tests/harness/test_alpha.py", ".claude.json"]
ANCHORS_SC2 = [
    "scripts/alpha.py",  # 与 SC1 重复，验证并集去重
    "src/akb2/retrieval/engine.py",
    "capabilities/source/skills/claude-active/akb-search/SKILL.md",
]
ALL_REPO = sorted({
    "scripts/alpha.py",
    "tests/harness/test_alpha.py",
    "src/akb2/retrieval/engine.py",
})


def _setup(mini_repo, anchors_by_sc: list[list[str]], populate_external: bool = True):
    write_manifest(mini_repo, anchors_by_sc)
    if populate_external:
        (mini_repo["home"] / ".claude.json").write_text("{}\n")
        skill = mini_repo["caps"] / "source" / "skills" / "claude-active" / "akb-search" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("# skill\n")
    return emit(mini_repo)


# ---------------------------------------------------------------------------
# ① scope_paths 与仓内子集逐字相等（顺序无关、去重后比对）
# ---------------------------------------------------------------------------


def test_scope_paths_equals_repo_subset_of_manifest_union(mini_repo):
    r, out = _setup(mini_repo, [ANCHORS_SC1, ANCHORS_SC2])
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    assert sorted(artifact["scope_paths"]) == ALL_REPO
    # 并集去重：SC1/SC2 共享的 scripts/alpha.py 只出现一次
    assert artifact["scope_paths"].count("scripts/alpha.py") == 1


# ---------------------------------------------------------------------------
# ② external_hashes 键集恰为白名单仓外路径，不含任何仓内路径
# ---------------------------------------------------------------------------


def test_external_hashes_keys_exactly_whitelisted_absolute(mini_repo):
    r, out = _setup(mini_repo, [ANCHORS_SC1, ANCHORS_SC2])
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    keys = sorted(artifact["external_hashes"].keys())
    assert keys == sorted([
        str((mini_repo["home"] / ".claude.json").resolve()),
        str(
            (mini_repo["caps"] / "source" / "skills" / "claude-active"
             / "akb-search" / "SKILL.md").resolve()
        ),
    ])
    # 仓内相对路径绝不进 external_hashes（判据双头回归守卫）
    for key in keys:
        assert not key.startswith("scripts/")
        assert not key.startswith("src/")
        assert not key.startswith("tests/")
    # 每个 hash 都是 64 位十六进制（文件已存在，不为 null）
    for value in artifact["external_hashes"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert artifact["external_pending"] == []


def test_external_pending_records_missing_whitelist_file(mini_repo):
    # .claude.json 存在、capabilities skill 不存在 → null + pending
    write_manifest(mini_repo, [ANCHORS_SC2, [".claude.json"]])
    (mini_repo["home"] / ".claude.json").write_text("{}\n")
    r, out = emit(mini_repo)
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    skill = str(
        (mini_repo["caps"] / "source" / "skills" / "claude-active" / "akb-search"
         / "SKILL.md").resolve()
    )
    assert artifact["external_hashes"][skill] is None
    assert artifact["external_pending"] == [skill]
    assert artifact["external_hashes"][
        str((mini_repo["home"] / ".claude.json").resolve())
    ] is not None


def test_set_digests_present_and_content_addressed(mini_repo):
    r, out = _setup(mini_repo, [ANCHORS_SC1, ANCHORS_SC2])
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    assert re.fullmatch(r"[0-9a-f]{64}", artifact["scope_set_digest"])
    assert re.fullmatch(r"[0-9a-f]{64}", artifact["external_set_digest"])
    # digest 由集合派生：换一组集合必须变
    r2, out2 = _setup(mini_repo, [["scripts/other.py"]])
    assert r2.returncode == 0, r2.stderr
    artifact2 = json.loads(out2.read_text())
    assert artifact2["scope_set_digest"] != artifact["scope_set_digest"]


# ---------------------------------------------------------------------------
# ③ manifest 漏一项 → 按当前 manifest 现算 digest 与 artifact 值不等 → 必红
#    （严格重算而非复用 artifact 旧值）
# ---------------------------------------------------------------------------


def test_manifest_shrink_detected_by_recomputed_digest(mini_repo):
    r, out = _setup(mini_repo, [ANCHORS_SC1, ANCHORS_SC2])
    assert r.returncode == 0, r.stderr
    # 被包命令 true：只校验前序，不引入其他变量
    report_dir = mini_repo["tmp_path"] / "report"
    run_env = {**mini_repo["env"], "repo": mini_repo["repo"]}
    rc = run_wrapper(
        run_env,
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode == 0, rc.stderr

    # 改 manifest（去掉一个 SC）后不 re-emit，直接拿旧 artifact 跑 → 必红
    write_manifest(mini_repo, [ANCHORS_SC1])
    rc2 = run_wrapper(
        run_env,
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc2.returncode not in (0,)
    assert "MANIFEST_DRIFT" in rc2.stderr or "set_digest" in rc2.stderr


def test_manifest_anchor_added_detected(mini_repo):
    r, out = _setup(mini_repo, [ANCHORS_SC1])
    assert r.returncode == 0, r.stderr
    # manifest 新增 anchor 而 artifact set_digest 未更新 → 红
    write_manifest(mini_repo, [ANCHORS_SC1, ["scripts/brand_new.py"]])
    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode != 0


# ---------------------------------------------------------------------------
# ④ 篡改仓外 anchor 内容 → 必红并指出该路径
# ---------------------------------------------------------------------------


def test_external_anchor_tamper_detected_with_path(mini_repo):
    r, out = _setup(mini_repo, [ANCHORS_SC1, ANCHORS_SC2])
    assert r.returncode == 0, r.stderr
    (mini_repo["home"] / ".claude.json").write_text('{"tampered": true}\n')
    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode != 0
    assert str((mini_repo["home"] / ".claude.json").resolve()) in rc.stderr


def test_external_anchor_deleted_detected(mini_repo):
    r, out = _setup(mini_repo, [ANCHORS_SC1, ANCHORS_SC2])
    assert r.returncode == 0, r.stderr
    (mini_repo["home"] / ".claude.json").unlink()
    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode != 0


# ---------------------------------------------------------------------------
# ⑤ 仓内待建文件在两次校验间创建 → 不因 external_hashes 误红
#    （合法阶段推进；创建未提交由 dirty=0 正确拦截——职责不重叠）
# ---------------------------------------------------------------------------


def test_repo_pending_file_creation_not_external_false_positive(mini_repo):
    # scope 内含尚不存在的仓内待建路径（p1 将创建）
    write_manifest(mini_repo, [["scripts/future_p1.py", ".claude.json"]])
    (mini_repo["home"] / ".claude.json").write_text("{}\n")
    r, out = emit(mini_repo)
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    # emit 时仓内待建路径只入清单，不出现在 external 任何键中
    assert "scripts/future_p1.py" in artifact["scope_paths"]
    assert "scripts/future_p1.py" not in json.dumps(artifact["external_hashes"])

    # 阶段推进：创建该文件（未提交）→ 外部判据不红；dirty=0 拦截
    scripts_dir = mini_repo["repo"] / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "future_p1.py").write_text("print('p1')\n")
    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode != 0
    assert "dirty" in rc.stderr
    # 红的原因是 dirty，而不是 external anchor 漂移
    assert "external anchor 漂移" not in rc.stderr


# ---------------------------------------------------------------------------
# ⑥ 术语扫描表 (a)-(d)
# ---------------------------------------------------------------------------


REQUIRED_KEYS = (
    "candidate_scope", "branch", "head_sha", "base_sha", "dirty_count",
    "generation", "scope_paths", "scope_set_digest", "external_hashes",
    "external_set_digest", "external_pending", "emitted_at",
)
DEPRECATED = ("anchor_hashes", "pending_paths")


def test_terminus_a_twelve_keys_complete(mini_repo):
    """(a) 十二键完整集合及其配对关系。"""
    r, out = _setup(mini_repo, [ANCHORS_SC1, ANCHORS_SC2])
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    assert sorted(artifact.keys()) == sorted(REQUIRED_KEYS)
    # 配对：scope_paths ↔ scope_set_digest、external_hashes ↔ external_set_digest
    import hashlib
    scope_digest = hashlib.sha256(
        "\n".join(sorted(set(artifact["scope_paths"]))).encode()
    ).hexdigest()
    ext_digest = hashlib.sha256(
        "\n".join(sorted(set(artifact["external_hashes"].keys()))).encode()
    ).hexdigest()
    assert artifact["scope_set_digest"] == scope_digest
    assert artifact["external_set_digest"] == ext_digest


def test_terminus_b_param_pairing(mini_repo):
    """(b) 参数配对：--scope↔--emit-candidate、文件名↔candidate_scope。"""
    out = mini_repo["out"] / "p0-harness.candidate.json"
    r = run_wrapper(mini_repo["env"], "--emit-candidate", str(out), "--scope", "p0-harness")
    write_manifest(mini_repo, [["scripts/a.py"]])  # manifest 须在 emit 前就绪
    r = run_wrapper(mini_repo["env"], "--emit-candidate", str(out), "--scope", "p0-harness")
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    assert artifact["candidate_scope"] == "p0-harness"
    assert out.name == f"{artifact['candidate_scope']}.candidate.json"

    # 文件名与 scope 不配对 → 拒绝
    bad = mini_repo["out"] / "p1-engine.candidate.json"
    r2 = run_wrapper(mini_repo["env"], "--emit-candidate", str(bad), "--scope", "p0-harness")
    assert r2.returncode != 0

    # 缺 --scope → 用法错误
    r3 = run_wrapper(mini_repo["env"], "--emit-candidate", str(out))
    assert r3.returncode != 0


def test_terminus_c_deprecated_names_only_in_negative_assertions(mini_repo):
    """(c) 旧名 anchor_hashes/pending_paths 只允许出现在负断言与废弃说明中。"""
    r, out = _setup(mini_repo, [ANCHORS_SC1])
    assert r.returncode == 0, r.stderr
    raw = out.read_text()
    for name in DEPRECATED:
        assert name not in raw  # artifact 字段中零出现

    # 构造含废弃键的旧世代 artifact → 消费侧 fail-closed STALE_ARTIFACT_FORMAT
    artifact = json.loads(raw)
    artifact["anchor_hashes"] = {}
    out.write_text(json.dumps(artifact))
    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr

    # pending_paths 同样拒收
    artifact2 = json.loads(out.read_text())
    del artifact2["anchor_hashes"]
    artifact2["pending_paths"] = []
    out.write_text(json.dumps(artifact2))
    rc2 = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc2.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc2.stderr

    # 缺 candidate_scope → STALE_ARTIFACT_FORMAT
    artifact3 = json.loads(out.read_text())
    del artifact3["pending_paths"]
    del artifact3["candidate_scope"]
    out.write_text(json.dumps(artifact3))
    rc3 = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc3.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc3.stderr

    # 裸 candidate.json 文件名 → 不配对 → STALE（不得成为实际消费路径）
    bare = mini_repo["out"] / "candidate.json"
    artifact4 = json.loads(out.read_text())
    artifact4["candidate_scope"] = "p0-harness"
    bare.write_text(json.dumps(artifact4))
    rc4 = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(bare),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc4.returncode != 0


def test_terminus_d_external_hashes_repo_path_rejected(mini_repo):
    """(d) external_hashes 混入仓内路径 → STALE_ARTIFACT_FORMAT。"""
    r, out = _setup(mini_repo, [ANCHORS_SC1])
    assert r.returncode == 0, r.stderr
    artifact = json.loads(out.read_text())
    artifact["external_hashes"]["scripts/alpha.py"] = "0" * 64
    out.write_text(json.dumps(artifact))
    report_dir = mini_repo["tmp_path"] / "report"
    rc = run_wrapper(
        {**mini_repo["env"], "repo": mini_repo["repo"]},
        "--candidate-manifest", str(out),
        "--report-dir", str(report_dir),
        "--", "true",
    )
    assert rc.returncode != 0
    assert "STALE_ARTIFACT_FORMAT" in rc.stderr


# ---------------------------------------------------------------------------
# ⑦ 路径越界 → 拒绝产出 artifact 且非 0 退出
# ---------------------------------------------------------------------------


def test_escape_path_rejected_no_artifact(mini_repo):
    write_manifest(mini_repo, [["../../etc/passwd"]])
    r, out = emit(mini_repo)
    assert r.returncode != 0
    assert not out.exists()


def test_non_whitelisted_absolute_path_rejected(mini_repo):
    write_manifest(mini_repo, [["/etc/passwd"]])
    r, out = emit(mini_repo)
    assert r.returncode != 0
    assert not out.exists()


def test_non_whitelisted_tilde_path_rejected(mini_repo):
    write_manifest(mini_repo, [["~/Desktop/evil.txt"]])
    r, out = emit(mini_repo)
    assert r.returncode != 0
    assert not out.exists()


def test_bare_capabilities_root_rejected(mini_repo):
    # 白名单是「capabilities/ 下的文件」，capabilities 根目录本身不是文件
    # anchor → fail-closed 拒绝产出
    write_manifest(mini_repo, [["capabilities", ".claude.json"]])
    (mini_repo["home"] / ".claude.json").write_text("{}\n")
    r, out = emit(mini_repo)
    assert r.returncode != 0
    assert not out.exists()


def test_capabilities_prefix_traversal_escape_rejected(mini_repo):
    # capabilities/ 前缀白名单误判回归：``..`` 段可借前缀豁免逃出 caps 根，
    # 必须仍按越界处理（fail-closed 拒绝产出，不得静默降级为 external）
    write_manifest(mini_repo, [["capabilities/../../etc/passwd"]])
    r, out = emit(mini_repo)
    assert r.returncode != 0
    assert not out.exists()


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
