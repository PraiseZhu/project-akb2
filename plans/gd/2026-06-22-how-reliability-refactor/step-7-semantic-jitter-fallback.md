# Step 7: 语义型 How 抖动兜底 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft **(provisional — P0/P1 实测后定稿)** | 对应 master-plan: P2-7

> ⚠ provisional：确定性 How 由 P0 代码兜住后，本步只针对"语义型 How"（隐含流程/决策/取舍）；
> 与 step-2/step-5 同改 distill.py，用 `blocked_by step-5` 串行链（step-2→5→7）避免重叠。
> 是否需要、兜底强度，待 P0 实测语义型 How 抖动率后定稿。

---

## 1. 目标链

```text
TASK_GOAL: 语义型 How（非确定性、靠 LLM 的隐含流程）抖动兜底：关键段多取（同段产 2 次取交集）；
           semantic segment 连续不稳定/产出不一致 → fail-closed。（不碰 must_cover——那是 P0 确定性范畴）
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `仅针对语义型 How(确定性 How 已由 P0 兜); 多取有界(预算显式); 连续漏 fail-closed 不无限重试`

## 3. Task Packet

- blocked_by: `step-5`（distill.py 串行链 step-2→5→7）
- can_parallel_with: `—`
- owned_paths: `src/akb2/quality/distill.py`（语义 segment 多取逻辑，step-5 merge 后）、`tests/quality/test_semantic_jitter.py`
- forbidden_paths: scanner、checks、grader、step-2/step-5 的 distill 段
- required_context: P0/P1 实测的语义型 How 抖动数据（决定是否做、做多强）
- deliverables: 多取兜底（有界）+ 连续漏 fail-closed + 测试

## 4. 成功标准（SC）

- [ ] SC-7.1: 同段语义 segment 产 2 次取交集；预算用具名常量（如 `SEMANTIC_RETAKE_MAX=2`）
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_semantic_jitter.py -q`
  - expect: 多取有界、交集逻辑正确
- [ ] SC-7.2: semantic segment 连续不稳定（多取交集为空/连续不一致）达阈值 → fail-closed（不无限重试）
  - verify (method: test): 构造连续不稳定，断言 fail-closed
  - expect: 有界停止
- [ ] SC-7.3（边界护栏）: 完成时 must_cover kind 仍只总计划三类；`DISTILL_MODEL_FAMILY`/`PRIMARY_GRADER_MODEL` 两常量未变
  - verify (method: assertion): 断言 must_cover kind 集合不变 + 两模型常量值不变
  - expect: 不新增 kind、不碰模型常量

## 5. 实现步骤（骨架，P0/P1 实测后定稿）
```text
Step.1
  WHERE: src/akb2/quality/distill.py（语义 segment 产出处）
  WHAT:  关键语义段产 2 次取交集；SEMANTIC_RETAKE_MAX 具名常量
  WHY:   语义型 How 单次 LLM 可能抖，产出不稳定复现
  VERIFY: python -m pytest tests/quality/test_semantic_jitter.py -q
```

## 6. 边界
修改：distill.py 语义 segment 多取逻辑 + 测试。
不修改：确定性 scanner、SG-must-cover、**must_cover kind（不新增）**、量规、grader、**`DISTILL_MODEL_FAMILY` / `PRIMARY_GRADER_MODEL` 模型常量（押后单元 owned，本步不碰）**。
