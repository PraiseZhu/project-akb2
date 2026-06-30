"""共享 chrome 剥离 helper — intake extract 与 calibration raw 组装共用 (WP-1 / D1)。

保守正则剥离站点导航 chrome，返回 ``(clean_content, diagnostics)``。
设计原则（不误伤正文）：
  - 整行匹配才删，不触碰内联 token；
  - 导航链接块需连续 ≥2 行才删（孤立单行链接可能是正文合法引用）；
  - 含空 href ``[text](#)`` 的行需短（≤80 字符）才删，避免误伤含页内锚的散文。

剥离类别（对照 root-cause trace 92c97 langchain raw）：
  - ``Go back to ...`` 返回导航
  - 单独 ``Share`` 分享按钮
  - ``(#)`` 空锚 / 含 ``[text](#)`` 空 href 链接的短行
  - 纯图片行 ``![alt](url)``
  - 站内导航链接块：连续 ≥2 行、每行仅由内部 href（``/...`` 或 ``#``）markdown 链接组成

干净页零回归是硬约束（SC-1）：上述模式不在正文散文/合法外链/列表项上命中。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 单行 chrome 模式（无歧义，命中即删整行）
# ---------------------------------------------------------------------------

# "Go back to blog" 之类的返回导航（IGNORECASE 容错大小写）
_GO_BACK_RE = re.compile(r"^\s*Go back to\b.*$", re.IGNORECASE)

# 单独 "Share" 分享按钮（整行只有 Share 词）
_SHARE_BUTTON_RE = re.compile(r"^\s*Share\s*$")

# 独立 (#) 空锚
_EMPTY_ANCHOR_RE = re.compile(r"^\s*\(#\)\s*$")

# 纯图片行 ![alt](url)（整行只有一张图）
_PURE_IMAGE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")

# 含 [text](#) 空 href 链接的短行（≤80 字符）—— 抓 "[Create agents](#) Share" 这类
_EMPTY_HREF_LINK_RE = re.compile(r"\[[^\]]*\]\(#\)")
_EMPTY_HREF_LINE_MAX_LEN = 80

# ---------------------------------------------------------------------------
# 站内导航链接块（连续 ≥2 行才删）
# ---------------------------------------------------------------------------

# 整行仅由内部 href（/path 或 #）markdown 链接组成，链接文本 ≤40 字符，无 bullet/无散文
_INTERNAL_LINK_ONLY_RE = re.compile(
    r"^\s*(?:\[[^\]]{0,40}\]\((?:/[^)]*|#)\)\s*)+\s*$"
)

# 锚点声明行（raw_archiver._inject_anchors 注入的 §A_CONTENT_START 等）
# —— 供 calibration 去重 hash 排除锚点行用（chrome 剥离本身不删它，留给 hash 专用路径）
ANCHOR_DECL_RE = re.compile(r"^§[A-Z]_[A-Z_]+\s*$")


@dataclass
class ChromeStripDiagnostics:
    """chrome 剥离诊断（fail-visible，可写 ledger）。"""

    stripped_lines: int = 0
    go_back: int = 0
    share: int = 0
    empty_anchor: int = 0
    pure_image: int = 0
    empty_href_link: int = 0
    nav_link_block_lines: int = 0
    matched_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stripped_lines": self.stripped_lines,
            "categories": {
                "go_back": self.go_back,
                "share": self.share,
                "empty_anchor": self.empty_anchor,
                "pure_image": self.pure_image,
                "empty_href_link": self.empty_href_link,
                "nav_link_block": self.nav_link_block_lines,
            },
        }


def _is_single_line_chrome(line: str) -> tuple[bool, str]:
    """单行 chrome 判定。返回 (是否 chrome, 类别名)。"""
    if _GO_BACK_RE.match(line):
        return True, "go_back"
    if _SHARE_BUTTON_RE.match(line):
        return True, "share"
    if _EMPTY_ANCHOR_RE.match(line):
        return True, "empty_anchor"
    if _PURE_IMAGE_RE.match(line):
        return True, "pure_image"
    # 含空 href 链接的短行（放最后，避免与上面精确模式冲突）
    if (
        len(line.strip()) <= _EMPTY_HREF_LINE_MAX_LEN
        and _EMPTY_HREF_LINK_RE.search(line)
    ):
        return True, "empty_href_link"
    return False, ""


def strip_chrome_blocks(content: str) -> tuple[str, ChromeStripDiagnostics]:
    """剥离站点导航 chrome，返回 ``(clean_content, diagnostics)``。

    保守策略：
      1. 单行无歧义 chrome（Go back to / Share / (#) / 纯图 / 空 href 短行）直接删；
      2. 站内导航链接块：连续 ≥2 个内部-href 链接行才整体删，孤立单行保留
         （干净页零回归：正文合法单链接不会被删）。
    """
    if not content:
        return content, ChromeStripDiagnostics()

    diag = ChromeStripDiagnostics()
    lines = content.split("\n")

    # 第一遍：标记单行 chrome 与"内部链接行"
    single_chrome_flags: list[tuple[bool, str]] = [
        _is_single_line_chrome(line) for line in lines
    ]
    internal_link_flags: list[bool] = [
        bool(_INTERNAL_LINK_ONLY_RE.match(line))
        and not single_chrome_flags[i][0]
        for i, line in enumerate(lines)
    ]

    # 导航链接块：连续 ≥2 个 internal_link 行才整体标删
    nav_delete = [False] * len(lines)
    i = 0
    while i < len(lines):
        if internal_link_flags[i]:
            j = i
            while j < len(lines) and internal_link_flags[j]:
                j += 1
            if j - i >= 2:
                for k in range(i, j):
                    nav_delete[k] = True
            i = j
        else:
            i += 1

    out: list[str] = []
    for idx, line in enumerate(lines):
        is_chrome, category = single_chrome_flags[idx]
        if is_chrome:
            diag.stripped_lines += 1
            if category == "go_back":
                diag.go_back += 1
            elif category == "share":
                diag.share += 1
            elif category == "empty_anchor":
                diag.empty_anchor += 1
            elif category == "pure_image":
                diag.pure_image += 1
            elif category == "empty_href_link":
                diag.empty_href_link += 1
            diag.matched_patterns.append(f"line {idx + 1}: {category}")
            continue
        if nav_delete[idx]:
            diag.stripped_lines += 1
            diag.nav_link_block_lines += 1
            diag.matched_patterns.append(f"line {idx + 1}: nav_link_block")
            continue
        out.append(line)

    return "\n".join(out), diag
