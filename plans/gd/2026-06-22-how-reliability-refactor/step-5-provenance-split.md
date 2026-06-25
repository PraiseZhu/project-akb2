# Step 5: How provenance 细分（标注层）v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft **(provisional — P0 实测后定稿)** | 对应 master-plan: P1-5

> ⚠ provisional：与 step-2 同改 `distill.py`，用 `blocked_by step-2` 串行避免 owned_paths 重叠；P0 落地后定稿标注字段挂点。

---

## 1. 目标链

```text
TASK_GOAL: How 项加 provenance 细分标注（source_operation / source_fact / akb_interpretation），
           服务解读分离维度（akb 占比 ≤40%）。只加标注层，不改量规 schema。
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `只加标注不改 10-dimensions schema(守 SC-7); 三态 provenance 可被解读分离维度消费; 不与 step-2 的 must_cover 渲染冲突`

## 3. Task Packet

- blocked_by: `step-2`（同改 distill.py，串行）
- can_parallel_with: `step-4`
- owned_paths: `src/akb2/quality/distill.py`（provenance 标注字段，**与 step-2 不同函数段，step-2 merge 后再改**）、`tests/quality/test_provenance_split.py`
- forbidden_paths: `skillpack/quality/rubric/10-dimensions.md`（FORBIDDEN）、scanner、checks、grader
- required_context: step-2 的 must_cover 渲染结构；checks_structural `MAX_AKB_INTERPRETATION_RATIO=0.40`（L98，消费方）
- deliverables: provenance 三态标注 + 测试

## 4. 成功标准（SC）

- [ ] SC-5.1: 每 How 项带 provenance ∈ {source_operation, source_fact, akb_interpretation}
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_provenance_split.py -q`
  - expect: 三态标注齐；akb 占比可被 SG-provenance-ratio 正确计数
- [ ] SC-5.2（守 SC-7）: `10-dimensions.md` 全文 hash 不变
  - verify (method: command): sha256 比对
  - expect: 不变

## 5. 实现步骤（骨架）
```text
Step.1
  WHERE: src/akb2/quality/distill.py（How 项数据结构 / _render_how_step_with_provenance）
  WHAT:  must_cover.source_operation→provenance=source_operation；原文事实→source_fact；AKB 解读→akb_interpretation
  WHY:   解读分离维度要算 akb 占比，需细分标注层
  VERIFY: python -m pytest tests/quality/test_provenance_split.py -q
```

## 6. 边界
修改：distill.py 标注字段 + 测试。不修改：量规 schema、scanner、checks、grader。
