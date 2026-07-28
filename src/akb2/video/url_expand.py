r"""短链展开 — 采集阶段的一次性网络解析（残余风险 2 的第一条缝）。

问题：`https://b23.tv/<code>`（bilibili 分享按钮给出的形式，也是人手动粘贴时最常见的
形式）不含 BV 号，`video_identity()` 解析不到平台原生 id → 落 sha256 兜底，与展开后的
`https://www.bilibili.com/video/BV...` 是**两个身份**。同一个视频因此可以被研究两次。

上一轮的判定是"需网络解析、`video_identity` 是纯函数且在热路径上，故只登记不修"。
本轮重新评估的结论：**展开不该发生在 `video_identity` 里，而该发生在采集阶段** ——

- 采集阶段本来就在跑网络（yt-dlp 拉列表、`_fill_upload_date` 逐视频 `--dump-json`），
  多一次 HEAD 请求在同一量级；
- 每个候选只展开一次，且只对**已知短链域名**发请求（其他 URL 零网络开销）；
- `video_identity()` 保持纯函数不变 —— 展开后的 URL 才交给它。

有界（宪法 P1）：`VIDEO_SHORTLINK_MAX_PER_RUN` 限单轮展开次数、
`VIDEO_SHORTLINK_MAX_HOPS` 限单条链的跳数、`VIDEO_SHORTLINK_TIMEOUT_SEC` 限单次超时，
同一 URL 走进程内缓存不重复请求。

失败可见（宪法 P4）：展开失败 / 预算耗尽 / 跳数耗尽都打 WARNING 并计数
（`failures` / `budget_exhausted`），由 `collector.collect()` 汇总 —— 不静默回退。
失败时返回**原始 URL**（继续走 sha256 兜底身份）：这只是"可能多烧一次额度"，而
硬失败会让整轮采集停摆，代价更大。
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from src.akb2.video.constants import (
    VIDEO_SHORTLINK_MAX_HOPS,
    VIDEO_SHORTLINK_MAX_PER_RUN,
    VIDEO_SHORTLINK_TIMEOUT_SEC,
)

logger = logging.getLogger("akb2.video.url_expand")

# 已知短链域名：只对这些 host 发网络请求（其余 URL 零开销）。
# b23.tv 是 bilibili 官方分享短链；youtu.be **不在**此列 —— 它的 path 段就是
# videoId，`video_identity()` 已经能直接解析，发请求纯属浪费。
SHORTLINK_HOSTS = frozenset({"b23.tv", "www.b23.tv"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拦住重定向：我们要的是 Location 头，不是最终页面内容。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def is_short_link(url: str) -> bool:
    """该 URL 是否是需要展开的已知短链。"""
    host = (urlparse(url if "//" in url else f"https://{url}").hostname or "").lower()
    return host in SHORTLINK_HOSTS


def _resolve_via_http(url: str, timeout: float) -> str | None:
    """发一次 HEAD 请求，返回 `Location` 头（不跟随、不下载正文）。

    Returns:
        下一跳的绝对 URL；无重定向 / 请求失败 / Location 不是 http(s) → None。
    """
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, method="HEAD")
    try:
        with opener.open(request, timeout=timeout) as response:
            location = response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        # 3xx 在 _NoRedirect 下以 HTTPError 形式抛出，Location 就在头里
        location = exc.headers.get("Location") if exc.headers else None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("[短链] 展开请求失败 %s: %s", url, exc)
        return None

    if not location:
        return None
    absolute = urljoin(url, location)
    scheme = urlparse(absolute).scheme.lower()
    if scheme not in {"http", "https"}:
        # 非 http(s) 的 Location（file: / data: 等）一律不采信
        logger.warning("[短链] 跳过非 http(s) 重定向目标: %s → %s", url, absolute)
        return None
    return absolute


class ShortLinkExpander:
    """把已知短链展开成完整 URL（有界 + 进程内缓存 + 失败可见）。"""

    def __init__(
        self,
        *,
        timeout: float = VIDEO_SHORTLINK_TIMEOUT_SEC,
        max_hops: int = VIDEO_SHORTLINK_MAX_HOPS,
        max_per_run: int = VIDEO_SHORTLINK_MAX_PER_RUN,
        resolver=None,
    ):
        self.timeout = timeout
        self.max_hops = max_hops
        self.max_per_run = max_per_run
        self._resolver = resolver  # 注入点（测试用）；None = 走 `_resolve_via_http`
        self._cache: dict[str, str] = {}

        # 可观测计数器（汇总进采集日志）
        self.expansions_used = 0
        self.expanded_ok = 0
        self.failures = 0
        self.budget_exhausted = 0

    def expand(self, url: str) -> str:
        """展开短链；非短链原样返回（不发任何请求）。

        Returns:
            展开后的完整 URL；无法展开时返回原始 URL（继续走 sha256 兜底身份）。
        """
        if not url or not is_short_link(url):
            return url
        if url in self._cache:
            return self._cache[url]
        if self.expansions_used >= self.max_per_run:
            self.budget_exhausted += 1
            # 预算耗尽后**不写缓存**（写了会让 budget_exhausted 从"命中次数"变成
            # "唯一 URL 数"，可观测计数的语义就变了）；但同一 URL 反复查会刷屏，
            # 所以日志只打第一次，计数照旧每次累加。
            if self.budget_exhausted == 1:
                logger.warning(
                    "[短链] 展开预算耗尽 (%d)，保持短链形态（该视频的身份可能与展开后的"
                    "完整链接分裂，最多多烧一次额度）；后续同类不再重复告警: %s",
                    self.max_per_run, url,
                )
            return url

        self.expansions_used += 1
        resolve = self._resolver or _resolve_via_http
        current = url
        for _ in range(self.max_hops):
            try:
                nxt = resolve(current, self.timeout)
            except Exception as exc:  # 解析器自身故障也不得让整轮采集停摆
                logger.warning("[短链] 展开异常 %s: %s", current, exc)
                nxt = None
            if not nxt or nxt == current:
                break
            current = nxt
            if not is_short_link(current):
                break

        if current == url:
            self.failures += 1
            logger.warning(
                "[短链] 未能展开，保持原样（身份走 sha256 兜底，与展开后的完整链接"
                "分裂成两个身份）: %s", url,
            )
        elif is_short_link(current):
            # 跳数用尽还停在短链上：展开了但没到底，仍算失败（身份仍会分裂）
            self.failures += 1
            logger.warning(
                "[短链] 跳数上限 %d 用尽仍是短链: %s → %s",
                self.max_hops, url, current,
            )
        else:
            self.expanded_ok += 1
            logger.info("[短链] 已展开: %s → %s", url, current)

        self._cache[url] = current
        return current
