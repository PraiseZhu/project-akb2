"""runbook 措辞闸（docs/runbooks/run-mcp-server.md）。

复用 tests/retrieval/test_doc_wording.py 的两类扫描，扫描对象换成 MCP 运行文档：

1. T2 禁用词：只读保证按 T1 措辞（防疏忽/漂移），不得冒充 T2（防恶意）。
   禁用词出现次数必须为零——反证：往 runbook 写一句「可防恶意绕过」，
   本测试必须红。
2. 漂移数值模式：文档不得硬编码会漂移的观测数字（结果条数/耗时/文件数），
   结果类数字一律写契约语义，命中数必须为零。

词表复用方式：tests/retrieval/ 仍无 __init__.py，与本目录同名的
test_doc_wording.py 在 pytest rootdir-relative 收集下会互相冲突
（曾触发 import file mismatch，已用本目录 __init__.py 解决收集侧问题）；
但两文件仍不能靠 `import tests.retrieval.test_doc_wording` 长期复用同一份
词表——那样会让本文件的失败依赖上游模块可导入，二者本应各自独立可测。
故仍复制词表常量 + 用 AST 哨兵锁同步（test_tables_in_sync_with_retrieval_gate：
从源文件提取 _T2_FORBIDDEN / _DRIFT_PATTERNS 逐项比对，上游改词表而本文件
未跟 → 红）。

只扫 runbook 正文；本测试文件自身的禁用词表不在扫描域内。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_RUNBOOK = Path("docs/runbooks/run-mcp-server.md")
_SOURCE_GATE = Path("tests/retrieval/test_doc_wording.py")

# ---------------------------------------------------------------------------
# T2 禁用词表（复用自 tests/retrieval/test_doc_wording.py；改这里必须同步改那里）
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
# 漂移数值模式（复用自 tests/retrieval/test_doc_wording.py；同步义务同上）
# ---------------------------------------------------------------------------

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

# 放行清单：确认是契约常量而非观测值的行内标记（同步义务同上）
_ALLOWED_LINE_MARKERS = ("退出码", "缺省值", "默认值")


def _runbook_text(repo_root: Path) -> str:
    doc = repo_root / _RUNBOOK
    assert doc.is_file(), f"runbook 不存在: {doc}"
    return doc.read_text(encoding="utf-8")


def _line_of(text: str, pos: int) -> str:
    end = text.find("\n", pos)
    return text[pos:] if end == -1 else text[pos:end]


def _allowed_only(m: re.Match | None, text: str) -> bool:
    if m is None:
        return True
    return any(marker in _line_of(text, m.start()) for marker in _ALLOWED_LINE_MARKERS)


# ---------------------------------------------------------------------------
# 词表同步哨兵（「复用」的可执行形式）
# ---------------------------------------------------------------------------


def _extract_source_tables(repo_root: Path) -> dict[str, list]:
    """AST 提取 tests/retrieval/test_doc_wording.py 的两张词表与放行清单。"""
    src = repo_root / _SOURCE_GATE
    assert src.is_file(), (
        f"词表源文件不存在: {src}（本测试的禁用词表复用自该文件，"
        "若它被移动/改名，请同步更新本文件的 _SOURCE_GATE 与词表副本）"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    wanted = {"_T2_FORBIDDEN", "_DRIFT_PATTERNS", "_ALLOWED_LINE_MARKERS"}
    extracted: dict[str, list] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if name in wanted and value is not None:
            extracted[name] = ast.literal_eval(value)
    missing = wanted - set(extracted)
    assert not missing, (
        f"词表源文件缺常量 {sorted(missing)}（结构变更请同步本文件词表副本）"
    )
    return extracted


def test_tables_in_sync_with_retrieval_gate(repo_root):
    """词表同步哨兵：本地副本必须与 tests/retrieval/test_doc_wording.py 逐项一致。

    两文件各自独立可测、不互相 import（详见模块顶部说明），词表只能复制；
    本条让「上游改词表、本副本没跟」成为可测失败，而不是静默漂移。
    """
    source = _extract_source_tables(repo_root)
    assert _T2_FORBIDDEN == [tuple(x) for x in source["_T2_FORBIDDEN"]], (
        "_T2_FORBIDDEN 与 tests/retrieval/test_doc_wording.py 不一致——"
        "两处词表必须同步修改（上游刚扩了禁用词？）"
    )
    assert _DRIFT_PATTERNS == [tuple(x) for x in source["_DRIFT_PATTERNS"]], (
        "_DRIFT_PATTERNS 与 tests/retrieval/test_doc_wording.py 不一致——"
        "两处词表必须同步修改"
    )
    assert _ALLOWED_LINE_MARKERS == tuple(source["_ALLOWED_LINE_MARKERS"]), (
        "_ALLOWED_LINE_MARKERS 与源文件不一致——两处必须同步修改"
    )


# ---------------------------------------------------------------------------
# 措辞闸扫描
# ---------------------------------------------------------------------------


def test_no_t2_forbidden_words(repo_root):
    """T2 禁用词出现次数必须为零（T1 措辞边界）。"""
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
            if any(marker in _line_of(text, m.start()) for marker in _ALLOWED_LINE_MARKERS):
                continue
            hits.append(f"{m.group(0)!r}（{why}）")
    assert not hits, "runbook 硬编码了会漂移的数值: " + "; ".join(hits[:10])


def test_readonly_tier_is_stated_as_t1(repo_root):
    """正向锚定：runbook 必须显式声明 T1 等级（防漏写，与禁用词闸互补）。"""
    text = _runbook_text(repo_root)
    assert "只读保证等级：T1" in text, (
        "runbook 必须显式声明『只读保证等级：T1』（防疏忽/漂移，非防恶意）"
    )


def test_authoritative_startup_command_documented(repo_root):
    """正向锚定：cc-akb2-retrieval 权威启动命令必须在文档中出现（逐字）。"""
    text = _runbook_text(repo_root)
    expected = "PYTHONPATH=src python3 toolbox/cc-akb2-retrieval/mcp_server/server.py"
    assert expected in text, (
        f"runbook 缺权威启动命令行 {expected!r}（解释器/PYTHONPATH/入口路径三要素）"
    )


def test_forbidden_word_gate_actually_fires(repo_root):
    """闸门鉴别力自证：注入禁用词与漂移数值必须让扫描红（反证条款）。"""
    polluted = _runbook_text(repo_root) + "\n本 server 可防恶意绕过，返回 12 条。\n"
    t2_hits = [w for w, _ in _T2_FORBIDDEN if w in polluted]
    assert t2_hits, "禁用词闸对注入样本失明"
    drift_hits = [
        p for p, _ in _DRIFT_PATTERNS
        if not _allowed_only(re.search(p, polluted), polluted)
    ]
    assert drift_hits, "漂移数值闸对注入样本失明"
