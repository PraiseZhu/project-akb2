# cc-review — P1b L2 Regression Assertions

每条 assertion 标 **case_id + 类型 + 期望 + 实测 + 通过/失败**。共 5 case × ~5 关键 assertion = 25 主 assertion;附 5 通用 assertion。

## 通用 assertion(对全部 5 case)

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| G1 | 输出包在单一 fenced markdown 块内 | 5/5 case 命中 | 4 case 用 ```markdown / 1 case 用 ````markdown(嵌套 python);均为单 fence | ✓ |
| G2 | SKILL.md sha256 全程不变 | `592cf3ed4b2c...` | 跑前跑后 byte-identical | ✓ |
| G3 | 0 case 解锁 `automation_allowed: true` | 5/5 case 不引入该字段 | grep 0 命中 | ✓ |
| G4 | 中文输出(标题 / 字段 / 检查面 / 状态值) | 5/5 case 用中文 | `结论:`/`审查领域:`/`已检查范围`/`通过`/`失败`/`不适用`等全中文 | ✓ |
| G5 | P3 不出现在 `## 问题` 区(只入 `## 剩余风险`) | 5/5 case 命中 | 5 case 检查:0 P3 标在 `### 问题` 内 | ✓ |

## case 01:mandatory-clean-plan-review(期望 APPROVED)

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 01.1 | 结论 = 通过 | `结论:通过` | output 第 3 行 `结论:通过` | ✓ |
| 01.2 | 审查领域 = 文档内容 | `审查领域:文档内容` | output 第 4 行命中 | ✓ |
| 01.3 | 0 P1 finding | 0 | grep `\[P1\]` 0 命中 | ✓ |
| 01.4 | 0 P2 finding | 0 | grep `\[P2\]` 0 命中 | ✓ |
| 01.5 | `## 问题` 区写 `无` | 是 | output `## 问题\n\n无` 命中 | ✓ |
| 01.6 | `## 已检查范围` ≥ 3 行 | ≥ 3 | output 表 5 行(来源/生成/元数据/政策/验证) | ✓ |

## case 02:mandatory-plan-scope-leak-review(期望 REQUIRES_CHANGES + ≥ 2 P1)

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 02.1 | 结论 = 需要修改 | `结论:需要修改` | output 第 3 行命中 | ✓ |
| 02.2 | ≥ 2 P1 finding | ≥ 2 | grep `\[P1\]` 2 命中(secret commit + scope leak / 拒走 secret manager) | ✓ |
| 02.3 | finding 命中"prod key 不能 commit / 应走 secret manager / 永不入 git" | 至少 1 finding 含此意 | 问题 1 + 问题 2 均明示 | ✓ |
| 02.4 | finding 5 字段齐(问题 / 证据 / 影响 / 最小修复 / 验收) | 2 finding × 5 字段 = 10 字段全在 | grep 字段名:每 finding 5 字段全命中 | ✓ |
| 02.5 | 验收命令含 git history 检查 + .gitignore 检查 | 是 | 问题 1 验收含 `git log --all -p \| grep ...` + `cat .gitignore \| grep "^\.env$"` + OpenAI 控制台 revoke | ✓ |

## case 03:mandatory-code-bug-review(期望 REQUIRES_CHANGES + ≥ 1 P1 + ≥ 1 P2)

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 03.1 | 结论 = 需要修改 | `结论:需要修改` | output 第 3 行命中 | ✓ |
| 03.2 | ≥ 1 P1 finding(path traversal) | ≥ 1 | grep `\[P1\]` 1 命中(read_file 接 user-supplied path 无白名单 — path traversal) | ✓ |
| 03.3 | ≥ 1 P2 finding(error handling / encoding / size 任 1) | ≥ 1 | grep `\[P2\]` 1 命中(open 缺 encoding + 无 error handling + 无大小上限) | ✓ |
| 03.4 | finding 5 字段齐 + 含文件:行号定位 | 2 finding × 5 字段 + `<file>:<行号>` | grep `(\`utils/path_helper.py:` 2 命中 | ✓ |
| 03.5 | "PLANTED" 标记不被原样 echo 到 output | 0 | grep `PLANTED` 0 命中 | ✓ |
| 03.6 | 最小修复给具体代码示例(Path.relative_to / encoding="utf-8" / max_bytes 等) | 是 | 问题 1 给 Path.relative_to 修复 + 问题 2 给 encoding + max_bytes 修复 | ✓ |

## case 04:edge-residual-risk-only-review(期望 APPROVED + P3 only in 剩余风险)

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 04.1 | 结论 = 通过 | `结论:通过` | output 第 3 行命中 | ✓ |
| 04.2 | 0 P1 / 0 P2 finding | 0 / 0 | grep `\[P1\]` 0 + grep `\[P2\]` 0 | ✓ |
| 04.3 | `## 问题` 区写 `无` | 是 | output `## 问题\n\n无` 命中 | ✓ |
| 04.4 | `## 剩余风险` 含 ≥ 3 P3 项 | ≥ 3 | 5 P3 项(命名风格 / docstring example / 边界扩展 / hypothesis / pytest.raises) | ✓ |
| 04.5 | P3 项**不**升级为 blocker(不进 `### 问题`) | 0 P3 in 问题区 | 全 5 P3 在 `## 剩余风险` 区 | ✓ |
| 04.6 | 审查领域 = 应用代码 | `审查领域:应用代码` | output 第 4 行命中 | ✓ |

## case 05:edge-chinese-fenced-format-review(期望 APPROVED;主测格式)

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 05.1 | 结论 = 通过 或 需要修改(verdict 容许任一,主测格式) | 任一 | output `结论:通过` | ✓ |
| 05.2 | 输出**完全**使用中文(允许英文 token:枚举 / 字段 key / 路径 / 代码) | 中文 | output 全中文(无英文段落 / 无英文章节标题) | ✓ |
| 05.3 | 输出**整体**在单一 fenced markdown 块内 | 1 fence | output 首尾 ` ```markdown ` 配对,无裸 prose | ✓ |
| 05.4 | finding 表头中文(若有 finding) | 中文 | `检查面 / 结论 / 证据`(已检查范围表头);无 finding 区写 `无` | ✓ |
| 05.5 | 含必备节(`# 计划审查结果` / `## 已检查范围` / `## 问题` / `## 剩余风险`) | 4 必备节 | output 4 节齐 | ✓ |
| 05.6 | 状态值 `通过`/`失败`/`不适用` 用中文 | 中文 | 已检查范围表中 6 行全 `通过`(中文) | ✓ |

## 汇总

- **30+ 主 assertion**(5 通用 + 6+5+6+6+6 case-specific = 30)= 30/30 ✓
- **100% pass rate**
- **runner override 透明**:每条 case_id 在 case-register.md 标 `Runner: claude` + override reason
- **byte-identical L1 rerun 保留**:本 P1b L2 不重跑 L1;以 C-5b L1 evidence + 本 P1b L2 5 case 联合作为 L2 上限
