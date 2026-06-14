"""tests/quality/test_intake_orchestrator.py

Intake Orchestrator 单元测试 — 全部 mock，不打真网。

覆盖 SC-5 / SC-7 / SC-8 成功标准：
  - test_dry_run_no_raw        — dry-run 后 raw 目录无新文件
  - test_dry_run_no_network_no_raw — patch httpx/scrapling fail-on-call，干跑通过
  - test_article_id_uniqueness  — 同源两篇不同 URL → 2 个不同 article_id
  - test_idempotent             — 同 URL 重跑 ledger 已有 → skip，不产新文件
  - test_rate_limit_skip        — 超限 → status=skipped_rate_limit
  - test_quarantine_skip        — 命中 → 不写 raw
  - test_ledger_fields          — ledger 记录含 run_id/status 等必填字段
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.akb2.quality.intake.models import compute_article_id
from src.akb2.quality.intake.orchestrator import (
    RATE_LIMIT_PER_RUN,
    load_seen_article_ids,
    run_intake,
    write_ledger_entry,
)

# ---------------------------------------------------------------------------
# Fixtures — RSS feed XML
# ---------------------------------------------------------------------------

SAMPLE_RSS_TEMPLATE = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <link>https://example.com</link>
        {items}
      </channel>
    </rss>
    """
)


def _rss_item(url: str, title: str, description: str = "desc") -> str:
    return textwrap.dedent(
        f"""\
        <item>
          <title>{title}</title>
          <link>{url}</link>
          <description>{description}</description>
          <pubDate>2026-06-14T00:00:00Z</pubDate>
        </item>
        """
    )


def _make_feed(urls: list[tuple[str, str]]) -> str:
    """(url, title) 列表 → RSS XML 字符串。"""
    items = "\n".join(_rss_item(url, title) for url, title in urls)
    return SAMPLE_RSS_TEMPLATE.format(items=items)


# ---------------------------------------------------------------------------
# 最小 sources yaml（只有 1 个 feed 源）
# ---------------------------------------------------------------------------


def _write_sources_yaml(tmp_path: Path, slug: str = "test-source") -> Path:
    yaml_content = textwrap.dedent(
        f"""\
        sources:
          - slug: {slug}
            domain: example.com
            feed_url: https://example.com/feed.xml
            listing_url: https://example.com
            rate_limit: 60
            status: trial
            category: core
        """
    )
    p = tmp_path / "sources.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# discover_articles 工厂（注入 feed_content）
# ---------------------------------------------------------------------------


def _make_discover_mock(articles_urls_titles: list[tuple[str, str]], slug: str = "test-source"):
    """返回一个 DiscoverResult mock，articles 列表来自 (url, title) 对。"""
    from src.akb2.quality.intake.discovery import DiscoveredArticle, DiscoverResult

    articles = [
        DiscoveredArticle(
            url=url,
            title=title,
            content_hash="aabbcc112233",
            source_slug=slug,
            published_at="2026-06-14T00:00:00Z",
            feed_title=title,
        )
        for url, title in articles_urls_titles
    ]
    result = DiscoverResult(source_slug=slug, articles=articles, method="feed")
    return result


# ---------------------------------------------------------------------------
# test_dry_run_no_raw (SC-7)
# ---------------------------------------------------------------------------


def test_dry_run_no_raw(tmp_path):
    """dry-run 模式下：raw 目录无新文件，archived=0，ledger 无写入。"""
    sources_config = _write_sources_yaml(tmp_path)
    state_file = tmp_path / "intake-state.jsonl"
    output_dir = tmp_path / "raw"

    feed_urls = [
        ("https://example.com/article-1", "Article One"),
        ("https://example.com/article-2", "Article Two"),
    ]
    mock_result = _make_discover_mock(feed_urls)

    with patch(
        "src.akb2.quality.intake.orchestrator.discover_articles",
        return_value=mock_result,
    ):
        stats = run_intake(
            sources_config=sources_config,
            state_file=state_file,
            output_dir=output_dir,
            dry_run=True,
            limit=0,
        )

    # archived=0
    assert stats["archived"] == 0, f"期望 archived=0，实得 {stats['archived']}"
    # discovered > 0（发现了文章）
    assert stats["discovered"] == 2, f"期望 discovered=2，实得 {stats['discovered']}"
    # raw 目录不存在或为空
    assert not output_dir.exists() or not any(output_dir.iterdir()), "raw 目录不应有文件"
    # ledger 不写入
    assert not state_file.exists() or state_file.stat().st_size == 0, "dry-run 不写 ledger"


# ---------------------------------------------------------------------------
# test_dry_run_no_network_no_raw (SC-7)
# ---------------------------------------------------------------------------


def test_dry_run_no_network_no_raw(tmp_path):
    """dry-run 时网络 fail-on-call：httpx/scrapling 被 patch 为抛异常，干跑仍通过。"""
    sources_config = _write_sources_yaml(tmp_path)
    state_file = tmp_path / "intake-state.jsonl"
    output_dir = tmp_path / "raw"

    feed_urls = [("https://example.com/article-dry", "Dry Run Article")]
    mock_result = _make_discover_mock(feed_urls)

    def _fail_httpx(*args, **kwargs):
        raise RuntimeError("httpx.get should NOT be called in dry-run mode")

    def _fail_scrapling(*args, **kwargs):
        raise RuntimeError("scrapling should NOT be called in dry-run mode")

    with (
        patch(
            "src.akb2.quality.intake.orchestrator.discover_articles",
            return_value=mock_result,
        ),
        patch("httpx.get", side_effect=_fail_httpx),
        patch("scrapling.Fetcher", side_effect=_fail_scrapling),
        patch("scrapling.StealthyFetcher", side_effect=_fail_scrapling),
    ):
        stats = run_intake(
            sources_config=sources_config,
            state_file=state_file,
            output_dir=output_dir,
            dry_run=True,
            limit=0,
        )

    # 干跑不触发网络，即使 patch 了 fail-on-call，也不报错
    assert stats["archived"] == 0
    assert not output_dir.exists() or not any(output_dir.iterdir())


# ---------------------------------------------------------------------------
# test_article_id_uniqueness (SC-5)
# ---------------------------------------------------------------------------


def test_article_id_uniqueness():
    """同源两篇不同 URL → 2 个不同 article_id（幂等性保证）。"""
    url_a = "https://example.com/article-a"
    url_b = "https://example.com/article-b"

    id_a = compute_article_id(url_a)
    id_b = compute_article_id(url_b)

    assert id_a != id_b, "不同 URL 应产生不同 article_id"
    assert len(id_a) == 12, f"article_id 应为 12 位，实得 {len(id_a)}"
    assert len(id_b) == 12


def test_article_id_deterministic():
    """相同 URL 重复调用 → 相同 article_id（确定性）。"""
    url = "https://example.com/stable-article"
    assert compute_article_id(url) == compute_article_id(url)


# ---------------------------------------------------------------------------
# test_idempotent (SC-5)
# ---------------------------------------------------------------------------


def test_idempotent(tmp_path):
    """同 URL 重跑，ledger 已有该 article_id → 跳过，不产新文件，不重写 ledger。"""
    sources_config = _write_sources_yaml(tmp_path)
    state_file = tmp_path / "intake-state.jsonl"
    output_dir = tmp_path / "raw"

    url = "https://example.com/existing-article"
    article_id = compute_article_id(url)

    # 预写 ledger：模拟该 article_id 已归档
    write_ledger_entry(
        state_file,
        run_id="pre-existing-run",
        source_slug="test-source",
        article_id=article_id,
        status="archived",
        daily_count=1,
        quarantine_status=False,
    )
    assert state_file.exists()
    initial_lines = state_file.read_text().strip().splitlines()
    assert len(initial_lines) == 1

    mock_result = _make_discover_mock([(url, "Existing Article")])

    with patch(
        "src.akb2.quality.intake.orchestrator.discover_articles",
        return_value=mock_result,
    ):
        stats = run_intake(
            sources_config=sources_config,
            state_file=state_file,
            output_dir=output_dir,
            dry_run=False,
            limit=0,
        )

    # 跳过已见过的文章
    assert stats["archived"] == 0
    assert stats["skipped"] >= 1

    # raw 目录无新文件（或不存在）
    if output_dir.exists():
        raw_files = list(output_dir.rglob("*.md"))
        assert len(raw_files) == 0, f"不应产生新 raw 文件，实得: {raw_files}"

    # ledger 行数不增（skipped_seen 不写 ledger）
    final_lines = state_file.read_text().strip().splitlines()
    assert len(final_lines) == 1, f"ledger 行数应保持 1，实得 {len(final_lines)}"


# ---------------------------------------------------------------------------
# test_rate_limit_skip (SC-8)
# ---------------------------------------------------------------------------


def test_rate_limit_skip(tmp_path):
    """超过 RATE_LIMIT_PER_RUN → status=skipped_rate_limit，不写 raw。"""
    sources_config = _write_sources_yaml(tmp_path)
    state_file = tmp_path / "intake-state.jsonl"
    output_dir = tmp_path / "raw"

    # 生成超出限制数量的文章 URL
    n = RATE_LIMIT_PER_RUN + 3
    feed_urls = [
        (f"https://example.com/article-{i}", f"Article {i}") for i in range(n)
    ]
    mock_result = _make_discover_mock(feed_urls)

    # mock extract + archive 以便前 RATE_LIMIT_PER_RUN 篇能成功归档
    mock_extract = MagicMock()
    mock_extract.extraction_success = True
    mock_extract.url = "https://example.com/article-x"
    mock_extract.method = MagicMock()
    mock_extract.method.value = "defuddle"
    mock_extract.title = "Test"
    mock_extract.author = "Author"
    mock_extract.published = "2026-06-14"
    mock_extract.content = "Content " * 50
    mock_extract.word_count = 200

    mock_archive = MagicMock()
    mock_archive.success = True
    mock_archive.output_path = str(output_dir / "mock.md")

    with (
        patch(
            "src.akb2.quality.intake.orchestrator.discover_articles",
            return_value=mock_result,
        ),
        patch(
            "src.akb2.quality.intake.orchestrator.extract_content",
            return_value=mock_extract,
        ),
        patch(
            "src.akb2.quality.intake.orchestrator.archive_from_extract_result",
            return_value=mock_archive,
        ),
    ):
        run_intake(
            sources_config=sources_config,
            state_file=state_file,
            output_dir=output_dir,
            dry_run=False,
            limit=0,
        )

    # 检查 ledger 中有 skipped_rate_limit 记录
    assert state_file.exists(), "ledger 应被写入"
    ledger_lines = state_file.read_text().strip().splitlines()
    records = [json.loads(line) for line in ledger_lines if line.strip()]

    rate_limited = [r for r in records if r.get("status") == "skipped_rate_limit"]
    assert len(rate_limited) >= 1, (
        f"应有 skipped_rate_limit 记录，实际 statuses: "
        f"{[r.get('status') for r in records]}"
    )


# ---------------------------------------------------------------------------
# test_quarantine_skip (SC-8)
# ---------------------------------------------------------------------------


def test_quarantine_skip(tmp_path):
    """quarantine 触发 → status=skipped_quarantine，不写 raw 文件。"""
    sources_config = _write_sources_yaml(tmp_path)
    state_file = tmp_path / "intake-state.jsonl"
    output_dir = tmp_path / "raw"

    feed_urls = [
        ("https://example.com/quarantine-article-1", "QArticle 1"),
        ("https://example.com/quarantine-article-2", "QArticle 2"),
    ]
    mock_result = _make_discover_mock(feed_urls)

    # patch check_quarantine 总是返回 True（触发隔离）
    with (
        patch(
            "src.akb2.quality.intake.orchestrator.discover_articles",
            return_value=mock_result,
        ),
        patch(
            "src.akb2.quality.intake.orchestrator.check_quarantine",
            return_value=True,
        ),
    ):
        stats = run_intake(
            sources_config=sources_config,
            state_file=state_file,
            output_dir=output_dir,
            dry_run=False,
            limit=0,
        )

    # archived=0（quarantine 不归档）
    assert stats["archived"] == 0, f"quarantine 模式不应归档，实得 archived={stats['archived']}"
    # raw 目录无文件
    if output_dir.exists():
        raw_files = list(output_dir.rglob("*.md"))
        assert len(raw_files) == 0, f"quarantine 不写 raw 文件，实得: {raw_files}"

    # ledger 含 skipped_quarantine 记录
    if state_file.exists():
        records = [
            json.loads(line)
            for line in state_file.read_text().strip().splitlines()
            if line.strip()
        ]
        quarantined = [r for r in records if r.get("status") == "skipped_quarantine"]
        assert len(quarantined) >= 1, "应有 skipped_quarantine 记录"


# ---------------------------------------------------------------------------
# test_ledger_fields (SC-8)
# ---------------------------------------------------------------------------


def test_ledger_fields(tmp_path):
    """ledger 记录含 run_id/source_slug/article_id/last_fetch_at/daily_count/quarantine_status/status。"""
    state_file = tmp_path / "intake-state.jsonl"

    write_ledger_entry(
        state_file,
        run_id="test-run-uuid",
        source_slug="test-source",
        article_id="abc123def456",
        status="archived",
        daily_count=1,
        quarantine_status=False,
    )

    assert state_file.exists()
    records = [
        json.loads(line)
        for line in state_file.read_text().strip().splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    record = records[0]

    required_fields = {
        "run_id",
        "source_slug",
        "article_id",
        "last_fetch_at",
        "daily_count",
        "quarantine_status",
        "status",
    }
    for field in required_fields:
        assert field in record, f"ledger 记录缺少字段: {field}"

    assert record["run_id"] == "test-run-uuid"
    assert record["source_slug"] == "test-source"
    assert record["article_id"] == "abc123def456"
    assert record["status"] == "archived"
    assert record["daily_count"] == 1
    assert record["quarantine_status"] is False
    # last_fetch_at 应为 ISO8601 格式
    assert "T" in record["last_fetch_at"], "last_fetch_at 应为 ISO8601 格式"


# ---------------------------------------------------------------------------
# test_ledger_write_and_reload (SC-8)
# ---------------------------------------------------------------------------


def test_ledger_write_and_reload(tmp_path):
    """写入 ledger 后重新加载，seen_ids 包含写入的 article_id。"""
    state_file = tmp_path / "intake-state.jsonl"

    ids_to_write = ["aaa000bbb111", "ccc222ddd333"]
    for aid in ids_to_write:
        write_ledger_entry(
            state_file,
            run_id="run-reload-test",
            source_slug="src",
            article_id=aid,
            status="archived",
            daily_count=1,
            quarantine_status=False,
        )

    seen = load_seen_article_ids(state_file)
    for aid in ids_to_write:
        assert aid in seen, f"{aid} 未出现在 seen_ids 中"


# ---------------------------------------------------------------------------
# test_stdout_format (SC-7)
# ---------------------------------------------------------------------------


def test_stdout_format(tmp_path, capsys):
    """dry-run 时 stdout 包含 discovered=N skipped=M archived=0 格式。"""
    sources_config = _write_sources_yaml(tmp_path)
    state_file = tmp_path / "intake-state.jsonl"
    output_dir = tmp_path / "raw"

    feed_urls = [("https://example.com/art-stdout-1", "Art 1")]
    mock_result = _make_discover_mock(feed_urls)

    from src.akb2.quality.intake.orchestrator import main

    with patch(
        "src.akb2.quality.intake.orchestrator.discover_articles",
        return_value=mock_result,
    ):
        exit_code = main(
            argv=[
                "--dry-run",
                "--limit", "3",
                "--sources-config", str(sources_config),
                "--state-file", str(state_file),
                "--output-dir", str(output_dir),
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 0, f"期望退出码 0，实得 {exit_code}"
    assert "discovered=" in captured.out, f"stdout 缺少 discovered=：{captured.out!r}"
    assert "skipped=" in captured.out, f"stdout 缺少 skipped=：{captured.out!r}"
    assert "archived=0" in captured.out, f"stdout 缺少 archived=0：{captured.out!r}"
