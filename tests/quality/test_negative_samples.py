"""负样本回归测试 — SC-6.3 (master SC-2)。

验证 2 个负样本 100% 拒于准入段，且账本显示零提炼调用。
全 fixture mock，不调真 API。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def plagiarism_content() -> str:
    """洗稿型负样本内容 (Zynd AI X Article)。"""
    return """---
artifact_type: raw
source_slug: zynd-ai-x-article
source_url: https://x.com/zyndai/status/2064344562110263722
source_title: Zynd AI on X
source_authors: Zynd AI
source_publisher: X (Twitter)
source_published_at: 2025-03-15
fetched_at: 2026-06-13T00:00:00Z
fetched_via: defuddle
---

# Zynd AI Agent Framework

AI agents are the future of software development. Many companies are building
agents that can code, test, and deploy. The key insight is that agents need
to be reliable and predictable.

Some people say agents should have loops. Others say they should be stateless.
The truth is somewhere in between. We think the best approach is to use a
hybrid model that combines both approaches.

Our framework supports multiple LLM providers. It has a nice UI and good
documentation. We're excited to see what developers build with it.

This is basically a rewrite of existing agent framework concepts without
adding any new executable knowledge or concrete implementation details.
"""


@pytest.fixture
def news_content() -> str:
    """新闻型负样本内容 (谷歌 I/O 视频简报)。"""
    return """---
artifact_type: raw
source_slug: google-io-2026-recap
source_url: fixture:google-io-2026-news
source_title: Google I/O 2026 Recap
source_authors: Tech Reporter
source_publisher: Tech News Daily
source_published_at: 2026-06-10
fetched_at: 2026-06-13T00:00:00Z
fetched_via: defuddle
---

# Google I/O 2026 Recap

Google announced several new products at I/O 2026. The keynote included
updates to Gemini, Android, and Web platform. The new Gemini 3.0 model
offers improved reasoning capabilities. Android 17 will ship with new
AI features. Chrome got a performance boost of 15% in the latest benchmark.

The event was held at Shoreline Amphitheatre with 5000 attendees.
Sundar Pichai opened the keynote with a focus on AI responsibility.

New features were announced for Google Workspace, including AI-powered
summaries in Docs and smart replies in Gmail. Google Cloud got new
AI infrastructure offerings.

This is pure news reporting with no executable steps, code examples,
or concrete how-to knowledge.
"""


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------


class TestNegativeSamples:
    """SC-6.3: 负样本拒收测试。"""

    def test_plagiarism_rejected_by_admission(
        self, tmp_path: Path, plagiarism_content: str
    ) -> None:
        """洗稿型负样本被准入段拒收。"""
        from src.akb2.quality.admission import run_admission
        from src.akb2.quality.llm_router import LLMResponse
        from tests.quality.conftest import make_mock_route_llm

        ledger_path = tmp_path / "test-ledger.jsonl"

        # exact slug/url 命中时，novelty 预筛直接拒收，不发起 unified LLM 判定。
        mock_llm = make_mock_route_llm()
        result = run_admission(
            raw_path="raw/zynd-ai-x-article--raw--2026-06-13.md",
            raw_content=plagiarism_content,
            ledger_path=str(ledger_path),
            wiki_references=[
                {
                    "slug": "zynd-ai-x-article",
                    "source_url": "https://x.com/zyndai/status/2064344562110263722",
                    "title": "Zynd AI on X",
                },
            ],
            route_llm_fn=mock_llm,
        )

        assert result.passed is False, (
            f"洗稿型负样本应被拒收，实际 passed={result.passed}"
        )

        # exact slug/url 预筛只留下确定性 novelty 拒收证据。
        assert len(result.checks) == 1
        novelty_check = result.checks[0]
        assert novelty_check.name == "novelty"
        assert novelty_check.passed is False
        assert "确定性拒收" in novelty_check.reason or "同源" in novelty_check.reason

    def test_news_rejected_by_admission(
        self, tmp_path: Path, news_content: str
    ) -> None:
        """新闻型负样本被准入段拒收。"""
        from src.akb2.quality.admission import run_admission
        from src.akb2.quality.llm_router import LLMResponse
        from tests.quality.conftest import make_mock_route_llm

        ledger_path = tmp_path / "test-ledger.jsonl"

        # unified schema：新闻负样本由 non_knowledge / reject lane 拒收，
        # 不再依赖 how_tendency / procedural 旧字段。
        mock_llm = make_mock_route_llm({
            "deepseek-v4-pro": LLMResponse(
                content={
                    "topic_pass": True,
                    "lane": "reject",
                    "non_knowledge": True,
                    "mechanism_pass": False,
                    "how_pass": False,
                    "reason": "mock: 纯新闻播报，非知识",
                },
                model_used="gemini-2.5-flash",
                calls_used=1,
            ),
        })

        result = run_admission(
            raw_path="raw/google-io-2026-recap--raw--2026-06-13.md",
            raw_content=news_content,
            ledger_path=str(ledger_path),
            route_llm_fn=mock_llm,
        )

        assert result.passed is False, (
            f"新闻型负样本应被拒收，实际 passed={result.passed}"
        )
        # P0-c：拒收主因是 non_knowledge reject 门，不是靠 how_tendency 造假绿。
        assert "non_knowledge" in result.reason, (
            f"新闻应靠 non_knowledge reject 门拒，实际 reason={result.reason}"
        )
        # reject 不下传 content_lane（不混进通过态，w9）。
        assert result.content_lane == "", (
            f"reject 不应下传 content_lane，实际={result.content_lane!r}"
        )

    def test_rejected_samples_zero_distill_calls(self, tmp_path: Path) -> None:
        """拒收样本的账本显示零提炼调用。"""
        from src.akb2.quality.admission import run_admission
        from src.akb2.quality.llm_router import LLMResponse
        from tests.quality.conftest import make_mock_route_llm

        ledger_path = tmp_path / "test-ledger.jsonl"

        reject_content = """---
artifact_type: raw
source_slug: test-reject
source_url: https://example.com/reject
source_title: Test Reject
source_authors: Test
source_publisher: Test
source_published_at: 2026-06-13
fetched_at: 2026-06-13T00:00:00Z
fetched_via: defuddle
---

# Test Reject

Just news, no how-to content.
"""

        # unified schema：reject / non_knowledge 直接拒收，非 how_tendency 拒因。
        mock_llm = make_mock_route_llm({
            "deepseek-v4-pro": LLMResponse(
                content={
                    "topic_pass": True,
                    "lane": "reject",
                    "non_knowledge": True,
                    "mechanism_pass": False,
                    "how_pass": False,
                    "reason": "mock: 纯新闻，非知识",
                },
                model_used="gemini-2.5-flash",
                calls_used=1,
            ),
        })

        result = run_admission(
            raw_path="raw/test-reject--raw--2026-06-13.md",
            raw_content=reject_content,
            ledger_path=str(ledger_path),
            route_llm_fn=mock_llm,
        )

        assert result.passed is False

        # 验证账本中无 distill 事件
        if Path(ledger_path).exists():
            events = []
            with open(ledger_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))

            distill_events = [
                e for e in events
                if e.get("event", "").startswith("distill")
            ]
            assert len(distill_events) == 0, (
                f"拒收样本不应有 distill 事件: {distill_events}"
            )

    def test_corpus_negative_count(self) -> None:
        """corpus.yaml 中负样本恰 2 条。"""
        import yaml

        corpus_path = (
            Path(__file__).resolve().parents[2]
            / "evals"
            / "main-pipeline-quality"
            / "corpus.yaml"
        )
        corpus = yaml.safe_load(corpus_path.read_text(encoding="utf-8"))

        entries = corpus["entries"]
        negative = [e for e in entries if e.get("expected") == "reject"]
        assert len(negative) == 2, (
            f"负样本应为 2 条，实际 {len(negative)}"
        )

        # 验证负样本 URL
        neg_urls = {e["url"] for e in negative}
        assert "fixture:x-plagiarism-negative" in neg_urls, (
            "缺少洗稿型负样本 URL（B 已转 fixture:x-plagiarism-negative）"
        )
        assert "fixture:google-io-2026-news" in neg_urls, (
            "缺少新闻型负样本 fixture URL"
        )
