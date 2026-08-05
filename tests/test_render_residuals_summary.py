"""`scripts/render_residuals_summary.py` 测试（SC-R4-04 / SC-R4-05）。

脚本不在 `src/` 下，按仓库既有约定（见 `tests/briefing/test_scoring_eval.py`）
用 `importlib` 按文件路径加载，不依赖 `scripts/` 是包。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PY = _REPO_ROOT / "scripts" / "render_residuals_summary.py"
_DOC_PATH = _REPO_ROOT / "docs" / "allowed-residuals.md"


def _load_render_module():
    spec = importlib.util.spec_from_file_location("render_residuals_summary", _SCRIPT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


render_residuals_summary = _load_render_module()


def test_schedule_entry_states_runtime_pending():
    """SC-R4-04：backstop 第二时段 11:00 条目渲染文本必须含"运行态待 owner 重装"字面。

    该条目本就登记了"模板已改，运行态待 owner 重装"——这是事故核心修复尚未
    在生产生效的唯一权威说明。lead 生成 PR 正文时应直接复制本脚本的渲染结果，
    而不是手抄（手抄正是历史上漏写这句话的根因）。
    """
    entries = render_residuals_summary.parse_entries(_DOC_PATH)
    matches = [e for e in entries if "backstop 第二时段 11:00" in e.title]
    assert matches, "未在 docs/allowed-residuals.md 找到 backstop 第二时段 11:00 条目"

    rendered = render_residuals_summary.render_entry(matches[0])
    assert "运行态待 owner 重装" in rendered


def test_entry_count_matches_document_independently():
    """SC-R4-05：脚本条目计数必须与测试独立重数的 `### ` 行数逐一相等。

    两边各自独立解析文档（脚本走 parse_entries，测试走裸正则数行），不共享
    任何硬编码常量——文档增删条目时两边天然同步，不需要人去改数字。
    """
    text = _DOC_PATH.read_text(encoding="utf-8")
    independent_count = len(re.findall(r"^### (?!#)", text, flags=re.MULTILINE))

    assert independent_count > 0, "文档里没有解析到任何 ### 级条目，测试本身失效"
    assert render_residuals_summary.count_entries(_DOC_PATH) == independent_count


def test_entry_count_is_derived_not_hardcoded(tmp_path):
    """`test_entry_count_matches_document_independently` 有个洞：两边读的是同一份
    真实文档，若 `count_entries` 被写死成当前恰好正确的常量，那条测试也会通过
    ——它只能抓「错的常量」，抓不到「硬编码」本身（R3 教训同构：测试通过的
    原因不是它声称在守的那条性质）。

    本用例用一份条目数与真实文档不同的合成文档，堵死这个洞：任何硬编码常量
    都不可能同时满足真实文档（9 条）和这份合成文档（3 条）两个断言。
    """
    synthetic_doc = tmp_path / "synthetic-allowed-residuals.md"
    synthetic_doc.write_text(
        "\n".join(
            [
                "# 合成测试文档",
                "",
                "## 批次 A（干扰项，不是条目）",
                "",
                "### 条目一",
                "正文一",
                "",
                "#### 条目一的四级子标题（不应被计为条目）",
                "四级子标题正文，不应被误计",
                "",
                "### 条目二",
                "正文二",
                "",
                "## 批次 B（干扰项，不是条目）",
                "",
                "### 条目三",
                "正文三",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert render_residuals_summary.count_entries(synthetic_doc) == 3
