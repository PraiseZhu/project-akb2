# Run Evidence — cc-executable-plan C-5b L1 rerun

```yaml
timestamp: 2026-05-06T00:30:00Z
runner: claude
runner_override_reason: "user approved Claude execution per C-4 真实 Runner 执行版 plan 选项 A; C-5b 沿用同一 override"
phase: C-5b
skill: cc-executable-plan
fixture_path: /Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/c4a/cc-executable-plan/fixture.md
skill_sha256_before_c5b_inject: 69364def87afa9ad88e66bf1b2285d0007ee245070b4ed8a6367496dc50357e8
skill_sha256_after_c5b_inject: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a
skill_diff_summary: "+4 lines: 2 blank + 2 HTML comments (akb-backlink + akb-interface) inserted between frontmatter close and # title; no other content changed"
skill_sha256_before_smoke: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a
skill_sha256_after_smoke: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a
output_sha256: a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae
output_sha256_matches_c4: true
output_lines: 200
assertion_status: pass
eval_level_after: L1
last_run_status: pass
backlink_status: linked
rollback_triggered: false
```

## C-5b 关键证据:输出 sha256 与 C-4 完全一致

| 项 | C-4 (2026-05-05) | C-5b (2026-05-06) | 一致 |
|---|---|---|---|
| skill_sha256 | `69364def87afa9ad88e66bf1b2285d0007ee245070b4ed8a6367496dc50357e8` | `5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a` | **不同(预期)** — backlink 注释注入 |
| output_sha256 | `a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae` | `a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae` | **完全一致** ✓ |

**含义**:输入 fixture 不变 + skill 仅添加 HTML 注释(注释对 skill 行为透明) → 输出字节完全相同。这是 skill 行为未被破坏的最强证据 — 不是"通过断言",而是"逐字节复现"。

## Pre-call checks

| # | check | result | evidence |
|---|---|---|---|
| 1 | sha256 matches expected (post-inject) | pass | `5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a` |
| 2 | template drift `diff -q` exit 0 | pass | `~/.claude/templates/plan-template.md` 与 Codex 镜像 byte-for-byte 一致 (双方 131 行) |
| 3 | backlink injection diff scope check | pass | grep `akb-backlink` 命中 1 次, `akb-interface` 命中 1 次, 无其它修改 |

## Assertions(逐字节复用 C-4 verdict)

### A. Template drift

- [x] `diff -q` exit 0 → **pass**

### B. Mandatory section headings present in skill output

(同 C-4 — 8/8 命中,output 字节完全一致)

- [x] `## Review 对齐` / `## 目标与验收` / `## 当前状态` / `## 代码执行方向` / `## 接口与数据合约` / `## 实现步骤` / `## 边界与失败模式` / `## 测试计划` → 8/8 → **pass**

### C. Review 对齐 alignment-block contents

- [x] `REVIEW_DOMAIN：app_code` 行存在(全角 colon 是中文 plan 模板正确约定) → **pass**
- [x] `REVIEW_FOCUS：` 5 维度 → **pass**

### D. Output integrity

- [x] Markdown well-formed,fence 平衡(3 对) → **pass**
- [x] Output 长度 = 200 行 ≥ 200 阈值 → **pass**
- [x] Output 提及 `legacy-skill-importer.sh`(15 次)+ `skill-importer-v2.sh`(11 次) → **pass**

## L1 promotion gate verdict

A pass + B 8/8 + C 2/2 + D 3/3 + output sha256 与 C-4 byte-identical → **all pass + skill behavior bit-perfect preserved** → recommend `eval_level_after: L1`(保持 L1 不回退)

## raw_output

输出与 C-4 字节完全相同(sha256 `a34aa2aec6eb86c9c01dd0b9432066dec413e63e886498b0f5c26f531cd516ae`)。完整 200 行 plan 见 C-4 evidence:[`../../c4a/cc-executable-plan/run-evidence.md`](../../c4a/cc-executable-plan/run-evidence.md) §raw_output 段。

C-5b 不重复 embed 完整 raw_output(避免冗余;sha256 已锁定一致性);transient cache 在 `/tmp/claude-501/c4-plan-output.md`。

## 后续 handoff to AKB2.0 write-back

- `toolbox/evals/cc-executable-plan.md`:`eval_level: L1` 保持,`last_run_at: 2026-05-06`,`last_run_status: pass`,`last_run_evidence` 指向本文件,新增 `workspace_version` 同步
- `toolbox/interfaces/cc-executable-plan.md`:`workspace_version` 已更新到 `5af73fdadd91`,`backlink_status: linked` 已更新
- C-5b Tool Gate matrix:全 7 项硬指标 + backlink linked + hash 对齐 + L1 重过 → **允许 `decision: active`**
