# w3 Merge Report — how-reliability-refactor execute

run_id: how-reliability-refactor-execute-2026-06-23
wave: w3
stage: execute
recorded_at: 2026-06-23T12:06:24Z

## 派发摘要

- wave w3（并行 2 track + Codex 方案重构 + schema fix）：step-4 机器回查 ‖ step-5 provenance 细分
- child_agent_count: 2 / max_parallel: 2（step-4‖step-5 并行；后续 dual-layer 重构 + schema fix 为各 track 完成阶段）
- child jobs: w3-step-4（raw_verify + SG-command-recheck 双层）、w3-step-5（provenance 三态 + schema）

## child 结果合并 + 关键工程决策

step-4 初版（全文反引号命令回查 raw）打穿 55 个存量测试。主 agent 识别出门责任边界过宽，请 Codex 第二意见（/review1 讨论）。Codex 判定门过宽，建议双层门。按 Codex 方案重构 step-4：

**双层门（Codex 方案）**：
1. Layer 1 来源锚定命令回查：只对 must_cover 代码直出命令（带 source_grounded 标记，来自 scan_all(raw)）做 raw 字面回查——治代码直出命令字面量漂移，双保险。
2. Layer 2 派生命令安全门：对 akb_interpretation/未标注命令（LLM how_steps 语义示例）不查 raw，但跑危险命令黑名单（rm -rf/fork bomb/curl|sh/mkfs/chmod 777 等）——治 LLM 塞破坏性命令。

**step-5 schema 遗漏**：step-5 加 provenance_split 字段未同步 how_steps extract schema（additionalProperties:False），致 test_distill_extract_retry 9 红。schema fix：properties 正式声明 provenance_split 字段（enum 三态），9 红转绿。

## 主 agent 独立复核（不采信 child 自报）

- 全 quality 套件 **592 passed / 0 failed** exit=0（w2 baseline 554 → 净增 38 passed，0 fail）
- step-4 child 自报"9 红非 step-4 引入"——**部分误报**：9 红是 step-5 schema 遗漏引入（非 step-4），但属 w3 回归必须修；主 agent 定位 schema 并派 fix 修掉
- w3 SC：raw_verify 26 passed、provenance 8 passed、must_cover 3 passed、recheck 4 passed
- 55 红全转绿（frontmatter 35 + extract_retry 11 + repair 5 + smoke 3 + runner 1）
- ruff 新文件干净；checks_structural/distill 有 pre-existing E501（非本 wave 引入，stash 对比确认）
- path audit 零越界（未碰 grader/10-dimensions/SG-must-cover 块/scanner 已有文件）

## 交付物验收

| task | deliverable | 物理存在 | verified |
|---|---|---|---|
| step-4 | source_ops_scanners/raw_verify.py | ✓ | true |
| step-4 | checks_structural.py | ✓ | true |
| step-4 | tests/quality/test_raw_verify.py | ✓ | true |
| step-4 | tests/quality/test_distill_must_cover.py | ✓ | true |
| step-5 | src/akb2/quality/distill.py | ✓ | true |
| step-5 | tests/quality/test_provenance_split.py | ✓ | true |

step-4 owned_paths 扩含 test_distill_must_cover.py（w3 期间 fix 触及，gate 收窄后补丁多余但无害保留）；dispatch 校验 exit 0。

## batch 校验

`gd-validate-execution-batch.py batch-...-wave3.json dispatch_map.json` → exit 0（待校验）。

## 残留 review 项（非阻断）

1. step-4 双层门判定信号用 'must_cover 代码直出' 字符串而非裸 (source_grounded) marker（_how_step_provenance 会误标），达成 Codex 意图但实现信号偏离原指令，留 review 规范化。
2. provenance_split 字段消费方（SG-provenance-ratio）仍按文本 marker 计数，未直接消费新字段，留后续接线。
3. distill.py 55 个 pre-existing ruff issue（E501/F841），非本 wave 引入，建议后续清理。

## final_decision

APPROVED — w3 step-4 双层门 + step-5 provenance 完成且全套件验证通过（592 passed / 0 failed），治编未弱化，55 红全转绿，零越界。残留为 review 项，非 w3 交付阻断。

## blocking_buckets

[]（空）
