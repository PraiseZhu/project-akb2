# w4 Merge Report — how-reliability-refactor execute

run_id: how-reliability-refactor-execute-2026-06-23
wave: w4 | stage: execute | recorded_at: 2026-06-23T12:51:36Z
child_agent_count: 1 | max_parallel: 1（w4 串行 step-6）

## 派发 + 结果
w4 step-6 grader degraded fail-closed 护栏。接线 MAX_GRADER_RETRIES(2) 重试循环（仅解析失败重试，不动三打分函数体）；repair.py 两处 grader 调用点（主路径 L1261 + 确定性修复路径 L1131）均补 fallback_used fail-closed 标 grader_degraded；runner._persist_grader_pass 写 status=DEGRADED 不写 PASS；promote._read_grader_result 拒收 fallback_used/suspended 的 PASS。

## 主 agent 独立复核
- 全 quality 套件 597 passed / 0 failed（w3 592 → 净增 5，0 fail）
- SC-6.5 FORBIDDEN 守护：stash 对比三打分函数体（AST 提取）+ 10-dimensions.md sha256 改动前后完全一致
- SC-6.4 异族不变：DISTILL_MODEL_FAMILY=deepseek-v4-pro ≠ PRIMARY_GRADER_MODEL=gpt-5.4
- child 报的 hash 值与主 agent 复算不同（提取方法差异），但前后对比一致才是 SC-7 真实判据，已独立确认
- path audit 零越界（6 个 owned 文件，未碰 scanner/distill/checks_structural/raw_verify/10-dimensions）
- child 发现并修复计划遗漏：repair.py 两个 grader 调用点（task 只指主路径），已对称补两处

## final_decision
APPROVED — step-6 完成且全套件验证通过，三函数体 hash 不变，异族维持，零越界。

## blocking_buckets
[]（空）
