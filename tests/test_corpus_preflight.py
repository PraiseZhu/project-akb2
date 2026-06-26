"""SC-7: corpus↔main_sources alignment + 双层 + 9 领域 + per-entry 元数据 + live 层代表性。

测试不发网络（走本地 YAML 解析 + 文件存在性检查）。
SC-3 的 preflight CLI 真发网络探活，不在此 pytest 集内。

运行:
    pytest tests/test_corpus_preflight.py -k alignment -q
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
CORPUS_PATH = REPO_ROOT / "evals" / "main-pipeline-quality" / "corpus.yaml"
MAIN_SOURCES_PATH = REPO_ROOT / "config" / "main_sources.yaml"
FIXTURES_DIR = REPO_ROOT / "evals" / "main-pipeline-quality" / "fixtures"

# 基线 9 领域正样本计数 (18 正样本)
BASELINE_DOMAIN_COUNTS: dict[str, int] = {
    "AI Agent": 4,
    "Loop Engineering": 3,
    "Context Engineering": 2,
    "Harness Engineering": 2,
    "Multi-Agent Orchestration": 2,
    "AI Coding Workflow": 2,
    "Design System & Tokens": 1,
    "Knowledge Engineering": 1,
    "Protocol Design": 1,
}

# SC-7 最低对齐配额: ≥2 真实命中 main_sources domain
MIN_ALIGNED_COUNT = 2


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _load_corpus() -> dict:
    """加载 corpus YAML。"""
    return yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))


def _load_main_sources() -> dict[str, dict]:
    """加载 main_sources YAML, 返回 {slug: source_dict}。只读不写 (B3-4)。"""
    data = yaml.safe_load(MAIN_SOURCES_PATH.read_text(encoding="utf-8"))
    return {s["slug"]: s for s in data.get("sources", [])}


def _url_domain(url: str) -> str:
    """提取 URL hostname（去掉前导 www.），与 preflight._domain 一致。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_fixture(entry: dict) -> bool:
    """entry 是否为 fixture: 引用。"""
    return entry.get("url", "").startswith("fixture:")


def _is_live_positive(entry: dict) -> bool:
    """entry 是否为 live 正样本 (expected=ingest 且非 fixture)。"""
    return entry.get("expected") == "ingest" and not _is_fixture(entry)


# ---------------------------------------------------------------------------
# 测试类 — 全部方法名含 "Alignment" (class name), -k alignment 匹配全部
# ---------------------------------------------------------------------------


class TestCorpusAlignment:
    """SC-7: 双层 + 对齐 + 9 领域配比守恒 + live 层代表性 (B3-5)。"""

    def test_nine_domain_counts_conserved(self) -> None:
        """替换后 9 领域正样本计数 == 基线 (M5, 防部分替换缩小语料代表性)。"""
        corpus = _load_corpus()
        positives = [e for e in corpus["entries"] if e.get("expected") == "ingest"]
        counts: dict[str, int] = {}
        for e in positives:
            d = e["domain"]
            counts[d] = counts.get(d, 0) + 1

        for domain, expected_count in BASELINE_DOMAIN_COUNTS.items():
            actual = counts.get(domain, 0)
            assert actual == expected_count, (
                f"domain {domain}: {actual} != baseline {expected_count}"
            )
        assert len(positives) == sum(BASELINE_DOMAIN_COUNTS.values()), (
            f"total positives {len(positives)} != "
            f"{sum(BASELINE_DOMAIN_COUNTS.values())}"
        )

    def test_two_layer_exists(self) -> None:
        """双层: ≥1 fixture entry 且快照文件存在。"""
        corpus = _load_corpus()
        fixture_entries = [e for e in corpus["entries"] if _is_fixture(e)]
        assert len(fixture_entries) >= 1, "no fixture entries in corpus"

        for e in fixture_entries:
            name = e["url"].replace("fixture:", "")
            fixture_file = FIXTURES_DIR / f"{name}.md"
            assert fixture_file.exists(), f"fixture file missing: {fixture_file}"

    def test_live_positives_aligned_count(self) -> None:
        """SC-7: live 正样本 aligned_count ≥ 2 真实命中 main_sources domain。"""
        corpus = _load_corpus()
        sources = _load_main_sources()

        # domain → slugs 映射
        domain_to_slugs: dict[str, list[str]] = {}
        for slug, src in sources.items():
            d = (src.get("domain") or "").lower()
            if d:
                domain_to_slugs.setdefault(d, []).append(slug)

        live_positives = [e for e in corpus["entries"] if _is_live_positive(e)]

        aligned = []
        for e in live_positives:
            url_domain = _url_domain(e["url"])
            if url_domain in domain_to_slugs:
                aligned.append(e)

        assert len(aligned) >= MIN_ALIGNED_COUNT, (
            f"aligned_count={len(aligned)} < {MIN_ALIGNED_COUNT}"
        )
        # aligned entry 的 source_slug 必须是真实 main_sources slug
        for e in aligned:
            slug = e.get("source_slug", "")
            assert slug in sources, (
                f"aligned entry {e['url']} source_slug={slug} not in main_sources"
            )

    def test_off_corpus_live_positives_have_rationale(self) -> None:
        """off_corpus live 正样本必须有 off_corpus_rationale 且具名真实 main_sources slug。"""
        corpus = _load_corpus()
        sources = _load_main_sources()

        domain_to_slugs: dict[str, list[str]] = {}
        for slug, src in sources.items():
            d = (src.get("domain") or "").lower()
            if d:
                domain_to_slugs.setdefault(d, []).append(slug)

        live_positives = [e for e in corpus["entries"] if _is_live_positive(e)]

        for e in live_positives:
            url_domain = _url_domain(e["url"])
            if url_domain not in domain_to_slugs:
                # off_corpus: 必须有 rationale 且具名一个真实 main_sources slug
                rationale = e.get("off_corpus_rationale", "")
                assert rationale, (
                    f"off_corpus entry missing off_corpus_rationale: {e['url']}"
                )
                found_slugs = [s for s in sources if s in rationale]
                assert found_slugs, (
                    f"off_corpus rationale has no real main_sources slug: {rationale}"
                )

    def test_per_entry_feed_metadata(self) -> None:
        """每条 live 正样本有 feed_title/feed_author/feed_published/source_publisher/source_slug。"""
        corpus = _load_corpus()
        live_positives = [e for e in corpus["entries"] if _is_live_positive(e)]

        required_fields = (
            "feed_title",
            "feed_author",
            "feed_published",
            "source_publisher",
            "source_slug",
        )
        for e in live_positives:
            for field in required_fields:
                assert field in e, f"entry {e['url']} missing {field}"
                assert e[field], f"entry {e['url']} has empty {field}"

    def test_live_layer_representativeness(self) -> None:
        """B3-5: 每个非豁免领域 expected=ingest 且 source=live 计数 ≥ 1。"""
        corpus = _load_corpus()
        fixture_only_domains = corpus.get("fixture_only_domains", [])

        # 按领域统计 live 正样本计数
        live_by_domain: dict[str, int] = {}
        for e in corpus["entries"]:
            if _is_live_positive(e):
                d = e["domain"]
                live_by_domain[d] = live_by_domain.get(d, 0) + 1

        # 基线中每个领域必须有 ≥1 live, 除非在 fixture_only_domains 豁免
        for domain in BASELINE_DOMAIN_COUNTS:
            if domain in fixture_only_domains:
                continue
            actual = live_by_domain.get(domain, 0)
            assert actual >= 1, (
                f"domain {domain} has 0 live positives "
                f"(not in fixture_only_domains allowlist)"
            )

        # fixture_only_domains 内每个领域必须真实存在于 corpus 且确为全 fixture
        for domain in fixture_only_domains:
            domain_entries = [
                e for e in corpus["entries"]
                if e.get("domain") == domain and e.get("expected") == "ingest"
            ]
            assert domain_entries, (
                f"fixture_only_domain {domain} has no entries in corpus"
            )
            for e in domain_entries:
                assert _is_fixture(e), (
                    f"fixture_only_domain {domain} has live entry: {e['url']}"
                )

    def test_fixture_only_domains_allowlist_declared(self) -> None:
        """corpus 顶层有 fixture_only_domains 字段 (B3-5)。"""
        corpus = _load_corpus()
        assert "fixture_only_domains" in corpus, (
            "fixture_only_domains not declared in corpus top-level"
        )

    def test_raw_html_snapshots_exist(self) -> None:
        """B3-6: crewai/aider/mcp raw HTML 快照存在 (SC-2b 注入源)。"""
        for slug in ("crewai", "aider", "mcp"):
            path = FIXTURES_DIR / "raw-html" / f"{slug}.html"
            assert path.exists(), f"raw-html snapshot missing: {path}"
