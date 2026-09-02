"""阶段 0：冻结 loop2 基线快照结构，禁止 silently 改死因表。"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "reports" / "loop2" / "baseline-2026-09-01.json"

REQUIRED_DEATH_CAUSES = {
    "compose_frontmatter_gt_25": 76,
    "extract_concept_components_or_edges": 74,
    "repair_loop_deleted_blocking": 49,
    "sg_how_density": 35,
    "integrity_floor_procedural_pattern": 36,
    "sg_must_cover": 13,
    "how_tendency": 11,
    "promote_lint": 12,
}


def test_baseline_snapshot_exists_and_shape() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert payload["source"] == "plan_empirical_v1.1"
    assert payload["ledger_path"] == (
        "/Users/praise/Obsidian/Praise Space/AKB2.0/staging/quality-ledger.jsonl"
    )
    assert payload["live_last_ts"] == "2026-08-26T17:43:35Z"
    assert payload["calendar_7d_status"] == "empty"
    assert payload["window_status"] == "empty_calendar_7d"

    assert payload["counts"] == {
        "intake": 387,
        "grader": 106,
        "promote": 0,
        "abort": 395,
    }
    assert payload["death_causes"] == REQUIRED_DEATH_CAUSES
