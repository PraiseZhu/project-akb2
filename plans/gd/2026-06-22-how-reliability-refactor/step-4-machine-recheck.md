# Step 4: 机器回查（命令字面量回溯 raw）v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft **(provisional — P0 实测后定稿)** | 对应 master-plan: P1-4

> ⚠ provisional：依赖 step-1 的 `raw_anchor_span` 实际形态；P0 跑通后定稿正则/匹配细节。

---

## 1. 目标链

```text
TASK_GOAL: 进正文的命令字面量必须能精确回查回 raw（用 step-1 raw_anchor_span）；编造的、原文没有的命令被挡。
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `命令精确匹配回查 raw(治"编"); anchor span 可追溯; 不与 SG-must-cover(治"漏") 职责重叠`

## 3. Task Packet

- blocked_by: `step-1`（用 raw_anchor_span）、`step-3`（生产接入点在 checks_structural SG，串行避免同文件冲突）；建议 `step-2` 先落
- can_parallel_with: `step-5`
- owned_paths: `src/akb2/quality/source_ops_scanners/raw_verify.py`（新文件，回查库函数）、**`checks_structural.py` 的新 SG-command-recheck 块（唯一生产接入点，Codex r4 问题1）**、`tests/quality/test_raw_verify.py`
  - **owned 协调**：step-1 owned `source_ops_scanners/**`→本 step 在 step-1 merge 后加 `raw_verify.py`；step-3 owned checks_structural 的 SG-must-cover 块→本 step 在 step-3 merge 后加 SG-command-recheck 块（同文件不同块，串行）
- forbidden_paths: step-1 已有 scanner 文件、step-3 的 SG-must-cover 块、distill、grader
- required_context: step-1 的 EvidenceItem.raw_anchor_span；step-3 的 SG 结构；渲染后候选正文样例
- deliverables: 命令回查库函数 + checks_structural SG-command-recheck 生产接入 + 库测试 + 生产路径测试

## 4. 成功标准（SC = master-plan SC-4）

- [ ] SC-4.1（库函数）: 候选含"原文没有的命令" → 回查 fail；含"原文有的命令" → pass 且可定位 raw 行
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_raw_verify.py -q`
  - expect: 编造命令被挡，真实命令可追溯到 raw span
- [ ] SC-4.2（生产 fail-closed，Codex r4 问题1）: 注入"原文不存在命令"的候选 → `run_candidate_self_gate` 返回 SG-command-recheck Defect（真实链路拒绝，非仅库函数）
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_checks_structural.py -k command_recheck -q`
  - expect: 生产路径产 fail-closed Defect

## 5. 实现步骤（骨架，P0 后定稿）

```text
Step.1
  WHERE: src/akb2/quality/source_ops_scanners/raw_verify.py（新建）
  WHAT:  verify_commands_in_raw(candidate_body, raw_text) → 对正文每个反引号命令，要求在 raw 精确出现（字面量匹配），否则记 violation + 期望 anchor
  WHY:   治"编"——SG-must-cover 治漏，本步治正文出现原文没有的命令
  VERIFY: python -m pytest tests/quality/test_raw_verify.py -q

Step.2
  WHERE: src/akb2/quality/checks_structural.py（step-3 SG-must-cover 块之后，新增 SG-command-recheck）
  WHAT:  调 raw_verify.verify_commands_in_raw(body, raw_content)；violation 非空 → append Defect(dimension="SG-command-recheck")
  WHY:   编造命令必须挡在出库前，且接入点须在 owned 内（Codex r4 问题1：原 owned 不含生产接入点会 SC-4 假通过）
  VERIFY: python -m pytest tests/quality/test_checks_structural.py -k command_recheck -q
```

## 6. 边界
修改：`raw_verify.py`（库函数）+ `checks_structural.py` 的 SG-command-recheck 块（生产接入）+ 测试。
不修改：step-1 已有 scanner、step-3 的 SG-must-cover 块、distill 抽取、grader。
