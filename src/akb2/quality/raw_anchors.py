r"""raw 锚点权威 parser — 全链唯一的 §-anchor 解析口径(P1a)。

历史病灶:全链至少 5 套 anchor 口径互相打架——distill/F4 认下划线仅 heading 形态,
checks/F5 认 4 形态但不认下划线 → 真实下划线锚 F4 认、F5 拒,直接制造双 fail。
本模块冻结一套权威符号,quality 包内其余模块一律委托,不得再散落 `§[A-Z0-9` 本地 regex
（由 rg 守护测试锁）。

canonical 值规则（[B3],核对 raw_archiver.py:62-88 ANCHOR_DEFINITIONS 实际产出形态）:
  - 已知 raw_archiver marker 后缀 `_CONTENT_START` / `_CONTENT_END` 一律剥离归一
    → `§A_CONTENT_START` 的锚名是 `§A`、`§Z_CONTENT_END` 的锚名是 `§Z`（位置哨兵,
    后缀非语义）。非贪婪消歧：剥离后须仍是合法 `§[A-Z0-9]+` 才生效。
  - 业务下划线锚（raw_archiver 生成的 `§I_LIMITATIONS` / `§B_INTRODUCTION` 类）保留
    完整名——后缀是语义章节名,剥离会丢信息。
  - 裸 `§X`（无后缀,fixture heading 形态 `## §A Overview`）→ `§X`。

声明形态（[B3]+[B4]）:
  - heading `^#{1,6}\s+§X...` 与 marker `^§X(_SUFFIX)?` 行首:全 raw 生效
  - 表格单元 `| §X |` 与行首 `[raw §X]`:只在 `## 锚点目录` 块内识别（防正文表格/示例行
    偶然提到 `§Z` 被当声明）

引用形态:
  - `RAW_ANCHOR_REF_RE` = `\[raw\s+(§[A-Z0-9_]+)\]`——全链统一认下划线,否则 A3/How/repair
    解析不到下划线引用形成新静默绕过。引用与声明都经 `canonicalize_anchor` 后再比对。

fail-closed:空 raw → 空集;匹配不上 → 维持原状（不造假 anchor）。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# canonical token pattern（全链唯一定义点）
# ---------------------------------------------------------------------------

# 锚点 token:§ + 字母/数字/下划线。下划线为业务锚（§I_LIMITATIONS）保留。
ANCHOR_TOKEN_PAT: str = r"§[A-Z0-9_]+"
# 单个 token 全匹配（校验 LLM 产的 anchor 字段值）
ANCHOR_VALUE_RE: re.Pattern[str] = re.compile(rf"^{ANCHOR_TOKEN_PAT}$")
# 在文本中找所有 token（保守:只认 § 起的连续 [A-Z0-9_]）
ANCHOR_TOKEN_RE: re.Pattern[str] = re.compile(ANCHOR_TOKEN_PAT)

# [raw §X] 引用形态——认下划线（[raw §I_LIMITATIONS] 合法）
RAW_ANCHOR_REF_PAT: str = rf"\[raw\s+({ANCHOR_TOKEN_PAT})\]"
RAW_ANCHOR_REF_RE: re.Pattern[str] = re.compile(RAW_ANCHOR_REF_PAT)

# 剥离归一的后缀（位置哨兵,非语义章节名）
_STRIP_SUFFIXES: tuple[str, ...] = ("_CONTENT_START", "_CONTENT_END")
# 剥离后须仍是合法裸锚（§+至少一个字母/数字）,防 `§_CONTENT_START` 误剥
_BARE_ANCHOR_RE: re.Pattern[str] = re.compile(r"^§[A-Z0-9]+$")


def canonicalize_anchor(token: str) -> str:
    """归一化锚点 token。

    `§A_CONTENT_START` → `§A`；`§Z_CONTENT_END` → `§Z`；
    `§I_LIMITATIONS` → `§I_LIMITATIONS`（保留）；`§A` → `§A`。
    非 str/空 → 原样返回。
    """
    if not isinstance(token, str) or not token:
        return token  # type: ignore[return-value]
    for suffix in _STRIP_SUFFIXES:
        if token.endswith(suffix):
            stripped = token[: -len(suffix)]
            if _BARE_ANCHOR_RE.match(stripped):
                return stripped
    return token


# ---------------------------------------------------------------------------
# 声明形态 regex
# ---------------------------------------------------------------------------

# heading 形态（全局）:`# §A`、`## §A.`、`### §I_LIMITATIONS 标题`
_HEADING_ANCHOR_RE: re.Pattern[str] = re.compile(
    rf"(?m)^#{{1,6}}\s+({ANCHOR_TOKEN_PAT})(?:[.\s:：]|$)"
)
# marker 形态（全局,行首无 #）:raw_archiver 注入的 `§A_CONTENT_START` / `§I_LIMITATIONS`
# 行首裸 §,后随空白/行尾（排除 [/#/|/- 起首的行）
_MARKER_LINE_RE: re.Pattern[str] = re.compile(
    rf"(?m)^({ANCHOR_TOKEN_PAT})(?=\s|$)"
)
# 目录块内表格单元 `| §X |`
_DIR_TABLE_RE: re.Pattern[str] = re.compile(
    rf"(?m)^\|\s*({ANCHOR_TOKEN_PAT})\s*\|"
)
# 目录块内行首 `[raw §X]`（声明形态,只在目录块）
_DIR_RAWLINE_RE: re.Pattern[str] = re.compile(
    rf"(?m)^\[raw\s+({ANCHOR_TOKEN_PAT})\]"
)

# `## 锚点目录` 块（raw_archiver 生成的目录区）
_TOC_HEADING_RE: re.Pattern[str] = re.compile(r"(?m)^##\s*锚点目录\s*$")
_NEXT_H2_RE: re.Pattern[str] = re.compile(r"(?m)^##\s+")


def _directory_block_range(raw_content: str) -> tuple[int, int]:
    """返回 `## 锚点目录` 块的 [start, end) 字符区间；无则 (-1, -1)。

    块从 `## 锚点目录` 标题行起到下一个 `## ` 标题或文末。
    """
    m = _TOC_HEADING_RE.search(raw_content)
    if not m:
        return -1, -1
    start = m.start()
    rest_start = m.end()
    nxt = _NEXT_H2_RE.search(raw_content, rest_start)
    end = nxt.start() if nxt else len(raw_content)
    return start, end


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def declared_anchor_positions(raw_content: str | None) -> list[tuple[int, str]]:
    """返回 raw 内所有声明锚点的 (位置, canonical 锚点) 有序列表（按位置升序）。

    heading + marker 全局；table cell + 行首 [raw §X] 只在 `## 锚点目录` 块内。
    每个 token 经 canonicalize_anchor 归一。
    """
    if not raw_content:
        return []
    decls: list[tuple[int, str]] = []
    for m in _HEADING_ANCHOR_RE.finditer(raw_content):
        decls.append((m.start(), canonicalize_anchor(m.group(1))))
    for m in _MARKER_LINE_RE.finditer(raw_content):
        decls.append((m.start(), canonicalize_anchor(m.group(1))))
    ds, de = _directory_block_range(raw_content)
    if ds >= 0:
        for m in _DIR_TABLE_RE.finditer(raw_content):
            if ds <= m.start() < de:
                decls.append((m.start(), canonicalize_anchor(m.group(1))))
        for m in _DIR_RAWLINE_RE.finditer(raw_content):
            if ds <= m.start() < de:
                decls.append((m.start(), canonicalize_anchor(m.group(1))))
    decls.sort(key=lambda x: x[0])
    return decls


def declared_anchor_set(raw_content: str | None) -> set[str]:
    """返回 raw 内所有声明锚点的 canonical 集合。

    空 raw → 空集（fail-closed:调用方对空集 → 全部引用判不在 → 拒,不造假）。
    """
    return {anchor for _, anchor in declared_anchor_positions(raw_content)}


def nearest_preceding_anchor(
    raw_content: str | None,
    span_start: int,
    decls: list[tuple[int, str]] | None = None,
) -> str:
    """把字符位置映射到最近的在其之前（含同位）的已声明锚点。

    语义严格 fail-closed:无任何声明锚点 → 返空串（caller 视为不 eligible / 不渲染）。
    span_start 先于所有声明（frontmatter/prologue 罕见）→ 返空串（不回落首锚,
    防把 frontmatter evidence 错配到首锚）。compose 渲染层若需 lenient 回落,
    自行 `decls[0][1]`（保留既有 must_cover 渲染兜底）。

    Args:
        raw_content: raw 全文（decls=None 时内部计算）。
        span_start: 待映射的字符偏移。
        decls: 预计算的 declared_anchor_positions 结果（复用,避免 N 次重算）。
    """
    if decls is None:
        if not raw_content:
            return ""
        decls = declared_anchor_positions(raw_content)
    if not decls:
        return ""
    chosen = ""
    for pos, anchor in decls:
        if pos <= span_start:
            chosen = anchor
        else:
            break
    return chosen


# ---------------------------------------------------------------------------
# 向后兼容别名（供 checks_structural 历史导出名过渡,消费方逐步改用 raw_anchors.*）
# ---------------------------------------------------------------------------

# 历史 `RAW_ANCHOR_DECL_RE`（string）→ 现用 canonical token pattern（含下划线）
RAW_ANCHOR_DECL_RE = ANCHOR_TOKEN_PAT
# 历史 `RAW_ANCHOR_RE` / `RAW_ANCHOR_TOKEN_RE` / `RAW_ANCHOR_VALUE_RE`（compiled）
# → 现用权威 ref / token / value
RAW_ANCHOR_RE = RAW_ANCHOR_REF_RE
RAW_ANCHOR_TOKEN_RE = ANCHOR_TOKEN_RE
RAW_ANCHOR_VALUE_RE = ANCHOR_VALUE_RE
