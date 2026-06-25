"""test_distill_must_cover.py — step-2: must_cover 代码直出渲染验收。

覆盖两条 SC：
    - SC-2.1: 含 must_cover 的 fixture，渲染后 How/适用边界/TLDR 节分别包含
      对应 must_cover 项字面量（source_operation/boundary/conclusion 三类各归其节）。
    - SC-2.2: mock 掉语义 segment（how_steps=[]）后，must_cover 操作仍被代码渲染
      进 How 节（LLM 无最终选择权）。

设计要点：
    - 真实写 raw 文件到 tmp，使 compose_candidate 能 read_text → scan_all 扫到 must_cover，
      且 self-gate A3 锚点校验生效（raw_content 非 None）。
    - 复用 conftest.make_mock_route_llm 与 test_distill_frontmatter._valid_compose_data，
      与既有测试同源，避免重复 fixture。
    - self-gate 必须通过（0 defects）——must_cover 渲染不能引入 A3/A8/SG-how 等缺陷。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from src.akb2.quality.checks_structural import run_candidate_self_gate
from src.akb2.quality.distill import ExtractResult, compose_candidate
from src.akb2.quality.source_ops_scanners import scan_all
from tests.quality.conftest import make_mock_route_llm
from tests.quality.test_distill_frontmatter import _valid_compose_data

# 含全部三类 must_cover 的 raw fixture：§A 显式结论、§B 编号步骤+MCP 命令+边界语句。
# §A/§B/§C 声明为 markdown 标题，A3 锚点校验可识别。
_MUST_COVER_RAW = """---
artifact_type: raw
source_slug: must-cover-fixture
source_url: https://example.com/must-cover
source_title: Must Cover Fixture
source_authors: Test
source_publisher: Test
source_published_at: 2026-06-23
fetched_at: 2026-06-23T00:00:00Z
fetched_via: defuddle
---

# §A Overview

The key insight: must_cover operations must be rendered by code, not chosen by the LLM.

# §B How Steps

1. Initialize the gateway with explicit timeout
2. Call tools/list to enumerate available tools
3. Validate responses against the schema

原文操作示例（字面量锚定，供 SG-command-recheck 回查；分离语义，不弱化 gate）：

- `initialize`
- `trigger={type:'schedule',cron:'0 * * * *'}`
- `work_item=ledger.next(status='pending',limit=1)`
- `assert run_quality_gate(result,grader='gpt-5.4').defects == []`
- `ledger.append({run_id:run_id,item_id:work_item.id,status:'checkpointed',next_action:'execute'})`
- `repair_router.dispatch(error_class=current_failure.type, anchor=current_failure.raw_anchor)`

This procedure 仅适用 bounded automation loops; 不支持 one-shot queries.

# §C Budget

Every loop must have a named budget constant.
"""


def _write_raw(tmp_dir: str, raw: str = _MUST_COVER_RAW) -> str:
    """Write raw content to a tmp file and return its path."""
    raw_path = os.path.join(tmp_dir, "must-cover-fixture--raw--2026-06-23.md")
    Path(raw_path).write_text(raw, encoding="utf-8")
    return raw_path


def _mock_llm(data: dict[str, Any]):
    """Build a mock route_llm returning the given extract data (no real API)."""
    return make_mock_route_llm(
        {
            "deepseek-v4-pro": type(
                "R",
                (),
                {
                    "content": data,
                    "model_used": "deepseek-v4-pro",
                    "calls_used": 1,
                },
            )()
        }
    )


def _extract_section(page: str, title: str) -> str:
    """Return the body text of a ## section (or ### subsection) by title."""
    pattern = rf"({re.escape(title)}.*?)(?=\n## |\Z)"
    m = re.search(pattern, page, re.S)
    return m.group(1) if m else ""


class TestMustCoverCodeDirectRender:
    """must_cover 三类由代码直接渲染进正文对应节，LLM 无最终选择权。"""

    def test_sc_2_1_must_cover_literals_in_target_sections(self):
        """SC-2.1: How/适用边界/TLDR 节分别包含对应 must_cover 字面量，且 self-gate 通过。

        fixture 含 source_operation（编号步骤 + mcp_protocol 命令）、
        source_boundary（仅适用/不支持 语句）、source_conclusion（The key insight 语句）。
        """
        data = _valid_compose_data()
        extract_result = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = _mock_llm(data)

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = _write_raw(tmp_dir)
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=raw_path,
                batch_id="sc-2-1",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True, f"compose 失败: {result.error}"
            page = Path(result.candidate_path).read_text(encoding="utf-8")

            # 先确认 scanner 确实扫到三类 must_cover（前置断言：测试本身有效）
            items = scan_all(_MUST_COVER_RAW)
            kinds = {it.kind for it in items}
            assert kinds == {"source_operation", "source_boundary", "source_conclusion"}, (
                f"fixture 应含三类 must_cover，实际: {kinds}"
            )

            how_section = _extract_section(page, "### 操作步骤")
            boundary_section = _extract_section(page, "## 适用边界")
            tldr_section = _extract_section(page, "## TLDR")

            # SC-2.1: source_operation 字面量出现在 How 节
            for op_literal in (
                "1. Initialize the gateway with explicit timeout",
                "2. Call tools/list to enumerate available tools",
                "3. Validate responses against the schema",
            ):
                assert op_literal in how_section, (
                    f"must_cover operation 未渲染进 How 节: {op_literal!r}"
                )
            # mcp_protocol 命令也由代码直出
            assert "tools/list" in how_section, "must_cover mcp_protocol 命令 tools/list 未渲染进 How 节"

            # SC-2.1: source_boundary 字面量出现在适用边界节
            boundary_literal = "This procedure 仅适用 bounded automation loops"
            assert boundary_literal in boundary_section, (
                f"must_cover boundary 未渲染进适用边界节: {boundary_literal!r}"
            )

            # SC-2.1: source_conclusion 字面量出现在 TLDR 节
            conclusion_literal = "The key insight: must_cover operations must be rendered by code"
            assert conclusion_literal in tldr_section, (
                f"must_cover conclusion 未渲染进 TLDR 节: {conclusion_literal!r}"
            )

            # self-gate 必须通过（must_cover 渲染不引入 A3/A8/SG-how 缺陷）
            defects = run_candidate_self_gate(page, raw_content=_MUST_COVER_RAW)
            assert defects == [], (
                "must_cover 渲染引入 self-gate 缺陷: "
                + "; ".join(f"{d.dimension}@{d.location}: {d.defect}" for d in defects)
            )

    def test_sc_2_2_must_cover_renders_when_how_steps_empty(self):
        """SC-2.2: mock how_steps=[] 后，must_cover 操作仍被代码渲染进 How 节。

        LLM 语义 segment 为空时，operation 仍由代码直出——LLM 无最终选择权。
        且不出现 FORBIDDEN_HOW_FALLBACK_MARKERS 占位（must_cover 存在时不降级）。
        """
        data = _valid_compose_data(how_steps=[])
        extract_result = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = _mock_llm(data)

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = _write_raw(tmp_dir)
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=raw_path,
                batch_id="sc-2-2",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True, f"compose 失败（how_steps=[]）: {result.error}"
            page = Path(result.candidate_path).read_text(encoding="utf-8")
            how_section = _extract_section(page, "### 操作步骤")

            # SC-2.2: how_steps=[] 时 How 节仍含全部 must_cover operation
            for op_literal in (
                "1. Initialize the gateway with explicit timeout",
                "2. Call tools/list to enumerate available tools",
                "3. Validate responses against the schema",
            ):
                assert op_literal in how_section, (
                    f"how_steps=[] 时 must_cover operation 未渲染进 How 节: {op_literal!r}"
                )

            # must_cover 存在时不得出现放弃式占位（SG-how-executability 会拒绝）
            assert "原文无可安全锚定的操作步骤" not in how_section, (
                "how_steps=[] 但 must_cover 存在时仍出现放弃式占位"
            )

            # self-gate 仍通过（must_cover 提供的可执行命令满足 MIN_HOW_EXECUTABLE_STEPS）
            defects = run_candidate_self_gate(page, raw_content=_MUST_COVER_RAW)
            assert defects == [], (
                "how_steps=[] 时 must_cover 渲染引入 self-gate 缺陷: "
                + "; ".join(f"{d.dimension}@{d.location}: {d.defect}" for d in defects)
            )

    def test_raw_pass_through_must_cover_when_raw_file_absent(self):
        """阶段1: raw_path 文件不存在但传入 raw_content 时，must_cover 仍渲染。

        模拟 calibration 链路（只构造 raw_path 但不落地文件）。回归前 compose_candidate
        只 read_text(raw_path)，文件缺失 → raw_content=None → scan_all 跳过 → must_cover
        operation 不渲染（How 节空，间接导致 SG-how-density 假 0）。修复后调用方传入的
        raw_content 是权威源，read_text 仅 fallback，所以 must_cover 不再静默失真。
        """
        data = _valid_compose_data()
        extract_result = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = _mock_llm(data)

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 关键：raw_path 指向一个不存在的文件（calibration 只拼路径不写盘）
            missing_raw_path = os.path.join(tmp_dir, "never-written--raw--calibration.md")
            assert not Path(missing_raw_path).exists()

            result = compose_candidate(
                extract_result=extract_result,
                raw_path=missing_raw_path,
                batch_id="raw-passthrough",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
                raw_content=_MUST_COVER_RAW,  # 权威源：磁盘没有，靠传入
            )

            assert result.success is True, f"compose 失败: {result.error}"
            page = Path(result.candidate_path).read_text(encoding="utf-8")
            how_section = _extract_section(page, "### 操作步骤")

            # must_cover operation 字面量仍被渲染（证明 raw_content pass-through 生效）
            for op_literal in (
                "1. Initialize the gateway with explicit timeout",
                "2. Call tools/list to enumerate available tools",
                "3. Validate responses against the schema",
            ):
                assert op_literal in how_section, (
                    f"raw 文件缺失但传了 raw_content 时 must_cover 未渲染: {op_literal!r}"
                    "（raw pass-through 修复失效）"
                )

    def test_canonical_sample_raw_fires_must_cover(self, sample_raw_content):
        """回归保护：既有 sample_raw_content fixture 也能触发 must_cover 渲染。

        sample_raw_content 含 5 条编号步骤（source_operation，§B）+ 1 条
        The key insight 结论（source_conclusion，§B）。渲染后 How/TLDR 节应含这些字面量，
        且 self-gate 通过——确认 must_cover 代码直出与既有 10 节模板/锚点契约兼容。
        """
        data = _valid_compose_data()
        extract_result = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = _mock_llm(data)

        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = os.path.join(tmp_dir, "addy-osmani-loop-engineering--raw--2026-06-11.md")
            # step-4 SG-command-recheck: 补原文操作示例命令字面量，使回查通过（分离语义，不弱化 gate）。
            raw_with_cmds = (
                sample_raw_content.rstrip()
                + "\n\n# §G 原文操作示例\n\n"
                + "- `trigger={type:'schedule',cron:'0 * * * *'}`\n"
                + "- `work_item=ledger.next(status='pending',limit=1)`\n"
                + "- `assert run_quality_gate(result,grader='gpt-5.4').defects == []`\n"
            )
            Path(raw_path).write_text(raw_with_cmds, encoding="utf-8")
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=raw_path,
                batch_id="canonical",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True, f"canonical fixture compose 失败: {result.error}"
            page = Path(result.candidate_path).read_text(encoding="utf-8")
            how_section = _extract_section(page, "### 操作步骤")
            tldr_section = _extract_section(page, "## TLDR")

            # sample_raw_content 的编号步骤必须由代码直出进 How 节
            assert "1. A trigger (schedule, event, or manual)" in how_section, (
                "canonical sample must_cover operation 未渲染进 How 节"
            )
            # sample_raw_content 的结论必须由代码直出进 TLDR 节
            assert "The key insight: every loop must write to the ledger" in tldr_section, (
                "canonical sample must_cover conclusion 未渲染进 TLDR 节"
            )

            # self-gate 通过（must_cover 与既有模板兼容）
            defects = run_candidate_self_gate(page, raw_content=raw_with_cmds)
            assert defects == [], (
                "canonical fixture must_cover 渲染引入 self-gate 缺陷: "
                + "; ".join(f"{d.dimension}@{d.location}: {d.defect}" for d in defects)
            )
