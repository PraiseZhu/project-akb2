# Step 6: grader degraded fail-closed 护栏（不换模型）v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft | 对应 master-plan: P1-6（Codex r3 问题1）

---

## 1. 目标链

```text
TASK_GOAL: 接线 MAX_GRADER_RETRIES 重试；repair loop 二次评分 + runner + promote 全部拒收
           fallback_used/suspended 的 PASS。本期不动 PRIMARY_GRADER_MODEL（维持 checker=gpt-5.4 异族）。
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `degraded fail-closed 覆盖 repair loop 二次评分(Codex r3 问题1); 不动 grader 三打分函数体(SC-7); 不换模型维持 maker≠checker 异族(问题2); 复用 GraderResult.fallback_used/suspended`

## 3. Task Packet

- blocked_by: `step-1, step-2, step-3`（属 P1，须 P0 完成后启动——分期纪律，Codex r4 问题5）
- can_parallel_with: `step-4, step-5`（同 P1，路径不重叠：grader/repair/runner/promote vs checks/distill）
- owned_paths: `src/akb2/quality/grader.py`（仅 `MAX_GRADER_RETRIES` 重试循环，**不碰打分函数体**）、`repair.py`（二次评分收敛判断）、`runner.py`（`_persist_grader_pass`）、`promote.py`（`_read_grader_result`）、对应 tests
- forbidden_paths: `grader.py` 的 `_build_grader_prompt`/`_build_grader_schema`/`_parse_grader_output` 函数体（FORBIDDEN，SC-7）、`grader.py:42` PRIMARY_GRADER_MODEL（本期不换）、scanner/distill/checks_structural
- required_context:
  - `grader.py:46`（MAX_GRADER_RETRIES 未接线）、`:315/341/363`（run_grader_detailed/primary/gemini 兜底）、`:65/77`（GraderResult.fallback_used/suspended）
  - `repair.py:1261-1294`（二次评分，仅检 suspended L1270，漏 fallback_used）。（注：repair.py:494 是 `_claim_to_how_step` 生成的示例字符串，非执行断言，见 master-plan）
  - `runner.py:600`（_persist_grader_pass，写 status=PASS）、`promote.py:183-194`（_read_grader_result，仅看 status==PASS L194）
- deliverables: 重试循环 + 三处 degraded 拒收 + 测试
- Hard-stop: 改动让 grader 三打分函数体 hash 变 → 违反 SC-7，回退

## 4. 成功标准（SC = master-plan SC-5）

- [ ] SC-6.1（重试）: mock grader 首次坏 JSON → 发生重试（≤MAX_GRADER_RETRIES）
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_grader.py -k retry -q`
  - expect: 重试发生；耗尽后标 degraded（suspended 或 fallback_used）
- [ ] SC-6.2（repair loop fail-closed）: repair 后 `grader_result.fallback_used=True` 且 `defects=[]` → `run_repair_loop` 不判收敛
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/ -k "repair and degraded" -q`
  - expect: 不走 converged（repair.py:1290 不收敛）
- [ ] SC-6.3（下游拒收）: runner `_persist_grader_pass` 不写 fallback PASS；promote `_read_grader_result` 对 fallback_used/suspended 的 PASS 返回 not-ok
  - verify (method: test): 构造 degraded GraderResult/PASS JSON，断言 runner 不写 + promote 拒收
  - expect: degraded 在 repair/runner/promote 三处都 fail-closed
- [ ] SC-6.4（异族不变）: `DISTILL_MODEL_FAMILY != PRIMARY_GRADER_MODEL`
  - verify (method: assertion): 断言两常量不等
  - expect: deepseek != gpt-5.4
- [ ] SC-6.5（FORBIDDEN 守护，本步改 grader.py 必验）: `_build_grader_prompt`/`_build_grader_schema`/`_parse_grader_output` 三函数体 + `10-dimensions.md` 全文 sha256 改动前后不变
  - verify (method: command): 提取三函数体文本（AST/def 行范围，排除 MAX_GRADER_RETRIES 重试循环）算 sha256，改动前后比对
  - expect: 四个 hash 完全一致（重试改动不碰这三个打分函数体）

## 5. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/grader.py:run_grader_detailed(L341 primary 调用)
  WHAT:  primary 解析失败时 for attempt in range(MAX_GRADER_RETRIES) 重试 route_llm；耗尽再走兜底；不改 _parse_grader_output/_build_* 函数体
  WHY:   MAX_GRADER_RETRIES 定义了却没接线，一次失败即掉 gemini 假 0
  VERIFY: python -m pytest tests/quality/test_grader.py -k retry -q

Step.2
  WHERE: src/akb2/quality/repair.py:1269-1294（二次评分收敛判断）
  WHAT:  if grader_result.suspended OR grader_result.fallback_used: 不判 converged（现仅检 suspended L1270）；标 trace status=grader_degraded
  WHY:   Codex r3 问题1：repair 后 deepseek/gemini fallback 假 0 会被当收敛
  VERIFY: python -m pytest tests/quality/ -k "repair and degraded" -q

Step.3
  WHERE: src/akb2/quality/runner.py:_persist_grader_pass(L600)
  WHAT:  grader_meta.grader_fallback_used **或 grader_suspended** 为真 → 不写 status=PASS（写 status=DEGRADED 阻断升格）
  WHY:   现记了 fallback_used 字段(L617)却仍写 PASS；且须同时挡 suspended，不能只挡 fallback（Codex r4 问题3）
  VERIFY: python -m pytest tests/quality/test_runner_lifecycle.py -k degraded -q

Step.4
  WHERE: src/akb2/quality/promote.py:_read_grader_result(L183-194)
  WHAT:  status==PASS 且 fallback_used/suspended 为真 → 返回 (False, "degraded grader, fail-closed")
  WHY:   现仅看 status==PASS(L194)，放行 degraded
  VERIFY: python -m pytest tests/quality/ -k "promote and grader" -q
```

## 6. 边界

修改：grader 重试循环（非打分函数体）+ repair/runner/promote 的 degraded 拒收 + 测试
不修改：grader 三打分函数体、`PRIMARY_GRADER_MODEL`（本期不换 checker）、10-dimensions.md、scanner/distill/checks

## 7. 检查点
单独 commit（涉敏感文件 grader，隔离便于 review/回滚）。
