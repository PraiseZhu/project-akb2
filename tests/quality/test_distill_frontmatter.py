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

import os
import tempfile

from src.akb2.quality.distill import (
    GATE_REASON_MAX_CHARS,
    ExtractResult,
    compose_candidate,
)


class TestFrontmatterGateDecision:
    """gate_decision: null 写死（已知坑 1）。"""

    def test_gate_decision_is_null(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """frontmatter 中 gate_decision 必须为 null（防范 HC-09 拦截）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test", "anchor": "§A", "provenance": "source_grounded"}
                ],
                "how_steps": ["step 1"],
                "params": {},
                "referenced_concepts": ["loop-engineering", "harness-engineering"],
            },
            retries_used=1,
        )

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


class TestFrontmatterSourceRefs:
    """source_refs 含 raw 路径（已知坑 2）。"""

    def test_source_refs_contains_raw_path(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """source_refs 必须含 raw 路径（HC-05 硬指标）。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test", "anchor": "§A", "provenance": "source_grounded"}
                ],
                "how_steps": ["step 1"],
                "params": {},
                "referenced_concepts": ["loop-engineering", "harness-engineering"],
            },
            retries_used=1,
        )

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

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test", "anchor": "§A", "provenance": "source_grounded"}
                ],
                "how_steps": ["step"],
                "params": {},
                "referenced_concepts": ["a", "b"],
            },
            retries_used=1,
        )

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

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test", "anchor": "§A", "provenance": "source_grounded"},
                    {"text": "test B", "anchor": "§B", "provenance": "source_grounded"},
                    {"text": "test C", "anchor": "§C", "provenance": "source_grounded"},
                ],
                "how_steps": ["step A", "step B"],
                "params": {},
                "referenced_concepts": ["a", "b"],
            },
            retries_used=1,
        )

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


class TestFrontmatterRelatedConcepts:
    """related_concepts ≥2。"""

    def test_related_concepts_count_at_least_two(
        self, sample_raw_content, sample_raw_path, extract_success_response
    ):
        """related_concepts 至少 2 条。"""
        from tests.quality.conftest import make_mock_route_llm

        mock_fn = make_mock_route_llm(extract_success_response)

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test about Agent Loop Pattern", "anchor": "§A", "provenance": "source_grounded"}
                ],
                "how_steps": [],
                "params": {},
                "referenced_concepts": ["loop-engineering"],
            },
            retries_used=1,
        )

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

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test", "anchor": "§A", "provenance": "source_grounded"}
                ],
                "how_steps": [],
                "params": {},
                "referenced_concepts": ["loop-engineering", "harness-engineering"],
            },
            retries_used=1,
        )

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

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test", "anchor": "§A", "provenance": "source_grounded"}
                ],
                "how_steps": ["step 1"],
                "params": {},
                "referenced_concepts": ["loop-engineering", "harness-engineering"],
            },
            retries_used=1,
        )

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
        assert fm["evidence_provenance"] == "source_grounded"
        assert fm["star_level"] == 0

    def test_key_findings_from_claims(self):
        """key_findings 为非空数组，来自 claims（真实内容 ≥3 满足 H7）。"""
        from src.akb2.quality.distill import _build_frontmatter

        fm = _build_frontmatter("raw/test.md", self._data())
        assert isinstance(fm["key_findings"], list)
        assert len(fm["key_findings"]) >= 3, f"3 条 claims 应产 ≥3 key_findings: {fm['key_findings']}"

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

        extract_result = ExtractResult(
            success=True,
            data={
                "claims": [
                    {"text": "test claim about loop engineering", "anchor": "§A", "provenance": "source_grounded"}
                ],
                "how_steps": ["step 1"],
                "params": {"key": "val"},
                "referenced_concepts": ["loop-engineering", "harness-engineering"],
            },
            retries_used=1,
        )

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
