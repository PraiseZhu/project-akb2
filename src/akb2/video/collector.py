"""视频采集器 — VideoCollector。

从旧 AKB scripts/pipeline/video_collector.py 迁移，适配 AKB2 目录结构：
- 导入路径：旧 AKB scripts.utils.db → 内建轻量 CatalogDBAdapter
- yt-dlp 路径：self.config.ytdlp_bin（解决 #5 硬编码路径）
- L1-L7 + L4.5 共 8 层过滤链路保持不变
- 文件锁保护 pending_research.json（解决 #7 并发竞态）
- L5 LLM 话题聚类通过 llm_adapter.py 注入接口调用
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.akb2.video.config import VideoConfig
from src.akb2.video.constants import (
    VIDEO_BUDGET_SECONDS,
    VIDEO_DAYS_LOOKBACK,
    VIDEO_MAX_DURATION_MIN,
    VIDEO_MIN_DURATION_MIN,
)
from src.akb2.video.llm_adapter import LLMAdapter, extract_json_from_llm_response

logger = logging.getLogger("akb2.video.collector")


class CatalogDBAdapter:
    """轻量级 catalog.db 适配器 — 替代旧 AKB scripts.utils.db.CatalogDB。

    只提供 collector 需要的 connect() 方法，返回 sqlite3.Connection。
    不依赖旧 AKB 任何模块。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        """返回只读 sqlite3 连接。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


class VideoCollector:
    """检测新视频 → 返回元数据（内容提取由 NLM 直接处理）。"""

    def __init__(
        self,
        config: VideoConfig | None = None,
        db: CatalogDBAdapter | None = None,
        llm_adapter: LLMAdapter | None = None,
    ):
        self.config = config or VideoConfig()
        self.db = db
        self._llm_adapter = llm_adapter

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
        results: list[dict] = []
        is_coldstart = self._settings.get("coldstart", False)

        creators = [c for c in self._video_config.get("creators", []) if c.get("enabled", False)]
        if not creators:
            logger.info("无视频源配置，跳过视频采集")
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
                continue

            # L6: per-creator cap
            max_per_day = (
                self._settings.get("coldstart_max_per_day", 2) if is_coldstart
                else creator.get("max_per_day", 1)
            )
            for video in new_videos[:max_per_day]:
                all_candidates.append({"video": video, "creator": creator})

        logger.info(f"候选视频总数（8 层过滤后）: {len(all_candidates)}")

        # 3. L5: LLM 话题聚类去重
        if self._settings.get("dedup_strategy") == "llm_cluster" and len(all_candidates) > 1:
            all_candidates = self._dedup_by_topic(all_candidates)

        # 4. NLM 配额回填（日常模式下）
        nlm_quota = self._settings.get("nlm_daily_quota", 20)
        if not is_coldstart and len(all_candidates) < nlm_quota:
            backfill = self._backfill_quota(all_candidates, creators, nlm_quota - len(all_candidates))
            if backfill:
                all_candidates.extend(backfill)
                logger.info(f"配额回填: +{len(backfill)} 个视频 (总计 {len(all_candidates)}/{nlm_quota})")

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

        # L7: catalog.db URL 去重（过滤已入库视频）
        if results and self.db:
            deduped, skipped_count = self._l7_catalog_dedup(results)
            results = deduped
            if skipped_count:
                logger.info(f"[L7] URL 去重: 过滤 {skipped_count} 个已入库视频")

        elapsed = int(time.time() - budget_start)
        logger.info(f"视频采集完成: {len(results)} 个视频, {elapsed}s / {VIDEO_BUDGET_SECONDS}s")
        return results

    # ------------------------------------------------------------------
    # 视频检测
    # ------------------------------------------------------------------

    def _detect_new_videos(self, creator: dict) -> list[dict]:
        """检测 creator 近 N 天的新视频，排除已入库的。"""
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
                pass

        for v in videos:
            # L1: URL 去重
            if self._is_video_ingested(v["url"]):
                continue
            # L2: start_date 过滤
            if start_date_cutoff and v.get("upload_date"):
                try:
                    upload_dt = datetime.strptime(v["upload_date"], "%Y%m%d")
                    if upload_dt < start_date_cutoff:
                        continue
                except ValueError:
                    pass
            # L3: 时长过滤
            duration = v.get("duration_minutes", 0)
            max_dur = self._settings.get("max_video_duration_minutes", VIDEO_MAX_DURATION_MIN)
            min_dur = self._settings.get("min_video_duration_minutes", VIDEO_MIN_DURATION_MIN)
            if max_dur > 0 and duration > max_dur:
                logger.info(f"跳过过长视频 ({duration}min): {v['title']}")
                continue
            if duration == 0:
                logger.info(
                    f"视频时长未知 (duration=0)，跳过时长过滤: "
                    f"[{v.get('platform', '?')}] {v['title']}"
                )
            elif duration < min_dur:
                logger.info(f"跳过过短视频 ({duration:.1f}min): {v['title']}")
                continue
            # L4: 关键词过滤（默认跳过 — 已启用的博主本身即内容筛选）
            if not self._settings.get("skip_keyword_filter", True):
                keywords = creator.get("keywords", [])
                if keywords and not any(kw.lower() in v["title"].lower() for kw in keywords):
                    logger.info(f"跳过不匹配关键词的视频: {v['title']}")
                    continue
            # L4.5: 全局关键词相关性过滤（默认启用）
            if not self._settings.get("skip_relevance_filter", False):
                if not self._is_ai_relevant(v["title"]):
                    continue
            filtered.append(v)
        return filtered

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
                "url": info.get("url") or info.get("webpage_url") or f"https://www.bilibili.com/video/{info.get('id', '')}",
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
                logger.info(f"[{creator['name']}] 补全后仍无 title，丢弃 {dropped} 个")

        return videos

    def _detect_yt_videos(self, creator: dict, days: int) -> list[dict]:
        """通过 yt-dlp 获取 YouTube 频道近期视频列表。"""
        channel_url = creator["channel_url"]
        cutoff = datetime.now() - timedelta(days=days)

        try:
            result = subprocess.run(
                [self.config.ytdlp_bin] + self._ytdlp_base_args +
                ["--flat-playlist", "--dump-json",
                 "--playlist-end", "20",
                 f"{channel_url}/videos"],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"YouTube 视频列表获取失败: {e}")
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
                "platform": "youtube",
                "id": info.get("id", ""),
                "title": info.get("title", ""),
                "url": info.get("url") or info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id', '')}",
                "duration_minutes": duration_s / 60,
                "upload_date": upload_date,
                "creator": creator["name"],
            })
        return videos

    # ------------------------------------------------------------------
    # L1 / L7: 入库去重
    # ------------------------------------------------------------------

    def _is_video_ingested(self, url: str) -> bool:
        """检查该视频 URL 是否已在 catalog.db 中。"""
        if not self.db:
            return False
        try:
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM items WHERE url = ?", (url,)
                ).fetchone()
                return row is not None
        except sqlite3.OperationalError:
            return False

    def _l7_catalog_dedup(self, results: list[dict]) -> tuple[list[dict], int]:
        """L7: catalog.db URL 去重（过滤已入库视频）。"""
        if not self.db:
            return results, 0
        deduped: list[dict] = []
        skipped_count = 0
        try:
            with self.db.connect() as conn:
                for r in results:
                    url = r["video"].get("url", "")
                    if url and conn.execute(
                        "SELECT 1 FROM items WHERE url = ?", (url,)
                    ).fetchone():
                        logger.info(f"[L7] 已入库，跳过: {r['video'].get('title', url)}")
                        skipped_count += 1
                    else:
                        deduped.append(r)
        except sqlite3.OperationalError as e:
            logger.warning(f"L7 去重跳过 (DB 不可用): {e}")
            return results, 0
        return deduped, skipped_count

    # ------------------------------------------------------------------
    # L5: LLM 话题聚类去重
    # ------------------------------------------------------------------

    def _dedup_by_topic(self, candidates: list[dict]) -> list[dict]:
        """调用 LLM 对视频标题做话题聚类，同话题保留最长视频。"""
        if not self._llm_adapter:
            logger.warning("话题去重: LLM adapter 未初始化，跳过去重")
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
                logger.warning("话题去重: 无法解析 LLM 响应，跳过去重")
                return candidates
            groups = data["groups"]
        except Exception as e:
            logger.warning(f"话题去重失败: {e}，跳过去重")
            return candidates

        kept: list[dict] = []
        deduped_count = 0
        for group in groups:
            valid = [i for i in group if 0 <= i < len(candidates)]
            if not valid:
                continue
            best = max(valid, key=lambda i: candidates[i]["video"].get("duration_minutes", 0))
            kept.append(candidates[best])
            if len(valid) > 1:
                skipped = [candidates[i]["video"]["title"] for i in valid if i != best]
                deduped_count += len(skipped)
                logger.info(f"话题去重: 保留 「{candidates[best]['video']['title']}」, "
                            f"跳过 {skipped}")

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