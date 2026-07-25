"""test_distill_frontmatter.py — 段 2 成稿 frontmatter 硬字段全检。

覆盖:
    - gate_decision: null 写死（已知坑 1）
    - source_refs 含 raw 路径（已知坑 2）
    - gate_reason ≤150 字符（已知坑 3）
    - future_questions 2-3 条
    - related_concepts ≥2
    - status: candidate
    - 后续需对照 candidate_page_fixture.md 进行完整字段校验
"""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import yaml

from src.akb2.quality.checks_structural import run_candidate_self_gate
from src.akb2.quality.distill import (
    GATE_REASON_MAX_CHARS,
    ExtractResult,
    compose_candidate,
)


def _valid_extract_result(extract_success_response) -> ExtractResult:
    return ExtractResult(
        success=True,
        data=extract_success_response["deepseek-v4-pro"].content,
        retries_used=1,
    )


# AKB 操作化命令字面量集（fenced text block），供各测试 raw 补全后通过 SG-command-recheck
# 全文回查。fenced block 不被 must_cover scanner 拾取（api_signatures/numbered_steps 排除
# fenced，cli_commands 只扫 bash-lang，code_blocks 只找 "method":"value"），故不引入新
# must_cover 项、不破坏 provenance ratio / TLDR count 等断言。
_COMMAND_EXAMPLES_BLOCK = """

原文操作示例（AKB 操作化命令字面量，供 SG-command-recheck 回查锚定）：

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
queue_item={source_url,raw_anchor,content_hash,output_path,lifecycle_state}
ledger.append({event:'distill_extract',status:'start'})
max_repair_rounds=3; assert remaining_defects == 0
ledger.append({run_id:run_id,item_id:work_item.id,status:'checkpointed',next_action:'execute'})
repair_router.dispatch(error_class=current_failure.type, anchor=current_failure.raw_anchor)
assert max_actions <= 5 and max_repair_rounds <= 3 and grader_timeout_seconds == 120
```
"""


def _valid_compose_data(**overrides):
    data = {
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
            {
                "instruction": "Define trigger type for the loop",
                "command": "configure_trigger",
                "executable": True,
                "anchor": "§B",
                "provenance": "akb_interpretation",
            },
            {
                "instruction": "Implement work unit selector from the ledger",
                "command": "select_work_unit",
                "executable": True,
                "anchor": "§B",
                "provenance": "akb_interpretation",
            },
            {
                "instruction": "Run a quality gate before accepting output",
                "command": "run_quality_gate",
                "executable": True,
                "anchor": "§B",
                "provenance": "akb_interpretation",
            },
        ],
        "params": {},
        "referenced_concepts": ["loop-engineering", "harness-engineering"],
        "tldr": "Loop engineering turns repeated agent work into bounded, observable workflows.",
        "argumentation": [
            {
                "claim_anchor": "§A",
                "provenance": "source_grounded",
                "explanation": "Loop engineering is the practice of designing bounded, convergent, observable AI agent loops",
            },
            {
                "claim_anchor": "§B",
                "provenance": "source_grounded",
                "explanation": "A well-designed loop has five components: trigger, work unit selector, action executor, quality gate, ledger writer",
            },
            {
                "claim_anchor": "§C",
                "provenance": "source_grounded",
                "explanation": "Fix specific defects rather than restarting the entire pipeline — convergence over restart",
            },
        ],
        "applicability_boundary": "适用：多步骤 agent 管线；不适用：一次性脚本；约束：需要可观测输出。",
        "graph_edges": [],
    }
    data.update(copy.deepcopy(overrides))
    return data


class TestFrontmatterGateDecision:
    """gate_decision: null 写死（已知坑 1）。"""

    def test_gate_decision_is_null(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """frontmatter 中 gate_decision 必须为 null（防范 HC-09 拦截）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True
            assert result.frontmatter["gate_decision"] is None, (
                f"gate_decision 应为 null，实际: {result.frontmatter.get('gate_decision')}"
            )

    def test_gate_decision_not_active(self):
        """验证 frontmatter 构建逻辑不会产生 active 值。"""
        from src.akb2.quality.distill import _build_frontmatter

        data = {
            "claims": [
                {"text": "test", "anchor": "§A", "provenance": "source_grounded"}
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": ["a", "b"],
        }

        fm = _build_frontmatter("raw/test.md", data)
        assert fm["gate_decision"] is None, "gate_decision 必须写死 null"

    def test_frontmatter_quality_fields_are_not_unanchored_source_claims(self):
        """GPT-5.4 会审 frontmatter，key_findings/gate_reason 不得像裸来源断言。"""
        from src.akb2.quality.distill import _build_frontmatter

        data = {
            "claims": [
                {
                    "text": "Loop engineering turns repeated AI-agent work into bounded feedback loops.",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供效果数据",
                },
                {
                    "text": "The work queue carries lifecycle state and source references.",
                    "anchor": "§B",
                    "provenance": "source_grounded",
                    "evidence_gap": "未覆盖：原文未展开并发冲突处理",
                },
                {
                    "text": "The checker must not treat fallback grading as deployment ready.",
                    "anchor": "§E",
                    "provenance": "source_grounded",
                    "evidence_gap": "缺少：原文未给出完整门禁样例",
                },
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": ["loop-engineering", "quality-gate"],
        }

        fm = _build_frontmatter("raw/test.md", data)
        assert all(str(item).startswith("[raw §") for item in fm["key_findings"])
        assert all("(source_grounded)" in str(item) for item in fm["key_findings"])
        assert "..." not in " ".join(fm["key_findings"])
        assert "[raw §A]" in fm["gate_reason"]
        assert "GPT-5.4 裁判" in fm["gate_reason"]
        assert "段1抽取" not in fm["gate_reason"]
        assert all(str(q).startswith("[raw §") for q in fm["future_questions"])
        assert all("(akb_interpretation)" in str(q) for q in fm["future_questions"])


class TestFrontmatterSourceRefs:
    """source_refs 含 raw 路径（已知坑 2）。"""

    def test_source_refs_contains_raw_path(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """source_refs 必须含 raw 路径（HC-05 硬指标）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True
            source_refs = result.frontmatter["source_refs"]
            assert len(source_refs) >= 1, "source_refs 不能为空"
            assert sample_raw_path in source_refs, (
                f"source_refs 必须含 raw 路径 {sample_raw_path}，实际: {source_refs}"
            )

    def test_source_refs_not_empty(self):
        """source_refs 不为空列表。"""
        from src.akb2.quality.distill import _build_frontmatter

        data = {
            "claims": [
                {"text": "test", "anchor": "§A", "provenance": "source_grounded"}
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": [],
        }

        fm = _build_frontmatter("raw/test.md", data)
        assert isinstance(fm["source_refs"], list)
        assert len(fm["source_refs"]) >= 1


class TestFrontmatterGateReason:
    """gate_reason ≤150 字符（已知坑 3）。"""

    def test_gate_reason_within_limit(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """gate_reason 不超过 GATE_REASON_MAX_CHARS（150 字符）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True
            gate_reason = result.frontmatter["gate_reason"]
            assert len(gate_reason) <= GATE_REASON_MAX_CHARS, (
                f"gate_reason 长度 {len(gate_reason)} > {GATE_REASON_MAX_CHARS}"
            )

    def test_gate_reason_constant_is_named(self):
        """GATE_REASON_MAX_CHARS 是具名常量。"""
        assert isinstance(GATE_REASON_MAX_CHARS, int)
        assert GATE_REASON_MAX_CHARS == 150

    def test_gate_reason_truncation(self):
        """长 reason 被截断到 150 字符。"""
        from src.akb2.quality.distill import _build_frontmatter

        data = {
            "claims": [
                {"text": "A" * 200, "anchor": "§A", "provenance": "source_grounded"}
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": [],
        }

        fm = _build_frontmatter("raw/test.md", data)
        assert len(fm["gate_reason"]) <= GATE_REASON_MAX_CHARS


class TestFrontmatterFutureQuestions:
    """future_questions 2-3 条。"""

    def test_future_questions_count_in_range(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """future_questions 数量在 2-3 之间。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True
            questions = result.frontmatter["future_questions"]
            assert 2 <= len(questions) <= 3, (
                f"future_questions 应为 2-3 条，实际: {len(questions)}"
            )

    def test_future_questions_not_empty_strings(self):
        """future_questions 每条非空。"""
        from src.akb2.quality.distill import _generate_questions

        claims = [{"text": "test claim about loop engineering in production systems", "anchor": "§A"}]
        questions = _generate_questions(claims, ["step 1"])

        for q in questions:
            assert len(q) > 0, "future_question 不应为空"
            assert "?" in q or "？" in q, f"future_question 应是问句: {q}"

    def test_future_questions_no_extraction_meta_words(self):
        """必修2:Future Questions 禁止出现提取/模板元问题词。

        痛点:旧实现生成「how_steps N 步骤哪些刚性」「多个论点优先级排序」「该文缺少可执行步骤」
        ——全是关于提取流程的自指问题,grader 维度9 扣分合理。
        断言绑定可观测行为:任一问题含禁词 → 失败。
        """
        from src.akb2.quality.distill import _generate_questions

        forbidden = ["how_steps", "论点优先级", "模板", "提取规则", "字段", "刚性约束"]
        for claims, how_steps in [
            ([{"text": "Loop engineering designs bounded agent loops", "anchor": "§A"}], []),
            ([{"text": "agent loop requires five primitives", "anchor": "§A"}], ["s1", "s2"]),
            (
                [
                    {"text": "claim one about loops", "anchor": "§A"},
                    {"text": "claim two", "anchor": "§B"},
                    {"text": "claim three", "anchor": "§C"},
                ],
                [],
            ),
        ]:
            questions = _generate_questions(claims, how_steps)
            for q in questions:
                for w in forbidden:
                    assert w not in q, f"future_question 含禁词 {w!r}: {q}"

    def test_future_questions_are_domain_engineering(self):
        """必修2:Future Questions 面向领域工程问题(失败/边界/恢复/验证),非自指。

        断言绑定可观测行为:对 loop-engineering 主题,问题面向真实工程研究
        (失败模式/停止条件/并发恢复/可靠性度量)。
        """
        from src.akb2.quality.distill import _generate_questions

        claims = [
            {"text": "Loop engineering is designing bounded convergent agent loops", "anchor": "§A"},
        ]
        questions = _generate_questions(claims, ["step"])
        assert 2 <= len(questions) <= 3
        joined = " ".join(questions)
        # 面向工程:失败/边界/停止条件/并发/恢复/可靠性 之一
        domain_markers = ["失败", "边界", "停止条件", "预算", "并发", "恢复", "可恢复", "可靠性", "升级"]
        assert any(m in joined for m in domain_markers), (
            f"future_questions 应面向工程问题: {questions}"
        )


class TestFrontmatterRelatedConcepts:
    """related_concepts ≥2。"""

    def test_related_concepts_count_at_least_two(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """related_concepts 至少 2 条。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)
        extract_result.data["referenced_concepts"] = ["loop-engineering"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True
            concepts = result.frontmatter["related_concepts"]
            assert len(concepts) >= 2, (
                f"related_concepts 应 ≥2，实际: {len(concepts)}: {concepts}"
            )

    def test_related_concepts_fallback_from_claims(self):
        """只有 1 个 referenced_concepts 时从 claims 反推补充。"""
        from src.akb2.quality.distill import _build_frontmatter

        data = {
            "claims": [
                {
                    "text": "Loop engineering is about designing bounded agent workflows with convergence guarantees",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                }
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": ["loop-engineering"],  # 只有 1 个
        }

        fm = _build_frontmatter("raw/test.md", data)
        assert len(fm["related_concepts"]) >= 2, (
            f"应从 claims 反推补充，实际: {fm['related_concepts']}"
        )


class TestFrontmatterStatus:
    """status: candidate。"""

    def test_status_is_candidate(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """status 必须为 candidate。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True
            assert result.frontmatter["status"] == "candidate"


class TestCandidatePageWritten:
    """候选页文件写入验证。"""

    def test_candidate_page_written_to_staging(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """候选页写入 staging/candidate/<batch>/ 目录。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True
            assert "candidate" in result.candidate_path
            assert "2026-06-13-001" in result.candidate_path
            assert os.path.isfile(result.candidate_path), f"候选页文件应存在: {result.candidate_path}"

            with open(result.candidate_path, encoding="utf-8") as fh:
                content = fh.read()

            assert "---" in content, "候选页应有 frontmatter"
            assert "candidate" in content
            assert "gate_decision: null" in content

    def test_extract_failure_no_candidate(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """段 1 失败时 compose_candidate 返回 failure。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        bad_extract = ExtractResult(
            success=False,
            retries_used=3,
            error="抽取失败",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=bad_extract,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is False
            assert "段 1 抽取未成功" in result.error

    def test_explicit_candidate_batch_dir_is_not_nested(
        self, sample_raw_path, extract_success_response
    ):
        """显式传 staging/candidate/<batch> 时不再重复拼 candidate/<batch>。"""
        from tests.quality.conftest import make_mock_route_llm

        batch_id = "cql-s3-canary"
        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            exact_dir = Path(tmp_dir) / "staging" / "candidate" / batch_id
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id=batch_id,
                output_dir=str(exact_dir),
                route_llm_fn=make_mock_route_llm(extract_success_response),
            )

        assert result.success is True
        assert f"candidate/{batch_id}/candidate/{batch_id}" not in result.candidate_path
        assert Path(result.candidate_path).parent.name == batch_id


class TestFrontmatterActiveRequiredFields:
    """active 必填字段补齐（2026-06-18 F4 schema 对账）——否则升格被 lint P1 硬门拦。"""

    def _data(self):
        return {
            "claims": [
                {"text": "Loop engineering bounds agent workflows", "anchor": "§A", "provenance": "source_grounded"},
                {"text": "Maker and checker must be different model families", "anchor": "§B", "provenance": "source_grounded"},
                {"text": "Every loop needs an explicit named budget", "anchor": "§C", "provenance": "source_grounded"},
            ],
            "how_steps": ["step 1"],
            "params": {},
            "referenced_concepts": ["loop-engineering", "harness-engineering"],
        }

    def test_constant_fields_present(self):
        """template_type/graph_role/evidence_provenance/graph_status/star_level 写死正确值。"""
        from src.akb2.quality.distill import _build_frontmatter

        fm = _build_frontmatter("raw/test.md", self._data())
        assert fm["template_type"] == "source"
        assert fm["graph_role"] == "evidence"
        assert fm["graph_status"] == "healthy"
        assert fm["evidence_provenance"] == "partial_source_grounded"
        assert fm["star_level"] == 0

    def test_key_findings_from_claims(self):
        """key_findings 为非空数组，来自 claims（真实内容 ≥3 满足 H7）。"""
        from src.akb2.quality.distill import _build_frontmatter

        fm = _build_frontmatter("raw/test.md", self._data())
        assert isinstance(fm["key_findings"], list)
        assert len(fm["key_findings"]) >= 3, f"3 条 claims 应产 ≥3 key_findings: {fm['key_findings']}"

    def test_assemble_page_frontmatter_is_valid_yaml_with_key_findings_punctuation(self):
        """_assemble_page 必须输出 PyYAML 可解析的 frontmatter，不因 key_findings 标点炸。"""
        from src.akb2.quality.distill import _assemble_page

        frontmatter = {
            "status": "candidate",
            "gate_decision": None,
            "source_refs": ["raw/test.md"],
            "gate_reason": "[raw §A] GPT-5.4 裁判待评审",
            "future_questions": [
                "[raw §A] (akb_interpretation) 如何验证: A/B?",
                "[raw §B] (akb_interpretation) 如何处理 [edge]?",
            ],
            "related_concepts": ["[[loop-engineering]]", "[[quality-gate]]"],
            "key_findings": [
                "[raw §A] (source_grounded) Loop shape: trigger/selector/gate",
                "[raw §B] (source_grounded) Evidence [bracket] must be quoted",
            ],
        }
        page = _assemble_page(frontmatter, "## TLDR\nbody [raw §A]")
        fm_raw = page.split("---\n", 2)[1]
        parsed = yaml.safe_load(fm_raw)

        assert parsed["key_findings"] == frontmatter["key_findings"]
        assert parsed["future_questions"] == frontmatter["future_questions"]
        assert parsed["gate_decision"] is None

    def test_related_concepts_wikilink_format(self):
        """related_concepts 统一 [[wikilink]] 格式（GRAPH-LINKING §3.8.1）。"""
        from src.akb2.quality.distill import _build_frontmatter

        fm = _build_frontmatter("raw/test.md", self._data())
        for c in fm["related_concepts"]:
            assert c.startswith("[[") and c.endswith("]]"), f"非 wikilink 格式: {c}"

    def test_source_metadata_from_raw_frontmatter(self):
        """source_url/author/published_at 从 raw frontmatter 取（不是空）。"""
        import tempfile

        from src.akb2.quality.distill import _build_frontmatter

        raw = """---
artifact_type: raw
source_slug: addy-osmani-loop-engineering
source_url: https://example.com/loop-engineering
source_title: Loop Engineering
source_authors: ["Addy Osmani"]
source_published_at: 2026-06-07
---

# §A body
"""
        with tempfile.NamedTemporaryFile("w", suffix="--raw--2026-06-11.md", delete=False, encoding="utf-8") as fh:
            fh.write(raw)
            path = fh.name

        fm = _build_frontmatter(path, self._data())
        assert fm["source_url"] == "https://example.com/loop-engineering"
        assert fm["source_author"] == "Addy Osmani"  # list → 首作者
        assert fm["source_published_at"] == "2026-06-07"
        assert "Loop Engineering" in fm["canonical_title"]
        assert fm["source_type"] == "blog"  # raw 无 source_type → 默认 blog

    def test_missing_raw_file_graceful(self):
        """raw 文件不存在时不崩，来源字段降级为空/默认。"""
        from src.akb2.quality.distill import _build_frontmatter

        fm = _build_frontmatter("raw/does-not-exist.md", self._data())
        assert fm["source_url"] == ""
        assert fm["source_type"] == "blog"
        assert fm["template_type"] == "source"  # 常量字段不受影响


class TestCandidatePageBodyStructure:
    """候选页正文 10 节结构验证。"""

    def test_body_has_ten_sections(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """候选页正文含 10 节模板。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = _valid_extract_result(extract_success_response)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=extract_result,
                raw_path=sample_raw_path,
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )

            assert result.success is True

            with open(result.candidate_path, encoding="utf-8") as fh:
                content = fh.read()

            expected_sections = [
                "TLDR",
                "关键发现",
                "入选理由",
                "证据地图",
                "核心论点",
                "论证与解释",
                "适用边界",
                "图谱关系",
                "Future Questions",
                "维护记录",
            ]

            for section in expected_sections:
                assert f"## {section}" in content, f"候选页缺少小节: {section}"


class TestBodyContractAlignment:
    """SC-1/SC-2:成稿 body 对齐量规/检查器契约（根因②——模板不再糟蹋合格提炼数据）。

    断言绑定可观测行为:图谱表英文契约列名、入选理由含锚点、论证逐条带 provenance、
    how 渲染 command、适用边界含「不适用」、A4 真实成稿零缺陷。
    """

    def _compose(self, extract_success_response):
        """用 fixture extract 数据真成稿，返回候选页正文。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)
        ex = ExtractResult(success=True, data=extract_success_response["deepseek-v4-pro"].content, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                extract_result=ex,
                raw_path="raw/addy-osmani-loop-engineering--raw--2026-06-11.md",
                batch_id="2026-06-13-001",
                output_dir=tmp_dir,
                route_llm_fn=mock_fn,
            )
            assert result.success is True
            with open(result.candidate_path, encoding="utf-8") as fh:
                return fh.read()

    def test_graph_table_english_contract_header(self, extract_success_response):
        """SC-1:图谱表头用英文契约列名 target/relation/strength/provenance/evidence。

        痛点:旧中文表头 | 概念 | 关系类型 | 强度 | 证据 | 与检查器期待的英文列名不符，
        A4 检查 graph_related 判否跳过（图谱边从未被真正校验）+ 评分器认不出→每条边误判。
        """
        body = self._compose(extract_success_response)
        assert "| target | relation | strength | provenance | evidence |" in body
        # 旧中文表头必须不再出现
        assert "| 概念 | 关系类型" not in body

    def test_graph_evidence_not_truncated(self):
        """SC-1:图谱 evidence 不再 [:80] 截断（旧截断丢依据，>80 字符的 evidence 被砍到 80）。

        断言可观测行为:构造 100+ 字符 evidence，成稿后完整出现（无 [:80] 截断）。
        """
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        long_evidence = "A" * 100  # 远超旧 80 截断阈值
        data = _valid_compose_data(
            graph_edges=[
                {"concept": "harness-engineering", "relation_type": "part_of", "strength": "strong",
                 "evidence": long_evidence},
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = make_mock_route_llm({})
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(ex, "raw/test.md", "b", output_dir=tmp_dir, route_llm_fn=mock_fn)
            body = open(result.candidate_path, encoding="utf-8").read()
        assert long_evidence in body, "evidence 应完整不被 [:80] 截断"

    def test_graph_fallback_edge_is_weak(self):
        """SC-1:无 graph_edges 时 fallback 边强度降 weak（旧 medium 偏高，与无依据矛盾）。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(graph_edges=[])  # 无边 → 走 concepts fallback
        ex = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = make_mock_route_llm({})
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(ex, "raw/test.md", "b", output_dir=tmp_dir, route_llm_fn=mock_fn)
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "| related | weak |" in body, "fallback 边应为 weak"
        assert "| related | medium |" not in body

    def test_selection_reason_anchors_claims(self, extract_success_response):
        """SC-1:入选理由引用 top claims 的 [raw §X] 锚点（旧入选理由写死无锚点）。"""
        body = self._compose(extract_success_response)
        # 入选理由节内应出现 [raw §A] 之类锚点
        assert "[raw §A]" in body, "入选理由应引用 top claims 的 [raw §X] 锚点"
        selection = body.split("## 入选理由", 1)[1].split("## 证据地图", 1)[0]
        assert "(akb_interpretation) 非来源事实" not in selection
        assert "最终是否入库以后续 GPT-5.4 裁判与五闸为准" not in selection
        assert selection.count("(source_grounded) 来源事实") >= 2
        assert "(akb_interpretation) 入选理由" not in selection

    def test_argumentation_each_has_provenance(self, extract_success_response):
        """SC-1:论证逐条标注 provenance（旧论证无 provenance，锚定/解读分离缺陷真凶）。

        断言可观测行为:每条 argumentation 渲染为 **[raw §X]** (provenance) explanation。
        """
        body = self._compose(extract_success_response)
        # fixture argumentation[0] claim_anchor=§A，对应 claim provenance=source_grounded
        assert "**[raw §A]** (source_grounded)" in body, (
            "论证每条应为 **[raw §X]** (provenance) explanation 形态"
        )

    def test_argumentation_does_not_collapse_multiple_claims_on_same_anchor(self):
        """同一 raw anchor 的多个核心论点必须逐条解释，不能被 anchor 字典覆盖成重复项。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            claims=[
                {
                    "text": "A loop needs five primitives plus external memory",
                    "anchor": "§B",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未测量各 primitive 的相对贡献",
                },
                {
                    "text": "Codex and Claude Code expose the primitives with similar capabilities",
                    "anchor": "§B",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未提供跨工具性能数据",
                },
            ],
            how_steps=[
                {
                    "instruction": "Configure five primitives plus external memory",
                    "command": "configure_primitives",
                    "executable": True,
                    "anchor": "§B",
                },
                {
                    "instruction": "Compare similar capabilities against the five primitives",
                    "command": "compare_capabilities",
                    "executable": True,
                    "anchor": "§B",
                },
                {
                    "instruction": "Record coverage for five primitives plus external memory",
                    "command": "record_primitive_coverage",
                    "executable": True,
                    "anchor": "§B",
                },
            ],
            referenced_concepts=["loop-engineering", "agent-workflow"],
            tldr="Loop primitives and tool parity are both relevant source claims.",
            argumentation=[
                {
                    "claim_anchor": "§B",
                    "provenance": "source_grounded",
                    "explanation": "A loop needs five primitives plus external memory",
                },
                {
                    "claim_anchor": "§B",
                    "provenance": "source_grounded",
                    "explanation": "Codex and Claude Code expose the primitives with similar capabilities",
                },
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/addy-osmani-loop-engineering--raw--2026-06-11.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()

        argumentation = body.split("## 论证与解释", 1)[1].split("### 操作步骤", 1)[0]
        assert "five primitives plus external memory" in argumentation
        assert "similar capabilities" in argumentation
        assert argumentation.count("five primitives plus external memory") == 1
        assert argumentation.count("similar capabilities") == 1

    def test_argumentation_uses_argumentation_provenance(self):
        """解释性论证必须显示自己的 akb_interpretation，而不是继承 claim provenance。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            argumentation=[
                {
                    "claim_anchor": "§A",
                    "provenance": "akb_interpretation",
                    "explanation": "这说明未来质量循环可能需要更强的异常升级策略。",
                },
                {
                    "claim_anchor": "§B",
                    "provenance": "source_grounded",
                    "explanation": "A well-designed loop has five components: trigger, work unit selector, action executor, quality gate, ledger writer",
                },
                {
                    "claim_anchor": "§C",
                    "provenance": "source_grounded",
                    "explanation": "Fix specific defects rather than restarting the entire pipeline — convergence over restart",
                },
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/test.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "**[raw §A]** (akb_interpretation)" in body

    def test_requires_strong_without_hard_evidence_is_downgraded(self):
        """成稿层兜底: requires:strong 证据不足时降为 medium。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            referenced_concepts=["loop-engineering", "agent-collaboration"],
            graph_edges=[
                {
                    "concept": "agent-collaboration",
                    "relation_type": "requires",
                    "strength": "strong",
                    "evidence": "multi-agent loops require coordination primitives",
                }
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/test.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "| agent-collaboration | requires | weak |" in body
        assert "| agent-collaboration | requires | strong |" not in body
        assert "(akb_interpretation)" in body

    def test_direct_graph_edge_with_raw_evidence_preserves_medium(self):
        """medium 边不因点名 target 自动升 strong，避免生成流程性强度痕迹。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            referenced_concepts=["MCP", "connectors"],
            graph_edges=[
                {
                    "concept": "MCP",
                    "relation_type": "related",
                    "strength": "medium",
                    "evidence": "[raw §F] connectors built on MCP let the agent access real tools.",
                }
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/test.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "| MCP | related | medium |" in body
        assert "候选阶段升为 strong" not in body
        assert "[[raw §F]]" not in body

    def test_related_edge_without_target_mention_downgrades_to_weak(self):
        """related 边 evidence 未直接点名 target 时按推测关联降为 weak。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            referenced_concepts=["factory model", "loop engineering"],
            graph_edges=[
                {
                    "concept": "factory model",
                    "relation_type": "related",
                    "strength": "medium",
                    "evidence": "[raw §A] the source frames loop engineering as a system for building software rather than a single prompt.",
                }
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/test.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "| factory model | related | weak |" in body
        assert "| factory model | related | medium |" not in body

    def test_extends_edge_is_not_auto_upgraded_to_strong(self):
        """extends 关系需要更直接的关系引文，不能只因点名 target 就自动升 strong。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            referenced_concepts=["agent harness engineering", "loop engineering"],
            graph_edges=[
                {
                    "concept": "agent harness engineering",
                    "relation_type": "extends",
                    "strength": "medium",
                    "evidence": "[raw §A] loop engineering sits above direct prompt/harness work and designs the surrounding system.",
                }
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/test.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "| agent harness engineering | extends | medium |" in body
        assert "| agent harness engineering | extends | strong |" not in body

    def test_how_steps_render_command_backticks(self):
        """SC-1:操作步骤有 command 时用反引号渲染（旧 how_steps 是纯字符串无标记）。

        断言可观测行为:对象形态 how_steps 的 command 以 markdown code 包裹。
        """
        from src.akb2.quality.distill import _render_how_step, _render_how_step_with_provenance

        rendered = _render_how_step(1, {"instruction": "run pipeline", "command": "make pipeline", "executable": True})
        assert "`make pipeline`" in rendered, "command 应反引号渲染"
        # 旧字符串形态向后兼容（Codex R2 剩余风险2:旧 staging 字符串候选）
        assert _render_how_step(1, "legacy string step") == "1.  (akb_interpretation) legacy string step"
        # 对象无 command 时不渲染反引号
        rendered_nc = _render_how_step(2, {"instruction": "review output", "command": "", "executable": False})
        assert "`" not in rendered_nc
        rendered_with_anchor = _render_how_step_with_provenance(
            1,
            {"instruction": "run pipeline", "command": "make pipeline", "executable": True},
            "§A",
        )
        assert "[raw §A] (source_grounded)" in rendered_with_anchor

    def test_applicability_boundary_contains_not_applicable(self):
        """SC-1:适用边界兜底含「不适用」（旧兜底「（适用边界待补充）」无场景结构）。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(applicability_boundary="")  # 空 → 走兜底
        ex = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = make_mock_route_llm({})
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(ex, "raw/test.md", "b", output_dir=tmp_dir, route_llm_fn=mock_fn)
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "不适用" in body, "适用边界兜底应含「不适用」场景"
        boundary = body.split("## 适用边界", 1)[1].split("## 图谱关系", 1)[0]
        assert boundary.count("(akb_interpretation) 适用场景：") >= 2
        assert boundary.count("(source_grounded) 来源事实：") == 0
        assert body.count("(akb_interpretation) 不适用场景：") >= 1
        assert body.count("(akb_interpretation) 约束条件：") >= 1
        assert "约束条件" in body
        assert "### 来源明示边界" not in boundary
        assert "来源事实：" not in boundary

    def test_graph_evidence_gets_provenance_and_local_contract_passes(self):
        """回归真实 smoke: 图谱 evidence 必须补 provenance，并通过本地 A7/A8 契约。"""
        from src.akb2.quality.checks_structural import (
            check_a4_graph_edges,
            check_a8_applicability_boundary,
        )
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            referenced_concepts=["loop-engineering", "agent-collaboration"],
            graph_edges=[
                {
                    "concept": "agent-collaboration",
                    "relation_type": "related",
                    "strength": "medium",
                    "evidence": "multi-agent review benefits from the same ledger and gate pattern",
                }
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/test.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "| agent-collaboration | related | weak |" in body
        assert "(akb_interpretation)" in body
        assert check_a4_graph_edges(body) == []
        assert check_a8_applicability_boundary(body) == []

    def test_novelty_increment_section_is_emitted(self, extract_success_response):
        """强 checker 要求新颖增量可核验，成稿必须显式产出新颖增量小节。"""
        body = self._compose(extract_success_response)
        assert "## 新颖增量" in body
        assert "| 既有条目/基线 | 已覆盖点 | 本文新增内容 | provenance | 新增类型 | 证据 |" in body
        assert "source claim" in body
        assert "| source_grounded | source operation |" in body or "| source_grounded | source claim |" in body
        assert "证据盲区列限定为本候选页的审查边界" in body
        assert "duplicate guard" not in body
        assert "同源 active 页若已存在" not in body
        assert "明确新增" not in body
        assert "AKB 操作化待验证项" in body
        assert "[raw §A] (akb_interpretation) 新颖性对照口径" in body
        assert "未读取到的部署 vault 覆盖情况一律标为待人工复核" in body
        assert "[raw §A] (akb_interpretation) Wiki 对照范围" not in body

    def test_novelty_increment_reads_isolated_neighbor_baseline(self):
        """部署 smoke 回归: 隔离 vault 有相邻 wiki 时，新颖表必须列出真实 baseline。"""
        from src.akb2.quality.distill import (
            build_deterministic_extract_result,
            compose_candidate,
        )

        raw_content = """---
artifact_type: raw
source_slug: smoke-loop-engineering
source_url: https://example.com/smoke-loop-engineering
source_title: Smoke Loop Engineering
---

# §A Loop Engineering Definition
Loop engineering is the practice of designing AI agent loops that are bounded, convergent, and observable.

# §B How: A Complete Loop Shape
Every loop must write to the ledger before executing the next action so crash recovery can resume from the last durable event.

# §C Convergence Over Restart
When a step produces defects, repair the failed section rather than restarting the whole pipeline.
""" + _COMMAND_EXAMPLES_BLOCK
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "AKB2.0"
            raw_path = root / "raw" / "smoke-loop-engineering--raw--2026-06-22.md"
            neighbor = root / "wiki" / "sources" / "loop-engineering-overview.md"
            raw_path.parent.mkdir(parents=True)
            neighbor.parent.mkdir(parents=True)
            raw_path.write_text(raw_content, encoding="utf-8")
            neighbor.write_text(
                """---
title: Loop Engineering Overview
source_url: https://example.com/neighbor
---

## TLDR

(source_grounded) Existing page covers bounded loops but does not cover ledger-before-action recovery.
""",
                encoding="utf-8",
            )
            extract_result = build_deterministic_extract_result(raw_content, str(raw_path))
            result = compose_candidate(
                extract_result,
                str(raw_path),
                "smoke-novelty",
                output_dir=str(root / "staging" / "candidate"),
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")

        novelty = body.split("## 新颖增量", 1)[1].split("## 适用边界", 1)[0]
        assert "wiki/sources/loop-engineering-overview.md" in novelty
        assert "ledger-before-action recovery" in novelty
        assert "隔离 vault 未发现相邻 wiki 条目" not in novelty
        assert "Every loop must write to the ledger" in novelty
        assert "[raw §B] (source_grounded)" in novelty

    def test_how_steps_include_params_example_and_nonuniform_anchors(self, extract_success_response):
        """How 段必须含可复制配置，且步骤锚点按内容贴近 §B/§D/§E。"""
        body = self._compose(extract_success_response)
        assert "以下步骤按 provenance 分层" in body
        assert "source_grounded 表示 raw 明确给出的协议/API/配置操作" in body
        assert "source basis、akb action 与 unsupported assumptions" in body
        assert "配置示例：" in body
        assert "max_repair_rounds: 3" in body
        assert "distill_timeout_seconds: 60" in body
        assert "grader_timeout_seconds: 120" in body
        assert "fallback_allowed: false" in body
        assert "[raw §B] (akb_interpretation)" in body
        assert "### 操作化边界" in body
        assert "| 步骤 | 来源支撑 | AKB 操作化 | 未直接支持/仍需验证 |" in body

    def test_conceptual_how_commands_become_copyable_examples(self):
        """真实 smoke 回归：DeepSeek 标签式 How 命令不得原样进入候选页。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "Every loop must have explicit budgets for max repair rounds, timeout per model call, max retries, and maximum parallel workers.",
                    "anchor": "§D",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较不同预算策略",
                },
                {
                    "text": "The model that produces content must be different from the model that judges it.",
                    "anchor": "§E",
                    "provenance": "source_grounded",
                    "evidence_gap": "未验证：原文未提供模型组合实验",
                },
                {
                    "text": "A loop is deployable only when failure is observable.",
                    "anchor": "§I",
                    "provenance": "source_grounded",
                    "evidence_gap": "未覆盖：原文未覆盖所有失败模式",
                },
            ],
            "how_steps": [
                {
                    "instruction": "When calling an LLM, record model family, prompt version, timeout, retry budget, fallback usage, and the raw anchor that justified the action.",
                    "command": "Record LLM call metadata",
                    "executable": True,
                },
                {
                    "instruction": "Define explicit budgets for max repair rounds, timeout per model call, max retries, and maximum parallel workers as named constants.",
                    "command": "Define budget constants",
                    "executable": True,
                },
                {
                    "instruction": "Use a maker model to draft claims and a separate checker model to independently review raw anchors, evidence gaps, and fallback status before promotion.",
                    "command": "Separate maker and checker models",
                    "executable": True,
                },
            ],
            "params": {"max_repair_rounds": "explicit budget constant"},
            "referenced_concepts": ["loop-engineering", "quality-gate"],
            "tldr": "A clear synthesis with enough length to pass check.",
            "argumentation": [],
            "applicability_boundary": "适用：有状态评估；不适用：一次性摘要；约束：需要账本。",
            "graph_edges": [],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "smoke-loop-engineering--raw--2026-06-22.md"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(
                # P1a [B4]:行首 [raw §X] 声明只在锚点目录块内识别,故在此声明 §D/§E/§I;
                # 下方 [raw §X] 段落行是引用(不算声明),但因目录块已声明故 A3 过。
                "## 锚点目录\n\n[raw §D]\n[raw §E]\n[raw §I]\n\n---\n\n"
                "[raw §D] Every loop must have explicit budgets for max repair rounds, timeout per model call, max retries, and maximum parallel workers.\n\n"
                "[raw §E] The model that produces content must be different from the model that judges it.\n\n"
                "[raw §I] A loop is deployable only when failure is observable.\n"
                + _COMMAND_EXAMPLES_BLOCK,
                encoding="utf-8",
            )
            result = compose_candidate(
                ExtractResult(success=True, data=data, retries_used=1),
                str(raw_path),
                "concept-how",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")

        assert "`Record LLM call metadata`" not in body
        assert "`Define budget constants`" not in body
        assert "`Separate maker and checker models`" not in body
        assert "ledger.append({event:'llm_call'" in body
        assert "MAX_REPAIR_ROUNDS=3" in body
        assert "maker_model='deepseek-v4-pro'; checker_model='gpt-5.4'" in body
        assert "max_repair_rounds: explicit budget constant" not in body
        assert "max_retries: 0" in body
        assert "maker_model: deepseek-v4-pro" in body
        assert "checker_model: gpt-5.4" in body

    def test_how_step_source_grounded_is_preserved_only_for_explicit_concrete_command(self):
        """来源提供的具体命令可标 source_grounded，AKB helper 命令仍标 akb_interpretation。"""
        from src.akb2.quality.distill import _render_how_step_with_provenance

        concrete = _render_how_step_with_provenance(
            1,
            {
                "instruction": "Run the documented command",
                "command": "python -m akb2.quality.runner --once",
                "executable": True,
                "provenance": "source_grounded",
            },
            "§A",
        )
        generated = _render_how_step_with_provenance(
            2,
            {
                "instruction": "Write a ledger event",
                "command": "ledger.append({event:'x'})",
                "executable": True,
                "provenance": "source_grounded",
            },
            "§B",
        )
        assert "[raw §A] (source_grounded)" in concrete
        assert "不是原文逐字指令" not in concrete
        assert "[raw §B] (akb_interpretation)" in generated
        assert "不是原文逐字指令" in generated

    def test_wrong_explicit_how_anchor_is_reselected_by_support(self):
        """真实 smoke 回归: DeepSeek 错把 failure signature 步骤标 §B 时必须改回 §J。"""
        from src.akb2.quality.distill import _select_step_anchor

        claims = [
            {
                "text": "A complete loop has a trigger, selector, executor, gate, and ledger writer",
                "anchor": "§B",
            },
            {
                "text": "Every failure must leave a signature with loop id, node, error class, timeout, retries, and model family",
                "anchor": "§J",
            },
        ]
        step = {
            "instruction": "Write a failure signature",
            "command": "record_failure(loop='loop1',node=node,error_class=error_class)",
            "anchor": "§B",
        }
        assert _select_step_anchor(step, claims, 1, {}) == "§J"

    def test_unsupported_how_steps_are_not_forced_onto_neighbor_claims(self):
        """找不到 raw/claim 支撑的操作步骤不得按序号硬挂到相邻锚点。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "Evaluation harness writes every tool call into an append-only ledger for replay",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供重放成功率",
                },
                {
                    "text": "Failures are classified into schema_mismatch, missing_evidence, and tool_timeout",
                    "anchor": "§C",
                    "provenance": "source_grounded",
                    "evidence_gap": "未验证：原文未提供分类器准确率",
                },
                {
                    "text": "Loops need explicit budgets",
                    "anchor": "§D",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较预算策略",
                }
            ],
            "how_steps": [
                {
                    "instruction": "Measure actual usage data for value verdict instead of predicting",
                    "command": "value = measure_usage_metrics(data)",
                    "executable": True,
                }
            ],
            "params": {},
            "referenced_concepts": ["loop-engineering", "quality-gate"],
            "tldr": "A clear synthesis with enough length to pass check.",
            "argumentation": [
                {
                    "claim_anchor": "§A",
                    "provenance": "source_grounded",
                    "explanation": "Evaluation harness writes every tool call into an append-only ledger for replay",
                },
                {
                    "claim_anchor": "§C",
                    "provenance": "source_grounded",
                    "explanation": "Failures are classified into schema_mismatch, missing_evidence, and tool_timeout",
                },
                {
                    "claim_anchor": "§D",
                    "provenance": "source_grounded",
                    "explanation": "Loops need explicit budgets",
                },
            ],
            "applicability_boundary": "适用：多步骤管线；不适用：一次性任务；约束：需要账本。",
            "graph_edges": [],
        }
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "test.md"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(
                "# §A Harness\nAppend-only ledger supports replay.\n\n"
                "# §C Failures\nschema_mismatch, missing_evidence, tool_timeout.\n\n"
                "# §D Budget Constraints\nEvery loop must have an explicit budget.\n"
                + _COMMAND_EXAMPLES_BLOCK,
                encoding="utf-8",
            )
            result = compose_candidate(
                ex,
                str(raw_path),
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")
        assert "Measure actual usage data" not in body
        assert "value = measure_usage_metrics" not in body
        assert "ledger.append({run_id:run_id,item_id:work_item.id,status:'checkpointed',next_action:'execute'})" in body
        assert "repair_router.dispatch" in body
        assert "在循环入口断言命名预算和停止条件" in body
        assert "[raw §D] (akb_interpretation)" in body

    def test_sparse_how_steps_are_supplemented_from_claims(self):
        """真实 smoke 回归：模型只抽 1 条 How 时，compose 要补足至少 3 个可执行步骤。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "Evaluation harness writes every tool call and response into an append-only ledger for replay",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供重放成功率",
                },
                {
                    "text": "Failures are classified into schema_mismatch, missing_evidence, tool_timeout, model_fallback, and reviewer_disagreement",
                    "anchor": "§C",
                    "provenance": "source_grounded",
                    "evidence_gap": "未验证：原文未提供分类器准确率",
                },
                {
                    "text": "Evaluation loops stop on max_actions, max_repair_rounds, timeout, or grader model_fallback",
                    "anchor": "§D",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较不同预算设置",
                },
            ],
            "how_steps": [
                {
                    "instruction": "Load the previous ledger checkpoint",
                    "command": "load the previous ledger checkpoint",
                    "executable": True,
                }
            ],
            "params": {},
            "referenced_concepts": ["evaluation-harness", "quality-gate"],
            "tldr": "A clear synthesis with enough length to pass check.",
            "argumentation": [],
            "applicability_boundary": "适用：有状态评估；不适用：一次性摘要；约束：需要账本。",
            "graph_edges": [],
        }
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "test.md"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(
                "# §A Harness\nAppend-only ledger supports replay.\n\n"
                "# §B How\nDefine fixture, load checkpoint, execute bounded action, run assertions, append result.\n\n"
                "# §C Failures\nschema_mismatch, missing_evidence, tool_timeout, model_fallback.\n\n"
                "# §D Budgets\nmax_actions=5, max_repair_rounds=3, grader_timeout_seconds=120.\n"
                + _COMMAND_EXAMPLES_BLOCK,
                encoding="utf-8",
            )
            result = compose_candidate(
                ex,
                str(raw_path),
                "sparse-how",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")

        how_section = body.split("### 操作步骤", 1)[1].split("## 新颖增量", 1)[0]
        assert how_section.count("`") >= 6
        assert "ledger.append({run_id:run_id,item_id:work_item.id,status:'checkpointed',next_action:'execute'})" in how_section
        assert "repair_router.dispatch" in how_section
        assert "assert max_actions <= 5" in how_section
        assert how_section.count("| ") >= 8

    def test_how_steps_only_use_claim_anchors_even_when_raw_section_exists(self):
        """raw 额外段落未进入 claims 时，不得在 How 中引用该锚点。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "Maker and checker must use different model families",
                    "anchor": "§E",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供模型组合实验",
                }
            ],
            "how_steps": [
                {
                    "instruction": "Anchor quality standards to concrete golden samples",
                    "command": "golden_sample.check(content)",
                    "executable": True,
                },
                {
                    "instruction": "Use different model families for maker and checker",
                    "command": "set_checker_model('gpt-5.4')",
                    "executable": True,
                },
                {
                    "instruction": "Assert maker and checker use different model families",
                    "command": "assert maker_model != checker_model",
                    "executable": True,
                },
                {
                    "instruction": "Record maker checker model family separation",
                    "command": "record_model_family_separation",
                    "executable": True,
                },
            ],
            "params": {},
            "referenced_concepts": ["maker-checker", "quality-gate"],
            "tldr": "A clear synthesis with enough length to pass check.",
            "argumentation": [],
            "applicability_boundary": "适用：多模型质量门；不适用：无审查任务；约束：需要模型分离。",
            "graph_edges": [],
        }
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "test.md"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(
                "# §E Maker/Checker Separation\nUse different model families.\n\n"
                "# §F Golden Sample Anchoring\nQuality standards must be anchored to concrete golden samples.\n"
                + _COMMAND_EXAMPLES_BLOCK,
                encoding="utf-8",
            )
            result = compose_candidate(
                ex,
                str(raw_path),
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")
        assert "golden_sample.check" not in body
        assert "[raw §F]" not in body
        assert "set_checker_model('gpt-5.4')" in body
        assert "[raw §E] (akb_interpretation)" in body

    def test_tldr_has_sentence_boundaries_per_anchor(self, extract_success_response):
        """TLDR 每条核心论点独立成句，避免 GPT-5.4 判定锚点映射不清。"""
        body = self._compose(extract_success_response)
        tldr = body.split("## TLDR", 1)[1].split("## 关键发现", 1)[0]
        assert tldr.count("(source_grounded)") == 3
        assert "[raw §A]." in tldr
        assert "[raw §B]." in tldr
        assert "[raw §C]." in tldr

    def test_smoke_style_compose_keeps_provenance_ratio_under_self_gate(self):
        """真实 smoke 回归：Future Questions 可标 provenance，但全文比例仍需过 self-gate。"""
        from src.akb2.quality.distill import (
            build_deterministic_extract_result,
            compose_candidate,
        )

        raw_content = """---
artifact_type: raw
source_slug: smoke-loop-engineering
source_url: https://example.com/smoke-loop-engineering
source_title: Smoke Loop Engineering
---

# §A Loop Engineering Definition
Loop engineering is the practice of designing AI agent loops that are bounded, convergent, and observable.

# §B How: A Complete Loop Shape
A loop has a trigger, selector, executor, quality gate, and ledger writer.

# §C Convergence Over Restart
When a step produces defects, repair the failed section rather than restarting the whole pipeline.

# §D Budget Constraints
Every loop must have explicit budgets for max repair rounds, timeout per model call, max retries, and maximum parallel workers.

# §E Maker Checker Separation
The model that produces content must be different from the model that judges it.
""" + _COMMAND_EXAMPLES_BLOCK
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "smoke-loop-engineering--raw--2026-06-22.md"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(raw_content, encoding="utf-8")
            extract_result = build_deterministic_extract_result(raw_content, str(raw_path))
            result = compose_candidate(
                extract_result,
                str(raw_path),
                "smoke-ratio",
                output_dir=tmp_dir,
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")

        assert result.success is True
        future = body.split("## Future Questions", 1)[1].split("## 维护记录", 1)[0]
        assert future.count("(akb_interpretation)") == 3
        how = body.split("### 操作步骤", 1)[1].split("### 操作化边界", 1)[0]
        assert "source_basis=[raw" in how
        assert "unsupported_assumptions=" in how
        assert run_candidate_self_gate(body, raw_content) == []

    def test_graph_evidence_normalizes_raw_anchor_mentions(self):
        """图谱 evidence 中的 原文 §X 必须统一成 [raw §X]。"""
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            graph_edges=[
                {
                    "concept": "harness",
                    "relation_type": "related",
                    "strength": "strong",
                    "evidence": "Loop sits above harness (原文 §A)",
                },
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex,
                "raw/test.md",
                "b",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        assert "[raw §A]" in body
        assert "原文 §A" not in body

    def test_a4_passes_on_composed_body(self, extract_success_response):
        """SC-2:真实 extract 成稿 → A4 图谱检查零缺陷（根因②修复兑现）。

        断言绑定可观测行为:用真实 extract_success_response 成稿后，
        checks_structural.check_a4_graph_edges 返回空缺陷列表（图谱边四列齐 + 非空）。
        """
        from src.akb2.quality.checks_structural import check_a4_graph_edges

        body = self._compose(extract_success_response)
        defects = check_a4_graph_edges(body)
        assert defects == [], f"成稿后 A4 图谱应零缺陷，实际: {defects}"

    def test_graph_evidence_pipe_escaped_and_a4_still_zero(self):
        """SC-2 补强:evidence 含 | 时转义为 \\|,A4 仍正确识别为 1 列(不假绿不误拒)。

        痛点:evidence 原样插入表格,内含 | 拆成 >4 列,旧 A4 只拒 <4 列 → 超列假绿
        (畸形图谱表却报零缺陷);直接收成 4 列又会误拒合法 evidence。
        断言绑定可观测行为:evidence="a | b" → 成稿行含 "a \\| b",A4 defects==[]。
        """
        from src.akb2.quality.checks_structural import check_a4_graph_edges
        from src.akb2.quality.distill import ExtractResult, compose_candidate
        from tests.quality.conftest import make_mock_route_llm

        data = _valid_compose_data(
            graph_edges=[
                {"concept": "harness", "relation_type": "part_of", "strength": "strong",
                 "evidence": "command uses a | b pipeline"},
            ],
        )
        ex = ExtractResult(success=True, data=data, retries_used=1)
        mock_fn = make_mock_route_llm({})
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = compose_candidate(
                ex, "raw/test.md", "b", output_dir=tmp_dir, route_llm_fn=mock_fn
            )
            body = open(result.candidate_path, encoding="utf-8").read()
        # 转义生效:成稿含 "a \| b"(反引号包裹的 code 不在表里,evidence 在表里转义)
        assert "command uses a \\| b pipeline" in body, "evidence 含 | 应转义为 \\|"
        # A4 不假绿:正确识别转义后为 1 列,4 列齐 → 0 缺陷
        defects = check_a4_graph_edges(body)
        assert defects == [], f"转义后 A4 应零缺陷,实际: {defects}"

    def test_md_table_cell_escapes_pipe_and_newline(self):
        """_md_table_cell 转义 | 和换行(单元级,防拆列)。"""
        from src.akb2.quality.distill import _md_table_cell

        assert _md_table_cell("a | b") == "a \\| b"
        assert _md_table_cell("line1\nline2") == "line1 line2"
        assert _md_table_cell("plain") == "plain"

    def test_evidence_map_uses_gap_not_argumentation(self, extract_success_response):
        """必修1:证据地图"未覆盖"列填 evidence_gap 真盲区,不填论证摘要。

        痛点:旧实现用 argumentation explanation 填该列 → 退化成背景释义,
        grader 维度4(证据诚实)扣分。
        断言绑定可观测行为:【证据地图节内】"未覆盖"列含 evidence_gap 盲区词(未量化/未比较/未验证),
        不含 argumentation explanation 文本(该文本只该出现在论证节)。
        """
        body = self._compose(extract_success_response)
        # 抽出"证据地图"节(## 证据地图 到下一 ## 之间)
        import re

        m = re.search(r"## 证据地图\n(.*?)\n## ", body, re.S)
        assert m, "成稿应有证据地图节"
        ev_section = m.group(1)
        assert "未量化" in ev_section, "证据地图未覆盖列应含 evidence_gap 盲区词"
        # argumentation[0] explanation 只该在论证节,不该塞进证据地图未覆盖列
        assert "human role from prompt-author" not in ev_section, (
            "证据地图未覆盖列不该填 argumentation explanation"
        )

    def test_fresh_mcp_numeric_anchors_and_source_how_pass_self_gate(self):
        """Fresh MCP 回归:数字锚点、协议操作、无 AKB 伪命令、无硬编码 novelty。"""
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "MCP clients start by sending initialize to negotiate protocol capabilities",
                    "anchor": "§1",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供不同客户端初始化耗时",
                },
                {
                    "text": "Clients use tools/list to discover tools before sending tools/call",
                    "anchor": "§2",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较工具发现策略",
                },
                {
                    "text": "Resources and prompts expose list/read or list/get style operations",
                    "anchor": "§3",
                    "provenance": "source_grounded",
                    "evidence_gap": "未覆盖：原文未展开权限失败处理",
                },
            ],
            "how_steps": [
                {
                    "instruction": "Send initialize before using the server",
                    "command": "initialize",
                    "executable": True,
                    "anchor": "§1",
                    "provenance": "source_grounded",
                },
                {
                    "instruction": "List tools before invoking one",
                    "command": "tools/list",
                    "executable": True,
                    "anchor": "§2",
                    "provenance": "source_grounded",
                },
                {
                    "instruction": "Invoke a selected tool",
                    "command": "tools/call",
                    "executable": True,
                    "anchor": "§2",
                    "provenance": "source_grounded",
                },
                {
                    "instruction": "Read a resource when the server exposes one",
                    "command": "resources/read",
                    "executable": True,
                    "anchor": "§3",
                    "provenance": "source_grounded",
                },
            ],
            "params": {},
            "referenced_concepts": ["mcp", "tool-protocol"],
            "tldr": "MCP exposes a protocol-level sequence for initialization, tool discovery, invocation, and resource access.",
            "argumentation": [
                {
                    "claim_anchor": "§1",
                    "explanation": "MCP clients start by sending initialize to negotiate protocol capabilities",
                    "provenance": "source_grounded",
                },
                {
                    "claim_anchor": "§2",
                    "explanation": "Clients use tools/list to discover tools before sending tools/call",
                    "provenance": "source_grounded",
                },
            ],
            "applicability_boundary": "适用：需要通过协议发现和调用工具的客户端；不适用：无工具调用的静态摘要；约束：需要服务端能力声明。",
            "graph_edges": [
                {
                    "concept": "mcp",
                    "relation_type": "describes",
                    "strength": "strong",
                    "evidence": "[raw §1] (source_grounded) initialize negotiates protocol capabilities.",
                    "anchor": "§1",
                    "provenance": "source_grounded",
                    "evidence_kind": "source_summary",
                }
            ],
        }
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "test-fresh-entry--raw--2026-06-21.md"
            raw_path.parent.mkdir(parents=True)
            raw_content = """---
artifact_type: raw
source_slug: test-fresh-entry
source_url: https://modelcontextprotocol.io/specification
---

# §1 Initialize

MCP clients start by sending initialize to negotiate protocol capabilities.

# §2 Tools

Clients use tools/list to discover tools before sending tools/call.

# §3 Resources and prompts

Resources and prompts expose resources/read and prompts/get operations.
"""
            raw_path.write_text(raw_content, encoding="utf-8")
            result = compose_candidate(
                ex,
                str(raw_path),
                "fresh-mcp",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")

        assert result.success is True
        assert "[raw §1]" in body
        assert "[raw §A]" not in body
        assert "`initialize`" in body
        assert "`tools/list`" in body
        assert "`tools/call`" in body
        assert "assert evidence_map.contains" not in body
        assert "maker_family" not in body
        assert "value_score=measure_usage" not in body
        assert "wiki/sources/addy-osmani-loop-engineering.md" not in body
        assert run_candidate_self_gate(body, raw_content) == []

    def test_compose_recovers_source_protocol_ops_before_self_gate(self):
        """P0-5: source 有真实协议操作时，compose 必须锚定来源操作而不是直接放弃。"""
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "MCP clients start by sending initialize to negotiate protocol capabilities",
                    "anchor": "§1",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供不同客户端初始化耗时",
                },
                {
                    "text": "Clients use tools/list to discover tools before sending tools/call",
                    "anchor": "§2",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较工具发现策略",
                },
                {
                    "text": "Resources and prompts expose resources/read and prompts/get operations",
                    "anchor": "§3",
                    "provenance": "source_grounded",
                    "evidence_gap": "未覆盖：原文未展开权限失败处理",
                },
            ],
            "how_steps": [
                {
                    "instruction": "Summarize the protocol",
                    "command": "",
                    "executable": False,
                    "anchor": "§1",
                    "provenance": "source_grounded",
                }
            ],
            "params": {},
            "referenced_concepts": ["mcp", "tool-protocol"],
            "tldr": "MCP exposes a protocol-level sequence for initialization, tool discovery, invocation, and resource access.",
            "argumentation": [
                {
                    "claim_anchor": "§1",
                    "explanation": "MCP clients start by sending initialize to negotiate protocol capabilities",
                    "provenance": "source_grounded",
                },
                {
                    "claim_anchor": "§2",
                    "explanation": "Clients use tools/list to discover tools before sending tools/call",
                    "provenance": "source_grounded",
                },
                {
                    "claim_anchor": "§3",
                    "explanation": "Resources and prompts expose resources/read and prompts/get operations",
                    "provenance": "source_grounded",
                },
            ],
            "applicability_boundary": "适用：需要通过协议发现和调用工具的客户端；不适用：无工具调用的静态摘要；约束：需要服务端能力声明。",
            "graph_edges": [
                {
                    "concept": "mcp",
                    "relation_type": "describes",
                    "strength": "strong",
                    "evidence": "[raw §1] (source_grounded) initialize negotiates protocol capabilities.",
                    "anchor": "§1",
                    "provenance": "source_grounded",
                    "evidence_kind": "source_summary",
                }
            ],
        }
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "test-fresh-entry--raw--2026-06-21.md"
            raw_path.parent.mkdir(parents=True)
            raw_content = """---
artifact_type: raw
source_slug: test-fresh-entry
source_url: https://modelcontextprotocol.io/specification
---

# §1 Initialize

MCP clients start by sending initialize to negotiate protocol capabilities.

# §2 Tools

Clients use tools/list to discover tools before sending tools/call.

# §3 Resources and prompts

Resources and prompts expose resources/read and prompts/get operations.
"""
            raw_path.write_text(raw_content, encoding="utf-8")
            result = compose_candidate(
                ex,
                str(raw_path),
                "fresh-mcp-source-how",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            body = Path(result.candidate_path).read_text(encoding="utf-8")

            assert result.success is True
            assert "`initialize`" in body
            assert "`tools/list`" in body
            assert "`tools/call`" in body
            assert "原文无可安全锚定的操作步骤" not in body
            assert run_candidate_self_gate(body, raw_content) == []

    def test_compose_recovers_source_protocol_ops_from_raw_when_claims_are_summaries(self):
        """P0-5: DeepSeek 摘要化 claims 时，compose 仍要从 raw section 恢复协议操作。"""
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "MCP defines a client-server protocol for tool context.",
                    "anchor": "§1",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供协议开销数据",
                },
                {
                    "text": "The lifecycle uses a handshake before clients use server capabilities.",
                    "anchor": "§2",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较不同握手策略",
                },
                {
                    "text": "The protocol exposes tools, resources, and prompts as capabilities.",
                    "anchor": "§3",
                    "provenance": "source_grounded",
                    "evidence_gap": "未覆盖：原文未展开权限失败处理",
                },
            ],
            "how_steps": [
                {
                    "instruction": "Summarize the protocol capabilities",
                    "command": "",
                    "executable": False,
                    "anchor": "§1",
                    "provenance": "source_grounded",
                }
            ],
            "params": {},
            "referenced_concepts": ["mcp", "source-protocol"],
            "tldr": "MCP defines a protocol lifecycle and server capability surface.",
            "argumentation": [
                {
                    "claim_anchor": "§1",
                    "explanation": "MCP defines a client-server protocol for tool context.",
                    "provenance": "source_grounded",
                },
                {
                    "claim_anchor": "§2",
                    "explanation": "The lifecycle uses a handshake before clients use server capabilities.",
                    "provenance": "source_grounded",
                },
                {
                    "claim_anchor": "§3",
                    "explanation": "The protocol exposes tools, resources, and prompts as capabilities.",
                    "provenance": "source_grounded",
                },
            ],
            "applicability_boundary": "适用：需要通过协议发现和调用工具的客户端；不适用：无工具调用的静态摘要；约束：需要服务端能力声明。",
            "graph_edges": [
                {
                    "concept": "mcp",
                    "relation_type": "describes",
                    "strength": "strong",
                    "evidence": "[raw §1] (source_grounded) protocol overview.",
                    "anchor": "§1",
                    "provenance": "source_grounded",
                    "evidence_kind": "source_summary",
                }
            ],
        }
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "test-fresh-entry--raw--2026-06-21.md"
            raw_path.parent.mkdir(parents=True)
            raw_content = """---
artifact_type: raw
source_slug: test-fresh-entry
source_url: https://modelcontextprotocol.io/specification
---

# §1 Overview

MCP defines a client-server protocol for tool context.

# §2 Lifecycle

The lifecycle starts when the client sends initialize. The server responds and the client sends notifications/initialized.

# §3 Tools

Clients use tools/list to discover tools before sending tools/call.

# §4 Resources

Clients use resources/list before resources/read.

# §5 Prompts

Clients use prompts/list before prompts/get.
"""
            raw_path.write_text(raw_content, encoding="utf-8")
            result = compose_candidate(
                ex,
                str(raw_path),
                "fresh-mcp-summary-claims",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )
            assert result.success is True, result.error
            body = Path(result.candidate_path).read_text(encoding="utf-8")

        assert "`initialize`" in body
        assert "`tools/list`" in body
        assert "`tools/call`" in body
        assert "原文无可安全锚定的操作步骤" not in body
        assert "| §3 |" in body
        assert run_candidate_self_gate(body, raw_content) == []

    def test_compose_fail_closed_when_how_steps_and_source_ops_are_insufficient(self):
        """P0-2: compose 写盘前必须自检 How 密度/可执行性，不能把坏稿交给 grader 抓。"""
        from tests.quality.conftest import make_mock_route_llm

        data = {
            "claims": [
                {
                    "text": "The document describes a conceptual protocol governance model",
                    "anchor": "§1",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供协议治理模型效果数据",
                },
                {
                    "text": "The document recommends reviewing permissions before deployment",
                    "anchor": "§2",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较不同权限审查策略",
                },
                {
                    "text": "The document asks teams to document assumptions",
                    "anchor": "§3",
                    "provenance": "source_grounded",
                    "evidence_gap": "未覆盖：原文未展开假设失效后的处理",
                },
            ],
            "how_steps": [
                {
                    "instruction": "Summarize the protocol",
                    "command": "",
                    "executable": False,
                    "anchor": "§1",
                    "provenance": "source_grounded",
                }
            ],
            "params": {},
            "referenced_concepts": ["protocol-governance", "quality-gate"],
            "tldr": "The source gives conceptual protocol governance guidance but not enough executable operations.",
            "argumentation": [
                {
                    "claim_anchor": "§1",
                    "explanation": "The document describes a conceptual protocol governance model",
                    "provenance": "source_grounded",
                },
                {
                    "claim_anchor": "§2",
                    "explanation": "The document recommends reviewing permissions before deployment",
                    "provenance": "source_grounded",
                },
                {
                    "claim_anchor": "§3",
                    "explanation": "The document asks teams to document assumptions",
                    "provenance": "source_grounded",
                },
            ],
            "applicability_boundary": "适用：协议治理审查；不适用：无权限边界的静态说明；约束：需要人工确认假设。",
            "graph_edges": [],
        }
        ex = ExtractResult(success=True, data=data, retries_used=1)
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw" / "test-conceptual-entry--raw--2026-06-21.md"
            raw_path.parent.mkdir(parents=True)
            raw_content = """---
artifact_type: raw
source_slug: test-conceptual-entry
source_url: https://example.com/conceptual
---

# §1 Overview

The document describes a conceptual protocol governance model.

# §2 Permission Review

The document recommends reviewing permissions before deployment.

# §3 Assumptions

The document asks teams to document assumptions.
"""
            raw_path.write_text(raw_content, encoding="utf-8")
            result = compose_candidate(
                ex,
                str(raw_path),
                "conceptual-bad-how",
                output_dir=tmp_dir,
                route_llm_fn=make_mock_route_llm({}),
            )

            assert result.success is False
            assert "compose_self_gate_failed" in result.error
            assert "SG-how" in result.error
            assert list((Path(tmp_dir) / "candidate").rglob("*.md")) == []


# ---------------------------------------------------------------------------
# F52: _read_raw_frontmatter 坏 YAML 降级逐行提取（不再静默返 {}）
# ---------------------------------------------------------------------------


class TestReadRawFrontmatterDegraded:
    """F52 回归: raw frontmatter YAML 不可解析时降级逐行提取,元数据不丢。

    实锤事故: 432/4184 篇 raw frontmatter 不可解析(@ 开头作者/多行作者),
    旧实现 except: return {} → zed-blog--ead0a0400895 与
    smashing-magazine--fa0b3ac1fdf6 以 source_url='' 升格 active。
    """

    def _write_raw(self, tmp_path, fm_block: str) -> str:
        p = tmp_path / "bad--raw--2026-07-21.md"
        p.write_text(f"---\n{fm_block}\n---\n\n# body\n", encoding="utf-8")
        return str(p)

    def test_at_prefix_author_degrades_to_line_extraction(self, tmp_path):
        """zed 实锤: @ 开头作者炸 YAML → 逐行降级仍提取出全部 9 字段。"""
        from src.akb2.quality.distill import _read_raw_frontmatter

        fm_block = (
            "artifact_type: raw\n"
            "source_slug: zed-blog\n"
            'source_url: "https://zed.dev/blog/zed-ai"\n'
            "source_title: Introducing Zed AI - Zed Blog\n"
            "source_authors: @nathansobo\n"
            "source_publisher: zed.dev\n"
            "source_published_at: 08/20/2024\n"
            'fetched_at: "2026-07-21T02:18:11Z"\n'
            "fetched_via: scrapling"
        )
        raw_path = self._write_raw(tmp_path, fm_block)
        fm = _read_raw_frontmatter(raw_path)
        assert fm["source_url"] == "https://zed.dev/blog/zed-ai"
        assert fm["source_authors"] == "@nathansobo"
        assert fm["source_title"] == "Introducing Zed AI - Zed Blog"
        assert fm["source_published_at"] == "08/20/2024"

    def test_multiline_author_degrades_keeps_other_fields(self, tmp_path):
        """smashing 实锤: 多行作者撑破块 → 其余单行字段仍逐行提取成功。"""
        from src.akb2.quality.distill import _read_raw_frontmatter

        fm_block = (
            "artifact_type: raw\n"
            "source_slug: smashing-magazine\n"
            'source_url: "https://smashingmagazine.com/2026/07/x/"\n'
            'source_title: "Weaponizing And Defending The React Flight Protocol"\n'
            "source_authors: Durgesh Pawar, More about\n"
            "Durgesh ↬\n"
            "source_publisher: smashingmagazine.com\n"
            'source_published_at: "2026-07-21 10:00:00 +0000 UTC"\n'
            'fetched_at: "2026-07-22T14:23:33Z"\n'
            "fetched_via: defuddle"
        )
        raw_path = self._write_raw(tmp_path, fm_block)
        fm = _read_raw_frontmatter(raw_path)
        assert fm["source_url"] == "https://smashingmagazine.com/2026/07/x/"
        assert "Weaponizing" in fm["source_title"]
        assert fm["source_publisher"] == "smashingmagazine.com"
        # 被撑破的作者行:降级提取拿到首行即可(比空强)
        assert "Durgesh Pawar" in fm.get("source_authors", "")

    def test_healthy_yaml_unchanged(self, tmp_path):
        """健康 frontmatter 走正常 yaml 解析路径,行为不变。"""
        from src.akb2.quality.distill import _read_raw_frontmatter

        fm_block = (
            "artifact_type: raw\n"
            "source_slug: ok-blog\n"
            "source_url: https://ok.dev/a\n"
            "source_title: T\n"
            'source_authors: ["A", "B"]\n'
            "source_publisher: ok.dev\n"
            "source_published_at: '2026-01-01'\n"
            "fetched_at: '2026-01-01T00:00:00Z'\n"
            "fetched_via: defuddle"
        )
        raw_path = self._write_raw(tmp_path, fm_block)
        fm = _read_raw_frontmatter(raw_path)
        assert fm["source_authors"] == ["A", "B"]  # list 语义保留(非降级路径)
        assert fm["source_url"] == "https://ok.dev/a"

    def test_missing_file_still_empty(self):
        """文件不存在仍返 {}(单测虚拟路径不崩,原契约保留)。"""
        from src.akb2.quality.distill import _read_raw_frontmatter

        assert _read_raw_frontmatter("/nonexistent/x.md") == {}

    def test_no_frontmatter_still_empty(self, tmp_path):
        """无 frontmatter 块仍返 {}(原契约保留)。"""
        from src.akb2.quality.distill import _read_raw_frontmatter

        p = tmp_path / "nofm.md"
        p.write_text("# just body\n", encoding="utf-8")
        assert _read_raw_frontmatter(str(p)) == {}

    def test_degraded_build_frontmatter_fills_source_url(self, tmp_path):
        """端到端: 坏 YAML raw 经 _build_frontmatter 后 source_url 不再为空。"""
        from src.akb2.quality.distill import _build_frontmatter

        fm_block = (
            "artifact_type: raw\n"
            "source_slug: zed-blog\n"
            'source_url: "https://zed.dev/blog/zed-ai"\n'
            "source_title: Introducing Zed AI\n"
            "source_authors: @nathansobo\n"
            "source_publisher: zed.dev\n"
            "source_published_at: 08/20/2024\n"
            'fetched_at: "2026-07-21T02:18:11Z"\n'
            "fetched_via: scrapling"
        )
        p = tmp_path / "zed-blog--x--raw--2026-07-21.md"
        p.write_text(f"---\n{fm_block}\n---\n\n# body\n", encoding="utf-8")
        data = {
            "claims": [
                {"text": "t", "anchor": "§A", "provenance": "source_grounded"}
            ],
            "how_steps": [],
            "params": {},
            "referenced_concepts": ["a", "b"],
        }
        fm = _build_frontmatter(str(p), data)
        assert fm["source_url"] == "https://zed.dev/blog/zed-ai"
        assert fm["source_author"] == "@nathansobo"
        assert "Introducing Zed AI" in fm["canonical_title"]
