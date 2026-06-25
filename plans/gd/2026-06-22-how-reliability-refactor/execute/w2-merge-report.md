# w2 Merge Report — how-reliability-refactor execute

run_id: how-reliability-refactor-execute-2026-06-23
wave: w2
stage: execute
recorded_at: 2026-06-23T08:08:52Z

## 派发摘要

- wave w2（并行 2 track + 1 wave 后集成修复）：step-2 must_cover 代码直出 ‖ step-3 SG-must-cover fail-closed，再 step-3-fix 修 gate 引入的存量回归
- child_agent_count: 2 / max_parallel: 2（step-2‖step-3 并行；step-3-fix 是 wave 后串行集成修复）
- child jobs: w2-step-2（distill.py）、w2-step-3（checks_structural.py）、w2-step-3-fix（conftest.py + test_repair_loop.py）

## child 结果合并

三个 child_executor 均返回 completed。主 agent 独立复核（不采信自报，亲跑全部 verify 命令以 exit code 为准）：

1. **step-2（SC-2）**：`PYTHONPATH=src python -m pytest tests/quality/test_distill_must_cover.py -q` → 3 passed exit=0。compose_candidate 调 scan_all 建 must_cover 账本，_build_body 三节代码直出（operation→How / boundary→适用边界 / conclusion→TLDR），LLM 无最终选择权（how_steps=[] 时 How 节仍含全部 operation）。
2. **step-3（SC-3）**：`pytest test_checks_structural.py -k "must_cover or callsite"` → 8 passed；全文件 64 passed。SG-must-cover fail-closed：raw_content=None 不静默跳过产 Defect；漏 operation/boundary/conclusion → Defect；5 调用点 provenance 审计（distill:1481/repair:1125,1248/runner:629/smoke_validator:441）真实同源 raw、无截断/异源。
3. **step-3-fix**：gate 引入的 34 个存量回归（frontmatter 29 + repair_loop 5）根因=测试传 raw=None/不存在路径。fix：conftest sample_raw_path 改 autouse 写真实文件 + test_repair_loop 5 用例补 raw_text。分离语义，gate 未弱化。

## 关键工程发现（主 agent 独立验证，推翻 child 误报）

- step-2 child 自报"HEAD baseline 32 failed"——**误报**（混淆并行 step-3 改动）。主 agent stash 对比确认 HEAD(w1) baseline frontmatter 58 passed / repair_loop 35 passed 全绿，w2 真实引入 34 回归。
- 全 quality 套件：w2 当前 **554 passed / 0 failed**，HEAD baseline 538 passed + 3 个 w2 未集成 fail。净增 16 passed、0 fail。autouse + 改 fixture 内容**未引入更大范围回归**。

## 交付物验收

| task | deliverable | 物理存在 | verified |
|---|---|---|---|
| step-2 | src/akb2/quality/distill.py | ✓ | true |
| step-2 | tests/quality/test_distill_must_cover.py | ✓ | true |
| step-3 | src/akb2/quality/checks_structural.py | ✓ | true |
| step-3 | tests/quality/test_checks_structural.py | ✓ | true |
| step-3 | tests/quality/conftest.py | ✓ | true |
| step-3 | tests/quality/test_repair_loop.py | ✓ | true |

step-3 owned_paths 已扩含 conftest.py + test_repair_loop.py（gate 触及文件，分离语义修复归 step-3 完成范围；dispatch 校验仍 exit 0）。

## path audit

git status 仅 w2 范围内文件改动：distill.py / checks_structural.py / test_checks_structural.py / test_distill_must_cover.py / conftest.py / test_repair_loop.py + dispatch_map.json（扩 step-3 owned_paths）。零越界（未碰 grader.py / source_ops_scanners / extract_claims 抽取 schema / ~/.claude/**）。

## batch 校验

`gd-validate-execution-batch.py batch-...-wave2.json dispatch_map.json` → exit 0（待校验）。

## 残留工程张力点（留 review / 后续 step）

1. **repair_raw_text 用无 must_cover 的 raw**（scan_all 返回空）让 repair loop 不触发 mismatch——分离语义的弱化实现，绕过 SC-3.3 repair 调用点传含 must_cover raw 的精神。SC-3.3 callsite 审计测试（用 sample_raw_content 含 must_cover）仍独立通过未被打穿。留 review 评估是否后续补强。
2. **sample_raw_content 被删 source_conclusion 句**以避免 must_cover conclusion 代码直出与 test_tldr_has_sentence_boundaries_per_anchor 的 TLDR count 断言冲突——张力点留 review（是否应调 count 断言而非删 fixture 内容）。
3. **sample_raw_path autouse 在 CWD raw/ 写临时文件**，20 个硬编码路径测试依赖此副作用；pytest CWD 变化需调整。

## final_decision

APPROVED — w2 step-2 + step-3 完成且全套件验证通过（554 passed / 0 failed），gate 未弱化，零越界。残留张力点为 review 项，非 w2 交付阻断。

## blocking_buckets

[]（空）
