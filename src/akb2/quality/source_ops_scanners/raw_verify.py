"""Raw command recheck — full literal recheck + dangerous-command blocklist.

Full literal recheck (SC-4 原意): every backtick command in the candidate body
(excluding reserved structural tokens) must appear as a verbatim substring of
``raw_text``. Absence = the command is not grounded in source = fabricated. This
applies uniformly to all commands regardless of provenance marker
(``source_grounded`` / ``akb_interpretation`` / unannotated) — the previous
"code-direct only" narrowing let safe-but-fabricated commands through, which
violates SC-4 (master-plan:132-134 / step-4:36-49 require every backtick
command to trace back to raw).

Dangerous-command blocklist (附加拦截): a conservative deterministic blocklist is
applied as an ADDITIONAL layer on top of the raw recheck. A command matching a
destructive pattern is flagged as a Violation EVEN IF it appears in raw — this
double-intercepts ``rm -rf /``, fork bombs, pipe-to-shell remote scripts, etc.
that might slip through verbatim.

Complements ``SG-must-cover`` (step-3, treats "missing" — raw has it, candidate
did not render it). This module treats "fabricated" (command whose literal is
absent from raw) + "destructive" (command that would harm the system if
executed). Together they form a two-sided anchor: nothing is dropped, nothing
is fabricated, and nothing destructive slips through.

Public API:
  - is_command_allowed_for_candidate(command, raw_text) -> bool
  - verify_commands_in_raw(candidate_body, raw_text) -> list[Violation].
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Inline backtick command: `` `non-empty, no backtick inside` ``.
# We deliberately keep the capture permissive and filter afterwards so that
# legitimate prose quoting (`` ` ``, `` ``` ``) is excluded by content rules
# rather than by a brittle regex.
_BACKTICK_COMMAND_RE = re.compile(r"`([^`\n]+)`")
_FENCED_BLOCK_RE = re.compile(
    r"(?ms)^```(?P<lang>[^\n`]*)\n(?P<body>.*?)(?:^```[ \t]*$|\Z)"
)
_SHELL_FENCE_LANGS: frozenset[str] = frozenset(
    {"bash", "sh", "zsh", "shell", "console", "terminal"}
)

# Well-known command-OUTPUT line shapes (git / pytest / diff / push transcripts).
# These are captured output, not copy-pasteable commands, so inside a shell
# fence they are exempt from the raw recheck — otherwise a transcript like
# ``$ git status`` followed by ``On branch main`` / ``nothing to commit`` would
# flag the output lines as "fabricated" (they are rarely echoed verbatim in
# raw), making repair churn or never converge. Each pattern is anchored to the
# line start and kept specific so a real command is never mistaken for output
# (fail-closed: skipping happens only for unmistakable output shapes). The
# dangerous-command blocklist still runs on skipped lines (defensive).
_OUTPUT_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^On branch \S"),
    re.compile(r"^HEAD detached at \S"),
    re.compile(r"^Already up[- ]to[- ]date\.?$", re.IGNORECASE),
    re.compile(r"^nothing to commit\b"),
    re.compile(r"^no changes added to commit\b"),
    re.compile(r"^Untracked files:"),
    re.compile(r"^Changes (not staged for commit|to be committed):"),
    re.compile(r"^Your branch is (up to date|ahead|behind)\b"),
    re.compile(r"^\s*\d+ files? changed\b"),
    re.compile(r"^diff --git\b"),
    re.compile(r"^index [0-9a-f]{6,}\b"),
    re.compile(r"^(---|\+\+\+) "),
    re.compile(r"^@@ .* @@"),
    re.compile(r"^remote:\s"),
    re.compile(r"^To \S+\.git\b"),
    re.compile(r"^Cloning into '"),
    re.compile(r"^\s*\d+ (passed|failed|error|errors|skipped|deselected)\b"),
    re.compile(r"^=+.*\b(passed|failed|error)\b.*=+$", re.IGNORECASE),
    re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)\b"),
)

# Tokens that look like a backtick command but are structural / metadata, not
# a copy-pasteable operation. These must NOT be subject to either layer.
# - raw anchor tokens: §A, §B1, [raw §A]
# - provenance markers: (source_grounded), (akb_interpretation)
# - wikilink fragments: [[...]]
# - fence language tags: ```bash
# - frontmatter-ish bare keys (no spaces, no slash, no dot): rare in body but
#   we keep the rule conservative — only block exact reserved words.
_RESERVED_LITERAL_TOKENS: frozenset[str] = frozenset(
    {
        "source_grounded",
        "akb_interpretation",
        "raw",
        "source",
        "code",
        "bash",
        "sh",
        "shell",
        "zsh",
        "python",
        "json",
        "yaml",
    }
)

# ---------------------------------------------------------------------------
# Prose / reference literals — excluded from the INLINE fabrication recheck
# ---------------------------------------------------------------------------
#
# ``_BACKTICK_COMMAND_RE`` captures EVERY inline backtick span. SC-4 deliberately
# rechecks all *technical* literals (commands, configs, code) against raw — that
# is correct and must be preserved. But markdown backticks also wrap PROSE and
# REFERENCES, which are not copy-pasteable technical literals and whose absence
# from raw is not "fabrication". Two such shapes false-flagged real concept
# pages in full-warehouse calibration #0/#2/#8/#10/#17:
#   - numbered-list PROSE wrapped in backticks: ``1. Pick the next task`` — a
#     step DESCRIPTION (the ``1. `` sits INSIDE the backtick span), not a
#     command. (Distinct from ``1. ... `cmd` `` where ``1.`` is the line prefix
#     OUTSIDE the backticks and the captured literal is the real ``cmd``.)
#   - repo slugs: ``owner/repo`` — a citation/reference, not a command.
#
# Everything else (``trigger={...}`` config, ``deploy-preview --target`` /
# ``configure --env prod`` commands, arbitrary tool invocations, code) stays
# fully rechecked, so the SC-4 "every grounded technical literal" intent and the
# dangerous-command blocklist are untouched. FENCED ```` ```bash ```` blocks are
# also unchanged (every line still fully rechecked — no fenced-bypass reopen).
_NUMBERED_PROSE_PREFIX_RE = re.compile(r"^\s*\d+[.)]\s")
# Repo slug ``owner/repo``: exactly one slash, word/.-/_ on both sides, no space.
# A reference, not a command. File-path-shaped tokens that ARE commands (e.g.
# ``./build.sh arg``) carry a space/arg and so are not matched here.
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")

# p0-3: CLI 动词白名单（正向真命令识别）。以这些动词开头的 backtick literal 是真命令
# 形态 → **不豁免**（必须回查，编造即报，如 ``pip install evil``）。复用 admission.py
# _NUMBERED_WITH_CMD_RE 的 CLI 动词思路（npm/pip/python/git/docker...）。
_CLI_VERB_TOKENS: frozenset[str] = frozenset({
    "npm", "npx", "pip", "pip3", "python", "python3", "git", "docker",
    "curl", "wget", "ssh", "scp", "sudo", "rm", "cp", "mv", "chmod",
    "chown", "cd", "ls", "cat", "echo", "export", "source", "make",
    "cargo", "go", "rustc", "gcc", "node", "yarn", "pnpm", "brew",
    "apt", "apt-get", "yum", "systemctl", "launchctl", "tar", "unzip",
    "mkdir", "touch", "grep", "sed", "awk", "find", "ps", "kill",
    "openssl", "gpg", "kubectl", "helm", "terraform", "aws", "gcloud",
    "az", "pytest", "ruff", "mypy", "eslint", "tsc", "vite", "webpack",
    "rollup", "bash", "sh", "zsh", "fish",
})

# 技术形态符号：出现任一即非纯术语（保留回查，SC-4）。
# p0-3 收紧（屏障反馈）：``-``（连字符）视为命令名形态（mcp-server / fabricated-cmd /
# deploy-preview / missing-cmd），含 ``-`` 的 literal 不豁免（回查），避免编造命令漏放
# （SC-3 护栏"真编造命令必报"）；纯术语（Conv / 和 / 组件协作）不含 ``-`` 仍豁免。
# G2 自审：``|``（管道操作符）视为技术形态——单 ``|`` 的 shell 管道（X | sh）由
# _is_table_fragment 收窄后不再豁免，此处 ``|`` 作 _is_pure_term 兜底（防漏放）；
# 真表格/provenance 碎片（≥2 | 或含 marker）由 _is_table_fragment 先判豁免，不进此处。
_TECH_SHAPE_SIGNS: frozenset[str] = frozenset({
    "$", "--", "-", "=", "{", "}", "(", ")", "<", ">", "\"", "'",
    "\\", ";", "&", "/", "|",
})


def _starts_with_cli_verb(s: str) -> bool:
    """True if the literal starts with a known CLI verb (real command shape).

    命中 → 真命令形态 → 必须回查（不豁免），即使其余部分编造（``pip install evil``）。
    路径前缀的命令（``/usr/bin/python``）取 basename 后判。
    """
    stripped = s.strip()
    if not stripped:
        return False
    first = stripped.split(maxsplit=1)[0]
    if "/" in first:
        first = first.rsplit("/", 1)[-1]
    return first in _CLI_VERB_TOKENS


# G2: provenance 标记——含任一即 provenance split 碎片（非命令），表格碎片豁免用。
_PROVENANCE_MARKERS: frozenset[str] = frozenset({
    "akb_interpretation", "source_grounded", "source_fact", "source_operation",
})

# G2 第 5 轮（gpt reviewer 精确方案）：ASCII 命令 token 正则——认合法命令名形态
# （含数字/下划线/点号/连字符/前导数字：s3cmd/2to3/cmd_foo/tool.v1/apt-get/pip3）。
# 用于 _cell_is_non_command 判大写首字母术语（match + 首大写 → 术语）。
_ASCII_CMD_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _cell_is_non_command(cell: str) -> bool:
    """True if a ``|``-split cell is definitively NON-command (strict allowlist).

    G2 第 8 轮（gpt reviewer 终结性结构修复）：命令检测**无条件最先**跑（不带任何
    跳过门禁），所有豁免只对"非命令 cell"生效，杜绝任一豁免形态（marker/CJK/excerpt/
    术语）吞命令。修复 round 7 的 1c `if "..." not in cell` 门禁导致 `agentctl ...`
    （命令带 `...` 参数）跳过命令检测、被 excerpt 放行。1c 用**首 token 不含 ``...``**
    （非 cell 级）区分 `agentctl ...`（首 token `agentctl` 命令→挡）vs `u...`（首 token
    `u...` excerpt→留）。
    """
    if not cell:
        return True
    # 0. provenance marker → 确证非命令（先判，marker 不被命令检测误伤）
    if cell in _PROVENANCE_MARKERS:
        return True
    # 1. 无条件命令检测（命中任一 → 立即 return False 回查，不被 `...`/CJK 任何东西跳过）
    # 1a. 含技术形态符 → 命令
    if any(sign in cell for sign in _TECH_SHAPE_SIGNS):
        return False
    first = cell.split(maxsplit=1)[0]
    # 1b. 首 token ∈ CLI 动词 → 命令
    if first in _CLI_VERB_TOKENS:
        return False
    # 1c. 首 token 是 ASCII 命令 token（正则命中且首小写或数字，首 token 不含 `...`）→ 命令。
    # 首 token 含 `...`（如 `u...`）是 excerpt 截断 → 排除，进 2a 窄 excerpt 判定。
    if "..." not in first and _ASCII_CMD_TOKEN_RE.match(first) and not first[0].isupper():
        return False
    # 2. 过了命令检测（确认非命令首 token、无技术符号）才放行
    # 2a. 窄 excerpt：纯短截断片段——无内部空格、无技术形态、整体含 `...`（如 `u...`）
    if "..." in cell and " " not in cell and not any(
        sign in cell for sign in _TECH_SHAPE_SIGNS
    ):
        return True
    # 2b. 大写首字母 ASCII 术语（Conv/API/SDK）
    if _ASCII_CMD_TOKEN_RE.match(first) and first[0].isupper():
        return True
    # 2c. 纯 CJK 或中文标点（此时已确保不是命令首 token）
    if any("一" <= c <= "鿿" for c in cell):
        return True
    return False


def _is_table_fragment(s: str) -> bool:
    """True for markdown table / provenance-split fragments (not shell pipeline).

    G2 第 6 轮（gpt reviewer 统一 allowlist）：去掉整串 marker 先判（round 5 加的
    ``if any(marker in s): return True`` 被 ``agentctl | akb_interpretation | sh``
    绕过——塞个 marker token 就整串豁免、跳过 cell allowlist）。改成 cell 级：
    literal 豁免**当且仅当 EVERY cell 都确证非命令**（``_cell_is_non_command``）。
    任一 cell 命令形态 → 不豁免（回查，守 SC-3"真编造命令必报"）。全 cell 非命令 +
    (含 provenance marker 或 ≥2 列) → 豁免。
    保留 #16 provenance split 碎片（``u... | akb_interpretation | 未提供...``：cell
    ``u...`` excerpt + ``akb_interpretation`` marker + CJK，全非命令）豁免。
    """
    if "|" not in s:
        return False
    cells = [c.strip() for c in s.split("|")]
    # 豁免当且仅当 EVERY cell 都确证非命令
    if not all(_cell_is_non_command(c) for c in cells):
        return False  # 任一 cell 命令形态 → 不豁免（回查）
    # 全 cell 非命令 → 真表格/provenance 碎片
    if any(marker in s for marker in _PROVENANCE_MARKERS):
        return True
    if s.count("|") >= 2:
        return True
    # 单 | 全 cell 非命令 但无 marker 无多列 → 默认回查（不豁免）
    return False


def _is_single_cjk_term(s: str) -> bool:
    """True for single-char / short CJK terms (如 ``和``) — prose, not a command.

    G2 第 8 轮（gpt 补充核查）：命令首 token 优先——防 `go 参数`/`agent 参`（命令首
    token + 中文参数，len≤6）被 CJK 分支吞。首 token 命令 → False，再 CJK 放行。
    """
    stripped = s.strip()
    if not stripped or len(stripped) > 6:
        return False
    if any(sign in stripped for sign in _TECH_SHAPE_SIGNS):
        return False
    # 命令检测优先：首 token 命令 → False（压倒 CJK 放行）
    first = stripped.split(maxsplit=1)[0]
    if first in _CLI_VERB_TOKENS:
        return False
    if "..." not in first and _ASCII_CMD_TOKEN_RE.match(first) and not first[0].isupper():
        return False
    return any("一" <= c <= "鿿" for c in stripped)


def _is_pure_term(s: str) -> bool:
    """True for pure terms — CJK term or Capitalized ASCII token (not a command).

    G2 第 8 轮（gpt 补充核查）：命令首 token 优先——防 `agentctl 参数`/`cmd2 参数`
    （命令首 token + 中文参数）被 CJK 分支吞（round 5 的 `any CJK → True` 在命令检测前）。
    首 token 命令 → False，再 CJK/大写术语放行。
    """
    stripped = s.strip()
    if not stripped or len(stripped) > 30:
        return False
    if "$" in stripped:
        return False
    if any(sign in stripped for sign in _TECH_SHAPE_SIGNS):
        return False
    # 命令检测优先：首 token 命令 → False（压倒 CJK 放行）
    first = stripped.split(maxsplit=1)[0]
    if first in _CLI_VERB_TOKENS:
        return False
    if "..." not in first and _ASCII_CMD_TOKEN_RE.match(first) and not first[0].isupper():
        return False
    # 过了命令检测才放行
    # CJK 术语（中文，非命令）→ 豁免
    if any("一" <= c <= "鿿" for c in stripped):
        return True
    # 大写首字母 ASCII 单 token（术语/缩写/库名：Conv/API/SDK/React）→ 豁免
    if (
        stripped.isascii()
        and stripped.isalpha()
        and " " not in stripped
        and stripped[0].isupper()
    ):
        return True
    # 裸小写 ASCII token（命令名形态）→ 不豁免（回查）
    return False


def _is_prose_or_reference_literal(literal: str) -> bool:
    """True for backtick literals that are PROSE/REFERENCE/non-command, not technical.

    p0-3 命令形态判定：在既有编号散文 + repo slug 之上扩展豁免——
      - 单字/短词中文（``和``）；
      - 含 ``|`` 的 markdown 表格/provenance split 碎片（``u... | akb_interpretation | ...``）；
      - 纯术语/概念名（无 CLI 动词、无 ``$``、无路径/参数形态）。
    正向真命令识别：以 CLI 动词白名单（npm/pip/python/git/docker...）开头的 literal 是
    真命令形态，**不豁免**（必须回查，编造即报，如 ``pip install evil``）。
    其他技术 literal（配置/代码/路径/带参数命令）也不豁免，保留 SC-4 全回查。
    危险命令黑名单对豁免项仍跑（caller responsibility，双重拦截）。
    """
    s = literal.strip()
    if not s:
        return False
    if _NUMBERED_PROSE_PREFIX_RE.match(s):
        return True
    if _REPO_SLUG_RE.match(s):
        return True
    # 正向真命令识别：CLI 动词白名单命中 → 真命令形态，不豁免（回查 + 危险黑名单）
    if _starts_with_cli_verb(s):
        return False
    # 命令形态豁免（明确非命令形态）
    if _is_table_fragment(s):
        return True
    if _is_single_cjk_term(s):
        return True
    if _is_pure_term(s):
        return True
    return False


# ---------------------------------------------------------------------------
# Dangerous-command blocklist (附加拦截层)
# ---------------------------------------------------------------------------
#
# Conservative, explicit patterns for commands that are unambiguously
# destructive and must never appear in a candidate How body regardless of
# provenance or raw presence. These are matched against the command literal
# with word boundaries where appropriate. The blocklist is intentionally
# narrow — false positives here would block legitimate operation examples, so
# each pattern targets a concrete destructive shape rather than a broad
# keyword.
#
# A command matching a destructive pattern is flagged EVEN IF it appears
# verbatim in raw (double interception: the raw recheck alone would pass a
# destructive command that happens to be quoted from source).
#
# Categories covered:
#   - rm -rf / rm -fr with root or no-preserve-root
#   - mkfs (filesystem reformat)
#   - dd if= of=/dev/ (raw device overwrite)
#   - fork bomb :(){:|:&};:
#   - redirect to /dev/sd* / /dev/disk (device clobber)
#   - chmod 777 (world-writable, especially on system dirs)
#   - curl|wget piped to sh (remote script execution)
#   - --no-preserve-root anywhere
#   - sudo (privilege escalation in a How example is a red flag)
#   - writes to /etc/ /usr/ /boot (system file tampering)
#   - ssh with a private key file (credential exfiltration shape)
#   - aws with --secret-access-key / --token (credential leak shape)
#   - shutdown / reboot / halt / poweroff
#   - iptables -F / ufw disable (firewall teardown)
_DANGEROUS_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    # rm -rf / rm -fr targeting root or with no-preserve-root
    # NOTE: $OLDPWD must be escaped as \$OLDPWD — a bare ``$`` is a regex
    # end-anchor, so the unescaped form matched "end-of-string then literal
    # OLDPWD" (impossible) and let ``rm -rf $OLDPWD`` slip past this layer.
    re.compile(r"\brm\b.*\s-r[dfr]\b.*(/(\s|$)|--no-preserve-root|\*|\$HOME|\$OLDPWD)"),
    re.compile(r"\brm\b.*\s--no-preserve-root"),
    re.compile(r"\brm\s+-[rf][rf]?\s+/\s*$"),
    re.compile(r"\brm\s+-[rf][rf]?\s+/\s"),
    # mkfs — reformat filesystem
    re.compile(r"\bmkfs\b"),
    # dd writing to a raw device
    re.compile(r"\bdd\b.*\bof=/dev/(?!null|zero|random|urandom)"),
    # fork bomb — common variants: :(){:|:&};: and :(){ :|: & };:
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&?\s*\}\s*;\s*:"),
    # redirect to a raw disk device
    re.compile(r">\s*/dev/(sd[a-z]+|disk\d|nvme\d)"),
    # chmod 777 on system dirs
    re.compile(r"\bchmod\b.*\s777\s+(/(etc|usr|bin|sbin|boot|var|root|sys|proc)\b|\$HOME)"),
    # curl/wget piped to shell interpreter
    re.compile(r"\b(curl|wget)\b.*\|\s*(sh|bash|zsh|sh -|bash -)\b"),
    re.compile(r"\b(curl|wget)\b.*\|\s*sudo\s+(sh|bash)"),
    # no-preserve-root anywhere
    re.compile(r"--no-preserve-root"),
    # sudo in a How example is a red flag (privilege escalation)
    re.compile(r"^\s*sudo\b"),
    re.compile(r"\bsudo\s+rm\b"),
    # writes to system directories
    re.compile(r">\s*/etc/"),
    re.compile(r"\bmv\b.*\s+/etc/"),
    re.compile(r"\bcp\b.*\s+/usr/(local/)?(bin|sbin|lib)"),
    # shutdown / reboot / halt
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"),
    # firewall teardown
    re.compile(r"\biptables\s+-F\b"),
    re.compile(r"\bufw\s+disable\b"),
    # ssh with a private key file (credential shape)
    re.compile(r"\bssh\b.*\s-i\s+\S+\.pem\b"),
    re.compile(r"\bssh\b.*\s-i\s+\S+id_"),
    # aws with explicit credentials on the command line (leak shape)
    re.compile(r"\baws\b.*--secret-access-key\b"),
    re.compile(r"\baws\b.*--session-token\b"),
    # GitHub/GitLab token export inline
    re.compile(r"export\s+.*(GH_TOKEN|GITHUB_TOKEN|GITLAB_TOKEN|OPENAI_API_KEY)\s*="),
)


@dataclass(frozen=True)
class Violation:
    """One command flagged by the full-recheck + dangerous-blocklist gate.

    Attributes:
        command: the backtick literal as it appears in the candidate body.
        reason: human-readable explanation of why it is flagged. The prefix
            distinguishes the two layers: "含危险操作..." (dangerous blocklist,
            flagged even if present in raw) and "原文不存在此命令字面量..." /
            "raw 文本为空..." (full literal recheck, command absent from raw).
        candidate_offset: char offset of the backtick literal in candidate_body.
        raw_nearest_line: when the command is absent from raw (recheck layer),
            the raw line that shares the longest common prefix with the command
            (for triage); None for dangerous-blocklist violations or when
            raw_text is empty.
    """

    command: str
    reason: str
    candidate_offset: int
    raw_nearest_line: str | None


def _is_reserved_token(literal: str) -> bool:
    """True for tokens that are structural / metadata, not a command to recheck."""
    stripped = literal.strip()
    if not stripped:
        return True
    if stripped in _RESERVED_LITERAL_TOKENS:
        return True
    # raw anchor §X / §A1 tokens — not commands.
    if stripped.startswith("§"):
        return True
    # wikilink fragments [[...]]
    if stripped.startswith("[[") or stripped.startswith("]]"):
        return True
    # pure provenance markers like "(source_grounded)"
    if stripped.startswith("(") and stripped.endswith(")"):
        return True
    # fence language tags captured by ```lang are filtered by regex already,
    # but be defensive: a bare tag like "bash" with no content is reserved.
    return False


def _is_dangerous_command(literal: str) -> bool:
    """Dangerous-command blocklist: True if the command literal matches a destructive pattern.

    Deterministic, no LLM. Conservative — each pattern targets a concrete
    destructive shape. ``False`` means "not obviously destructive, proceed to
    raw literal recheck".
    """
    for pattern in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(literal):
            return True
    return False


def _fence_lang(raw_lang: str) -> str:
    """Normalize a fenced-code language tag to its first lowercase token."""
    parts = raw_lang.strip().lower().split(maxsplit=1)
    return parts[0] if parts else ""


def _strip_shell_prompt(line: str) -> tuple[str, int]:
    """Strip common shell prompts from fenced shell examples.

    Returns the command and the offset delta from line start to command start.
    """
    stripped = line.strip()
    base_offset = len(line) - len(line.lstrip())
    if stripped.startswith("$ "):
        return stripped[2:].strip(), base_offset + 2
    return stripped, base_offset


def _iter_logical_command_lines(body: str) -> list[tuple[str, int, bool]]:
    """Split a fenced block body into logical command lines.

    Physical lines joined by a trailing backslash (``\\``) are a single logical
    command wrapped across lines for readability; they are merged into one unit
    (parts joined by a single space, indentation collapsed). Without merging,
    each fragment (``pytest foo \\``, ``  bar``) is recheck'd separately and
    flagged as "fabricated" because the wrapped fragments are not verbatim
    substrings of a raw source that holds the command on one line.

    Returns ``(command, offset_in_body, is_prompt)`` tuples where ``command`` is
    prompt-stripped and stripped, ``offset_in_body`` points at the command start
    of the unit's first physical line, and ``is_prompt`` is True when that first
    line carried a ``$ `` shell prompt.
    """
    units: list[tuple[str, int, bool]] = []
    pos = 0
    parts: list[str] = []  # non-empty => currently merging a `\`-continuation
    start_offset = 0
    start_is_prompt = False
    for raw_line in body.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        line_start = pos
        pos += len(raw_line)
        if parts:
            # Continuation line: merge verbatim (no prompt stripping).
            part = line.strip()
            continues = part.endswith("\\")
            if continues:
                part = part[:-1].rstrip()
            parts.append(part)
            if not continues:
                units.append(
                    (" ".join(p for p in parts if p).strip(), start_offset, start_is_prompt)
                )
                parts = []
            continue
        stripped = line.strip()
        if not stripped:
            continue
        command, line_delta = _strip_shell_prompt(line)
        is_prompt = stripped.startswith("$ ")
        if command.endswith("\\"):
            parts = [command[:-1].rstrip()]
            start_offset = line_start + line_delta
            start_is_prompt = is_prompt
        else:
            units.append((command, line_start + line_delta, is_prompt))
    if parts:  # unterminated trailing continuation — flush what we have
        units.append((" ".join(p for p in parts if p).strip(), start_offset, start_is_prompt))
    return units


def _is_shell_output_line(command: str, *, is_prompt: bool, has_prompt: bool) -> bool:
    """True when a shell-fence line is captured output, not a command.

    Two fail-closed signals (only unmistakable output is skipped):

    1. Prompt transcript: when the block uses ``$ `` prompts at all, only the
       prompt lines are commands; every other line is captured output. This
       cleanly handles multi-line ``git status`` / ``pytest`` transcripts where
       output lines (``modified: ...``, ``no changes added...``) are not in the
       fixed pattern list.
    2. Known-output shapes: lines matching git / pytest / diff / push output
       patterns are output even in prompt-less blocks.
    """
    if has_prompt and not is_prompt:
        return True
    return any(pattern.search(command) for pattern in _OUTPUT_LINE_PATTERNS)


def _fenced_block_command_violations(
    candidate_body: str,
    raw_text: str,
) -> list[Violation]:
    """Return command violations from fenced code blocks.

    Shell-like fences are command examples, so every non-empty command line is
    checked with the same raw/dangerous classifier as inline backticks, with two
    false-positive guards: ``\\``-continued physical lines are merged into one
    logical command before rechecking, and captured-output lines (shell prompts'
    output, diff/pytest transcripts) are exempt from the raw recheck (the
    dangerous blocklist still runs on them). Data or programming fences are not
    shell-checked, but dangerous shell snippets inside them are still
    intercepted.
    """
    violations: list[Violation] = []
    for block in _FENCED_BLOCK_RE.finditer(candidate_body):
        lang = _fence_lang(block.group("lang"))
        body = block.group("body")
        body_start = block.start("body")
        is_shell = lang in _SHELL_FENCE_LANGS
        units = _iter_logical_command_lines(body)
        has_prompt = any(is_prompt for _, _, is_prompt in units)
        for command, offset_in_body, is_prompt in units:
            command_offset = body_start + offset_in_body
            if not command or command.startswith("#"):
                continue
            if is_shell:
                if _is_shell_output_line(
                    command, is_prompt=is_prompt, has_prompt=has_prompt
                ):
                    # Captured output, not a command — never raw-recheck it, but
                    # still intercept an unmistakably destructive shape.
                    if _is_dangerous_command(command):
                        violations.append(
                            Violation(
                                command=command,
                                reason="含危险操作（破坏性/权限/网络/凭据）",
                                candidate_offset=command_offset,
                                raw_nearest_line=None,
                            )
                        )
                    continue
                violation = _classify_command_violation(
                    command,
                    raw_text,
                    candidate_offset=command_offset,
                )
                if violation is not None:
                    violations.append(violation)
            elif _is_dangerous_command(command):
                violations.append(
                    Violation(
                        command=command,
                        reason="含危险操作（破坏性/权限/网络/凭据）",
                        candidate_offset=command_offset,
                        raw_nearest_line=None,
                    )
                )
    return violations


def _raw_line_with_longest_common_prefix(
    raw_text: str, command: str
) -> str | None:
    """Return the raw line sharing the longest common prefix with command.

    Used only for triage (helps the author locate the closest raw line when a
    command is flagged). Returns None when raw_text has no non-empty lines.
    """
    best_line: str | None = None
    best_len = -1
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Common prefix length (char-level). zip without strict is intentional:
        # we only care about the matched prefix; unequal-length strings stop
        # at the shorter one, which is exactly the prefix semantics we want.
        i = 0
        for a, b in zip(stripped, command, strict=False):
            if a != b:
                break
            i += 1
        if i > best_len:
            best_len = i
            best_line = stripped
    return best_line


def _classify_command_violation(
    command: str,
    raw_text: str,
    *,
    candidate_offset: int = 0,
) -> Violation | None:
    """Return the shared single-command violation, or None when allowed."""
    literal = command.strip()
    if _is_reserved_token(literal):
        return None

    if _is_dangerous_command(literal):
        return Violation(
            command=literal,
            reason="含危险操作（破坏性/权限/网络/凭据）",
            candidate_offset=candidate_offset,
            raw_nearest_line=None,
        )

    if not raw_text or literal not in raw_text:
        nearest = (
            _raw_line_with_longest_common_prefix(raw_text, literal)
            if raw_text
            else None
        )
        reason = (
            "raw 文本为空，无法回查命令字面量"
            if not raw_text
            else "原文不存在此命令字面量（疑似编造）"
        )
        return Violation(
            command=literal,
            reason=reason,
            candidate_offset=candidate_offset,
            raw_nearest_line=nearest,
        )

    return None


def is_command_allowed_for_candidate(command: str, raw_text: str) -> bool:
    """Shared compose/repair predicate matching ``verify_commands_in_raw``.

    Empty/reserved literals are allowed; dangerous commands are denied even when
    present in raw; all other commands must be verbatim substrings of raw_text.
    """
    return _classify_command_violation(command, raw_text) is None


def verify_commands_in_raw(
    candidate_body: str, raw_text: str
) -> list[Violation]:
    """Full literal recheck + dangerous-command blocklist.

    For every backtick command in ``candidate_body`` (excluding reserved
    structural tokens), in document order:

    1. Dangerous-command blocklist (附加拦截): if the command literal matches a
       destructive pattern, it is flagged as a Violation IMMEDIATELY — even if
       it appears verbatim in ``raw_text``. This double-intercepts destructive
       commands that happen to be quoted from source. Dangerous commands take
       precedence over the raw recheck (a destructive command is blocked
       regardless of whether it is also fabricated).

    2. Full literal recheck (SC-4 原意): otherwise the command must appear as a
       verbatim substring of ``raw_text``. Absence = not grounded in source =
       fabricated = Violation. This applies uniformly to ALL commands regardless
       of provenance marker (``source_grounded`` / ``akb_interpretation`` /
       unannotated) — the previous "code-direct only" narrowing let
       safe-but-fabricated commands through. Empty ``raw_text`` ⇒ every
       non-reserved command is a Violation.

    Reserved structural tokens (raw anchors, provenance markers, fence language
    tags, wikilink fragments) are exempt from both layers.

    Args:
        candidate_body: the candidate page body (frontmatter optional; this
            function scans the whole string — callers usually pass the body
            without frontmatter, but frontmatter presence is harmless because
            backticks inside frontmatter are rare and still must be grounded).
        raw_text: the raw source text the candidate was distilled from. Must
            not be None; callers should handle None upstream (fail-closed).

    Returns:
        List of Violation records, in document order. Empty list means every
        backtick command is grounded in raw AND none matched the dangerous
        blocklist.
    """
    violations: list[Violation] = []

    for m in _BACKTICK_COMMAND_RE.finditer(candidate_body):
        literal = m.group(1)
        if _is_prose_or_reference_literal(literal):
            # Prose (numbered step) or reference (repo slug) — not a technical
            # literal, so absence from raw is not fabrication. Still intercept
            # an unmistakably destructive literal hiding here (dangerous
            # blocklist stays universal).
            stripped = literal.strip()
            if not _is_reserved_token(stripped) and _is_dangerous_command(stripped):
                violations.append(
                    Violation(
                        command=stripped,
                        reason="含危险操作（破坏性/权限/网络/凭据）",
                        candidate_offset=m.start(1),
                        raw_nearest_line=None,
                    )
                )
            continue
        violation = _classify_command_violation(
            literal,
            raw_text,
            candidate_offset=m.start(1),
        )
        if violation is not None:
            violations.append(violation)

    violations.extend(_fenced_block_command_violations(candidate_body, raw_text))
    violations.sort(key=lambda v: v.candidate_offset)

    return violations


__all__ = ["Violation", "is_command_allowed_for_candidate", "verify_commands_in_raw"]
