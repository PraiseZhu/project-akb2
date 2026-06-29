verdict: REQUIRES_CHANGES
findings:
  - severity: P2
    file: plans/gd/2026-06-26-loop1-calibration-harness-fix/merge-report-execute.md:53; plans/gd/2026-06-26-loop1-calibration-harness-fix/merge-report-execute.md:65; plans/gd/2026-06-26-loop1-calibration-harness-fix/controller-report-execute.json:8
    issue: 修复状态工件仍把已补项列为 blocking，最终状态自相矛盾。
    evidence: v2 重跑确认 `execution-dispatch-map.json` 已有 `overrides_rationale`，内容说明 `constitution.md v1.2.0` 是 master-plan GOAL_SOURCE 治理前提、`CLAUDE.md` 是工程文档同步；hotfix ledger 也已补齐，`stage-dispatch-ledger-execute-hotfix1.json` 锚定 `t2b-fix-continue-url` 和 `t1-fix-anchor`，`stage-dispatch-ledger-execute-hotfix2.json` 锚定 `t2a-fix-fallback`，所有 controller batch ledger hash 与 child result_hash 均用 sha256 复算匹配。但 `merge-report-execute.md` 仍在“待处理项”写 `P1#3 + P2#3`，最终 bullet 写 `P1#3 + P2#3 待处理`，`blocking_buckets` 仍包含 `P1#3 constitution 越界, P2#3 hotfix ledger`；`controller-report-execute.json.primary_gate.blocking` 也仍包含 `P1_3_constitution`、`P2_3_hotfix_ledger`。这会让审计者无法判断这两项究竟是已修复/已豁免，还是仍阻塞。
summary: P1 谎报已修正，SC-1 权威 verify 如实 exit 1 且 factory_acceptance=False；overrides_rationale 与 hotfix ledger hash 已补并复算通过，边界未碰包 A 源码，pytest 组通过；但 merge/controller 仍把已补的 P1#3/P2#3 保留为 blocking，需同步汇总工件后才能清审。
