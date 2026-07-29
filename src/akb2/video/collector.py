"""视频采集器 — VideoCollector。

从旧 AKB scripts/pipeline/video_collector.py 迁移，适配 AKB2 目录结构：
- 去重账本：`dedup_store.VideoDedupStore`（读端 + 写端，默认从 config 注入）
- yt-dlp 路径：self.config.ytdlp_bin（解决 #5 硬编码路径）
- L1-L7 + L4.5 共 8 层过滤链路保持不变
- 文件锁保护 pending_research.json（解决 #7 并发竞态）
- L5 LLM 话题聚类通过 llm_adapter.py 注入接口调用

缺陷 #2 / #12 修复记录（2026-07-26）：
- 去重库不再可能为 None：`db` 不传时从 `config.dedup_db_path` 构造，
  L1/L7 真正生效；库不可用时抛 `VideoDedupStoreError`（fail-closed），
  不再 `return False` 静默放行（原来正是这一步让重复产出通过：实测 608 份产出
  只对应 230 个视频身份；只看 `source_url:` 子集是 325 份 → 68 个身份 = 79%）。
- 时间闸（`days_lookback` / `start_date`）不再被空 upload_date 静默绕过：
  缺日期先补取，仍缺则拒收并告警。
- `nlm_daily_quota` 成为真正的日硬上限（按账本统计今日已入库数扣减），
  超额候选写回 pending 而不是静默处理掉。
"""

from __future__ import annotations

import fcntl
import json
import logging
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from src.akb2.video.config import VideoConfig
from src.akb2.video.constants import (
    VIDEO_APPROX_DATE_MARGIN_DAYS,
    VIDEO_BUDGET_SECONDS,
    VIDEO_DATE_BACKFILL_SLEEP_SEC,
    VIDEO_DAYS_LOOKBACK,
    VIDEO_MAX_DATE_BACKFILL_PER_RUN,
    VIDEO_MAX_DURATION_MIN,
    VIDEO_MIN_DURATION_MIN,
    VIDEO_NLM_DAILY_QUOTA,
    VIDEO_YT_LIST_EXTRACTOR_ARGS,
)
from src.akb2.video.dedup_store import (
    VideoDedupStore,
    local_day_start_utc_iso,
    video_identity,
)
from src.akb2.video.llm_adapter import LLMAdapter, extract_json_from_llm_response
from src.akb2.video.published_index import PublishedIdentityIndex
from src.akb2.video.url_expand import ShortLinkExpander

logger = logging.getLogger("akb2.video.collector")


@dataclass
class CollectStats:
    """单轮采集的分层漏斗计数 —— "0 产出"必须能被归因（宪法 P4）。

    2026-07-28 事故：视频链路产出 0 个，launchd 退出码正常，无任何告警出口；
    日志里 109 行"拒收"要人工 grep 才看得出采集能力已经整体失效。计数在这里
    结构化留存，由 `blocked.write_zero_yield_diagnostic` 落盘成机读诊断。
    """

    sources_enabled: int = 0
    raw_listed: int = 0            # 列表阶段返回的原始条目数
    dropped_no_url: int = 0        # L0 无 url
    dropped_no_title: int = 0      # bilibili 2 阶段补全后仍无 title（上游元数据坏）
    dropped_dedup: int = 0         # L1 已入库（账本 或 已发布产出索引）
    dropped_duration: int = 0      # L3 时长
    dropped_keyword: int = 0       # L4 creator 关键词
    dropped_relevance: int = 0     # L4.5 全局 AI 相关性
    dropped_out_of_window: int = 0  # L2 精确日期超窗（含 start_date）
    rejected_date_prefilter: int = 0  # L2a 近似日期已超窗，直接拒（零网络成本）
    rejected_date_unknown: int = 0    # L2b 精确日期取不到 → fail-closed 拒收
    date_backfill_used: int = 0
    date_backfill_ok: int = 0
    date_backfill_failed: int = 0
    date_backfill_budget: int = VIDEO_MAX_DATE_BACKFILL_PER_RUN
    date_backfill_budget_exhausted: bool = False
    candidates_after_filters: int = 0  # 进入研究阶段前的候选数
    elapsed_sec: int = 0
    budget_sec: int = VIDEO_BUDGET_SECONDS
    creator_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class VideoCollector:
    """检测新视频 → 返回元数据（内容提取由 NLM 直接处理）。"""

    def __init__(
        self,
        config: VideoConfig | None = None,
        db: VideoDedupStore | None = None,
        llm_adapter: LLMAdapter | None = None,
        published_index: PublishedIdentityIndex | None = None,
        shortlink_expander: ShortLinkExpander | None = None,
    ):
        self.config = config or VideoConfig()
        # db 不传就自己建：生产入口从不传 db 正是 L1/L7 空转的根因（缺陷 #2）。
        self.db = db if db is not None else VideoDedupStore(self.config.dedup_db_path)
        # 已发布产出的身份索引：账本之外的第二个真值来源（残余风险「视频存量覆盖」）。
        # 账本可能被清空 / 损坏 / 误标 seeded，而 vault 里的 608 份产出还在——
        # L1 同时查这两处，历史视频才不会在账本失守时被整批重跑（每个重烧一次
        # NLM 深研 + 两次 LLM）。索引有界且按实例缓存，不逐候选重扫。
        self.published_index = published_index or PublishedIdentityIndex(
            self.config.promotion_target_path
        )
        # 短链展开器（残余风险 2）：`b23.tv/<code>` 在 L1 之前展开成完整 bilibili URL，
        # 否则它的 sha256 兜底身份与账本里的 `bilibili:BV...` 对不上，去重整体失效。
        self.shortlink_expander = shortlink_expander or ShortLinkExpander()
        self._llm_adapter = llm_adapter
        # 单轮发布日期补取预算（有界，宪法 P1）与因日期/身份未知被拒收的计数
        self._date_backfill_used = 0
        self._date_unknown_rejected = 0
        self._url_missing_rejected = 0
        # 单轮分层漏斗计数（宪法 P4：0 产出必须可归因）
        self.stats = CollectStats()

        # 加载视频源配置
        self._video_config = self._load_video_config()
        self._settings = self._video_config.get("settings", {})

        # yt-dlp 反爬配置
        self._ytdlp_base_args: list[str] = self._build_ytdlp_args()

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    def _load_video_config(self) -> dict:
        """加载 video_sources.json。"""
        path = self.config.video_sources_path
        if not path.exists():
            logger.warning(f"视频配置不存在: {path}")
            return {"creators": [], "settings": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def _build_ytdlp_args(self) -> list[str]:
        """构建 yt-dlp 基础参数（cookies 优先）。"""
        cookies_file_name = self._settings.get("ytdlp_cookies_file", "yt-cookies.txt")
        cookies_file_path = self.config.video_sources_path.parent / cookies_file_name
        if cookies_file_path.exists() and cookies_file_path.stat().st_size > 0:
            logger.info(f"yt-dlp 使用 cookies 文件: {cookies_file_path.name}")
            return [
                "--cookies", str(cookies_file_path),
                "--remote-components", "ejs:github",
            ]
        browser = self._settings.get("ytdlp_cookies_browser", "chrome")
        logger.info(
            f"yt-dlp 使用浏览器 cookies: {browser} "
            f"(若超时频繁，建议导出 cookies 文件到 {cookies_file_path})"
        )
        return [
            "--cookies-from-browser", browser,
            "--remote-components", "ejs:github",
        ]

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def collect(self) -> list[dict]:
        """主流程：检测 → 8 层过滤 → 处理。受 VIDEO_BUDGET_SECONDS 总预算控制。"""
        budget_start = time.time()
        self._date_backfill_used = 0
        self._date_unknown_rejected = 0
        self._url_missing_rejected = 0
        self.stats = CollectStats()
        results: list[dict] = []
        is_coldstart = self._settings.get("coldstart", False)

        creators = [c for c in self._video_config.get("creators", []) if c.get("enabled", False)]
        self.stats.sources_enabled = len(creators)
        if not creators:
            logger.info("无视频源配置，跳过视频采集")
            return results

        # NLM 日配额硬上限（缺陷 #12）：按账本统计今日已入库数扣减，多次触发也不超额
        quota_remaining = self._remaining_daily_quota()
        if quota_remaining <= 0:
            logger.warning(
                "[配额硬上限] NLM 日配额 %d 今日已用尽，本轮不处理任何视频",
                self._nlm_daily_quota(),
            )
            return results

        # 1. quality_first 排序：quality tier 先处理
        if self._settings.get("process_order") == "quality_first":
            creators = sorted(creators, key=lambda c: (0 if c.get("tier") == "quality" else 1))

        # 2. 检测所有 creator 的候选视频（L1-L4 过滤在 _detect_new_videos 内）
        all_candidates: list[dict] = []
        for creator in creators:
            try:
                new_videos = self._detect_new_videos(creator)
                logger.info(f"[{creator['name']}] 检测到 {len(new_videos)} 个新视频")
            except Exception as e:
                logger.error(f"[{creator['name']}] 视频检测失败: {e}")
                self.stats.creator_errors.append(f"{creator['name']}: {e}")
                continue

            # L6: per-creator cap
            max_per_day = (
                self._settings.get("coldstart_max_per_day", 2) if is_coldstart
                else creator.get("max_per_day", 1)
            )
            for video in new_videos[:max_per_day]:
                all_candidates.append({"video": video, "creator": creator})

        logger.info(f"候选视频总数（8 层过滤后）: {len(all_candidates)}")
        self.stats.candidates_after_filters = len(all_candidates)

        # 3. L5: LLM 话题聚类去重
        if self._settings.get("dedup_strategy") == "llm_cluster" and len(all_candidates) > 1:
            all_candidates = self._dedup_by_topic(all_candidates)

        # 3.5 同轮内身份去重：同一视频可能以不同 URL 形态（youtu.be / watch?v=&t=）
        # 出现在多个 creator 列表里；账本此刻还没有它们，L1/L7 都拦不住。
        all_candidates = self._dedup_by_identity(all_candidates)

        # 4. NLM 配额回填（日常模式下）
        if not is_coldstart and len(all_candidates) < quota_remaining:
            backfill = self._backfill_quota(
                all_candidates,
                creators,
                quota_remaining - len(all_candidates),
            )
            if backfill:
                all_candidates.extend(backfill)
                logger.info(
                    f"配额回填: +{len(backfill)} 个视频 "
                    f"(总计 {len(all_candidates)}/{quota_remaining})"
                )

        # 4.5 配额硬截断（缺陷 #12）：超额候选写回 pending 等下轮，不静默丢弃
        if len(all_candidates) > quota_remaining:
            deferred = all_candidates[quota_remaining:]
            all_candidates = all_candidates[:quota_remaining]
            logger.warning(
                "[配额硬上限] NLM 日配额 %d，今日剩余 %d；推迟 %d 个候选到下轮: %s",
                self._nlm_daily_quota(),
                quota_remaining,
                len(deferred),
                [c["video"].get("title", "?") for c in deferred],
            )
            self._save_pending(deferred, increment_retry=False)

        # 5. 逐个处理
        for candidate in all_candidates:
            if time.time() - budget_start >= VIDEO_BUDGET_SECONDS:
                logger.info("视频采集预算耗尽，停止")
                break
            try:
                result = self._process_video(candidate["video"], candidate["creator"])
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"视频处理失败 [{candidate['video'].get('title', '?')}]: {e}")

        # L7: 去重库 URL 去重（过滤已入库视频）
        if results:
            deduped, skipped_count = self._l7_catalog_dedup(results)
            results = deduped
            if skipped_count:
                logger.info(f"[L7] URL 去重: 过滤 {skipped_count} 个已入库视频")

        elapsed = int(time.time() - budget_start)
        self.stats.elapsed_sec = elapsed
        logger.info(f"视频采集完成: {len(results)} 个视频, {elapsed}s / {VIDEO_BUDGET_SECONDS}s")
        # 分层漏斗一行留档：0 产出时"卡在哪一层"必须不靠 grep 就能看出（宪法 P4）。
        logger.info(
            "[漏斗] 源 %d | 列表 %d → 去重 -%d → 时长 -%d → 关键词 -%d → 相关性 -%d "
            "→ 近似日期超窗 -%d → 精确日期超窗 -%d → 日期未知拒收 -%d "
            "→ 候选 %d → 产出 %d（日期补取 %d/%d，成功 %d 失败 %d）",
            self.stats.sources_enabled, self.stats.raw_listed,
            self.stats.dropped_dedup, self.stats.dropped_duration,
            self.stats.dropped_keyword, self.stats.dropped_relevance,
            self.stats.rejected_date_prefilter, self.stats.dropped_out_of_window,
            self.stats.rejected_date_unknown, self.stats.candidates_after_filters,
            len(results), self.stats.date_backfill_used,
            self.stats.date_backfill_budget, self.stats.date_backfill_ok,
            self.stats.date_backfill_failed,
        )
        # "0 个新视频" 与 "全被时间闸拒收（cookies 失效 / yt-dlp 取不到日期）"必须可区分
        # （宪法 P4）：否则采集能力整体失效会长期伪装成"今天没有新视频"。
        if self._date_unknown_rejected:
            logger.warning(
                "[时间闸] 本轮 %d 个候选因无法确定发布日期被拒收（产出 %d 个）——"
                "若持续为 0 产出，请检查 yt-dlp cookies 与日期补取是否可用",
                self._date_unknown_rejected, len(results),
            )
        if self.stats.date_backfill_budget_exhausted:
            # 预算撞顶不等于"没有新视频"：撞顶时后续候选一律日期未知 → 全拒。
            # 排序靠后的 creator 会整批消失，且现象与"今天没更新"完全一样。
            logger.warning(
                "[时间闸] 发布日期补取预算 %d 本轮已撞顶（实际用 %d，耗时 %ds / %ds）——"
                "撞顶后的候选一律按日期未知拒收，排序靠后的源会整批消失；"
                "请检查列表阶段是否返回近似日期（%s）",
                self.stats.date_backfill_budget, self.stats.date_backfill_used,
                elapsed, VIDEO_BUDGET_SECONDS, VIDEO_YT_LIST_EXTRACTOR_ARGS,
            )
        if self._url_missing_rejected:
            logger.warning(
                "[身份闸] 本轮 %d 个候选因缺 url（无稳定身份）被拒收（产出 %d 个）——"
                "上游元数据缺失，检查 yt-dlp 输出字段",
                self._url_missing_rejected, len(results),
            )
        expander = self.shortlink_expander
        if expander.failures or expander.budget_exhausted:
            # 未展开的短链身份会与展开后的完整链接分裂 → 同一视频可能被研究两次。
            # 只是多烧额度（不丢内容），但不得静默（宪法 P4）。
            logger.warning(
                "[短链] 本轮展开 %d 次（成功 %d，失败 %d，预算耗尽跳过 %d）——"
                "未展开的短链与完整链接是两个身份，去重对它们无效",
                expander.expansions_used, expander.expanded_ok,
                expander.failures, expander.budget_exhausted,
            )
        return results

    # ------------------------------------------------------------------
    # 视频检测
    # ------------------------------------------------------------------

    def _detect_new_videos(self, creator: dict) -> list[dict]:
        """检测 creator 近 N 天的新视频，排除已入库的。

        时间闸契约（缺陷 #12）：`days_lookback` 与 `start_date` 都必须真正生效。
        `--flat-playlist` 对 YouTube 从不返回 `upload_date`（实测：608 份产出里只有
        1 份 frontmatter 带 `published_at`），原实现 `if upload_date:` 守卫让空值直接跳过两道闸
        —— 2025 年的老视频因此永驻候选集。现在缺日期先补取，补不到就拒收。

        **过滤顺序（2026-07-28 事故修复）**：时间闸是整条链上唯一要花 per-video
        网络调用的一层（`_fill_upload_date`），却排在时长/关键词/相关性三道**纯本地**
        过滤之前 —— 于是 40 次补取预算被大量注定要被本地过滤丢掉的候选吃光。
        实测当天真实漏斗：列表 326 → L1 去重后 222（全部要补日期，预算只有 40）
        → 时长 215 → **相关性 18** → 窗内 9。即 92% 的补取花在 L4.5 会免费丢掉的
        候选上，而排序靠后的源一个日期都拿不到，109 个候选全被"日期未知"拒收。
        现在纯本地的三层先跑，时间闸最后跑：补取需求从 222 降到 18，稳稳落在预算内。
        层号（L1/L2/L3…）保持原编号不变，变的只是执行顺序。
        """
        platform = creator["platform"]
        days = self._settings.get("days_lookback", VIDEO_DAYS_LOOKBACK)

        if platform == "bilibili":
            videos = self._detect_bili_videos(creator, days)
        elif platform == "youtube":
            videos = self._detect_yt_videos(creator, days)
        else:
            logger.warning(f"未知平台: {platform}")
            return []

        # L1-L4 + L4.5 过滤
        filtered: list[dict] = []
        start_date_str = self._settings.get("start_date", "")
        start_date_cutoff: datetime | None = None
        if start_date_str:
            try:
                start_date_cutoff = datetime.strptime(start_date_str, "%Y-%m-%d")
            except ValueError:
                logger.warning(
                    "start_date 配置非法(%r)，该道时间闸本轮不生效", start_date_str
                )
        lookback_cutoff = datetime.now() - timedelta(days=days)

        self.stats.raw_listed += len(videos)
        for v in videos:
            # L0: 无 url 拒收 —— 没有 url 就没有稳定身份，去重、账本、研究全都做不了。
            # 原实现在 `is_ingested_url` 里打 warning 后 `return False` 放行，
            # fail-closed 在这条缝上是开的（无身份候选一路走到研究，且永远无法登记
            # → 每轮重跑）。
            if not v.get("url"):
                self._url_missing_rejected += 1
                self.stats.dropped_no_url += 1
                logger.warning(
                    "[身份闸] 拒收（候选缺 url，无稳定身份 → 无法去重也无法登记）: "
                    "[%s] %s",
                    v.get("platform", "?"), v.get("title", "?"),
                )
                continue
            # L0.5: 短链展开（残余风险 2）—— 必须在 L1 之前，否则 `b23.tv/<code>`
            # 落 sha256 兜底身份，与账本里展开后的 `bilibili:BV...` 对不上，去重失效。
            # 只对已知短链域名发网络请求，每候选一次，预算有界。
            v["url"] = self.shortlink_expander.expand(v["url"])
            # L1: URL 去重
            if self.is_ingested_url(v["url"]):
                self.stats.dropped_dedup += 1
                continue
            # L3: 时长过滤（纯本地，零成本 → 排在花网络调用的时间闸之前）
            duration = v.get("duration_minutes", 0)
            max_dur = self._settings.get("max_video_duration_minutes", VIDEO_MAX_DURATION_MIN)
            min_dur = self._settings.get("min_video_duration_minutes", VIDEO_MIN_DURATION_MIN)
            if max_dur > 0 and duration > max_dur:
                self.stats.dropped_duration += 1
                logger.info(f"跳过过长视频 ({duration}min): {v['title']}")
                continue
            if duration == 0:
                logger.info(
                    f"视频时长未知 (duration=0)，跳过时长过滤: "
                    f"[{v.get('platform', '?')}] {v['title']}"
                )
            elif duration < min_dur:
                self.stats.dropped_duration += 1
                logger.info(f"跳过过短视频 ({duration:.1f}min): {v['title']}")
                continue
            # L4: 关键词过滤（纯本地；默认跳过 — 已启用的博主本身即内容筛选）
            if not self._settings.get("skip_keyword_filter", True):
                keywords = creator.get("keywords", [])
                if keywords and not any(kw.lower() in v["title"].lower() for kw in keywords):
                    self.stats.dropped_keyword += 1
                    logger.info(f"跳过不匹配关键词的视频: {v['title']}")
                    continue
            # L4.5: 全局关键词相关性过滤（纯本地正则，默认启用；实测 215 → 18 的
            # 主力过滤层 —— 放在时间闸之后就等于把 92% 的补取预算烧在必被它丢掉的
            # 候选上，正是 2026-07-28 全灭的直接机制）
            if not self._settings.get("skip_relevance_filter", False):
                if not self._is_ai_relevant(v["title"]):
                    self.stats.dropped_relevance += 1
                    continue
            # L2: 时间闸（本层最贵 —— 唯一可能发 per-video 网络请求的一层，放最后）
            if not self._pass_time_gate(v, days, lookback_cutoff,
                                        start_date_cutoff, start_date_str):
                continue
            filtered.append(v)
        return filtered

    def _pass_time_gate(
        self,
        v: dict,
        days: int,
        lookback_cutoff: datetime,
        start_date_cutoff: datetime | None,
        start_date_str: str,
    ) -> bool:
        """L2 时间闸：近似日期免费预筛 → 精确日期补取 → fail-closed 判定。

        准入只认精确 `upload_date`：近似日期（`upload_date_approx`）是真实发布时间的
        **上界**，只能用来"确定性排除"（approx 已超窗 ⇒ 真实必然超窗），不能用来放行
        （approx 在窗内不代表真实在窗内）。这条不对称性就是为什么近似值永远不写进
        `upload_date`（宪法 P3：不为了有产出放宽时间闸）。
        """
        # L2a: 近似日期预筛 —— 零网络成本，把注定超窗的候选挡在补取之前。
        if not v.get("upload_date"):
            approx = self._parse_upload_date(v.get("upload_date_approx", ""))
            if approx is not None:
                margin = timedelta(days=VIDEO_APPROX_DATE_MARGIN_DAYS)
                floor = lookback_cutoff
                if start_date_cutoff and start_date_cutoff > floor:
                    floor = start_date_cutoff
                if approx < floor - margin:
                    self.stats.rejected_date_prefilter += 1
                    logger.info(
                        "[时间闸] 近似日期 %s 已早于窗口下界 %s（含 %d 天边距），"
                        "免补取直接排除: %s",
                        v["upload_date_approx"], floor.strftime("%Y%m%d"),
                        VIDEO_APPROX_DATE_MARGIN_DAYS, v.get("title", "?"),
                    )
                    return False
        # L2b: 缺发布日期先补取，仍缺则拒收（不静默放行）
        if not v.get("upload_date"):
            self._fill_upload_date(v)
        upload_dt = self._parse_upload_date(v.get("upload_date", ""))
        if upload_dt is None:
            self._date_unknown_rejected += 1
            self.stats.rejected_date_unknown += 1
            logger.warning(
                "[时间闸] 拒收（无法确定发布日期，fail-closed）: [%s] %s — %s",
                v.get("platform", "?"), v.get("title", "?"), v.get("url", "?"),
            )
            return False
        if upload_dt < lookback_cutoff:
            self.stats.dropped_out_of_window += 1
            logger.info(
                "[时间闸] 超出回溯窗口 %d 天 (%s): %s",
                days, v["upload_date"], v.get("title", "?"),
            )
            return False
        if start_date_cutoff and upload_dt < start_date_cutoff:
            self.stats.dropped_out_of_window += 1
            logger.info(
                "[时间闸] 早于 start_date %s (%s): %s",
                start_date_str, v["upload_date"], v.get("title", "?"),
            )
            return False
        return True

    # ------------------------------------------------------------------
    # 时间闸辅助（缺陷 #12）
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_upload_date(raw: str) -> datetime | None:
        """解析 yt-dlp 的 `upload_date`（YYYYMMDD）；空值或非法格式返回 None。"""
        value = (raw or "").strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y%m%d")
        except ValueError:
            logger.warning("[时间闸] upload_date 格式非法(%r)，按未知处理", value)
            return None

    def _fill_upload_date(self, video: dict) -> None:
        """对缺 upload_date 的视频单独 `yt-dlp --dump-json` 补取真实发布日期。

        与 `_detect_bili_videos` 的 2 阶段补全同一范式。有界：单轮最多
        `VIDEO_MAX_DATE_BACKFILL_PER_RUN` 次（宪法 P1），预算耗尽时告警而不是
        静默放行——放行的后果就是 2025 年的老视频被反复研究。
        """
        url = video.get("url", "")
        if not url:
            return
        if self._date_backfill_used >= VIDEO_MAX_DATE_BACKFILL_PER_RUN:
            self.stats.date_backfill_budget_exhausted = True
            logger.warning(
                "[时间闸] 发布日期补取预算耗尽 (%d)，%s 保持日期未知（将被拒收）",
                VIDEO_MAX_DATE_BACKFILL_PER_RUN, video.get("title", url),
            )
            return

        self._date_backfill_used += 1
        self.stats.date_backfill_used = self._date_backfill_used
        try:
            detail = subprocess.run(
                [self.config.ytdlp_bin] + self._ytdlp_base_args +
                ["--dump-json", "--skip-download", url],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            self.stats.date_backfill_failed += 1
            logger.warning("[时间闸] 发布日期补取失败 %s: %s", url, exc)
            return

        if detail.returncode != 0 or not detail.stdout.strip():
            self.stats.date_backfill_failed += 1
            # 实测（2026-07-28）：连发 per-video `--dump-json` 会被 YouTube 判机器人
            # （"Sign in to confirm you're not a bot"），即使带 cookies 也一样 →
            # rc!=0 且无输出。这是本层最可能的真实失败形态，运维要看得懂。
            logger.warning(
                "[时间闸] 发布日期补取无输出 %s (rc=%s) —— 常见真因：平台限频"
                "（\"Sign in to confirm you're not a bot\"）或 cookies 失效；"
                "stderr 尾部: %s",
                url, detail.returncode, (detail.stderr or "").strip()[-300:],
            )
            return
        try:
            info = json.loads(detail.stdout.strip().split("\n")[0])
        except (json.JSONDecodeError, IndexError) as exc:
            self.stats.date_backfill_failed += 1
            logger.warning("[时间闸] 发布日期补取响应不可解析 %s: %s", url, exc)
            return

        upload_date = (info.get("upload_date") or "").strip()
        if upload_date:
            video["upload_date"] = upload_date
            self.stats.date_backfill_ok += 1
            logger.info("[时间闸] 补取到发布日期 %s: %s", upload_date, url)
        else:
            self.stats.date_backfill_failed += 1
            logger.warning("[时间闸] 平台未返回发布日期: %s", url)

        time.sleep(VIDEO_DATE_BACKFILL_SLEEP_SEC)

    # ------------------------------------------------------------------
    # 日配额（缺陷 #12）
    # ------------------------------------------------------------------

    def _nlm_daily_quota(self) -> int:
        """NLM 每日深研配额（配置优先，缺省用具名常量）。"""
        try:
            return int(self._settings.get("nlm_daily_quota", VIDEO_NLM_DAILY_QUOTA))
        except (TypeError, ValueError):
            logger.warning(
                "nlm_daily_quota 配置非法(%r)，回退到默认 %d",
                self._settings.get("nlm_daily_quota"), VIDEO_NLM_DAILY_QUOTA,
            )
            return VIDEO_NLM_DAILY_QUOTA

    def _remaining_daily_quota(self) -> int:
        """今日剩余配额 = 日配额 − 账本中今日已入库条数。

        缺陷 V-3：原实现用 `date.today().isoformat()`（**本地日**字符串）去比对
        `mark_ingested` 写入的 `datetime.now(UTC).isoformat()`（**UTC** 时刻）。
        CST(UTC+8) 下本地 00:00–08:00 入库的条目 UTC 日期还是前一天，字典序小于
        本地日字符串 → 同一本地日后续轮次数不到它们 → 当日实际入库量 =
        配额 + 漏算数（硬上限被时区错位突破）。现在两侧统一到 UTC 基准：
        把本地日起点换算成 UTC 时刻再做区间查询。
        """
        quota = self._nlm_daily_quota()
        since = local_day_start_utc_iso()
        used = self.db.count_ingested_since(since)
        if used:
            logger.info(
                "[配额] NLM 日配额 %d，本地日（起点 UTC %s）已入库 %d",
                quota, since, used,
            )
        return max(0, quota - used)

    # ------------------------------------------------------------------
    # 关键词过滤
    # ------------------------------------------------------------------

    def _load_global_keywords(self) -> tuple[list[re.Pattern], list[re.Pattern]]:
        """加载 config/keywords.json 的 include/exclude 关键词（编译为单词边界正则）。"""
        kw_path = self.config.keywords_path
        if not kw_path.exists():
            return [], []
        data = json.loads(kw_path.read_text(encoding="utf-8"))
        include = [kw.lower() for cat in data.get("include", {}).values() for kw in cat]
        exclude = [kw.lower() for cat in data.get("exclude", {}).values() for kw in cat]

        def compile_patterns(keywords: list[str]) -> list[re.Pattern]:
            patterns: list[re.Pattern] = []
            for kw in keywords:
                if len(kw) <= 3:
                    patterns.append(re.compile(
                        r'(?<![a-zA-Z0-9])' + re.escape(kw) + r'[a-zA-Z]*(?![a-zA-Z0-9])',
                        re.IGNORECASE,
                    ))
                else:
                    patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
            return patterns

        return compile_patterns(include), compile_patterns(exclude)

    def _is_ai_relevant(self, title: str) -> bool:
        """L4.5: 用全局关键词表判断视频标题是否与 AI 领域相关。"""
        if not hasattr(self, "_global_include"):
            self._global_include, self._global_exclude = self._load_global_keywords()
        # 排除词命中 → 不相关
        if any(p.search(title) for p in self._global_exclude):
            logger.info(f"L4.5 排除词命中，过滤: {title}")
            return False
        # 包含词命中任一 → 相关
        if any(p.search(title) for p in self._global_include):
            return True
        logger.info(f"L4.5 无关键词命中，过滤: {title}")
        return False

    # ------------------------------------------------------------------
    # 平台检测
    # ------------------------------------------------------------------

    def _detect_bili_videos(self, creator: dict, days: int) -> list[dict]:
        """通过 yt-dlp 获取 B 站用户近期视频列表。

        B 站 --flat-playlist 经常返回空 title / duration=0，导致后续 L4.5
        关键词过滤把所有视频丢掉。对这类视频追加单独的 --dump-json 调用
        补全 metadata（带 1.2s 间隔避免 IP 限频）。
        """
        uid = creator["uid"]
        url = f"https://space.bilibili.com/{uid}/video"
        cutoff = datetime.now() - timedelta(days=days)

        try:
            result = subprocess.run(
                [self.config.ytdlp_bin, "--flat-playlist", "--dump-json",
                 "--playlist-end", "20", url],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"bili 视频列表获取失败: {e}")
            return []

        videos: list[dict] = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
            upload_date = info.get("upload_date", "")
            if upload_date:
                try:
                    dt = datetime.strptime(upload_date, "%Y%m%d")
                    if dt < cutoff:
                        continue
                except ValueError:
                    pass
            duration_s = info.get("duration") or 0
            videos.append({
                "platform": "bilibili",
                "id": info.get("id", ""),
                "bv": info.get("id", ""),
                "title": info.get("title", ""),
                "url": (
                    info.get("url")
                    or info.get("webpage_url")
                    or f"https://www.bilibili.com/video/{info.get('id', '')}"
                ),
                "duration_minutes": duration_s / 60,
                "upload_date": upload_date,
                "creator": creator["name"],
            })

        # 2 阶段补全：对 title 为空或 duration=0 的视频单独抓取详细元数据
        incomplete = [v for v in videos
                      if not v["title"].strip() or v["duration_minutes"] == 0]
        if incomplete:
            logger.info(f"[{creator['name']}] {len(incomplete)}/{len(videos)} "
                        f"个视频元数据不全，启动 2 阶段补全")
            for idx, v in enumerate(incomplete):
                bv = v.get("bv") or v.get("id", "")
                if not bv:
                    continue
                video_url = f"https://www.bilibili.com/video/{bv}"
                try:
                    detail = subprocess.run(
                        [self.config.ytdlp_bin, "--dump-json", "--skip-download", video_url],
                        capture_output=True, text=True, timeout=30,
                    )
                    if detail.returncode != 0 or not detail.stdout.strip():
                        logger.debug(f"补全失败 {bv}: rc={detail.returncode}")
                        continue
                    info = json.loads(detail.stdout.strip().split("\n")[0])
                    title = info.get("title", "").strip()
                    if title:
                        v["title"] = title
                    duration_s = info.get("duration") or 0
                    if duration_s:
                        v["duration_minutes"] = duration_s / 60
                    upload_date = info.get("upload_date", "")
                    if upload_date and not v.get("upload_date"):
                        v["upload_date"] = upload_date
                except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError) as e:
                    logger.debug(f"补全异常 {bv}: {e}")
                # 间隔 1.2s 避免触发 B 站 IP 限频
                if idx < len(incomplete) - 1:
                    time.sleep(1.2)

            # 二次清洗：补全后仍空 title 的视频丢弃
            before = len(videos)
            videos = [v for v in videos if v["title"].strip()]
            dropped = before - len(videos)
            if dropped:
                self.stats.dropped_no_title += dropped
                logger.info(f"[{creator['name']}] 补全后仍无 title，丢弃 {dropped} 个")

        return videos

    def _detect_yt_videos(self, creator: dict, days: int) -> list[dict]:
        """通过 yt-dlp 获取 YouTube 频道近期视频列表。

        `--extractor-args youtubetab:approximate_date`（2026-07-28 事故修复）让这一次
        列表调用顺带返回**近似**发布日期，零额外网络成本。它落在 `upload_date_approx`
        而**不是** `upload_date`：近似值只用于 L2a 的确定性排除（它是真实日期的上界），
        准入判定仍然只认 per-video 补取来的精确日期。见 `constants` 里的推导。

        `days` 保留在签名里与 `_detect_bili_videos` 对齐，但本函数不再自行按日期
        截断：列表日期是近似值，精确判定统一在 `_pass_time_gate` 一处做，避免两处
        时间闸口径分裂（一处按近似值放行、一处按精确值拒收会非常难排查）。
        """
        channel_url = creator["channel_url"]

        try:
            result = subprocess.run(
                [self.config.ytdlp_bin] + self._ytdlp_base_args +
                ["--flat-playlist", "--dump-json",
                 "--playlist-end", "20",
                 "--extractor-args", VIDEO_YT_LIST_EXTRACTOR_ARGS,
                 f"{channel_url}/videos"],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"YouTube 视频列表获取失败: {e}")
            return []

        videos: list[dict] = []
        approx_seen = 0
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 列表阶段自带的日期一律按"近似"处理：开了 approximate_date 之后
            # `upload_date` 就是从"3 days ago"折算的近似值，不能当精确日期用。
            approx_date = (info.get("upload_date") or "").strip()
            if approx_date:
                approx_seen += 1
            duration_s = info.get("duration") or 0
            videos.append({
                "platform": "youtube",
                "id": info.get("id", ""),
                "title": info.get("title", ""),
                "url": (
                    info.get("url")
                    or info.get("webpage_url")
                    or f"https://www.youtube.com/watch?v={info.get('id', '')}"
                ),
                "duration_minutes": duration_s / 60,
                "upload_date": "",
                "upload_date_approx": approx_date,
                "creator": creator["name"],
            })
        if videos and not approx_seen:
            # 近似日期整批缺失 = 每个候选都要走 per-video 补取（预算 40 撞顶的老路）。
            # 不静默（宪法 P4）：这条 warning 就是"链路即将退化回 0 产出"的早期信号。
            logger.warning(
                "[时间闸] %s: 列表阶段 %d 条全无近似日期（extractor-args=%s 未生效？）"
                "—— 每个候选都将占用 per-video 补取预算 %d",
                creator["name"], len(videos), VIDEO_YT_LIST_EXTRACTOR_ARGS,
                VIDEO_MAX_DATE_BACKFILL_PER_RUN,
            )
        return videos

    # ------------------------------------------------------------------
    # L1 / L7: 入库去重
    # ------------------------------------------------------------------

    def is_ingested_url(self, url: str) -> bool:
        """L1: 该视频是否已处理过（在花任何 LLM/NLM 额度之前）。

        两个真值来源，任一命中即跳过：

        1. 去重账本（机器本地 sqlite）——快，但是单点：`AKB2_STATE_ROOT` 被改指向
           空目录、sqlite 损坏被重建、`seeded_from:` 标记误置成已 seed 而表是空的，
           都会让它整体失守；
        2. 已发布产出的身份索引（vault 只读扫描）——真值就是产出本身，与命名规则
           无关，所以覆盖 608 份旧命名历史产出（残余风险「视频存量覆盖」）。

        fail-closed：去重库缺失或不可用时 **抛异常**，不再 `return False`
        —— 原来的静默放行正是重复产出（实测 608 份产出只对应 230 个视频身份，
        271 份冗余）的直接出口。LLM 调用次数无遥测，不引用具体次数
        （见 `dedup_store` 模块 docstring 的实测口径说明）。
        """
        if self.db is None:
            raise RuntimeError(
                "去重库未注入（config.dedup_db_path 未解析）：拒绝在无去重保护下采集"
            )
        if not url:
            # 无 url = 无稳定身份。原实现 warning 后 return False（放行）——
            # 无身份候选既去不了重也登记不了，放行等于每轮重跑。调用方必须显式拒收。
            raise ValueError("候选缺 url，无稳定身份：拒绝在无去重保护下继续")
        if self.db.is_ingested(url):
            return True
        existing = self.published_index.lookup(url)
        if existing:
            logger.warning(
                "[L1] 账本无记录但历史产出已存在（账本可能失守：状态根被改/库被重建/"
                "seed 标记误置）→ 跳过以免重烧额度: %s → 《%s》",
                url, existing,
            )
            return True
        return False

    def _l7_catalog_dedup(self, results: list[dict]) -> tuple[list[dict], int]:
        """L7: 去重库 URL 去重（L1 之后的兜底，过滤本轮内已入库视频）。

        无 url 的产出同样拒收（fail-closed）：它不可能被登记，留着就是下轮重跑。
        """
        deduped: list[dict] = []
        skipped_count = 0
        for r in results:
            url = r["video"].get("url", "")
            if not url:
                logger.warning(
                    "[L7] 拒收（缺 url，无稳定身份 → 无法登记，留着必然重跑）: %s",
                    r["video"].get("title", "?"),
                )
                skipped_count += 1
                continue
            if self.is_ingested_url(url):
                logger.info(f"[L7] 已入库，跳过: {r['video'].get('title', url)}")
                skipped_count += 1
            else:
                deduped.append(r)
        return deduped, skipped_count

    def mark_ingested(self, video: dict, output_path: Path | str | None = None) -> bool:
        """写端：产出已落地后登记视频身份（缺陷 #2 的"读端有、写端无"修复）。

        由 `assembler.run_pipeline` 在报告成功发布之后调用，绝不提前调用
        （提前 = 崩溃窗口里内容永久丢失；滞后 = 最多重跑一次）。

        Returns:
            True 新登记，False 之前已登记过。
        """
        if self.db is None:
            raise RuntimeError("去重库未注入：无法登记已处理视频")
        url = video.get("url", "")
        if not url:
            raise ValueError("mark_ingested: video 缺 url，无法登记稳定身份")
        return self.db.mark_ingested(
            url,
            platform=video.get("platform", ""),
            title=video.get("title", ""),
            output_path=output_path,
        )

    # ------------------------------------------------------------------
    # 同轮内身份去重
    # ------------------------------------------------------------------

    def _dedup_by_identity(self, candidates: list[dict]) -> list[dict]:
        """同一轮内按稳定身份去重（保留首次出现，丢弃项显式记日志）。"""
        seen: set[str] = set()
        kept: list[dict] = []
        for c in candidates:
            url = c["video"].get("url", "")
            if not url:
                # 原实现 `kept.append(c)`：无身份候选被放行 → 同一无 url 视频可以
                # 重复出现且永远登记不上。fail-closed 拒收。
                logger.warning(
                    "[同轮去重] 拒收（缺 url，无稳定身份）: %s",
                    c["video"].get("title", "?"),
                )
                continue
            identity = video_identity(url)
            if identity in seen:
                logger.info(
                    "[同轮去重] 同一视频重复出现(%s)，丢弃: %s",
                    identity, c["video"].get("title", url),
                )
                continue
            seen.add(identity)
            kept.append(c)
        return kept

    # ------------------------------------------------------------------
    # L5: LLM 话题聚类去重
    # ------------------------------------------------------------------

    def _dedup_by_topic(self, candidates: list[dict]) -> list[dict]:
        """调用 LLM 对视频标题做话题聚类，同话题保留最长视频。

        降级策略（缺陷 #12 附带项的裁决）：本层是**省钱的优化**，不是安全闸。
        adapter 缺失或响应不可解析时保持"不去重"降级而不 fail-closed —— 让全天
        的运行因为一次分类调用失败而中断，代价高于多研究一两个同话题视频；且超额
        风险已被 `_remaining_daily_quota` 的日硬上限接住（缺陷 #12 主项）。
        但降级必须**说清后果**（宪法 P4），不能只留一行"跳过去重"。
        """
        if not self._llm_adapter:
            logger.warning(
                "[话题去重] LLM adapter 未初始化 → 本轮不做同话题去重，"
                "%d 个候选按原样进入研究（同话题可能被重复研究；总量仍受日配额硬上限约束）",
                len(candidates),
            )
            return candidates

        titles = [f"{i}: {c['video']['title']}" for i, c in enumerate(candidates)]
        prompt = (
            "以下是今天待处理的视频标题列表。请将讲述同一话题/事件的视频分组。\n"
            '输出 JSON: {"groups": [[index1, index2], [index3], ...]}\n'
            "只有真正讲同一件事的才分到一组，不要过度合并。\n\n"
            + "\n".join(titles)
        )

        try:
            resp = self._llm_adapter.chat(
                task="classify",
                system="你是一个视频话题分类助手。",
                user=prompt,
                temperature=0.1,
            )
            data = extract_json_from_llm_response(resp)
            if not data or "groups" not in data:
                logger.warning(
                    "[话题去重] LLM 响应不含 groups → 本轮不做同话题去重，"
                    "%d 个候选按原样进入研究（受日配额硬上限约束）",
                    len(candidates),
                )
                return candidates
            groups = data["groups"]
        except Exception as e:
            logger.warning(
                "[话题去重] 分类调用失败(%s) → 本轮不做同话题去重，"
                "%d 个候选按原样进入研究（受日配额硬上限约束）",
                e, len(candidates),
            )
            return candidates

        kept_indices: list[int] = []
        deduped_count = 0
        covered: set[int] = set()
        for group in groups:
            valid = [i for i in group if isinstance(i, int) and 0 <= i < len(candidates)]
            if not valid:
                continue
            covered.update(valid)
            best = max(valid, key=lambda i: candidates[i]["video"].get("duration_minutes", 0))
            kept_indices.append(best)
            if len(valid) > 1:
                skipped = [candidates[i]["video"]["title"] for i in valid if i != best]
                deduped_count += len(skipped)
                logger.info(f"话题去重: 保留 「{candidates[best]['video']['title']}」, "
                            f"跳过 {skipped}")

        # LLM 未提到的索引必须保留（宪法 P4）：原实现只遍历 groups，漏掉的候选
        # 被静默丢弃——分类模型少写一个下标就等于一个视频永久消失。
        missing = [i for i in range(len(candidates)) if i not in covered]
        if missing:
            logger.warning(
                "[话题去重] LLM 分组未覆盖 %d 个候选(索引 %s)，按未去重全部保留: %s",
                len(missing), missing,
                [candidates[i]["video"].get("title", "?") for i in missing],
            )
            kept_indices.extend(missing)

        # 去掉重复下标（LLM 可能把同一下标写进多组），按原顺序输出
        ordered = sorted(set(kept_indices))
        kept = [candidates[i] for i in ordered]

        if deduped_count:
            logger.info(f"话题去重: {len(candidates)} → {len(kept)} (去除 {deduped_count} 个重复)")
        return kept

    # ------------------------------------------------------------------
    # NLM 配额回填
    # ------------------------------------------------------------------

    def _backfill_quota(
        self, existing: list[dict], creators: list[dict], slots: int
    ) -> list[dict]:
        """配额回填：尽量用满 NLM 每日配额。"""
        backfill: list[dict] = []
        existing_urls = {c["video"]["url"] for c in existing}

        # 策略 1: 已达上限的 creator，允许多抓 1 个
        for creator in creators:
            if len(backfill) >= slots:
                break
            try:
                extras = self._detect_new_videos(creator)
            except Exception:
                continue
            for v in extras:
                if v["url"] not in existing_urls and len(backfill) < slots:
                    backfill.append({"video": v, "creator": creator, "backfill": True})
                    existing_urls.add(v["url"])

        # 策略 2: 深挖积压
        skip_relevance = self._settings.get("skip_relevance_filter", False)
        if len(backfill) < slots:
            pending_path = self._pending_path()
            if pending_path.exists():
                try:
                    pending = json.loads(pending_path.read_text(encoding="utf-8"))
                    for item in pending:
                        if len(backfill) >= slots:
                            break
                        url = item.get("url", "")
                        if url and url not in existing_urls:
                            if not skip_relevance and not self._is_ai_relevant(
                                item.get("title", "")
                            ):
                                continue
                            backfill.append({
                                "video": item,
                                "creator": None,
                                "backfill": True,
                            })
                            existing_urls.add(url)
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"读取 pending_research.json 失败: {e}")

        return backfill

    # ------------------------------------------------------------------
    # 单个视频处理
    # ------------------------------------------------------------------

    def _process_video(self, video: dict, creator: dict | None) -> dict | None:
        """视频已通过过滤，直接返回元数据（内容提取由 NLM 处理）。"""
        logger.info(f"视频通过过滤: [{video['platform']}] {video['title']}")
        return {
            "video": video,
            "creator": creator,
        }

    # ------------------------------------------------------------------
    # Pending research 文件操作（带 flock 保护 — 修复 #7）
    # ------------------------------------------------------------------

    def _pending_path(self) -> Path:
        """返回 pending_research.json 的完整路径。"""
        return self.config.resolve_pending_path()

    def _save_pending(
        self, results: list[dict], increment_retry: bool = True
    ) -> None:
        """将失败/跳过的视频追加到 pending_research.json（带文件锁保护）。"""
        pending_path = self._pending_path()
        pending_path.parent.mkdir(parents=True, exist_ok=True)

        # fcntl.flock 保护 read-modify-write
        with open(pending_path, "a+") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            try:
                f.seek(0)
                existing: list[dict] = []
                raw = f.read()
                if raw.strip():
                    # 损坏文件（git 冲突标记 / 半写 / 被写成非数组）绝不静默丢历史：
                    # 先把原文备份到 .corrupt-<ts>，再以空集继续，历史可从备份追回。
                    corrupt_reason: str | None = None
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        parsed = None
                        corrupt_reason = f"JSON 解析失败: {exc}"
                    else:
                        if isinstance(parsed, list):
                            existing = parsed
                        else:
                            corrupt_reason = f"非数组(实际 {type(parsed).__name__})"
                    if corrupt_reason is not None:
                        backup = pending_path.with_suffix(
                            f".corrupt-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
                        )
                        backup.write_text(raw, encoding="utf-8")
                        logger.error(
                            "pending_research.json 损坏(%s),已备份历史到 %s 后继续",
                            corrupt_reason,
                            backup,
                        )

                url_to_idx: dict[str, int] = {
                    item.get("url"): idx
                    for idx, item in enumerate(existing)
                    if item.get("url")
                }
                for result in results:
                    video = result.get("video", {})
                    url = video.get("url", "")
                    if not url:
                        continue
                    if url in url_to_idx:
                        if increment_retry:
                            existing[url_to_idx[url]]["retry_count"] = (
                                existing[url_to_idx[url]].get("retry_count", 1) + 1
                            )
                    else:
                        new_entry = {
                            "url": url,
                            "title": video.get("title", ""),
                            "platform": video.get("platform", ""),
                            "creator": video.get("creator", ""),
                            "added_at": datetime.now().isoformat(),
                            "retry_count": 1 if increment_retry else 0,
                        }
                        existing.append(new_entry)
                        url_to_idx[url] = len(existing) - 1

                f.seek(0)
                f.truncate()
                json.dump(existing, f, ensure_ascii=False, indent=2)
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

    def _remove_from_pending(self, result: dict) -> None:
        """研究成功后，从 pending_research.json 中移除（带文件锁保护）。"""
        pending_path = self._pending_path()
        if not pending_path.exists():
            return
        url = result.get("video", {}).get("url", "")
        if not url:
            return

        with open(pending_path, "r+") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            try:
                try:
                    pending = json.loads(f.read())
                except (json.JSONDecodeError, OSError):
                    return
                pending = [item for item in pending if item.get("url") != url]
                f.seek(0)
                f.truncate()
                json.dump(pending, f, ensure_ascii=False, indent=2)
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
