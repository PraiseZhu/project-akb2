"""raw 锚点化归档 — frontmatter 9 字段 + 锚点注入 + fail-closed。

公开函数:
    archive_raw(extract_result, output_dir, failures_path) -> ArchiveResult
    ANCHOR_DEFINITIONS: 锚点定义映射
    REQUIRED_ANCHORS: 必须注入的锚点键列表

产出格式:
    <slug>--raw--<YYYY-MM-DD>.md
    frontmatter 9 字段:
        artifact_type, source_slug, source_url, source_title,
        source_authors, source_publisher, source_published_at,
        fetched_at, fetched_via

golden-sample 步 2 标准:
    1. 文件名 <slug>--raw--<YYYY-MM-DD>.md
    2. frontmatter 9 字段
    3. 原文标题加锚点,每个锚点替换必须 assert 命中
    4. 带锚点目录表
    5. 英文原文不翻译
    6. 传播数据等时点信息标注实测时间

fail-closed (P3):
    任一锚点 assert 未命中 → 不写文件，落 anchor_mismatch 签名
    frontmatter 9 字段不齐 → 不写文件，落签名
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

# frontmatter 9 字段（golden-sample 步 2 标准 2）
FRONTMATTER_FIELDS: tuple[str, ...] = (
    "artifact_type",
    "source_slug",
    "source_url",
    "source_title",
    "source_authors",
    "source_publisher",
    "source_published_at",
    "fetched_at",
    "fetched_via",
)

ARTIFACT_TYPE = "raw"

# 锚点定义（golden-sample 步 2 标准 3）
# 格式: {anchor_key: placeholder_text}
# 每个锚点以 §X 标记注入原文，用于后续提炼引用。
ANCHOR_DEFINITIONS: dict[str, str] = {
    "A": "§A_CONTENT_START",
    "B": "§B_INTRODUCTION",
    "C": "§C_CONTEXT",
    "D": "§D_ARGUMENT",
    "E": "§E_CLAIMS",
    "F": "§F_EVIDENCE",
    "G": "§G_EXAMPLES",
    "H": "§H_HOW_STEPS",
    "I": "§I_LIMITATIONS",
    "J": "§J_DISCUSSION",
    "K": "§K_IMPLICATIONS",
    "L": "§L_RELATED_WORK",
    "M": "§M_PARAMS",
    "N": "§N_DATA",
    "O": "§O_METHODOLOGY",
    "P": "§P_RESULTS",
    "Q": "§Q_ANALYSIS",
    "R": "§R_RECOMMENDATIONS",
    "S": "§S_CODE",
    "T": "§T_CONFIGURATION",
    "U": "§U_BENCHMARKS",
    "V": "§V_OBSERVATIONS",
    "W": "§W_CAVEATS",
    "X": "§X_ALTERNATIVES",
    "Y": "§Y_REFERENCES",
    "Z": "§Z_CONTENT_END",
}

# 最少必须注入的锚点（golden-sample 步 2 要求至少实现这些）
REQUIRED_ANCHORS: tuple[str, ...] = ("A", "E", "H", "M", "Z")

# 锚点注入标记
ANCHOR_MARKER_LEFT = "§"
ANCHOR_MARKER_RIGHT = " "


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class ArchiveResult:
    """raw 归档结果。"""

    slug: str
    success: bool = False
    output_path: str | None = None
    missing_anchors: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    error: str | None = None
    anchor_count: int = 0


# ---------------------------------------------------------------------------
# 锚点注入
# ---------------------------------------------------------------------------


def _split_into_paragraphs(content: str) -> list[str]:
    """将正文按空行分割为段落。"""
    return [p.strip() for p in content.split("\n\n") if p.strip()]


def _inject_anchors(
    content: str,
    anchors: dict[str, str] | None = None,
) -> tuple[str, list[str], dict[str, int]]:
    """在正文中注入锚点标记。

    算法:
        1. 将正文按段落分割
        2. 分配锚点到段落: §A 在开头, §Z 在结尾, 其余锚点按段落均匀分布
        3. 段落数不足时，优先保留必需锚点（REQUIRED_ANCHORS），其余截断
        4. 每个锚点替换 assert 命中

    Args:
        content: 原文 markdown
        anchors: 要注入的锚点映射；None 时使用全部 ANCHOR_DEFINITIONS

    Returns:
        (带锚点的正文, 锚点目录条目列表, 锚点命中计数)
    """
    if anchors is None:
        anchors = ANCHOR_DEFINITIONS

    paragraphs = _split_into_paragraphs(content)
    if not paragraphs:
        return content, [], {}

    n_paras = len(paragraphs)

    # 确定要注入的锚点键列表
    # 段落数不足时优先保留 REQUIRED_ANCHORS，其余按字母序填充
    anchor_keys_all = list(anchors.keys())
    if n_paras < len(anchor_keys_all):
        required_keys = [k for k in anchor_keys_all if k in REQUIRED_ANCHORS]
        optional_keys = [k for k in anchor_keys_all if k not in REQUIRED_ANCHORS]
        selected = required_keys.copy()
        for k in optional_keys:
            if len(selected) >= n_paras:
                break
            selected.append(k)
        anchor_keys = selected
    else:
        anchor_keys = anchor_keys_all
    n_anchors = len(anchor_keys)

    # 分配锚点到段落: §A 在 para 0, §Z 在 para -1, 其余均匀分布
    assignment: dict[int, str] = {}
    if n_anchors >= 1 and n_paras >= 1:
        assignment[0] = anchor_keys[0]  # §A
    if n_anchors >= 2 and n_paras >= 2:
        assignment[n_paras - 1] = anchor_keys[-1]  # §Z

    # 中间锚点均匀分布
    middle_keys = anchor_keys[1:-1] if n_anchors > 2 else []
    if middle_keys and n_paras > 2:
        step = (n_paras - 2) / (len(middle_keys) + 1)
        for i, key in enumerate(middle_keys):
            para_idx = int(step * (i + 1)) + 1
            # 确保 para_idx 在有效范围内且不冲突
            para_idx = max(1, min(para_idx, n_paras - 2))
            if para_idx not in assignment:
                assignment[para_idx] = key
            else:
                # 冲突时找最近空位
                for offset in range(1, n_paras):
                    if para_idx + offset < n_paras - 1 and (para_idx + offset) not in assignment:
                        assignment[para_idx + offset] = key
                        break
                    if para_idx - offset > 0 and (para_idx - offset) not in assignment:
                        assignment[para_idx - offset] = key
                        break

    # 注入锚点
    toc_entries: list[str] = []
    hit_counts: dict[str, int] = {}
    new_paragraphs: list[str] = []

    for i, para in enumerate(paragraphs):
        if i in assignment:
            key = assignment[i]
            anchor_text = anchors[key]
            new_paragraphs.append(f"{anchor_text}\n\n{para}")
            toc_entries.append(f"- §{key}: {anchor_text.replace('§' + key + '_', '')}")
            hit_counts[key] = hit_counts.get(key, 0) + 1
        else:
            new_paragraphs.append(para)

    return "\n\n".join(new_paragraphs), toc_entries, hit_counts


def _assert_anchors(hit_counts: dict[str, int], required: tuple[str, ...]) -> list[str]:
    """检查必需锚点是否全部命中。

    Returns:
        缺失的锚点键列表（空列表 = 全部命中）。
    """
    missing: list[str] = []
    for key in required:
        if hit_counts.get(key, 0) == 0:
            missing.append(key)
    return missing


# ---------------------------------------------------------------------------
# frontmatter 构建
# ---------------------------------------------------------------------------


def _build_frontmatter(
    slug: str,
    url: str,
    title: str,
    author: str,
    publisher: str,
    published_at: str,
    fetched_via: str,
) -> dict[str, str]:
    """构建 9 字段 frontmatter。"""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "artifact_type": ARTIFACT_TYPE,
        "source_slug": slug,
        "source_url": url,
        "source_title": title,
        "source_authors": author,
        "source_publisher": publisher,
        "source_published_at": published_at,
        "fetched_at": now,
        "fetched_via": fetched_via,
    }


def _validate_frontmatter(fm: dict[str, str]) -> list[str]:
    """验证 frontmatter 9 字段齐全。

    Returns:
        缺失字段列表（空列表 = 全部齐）。
    """
    missing: list[str] = []
    for fname in FRONTMATTER_FIELDS:
        if not fm.get(fname):
            missing.append(fname)
    return missing


def _format_frontmatter_yaml(fm: dict[str, str]) -> str:
    """将 frontmatter dict 格式化为 YAML frontmatter 块。"""
    lines = ["---"]
    for key in FRONTMATTER_FIELDS:
        value = fm.get(key, "")
        # 值含特殊字符时用引号
        special_chars = (":", "#", "[", "]", "{", "}", "*", "&", "!", "|", ">", "'", '"')
        if any(c in value for c in special_chars):
            value = f'"{value}"'
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主归档函数
# ---------------------------------------------------------------------------


def archive_raw(
    slug: str,
    url: str,
    title: str,
    author: str,
    content: str,
    published_at: str = "",
    publisher: str = "",
    fetched_via: str = "defuddle",
    output_dir: str | Path | None = None,
    failures_path: str | None = None,
    anchors: dict[str, str] | None = None,
    required_anchors: tuple[str, ...] = REQUIRED_ANCHORS,
    date_str: str | None = None,
) -> ArchiveResult:
    """生成 raw 锚点化归档文件。

    Args:
        slug: 源标识（kebab-case）
        url: 源 URL
        title: 文章标题
        author: 作者
        content: 正文 markdown
        published_at: 发布时间（ISO-8601）
        publisher: 发布者/站点名
        fetched_via: 抓取方式（defuddle/scrapling/docling）
        output_dir: 输出目录（raw/ 目录路径）
        failures_path: 故障签名落账路径
        anchors: 锚点定义映射（None 使用全部 ANCHOR_DEFINITIONS）
        required_anchors: 必须全部命中的锚点键
        date_str: 覆盖日期（YYYY-MM-DD，默认今天）

    Returns:
        ArchiveResult 含成功/失败信息与输出路径。
    """
    if date_str is None:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    result = ArchiveResult(slug=slug)

    # 1. 构建 frontmatter
    fm = _build_frontmatter(
        slug=slug,
        url=url,
        title=title,
        author=author,
        publisher=publisher,
        published_at=published_at,
        fetched_via=fetched_via,
    )

    # 2. 验证 frontmatter 9 字段齐全
    missing_fields = _validate_frontmatter(fm)
    if missing_fields:
        result.missing_fields = missing_fields
        result.error = f"Frontmatter missing fields: {missing_fields}"
        if failures_path:
            _record_archive_fail(
                failures_path, slug, "anchor_mismatch",
                f"frontmatter missing: {missing_fields}",
            )
        return result

    # 3. 注入锚点
    if anchors is None:
        anchors = ANCHOR_DEFINITIONS

    anchored_content, toc_entries, hit_counts = _inject_anchors(content, anchors)
    result.anchor_count = len(hit_counts)

    # 4. 断言必需锚点全部命中（fail-closed）
    missing_anchors = _assert_anchors(hit_counts, required_anchors)
    if missing_anchors:
        result.missing_anchors = missing_anchors
        result.error = f"Required anchors not hit: {missing_anchors}"
        if failures_path:
            _record_archive_fail(
                failures_path, slug, "anchor_mismatch",
                f"missing anchors: {missing_anchors}",
            )
        return result

    # 5. 组装文件
    fm_block = _format_frontmatter_yaml(fm)
    toc_block = "## 锚点目录\n\n" + "\n".join(toc_entries) + "\n"

    raw_md = f"{fm_block}\n\n{toc_block}\n\n{anchored_content}\n"

    # 6. 写入文件
    filename = f"{slug}--raw--{date_str}.md"
    if output_dir is None:
        output_dir = Path(".")
    out_path = Path(output_dir) / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(raw_md, encoding="utf-8")

    result.success = True
    result.output_path = str(out_path)
    return result


def _record_archive_fail(
    failures_path: str,
    slug: str,
    error_class: str,
    detail: str,
) -> None:
    """记录归档失败签名。"""
    from src.akb2.loops.signatures import record_failure

    record_failure(
        failures_path=failures_path,
        error_class=error_class,
        node=f"intake.raw_archiver.{slug}",
        loop="loop1",
    )


# ---------------------------------------------------------------------------
# 辅助：从 ExtractResult 一键归档
# ---------------------------------------------------------------------------


def archive_from_extract_result(
    extract_result: Any,  # ExtractResult from extract.py
    output_dir: str | Path,
    failures_path: str | None = None,
    publisher: str = "",
    date_str: str | None = None,
) -> ArchiveResult:
    """从 ExtractResult 直接生成 raw 归档。

    便捷函数，串联 extract → archive 流程。
    """
    method_str = (
        extract_result.method.value
        if hasattr(extract_result.method, "value")
        else str(extract_result.method)
    )
    slug = (
        extract_result.source_slug
        if hasattr(extract_result, "source_slug")
        else "unknown"
    )
    return archive_raw(
        slug=slug,
        url=extract_result.url,
        title=extract_result.title,
        author=extract_result.author,
        content=extract_result.content,
        published_at=extract_result.published,
        publisher=publisher,
        fetched_via=method_str,
        output_dir=output_dir,
        failures_path=failures_path,
        date_str=date_str,
    )
