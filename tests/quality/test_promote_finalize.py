"""SC-5.4: 开关 1 升格收尾对齐黄金样张步 10-14。

验证: frontmatter 翻转、00-Index 人读摘要行(非裸文件名) + 修订历史行、
概念回链四列齐、缺失概念落 pending 队列不建 wikilink、维护记录超 5 条合并同日、
staging 保留、触及文件 lint 复验 0。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


@pytest.fixture
def finalize_vault_root(tmp_path: Path) -> Path:
    """创建 fixture vault 用于收尾测试。"""
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "staging" / "candidate" / "test-batch").mkdir(parents=True)
    (vault / "wiki" / "sources").mkdir(parents=True)
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "manifests" / "ingest").mkdir(parents=True)
    (vault / ".akb-runtime").mkdir(parents=True)

    # 候选页
    candidate = vault / "staging" / "candidate" / "test-batch" / "test-finalize.md"
    candidate.write_text("""---
page_status: candidate
source_type: blog
template_type: source
source_refs: [raw/test-finalize--raw.md]
future_questions:
  - Q1: How does finalization affect indexing?
  - Q2: What are the backlink edge cases?
related_concepts:
  - concept-harness-finalize
  - concept-nonexistent-missing
gate_decision: null
slug: test-finalize
title: Test Finalize Article
source_url: https://example.com/finalize-test
date: 2026-06-13
evidence_provenance: source_grounded
graph_role: leaf
graph_status: healthy
key_findings:
  - Finalization test finding one
  - Finalization test finding two
  - Finalization test finding three
---

## TLDR

Test article for promotion finalization validation. This article verifies that
the finalize step correctly handles all golden sample steps 10-14.

## 关键发现

Finding one. Finding two. Finding three.

## 入选理由

Test finalization inclusion rationale for validation purposes.

## 证据地图

| 证据 | 来源 | 强度 |
|------|------|------|
| Test evidence | [raw] | strong |

## 核心论点

Core argument for finalization testing.

## 论证与解释

Detailed explanation of the finalization process.

## 适用边界

Test scenarios for promotion finalization.

## 图谱关系

| 目标 | 关系 | 强度 | 证据 |
|------|------|------|------|
| concept-harness-finalize | supports | strong | 原文明示 finalize pattern |
| concept-nonexistent-missing | references | medium | Referenced but not in vault |

## Future Questions

Q1: How does finalization affect indexing?
Q2: What are the backlink edge cases?

## 维护记录

| 2026-06-13 | Initial candidate | gate: null |""")

    # 00-Index.md
    index_file = vault / "wiki" / "sources" / "00-Index.md"
    index_file.write_text("""# 00-Index — Wiki Sources

## §4 blog

### 2026-06

- [[existing-slug]] — Test Blog · 2026-06-01 · Existing content · serves none · basis: manual

## 修订历史

| 2026-06-01 | Initial index created | |""")

    # 概念页（存在）
    concept_file = vault / "wiki" / "concepts" / "concept-harness-finalize.md"
    concept_file.write_text("""---
page_status: active
template_type: concept
slug: concept-harness-finalize
title: Harness Finalize
graph_role: leaf
graph_status: healthy
---

## TLDR

Test concept for finalization validation.

## 图谱关系

| 目标 | 关系 | 强度 | 证据 |
|------|------|------|------|

## 维护记录

| 2026-06-13 | Initial creation | |""")

    return vault


class TestPromoteFinalize:
    """收尾完整性测试。"""

    def test_frontmatter_flip(self, finalize_vault_root: Path, monkeypatch):
        """frontmatter 翻转: status: active + gate_decision: active。"""
        from src.akb2.quality.promote import _flip_frontmatter

        candidate_path = (
            finalize_vault_root / "staging" / "candidate" / "test-batch" / "test-finalize.md"
        )
        text = candidate_path.read_text()

        flipped = _flip_frontmatter(text, "test-finalize")

        assert "page_status: active" in flipped, "page_status should be active"
        # gate_decision 应为 active
        assert "gate_decision: active" in flipped, "gate_decision should be active"
        # 不应再有 candidate status
        assert "page_status: candidate" not in flipped
        assert "gate_decision: null" not in flipped
        # 正文应保留
        assert "## TLDR" in flipped
        assert "## 维护记录" in flipped

    def test_index_summary_line_format(self, finalize_vault_root: Path):
        """索引摘要行格式正则: 含来源+日期+内容描述，拒绝裸 .md 文件名。"""
        from src.akb2.quality.promote import _add_index_summary_line

        index_path = finalize_vault_root / "wiki" / "sources" / "00-Index.md"
        _add_index_summary_line(
            index_path,
            slug="test-finalize",
            source_label="Test Finalize Article",
            date_str="2026-06-13",
            core_content="Test article for promotion finalization validation",
            concepts=["concept-harness-finalize", "concept-nonexistent-missing"],
            basis="gate: active (five-gate auto-promote)",
        )

        updated = index_path.read_text()

        # 正则：摘要行必须包含 source + date + content description
        summary_pattern = re.compile(
            r"- \[\[test-finalize\]\] — .+ · \d{4}-\d{2}-\d{2} · .+ · serves .+ · basis: .+"
        )
        assert summary_pattern.search(updated), (
            f"Summary line must match format: source · date · content · serves concepts · basis\n"
            f"Got: {updated}"
        )

        # 拒绝裸文件名（禁止只有 [[slug]] 没有描述的条目）
        bare_pattern = re.compile(r"- \[\[test-finalize\]\]\s*$", re.M)
        assert not bare_pattern.search(updated), (
            "Index entry must NOT be a bare filename without description"
        )

    def test_index_revision_line(self, finalize_vault_root: Path):
        """修订历史表追加行。"""
        from src.akb2.quality.promote import _add_index_revision_line

        index_path = finalize_vault_root / "wiki" / "sources" / "00-Index.md"
        _add_index_revision_line(index_path, "test-finalize", "gate: active")

        updated = index_path.read_text()
        assert "| 20" in updated, "Should have a date in the revision line"
        assert "test-finalize" in updated
        assert "gate: active" in updated

    def test_concept_backlink_four_columns(self, finalize_vault_root: Path):
        """概念回链四列齐: target/relation/strength/evidence。"""
        from src.akb2.quality.promote import _add_concept_backlink

        concept_path = finalize_vault_root / "wiki" / "concepts" / "concept-harness-finalize.md"
        _add_concept_backlink(
            concept_path,
            source_slug="test-finalize",
            relation="source",
            strength="strong",
            evidence="原文明示 finalize pattern",
        )

        updated = concept_path.read_text()

        # 验证四列
        backlink_pattern = re.compile(
            r"\| \[\[test-finalize\]\] \| source \| strong \| 原文明示 finalize pattern \|"
        )
        assert backlink_pattern.search(updated), (
            f"Backlink must have four columns: target|relation|strength|evidence\n"
            f"Got: {updated}"
        )

    def test_concept_backlink_dedup(self, finalize_vault_root: Path):
        """同 slug 回链去重，不重复插入。"""
        from src.akb2.quality.promote import _add_concept_backlink

        concept_path = finalize_vault_root / "wiki" / "concepts" / "concept-harness-finalize.md"

        _add_concept_backlink(concept_path, "test-finalize", "source", "strong", "test")
        after_first = concept_path.read_text()

        _add_concept_backlink(concept_path, "test-finalize", "source", "strong", "test")
        after_second = concept_path.read_text()

        # 第二次不应改变文件
        assert after_first == after_second, "Duplicate backlink should not modify file"

    def test_missing_concept_pending_queue(self, finalize_vault_root: Path):
        """概念不存在 -> 不建 wikilink，落 pending 队列（黄金样张坑 5）。"""
        from src.akb2.quality.promote import _add_concept_backlink

        # 尝试给不存在的概念加回链
        nonexistent_path = finalize_vault_root / "wiki" / "concepts" / "concept-nonexistent-missing.md"
        assert not nonexistent_path.exists(), "Concept should not exist"

        # 调用不应报错，只是静默跳过（由调用方处理 pending）
        _add_concept_backlink(
            nonexistent_path, "test-finalize", "references", "medium", "test"
        )
        # 文件不应被创建
        assert not nonexistent_path.exists(), "Nonexistent concept should NOT be created"

    def test_pending_queue_written(self, finalize_vault_root: Path, monkeypatch):
        """finalize_promotion 把缺失概念写入 pending 队列。"""
        import src.akb2.quality.promote as promote_mod

        # Mock lint 以避免真实 CLI 调用
        def mock_lint_reverify(touched, vault_root):
            return True, "mock: 0 findings"

        monkeypatch.setattr(promote_mod, "_lint_reverify", mock_lint_reverify)

        def mock_run_lint(*args, **kwargs):
            return True, "mock lint: 0 findings"

        monkeypatch.setattr(promote_mod, "_run_lint_check", mock_run_lint)

        from src.akb2.quality.promote import finalize_promotion

        candidate = finalize_vault_root / "staging" / "candidate" / "test-batch" / "test-finalize.md"

        success, detail, touched = finalize_promotion(
            candidate, finalize_vault_root, "test-snapshot-id"
        )

        assert success is True, f"Finalize should succeed: {detail}"

        # 验证 pending 队列
        pending_path = finalize_vault_root / "staging" / "pending" / "concept-backlinks.jsonl"
        assert pending_path.exists(), "Pending queue file should be created"

        pending_entries = [
            json.loads(line) for line in pending_path.read_text().strip().split("\n") if line
        ]
        missing_slugs = [e["concept_slug"] for e in pending_entries]
        assert "concept-nonexistent-missing" in missing_slugs, (
            f"Nonexistent concept should be in pending queue. Got: {missing_slugs}"
        )

    def test_maintenance_record_append(self, finalize_vault_root: Path):
        """维护记录追加一行。"""
        from src.akb2.quality.promote import _add_maintenance_record

        active_path = finalize_vault_root / "wiki" / "sources" / "test-finalize-maintenance.md"
        active_path.write_text("""---
page_status: active
slug: test-finalize-maintenance
---

## TLDR

Test page.

## 维护记录

| 2026-06-13 | Initial creation | |""")

        _add_maintenance_record(active_path, "Promoted to active", "gate: active")

        updated = active_path.read_text()
        assert "Promoted to active" in updated
        assert "gate: active" in updated

    def test_maintenance_record_merge_same_day(self, finalize_vault_root: Path):
        """维护记录超 5 条先合并同日（黄金样张坑 4）。"""
        from src.akb2.quality.promote import _add_maintenance_record

        # 创建已有 5 条维护记录的文件
        maintenance_path = finalize_vault_root / "wiki" / "sources" / "test-overflow.md"
        lines = [
            "---",
            "page_status: active",
            "slug: test-overflow",
            "---",
            "",
            "## TLDR",
            "",
            "Test overflow page.",
            "",
            "## 维护记录",
            "",
            "| 2026-06-13 | Entry 1 | detail 1 |",
            "| 2026-06-13 | Entry 2 | detail 2 |",
            "| 2026-06-12 | Entry 3 | detail 3 |",
            "| 2026-06-12 | Entry 4 | detail 4 |",
            "| 2026-06-11 | Entry 5 | detail 5 |",
        ]
        maintenance_path.write_text("\n".join(lines))

        _add_maintenance_record(maintenance_path, "New entry", "new detail")

        updated = maintenance_path.read_text()

        # 同日条目应被合并
        # 2026-06-13 的 Entry 1 和 Entry 2 应被合并为一行
        merged_pattern = re.compile(r"merged", re.I)
        assert merged_pattern.search(updated) or "(merged)" in updated or "Entry 1; Entry 2" in updated, (
            f"Same-day entries should be merged when >5. Got:\n{updated}"
        )

    def test_staging_candidate_preserved(self, finalize_vault_root: Path, monkeypatch):
        """staging 候选保留不删除。"""
        import src.akb2.quality.promote as promote_mod

        def mock_lint_reverify(touched, vault_root):
            return True, "mock: 0 findings"

        monkeypatch.setattr(promote_mod, "_lint_reverify", mock_lint_reverify)

        from src.akb2.quality.promote import finalize_promotion

        candidate = finalize_vault_root / "staging" / "candidate" / "test-batch" / "test-finalize.md"

        success, detail, touched = finalize_promotion(
            candidate, finalize_vault_root, "test-snapshot-id"
        )

        assert success is True
        # 候选页应仍在 staging
        assert candidate.exists(), "Candidate must remain in staging after finalize"

    def test_active_page_created(self, finalize_vault_root: Path, monkeypatch):
        """升格后在 wiki/sources/ 创建 active 页。"""
        import src.akb2.quality.promote as promote_mod

        def mock_lint_reverify(touched, vault_root):
            return True, "mock: 0 findings"

        monkeypatch.setattr(promote_mod, "_lint_reverify", mock_lint_reverify)

        from src.akb2.quality.promote import finalize_promotion

        candidate = finalize_vault_root / "staging" / "candidate" / "test-batch" / "test-finalize.md"

        success, detail, touched = finalize_promotion(
            candidate, finalize_vault_root, "test-snapshot-id"
        )

        assert success is True, f"Finalize should succeed: {detail}"

        active_path = finalize_vault_root / "wiki" / "sources" / "test-finalize.md"
        assert active_path.exists(), "Active page should be created in wiki/sources/"

        active_text = active_path.read_text()
        assert "page_status: active" in active_text
        assert "gate_decision: active" in active_text
        assert "## TLDR" in active_text

    def test_lint_reverify_failure_triggers_rollback(self, finalize_vault_root: Path, monkeypatch):
        """lint 复验非 0 即整体失败。"""
        import src.akb2.quality.promote as promote_mod

        def mock_lint_reverify(touched, vault_root):
            return False, "mock: lint re-verify failed with 2 P1 findings"

        monkeypatch.setattr(promote_mod, "_lint_reverify", mock_lint_reverify)

        from src.akb2.quality.promote import finalize_promotion

        candidate = finalize_vault_root / "staging" / "candidate" / "test-batch" / "test-finalize.md"

        success, detail, touched = finalize_promotion(
            candidate, finalize_vault_root, "test-snapshot-id"
        )

        assert success is False, "Finalize should fail when lint re-verify fails"
        assert "lint re-verify failed" in detail.lower() or "roll" in detail.lower()


class TestMachineReviewerSignature:
    """机器 reviewer 升格签名（2026-06-17 修宪，CONTENT-LIFECYCLE §6.1 + §7 第 8 条）。"""

    def test_flip_writes_machine_reviewer(self):
        """五闸升 active 时写全 reviewer + reviewed_at；gate_reason（distill 已写）保留。"""
        from src.akb2.quality.promote import MACHINE_REVIEWER, _flip_frontmatter

        candidate = (
            "---\n"
            "slug: test-x\n"
            "status: candidate\n"
            "gate_decision: null\n"
            "gate_reason: ok\n"
            "---\n\n# body\n"
        )
        out = _flip_frontmatter(candidate, "test-x")
        assert "status: active" in out
        assert "gate_decision: active" in out
        assert f"reviewer: {MACHINE_REVIEWER}" in out
        assert re.search(r"^reviewed_at: \d{4}-\d{2}-\d{2}$", out, re.M)
        assert "gate_reason: ok" in out

    def test_flip_replaces_stale_reviewer_no_dup(self):
        """已有 reviewer/reviewed_at 时替换为机器 reviewer，不重复。"""
        from src.akb2.quality.promote import MACHINE_REVIEWER, _flip_frontmatter

        candidate = (
            "---\n"
            "status: candidate\n"
            "reviewer: stale\n"
            "reviewed_at: 2020-01-01\n"
            "---\n\n# body\n"
        )
        out = _flip_frontmatter(candidate, "x")
        assert out.count("reviewer:") == 1
        assert f"reviewer: {MACHINE_REVIEWER}" in out
        assert "2020-01-01" not in out
