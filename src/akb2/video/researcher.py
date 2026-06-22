"""视频 Deep Research 自动化 — VideoResearcher。

从旧 AKB scripts/pipeline/video_researcher.py 迁移，关键改动：
- 路径外部化：NLM_BIN/OBSIDIAN_RESEARCH_DIR → config 注入
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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.akb2.video.config import VideoConfig
from src.akb2.video.constants import (
    VIDEO_NLM_MAX_WAIT,
)
from src.akb2.video.llm_adapter import LLMAdapter

logger = logging.getLogger("akb2.video.researcher")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class NLMQuotaExhausted(Exception):
    """两个 NLM profile 配额都用完。"""


class NLMNetworkError(Exception):
    """NLM CLI 网络不可达（ConnectError/DNS/timeout）— 可重试，不应触发降级。"""


# ---------------------------------------------------------------------------
# 网络错误检测（nlm CLI 在未捕获异常时可能返回 exit 0，需检查 stderr）
# ---------------------------------------------------------------------------

_NETWORK_ERROR_PATTERNS: list[str] = [
    "ConnectError",
    "Connection refused",
    "Connection reset",
    "Name or service not known",
    "nodename nor servname provided",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "Connection timed out",
    "Traceback (most recent call last)",
]


def _is_network_error_stderr(stderr: str) -> bool:
    """检查 stderr 是否包含网络/连接错误特征。

    nlm CLI bug: 网络不可达时 httpx 抛未捕获异常 → Python traceback 输出到
    stderr 但进程 exit code 为 0。此函数通过 stderr 文本特征识别此类假成功。
    """
    if not stderr:
        return False
    return any(p in stderr for p in _NETWORK_ERROR_PATTERNS)


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

        # 逐视频健康检查：区分 auth 状态与网络状态
        health = self._health_check(profile)

        if health == "ok":
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
        elif health == "network_error":
            # 网络不可达（transient）→ 转 pending 重试，不降级
            # yt-dlp 同样需要网络，走降级也是注定失败的空跑
            logger.warning(f"网络不可达，视频转 pending 等待重试: {title}")
            return None
        else:
            # auth_expired 或其它未知状态 → 降级路径
            logger.warning(f"NLM auth 不可用 (health={health})，降级到转录摘要: {title}")
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
            (report_content, notebook_id, profile_used)。
            - report_content 非 None：NLM 研究成功。
            - report_content 为 None：本视频 NLM 处理失败（非配额），调用方应转
              字幕降级路径，且不影响其它视频。
        Raises:
            NLMQuotaExhausted: 仅当 _nlm 探测到 exit 8（真·全局配额耗尽）时由底层
              抛出并向上传播；此时调用方应停批并标 pending（等配额重置）。
        """
        nb_title = f"[Video] {title} - {datetime.now().strftime('%Y-%m-%d')}"
        nb_id = self._nlm_create_notebook(nb_title, profile)
        if not nb_id:
            logger.warning(f"NLM notebook 创建失败（非配额），转降级: {title}")
            return None, None, profile

        # 添加源
        if platform == "youtube":
            source_ok = self._nlm_add_youtube(nb_id, url, profile)
        else:
            source_ok = self._nlm_add_url(nb_id, url, profile)

        if not source_ok:
            # 尝试 profile 轮替；弃用原 notebook（GC：防 orphan）
            logger.info(f"尝试切换 profile: {profile} → {self.config.nlm_alt_profile}")
            old_nb_id = nb_id
            old_profile = profile
            profile = self.config.nlm_alt_profile
            nb_id = self._nlm_create_notebook(nb_title, profile)
            self._nlm_delete_notebook(old_nb_id, old_profile)
            if not nb_id:
                logger.warning(f"备用 profile notebook 创建失败（非配额），转降级: {title}")
                return None, None, profile
            if platform == "youtube":
                source_ok = self._nlm_add_youtube(nb_id, url, profile)
            else:
                source_ok = self._nlm_add_url(nb_id, url, profile)
            if not source_ok:
                logger.warning(
                    f"NLM 源处理失败（非配额，两 profile 均失败），转字幕降级: {title}"
                )
                self._nlm_delete_notebook(nb_id, profile)
                return None, None, profile

        # 启动 Deep Research
        research_query = f"深入分析这个技术视频的核心内容、关键概念和实践要点: {title}"
        research_ok = self._nlm_research(nb_id, research_query, profile)

        if not research_ok:
            # 尝试 profile 轮替；弃用原 notebook（GC：防 orphan）
            logger.info(f"尝试切换 profile: {profile} → {self.config.nlm_alt_profile}")
            old_nb_id = nb_id
            old_profile = profile
            profile = self.config.nlm_alt_profile
            nb_id = self._nlm_create_notebook(nb_title, profile)
            self._nlm_delete_notebook(old_nb_id, old_profile)
            if not nb_id:
                logger.warning(f"备用 profile notebook 创建失败（非配额），转降级: {title}")
                return None, None, profile
            if platform == "youtube":
                source_ok = self._nlm_add_youtube(nb_id, url, profile)
            else:
                source_ok = self._nlm_add_url(nb_id, url, profile)
            if not source_ok:
                logger.warning(f"备用 profile 源处理失败（非配额），转字幕降级: {title}")
                self._nlm_delete_notebook(nb_id, profile)
                return None, None, profile
            research_ok = self._nlm_research(nb_id, research_query, profile)
            if not research_ok:
                logger.warning(
                    f"NLM Deep Research 启动失败（非配额，两 profile 均失败），转降级: {title}"
                )
                self._nlm_delete_notebook(nb_id, profile)
                return None, None, profile

        # 等待研究完成
        nlm_success = self._nlm_wait_research(nb_id, profile)

        if nlm_success:
            # 导入发现的源
            self._nlm_import(nb_id, profile)
            report_content = self._generate_and_download_report(nb_id, title, profile)
            return report_content, nb_id, profile
        else:
            self._nlm_delete_notebook(nb_id, profile)
            return None, nb_id, profile

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    def _health_check(self, profile: str) -> str:
        """前置健康检查：区分 auth 状态与网络状态（逐视频，非整批）。

        直接调用 subprocess 以获取 stderr 文本，避免 _nlm 的抽象层丢失
        网络错误信号（nlm CLI bug: 网络不可达时 exit 0）。

        Returns:
            "ok":             auth 正常 — 可走 NLM 路径
            "network_error":  网络/CLI 不可达（transient）— 应转 pending 重试
            "auth_expired":   auth 过期/不可用 — 应走 yt-dlp 降级路径
        """
        cmd = [str(self.config.nlm_bin), "notebook", "list"]
        if profile != "alt":
            cmd.extend(["--profile", profile])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            logger.error("健康检查超时（网络不可达）")
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, UnboundLocalError):
                pass
            return "network_error"
        except FileNotFoundError:
            logger.error("健康检查: nlm CLI 不可执行（%s）", self.config.nlm_bin)
            return "network_error"
        except OSError as e:
            logger.error("健康检查: nlm CLI 启动失败: %s", e)
            return "network_error"

        if proc.returncode == 8:
            # 配额耗尽不代表 auth 失效：视为可用。后续首个视频进 NLM 路径会立刻
            # 再次抛配额异常，由 run() 的 break 分支停批标 pending。
            return "ok"

        stdout_text = stdout.strip()
        stderr_text = stderr.strip()
        combined_text = "\n".join(part for part in (stdout_text, stderr_text) if part)

        if proc.returncode == 0:
            if _is_network_error_stderr(combined_text):
                logger.warning(
                    "健康检查: 网络不可达（nlm CLI 返回 exit 0 但输出含网络错误特征）"
                )
                return "network_error"
            if stdout_text:
                return "ok"
            # stdout 为空且无网络错误特征 → 保守视为 auth 问题
            logger.warning("健康检查: nlm 返回空响应，疑似 auth 过期")
            return "auth_expired"

        # exit != 0（且 != 8）
        if _is_network_error_stderr(combined_text):
            logger.warning("健康检查: 网络不可达（exit %d + 网络错误特征）", proc.returncode)
            return "network_error"

        # 检查 auth 相关特征
        auth_text = combined_text.lower()
        if (
            ("profile" in auth_text and ("not found" in auth_text or "login" in auth_text))
            or "unauthorized" in auth_text
            or "authentication" in auth_text
            or "auth" in auth_text and "expired" in auth_text
        ):
            logger.error(
                "健康检查: auth 过期 — profile '%s' 不可用，终端运行: nlm login",
                profile,
            )
            return "auth_expired"

        # 其余非零码多为 CLI/网络临时错误；转 pending，避免降级产低质报告。
        logger.error(
            "健康检查失败 (exit %d): output=%s",
            proc.returncode, combined_text[:200],
        )
        return "network_error"

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
             timeout: int = 60, with_profile: bool = True) -> str | None:
        """执行 NLM CLI 命令。使用 start_new_session + killpg 防护孤儿进程。

        with_profile=False：跳过 --profile flag（`download report` 子命令不支持
        --profile，必须用 active default profile）。
        """
        cmd = [str(self.config.nlm_bin)] + list(args)
        if with_profile and profile != "alt":
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
                # exit 8 = 真·配额耗尽（全局），需停批等配额重置；抛异常以与
                # 单视频处理失败（其它非零码）区分，后者应触发字幕降级而非停批。
                logger.warning(f"NLM 配额耗尽 (exit 8): {' '.join(args)}")
                raise NLMQuotaExhausted(f"NLM 配额耗尽 (exit 8): {' '.join(args)}")
            if proc.returncode != 0:
                # 非配额失败（含 source add --wait 处理失败的 exit 1）：返回 None，
                # 由 _nlm_research_path 转该视频降级路径，不中断整批。
                logger.warning(
                    f"nlm {' '.join(args)} failed (exit {proc.returncode}): "
                    f"stdout={stdout[-300:]!r} stderr={stderr[-300:]!r}"
                )
                return None
            # exit 0 — 但 nlm CLI 在网络不可达时可能返回 exit 0 且 stderr 含
            # Python traceback（httpx 未捕获异常）。检查 stderr 防假成功。
            stdout_text = _strip_ansi(stdout.strip())
            if not stdout_text and _is_network_error_stderr(stderr):
                logger.warning(
                    f"nlm 网络不可达（exit 0 但 stderr 含网络错误）: {' '.join(args)} "
                    f"stderr={stderr[-300:]!r}"
                )
                return None
            return stdout_text
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

    def _nlm_delete_notebook(self, nb_id: str | None, profile: str) -> None:
        """Best-effort 删除 notebook，防止失败/轮替路径产生 orphan。

        短 timeout + 吞异常：delete 失败绝不阻断主流程。
        """
        if not nb_id:
            return
        try:
            self._nlm("notebook", "delete", nb_id, "--confirm", profile=profile, timeout=15)
            logger.info(f"[GC] orphan notebook 已删除: {nb_id}")
        except Exception:
            logger.warning(f"[GC] notebook 删除失败（已忽略）: {nb_id}")

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
    # NLM 报告生成（三步：report create → 轮询 list artifacts → download）
    # ------------------------------------------------------------------

    def _generate_and_download_report(
        self,
        nb_id: str,
        title: str,
        profile: str | None = None,
    ) -> str | None:
        """通过 nlm 三步生成并下载报告：report create → 轮询 list artifacts → download report。"""
        profile = profile or self.config.nlm_profile
        logger.info(
            "[DIAG-GEN-REPORT] nb_id=%s, title=%r, profile=%s",
            nb_id, title[:60], profile,
        )

        slug = re.sub(r"[^\w-]", "", title.lower().replace(" ", "-"))[:40]
        report_path = f"/tmp/research-video-{slug}.md"

        # Step 1: 异步创建报告，获取 Artifact ID
        create_output = self._nlm(
            "report", "create", nb_id,
            "--format", "Briefing Doc",
            "--language", "zh-Hans",
            "--confirm",
            profile=profile,
            timeout=60,
        )
        if not create_output:
            logger.error("report create 失败或返回空")
            return None

        artifact_id: str | None = None
        for line in create_output.split("\n"):
            m = re.search(r"Artifact\s+ID[:\s]+([0-9a-f-]{8,})", line, re.IGNORECASE)
            if m:
                artifact_id = m.group(1).strip()
                break
        if not artifact_id:
            uuid_m = re.search(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                create_output, re.IGNORECASE,
            )
            if uuid_m:
                artifact_id = uuid_m.group(0)
        if not artifact_id:
            logger.error(f"无法从 report create 输出中提取 Artifact ID: {create_output[:300]}")
            return None

        logger.info(f"[DIAG-GEN-REPORT] artifact_id={artifact_id}")

        # Step 2: 轮询 list artifacts 等待 status=completed
        poll_interval = 15
        deadline = VIDEO_NLM_MAX_WAIT
        elapsed = 0
        completed = False
        while elapsed < deadline:
            list_output = self._nlm(
                "list", "artifacts", nb_id, "--json",
                profile=profile,
                timeout=60,
            )
            if list_output:
                try:
                    artifacts = json.loads(list_output)
                    for art in artifacts:
                        if art.get("id") == artifact_id and art.get("status") == "completed":
                            completed = True
                            break
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"list artifacts 解析失败: {list_output[:200]}")
            if completed:
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        if not completed:
            logger.error(f"report 轮询超时 ({deadline}s)，artifact_id={artifact_id}")
            return None

        logger.info("[DIAG-GEN-REPORT] artifact completed, downloading")

        # Step 3: 下载报告（download report 不支持 --profile，用 active default profile）
        dl_output = self._nlm(
            "download", "report", nb_id,
            "--id", artifact_id,
            "-o", report_path,
            with_profile=False,
            timeout=120,
        )
        if dl_output is None:
            logger.error(f"download report 失败，artifact_id={artifact_id}")
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
