"""Unit + small integration tests for the read-only workspace scanner."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from akb2_toolbox_scan.__main__ import main


def _seed_akb(root: Path) -> None:
    (root / "manifests" / "workspace-scan").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "phase-c-reports").mkdir(parents=True, exist_ok=True)
    (root / "toolbox" / "registry").mkdir(parents=True, exist_ok=True)
    (root / "toolbox" / "interfaces").mkdir(parents=True, exist_ok=True)
    (root / "toolbox" / "evals").mkdir(parents=True, exist_ok=True)
    (root / "staging" / "candidate" / "toolbox-phase-b-2026-05-04").mkdir(parents=True, exist_ok=True)


def _seed_codex_skill(root: Path, slug: str, *, body: str = "name: x\ndescription: y\n") -> Path:
    d = root / slug
    d.mkdir(parents=True)
    f = d / "SKILL.md"
    f.write_text(f"---\n{body}---\n# body\n", encoding="utf-8")
    return f


def _seed_claude_script(root: Path, name: str, *, body: str = "echo hello\n") -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"#!/bin/bash\n{body}", encoding="utf-8")
    return p


def _seed_claude_hook(root: Path, name: str) -> Path:
    p = root / "hooks" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("module.exports = () => {};\n", encoding="utf-8")
    return p


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run(akb_root: Path, *, roots: list[Path], out: str, report: str, vendor: list[Path] | None = None) -> int:
    argv = ["--akb-root", str(akb_root), "--out", out, "--report", report]
    for r in roots:
        argv += ["--root", str(r)]
    for r in (vendor or []):
        argv += ["--vendor-root", str(r)]
    return main(argv)


class TestDetection(unittest.TestCase):
    def test_codex_skill_detected(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            _seed_codex_skill(codex, "cc-foo")
            rc = _run(
                akb,
                roots=[codex],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["detected_kind"], "codex_skill")
            self.assertEqual(recs[0]["candidate_asset_type"], "tool")
            self.assertEqual(recs[0]["slug_candidate"], "cc-foo")
            self.assertEqual(recs[0]["not_active"], True)
            self.assertEqual(recs[0]["automation_allowed"], False)
            self.assertIsNone(recs[0]["gate_decision"])

    def test_claude_skill_detected(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            claude_skills = Path(td) / ".claude" / "skills"
            claude_skills.mkdir(parents=True)
            _seed_codex_skill(claude_skills, "my-skill")
            rc = _run(
                akb,
                roots=[claude_skills],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertEqual(recs[0]["detected_kind"], "claude_skill")

    def test_claude_script_detected(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            scripts = Path(td) / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            _seed_claude_script(scripts, "foo.sh")
            rc = _run(
                akb,
                roots=[scripts],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["detected_kind"], "claude_script")

    def test_hook_not_double_counted(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            scripts = Path(td) / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            hooks = scripts / "hooks"
            _seed_claude_script(scripts, "review.sh")
            _seed_claude_hook(scripts, "h1.js")
            rc = _run(
                akb,
                roots=[scripts, hooks],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            kinds = sorted(r["detected_kind"] for r in recs)
            self.assertEqual(kinds, ["claude_hook", "claude_script"])

    def test_empty_root_recorded(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            empty = Path(td) / ".claude" / "skills"
            empty.mkdir(parents=True)
            rc = _run(
                akb,
                roots=[empty],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            text = (akb / "docs" / "phase-c-reports" / "x.md").read_text(encoding="utf-8")
            self.assertIn("empty", text)

    def test_vendor_default_skipped(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            user = Path(td) / "user-skills"
            user.mkdir()
            _seed_codex_skill(user, "user-foo")
            vendor = Path(td) / ".codex" / "plugins" / "cache"
            vendor.mkdir(parents=True)
            _seed_codex_skill(vendor, "vendor-foo")
            # without --vendor-root, vendor must NOT appear
            rc = _run(
                akb,
                roots=[user],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            slugs = {r["slug_candidate"] for r in recs}
            self.assertIn("user-foo", slugs)
            self.assertNotIn("vendor-foo", slugs)

    def test_symlink_recorded_not_followed(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            real_dir = Path(td) / "real" / "skills"
            real_dir.mkdir(parents=True)
            _seed_codex_skill(real_dir, "real-skill")
            scan_root = Path(td) / "scan"
            scan_root.mkdir()
            (scan_root / "linked").symlink_to(real_dir, target_is_directory=True)
            rc = _run(
                akb,
                roots=[scan_root],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            symlinks = [r for r in recs if r["is_symlink"]]
            self.assertEqual(len(symlinks), 1)
            self.assertEqual(symlinks[0]["sha256"], None)
            self.assertEqual(symlinks[0]["scan_error"], "symlink_not_followed")
            # The real SKILL.md inside the symlinked dir must NOT appear.
            self.assertEqual(len(recs), 1)


class TestPathBoundaries(unittest.TestCase):
    def test_out_outside_manifests_workspace_scan(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            _seed_codex_skill(codex, "x")
            rc = _run(
                akb,
                roots=[codex],
                out="manifests/workspace-scan-evil/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 2)
            self.assertFalse((akb / "manifests" / "workspace-scan-evil" / "x.jsonl").exists())

    def test_report_outside_phase_c_reports(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            _seed_codex_skill(codex, "x")
            rc = _run(
                akb,
                roots=[codex],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports-evil/x.md",
            )
            self.assertEqual(rc, 2)
            self.assertFalse((akb / "manifests" / "workspace-scan" / "x.jsonl").exists())
            self.assertFalse((akb / "docs" / "phase-c-reports-evil" / "x.md").exists())

    def test_path_traversal_rejected(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            _seed_codex_skill(codex, "x")
            rc = _run(
                akb,
                roots=[codex],
                out="manifests/workspace-scan/../../x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 2)
            self.assertFalse((akb / "x.jsonl").exists())

    def test_no_orphan_when_report_dir_unwritable(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            _seed_codex_skill(codex, "x")
            report_parent = akb / "docs" / "phase-c-reports"
            # Make the report parent dir unwritable to force the report write
            # to fail AFTER the JSONL temp/rename succeeded.
            report_parent.chmod(0o500)
            try:
                rc = _run(
                    akb,
                    roots=[codex],
                    out="manifests/workspace-scan/x.jsonl",
                    report="docs/phase-c-reports/x.md",
                )
                self.assertEqual(rc, 3)
                self.assertFalse((akb / "manifests" / "workspace-scan" / "x.jsonl").exists())
            finally:
                report_parent.chmod(0o700)


class TestSafetyContracts(unittest.TestCase):
    def test_records_uniformly_not_active(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            _seed_codex_skill(codex, "alpha")
            _seed_codex_skill(codex, "beta")
            rc = _run(
                akb,
                roots=[codex],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertGreaterEqual(len(recs), 2)
            for r in recs:
                self.assertTrue(r["not_active"])
                self.assertFalse(r["automation_allowed"])
                self.assertIsNone(r["gate_decision"])
                self.assertEqual(r["manifest_type"], "workspace_scan")

    def test_secret_body_not_in_report(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            secret_blob = "OPENAI_API_KEY=sk-1234567890abcdef\n"
            _seed_codex_skill(codex, "leaky", body=f"name: leaky\ndescription: x\n{secret_blob}")
            rc = _run(
                akb,
                roots=[codex],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            report = (akb / "docs" / "phase-c-reports" / "x.md").read_text(encoding="utf-8")
            self.assertNotIn("sk-1234567890abcdef", report)
            self.assertNotIn("OPENAI_API_KEY", report)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertTrue(recs[0]["looks_like_secret_in_header"])

    def test_phase_b_shadow_match(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            (akb / "staging" / "candidate" / "toolbox-phase-b-2026-05-04" / "cc-review").mkdir(parents=True)
            codex = Path(td) / ".codex" / "skills"
            codex.mkdir(parents=True)
            _seed_codex_skill(codex, "cc-review")
            _seed_codex_skill(codex, "cc-other")
            rc = _run(
                akb,
                roots=[codex],
                out="manifests/workspace-scan/x.jsonl",
                report="docs/phase-c-reports/x.md",
            )
            self.assertEqual(rc, 0)
            recs = {r["slug_candidate"]: r for r in _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")}
            self.assertTrue(recs["cc-review"]["phase_b_shadow_match"])
            self.assertFalse(recs["cc-other"]["phase_b_shadow_match"])


if __name__ == "__main__":
    unittest.main()
