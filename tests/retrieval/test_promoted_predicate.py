"""sc-engine-promoted-authority：promoted 判定口径钉死测试。

判据 = 升格写入口 _flip_frontmatter 当年实际写入的字段：status/page_status: active +
gate_decision: active + reviewer: akb2-quality-loop-v1（同批 page_status/reviewer/reviewed_at），
落点 wiki/sources/<slug>.md。

写入口 src/akb2/quality/promote.py 已于 2026-10-24 随主链路删除。知识库现存的已升格笔记
都带这组字段，所以判据冻结为删除时的值（_PROMOTED_REVIEWER），不再从源码现场解析。
新主链路重建时：沿用这组字段，或者同步改引擎与本文件，不允许静默漂移。

夹具矩阵恰五格：①字段齐全且在 wiki/sources ②仅 status active
③仅 gate_decision active ④两者皆非 active ⑤字段齐全但落在 staging/candidate。
恰第①格 promoted，其余四格一律排除。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from akb2.retrieval import engine

# 删除前 src/akb2/quality/promote.py:518 的 MACHINE_REVIEWER 值
_PROMOTED_REVIEWER = "akb2-quality-loop-v1"


def test_engine_reviewer_matches_frozen_promote_signature() -> None:
    """引擎的机器 reviewer 必须等于现存已升格笔记携带的签名。

    改了引擎常量却没有同步迁移存量笔记，检索会静默看不到全部已升格内容。
    """
    assert engine.MACHINE_REVIEWER == _PROMOTED_REVIEWER


def test_engine_regexes_encode_promote_semantics() -> None:
    """引擎四个行首锚定正则的语义与升格写入口径一致（值级断言）。"""
    # is_promoted 的判据 = (page_)status active + gate_decision active +
    # reviewer = 冻结的升格签名
    promoted_fm = (
        "---\n"
        "title: probe\n"
        "page_status: active\n"
        "status: active\n"
        "gate_decision: active\n"
        f"reviewer: {_PROMOTED_REVIEWER}\n"
        "reviewed_at: 2026-01-01\n"
        "---\n"
    )
    assert engine.is_promoted(promoted_fm) is True
    # reviewer 值偏离权威签名 → 不 promoted
    tampered = promoted_fm.replace(
        f"reviewer: {_PROMOTED_REVIEWER}", "reviewer: someone-else"
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
    reviewer = _PROMOTED_REVIEWER

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
