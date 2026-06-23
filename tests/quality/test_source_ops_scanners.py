"""Tests for source_ops_scanners module.

Verifies:
- SC-1.1: scan_all(test-fresh raw) produces 3 categories ⊇ golden fixture items.
- SC-1.2: Negative samples — empty text yields empty inventory; plain prose
  without operations yields zero source_operation items.
- Span traceability: every EvidenceItem's raw_text[span.start:span.end] contains
  its literal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from akb2.quality.source_ops_scanners import EvidenceItem, Span, scan_all

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "test-fresh-golden-ops.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden_data() -> dict:
    """Load the golden fixture (contains raw_text + expected must_cover items)."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def test_fresh_raw(golden_data: dict) -> str:
    """The test-fresh raw text embedded in the golden fixture."""
    return golden_data["raw_text"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Normalize text for comparison: collapse whitespace, strip edge quotes."""
    return " ".join(text.split()).strip('>"').strip()


def _scan_contains(scan_items: list[EvidenceItem], golden_literal: str) -> bool:
    """True if golden_literal matches any scan item (substring either direction)."""
    norm_golden = _normalize(golden_literal)
    if not norm_golden:
        return False
    for item in scan_items:
        norm_item = _normalize(item.literal)
        if norm_golden in norm_item or norm_item in norm_golden:
            return True
    return False


# ---------------------------------------------------------------------------
# SC-1.1: Positive — scan_all(test-fresh) ⊇ golden (3 categories)
# ---------------------------------------------------------------------------


class TestScanAllCoversGolden:
    """Each golden must_cover item must appear in scan_all output."""

    def test_produces_all_three_categories(self, test_fresh_raw: str) -> None:
        items = scan_all(test_fresh_raw)
        kinds = {i.kind for i in items}
        assert "source_operation" in kinds
        assert "source_boundary" in kinds
        assert "source_conclusion" in kinds

    @pytest.mark.parametrize(
        "kind",
        ["source_operation", "source_boundary", "source_conclusion"],
    )
    def test_golden_operation_subset(
        self, test_fresh_raw: str, golden_data: dict, kind: str
    ) -> None:
        items = scan_all(test_fresh_raw)
        kind_items = [i for i in items if i.kind == kind]
        golden_items = golden_data.get(kind, [])

        missing = []
        for g in golden_items:
            if not _scan_contains(kind_items, g["literal"]):
                missing.append(g["literal"])
        assert not missing, (
            f"{kind}: {len(missing)} golden items not found in scan output.\n"
            f"Missing: {missing}"
        )

    def test_mcp_protocol_commands_found(self, test_fresh_raw: str) -> None:
        """The 6 MCP commands that appear in test-fresh must all be found."""
        items = scan_all(test_fresh_raw)
        ops = {i.literal for i in items if i.kind == "source_operation"}
        expected_mcp = {
            "initialize",
            "tools/list",
            "tools/call",
            "resources/list",
            "resources/read",
            "notifications/initialized",
        }
        missing = expected_mcp - ops
        assert not missing, f"Missing MCP commands: {missing}"

    def test_numbered_steps_found(self, test_fresh_raw: str) -> None:
        """Step 1-4 and 1.-3. numbered steps must be found."""
        items = scan_all(test_fresh_raw)
        step_items = [
            i.literal for i in items if i.kind == "source_operation" and i.source == "numbered_steps"
        ]
        assert len(step_items) >= 7, (
            f"Expected >= 7 numbered steps, got {len(step_items)}: {step_items}"
        )


# ---------------------------------------------------------------------------
# SC-1.2: Negative — empty text and plain prose
# ---------------------------------------------------------------------------


class TestNegativeSamples:
    """Empty text and plain prose must not produce source_operation items."""

    def test_empty_text_yields_empty_inventory(self) -> None:
        assert scan_all("") == []

    def test_plain_prose_no_operations(self) -> None:
        plain = (
            "The quick brown fox jumps over the lazy dog. "
            "This is a plain paragraph without any commands, code, or steps. "
            "It describes a simple scene in nature."
        )
        items = scan_all(plain)
        ops = [i for i in items if i.kind == "source_operation"]
        assert ops == [], (
            f"Plain prose should not produce source_operation items, got: {ops}"
        )

    def test_plain_prose_no_boundaries_or_conclusions(self) -> None:
        plain = (
            "The quick brown fox jumps over the lazy dog. "
            "This is a plain paragraph without any commands, code, or steps."
        )
        items = scan_all(plain)
        assert items == [], (
            f"Plain prose without markers should yield empty inventory, got: {items}"
        )


# ---------------------------------------------------------------------------
# Span traceability — every EvidenceItem's span must contain its literal
# ---------------------------------------------------------------------------


class TestSpanTraceability:
    """raw_text[span.start:span.end] must contain item.literal for every item."""

    def test_all_spans_contain_literal(self, test_fresh_raw: str) -> None:
        items = scan_all(test_fresh_raw)
        failures = []
        for item in items:
            span = item.raw_anchor_span
            slice_text = test_fresh_raw[span.start : span.end]
            if item.literal not in slice_text:
                failures.append(
                    f"literal={item.literal[:60]!r} not in "
                    f"slice={slice_text[:60]!r} (source={item.source})"
                )
        assert not failures, (
            f"{len(failures)} items have span/literal mismatch:\n"
            + "\n".join(failures)
        )

    def test_spans_are_valid_offsets(self, test_fresh_raw: str) -> None:
        items = scan_all(test_fresh_raw)
        n = len(test_fresh_raw)
        for item in items:
            span = item.raw_anchor_span
            assert 0 <= span.start < span.end <= n, (
                f"Invalid span {span} for literal={item.literal[:40]!r} "
                f"(raw length={n})"
            )

    def test_span_property_length(self) -> None:
        span = Span(start=10, end=20)
        assert span.length == 10


# ---------------------------------------------------------------------------
# Module structure sanity checks
# ---------------------------------------------------------------------------


class TestModuleStructure:
    """Verify the module exports and EvidenceItem dataclass shape."""

    def test_scan_all_returns_list_of_evidence_item(self, test_fresh_raw: str) -> None:
        items = scan_all(test_fresh_raw)
        assert isinstance(items, list)
        assert all(isinstance(i, EvidenceItem) for i in items)

    def test_evidence_item_fields(self, test_fresh_raw: str) -> None:
        items = scan_all(test_fresh_raw)
        assert len(items) > 0
        item = items[0]
        assert hasattr(item, "kind")
        assert hasattr(item, "literal")
        assert hasattr(item, "raw_anchor_span")
        assert hasattr(item, "must_cover_class")
        assert hasattr(item, "source")

    def test_kind_values_are_valid(self, test_fresh_raw: str) -> None:
        items = scan_all(test_fresh_raw)
        valid_kinds = {"source_operation", "source_boundary", "source_conclusion"}
        for item in items:
            assert item.kind in valid_kinds, f"Invalid kind: {item.kind}"
            assert item.must_cover_class == item.kind
