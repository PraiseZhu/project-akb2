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
from pathlib import Path

from src.akb2.video.assembler import (
    Assembler,
    CandidateTargetExists,
    GateRejected,
    PromotionTargetNotFound,
)
from src.akb2.video.blocked import write_zero_yield_diagnostic
from src.akb2.video.collector import VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.dedup_store import VideoDedupStore
from src.akb2.video.external_credentials import (
    STATUS_DEAD,
    CredentialHealth,
    probe_credentials,
)
from src.akb2.video.extractor import Extractor
from src.akb2.video.llm_adapter import DeepSeekAdapter
from src.akb2.video.reasoner import ConceptStore, Reasoner
from src.akb2.video.researcher import VideoResearcher
from src.akb2.video.url_expand import ShortLinkExpander

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


def _build_dedup_pair(
    config: VideoConfig, llm_adapter: DeepSeekAdapter | None = None
) -> tuple[VideoDedupStore, VideoCollector]:
    """构造去重账本 + 接了账本的 collector（三条 CLI 路径共用，缺陷 V-2）。

    `--source` / `--fixture` 过去是裸 `assembler.commit(...)`：既不查账本
    （`is_ingested`）也不登记（`mark_ingested`），且 commit 不传 slug → 产出文件名
    与主路径不同族，连文件名层都抓不到重复。这两条路是官方后门，现在与主路径共用
    同一套查/记逻辑。
    """
    store = VideoDedupStore(config.dedup_db_path)
    logger.info("去重账本: %s", config.dedup_db_path)
    return store, VideoCollector(config, db=store, llm_adapter=llm_adapter)


def _seed_history(store: VideoDedupStore, config: VideoConfig) -> None:
    """一次性回填历史产出身份，并把部分失败显式暴露（宪法 P4，缺陷 V-1）。"""
    outcome = store.seed_from_published(config.promotion_target_path)
    if outcome.already_seeded or outcome.dir_unavailable:
        return
    if not outcome.marked_complete:
        logger.error(
            "[去重库] 历史回填未完整覆盖（%d/%d 份，覆盖率 %.1f%%）——"
            "未覆盖的已发布视频本轮可能被重跑；标记未写入，下轮会重试",
            outcome.scanned, outcome.files_total, outcome.coverage * 100,
        )


def _commit_and_mark(
    assembler: Assembler,
    collector: VideoCollector,
    video_meta: dict,
    candidate_md: str,
) -> tuple[Path, bool, bool]:
    """提交 + 登记（与 `run_pipeline` 相同的 slug / 账本契约）。

    Returns:
        (产出路径, 是否仍在 staging（= needs_repair）, 账本是否已登记)
    """
    candidate_path = assembler.commit(
        candidate_md, slug=assembler._slug_for_video(video_meta)
    )
    is_staging = assembler._is_staging_candidate(candidate_path)
    if is_staging:
        # needs_repair 不登记：它就是要下轮重试的（与 run_pipeline 一致）。
        return candidate_path, True, False
    marked = assembler._mark_ingested(collector, video_meta, candidate_path)
    return candidate_path, False, marked


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。exit 0 = 全链路通过，exit 1 = 有失败，exit 2 = 网络不可达。"""
    parser = argparse.ArgumentParser(
        prog="akb2.video",
        description="AKB2 视频报告链路 — 采集 → 研究 → 提取 → 推理 → 组装 → gate dry-run",
    )
    parser.add_argument("--dry-run", action="store_true", help="跑全链路 + gate dry-run，不写文件")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="使用 tests/fixtures/ 跑 mock 全链路",
    )
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
        # 缺陷 #2：生产入口过去从不传 db → L1/L7 去重整条空转（79% 重复产出）。
        # 现在显式注入去重账本，并一次性回填历史产出身份，避免已发布视频再跑一遍。
        dedup_store, collector = _build_dedup_pair(config, llm_adapter)
        _seed_history(dedup_store, config)
        researcher = VideoResearcher(config, llm_adapter=llm_adapter)
        extractor = Extractor(llm_adapter=llm_adapter)
        reasoner = Reasoner(ConceptStore(), llm_adapter=llm_adapter)
        # 共享 collector 的身份索引：不注入时 collector 与 assembler 各扫一遍
        # 同一目录（608 份产出实测 1216 次读 / 5.55MB 解码每轮），且两份缓存
        # 状态不通 —— collector 那份永远收不到 assembler 的 mark_published()。
        assembler = Assembler(config, published_index=getattr(collector, "published_index", None))

        # 凭证预检：只在会落盘诊断的路径上做（dry-run 不写诊断，也不该为它多打一次
        # nlm CLI）。放在 run_pipeline 之前 —— 采集要跑近一分钟，凭证状态先见于日志。
        credential_health = None if dry_run else _probe_credentials(config, collector, researcher)

        results = assembler.run_pipeline(
            collector, researcher, extractor, reasoner, dry_run=dry_run,
        )
        rc = _report_results(results)
        # 0 产出归因 + 诊断落盘（宪法 P4）：`_report_results([])` 返回 0，
        # launchd 只看到"正常退出"—— 2026-07-28 的 109 个候选全灭就是这样静默过去的。
        # dry-run 不写诊断（它不代表生产产出能力）。
        if not dry_run:
            verdict = _write_collect_diagnostic(collector, results, credential_health)
            if verdict is not None and verdict.is_blocked and rc == 0:
                # blocked 必须让 launchd 的 LastExitStatus 非 0；
                # "窗口内确实没有新视频"（zero_yield）仍然是正常退出。
                return 1
        return rc
    except Exception as e:
        logger.error(f"全链路失败: {e}")
        return 1


def _probe_credentials(
    config: VideoConfig,
    collector: VideoCollector,
    researcher: VideoResearcher,
) -> CredentialHealth | None:
    """链路启动前的凭证健康预检（yt-dlp cookies + NLM 登录态）。

    **只观测，不改控制流**：凭证死了链路照常跑（NLM 死了走字幕降级、cookies 弱了照样
    试）—— fail-fast 会把"降级还能出东西"的那部分产出一起掐掉，不是本次要的语义。
    唯一产物是 `logs/video-blocked.json` 里的 `credential_health` 归因块，让 0 产出的
    共同根因（2026-07-30：三处症状同一个凭证根因）一次说清。

    预检本身绝不让主链路失败：探测异常返回 None，归因退回改动前行为。
    """
    try:
        health = probe_credentials(
            ytdlp_bin=config.ytdlp_bin,
            cookie_source=getattr(collector, "cookie_source", ""),
            nlm_profile=config.nlm_profile,
            nlm_health_check=researcher.nlm_auth_status,
        )
    except Exception as exc:  # noqa: BLE001 — 预检失败不得掩盖主链路结论
        logger.error("凭证健康预检异常（主链路结论不受影响）: %s", exc)
        return None

    if health.is_healthy:
        logger.info("凭证预检: 全部正常（yt-dlp cookies / NLM 登录态）")
        return health
    for item in health.unhealthy:
        log = logger.error if item.status == STATUS_DEAD else logger.warning
        log("凭证预检 [%s] %s: %s —— 修复: %s",
            item.status, item.name, item.detail, item.fix_command)
    return health


def _write_collect_diagnostic(
    collector: VideoCollector,
    results: list[dict],
    credential_health: CredentialHealth | None = None,
):
    """把采集漏斗计数归因并落盘；诊断本身绝不让主链路失败。"""
    stats = getattr(collector, "stats", None)
    if stats is None:
        return None
    published = sum(1 for r in results if r.get("status") == "ok")
    try:
        return write_zero_yield_diagnostic(
            stats.as_dict(), published, credential_health=credential_health
        )
    except Exception as exc:  # noqa: BLE001 — 诊断失败不得掩盖主链路结论
        logger.error("0 产出诊断写入异常（主链路结论不受影响）: %s", exc)
        return None


def _run_single_source(config: VideoConfig, url: str, dry_run: bool) -> int:
    """对单个视频 URL 跑全链路。"""
    try:
        # 短链展开（残余风险 2）：`--source` 是人手粘贴链接的入口，而 bilibili 的分享
        # 按钮给的就是 `https://b23.tv/<code>` —— 不展开则身份走 sha256 兜底，与账本里
        # 展开后的 `bilibili:BV...` 对不上，下面的 `is_ingested` 查不到 → 已研究过的
        # 视频照样重烧一次 NLM 深研。展开在采集阶段做，`video_identity` 保持纯函数。
        url = ShortLinkExpander().expand(url)

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
        # 缺陷 V-2：这条路过去完全绕过账本。查端必须在 researcher.run 之前
        # （花 NLM/LLM 额度之前），写端必须在发布之后。
        #
        # 第二轮复核补：只查账本不够。账本实证会缺记录（V-1 部分回填 / 新装机器
        # 账本为空），此时 `--source` 会先把 NLM 深研 + LLM 额度全烧完，最后才在
        # `assembler.commit` 撞文件名 fail-closed —— "花钱之前拦"只对"账本里已有
        # 记录"成立。所以这里与主路径一样先做历史回填（幂等，已 seed 过直接返回），
        # 让"历史产出里已存在该视频"这件事在花钱之前就能查到。
        dedup_store, collector = _build_dedup_pair(config, llm_adapter)
        _seed_history(dedup_store, config)
        # 走 collector 的 L1 判定（账本 **或** 已发布产出的身份索引），不是裸查账本：
        # 回填是按目录记 `seeded_from:` 标记的，标记已置而账本被清空/重建时
        # （残余风险「视频存量覆盖」的失守形态）裸查账本会漏判，索引仍能查到。
        if collector.is_ingested_url(url):
            logger.warning(
                "[去重] 该视频已入库，跳过 --source 全链路（避免重复烧 NLM/LLM 额度）: %s",
                url,
            )
            return 0

        researcher = VideoResearcher(config, llm_adapter=llm_adapter)
        extractor = Extractor(llm_adapter=llm_adapter)
        reasoner = Reasoner(ConceptStore(), llm_adapter=llm_adapter)
        # 共享 collector 的身份索引：不注入时 collector 与 assembler 各扫一遍
        # 同一目录（608 份产出实测 1216 次读 / 5.55MB 解码每轮），且两份缓存
        # 状态不通 —— collector 那份永远收不到 assembler 的 mark_published()。
        assembler = Assembler(config, published_index=getattr(collector, "published_index", None))

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
                    candidate_path, is_staging, marked = _commit_and_mark(
                        assembler, collector, output.video_meta, candidate_md
                    )
                    if is_staging:
                        logger.warning(
                            "gate 判 needs_repair，报告未发布，已保留 staging: %s",
                            candidate_path,
                        )
                        return 1
                    logger.info(f"已发布到 AI TRENDS/Video: {candidate_path}")
                    if not marked:
                        logger.error(
                            "已发布但去重账本未登记，下轮会重跑该视频: %s", candidate_path
                        )
                        return 1
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
            report_markdown=(
                "# DeepSeek V4 架构分析\n\n## 执行摘要\n\nDeepSeek V4 是一个强大的模型。"
                "\n\n## 关键发现\n\n模型在 MMLU 上达到 92.5%。\n\n## 核心观点\n\n- 16 万亿参数"
                "\n- 128K 上下文窗口\n\n## 关键概念\n\nMoE, Multi-head Attention, KV Cache"
            ),
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

        # 缺陷 V-2：非 dry-run 的 fixture 模式是真写 vault 的路径，
        # 必须和主路径共用同一套查/记逻辑（原来是裸 commit，账本既不查也不记）。
        # 同 `--source`：先历史回填再查，否则账本缺记录时这条路照样会写重复产出。
        #
        # 构造顺序：dedup_pair 提到 assembler **之前**，这样 assembler 能复用
        # collector 的身份索引（否则两份索引各扫一遍同一目录、缓存状态还不通）。
        # dry_run 分支在下面才返回，所以 fixture dry-run 也会走这次构造 —— 它只读
        # 目录、不写任何东西，代价是一次有界扫描。
        dedup_store, collector = _build_dedup_pair(config)
        assembler = Assembler(config, published_index=getattr(collector, "published_index", None))
        candidate_md = assembler.assemble(video_meta, research_output, extracted, reasoned)

        if dry_run:
            logger.info("gate_dry_run: pass (fixture mode)")
            logger.info("PASS: fixture 全链路通过")
            return 0

        _seed_history(dedup_store, config)
        # 同 `--source`：走 L1 双真值源判定（账本 或 已发布产出身份索引），不裸查账本。
        if collector.is_ingested_url(video_meta["url"]):
            logger.warning(
                "[去重] fixture 视频已入库，跳过写入（避免重复产出）: %s",
                video_meta["url"],
            )
            return 0
        candidate_path, is_staging, marked = _commit_and_mark(
            assembler, collector, video_meta, candidate_md
        )
        logger.info(f"candidate 写入: {candidate_path}")
        if is_staging:
            logger.warning("gate 判 needs_repair，保留 staging: %s", candidate_path)
            return 1
        if not marked:
            logger.error("已发布但去重账本未登记，下轮会重跑该视频: %s", candidate_path)
            return 1
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

    # 已发布但去重账本未登记 → 明天会重跑并重复烧额度，必须显式暴露（宪法 P4）
    unmarked = [r for r in results if r.get("dedup_marked") is False]
    if unmarked:
        logger.error(
            "去重账本未登记 %d 个已发布视频（下轮会重复研究，请检查去重库可写性）:",
            len(unmarked),
        )
        for r in unmarked:
            logger.error("  未登记: %s", r.get("video", {}).get("title", "?"))

    # exit 1 = 有失败 / 待修复 / 账本未登记（即使部分成功）；exit 0 = 全成功
    if failed > 0 or needs_repair > 0 or unmarked:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
