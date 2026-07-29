"""Candidate 组装器 — Assembler。

将 Collector → Researcher → Extractor → Reasoner 的产出组装为
gate-compliant candidate 文件，写入 staging/candidate/ + gate dry-run 验证。

Assembler 是本链路唯一 durable write 所有者（Researcher 不写 staging/Obsidian/DB）。
写入原子性：先写 candidate → gate dry-run → gate rejected 时回滚删除。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from src.akb2.video.config import VideoConfig
from src.akb2.video.dedup_store import VideoDedupStoreError, video_identity
from src.akb2.video.extractor import ExtractedFields
from src.akb2.video.published_index import PublishedIdentityIndex
from src.akb2.video.reasoner import ReasonedLinks
from src.akb2.video.researcher import ResearchOutput

logger = logging.getLogger("akb2.video.assembler")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class CandidateTargetExists(Exception):
    """candidate 文件已存在（slug/date 冲突）。"""


class PublishedIdentityExists(CandidateTargetExists):
    """已发布目录里已存在**同身份**产出（与文件名无关的第二道防线）。

    继承 `CandidateTargetExists` 是为了让既有捕获点（`run_pipeline` 的 except 元组、
    `__main__` 的三处 `except CandidateTargetExists`）自动接住，不必逐处改签名；
    单独成类则让调用方/日志能区分"文件名撞了"与"身份撞了"（后者说明历史产出用的是
    旧命名，文件名层根本抓不到）。
    """


class GateRejected(Exception):
    """gate dry-run 拒绝 candidate。"""


class PromotionTargetNotFound(Exception):
    """L4 promotion 目标路径不存在。"""


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


class Assembler:
    """Candidate 组装器 — 唯一 durable write 所有者。"""

    def __init__(
        self,
        config: VideoConfig | None = None,
        published_index: PublishedIdentityIndex | None = None,
    ):
        self.config = config or VideoConfig()
        # 身份层第二道防线（残余风险「视频存量覆盖」）：文件名判定对 608 份旧命名
        # 历史产出恒不命中，这里改按 `video_identity` 判定。索引按实例缓存、有界。
        self.published_index = published_index or PublishedIdentityIndex(
            self.config.promotion_target_path
        )

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
        # 标题是**事实性身份字段**，从 video_meta（yt-dlp 原值）结构化透传。
        # 缺陷 #10：原实现 `report_title = self._extract_h1(report_markdown) or title`
        # 把标题取自 LLM 产出的报告 H1 —— 身份字段被生成路径污染。
        # 实测（2026-07-26 只读统计 `AI TRENDS/Video`，608 份 md）：同一视频
        # `youtube:M_AmPWmkpwA` 的产出带 **8 个互不相同的标题**，其中
        # "New AI coding paradigm - OpenAI Symphony" 与拼错的 "…paradiagm…" 并存
        # （全目录 4 份含 paradiagm）—— 同一 URL 的标题不稳定，铁证。
        # 另有 89 份产出的 title 是纯 ASCII（疑似 LLM 未按中文 prompt 输出），但
        # **无法归因到 degraded 路**：目录里 0 份笔记的 frontmatter 记了 `degraded`
        # 字段，早期注释写的"98 份英文标题全部来自 degraded 路"既数字不对
        # （实为 89 份）、也没有任何数据支持那个因果。
        title = video_meta.get("title", "Untitled")
        video_url = video_meta.get("url", "")
        platform = video_meta.get("platform", "youtube")
        creator = video_meta.get("creator", "")
        upload_date = video_meta.get("upload_date", "")
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 构建 frontmatter
        frontmatter = self._build_frontmatter(
            title=title,
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

        # 构建正文（注入 gate 要求的结构化章节）。顶层 H1 = 透传标题，
        # 保持与 frontmatter.title 一致；LLM 报告正文原样附在后面不改写。
        gate_sections = self._build_gate_sections(extracted_fields)
        body = f"# {title}\n\n{gate_sections}{research_output.report_markdown}"

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
        """写 staging 中转 → gate 决策 → active 发布或按决策分流处理。

        发布流程：
        1. 写 staging/candidate/video-{slug}-{date}.md（中转）
        2. _gate_decision → proposed_gate_decision
        3. active：copy 到 promotion_target_path，删 staging 中转，返回正式路径
        4. needs_repair：保留 staging 中转 + 写入 pending 等待重试
        5. reject：删 staging 中转 + warning 日志 + raise GateRejected
        6. parse_error：删 staging 中转 + warning 日志 + raise GateRejected
        7. promotion 目录不存在 → raise PromotionTargetNotFound（删中转）
        8. 正式路径同名已存在 → raise CandidateTargetExists（fail-closed，删中转）

        Args:
            candidate_md: 完整的 candidate markdown。
            slug: 文件名 slug（默认从标题生成）。
            date_str: 日期字符串（默认今天）。
            staging_dir: staging 目录（默认从 config）。

        Returns:
            正式发布路径（AI TRENDS/Video/video-{slug}.md，**不含日期**）。

        Raises:
            PublishedIdentityExists: 已发布目录里已有**同身份**产出（旧命名也算）。
            CandidateTargetExists: staging 或正式路径同名已存在。
            GateRejected: gate 判 reject 或 parse_error（永久拒绝）。
            PromotionTargetNotFound: promotion 目标路径不存在。
        """
        # 身份层 fail-closed（残余风险「视频存量覆盖」）：在写任何文件之前，按
        # `video_identity` 查一次"已发布目录里是否已有同身份产出"。文件名层
        # (`promotion_path.exists()`) 只认新命名，对 608 份旧命名历史产出恒不命中。
        self._reject_if_identity_published(candidate_md)

        date = date_str or datetime.now().strftime("%Y-%m-%d")
        slug = slug or self._make_slug(candidate_md)
        staging = staging_dir or self.config.staging_dir

        staging.mkdir(parents=True, exist_ok=True)

        # staging 中转名保留日期：它是 needs_repair 的重试工作区，同一视频跨日重试
        # 需要能再写一次（去掉日期会让重试直接撞 CandidateTargetExists 卡死）。
        filename = f"video-{slug}-{date}.md"
        # 正式产出名**不含日期**（缺陷 V-4）：原来 `video-{slug}-{hash}-{date}.md`
        # 每天都是新名字，于是 `promotion_path.exists()` 这道 fail-closed 对
        # **跨日重复**永不触发 —— 跨日防线只剩账本这个单点。身份哈希已在 slug 里，
        # 日期对唯一性没有任何贡献，去掉后同一视频跨日重复会在文件名层被挡住。
        published_filename = f"video-{slug}.md"
        candidate_path = staging / filename

        if candidate_path.exists():
            raise CandidateTargetExists(
                f"candidate 已存在: {candidate_path}（slug/date 冲突）"
            )

        candidate_path.write_text(candidate_md, encoding="utf-8")
        logger.info(f"staging 中转写入: {candidate_path}")

        decision = self._gate_decision(candidate_path, staging)

        if decision == "active":
            pass  # 继续发布流程
        elif decision == "needs_repair":
            # 可重试：保留 staging 文件，写入 pending 等待下次重试
            logger.warning(
                "gate 判 needs_repair（内容质量不足，可重试）: %s — "
                "staging 文件保留，写入 pending 等待 NLM 可用时重新研究",
                filename,
            )
            self._save_repair_pending(candidate_md, filename)
            return candidate_path  # 保留 staging，不删除
        else:
            # reject / parse_error → 永久丢弃
            candidate_path.unlink(missing_ok=True)
            logger.warning(f"gate 拒绝发布 (decision={decision}): {filename}")
            raise GateRejected(decision)

        promotion_dir = self.config.promotion_target_path
        if not promotion_dir.is_dir():
            candidate_path.unlink(missing_ok=True)
            raise PromotionTargetNotFound(
                f"promotion 目标路径不存在: {promotion_dir}"
            )

        promotion_path = promotion_dir / published_filename
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

        # 增量并入身份索引：同一轮里该视频的其他 URL 形态（youtu.be / watch?v=&t=）
        # 立刻被身份层挡住，且不必为一条记录重扫整个目录。
        source_url = self._extract_source_url(candidate_md)
        if source_url:
            self.published_index.mark_published(source_url, promotion_path)

        return promotion_path

    # ------------------------------------------------------------------
    # 身份层第二道防线
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_source_url(candidate_md: str) -> str:
        """从 candidate frontmatter 取 `source_url:`（`assemble()` 恒写这一行）。"""
        match = re.search(r"^source_url:\s*(.+)$", candidate_md, re.MULTILINE)
        return match.group(1).strip().strip("\"'") if match else ""

    def _reject_if_identity_published(self, candidate_md: str) -> None:
        """已发布目录里已有同身份产出 → 拒绝落地（fail-closed，宪法 P3）。

        Raises:
            PublishedIdentityExists: 命中历史产出（点名出处笔记，便于人工核对）。
        """
        source_url = self._extract_source_url(candidate_md)
        if not source_url:
            # 上游（collector L0/L7、`_dedup_by_identity`）已按 fail-closed 拒收无 url
            # 候选，这里能拿不到 url 说明 candidate 不是本链路产的；不阻断，但要可见。
            logger.warning(
                "candidate 缺 source_url，身份层防线本次不生效（只剩文件名层 + 账本）"
            )
            return
        existing = self.published_index.lookup(source_url)
        if existing:
            raise PublishedIdentityExists(
                f"已发布目录已存在同身份产出: {video_identity(source_url)} "
                f"→ 《{existing}》（fail-closed，不重复产出；历史产出用旧命名，"
                "文件名层抓不到）"
            )

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
            lines.append("tldr: >-")
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
        """从内容生成 URL-safe slug（H1 现在就是透传标题，见 assemble()）。"""
        title = self._extract_h1(content) or "untitled"
        return self._slugify(title)

    @staticmethod
    def _slugify(text: str) -> str:
        """标题 → URL-safe slug 片段。"""
        slug = re.sub(r"[^\w-]", "", (text or "").lower().replace(" ", "-"))[:40]
        return slug.strip("-") or "untitled"

    def _slug_for_video(self, video_meta: dict) -> str:
        """文件名 slug = 透传标题 slug + 稳定身份短哈希。

        缺陷 #10：slug 不能从 LLM 产出派生（报告 H1 会被改写 → 同一视频每天一个
        新文件名 → 文件名层面完全无法察觉重复）。这里 slug 只依赖 yt-dlp 原标题
        与 URL 派生的稳定身份，后缀让"标题被作者改过"的同一视频仍映射到同一族
        文件名，也让两个同名不同源的视频不撞名。
        """
        title_part = self._slugify(video_meta.get("title", ""))
        url = video_meta.get("url", "")
        if not url:
            return title_part
        identity = video_identity(url)
        short = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
        return f"{title_part}-{short}"

    def _compute_hash(self, content: str) -> str:
        """计算 candidate 的 SHA-256 content_hash。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Gate needs_repair 持久化
    # ------------------------------------------------------------------

    def _save_repair_pending(self, candidate_md: str, filename: str) -> None:
        """gate 判 needs_repair 时持久化待重试记录。

        写入 assembler_repair_pending.jsonl（每行一条 JSON），与 researcher 的
        pending_research.json 分属不同阶段，互不干扰。
        """
        repair_path = self.config.staging_dir.parent / "assembler_repair_pending.jsonl"
        repair_path.parent.mkdir(parents=True, exist_ok=True)

        # 从 candidate markdown 提取 frontmatter 中的关键字段
        source_url = ""
        title = ""
        m = re.search(r"^source_url:\s*(.+)$", candidate_md, re.MULTILINE)
        if m:
            source_url = m.group(1).strip()
        m = re.search(r'^title:\s*"(.+)"$', candidate_md, re.MULTILINE)
        if m:
            title = m.group(1).strip()

        record = {
            "filename": filename,
            "source_url": source_url,
            "title": title,
            "gate_decision": "needs_repair",
            "added_at": datetime.now(UTC).isoformat(),
        }
        with open(repair_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(
            "needs_repair 已记录到 %s: %s",
            repair_path.name, title or filename,
        )

    def _is_staging_candidate(self, candidate_path: Path) -> bool:
        """判断 commit 返回路径是否仍在 staging/candidate 内。

        launchd 的 live 形态使用相对 staging_dir，commit() 返回值也可能是
        相对路径；必须 resolve 后比较，避免 needs_repair 被误报为 ok。
        """
        try:
            candidate_resolved = candidate_path.resolve()
            staging_resolved = self.config.staging_dir.resolve()
            return (
                candidate_resolved == staging_resolved
                or staging_resolved in candidate_resolved.parents
            )
        except OSError:
            return False

    @staticmethod
    def _mark_ingested(collector, video_meta: dict, output_path: Path) -> bool:
        """产出落地后登记去重身份，失败必须可见（宪法 P4）。

        顺序契约：**发布成功之后**才登记。两种失败窗口的代价不对称——
        - 先登记后发布：崩溃则该视频被永久标记已处理，报告永远不会产出（丢内容）；
        - 先发布后登记：崩溃则下轮最多重跑一次（多花一次额度，内容不丢）。
        因此选后者；登记失败时不吞异常，打 ERROR 并在结果里标 `dedup_marked=False`，
        由 `_report_results` 汇总——否则"账本没记"会静默演化成明天的重复研究。

        异常范围只收运行时故障（账本不可写 / 缺 url）。原实现还捕获
        `AttributeError`，把"collector 没有 mark_ingested 方法"这类**接线/改名的
        编程错误**降级成"账本写失败"，排障方向被带偏到磁盘和权限上；现在让它
        原样抛出（由 run_pipeline 记成 failed，堆栈可见）。
        """
        try:
            collector.mark_ingested(video_meta, output_path)
            return True
        except (VideoDedupStoreError, ValueError, RuntimeError) as exc:
            logger.error(
                "产出已发布但去重登记失败: %s (%s) — 下轮会重跑该视频并重复烧额度: %s",
                video_meta.get("title", "?"), video_meta.get("url", "?"), exc,
            )
            return False

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

        # SC-1 fail-visible：上游采集非空但研究阶段 0 产出（NLM 主路 + 字幕降级双路全挂）
        # 不能静默返回空列表 → 否则 _report_results([]) 算 failed=0 → exit 0，掩盖全量失败。
        # 仅 no_new_videos（采集空）可 exit 0；all_research_failed 必须 exit≠0。
        if not research_outputs:
            err = (
                f"all_research_failed: 0/{len(video_results)} outputs "
                "(NLM + transcript fallback both failed)"
            )
            logger.error(err)
            return [{
                "status": "failed",
                "stage": "research",
                "error": err,
                "video_count": len(video_results),
            }]

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
                    # 6. 提交（slug 用稳定身份派生，不用 LLM 产出的 H1）
                    candidate_path = self.commit(
                        candidate_md, slug=self._slug_for_video(video_meta)
                    )
                    # 判断 gate 决策：staging 路径 = needs_repair，promotion 路径 = active
                    is_staging = self._is_staging_candidate(candidate_path)
                    result = {
                        "video": video_meta,
                        "candidate_path": str(candidate_path),
                        "status": "needs_repair" if is_staging else "ok",
                    }
                    if not is_staging:
                        # 7. 产出已落地 → 登记去重身份（写端，缺陷 #2）。
                        # needs_repair 不登记：它就是要下轮重试的。
                        result["dedup_marked"] = self._mark_ingested(
                            collector, video_meta, candidate_path
                        )
                    results.append(result)
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
