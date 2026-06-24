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
    re.compile(r"\brm\b.*\s-r[dfr]\b.*(/(\s|$)|--no-preserve-root|\*|\$HOME|$OLDPWD)"),
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

    return violations


__all__ = ["Violation", "is_command_allowed_for_candidate", "verify_commands_in_raw"]
