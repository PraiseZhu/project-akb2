"""视频 Deep Research 自动化 — VideoResearcher。

从旧 AKB scripts/pipeline/video_researcher.py 迁移，关键改动：
- 路径外部化：NLM_BIN/NOTEBOOKLM_BIN/OBSIDIAN_RESEARCH_DIR → config 注入
- 子进程孤儿防护：Popen(start_new_session=True) + killpg(SIGKILL)
- 健康检查降级：逐视频检查，非整批放弃
- ResearchOutput dataclass 产出，不写 staging/Obsidian/DB（所有 durable write 归 Assembler）
- 无 macOS 通知（用结构化日志替代）
- 无 IngestGate / research_ingest / CatalogDB 依赖
"""

from __future__ import annotations

import fcntl
import glob
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.akb2.video.config import VideoConfig
from src.akb2.video.constants import (
    VIDEO_NLM_MAX_WAIT,
    VIDEO_PERMANENT_FAILURE_THRESHOLD,
)
from src.akb2.video.llm_adapter import LLMAdapter

logger = logging.getLogger("akb2.video.researcher")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class NLMQuotaExhausted(Exception):
    """两个 NLM profile 配额都用完。"""


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class ResearchOutput:
    """Researcher 的唯一产出 — 不写任何 durable storage。

    所有文件写入（staging/candidate/、Obsidian、DB）由 Assembler (step-4) 负责。
    """

    report_markdown: str
    """NLM 报告正文或降级 LLM 摘要（markdown）。"""

    notebook_id: str | None
    """NLM notebook ID（降级路径为 None）。"""

    video_meta: dict
    """collector 产出的视频元数据（title, url, platform, creator, upload_date）。"""

    degraded: bool = False
    """是否走了降级路径（yt-dlp 字幕 + LLM）。"""

    nlm_profile_used: str | None = None
    """实际使用的 NLM profile（default / alt / None=降级）。"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列（颜色/样式码），避免污染 notebook ID 等解析值。"""
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# VideoResearcher
# ---------------------------------------------------------------------------


class VideoResearcher:
    """对视频执行 NLM Deep Research → 产出 ResearchOutput（不写 durable storage）。"""

    def __init__(
        self,
        config: VideoConfig | None = None,
        llm_adapter: LLMAdapter | None = None,
    ):
        self.config = config or VideoConfig()
        self._llm_adapter = llm_adapter

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self, video_results: list[dict]) -> list[ResearchOutput]:
        """处理 video_collector 的输出列表，返回 ResearchOutput 列表。

        每个视频独立处理：auth 过期 → 降级到 yt-dlp 字幕路径，不整批丢弃。
        """
        outputs: list[ResearchOutput] = []
        failed: list[dict] = []
        quota_skipped: list[dict] = []

        for i, result in enumerate(video_results):
            try:
                output = self._research_single(result)
                if output is not None:
                    outputs.append(output)
                else:
                    failed.append(result)
            except NLMQuotaExhausted:
                logger.warning("NLM 配额用完，剩余视频标记 pending")
                quota_skipped.append(result)
                quota_skipped.extend(video_results[i + 1:])
                break
            except Exception as e:
                logger.error(f"视频研究失败 [{result.get('video', {}).get('title', '?')}]: {e}")
                failed.append(result)

        # 持久化 pending 状态
        if failed:
            self._save_pending(failed, increment_retry=True)
        if quota_skipped:
            self._save_pending(quota_skipped, increment_retry=False)

        logger.info(f"视频研究完成: {len(outputs)}/{len(video_results)} 成功")
        return outputs

    # ------------------------------------------------------------------
    # 单视频研究
    # ------------------------------------------------------------------

    def _research_single(self, result: dict) -> ResearchOutput | None:
        """单个视频的完整 Deep Research 流程。

        Returns:
            ResearchOutput 成功，None 失败。
        """
        video = result.get("video", {})
        title = video.get("title", "")
        url = video.get("url", "")
        platform = video.get("platform", "youtube")

        if not url:
            logger.error(f"视频缺少 URL: {title}")
            return None

        logger.info(f"开始研究: {title}")

        profile = self.config.nlm_profile
        report_content: str | None = None
        used_fallback = False
        notebook_id: str | None = None

        # 逐视频健康检查：auth 可用？
        auth_ok = self._health_check(profile)

        if auth_ok:
            # 正常 NLM 路径
            try:
                report_content, notebook_id, profile = self._nlm_research_path(
                    video, title, url, platform, profile
                )
            except NLMQuotaExhausted:
                raise
            except Exception as e:
                logger.warning(f"NLM 路径失败: {e}，尝试降级")

            if not report_content:
                logger.warning(f"NLM 研究失败，降级到转录摘要: {title}")
                report_content = self._fallback_transcript_research(video)
                used_fallback = True
        else:
            # auth 不可用 → 直接降级
            logger.warning(f"NLM auth 不可用，降级到转录摘要: {title}")
            report_content = self._fallback_transcript_research(video)
            used_fallback = True

        if not report_content:
            logger.error(f"研究失败（含降级路径）: {title}")
            return None

        return ResearchOutput(
            report_markdown=report_content,
            notebook_id=notebook_id,
            video_meta=video,
            degraded=used_fallback,
            nlm_profile_used=profile if not used_fallback else None,
        )

    # ------------------------------------------------------------------
    # NLM 正常路径
    # ------------------------------------------------------------------

    def _nlm_research_path(
        self, video: dict, title: str, url: str, platform: str, profile: str
    ) -> tuple[str | None, str | None, str]:
        """完整的 NLM Deep Research 流程。

        Returns:
            (report_content, notebook_id, profile_used) 或 raise NLMQuotaExhausted。
        """
        nb_title = f"[Video] {title} - {datetime.now().strftime('%Y-%m-%d')}"
        nb_id = self._nlm_create_notebook(nb_title, profile)
        if not nb_id:
            raise NLMQuotaExhausted(f"Notebook 创建失败: {title}")

        # 添加源
        if platform == "youtube":
            source_ok = self._nlm_add_youtube(nb_id, url, profile)
        else:
            source_ok = self._nlm_add_url(nb_id, url, profile)

        if not source_ok:
            # 尝试 profile 轮替
            logger.info(f"尝试切换 profile: {profile} → {self.config.nlm_alt_profile}")
            profile = self.config.nlm_alt_profile
            nb_id = self._nlm_create_notebook(nb_title, profile)
            if not nb_id:
                raise NLMQuotaExhausted("备用 profile 也失败")
            if platform == "youtube":
                source_ok = self._nlm_add_youtube(nb_id, url, profile)
            else:
                source_ok = self._nlm_add_url(nb_id, url, profile)
            if not source_ok:
                raise NLMQuotaExhausted("备用 profile 源添加失败")

        # 启动 Deep Research
        research_query = f"深入分析这个技术视频的核心内容、关键概念和实践要点: {title}"
        research_ok = self._nlm_research(nb_id, research_query, profile)

        if not research_ok:
            # 尝试 profile 轮替
            logger.info(f"尝试切换 profile: {profile} → {self.config.nlm_alt_profile}")
            profile = self.config.nlm_alt_profile
            nb_id = self._nlm_create_notebook(nb_title, profile)
            if not nb_id:
                raise NLMQuotaExhausted("备用 profile 也失败")
            if platform == "youtube":
                source_ok = self._nlm_add_youtube(nb_id, url, profile)
            else:
                source_ok = self._nlm_add_url(nb_id, url, profile)
            if not source_ok:
                raise NLMQuotaExhausted("备用 profile 源添加失败")
            research_ok = self._nlm_research(nb_id, research_query, profile)
            if not research_ok:
                raise NLMQuotaExhausted("两个 profile 配额都用完")

        # 等待研究完成
        nlm_success = self._nlm_wait_research(nb_id, profile)

        if nlm_success:
            # 导入发现的源
            self._nlm_import(nb_id, profile)
            report_content = self._generate_and_download_report(nb_id, title)
            return report_content, nb_id, profile
        else:
            return None, nb_id, profile

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    def _health_check(self, profile: str) -> bool:
        """前置健康检查：nlm CLI 可用 + 当前 profile auth 未过期。

        逐视频检查（非整批），失败时记录结构化日志（不弹 macOS 通知）。
        """
        output = self._nlm("notebook", "list", profile=profile, timeout=30)
        if output is None:
            logger.error(
                f"健康检查失败: nlm CLI 或 profile {profile} 凭据已过期 — "
                f"终端运行: nlm login"
            )
            return False
        return True

    # ------------------------------------------------------------------
    # 降级路径：yt-dlp 字幕 → LLM 摘要
    # ------------------------------------------------------------------

    def _fallback_transcript_research(self, video: dict) -> str | None:
        """NLM 不可用时的降级路径：yt-dlp 获取字幕 → LLM 生成摘要。"""
        url = video.get("url", "")
        title = video.get("title", "")
        platform = video.get("platform", "youtube")

        transcript = self._get_transcript(url, platform)
        if not transcript:
            logger.warning(f"降级研究: 无法获取转录 [{title}]")
            return None

        if not self._llm_adapter:
            logger.warning("降级研究: LLM adapter 未初始化，无法生成摘要")
            return None

        max_chars = 15000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n\n[转录内容已截断...]"

        try:
            summary = self._llm_adapter.chat(
                task="summarize",
                system=(
                    "你是一位专业的 AI/技术视频分析师。根据视频转录文本生成结构化分析报告。\n"
                    "报告格式:\n"
                    "# <视频标题>\n\n"
                    "> [!abstract] 摘要\n> <一段话总结核心内容>\n\n"
                    "## 核心观点\n<列出 3-5 个核心观点>\n\n"
                    "## 关键概念\n<提取关键技术概念和定义>\n\n"
                    "## 实操要点\n<可操作的建议或步骤>\n\n"
                    "## 工具/框架提及\n<提到的工具、库、框架>\n\n"
                    "请用中文输出，技术术语保留英文原文。"
                ),
                user=f"视频标题: {title}\n\n转录文本:\n{transcript}",
                temperature=0.3,
            )
            if summary and summary.strip():
                header = (
                    "> [!warning] 降级研究\n"
                    "> 此报告由字幕转录 + LLM 摘要生成（NLM Deep Research 不可用），"
                    "内容深度可能不足。\n\n"
                )
                return header + summary.strip()
        except Exception as e:
            logger.error(f"降级研究 LLM 摘要失败: {e}")

        return None

    def _get_transcript(self, url: str, platform: str) -> str | None:
        """通过 yt-dlp 获取视频字幕/自动字幕。"""
        tmpdir = tempfile.mkdtemp(prefix="akb-transcript-")
        try:
            proc = subprocess.run(
                [self.config.ytdlp_bin,
                 "--skip-download",
                 "--write-subs", "--write-auto-subs",
                 "--sub-lang", "en,zh-Hans,zh",
                 "--sub-format", "vtt",
                 "--convert-subs", "srt",
                 "-o", f"{tmpdir}/%(id)s",
                 url],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                logger.warning(f"yt-dlp 字幕获取失败 (exit {proc.returncode}): "
                               f"{proc.stderr[:200]}")

            srt_files = glob.glob(f"{tmpdir}/*.srt")
            if not srt_files:
                srt_files = glob.glob(f"{tmpdir}/*.vtt")
            if not srt_files:
                logger.warning(f"未找到字幕文件: {url}")
                return None

            srt_path = Path(srt_files[0])
            raw = srt_path.read_text(encoding="utf-8", errors="ignore")

            lines = []
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if re.match(r"^\d+$", line):
                    continue
                if re.match(r"\d{2}:\d{2}:\d{2}", line):
                    continue
                if line.startswith("WEBVTT"):
                    continue
                line = re.sub(r"<[^>]+>", "", line)
                if line:
                    lines.append(line)

            transcript = " ".join(lines)
            if len(transcript) < 100:
                logger.warning(f"转录内容太短 ({len(transcript)} chars): {url}")
                return None

            logger.info(f"获取转录: {len(transcript)} chars")
            return transcript

        except subprocess.TimeoutExpired:
            logger.error(f"yt-dlp 字幕获取超时: {url}")
            return None
        except Exception as e:
            logger.error(f"字幕获取失败: {e}")
            return None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # NLM CLI 操作
    # ------------------------------------------------------------------

    def _nlm(self, *args: str, profile: str = "alt",
             timeout: int = 60) -> str | None:
        """执行 NLM CLI 命令。使用 start_new_session + killpg 防护孤儿进程。"""
        cmd = [str(self.config.nlm_bin)] + list(args)
        if profile != "alt":
            cmd.extend(["--profile", profile])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # 创建新进程组，防止孤儿
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            if proc.returncode == 8:
                logger.warning(f"NLM 配额耗尽 (exit 8): {' '.join(args)}")
                return None
            if proc.returncode != 0:
                logger.warning(
                    f"nlm {' '.join(args)} failed (exit {proc.returncode}): "
                    f"{stderr[-500:]}"
                )
                return None
            return _strip_ansi(stdout.strip())
        except subprocess.TimeoutExpired:
            logger.error(f"nlm {' '.join(args)} timed out ({timeout}s)")
            # 清理进程组（包括孙进程）
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error(f"nlm 进程 {proc.pid} killpg 后仍无法回收")
            return None

    def _nlm_create_notebook(self, title: str, profile: str) -> str | None:
        """创建 notebook，返回 notebook_id（5 层 fallback 解析）。"""
        output = self._nlm("notebook", "create", title, profile=profile)
        if not output:
            return None
        logger.info(f"[DIAG-NLM-CREATE] raw output (len={len(output)}): "
                    f"{output[:300]!r}")

        nb_id: str | None = None
        # Layer 1: "ID: <uuid>" 格式
        for line in output.split("\n"):
            id_match = re.match(r"\s*ID:\s*(\S+)", line)
            if id_match:
                nb_id = id_match.group(1)
                break
        # Layer 2: UUID 格式
        if nb_id is None:
            uuid_match = re.search(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                output, re.IGNORECASE,
            )
            if uuid_match:
                nb_id = uuid_match.group(0)
        # Layer 3: JSON 解析
        if nb_id is None:
            try:
                data = json.loads(output)
                nb_id = data.get("id") or data.get("notebook_id")
            except json.JSONDecodeError:
                pass
        # Layer 4: 最后一行可能是 id
        if nb_id is None:
            clean = output.strip().split("\n")[-1].strip()
            if len(clean) > 8 and " " not in clean:
                nb_id = clean
        # Layer 5: 都失败
        if nb_id is None:
            logger.error(f"无法从输出中提取 notebook_id: {output[:200]}")
            return None
        logger.info(f"[DIAG-NLM-CREATE] parsed nb_id={nb_id}")
        return nb_id

    def _nlm_add_youtube(self, nb_id: str, url: str, profile: str) -> bool:
        output = self._nlm(
            "source", "add", nb_id, "--youtube", url, "--wait",
            profile=profile, timeout=180,
        )
        return output is not None

    def _nlm_add_url(self, nb_id: str, url: str, profile: str) -> bool:
        output = self._nlm(
            "source", "add", nb_id, "--url", url, "--wait",
            profile=profile, timeout=180,
        )
        return output is not None

    def _nlm_research(self, nb_id: str, query: str, profile: str) -> bool:
        output = self._nlm(
            "research", "start", query,
            "--notebook-id", nb_id,
            "--mode", "deep",
            "--source", "web",
            "--force",
            profile=profile, timeout=120,
        )
        return output is not None

    def _nlm_wait_research(self, nb_id: str, profile: str) -> bool:
        output = self._nlm(
            "research", "status", nb_id,
            "--poll-interval", "30",
            "--max-wait", str(VIDEO_NLM_MAX_WAIT),
            profile=profile, timeout=VIDEO_NLM_MAX_WAIT + 60,
        )
        if output and "completed" in output.lower():
            return True
        logger.warning(f"研究状态: {output[:200] if output else 'timeout'}")
        return False

    def _nlm_import(self, nb_id: str, profile: str) -> bool:
        output = self._nlm("research", "import", nb_id, profile=profile, timeout=180)
        return output is not None

    # ------------------------------------------------------------------
    # notebooklm-py 报告生成
    # ------------------------------------------------------------------

    def _generate_and_download_report(self, nb_id: str, title: str) -> str | None:
        """通过 notebooklm-py 生成并下载报告。"""
        logger.info(f"[DIAG-GEN-REPORT] nb_id={nb_id}, title={title[:60]!r}")

        slug = re.sub(r"[^\w-]", "", title.lower().replace(" ", "-"))[:40]
        report_path = f"/tmp/research-video-{slug}.md"

        # 生成报告
        try:
            gen_result = subprocess.run(
                [str(self.config.notebooklm_bin), "generate", "report",
                 "--notebook", nb_id,
                 "--format", "briefing-doc",
                 "--language", "zh_Hans",
                 "--append", f"生成关于视频「{title}」的全面技术分析报告",
                 "--wait"],
                capture_output=True, text=True, timeout=300,
            )
            if gen_result.returncode != 0:
                logger.error(
                    f"报告生成失败 (exit {gen_result.returncode}): "
                    f"{gen_result.stderr[-500:]}"
                )
                return None
        except subprocess.TimeoutExpired as e:
            logger.error(f"报告生成超时 (300s): {e}")
            return None

        # 下载报告
        dl_result = subprocess.run(
            [str(self.config.notebooklm_bin), "download", "report",
             "--notebook", nb_id, report_path],
            capture_output=True, text=True, timeout=60,
        )
        if dl_result.returncode != 0:
            logger.info(f"报告下载失败，重试一次: {dl_result.stderr[-200:]}")
            dl_result = subprocess.run(
                [str(self.config.notebooklm_bin), "download", "report",
                 "--notebook", nb_id, report_path],
                capture_output=True, text=True, timeout=60,
            )
            if dl_result.returncode != 0:
                logger.error(f"报告下载重试失败: {dl_result.stderr[-500:]}")
                return None

        report_file = Path(report_path)
        if not report_file.exists():
            logger.error(f"报告文件不存在: {report_path}")
            return None

        content = report_file.read_text(encoding="utf-8")
        report_file.unlink(missing_ok=True)
        return content

    # ------------------------------------------------------------------
    # Pending research 追踪（带 flock 保护）
    # ------------------------------------------------------------------

    def _pending_path(self) -> Path:
        return self.config.resolve_pending_path()

    def _save_pending(
        self, results: list[dict], increment_retry: bool = True
    ) -> None:
        """追加失败/跳过视频到 pending_research.json（带 fcntl.flock 保护）。"""
        pending_path = self._pending_path()
        pending_path.parent.mkdir(parents=True, exist_ok=True)

        with open(pending_path, "a+") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            try:
                f.seek(0)
                existing: list[dict] = []
                try:
                    raw = f.read()
                    if raw.strip():
                        existing = json.loads(raw)
                except (json.JSONDecodeError, OSError):
                    pass

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
        """研究成功后从 pending 移除（带 flock 保护）。"""
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