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
    - 正文字数 >= 200（与页面声明字数核对，放宽至保守下限）
    - 元数据 4 件齐: title / author / published / url
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.akb2.quality.intake.models import ExtractionContext  # noqa: F401

logger = logging.getLogger(__name__)


def _docling_probe() -> None:
    """运行时探针：实例化 DocumentConverter 验证 docling 真实可用。"""
    from docling.document_converter import DocumentConverter
    DocumentConverter()

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

MIN_CONTENT_LENGTH = 200  # 最小正文字数（保守下限，与页面声明核对）
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
    TWITTER_ARTICLE = "twitter_article"
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


def _apply_chrome_strip(extracted: dict[str, Any]) -> dict[str, Any]:
    """WP-1/D1: 对抽取器返回的 content 剥 chrome + 重算 word_count。

    在 ``wc >= MIN_CONTENT_LENGTH`` 判定之前应用 → 剥离后 <200 走 fail-closed
    （对**剥离后**字数判定，非原始字数）。原地修改 extracted 的 content/word_count，
    返回 diagnostics dict（含 pre/post wc，供 result.raw_metadata fail-visible）。

    干净页零回归：clean prose 不命中任何 chrome 模式 → content/word_count 不变。
    """
    from src.akb2.quality.chrome_strip import strip_chrome_blocks

    content = extracted.get("content", "") or ""
    pre_wc = extracted.get("word_count") or _count_words(content)
    clean, diag = strip_chrome_blocks(content)
    post_wc = _count_words(clean)
    extracted["content"] = clean
    extracted["word_count"] = post_wc
    diag_dict = diag.to_dict()
    diag_dict["pre_strip_word_count"] = pre_wc
    diag_dict["post_strip_word_count"] = post_wc
    return diag_dict



def _validate_metadata(result: ExtractResult) -> bool:
    """验证元数据 4 件齐。"""
    return all([result.title, result.author, result.published, result.url])


def _apply_feed_fallback(
    result: ExtractResult,
    context: ExtractionContext | None,
) -> None:
    """将 ExtractionContext 的 feed 元数据回填到 result 中缺失的字段。

    fallback 顺序（四个字段独立判断）:
        title     ← result.title     or context.feed_title
        author    ← result.author    or context.feed_author or context.source_publisher
        published ← result.published or context.feed_published
        publisher ← result.raw_metadata["publisher"] or context.source_publisher

    author 语义为"可归属主体"而非"个人署名"：抽取器/feed 都拿不到作者时，
    回退到来源机构名（source_publisher）并标记 author_source=publisher_fallback，
    供 provenance 区分机构归属与个人署名。仅当连 publisher 都无时 author 才留空
    （→ _validate_metadata fail-closed）。

    此函数在 _validate_metadata 之前调用，仅补充空字段，不覆盖已有值。
    context 为 None 时本函数为 no-op。
    """
    if context is None:
        return

    if not result.title and context.feed_title:
        result.title = context.feed_title
    # author: extracted → feed_author → publisher（可归属主体，非个人署名硬性）
    if not result.author:
        if context.feed_author:
            result.author = context.feed_author
        elif context.source_publisher:
            result.author = context.source_publisher
            result.raw_metadata["author_source"] = "publisher_fallback"
    if not result.published and context.feed_published:
        result.published = context.feed_published
    # publisher 存入 raw_metadata 供下游消费
    if not result.raw_metadata.get("publisher") and context.source_publisher:
        result.raw_metadata["publisher"] = context.source_publisher


# ---------------------------------------------------------------------------
# 抽取方法（占位实现，真实工具在运行期注入）
# ---------------------------------------------------------------------------


def _extract_via_defuddle(url: str, html: str | None = None) -> dict[str, Any]:
    """通过 defuddle CLI 抽取正文。

    调用 defuddle parse <tmpfile> --json -m，解析 JSON 输出提取
    title / author / published / content（markdown 正文） / word_count。

    html 为 None 时直接传 url 给 defuddle（defuddle 支持 URL 输入）。
    返回空 dict 表示抽取失败。
    """
    try:
        if html is not None:
            # HTML 写入临时文件
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".html", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(html)
                tmp_path = tmp.name
            source_arg = tmp_path
        else:
            source_arg = url
            tmp_path = None

        try:
            proc = subprocess.run(
                ["defuddle", "parse", source_arg, "--json", "-m"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

        if proc.returncode != 0:
            logger.debug("defuddle exited %d: %s", proc.returncode, proc.stderr[:200])
            return {}

        data: dict[str, Any] = json.loads(proc.stdout)
        content: str = data.get("content", "")
        word_count: int = data.get("wordCount") or _count_words(content)

        return {
            "title": data.get("title", ""),
            "author": data.get("author", ""),
            "published": data.get("published", ""),
            "content": content,
            "word_count": word_count,
        }

    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("defuddle extraction failed: %s", exc)
        return {}


# P0-a：HTML 噪音清洗。scrapling fallback 旧逻辑把整页 rendered HTML（含 <script> JS bundle）
# 当正文返回，word_count 爆到 9 万~36 万、source_operation 被 JS API 名污染顶垮 how_tendency
# （满仓 5 篇 #2/#7/#12/#13/#16）。去 script/style 噪音后让 defuddle 抽，兜底也用纯文本。
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_noise(html: str) -> str:
    """去 <script>/<style> 块（保留其余结构供 defuddle 抽取）。"""
    return _SCRIPT_STYLE_RE.sub(" ", html)


def _html_to_clean_text(html: str) -> str:
    """去 script/style + 所有标签 + 压空白，得纯正文文本（fallback 用，非脏 HTML）。"""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _HTML_TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_via_scrapling(
    url: str, html: str | None = None, browser_mode: bool = False
) -> dict[str, Any]:
    """通过 Scrapling 抽取正文。

    import gate：scrapling 不可用时 log warning 并返回空 dict。
    自适应模式（browser_mode=False）：Fetcher.get(url)（scrapling 0.4.8 classmethod）
    浏览器模式（browser_mode=True）：StealthyFetcher.fetch(url)（classmethod）

    返回空 dict 表示抽取失败。
    """
    from akb2.quality.intake import _check_tool_available
    if not _check_tool_available("scrapling", logger=logger):
        return {}

    try:
        if browser_mode:
            from scrapling import StealthyFetcher

            page = StealthyFetcher.fetch(url)
        else:
            from scrapling import Fetcher

            page = Fetcher.get(url)

        # Scrapling 只负责"绕反爬拿渲染后的 HTML"；抽取交给 defuddle。
        rendered_html: str = getattr(page, "html_content", None) or str(page.body or "")
        if not rendered_html.strip():
            return {}

        # 转交 defuddle：先去 script/style 噪音再抽（P0-a），避免 JS bundle 当正文。
        clean_html = _strip_html_noise(rendered_html)
        extracted = _extract_via_defuddle(url, html=clean_html)
        if extracted and extracted.get("content"):
            return extracted

        # defuddle 仍抽不出时：用去标签纯文本兜底（非原始脏 HTML）。
        # P0-a：旧逻辑返回整页 rendered_html（含 JS bundle），word_count 爆到 27 万、
        # source_operation 被 JS API 名污染顶垮 how_tendency。去噪后正文不足则 fail-closed。
        clean_text = _html_to_clean_text(rendered_html)
        wc = _count_words(clean_text)
        if wc < MIN_CONTENT_LENGTH:
            # 去噪后正文不足 → 抽取失败（fail-closed），不喂脏数据进下游。
            return {}
        title: str = str(getattr(page, "title", "") or "")
        return {
            "title": title,
            "author": "",
            "published": "",
            "content": clean_text,
            "word_count": wc,
        }

    except Exception as exc:
        logger.warning("scrapling extraction failed: %s", exc)
        return {}


def _extract_via_docling(url: str, file_bytes: bytes | None = None) -> dict[str, Any]:
    """通过 Docling 抽取 PDF 正文。

    import gate：docling 不可用时 log warning 并返回空 dict。
    file_bytes 落临时 PDF 文件，调用 DocumentConverter().convert()，
    返回 result.document.export_to_markdown() 内容。

    返回空 dict 表示抽取失败。
    """
    from akb2.quality.intake import _check_tool_available
    if not _check_tool_available("docling", probe=_docling_probe, logger=logger):
        return {}

    if not file_bytes:
        logger.debug("_extract_via_docling: no file_bytes provided")
        return {}

    try:
        from docling.document_converter import DocumentConverter

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_pdf = tmp.name

        try:
            result = DocumentConverter().convert(tmp_pdf)
        finally:
            Path(tmp_pdf).unlink(missing_ok=True)

        content: str = result.document.export_to_markdown()
        word_count: int = _count_words(content)

        # Docling result 可能携带元数据
        meta = getattr(result, "metadata", None) or {}
        title: str = str(meta.get("title", "") or "")
        author: str = str(meta.get("author", "") or "")
        published: str = str(meta.get("date", "") or "")

        return {
            "title": title,
            "author": author,
            "published": published,
            "content": content,
            "word_count": word_count,
        }

    except Exception as exc:
        logger.warning("docling extraction failed: %s", exc)
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
    extraction_config: ExtractionContext | None = None,
) -> ExtractResult:
    """对单个 URL 执行抽取决策树。

    Args:
        url: 目标 URL
        html: 注入的 HTML 文本（测试用；为 None 时实际抓取）
        content_type: HTTP Content-Type 头
        file_bytes: 注入的 PDF 字节（测试用）
        failures_path: 故障签名落账路径（为 None 时不落签名）
        extraction_config: ExtractionContext，携带 feed 元数据供 fallback。
            当抽取器无法提取 title/author/published/publisher 时，
            从 context 中补充。四者皆缺且 _validate_metadata 仍失败时，
            fail-closed 不产 raw（不写签名，返回失败 ExtractResult）。

    Returns:
        ExtractResult 含抽取结果与成功判定。
    """
    result = ExtractResult(url=url)

    # X Article 路径：正文已由 twitter article -m 取回，禁止再走网页抓取。
    prefetched = None
    if extraction_config is not None:
        prefetched = getattr(extraction_config, "prefetched_content", None)
        prefetched_via = getattr(extraction_config, "prefetched_via", "") or ""
        if prefetched:
            result.method = (
                ExtractMethod.TWITTER_ARTICLE
                if prefetched_via == "twitter_article"
                else ExtractMethod.DEFUDDLE
            )
            result.title = extraction_config.feed_title or ""
            result.author = extraction_config.feed_author or ""
            result.published = extraction_config.feed_published or ""
            result.content = str(prefetched)
            result.word_count = _count_words(result.content)
            _apply_feed_fallback(result, extraction_config)
            result.metadata_complete = _validate_metadata(result)
            result.extraction_success = (
                result.word_count >= MIN_CONTENT_LENGTH and result.metadata_complete
            )
            if result.extraction_success:
                return result
            result.error = (
                "prefetched content too short or metadata incomplete after feed fallback"
            )
            result.method = ExtractMethod.FAILED
            result.extraction_success = False
            return result

    # HTTP fetcher 入口：html 为 None 且非 PDF 时自动抓取 HTML
    if html is None and file_bytes is None and not _detect_pdf(url, content_type):
        try:
            import httpx

            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            html = resp.text
            if not content_type:
                content_type = resp.headers.get("content-type", "")
        except Exception as exc:
            logger.warning("HTTP fetch failed for %s: %s", url, exc)
            html = None

    # 分支 1: PDF 检测
    if _detect_pdf(url, content_type):
        extracted = _extract_via_docling(url, file_bytes)
        if extracted:
            # WP-1: 注入锚点前剥 chrome + 重算 wc（剥离后 <200 走 fail-closed）
            result.raw_metadata["chrome_strip"] = _apply_chrome_strip(extracted)
            result.method = ExtractMethod.DOCLING
            result.title = extracted.get("title", "")
            result.author = extracted.get("author", "")
            result.published = extracted.get("published", "")
            result.content = extracted.get("content", "")
            result.word_count = extracted.get("word_count", 0)
            # feed 元数据 fallback（补充抽取器未能获取的字段）
            _apply_feed_fallback(result, extraction_config)
            result.metadata_complete = _validate_metadata(result)
            result.extraction_success = (
                result.word_count >= MIN_CONTENT_LENGTH and result.metadata_complete
            )
            if result.extraction_success:
                return result
            result.error = "Docling extraction failed: content too short or metadata incomplete"

    # 分支 2: defuddle 优先
    if html is not None:
        # 检测反爬/JS（结果复用到分支 3）
        is_anti_bot = _detect_anti_bot(html)
        if not is_anti_bot:
            extracted = _extract_via_defuddle(url, html)
            if extracted:
                # WP-1: 剥 chrome + 重算 wc（在 wc >= MIN 判定前，剥离后 <200 走 fail-closed）
                result.raw_metadata["chrome_strip"] = _apply_chrome_strip(extracted)
                wc = extracted.get("word_count", 0)
                if wc >= MIN_CONTENT_LENGTH:
                    result.method = ExtractMethod.DEFUDDLE
                    result.title = extracted.get("title", "")
                    result.author = extracted.get("author", "")
                    result.published = extracted.get("published", "")
                    result.content = extracted.get("content", "")
                    result.word_count = wc
                    # feed 元数据 fallback
                    _apply_feed_fallback(result, extraction_config)
                    result.metadata_complete = _validate_metadata(result)
                    result.extraction_success = result.metadata_complete
                    if result.extraction_success:
                        return result
                    # fallback 后仍缺元数据 → fail-closed（不产 raw，不落签名）
                    result.error = "defuddle succeeded but metadata incomplete after feed fallback"
                    result.method = ExtractMethod.FAILED
                    result.extraction_success = False
                    return result
                else:
                    result.error = "defuddle content too short"

        # 分支 3: defuddle 失败/反爬 → Scrapling adaptive → browser fallback
        # 普通页 adaptive 失败（空 dict 或 content 过短）也应 fallback browser，
        # 不只在 is_anti_bot=True 时才用 browser。adaptive → browser → 全失败才签名。
        for use_browser in (False, True):
            extracted = _extract_via_scrapling(url, html, browser_mode=use_browser)
            if not extracted:
                result.error = (
                    f"Scrapling {'browser' if use_browser else 'adaptive'} returned empty"
                )
                continue
            # WP-1: 剥 chrome + 重算 wc（在 wc 判定前，剥离后 <200 走 fail-closed）
            result.raw_metadata["chrome_strip"] = _apply_chrome_strip(extracted)
            wc = extracted.get("word_count", 0)
            if wc < MIN_CONTENT_LENGTH:
                result.error = (
                    f"Scrapling {'browser' if use_browser else 'adaptive'} content too short"
                )
                continue
            # content 足够长 → 填充 result
            result.method = (
                ExtractMethod.SCRAPLING_BROWSER if use_browser else ExtractMethod.SCRAPLING
            )
            result.title = extracted.get("title", "")
            result.author = extracted.get("author", "")
            result.published = extracted.get("published", "")
            result.content = extracted.get("content", "")
            result.word_count = wc
            # feed 元数据 fallback
            _apply_feed_fallback(result, extraction_config)
            result.metadata_complete = _validate_metadata(result)
            result.extraction_success = result.metadata_complete
            if result.extraction_success:
                return result
            # fallback 后仍缺元数据 → fail-closed（不产 raw，不落签名）
            result.error = (
                f"Scrapling {'browser' if use_browser else 'adaptive'}"
                " succeeded but metadata incomplete after feed fallback"
            )
            result.method = ExtractMethod.FAILED
            result.extraction_success = False
            return result

    # 分支 4: 全失败 → 落签名
    result.method = ExtractMethod.FAILED
    result.extraction_success = False
    if not result.error:
        result.error = "All extraction methods failed"

    if failures_path:
        _record_fetch_fail(failures_path, url, result.error)

    return result



def validate_extraction_success(result: ExtractResult) -> bool:
    """return result.extraction_success (convenience alias used in tests)."""
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
    extraction_config: ExtractionContext | None = None,
) -> dict[str, ExtractResult]:
    """批量执行抽取决策树。

    Args:
        urls: 目标 URL 列表
        html_map: {url: html_text} 注入（测试用）
        failures_path: 故障签名落账路径
        extraction_config: ExtractionContext，供所有 URL 共享的 feed 元数据 fallback

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
            extraction_config=extraction_config,
        )
    return results
