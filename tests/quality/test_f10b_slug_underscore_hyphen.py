"""test_f10b_slug_underscore_hyphen.py — F10-B 图谱 resolve slug 口径 bug 测试。

probe-p8:4 样本图谱维度全挂 'loop_engineering/quality_gate 未 resolve 到 known/pending',
但 vault wiki 里 loop-engineering 相关条目存在(sources/addy-osmani-loop-engineering.md)。
根因:resolver 的 _slug_normalize_variants 只处理 concept- 前缀,不桥接下划线↔连字符;
_slugify_component_id 产下划线 slug(proposed node 侧),vault 文件 stem 用连字符(known 侧)
→ 精确匹配永远 miss。

F10-B:_slug_normalize_variants 加下划线↔连字符互认(两侧互认再匹配)。
只修口径,不放宽 resolve 语义(真不存在的概念仍 miss)。
"""

from __future__ import annotations

from src.akb2.quality.checks_structural import (
    _resolve_graph_node,
    _slug_normalize_variants,
)


class TestF10BUnderscoreHyphenInterchange:
    def test_underscore_target_resolves_hyphen_known(self):
        """target 'loop_engineering'(下划线)→ known ['loop-engineering'](连字符)→ resolve True。"""
        assert _resolve_graph_node("loop_engineering", ["loop-engineering"], None) is True

    def test_hyphen_target_resolves_underscore_known(self):
        """target 'loop-engineering' → known ['loop_engineering'] → resolve True。"""
        assert _resolve_graph_node("loop-engineering", ["loop_engineering"], None) is True

    def test_quality_gate_underscore_resolves(self):
        """probe-p8 'quality_gate' 未 resolve → 修后对 'quality-gate' resolve。"""
        assert _resolve_graph_node("quality_gate", ["quality-gate"], None) is True

    def test_concept_prefix_with_underscore_resolves(self):
        """concept-loop_engineering 对 concept-loop-engineering resolve(前缀+下划线)。"""
        assert _resolve_graph_node(
            "concept-loop_engineering", ["concept-loop-engineering"], None
        ) is True

    def test_truly_nonexistent_still_miss(self):
        """真不存在的概念仍 miss(不放宽 resolve 语义)。"""
        assert _resolve_graph_node("fake_concept_xyz", ["loop-engineering"], None) is False
        assert _resolve_graph_node("fake-concept-xyz", ["loop-engineering"], None) is False

    def test_different_slug_still_miss(self):
        """短 slug 对长 slug(不同 slug)仍 miss——非口径问题,是 slug 不匹配(不放宽)。"""
        # loop_engineering(短)对 addy-osmani-loop-engineering(长,不同 slug)→ miss
        # (这是 slug 不匹配,非 _↔- 口径;lead 说真不存在仍 miss,此例属不同 slug)
        assert _resolve_graph_node(
            "loop_engineering", ["addy-osmani-loop-engineering"], None
        ) is False

    def test_slug_variants_contain_both_separators(self):
        """_slug_normalize_variants 同时含下划线和连字符形式。"""
        v = _slug_normalize_variants("loop_engineering")
        assert "loop_engineering" in v
        assert "loop-engineering" in v
        # concept- 前缀形式也在
        assert "concept-loop-engineering" in v
        assert "concept-loop_engineering" in v

    def test_pending_underscore_resolves_hyphen(self):
        """pending 共指下划线/连字符互认(≥GRAPH_PENDING_COREF_MIN 个)。"""
        # 3 个 loop-engineering pending(连字符)→ target loop_engineering(下划线)resolve
        assert _resolve_graph_node(
            "loop_engineering", None, ["loop-engineering", "loop-engineering", "loop-engineering"]
        ) is True


class TestF10BNoRegression:
    def test_exact_hyphen_match_still_works(self):
        """精确连字符匹配不回归。"""
        assert _resolve_graph_node("loop-engineering", ["loop-engineering"], None) is True

    def test_concept_prefix_bare_name_still_works(self):
        """concept- 前缀互译不回归(SC-2a 既有行为)。"""
        assert _resolve_graph_node("context-engineering", ["concept-context-engineering"], None) is True

    def test_no_separator_slug_unchanged(self):
        """无分隔符 slug 不受影响。"""
        v = _slug_normalize_variants("concepta")
        assert "concepta" in v
        assert "concept-concepta" in v
