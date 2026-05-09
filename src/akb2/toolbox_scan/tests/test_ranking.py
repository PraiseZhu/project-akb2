"""Phase C-2 ranking tests.

Cover: tier safety filter, dedupe stability, output invariants, error
modes, and `exclude_this_round` semantics.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from akb2_toolbox_scan import rank


def _seed_akb(root: Path) -> None:
    (root / "manifests" / "toolbox-ranking").mkdir(parents=True, exist_ok=True)
    (root / "manifests" / "workspace-scan").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "phase-c-reports").mkdir(parents=True, exist_ok=True)


def _write_manifest(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + ("\n" if records else ""),
        encoding="utf-8",
    )
    return path


def _base_record(**overrides) -> dict:
    rec: dict = {
        "manifest_type": "workspace_scan",
        "workspace_path": f"/abs/path/{overrides.get('slug_candidate', 'x')}",
        "relative_path": f"path/{overrides.get('slug_candidate', 'x')}",
        "detected_kind": "codex_skill",
        "candidate_asset_type": "tool",
        "name": overrides.get("name", "x"),
        "slug_candidate": overrides.get("slug_candidate", "x"),
        "extension": ".md",
        "size_bytes": 4096,
        "mtime": "2026-05-04T00:00:00Z",
        "sha256": overrides.get("sha256", f"deadbeef{abs(hash(overrides.get('slug_candidate', 'x'))) % 100000000:08d}"),
        "is_symlink": False,
        "has_akb_backlink": False,
        "phase_b_shadow_match": False,
        "registry_exists": False,
        "interface_exists": False,
        "eval_exists": False,
        "risk_hint": "low",
        "side_effects_hint": "read_only",
        "looks_like_secret_in_header": False,
        "scan_error": None,
        "scan_scope": "global",
        "not_active": True,
        "automation_allowed": False,
        "gate_decision": None,
    }
    rec.update(overrides)
    return rec


def _run_cli(akb: Path, manifests: list[Path], *, pilot_limit: int = 20) -> int:
    argv = [
        "--akb-root", str(akb),
        "--out", "manifests/toolbox-ranking/rank.jsonl",
        "--report", "docs/phase-c-reports/rank.md",
        "--pilot-limit", str(pilot_limit),
    ]
    for m in manifests:
        argv += ["--manifest", str(m)]
    return rank.main(argv)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestSafetyFilter(unittest.TestCase):
    def test_secret_header_routed_to_manual_review(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [_base_record(slug_candidate="leaky", looks_like_secret_in_header=True)],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            self.assertEqual(recs[0]["tier"], "manual_review")
            self.assertNotEqual(recs[0]["tier"], "pilot_next")
            # secret value (none was ever in the source) must not appear in report
            report = (akb / "docs" / "phase-c-reports" / "rank.md").read_text(encoding="utf-8")
            self.assertIn("looks_like_secret_in_header", report)

    def test_scan_error_excluded_this_round(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [_base_record(slug_candidate="broken", scan_error="symlink_not_followed", is_symlink=True, sha256=None)],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            self.assertEqual(recs[0]["tier"], "exclude_this_round")
            self.assertIn("skip", recs[0]["next_action"])

    def test_medium_hook_not_in_pilot(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [_base_record(slug_candidate="hook1", detected_kind="claude_hook",
                              candidate_asset_type="agent_procedure", risk_hint="medium",
                              side_effects_hint="write_local")],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            self.assertEqual(recs[0]["tier"], "manual_review")

    def test_high_risk_routed_to_manual_review(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [_base_record(slug_candidate="risky", risk_hint="high")],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            self.assertEqual(recs[0]["tier"], "manual_review")


class TestPhaseBShadowBoost(unittest.TestCase):
    def test_phase_b_shadow_outranks_plain_skill(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [
                    _base_record(slug_candidate="plain"),
                    _base_record(slug_candidate="shadowed", phase_b_shadow_match=True),
                ],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            ranks = {r["slug_candidate"]: r["rank"] for r in recs}
            self.assertLess(ranks["shadowed"], ranks["plain"])
            shadowed = next(r for r in recs if r["slug_candidate"] == "shadowed")
            self.assertEqual(shadowed["tier"], "pilot_next")
            # Even Phase B shadow must remain not_active.
            self.assertTrue(shadowed["not_active"])
            self.assertFalse(shadowed["automation_allowed"])
            self.assertIsNone(shadowed["gate_decision"])


class TestDedupe(unittest.TestCase):
    def test_same_workspace_path_deduped(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            shared = "/abs/foo/SKILL.md"
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [
                    {**_base_record(slug_candidate="foo"), "workspace_path": shared, "detected_kind": "codex_skill"},
                    {**_base_record(slug_candidate="foo-other"), "workspace_path": shared, "detected_kind": "claude_skill"},
                ],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            preferred = [r for r in recs if r["preferred_record"]]
            secondaries = [r for r in recs if not r["preferred_record"]]
            self.assertEqual(len(preferred), 1)
            self.assertEqual(len(secondaries), 1)
            self.assertEqual(preferred[0]["detected_kind"], "codex_skill")  # higher priority
            self.assertEqual(secondaries[0]["tier"], "exclude_this_round")
            self.assertIn("duplicate", secondaries[0]["next_action"])

    def test_same_slug_across_kinds_grouped(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [
                    _base_record(slug_candidate="cc-foo", detected_kind="codex_skill"),
                    {
                        **_base_record(slug_candidate="cc-foo", detected_kind="claude_script"),
                        "workspace_path": "/abs/other/cc-foo.sh",
                        "sha256": "ffffffff00000000",
                    },
                ],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            groups = {r["duplicate_group_id"] for r in recs if r["duplicate_group_id"]}
            self.assertEqual(len(groups), 1)


class TestPilotLimit(unittest.TestCase):
    def test_pilot_limit_capped(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            many = [_base_record(slug_candidate=f"good-{i}") for i in range(10)]
            manifest = _write_manifest(Path(td) / "m.jsonl", many)
            self.assertEqual(_run_cli(akb, [manifest], pilot_limit=3), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            pilots = [r for r in recs if r["tier"] == "pilot_next"]
            self.assertEqual(len(pilots), 3)
            defers = [r for r in recs if r["tier"] == "defer"]
            self.assertEqual(len(defers), 7)


class TestInvariants(unittest.TestCase):
    def test_every_record_holds_safety_invariants(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            mixed = [
                _base_record(slug_candidate="a"),
                _base_record(slug_candidate="b", phase_b_shadow_match=True),
                _base_record(slug_candidate="h", detected_kind="claude_hook", risk_hint="medium",
                              side_effects_hint="write_local"),
                _base_record(slug_candidate="leaky", looks_like_secret_in_header=True),
                _base_record(slug_candidate="broken", scan_error="symlink_not_followed", is_symlink=True),
            ]
            # Even if input had bogus values claiming active, ranking output must override to not_active.
            mixed[0]["not_active"] = True
            mixed[0]["automation_allowed"] = True  # bogus
            mixed[0]["gate_decision"] = "active"   # bogus
            manifest = _write_manifest(Path(td) / "m.jsonl", mixed)
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            for r in recs:
                self.assertTrue(r["not_active"], r)
                self.assertFalse(r["automation_allowed"], r)
                self.assertIsNone(r["gate_decision"], r)
            # Tier coverage: 4 known tiers may all appear.
            tiers = {r["tier"] for r in recs}
            self.assertTrue(tiers.issubset({"pilot_next", "manual_review", "defer", "exclude_this_round"}))


class TestDeterministicOutput(unittest.TestCase):
    """Round 1 review Finding 1 regression: duplicate_group_id must be stable."""

    def test_group_id_stable_across_runs(self):
        records = [
            {**_base_record(slug_candidate="foo"), "workspace_path": "/abs/foo/SKILL.md", "detected_kind": "codex_skill"},
            {**_base_record(slug_candidate="foo-other"), "workspace_path": "/abs/foo/SKILL.md", "detected_kind": "claude_skill"},
            {**_base_record(slug_candidate="bar"), "workspace_path": "/abs/bar.sh", "detected_kind": "claude_script"},
            {**_base_record(slug_candidate="bar"), "workspace_path": "/abs/other/bar.py", "sha256": "ffaa", "detected_kind": "claude_script"},
        ]
        # Two independent runs of the same dedupe must produce identical group ids.
        first_records = json.loads(json.dumps(records))
        second_records = json.loads(json.dumps(records))
        _, groups_a = rank.dedupe_candidates(first_records)
        _, groups_b = rank.dedupe_candidates(second_records)
        ids_a = sorted(g.group_id for g in groups_a)
        ids_b = sorted(g.group_id for g in groups_b)
        self.assertEqual(ids_a, ids_b)
        # And the per-record annotation must agree too.
        per_a = {r["workspace_path"]: r.get("_duplicate_group_id") for r in first_records}
        per_b = {r["workspace_path"]: r.get("_duplicate_group_id") for r in second_records}
        self.assertEqual(per_a, per_b)


class TestSecretAndDuplicateInteraction(unittest.TestCase):
    """Round 1 review Finding 2 regression: dedupe-secondary must hard-exclude
    even when the record also carries looks_like_secret_in_header."""

    def test_dedupe_secondary_with_secret_routes_to_exclude(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            shared_path = "/abs/foo/SKILL.md"
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [
                    # Preferred dedupe winner (codex_skill > claude_skill priority).
                    {**_base_record(slug_candidate="foo"), "workspace_path": shared_path, "detected_kind": "codex_skill"},
                    # Dedupe loser AND carries the secret flag — must still go to exclude.
                    {
                        **_base_record(slug_candidate="foo-other"),
                        "workspace_path": shared_path,
                        "detected_kind": "claude_skill",
                        "looks_like_secret_in_header": True,
                    },
                ],
            )
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            secondaries = [r for r in recs if not r["preferred_record"]]
            self.assertEqual(len(secondaries), 1)
            self.assertEqual(secondaries[0]["tier"], "exclude_this_round")
            self.assertIn("duplicate", secondaries[0]["next_action"])

    def test_invariant_no_dedupe_loser_outside_exclude(self):
        """Hard guarantee: across the full output, no preferred_record=False
        record may sit anywhere except exclude_this_round."""
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            # Mix several dedupe pairs, some with the secret flag, some clean.
            recs_in: list[dict] = []
            for i, kind_b in enumerate(("claude_skill", "claude_skill", "claude_script")):
                shared = f"/abs/group{i}/SKILL.md"
                recs_in.append({**_base_record(slug_candidate=f"g{i}"), "workspace_path": shared, "detected_kind": "codex_skill"})
                losing = {**_base_record(slug_candidate=f"g{i}-b"), "workspace_path": shared, "detected_kind": kind_b}
                if i == 0:
                    losing["looks_like_secret_in_header"] = True
                if i == 1:
                    losing["risk_hint"] = "high"
                recs_in.append(losing)
            manifest = _write_manifest(Path(td) / "m.jsonl", recs_in)
            self.assertEqual(_run_cli(akb, [manifest]), 0)
            recs = _read_jsonl(akb / "manifests" / "toolbox-ranking" / "rank.jsonl")
            offenders = [
                r for r in recs
                if r.get("preferred_record") is False and r.get("tier") != "exclude_this_round"
            ]
            self.assertEqual(offenders, [], offenders)


class TestErrorModes(unittest.TestCase):
    def test_missing_manifest_argument(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            argv = [
                "--akb-root", str(akb),
                "--out", "manifests/toolbox-ranking/rank.jsonl",
                "--report", "docs/phase-c-reports/rank.md",
            ]
            self.assertEqual(rank.main(argv), 2)

    def test_invalid_json_aborts(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            bad = Path(td) / "bad.jsonl"
            bad.write_text("{not json\n", encoding="utf-8")
            self.assertEqual(_run_cli(akb, [bad]), 2)
            # No half-write
            self.assertFalse((akb / "manifests" / "toolbox-ranking" / "rank.jsonl").exists())

    def test_record_missing_not_active_aborts(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            rec = _base_record(slug_candidate="x")
            rec.pop("not_active")
            manifest = _write_manifest(Path(td) / "m.jsonl", [rec])
            self.assertEqual(_run_cli(akb, [manifest]), 2)

    def test_output_outside_ranking_dir_rejected(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            manifest = _write_manifest(
                Path(td) / "m.jsonl",
                [_base_record(slug_candidate="x")],
            )
            argv = [
                "--akb-root", str(akb),
                "--out", "manifests/toolbox-ranking-evil/rank.jsonl",
                "--report", "docs/phase-c-reports/rank.md",
                "--manifest", str(manifest),
            ]
            self.assertEqual(rank.main(argv), 2)
            self.assertFalse((akb / "manifests" / "toolbox-ranking-evil" / "rank.jsonl").exists())

    def test_empty_manifest_emits_no_candidates_report(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            empty = _write_manifest(Path(td) / "m.jsonl", [])
            self.assertEqual(_run_cli(akb, [empty]), 0)
            report = (akb / "docs" / "phase-c-reports" / "rank.md").read_text(encoding="utf-8")
            self.assertIn("no_candidates", report)
            jsonl = (akb / "manifests" / "toolbox-ranking" / "rank.jsonl").read_text(encoding="utf-8")
            self.assertEqual(jsonl, "")


if __name__ == "__main__":
    unittest.main()
