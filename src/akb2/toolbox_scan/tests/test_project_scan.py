"""Phase C-1b project-scope unit tests.

Cover: classification per allowed dir, exclusion list, executable-bit gate
for bin/, no-exec safety sentinel, symlink recording, --previous-manifest
duplicate marking, --max-files-per-root truncation, output path enforcement
parity with C-1.
"""

from __future__ import annotations

import json
import os
import stat
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
    (root / "staging" / "candidate" / "toolbox-phase-b-2026-05-04").mkdir(
        parents=True,
        exist_ok=True,
    )


def _write(path: Path, body: str = "echo hi\n", *, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    if executable:
        os.chmod(path, path.stat().st_mode | stat.S_IXUSR)
    return path


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run_project(
    akb: Path,
    *,
    project_roots: list[Path],
    out: str = "manifests/workspace-scan/x.jsonl",
    report: str = "docs/phase-c-reports/x.md",
    previous_manifest: Path | None = None,
    max_files: int | None = None,
) -> int:
    argv = [
        "--akb-root", str(akb),
        "--scan-scope", "project_scripts_bin",
        "--out", out,
        "--report", report,
    ]
    for r in project_roots:
        argv += ["--project-root", str(r)]
    if previous_manifest is not None:
        argv += ["--previous-manifest", str(previous_manifest)]
    if max_files is not None:
        argv += ["--max-files-per-root", str(max_files)]
    return main(argv)


class TestProjectClassification(unittest.TestCase):
    def test_each_allowed_dir_kind(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "myproj"
            _write(proj / "scripts" / "build.sh")
            _write(proj / "tools" / "clean.py", body="print('hi')\n")
            _write(proj / "cli" / "run.ts", body="// stub\n")
            _write(proj / "bin" / "foo", body="#!/bin/sh\necho hi\n", executable=True)
            # negative cases
            _write(proj / "commands" / "foo.py", body="print('hi')\n")          # not collected
            _write(
                proj / "scripts" / "commands" / "nested.py",
                body="print()\n",
            )  # collected (nested)

            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            kinds_by_rel = {r["relative_path"]: r["detected_kind"] for r in recs}

            self.assertEqual(kinds_by_rel.get("scripts/build.sh"), "project_script")
            self.assertEqual(kinds_by_rel.get("tools/clean.py"), "project_tool_script")
            self.assertEqual(kinds_by_rel.get("cli/run.ts"), "project_cli")
            self.assertEqual(kinds_by_rel.get("bin/foo"), "project_bin")
            self.assertEqual(kinds_by_rel.get("scripts/commands/nested.py"), "project_script")
            self.assertNotIn("commands/foo.py", kinds_by_rel)

            for rec in recs:
                self.assertEqual(rec["scan_scope"], "project_scripts_bin")
                self.assertEqual(rec["project_name"], "myproj")
                self.assertTrue(rec["not_active"])
                self.assertFalse(rec["automation_allowed"])
                self.assertIsNone(rec["gate_decision"])

    def test_bin_without_exec_bit_or_extension_skipped(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            _write(proj / "bin" / "data.txt", body="not a script\n")
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertEqual(recs, [])


class TestExclusionList(unittest.TestCase):
    def test_node_modules_pruned(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            _write(proj / "node_modules" / ".bin" / "foo", body="bin\n", executable=True)
            _write(proj / "node_modules" / "pkg" / "scripts" / "x.sh")
            _write(proj / "scripts" / "real.sh")
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            paths = [
                r["relative_path"]
                for r in _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            ]
            self.assertEqual(paths, ["scripts/real.sh"])

    def test_dot_dirs_pruned(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            _write(proj / ".venv" / "bin" / "python", executable=True)
            _write(proj / ".git" / "hooks" / "pre-commit", body="#!/bin/sh\n", executable=True)
            _write(proj / ".cache" / "scripts" / "x.sh")
            _write(proj / "scripts" / "ok.sh")
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            paths = [
                r["relative_path"]
                for r in _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            ]
            self.assertEqual(paths, ["scripts/ok.sh"])

    def test_dist_and_build_pruned(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            _write(proj / "dist" / "scripts" / "generated.sh")
            _write(proj / "build" / "tools" / "x.py", body="print()\n")
            _write(proj / "scripts" / "real.sh")
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            paths = sorted(
                r["relative_path"]
                for r in _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            )
            self.assertEqual(paths, ["scripts/real.sh"])


class TestSafety(unittest.TestCase):
    def test_no_execution_during_scan(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            sentinel = Path(td) / "SHOULD_NOT_EXIST"
            # If anyone executed this script the sentinel would appear.
            _write(
                proj / "scripts" / "trojan.sh",
                body=f"#!/bin/sh\ntouch '{sentinel}'\n",
                executable=True,
            )
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            self.assertFalse(sentinel.exists())

    def test_secret_in_header_not_in_report(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            _write(
                proj / "scripts" / "leaky.sh",
                body="#!/bin/sh\nexport OPENAI_API_KEY=sk-1234567890abcdef\n",
            )
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            report = (akb / "docs" / "phase-c-reports" / "x.md").read_text(encoding="utf-8")
            self.assertNotIn("sk-1234567890abcdef", report)
            self.assertNotIn("OPENAI_API_KEY", report)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertTrue(recs[0]["looks_like_secret_in_header"])

    def test_symlink_recorded_not_followed(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            real_outside = Path(td) / "outside" / "stuff"
            _write(real_outside / "do_not_collect.sh")
            proj = Path(td) / "p"
            (proj / "scripts").mkdir(parents=True)
            (proj / "scripts" / "linked").symlink_to(real_outside, target_is_directory=True)
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            symlinks = [r for r in recs if r["is_symlink"]]
            self.assertEqual(len(symlinks), 1)
            self.assertEqual(symlinks[0]["scan_error"], "symlink_not_followed")
            # The real do_not_collect.sh inside the symlink target must NOT appear.
            self.assertEqual(len(recs), 1)


class TestPreviousManifestDedup(unittest.TestCase):
    def test_duplicate_marker(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            shared_path = _write(proj / "scripts" / "x.sh").resolve()
            _write(proj / "scripts" / "y.sh")
            # Build a fake C-1 manifest with shared_path already present.
            prev = Path(td) / "prev.jsonl"
            prev.write_text(
                json.dumps({"workspace_path": str(shared_path)}) + "\n",
                encoding="utf-8",
            )
            rc = _run_project(akb, project_roots=[proj], previous_manifest=prev)
            self.assertEqual(rc, 0)
            recs = {
                r["relative_path"]: r
                for r in _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            }
            self.assertTrue(recs["scripts/x.sh"]["duplicate_of_prior_scan"])
            self.assertFalse(recs["scripts/y.sh"]["duplicate_of_prior_scan"])
            self.assertEqual(recs["scripts/x.sh"]["prior_manifest_path"], str(prev.resolve()))

    def test_no_previous_still_runs(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            _write(proj / "scripts" / "x.sh")
            rc = _run_project(akb, project_roots=[proj])
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertEqual(recs[0]["duplicate_of_prior_scan"], False)
            self.assertIsNone(recs[0]["prior_manifest_path"])


class TestTruncation(unittest.TestCase):
    def test_truncated_at_max_files(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            proj = Path(td) / "p"
            for i in range(5):
                _write(proj / "scripts" / f"x{i}.sh")
            rc = _run_project(akb, project_roots=[proj], max_files=2)
            self.assertEqual(rc, 0)
            recs = _read_jsonl(akb / "manifests" / "workspace-scan" / "x.jsonl")
            self.assertEqual(len(recs), 2)
            report = (akb / "docs" / "phase-c-reports" / "x.md").read_text(encoding="utf-8")
            self.assertIn("truncated", report)


class TestCLIRequirements(unittest.TestCase):
    def test_project_scope_requires_project_root(self):
        with TemporaryDirectory() as td:
            akb = Path(td) / "akb"
            _seed_akb(akb)
            argv = [
                "--akb-root", str(akb),
                "--scan-scope", "project_scripts_bin",
                "--out", "manifests/workspace-scan/x.jsonl",
                "--report", "docs/phase-c-reports/x.md",
                "--root", str(akb),  # any root; should still fail because no project-root
            ]
            self.assertEqual(main(argv), 2)


if __name__ == "__main__":
    unittest.main()
