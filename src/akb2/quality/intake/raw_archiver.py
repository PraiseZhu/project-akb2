"""raw 锚点化归档 — frontmatter 9 字段 + 锚点注入 + fail-closed。

公开函数:
    archive_raw(extract_result, output_dir, failures_path) -> ArchiveResult
    archive_from_extract_result(extract_result, output_dir, ...) -> ArchiveResult
    ANCHOR_DEFINITIONS: 锚点定义映射
    REQUIRED_ANCHORS: 必须注入的锚点键列表

产出格式:
    <slug>--<article_id>--raw--<YYYY-MM-DD>.md
    frontmatter 9 字段:
        artifact_type, source_slug, source_url, source_title,
        source_authors, source_publisher, source_published_at,
        fetched_at, fetched_via

golden-sample 步 2 标准:
    1. 文件名 <slug>--<article_id>--raw--<YYYY-MM-DD>.md（F3 article_id 唯一化）
    2. frontmatter 9 字段
    3. 原文标题加锚点,每个锚点替换必须 assert 命中
    4. 带锚点目录表
    5. 英文原文不翻译
    6. 传播数据等时点信息标注实测时间

fail-closed (P3):
    任一锚点 assert 未命中 → 不写文件，落 anchor_mismatch 签名
    frontmatter 9 字段不齐 → 不写文件，落签名
    签名含 details/sample_error 字段（P3 排障）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.akb2.quality.intake.models import ExtractionContext, compute_article_id

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
    """将 frontmatter dict 格式化为 YAML frontmatter 块。

    F52: 序列化交给 yaml.safe_dump（逐字段，保持 FRONTMATTER_FIELDS 顺序）。
    旧手写引号逻辑漏 `@` 开头（zed-blog @nathansobo）与含换行值
    （smashing-magazine 多行作者）→ 产出不可解析 frontmatter → 下游
    _read_raw_frontmatter 吞错返 {} → wiki 页 source_url/author 全空。
    换行统一清洗为单空格（frontmatter 值语义上都是单行标量）。
    """
    import yaml

    lines = ["---"]
    for key in FRONTMATTER_FIELDS:
        value = fm.get(key, "")
        # 值内换行清洗为单空格（多行作者等抓取噪声）
        value = " ".join(str(value).split())
        # safe_dump 保证任意值可被 safe_load 还原（引号/转义由 PyYAML 决定）
        dumped = yaml.safe_dump(
            {key: value}, allow_unicode=True, default_flow_style=False, width=10**6
        ).strip()
        lines.append(dumped)
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
    fetched_via: str = "unknown",
    output_dir: str | Path | None = None,
    failures_path: str | None = None,
    anchors: dict[str, str] | None = None,
    required_anchors: tuple[str, ...] = REQUIRED_ANCHORS,
    date_str: str | None = None,
    article_id: str | None = None,
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
        article_id: 文章唯一短码（默认 compute_article_id(url)[:12]）

    Returns:
        ArchiveResult 含成功/失败信息与输出路径。
    """
    if date_str is None:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    # 计算 article_id（F3 唯一化）
    if article_id is None:
        article_id = compute_article_id(url) if url else "unknown000000"

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
                details=f"frontmatter missing: {missing_fields}",
                sample_error=f"Missing fields: {missing_fields}",
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
                details=f"missing anchors: {missing_anchors}",
                sample_error=f"Required anchors not hit: {missing_anchors}",
            )
        return result

    # 5. 组装文件
    fm_block = _format_frontmatter_yaml(fm)
    toc_block = "## 锚点目录\n\n" + "\n".join(toc_entries) + "\n"

    raw_md = f"{fm_block}\n\n{toc_block}\n\n{anchored_content}\n"

    # 6. 写入文件（F3: 文件名含 article_id 短码）
    filename = f"{slug}--{article_id}--raw--{date_str}.md"
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
    details: str = "",
    sample_error: str = "",
) -> None:
    """记录归档失败签名，含 details/sample_error 字段（P3 排障）。

    直接写入 JSONL 行（含 details/sample_error），不经过 record_failure 以保留扩展字段。
    """
    path = Path(failures_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record: dict[str, Any] = {
        "loop": "loop1",
        "node": f"intake.raw_archiver.{slug}",
        "error_class": error_class,
        "count": 1,
        "ts": ts,
        "details": details,
        "sample_error": sample_error,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 辅助：从 ExtractResult 一键归档
# ---------------------------------------------------------------------------


def archive_from_extract_result(
    extract_result: Any,  # ExtractResult from extract.py
    output_dir: str | Path,
    failures_path: str | None = None,
    publisher: str = "",
    date_str: str | None = None,
    source_metadata: ExtractionContext | None = None,
) -> ArchiveResult:
    """从 ExtractResult 直接生成 raw 归档。

    便捷函数，串联 extract → archive 流程。

    Args:
        extract_result: ExtractResult（含 url/title/author/content/method/published）
        output_dir: 输出目录
        failures_path: 故障签名落账路径
        publisher: 发布者/站点名（source_metadata 有值时优先从其获取）
        date_str: 覆盖日期（YYYY-MM-DD）
        source_metadata: ExtractionContext，携带 feed 元数据供 frontmatter 补全
    """
    method_str = (
        extract_result.method.value
        if hasattr(extract_result.method, "value")
        else str(extract_result.method)
    )

    # slug 优先从 source_metadata 取，其次从 extract_result
    if source_metadata is not None and source_metadata.source_slug:
        slug = source_metadata.source_slug
    elif hasattr(extract_result, "source_slug"):
        slug = extract_result.source_slug
    else:
        slug = "unknown"

    # publisher 优先从 source_metadata 取
    effective_publisher = publisher
    if source_metadata is not None and source_metadata.source_publisher:
        effective_publisher = source_metadata.source_publisher

    return archive_raw(
        slug=slug,
        url=extract_result.url,
        title=extract_result.title,
        author=extract_result.author,
        content=extract_result.content,
        published_at=extract_result.published,
        publisher=effective_publisher,
        fetched_via=method_str,
        output_dir=output_dir,
        failures_path=failures_path,
        date_str=date_str,
    )
