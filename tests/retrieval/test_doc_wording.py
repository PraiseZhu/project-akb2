"""runbook 措辞闸（docs/runbooks/retrieval-interface.md）。

两类扫描（hardening 9 + gap-catalog 自托管操作合同第 5 问）：

1. T2 禁用词：只读保证按 T1 措辞（防疏忽/漂移），不得冒充 T2（防恶意）。
   禁用词出现次数必须为零——反证：往 runbook 写一句「可防恶意绕过」，
   本测试必须红。
2. 漂移数值模式：文档不得硬编码会漂移的观测数字（结果条数/耗时/文件数），
   结果类数字一律写契约语义（如「count 为零」），命中数必须为零——反证：
   写死「返回 12 条」或「耗时 3 秒」，本测试必须红。

只扫 runbook 正文；本测试文件自身的禁用词表不在扫描域内。
"""

from __future__ import annotations

import re
from pathlib import Path

_RUNBOOK = Path("docs/runbooks/retrieval-interface.md")

# ---------------------------------------------------------------------------
# T2 禁用词表（冒充防恶意/抗攻击等级的措辞）
# ---------------------------------------------------------------------------

# (词, 说明) —— 命中即红；说明只进失败信息，不进文档
_T2_FORBIDDEN: list[tuple[str, str]] = [
    ("防恶意", "只读保证是 T1（防疏忽），不得冒充防恶意等级"),
    ("抗攻击", "接口不以对抗性输入为验证对象"),
    ("防攻击", "同上，T2 等级措辞"),
    ("恶意构造", "对抗性场景不在承诺范围，不得写成已防"),
    ("恶意输入", "同上"),
    ("无法绕过", "不得声称不可绕过"),
    ("不可绕过", "同上"),
    ("保证安全", "只读实现不构成安全保证"),
    ("杜绝", "不得声称杜绝任何行为"),
    ("彻底防", "绝对化防护措辞"),
    ("完全防", "同上"),
    ("百分之百", "绝对化数字承诺"),
    ("绝对安全", "绝对化安全承诺"),
    ("安全保证", "与『保证安全』同族，倒序形式也拦"),
    ("immune", "英文同族：声称免疫"),
    ("unbreakable", "英文同族：声称不可破坏"),
    ("tamper-proof", "英文同族：声称防篡改"),
]

# ---------------------------------------------------------------------------
# 漂移数值模式（文档里硬编码的观测结果数字）
# ---------------------------------------------------------------------------

# 匹配语义：紧邻「返回/命中/耗时/文件数」等观测语境的裸数字
# （含中文数字常用档）。契约常量不拦：limit 默认值、退出码、T1/T2 等级名、
# §章节号、promote 四要素这类规格数字必须放行，否则该测试自身不可维护。
_DRIFT_PATTERNS: list[tuple[str, str]] = [
    (
        r"(?:返回|命中|检[索出]|收录)[^。\n]{0,6}?[0-9０-９一二三四五六七八九十百]+\s*(?:条|篇|个结果|个笔记)",
        "结果条数会随 vault 漂移，写契约语义（如『count 为零』），不写具体条数",
    ),
    (
        r"[0-9０-９]+\s*(?:秒|毫秒|ms|s)\s*(?:内|左右|上下)?[完执]成",
        "耗时随环境漂移，不写具体耗时",
    ),
    (
        r"(?:耗时|花费|花了)[^。\n]{0,6}?[0-9０-９一二三四五六七八九十百]+\s*(?:秒|毫秒|ms)",
        "同上，倒装形式",
    ),
    (
        r"[0-9０-９]+\s*(?:个文件|篇文件|个\s*\.md)",
        "文件数量随 vault 漂移，不写具体文件数",
    ),
    (
        r"(?:共|合计)[^。\n]{0,4}?[0-9０-９一二三四五六七八九十百]+\s*(?:条结果|个结果)",
        "总量数字会漂移",
    ),
]

# 放行清单：确认是契约常量而非观测值的行内标记（如「退出码为 2」不在
# 漂移模式内，无需豁免；此清单给未来需要豁免的规格数字留口子）
_ALLOWED_LINE_MARKERS = ("退出码", "缺省值", "默认值")


def _runbook_text(repo_root: Path) -> str:
    doc = repo_root / _RUNBOOK
    assert doc.is_file(), f"runbook 不存在: {doc}"
    return doc.read_text(encoding="utf-8")


def test_no_t2_forbidden_words(repo_root):
    """T2 禁用词出现次数必须为零（T1 措辞边界，hardening 9）。"""
    text = _runbook_text(repo_root)
    hits: list[str] = []
    for word, why in _T2_FORBIDDEN:
        count = text.count(word)
        if count:
            hits.append(f"{word!r}×{count}（{why}）")
    assert not hits, (
        "runbook 含 T2 禁用词（只读保证只到 T1 防疏忽，不冒充 T2）: "
        + "; ".join(hits)
    )


def test_no_drift_prone_numbers(repo_root):
    """漂移数值模式命中数必须为零（不硬编码会漂移的观测数字）。"""
    text = _runbook_text(repo_root)
    hits: list[str] = []
    for pattern, why in _DRIFT_PATTERNS:
        for m in re.finditer(pattern, text):
            line = text[m.start():text.find("\n", m.start())]
            if any(marker in line for marker in _ALLOWED_LINE_MARKERS):
                continue
            hits.append(f"{m.group(0)!r}（{why}）")
    assert not hits, "runbook 硬编码了会漂移的数值: " + "; ".join(hits[:10])


def test_readonly_tier_is_stated_as_t1(repo_root):
    """正向锚定：runbook 必须显式声明 T1 等级（防漏写，与禁用词闸互补）。

    只拦禁用词不要求声明，文档可以把只读等级整段删掉而不被发现——
    这条让「等级措辞缺失」本身成为可测失败。
    """
    text = _runbook_text(repo_root)
    assert "只读保证等级：T1" in text, (
        "runbook 必须显式声明『只读保证等级：T1』（防疏忽/漂移，非防恶意）"
    )


def test_forbidden_word_gate_actually_fires(tmp_path, repo_root):
    """闸门鉴别力自证：注入禁用词与漂移数值必须让扫描红（反证条款）。

    用内存中的污染文本直接跑同套扫描逻辑，验证两类模式都不是摆设。
    """
    polluted = _runbook_text(repo_root) + "\n本接口可防恶意绕过，返回 12 条。\n"
    t2_hits = [w for w, _ in _T2_FORBIDDEN if w in polluted]
    assert t2_hits, "禁用词闸对注入样本失明"
    drift_hits = [
        p for p, _ in _DRIFT_PATTERNS
        if not _allowed_only(re.search(p, polluted), polluted)
    ]
    assert drift_hits, "漂移数值闸对注入样本失明"


def _allowed_only(m: re.Match | None, text: str) -> bool:
    if m is None:
        return True
    line = text[m.start():text.find("\n", m.start())]
    return any(marker in line for marker in _ALLOWED_LINE_MARKERS)
