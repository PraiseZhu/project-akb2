# Fixture 05 — edge: 中文结构化输出 + 单一 fenced markdown

## Capsule

```
REVIEW_KIND: plan
REVIEW_DOMAIN: docs_content
PLAN_REVIEW_ALIGNMENT: 字段齐
PLAN_ALIGNMENT_PRESENT: true
REVIEW_FOCUS: 中文输出一致性;模板字段齐;fenced markdown 完整性
REVIEW_FOCUS_SOURCE: plan
REVIEW_MODE: single_pass
REVIEW_ROUND: initial
REVIEW_DELTA_SCOPE: full_matrix
PREVIOUS_FINDINGS: 无
ONE_REVIEW_ONLY: true
PROJECT_ROOT: /tmp/p1b-format-fixture
REPO_ROOT: 无
BRANCH: 无
BASELINE: 单 markdown 文档新增
PROJECT_GOAL: 给 AKB2.0 增补一份"中文输出语言规则"说明页
PROJECT_GOAL_SOURCE: user_latest
CURRENT_TASK_GOAL: 在 wiki/standards/ 新建 OUTPUT-LANGUAGE.md;说明所有面向用户输出强制中文,代码/枚举/路径例外
SUCCESS_CRITERIA: 文件含 §核心规则;文件含 §例外清单;frontmatter 含 status: ready
IN_SCOPE: wiki/standards/OUTPUT-LANGUAGE.md
OUT_OF_SCOPE: 修改既有 standards;改 hook;改 settings.json
USER_ACCEPTED_DECISIONS: 单文件;不接 lint
CHANGESET_OR_PLAN: |
  新建 wiki/standards/OUTPUT-LANGUAGE.md:

  ---
  slug: output-language
  asset_type: standard
  status: ready
  ---

  # 输出语言规则

  ## §核心规则
  所有面向用户的自然语言输出必须使用中文。Section 标题、表格列标题、说明文字、回复正文均属面向用户。

  ## §例外清单
  保持英文(机器可读,不属面向用户文字):
  - 代码、变量名、函数名、命令
  - 枚举 key:pass/fail/APPROVED/REQUIRES_CHANGES
  - 模板字段 key:REVIEW_DOMAIN/PLAN_SOURCE 等
  - 路径、URL、git hash

  ## §适用边界
  本规则只约束输出语言,不约束代码注释语言(代码注释另由编码风格规则约束)。
VALIDATION_EVIDENCE: |
  本地 grep 检查:
  - grep -c "^## §核心规则" 命中 1
  - grep -c "^## §例外清单" 命中 1
  - frontmatter `status: ready` 存在
KNOWN_LIMITATIONS: 未接 lint 强制扫描;违规靠人工 review 发现
BASELINE_CONFIDENCE: high
REVIEW_RULES: ~/.claude/commands/review.md §审查标准
```

## Expected verdict

- VERDICT: APPROVED(或 REQUIRES_CHANGES — 本 fixture 主测**输出格式**,verdict 容许任一)
- 结论:**通过** 或 **需修改**
- **关键 assertion**:
  - 输出**完全使用中文**(允许英文 token:枚举/字段 key/路径/代码)
  - 输出**整体在单一 fenced markdown 块内**(`` ``` `` 起 + `` ``` `` 止,中间无裸文本/二级 fence)
  - 输出含必备节:`# 计划审查结果` / `## 总结` / `## 审查范围` / `## 发现` / `## 剩余风险` / `## 建议`(或同义中文小节)
  - finding 表头使用中文(如 `| 严重度 | 文件 | 描述 |`)
- 验证 cc-review 是否守住"中文 + 单 fence"格式边界
