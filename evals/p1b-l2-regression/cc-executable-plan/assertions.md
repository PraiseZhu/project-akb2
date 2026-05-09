# cc-executable-plan — P1b L2 Regression Assertions

每条 assertion 标 **case_id + 类型 + 期望 + 实测 + 通过/失败**。共 5 case × ~5 关键 assertion = 25 主 assertion;附 5 通用 assertion(11 节齐 / 不动 SKILL.md / 不解锁 automation_allowed / Chinese output / 单一 fenced)。

## 通用 assertion(对全部 5 case)

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| G1 | 11 节模板齐(10 H2 + 1 H1 Title) | 5/5 case 命中 | 5/5(grep H2 = 10 per file) | ✓ |
| G2 | SKILL.md sha256 全程不变 | `5af73fdadd91...` | 跑前跑后 byte-identical | ✓ |
| G3 | 0 case 解锁 `automation_allowed: true` | 5/5 case 不引入该字段 | grep 0 命中 | ✓ |
| G4 | 中文输出 | 5/5 case 用中文 | 全 5 case 标题 / 节名 / 字段全中文 | ✓ |
| G5 | plan 在 outputs/<case_id>.md 单一文件落盘 | 5/5 case 1 文件 1 case | ls 命中 5 文件 | ✓ |

## case 01:mandatory-docs-content-plan

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 01.1 | REVIEW_DOMAIN | docs_content | output 第 11 行 `REVIEW_DOMAIN:docs_content` | ✓ |
| 01.2 | §代码执行方向 修改范围只 1 文件 | `AKB-2.0-NAMING-CONVENTION.md` | output §代码执行方向 列出 1 文件 | ✓ |
| 01.3 | §6 文档段落合约 7 条款明示(slug 一致 / 字符集 / cc- / mcp- / hook- / 示例 / 禁忌) | 7 条款齐 | output §接口与数据合约 列 7 条款 | ✓ |
| 01.4 | 不引入 mutation 风险段落到代码 | §不修改 含"任何代码文件" | output §代码执行方向 不修改段落含"任何代码文件 / 任何 hook / 任何 settings.json" | ✓ |
| 01.5 | §测试计划 仅 grep 验证 | 5 条 grep + 0 写命令 | output §测试计划 5 条 grep | ✓ |

## case 02:mandatory-ai-infra-plan

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 02.1 | REVIEW_DOMAIN | app_code | output 第 11 行 `REVIEW_DOMAIN:app_code` | ✓ |
| 02.2 | `--strict-mode` 默认 false + 不影响 `--mode dry-run/write-proposal` 既有 mutation 边界 | 默认 off + 既有行为 100% 兼容 | output §目标与验收 + §接口与数据合约 明示 | ✓ |
| 02.3 | exit code 3 新增,不与既有 exit 0/1/2 冲突 | exit 3 是新值;reject 优先于 strict | output §接口与数据合约 + §边界与失败模式 明示 | ✓ |
| 02.4 | 不解锁 `automation_allowed: true`(strict 与 active 提升正交) | 0 命中 | output §目标与验收 + §非目标 + §边界 显式 | ✓ |
| 02.5 | 单测覆盖矩阵:strict × {dry-run, write-proposal} × {0, 1, N 条 needs_human_review} | 4-6 case + 既有 18 测试不打挂 | output §测试计划 列 6 unit case | ✓ |

## case 03:mandatory-mutation-boundary-plan

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 03.1 | REVIEW_DOMAIN | ai_infra | output 第 11 行 `REVIEW_DOMAIN:ai_infra` | ✓ |
| 03.2 | §不修改 显式列旧 AKB 全链路 | fetch / synth / translate / NLM / 简报 / cron / LaunchAgent / Project AKB/** 全列 | output §代码执行方向 不修改段含全 8 项 | ✓ |
| 03.3 | §实现步骤 集中在 staging/_review-queue.md 新建 + 1 行登记 | 1 文件新建 / 1 数据行 | output §代码执行方向 新增段 + §实现步骤 | ✓ |
| 03.4 | §边界与失败模式 含"如发现旧 fetch 仍跑 → 单独 plan,不在本 plan 范围" | 该段落明示 | output §边界与失败模式 命中 | ✓ |
| 03.5 | Project AKB/** 0 改 + 物理文件 mcp.md 不删 | §非目标 + §测试计划 verify | output §非目标 + §测试计划 第 3 条 `find Project AKB -newer ... -type f` 应空 | ✓ |

## case 04:edge-ambiguous-input-plan

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 04.1 | REVIEW_DOMAIN | other(明示等用户答 §待确认 后再判) | output `REVIEW_DOMAIN:other` | ✓ |
| 04.2 | §目标与验收 明示"目标不明,无法定 success criteria" | 该原话或等价 | output §目标与验收 含"目标不明,无法定 success criteria" | ✓ |
| 04.3 | §当前状态 明示"上下文未提供,无法盘点" | 该原话或等价 | output §当前状态 含"上下文未提供,无法盘点" | ✓ |
| 04.4 | §代码执行方向 写"待用户明示"(非编造路径) | 全 3 子段(新增 / 修改 / 不修改)均含"待用户明示" | output §代码执行方向 命中 | ✓ |
| 04.5 | §待确认 ≥ 5 条具体追问(加什么 / 哪件事 / 优先级 / 验收 / 边界) | ≥ 5 条编号 | output §待确认 列 7 条(5 必答 + 2 可选) | ✓ |

## case 05:edge-review-handoff-plan

| # | assertion | 期望 | 实测 | 通过 |
|---|---|---|---|---|
| 05.1 | REVIEW_DOMAIN | ai_infra | output `REVIEW_DOMAIN:ai_infra` | ✓ |
| 05.2 | §代码执行方向 明示 "Bash 脚本不写 Python" | bin/lint.sh + bin/lint/rules/*.sh + 不调 python/node | output §代码执行方向 明示"仅用 bash 4+ / coreutils / grep / awk / find,不调 python / node / 其它解释器" | ✓ |
| 05.3 | §实现步骤 末尾含 review handoff 段(3 类 finding 抽查) | 该段标题 + 3 项 | output §实现步骤 末尾 `### Review handoff` 段含 3 类抽查(r04 / r02 / r05) | ✓ |
| 05.4 | §测试计划 含 ≥ 4 条 reviewer 可跑命令 + 期望输出 | 4 条编号 | output §测试计划 集成段含 5 条编号对账(finding 总数 / r03 / r08 / r05 / r04) | ✓ |
| 05.5 | 不解锁 automation_allowed + 不接 cron / LaunchAgent / hook | §目标与验收 + §非目标 + §边界 三处声明 | output 三处明示 | ✓ |

## 汇总

- **30 主 assertion**(5 通用 + 5×5 case-specific)= 30/30 ✓
- **100% pass rate**
- **runner override 透明**:每条 case_id 在 case-register.md 标 `Runner: claude` + override reason
- **byte-identical L1 rerun 保留**:本 P1b L2 不重跑 L1;以 C-5b L1 evidence + 本 P1b L2 5 case 联合作为 L2 上限
