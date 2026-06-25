# w6 Merge Report — how-reliability-refactor execute

run_id: how-reliability-refactor-execute-2026-06-23
wave: w6 | stage: execute | recorded_at: 2026-06-23T13:23:44Z
child_agent_count: 1 | max_parallel: 1（w6 串行 step-8）

## 派发 + 结果
w6 step-8 How 回归测试三类语料。test-fresh（有操作→捞全核心 MCP 命令）+ 纯观点博客（无 How→fail-closed，scan_all 空 + 硬造命令被 SG-command-recheck Layer 1 挡）+ 陷阱样本（枚举带偏→抽核心不被带偏，15 次要+3 核心全捞）。未碰任何 src 实现。

## 主 agent 独立复核
- 全 quality 套件 630 passed / 0 failed（w5 609 → 净增 21，0 fail）
- SC-6 测试 21 passed；三语料就位（ls 确认）
- path audit：git status 只加 tests/（regression/ + test_how_regression.py），零 src 改动
- child 揭示 step-4 双层门 Layer 1 信号实现（'原文操作（must_cover 代码直出）'字符串），与 w3 dual-layer 一致

## final_decision
APPROVED — step-8 完成且全套件验证通过，三类语料就位，零 src 改动。

## blocking_buckets
[]（空）
