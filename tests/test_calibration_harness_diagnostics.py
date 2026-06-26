"""Calibration harness diagnostics 直测 — SC-1/1a/2a/2b/4/6.

测试覆盖:
    extraction_context             (SC-2a) — 同一缺 author/published 的 ExtractResult ± ctx False→True
    extraction_context_named_urls  (SC-2b) — crewai/aider/mcp 注入保存 HTML 走真实 extract_content
    fixture_load                   (SC-1a) — 非 google-io fixture 经 _load_fixture_content 返回非空
    e2e_thin_slice                 (SC-4)  — 活语料端到端薄切片 ≥1 正样本越过 intake_raw
    raw_frontmatter                (SC-6)  — raw_content frontmatter 含 FRONTMATTER_FIELDS 全 9 字段
    failure_class + stratified     (SC-1)  — 三分类正确 + 分层率不被 fixture 灌绿

mock 边界设在 extract_content 内部抽取器返回值 + _run_pipeline_segments 入参截获，
断言绑行为（extraction_success 翻转/不卡 intake_raw/9 字段 frontmatter/三分类/fixture 正文加载/分层率）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "evals" / "main-pipeline-quality" / "fixtures"
_RAW_HTML_DIR = _FIXTURES_DIR / "raw-html"
_CORPUS_PATH = _REPO_ROOT / "evals" / "main-pipeline-quality" / "corpus.yaml"


def _test_config() -> dict[str, Any]:
    """测试用 config（路径指向 /tmp，避免写真实 vault）。"""
    return {
        "vault_root": "/tmp/akb2-test-vault",
        "staging_dir": "/tmp/akb2-test-staging",
        "raw_dir": "/tmp/akb2-test-raw",
        "ledger_path": "/tmp/akb2-test-ledger.jsonl",
        "failures_path": "",
        "grader_result_dir": "/tmp/akb2-test-grader",
        "auto_promote": False,
        "fixtures_dir": str(_FIXTURES_DIR),
        "preflight_lookup": {},
    }


def _load_corpus_entries() -> list[dict[str, Any]]:
    """读取 corpus.yaml 全部 entries。"""
    import yaml

    data = yaml.safe_load(_CORPUS_PATH.read_text(encoding="utf-8"))
    return data.get("entries", [])


def _html_to_text(html: str, limit: int = 200_000) -> str:
    """简单 HTML→text（strip tags），取前 limit 字符避免大页超时。"""
    sample = html[:limit]
    sample = re.sub(
        r"<script[^>]*>.*?</script>", "", sample, flags=re.DOTALL | re.IGNORECASE
    )
    sample = re.sub(
        r"<style[^>]*>.*?</style>", "", sample, flags=re.DOTALL | re.IGNORECASE
    )
    sample = re.sub(r"<[^>]+>", " ", sample)
    sample = re.sub(r"\s+", " ", sample).strip()
    return sample


def _mock_extractor_return(html: str) -> dict[str, Any]:
    """模拟 defuddle/scrapling 返回：有正文但 author/published 缺（B3-6 前提）。"""
    text = _html_to_text(html)
    content = text[:8000]
    wc = len(content.split())
    # 若前 200K 不足 200 词，扩大窗口
    if wc < 200:
        text = _html_to_text(html, limit=len(html))
        content = text[:8000]
        wc = len(content.split())
    return {
        "title": "Extracted Page Content",
        "author": "",       # 缺 — SC-2b 前提：正文在、author 缺
        "published": "",    # 缺 — SC-2b 前提：正文在、published 缺
        "content": content,
        "word_count": wc,
    }


def _mock_pipeline_success(
    raw_path: str, raw_content: str, config: Any
) -> dict[str, Any]:
    """mock _run_pipeline_segments 返回成功（正样本 proposal_passed）。"""
    return {
        "converged": True,
        "rounds": 0,
        "promoted": True,
        "promotion_mode": "proposal",
        "final_status": "proposal_passed",
        "error": None,
    }


# ---------------------------------------------------------------------------
# SC-2a: extraction_context — ctx 回填机制无条件 mock
# ---------------------------------------------------------------------------


def test_extraction_context_without_ctx():
    """SC-2a: 不传 ctx → author/published 缺 → metadata_complete=False。"""
    from src.akb2.quality.intake.extract import (
        MIN_CONTENT_LENGTH,
        ExtractMethod,
        ExtractResult,
        _apply_feed_fallback,
        _validate_metadata,
    )

    content = "word " * (MIN_CONTENT_LENGTH + 10)
    result = ExtractResult(
        url="https://example.com/article",
        method=ExtractMethod.DEFUDDLE,
        title="Test Article",
        content=content,
        word_count=len(content.split()),
    )
    # 不传 ctx → _apply_feed_fallback no-op
    _apply_feed_fallback(result, None)
    result.metadata_complete = _validate_metadata(result)
    result.extraction_success = result.metadata_complete

    assert not result.extraction_success
    assert not result.metadata_complete
    assert not result.author
    assert not result.published


def test_extraction_context_with_ctx():
    """SC-2a: 传 ctx（feed 字段构造）→ feed 回填 author/published → metadata_complete=True。"""
    from src.akb2.quality.intake.extract import (
        MIN_CONTENT_LENGTH,
        ExtractMethod,
        ExtractResult,
        _apply_feed_fallback,
        _validate_metadata,
    )
    from src.akb2.quality.intake.models import ExtractionContext

    content = "word " * (MIN_CONTENT_LENGTH + 10)
    result = ExtractResult(
        url="https://example.com/article",
        method=ExtractMethod.DEFUDDLE,
        title="Test Article",
        content=content,
        word_count=len(content.split()),
    )
    ctx = ExtractionContext(
        source_slug="test-slug",
        source_publisher="Test Publisher",
        feed_title="Test Article",
        feed_author="Test Author",
        feed_published="2026-06-01",
        url="https://example.com/article",
    )
    _apply_feed_fallback(result, ctx)
    result.metadata_complete = _validate_metadata(result)
    result.extraction_success = result.metadata_complete

    assert result.extraction_success
    assert result.metadata_complete
    assert result.author == "Test Author"
    assert result.published == "2026-06-01"


def test_extraction_context_false_to_true_flip():
    """SC-2a 核心断言：同一 base ± ctx 跑两次，extraction_success False→True 翻转。

    不走 fixture 路径、不硬编码非空 ctx（feed 字段从 entry 构造）。
    """
    from src.akb2.quality.calibration import _build_extraction_context
    from src.akb2.quality.intake.extract import (
        MIN_CONTENT_LENGTH,
        ExtractMethod,
        ExtractResult,
        _apply_feed_fallback,
        _validate_metadata,
    )

    content = "word " * (MIN_CONTENT_LENGTH + 10)

    # corpus entry 形态的 feed_*（非硬编码非空 ctx，从 entry 构造）
    entry = {
        "url": "https://example.com/article",
        "feed_title": "Test Article",
        "feed_author": "Test Author",
        "feed_published": "2026-06-01",
        "source_publisher": "Test Publisher",
        "source_slug": "test-slug",
    }
    ctx = _build_extraction_context(entry, {})

    def make_base() -> ExtractResult:
        return ExtractResult(
            url="https://example.com/article",
            method=ExtractMethod.DEFUDDLE,
            title="Test Article",
            content=content,
            word_count=len(content.split()),
        )

    # 不传 ctx: False
    r_no_ctx = make_base()
    _apply_feed_fallback(r_no_ctx, None)
    r_no_ctx.metadata_complete = _validate_metadata(r_no_ctx)
    r_no_ctx.extraction_success = r_no_ctx.metadata_complete
    assert not r_no_ctx.extraction_success

    # 传 ctx: True
    r_ctx = make_base()
    _apply_feed_fallback(r_ctx, ctx)
    r_ctx.metadata_complete = _validate_metadata(r_ctx)
    r_ctx.extraction_success = r_ctx.metadata_complete
    assert r_ctx.extraction_success

    # feed_published 非空（M4，非硬编码）
    assert r_ctx.published == entry["feed_published"]


# ---------------------------------------------------------------------------
# SC-2b: extraction_context_named_urls — 3 具名 URL 注入保存 HTML 走真实路径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_substring,html_file",
    [
        ("crewai.com", "crewai.html"),
        ("aider.chat", "aider.html"),
        ("modelcontextprotocol.io", "mcp.html"),
    ],
    ids=["crewai", "aider", "mcp"],
)
def test_extraction_context_named_urls(url_substring: str, html_file: str, monkeypatch):
    """SC-2b: crewai/aider/mcp 保持 live（真实 feed_*），注入保存 HTML 走真实 extract_content。

    monkeypatch 抽取器底层（不发网络），走真实 extract_content 决策树 +
    真实 corpus feed_* + 真实 _apply_feed_fallback，断言每条 ±ctx False→True。
    """
    import src.akb2.quality.intake.extract as extract_mod
    from src.akb2.quality.calibration import _build_extraction_context
    from src.akb2.quality.intake.extract import extract_content

    # 1. 从 corpus 找对应 entry（真实 feed_*）
    entries = _load_corpus_entries()
    entry = next(e for e in entries if url_substring in e.get("url", ""))
    assert entry.get("feed_author"), f"{url_substring} entry 缺 feed_author"
    assert entry.get("feed_published"), f"{url_substring} entry 缺 feed_published"

    # 2. 读保存的 HTML
    html_path = _RAW_HTML_DIR / html_file
    assert html_path.exists(), f"保存 HTML 不存在: {html_path}"
    html = html_path.read_text(encoding="utf-8")

    # 3. mock 抽取器返回值（内容在、author/published 缺）
    mock_return = _mock_extractor_return(html)
    assert mock_return["word_count"] >= 200, (
        f"{html_file} 抽取后 word_count < 200"
    )
    assert not mock_return["author"], "mock author 应为空（模拟缺 author）"
    assert not mock_return["published"], "mock published 应为空"

    monkeypatch.setattr(
        extract_mod,
        "_extract_via_defuddle",
        lambda url, html=None: mock_return,
    )
    monkeypatch.setattr(
        extract_mod,
        "_extract_via_scrapling",
        lambda url, html=None, browser_mode=False: mock_return,
    )

    # 4. 不传 ctx: extraction_success=False（metadata incomplete）
    result_no_ctx = extract_content(
        entry["url"], html=html, failures_path=None
    )
    assert not result_no_ctx.extraction_success, (
        f"{url_substring} 不传 ctx 应 False"
    )
    assert not result_no_ctx.metadata_complete

    # 5. 传 ctx（真实 corpus feed_*）: extraction_success=True
    ctx = _build_extraction_context(entry, {})
    result_ctx = extract_content(
        entry["url"], html=html, failures_path=None, extraction_config=ctx
    )
    assert result_ctx.extraction_success, (
        f"{url_substring} 传 ctx 应 True"
    )
    assert result_ctx.metadata_complete
    assert result_ctx.author, "feed 回填后 author 非空"
    assert result_ctx.published, "feed 回填后 published 非空"


# ---------------------------------------------------------------------------
# SC-1a: fixture_load — 非 google-io fixture 加载
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        "vercel-ai-sdk-nosource",
        "ag2-captain-agent-concept",
        "loop-engineering-golden",
        "x-plagiarism-negative",
        "google-io-2026-news",
    ],
)
def test_fixture_load_content(fixture_name: str):
    """SC-1a: 非 google-io fixture 经 _load_fixture_content 返回非空正文。"""
    from src.akb2.quality.calibration import _load_fixture_content

    content = _load_fixture_content(fixture_name, {"fixtures_dir": str(_FIXTURES_DIR)})
    assert content is not None, f"fixture {fixture_name} 加载失败（返回 None）"
    assert len(content) > 100, f"fixture {fixture_name} 正文过短"
    assert "artifact_type" in content, f"fixture {fixture_name} 缺 frontmatter"


def test_fixture_load_missing_returns_none():
    """SC-1a: 不存在的 fixture 名返回 None（非崩溃）。"""
    from src.akb2.quality.calibration import _load_fixture_content

    content = _load_fixture_content("nonexistent-fixture-xyz")
    assert content is None


def test_fixture_load_not_intake_raw(monkeypatch):
    """SC-1a: fixture entry 经 _process_corpus_entry 不卡 intake_raw。"""
    import src.akb2.quality.runner as runner_mod
    from src.akb2.quality.calibration import _process_corpus_entry

    # mock pipeline 返回成功
    monkeypatch.setattr(runner_mod, "_run_pipeline_segments", _mock_pipeline_success)

    entry = {
        "url": "fixture:vercel-ai-sdk-nosource",
        "domain": "Harness Engineering",
        "expected": "ingest",
        "notes": "test fixture",
    }
    result = _process_corpus_entry(entry, 0, _test_config())
    assert result["source"] == "fixture"
    assert result["extract_method"] == "fixture"
    assert result["extraction_success"] is True
    assert result["failure_segment"] != "intake_raw"
    assert result["failure_class"] is None


# ---------------------------------------------------------------------------
# SC-4: e2e_thin_slice — 活语料端到端薄切片
# ---------------------------------------------------------------------------


def test_e2e_thin_slice_live_positive(monkeypatch):
    """SC-4: live 正样本（mock 抽取器返回完整元数据）越过 intake_raw。"""
    import src.akb2.quality.intake.extract as extract_mod
    import src.akb2.quality.runner as runner_mod
    from src.akb2.quality.calibration import _process_corpus_entry

    # mock 抽取器返回完整元数据（模拟成功 live 抽取）
    mock_return = {
        "title": "Test Live Article",
        "author": "Live Author",
        "published": "2026-06-01",
        "content": "word " * 250,
        "word_count": 250,
    }
    monkeypatch.setattr(
        extract_mod,
        "_extract_via_defuddle",
        lambda url, html=None: mock_return,
    )
    monkeypatch.setattr(
        extract_mod,
        "_extract_via_scrapling",
        lambda url, html=None, browser_mode=False: mock_return,
    )
    # mock pipeline 返回成功
    monkeypatch.setattr(runner_mod, "_run_pipeline_segments", _mock_pipeline_success)

    entry = {
        "url": "https://example.com/live-article",
        "domain": "AI Agent",
        "expected": "ingest",
        "feed_title": "Test Live Article",
        "feed_author": "Live Author",
        "feed_published": "2026-06-01",
        "source_publisher": "Live Publisher",
        "source_slug": "live-slug",
    }
    result = _process_corpus_entry(entry, 0, _test_config())
    assert result["source"] == "live"
    assert result["extraction_success"] is True
    assert result["failure_segment"] != "intake_raw"
    assert result["passed"] is True


# ---------------------------------------------------------------------------
# SC-6: raw_frontmatter — 9 字段完整
# ---------------------------------------------------------------------------


def test_raw_frontmatter_via_pipeline(monkeypatch):
    """SC-6: _process_corpus_entry 传给 pipeline 的 raw_content 含全 9 字段。

    测试 import raw_archiver.FRONTMATTER_FIELDS 断言 set(produced_keys) >= set(FRONTMATTER_FIELDS)，
    非硬编码 4 字段子集。
    """
    import src.akb2.quality.intake.extract as extract_mod
    import src.akb2.quality.runner as runner_mod
    from src.akb2.quality.calibration import _process_corpus_entry
    from src.akb2.quality.intake.raw_archiver import FRONTMATTER_FIELDS

    # 截获传给 _run_pipeline_segments 的 raw_content
    captured: dict[str, str] = {}

    def capture_pipeline(raw_path, raw_content, config):
        captured["raw_content"] = raw_content
        return _mock_pipeline_success(raw_path, raw_content, config)

    monkeypatch.setattr(runner_mod, "_run_pipeline_segments", capture_pipeline)

    # mock 抽取器返回完整元数据
    mock_return = {
        "title": "Test Article",
        "author": "Author",
        "published": "2026-06-01",
        "content": "word " * 250,
        "word_count": 250,
    }
    monkeypatch.setattr(
        extract_mod,
        "_extract_via_defuddle",
        lambda url, html=None: mock_return,
    )

    entry = {
        "url": "https://example.com/article",
        "domain": "AI Agent",
        "expected": "ingest",
        "feed_title": "Test Article",
        "feed_author": "Author",
        "feed_published": "2026-06-01",
        "source_publisher": "Test Publisher",
        "source_slug": "test-slug",
    }
    _process_corpus_entry(entry, 0, _test_config())

    raw = captured["raw_content"]
    assert raw, "raw_content 未被截获"

    # 解析 frontmatter keys
    fm_keys: set[str] = set()
    assert raw.startswith("---"), "raw_content 应以 --- frontmatter 开头"
    end = raw.find("---", 3)
    assert end > 0, "frontmatter 未闭合"
    fm_block = raw[3:end]
    for line in fm_block.strip().split("\n"):
        if ":" in line:
            key = line.split(":")[0].strip()
            fm_keys.add(key)

    # SC-6: import 同一常量断言 superset（非硬编码 4 字段）
    assert set(FRONTMATTER_FIELDS) <= fm_keys, (
        f"frontmatter 缺字段: {set(FRONTMATTER_FIELDS) - fm_keys}"
    )
    assert len(fm_keys) >= 9, f"frontmatter 字段数 < 9: {fm_keys}"


def test_raw_frontmatter_direct():
    """SC-6: 直接测 _compose_raw_content 产 9 字段（单元级）。"""
    from src.akb2.quality.calibration import _compose_raw_content
    from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult
    from src.akb2.quality.intake.models import ExtractionContext
    from src.akb2.quality.intake.raw_archiver import FRONTMATTER_FIELDS

    result = ExtractResult(
        url="https://example.com/article",
        method=ExtractMethod.DEFUDDLE,
        title="Test",
        author="Author",
        published="2026-06-01",
        content="content body",
        word_count=250,
        metadata_complete=True,
        extraction_success=True,
    )
    ctx = ExtractionContext(
        source_slug="test-slug",
        source_publisher="Test Publisher",
        feed_title="Test",
        feed_author="Author",
        feed_published="2026-06-01",
        url="https://example.com/article",
    )
    raw = _compose_raw_content(result, ctx)

    fm_keys: set[str] = set()
    end = raw.find("---", 3)
    for line in raw[3:end].strip().split("\n"):
        if ":" in line:
            fm_keys.add(line.split(":")[0].strip())
    assert set(FRONTMATTER_FIELDS) <= fm_keys


# ---------------------------------------------------------------------------
# SC-1: failure_class 三分类
# ---------------------------------------------------------------------------


def test_failure_class_dead_link_http_status():
    """SC-1: HTTP 死链（preflight http_status>=400）→ dead_link。"""
    from src.akb2.quality.calibration import _classify_failure_class
    from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult

    r = ExtractResult(
        url="https://example.com/404",
        method=ExtractMethod.FAILED,
        error="All extraction methods failed",
    )
    assert _classify_failure_class(r, http_status=404) == "dead_link"
    assert _classify_failure_class(r, http_status=503) == "dead_link"
    assert _classify_failure_class(r, http_status="unreachable") == "dead_link"


def test_failure_class_dead_link_degraded():
    """SC-1: 无 preflight 时退化按 error + 无正文判 dead_link。"""
    from src.akb2.quality.calibration import _classify_failure_class
    from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult

    r = ExtractResult(
        url="https://example.com/dead",
        method=ExtractMethod.FAILED,
        content="",
        word_count=0,
        error="All extraction methods failed",
    )
    assert _classify_failure_class(r, http_status=None) == "dead_link"


def test_failure_class_metadata_failure():
    """SC-1: 有正文但元数据不全 → metadata_failure。"""
    from src.akb2.quality.calibration import _classify_failure_class
    from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult

    r = ExtractResult(
        url="https://example.com/article",
        method=ExtractMethod.DEFUDDLE,
        content="word " * 250,
        word_count=250,
        metadata_complete=False,
        extraction_success=False,
        error="metadata incomplete after feed fallback",
    )
    assert _classify_failure_class(r, http_status=200) == "metadata_failure"


def test_failure_class_extraction_failure():
    """SC-1: 正文过短/全失败 → extraction_failure。"""
    from src.akb2.quality.calibration import _classify_failure_class
    from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult

    r = ExtractResult(
        url="https://example.com/short",
        method=ExtractMethod.FAILED,
        content="short",
        word_count=1,
        metadata_complete=False,
        extraction_success=False,
        error="content too short",
    )
    assert _classify_failure_class(r, http_status=200) == "extraction_failure"


def test_failure_class_success_returns_none():
    """SC-1: 抽取成功 → None。"""
    from src.akb2.quality.calibration import _classify_failure_class
    from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult

    r = ExtractResult(
        url="https://example.com/ok",
        method=ExtractMethod.DEFUDDLE,
        content="word " * 250,
        word_count=250,
        metadata_complete=True,
        extraction_success=True,
    )
    assert _classify_failure_class(r, http_status=200) is None


# ---------------------------------------------------------------------------
# SC-1: stratified 分层率
# ---------------------------------------------------------------------------


def test_stratified_fixture_does_not_inflate_live():
    """SC-1/B3-1: fixture 全通过不抬高 live pass rate；live 全失败时 factory=False。"""
    from src.akb2.quality.calibration import (
        LIVE_POSITIVE_PASS_FLOOR,
        LIVE_POSITIVE_QUOTA,
        _compute_statistics,
    )

    report: dict[str, Any] = {
        "entries": [
            # 2 fixture 正样本: 全通过
            {
                "expected": "ingest", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            # 3 live 正样本: 全失败（卡 intake_raw）
            {
                "expected": "ingest", "source": "live", "passed": False,
                "failure_segment": "intake_raw", "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "live", "passed": False,
                "failure_segment": "intake_raw", "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "live", "passed": False,
                "failure_segment": "intake_raw", "repair_rounds": 0,
            },
            # 1 负样本: 正确拒收
            {
                "expected": "reject", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
        ],
    }

    _compute_statistics(report)

    # fixture 灌绿混池：混池 2/5=0.4（不达 0.9）
    assert report["positive_pass_rate"] == 0.4
    # fixture 层 2/2=1.0
    assert report["positive_fixture_pass_rate"] == 1.0
    # live 层 0/3=0.0（不被 fixture 抬高）
    assert report["positive_live_pass_rate"] == 0.0
    assert report["live_positive_intake_raw_failures"] == 3
    assert report["positive_live_total"] == 3
    assert report["positive_live_total"] >= LIVE_POSITIVE_QUOTA

    # factory: live 全失败 → False（fixture 不补偿）
    report["factory_acceptance"] = (
        report["positive_pass_rate"] >= 0.9
        and report["negative_reject_rate"] == 1.0
        and report["positive_live_total"] >= LIVE_POSITIVE_QUOTA
        and report["live_positive_intake_raw_failures"] == 0
        and report["positive_live_pass_rate"] >= LIVE_POSITIVE_PASS_FLOOR
    )
    assert report["factory_acceptance"] is False


def test_stratified_factory_passes_when_live_healthy():
    """SC-1/B3-1: live 健康时 factory 通过（正反向断言）。"""
    from src.akb2.quality.calibration import (
        LIVE_POSITIVE_PASS_FLOOR,
        LIVE_POSITIVE_QUOTA,
        _compute_statistics,
    )

    report: dict[str, Any] = {
        "entries": [
            # 3 live 正样本: 全通过
            {
                "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            # 2 fixture 正样本: 全通过
            {
                "expected": "ingest", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            # 1 负样本: 正确拒收
            {
                "expected": "reject", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
        ],
    }

    _compute_statistics(report)

    assert report["positive_live_pass_rate"] == 1.0
    assert report["positive_fixture_pass_rate"] == 1.0
    assert report["live_positive_intake_raw_failures"] == 0
    assert report["positive_pass_rate"] == 1.0
    assert report["negative_reject_rate"] == 1.0
    assert report["positive_live_total"] >= LIVE_POSITIVE_QUOTA

    report["factory_acceptance"] = (
        report["positive_pass_rate"] >= 0.9
        and report["negative_reject_rate"] == 1.0
        and report["positive_live_total"] >= LIVE_POSITIVE_QUOTA
        and report["live_positive_intake_raw_failures"] == 0
        and report["positive_live_pass_rate"] >= LIVE_POSITIVE_PASS_FLOOR
    )
    assert report["factory_acceptance"] is True


def test_stratified_factory_fails_below_live_quota():
    """SC-1/B3-1: live 正样本条数 < LIVE_POSITIVE_QUOTA → factory=False。"""
    from src.akb2.quality.calibration import (
        LIVE_POSITIVE_PASS_FLOOR,
        LIVE_POSITIVE_QUOTA,
        _compute_statistics,
    )

    report: dict[str, Any] = {
        "entries": [
            # 仅 2 live 正样本（< LIVE_POSITIVE_QUOTA=3），全通过
            {
                "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            # 3 fixture 正样本: 全通过
            {
                "expected": "ingest", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            {
                "expected": "ingest", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
            # 1 负样本: 正确拒收
            {
                "expected": "reject", "source": "fixture", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
            },
        ],
    }

    _compute_statistics(report)

    assert report["positive_live_total"] == 2
    assert report["positive_live_total"] < LIVE_POSITIVE_QUOTA
    assert report["positive_live_pass_rate"] == 1.0
    assert report["positive_pass_rate"] == 1.0  # 混池全通过

    report["factory_acceptance"] = (
        report["positive_pass_rate"] >= 0.9
        and report["negative_reject_rate"] == 1.0
        and report["positive_live_total"] >= LIVE_POSITIVE_QUOTA
        and report["live_positive_intake_raw_failures"] == 0
        and report["positive_live_pass_rate"] >= LIVE_POSITIVE_PASS_FLOOR
    )
    assert report["factory_acceptance"] is False  # live 不够配额


def test_stratified_diagnostic_keys_all_paths(monkeypatch):
    """SC-1: 全返回路径的 entry 都携带 7 诊断键 + source（防 all() KeyError）。"""
    from src.akb2.quality.calibration import _process_corpus_entry

    config = _test_config()
    diag_keys = {
        "failure_class", "extract_method", "word_count",
        "metadata_complete", "extraction_success", "extract_error",
        "missing_metadata", "source",
    }

    # path: fixture 成功（mock pipeline）
    import src.akb2.quality.runner as runner_mod
    monkeypatch.setattr(runner_mod, "_run_pipeline_segments", _mock_pipeline_success)

    entry_fixture = {
        "url": "fixture:vercel-ai-sdk-nosource",
        "domain": "Harness Engineering",
        "expected": "ingest",
    }
    result = _process_corpus_entry(entry_fixture, 0, config)
    assert diag_keys <= set(result), (
        f"fixture path 缺诊断键: {diag_keys - set(result)}"
    )
    assert result["source"] == "fixture"

    # path: live 抽取失败（mock 抽取器返回失败）
    import src.akb2.quality.intake.extract as extract_mod
    from src.akb2.quality.intake.extract import ExtractMethod, ExtractResult

    fail_result = ExtractResult(
        url="https://example.com/fail",
        method=ExtractMethod.FAILED,
        error="All extraction methods failed",
    )
    monkeypatch.setattr(
        extract_mod,
        "extract_content",
        lambda url, **kwargs: fail_result,
    )

    entry_live_fail = {
        "url": "https://example.com/fail",
        "domain": "AI Agent",
        "expected": "ingest",
    }
    result = _process_corpus_entry(entry_live_fail, 1, config)
    assert diag_keys <= set(result), (
        f"live-fail path 缺诊断键: {diag_keys - set(result)}"
    )
    assert result["source"] == "live"
    assert result["failure_class"] is not None
    assert result["failure_segment"] == "intake_raw"
