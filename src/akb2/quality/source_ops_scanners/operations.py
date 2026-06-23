"""Operations scanner — extracts source_operation evidence from raw text.

Five sub-scanners, all producing kind=source_operation:

1. mcp_protocol: 8 MCP protocol commands (migrated from distill.py:2412
   _SOURCE_PROTOCOL_COMMANDS). Uses the same word-boundary / substring logic
   as distill.py:2425 _source_text_has_command.
2. cli_commands: ``$ cmd`` inline patterns and fenced bash/sh block lines.
3. code_blocks: ``"method": "value"`` tokens inside fenced code blocks.
4. api_signatures: ``func(...)`` / ``obj.method(...)`` call patterns.
5. numbered_steps: ``Step N:`` and ``N.`` numbered list items (outside code
   blocks).
"""

from __future__ import annotations

import re

from . import EvidenceItem, Span
from ._common import fenced_block_spans, is_in_fenced_block

# ---------------------------------------------------------------------------
# MCP protocol commands — migrated from distill.py:2412
# ---------------------------------------------------------------------------

_SOURCE_PROTOCOL_COMMANDS: tuple[str, ...] = (
    "initialize",
    "tools/list",
    "tools/call",
    "resources/list",
    "resources/read",
    "prompts/list",
    "prompts/get",
    "notifications/initialized",
)


def _protocol_command_span(raw_text: str, command: str) -> Span | None:
    """Return span of first occurrence of command in raw_text.

    Migrates distill.py:2425 _source_text_has_command:
    - Commands containing "/" use substring match (case-insensitive).
    - Commands without "/" use word-boundary regex: (?<![a-z0-9_/-])cmd(?![a-z0-9_/-])
    """
    lower_text = raw_text.lower()
    if "/" in command:
        lower_cmd = command.lower()
        idx = lower_text.find(lower_cmd)
        if idx == -1:
            return None
        return Span(start=idx, end=idx + len(command))
    pattern = re.compile(
        rf"(?<![a-z0-9_/-]){re.escape(command)}(?![a-z0-9_/-])",
        re.IGNORECASE,
    )
    m = pattern.search(raw_text)
    if m is None:
        return None
    return Span(start=m.start(), end=m.end())


def scan_mcp_protocol(raw_text: str) -> list[EvidenceItem]:
    """Scan for the 8 MCP protocol commands defined in _SOURCE_PROTOCOL_COMMANDS."""
    items: list[EvidenceItem] = []
    for command in _SOURCE_PROTOCOL_COMMANDS:
        span = _protocol_command_span(raw_text, command)
        if span is None:
            continue
        items.append(
            EvidenceItem(
                kind="source_operation",
                literal=command,
                raw_anchor_span=span,
                must_cover_class="source_operation",
                source="mcp_protocol",
            )
        )
    return items


# ---------------------------------------------------------------------------
# CLI commands — $ cmd inline and fenced bash/sh blocks
# ---------------------------------------------------------------------------

_DOLLAR_CMD_RE = re.compile(r"(?m)^\s*\$\s+(?P<cmd>[^\n]+)$")
_BASH_LANGS = frozenset({"bash", "sh", "shell", "zsh", "shellsession"})
_FENCED_BLOCK_RE = re.compile(r"```(?P<lang>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)


def scan_cli_commands(raw_text: str) -> list[EvidenceItem]:
    """Scan for ``$ cmd`` inline patterns and fenced bash/sh block command lines."""
    items: list[EvidenceItem] = []
    # Inline $ cmd
    for m in _DOLLAR_CMD_RE.finditer(raw_text):
        cmd_text = m.group("cmd").strip()
        if not cmd_text:
            continue
        items.append(
            EvidenceItem(
                kind="source_operation",
                literal=cmd_text,
                raw_anchor_span=Span(start=m.start("cmd"), end=m.end("cmd")),
                must_cover_class="source_operation",
                source="cli_commands",
            )
        )
    # Fenced bash/sh blocks
    for m in _FENCED_BLOCK_RE.finditer(raw_text):
        lang = m.group("lang").strip().lower()
        if lang not in _BASH_LANGS:
            continue
        body = m.group("body")
        body_start = m.start("body")
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            line_start = body.find(line)
            abs_start = body_start + line_start
            items.append(
                EvidenceItem(
                    kind="source_operation",
                    literal=stripped,
                    raw_anchor_span=Span(start=abs_start, end=abs_start + len(stripped)),
                    must_cover_class="source_operation",
                    source="cli_commands",
                )
            )
    return items


# ---------------------------------------------------------------------------
# Code blocks — "method": "value" tokens inside fenced blocks
# ---------------------------------------------------------------------------

_JSON_METHOD_RE = re.compile(r'"method"\s*:\s*"(?P<method>[^"]+)"')


def scan_code_blocks(raw_text: str) -> list[EvidenceItem]:
    """Scan fenced code blocks for JSON-RPC method tokens (``"method": "value"``)."""
    items: list[EvidenceItem] = []
    for m in _FENCED_BLOCK_RE.finditer(raw_text):
        body = m.group("body")
        body_start = m.start("body")
        for mm in _JSON_METHOD_RE.finditer(body):
            method = mm.group("method")
            abs_start = body_start + mm.start("method")
            items.append(
                EvidenceItem(
                    kind="source_operation",
                    literal=method,
                    raw_anchor_span=Span(start=abs_start, end=abs_start + len(method)),
                    must_cover_class="source_operation",
                    source="code_blocks",
                )
            )
    return items


# ---------------------------------------------------------------------------
# API signatures — func(...) / obj.method(...) patterns
# ---------------------------------------------------------------------------

_API_CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<sig>[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\s*\("
)


def scan_api_signatures(raw_text: str) -> list[EvidenceItem]:
    """Scan for ``obj.method(...)`` call patterns outside code blocks.

    Only matches dotted method calls (``obj.method``) to avoid false positives
    from natural-language parentheticals like ``tools (e.g., ...)``. Bare
    ``func(...)`` patterns without a dot are intentionally skipped in this
    conservative first pass; they are ambiguous without type context.
    """
    block_spans = fenced_block_spans(raw_text)
    items: list[EvidenceItem] = []
    for m in _API_CALL_RE.finditer(raw_text):
        if is_in_fenced_block(m.start(), block_spans):
            continue
        sig = m.group("sig")
        items.append(
            EvidenceItem(
                kind="source_operation",
                literal=sig,
                raw_anchor_span=Span(start=m.start("sig"), end=m.end("sig")),
                must_cover_class="source_operation",
                source="api_signatures",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Numbered steps — Step N: and N. list items (outside code blocks)
# ---------------------------------------------------------------------------

_STEP_N_RE = re.compile(
    r"(?m)^(?:>\s*)?\"?(?P<step>Step\s+\d+\s*[:：]\s*[^\n\"]+)"
)
_NUMBERED_LIST_RE = re.compile(
    r"(?m)^(?:>\s*)?\"?(?P<item>\d+\.\s+[^\n\"]+)"
)


def scan_numbered_steps(raw_text: str) -> list[EvidenceItem]:
    """Scan for ``Step N:`` and ``N.`` numbered list items outside fenced code blocks."""
    block_spans = fenced_block_spans(raw_text)
    items: list[EvidenceItem] = []
    for m in _STEP_N_RE.finditer(raw_text):
        if is_in_fenced_block(m.start(), block_spans):
            continue
        step_text = m.group("step")
        items.append(
            EvidenceItem(
                kind="source_operation",
                literal=step_text,
                raw_anchor_span=Span(start=m.start("step"), end=m.end("step")),
                must_cover_class="source_operation",
                source="numbered_steps",
            )
        )
    for m in _NUMBERED_LIST_RE.finditer(raw_text):
        if is_in_fenced_block(m.start(), block_spans):
            continue
        item_text = m.group("item")
        items.append(
            EvidenceItem(
                kind="source_operation",
                literal=item_text,
                raw_anchor_span=Span(start=m.start("item"), end=m.end("item")),
                must_cover_class="source_operation",
                source="numbered_steps",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def scan(raw_text: str) -> list[EvidenceItem]:
    """Run all five operation sub-scanners and return combined items."""
    items: list[EvidenceItem] = []
    items.extend(scan_mcp_protocol(raw_text))
    items.extend(scan_cli_commands(raw_text))
    items.extend(scan_code_blocks(raw_text))
    items.extend(scan_api_signatures(raw_text))
    items.extend(scan_numbered_steps(raw_text))
    return items
