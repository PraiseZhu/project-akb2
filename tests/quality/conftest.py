"""Quality 模块共享测试 fixtures。

所有 fixture LLM 响应，不打真 API。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Intake 模块 fixtures（step-2: 抓取/发现/抽取/raw 归档）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def intake_fixtures_dir() -> Path:
    """Path to the intake test fixtures directory."""
    return Path(__file__).parent / "fixtures" / "intake"


@pytest.fixture()
def sample_rss_xml(intake_fixtures_dir: Path) -> str:
    """Sample RSS 2.0 feed XML content."""
    return (intake_fixtures_dir / "sample_feed_rss.xml").read_text(encoding="utf-8")


@pytest.fixture()
def sample_atom_xml(intake_fixtures_dir: Path) -> str:
    """Sample Atom 1.0 feed XML content."""
    return (intake_fixtures_dir / "sample_feed_atom.xml").read_text(encoding="utf-8")


@pytest.fixture()
def sample_article_html(intake_fixtures_dir: Path) -> str:
    """Full article HTML (loop engineering)."""
    return (intake_fixtures_dir / "article_loop_engineering.html").read_text(encoding="utf-8")


@pytest.fixture()
def sample_article_md(intake_fixtures_dir: Path) -> str:
    """Full article markdown content."""
    return (intake_fixtures_dir / "article_loop_engineering.md").read_text(encoding="utf-8")


@pytest.fixture()
def sample_short_html(intake_fixtures_dir: Path) -> str:
    """Short article HTML (too short to pass extraction)."""
    return (intake_fixtures_dir / "article_short.html").read_text(encoding="utf-8")


@pytest.fixture()
def sample_short_md(intake_fixtures_dir: Path) -> str:
    """Short article markdown."""
    return (intake_fixtures_dir / "article_short.md").read_text(encoding="utf-8")


@pytest.fixture()
def sample_anti_bot_html(intake_fixtures_dir: Path) -> str:
    """Anti-bot / Cloudflare challenge HTML."""
    return (intake_fixtures_dir / "article_anti_bot.html").read_text(encoding="utf-8")


@pytest.fixture()
def sample_source_config() -> dict:
    """Single source config dict matching main_sources.yaml schema."""
    return {
        "slug": "test-blog",
        "domain": "example.com",
        "feed_url": "https://example.com/feed.xml",
        "listing_url": "https://example.com/blog",
        "rate_limit": 3600,
        "status": "trial",
        "category": "core",
    }


@pytest.fixture()
def sample_source_config_no_feed() -> dict:
    """Source config without feed_url (listing-only)."""
    return {
        "slug": "test-no-feed",
        "domain": "no-feed.example.com",
        "listing_url": "https://no-feed.example.com",
        "rate_limit": 3600,
        "status": "trial",
        "category": "supplementary",
    }


@pytest.fixture()
def temp_ledger_path(tmp_path: Path) -> Path:
    """Temporary ledger jsonl path for dedup testing."""
    return tmp_path / "quality-ledger.jsonl"


@pytest.fixture()
def temp_failures_path(tmp_path: Path) -> Path:
    """Temporary failures jsonl path for signature testing."""
    return tmp_path / "failures.jsonl"


@pytest.fixture()
def temp_output_dir(tmp_path: Path) -> Path:
    """Temporary output directory for raw archiver."""
    out = tmp_path / "raw"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Fixture LLM 响应工厂
# ---------------------------------------------------------------------------


def make_mock_route_llm(responses: dict[str, Any] | None = None):
    """创建一个 mock route_llm 函数，返回预设响应。

    Args:
        responses: key="model_family:call_index" → LLMResponse 的 dict。
            默认提供空响应。

    Returns:
        callable: 签名兼容 route_llm 的 mock 函数。
    """
    _responses = responses or {}
    _call_counts: dict[str, int] = {}

    def mock_route_llm(
        model_family: str,
        messages: list[dict],
        response_schema: dict | None = None,
        **kwargs: Any,
    ):
        from src.akb2.quality.llm_router import LLMResponse

        key = model_family
        idx = _call_counts.get(key, 0)
        _call_counts[key] = idx + 1
        lookup = f"{key}:{idx}"

        if lookup in _responses:
            return _responses[lookup]
        if key in _responses:
            return _responses[key]
        # 默认空响应
        return LLMResponse(
            content={"pass": True, "reason": "mock default"},
            model_used=key,
            calls_used=1,
        )

    mock_route_llm.call_counts = _call_counts  # type: ignore[attr-defined]
    return mock_route_llm


# ---------------------------------------------------------------------------
# 共享 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_raw_content() -> str:
    """黄金样张风格的 raw 内容（模拟 Loop Engineering 文章）。"""
    return """---
artifact_type: raw
source_slug: addy-osmani-loop-engineering
source_url: https://example.com/loop-engineering
source_title: Loop Engineering
source_authors: Addy Osmani
source_publisher: Addy Osmani's Blog
source_published_at: 2026-06-07
fetched_at: 2026-06-13T00:00:00Z
fetched_via: defuddle
---

# §A Loop Engineering

Loop engineering is the practice of designing AI agent loops that are bounded,
convergent, and observable. An agent loop without explicit stopping conditions is
a runaway cost generator.

# §B How: A Complete Loop Shape

A well-designed loop has five components:
1. A trigger (schedule, event, or manual)
2. A work unit selector (read ledger, pick next item)
3. An action executor (run the actual work)
4. A quality gate (verify output meets standards)
5. A ledger writer (record what happened)

原文操作示例（AKB 操作化命令字面量集，供 SG-command-recheck 全文回查锚定。
fenced text block 不被 must_cover scanner 拾取——api_signatures/numbered_steps 排除
fenced，cli_commands 只扫 bash-lang，code_blocks 只找 "method":"value"）：

```text
trigger={type:'schedule',cron:'0 * * * *'}
work_item=ledger.next(status='pending',limit=1)
assert run_quality_gate(result,grader='gpt-5.4').defects == []
read ledger; pick next
run the work
ledger.append(entry)
ledger.append(event)
ledger.append({event:'x'})
ledger.append({event:'llm_call',model_family:model_family,prompt_version:prompt_version,timeout_s:60,max_retries:0,fallback_used:false,raw_anchor:raw_anchor})
checkpoint=ledger.load_latest(run_id=run_id)
queue.next()
golden_sample.check(content)
set_checker_model('gpt-5.4')
record_failure(loop='loop1',node=node,error_class=error_class)
value = measure_usage_metrics(data)
assert maker_model != checker_model
maker_model='deepseek-v4-pro'; checker_model='gpt-5.4'; assert maker_model != checker_model and fallback_used is False
MAX_REPAIR_ROUNDS=3; DISTILL_TIMEOUT_SECONDS=60; GRADER_TIMEOUT_SECONDS=120; MAX_PARALLEL_ITEMS=1
trigger=schedule
configure_primitives(input=work_item, timeout_seconds=60)
compare_capabilities(input=work_item, timeout_seconds=60)
record_primitive_coverage(input=work_item, timeout_seconds=60)
record_model_family_separation(input=work_item, timeout_seconds=60)
cmd(input=work_item, timeout_seconds=60)
make deploy
make pipeline
make test
python -m akb2.quality.runner --once
```

The key insight: every loop must write to the ledger BEFORE executing the next action,
so recovery from crashes is a simple read of the last ledger entry.

# §C Convergence Over Restart

When a step produces defects, fix the specific defects rather than restarting the
entire pipeline. This is the principle of convergence over restart. The cost of
re-running upstream steps (fetching, scoring, synthesizing) is far higher than the
cost of targeted repair.

# §D Budget Constraints

Every loop must have an explicit budget. The budget must be a named constant, not
an inline magic number. Common budget types include max_rounds (for iteration
loops), budget_seconds (for time-bounded loops), and max_retries (for retry loops).

# §E Maker/Checker Separation

The model that produces content must not be the model that judges it. Use different
model families (e.g., DeepSeek for extraction, GPT-5.4 for review, Gemini Flash for
fallback). This ensures that systematic biases in one family are caught by another.

# §F Golden Sample Anchoring

Quality standards must be anchored to concrete golden samples. The golden sample
defines the minimum acceptable quality level. Every new piece of content must pass
the same 14-step checklist that the golden sample passed.

# §G Measured Value Verdict

Don't predict value — measure it. Use real usage data (call frequency, co-occurrence,
hit rate, conversion rate) to determine the actual value of knowledge. Star ratings
(0-3) are driven by usage data, not by model predictions.

# §H Agent Boundary Rules

Not every node needs an agent. Fixed transformations (fixed input → one API call →
schema-validated output) should be implemented as scripts or LLM functions, not as
agent sessions. Agents are only needed when the node requires "autonomous decision
about the next step."

# §I Skill Packs

Loops need externalized knowledge: rubrics, known pitfalls, templates — these are
the carriers of loop compound interest. A loop without a skill pack is a loop that
cannot improve from experience.

# §J Failure Signatures

Every failure must leave a signature. Signatures are classified by (loop, node,
error_class). The third occurrence of the same signature triggers escalation. This
is the immune system of the loop ecosystem.

## 锚点目录

- §A: Loop Engineering 定义
- §B: How 模板 — 完整 loop 形状
- §C: 收敛优先于重跑
- §D: 预算约束
- §E: Maker/Checker 分离
- §F: 黄金样张锚定
- §G: 实测价值裁决
- §H: Agent 边界规则
- §I: 技能包
- §J: 故障签名
"""


@pytest.fixture(autouse=True)
def sample_raw_path(sample_raw_content, tmp_path, monkeypatch) -> str:
    """raw 文件 fixture（autouse）：在 per-test 隔离的 tmp CWD 下创建真实文件。

    覆盖两类存量测试：
    - 用 sample_raw_path fixture 的测试：拿到路径字符串
    - 硬编码 "raw/addy-osmani-loop-engineering--raw--2026-06-11.md" 或 "raw/test.md"
      的测试：compose_candidate 读 Path(raw_path).read_text() 时能扫到 must_cover，
      不再因 raw_content=None 触发 SG-must-cover fail-closed（gate 未弱化）。

    隔离机制（F-P2-B 修复）：以前在 *进程* CWD 相对 raw/ 下 create + teardown rmdir，
    多 test 共享同一 raw/ 目录，并行/乱序下首个 mkdir 与末个 rmdir 竞态曾爆
    FileNotFoundError。现改用 pytest tmp_path + monkeypatch.chdir：每个 test 独占
    自己的 tmp 工作目录，相对路径契约不变（test 内 "raw/test.md" 仍按 CWD 解析、
    CWD 已切到本 fixture 专属子目录），但目录不再跨 test 共享，teardown 由 pytest
    清理 tmp_path 完成、无需手动 rmdir，竞态消除。断言语义不动。

    关键：chdir 进 tmp_path 下一个 **专属子目录**（不是 tmp_path 本身），避免与那些
    自己往 `tmp_path / "raw"` 写文件并断言计数的 test（如 test_runner_queue /
    test_intake_raw）撞同一个 raw/ 目录——它们用绝对 tmp_path/raw，本 fixture 的
    relative raw/ 落在 tmp_path/_sample_raw_cwd/raw/，两者互不相干。

    主路径去掉 source_conclusion 句（"The key insight:..."），避免 must_cover 代码直出
    在 TLDR 多渲染一条 (source_grounded) 使 test_tldr_has_sentence_boundaries_per_anchor
    的 count==3 断言失败。保留 5 条 source_operation 编号步骤，gate 仍 fail-closed
    要求 How 节覆盖。raw/test.md 用完整内容（图测试不检查 TLDR count）。
    """
    work_dir = tmp_path / "_sample_raw_cwd"
    work_dir.mkdir(exist_ok=True)
    monkeypatch.chdir(work_dir)
    raw_dir = work_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    main_path = "raw/addy-osmani-loop-engineering--raw--2026-06-11.md"
    test_path = "raw/test.md"
    main_content = sample_raw_content.replace(
        "The key insight: every loop must write to the ledger BEFORE executing the next action,\n"
        "so recovery from crashes is a simple read of the last ledger entry.",
        "",
    )
    (work_dir / main_path).write_text(main_content, encoding="utf-8")
    (work_dir / test_path).write_text(sample_raw_content, encoding="utf-8")
    yield main_path


@pytest.fixture
def tmp_ledger_path() -> str:
    """临时账本路径 fixture。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        f.write("")
    path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def admission_pass_responses() -> dict[str, Any]:
    """准入三判定全 pass 的 fixture LLM 响应。"""
    from src.akb2.quality.llm_router import LLMResponse

    return {
        "gemini-flash:0": LLMResponse(
            content={"pass": True, "reason": "命中 Loop Engineering / AI Agent 领域"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
        "gemini-flash:1": LLMResponse(
            content={"pass": True, "reason": "含可执行 loop 组件模板和代码示例"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
        "gemini-flash:2": LLMResponse(
            content={"pass": True, "reason": "无既有 wiki 引用，默认通过"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
    }


@pytest.fixture
def admission_fail_news_response() -> dict[str, Any]:
    """新闻型负样本 fixture 响应（How 倾向 fail）。"""
    from src.akb2.quality.llm_router import LLMResponse

    return {
        "gemini-flash:0": LLMResponse(
            content={"pass": True, "reason": "命中 AI Agent 领域"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
        "gemini-flash:1": LLMResponse(
            content={"pass": False, "reason": "纯新闻播报，无可执行步骤或代码"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
        "gemini-flash:2": LLMResponse(
            content={"pass": True, "reason": "无既有 wiki 引用，默认通过"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
    }


@pytest.fixture
def admission_fail_plagiarism_response() -> dict[str, Any]:
    """洗稿型负样本 fixture 响应（新颖度 fail）。"""
    from src.akb2.quality.llm_router import LLMResponse

    return {
        "gemini-flash:0": LLMResponse(
            content={"pass": True, "reason": "命中 AI Agent 领域"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
        "gemini-flash:1": LLMResponse(
            content={"pass": True, "reason": "含 loop 设计模式"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
        "gemini-flash:2": LLMResponse(
            content={"pass": False, "reason": "与既有 wiki 高度重叠，零增量"},
            model_used="gemini-2.5-flash",
            calls_used=1,
        ),
    }


@pytest.fixture
def extract_success_response() -> dict[str, Any]:
    """段 1 抽取成功 fixture 响应。"""
    from src.akb2.quality.llm_router import LLMResponse

    return {
        "deepseek-v4-pro": LLMResponse(
            content={
                "claims": [
                    {
                        "text": "Loop engineering is the practice of designing bounded, convergent, observable AI agent loops",
                        "anchor": "§A",
                        "provenance": "source_grounded",
                        "evidence_gap": "未量化：原文未提供 bounded/convergent 的可度量判据与失败样本",
                    },
                    {
                        "text": "A well-designed loop has five components: trigger, work unit selector, action executor, quality gate, ledger writer",
                        "anchor": "§B",
                        "provenance": "source_grounded",
                        "evidence_gap": "未比较：原文未比较五组件在不同负载下的取舍与替代方案",
                    },
                    {
                        "text": "Fix specific defects rather than restarting the entire pipeline — convergence over restart",
                        "anchor": "§C",
                        "provenance": "source_grounded",
                        "evidence_gap": "未验证：原文未提供定点修复 vs 整体重跑的实测成本/收敛数据",
                    },
                ],
                "how_steps": [
                    {"instruction": "Define trigger type", "command": "schedule|event|manual", "executable": True},
                    {"instruction": "Implement work unit selector", "command": "read ledger; pick next", "executable": True},
                    {"instruction": "Build action executor", "command": "run the work", "executable": True},
                    {"instruction": "Add quality gate", "command": "verify output", "executable": True},
                    {"instruction": "Write ledger entry BEFORE next action", "command": "ledger.append(entry)", "executable": True},
                ],
                "params": {
                    "max_rounds": 3,
                    "budget_seconds": 300,
                    "maker_model": "deepseek-v4-pro",
                    "checker_model": "gpt-5.4",
                },
                "referenced_concepts": [
                    "loop-engineering",
                    "harness-engineering",
                    "agent-collaboration",
                ],
                "tldr": (
                    "Loop engineering is designing bounded, convergent, observable AI agent loops "
                    "instead of prompting agents directly."
                ),
                "argumentation": [
                    {
                        "claim_anchor": "§A",
                        "provenance": "source_grounded",
                        "explanation": (
                            "The practice shifts the human role from prompt-author to loop-designer, "
                            "enabling scalable automation via observable, convergent pipelines."
                        ),
                    },
                    {
                        "claim_anchor": "§B",
                        "provenance": "source_grounded",
                        "explanation": (
                            "Five components form the minimal viable loop: each is independently testable "
                            "and replaceable, satisfying the bounded-automation principle."
                        ),
                    },
                    {
                        "claim_anchor": "§C",
                        "provenance": "source_grounded",
                        "explanation": (
                            "Targeted repair of specific defects avoids full restart costs and preserves "
                            "already-converged sections, a key efficiency gain over naive retry."
                        ),
                    },
                ],
                "applicability_boundary": (
                    "Applies to multi-step AI pipelines with measurable quality outputs. "
                    "Not suitable for one-shot queries or pipelines without a quality gate. "
                    "Requires a reliable ledger store and idempotent work units."
                ),
                "graph_edges": [
                    {
                        "concept": "harness-engineering",
                        "relation_type": "part_of",
                        "strength": "strong",
                        "evidence": "[raw §B] original text: \"loop sits one floor above the harness\"",
                    },
                    {
                        "concept": "agent-collaboration",
                        "relation_type": "requires",
                        "strength": "medium",
                        "evidence": "multi-agent loops require coordination primitives",
                    },
                ],
            },
            model_used="deepseek-v4-pro",
            calls_used=1,
        ),
    }


@pytest.fixture
def extract_fail_invalid_json_response() -> dict[str, Any]:
    """段 1 抽取失败 fixture（非法 JSON，3 次重试全失败）。"""
    from src.akb2.quality.llm_router import LLMResponse

    return {
        "deepseek-v4-pro": LLMResponse(
            content="not a valid json response",
            model_used="deepseek-v4-pro",
            calls_used=1,
        ),
    }


@pytest.fixture
def extract_fail_missing_anchor_response() -> dict[str, Any]:
    """段 1 抽取失败 fixture（claims 缺少 anchor 字段）。"""
    from src.akb2.quality.llm_router import LLMResponse

    return {
        "deepseek-v4-pro": LLMResponse(
            content={
                "claims": [
                    {
                        "text": "some claim without anchor",
                        "provenance": "source_grounded",
                    }
                ],
                "how_steps": [],
                "params": {},
                "referenced_concepts": [],
                "tldr": "A short summary that is long enough to pass the minimum length check.",
                "argumentation": [],
                "applicability_boundary": (
                    "Applies when there is at least one claim. Not applicable to empty content."
                ),
                "graph_edges": [],
            },
            model_used="deepseek-v4-pro",
            calls_used=1,
        ),
    }


@pytest.fixture
def news_content() -> str:
    """新闻型内容 fixture（How 倾向应 fail）。"""
    return """---
artifact_type: raw
source_slug: google-io-2026-recap
source_url: https://example.com/google-io-2026
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
"""
