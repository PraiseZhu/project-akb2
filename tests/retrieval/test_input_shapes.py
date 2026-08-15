"""sc-engine-input-shapes：不可信读路径形状矩阵（四形状 × 两入口 = 八格）。

vault 笔记是外部内容，四种畸形形状不得让检索整体崩溃或静默吞掉整批结果：
  1. frontmatter 缺失 / YAML 畸形不可解析
  2. 超大文件（超读取预算 MAX_NOTE_BYTES）
  3. 非 UTF-8 字节
  4. （复合）同一 vault 内混合健康笔记 + 各畸形形状

每格断言（封死『零结果也算过』的假绿）：
  - 不抛未捕获异常
  - skipped 计数 ≥1 且列出被跳过的相对路径
  - 可解析笔记条数等于预期（健康笔记正常返回）

两入口行为一致（hardening 5）：函数入口 engine.scan_report 与
CLI 入口 python3 -m akb2.retrieval.cli（stdout JSON + stderr skipped 行）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from akb2.retrieval import engine

REPO_ROOT = Path(__file__).resolve().parents[2]

_HEALTHY_FM = """---
title: Healthy Note
page_status: active
status: active
slug: healthy-note
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-01-01
---
"""

_HEALTHY_BODY = """## 论证与解释

healthy token 目标内容。

### 操作步骤

1. 步骤 `echo healthy`
"""


def _healthy(name: str = "healthy.md") -> tuple[str, str]:
    return name, _HEALTHY_FM + _HEALTHY_BODY


def _make_sources(tmp_path: Path) -> Path:
    sources = tmp_path / "vault" / "wiki" / "sources"
    sources.mkdir(parents=True)
    return sources.parent.parent  # vault root


# ---------------------------------------------------------------------------
# 形状 1：frontmatter 缺失 / 畸形
# ---------------------------------------------------------------------------


def test_shape_missing_frontmatter_function_entry(tmp_path: Path) -> None:
    root = _make_sources(tmp_path)
    name, content = _healthy()
    (root / "wiki" / "sources" / name).write_text(content, encoding="utf-8")
    # 畸形：正文开头没有 --- 围栏
    (root / "wiki" / "sources" / "no-fm.md").write_text(
        "直接正文，没有 frontmatter token\n", encoding="utf-8"
    )

    report = engine.scan_report(root, "token")
    assert [r.path for r in report.results] == [f"wiki/sources/{name}"]
    skipped = dict(report.skipped)
    assert "wiki/sources/no-fm.md" in skipped


def test_shape_missing_frontmatter_cli_entry(tmp_path: Path) -> None:
    root = _make_sources(tmp_path)
    name, content = _healthy()
    (root / "wiki" / "sources" / name).write_text(content, encoding="utf-8")
    (root / "wiki" / "sources" / "no-fm.md").write_text(
        "直接正文，没有 frontmatter token\n", encoding="utf-8"
    )

    proc = _run_cli(root, "token")
    payload = json.loads(proc.stdout)
    assert payload["count"] == 1
    assert payload["results"][0]["path"] == f"wiki/sources/{name}"
    assert "skipped: wiki/sources/no-fm.md" in proc.stderr


def test_shape_broken_frontmatter_fence(tmp_path: Path) -> None:
    """frontmatter 有 --- 但围栏没闭合（畸形 YAML 区）→ 同样跳过计数。"""
    root = _make_sources(tmp_path)
    name, content = _healthy()
    (root / "wiki" / "sources" / name).write_text(content, encoding="utf-8")
    (root / "wiki" / "sources" / "broken-fence.md").write_text(
        "---\ntitle: never closed\ngate_decision: active\n" + _HEALTHY_BODY,
        encoding="utf-8",
    )
    report = engine.scan_report(root, "token")
    assert [r.path for r in report.results] == [f"wiki/sources/{name}"]
    assert "wiki/sources/broken-fence.md" in dict(report.skipped)


# ---------------------------------------------------------------------------
# 形状 2：超大文件（超读取预算）
# ---------------------------------------------------------------------------


def test_shape_oversize_function_entry(tmp_path: Path) -> None:
    root = _make_sources(tmp_path)
    name, content = _healthy()
    (root / "wiki" / "sources" / name).write_text(content, encoding="utf-8")
    big = root / "wiki" / "sources" / "huge.md"
    # 头部是合法 promoted frontmatter，正文超出预算
    filler = "x" * (engine.MAX_NOTE_BYTES + 1024)
    big.write_text(_HEALTHY_FM + filler, encoding="utf-8")

    report = engine.scan_report(root, "token")
    assert [r.path for r in report.results] == [f"wiki/sources/{name}"]
    assert dict(report.skipped).get("wiki/sources/huge.md") == "oversize"


def test_shape_oversize_cli_entry(tmp_path: Path) -> None:
    root = _make_sources(tmp_path)
    name, content = _healthy()
    (root / "wiki" / "sources" / name).write_text(content, encoding="utf-8")
    big = root / "wiki" / "sources" / "huge.md"
    big.write_text(
        _HEALTHY_FM + "y" * (engine.MAX_NOTE_BYTES + 1), encoding="utf-8"
    )

    proc = _run_cli(root, "token")
    payload = json.loads(proc.stdout)
    assert payload["count"] == 1
    assert "skipped: wiki/sources/huge.md (oversize)" in proc.stderr


# ---------------------------------------------------------------------------
# 形状 3：非 UTF-8 字节
# ---------------------------------------------------------------------------


def test_shape_non_utf8_function_entry(tmp_path: Path) -> None:
    root = _make_sources(tmp_path)
    name, content = _healthy()
    (root / "wiki" / "sources" / name).write_text(content, encoding="utf-8")
    bad = root / "wiki" / "sources" / "latin1.md"
    bad.write_bytes(
        b"---\ntitle: \xc3\x28 broken\n---\n\xff\xfe binary garbage\n"
    )

    report = engine.scan_report(root, "token")
    assert [r.path for r in report.results] == [f"wiki/sources/{name}"]
    assert dict(report.skipped).get("wiki/sources/latin1.md") == "non_utf8"


def test_shape_non_utf8_cli_entry(tmp_path: Path) -> None:
    root = _make_sources(tmp_path)
    name, content = _healthy()
    (root / "wiki" / "sources" / name).write_text(content, encoding="utf-8")
    (root / "wiki" / "sources" / "latin1.md").write_bytes(
        b"---\n---\n\xff\xfe\x80 binary\n"
    )

    proc = _run_cli(root, "token")
    payload = json.loads(proc.stdout)
    assert payload["count"] == 1
    assert "skipped: wiki/sources/latin1.md (non_utf8)" in proc.stderr


# ---------------------------------------------------------------------------
# 形状 4：复合 vault（全部形状同炉 + 多篇健康笔记）
# ---------------------------------------------------------------------------


def test_composite_all_shapes_function_entry(tmp_path: Path) -> None:
    """同炉四种形状 + 两篇健康笔记：两篇返回、畸形各自计数。"""
    root = _make_sources(tmp_path)
    (root / "wiki" / "sources" / "healthy-a.md").write_text(
        _HEALTHY_FM + _HEALTHY_BODY, encoding="utf-8"
    )
    (root / "wiki" / "sources" / "healthy-b.md").write_text(
        _HEALTHY_FM + _HEALTHY_BODY, encoding="utf-8"
    )
    (root / "wiki" / "sources" / "no-fm.md").write_text(
        "裸正文 token\n", encoding="utf-8"
    )
    (root / "wiki" / "sources" / "huge.md").write_text(
        _HEALTHY_FM + "z" * (engine.MAX_NOTE_BYTES + 1), encoding="utf-8"
    )
    (root / "wiki" / "sources" / "bin.md").write_bytes(b"\xff\xfe\x00 raw")

    report = engine.scan_report(root, "token")
    assert {r.path for r in report.results} == {
        "wiki/sources/healthy-a.md",
        "wiki/sources/healthy-b.md",
    }
    skipped = dict(report.skipped)
    assert skipped["wiki/sources/no-fm.md"] == "frontmatter_unparsable"
    assert skipped["wiki/sources/huge.md"] == "oversize"
    assert skipped["wiki/sources/bin.md"] == "non_utf8"


def test_composite_all_shapes_cli_entry(tmp_path: Path) -> None:
    root = _make_sources(tmp_path)
    (root / "wiki" / "sources" / "healthy-a.md").write_text(
        _HEALTHY_FM + _HEALTHY_BODY, encoding="utf-8"
    )
    (root / "wiki" / "sources" / "no-fm.md").write_text(
        "裸正文 token\n", encoding="utf-8"
    )
    (root / "wiki" / "sources" / "bin.md").write_bytes(b"\xff raw")

    proc = _run_cli(root, "token")
    payload = json.loads(proc.stdout)
    assert payload["count"] == 1
    assert payload["results"][0]["path"] == "wiki/sources/healthy-a.md"
    assert proc.returncode == 0
    assert proc.stderr.count("skipped: ") == 2


def test_search_backcompat_no_exception_on_shapes(tmp_path: Path) -> None:
    """search() 兼容入口：同批畸形不抛异常、健康笔记条数符合预期。"""
    root = _make_sources(tmp_path)
    (root / "wiki" / "sources" / "healthy-a.md").write_text(
        _HEALTHY_FM + _HEALTHY_BODY, encoding="utf-8"
    )
    (root / "wiki" / "sources" / "bin.md").write_bytes(b"\xff\xfe raw")
    results = engine.search(root, "token")
    assert len(results) == 1


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


def _run_cli(root: Path, query: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable, "-m", "akb2.retrieval.cli",
            "search", query, "--limit", "10", "--vault-root", str(root),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"CLI 异常退出 rc={proc.returncode}: {proc.stderr}"
    )
    return proc
