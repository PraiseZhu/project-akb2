"""harvest 落盘前凭证扫描与脱敏(sc-asm-redact)。

保证等级如实标注 T1(防疏忽泄露,不冒充防恶意构造绕过)。

公开面:
- ``RedactionPolicy`` — 策略枚举,恰两值:``Reject``(默认,命中即拒)与
  ``Redact``(命中即替换 [REDACTED] 后继续落盘)。没有 None/Off/Skip 之类
  第三值 —— 该参数只选策略、不能关闭扫描。
- ``RedactionFinding`` — 单次命中记录:模式类 + 位置(行号/JSON key 路径)。
  刻意**不携带命中值本身**(拒绝消息不回显凭证)。
- ``RedactionRejectedError`` — Reject 模式下任一命中即抛(调用方 exit 3)。
- ``scan_and_redact(payload, rendered_markdown, *, policy) -> (markdown, findings)``
  — 扫描对象 = 输入 JSON 全文 + 渲染后 markdown 全文;替换动作只发生在
  markdown 上(落盘的是它),payload 命中只参与 Reject 判定。

叙述性 token 字样(token budget / token 节省)不误伤:模式族只匹配「赋值/
头/块」形态,词组形态天然不命中。

无网络调用;独立函数可单测、可被 p2 复用。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "RedactionPolicy",
    "RedactionFinding",
    "RedactionRejectedError",
    "scan_and_redact",
]

# 替换占位符(替换动作的确定输出;断言差异集时以其为锚)
REDACTED_MARKER = "[REDACTED]"


class RedactionPolicy(Enum):
    """落盘策略:命中凭证时 拒(Reject) 或 替换后继续(Redact)。

    恰两值,无 None/Off/Skip 同义成员 —— 扫描不可由参数关闭。
    """

    Reject = "reject"
    Redact = "redact"


@dataclass(frozen=True)
class RedactionFinding:
    """一次凭证命中记录(含位置,不含值)。

    ``position`` 为可读位置描述:markdown 内 ``行号`` 或 payload 内 JSON
    key 路径;``offset``/``length`` 为 markdown 中的字符区间(替换动作与
    差异比对用,payload 命中为 -1)。
    """

    pattern_class: str
    position: str
    offset: int = -1
    length: int = 0


class RedactionRejectedError(RuntimeError):
    """Reject 策略下命中凭证:exit 3,消息点名位置与模式类,不回显命中值。"""

    def __init__(self, findings: list[RedactionFinding]):
        self.findings = tuple(findings)
        summary = ", ".join(f"{f.position} {f.pattern_class}" for f in findings)
        super().__init__(
            f"redaction: 检测到 {len(findings)} 处凭证形态,已拒绝落盘"
            f"(位置与模式类: {summary or '(无)'})"
        )


# ---------------------------------------------------------------------------
# 模式族(每族一个编译正则 + 具名类;值永不进入 finding/消息)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern]] = [
    # 1. PEM 私钥块(任何算法/加密形式)
    ("pem_private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL)),
    # 2. JWT 三段式(首段以 ey 开头,标准 base64url 头)
    ("jwt", re.compile(
        r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}")),
    # 3. GitHub token 前缀族
    ("github_token", re.compile(r"\b(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}")),
    # 4. AWS access key(AKIA 前缀 + 16 位大写)
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}")),
    # 5. Authorization 头(Bearer / Basic;接受 authorization 或 auth 键名,
    #    值前允许引号,如 auth = "Basic dXNlcjpwYXNz";也接受无键名的裸值,
    #    如正文里独立出现的 "Bearer <token>",但值至少 16 字符,
    #    避免 "Bearer of the news" 这类叙述性短语误伤)
    ("authorization_header", re.compile(
        r"(?i)(?:\b(?:authorization|auth)\b\s*[:=]\s*[\"']?"
        r"(?:bearer|basic)\s+[A-Za-z0-9+/=_-]+"
        r"|\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{16,})")),
    # 6. URL query / fragment 内凭证参数(任意位置,含 markdown 链接)
    ("url_query_secret", re.compile(
        r"(?i)[?#&](?:access_token|api_key|apikey|token|client_secret|auth|key|sig)"
        r"=[^&\s\"'<>]+")),
    # 7. 连接串 user:password@(任意 scheme,含 markdown 链接内的)
    ("connstring_userpass", re.compile(
        r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@")),
    # 8. 已知 secret 类键名赋值(env / JSON / YAML / 代码;键名族固定,
    #    含 AWS_SECRET_ACCESS_KEY / private_key 等组合键名;键名两侧允许
    #    JSON 引号,如 "password": "hunter2")
    ("secret_key_value", re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|access[_-]?token|access[_-]?key|"
        r"client[_-]?secret|(?:aws[_-]?)?secret[_-]?access[_-]?key|secret|"
        r"password|passwd|private[_-]?key|auth[_-]?token|session[_-]?token|"
        r"refresh[_-]?token|bearer[_-]?token|token)\b[\"']?"
        r"\s*[:=]\s*(?:[\"']?[^\"'\s,;}][^\"'\s,;}]*[\"']?|\"[^\"]*\")")),
]

# 未知高熵检测:键名不在已知族的赋值形态 + 值高熵
_UNKNOWN_KEY_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s*=\s*([^\s,;}]+)")

# 已知键名族(与 secret_key_value 模式的键名部分逐字对齐,用于排除双报,
# 而不是宽泛子串匹配——"MYSTERY_KEY" 含 "KEY" 但不属于该族,不应被排除)
_KNOWN_KEY_FAMILY_RE = re.compile(
    r"(?i)^(?:api[_-]?key|apikey|access[_-]?token|access[_-]?key|"
    r"client[_-]?secret|(?:aws[_-]?)?secret[_-]?access[_-]?key|secret|"
    r"password|passwd|private[_-]?key|auth[_-]?token|session[_-]?token|"
    r"refresh[_-]?token|bearer[_-]?token|token|authorization|auth)$")

# 高熵判据:长度 ≥ 24 且字符类别 ≥ 3(小写/大写/数字/符号)
_MIN_HIGH_ENTROPY_LEN = 24


def _is_high_entropy(value: str) -> bool:
    if len(value) < _MIN_HIGH_ENTROPY_LEN:
        return False
    classes = 0
    for char in value:
        if char.islower():
            classes |= 1
        elif char.isupper():
            classes |= 2
        elif char.isdigit():
            classes |= 4
        else:
            classes |= 8
        if classes >= 7:
            return True
    return classes >= 7


def _scan_unknown_high_entropy(text: str, base_line: int,
                               out: list[RedactionFinding]) -> None:
    """未知键名 + 高熵值的赋值形态(默认拒绝)。

    ``base_line`` 为文本起始行号(markdown 从 1 起;payload 扫描用 0 起防误报
    位置错乱,其命中只参与 Reject)。
    """
    for m in _UNKNOWN_KEY_RE.finditer(text):
        key, value = m.group(1), m.group(2)
        if _KNOWN_KEY_FAMILY_RE.match(key):
            continue  # 已知族由 _PATTERNS 覆盖,避免双报(锚定全匹配,非子串)
        if not _is_high_entropy(value):
            continue
        line = text.count("\n", 0, m.start(1)) + 1 + base_line
        out.append(RedactionFinding(
            pattern_class="unknown_high_entropy",
            position=f"line {line}",
            offset=-1,  # payload 侧无 markdown 区间
        ))


def _line_position(text: str, start: int) -> int:
    return text.count("\n", 0, start) + 1


# ---------------------------------------------------------------------------
# 扫描实现
# ---------------------------------------------------------------------------


def _scan_markdown(markdown: str) -> list[RedactionFinding]:
    """扫描 markdown 全文,返回按 offset 排序的命中(带行号与字符区间)。"""
    findings: list[RedactionFinding] = []
    for pattern_class, rx in _PATTERNS:
        for m in rx.finditer(markdown):
            findings.append(RedactionFinding(
                pattern_class=pattern_class,
                position=f"line {_line_position(markdown, m.start())}",
                offset=m.start(),
                length=m.end() - m.start(),
            ))
    _scan_unknown_high_entropy(markdown, 0, findings)
    findings.sort(key=lambda f: f.offset)
    return findings


def _scan_payload(payload: Any) -> list[RedactionFinding]:
    """扫描 payload 的规范化 JSON 全文(只参与 Reject 判定)。

    offset 置 -1:payload 不落盘、不产生替换动作。
    """
    try:
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return []
    findings: list[RedactionFinding] = []
    for pattern_class, rx in _PATTERNS:
        for _m in rx.finditer(text):
            findings.append(RedactionFinding(
                pattern_class=pattern_class,
                position="payload(json)",
                offset=-1,
            ))
    _scan_unknown_high_entropy(text, 0, findings)
    return findings


def _apply_replacements(markdown: str,
                        findings: list[RedactionFinding]) -> str:
    """将 markdown 中命中区间替换为 [REDACTED](区间外字节逐字保留)。

    命中区间先做合并:不同模式可能命中同一凭证的相邻/重叠片段
    (如 ``Authorization: Bearer eyJ...`` 同时命中 authorization_header
    与 jwt),重叠区间若各自切片会错乱 —— 合并后整体替换。
    """
    spans = sorted((f.offset, f.offset + f.length)
                   for f in findings if f.offset >= 0)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    chunks: list[str] = []
    cursor = len(markdown)
    for start, end in reversed(merged):
        chunks.append(markdown[end:cursor])
        chunks.append(REDACTED_MARKER)
        cursor = start
    chunks.append(markdown[:cursor])
    return "".join(reversed(chunks))


def scan_and_redact(
    payload: Any,
    rendered_markdown: str,
    *,
    policy: RedactionPolicy,
) -> tuple[str, list[RedactionFinding]]:
    """落盘前强制扫描:输入 JSON 全文 + 渲染后 markdown 全文。

    Args:
        payload: harvest JSON(扫描对象之一)。
        rendered_markdown: archive_raw 产出的 markdown 全文(含 frontmatter 与
            锚点 —— 只扫 payload 扫不到这部分)。
        policy: RedactionPolicy.Reject(默认,命中即抛)或 Redact(替换后返回)。

    Returns:
        (markdown, findings):Redact 分支返回替换后的 markdown 与替换清单
        (含位置,供差异比对);Reject 分支命中即抛 RedactionRejectedError。

    Raises:
        RedactionRejectedError: policy=Reject 且任一命中。消息点名位置与
            模式类,不回显命中值。
    """
    if not isinstance(policy, RedactionPolicy):
        raise TypeError(f"policy 必须是 RedactionPolicy,实际 {policy!r}")
    findings = _scan_markdown(rendered_markdown)
    payload_findings = _scan_payload(payload)
    if policy is RedactionPolicy.Reject:
        if findings or payload_findings:
            raise RedactionRejectedError(findings + payload_findings)
        return rendered_markdown, []
    # Redact:替换 markdown 内命中;payload 命中不落盘、不产生动作
    if findings:
        return _apply_replacements(rendered_markdown, findings), findings
    return rendered_markdown, []
