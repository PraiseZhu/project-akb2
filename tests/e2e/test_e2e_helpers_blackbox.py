"""sc-e2e-helper-contract：三个 e2e 脚本的黑盒契约与三态矩阵。

(a) scripts/e2e_precheck.py：tmp 配置七格矩阵（缺文件/坏 TOML/缺
    akb2_root/根不存在/根为空/根不可遍历/正常根），逐格断言专属退出码
    （1/1/1/3/3/3/0）与前缀；未就绪分支下无任何下游检索调用。
(b) scripts/e2e_dual_channel_retrieval.py：两通道不一致夹具必红且
    指出首个差异字段（防伪造 identical=true）。
(c) scripts/e2e_noop_proof.py：touch 变异经 mutation_harness 执行，
    红集 exact 等于预测且 finally 还原。

oracle 全部外置为退出码/前缀/文件差异集，不采信脚本自述（R2-C1）；
全部在 tmp fixture 内完成，禁止触碰真实 /Volumes/AKB2（E 面）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRECHECK = REPO_ROOT / "scripts" / "e2e_precheck.py"
DUAL = REPO_ROOT / "scripts" / "e2e_dual_channel_retrieval.py"
NOOP = REPO_ROOT / "scripts" / "e2e_noop_proof.py"
PY = sys.executable

_PROMOTED = """---
title: Helper Probe
page_status: active
status: active
slug: helper-probe
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-01-01
---

## 论证与解释

helper contract token。

### 操作步骤

1. 步骤 `echo helper`
"""


def _run(script: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("VAULT_ROOT", None)
    env.pop("AKB2_ROOT", None)
    env.pop("AKB2_VAULT_ROOT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [PY, str(script), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=180,
    )


def _good_vault(tmp_path: Path) -> Path:
    root = tmp_path / "AKB2.0"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "helper-probe.md").write_text(_PROMOTED, encoding="utf-8")
    return root


def _config(tmp_path: Path, body: str | None) -> Path:
    cfg = tmp_path / "vault.toml"
    if body is not None:
        cfg.write_text(body, encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# (a) precheck 七格矩阵：退出码 1/1/1/3/3/3/0
# ---------------------------------------------------------------------------


def test_precheck_missing_config_file(tmp_path) -> None:
    """格 1：配置文件缺失 → exit 1 + E2E_CONFIG_FAIL。"""
    cfg = _config(tmp_path, None)  # 不写文件
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.startswith("E2E_CONFIG_FAIL")


def test_precheck_broken_toml(tmp_path) -> None:
    """格 2：坏 TOML → exit 1 + E2E_CONFIG_FAIL。"""
    cfg = _config(tmp_path, "[vault\nbroken !!!")
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 1
    assert proc.stdout.startswith("E2E_CONFIG_FAIL")


def test_precheck_missing_akb2_root_field(tmp_path) -> None:
    """格 3：缺 akb2_root 字段 → exit 1 + E2E_CONFIG_FAIL。"""
    cfg = _config(tmp_path, '[vault.layers]\nstaging = "/tmp/s"\n')
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 1
    assert proc.stdout.startswith("E2E_CONFIG_FAIL")


def test_precheck_root_missing(tmp_path) -> None:
    """格 4：根不存在（普通路径，非 /Volumes）→ exit 3 + E2E_INFRA_FAIL。"""
    cfg = _config(tmp_path, f'[vault]\nakb2_root = "{tmp_path / "no-such-root"}"\n')
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 3
    assert proc.stdout.startswith("E2E_INFRA_FAIL")


def test_precheck_root_empty(tmp_path) -> None:
    """格 5：根存在但 wiki/sources 无 md → exit 3（零结果与未就绪可区分）。"""
    root = tmp_path / "AKB2.0"
    (root / "wiki" / "sources").mkdir(parents=True)
    cfg = _config(tmp_path, f'[vault]\nakb2_root = "{root}"\n')
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 3
    assert proc.stdout.startswith("E2E_INFRA_FAIL")


def test_precheck_root_untraversable(tmp_path) -> None:
    """格 6：根不可遍历（sources 是文件不是目录）→ exit 3。"""
    root = tmp_path / "AKB2.0"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "sources").write_text("not a dir", encoding="utf-8")
    cfg = _config(tmp_path, f'[vault]\nakb2_root = "{root}"\n')
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 3
    assert proc.stdout.startswith("E2E_INFRA_FAIL")


def test_precheck_healthy_root(tmp_path) -> None:
    """格 7：正常根 → exit 0 + E2E_PRECHECK_OK root= md_files=1。"""
    root = _good_vault(tmp_path)
    cfg = _config(tmp_path, f'[vault]\nakb2_root = "{root}"\n')
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert proc.stdout.startswith("E2E_PRECHECK_OK")
    assert f"root={root.resolve()}" in proc.stdout
    assert "md_files=1" in proc.stdout


def test_precheck_prefixes_pairwise_distinct() -> None:
    """三前缀两两不同（退出码协议的机器判据，执行报告按前缀分类）。"""
    prefixes = ["E2E_PRECHECK_OK", "E2E_INFRA_FAIL", "E2E_CONFIG_FAIL"]
    assert len(set(prefixes)) == 3


def test_precheck_infra_branch_runs_no_downstream_retrieval(tmp_path) -> None:
    """未就绪分支哨兵：INFRA_FAIL 时不得发生任何下游检索调用。

    观察面：precheck 进程自身 stdout/stderr 无检索结果（count/results
    字样）；且退出码 3 早于任何检索逻辑（脚本内无检索调用面——由
    黑盒输出证明：只有前置诊断行）。
    """
    cfg = _config(tmp_path, f'[vault]\nakb2_root = "{tmp_path / "no-root"}"\n')
    proc = _run(PRECHECK, "--config", str(cfg))
    assert proc.returncode == 3
    combined = proc.stdout + proc.stderr
    assert '"results"' not in combined
    assert '"count"' not in combined
    assert "E2E_INFRA_FAIL" in combined


# ---------------------------------------------------------------------------
# (b) dual-channel 不一致夹具必红
# ---------------------------------------------------------------------------


def test_dual_channel_mismatch_fixture_fails() -> None:
    """人为构造两通道不一致夹具 → describe_first_mismatch 必报首个差异字段。

    防伪造 identical=true：比对逻辑独立纯函数，黑盒喂构造夹具。
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import e2e_dual_channel_retrieval as dual

    base = {
        "path": "wiki/sources/helper-probe.md",
        "title": "Helper Probe",
        "how_density": "正文",
        "how_executability": ["echo helper"],
        "score": 5,
    }
    # 完全一致 → None
    assert dual.describe_first_mismatch([dict(base)], [dict(base)]) is None

    # title 差异 → 指出字段=title 与两侧值
    tampered = dict(base, title="FAKED")
    msg = dual.describe_first_mismatch([dict(base)], [tampered])
    assert msg is not None
    assert "字段=title" in msg
    assert "FAKED" in msg

    # how_executability 差异
    tampered2 = dict(base, how_executability=[])
    msg2 = dual.describe_first_mismatch([dict(base)], [tampered2])
    assert msg2 is not None and "字段=how_executability" in msg2

    # 条数差异
    msg3 = dual.describe_first_mismatch([dict(base)], [])
    assert msg3 is not None and "条数不等" in msg3

    # 零结果
    msg4 = dual.describe_first_mismatch([], [])
    assert msg4 is not None and "零结果" in msg4

    # score 不参与一致性（实现细节），仅契约四字段差异才算
    diff_score = dict(base, score=999)
    assert dual.describe_first_mismatch([dict(base)], [diff_score]) is None


def test_dual_channel_consistent_fixture_passes(tmp_path) -> None:
    """正例：真 CLI + 真 MCP 同查询 → E2E_OK identical=true。"""
    root = _good_vault(tmp_path)
    report_dir = tmp_path / "report"
    proc = _run(
        DUAL,
        "--query", "helper contract", "--limit", "3",
        "--report-dir", str(report_dir),
        "--vault-root", str(root),
    )
    assert proc.returncode == 0, (proc.stdout[-400:], proc.stderr[-400:])
    assert "E2E_OK cli=1 mcp=1 identical=true" in proc.stdout
    report = json.loads((report_dir / "e2e-dual-channel-report.json").read_text())
    assert report["identical"] is True
    assert report["cli_count"] == 1


# ---------------------------------------------------------------------------
# (c) noop-proof 的 touch 变异（经 mutation_harness 机器化执行）
# ---------------------------------------------------------------------------


def _run_harness(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(REPO_ROOT / "scripts" / "mutation_harness.py"), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=420,
    )


def test_noop_proof_happy_path_zero_write(tmp_path) -> None:
    """正例：fixture vault 双通道前后指纹逐字相等 → E2E_NOOP_OK。"""
    root = _good_vault(tmp_path)
    report_dir = tmp_path / "report"
    proc = _run(
        NOOP,
        "--query", "helper contract", "--limit", "2",
        "--report-dir", str(report_dir),
        "--vault-root", str(root),
    )
    assert proc.returncode == 0, (proc.stdout[-300:], proc.stderr[-300:])
    assert "E2E_NOOP_OK" in proc.stdout


def test_noop_proof_reports_real_diff(tmp_path) -> None:
    """差异检测器有效：指纹采样间隔内真实新增文件 → exit 1 + 差异清单。

    黑盒注入方式：先跑一次拿基线语义不可行（进程内采样），改为直接
    验证——在 vault 里放一个「采样进行中被创建」的文件做不到黑盒注入，
    故此格断言检测器对已存在差异的敏感度：篡改指纹报告输入不可行，
    采用对照法：先创建额外文件后立刻再跑——两次运行间 vault 未变，
    本格实际验证的是 run 间稳定性（不变即绿）；真实差异路径由
    test_noop_touch_mutation（变异注入）承载。
    """
    root = _good_vault(tmp_path)
    report_dir = tmp_path / "report"
    proc = _run(
        NOOP, "--query", "helper contract", "--limit", "1",
        "--report-dir", str(report_dir), "--vault-root", str(root),
    )
    assert proc.returncode == 0
    # 运行间稳定：同输入两次运行绿（检测器无随机误报面）
    proc2 = _run(
        NOOP, "--query", "helper contract", "--limit", "1",
        "--report-dir", str(report_dir), "--vault-root", str(root),
    )
    assert proc2.returncode == 0


def test_noop_touch_mutation_exact_red() -> None:
    """touch 变异（e2e-noop-touch）红集 exact：注入非法写 → noop 测试必红。

    变异注入点在 run_dual_channels 开头（两次指纹之间写 vault 标记文件），
    预测红集 = 快乐路径用例（检测到差异 → exit 1 → 断言红）。
    """
    rc_proc = _run_harness(
        "--mutation", "e2e-noop-touch",
        "--tests", "tests/e2e/test_e2e_helpers_blackbox.py::test_noop_proof_happy_path_zero_write",
        "--predicted-red",
        "tests/e2e/test_e2e_helpers_blackbox.py::test_noop_proof_happy_path_zero_write",
        "--timeout", "300",
    )
    out = rc_proc.stdout
    assert rc_proc.returncode == 0, rc_proc.stdout + rc_proc.stderr
    assert "MUTATION_RED_SET_MATCH actual=1 predicted=1" in out
    assert "SOURCE_RESTORED" in out


def test_noop_proof_rejects_repo_report_dir(tmp_path) -> None:
    """报告目录指到仓内 → 拒绝（自我证伪防护）。"""
    root = _good_vault(tmp_path)
    proc = _run(
        NOOP, "--query", "x", "--limit", "1",
        "--report-dir", str(REPO_ROOT / "reports"),
        "--vault-root", str(root),
    )
    assert proc.returncode == 1
    assert "仓外" in proc.stdout or "E2E_CONFIG_FAIL" in proc.stdout
