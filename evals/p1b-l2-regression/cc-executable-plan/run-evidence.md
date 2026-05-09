# cc-executable-plan — P1b L2 Regression Run Evidence

## 元数据

| 字段 | 值 |
|---|---|
| Phase | P1b L2 regression |
| Tool | cc-executable-plan |
| Workspace path | `~/.codex/skills/cc-executable-plan/SKILL.md` |
| SKILL.md sha256(P1b 全程不变) | `5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a` |
| Workspace mtime | `2026-05-06`(锁自 C-5b backlink injection 后) |
| Runner | **claude**(Option A — 用户 2026-05-04 override) |
| Runner override reason | Codex CLI 在本 Claude Code session 内不可直接调用;Claude 严格按 cc-executable-plan SKILL.md + plan-template.md 11 节模板自驱产出;模板 source-of-truth 为 `~/.claude/templates/plan-template.md` |
| Run started | 2026-05-06 |
| Run finished | 2026-05-06 |
| Plan ref | `~/.claude/plans/joyful-squishing-moonbeam.md` §P1b 段(toolbox v2 P1b L2 regression) |

## SKILL.md 不变性证据

```bash
# preflight(P1b 启动前)
shasum -a 256 ~/.codex/skills/cc-executable-plan/SKILL.md
# expected: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a

# postflight(P1b 5 case 跑完后)
shasum -a 256 ~/.codex/skills/cc-executable-plan/SKILL.md
# expected: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a
```

预期:byte-identical(本 P1b 流程零 mutation 到 SKILL.md;Claude 仅读 + 执行)。

## 5 case 执行表

| # | case_id | input fixture | output path | output 行数 | output 字节 | output sha256 (前12) | 11 节齐 |
|---|---|---|---|---|---|---|---|
| 1 | mandatory-docs-content-plan | `cc-executable-plan/fixtures/01-...md` | `cc-executable-plan/outputs/01-...md` | 183 | 8412 | `dd51650cd76e` | ✓(10 H2) |
| 2 | mandatory-ai-infra-plan | `02-...md` | `02-...md` | 222 | 11020 | `6a703550876e` | ✓(10 H2) |
| 3 | mandatory-mutation-boundary-plan | `03-...md` | `03-...md` | 212 | 9757 | `5b1d8ebcb83c` | ✓(10 H2) |
| 4 | edge-ambiguous-input-plan | `04-...md` | `04-...md` | 149 | 6044 | `9ef97c8017b2` | ✓(10 H2) |
| 5 | edge-review-handoff-plan | `05-...md` | `05-...md` | 245 | 13005 | `5cfa4f097411` | ✓(10 H2) |

## reviewer 复测命令

```bash
# 1. 5 fixture / 5 output 文件齐
ls "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-executable-plan/fixtures" | wc -l
# expected: 5

ls "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-executable-plan/outputs" | wc -l
# expected: 5

# 2. 5/5 case 11 节齐(grep H2 命中)
for f in "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-executable-plan/outputs/"*.md; do
  cnt=$(grep -cE "^## (Review 对齐|目标与验收|当前状态|代码执行方向|接口与数据合约|实现步骤|边界与失败模式|测试计划|待确认|实施前 Review Checklist)" "$f")
  printf "%s: %s/10\n" "$(basename $f)" "$cnt"
done
# expected: each row "10/10"

# 3. SKILL.md byte-identical to baseline
shasum -a 256 ~/.codex/skills/cc-executable-plan/SKILL.md | cut -c1-64
# expected: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a

# 4. 0 自动化解锁 — 5 output 不含 automation_allowed: true
grep -lE "^automation_allowed: true$" "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-executable-plan/outputs/"*.md
# expected: empty output

# 5. case 04 edge ambiguous — §待确认 ≥ 5 条
grep -cE "^[0-9]+\. \*\*" "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-executable-plan/outputs/04-edge-ambiguous-input-plan.md"
# expected: ≥ 5

# 6. case 05 edge review handoff — Review handoff 段存在 + 4+ 验收 evidence 命令
grep -c "Review handoff" "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-executable-plan/outputs/05-edge-review-handoff-plan.md"
# expected: ≥ 1

grep -cE "^[0-9]+\. \*\*" "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-executable-plan/outputs/05-edge-review-handoff-plan.md"
# expected: ≥ 4(验收 evidence 5 条编号对账)
```

## 评估结论

- **5/5 case pass**(per assertions.md 30 主 assertion 全过)
- **100% 11 节齐**
- **0 SKILL.md mutation**
- **0 automation_allowed: true 解锁**
- **runner override 全程透明**

## 升级提议

- **L1 → L2**:本 P1b 5 case(3 mandatory + 2 edge)对 cc-executable-plan SKILL.md 输出形式 + 边界守门 + ambiguous 输入处理做了多 case 覆盖,且 byte-identical SKILL.md 保证回归不破坏 C-5b L1 baseline,**满足 L2 升级最小条件**(≥ 5 case + 边界覆盖 + 跨 domain)
- **不解锁 `automation_allowed: true`**:L2 不等于自动化;automation_allowed 提升留 L3 + 人审(per TOOL-EVAL-RULES)
- **下一轮**:L3 需添加 risk_level 不动 + 失败注入 case 覆盖(本 P1b 不在范围)
