"""sc-engine-readonly：只读契约测试（字节级树 hash 前后相等）。

(a) 函数入口与 CLI 入口各完整跑一次检索后，fixture vault 目录树 hash
    与全部索引/DB 文件字节不变——断言绑定字节级不变，不是『能跑通』。
(b) 反向变异由 scripts/mutation_harness.py 机器化执行（retrieval-readonly-open
    注册表条目），红集 exact 比对由 verify 命令承载，本文件只测只读正向面。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from akb2.retrieval import engine

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROMOTED = """---
title: Readonly Probe
page_status: active
status: active
slug: readonly-probe
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-01-01
---

## 论证与解释

readonly token 内容。

### 操作步骤

1. 步骤 `echo readonly`
"""


def _make_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "readonly-probe.md").write_text(_PROMOTED, encoding="utf-8")
    # 预置索引/DB 类文件（若有索引机制会碰的路径全在此观察面内）
    idx = root / "wiki" / "sources" / "00-Index.md"
    idx.write_text("---\ntitle: 索引\n---\n", encoding="utf-8")
    (root / ".akb-index.db").write_bytes(b"\x00\x01\x02stub-index")
    (root / "wiki" / ".search-cache.json").write_text("{}", encoding="utf-8")
    return root


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, int, str]]:
    """相对路径 → (size, mtime_ns, sha256) 的有序指纹（字节级）。"""
    fp: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            stat = p.stat()
            fp[p.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
    return fp


def test_engine_entry_no_write(tmp_path: Path) -> None:
    """函数入口：完整检索前后 vault 树指纹（含索引/DB）逐字节相等。

    同一条测试内覆盖健康-only 与混合畸形两轮（skip 分支同样零写）——
    轮次内联而非独立测试，保证 retrieval-readonly-open 变异的红集
    恰为 verify 命令钉死的两条 nodeid（exact 语义）。
    """
    root = _make_vault(tmp_path)
    before = _tree_fingerprint(root)

    results = engine.search(root, "readonly token")
    assert len(results) == 1, "正例必须命中（防零结果假绿）"
    report = engine.scan_report(root, "readonly token")
    assert len(report.results) == 1

    after = _tree_fingerprint(root)
    assert before == after, (
        "引擎检索后 vault 发生字节级变化:\n"
        f"  新增: {sorted(set(after) - set(before))}\n"
        f"  消失: {sorted(set(before) - set(after))}\n"
        f"  内容变: {sorted(k for k in before if k in after and before[k] != after[k])}"
    )

    # 第二轮：混入畸形形状（非 UTF-8 / 超预算），skip 分支不得顺手写
    (root / "wiki" / "sources" / "bin.md").write_bytes(b"\xff\xfe raw")
    huge = root / "wiki" / "sources" / "huge.md"
    huge.write_text(
        _PROMOTED[:200] + "w" * (engine.MAX_NOTE_BYTES + 1), encoding="utf-8"
    )
    before2 = _tree_fingerprint(root)
    report2 = engine.scan_report(root, "readonly token")
    assert len(report2.results) == 1
    assert dict(report2.skipped).get("wiki/sources/bin.md") == "non_utf8"
    assert dict(report2.skipped).get("wiki/sources/huge.md") == "oversize"
    assert _tree_fingerprint(root) == before2, "skip 分支产生额外写入"


def test_cli_entry_no_write(tmp_path: Path) -> None:
    """CLI 入口：python3 -m akb2.retrieval.cli 检索前后树指纹逐字节相等。"""
    root = _make_vault(tmp_path)
    before = _tree_fingerprint(root)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable, "-m", "akb2.retrieval.cli",
            "search", "readonly token", "--limit", "5",
            "--vault-root", str(root),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    import json

    payload = json.loads(proc.stdout)
    assert payload["count"] == 1

    after = _tree_fingerprint(root)
    assert before == after, (
        f"CLI 检索后 vault 变化: 新增={sorted(set(after) - set(before))} "
        f"变化={[k for k in before if k in after and before[k] != after[k]]}"
    )
