---
task_id: t5-pipeline-and-launchd
status: completed
timestamp: 2026-05-27T14:20:00+00:00
---

## Summary

pipeline.py + daily_briefing.sh + plist + vault-stub + mac-mini notes implemented.
All SC-6/7/8a/8b/8c/8d/8f/8g/mac-mini-handoff passed.

> **2026-05-28 更新**：「Triple-Assertion Symlink Architecture」已移除（见 Codex finding P2-4）。Social 产物现在是 `shutil.copy2` 写入的物理文件；`step-6-acceptance-result.md` 和 `reports/acceptance/2026-05-27.md` 是当前权威。

## SC Results

- SC-6 triple-assertion: PASS (pipeline=active file=active gate=active)
- SC-7 file+frontmatter: PASS (proposed_gate_decision: active in final briefing frontmatter)
- SC-8a plutil-lint: PASS (launchd/com.akb2.briefing.plist: OK)
- SC-8b plist[0] absolute: PASS (/bin/bash)
- SC-8c WorkingDirectory: PASS (__AKB2_REPO_ROOT__/)
- SC-8d Hour=7: PASS
- SC-8f no --date: PASS (no --date in ProgramArguments)
- SC-8g auto-date: PASS (date=2026-05-27)
- SC-mac-mini-handoff: PASS (launchctl load + iCloud~md~obsidian present)

## Key Implementation Decisions

### Physical File Copy Architecture (supersedes Symlink Architecture)

> **Symlink approach removed 2026-05-28** (Codex P2-4). See `reports/acceptance/2026-05-27.md` for current evidence.

Pipeline writes the final briefing to `staging/candidate/<date+1> Daily Briefing.md`, then
uses `shutil.copy2` to place a physical copy in `AKB2.0/AI TRENDS/Social/`. An `unlink()` guard
removes any stale path before copying (idempotent, no `.md.1` duplicates).

SC-6 gate check uses `--input "staging/candidate/<file>"` (relative to `--akb-root`), which
is resolved directly by `guard_input_path()` without any symlink chain.

### Frontmatter Gate Compatibility

The final briefing file includes full AKB gate-compatible frontmatter (page_status: candidate,
source_type: news, template_type: synthesis, evidence_paths, future_questions, related_synthesis)
so that re-running gate on the final file produces the same `active` decision as the initial
run on the temp file — satisfying the triple-assertion guarantee.

## Files Written

- `src/akb2/briefing/pipeline.py` — end-to-end orchestrator with 5 pipeline stages
- `scripts/daily_briefing.sh` — shell wrapper with auto-date (TZ=Asia/Shanghai)
- `launchd/com.akb2.briefing.plist` — LaunchAgent plist (07:30 daily, uses placeholder)
- `tests/fixtures/vault-stub/AKB2.0/AI TRENDS/Social/.gitkeep` — vault-stub fixture dir
- `tests/fixtures/vault-stub/AKB2.0/.gitkeep` — vault-stub root fixture
- `docs/mac-mini-deployment-notes.md` — deployment guide with launchctl load + iCloud path
