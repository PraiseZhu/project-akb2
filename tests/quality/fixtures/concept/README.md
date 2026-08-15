# concept 车道 E2E fixtures

本目录存 step-3 SC-3g E2E 验收用的 concept 车道 fixture（正样本 + 负样本 + 对抗样本）。

- `positive.md` — concept 正样本（0 命令、命名机制组件≥3 + 因果边、全溯源、entailment=true）→ 质量段通过。
- `mechanism-mush.md` — 机制糊清单负样本（3 组件但 0 causal_edges）→ F4 地板拒。
- `adversarial-floor-edge.md` — 对抗样本（F4 fail + 辅助维满分）→ 地板 early-return 拒、加权分不算。

E2E 测试（`test_concept_judgment_e2e.py`）用注入式 fixture LLM 跑 judgment chain
（integrity_floor → grader entailment → weighted_score → runner early-return），
不依赖真网关/抓取（spec Assumptions）。
