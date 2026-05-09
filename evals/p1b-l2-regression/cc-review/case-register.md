# cc-review — P1b L2 Regression Case Register

Phase:P1b L2 regression(承接 C-5b active)
工具:`cc-review`
SKILL.md sha256(P1b 全程不动):`592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274`
Workspace path:`~/.codex/skills/cc-review/SKILL.md`
Runner:**claude**(Option A — 用户 2026-05-04 override)
Runner override reason:Codex CLI 在本 Claude Code session 内不可直接调用;Claude 严格按 cc-review SKILL.md `Output Contract` + `references/structured-review-templates.md` 自驱产出
Run started:2026-05-06
Run finished:2026-05-06

## 5 case 矩阵

| # | case_id | 类型 | REVIEW_KIND | 期望 verdict | 期望 P1/P2 | fixture path | output path | output sha256 |
|---|---|---|---|---|---|---|---|---|
| 1 | mandatory-clean-plan-review | mandatory | plan | APPROVED / 通过 | 0 P1 / 0 P2 | `cc-review/fixtures/01-mandatory-clean-plan-review.md` | `cc-review/outputs/01-mandatory-clean-plan-review.md` | `2e105fadee30` |
| 2 | mandatory-plan-scope-leak-review | mandatory | plan | REQUIRES_CHANGES / 需要修改 | ≥ 2 P1(secret commit + scope leak) | `cc-review/fixtures/02-mandatory-plan-scope-leak-review.md` | `cc-review/outputs/02-mandatory-plan-scope-leak-review.md` | `756547f771be` |
| 3 | mandatory-code-bug-review | mandatory | code | REQUIRES_CHANGES / 需要修改 | ≥ 1 P1(path traversal)+ ≥ 1 P2(error handling/encoding/size) | `cc-review/fixtures/03-mandatory-code-bug-review.md` | `cc-review/outputs/03-mandatory-code-bug-review.md` | `92bf108b1e7c` |
| 4 | edge-residual-risk-only-review | edge | code | APPROVED / 通过 | 0 P1 / 0 P2 + ≥ 3 P3 in 剩余风险 | `cc-review/fixtures/04-edge-residual-risk-only-review.md` | `cc-review/outputs/04-edge-residual-risk-only-review.md` | `41082b9549dc` |
| 5 | edge-chinese-fenced-format-review | edge | plan | APPROVED / 通过 | 0 P1 / 0 P2(主测中文 + 单一 fenced 格式) | `cc-review/fixtures/05-edge-chinese-fenced-format-review.md` | `cc-review/outputs/05-edge-chinese-fenced-format-review.md` | `429f480ee136` |

## verdict & 严重度实测对账

| case_id | 期望 verdict | 实测 verdict | 期望 P1 数 | 实测 P1 数 | 期望 P2 数 | 实测 P2 数 | 通过 |
|---|---|---|---|---|---|---|---|
| 01-mandatory-clean-plan-review | 通过 | 通过 | 0 | 0 | 0 | 0 | ✓ |
| 02-mandatory-plan-scope-leak-review | 需要修改 | 需要修改 | ≥ 2 | 2 | n/a | 0 | ✓ |
| 03-mandatory-code-bug-review | 需要修改 | 需要修改 | ≥ 1 | 1 | ≥ 1 | 1 | ✓ |
| 04-edge-residual-risk-only-review | 通过 | 通过 | 0 | 0 | 0 | 0 | ✓ |
| 05-edge-chinese-fenced-format-review | 通过 | 通过 | 0 | 0 | 0 | 0 | ✓ |

**汇总**:5/5 case verdict + 严重度全过期望。

## 输出格式实测

| case_id | 外层 fence | 单一 markdown 块 | 中文输出 |
|---|---|---|---|
| 01-mandatory-clean-plan-review | ` ```markdown ` | ✓ | ✓(标题/字段/状态值/检查面全中文) |
| 02-mandatory-plan-scope-leak-review | ` ```markdown ` | ✓ | ✓ |
| 03-mandatory-code-bug-review | ` ````markdown `(4-backtick,因含 python 代码块) | ✓(嵌套兼容) | ✓ |
| 04-edge-residual-risk-only-review | ` ```markdown ` | ✓ | ✓ |
| 05-edge-chinese-fenced-format-review | ` ```markdown ` | ✓ | ✓ |

> case 03 因含 `python` inner fenced block,主 fence 用 4-backtick `` ````markdown `` 形式以保证嵌套不破坏外层 fence;符合 CommonMark "fence 使用更多 backtick 即可包含较少 backtick 的 inner fence" 规则。
