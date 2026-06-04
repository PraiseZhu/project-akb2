"""Candidate 组装器 — Assembler。

将 Collector → Researcher → Extractor → Reasoner 的产出组装为
gate-compliant candidate 文件，写入 staging/candidate/ + gate dry-run 验证。

Assembler 是本链路唯一 durable write 所有者（Researcher 不写 staging/Obsidian/DB）。
写入原子性：先写 candidate → gate dry-run → gate rejected 时回滚删除。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.akb2.video.config import VideoConfig
from src.akb2.video.researcher import ResearchOutput
from src.akb2.video.extractor import ExtractedFields
from src.akb2.video.reasoner import ReasonedLinks

logger = logging.getLogger("akb2.video.assembler")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class CandidateTargetExists(Exception):
    """candidate 文件已存在（slug/date 冲突）。"""


class GateRejected(Exception):
    """gate dry-run 拒绝 candidate。"""


class PromotionTargetNotFound(Exception):
    """L4 promotion 目标路径不存在。"""


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


class Assembler:
    """Candidate 组装器 — 唯一 durable write 所有者。"""

    def __init__(self, config: VideoConfig | None = None):
        self.config = config or VideoConfig()

    def assemble(
        self,
        video_meta: dict,
        research_output: ResearchOutput,
        extracted_fields: ExtractedFields,
        reasoned_links: ReasonedLinks,
    ) -> str:
        """组装 candidate markdown 文本（不写文件）。

        Returns:
            candidate markdown 全文。
        """
        title = video_meta.get("title", "Untitled")
        video_url = video_meta.get("url", "")
        platform = video_meta.get("platform", "youtube")
        creator = video_meta.get("creator", "")
        upload_date = video_meta.get("upload_date", "")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 从报告提取 H1 标题
        report_title = self._extract_h1(research_output.report_markdown) or title

        # 构建 frontmatter
        frontmatter = self._build_frontmatter(
            title=report_title,
            source_url=video_url,
            author=creator,
            published_at=upload_date,
            fetched_at=now,
            notebook_id=research_output.notebook_id,
            platform=platform,
            extracted_fields=extracted_fields,
            reasoned_links=reasoned_links,
            degraded=research_output.degraded,
        )

        # 构建正文（注入 gate 要求的结构化章节）
        gate_sections = self._build_gate_sections(extracted_fields)
        report_h1 = self._extract_h1(research_output.report_markdown) or report_title
        body = f"# {report_h1}\n\n{gate_sections}{research_output.report_markdown}"

        # 完整的 candidate markdown
        candidate_md = frontmatter + "\n" + body

        return candidate_md

    def commit(
        self,
        candidate_md: str,
        slug: str | None = None,
        date_str: str | None = None,
        staging_dir: Path | None = None,
    ) -> Path:
        """写 staging 中转 → gate 决策 → active 发布到 AI TRENDS/Video，非 active 丢弃。

        发布流程：
        1. 写 staging/candidate/video-{slug}-{date}.md（中转）
        2. _gate_decision → proposed_gate_decision
        3. active：copy 到 promotion_target_path，删 staging 中转，返回正式路径
        4. 非 active：删 staging 中转 + warning 日志 + raise GateRejected
        5. promotion 目录不存在 → raise PromotionTargetNotFound（删中转）
        6. 正式路径同名已存在 → raise CandidateTargetExists（fail-closed，删中转）

        Args:
            candidate_md: 完整的 candidate markdown。
            slug: 文件名 slug（默认从标题生成）。
            date_str: 日期字符串（默认今天）。
            staging_dir: staging 目录（默认从 config）。

        Returns:
            正式发布路径（AI TRENDS/Video/video-{slug}-{date}.md）。

        Raises:
            CandidateTargetExists: staging 或正式路径同名已存在。
            GateRejected: gate 判非 active（含 parse_error）。
            PromotionTargetNotFound: promotion 目标路径不存在。
        """
        date = date_str or datetime.now().strftime("%Y-%m-%d")
        slug = slug or self._make_slug(candidate_md)
        staging = staging_dir or self.config.staging_dir

        staging.mkdir(parents=True, exist_ok=True)

        filename = f"video-{slug}-{date}.md"
        candidate_path = staging / filename

        if candidate_path.exists():
            raise CandidateTargetExists(
                f"candidate 已存在: {candidate_path}（slug/date 冲突）"
            )

        candidate_path.write_text(candidate_md, encoding="utf-8")
        logger.info(f"staging 中转写入: {candidate_path}")

        decision = self._gate_decision(candidate_path, staging)

        if decision != "active":
            candidate_path.unlink(missing_ok=True)
            logger.warning(f"gate 拒绝发布 (decision={decision}): {filename}")
            raise GateRejected(decision)

        promotion_dir = self.config.promotion_target_path
        if not promotion_dir.is_dir():
            candidate_path.unlink(missing_ok=True)
            raise PromotionTargetNotFound(
                f"promotion 目标路径不存在: {promotion_dir}"
            )

        promotion_path = promotion_dir / filename
        if promotion_path.exists():
            candidate_path.unlink(missing_ok=True)
            raise CandidateTargetExists(
                f"promotion 目标已存在: {promotion_path}（fail-closed，不覆盖）"
            )

        shutil.copy2(str(candidate_path), str(promotion_path))
        candidate_path.unlink(missing_ok=True)

        # 正式文件标记已发布：gate_decision null → active
        formal_md = promotion_path.read_text(encoding="utf-8")
        formal_md = formal_md.replace("gate_decision: null", "gate_decision: active", 1)
        promotion_path.write_text(formal_md, encoding="utf-8")

        logger.info(f"已发布到 AI TRENDS/Video: {promotion_path}")

        return promotion_path

    # ------------------------------------------------------------------
    # Frontmatter 构建
    # ------------------------------------------------------------------

    def _build_frontmatter(
        self,
        title: str,
        source_url: str,
        author: str,
        published_at: str,
        fetched_at: str,
        notebook_id: str | None,
        platform: str,
        extracted_fields: ExtractedFields,
        reasoned_links: ReasonedLinks,
        degraded: bool = False,
    ) -> str:
        """构建 gate-compliant candidate frontmatter（HC-01~HC-09）。"""
        lines = ["---"]

        # HC-02: page_status
        lines.append("page_status: candidate")
        # HC-03: source_type
        lines.append("source_type: video")
        # HC-04: template_type
        lines.append("template_type: source")

        # 标题
        lines.append(f'title: "{title}"')
        lines.append(f"source_url: {source_url}")
        lines.append(f"author: {author}")
        if published_at:
            lines.append(f"published_at: {published_at}")
        lines.append(f"fetched_at: {fetched_at}")

        # HC-09: gate_decision must be null
        lines.append("gate_decision: null")

        # HC-05: evidence_paths
        lines.append("evidence_paths:")
        lines.append(f'  - "{source_url}"')
        if notebook_id:
            lines.append(f'  - "https://notebooklm.google.com/notebook/{notebook_id}"')

        # HC-06: future_questions
        if extracted_fields.future_questions:
            lines.append("future_questions:")
            for fq in extracted_fields.future_questions:
                lines.append(f'  - "{fq.question}"')

        # HC-07: related_concepts
        if reasoned_links.related_concept:
            lines.append("related_concepts:")
            for rc in reasoned_links.related_concept:
                lines.append(f'  - id: "{rc.concept_id}"')
                lines.append(f'    relationship_type: "{rc.relationship_type}"')
                lines.append(f'    evidence: "{rc.evidence[:200]}"')

        # proposed_graph_nodes
        if reasoned_links.proposed_graph_nodes:
            lines.append("proposed_graph_nodes:")
            for pn in reasoned_links.proposed_graph_nodes:
                lines.append(f'  - name: "{pn.name}"')
                lines.append(f'    type: "{pn.type}"')
                lines.append(f'    reason: "{pn.reason[:200]}"')

        # HC-08: key_findings
        if extracted_fields.key_findings:
            lines.append("key_findings:")
            for kf in extracted_fields.key_findings:
                lines.append(f'  - "{kf.assertion[:300]}"')

        # TLDR
        if extracted_fields.tldr:
            # 单行化（替换换行为空格），避免多段文字破坏 YAML 缩进
            tldr_single = " ".join(extracted_fields.tldr[:500].split())
            lines.append(f"tldr: >-")
            lines.append(f"  {tldr_single}")

        # tags
        lines.append("tags:")
        lines.append("  - research")
        lines.append("  - video")
        lines.append(f"  - {platform}")
        if degraded:
            lines.append("  - degraded")

        lines.append("---")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Gate dry-run
    # ------------------------------------------------------------------

    def _gate_dry_run(self, candidate_path: Path, staging_dir: Path) -> None:
        """在隔离临时目录中运行 gate dry-run 验证。

        构造临时 AKB root 目录结构，将 candidate 复制进去，
        调用 akb2_gate_check.py --dry-run 验证。
        """
        # 检查 gate CLI 可用性
        gate_script = Path("src/akb2/gate/akb2_gate_check.py")
        if not gate_script.exists():
            logger.warning("gate check CLI 不可用，跳过 dry-run 验证")
            return

        with tempfile.TemporaryDirectory() as tmp_root:
            tmp_root_path = Path(tmp_root)
            tmp_staging = tmp_root_path / "staging" / "candidate"
            tmp_staging.mkdir(parents=True, exist_ok=True)

            # 复制 candidate 到临时目录
            tmp_candidate = tmp_staging / candidate_path.name
            tmp_candidate.write_text(candidate_path.read_text(encoding="utf-8"))

            try:
                result = subprocess.run(
                    [
                        "python3", str(gate_script), "check",
                        "--akb-root", str(tmp_root_path),
                        "--input", "staging/candidate",
                        "--dry-run",
                    ],
                    capture_output=True, text=True, timeout=60,
                )
            except subprocess.TimeoutExpired:
                logger.error("gate dry-run 超时")
                raise GateRejected("gate dry-run 超时")

            if result.returncode != 0:
                logger.error(f"gate dry-run 失败 (exit {result.returncode}): {result.stderr[:500]}")
                raise GateRejected(f"gate dry-run exit {result.returncode}")

            # 解析输出：检查 candidate 是否通过
            output = result.stdout + result.stderr
            if "parse_errors" in output:
                # 尝试找 parse_errors 数量
                errors_match = re.search(r"parse_errors[:\s]*(\d+)", output)
                if errors_match and int(errors_match.group(1)) > 0:
                    raise GateRejected(f"gate parse_errors: {errors_match.group(1)}")

            logger.info(f"gate dry-run 通过: {candidate_path.name}")

    def _gate_decision(self, candidate_path: Path, staging_dir: Path) -> str:
        """在隔离临时目录中运行 gate check，解析 proposed_gate_decision。

        Returns:
            proposed_gate_decision 值（如 "active"/"needs_repair"/"reject"），
            解析失败时返回 "parse_error"。
        """
        gate_script = Path("src/akb2/gate/akb2_gate_check.py")
        if not gate_script.exists():
            logger.warning("gate check CLI 不可用，跳过决策验证 (返回 parse_error)")
            return "parse_error"

        with tempfile.TemporaryDirectory() as tmp_root:
            tmp_root_path = Path(tmp_root)
            tmp_staging = tmp_root_path / "staging" / "candidate"
            tmp_staging.mkdir(parents=True, exist_ok=True)

            tmp_candidate = tmp_staging / candidate_path.name
            tmp_candidate.write_text(candidate_path.read_text(encoding="utf-8"))

            try:
                result = subprocess.run(
                    [
                        "python3", str(gate_script), "check",
                        "--akb-root", str(tmp_root_path),
                        "--input", "staging/candidate",
                        "--dry-run",
                    ],
                    capture_output=True, text=True, timeout=60,
                )
            except subprocess.TimeoutExpired:
                logger.error("gate dry-run 超时")
                return "parse_error"

            output = result.stdout + result.stderr
            match = re.search(r"proposed_gate_decision:\s*(\S+)", output)
            if match:
                return match.group(1)

            logger.warning(
                f"gate output 未含 proposed_gate_decision (exit {result.returncode})"
            )
            return "parse_error"

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _build_gate_sections(self, extracted_fields: ExtractedFields) -> str:
        """渲染 ## TLDR + ## 关键发现 两个 H2 章节（gate HC-08 要求）。"""
        lines: list[str] = []

        lines.append("## TLDR")
        if extracted_fields.tldr:
            lines.append("")
            lines.append(extracted_fields.tldr)
        else:
            logger.warning("tldr 为空，## TLDR 章节无内容（extractor 质量问题）")
        lines.append("")

        lines.append("## 关键发现")
        if extracted_fields.key_findings:
            lines.append("")
            for kf in extracted_fields.key_findings:
                cat = f"（{kf.category}）" if kf.category else ""
                conf = f" [置信度:{kf.confidence:.1f}]" if kf.confidence else ""
                lines.append(f"- {kf.assertion}{cat}{conf}")
        else:
            logger.warning("key_findings 为空，## 关键发现 章节无内容（extractor 质量问题）")
        lines.append("")

        return "\n".join(lines) + "\n"

    def _extract_h1(self, content: str) -> str | None:
        """从报告正文提取第一个 H1 标题。"""
        if not content:
            return None
        lines = content.splitlines()
        # 跳过 frontmatter
        i = 0
        if lines and lines[0].strip() == "---":
            for j in range(1, len(lines)):
                if lines[j].strip() == "---":
                    i = j + 1
                    break
        for line in lines[i:]:
            s = line.strip()
            if s.startswith("# ") and not s.startswith("## "):
                return s[2:].strip()
        return None

    def _make_slug(self, content: str) -> str:
        """从内容生成 URL-safe slug。"""
        title = self._extract_h1(content) or "untitled"
        slug = re.sub(r"[^\w-]", "", title.lower().replace(" ", "-"))[:40]
        return slug or "untitled"

    def _compute_hash(self, content: str) -> str:
        """计算 candidate 的 SHA-256 content_hash。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # 全链路编排
    # ------------------------------------------------------------------

    def run_pipeline(
        self,
        collector,
        researcher,
        extractor,
        reasoner,
        dry_run: bool = False,
    ) -> list[dict]:
        """串联全链路：collect → research → extract → reason → assemble → commit。

        每个阶段的异常独立捕获，单视频失败不影响其他视频。

        Args:
            collector: VideoCollector 实例。
            researcher: VideoResearcher 实例。
            extractor: Extractor 实例。
            reasoner: Reasoner 实例。
            dry_run: True 时只组装不写文件。

        Returns:
            [{"video": ..., "candidate_path": ..., "status": "ok"|"failed", "error": ...}]
        """
        results = []

        # 1. 采集
        try:
            video_results = collector.collect()
        except Exception as e:
            logger.error(f"采集失败: {e}")
            return [{"status": "failed", "stage": "collect", "error": str(e)}]

        if not video_results:
            logger.info("无新视频")
            return []

        # 2. 研究
        try:
            research_outputs = researcher.run(video_results)
        except Exception as e:
            logger.error(f"研究失败: {e}")
            return [{"status": "failed", "stage": "research", "error": str(e)}]

        # 3-5. 逐个视频处理
        for output in research_outputs:
            video_meta = output.video_meta
            try:
                # 3. 提取
                extracted = extractor.extract(output.report_markdown)

                # 4. 推理
                reasoned = reasoner.reason(
                    extracted.concepts_mentioned,
                    output.report_markdown[:3000],
                )

                # 5. 组装
                candidate_md = self.assemble(
                    video_meta, output, extracted, reasoned
                )

                if dry_run:
                    results.append({
                        "video": video_meta,
                        "candidate_path": None,
                        "status": "ok",
                        "dry_run": True,
                    })
                else:
                    # 6. 提交
                    candidate_path = self.commit(candidate_md)
                    results.append({
                        "video": video_meta,
                        "candidate_path": str(candidate_path),
                        "status": "ok",
                    })
            except (CandidateTargetExists, GateRejected, PromotionTargetNotFound) as e:
                logger.warning(f"视频 {video_meta.get('title', '?')} 提交失败: {e}")
                results.append({
                    "video": video_meta,
                    "status": "failed",
                    "error": str(e),
                })
            except Exception as e:
                logger.error(f"视频处理失败: {e}")
                results.append({
                    "video": video_meta,
                    "status": "failed",
                    "error": str(e),
                })

        return results