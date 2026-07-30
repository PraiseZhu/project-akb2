"""视频链路外部凭证的统一配置 + 健康归因 —— 让"凭证已死"停止伪装成"取不到日期"。

事故实况（2026-07-30，`logs/video-blocked.json` + `logs/video-stderr.log`）：
漏斗 源 21 → 列表 246 → 候选 2 → 发布 0，连续 2 轮 `blocked`。三处断点：

1. 日期补取 11 次里 **9 次失败** —— yt-dlp 撞 YouTube 机器人校验
   （`Sign in to confirm you're not a bot`，需要有效 cookies）→ 9 个候选被时间闸
   fail-closed 拒收（`reason=date_unresolvable`）；
2. NLM auth 过期（`profile 'default' 不可用，终端运行: nlm login`）→ 研究降级；
3. 降级路径的 yt-dlp 字幕获取撞**同一个**机器人校验 → 2 个候选研究全灭 0/2。

三处症状的共同根因只有一个：**外部凭证（YouTube cookies / NLM 登录态）失效**。
但链路把它拆成三处分散 WARNING，落盘的 `reason` 只报了最末一层的
`date_unresolvable`（"取不到日期"）—— 运维看到的是症状，看不到"凭证已死、该跑哪条
命令"。宪法 P4（预算显式 / 失败可见）要求把共同根因一次说清。

本模块做两件事，**都不改变任何既有降级语义**（凭证死了链路照常跑）：

1. **cookies 配置统一**（`resolve_cookie_config` / `cookie_args`）：把 env 配置翻译成
   yt-dlp 参数，供 collector（列表 + 日期补取）与 researcher（字幕降级）注入**同一
   份**配置。researcher 过去完全不带 cookies —— 降级路径注定撞同一堵墙。
2. **凭证健康预检**（`probe_credentials`）：开跑前静态探测 yt-dlp 可用性 + cookies
   配置来源，并复用 researcher 已有的 NLM 健康检查，产出可落盘的 `credential_health`
   块（含修复命令）。

env 配置（二选一，文件优先；两个都配会告警后取文件）：

    AKB2_YTDLP_COOKIES=/path/to/yt-cookies.txt      # Netscape 格式 cookie 文件
    AKB2_YTDLP_COOKIES_FROM_BROWSER=chrome          # 直接从浏览器读

**未配置时两个调用点的命令行逐字不变**：collector 继续走 `video_sources.json` 的
文件探测 / 浏览器兜底，researcher 继续不带 cookies。恢复步骤见
`docs/runbooks/video-credential-recovery.md`。
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("akb2.video.external_credentials")

# ---------------------------------------------------------------------------
# env 键名
# ---------------------------------------------------------------------------

ENV_COOKIES_FILE = "AKB2_YTDLP_COOKIES"
ENV_COOKIES_FROM_BROWSER = "AKB2_YTDLP_COOKIES_FROM_BROWSER"

# ---------------------------------------------------------------------------
# 状态枚举（机读，不本地化）
# ---------------------------------------------------------------------------

#: 凭证可用 —— 显式配置到位（不代表已做过 liveness 验证）
STATUS_OK = "ok"
#: 凭证可疑 —— 只有兜底配置 / 临时性网络错误，需与运行时症状交叉才能定论
STATUS_DEGRADED = "degraded"
#: 凭证确证不可用 —— 探测直接判死（二进制缺失 / 文件不存在 / auth 过期）
STATUS_DEAD = "dead"

_SEVERITY = {STATUS_OK: 0, STATUS_DEGRADED: 1, STATUS_DEAD: 2}

# ---------------------------------------------------------------------------
# cookies 配置来源枚举
# ---------------------------------------------------------------------------

#: env 指定了 cookie 文件且文件可用
SOURCE_ENV_FILE = "env_file"
#: env 指定了 cookie 文件但文件缺失 / 为空 —— 注入它只会让 yt-dlp 整体报错
SOURCE_ENV_FILE_MISSING = "env_file_missing"
#: env 指定从浏览器读 cookies
SOURCE_ENV_BROWSER = "env_browser"
#: `video_sources.json` 的 `ytdlp_cookies_file` 存在且非空
SOURCE_CONFIG_FILE = "config_file"
#: 无任何显式 cookie 文件，回退 `--cookies-from-browser`（YouTube bot-check 高发）
SOURCE_BROWSER_FALLBACK = "browser_fallback"
#: 未配置任何 cookies（researcher 降级路径改动前的状态）
SOURCE_NONE = "none"

#: 来源 → 健康状态。`browser_fallback` 判 degraded 而非 dead：它**可能**能用，
#: 只有与运行时症状（列表全空 / 补取成批失败）同时成立才够定论（见 `blocked`）。
_SOURCE_STATUS = {
    SOURCE_ENV_FILE: STATUS_OK,
    SOURCE_ENV_BROWSER: STATUS_OK,
    SOURCE_CONFIG_FILE: STATUS_OK,
    SOURCE_BROWSER_FALLBACK: STATUS_DEGRADED,
    SOURCE_NONE: STATUS_DEGRADED,
    SOURCE_ENV_FILE_MISSING: STATUS_DEAD,
}

# ---------------------------------------------------------------------------
# 凭证名 + 修复命令
# ---------------------------------------------------------------------------

CREDENTIAL_YTDLP_COOKIES = "ytdlp_cookies"
CREDENTIAL_NLM_AUTH = "nlm_auth"

_RUNBOOK = "docs/runbooks/video-credential-recovery.md"

FIX_YTDLP_COOKIES = (
    f"导出 YouTube cookies 后 export {ENV_COOKIES_FILE}=<cookie 文件路径>"
    f"（或 export {ENV_COOKIES_FROM_BROWSER}=chrome）；步骤见 {_RUNBOOK}"
)
FIX_YTDLP_BINARY = f"安装 / 修复 yt-dlp（`pipx install yt-dlp` 或设 YTDLP_BIN）；见 {_RUNBOOK}"


def fix_nlm_login(profile: str) -> str:
    """NLM 登录态修复命令（profile 显式带上，省掉一次"到底哪个 profile"的追问）。"""
    return f"终端运行: nlm login --profile {profile}（需交互）；步骤见 {_RUNBOOK}"


# ---------------------------------------------------------------------------
# cookies 配置解析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CookieConfig:
    """env 解析出的 cookies 配置。`args` 为空 = 调用方保持改动前行为。"""

    args: tuple[str, ...]
    source: str
    detail: str

    @property
    def configured(self) -> bool:
        """env 是否给出了**可用**的 cookies 配置（缺文件不算）。"""
        return self.source in (SOURCE_ENV_FILE, SOURCE_ENV_BROWSER)


def resolve_cookie_config(env: Mapping[str, str] | None = None) -> CookieConfig:
    """解析 env 里的 yt-dlp cookies 配置（二选一，文件优先）。

    未配置 → `source=none` / `args=()`：调用方必须在这种情况下保持改动前的命令行
    **完全不变**。

    env 指定的文件不存在或为空 → 判 `env_file_missing` 且 **不注入**：把
    `--cookies /nonexistent` 递给 yt-dlp 会让它整体报错（连兜底路径一起打死），
    比"配置写错了"这件事本身更难排查。这里记 ERROR + 交给健康块播报。
    """
    env = os.environ if env is None else env
    raw_file = (env.get(ENV_COOKIES_FILE) or "").strip()
    raw_browser = (env.get(ENV_COOKIES_FROM_BROWSER) or "").strip()

    if raw_file and raw_browser:
        # 二选一：静默择一是下次排查的坑，说出来。
        logger.warning(
            "%s 与 %s 同时配置，按「文件优先」取文件（%s），忽略浏览器（%s）",
            ENV_COOKIES_FILE, ENV_COOKIES_FROM_BROWSER, raw_file, raw_browser,
        )

    if raw_file:
        path = Path(raw_file).expanduser()
        try:
            usable = path.is_file() and path.stat().st_size > 0
        except OSError:
            usable = False
        if not usable:
            logger.error(
                "%s 指向的 cookie 文件不可用（不存在 / 为空）: %s —— 本轮不注入 cookies",
                ENV_COOKIES_FILE, path,
            )
            return CookieConfig(
                args=(),
                source=SOURCE_ENV_FILE_MISSING,
                detail=f"{ENV_COOKIES_FILE}={path} 指向的文件不存在或为空",
            )
        return CookieConfig(
            args=("--cookies", str(path)),
            source=SOURCE_ENV_FILE,
            detail=f"{ENV_COOKIES_FILE}={path}",
        )

    if raw_browser:
        return CookieConfig(
            args=("--cookies-from-browser", raw_browser),
            source=SOURCE_ENV_BROWSER,
            detail=f"{ENV_COOKIES_FROM_BROWSER}={raw_browser}",
        )

    return CookieConfig(args=(), source=SOURCE_NONE, detail="env 未配置 cookies")


def cookie_args(env: Mapping[str, str] | None = None) -> list[str]:
    """env 配置对应的 yt-dlp cookies 参数；未配置返回 `[]`（命令行不变）。"""
    return list(resolve_cookie_config(env).args)


# ---------------------------------------------------------------------------
# 健康探测
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialItem:
    """单个凭证的探测结论。"""

    name: str
    status: str
    detail: str
    fix_command: str
    source: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status == STATUS_OK

    def as_dict(self) -> dict:
        """落盘用字典 —— 一律强制成 str。

        诊断文件是 0 产出归因的最后一道出口：探测值万一不是字符串（上游改了返回类型 /
        注入了假对象），`json.dumps` 会连**整份漏斗诊断**一起写失败，故障就又静默了。
        在这里收口比在写盘处补救便宜。
        """
        payload = {
            "status": str(self.status),
            "detail": str(self.detail),
            "fix_command": str(self.fix_command),
        }
        if self.source:
            payload["source"] = str(self.source)
        return payload


@dataclass(frozen=True)
class CredentialHealth:
    """一轮凭证预检的完整结论。"""

    items: tuple[CredentialItem, ...]

    @property
    def overall(self) -> str:
        """最坏项即整体状态。"""
        if not self.items:
            return STATUS_OK
        return max((i.status for i in self.items), key=lambda s: _SEVERITY.get(s, 0))

    @property
    def is_healthy(self) -> bool:
        return self.overall == STATUS_OK

    def get(self, name: str) -> CredentialItem | None:
        for item in self.items:
            if item.name == name:
                return item
        return None

    def status_of(self, name: str) -> str:
        """指定凭证的状态；未探测过按 `ok` 处理（不凭空造故障）。"""
        item = self.get(name)
        return item.status if item is not None else STATUS_OK

    @property
    def unhealthy(self) -> tuple[CredentialItem, ...]:
        return tuple(i for i in self.items if not i.is_ok)

    @property
    def fix_commands(self) -> list[str]:
        """不健康项的修复命令（保序去重），播报侧直接可用。"""
        out: list[str] = []
        for item in self.unhealthy:
            if item.fix_command and item.fix_command not in out:
                out.append(item.fix_command)
        return out

    def as_dict(self) -> dict:
        return {
            "overall": str(self.overall),
            "items": {str(i.name): i.as_dict() for i in self.items},
            "fix_commands": [str(c) for c in self.fix_commands],
        }


def _ytdlp_binary_usable(ytdlp_bin: str, runner: Callable[..., object] | None = None) -> bool:
    """`yt-dlp --version` 能跑通即算可用（本地调用，无网络）。"""
    run = runner or subprocess.run
    try:
        proc = run(  # type: ignore[call-arg]
            [str(ytdlp_bin), "--version"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("凭证预检: yt-dlp 不可执行（%s）: %s", ytdlp_bin, exc)
        return False
    return getattr(proc, "returncode", 1) == 0


def probe_ytdlp_cookies(
    ytdlp_bin: str,
    cookie_source: str,
    runner: Callable[..., object] | None = None,
) -> CredentialItem:
    """探测 yt-dlp 可用性 + cookies 配置状态。

    `cookie_source` 由实际构建命令行的一方（collector）提供 —— 探测不重算一遍
    "文件在不在"的逻辑，否则两处口径迟早分裂。
    """
    if not _ytdlp_binary_usable(ytdlp_bin, runner):
        return CredentialItem(
            name=CREDENTIAL_YTDLP_COOKIES,
            status=STATUS_DEAD,
            detail=f"yt-dlp 不可执行: {ytdlp_bin}",
            fix_command=FIX_YTDLP_BINARY,
            source=cookie_source or SOURCE_NONE,
        )

    source = cookie_source or SOURCE_NONE
    status = _SOURCE_STATUS.get(source, STATUS_DEGRADED)
    details = {
        SOURCE_ENV_FILE: f"cookies 来自 env（{ENV_COOKIES_FILE}）",
        SOURCE_ENV_BROWSER: f"cookies 来自 env 浏览器（{ENV_COOKIES_FROM_BROWSER}）",
        SOURCE_CONFIG_FILE: "cookies 来自 video_sources.json 指定的 cookie 文件",
        SOURCE_BROWSER_FALLBACK: (
            "无显式 cookie 文件，回退 --cookies-from-browser —— "
            "YouTube 机器人校验（Sign in to confirm you're not a bot）高发"
        ),
        SOURCE_NONE: "未配置任何 cookies",
        SOURCE_ENV_FILE_MISSING: f"{ENV_COOKIES_FILE} 指向的 cookie 文件不存在或为空",
    }
    return CredentialItem(
        name=CREDENTIAL_YTDLP_COOKIES,
        status=status,
        detail=details.get(source, f"cookies 配置来源未知: {source}"),
        fix_command="" if status == STATUS_OK else FIX_YTDLP_COOKIES,
        source=source,
    )


def probe_nlm_auth(
    nlm_health_check: Callable[[], str],
    profile: str,
) -> CredentialItem:
    """复用 researcher 的健康检查判 NLM 登录态。

    映射：`ok` → ok；`auth_expired` → dead（确证，登录态没了）；
    `network_error` → degraded（transient，researcher 会转 pending 重试，不是凭证问题）。
    探测本身抛异常 → degraded（"探不到"不等于"死了"，不许凭空升级成硬故障）。
    """
    try:
        health = str(nlm_health_check())
    except Exception as exc:  # noqa: BLE001 — 探测失败不得伪装成凭证死亡
        logger.error("凭证预检: NLM 健康检查异常: %s", exc)
        return CredentialItem(
            name=CREDENTIAL_NLM_AUTH,
            status=STATUS_DEGRADED,
            detail=f"NLM 健康检查未能完成: {exc}",
            fix_command=fix_nlm_login(profile),
        )

    if health == "ok":
        return CredentialItem(
            name=CREDENTIAL_NLM_AUTH,
            status=STATUS_OK,
            detail=f"NLM profile '{profile}' 登录态正常",
            fix_command="",
        )
    if health == "network_error":
        return CredentialItem(
            name=CREDENTIAL_NLM_AUTH,
            status=STATUS_DEGRADED,
            detail="NLM CLI / 网络暂时不可达（transient，非凭证失效）",
            fix_command=fix_nlm_login(profile),
        )
    return CredentialItem(
        name=CREDENTIAL_NLM_AUTH,
        status=STATUS_DEAD,
        detail=f"NLM auth 过期 — profile '{profile}' 不可用（health={health}）",
        fix_command=fix_nlm_login(profile),
    )


def probe_credentials(
    ytdlp_bin: str,
    cookie_source: str,
    nlm_profile: str,
    nlm_health_check: Callable[[], str],
    runner: Callable[..., object] | None = None,
) -> CredentialHealth:
    """开跑前的凭证健康预检（yt-dlp cookies + NLM 登录态）。

    **只观测，不改变控制流**：凭证死亡时链路照常跑（NLM 死了走字幕降级、cookies
    弱了照样试），这里唯一的产物是可落盘的归因块。fail-fast 会把"降级也能出东西"
    的那部分产出一起掐掉，不是本次要的语义。
    """
    return CredentialHealth(
        items=(
            probe_ytdlp_cookies(ytdlp_bin, cookie_source, runner=runner),
            probe_nlm_auth(nlm_health_check, nlm_profile),
        )
    )
