<!-- PLAN_TEMPLATE_V3 -->

# Plan: AKB2.0 工具库 P2.1 Toolbox Lint v0(Bash 版规则集)

日期:2026-05-06
状态:ready-for-implementation
负责人:Claude Code(per cc-executable-plan skill)

## Review 对齐

REVIEW_DOMAIN:ai_infra

REVIEW_FOCUS:
- Bash 实现完整 + 不混入 Python(per 用户硬约束)
- 8 类规则覆盖完整 + finding 输出格式可被 reviewer / 未来 P2.2 eval runner 解析
- reviewer handoff 段明示要抽查的 3 类 finding(避免 reviewer 漫无目的)
- 验收 evidence 段含 4+ 条对账命令 + 期望输出(reviewer 可逐条复跑)
- automation_allowed 不打开 / 不接 cron / 不接 LaunchAgent

Domain-specific notes:
- ai_infra:runtime active = 不接;mirror backup = 不引入;trigger surface = bin/lint.sh 是 manual CLI(无 trigger);generation surface = stdout finding;state baseline = 0 文件 mutation(只读 lint);validation runtime health = 本 plan 不接 health check;stale cleanup = 不在本 plan(P2.3 周巡检 plan)

## 目标与验收

用户目标:
- 让 reviewer / Claude / Codex 能用一条命令 `bash bin/lint.sh` 扫出当前 AKB2.0 工具库 5 件套的 8 类违规,而不是手动逐个 grep
- 实现限定 Bash(用户硬约束:不写 Python,与 akb2-gate-checker 解耦)
- finding 输出可读 + reviewer 可抽样复测

成功标准:
- [ ] `AKB2.0/bin/lint.sh` 文件存在 + 可执行(`chmod +x`)
- [ ] 脚本仅用 bash 4+ / coreutils / grep / awk / find,不调 python / node / 其它解释器
- [ ] 实现 8 类规则:(1) 五件套缺失 (2) status 不一致 (3) workspace_path 不存在 (4) sha256 漂移 (5) backlink_status pending (6) active 但 L0 (7) eval stale > 30d (8) automation_allowed: true
- [ ] finding 输出格式:`<rule_id>\t<slug>\t<file>\t<reason>`(TSV,易 awk / wc -l)
- [ ] 在当前 5 工具(2 active + 3 candidate)上跑得到稳定结果(0 false positive 在 2 active)
- [ ] reviewer handoff 段在 plan 末尾(本节 §实现步骤 末尾,见下文)
- [ ] 验收 evidence 段含 ≥ 4 条对账命令 + 每条期望输出(在 §测试计划 内)
- [ ] 0 mutation(脚本只读 / 不写 / 不改任何 5 件套)
- [ ] 不接 cron / LaunchAgent / hook
- [ ] 不打开任何 `automation_allowed: true`

非目标:
- 不实现 finding manifest 写入(P2.2 eval runner plan 范围)
- 不实现 lint 自动修复 / auto-fix
- 不实现 lint 规则白名单 / 抑制 / 配置文件
- 不接 cron 周巡检(P2.3 plan)
- 不写 Python 任何代码
- 不打开 active 提升 / 不动 candidate 的 status

## 当前状态

相关事实:
- AKB2.0 当前 5 工具 5 件套 25 文件齐(per P0 状态切片 2026-05-06):
  - cc-executable-plan / cc-review:active L1
  - cc-akb-search:candidate L0 blocked(mcp 未挂载)
  - cc-local-llm / cc-skill-stocktake:candidate L0
- 5 件套字段范式:
  - frontmatter 必含 `slug` / `asset_type` / `status` / `eval_level`(eval 文件)/ `risk_level`(interface 文件)/ `workspace_version`(eval / interface)/ `backlink_status`(eval / interface)/ `last_run_at`(eval)
  - automation_allowed 当前全 false / 未设
- AKB2.0 不存在 `bin/` 目录,需新建
- bash 4+ 可用(macOS 默认 zsh 但 bash 4+ 已 brew install)

现有入口:
- 无 lint 入口(本 plan 创建)
- 既有 akb2-gate-checker(Python)是 candidate gate 用,与本 lint 正交

约束:
- 用户硬约束:不写 Python
- 不修改 5 件套 任一文件 / 不修改 SKILL.md / 不修改 settings.json
- 不接 cron / LaunchAgent / hook
- 不依赖 mcp__akb-search(本 lint 直接 grep / find)

## 代码执行方向

新增:
- `AKB2.0/bin/lint.sh`:**Bash 脚本(不写 Python)**;主入口,主循环 5 slug × 8 规则
- `AKB2.0/bin/lint/rules/` 目录:每条规则 1 个 .sh 文件(可被 lint.sh source 调用),共 8 个文件
  - `r01_quintet_missing.sh`
  - `r02_status_inconsistent.sh`
  - `r03_workspace_path_missing.sh`
  - `r04_sha256_drift.sh`
  - `r05_backlink_pending.sh`
  - `r06_active_but_l0.sh`
  - `r07_eval_stale.sh`
  - `r08_automation_allowed_true.sh`
- `AKB2.0/bin/README.md`:简短使用说明 + 规则一览表

修改:
- 无既有文件修改

不修改:
- 5 件套 任一文件(`wiki/tools/<slug>.md` / `toolbox/{tools,interfaces,evals,registry}/<slug>.md`)
- akb2-gate-checker 任何 Python 代码
- 任何 SKILL.md / settings.json / hook
- 任何 cron / LaunchAgent

## 接口与数据合约

CLI 合约:
- 命令:`bash AKB2.0/bin/lint.sh [--rules r01,r02,...] [--slugs cc-executable-plan,...]`
- 输入:
  - `--rules`(可选,默认全 8 条)— 用逗号选规则子集
  - `--slugs`(可选,默认全 5 工具)— 用逗号选工具子集
- 输出:
  - stdout:每条 finding 1 行,TSV 格式 `<rule_id>\t<slug>\t<file>\t<reason>`
  - stderr:运行 progress(每条规则跑完输出 `[r0X] checked N slugs, M findings`)
- 错误行为 / exit code:
  - 0:跑完(0 finding 或 N finding 都返回 0;lint 不因有 finding 而 fail,与 reviewer 决策解耦)
  - 1:脚本内部错误(参数错 / 文件读不到)
  - 2:usage error
- 不支持 `--strict-mode`(本 plan 不混入 akb2-gate-checker 的 exit 3 语义)

函数/模块合约:
- 每个 `r0X_*.sh` exports 1 函数:`r0X_run "$slug"`,echo finding 行(若有)到 stdout
- `lint.sh` 主循环:`for slug in "${SLUGS[@]}"; do for rule in "${RULES[@]}"; do "$rule"_run "$slug"; done; done`

数据/Schema 变更:
- 无(只读 lint,不写)

## 实现步骤

1. 准备/审计步骤
   - `which bash && bash --version` 确认 bash 4+
   - `mkdir -p AKB2.0/bin/lint/rules` 创建目录
   - `find AKB2.0/wiki/tools AKB2.0/toolbox -name "*.md" | wc -l` 确认 5 件套 25 文件齐(per P0)

2. 核心实现步骤
   - 写 `bin/lint.sh` 主入口:argparse(用 case / shift)、SLUGS 默认值、source 8 个 rule 文件、主循环、TSV 输出
   - 写 8 个 `bin/lint/rules/r0X_*.sh`:
     - r01:对每 slug,test 5 件套 5 文件存在;缺即 finding
     - r02:对每 slug,grep `^status:` 5 件套,字符串集合 cardinality > 1 即 finding
     - r03:对每 slug 的 interface 文件,grep `workspace_path:`,test -f 该路径;不存在即 finding
     - r04:对每 slug 的 interface 文件,grep `workspace_version:`,正则取 `sha256:[a-f0-9]+`,实算 SKILL.md sha256(`shasum -a 256` 取前 12 hex)对比;不一致即 finding
     - r05:对每 slug 的 interface 文件,grep `backlink_status:`,值若为 `pending` 即 finding
     - r06:对每 slug,若 5 件套任一含 `status: active`,且对应 eval 文件 `eval_level:` 不在 {L1, L2, L3} 即 finding
     - r07:对每 slug 的 eval 文件,grep `last_run_at:`,与 `date -v-30d +%Y-%m-%d` 对比;早于 30 天前即 finding
     - r08:对 5 件套 25 文件,grep `^automation_allowed: true$`;命中即 finding(本应 0 命中)
   - 写 `bin/README.md`:用法 + 8 规则简表

3. 集成步骤
   - 不接 cron / LaunchAgent
   - 不接 hook
   - 不写 manifest(P2.2 eval runner plan)
   - 不动 settings.json

4. 收尾步骤
   - `chmod +x AKB2.0/bin/lint.sh`
   - 跑 `bash AKB2.0/bin/lint.sh > /tmp/findings.txt 2>/tmp/progress.txt`
   - `wc -l /tmp/findings.txt`(预期:cc-akb-search 命中 r05 backlink_pending 0 次 / r03 workspace 0 次;若 P0 切片所示 backlink_status: linked + workspace_path 存在,则全 5 工具 finding 总数应 ≤ 几条)

### Review handoff(reviewer 应抽查的 3 类 finding)

> reviewer:本 plan 实施完成后,请抽查以下 3 类 finding,验证 lint 逻辑正确性:

1. **r04 sha256 drift**:挑 cc-executable-plan,手动跑 `shasum -a 256 ~/.codex/skills/cc-executable-plan/SKILL.md | cut -c1-12` 对比 `grep workspace_version AKB2.0/toolbox/interfaces/cc-executable-plan.md`;两者前 12 hex 一致 → r04 应 0 finding;若 lint 报 finding 而手算无漂移 → bug;若 lint 报无漂移而手算漂移 → bug
2. **r02 status inconsistent**:挑 cc-akb-search,grep `^status:` 5 件套 5 文件,人工确认 5 个 status 字符串(预期全 candidate),lint 应 0 finding;若 lint 报 finding 但人工无差异 → bug
3. **r05 backlink pending**:挑 cc-skill-stocktake,grep `^backlink_status:` 该 slug 的 interface + eval 文件,人工读值;若值是 `pending` → lint 必须 finding;若值是 `linked` → lint 必须无 finding

reviewer handoff sample size:3 类 finding × 1 slug = 3 项手动复测;预期总耗时 < 10 分钟。

## 边界与失败模式

必须处理:
- bash 4+ 不可用(macOS 默认 zsh):脚本第一行 shebang `#!/usr/bin/env bash` + 在脚本头加 `[[ ${BASH_VERSINFO[0]} -lt 4 ]] && { echo "bash 4+ required"; exit 1; }`
- shasum 命令不可用:fallback 到 `openssl dgst -sha256`(macOS 自带)
- workspace_version 字段缺失(legacy slug):r04 跳过该 slug + stderr warn
- date -v 不可用(linux 不是 BSD):本 plan 限定 macOS;若需 linux,留 r07 改 `date -d` 单独 plan

明确不处理:
- finding 自动修复(不在本 plan 范围)
- finding 写 manifest(P2.2 plan)
- 周巡检自动跑(P2.3 plan)
- 跨工具(非 5 件套范围)的 lint(留扩展)

风险与防护:
- 风险:lint 误报导致 reviewer 信任降低
  防护:r02 / r04 / r05 在初版 hardcode 当前 5 工具的实证 baseline 做 self-test(脚本顶部 `assert_baseline()` 函数,跑完主循环后回报"baseline 5 slugs 应 0 finding for r01/r03/r06/r08")
- 风险:bash 脚本字符转义错(awk regex 中的 `$1` vs bash `$1`)
  防护:awk 用 single-quote 括起(`awk -F'\t' '{print $1}'`)
- 风险:用户期望未来加 lint rule 时不需要改 lint.sh
  防护:rule 文件独立于 lint.sh;新增规则只需 `cp r08_*.sh r09_<name>.sh` + 加入 RULES 数组
- 风险:lint 跑过程中误改文件
  防护:本 plan 仅 grep / shasum / find / awk;0 写命令(no >, no Edit, no Write)

## 测试计划

单元测试:
- 无 unit test 框架(纯 bash)
- 用 `bin/lint/test/` 下 fixture 5 件套 mock 文件做手动 smoke

集成/CLI 验证(reviewer 可逐条复跑的对账命令):

1. **finding 总数对账**:
   - 命令:`bash AKB2.0/bin/lint.sh > /tmp/findings.txt 2> /tmp/progress.txt && wc -l < /tmp/findings.txt`
   - 期望输出:`<N>` 数字,且 N ≤ 5(per P0 切片,当前 5 工具应 0-5 个 finding,主要在 cc-akb-search 因 mcp 未挂载可能命中 r03)

2. **r03 workspace_path 不存在抽查**:
   - 命令:`bash AKB2.0/bin/lint.sh --rules r03 --slugs cc-akb-search`
   - 期望输出:0 行(因 cc-akb-search workspace_path 在文件系统是 mcp 配置,不是 .md 文件;r03 应跳过 mcp- prefix 类或不命中)
   - 备选期望:1 行 finding 标注 `r03\tcc-akb-search\t<file>\tworkspace_path 不存在`(若 workspace_path 字段值是真实路径而 file 不在,则 lint 报警)

3. **r08 automation_allowed: true 全库 0 命中**:
   - 命令:`bash AKB2.0/bin/lint.sh --rules r08`
   - 期望输出:0 行(per P0 守门;若有命中则系统级违规)

4. **r05 backlink pending(active 工具应 0)**:
   - 命令:`bash AKB2.0/bin/lint.sh --rules r05 --slugs cc-executable-plan,cc-review`
   - 期望输出:0 行(per P0 切片,2 active 工具 backlink_status: linked)

5. **r04 sha256 漂移(active 工具)**:
   - 命令:`bash AKB2.0/bin/lint.sh --rules r04 --slugs cc-executable-plan`
   - 期望输出:0 行(per C-5b 实证,workspace_version 已锁 sha256:5af73fdadd91 mtime:2026-05-06,当前 SKILL.md 应一致)

测试隔离:
- 0 mutation(只读 lint)
- 0 临时文件落到 wiki/ 或 toolbox/ 内(只到 /tmp/)
- 不动 5 件套任一字段 / 不动 SKILL.md / 不动 settings.json / 不动 cron / 不动 LaunchAgent

暂不执行的验证:
- 写 finding 到 manifest(P2.2 plan)
- 跑 weekly 巡检 cron(P2.3 plan)
- 与 P1c cc-local-llm 的 LLM-driven lint 互动(P2.x 后续)

## 待确认

- `bin/` 目录是否需要双向反链到 `wiki/standards/AUTOMATION-BOUNDARY.md`?默认:不(本 plan 范围只 lint v0;反链留 P2.1 standards 同步轮)
- finding TSV 格式是否需要含 timestamp 列?默认:不(reviewer 跑命令时间已知;若需写 manifest 由 P2.2 加)
- `--quiet` flag(只输出 finding 不输出 progress)是否本轮加?默认:不(初版简单)
- r07 stale 阈值 30 天是否硬编码 vs 配置?默认:硬编码 30(per AKB2.0 freshness_window_days 默认值);未来 P2.3 加 `--stale-days`

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚(8 规则 / Bash only / 0 mutation)
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚(ai_infra + 5 focus)
- [x] 新增/修改/不修改的文件范围清楚(只新建 bin/ 子树;不动 5 件套 / SKILL.md)
- [x] CLI/API/函数输出合约清楚(TSV finding + exit 0/1/2 + --rules / --slugs)
- [x] 数据迁移和旧数据兼容清楚(无 schema)
- [x] 边界情况和失败模式清楚(bash 版本 / shasum fallback / 误报防护)
- [x] 测试夹具不会污染真实数据(0 写;reviewer 5 条对账命令全只读)
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改(0 cron / 0 LaunchAgent / 0 settings.json / 0 SKILL.md)

---

> **审查提醒**: 计划完成后可手动 `/review plan` 触发 Codex 审查。不自动触发。
> **Reviewer handoff 段 + 5 条验收 evidence 命令** 见 §实现步骤 末尾 + §测试计划 集成 5 条对账。
