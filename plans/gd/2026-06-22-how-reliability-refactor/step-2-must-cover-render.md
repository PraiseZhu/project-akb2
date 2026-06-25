# Step 2: must_cover 代码直接渲染进正文 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft | 对应 master-plan: P0-2

---

## 1. 目标链

```text
TASK_GOAL: compose 阶段调 scanner 产 must_cover，传入 _build_body；正文 How/适用边界/TLDR 节由代码
           直接渲染 must_cover 三类，LLM 无最终选择权（删掉 LLM segment 后 must_cover 仍在）。
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `must_cover 代码直出(非 LLM 选择); operation→How / boundary→适用边界 / conclusion→TLDR; 不破坏现有 10 节结构与锚点契约`

## 3. Task Packet

- blocked_by: `step-1`（需要 `scan_all` + EvidenceItem）
- can_parallel_with: `step-3`（改 checks_structural，与 distill 不重叠）
- owned_paths: `src/akb2/quality/distill.py`（`compose_candidate` 挂 must_cover + `_build_body` 渲染段）、`tests/quality/test_distill_must_cover.py`（新建）
- forbidden_paths: `source_ops_scanners/**`（step-1，只 import）、`checks_structural.py`（step-3）、`grader.py`、`distill.py` 的 `extract_claims`/抽取 schema（押后单元 owned）
- required_context: `source_ops_scanners.scan_all`（step-1 deliverable）、`distill.py:1411`（compose_candidate）、`:2851`（_build_body）、`~2949`（how_text 生成，*待实现核实*）
- deliverables: compose 挂 must_cover + _build_body 三节代码直出 + 单测
- Hard-stop: 现有 `test_distill_frontmatter.py` 任一 self-gate 用例红 → 修复后再进 step-3 合并

## 4. 成功标准（SC）

- [ ] SC-2.1（代码直出）: 含 must_cover 的 fixture，渲染后 How/适用边界/TLDR 节分别包含对应 must_cover 项的字面量
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_distill_must_cover.py -q`
  - expect: must_cover 项 100% 出现在目标节
- [ ] SC-2.2（LLM 无选择权）: mock 掉语义 segment（how_steps 返回空）后，must_cover 操作仍被代码渲染进 How 节
  - verify (method: test): 同上文件，断言 how_steps=[] 时 How 节仍含 must_cover operation
  - expect: must_cover 不依赖 LLM segment

## 5. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/distill.py:compose_candidate(L1411)
  WHAT:  调 source_ops_scanners.scan_all(raw_content) 产 must_cover；挂到传给 _build_body 的数据结构（新增 must_cover 字段）
  WHY:   现 _build_body 只拿 LLM 抽的 how_steps，原文操作 LLM 漏了代码补不到
  VERIFY: python -m pytest tests/quality/test_distill_must_cover.py::test_compose_attaches_must_cover -q

Step.2
  WHERE: src/akb2/quality/distill.py:_build_body How 段渲染处(~L2949，待核实)
  WHAT:  must_cover.source_operation 由代码直接渲染进 How 节（带 raw anchor），排在 LLM how_steps 之前/合并去重；LLM segment 只补充语义，不决定 operation 是否出现
  WHY:   把"哪些 How 进正文"从 LLM 选择改为代码强制
  VERIFY: python -m pytest tests/quality/test_distill_must_cover.py::test_operation_rendered_without_llm -q

Step.3
  WHERE: src/akb2/quality/distill.py:_build_body 适用边界节 + TLDR/论断节
  WHAT:  must_cover.source_boundary 代码渲染进适用边界节；source_conclusion 进 TLDR/论断位
  WHY:   master-plan Q2 三类 must_cover 各有归属节
  VERIFY: python -m pytest tests/quality/test_distill_must_cover.py -q
```

## 6. 边界

修改：仅 `distill.py` 的 compose/_build_body 渲染逻辑 + 新测试
不修改：`extract_claims` 及抽取 schema（押后单元）、scanner 模块、checks_structural、grader

## 7. 检查点
与 step-3 一起 commit（must_cover 渲染 + 校验是一对）。
