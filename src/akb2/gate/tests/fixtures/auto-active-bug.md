---
title: Auto-Active Bug Fixture
page_status: candidate
gate_decision: active   # ← BUG: 不允许 candidate 预填 active
source_type: research
template_type: source
ingest_channel: manual
evidence_paths:
  - https://example.com/source
future_questions:
  - 具体问题 1?
  - 具体问题 2?
graph_role: evidence
graph_status: healthy
related_concepts: ["[[concept-foo]]"]
---

# Auto-Active Bug Fixture

## TLDR

This fixture has gate_decision: active in candidate frontmatter — should fail HC-09.

## 关键发现

(For source template.)
