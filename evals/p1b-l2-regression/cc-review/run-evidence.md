# cc-review — P1b L2 Regression Run Evidence

## 元数据

| 字段 | 值 |
|---|---|
| Phase | P1b L2 regression |
| Tool | cc-review |
| Workspace path | `~/.codex/skills/cc-review/SKILL.md` |
| SKILL.md sha256(P1b 全程不变) | `592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274` |
| Workspace mtime | `2026-05-06`(锁自 C-5b backlink injection 后) |
| Runner | **claude**(Option A — 用户 2026-05-04 override) |
| Runner override reason | Codex CLI 在本 Claude Code session 内不可直接调用;Claude 严格按 cc-review SKILL.md `Output Contract` + `references/structured-review-templates.md` 自驱产出;模板 source-of-truth 为 `~/.codex/skills/cc-review/references/structured-review-templates.md` |
| Run started | 2026-05-06 |
| Run finished | 2026-05-06 |
| Plan ref | `~/.claude/plans/joyful-squishing-moonbeam.md` §P1b 段(toolbox v2 P1b L2 regression) |

## SKILL.md 不变性证据

```bash
# preflight(P1b 启动前)
shasum -a 256 ~/.codex/skills/cc-review/SKILL.md
# expected: 592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274

# postflight(P1b 5 case 跑完后)
shasum -a 256 ~/.codex/skills/cc-review/SKILL.md
# expected: 592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274
```

预期:byte-identical(本 P1b 流程零 mutation 到 SKILL.md;Claude 仅读 + 执行)。

## 5 case 执行表

| # | case_id | input fixture | output path | output 行数 | output 字节 | output sha256 (前12) | verdict 实测 | P1 数 | P2 数 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | mandatory-clean-plan-review | `01-...md` | `01-...md` | 27 | 1101 | `2e105fadee30` | 通过 | 0 | 0 |
| 2 | mandatory-plan-scope-leak-review | `02-...md` | `02-...md` | 41 | 4666 | `756547f771be` | 需要修改 | 2 | 0 |
| 3 | mandatory-code-bug-review | `03-...md` | `03-...md` | 97 | 5636 | `92bf108b1e7c` | 需要修改 | 1 | 1 |
| 4 | edge-residual-risk-only-review | `04-...md` | `04-...md` | 30 | 2040 | `41082b9549dc` | 通过 | 0 | 0 |
| 5 | edge-chinese-fenced-format-review | `05-...md` | `05-...md` | 30 | 2122 | `429f480ee136` | 通过 | 0 | 0 |

## reviewer 复测命令

```bash
# 1. 5 fixture / 5 output 文件齐
ls "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-review/fixtures" | wc -l
# expected: 5

ls "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-review/outputs" | wc -l
# expected: 5

# 2. verdict 实测对账(5 case 应分别 通过/需要修改/需要修改/通过/通过)
for f in "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-review/outputs/"*.md; do
  v=$(grep "^结论:" "$f" | head -1)
  printf "%s\t%s\n" "$(basename $f)" "$v"
done
# expected:
#   01-mandatory-clean-plan-review.md       结论:通过
#   02-mandatory-plan-scope-leak-review.md  结论:需要修改
#   03-mandatory-code-bug-review.md         结论:需要修改
#   04-edge-residual-risk-only-review.md    结论:通过
#   05-edge-chinese-fenced-format-review.md 结论:通过

# 3. P1 / P2 实测分布
for f in "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-review/outputs/"*.md; do
  p1=$(grep -cE "^### 问题 [0-9]+ .*\[P1\]" "$f")
  p2=$(grep -cE "^### 问题 [0-9]+ .*\[P2\]" "$f")
  printf "%s\tP1=%s\tP2=%s\n" "$(basename $f)" "$p1" "$p2"
done
# expected: 01:0/0  02:2/0  03:1/1  04:0/0  05:0/0

# 4. SKILL.md byte-identical to baseline
shasum -a 256 ~/.codex/skills/cc-review/SKILL.md | cut -c1-64
# expected: 592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274

# 5. 0 自动化解锁 — 5 output 不含 automation_allowed: true
grep -lE "^automation_allowed: true$" "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-review/outputs/"*.md
# expected: empty output

# 6. case 04 edge-residual-risk-only — P3 不升级为 blocker
# 该 case `## 问题` 区下应只有 `无`,所有 P3 在 `## 剩余风险` 区
grep -A 1 "^## 问题$" "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-review/outputs/04-edge-residual-risk-only-review.md" | tail -1
# expected: "无"

# 7. case 03 中 PLANTED 标记不被 echo 到 output
grep -c "PLANTED" "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1b-l2-regression/cc-review/outputs/03-mandatory-code-bug-review.md"
# expected: 0
```

## 评估结论

- **5/5 case pass**(per assertions.md 30 主 assertion 全过)
- **verdict 100% 命中期望**(2 通过 + 2 需要修改 + 1 中性格式测试)
- **0 SKILL.md mutation**
- **0 automation_allowed: true 解锁**
- **runner override 全程透明**

## 升级提议

- **L1 → L2**:本 P1b 5 case(3 mandatory + 2 edge)对 cc-review SKILL.md 输出形式 + verdict 阈值 + P1/P2/P3 收敛规则 + 中文 / 单一 fence 输出契约做了多 case 覆盖,且 byte-identical SKILL.md 保证回归不破坏 C-5b L1 baseline,**满足 L2 升级最小条件**
- **不解锁 `automation_allowed: true`**:L2 不等于自动化;automation_allowed 提升留 L3 + 人审
- **下一轮**:L3 需添加 risk_level 不动 + 长 capsule 失败注入 case + Codex 不可达 degraded 路径(本 P1b 不在范围)
