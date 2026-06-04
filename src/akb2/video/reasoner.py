"""知识图谱概念对照器 — Reasoner。

将 Extractor 的概念列表对照 AKB 现有概念图：
- 命中概念 → 判断 relationship_type（12 枚举）
- 未命中概念 → 创建 proposed_graph_nodes
- 关系的 evidence 指向报告中的具体段落

核心设计：不存在的概念也不丢，作为候选节点留待 reviewer 确认。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.akb2.video.llm_adapter import LLMAdapter, extract_json_from_llm_response

logger = logging.getLogger("akb2.video.reasoner")


# ---------------------------------------------------------------------------
# 合法 relationship_type 枚举
# ---------------------------------------------------------------------------

ALLOWED_RELATIONSHIP_TYPES = frozenset({
    "evaluates",
    "extends",
    "contradicts",
    "implements",
    "compares",
    "references",
    "derives_from",
    "inspires",
    "depends_on",
    "supersedes",
    "integrates_with",
    "alternatives_to",
})


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class RelatedConcept:
    """已命中的概念链接。"""
    concept_id: str
    relationship_type: str  # 必须是 ALLOWED_RELATIONSHIP_TYPES 之一
    evidence: str  # 指向报告中的具体段落


@dataclass
class RelatedEntity:
    """已命中的实体链接。"""
    entity_id: str
    relationship_type: str
    evidence: str


@dataclass
class ProposedNode:
    """未命中概念 → 候选图节点。"""
    name: str
    type: str  # "tool" | "concept" | "entity" | "pattern"
    reason: str  # 为什么建议加入知识图谱


@dataclass
class ReasonedLinks:
    """Reasoner 的完整输出。"""
    related_concept: list[RelatedConcept] = field(default_factory=list)
    related_entity: list[RelatedEntity] = field(default_factory=list)
    proposed_graph_nodes: list[ProposedNode] = field(default_factory=list)
    degraded: bool = False  # 概念图查询不可用时为 True


# ---------------------------------------------------------------------------
# 概念图存储接口
# ---------------------------------------------------------------------------


class ConceptStore:
    """AKB 概念图查询接口。

    当前为最小实现：内存字典存储。后续可替换为 AKB2 矩阵/概念图模块。
    """

    def __init__(self, concepts: dict[str, dict] | None = None):
        self._concepts = concepts or {}

    def lookup(self, name: str) -> dict | None:
        """按名称查找概念。返回 {"id": ..., "type": ..., "name": ...} 或 None。"""
        # 精确匹配
        if name.lower() in self._concepts:
            return self._concepts[name.lower()]
        # 模糊匹配
        for key, val in self._concepts.items():
            if key in name.lower() or name.lower() in key:
                return val
        return None

    def add_concept(self, name: str, concept_type: str = "concept"):
        """添加概念到存储（用于测试）。"""
        self._concepts[name.lower()] = {
            "id": f"concept:{name.lower()}",
            "name": name,
            "type": concept_type,
        }

    def __len__(self) -> int:
        return len(self._concepts)


# ---------------------------------------------------------------------------
# Reasoner
# ---------------------------------------------------------------------------


class Reasoner:
    """概念图对照器 — 将 Extractor 概念列表对照 AKB 知识图谱。"""

    def __init__(
        self,
        concept_store: ConceptStore | None = None,
        llm_adapter: LLMAdapter | None = None,
    ):
        self._store = concept_store or ConceptStore()
        self._llm = llm_adapter

    def reason(
        self,
        concepts_mentioned: list[str],
        report_context: str = "",
    ) -> ReasonedLinks:
        """对照概念列表与 AKB 知识图谱。

        Args:
            concepts_mentioned: Extractor 提取的概念名称列表。
            report_context: 报告摘要（用于 LLM 辅助判断关系类型）。

        Returns:
            ReasonedLinks 含 related_concept、proposed_graph_nodes。
        """
        related: list[RelatedConcept] = []
        proposed: list[ProposedNode] = []
        degraded = False

        if not self._store:
            degraded = True

        for concept_name in concepts_mentioned:
            if not concept_name.strip():
                continue

            match = self._store.lookup(concept_name) if not degraded else None

            if match:
                rel_type = self._determine_relationship(
                    concept_name, match, report_context
                )
                evidence = self._build_evidence(concept_name, rel_type, report_context)
                related.append(RelatedConcept(
                    concept_id=match.get("id", f"concept:{concept_name}"),
                    relationship_type=rel_type,
                    evidence=evidence,
                ))
            else:
                proposed.append(ProposedNode(
                    name=concept_name,
                    type=self._infer_node_type(concept_name),
                    reason=f"报告中提到 {concept_name}，当前 AKB 概念图未收录",
                ))

        if degraded:
            logger.info("Reasoner: 概念图不可用，所有概念进 proposed_graph_nodes")

        return ReasonedLinks(
            related_concept=related,
            proposed_graph_nodes=proposed,
            degraded=degraded,
        )

    # ------------------------------------------------------------------
    # 关系类型判断
    # ------------------------------------------------------------------

    def _determine_relationship(
        self, concept_name: str, match: dict, report_context: str
    ) -> str:
        """判断报告中的概念与 AKB 已有概念的关系类型。

        优先 LLM 辅助分类，规则兜底。
        """
        # 规则兜底（基于报告内容中的关键词）
        ctx_lower = report_context.lower()
        name_lower = concept_name.lower()

        if any(kw in ctx_lower for kw in ["对比", "benchmark", "evaluat", "测试"]):
            if name_lower in ctx_lower:
                return "evaluates"
        if any(kw in ctx_lower for kw in ["基于", "基于", "扩展", "extend", "build on"]):
            if name_lower in ctx_lower:
                return "extends"
        if any(kw in ctx_lower for kw in ["实现", "implement", "用.*构建"]):
            return "implements"
        if any(kw in ctx_lower for kw in ["替代", "取代", "replac", "supersede"]):
            return "alternatives_to"

        # LLM 辅助分类
        if self._llm and report_context:
            try:
                return self._llm_classify_relationship(concept_name, report_context)
            except Exception as e:
                logger.warning(f"LLM 关系分类失败: {e}")

        return "references"  # 默认

    def _llm_classify_relationship(
        self, concept_name: str, report_context: str
    ) -> str:
        """用 LLM 判断关系类型。"""
        prompt = (
            f"概念: {concept_name}\n\n"
            f"报告上下文: {report_context[:2000]}\n\n"
            f"判断报告与这个概念的关系类型。合法类型: {', '.join(sorted(ALLOWED_RELATIONSHIP_TYPES))}\n"
            '输出 JSON: {{"relationship_type": "xxx", "reason": "..."}}'
        )
        resp = self._llm.chat(
            task="classify",
            system="你是一个知识图谱关系分类助手。只输出 JSON。",
            user=prompt,
            temperature=0.1,
        )
        data = extract_json_from_llm_response(resp)
        if data:
            rel_type = data.get("relationship_type", "references")
            if rel_type in ALLOWED_RELATIONSHIP_TYPES:
                return rel_type
        return "references"

    # ------------------------------------------------------------------
    # Evidence 构建
    # ------------------------------------------------------------------

    def _build_evidence(
        self, concept_name: str, rel_type: str, report_context: str
    ) -> str:
        """从报告上下文构建 evidence 描述。"""
        if report_context:
            # 找含概念名的句子
            for line in report_context.split("\n"):
                if concept_name.lower() in line.lower():
                    return line.strip()[:300]
        return f"报告中将 {concept_name} 关系标记为 {rel_type}"

    # ------------------------------------------------------------------
    # 节点类型推断
    # ------------------------------------------------------------------

    def _infer_node_type(self, name: str) -> str:
        """推断 proposed node 的类型。"""
        # 工具类关键词
        tool_keywords = ["cli", "sdk", "api", "tool", "plugin", "framework", "library"]
        if any(kw in name.lower() for kw in tool_keywords):
            return "tool"
        # 实体类关键词
        entity_keywords = ["company", "lab", "team", "org", "inc", "ltd"]
        if any(kw in name.lower() for kw in entity_keywords):
            return "entity"
        # 模式类关键词
        pattern_keywords = ["pattern", "architecture", "design", "workflow", "pipeline"]
        if any(kw in name.lower() for kw in pattern_keywords):
            return "pattern"
        return "concept"