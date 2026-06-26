"""AKB2 视频报告链路 CLI 入口。

用法：
    python3 -m akb2.video --help
    python3 -m akb2.video --dry-run --fixture
    python3 -m akb2.video --source <url>
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import time

from src.akb2.video.assembler import (
    Assembler,
    CandidateTargetExists,
    GateRejected,
    PromotionTargetNotFound,
)
from src.akb2.video.collector import VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.extractor import Extractor
from src.akb2.video.llm_adapter import DeepSeekAdapter
from src.akb2.video.reasoner import ConceptStore, Reasoner
from src.akb2.video.researcher import VideoResearcher

logger = logging.getLogger("akb2.video")

# ---------------------------------------------------------------------------
# 网络就绪预检（SC-6）
# ---------------------------------------------------------------------------

_PREFLIGHT_HOSTS: list[tuple[str, int, str]] = [
    ("llm-proxy.tapsvc.com", 443, "LLM Proxy"),
    ("youtube.com", 443, "YouTube"),
    ("notebooklm.google.com", 443, "NotebookLM"),
]

_PREFLIGHT_MAX_RETRIES: int = 5
_PREFLIGHT_INITIAL_BACKOFF: float = 30.0  # 秒
_PREFLIGHT_BACKOFF_FACTOR: float = 2.0


def network_preflight(
    hosts: list[tuple[str, int, str]] | None = None,
    max_retries: int = _PREFLIGHT_MAX_RETRIES,
    initial_backoff: float = _PREFLIGHT_INITIAL_BACKOFF,
) -> bool:
    """启动前网络就绪探测：逐个 TCP connect 关键依赖。

    全部可达 → True；任一不可达 → 退避重试 N 次。全部耗尽仍不可达 → False。

    Args:
        hosts: [(host, port, label), ...]
        max_retries: 最大重试次数（含首次探测）
        initial_backoff: 首次退避秒数

    Returns:
        True 全部关键依赖可达，False 耗尽重试仍不可达。
    """
    targets = hosts or _PREFLIGHT_HOSTS
    backoff = initial_backoff

    for attempt in range(1, max_retries + 1):
        unreachable: list[str] = []
        for host, port, label in targets:
            try:
                sock = socket.create_connection((host, port), timeout=10)
                sock.close()
            except OSError as e:
                unreachable.append(f"{label}({host}:{port}): {e}")

        if not unreachable:
            logger.info(
                "网络就绪预检通过 (attempt=%d/%d): 所有关键依赖可达",
                attempt, max_retries,
            )
            return True

        if attempt < max_retries:
            logger.warning(
                "网络就绪预检不可达 (attempt=%d/%d, backoff=%.0fs): %s",
                attempt, max_retries, backoff, "; ".join(unreachable),
            )
            time.sleep(backoff)
            backoff *= _PREFLIGHT_BACKOFF_FACTOR
        else:
            logger.error(
                "网络就绪预检失败 (attempt=%d/%d, 耗尽重试): %s",
                attempt, max_retries, "; ".join(unreachable),
            )

    return False


def _build_llm_adapter(config: VideoConfig) -> DeepSeekAdapter | None:
    """从 config 构造 LLM adapter；无 key 时返回 None（规则法降级）。"""
    if not config.llm_api_key:
        logger.warning(
            "LLM API key 未配置（keychain claude-ai-gateway / env AKB2_LLM_API_KEY 均未设置）"
            "—— Extractor 仅用规则法，future_questions 可能 <2 → candidate needs_repair"
        )
        return None
    return DeepSeekAdapter(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
        model=config.llm_model,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。exit 0 = 全链路通过，exit 1 = 有失败，exit 2 = 网络不可达。"""
    parser = argparse.ArgumentParser(
        prog="akb2.video",
        description="AKB2 视频报告链路 — 采集 → 研究 → 提取 → 推理 → 组装 → gate dry-run",
    )
    parser.add_argument("--dry-run", action="store_true", help="跑全链路 + gate dry-run，不写文件")
    parser.add_argument("--fixture", action="store_true", help="使用 tests/fixtures/ 跑 mock 全链路")
    parser.add_argument("--source", type=str, help="对单个视频 URL 跑全链路")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--skip-preflight", action="store_true", help="跳过网络就绪预检（调试用）")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s [%(name)s] %(message)s",
    )

    # 网络就绪预检（fixture 模式 + skip-preflight 跳过）
    if not args.fixture and not args.skip_preflight:
        if not network_preflight():
            logger.critical("网络不可达，放弃本次运行（下次 LaunchAgent 触发时会自动重试）")
            return 2

    config = VideoConfig()

    if args.source:
        return _run_single_source(config, args.source, args.dry_run)

    if args.fixture:
        return _run_fixture(config, args.dry_run)

    # 默认：全链路
    return _run_full(config, args.dry_run)


def _run_full(config: VideoConfig, dry_run: bool) -> int:
    """运行完整链路。"""
    try:
        llm_adapter = _build_llm_adapter(config)
        collector = VideoCollector(config, llm_adapter=llm_adapter)
        researcher = VideoResearcher(config, llm_adapter=llm_adapter)
        extractor = Extractor(llm_adapter=llm_adapter)
        reasoner = Reasoner(ConceptStore(), llm_adapter=llm_adapter)
        assembler = Assembler(config)

        results = assembler.run_pipeline(
            collector, researcher, extractor, reasoner, dry_run=dry_run,
        )
        return _report_results(results)
    except Exception as e:
        logger.error(f"全链路失败: {e}")
        return 1


def _run_single_source(config: VideoConfig, url: str, dry_run: bool) -> int:
    """对单个视频 URL 跑全链路。"""
    try:
        # 构造最小 video_result
        video_result = {
            "video": {
                "title": url.split("/")[-1] or "Video",
                "url": url,
                "platform": "youtube" if "youtube" in url else "bilibili",
                "creator": "manual",
            },
            "creator": {"name": "manual"},
        }

        llm_adapter = _build_llm_adapter(config)
        researcher = VideoResearcher(config, llm_adapter=llm_adapter)
        extractor = Extractor(llm_adapter=llm_adapter)
        reasoner = Reasoner(ConceptStore(), llm_adapter=llm_adapter)
        assembler = Assembler(config)

        outputs = researcher.run([video_result])
        if not outputs:
            logger.error("研究失败")
            return 1

        for output in outputs:
            extracted = extractor.extract(output.report_markdown)
            reasoned = reasoner.reason(extracted.concepts_mentioned, output.report_markdown[:3000])
            candidate_md = assembler.assemble(output.video_meta, output, extracted, reasoned)

            if dry_run:
                logger.info(f"dry-run: {output.video_meta.get('title', '?')}")
            else:
                try:
                    candidate_path = assembler.commit(candidate_md)
                    if assembler._is_staging_candidate(candidate_path):
                        logger.warning(
                            "gate 判 needs_repair，报告未发布，已保留 staging: %s",
                            candidate_path,
                        )
                        return 1
                    logger.info(f"已发布到 AI TRENDS/Video: {candidate_path}")
                except GateRejected as e:
                    logger.warning(f"gate 拒绝，报告已丢弃: {e}")
                    return 1

        return 0
    except Exception as e:
        logger.error(f"单源处理失败: {e}")
        return 1


def _run_fixture(config: VideoConfig, dry_run: bool) -> int:
    """使用 fixture 跑 mock 全链路。"""
    try:
        from src.akb2.video.extractor import ExtractedFields, KeyFinding, SourceSpan
        from src.akb2.video.reasoner import ProposedNode, ReasonedLinks, RelatedConcept
        from src.akb2.video.researcher import ResearchOutput

        # 构造 mock 数据
        video_meta = {
            "title": "DeepSeek V4 Architecture Deep Dive",
            "url": "https://www.youtube.com/watch?v=test123",
            "platform": "youtube",
            "creator": "Test Channel",
            "upload_date": "20260528",
        }

        research_output = ResearchOutput(
            report_markdown="# DeepSeek V4 架构分析\n\n## 执行摘要\n\nDeepSeek V4 是一个强大的模型。\n\n## 关键发现\n\n模型在 MMLU 上达到 92.5%。\n\n## 核心观点\n\n- 16 万亿参数\n- 128K 上下文窗口\n\n## 关键概念\n\nMoE, Multi-head Attention, KV Cache",
            notebook_id="nb-test-123",
            video_meta=video_meta,
            degraded=False,
        )

        extracted = ExtractedFields(
            tldr="DeepSeek V4 是一个强大的模型。",
            key_findings=[
                KeyFinding(
                    assertion="模型在 MMLU 上达到 92.5%",
                    source_span=SourceSpan(line_start=7, line_end=7),
                    confidence=0.9,
                ),
            ],
            future_questions=[],
            concepts_mentioned=["DeepSeek", "MoE", "KV Cache"],
        )

        reasoned = ReasonedLinks(
            related_concept=[
                RelatedConcept(
                    concept_id="concept:deepseek",
                    relationship_type="evaluates",
                    evidence="报告对比了 DeepSeek V4 的性能",
                ),
            ],
            proposed_graph_nodes=[
                ProposedNode(
                    name="MoE",
                    type="concept",
                    reason="报告提到但 AKB 概念图未收录",
                ),
            ],
        )

        assembler = Assembler(config)
        candidate_md = assembler.assemble(video_meta, research_output, extracted, reasoned)

        if dry_run:
            logger.info("gate_dry_run: pass (fixture mode)")
            logger.info("PASS: fixture 全链路通过")
            return 0
        else:
            candidate_path = assembler.commit(candidate_md)
            logger.info(f"candidate 写入: {candidate_path}")
            return 0

    except (CandidateTargetExists, GateRejected, PromotionTargetNotFound) as e:
        logger.error(f"提交失败: {e}")
        return 1
    except Exception as e:
        logger.error(f"fixture 链路失败: {e}")
        return 1


def _report_results(results: list[dict]) -> int:
    """汇总结果（SC-7: fail-visibly — 结构化分层计数）。"""
    ok = sum(1 for r in results if r.get("status") == "ok")
    needs_repair = sum(1 for r in results if r.get("status") == "needs_repair")
    failed = sum(1 for r in results if r.get("status") == "failed")
    total = len(results)
    dry = sum(1 for r in results if r.get("dry_run"))

    parts = [f"{ok} 已发布"]
    if needs_repair:
        parts.append(f"{needs_repair} 待修复(needs_repair)")
    if failed:
        parts.append(f"{failed} 失败")
    if dry:
        parts.append(f"{dry} dry-run")

    logger.info(f"视频链路完成 ({total} 个视频): {', '.join(parts)}")

    if needs_repair:
        logger.warning("needs_repair 视频详情:")
        for r in results:
            if r.get("status") == "needs_repair":
                title = r.get("video", {}).get("title", "?")
                path = r.get("candidate_path", "?")
                logger.warning("  needs_repair: %s — staging=%s", title, path)

    if failed:
        logger.error("失败视频详情:")
        for r in results:
            if r.get("status") == "failed":
                title = r.get("video", {}).get("title", "?")
                logger.error("  失败: %s — %s", title, r.get("error", "?"))

    # exit 1 = 有失败或待修复（即使部分成功）；exit 0 = 全成功
    if failed > 0 or needs_repair > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
