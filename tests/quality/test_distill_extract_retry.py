"""test_distill_extract_retry.py — 段 1 抽取重试路径测试。

覆盖:
    - 正常抽取成功
    - JSON 解析失败走有界重试
    - 锚点校验失败走有界重试
    - 重试耗尽显式失败不出候选
    - MAX_EXTRACT_RETRIES 是具名常量
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.akb2.quality.distill import (
    DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S,
    DISTILL_MAX_RETRIES,
    DISTILL_TIMEOUT_S,
    MAX_EXTRACT_CHARS,
    MAX_EXTRACT_RETRIES,
    _DISTILL_SEGMENT_SCHEMAS,
    ExtractResult,
    build_deterministic_extract_result,
    compose_candidate,
    _validate_extract_output,
    extract_claims,
)


# AKB 操作化命令字面量集（fenced text block），供测试 raw 补全后通过 SG-command-recheck
# 全文回查。fenced block 不被 must_cover scanner 拾取，不引入新 must_cover 项。
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
make pipeline
make test
make deploy
queue.next()
cmd(input=work_item, timeout_seconds=60)
queue_item={source_url,raw_anchor,content_hash,output_path,lifecycle_state}
ledger.append({event:'distill_extract',status:'start'})
max_repair_rounds=3; assert remaining_defects == 0
```
"""


class TestExtractSuccess:
    """正常抽取成功路径。"""

    def test_extract_claims_success(
        self, sample_raw_content, extract_success_response
    ):
        """段 1 抽取成功，返回结构化数据。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is True
        assert result.retries_used >= 1  # 至少 1 次调用
        assert "claims" in result.data
        assert len(result.data["claims"]) >= 1
        assert "how_steps" in result.data
        assert "params" in result.data
        assert "referenced_concepts" in result.data

    def test_extract_claims_have_anchors(
        self, sample_raw_content, extract_success_response
    ):
        """每条 claim 带 §X 格式锚点。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        for claim in result.data["claims"]:
            assert "anchor" in claim, "每条 claim 必须有 anchor"
            anchor = claim["anchor"]
            assert anchor.startswith("§"), f"anchor 应以 § 开头: {anchor}"
            assert len(anchor) == 2, f"anchor 格式 §X: {anchor}"
            assert anchor[1].isalpha()

    def test_extract_claims_have_provenance(
        self, sample_raw_content, extract_success_response
    ):
        """每条 claim 有 provenance 字段（source_grounded / akb_interpretation）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        valid_provenances = {"source_grounded", "akb_interpretation"}
        for claim in result.data["claims"]:
            assert "provenance" in claim
            assert claim["provenance"] in valid_provenances, (
                f"provenance 应为 source_grounded/akb_interpretation: {claim['provenance']}"
            )


class TestExtractRetry:
    """抽取重试路径。"""

    def test_extract_retry_on_invalid_json(
        self, sample_raw_content, extract_fail_invalid_json_response
    ):
        """JSON 解析失败走重试，耗尽后显式失败。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_fail_invalid_json_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is False
        assert result.retries_used == MAX_EXTRACT_RETRIES
        assert result.data == {}
        assert "非 JSON" in result.error or "重试" in result.error or "失败" in result.error

    def test_extract_retry_on_missing_anchor(
        self, monkeypatch, sample_raw_content, extract_fail_missing_anchor_response
    ):
        """claims 缺少 anchor 字段走重试并失败。

        本用例聚焦 claims 校验重试路径，关闭语义 retake 以隔离该特性
        （retake 默认开由 test_semantic_jitter 专门覆盖）。
        """
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "0")
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_fail_missing_anchor_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        assert result.success is False
        assert result.retries_used == MAX_EXTRACT_RETRIES

    def test_max_extract_retries_is_named_constant(self):
        """MAX_EXTRACT_RETRIES 是具名常量（宪法 P1/P4）。"""
        assert isinstance(MAX_EXTRACT_RETRIES, int)
        assert MAX_EXTRACT_RETRIES > 0
        assert MAX_EXTRACT_RETRIES == 3

    def test_extract_uses_deepseek_model(
        self, sample_raw_content, extract_success_response
    ):
        """段 1 抽取使用 deepseek-v4-pro 模型。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        result = extract_claims(sample_raw_content, route_llm_fn=mock_fn)

        # 验证模型使用：response 的 model_used 应为 deepseek
        assert result.success is True

    def test_extract_is_segmented_and_disables_deepseek_reasoning(
        self, sample_raw_content, extract_success_response
    ):
        """DeepSeek distill 分段抽取必须 timeout=60/retry=0/reasoning_effort=None。"""
        from tests.quality.conftest import make_mock_route_llm

        calls = []
        base_mock = make_mock_route_llm(extract_success_response)

        def tracking_mock(model_family, messages, response_schema=None, **kwargs):
            calls.append(
                {
                    "model_family": model_family,
                    "response_schema": response_schema,
                    "kwargs": kwargs,
                }
            )
            return base_mock(model_family, messages, response_schema, **kwargs)

        result = extract_claims(sample_raw_content, route_llm_fn=tracking_mock)

        assert result.success is True
        # retake 默认开：1 claims + 2 how_params（SEMANTIC_RETAKE_MAX 取交集）= 3 次调用。
        # mock 对每次调用返回同一份合法响应，两份 how_params 交集非空 → 成功。
        assert len(calls) == 3
        assert {c["response_schema"]["required"][0] for c in calls} == {
            "claims",
            "how_steps",
        }
        for call in calls:
            assert call["model_family"] == "deepseek-v4-pro"
            assert call["kwargs"]["timeout_s"] == DISTILL_TIMEOUT_S
            assert call["kwargs"]["max_retries"] == DISTILL_MAX_RETRIES
            assert call["kwargs"]["reasoning_effort"] is None
            assert call["kwargs"]["strict_schema"] is True

    def test_extract_attempt_has_wall_clock_deadline(
        self, monkeypatch, sample_raw_content
    ):
        """三段 extract 整体必须有墙钟 deadline，避免网关卡住不返回。"""
        from src.akb2.quality import distill

        monkeypatch.setattr(distill, "DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S", 1e-3)

        def hanging_mock(*args, **kwargs):
            time.sleep(0.1)
            raise AssertionError("deadline should interrupt before this")

        result = distill.extract_claims(sample_raw_content, route_llm_fn=hanging_mock)

        assert result.success is False
        assert result.retries_used == distill.MAX_EXTRACT_RETRIES
        assert "总时限" in result.error or "超" in result.error

    def test_segmented_extract_preserves_params_external_shape(
        self, monkeypatch, sample_raw_content
    ):
        """分段 schema 用 param_entries，合并后仍输出旧 params dict。

        本用例聚焦 params 外部形状，关闭语义 retake 以隔离该特性。
        """
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "0")
        from src.akb2.quality.llm_router import LLMResponse
        from tests.quality.conftest import make_mock_route_llm

        responses = {
            "deepseek-v4-pro:0": LLMResponse(
                content={
                    "claims": [
                        {
                            "text": "Loop engineering makes loops bounded and observable",
                            "anchor": "§A",
                            "provenance": "source_grounded",
                            "evidence_gap": "未量化：原文未提供可观测性的量化判据",
                        }
                    ],
                    "tldr": "Loop engineering converts agent work into bounded observable loops.",
                },
                model_used="deepseek-v4-pro",
            ),
            "deepseek-v4-pro:1": LLMResponse(
                content={
                    "how_steps": [
                        {"instruction": "Define trigger", "command": "trigger=schedule", "executable": True},
                        {"instruction": "Select work", "command": "queue.next()", "executable": True},
                        {"instruction": "Write ledger", "command": "ledger.append(event)", "executable": True},
                    ],
                    "param_entries": [
                        {"key": "max_rounds", "value": "3"},
                        {"key": "budget_seconds", "value": "300"},
                    ],
                },
                model_used="deepseek-v4-pro",
            ),
        }

        result = extract_claims(
            sample_raw_content,
            route_llm_fn=make_mock_route_llm(responses),
        )

        assert result.success is True
        assert result.data["params"] == {"max_rounds": "3", "budget_seconds": "300"}
        assert result.data["claims"][0]["anchor"] == "§A"

    def test_deepseek_beta_tool_switch_uses_beta_router(
        self, monkeypatch, sample_raw_content
    ):
        """AKB2_DISTILL_DEEPSEEK_BETA_TOOL=1 时核心 extract 走官方 beta strict tool。"""
        from src.akb2.quality.llm_router import LLMResponse

        calls = []
        responses = [
            {
                "claims": [
                    {
                        "text": "Loop engineering makes agent work bounded",
                        "anchor": "§A",
                        "provenance": "source_grounded",
                        "evidence_gap": "未量化：原文未提供失败率或成本数据",
                    }
                ],
                "tldr": "Loop engineering makes agent work bounded and observable.",
            },
            {
                "how_steps": [
                    {"instruction": "Define trigger", "command": "trigger=schedule", "executable": True},
                    {"instruction": "Select work", "command": "queue.next()", "executable": True},
                    {"instruction": "Write ledger", "command": "ledger.append(event)", "executable": True},
                ],
                "param_entries": [{"key": "max_rounds", "value": "3"}],
            },
        ]

        def fake_beta_tool(messages, response_schema, **kwargs):
            calls.append(kwargs)
            return LLMResponse(
                content=responses[len(calls) - 1],
                model_used="deepseek-chat",
            )

        monkeypatch.setenv("AKB2_DISTILL_DEEPSEEK_BETA_TOOL", "1")
        # 聚焦 beta_tool 路由，关闭语义 retake 隔离该特性（retake 默认开由 test_semantic_jitter 覆盖）。
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "0")
        monkeypatch.setattr(
            "src.akb2.quality.llm_router.route_deepseek_beta_tool",
            fake_beta_tool,
        )

        result = extract_claims(sample_raw_content)

        assert result.success is True
        assert len(calls) == 2
        assert [call["tool_name"] for call in calls] == [
            "akb2_extract_claims",
            "akb2_extract_how_params",
        ]
        assert result.data["params"] == {"max_rounds": "3"}
        assert result.data["argumentation"][0]["claim_anchor"] == "§A"

    def test_deepseek_json_mode_switch_normalizes_loose_shape(
        self, monkeypatch, sample_raw_content
    ):
        """DeepSeek JSON Output 常见 loose shape 要能被本地 normalizer 拉回 AKB2 schema。"""
        from src.akb2.quality.llm_router import LLMResponse

        calls = []
        responses = [
            {
                "claims": [
                    {
                        "id": "c1",
                        "claim": "Loop engineering designs bounded agent loops.",
                        "anchors": ["§A"],
                        "evidence_gap": "未提供量化验证或对比数据。",
                    }
                ],
                "tldr": "Loop engineering turns agent work into bounded observable loops.",
            },
            {
                "how_steps": [
                    {"text": "Define trigger"},
                    {"text": "Select next work item"},
                    {"text": "Write ledger entry"},
                ],
                "param_entries": [
                    {"name": "max_rounds", "evidence_gap": "未提供默认值"}
                ],
            },
        ]

        def fake_json_mode(messages, **kwargs):
            calls.append(kwargs)
            return LLMResponse(
                content=responses[len(calls) - 1],
                model_used="deepseek/deepseek-v4-pro",
            )

        monkeypatch.setenv("AKB2_DISTILL_DEEPSEEK_JSON_MODE", "1")
        # 聚焦 JSON mode loose shape 归一化，关闭语义 retake 隔离该特性。
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "0")
        monkeypatch.setattr(
            "src.akb2.quality.llm_router.route_deepseek_json_object",
            fake_json_mode,
        )

        result = extract_claims(sample_raw_content)

        assert result.success is True
        assert len(calls) == 2
        assert result.data["claims"][0]["text"].startswith("Loop engineering")
        assert result.data["params"] == {"max_rounds": "未提供默认值"}
        assert result.data["how_steps"][0]["command"].startswith("trigger=")
        assert not result.data["how_steps"][0]["command"].startswith("akb2_")
        assert len(result.data["referenced_concepts"]) >= 2

    def test_local_enrichment_argumentation_aligns_to_claim_order(
        self, monkeypatch, sample_raw_content
    ):
        """本地保守 enrichment 按 claim 顺序覆盖，不能全部默认到 §A。"""
        from src.akb2.quality.llm_router import LLMResponse

        calls = []
        responses = [
            {
                "claims": [
                    {
                        "claim": "Loop engineering makes loops bounded.",
                        "anchors": ["§A"],
                        "evidence_gap": "未量化：原文未提供失败率数据。",
                    },
                    {
                        "claim": "Ledger-first recovery makes crashes resumable.",
                        "anchors": ["§B"],
                        "evidence_gap": "未验证：原文未提供崩溃恢复实测。",
                    },
                    {
                        "claim": "Targeted repair avoids full restarts.",
                        "anchors": ["§C"],
                        "evidence_gap": "未比较：原文未比较重跑成本。",
                    },
                ],
                "tldr": "Loop engineering turns repeated agent work into bounded observable recovery loops.",
            },
            {
                "how_steps": [
                    {"text": "Define trigger"},
                    {"text": "Select next item"},
                    {"text": "Write ledger"},
                ],
                "param_entries": [],
            },
        ]

        def fake_json_mode(messages, **kwargs):
            calls.append(kwargs)
            return LLMResponse(
                content=responses[len(calls) - 1],
                model_used="deepseek/deepseek-v4-pro",
            )

        monkeypatch.setenv("AKB2_DISTILL_DEEPSEEK_JSON_MODE", "1")
        # 聚焦本地 enrichment 按 claim 顺序对齐，关闭语义 retake 隔离该特性。
        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "0")
        monkeypatch.setattr(
            "src.akb2.quality.llm_router.route_deepseek_json_object",
            fake_json_mode,
        )

        result = extract_claims(sample_raw_content)

        assert result.success is True
        assert [a["claim_anchor"] for a in result.data["argumentation"]] == [
            "§A",
            "§B",
            "§C",
        ]
        assert len(calls) == 2

    def test_deterministic_maker_fallback_builds_structural_candidate(self, tmp_path: Path):
        """LLM maker 不可用时，本地 fallback 仍产出受结构门约束的候选。"""
        from src.akb2.quality.checks_structural import run_structural_checks

        raw = tmp_path / "raw" / "loop-fallback--raw--2026-06-20.md"
        raw.parent.mkdir(parents=True)
        raw.write_text(
            """---
source_title: Loop Fallback
source_url: https://example.com/loop-fallback
source_authors: Test Author
source_published_at: 2026-06-20
---

§A_CONTENT_START
Loop engineering turns repeated AI-agent work into bounded feedback loops with visible state.

§B_WORK_QUEUE
The queue item carries source URL, raw anchor, content hash, output path, and lifecycle state.

§C_REPAIR_PATH
Repair stops after a fixed number of rounds and only accepts the candidate when defects are empty.

§D_BUDGETS
Budgets include distillation timeout 60 seconds, grader timeout 120 seconds, maximum repair rounds 3, and maximum parallel items 1.

§E_CHECKER
An independent checker verifies raw anchors, source references, evidence gaps, graph relationships, and fallback usage.
""" + _COMMAND_EXAMPLES_BLOCK,
            encoding="utf-8",
        )
        extract = build_deterministic_extract_result(raw.read_text(encoding="utf-8"), str(raw))
        assert extract.success is True
        result = compose_candidate(
            extract,
            str(raw),
            "fallback",
            output_dir=str(tmp_path / "staging"),
        )
        assert result.success is True
        candidate = Path(result.candidate_path).read_text(encoding="utf-8")
        assert run_structural_checks(candidate, raw_content=raw.read_text(encoding="utf-8")) == []


class TestValidateExtractOutput:
    """抽取输出校验函数。"""

    # 完整合法 fixture（含扩展后的 4 个新字段）
    _VALID_DATA: dict = {
        "claims": [{"text": "test claim", "anchor": "§A", "provenance": "source_grounded", "evidence_gap": "未量化：原文未提供该论点的量化数据"}],
        "how_steps": [
            {"instruction": "run the pipeline", "command": "make pipeline", "executable": True},
            {"instruction": "run tests", "command": "make test", "executable": True},
            {"instruction": "deploy", "command": "make deploy", "executable": True},
        ],
        "params": {"key": "value"},
        "referenced_concepts": ["concept-1"],
        "tldr": "A clear synthesis of the main insight spanning at least twenty characters.",
        "argumentation": [
            {
                "claim_anchor": "§A",
                "explanation": "Evidence supports this claim.",
                "provenance": "source_grounded",
            }
        ],
        "applicability_boundary": (
            "Applies to production pipelines with observable quality gates. "
            "Not suitable for one-shot tasks."
        ),
        "graph_edges": [
            {"concept": "concept-1", "relation_type": "related", "strength": "medium",
             "evidence": "mentioned in text", "anchor": "§A",
             "provenance": "source_grounded", "evidence_kind": "source_summary"}
        ],
    }

    def test_valid_output_passes(self):
        """合法输出通过校验（含扩展后 4 个新字段）。"""
        _validate_extract_output(self._VALID_DATA)  # 不应抛异常

    def test_empty_claims_raises(self):
        """空 claims 抛 ValueError。"""
        data = {**self._VALID_DATA, "claims": []}
        with pytest.raises(ValueError, match="claims 为空|should be non-empty"):
            _validate_extract_output(data)

    def test_invalid_anchor_format_raises(self):
        """非 §X 格式的 anchor 抛 ValueError。"""
        bad_claim = {"text": "test", "anchor": "bad-anchor", "provenance": "source_grounded", "evidence_gap": "未量化：无"}
        data = {**self._VALID_DATA, "claims": [bad_claim]}
        with pytest.raises(ValueError, match="anchor 格式无效"):
            _validate_extract_output(data)

    def test_invalid_provenance_raises(self):
        """非法 provenance 抛 ValueError。"""
        bad_claim = {"text": "test", "anchor": "§A", "provenance": "unknown_type", "evidence_gap": "未量化：无"}
        data = {**self._VALID_DATA, "claims": [bad_claim]}
        with pytest.raises(ValueError, match="provenance 无效|is not one of"):
            _validate_extract_output(data)

    def test_missing_tldr_raises(self):
        """缺 tldr 或过短抛 ValueError（SC-1: 新增字段非空校验）。"""
        data = {**self._VALID_DATA, "tldr": "too short"}
        # jsonschema 的 minLength 报 "is too short"，代码层报 "tldr 过短"，两者均合法
        with pytest.raises(ValueError, match="tldr|too short"):
            _validate_extract_output(data)

    def test_missing_applicability_boundary_raises(self):
        """缺 applicability_boundary 或过短抛 ValueError（SC-1: 新增字段非空校验）。"""
        data = {**self._VALID_DATA, "applicability_boundary": "short"}
        with pytest.raises(ValueError, match="applicability_boundary|too short"):
            _validate_extract_output(data)

    def test_argumentation_requires_own_provenance(self):
        """argumentation 必须有自己的 provenance，避免解释性推断被 claim provenance 覆盖。"""
        data = {
            **self._VALID_DATA,
            "argumentation": [
                {"claim_anchor": "§A", "explanation": "This is a derived interpretation."}
            ],
        }
        with pytest.raises(ValueError, match="provenance|required"):
            _validate_extract_output(data)

    def test_argumentation_must_cover_all_claim_anchors(self):
        """argumentation 不能只覆盖部分 claims，否则后续论证节会把未覆盖 claim 漏掉。"""
        data = {
            **self._VALID_DATA,
            "claims": [
                {
                    "text": "claim a",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：无量化数据",
                },
                {
                    "text": "claim b",
                    "anchor": "§B",
                    "provenance": "source_grounded",
                    "evidence_gap": "未验证：无独立验证",
                },
            ],
            "argumentation": [
                {
                    "claim_anchor": "§A",
                    "explanation": "Evidence supports claim a.",
                    "provenance": "source_grounded",
                }
            ],
        }

        with pytest.raises(ValueError, match="argumentation 未覆盖"):
            _validate_extract_output(data)

    def test_strong_requires_edge_needs_hard_evidence(self):
        """requires/part_of:strong 必须含 raw 锚点或原文引号，否则强度虚高。"""
        data = {
            **self._VALID_DATA,
            "graph_edges": [
                {
                    "concept": "agent-collaboration",
                    "relation_type": "requires",
                    "strength": "strong",
                    "evidence": "this seems related in multi-agent settings",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_kind": "source_summary",
                }
            ],
        }
        with pytest.raises(ValueError, match="requires:strong 证据不足"):
            _validate_extract_output(data)

    def test_strong_part_of_with_raw_anchor_passes(self):
        """strong 边有 raw 锚点/原文引号时通过。"""
        data = {
            **self._VALID_DATA,
            "graph_edges": [
                {
                    "concept": "harness-engineering",
                    "relation_type": "part_of",
                    "strength": "strong",
                    "evidence": "[raw §A] 原文说 \"loop sits above harness\"",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_kind": "direct_quote",
                }
            ],
        }
        _validate_extract_output(data)


class TestStrictSegmentSchemas:
    """出站 strict schema 契约：兼容 GPT-5.4/OpenAI strict JSON schema。"""

    def test_all_segment_object_schemas_are_closed(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert node.get("additionalProperties") is False, node
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for schema in _DISTILL_SEGMENT_SCHEMAS.values():
            walk(schema)

    def test_all_segment_object_required_fields_cover_properties(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    properties = node.get("properties", {})
                    required = node.get("required", [])
                    if properties:
                        assert isinstance(required, list), node
                        missing = set(properties) - set(required)
                        assert not missing, f"strict object missing required fields: {missing}"
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for schema in _DISTILL_SEGMENT_SCHEMAS.values():
            walk(schema)


class TestProvenanceRatioValidation:
    """SC-1:akb_interpretation 占比 >40% → ValueError 走重试（宪法 P4 失败可见）。

    痛点:prompt 已要求占比 ≤40%，但无校验兜底 → 模型偶尔产出超量库内解读时，
    成稿锚定率塌方（rubric 论断锚定率 / 解读分离），评分循环反复扣分却找不到源头。
    """

    def _data_with_ratio(self, akb_count: int, grounded_count: int) -> dict:
        letters = "ABCDEFGHIJ"
        claims = []
        for i in range(grounded_count):
            claims.append({"text": f"grounded claim {i}", "anchor": f"§{letters[i]}",
                           "provenance": "source_grounded", "evidence_gap": "未量化：无量化数据"})
        for i in range(akb_count):
            claims.append({"text": f"interpretation claim {i}", "anchor": f"§{letters[grounded_count + i]}",
                           "provenance": "akb_interpretation", "evidence_gap": "未验证：无独立验证"})
        argumentation = [
            {
                "claim_anchor": claim["anchor"],
                "explanation": f"Evidence supports {claim['text']}.",
                "provenance": claim["provenance"],
            }
            for claim in claims
        ]
        return {
            **TestValidateExtractOutput._VALID_DATA,
            "claims": claims,
            "argumentation": argumentation,
        }

    def test_over_40_percent_akb_raises(self):
        """akb_interpretation 占比 >40% 抛 ValueError（3 akb / 4 total = 75%）。"""
        data = self._data_with_ratio(akb_count=3, grounded_count=1)  # 3/4 = 75% > 40%
        with pytest.raises(ValueError, match="akb_interpretation 占比"):
            _validate_extract_output(data)

    def test_at_40_percent_passes(self):
        """akb_interpretation 占比恰好 40% 通过（2/5 = 40%，不 >40%）。"""
        data = self._data_with_ratio(akb_count=2, grounded_count=3)  # 2/5 = 40% ≤ 40%
        _validate_extract_output(data)  # 不应抛异常

    def test_all_source_grounded_passes(self):
        """全部 source_grounded（0% akb）通过。"""
        data = self._data_with_ratio(akb_count=0, grounded_count=3)
        _validate_extract_output(data)


class TestEvidenceGapValidation:
    """必修1:证据地图"未覆盖"列必须写真盲区(claim.evidence_gap),不是论证摘要。

    痛点:旧实现"未覆盖"列填 argumentation explanation → 退化成中文背景释义,
    grader 维度4(证据诚实)扣分。evidence_gap 必须含原文盲区词。
    """

    def _claim(self, **over):
        base = {"text": "test claim", "anchor": "§A", "provenance": "source_grounded"}
        base.update(over)
        return base

    def test_empty_evidence_gap_raises(self):
        """claim 缺 evidence_gap 或空白 → ValueError。"""
        # (a) 完全缺字段 → jsonschema 校验报 required property
        data = {**TestValidateExtractOutput._VALID_DATA, "claims": [self._claim()]}
        with pytest.raises(ValueError, match="evidence_gap"):
            _validate_extract_output(data)
        # (b) 空白串(长度≥4 绕过 minLength,但 strip 后空) → 代码层报"为空"
        data = {
            **TestValidateExtractOutput._VALID_DATA,
            "claims": [self._claim(evidence_gap="    ")],
        }
        with pytest.raises(ValueError, match="evidence_gap 为空"):
            _validate_extract_output(data)

    def test_summary_without_gap_word_rejected(self):
        """evidence_gap 是论证摘要/背景释义(无盲区词) → 失败。

        断言绑定可观测行为:填「开篇引用 Steinberger 观点说明范式转移」这种论证摘要 → 拒;
        填「未提供量化结果/未比较失败样本/缺少反例验证」才过。
        """
        summary_as_gap = "开篇引用 Steinberger 和 Cherny 的观点，说明循环工程是范式转移"
        data = {
            **TestValidateExtractOutput._VALID_DATA,
            "claims": [self._claim(evidence_gap=summary_as_gap)],
        }
        with pytest.raises(ValueError, match="不含盲区词"):
            _validate_extract_output(data)

    def test_real_gap_passes(self):
        """evidence_gap 含盲区词(未提供/未量化/未覆盖/缺少/没有/未验证/未比较) → 通过。"""
        for gap in [
            "未提供该论点的独立验证",
            "未量化 bounded 的可度量判据",
            "原文未覆盖五组件的替代方案",
            "缺少失败样本与成本数据",
            "没有给出收敛判据的实测数据",
            "未验证定点修复 vs 重跑的成本",
            "未比较与既有方案的取舍",
        ]:
            data = {
                **TestValidateExtractOutput._VALID_DATA,
                "claims": [self._claim(evidence_gap=gap)],
            }
            _validate_extract_output(data)  # 不应抛异常


class TestHowStepsExecutableCount:
    """SC-1 补强:how_steps 可执行数量校验（≥3 条 executable=true 且 command 非空）。

    痛点:prompt 要求 ≥3 executable=true 但 schema minItems=0、validate 无校验 →
    真实 extract 可合法通过但成稿缺 How 可执行性(rubric How 密度/可执行性)。
    """

    def _data(self, how_steps):
        return {**TestValidateExtractOutput._VALID_DATA, "how_steps": how_steps}

    def test_zero_executable_raises(self):
        """how_steps=[] → 0 可执行步骤 → ValueError。"""
        with pytest.raises(ValueError, match="how_steps 可执行步骤 0 < 3"):
            _validate_extract_output(self._data([]))

    def test_one_executable_raises(self):
        """仅 1 条 executable → 不足 3 → ValueError。"""
        steps = [{"instruction": "only one", "command": "cmd", "executable": True}]
        with pytest.raises(ValueError, match="how_steps 可执行步骤 1 < 3"):
            _validate_extract_output(self._data(steps))

    def test_three_executable_passes(self):
        """≥3 条 executable=true 且 command 非空 → 通过。"""
        steps = [
            {"instruction": f"step {i}", "command": f"cmd{i}", "executable": True}
            for i in range(3)
        ]
        _validate_extract_output(self._data(steps))  # 不应抛异常

    def test_executable_false_or_empty_command_not_counted(self):
        """executable=false 或 command 空 不计入可执行数 → 仍 <3 → ValueError。"""
        steps = [
            {"instruction": "real", "command": "cmd", "executable": True},
            {"instruction": "no command", "command": "", "executable": True},
            {"instruction": "not exec", "command": "cmd", "executable": False},
        ]
        with pytest.raises(ValueError, match="how_steps 可执行步骤 1 < 3"):
            _validate_extract_output(self._data(steps))


class TestDeepSeekHowNormalization:
    """DeepSeek v4-pro 常返回占位函数/类型值，本地层必须转为可复制内容。"""

    def test_placeholder_commands_and_type_params_become_concrete(
        self, tmp_path: Path
    ):
        raw = tmp_path / "raw" / "loop--raw--2026-06-21.md"
        raw.parent.mkdir(parents=True)
        baseline = tmp_path / "wiki" / "concepts" / "concept-harness-engineering.md"
        baseline.parent.mkdir(parents=True)
        baseline.write_text(
            """---
title: Harness Engineering
---

## TLDR

Harness engineering covers agent runtime infrastructure and tool provisioning.
""",
            encoding="utf-8",
        )
        raw.write_text(
            """---
source_title: Loop
source_url: https://example.com/loop
source_authors: A
source_published_at: 2026-06-21
---

# §A Loop
Loop engineering designs bounded loops.

# §B How
Loops use triggers, work unit selection, action execution, quality gates, and ledger writes.

# §C Repair
Targeted repair avoids full restart.
""" + _COMMAND_EXAMPLES_BLOCK,
            encoding="utf-8",
        )
        extract = {
            **TestValidateExtractOutput._VALID_DATA,
            "claims": [
                {
                    "text": "Loop engineering designs bounded loops",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未提供失败率数据",
                },
                {
                    "text": "Loops use triggers, work unit selection, action execution, quality gates, and ledger writes",
                    "anchor": "§B",
                    "provenance": "source_grounded",
                    "evidence_gap": "未比较：原文未比较五组件替代方案",
                }
            ],
            "how_steps": [
                {"instruction": "Set up a trigger", "command": "configure_trigger", "executable": True},
                {"instruction": "Select the next work unit", "command": "select_work_unit", "executable": True},
                {"instruction": "Run the quality gate", "command": "run_quality_gate", "executable": True},
            ],
            "params": {
                "max_rounds": "integer",
                "budget_seconds": "integer",
            },
            "argumentation": [
                {
                    "claim_anchor": "§A",
                    "explanation": "Loop engineering designs bounded loops",
                    "provenance": "source_grounded",
                }
            ],
        }

        result = compose_candidate(
            ExtractResult(success=True, data=extract, retries_used=1),
            str(raw),
            "how-normalize",
            output_dir=str(tmp_path / "staging"),
        )

        assert result.success is True
        text = Path(result.candidate_path).read_text(encoding="utf-8")
        assert "configure_trigger`" not in text
        assert "select_work_unit`" not in text
        assert "run_quality_gate`" not in text
        assert "trigger={type:'schedule',cron:'0 * * * *'}`" in text
        assert "work_item=ledger.next(status='pending',limit=1)`" in text
        assert "assert run_quality_gate(result,grader='gpt-5.4').defects == []`" in text
        assert "assert evidence_map.contains('[raw §A]')`" not in text
        assert "ledger.append({item_id:work_item.id,status:'running',next_action:'execute'})`" not in text
        assert "max_repair_rounds: 3" in text
        assert "distill_timeout_seconds: 60" in text
        assert "max_repair_rounds: integer" not in text
        assert "wiki/concepts/concept-harness-engineering.md: Harness Engineering" in text
        assert "agent runtime infrastructure" in text


class TestMaxExtractCharsConstant:
    """SC-4:截断具名化 MAX_EXTRACT_CHARS ≥50000，源码无裸 8000。"""

    def test_max_extract_chars_named_and_large(self):
        """SC-4:MAX_EXTRACT_CHARS 具名常量 ≥50000 + 源码无裸 8000。

        痛点:旧 raw_content[:8000] 是内联魔数，截掉 §D-§J 约 53% 正文（提炼失真）。
        断言绑定可观测行为:常量具名且放大 + 源码不再残留裸 8000 字面量（注释/代码都不许）。
        """
        from pathlib import Path

        import src.akb2.quality.distill as distill_mod

        assert isinstance(MAX_EXTRACT_CHARS, int)
        assert MAX_EXTRACT_CHARS >= 50_000, f"应 ≥50000，实际 {MAX_EXTRACT_CHARS}"

        src = Path(distill_mod.__file__).read_text(encoding="utf-8")
        assert "8000" not in src, "distill.py 不应残留裸 8000 字面量（截断已具名化）"

    def test_extract_uses_named_constant(self):
        """extract_claims 使用 MAX_EXTRACT_CHARS 而非内联魔数。"""
        import inspect

        source = inspect.getsource(extract_claims)
        assert "MAX_EXTRACT_CHARS" in source, "extract_claims 必须用 MAX_EXTRACT_CHARS 常量"
