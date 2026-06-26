"""NLM 报告结构化提取器 — Extractor。

将 NLM briefing-doc 报告转为 gate-compliant 结构化字段：
- TLDR（从"执行摘要"提取或 LLM 精炼）
- 关键发现（含 source_span 行号引用）
- future_questions（gap-analysis 模式，非 question-generation）
- 概念列表（用于 Reasoner 对照）

核心设计：每个 gate 字段都锚定到报告的特定位置（source_span），
解决泛化填表问题。反事实防御：对空/随机/非技术输入不产出 finding。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.akb2.video.llm_adapter import LLMAdapter, extract_json_from_llm_response

logger = logging.getLogger("akb2.video.extractor")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SourceSpan:
    """报告中 source_span 的行号范围。"""
    line_start: int
    line_end: int


@dataclass
class KeyFinding:
    """关键发现 — 含断言 + source_span 行号引用 + 置信度。"""
    assertion: str
    source_span: SourceSpan
    confidence: float  # 0.0-1.0
    category: str = ""  # 如 "benchmark", "architecture", "limitation"


@dataclass
class FutureQuestion:
    """future_question — 从报告裂缝中提取（gap-analysis），非凭空生成。"""
    question: str
    gap_anchor_span: SourceSpan
    gap_type: str  # "condition_limit" | "missing_comparison" | "unexplored_concept"


@dataclass
class ExtractedFields:
    """Extractor 的完整输出。"""
    tldr: str = ""
    tldr_source_span: SourceSpan | None = None
    key_findings: list[KeyFinding] = field(default_factory=list)
    future_questions: list[FutureQuestion] = field(default_factory=list)
    evidence_paths: list[str] = field(default_factory=list)
    concepts_mentioned: list[str] = field(default_factory=list)

    # 交叉验证结果（SC-5）
    cross_validation_results: list[dict] = field(default_factory=list)
    """每个 key_finding 的交叉验证结果: [{finding_index, claim, source_text, verdict, reason}]"""


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class Extractor:
    """NLM 报告 → gate 字段提取器。"""

    # gap_type 枚举
    GAP_CONDITION_LIMIT = "condition_limit"
    GAP_MISSING_COMPARISON = "missing_comparison"
    GAP_UNEXPLORED_CONCEPT = "unexplored_concept"

    MAX_FUTURE_QUESTIONS = 3

    def __init__(self, llm_adapter: LLMAdapter | None = None):
        self._llm = llm_adapter

    def extract(self, report_markdown: str) -> ExtractedFields:
        """从 NLM 报告提取结构化字段。

        Args:
            report_markdown: NLM briefing-doc 报告正文（markdown）。

        Returns:
            ExtractedFields 含 TLDR、关键发现、future_questions、概念列表。
        """
        lines = report_markdown.split("\n")

        # 反事实防御：空/过短/非技术输入
        if not report_markdown.strip() or len(lines) < 5:
            logger.info("Extractor: 输入为空或过短，返回空 fields")
            return ExtractedFields()

        if self._is_non_technical(report_markdown):
            logger.info("Extractor: 输入为非技术内容，返回空 fields")
            return ExtractedFields()

        # 1. TLDR 提取
        tldr, tldr_span = self._extract_tldr(lines)

        # 2. 关键发现提取
        key_findings = self._extract_key_findings(lines, report_markdown)

        # 3. future_questions（gap-analysis）
        future_questions = self._extract_future_questions(lines, report_markdown)

        # 4. 概念提取
        concepts = self._extract_concepts(report_markdown)

        # 5. 交叉验证（SC-5）
        cross_validation = self._cross_validate(key_findings, lines)

        return ExtractedFields(
            tldr=tldr,
            tldr_source_span=tldr_span,
            key_findings=key_findings,
            future_questions=future_questions[:self.MAX_FUTURE_QUESTIONS],
            concepts_mentioned=concepts,
            cross_validation_results=cross_validation,
        )

    # ------------------------------------------------------------------
    # 反事实防御
    # ------------------------------------------------------------------

    def _is_non_technical(self, text: str) -> bool:
        """判断输入是否为非技术内容（天气预报、食谱等）。"""
        # 快速关键词检测
        non_tech_patterns = [
            r"天气预报", r"今日菜谱", r"炒股", r"彩票",
            r"娱乐八卦", r"明星", r"综艺",
        ]
        for pattern in non_tech_patterns:
            if re.search(pattern, text):
                return True
        return False

    # ------------------------------------------------------------------
    # TLDR 提取
    # ------------------------------------------------------------------

    def _extract_tldr(self, lines: list[str]) -> tuple[str, SourceSpan | None]:
        """从报告提取 TLDR。

        优先级：H2 "执行摘要" → H2 "摘要" → 正文前 500 字符 LLM 精炼。
        """
        # 找 H2 "执行摘要" 或 "摘要" 段落
        for i, line in enumerate(lines):
            s = line.strip()
            if re.match(r"^##\s+(执行摘要|摘要|TLDR|Executive Summary)", s, re.IGNORECASE):
                # 取该 H2 后的段落（直到下一个 H2）
                content_lines = []
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith("## "):
                    content_lines.append(lines[j])
                    j += 1
                tldr_text = "\n".join(content_lines).strip()
                if tldr_text:
                    return tldr_text[:500], SourceSpan(line_start=i + 1, line_end=j)

        # Fallback: 正文前 500 字符
        body = "\n".join(lines)
        # 跳过 frontmatter
        if body.startswith("---"):
            parts = body.split("---", 2)
            if len(parts) >= 3:
                body = parts[2]
        return body[:500].strip(), None

    # ------------------------------------------------------------------
    # 关键发现提取
    # ------------------------------------------------------------------

    def _extract_key_findings(
        self, lines: list[str], full_text: str
    ) -> list[KeyFinding]:
        """从报告提取关键发现。

        遍历 H2/H3 section，提取含数据的断言（数字/对比/结论）。
        优先用 LLM 提取，LLM 不可用时用规则兜底。
        """
        if self._llm:
            return self._extract_key_findings_llm(lines, full_text)
        return self._extract_key_findings_rules(lines)

    def _extract_key_findings_llm(
        self, lines: list[str], full_text: str
    ) -> list[KeyFinding]:
        """用 LLM 提取关键发现（带 source_span）。"""
        prompt = (
            "从以下技术报告中提取 3-5 个关键发现。\n"
            "每个发现必须包含：\n"
            '1. assertion: 断言内容（1-2 句）\n'
            '2. source_span: 该断言在报告中的行号范围 {{"line_start": N, "line_end": M}}\n'
            "3. confidence: 0.0-1.0 置信度\n\n"
            "只提取有具体数据/对比/结论的断言。不要编造。\n\n"
            "报告内容（每行前有行号）:\n"
        )
        numbered = "\n".join(f"{i + 1:4d}| {line}" for i, line in enumerate(lines))
        prompt += numbered[:12000]  # 截断防止超 token

        try:
            resp = self._llm.chat(
                task="extract",
                system="你是一个技术报告分析助手。只提取有原文依据的断言，不编造。输出 JSON。",
                user=prompt,
                temperature=0.1,
            )
            data = extract_json_from_llm_response(resp)
            if not data:
                return self._extract_key_findings_rules(lines)

            findings = data.get("findings") or data.get("key_findings") or []
            result = []
            for f in findings:
                span_data = f.get("source_span", {})
                span = SourceSpan(
                    line_start=span_data.get("line_start", 1),
                    line_end=span_data.get("line_end", 1),
                )
                # 校验 source_span 在报告行数范围内
                if span.line_start < 1 or span.line_end > len(lines):
                    span.line_start = max(1, min(span.line_start, len(lines)))
                    span.line_end = max(1, min(span.line_end, len(lines)))

                finding = KeyFinding(
                    assertion=f.get("assertion", ""),
                    source_span=span,
                    confidence=float(f.get("confidence", 0.5)),
                    category=f.get("category", ""),
                )
                if finding.assertion.strip():
                    result.append(finding)
            return result[:5]
        except Exception as e:
            logger.warning(f"LLM 关键发现提取失败: {e}")
            return self._extract_key_findings_rules(lines)

    def _extract_key_findings_rules(self, lines: list[str]) -> list[KeyFinding]:
        """规则兜底：从 H2/H3 section 提取含数字/对比的句子。"""
        findings = []
        for i, line in enumerate(lines):
            s = line.strip()
            # 跳过标题行
            if s.startswith("#"):
                continue
            # 找含数字的句子
            if re.search(r"\d+%|\d+倍|\d+\.\d+|\d+\s*(亿|万|倍|ms|GB|TB)", s):
                if len(s) > 20:
                    findings.append(KeyFinding(
                        assertion=s[:300],
                        source_span=SourceSpan(line_start=i + 1, line_end=i + 1),
                        confidence=0.5,
                    ))
        return findings[:5]

    # ------------------------------------------------------------------
    # future_questions 提取（gap-analysis 模式）
    # ------------------------------------------------------------------

    def _extract_future_questions(
        self, lines: list[str], full_text: str
    ) -> list[FutureQuestion]:
        """从报告裂缝中提取 future_questions（gap-analysis 模式）。

        找 3 类 gap：
        1. 限定条件句（"在 X 条件下""当前 benchmark 下"）
        2. 对比缺失（"X 声称优于 Y 但未提供同等条件数据"）
        3. 提到但不深入的概念（"值得一提但未展开"）
        """
        questions = []

        # 规则扫描：找限定条件句和对比缺失
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("#"):
                continue

            # 类型 1: 限定条件句
            if re.search(r"在.*条件|当前.*benchmark|在.*窗口内|仅.*测试", s):
                q = self._build_question_from_gap(s, "condition_limit")
                if q:
                    questions.append(FutureQuestion(
                        question=q,
                        gap_anchor_span=SourceSpan(line_start=i + 1, line_end=i + 1),
                        gap_type=self.GAP_CONDITION_LIMIT,
                    ))

            # 类型 2: 对比缺失
            if re.search(r"声称|优于|但.*未|缺少|未提供|无.*数据", s):
                q = self._build_question_from_gap(s, "missing_comparison")
                if q:
                    questions.append(FutureQuestion(
                        question=q,
                        gap_anchor_span=SourceSpan(line_start=i + 1, line_end=i + 1),
                        gap_type=self.GAP_MISSING_COMPARISON,
                    ))

        # 类型 3: 提到但不深入的概念（用 LLM 找）
        if self._llm and len(questions) < self.MAX_FUTURE_QUESTIONS:
            llm_questions = self._extract_gaps_llm(full_text, lines)
            questions.extend(llm_questions)

        # 去重 + 硬上限
        seen = set()
        unique = []
        for q in questions:
            if q.question not in seen:
                seen.add(q.question)
                unique.append(q)
        return unique[:self.MAX_FUTURE_QUESTIONS]

    def _build_question_from_gap(self, text: str, gap_type: str) -> str | None:
        """从 gap 句子构建具体问题。"""
        # 泛化问题黑名单
        generic_patterns = [
            r"未来趋势", r"发展方向", r"前景如何", r"会怎样",
            r"前景", r"趋势", r"发展",
        ]
        text_clean = text[:200]

        if gap_type == "condition_limit":
            return f"该结论在更广泛条件下是否仍然成立？原文提示: {text_clean}"
        elif gap_type == "missing_comparison":
            return f"缺少的对比数据在什么条件下可获得？原文提示: {text_clean}"
        return None

    def _extract_gaps_llm(
        self, full_text: str, lines: list[str]
    ) -> list[FutureQuestion]:
        """用 LLM 找 gap（未深入概念）。"""
        prompt = (
            "从以下技术报告中找出 1-2 个\"提到但未深入展开\"的概念。\n"
            "对每个概念，写一个具体的研究问题（不是泛化的\"未来趋势\"）。\n"
            "输出 JSON: {\"gaps\": [{\"question\": \"...\", \"line_number\": N}]}\n\n"
            + full_text[:8000]
        )
        try:
            resp = self._llm.chat(
                task="extract",
                system="只找报告中提到但未深入的概念。输出 JSON。",
                user=prompt,
                temperature=0.2,
            )
            data = extract_json_from_llm_response(resp)
            if not data:
                return []
            gaps = data.get("gaps") or []
            result = []
            for g in gaps:
                q = g.get("question", "")
                if not q or any(kw in q for kw in ["未来趋势", "发展方向", "前景如何"]):
                    continue
                ln = g.get("line_number", 1)
                result.append(FutureQuestion(
                    question=q,
                    gap_anchor_span=SourceSpan(
                        line_start=max(1, ln),
                        line_end=min(len(lines), ln + 1),
                    ),
                    gap_type=self.GAP_UNEXPLORED_CONCEPT,
                ))
            return result
        except Exception as e:
            logger.warning(f"LLM gap 提取失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 概念提取
    # ------------------------------------------------------------------

    def _extract_concepts(self, text: str) -> list[str]:
        """从报告提取技术概念名称列表。"""
        if self._llm:
            try:
                prompt = (
                    "从以下技术报告中提取提到的技术概念名称列表（如模型名、框架名、协议名）。\n"
                    "输出 JSON: {\"concepts\": [\"concept1\", \"concept2\", ...]}\n\n"
                    + text[:6000]
                )
                resp = self._llm.chat(
                    task="extract",
                    system="提取技术概念名称。输出 JSON。",
                    user=prompt,
                    temperature=0.1,
                )
                data = extract_json_from_llm_response(resp)
                if data:
                    concepts = data.get("concepts") or []
                    return list(dict.fromkeys(concepts))[:20]  # 去重 + 上限
            except Exception as e:
                logger.warning(f"LLM 概念提取失败: {e}")

        # 规则兜底：匹配大写缩写、驼峰命名
        patterns = [
            r"\b[A-Z]{2,8}\b",  # LLM, MCP, GPT, RAG
            r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b",  # LangGraph, DeepSeek
        ]
        concepts = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                concepts.add(match.group(0))
        return list(concepts)[:20]

    # ------------------------------------------------------------------
    # 交叉验证（SC-5）
    # ------------------------------------------------------------------

    def _cross_validate(
        self, findings: list[KeyFinding], lines: list[str]
    ) -> list[dict]:
        """对每个 key_finding 做交叉验证：用 source_span 取回原文 → LLM 判断。"""
        results = []
        for idx, finding in enumerate(findings):
            # 取回 source_span 对应的原文
            span = finding.source_span
            source_lines = lines[span.line_start - 1:span.line_end]
            source_text = "\n".join(source_lines).strip()

            if not source_text or not self._llm:
                results.append({
                    "finding_index": idx,
                    "claim": finding.assertion,
                    "source_text": source_text,
                    "verdict": "unverified",
                    "reason": "no source text or no LLM",
                })
                continue

            try:
                prompt = (
                    "判断以下断言是否被原文支持。\n\n"
                    f"断言: {finding.assertion}\n\n"
                    f"原文: {source_text}\n\n"
                    '输出 JSON: {{"verdict": "supported|partially|unsupported", "reason": "..."}}'
                )
                resp = self._llm.chat(
                    task="validate",
                    system="你是一个严格的事实核查员。只输出 JSON。",
                    user=prompt,
                    temperature=0.0,
                )
                data = extract_json_from_llm_response(resp)
                results.append({
                    "finding_index": idx,
                    "claim": finding.assertion,
                    "source_text": source_text,
                    "verdict": data.get("verdict", "unverified") if data else "unverified",
                    "reason": data.get("reason", "") if data else "LLM response parse failed",
                })
            except Exception as e:
                results.append({
                    "finding_index": idx,
                    "claim": finding.assertion,
                    "source_text": source_text,
                    "verdict": "unverified",
                    "reason": f"cross-validation error: {e}",
                })

        return results
