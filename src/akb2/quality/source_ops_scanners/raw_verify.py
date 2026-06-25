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
        violation = _classify_command_violation(
            m.group(1),
            raw_text,
            candidate_offset=m.start(1),
        )
        if violation is not None:
            violations.append(violation)

    violations.extend(_fenced_block_command_violations(candidate_body, raw_text))
    violations.sort(key=lambda v: v.candidate_offset)

    return violations


__all__ = ["Violation", "is_command_allowed_for_candidate", "verify_commands_in_raw"]
