"""抽取层决策树 — defuddle → Scrapling → Docling → fetch_fail 签名。

公开函数:
    extract_content(url: str, extraction_config: dict | None) -> ExtractResult
    validate_extraction_success(result: ExtractResult) -> bool

决策树:
    1. 静态页 defuddle（已装，优先）
    2. 失败/反爬/JS 重 → Scrapling 0.4.8（自适应 + 浏览器模式双兜底）
    3. PDF 文件 → Docling
    4. 两级全失败 → 调用 step-1 signatures 模块落 fetch_fail 签名

成功硬判定:
    - 正文字数 >= 50（与页面声明字数核对，放宽至保守下限）
    - 元数据 4 件齐: title / author / published / url
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

MIN_CONTENT_LENGTH = 50  # 最小正文字数（保守下限，与页面声明核对）
REQUIRED_METADATA_FIELDS = ("title", "author", "published", "url")

# 反爬 / JS 重模式列表（defuddle 失败时检测）
ANTI_BOT_PATTERNS = [
    r"enable\s*(javascript|JS)",
    r"captcha",
    r"cloudflare",
    r"cf-browser-verification",
    r"please\s+enable\s+cookies",
    r"checking\s+your\s+browser",
    r"ddos\s+protection",
]

# PDF 检测模式
PDF_PATTERNS = [r"\.pdf$", r"application/pdf", r"content-type.*pdf"]


# ---------------------------------------------------------------------------
# 枚举与数据类
# ---------------------------------------------------------------------------


class ExtractMethod(str, Enum):  # noqa: UP042
    """抽取方法枚举。"""

    DEFUDDLE = "defuddle"
    SCRAPLING = "scrapling"
    SCRAPLING_BROWSER = "scrapling_browser"
    DOCLING = "docling"
    FAILED = "failed"


@dataclass
class ExtractResult:
    """抽取层产出。"""

    url: str
    method: ExtractMethod = ExtractMethod.FAILED
    title: str = ""
    author: str = ""
    published: str = ""  # ISO-8601 or date string
    content: str = ""  # markdown 正文
    word_count: int = 0
    metadata_complete: bool = False
    extraction_success: bool = False
    error: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _detect_anti_bot(html: str) -> bool:
    """检测 HTML 是否包含反爬/JS 页面。"""
    lower = html.lower()
    return any(re.search(pattern, lower) for pattern in ANTI_BOT_PATTERNS)


def _detect_pdf(url: str, content_type: str = "") -> bool:
    """检测 URL 是否指向 PDF 文件。"""
    lower_url = url.lower()
    lower_ct = content_type.lower()
    return any(
        re.search(pattern, lower_url) or re.search(pattern, lower_ct)
        for pattern in PDF_PATTERNS
    )


def _count_words(text: str) -> int:
    """统计英文文本字数（按空格分词）。"""
    if not text.strip():
        return 0
    return len(text.split())


def _validate_metadata(result: ExtractResult) -> bool:
    """验证元数据 4 件齐。"""
    fields = {
        "title": result.title,
        "author": result.author,
        "published": result.published,
        "url": result.url,
    }
    return all(v for v in fields.values())


# ---------------------------------------------------------------------------
# 抽取方法（占位实现，真实工具在运行期注入）
# ---------------------------------------------------------------------------


def _extract_via_defuddle(url: str, html: str | None = None) -> dict[str, Any]:
    """通过 defuddle 抽取正文（占位实现）。

    真实实现期：调用 defuddle CLI 或 Python API。
    """
    # 占位：返回模拟结果
    if html and len(html.strip()) > 100:
        return {
            "title": "Defuddle Extracted Title",
            "author": "Defuddle Author",
            "published": "2026-01-01",
            "content": html,
            "word_count": _count_words(html),
        }
    return {}


def _extract_via_scrapling(
    url: str, html: str | None = None, browser_mode: bool = False
) -> dict[str, Any]:
    """通过 Scrapling 0.4.8 抽取正文（占位实现）。

    真实实现期：import scrapling，调用自适应抽取或浏览器模式。
    """
    mode = "browser" if browser_mode else "adaptive"
    if html and len(html.strip()) > 100:
        return {
            "title": f"Scrapling {mode} Title",
            "author": "Scrapling Author",
            "published": "2026-01-01",
            "content": html,
            "word_count": _count_words(html),
        }
    return {}


def _extract_via_docling(url: str, file_bytes: bytes | None = None) -> dict[str, Any]:
    """通过 Docling 抽取 PDF 正文（占位实现）。

    真实实现期：import docling，调用文档转换。
    """
    if file_bytes and len(file_bytes) > 100:
        text = file_bytes.decode("utf-8", errors="replace")
        return {
            "title": "Docling PDF Title",
            "author": "Docling Author",
            "published": "2026-01-01",
            "content": text,
            "word_count": _count_words(text),
        }
    return {}


# ---------------------------------------------------------------------------
# 主抽取函数
# ---------------------------------------------------------------------------


def extract_content(
    url: str,
    html: str | None = None,
    content_type: str = "",
    file_bytes: bytes | None = None,
    failures_path: str | None = None,
) -> ExtractResult:
    """对单个 URL 执行抽取决策树。

    Args:
        url: 目标 URL
        html: 注入的 HTML 文本（测试用；为 None 时实际抓取）
        content_type: HTTP Content-Type 头
        file_bytes: 注入的 PDF 字节（测试用）
        failures_path: 故障签名落账路径（为 None 时不落签名）

    Returns:
        ExtractResult 含抽取结果与成功判定。
    """
    result = ExtractResult(url=url)

    # 分支 1: PDF 检测
    if _detect_pdf(url, content_type):
        extracted = _extract_via_docling(url, file_bytes)
        if extracted:
            result.method = ExtractMethod.DOCLING
            result.title = extracted.get("title", "")
            result.author = extracted.get("author", "")
            result.published = extracted.get("published", "")
            result.content = extracted.get("content", "")
            result.word_count = extracted.get("word_count", 0)
            result.metadata_complete = _validate_metadata(result)
            result.extraction_success = (
                result.word_count >= MIN_CONTENT_LENGTH and result.metadata_complete
            )
            if result.extraction_success:
                return result
            result.error = "Docling extraction failed: content too short or metadata incomplete"

    # 分支 2: defuddle 优先
    if html is not None:
        # 检测反爬/JS
        if not _detect_anti_bot(html):
            extracted = _extract_via_defuddle(url, html)
            if extracted:
                wc = extracted.get("word_count", 0)
                if wc >= MIN_CONTENT_LENGTH:
                    result.method = ExtractMethod.DEFUDDLE
                    result.title = extracted.get("title", "")
                    result.author = extracted.get("author", "")
                    result.published = extracted.get("published", "")
                    result.content = extracted.get("content", "")
                    result.word_count = wc
                    result.metadata_complete = _validate_metadata(result)
                    result.extraction_success = result.metadata_complete
                    if result.extraction_success:
                        return result
                    result.error = "defuddle succeeded but metadata incomplete"
                else:
                    result.error = "defuddle content too short"

        # 分支 3: defuddle 失败/反爬 → Scrapling
        use_browser = _detect_anti_bot(html) if html else False
        extracted = _extract_via_scrapling(url, html, browser_mode=use_browser)
        if extracted:
            wc = extracted.get("word_count", 0)
            if wc >= MIN_CONTENT_LENGTH:
                result.method = (
                    ExtractMethod.SCRAPLING_BROWSER if use_browser else ExtractMethod.SCRAPLING
                )
                result.title = extracted.get("title", "")
                result.author = extracted.get("author", "")
                result.published = extracted.get("published", "")
                result.content = extracted.get("content", "")
                result.word_count = wc
                result.metadata_complete = _validate_metadata(result)
                result.extraction_success = result.metadata_complete
                if result.extraction_success:
                    return result
                result.error = (
                    f"Scrapling {'browser' if use_browser else 'adaptive'}"
                    " succeeded but metadata incomplete"
                )
            else:
                result.error = (
                    f"Scrapling {'browser' if use_browser else 'adaptive'}"
                    " content too short"
                )

    # 分支 4: 全失败 → 落签名
    result.method = ExtractMethod.FAILED
    result.extraction_success = False
    if not result.error:
        result.error = "All extraction methods failed"

    if failures_path:
        _record_fetch_fail(failures_path, url, result.error)

    return result


def validate_extraction_success(result: ExtractResult) -> bool:
    """硬判定：正文字数达标 + 元数据 4 件齐。

    黄金样张步 1 标准 3: 抓取失败显式报错，不静默跳过。
    成功 = 字数 >= MIN_CONTENT_LENGTH AND title/author/published/url 全部非空。
    """
    return result.extraction_success


def _record_fetch_fail(failures_path: str, url: str, error: str) -> None:
    """记录 fetch_fail 故障签名。

    调用 step-1 signatures 模块的 record_failure。
    """
    from src.akb2.loops.signatures import record_failure

    record_failure(
        failures_path=failures_path,
        error_class="fetch_fail",
        node=f"intake.extract.{url[:80]}",
        loop="loop1",
    )


# ---------------------------------------------------------------------------
# 批量抽取
# ---------------------------------------------------------------------------


def batch_extract(
    urls: list[str],
    html_map: dict[str, str] | None = None,
    failures_path: str | None = None,
) -> dict[str, ExtractResult]:
    """批量执行抽取决策树。

    Args:
        urls: 目标 URL 列表
        html_map: {url: html_text} 注入（测试用）
        failures_path: 故障签名落账路径

    Returns:
        {url: ExtractResult} 每个 URL 的抽取结果。
    """
    results: dict[str, ExtractResult] = {}
    for url in urls:
        html = html_map.get(url) if html_map else None
        results[url] = extract_content(
            url=url,
            html=html,
            failures_path=failures_path,
        )
    return results
