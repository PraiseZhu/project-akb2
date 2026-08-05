"""共享 HTTP 退避重试策略 + provider 错误分类（宪法 P4「预算显式 / 失败可见」）。

## 这个模块从哪来、要合并谁

实现原型是 `src/akb2/feishu_sync/feishu/send.py:67-90` 的 `_parse_retry_after` /
`_backoff_delay` / `_is_retryable_http` 三件套 —— 那是本仓库里唯一一份「尊重
Retry-After + 指数退避 + full jitter + 封顶」的正确范式。GitHub briefing 的
`_call_deepseek` 此前是 `for attempt in range(MAX_RETRIES)` + `except Exception`
一网打尽（无状态码分类、无 Retry-After、无 jitter），本模块把 send.py 的范式提取出来
供两处共用，避免造第二套。

**当前状态：`send.py` 尚未改为调用本模块。** 本轮改动的边界只覆盖 briefing 侧，
`send.py` 的调用点不在范围内；留下的重复是**已知的、有意的**，后续应把 `send.py`
的三个私有函数替换为 `from akb2.common.http_retry import ...` 后删除本地实现
（届时它的 `_MAX_RETRIES` / `_BASE_BACKOFF` / `_MAX_BACKOFF` env 覆盖以 `base`/`cap`
参数传入即可，行为不变）。命名上去掉了前导下划线：这是公共共享模块，私有名不合适。

## 为什么分类逻辑也在这里

「这个 429 该不该重试」是**退避策略的输入**，不是调用方的业务判断：预算耗尽型 429
重试多少次都无用（额度不会因为等待而回来），把它和瞬时限流一起退避就是白烧时间和额度。
所以状态码集合与 provider 错误体分类和退避函数放在同一个模块里，调用方只需问一句
「retryable 吗」。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 具名重试策略常量（宪法 P4：预算/重试参数必须具名，禁裸字面量）
# ---------------------------------------------------------------------------

# 与 src/akb2/quality/intake/source_validator.py 的 _RETRYABLE_HTTP_STATUSES 对齐
# （408 请求超时 / 425 too early / 429 限流 / 5xx 网关侧瞬时故障）。
# 该处额外收了 Cloudflare 520-524；那是抓站场景专属，LLM 网关不产出这些码，故不纳入。
RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_BASE_BACKOFF_S: float = 1.0
DEFAULT_MAX_BACKOFF_S: float = 30.0

# ---------------------------------------------------------------------------
# 契约 2 —— 429 统一诊断字段名（跨 lane 共用，字段名不得各自发明）
# ---------------------------------------------------------------------------

ERROR_TYPE_BUDGET_EXCEEDED = "budget_exceeded"
ERROR_TYPE_RATE_LIMIT = "rate_limit"
ERROR_TYPE_OTHER = "other"

BUDGET_SCOPE_TOTAL = "total"
BUDGET_SCOPE_UTC_DAILY = "utc_daily"
BUDGET_SCOPE_NONE = ""

# Gateway 预算耗尽的两种真实响应体形态（2026-08-01/08-02 生产实测，两种都出现过，
# 只认一种会漏判 → 漏判就退回「硬重试烧额度」的旧行为）：
#   形态 A（总预算，无 Date）：
#     ExceededBudget: User=zhuzan over budget. Spend=18000.62608448676, Budget=18000.0
#   形态 B（UTC 日配额，有 Date）：
#     ExceededBudget: User=zhuzan exceeded UTC daily cap. Date=2026-08-01,
#     Spend=2701.228476741798, DailyCap=2700.0
# 响应体可能是裸文本也可能是 {"error":{"message":"ExceededBudget: ..."}} 包装，
# 故一律按正则扫原文，不预设 JSON 结构。
_BUDGET_MARKERS: tuple[str, ...] = (
    "exceededbudget",
    "over budget",
    "daily cap",
    "exceeded budget",
)
_RE_DAILY_CAP = re.compile(r"DailyCap\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_TOTAL_CAP = re.compile(r"\bBudget\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_SPEND = re.compile(r"\bSpend\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_DATE = re.compile(r"\bDate\s*=\s*(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------
# provider 原文脱敏（加固清单第 9 类 / 宪法 P4 —— 「失败可见」不等于「原文落盘」）
# ---------------------------------------------------------------------------
# R1 审查实测：把 `sk-probe-secret` 塞进 provider body/message/异常文本后，
# `quality-ledger.jsonl` 与 `shared-budget-gate.json` 都出现 `contains_secret=True`。
# 429 原文的**诊断价值**全在结构化字段里（error_type/budget_scope/spend/cap/
# budget_date），原文只是给人读一眼的上下文 —— 所以原文进异常/日志/状态文件前
# 一律经本函数：凭证样式与账户身份掩码 + 截断，分类字段一个不动。
PROVIDER_TEXT_MAX_CHARS: int = 200
REDACTED_PLACEHOLDER = "<redacted>"
_TRUNCATION_MARKER = "…<truncated>"

# 键名/值可能被引号包住 —— 三种真实形态必须同时命中，否则「脱敏」只在一部分站点成立：
#   裸 kv:            `api_key=<value>`            （env / query string / 裸日志行）
#   JSON:             `{"api_key":"<value>"}`      （provider 响应体、httpx 异常 body）
#   Python dict repr: `{'api_key': '<value>'}`     （异常 repr、traceback 里的 kwargs）
# R4 实测（旧规则）：键名规则写成 `\b<key>\b\s*[=:]`，键被引号包住时 `\b` 之后紧跟的是
# `"` 而不是 `[=:]` → **整条规则失配**，`{"api_key":"<opaque>"}` 原样落盘。当时之所以
# 没被测出，是因为旧测试用的哨兵带 `sk-` 前缀，被前一条**前缀规则**兜住了 ——
# 一条规则的命中掩盖了另一条规则的洞。故这里把「可选引号」抽成共享片段，键名侧与值侧
# 用同一份定义（加固清单：同一判据只允许有一个 owner）。
#
# ⚠ R5 修正一处 R4 的过度断言：R4 的注释写「物理上不可能只补一半」，但那只覆盖了
# **引号边界**这一个轴 —— 共享片段管不到「词边界」这另一个轴，`\b` 的前导缺口因此活到了 R5。
# 结论落成纪律：共享片段只保证被它覆盖的那个轴一致，不保证规则整体没有别的轴出问题；
# 注释不得把"某一轴有单一 owner"写成"整条规则不可能只修一半"。
#
# R6（SC-R6-04）：键名匹配从「regex 逐轴补」改为**按 token 分解**。四轮里每轮冒出一条
# 新轴（R2 缺调用 / R4 引号轴 / R5 下划线轴 / R6 驼峰轴 clientSecret），根因是 regex 把
# 「键名的词法形态」和「键名的语义」揉在一条 pattern 里 —— 换一种词法就漏一次。现在
# 拆开：先把候选键名按分隔符（_ - . 空格）与驼峰边界切成 token 序列，再对 token 做语义
# 判定，所有词法轴（下划线/连字符/驼峰/引号/大小写）一次覆盖，后续不存在"再补一轴"。
_OPT_QUOTE = r"['\"]?"
# 值以空白 / 结构分隔符 / 引号为边界；`}` 与 `]` 排除掉才不会把 JSON 收尾符吃进掩码。
# `&` / `?` 也是边界（R6）：通用 kv 捕获下，query string 里前一个非凭证键的值若把 `&`
# 吃进去，会把后面的 `client_secret=…` 一起吞掉 —— 值在 `&` 处截断，后续键才轮得到判定。
_KV_VALUE = r"[^\s,;&?)\]}'\"]+"
# 键名**前导**边界（SC-R5-04）：不能用 `\b` —— `_` 是词字符，遇下划线复合键整条失配。
# 「前一个字符不是字母数字」的否定回顾：`_` / `?` / `&` / `{` / `"` 都算合法分隔。
# 与 `User` 规则共用同一片段，避免两条规则各修一半。
_KEY_LEAD = r"(?<![A-Za-z0-9])"

# 凭证语义词集合（SC-R6-04 冻结）：token 判定的唯一依据，不再有独立的 regex 词表。
_CRED_WORDS = frozenset(
    {"secret", "token", "password", "passwd", "apikey", "credential", "auth"}
)
# 复合分词字典 = 凭证词 + 允许出现在凭证复合词里的中性件。`secretkey` 因可全切成
# secret+key（含凭证词）而命中；`mysecret` 因 "my" 不在字典、整词不可切而**不**命中 ——
# 这是 R5「字母直连不收」取舍在 R6 的精化：可分解进凭证词汇的直连收，其余直连仍不收。
_CRED_COMPOUND_DICT = _CRED_WORDS | {"key", "api"}

_RE_CAMEL_LOWER_UPPER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_RE_CAMEL_ACRONYM = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_RE_KEY_SEP = re.compile(r"[_\-. ]+")


def _key_tokens(key: str) -> list[str]:
    """键名 → 小写 token 序列（分隔符 + 驼峰边界，两类边界都要切）。"""
    marked = _RE_CAMEL_ACRONYM.sub("_", _RE_CAMEL_LOWER_UPPER.sub("_", key))
    return [t.lower() for t in _RE_KEY_SEP.split(marked) if t]


def _segments_into_cred_vocab(token: str) -> bool:
    """token 可否**完整**切成复合字典词、且至少一段是凭证词（如 secretkey→secret+key）。"""
    n = len(token)
    # best[i]: None=位置 i 不可达；否则记录"到 i 为止是否已含凭证词段"（True 优先保留）
    best: list[bool | None] = [None] * (n + 1)
    best[0] = False
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(i + 1, n + 1):
            piece = token[i:j]
            if piece in _CRED_COMPOUND_DICT:
                flag = bool(best[i]) or piece in _CRED_WORDS
                if best[j] is None or (flag and not best[j]):
                    best[j] = flag
    return bool(best[n])


def _is_credential_key(key: str) -> bool:
    """键名语义判定：**尾 token（头名词）**决定这是不是凭证键。

    为什么是尾 token 而不是"任一 token"：`client_secret` 的头名词是 secret（值是凭证），
    `secret_id` 的头名词是 id（值是标识符）—— 两者都含 "secret" 这个 token，语义却相反。
    SC-R6-04 冻结的正反例集合（secret_id / token_id / user_id 必须不命中）只有位置语义
    能同时满足。尾复合（api+key→apikey、secret+key→secretkey）并入尾 token 一起判。
    """
    tokens = _key_tokens(key)
    if not tokens:
        return False
    last = tokens[-1]
    if last in _CRED_WORDS or _segments_into_cred_vocab(last):
        return True
    if len(tokens) >= 2 and _segments_into_cred_vocab(tokens[-2] + last):
        return True
    return False


# 通用 kv 捕获：先抓「任意键 = / : 值」，再用 _is_credential_key 判语义。三种真实形态
# （裸 kv / JSON / Python dict repr）与引号轴沿用 R4 的共享片段。
_KV_PATTERN = re.compile(
    _KEY_LEAD + r"([A-Za-z][A-Za-z0-9_.\-]*)" + _OPT_QUOTE + r"\s*[=:]\s*"
    + _OPT_QUOTE + _KV_VALUE + _OPT_QUOTE
)


def _redact_credential_kv(match: re.Match[str]) -> str:
    key = match.group(1)
    if _is_credential_key(key):
        return f"{key}={REDACTED_PLACEHOLDER}"
    return match.group(0)


_REDACT_RULES: tuple[tuple[re.Pattern[str], Any], ...] = (
    # 凭证样式（前缀保留，便于判断"是哪类 key 泄了"，值不留）
    (re.compile(r"sk-[A-Za-z0-9_\-]{3,}"), f"sk-{REDACTED_PLACEHOLDER}"),
    (re.compile(r"ghp_[A-Za-z0-9_\-]{3,}"), f"ghp_{REDACTED_PLACEHOLDER}"),
    (re.compile(r"github_pat_[A-Za-z0-9_\-]{3,}"), f"github_pat_{REDACTED_PLACEHOLDER}"),
    (re.compile(r"AIza[A-Za-z0-9_\-]{3,}"), f"AIza{REDACTED_PLACEHOLDER}"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{3,}", re.IGNORECASE), f"xox{REDACTED_PLACEHOLDER}"),
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), f"Bearer {REDACTED_PLACEHOLDER}"),
    # 键名规则：**不依赖任何前缀**，这是唯一能兜住不透明凭证（自签 token / UUID 形态）的一条。
    # 语义判定在 _is_credential_key（token 分解），非凭证键原样保留。
    (_KV_PATTERN, _redact_credential_kv),
    # 账户身份：Gateway 的预算 429 原文形如 `User=zhuzan over budget. …`
    # ——「谁打穿了额度」对排查无增量（本机只有一个账户），但它是 PII，不落盘。
    # 引号形态与 `[=:]` 与上面同口径：同一失败形状不允许在两条规则里各修一半。
    (
        re.compile(
            _KEY_LEAD + r"User\b" + _OPT_QUOTE + r"\s*[=:]\s*"
            + _OPT_QUOTE + _KV_VALUE + _OPT_QUOTE,
            re.IGNORECASE,
        ),
        f"User={REDACTED_PLACEHOLDER}",
    ),
)


def redact_provider_text(text: str | bytes | None, *, limit: int | None = None) -> str:
    """把 provider 原文脱敏并截断，供落盘/日志/异常文本统一使用。

    幂等：对已脱敏文本再调用一次结果不变（掩码后的字面量不再匹配任何规则）。

    Args:
        text: provider 响应体 / 异常文本，可为 bytes / str / None。
        limit: 截断上限，默认 `PROVIDER_TEXT_MAX_CHARS`。

    Returns:
        脱敏后的单行片段；超长时尾部带 `…<truncated>` 明示被截（不静默丢）。
    """
    if isinstance(text, bytes):
        raw = text.decode("utf-8", errors="replace")
    else:
        raw = text or ""
    for pattern, replacement in _REDACT_RULES:
        raw = pattern.sub(replacement, raw)
    max_chars = PROVIDER_TEXT_MAX_CHARS if limit is None else limit
    if len(raw) > max_chars:
        return raw[:max_chars] + _TRUNCATION_MARKER
    return raw


@dataclass(frozen=True)
class ProviderErrorDiagnosis:
    """provider 错误的结构化归因 —— 字段名即 COMMON.md 契约 2。

    error_type:   budget_exceeded | rate_limit | other
    budget_scope: total | utc_daily | ""（空 = 不适用）
    spend / cap:  额度数字，解析不到为 None
    budget_date:  形态 B 的 UTC 配额日；形态 A / 非预算错为空串
    provider_code: HTTP 状态码
    retryable:    「退避后重试还有意义吗」—— 预算耗尽恒为 False
    """

    error_type: str
    provider_code: int
    budget_scope: str = BUDGET_SCOPE_NONE
    spend: float | None = None
    cap: float | None = None
    budget_date: str = ""
    retryable: bool = False
    raw_excerpt: str = field(default="", repr=False)

    @property
    def is_budget_exceeded(self) -> bool:
        return self.error_type == ERROR_TYPE_BUDGET_EXCEEDED

    def as_dict(self) -> dict[str, Any]:
        """契约 2 落盘形态（字段名固定，供 Lane 2 复用同名字段）。"""
        return {
            "error_type": self.error_type,
            "budget_scope": self.budget_scope,
            "spend": self.spend,
            "cap": self.cap,
            "budget_date": self.budget_date,
            "provider_code": self.provider_code,
        }

    def summary(self) -> str:
        """单行人可读归因（进日志/异常文本，让真因自证而不是只留 429）。"""
        parts = [f"error_type={self.error_type}", f"provider_code={self.provider_code}"]
        if self.budget_scope:
            parts.append(f"budget_scope={self.budget_scope}")
        if self.spend is not None:
            parts.append(f"spend={self.spend}")
        if self.cap is not None:
            parts.append(f"cap={self.cap}")
        if self.budget_date:
            parts.append(f"budget_date={self.budget_date}")
        return " ".join(parts)


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover — 正则已保证是数字
        return None


def is_retryable_status(status: int | None) -> bool:
    """状态码本身是否值得退避重试（不看响应体）。"""
    return status in RETRYABLE_HTTP_STATUS


def classify_provider_error(status: int | None, body: str | bytes | None) -> ProviderErrorDiagnosis:
    """把 provider 的 (状态码, 响应体) 归因成契约 2 结构。

    判定顺序刻意如此：**预算耗尽优先于状态码**。预算耗尽也走 429，若先按状态码判
    「429 → 可重试」就会把它当瞬时限流退避重试 —— 2026-08-02 主跑 3 次 + backstop 3 次
    共 6 次同族尝试全 429 全预算耗尽，白烧 20s 与额度，正是这个顺序错误的后果。

    Args:
        status: HTTP 状态码；None（纯网络异常，无响应）→ other + retryable=True。
        body: 响应体原文，可为 bytes / str / None。

    Returns:
        ProviderErrorDiagnosis；`retryable` 已把「预算耗尽不可重试」算进去。
    """
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = body or ""
    # 分类看**原文**（凭证掩码会破坏 Spend=/Budget= 之外的字面量匹配吗？不会，
    # 但顺序仍取「先分类再脱敏」以免将来加规则时误伤判据）；落盘只留脱敏片段。
    excerpt = redact_provider_text(text)
    lowered = text.lower()

    if status is None:
        # 连接失败 / 超时 —— 无响应可分类，按瞬时故障处理
        return ProviderErrorDiagnosis(
            error_type=ERROR_TYPE_OTHER, provider_code=0,
            retryable=True, raw_excerpt=excerpt,
        )

    if any(marker in lowered for marker in _BUDGET_MARKERS):
        daily = _RE_DAILY_CAP.search(text)
        if daily or "daily cap" in lowered:
            scope = BUDGET_SCOPE_UTC_DAILY
            cap = _to_float(daily.group(1)) if daily else None
            date_m = _RE_DATE.search(text)
            budget_date = date_m.group(1) if date_m else ""
        else:
            scope = BUDGET_SCOPE_TOTAL
            total = _RE_TOTAL_CAP.search(text)
            cap = _to_float(total.group(1)) if total else None
            budget_date = ""
        spend_m = _RE_SPEND.search(text)
        return ProviderErrorDiagnosis(
            error_type=ERROR_TYPE_BUDGET_EXCEEDED,
            provider_code=status,
            budget_scope=scope,
            spend=_to_float(spend_m.group(1)) if spend_m else None,
            cap=cap,
            budget_date=budget_date,
            retryable=False,  # 额度不会因为等待而回来 —— 重试无论多少次都无用
            raw_excerpt=excerpt,
        )

    if status == 429:
        return ProviderErrorDiagnosis(
            error_type=ERROR_TYPE_RATE_LIMIT, provider_code=status,
            retryable=True, raw_excerpt=excerpt,
        )

    return ProviderErrorDiagnosis(
        error_type=ERROR_TYPE_OTHER, provider_code=status,
        retryable=is_retryable_status(status), raw_excerpt=excerpt,
    )


def parse_retry_after(resp: Any) -> float | None:
    """从响应/异常对象读 Retry-After 秒数；读不到返回 None。

    对 httpx response 与 urllib 的 HTTPError 都成立（两者都有 `.headers` 且支持
    `.get()`）。只支持 delta-seconds 形态；HTTP-date 形态解析失败按缺失处理，
    退回退避表（不因为格式不认识就放弃重试）。
    """
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:  # noqa: BLE001 — headers 可能不是 dict-like
        return None
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def backoff_delay(
    attempt: int,
    retry_after: float | None = None,
    *,
    schedule: list[float] | tuple[float, ...] | None = None,
    base: float = DEFAULT_BASE_BACKOFF_S,
    cap: float = DEFAULT_MAX_BACKOFF_S,
) -> float:
    """本次重试前该等多少秒：Retry-After 优先，否则退避上限 + full jitter，全程封顶。

    jitter 不是装饰：多进程并发（主跑 + backstop + 手动补跑）用固定退避序列会**同步**
    撞进同一个限流窗口，越退避越同时。full jitter（在 [0, ceiling] 上均匀取）是
    AWS 退避指南给的标准解法，也是 send.py 已在用的形态。

    Args:
        attempt: 0-based 尝试序号。
        retry_after: provider 给的 Retry-After 秒数；>0 时**原样尊重**（仅封顶），
            不加 jitter —— provider 明说了等多久，抖它反而是抢跑。
        schedule: 显式退避上限表（如 [5, 15, 45]）；超出长度取最后一项。
            不给则用 `base * 2**attempt` 指数式。
        cap: 单次等待硬上限。

    Returns:
        等待秒数（可能为 0.0 —— full jitter 允许立即重试）。
    """
    if retry_after and retry_after > 0:
        return min(float(retry_after), cap)
    if schedule:
        idx = min(max(attempt, 0), len(schedule) - 1)
        ceiling = float(schedule[idx])
    else:
        ceiling = base * (2 ** max(attempt, 0))
    return random.uniform(0.0, min(ceiling, cap))
