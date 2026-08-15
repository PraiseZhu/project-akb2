"""sc-engine-promoted-authority：promoted 判定权威口径钉死测试。

判据必须与 src/akb2/quality/promote.py 的 _flip_frontmatter（升格唯一写入口）
实际写入一致：status/page_status: active + gate_decision: active +
reviewer: akb2-quality-loop-v1（同批 page_status/reviewer/reviewed_at），
落点 wiki/sources/<slug>.md。

夹具矩阵恰五格：①字段齐全且在 wiki/sources ②仅 status active
③仅 gate_decision active ④两者皆非 active ⑤字段齐全但落在 staging/candidate。
恰第①格 promoted，其余四格一律排除。

同源反证：改 promote.py 的判定字段而不改引擎时，本文件的同源断言必须红
——判据值不复制粘贴，而是从 promote.py 源文件现场解析（MACHINE_REVIEWER
常量 + _flip_frontmatter 写入的四个字段名），promote.py 一动测试即失配。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from akb2.retrieval import engine

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMOTE_PY = REPO_ROOT / "src" / "akb2" / "quality" / "promote.py"


# ---------------------------------------------------------------------------
# 同源解析：从 promote.py 现场提取权威判据（不复制粘贴常量值）
# ---------------------------------------------------------------------------


def _authority_from_promote() -> dict[str, str]:
    """解析 promote.py 的 MACHINE_REVIEWER 与 _flip_frontmatter 写入字段。"""
    src = PROMOTE_PY.read_text(encoding="utf-8")

    m = re.search(r'^MACHINE_REVIEWER\s*=\s*"([^"]+)"', src, re.M)
    assert m, "promote.py 缺 MACHINE_REVIEWER 常量（源码已漂移）"
    machine_reviewer = m.group(1)

    fn = re.search(
        r"^def _flip_frontmatter\(.*?(?=^def )", src, re.M | re.S
    )
    assert fn, "promote.py 缺 _flip_frontmatter（源码已漂移）"
    body = fn.group(0)

    # 翻转动写入的键（re.sub 替换目标 / 新增行），每个都必须在函数体里
    written = {
        "page_status": bool(re.search(r'"?page_status"?\s*:\s*active|page_status:\\s\\s\*', body))
        or '"page_status: active"' in body,
        "status": '"status: active"' in body,
        "gate_decision": '"gate_decision: active"' in body,
        "reviewer": '"reviewer: {MACHINE_REVIEWER}"' in body
        or "f\"reviewer: {MACHINE_REVIEWER}\"" in body,
    }
    return {"machine_reviewer": machine_reviewer, "written": written}


def test_engine_constants_same_source_as_promote() -> None:
    """同源断言：引擎常量与 promote.py 现场解析值逐字相等。

    反证路径：改 promote.py 的 MACHINE_REVIEWER 或 _flip_frontmatter 的
    写入字段而不改引擎 → 本测试红（判据失配，不允许静默漂移）。
    """
    auth = _authority_from_promote()
    assert engine.MACHINE_REVIEWER == auth["machine_reviewer"], (
        f"引擎 MACHINE_REVIEWER={engine.MACHINE_REVIEWER!r} 与 promote.py "
        f"权威值 {auth['machine_reviewer']!r} 失配——改了升格签名没同步引擎"
    )
    # _flip_frontmatter 写入的四个判据字段全部真实存在（缺一个 = 源码漂移）
    for key, present in auth["written"].items():
        assert present, f"promote.py _flip_frontmatter 不再写 {key}（判据漂移）"


def test_engine_regexes_encode_promote_semantics() -> None:
    """引擎四个行首锚定正则的语义与 promote 写入口径一致（值级断言）。"""
    auth = _authority_from_promote()
    # is_promoted 的判据 = (page_)status active + gate_decision active +
    # reviewer = MACHINE_REVIEWER —— 与 _flip_frontmatter 写入集合同源
    promoted_fm = (
        "---\n"
        "title: probe\n"
        "page_status: active\n"
        "status: active\n"
        "gate_decision: active\n"
        f"reviewer: {auth['machine_reviewer']}\n"
        "reviewed_at: 2026-01-01\n"
        "---\n"
    )
    assert engine.is_promoted(promoted_fm) is True
    # reviewer 值偏离权威签名 → 不 promoted
    tampered = promoted_fm.replace(
        f"reviewer: {auth['machine_reviewer']}", "reviewer: someone-else"
    )
    assert engine.is_promoted(tampered) is False


# ---------------------------------------------------------------------------
# 五格夹具矩阵（恰 1 真 4 假）
# ---------------------------------------------------------------------------

_FULL_FM = """---
title: Matrix Probe
page_status: active
status: active
slug: matrix-probe
gate_decision: active
reviewer: {reviewer}
reviewed_at: 2026-01-01
---
"""

_BODY = """## 论证与解释

matrix probe token 内容。

### 操作步骤

1. 步骤 `echo probe`
"""


def _write(root: Path, rel: str, fm: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fm + _BODY, encoding="utf-8")


@pytest.fixture()
def matrix_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    auth = _authority_from_promote()
    reviewer = auth["machine_reviewer"]

    # ① 字段齐全且在 wiki/sources → promoted
    _write(root, "wiki/sources/full.md", _FULL_FM.format(reviewer=reviewer))
    # ② 仅 status active（无 gate_decision: active）
    _write(
        root, "wiki/sources/only-status.md",
        _FULL_FM.format(reviewer=reviewer).replace("gate_decision: active", "gate_decision: null"),
    )
    # ③ 仅 gate_decision active（status 非 active）
    _write(
        root, "wiki/sources/only-gate.md",
        _FULL_FM.format(reviewer=reviewer)
        .replace("page_status: active", "page_status: candidate")
        .replace("status: active", "status: candidate"),
    )
    # ④ 两者皆非 active
    _write(
        root, "wiki/sources/neither.md",
        _FULL_FM.format(reviewer=reviewer)
        .replace("page_status: active", "page_status: candidate")
        .replace("status: active", "status: candidate")
        .replace("gate_decision: active", "gate_decision: null"),
    )
    # ⑤ 字段齐全但落在 staging/candidate 目录
    _write(root, "staging/candidate/batch/full.md", _FULL_FM.format(reviewer=reviewer))
    return root


def test_matrix_exactly_one_promoted(matrix_vault: Path) -> None:
    """五格扫描：恰第①格命中（1 真 4 假）。"""
    results = engine.search(matrix_vault, "matrix probe token")
    assert [r.path for r in results] == ["wiki/sources/full.md"]


def test_matrix_cell2_only_status_excluded(matrix_vault: Path) -> None:
    """②仅 status active：四要素缺 gate_decision → 排除。"""
    paths = {r.path for r in engine.search(matrix_vault, "matrix")}
    assert "wiki/sources/only-status.md" not in paths


def test_matrix_cell3_only_gate_excluded(matrix_vault: Path) -> None:
    """③仅 gate_decision active：status 非 active → 排除。"""
    paths = {r.path for r in engine.search(matrix_vault, "matrix")}
    assert "wiki/sources/only-gate.md" not in paths


def test_matrix_cell4_neither_excluded(matrix_vault: Path) -> None:
    """④两者皆非 active → 排除。"""
    paths = {r.path for r in engine.search(matrix_vault, "matrix")}
    assert "wiki/sources/neither.md" not in paths


def test_matrix_cell5_wrong_dir_excluded(matrix_vault: Path) -> None:
    """⑤字段齐全但落 staging/candidate：『字段对目录不对』不算 promoted。"""
    paths = {r.path for r in engine.search(matrix_vault, "matrix")}
    assert "staging/candidate/batch/full.md" not in paths
    assert not any("staging" in p for p in paths)


def test_scan_domain_is_wiki_sources_only(matrix_vault: Path) -> None:
    """扫描域 exact：wiki/sources/ 之外的任何 promoted 形状都不可见。"""
    report = engine.scan_report(matrix_vault, "matrix")
    for r in report.results:
        assert r.path.startswith("wiki/sources/")
