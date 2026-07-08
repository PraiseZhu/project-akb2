"""test_f33_finalize_fixes.py — F33 finalize 三连修行为测试。

replit 首篇 auto-active 尸检(2026-07-08T11:59 账本)钉出的三个确定性 bug:
  Bug-A snapshot 只记既存文件 → 回滚不删 promote 新建文件 → active 孤儿残留
  Bug-B auto_active(graph_pending)路径 related_concepts 恒空 → R14 必炸 +
        pending 概念队列(修宪图谱兜底链路唯一进水口)永远断供
  Bug-C R6/R7/R11 对 00-Index.md(导航文件)误报既有 P1 → finalize 复验连坐,
        修宪 auto_active 路径确定性全阻断

标准零点不动:R14 语义/阈值不变;R7/R11 补 active 守卫与 F8-B 判例同构;
R6 仅豁免索引文件(内容页行为零变化);related 回填只取候选自身
proposed_graph_nodes(Gate-3 proposed_node_only 保证非空,非编造),
材料为空时保持空 → R14 照常阻断(fail-closed)。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.akb2.lint.akb2_lint import (
    rule_r6_body_quality_missing_structure,
    rule_r7_source_grounded_missing_field,
    rule_r11_graph_role_missing,
    rule_r14_active_missing_related,
)
from src.akb2.quality import promote
from src.akb2.quality.promote import (
    _flip_frontmatter,
    _inject_related_concepts,
    _parse_proposed_graph_node_labels,
    rollback_snapshot,
    snapshot_before_promote,
)

# ---------------------------------------------------------------------------
# Bug-A: 回滚删除 promote 期间新建的文件
# ---------------------------------------------------------------------------


class TestSnapshotRollbackCreatedFiles:
    def _vault(self, tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
        monkeypatch.setattr(promote, "_SNAPSHOTS_DIR", tmp_path / "snapshots")
        vault = tmp_path / "vault"
        (vault / "wiki" / "sources").mkdir(parents=True)
        existing = vault / "wiki" / "sources" / "00-Index.md"
        existing.write_text("old index\n", encoding="utf-8")
        new_active = vault / "wiki" / "sources" / "new-page.md"
        return vault, existing, new_active

    def test_created_file_recorded_in_manifest(self, tmp_path, monkeypatch) -> None:
        vault, existing, new_active = self._vault(tmp_path, monkeypatch)
        sid = snapshot_before_promote(vault, [existing, new_active])
        manifest = json.loads(
            (promote._SNAPSHOTS_DIR / sid / "rollback-manifest.json").read_text()
        )
        assert manifest["created_files"] == ["wiki/sources/new-page.md"]
        assert "wiki/sources/new-page.md" not in manifest["files"]

    def test_rollback_deletes_created_and_restores_existing(
        self, tmp_path, monkeypatch
    ) -> None:
        """孤儿场景复现:promote 新建 active 页 + 改 index → 回滚后两者都还原。"""
        vault, existing, new_active = self._vault(tmp_path, monkeypatch)
        sid = snapshot_before_promote(vault, [existing, new_active])
        existing.write_text("index + summary line\n", encoding="utf-8")
        new_active.write_text("---\nstatus: active\n---\nbody\n", encoding="utf-8")

        assert rollback_snapshot(sid) is True
        assert existing.read_text(encoding="utf-8") == "old index\n"
        assert not new_active.exists(), "F33 Bug-A: 回滚必须删除 promote 期间新建文件"

    def test_legacy_manifest_without_created_files_key(
        self, tmp_path, monkeypatch
    ) -> None:
        """旧快照 manifest 无 created_files key → 回滚兼容不炸。"""
        vault, existing, _ = self._vault(tmp_path, monkeypatch)
        sid = snapshot_before_promote(vault, [existing])
        mpath = promote._SNAPSHOTS_DIR / sid / "rollback-manifest.json"
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data.pop("created_files", None)
        mpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        existing.write_text("changed\n", encoding="utf-8")

        assert rollback_snapshot(sid) is True
        assert existing.read_text(encoding="utf-8") == "old index\n"


# ---------------------------------------------------------------------------
# Bug-B: related_concepts 回填(方案①,材料取自候选自身)
# ---------------------------------------------------------------------------

_FM_WITH_PROPOSED = (
    "status: candidate\n"
    "slug: replit-x\n"
    "related_concepts: []\n"
    "proposed_graph_nodes:\n"
    "  - slug: loop_engineering\n"
    "    label: loop-engineering\n"
    "    reason: 冷启动\n"
    "    source_anchor: §E_CLAIMS\n"
    "  - slug: quality_gate\n"
    "    label: quality-gate\n"
    "    reason: 冷启动\n"
    "    source_anchor: §T_CONFIGURATION\n"
)


class TestProposedNodeLabelParsing:
    def test_slugs_extracted_in_order(self) -> None:
        """[F33-R CONCERN-1] slug 优先(_slugify_component_id 保证单 token 机器安全)。"""
        assert _parse_proposed_graph_node_labels(_FM_WITH_PROPOSED) == [
            "loop_engineering", "quality_gate",
        ]

    def test_label_fallback_when_slug_missing(self) -> None:
        fm = "proposed_graph_nodes:\n  - label: foo-bar\n    reason: x\n"
        assert _parse_proposed_graph_node_labels(fm) == ["foo-bar"]

    def test_multiword_label_not_truncated(self) -> None:
        """[F33-R CONCERN-1] 多词/带引号值全行捕获,不截断到首 token。"""
        fm = (
            "proposed_graph_nodes:\n"
            "  - label: Reinforcement Learning\n"
            "    reason: x\n"
            "  - label: 'a: b'\n"
            "    reason: y\n"
        )
        assert _parse_proposed_graph_node_labels(fm) == [
            "Reinforcement Learning", "a: b",
        ]

    def test_no_block_returns_empty(self) -> None:
        assert _parse_proposed_graph_node_labels("status: candidate\n") == []


class TestInjectRelatedConcepts:
    def test_fills_empty_inline_and_satisfies_r14(self) -> None:
        """flip 后回填 → R14 不再触发(replit 死因 1 消除)。"""
        text = f"---\n{_FM_WITH_PROPOSED}---\n\nbody\n"
        active = _flip_frontmatter(text, "replit-x")
        filled = _inject_related_concepts(
            active, ["loop-engineering", "quality-gate"]
        )
        fm = re.match(r"^---\n(.*?)\n---\n", filled, re.DOTALL).group(1)
        assert "related_concepts: [loop-engineering, quality-gate]" in fm
        assert rule_r14_active_missing_related(
            Path("replit-x.md"), fm, "", {"template_type": "source"}
        ) == []

    def test_never_overwrites_nonempty(self) -> None:
        text = "---\nstatus: active\nrelated_concepts: [real-one]\n---\nbody\n"
        assert _inject_related_concepts(text, ["other"]) == text

    def test_empty_labels_failclosed_r14_still_blocks(self) -> None:
        """材料为空 → 不硬塞,active 空 related 仍被 R14 阻断(严格性不降)。"""
        text = "---\nstatus: active\nrelated_concepts: []\n---\nbody\n"
        out = _inject_related_concepts(text, [])
        assert out == text
        fm = re.match(r"^---\n(.*?)\n---\n", out, re.DOTALL).group(1)
        findings = rule_r14_active_missing_related(
            Path("x.md"), fm, "", {"template_type": "source"}
        )
        assert findings and findings[0]["rule_id"] == "ACTIVE-MISSING-RELATED"

    def test_appends_when_field_absent(self) -> None:
        text = "---\nstatus: active\n---\nbody\n"
        out = _inject_related_concepts(text, ["a"])
        assert "related_concepts: [a]" in out


# ---------------------------------------------------------------------------
# Bug-C: lint 误报修正(R6 索引豁免;R7/R11 active 守卫,同 F8-B 判例)
# ---------------------------------------------------------------------------

_INDEX_FM = "title: Wiki Sources — 主索引\ndoc_type: index\nindex_kind: source-index\n"
_INDEX_BODY = "# 主索引\n\n- [[some-page]]\n"
_CTX_NONE = {"template_type": None}


class TestLintIndexExemptionAndActiveGuards:
    def test_r6_exempts_00_index_by_name(self) -> None:
        assert rule_r6_body_quality_missing_structure(
            Path("00-Index.md"), "title: x\n", _INDEX_BODY, _CTX_NONE
        ) == []

    def test_r6_exempts_doc_type_index(self) -> None:
        assert rule_r6_body_quality_missing_structure(
            Path("nav-page.md"), _INDEX_FM, _INDEX_BODY, _CTX_NONE
        ) == []

    def test_r6_still_fires_on_content_page(self) -> None:
        """严格性不降:普通内容页缺 9 必备节照报。"""
        findings = rule_r6_body_quality_missing_structure(
            Path("concept-x.md"), "status: active\n", "# x\n\n正文\n",
            {"template_type": "concept"},
        )
        assert findings and findings[0]["rule_id"] == "BODY-QUALITY-MISSING-STRUCTURE"

    def test_r7_r11_skip_index_without_status(self) -> None:
        """00-Index.md 无 status → 不再误报(replit 死因 2 的 index 侧消除)。"""
        assert rule_r7_source_grounded_missing_field(
            Path("00-Index.md"), _INDEX_FM, _INDEX_BODY, _CTX_NONE
        ) == []
        assert rule_r11_graph_role_missing(
            Path("00-Index.md"), _INDEX_FM, _INDEX_BODY, _CTX_NONE
        ) == []

    def test_r7_r11_skip_candidate(self) -> None:
        """candidate 冷启动缺字段是诚实状态(F8-B 同款语义)。"""
        fm = "status: candidate\n"
        assert rule_r7_source_grounded_missing_field(
            Path("c.md"), fm, "", _CTX_NONE
        ) == []
        assert rule_r11_graph_role_missing(Path("c.md"), fm, "", _CTX_NONE) == []

    def test_r7_r11_still_fire_on_active_missing(self) -> None:
        """严格性不降:active 缺字段照报 P1。"""
        fm = "status: active\n"
        r7 = rule_r7_source_grounded_missing_field(Path("a.md"), fm, "", _CTX_NONE)
        r11 = rule_r11_graph_role_missing(Path("a.md"), fm, "", _CTX_NONE)
        assert r7 and r7[0]["rule_id"] == "SOURCE-GROUNDED-MISSING-FIELD"
        assert r11 and r11[0]["rule_id"] == "GRAPH-ROLE-MISSING"
