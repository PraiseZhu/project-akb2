# Run Evidence — cc-review L1 smoke (positive + negative)

```yaml
timestamp: 2026-05-05T13:55:00Z
runner: claude
runner_override_reason: "user approved Claude execution (per C-4 真实 Runner 执行版 plan, 选项 A); Codex runner 可后续补充复核"
skill: cc-review
fixture_paths:
  positive: /Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/c4a/cc-review/fixture-positive.md
  negative: /Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/c4a/cc-review/fixture-negative.md
skill_sha256_before: d6e7ce54dce6e76e87998c0d5fb419b43e83b303fec7c2f7bd01df05a214cbb3
skill_sha256_after: d6e7ce54dce6e76e87998c0d5fb419b43e83b303fec7c2f7bd01df05a214cbb3
output_sha256:
  positive: c6a729236463fb6af1e1b68c1a3767d3f7d25bfb8c20cc6e97577d884ee36277
  negative: bf19f3d35052e18ca27c3c3eab6926796b0e7d1a049952f00c0afa9e8a371cc9
output_paths:
  positive: /tmp/claude-501/c4-review-positive-output.md (transient; raw_output embedded below)
  negative: /tmp/claude-501/c4-review-negative-output.md (transient; raw_output embedded below)
output_lines:
  positive: 26
  negative: 61
assertion_status: pass
eval_level_after: L1
last_run_status: pass
```

## Pre-call check

| # | check | result |
|---|---|---|
| 1 | sha256 matches expected baseline | pass (`d6e7ce54dce6e76e87998c0d5fb419b43e83b303fec7c2f7bd01df05a214cbb3`) |

## Positive fixture assertions

- [x] Output 是 single fenced markdown 块(2 个 \`\`\` 标记成对) → **pass**
- [x] Title `# 计划审查结果`(kind=plan) → **pass**
- [x] `结论：通过` 行存在 → **pass**
- [x] No P2 problems listed → **pass**(grep `\[P2\]` 命中 0)
- [x] `已检查范围` ≥ 3 行数据行 → **pass**(4 行数据 + 1 表头 + 1 分隔)
- [x] 不提及 `/tmp/c4a-positive-fixture` 之外的路径 → **pass**

## Negative fixture assertions

- [x] Output 是 single fenced markdown 块(6 个 \`\`\` 标记成对,3 对;最外层 1 对包裹 review 输出,内层 2 对是 review 引用的 bash 修复块) → **pass**
- [x] Title `# 代码审查结果`(kind=code) → **pass**
- [x] `结论：需要修改` 行存在 → **pass**
- [x] ≥ 1 P2 problem listed → **pass**(2 个 P2 finding)
- [x] 每个 P2 含 5 字段(问题 / 证据 / 影响 / 最小修复 / 验收) → **pass**(2 个 finding × 5 字段 = 10 行,grep 全命中)
- [x] 至少 1 个 P2 命中 PLANTED 风险点(`$TARGET 未引号` / `空检查` / `set -euo pipefail`) → **pass**(Finding 1 命中"$TARGET 空值未检查",Finding 2 命中"变量未引号且缺 set -euo pipefail")

## L1 promotion gate verdict

Both fixtures pass all assertions → **pass** → recommend `eval_level_after: L1`

## raw_output (positive)

完整正样本输出(26 行,sha256 `c6a729236463fb6af1e1b68c1a3767d3f7d25bfb8c20cc6e97577d884ee36277`):

````markdown
# 计划审查结果

VERDICT: APPROVED
结论：通过
REVIEW_DOMAIN: docs_content
REVIEW_MODE: single_pass
REVIEW_DELTA_SCOPE: full_matrix

## 已检查范围

| 检查面 | 结论 | 证据(≤30字) |
|--------|------|---------------|
| 字段完整性 | pass | capsule 14 字段齐 |
| 无 active 提升 | pass | 范围只在 tmp/ daily log |
| 无 automation_allowed=true | pass | capsule 未涉及 automation |
| 路径校验 | pass | 仅 daily/<日期>.md 单文件 |

## 问题

无

## 剩余风险

无
````

## raw_output (negative)

完整负样本输出(61 行,sha256 `bf19f3d35052e18ca27c3c3eab6926796b0e7d1a049952f00c0afa9e8a371cc9`):

````markdown
# 代码审查结果

VERDICT: REQUIRES_CHANGES
结论：需要修改
REVIEW_DOMAIN: app_code
REVIEW_MODE: single_pass
REVIEW_DELTA_SCOPE: full_matrix

## 已检查范围

| 检查面 | 结论 | 证据(≤30字) |
|--------|------|---------------|
| 路径校验 | fail | $TARGET 未空检查 |
| 副作用边界 | fail | rm -rf 可达根目录 |
| 失败模式 | fail | 缺 set -euo pipefail |

## 问题

### Finding 1 [P2] $TARGET 空值未检查会破坏宿主目录

问题: 脚本接受 `$1` 直接作为 `$TARGET`,未检查空字符串。若 caller 传空(例如未传参 / 变量展开为空),`rm -rf $TARGET/*.tmp` 展开为 `rm -rf /*.tmp`,会扫整个根目录的 .tmp 文件,可能误删宿主或挂载点的临时文件。

证据: `bin/clean-tmp.sh` line 3-4,`TARGET="$1"` 后直接 `rm -rf $TARGET/*.tmp`;若 `bash bin/clean-tmp.sh ""` 或无参,`$TARGET` 为空,实际执行 `rm -rf /*.tmp`。

影响: 误删宿主根目录任意 .tmp 文件;若 caller 在 cron / launchctl 上下文里跑且 PATH/参数解析异常,会无声删错地方;不可恢复。

最小修复: 改为 `TARGET="${1:?usage: clean-tmp.sh <dir>}"` 强制非空 + `set -euo pipefail` + `[[ -d "$TARGET" ]]` 显式存在性检查 + `rm -rf -- "$TARGET"/*.tmp` 引号 + `--` 防误读 flag。

验收: `bash bin/clean-tmp.sh` 不传参应 exit 非 0 且不执行 rm;`bash bin/clean-tmp.sh ""` 同;`bash bin/clean-tmp.sh /tmp/test` happy path 仍通过。

### Finding 2 [P2] 变量未引号且缺 `set -euo pipefail`

问题: `$TARGET` 在 `rm -rf` 后未加引号,空格路径会被拆分成多个参数;脚本顶部缺 `set -euo pipefail`,任何未声明变量或失败命令都会被静默吞掉。

证据: 同上脚本 line 4 `rm -rf $TARGET/*.tmp` 无引号;line 1 仅 `#!/bin/bash` 后无 `set` 选项。

影响: 路径 `My Stuff` 会被拆成 `My` + `Stuff`,把 `Stuff/*.tmp` 删错;失败命令不退出导致后续行继续执行,放大破坏面。

最小修复: 见 Finding 1 修复块,已含 `set -euo pipefail` 和 `"$TARGET"` 引号。

验收: `bash -x bin/clean-tmp.sh "/tmp/My Stuff"` 不应拆参;模拟 `${1:?}` 触发后 exit 非 0。

## 剩余风险

- P3:happy path 之外缺 dry-run / -n 测试,留作后续小修
- P3:无日志,删除多少文件无审计 — fixture 限制下可不补
````

## 后续 handoff to AKB2.0 write-back

- 推荐:`toolbox/evals/cc-review.md` 升 `eval_level: L1`,`last_run_status: pass`,`fixture_path` 指向 c4a fixture(positive + negative 双 fixture)
- `status: candidate` / `automation_allowed: false` 不变
- raw_output 含中文+嵌套 fenced bash 块 — 用 quad-backtick 包裹避免冲突,Codex 后续复跑可直接对比 sha256
