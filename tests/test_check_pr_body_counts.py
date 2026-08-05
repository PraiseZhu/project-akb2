"""SC-R7-05 — PR body 复核轨迹表计数闸（scripts/check_pr_body_counts.py）的坏样本矩阵。

复核轨迹表此前是全链唯一没有机器闸的产物：每行「N 条 finding」「N 条 SC」都是手写，从未
接过 consensus-r<N>.json / sc-manifest*.json 的实算值 —— R7 复审抓到的漂移（真实 R4 delta
行写「5 条 SC」，实际绑定的 sc-manifest 只有 4 条）正是这条规律的表现。本模块用**合成
fixture**（不依赖 `_tmp/pr-review/` 下的真实文件，那些不入 git、CI 里不存在）证明：
① 手写漂移必被检出并指名两侧数值；② 闸真的从 artifact 重算，不是把某个数字硬编码进闸里。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER = PROJECT_ROOT / "scripts" / "check_pr_body_counts.py"

TABLE_HEADER = "| 轮次 | candidate | 共识确认 | 其中 actionable | 修复编排 |\n|---|---|---|---|---|\n"


def _row(label: str, candidate: str, n_findings: int, n_sc: int) -> str:
    return (
        f"| {label} | `{candidate}` | {n_findings} 条 | 1 条（0 blocker / 1 major） | "
        f"{n_sc} 条 SC，1 组并行 → squash |"
    )


def _body(rows: list[str]) -> str:
    return "## 复核轨迹（合成 fixture）\n\n" + TABLE_HEADER + "\n".join(rows) + "\n"


def _consensus(candidate_sha: str, consensus_hash: str, n_findings: int) -> dict:
    return {
        "schema_version": "v2",
        "candidate_sha": candidate_sha,
        "consensus_artifact_hash": consensus_hash,
        "canonical_findings": [{"id": f"f{i}"} for i in range(n_findings)],
    }


def _sc_manifest(consensus_hash: str, n_scs: int) -> dict:
    return {
        "schema_version": "v2",
        "consensus_artifact_hash": consensus_hash,
        "scs": [{"id": f"SC-{i}"} for i in range(n_scs)],
    }


def _write_artifacts(dirpath: Path, *, consensus: dict[str, dict], sc_manifests: dict[str, dict]) -> None:
    for name, data in consensus.items():
        (dirpath / f"consensus-{name}.json").write_text(json.dumps(data), encoding="utf-8")
    for name, data in sc_manifests.items():
        (dirpath / f"sc-manifest-{name}.json").write_text(json.dumps(data), encoding="utf-8")


def _run(body_path: Path, artifacts_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--body", str(body_path), "--artifacts-dir", str(artifacts_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_detects_handwritten_count_drift(tmp_path: Path) -> None:
    """手写的三种漂移形态各自被检出并指名行号 + 两侧数值；合规行不被误伤。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    # r1 的 consensus 有 3 条 finding；绑定它的 sc-manifest（按 hash 绑定，编号本身故意错位——
    # 复现真实场景里 sc-manifest-r5 绑定 consensus-r4 的编号偏移）只有 2 条 SC。
    _write_artifacts(
        artifacts,
        consensus={"r1": _consensus("1111111111111111111111111111111111abcd", "chash-1", 3)},
        sc_manifests={"r2": _sc_manifest("chash-1", 2)},
    )

    # 坏样本 1：SC 条数手写成 3（真实是 2）—— R7 复审抓到的真实漂移形态
    sc_drift_body = tmp_path / "sc-drift.md"
    sc_drift_body.write_text(_body([_row("R1", "1111111", 3, 3)]), encoding="utf-8")
    result = _run(sc_drift_body, artifacts)
    assert result.returncode == 1, result.stdout
    assert "R1" in result.stderr
    assert "3 条 SC" in result.stderr
    assert "实算 2 条" in result.stderr
    assert "sc-manifest-r2.json" in result.stderr

    # 坏样本 2：finding 条数手写成 4（真实是 3）
    finding_drift_body = tmp_path / "finding-drift.md"
    finding_drift_body.write_text(_body([_row("R1", "1111111", 4, 2)]), encoding="utf-8")
    result = _run(finding_drift_body, artifacts)
    assert result.returncode == 1, result.stdout
    assert "4 条」finding" in result.stderr
    assert "实算 3 条" in result.stderr
    assert "consensus-r1.json" in result.stderr

    # 坏样本 3：candidate SHA 打错/引用了不存在的 commit —— 该行整体无法绑定
    unbound_body = tmp_path / "unbound-candidate.md"
    unbound_body.write_text(_body([_row("R1", "deadbee", 3, 2)]), encoding="utf-8")
    result = _run(unbound_body, artifacts)
    assert result.returncode == 1, result.stdout
    assert "deadbee" in result.stderr
    assert "未匹配任何 consensus artifact" in result.stderr

    # 合规样本：三个数字都对得上 —— 不被误伤，返回 0
    good_body = tmp_path / "good.md"
    good_body.write_text(_body([_row("R1", "1111111", 3, 2)]), encoding="utf-8")
    result = _run(good_body, artifacts)
    assert result.returncode == 0, result.stderr


def test_gate_recomputes_from_artifacts(tmp_path: Path) -> None:
    """闸真的从 artifact 重算，不是把某组数字硬编码进闸里；改 artifact 数值就同步改判定。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    # 故意选用与真实 PR 轮次完全不同的任意数字（7 条 finding / 5 条 SC），
    # 证明闸不依赖任何写死的"真实轮次"常量。
    _write_artifacts(
        artifacts,
        consensus={"r9": _consensus("9999999999999999999999999999999999beef", "chash-9", 7)},
        sc_manifests={"r10": _sc_manifest("chash-9", 5)},
    )
    body = tmp_path / "body.md"
    body.write_text(_body([_row("R9 delta", "9999999", 7, 5)]), encoding="utf-8")

    result = _run(body, artifacts)
    assert result.returncode == 0, result.stderr

    # 原地重写 sc-manifest（同一路径、同一 consensus_hash），把 SC 条数从 5 改成 6；
    # body 完全没动。若闸是即时重算，同一份 body 现在必须翻红；若闸缓存/硬编码了上一次
    # 读到的 5，则会继续放行——本断言正是那个区分点。
    (artifacts / "sc-manifest-r10.json").write_text(
        json.dumps(_sc_manifest("chash-9", 6)), encoding="utf-8"
    )
    result = _run(body, artifacts)
    assert result.returncode == 1, "sc-manifest 变了但闸仍放行 —— 判定没有从 artifact 重算"
    assert "6 条" in result.stderr and "5 条 SC" in result.stderr

    # 把 body 里的数字改成新的真值（6），同一 artifacts 目录，闸必须重新放行——
    # 证明判定标准始终是"当下 artifact 的实算值"，不是记住了第一次跑到的数字。
    body.write_text(_body([_row("R9 delta", "9999999", 7, 6)]), encoding="utf-8")
    result = _run(body, artifacts)
    assert result.returncode == 0, result.stderr
