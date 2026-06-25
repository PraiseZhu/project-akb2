# Step 8: How 回归测试三类语料 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft **(provisional — 依赖 step-1~7 接口定稿)** | 对应 master-plan: P2-8

> ⚠ provisional：三类语料的断言锚点依赖 step-1~7 的实际接口；其中两类语料需先创建（见 Hard-stop）。

---

## 1. 目标链

```text
TASK_GOAL: How 回归网三类语料防退化：test-fresh(有操作→捞全) + 纯观点博客(无 How→fail-closed)
           + 陷阱样本(枚举带偏→抽核心不被带偏)，落 tests/quality/fixtures/regression/。
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `三类语料各验一种失败模式; 纯观点博客验 fail-closed(不硬造 How); 陷阱样本验抽核心; 语料缺失 not_run 不蒙混 pass`

## 3. Task Packet

- blocked_by: `step-1, step-2, step-3, step-4, step-5, step-6`（全部 P0+P1；P2 依赖 P0/P1——Codex r4 问题5）；step-7 若已落一并覆盖
- can_parallel_with: `—`
- owned_paths: `tests/quality/fixtures/regression/**`、`tests/quality/test_how_regression.py`
- forbidden_paths: 所有 src 代码（本步只加测试与语料，不改实现）
- required_context: step-1 golden 形态、step-2 渲染产出、step-3 SG-must-cover、step-6 degraded 行为
- deliverables: 三类语料 fixture + 回归测试
- Hard-stop: "纯观点博客""陷阱样本"两类语料未创建 → SC 标 not_run，**不得标 pass**

## 4. 成功标准（SC = master-plan SC-6）

- [ ] SC-8.1（语料就位）: 三类语料文件存在于 `tests/quality/fixtures/regression/`
  - verify (method: path): `ls tests/quality/fixtures/regression/`
  - expect: test-fresh + 纯观点博客 + 陷阱样本三个文件
- [ ] SC-8.2（三类行为，须 step-1~6 完成）: 有操作→捞全 / 无 How→fail-closed / 枚举带偏→抽核心
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_how_regression.py -q`
  - expect: 三类各自断言通过；**step-1~6（全 P0+P1）未完成前只能 partial/not_run，不得标 pass**（语料准备可先做，Codex r4 问题5）

## 5. 实现步骤（骨架）
```text
Step.1
  WHERE: tests/quality/fixtures/regression/（新建三类语料）
  WHAT:  test-fresh 软链/复制；纯观点博客（raw/ 取无 How 观点文 或 人工构造）；陷阱样本（人工构造：大量次要操作 + 核心藏结论）
  WHY:   master-plan SC-6 需三类语料就位
  VERIFY: ls tests/quality/fixtures/regression/

Step.2
  WHERE: tests/quality/test_how_regression.py（新建）
  WHAT:  三类各一组断言（捞全 / fail-closed / 抽核心）
  WHY:   防 How 可靠性退化无人发现
  VERIFY: python -m pytest tests/quality/test_how_regression.py -q
```

## 6. 边界
修改：`tests/quality/fixtures/regression/` + `tests/quality/test_how_regression.py`。不修改：任何 src 实现。
