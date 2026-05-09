<!-- PLAN_TEMPLATE_V3 -->

# Plan: akb2-gate-checker 增加 --strict-mode 旗标

日期:2026-05-06
状态:ready-for-implementation
负责人:Claude Code(per cc-executable-plan skill)

## Review 对齐

REVIEW_DOMAIN:app_code

REVIEW_FOCUS:
- `--strict-mode` 与 `--mode {dry-run,write-proposal}` 的正交语义清晰(strict 不改 mode 的 mutation 边界)
- exit code 3 的语义不与现有 exit 0/1/2 冲突
- stderr `[STRICT-MODE]` 输出对调用方(reviewer / cron / CI)是否清晰
- 单测覆盖:dry-run + strict 触发 / write-proposal + strict 触发 / 无 needs_human_review 时不触发 / 多条 needs_human_review 计数
- 不打开 automation_allowed(本旗标只影响 exit code,不解锁 active gate)

Domain-specific notes:
- app_code:changed files = `__main__.py` + 1 unit test 文件;API contracts = CLI flag 新增;data schema = 无;state concurrency = 无(单进程 CLI);tests = 4-6 新增 case;security = 无;performance = O(N) 已存在,strict 只改判定;deployment = 无 cron / 无 daemon 改

## 目标与验收

用户目标:
- 当 reviewer 想"任何 needs_human_review 不能静默通过"时,有一个明确旗标启用 hard exit + 显著告警,而不是默默 exit 0
- 默认行为 100% 不变(向后兼容)

成功标准:
- [ ] CLI 新增 `--strict-mode` 旗标(默认 false / 不启用)
- [ ] 启用 strict + 任一 record `proposed_gate_decision: needs_human_review` → exit 3
- [ ] 启用 strict + 0 needs_human_review → exit 0(行为与不开 strict 一致)
- [ ] 启用 strict + 任一 needs_human_review → stderr 输出 `[STRICT-MODE]` 前缀的告警(每条 record 1 行)
- [ ] 不启用 strict → 行为 100% 与 C-3 baseline 一致(包括 exit code 0/1/2 全部不变)
- [ ] 4-6 新增单测全过(覆盖矩阵:strict × {dry-run, write-proposal} × {0, 1, N 条 needs_human_review})
- [ ] 既有 18 unit tests 仍 100% 通过
- [ ] `--help` 文本显示 `--strict-mode` 行 + 一句解释
- [ ] 不修改 candidate 文件物理内容(strict 不引入新 mutation)
- [ ] `automation_allowed: true` 全库仍 0(本旗标与 active 提升正交)

非目标:
- 不让 strict 自动写 needs_human_review → needs_repair / archived(那是 reviewer 决策,不是 CLI)
- 不引入新 proposed_gate_decision 枚举值
- 不接 cron / 不接 LaunchAgent / 不接 hook
- 不解锁 active 任何工具
- 不改 `--mode dry-run` vs `--mode write-proposal` 的现有 mutation 边界

## 当前状态

相关事实:
- `Codex/ai-infra/akb2-gate-checker/akb2_gate_check/__main__.py` 已 active(C-3 APPROVED)
- 现有 CLI:`--mode {dry-run,write-proposal}`,默认 `dry-run`
- 现有 exit code:0(全 pass)/ 1(任一 reject)/ 2(usage error / 路径错误)
- 18 unit tests 在 `tests/test_gate_check.py` 全过
- proposed_gate_decision 枚举值:`active` / `candidate` / `needs_repair` / `needs_human_review` / `deprecated` / `archived`(6 类)
- needs_human_review 当前的 default 行为:写入 proposal,exit 0(与 active 一视同仁)

现有入口:
- CLI:`python -m akb2_gate_check --mode dry-run` / `... --mode write-proposal`
- 模块:`akb2_gate_check/__main__.py:main()`(argparse)
- 测试:`tests/test_gate_check.py`(pytest)

约束:
- 不修改 `~/.codex/skills/**` / 不修改 wiki/ / 不修改 toolbox/
- 不接任何旧 AKB 链路
- 不改 candidate 文件 schema
- 不引入新依赖(stdlib only,与现有一致)
- exit code 3 必须新增,不与现有 exit 0/1/2 冲突

## 代码执行方向

新增:
- `tests/test_gate_check_strict_mode.py`:4-6 新单测(strict × mode × 触发条件矩阵)

修改:
- `Codex/ai-infra/akb2-gate-checker/akb2_gate_check/__main__.py`:
  - argparse 增加 `--strict-mode` flag(action="store_true",default=False)
  - 在 main 末尾(返回 exit code 之前),strict_mode=True 时遍历 results,统计 needs_human_review 条数 N
  - N > 0:对每条 record 输出 `[STRICT-MODE] needs_human_review: <slug or path>` 到 stderr,设 exit code = 3
  - N = 0 + strict:exit code 维持原始(0 或 1)
  - N > 0 + 不开 strict:exit code 维持原始(向后兼容)
- `--help` 文本由 argparse 自动渲染,无需手写

不修改:
- `proposed_gate_decision` 枚举集合
- candidate 文件实际写入逻辑(不论 strict 与否,write-proposal mode 都会写;dry-run 都不写)
- exit code 0/1/2 既有语义
- 任何其它 ai-infra 模块
- 任何 SKILL.md / wiki / toolbox / hook / settings.json

## 接口与数据合约

CLI 合约(变更):
- 命令:`python -m akb2_gate_check --mode {dry-run,write-proposal} [--strict-mode]`
- 输入:
  - 既有 `--mode`(必填)
  - 新增 `--strict-mode`(可选,默认 False)
- 输出:
  - stdout:既有 finding 输出格式不变
  - stderr:strict + 命中 needs_human_review 时,每条 record 1 行 `[STRICT-MODE] needs_human_review: <slug>`(slug 取自 record;若无 slug 用 path)
- 错误行为 / exit code:
  - 0:全 pass(无 reject 且无 strict 触发)
  - 1:任一 reject(既有,与 strict 正交;若同时触发 reject + strict needs_human_review,exit 1 优先 — 因为 reject 是更强的 fail)
  - 2:usage error(既有,argparse 自动)
  - **3(新)**:strict 启用 + 任一 needs_human_review

函数/模块合约:
- `main(argv: list[str] | None = None) -> int`:返回值新增 3 一类(needs_human_review under strict)
- 内部新增辅助函数(可选):`_count_needs_human_review(results: list[dict]) -> int`

数据/Schema 变更:
- 无 schema 变更
- candidate 文件结构 / proposed_gate_decision 枚举不动
- 旧数据兼容:strict 默认 off,既有调用方(包括 dry-run / write-proposal)100% 行为不变

## 实现步骤

1. 准备/审计步骤
   - Read `__main__.py` 确认 argparse 当前结构 + main 返回路径
   - Read `tests/test_gate_check.py` 确认 18 既有测试模式(fixture 风格 / assert 习惯)
   - 确认 dry-run worktree clean(`git status` 空 / iCloud 同步完)

2. 核心实现步骤
   - 在 argparse 中加 `parser.add_argument("--strict-mode", action="store_true", default=False, help="...")`
   - 在 main 末尾(收集 results 之后,return 之前):
     ```python
     if args.strict_mode:
         nhr = [r for r in results if r.get("proposed_gate_decision") == "needs_human_review"]
         if nhr:
             for r in nhr:
                 sys.stderr.write(f"[STRICT-MODE] needs_human_review: {r.get('slug') or r.get('path', '<unknown>')}\n")
             # strict 触发 exit 3,但若已经因 reject 决定 exit 1,保留 1
             if exit_code == 0:
                 exit_code = 3
     return exit_code
     ```
   - 不改既有 reject / pass 判定路径

3. 集成步骤
   - 无 cron / LaunchAgent / hook 接入
   - 不更新 README.md(本 plan 范围只是 flag 新增,文档另行 PR)

4. 收尾步骤
   - 跑全 unit tests:`cd Codex/ai-infra/akb2-gate-checker && python -m pytest tests/ -v` 应 22-24/22-24 全过
   - 跑 integration:对 P0 状态切片 5 工具跑 `python -m akb2_gate_check --mode dry-run --strict-mode`,观察:
     - 当前 5 工具 0 needs_human_review → exit 0(strict 不触发)
   - 不写新 candidate 文件(dry-run 模式;write-proposal mode 留 reviewer 单独跑)

## 边界与失败模式

必须处理:
- strict + write-proposal:flag 不影响 candidate 写入(写仍照常,但 exit 3 让 reviewer 注意)
- strict + 0 needs_human_review:exit 0(向后兼容)
- strict + 同时有 reject 和 needs_human_review:exit 1(reject 优先,因为更强)
- record 无 slug 字段:fallback 到 path 或 `<unknown>`
- argparse `--help` 必须显示 `--strict-mode`

明确不处理:
- strict 自动写 status 变更(reviewer 决策,本旗标只 surface)
- exit 4-9 等其它新 code(未来 PR)
- needs_human_review 多分桶细分(bucket 1/2/3)留 P2.2 lint plan
- `--strict-mode` 配置文件读取(本旗标只 CLI flag)

风险与防护:
- 风险:exit 3 在 cron 调用方被误判为成功(若调用方只检查 exit 0/non-0)
  防护:本 plan 不接 cron;若未来接,需在调用方明示 `0 or 3` 是 acceptable / `0 only` 是 strict
- 风险:既有调用方已经依赖某个未文档化的 exit code(如 4)
  防护:`grep -rn "exit" __main__.py` 确认 exit 路径只 0/1/2;3 是新值
- 风险:stderr 输出污染 cron mail
  防护:strict 默认 off,cron 调用方按需开;开则 mail 中含 [STRICT-MODE] 前缀显著
- 风险:既有 18 测试因 main signature 变化打挂
  防护:`--strict-mode` 默认 False,既有测试不传该 flag 应 100% 不变

## 测试计划

单元测试:
- 文件:`tests/test_gate_check_strict_mode.py`
- 关键 case:
  1. `test_strict_off_baseline`:不传 --strict-mode,1 条 needs_human_review record,exit 0(向后兼容)
  2. `test_strict_on_no_nhr`:--strict-mode + 0 needs_human_review,exit 0
  3. `test_strict_on_one_nhr_dry_run`:--strict-mode --mode dry-run + 1 条 needs_human_review,exit 3 + stderr 含 `[STRICT-MODE]` 1 行
  4. `test_strict_on_multi_nhr`:--strict-mode + 3 条 needs_human_review,exit 3 + stderr 3 行
  5. `test_strict_on_with_reject`:--strict-mode + 1 reject + 1 needs_human_review,exit 1(reject 优先)
  6. `test_strict_on_write_proposal`:--strict-mode --mode write-proposal + 1 needs_human_review,exit 3 + candidate 文件已写(write 行为不变)

集成/CLI 验证:
- `cd Codex/ai-infra/akb2-gate-checker && python -m akb2_gate_check --mode dry-run --strict-mode`
  期望:exit 0(当前 5 工具 0 needs_human_review)
- `cd Codex/ai-infra/akb2-gate-checker && python -m pytest tests/ -v`
  期望:全过(既有 18 + 新增 ~6 = 24)
- `python -m akb2_gate_check --help | grep strict-mode`
  期望:1 行命中

测试隔离:
- 单测用 tmp_path fixture(pytest 自带),不动 wiki/ / toolbox/ / candidate 文件
- mock results 用 dict 列表,不读真实 5 件套
- 不触碰 ~/.codex/skills/

暂不执行的验证:
- production cron 接入(本 plan 不接)
- 与 P2.1 lint v0 的联动测试(P2.1 单独 plan)

## 待确认

- exit code 3 是否需要在 README.md 新增一节?默认:本 plan 不接 README,留独立 docs PR
- `[STRICT-MODE]` 前缀是否需要可配置(如 `--strict-prefix`)?默认:不可配置,硬编码 `[STRICT-MODE]`
- needs_human_review 多条时,exit 3 是否需要附加额外 exit code(如 record 数 N → exit 3 + N % 256)?默认:不附加,exit 3 表"任一触发"

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚(strict off 100% 兼容)
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚(app_code + 5 focus)
- [x] 新增/修改/不修改的文件范围清楚(1 src + 1 test;不动 SKILL.md / wiki / toolbox)
- [x] CLI/API/函数输出合约清楚(新 flag + exit 3 新 + stderr 格式)
- [x] 数据迁移和旧数据兼容清楚(无 schema;flag default off)
- [x] 边界情况和失败模式清楚(strict + reject 优先;0 nhr 不触发;help 必须显示)
- [x] 测试夹具不会污染真实数据(tmp_path + mock results)
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改(无 cron / 无 daemon / 无 secret / 无 settings.json 改)

---

> **审查提醒**: 计划完成后可手动 `/review plan` 触发 Codex 审查。不自动触发。
