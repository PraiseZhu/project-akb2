# AKB2.0 Context Handoff - 2026-05-02

## Purpose

This file preserves the current AKB2.0 planning context so future Codex or Claude windows do not lose the agreed architecture, phase boundaries, and next task.

## Canonical Root

AKB2.0 lives in the user's Obsidian default workspace:

`/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0`

Do not relocate AKB2.0 into the Claude Code or Codex workspaces. Claude and Codex are consumers/editors of the knowledge base, not the owning storage location.

Old AKB paths are reference material only:

- `/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB`
- `/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB`

## Project Goal

AKB2.0 is intended to become a Karpathy LLM Wiki-style knowledge base: a three-layer, self-improving, concept-graph-oriented backend for the user's design work and AI projects.

It must support:

- Claude and Codex querying the same Obsidian knowledge base.
- High-quality manually curated knowledge.
- Future re-connection of fetch, note synthesis, translation, NLM reports, daily AI brief, and manual report ingestion.
- Strict intake standards before any content becomes `active`.
- A toolbox area for reusable agent procedures, workflows, prompts, checklists, and tools.

## Layer Model

AKB2.0 has three knowledge layers:

1. Raw material layer: `raw/` plus `staging/`.
2. Finished knowledge layer: `wiki/`.
3. Rules and calling layer: `AKB-2.0-CONTENT-STANDARD.md` plus `docs/`.

`toolbox/` is not a fourth knowledge layer. It is a capability asset area for executable procedures, prompts, workflows, checklists, and tool definitions.

`active` is a status, not a directory layer.

## Phase Boundary

Current phase: Phase A, standards and schema only.

Allowed in Phase A:

- Write and revise standard documents.
- Define contracts, templates, gates, schema fields, lifecycle rules, lint rules, migration policy, and automation boundaries.
- No real migration, no CLI wiring, no LaunchAgent changes, no old AKB code changes.

Not allowed in Phase A:

- Do not reconnect old fetch/synth/translate.
- Do not run NLM automation.
- Do not bulk migrate old AKB content.
- Do not create real active content pages as part of standard-writing tasks.
- Do not delete old AKB content.

## Completed or Reviewed Context

### Intake Gate

`docs/AKB-2.0-INTAKE-GATE.md` has been rewritten and reviewed.

Core decisions:

- All entries must first enter `staging/candidate`.
- No entry point may write directly to active `wiki/` or active `toolbox/`.
- No auto-active, no fast-track.
- Gate is not only a rejector; it is a repair and routing mechanism.
- Gate decisions include at least:
  - `active`
  - `needs_repair`
  - `needs_human_review`
  - `brief_only`
  - `archive`
  - `reject`
- `active` content must include 2-3 `future_questions`.
- Human-approved active content must include `gate_reason`.
- Useful but incomplete content should go to `needs_repair`, not be deleted.

### Source Type Policy

`docs/AKB-2.0-SOURCE-TYPE-POLICY.md` was rewritten and reviewed.

Core decisions:

- `source_type` controls trust posture, retention tendency, and default gate recommendation only.
- `source_type` never grants active status.
- NLM reports, manual reports, design materials, reddit/forum curated items, and old AKB migration all still pass through Intake Gate.
- `design` and `work-note` must not use the old "own source directly active" rule.
- `ingest_channel` should include channels such as automatic fetch, manual report, NLM, query writeback, video research, and old migration as applicable.

## Current Next Task

Next branch/window: `AKB2.0 页面模板`.

Stage: Phase A-3, Page Templates / 页面模板标准.

Target file:

`/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0/docs/AKB-2.0-PAGE-TEMPLATES.md`

This window should only handle page-template-related content. It should not implement naming rules, fetch chains, migration, lint CLI, or real content pages.

## Page Templates Scope

The Page Templates document should define:

- Active content page templates.
- Directory index page templates.
- Non-active page boundaries.
- Field tiers: active-required, repair-required, enhance-optional.
- Naming contract fields only, not the full naming rules.
- Score fields only, not scoring algorithms.

### Active Content Templates

At least these 10 templates should be covered:

1. `wiki/concepts/`: `concept`
2. `wiki/sources/`: `source`
3. `wiki/projects/`: `project`
4. `wiki/designs/`: `design`
5. `wiki/tools/`: `tool-understanding`
6. `toolbox/tools/`: `tool-card`
7. `toolbox/workflows/`: `workflow`
8. `toolbox/prompts/`: `prompt`
9. `toolbox/checklists/`: `checklist`
10. `toolbox/agent-procedures/`: `agent-procedure`

Each template must specify:

- Purpose.
- What it accepts.
- What it rejects.
- Active-required fields.
- Repair-required fields or sections.
- Enhance-optional fields or sections.
- Body structure.
- Required links.
- Claude/Codex usage.
- Common failure modes.

### Directory Index Templates

Index pages are navigation and maintenance surfaces, not content pages. They must not bypass Intake Gate or carry unreviewed content as if it were active.

The Page Templates document should define one shared index-page contract and then per-directory differences.

The index coverage should include:

1. AKB2.0 root: `home-index`
2. `docs/`: `docs-index`
3. `wiki/`: `wiki-index`
4. `wiki/concepts/`: `concepts-index`
5. `wiki/projects/`: `projects-index`
6. `wiki/designs/`: `designs-index`
7. `wiki/tools/`: `tools-index`
8. `wiki/sources/`: `sources-index`
9. `toolbox/`: `toolbox-index`
10. `toolbox/tools/`: `tool-cards-index`
11. `toolbox/workflows/`: `workflows-index`
12. `toolbox/prompts/`: `prompts-index`
13. `toolbox/checklists/`: `checklists-index`
14. `toolbox/agent-procedures/`: `agent-procedures-index`
15. `staging/`: `staging-index`
16. `raw/`: `raw-index`
17. `archive/`: `archive-index`
18. `manifests/`: `manifests-index`
19. `attachments/`: `attachments-index`

Index page fields should include, or explicitly justify omissions:

- `index_type`
- `directory_scope`
- `directory_purpose`
- `accepts`
- `rejects`
- `grouping_rule`
- `default_sort`
- `query_entrypoints`
- `agent_usage`
- `maintenance_rule`
- `last_curated`
- `related_standards`
- optional counts such as `active_count`, `candidate_count`, `needs_repair_count`

### Field Tiers

All templates should distinguish:

1. Active-required: missing means not active.
2. Repair-required: valuable content can enter `needs_repair`.
3. Enhance-optional: improves quality but does not block active.

The operating principle is:

`active` strict, `repair` tolerant, `archive` decisive.

### Naming Contract Boundary

The Page Templates task should not define the full naming standard. That belongs to the `AKB2.0 统一命名方式` branch.

However, templates should reserve naming contract fields:

- `canonical_title`
- `slug`
- `aliases`
- `template_type`
- `directory_scope`
- `file_naming_rule_ref`

`file_naming_rule_ref` points to the future Naming Convention document.

### Score Fields Boundary

Two scoring stages are desirable:

1. `gate_score`: pre-intake score for sorting candidate repair and migration priority.
2. `active_quality_score`: post-intake score for active health, rewrite, demotion, or archival priority.

The Page Templates task should only reserve fields:

- `gate_score`
- `active_quality_score`
- `quality_dimensions`
- `quality_reviewed_at`
- `quality_reviewer`

Do not implement the scoring algorithm in Page Templates.

Scoring belongs across later standards:

- `INTAKE-GATE`: score meaning and non-authority over hard gates.
- `LINT-RULES`: dimensions, checks, and deduction rules.
- `CONTENT-LIFECYCLE`: re-review cadence and demotion rules.
- `MIGRATION-POLICY`: old library migration sorting.

Scores do not issue active permission. A high score cannot bypass hard active requirements.

## Old AKB Quality Scan Result

A read-only scan of old AKB found 1837 Markdown files under:

`/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB`

Using an initial quality heuristic, the top 10 high-potential old entries were evaluated. Result:

- 0/10 can directly become AKB2.0 `active`.
- 10/10 are worth `needs_repair` rather than reject/archive.
- 8/10 look like light repair candidates.
- 2/10 likely need split/restructure before active.

Reason: even the best old entries lack AKB2.0 active-required schema fields such as:

- `source_type`
- `ingest_channel`
- `gate_decision`
- `gate_reason`
- `future_questions`
- `reviewer`
- `reviewed_at`

This supports the current policy: old AKB should be treated as a candidate pool, not automatically migrated as active.

## Current Instruction for Page Templates Window

Use the latest full instruction already prepared in chat. Key points to preserve:

- The Page Templates window only modifies `AKB-2.0-PAGE-TEMPLATES.md`.
- It must not modify other docs unless only listing handoff items.
- It must define the 10 active content templates.
- It must define the 19 index page templates.
- It must define active/repair/enhance field tiers.
- It must reserve naming fields without defining naming rules.
- It must reserve score fields without defining scoring logic.
- It must preserve Intake Gate and Source Type Policy boundaries.

## Recommended Execution Order After Page Templates

After `PAGE-TEMPLATES` is reviewed, continue Phase A in this order:

1. `GRAPH-LINKING`: define graph link rules after page shapes are known.
2. `QUERY-CONTRACT` / `AI-QUERY-CONTRACT`: define how Claude/Codex read and cite AKB2.0.
3. `LINT-RULES`: turn standards into checkable rules, including score dimension definitions.
4. `CONTENT-LIFECYCLE`: define active re-review, stale, deprecated, archive, and demotion rules.
5. `NAMING-CONVENTION`: define full file naming, canonical title, slug, aliases, and rename rules.
6. `MIGRATION-POLICY`: define old AKB candidate scoring, repair, split, archive, and deletion strategy.
7. `FETCH-INGEST-CONTRACT` and automation boundaries: only after gates, templates, graph, query, lint, lifecycle, and naming are stable.

Do not reconnect old ingestion chains before Phase A standards are reviewed and stable.

## Risks to Avoid

- Do not let index pages become a second content system that bypasses Intake Gate.
- Do not let `source_type` grant active status.
- Do not let Page Templates define the full Naming Convention.
- Do not let Page Templates implement scoring algorithms.
- Do not let old AKB content inherit old schema or old active assumptions.
- Do not use old Project AKB code/database as the canonical AKB2.0 source.

