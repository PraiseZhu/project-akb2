# 知识复利（Knowledge Compounding）v3

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

日期：2026-05-14
状态：draft
负责人：Claude 执行

---

## 1. 目标链

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路，提升复杂任务的计划、审查、执行、验收效率，并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准，保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   在 AKB2.0 中建立知识复利机制——新增 query-result 模板、简化 gate 路径、扩展 lint 规则、自动生成 00-Index.md、对话触发存储入口、搜索路由分流，实现 query→save→discover 端到端闭环。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`query-result 模板与现有 12 模板兼容; gate HC 对非 query-result 零回归; lint R23-R25 无误报; index 生成器输出正确; 对话触发→写入 staging 闭环; 搜索路由分流; E2E 可复现`
- Domain-specific notes：vault.toml 不变；新 Python 代码在 `src/akb2/`；vault 内只增 Markdown；index 文件名 `00-Index.md`（遵循 §2.3）

---

## 2.5 Plan 前置条件与 SC 关系声明

**blocked_by**：Plan 1 Review-Chain Correctness Round 2 已 closed。
确认来源：`Project GD/reports/plan1-review-chain-correctness/closure.json`
字段验证：`round_2_status == "all findings resolved"`（已于 2026-05-16T08:22:44Z 确认）

**SC 关系注**（Plan 2 Round 2 更新，2026-05-16）：
- §3 SC-1..SC-7 为本 AKB2 计划套装的**原始内容正确性 SC**，Plan 2 内容审查的验收主体
- R2-SC-1..R2-SC-7 为 Plan 2 Round 2 补丁验证项（见 Closure 报告）
- 最终 Closure 必须同时报告两套 SC 结果，共 14 项（7 + 7）

## 3. 成功标准（SC）

- [ ] SC-1：`AKB-2.0-CONTENT-STANDARD.md` 新增 `query-result` 模板，含 `derived_from`（数组）、`query`（字符串）、`confidence`（high/medium/low）三个必填字段
  - verify: `grep -c 'query-result' "${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md"` >= 3
- [ ] SC-2：`python3 src/akb2/index/generate_index.py --dry-run --vault-root "${AKB2_ROOT}"` exit 0，输出含 `##` 分组标题
  - verify: 命令 exit 0 且 `head -20` 含 `##`
- [ ] SC-3：`akb2_lint.py` v0.3 新增 R23/R24/R25，合法 fixture exit 0，非法 fixture exit 非 0 报对应 rule
  - verify: `python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md` exit 0
- [ ] SC-4：`akb2_gate_check.py` 对 query-result 跳过 HC-03/HC-05，HC-04 枚举含 query-result
  - verify: `/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py check --akb-root tests/fixtures/gate --input staging/candidate/candidate-query-result.md --dry-run` exit 0
- [ ] SC-5：E2E 闭环测试全部通过——覆盖 lint→gate→index 管道 + save-query-result 写入 staging + 搜索路由三类查询断言
  - verify: `python3 -m pytest tests/test_compound_e2e.py -v` all passed（含 test_save_trigger + test_search_routing）
- [ ] SC-6：用户说"存一下"触发 save-query-result，自动写候选页到 staging 并跑 lint+gate
  - verify: `grep -c 'save-query-result' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md` >= 3 (plan doc defines workflow; toolbox/cc-knowledge-ops/SKILL.md is Plan 3 future target)
  - note: 触发词注册到 `~/.claude/rules/skill-trigger-scan.md` 需用户明确授权；本计划只交付计划文档，触发词激活作为授权等待项（见 §9 Assumptions）
  - **scope-of-verification note**：SC-6 是 **plan-doc structural check**——验证本计划文档自身包含 workflow 定义文本，不是验证 runtime workflow 可被触发或执行成功。**runtime effect SC**（保存→lint+gate effect 闭环 + 触发词激活）由 Plan 3 交付时配套独立 SC 验证。本 Plan 2 不为 runtime effect 背书。
- [ ] SC-7：akb-search 搜索路由——工具问题搜工具库，知识问题搜知识库，分不清两边都搜
  - verify: `grep -c '路由' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md` >= 3 且 `grep -c 'scope=toolbox' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md` >= 1 (plan doc; toolbox/cc-akb-search/SKILL.md will be created in future execution)

---

## 4. 非目标（NON_GOALS）

- 不建 MCP `akb_save` 写入工具
- 不建 LLM 语义 lint
- 不改 `akb_search` MCP server.py（路由在 skill 层）
- 不自动化 `wiki/log.md`
- 不改 L4 人工授权流程
- 不接 qmd / 向量检索
- 不新建 `staging/compound/`（复用 `staging/candidate/`）
- 不做每次对话自动搜知识库

---

## 5. Step 拆分

| Step | 名称 | owned_paths | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------|-----------|-------------------|---------|
| 1 | query-result 模板定义 | vault `AKB-2.0-CONTENT-STANDARD.md` | — | step 2 | SC-1 |
| 2 | 00-Index.md 生成器 | `src/akb2/index/`, `tests/test_index.py` | — | step 1 | SC-2 |
| 3 | gate 简化路径 | `src/akb2/gate/akb2_gate_check.py`, `tests/test_gate.py`, `tests/fixtures/gate/staging/candidate/candidate-query-result.md` | step 1 | step 4 | SC-4 |
| 4 | lint 规则扩展 | `src/akb2/lint/akb2_lint.py`, `tests/test_lint.py`, `tests/fixtures/compound/{valid,invalid-*}.md`, `tests/fixtures/compound/wiki/existing-source.md` | step 1 | step 3 | SC-3 |
| 5 | E2E 集成验证 | `tests/test_compound_e2e.py` | step 1-4, 6-7 | — | SC-5 |
| 6 | 对话触发存储（toolbox/cc-knowledge-ops） | `toolbox/cc-knowledge-ops/SKILL.md` | step 1, 3, 4 | step 7 | SC-6 |
| 7 | 搜索路由分流（toolbox/cc-akb-search） | `toolbox/cc-akb-search/SKILL.md` | — | step 6 | SC-7 |

---

## 6. 边界（修改 / 不修改）

修改：
- `src/akb2/index/` — 新建 index 生成器
- `src/akb2/gate/akb2_gate_check.py` — query-result 分派路径
- `src/akb2/lint/akb2_lint.py` — R23-R25 + guard + R16 兼容
- `tests/` — 新增测试和 fixtures（包含 `tests/fixtures/gate/staging/candidate/` 路径下的 gate fixture）
- `tests/fixtures/compound/wiki/existing-source.md` — R23 derived_from 源页 fixture
- vault `AKB-2.0-CONTENT-STANDARD.md` — 新增模板章节
- `toolbox/cc-knowledge-ops/SKILL.md` — save-query-result workflow 定义（权威源；runtime trigger activation deferred）
- `toolbox/cc-akb-search/SKILL.md` — 搜索路由（scope=toolbox/knowledge/all 三分支；权威源）

授权等待（不在本计划执行范围内）：
- `~/.claude/rules/skill-trigger-scan.md` — 触发词激活需用户明确授权；本计划只交付 skill 文件（SKILL.md），触发词行由用户在授权后手动追加；此路径在本计划期间处于只读/禁止修改状态

不修改：
- `toolbox/cc-akb-search/mcp_server/server.py`
- `config/vault.toml`
- 现有 R1-R22 对 12 模板的行为
- 现有 HC-01..HC-10 对非 query-result 的行为

---

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| query-result 幻觉固化 | confidence 标注 + derived_from 追溯 + R23 源页存在性 |
| 00-Index.md 过大 | 每页 ~50 字符，20 页 < 2KB |
| query-result 泛滥 | staging→gate 保留 + confidence:low P2 警告 |
| 简化 gate 被滥用 | 按 template_type 分派 |
| 触发词误触发 | 生成前让用户确认 confidence |
| 搜索路由判断错 | 分不清时全量搜 |

---

## 8. 测试计划

```bash
AKB2_ROOT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0"

# SC-1
grep -c 'query-result' "${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md"

# SC-2
python3 src/akb2/index/generate_index.py --dry-run --vault-root "${AKB2_ROOT}" | head -20

# SC-3
python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md
python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/invalid-missing-derived.md

# SC-4
/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py check --akb-root tests/fixtures/gate --input staging/candidate/candidate-query-result.md --dry-run

# SC-5
python3 -m pytest tests/test_compound_e2e.py -v

# SC-6（F3 分文件分 token 检查，Step 6 独立验证）
grep -c 'save-query-result' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md
# 期望: >= 3（plan 文档含 save-query-result workflow 定义；toolbox/cc-knowledge-ops/SKILL.md 是 Plan 3 future target，Plan 2 不创建不 grep）
grep -iE 'deferred|runtime trigger activation deferred|授权等待' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md
# 期望: >= 1（plan 文档含 deferred 声明）
# 触发词注册（~/.claude/rules/skill-trigger-scan.md）需用户明确授权，不在本计划测试范围内

# SC-7（F3 分文件分 token 检查，Step 7 独立验证三个 scope 分支）
grep -c 'scope=toolbox' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md    # >= 1 (plan doc)
grep -c 'scope=knowledge' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md  # >= 1 (plan doc)
grep -c 'scope=all' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md        # >= 1 (plan doc)
```

---

## 8.1 Commit Gate（SC PASS 的 git state 要求）

**本计划交付物的 SC PASS 状态在 commit 前后的语义**：

- **本计划允许 SC PASS 时改动处于 staged / uncommitted 状态**（worktree 边界自包含；用户独立决策 commit 时机）
- 但**最终交付到主分支前**，以下 deliverable **必须 commit** 才视为永久 PASS：
  - SC-1: `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md`（vault 文件，跨 worktree 共享）
  - SC-2~SC-5: `src/akb2/index/`, `tests/test_index.py`, `src/akb2/gate/akb2_gate_check.py`, `tests/test_gate.py`, `src/akb2/lint/akb2_lint.py`, `tests/test_lint.py`, `tests/fixtures/compound/`, `tests/fixtures/gate/`, `tests/test_compound_e2e.py`
  - SC-7: `toolbox/cc-akb-search/SKILL.md`
- SC-6 不需 commit（plan doc 已在 git tracking 内；workflow 定义在 plan 文档本身）
- **未 commit 前的 SC PASS 视为 ephemeral**：任何 reset/stash 会让 SC PASS 退化为 FAIL；用户主动 commit 后才稳定

**验收命令**（commit 之前可跳过；commit 之后必跑）：
```bash
# 列出 SC-1~5/7 涉及的所有 deliverable 是否都已 commit
WORKTREE_FILES=(
  "src/akb2/index/generate_index.py"
  "tests/test_index.py"
  "src/akb2/gate/akb2_gate_check.py"
  "tests/test_gate.py"
  "tests/fixtures/gate/staging/candidate/candidate-query-result.md"
  "src/akb2/lint/akb2_lint.py"
  "tests/test_lint.py"
  "tests/fixtures/compound/"
  "tests/test_compound_e2e.py"
  "toolbox/cc-akb-search/SKILL.md"
)
for f in "${WORKTREE_FILES[@]}"; do
  git log -1 --oneline -- "$f" >/dev/null 2>&1 && echo "✓ $f" || echo "✗ uncommitted: $f"
done
```

---

## 9. Assumptions

- wiki ~20 .md 页面，00-Index.md < 2KB
- Claude Code 可 Read vault Markdown
- `staging/candidate/` 是标准候选页路径
- lint 编号延续 R1-R22，新增从 R23 起
- knowledge-ops skill 已存在且可扩展
- `${WORKSPACE}` = `~/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/`（iCloud 工作区根）
- `${AKB2_ROOT}` = `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0`（vault 根）

**Wave Topology Source-of-Truth（F2）**：
Wave topology（waves/tracks 并行/串行约束）的唯一权威源是 `dispatch_map.json` 的 `waves` 字段。`planning_dispatch_log.json` 只记录 dispatch proof/log，不复制 wave topology；`wave_topology_source` 字段指向 dispatch_map.json。

**Plan 2 v3 post-Plan-2 Next Step（F6）**：
Plan 2 v3 完成并 stage 后，下一步必须用 Project GD revision=18 runner 重新跑 `/gd review plan` 生成 fresh `aggregate-final.json`；不得复用 Plan 3b R2 的旧 aggregate-final，因为旧 aggregate 的 `review_contract_hash` 来自 `commands/gd.md` revision=17 时期，contract 已变（revision=18 加入 suite-mode serial contract + task packet metadata 共同标准）。此步骤是 fresh review gate，不是执行真实 AKB2 knowledge compounding。

**Plan 3 Future Targets（Plan 2 不创建，仅声明）**：
- `docs/AKB-2.0-PAGE-TEMPLATES.md` — 独立 PAGE-TEMPLATES 参考文档，由 Plan 3 创建；已在 Step 1 owned_paths 中声明为 future target（不得 `test -f` 或 `grep` 其内容）
- `toolbox/cc-knowledge-ops/SKILL.md` — cc-knowledge-ops toolbox skill 文件，由 Plan 3 创建；已在 Step 6 owned_paths 中声明为 future target（不得 `test -f` 或 `grep` 其内容）

**R2-SC-1 验证命令（~/.claude 作用域检查）**：
计划文件中所有 `~/.claude` 引用须通过以下 heuristic，不在 action context 中：
```bash
AKB2_SUITE="plans/gd/2026-05-13-knowledge-compounding"
rg --no-heading -n '~/.claude|/Users/praise/\.claude' "$AKB2_SUITE" \
  | grep -v -iE 'non.?goal|forbidden|不写|不动|不允许|背景|do not|never|历史|被保护' \
  | grep -iE 'write|install|target|output|deliverable|dest|写入' \
  && exit 1 || echo "PASS: no forbidden action-context ~/.claude references"
```

---

## 10. Machine-Readable: Work Items

```json gd-work-items
{
  "work_items": [
    {
      "id": "wi-1",
      "description": "在 AKB-2.0-CONTENT-STANDARD.md PAGE-TEMPLATES §4 追加 query-result 模板：template_type 枚举新增 query-result，定义 derived_from(数组)/query(字符串)/confidence(high|medium|low) 三个必填字段",
      "sc_refs": ["SC-1"],
      "forbidden_paths": ["src/akb2/**", "toolbox/**", "~/.claude/**"],
      "required_context": ["plans/gd/2026-05-13-knowledge-compounding/gd-step-1-query-result-template.md"],
      "deliverables": [{ "path": "${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md", "kind": "file_modified", "must_exist": true }],
      "verify": [{ "cmd": "grep -c 'query-result' '${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md'", "expect": ">= 3" }],
      "evidence_anchor": { "kind": "command_expect", "ref": "grep -c 'query-result' AKB-2.0-CONTENT-STANDARD.md → >= 3" }
    },
    {
      "id": "wi-2",
      "description": "新建 src/akb2/index/generate_index.py，支持 --dry-run(stdout)/--apply(写 wiki/00-Index.md) 两种模式，遍历 vault wiki/ 读 frontmatter 按 template_type 分组输出 Markdown，排除 00-Index.md 自身；+ tests/test_index.py 3 个测试",
      "sc_refs": ["SC-2"],
      "forbidden_paths": ["${AKB2_ROOT}/**（除 --apply 写入的 wiki/00-Index.md）", "toolbox/**", "~/.claude/**"],
      "required_context": ["plans/gd/2026-05-13-knowledge-compounding/gd-step-2-index-generator.md"],
      "deliverables": [
        { "path": "src/akb2/index/generate_index.py", "kind": "file", "must_exist": true },
        { "path": "tests/test_index.py", "kind": "file", "must_exist": true }
      ],
      "verify": [
        { "cmd": "python3 src/akb2/index/generate_index.py --dry-run --vault-root '${AKB2_ROOT}' | head -20", "expect": "exit 0, output contains ##" },
        { "cmd": "python3 -m pytest tests/test_index.py -v", "expect": "all passed" }
      ],
      "evidence_anchor": { "kind": "command_expect", "ref": "python3 src/akb2/index/generate_index.py --dry-run --vault-root $AKB2_ROOT → exit 0" }
    },
    {
      "id": "wi-3",
      "description": "修改 akb2_gate_check.py：HC-04 枚举加 query-result；query-result 跳过 HC-03+HC-05；HC-07 加 derived_from 替代条件 + 2 个测试 + 1 个 fixture",
      "sc_refs": ["SC-4"],
      "forbidden_paths": ["src/akb2/lint/**", "toolbox/**", "~/.claude/**"],
      "required_context": ["plans/gd/2026-05-13-knowledge-compounding/gd-step-3-gate-simplification.md"],
      "deliverables": [
        { "path": "src/akb2/gate/akb2_gate_check.py", "kind": "file_modified", "must_exist": true },
        { "path": "tests/fixtures/gate/staging/candidate/candidate-query-result.md", "kind": "file", "must_exist": true }
      ],
      "verify": [{ "cmd": "/Users/praise/.pyenv/versions/3.13.12/bin/python3 src/akb2/gate/akb2_gate_check.py check --akb-root tests/fixtures/gate --input staging/candidate/candidate-query-result.md --dry-run", "expect": "exit 0" }],
      "evidence_anchor": { "kind": "command_expect", "ref": "akb2_gate_check.py check --akb-root tests/fixtures/gate --input staging/candidate/candidate-query-result.md --dry-run → exit 0" }
    },
    {
      "id": "wi-4",
      "description": "修改 akb2_lint.py：R6/R7/R11/R14 头部加 query-result guard(4处)；新增 R23/R24/R25；R16 加 query-result→canonical_dir 映射避免 TEMPLATE-DIRECTORY-MISMATCH；支持单文件位置参数；+ 4 个 fixture + 4 个测试",
      "sc_refs": ["SC-3"],
      "forbidden_paths": ["src/akb2/gate/**", "toolbox/**", "~/.claude/**"],
      "required_context": ["plans/gd/2026-05-13-knowledge-compounding/gd-step-4-lint-extension.md"],
      "deliverables": [
        { "path": "src/akb2/lint/akb2_lint.py", "kind": "file_modified", "must_exist": true },
        { "path": "tests/fixtures/compound/valid-query-result.md", "kind": "file", "must_exist": true }
      ],
      "verify": [
        { "cmd": "python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/valid-query-result.md", "expect": "exit 0, no TEMPLATE-DIRECTORY-MISMATCH" },
        { "cmd": "python3 src/akb2/lint/akb2_lint.py --version v0.3 tests/fixtures/compound/invalid-missing-derived.md", "expect": "exit nonzero, R23 in output" }
      ],
      "evidence_anchor": { "kind": "command_expect", "ref": "python3 src/akb2/lint/akb2_lint.py --version v0.3 valid-query-result.md → exit 0" }
    },
    {
      "id": "wi-5",
      "description": "新建 tests/test_compound_e2e.py：临时 vault 验证 lint→gate→index 端到端闭环（包含 save-trigger 和 search-routing 断言）",
      "sc_refs": ["SC-5"],
      "forbidden_paths": ["src/akb2/**（只调用不改）", "${AKB2_ROOT}/**", "~/.claude/**"],
      "required_context": [
        "plans/gd/2026-05-13-knowledge-compounding/gd-step-5-e2e-validation.md",
        "plans/gd/2026-05-13-knowledge-compounding/gd-master-knowledge-compounding-v3.md"
      ],
      "deliverables": [{ "path": "tests/test_compound_e2e.py", "kind": "file", "must_exist": true }],
      "verify": [{ "cmd": "python3 -m pytest tests/test_compound_e2e.py -v", "expect": "all passed (incl. test_save_trigger + test_search_routing)" }],
      "evidence_anchor": { "kind": "command_expect", "ref": "python3 -m pytest tests/test_compound_e2e.py -v → all passed" }
    },
    {
      "id": "wi-6",
      "description": "在 toolbox/cc-knowledge-ops/SKILL.md 定义 save-query-result workflow（写 staging/candidate/→跑 lint+gate）；runtime trigger activation deferred，需用户授权",
      "sc_refs": ["SC-6"],
      "forbidden_paths": ["~/.claude/**", "${AKB2_ROOT}/**", "src/akb2/**"],
      "required_context": ["plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md"],
      "deliverables": [{ "path": "toolbox/cc-knowledge-ops/SKILL.md", "kind": "future_target", "must_exist": false, "note": "Plan 3 future target; Plan 2 does not create this file" }],
      "verify": [
        { "cmd": "grep -c 'save-query-result' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md", "expect": ">= 3 (plan doc; toolbox/cc-knowledge-ops/SKILL.md is Plan 3 future target)" },
        { "cmd": "grep -iE 'deferred|runtime trigger activation deferred|授权等待' plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md", "expect": ">= 1 (deferred declaration in plan doc)" }
      ],
      "evidence_anchor": { "kind": "plan_doc_section", "ref": "plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md#save-query-result workflow" }
    },
    {
      "id": "wi-7",
      "description": "修改 toolbox/cc-akb-search/SKILL.md 增加搜索路由：scope=toolbox（工具信号词）→工具库，scope=knowledge（知识信号词）→知识库，scope=all（无信号词）→全量，结果标注 [知识库]/[工具库]",
      "sc_refs": ["SC-7"],
      "forbidden_paths": ["~/.claude/**", "toolbox/cc-knowledge-ops/**", "${AKB2_ROOT}/**", "src/akb2/**"],
      "required_context": ["plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md"],
      "deliverables": [{ "path": "toolbox/cc-akb-search/SKILL.md", "kind": "file_modified", "must_exist": true }],
      "verify": [
        { "cmd": "grep -c 'scope=toolbox' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md", "expect": ">= 1 (plan doc)" },
        { "cmd": "grep -c 'scope=knowledge' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md", "expect": ">= 1 (plan doc)" },
        { "cmd": "grep -c 'scope=all' plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md", "expect": ">= 1 (plan doc)" }
      ],
      "evidence_anchor": { "kind": "doc_section", "ref": "toolbox/cc-akb-search/SKILL.md#搜索路由" }
    }
  ]
}
```

---

## 11. Machine-Readable: Step Clusters

```json gd-step-clusters
{
  "clusters": [
    {
      "cluster_id": "c1",
      "step_ids": ["step-1", "step-2"],
      "clustering_reason": { "type": "performance", "description": "模板定义和 index 生成器无数据依赖，并行" },
      "alternatives_rejected": [{ "option": "step-2 串行等 step-1", "reason": "generate_index.py 只读 title/tldr/tags/page_status，不依赖 derived_from/query/confidence" }],
      "chosen": "step-1 step-2 并行",
      "removal_proof": "grep -rn 'derived_from|confidence|query' src/akb2/index/ → 0 hits"
    },
    {
      "cluster_id": "c2",
      "step_ids": ["step-3", "step-4"],
      "clustering_reason": { "type": "dependency", "description": "gate 和 lint 都依赖 step-1 的 schema，但修改独立文件" },
      "alternatives_rejected": [{ "option": "合并一个 step", "reason": "akb2_gate_check.py 和 akb2_lint.py 独立文件，0 行交叉" }],
      "chosen": "step-3 step-4 并行，step-1 后启动",
      "removal_proof": "grep 'import.*akb2_lint' akb2_gate_check.py → 0"
    },
    {
      "cluster_id": "c3",
      "step_ids": ["step-6", "step-7"],
      "clustering_reason": { "type": "performance", "description": "修改不同 skill 文件，无交叉" },
      "alternatives_rejected": [{ "option": "串行执行（step-6 后跑 step-7）", "reason": "knowledge-ops 和 akb-search 是独立 skill 目录" }],
      "chosen": "step-6 step-7 并行",
      "removal_proof": "step-6 改 toolbox/cc-knowledge-ops/SKILL.md（Plan 3 future target）；step-7 改 toolbox/cc-akb-search/SKILL.md；两者 owned_path 不同，可并行"
    }
  ]
}
```

---

## 12. Machine-Readable: Step Plan Inventory

```json gd-step-plan-inventory
{
  "step_plans": [
    { "step_id": "step-1", "file": "plans/gd/2026-05-13-knowledge-compounding/gd-step-1-query-result-template.md", "status": "draft" },
    { "step_id": "step-2", "file": "plans/gd/2026-05-13-knowledge-compounding/gd-step-2-index-generator.md", "status": "draft" },
    { "step_id": "step-3", "file": "plans/gd/2026-05-13-knowledge-compounding/gd-step-3-gate-simplification.md", "status": "draft" },
    { "step_id": "step-4", "file": "plans/gd/2026-05-13-knowledge-compounding/gd-step-4-lint-extension.md", "status": "draft" },
    { "step_id": "step-5", "file": "plans/gd/2026-05-13-knowledge-compounding/gd-step-5-e2e-validation.md", "status": "draft" },
    { "step_id": "step-6", "file": "plans/gd/2026-05-13-knowledge-compounding/gd-step-6-save-trigger.md", "status": "draft" },
    { "step_id": "step-7", "file": "plans/gd/2026-05-13-knowledge-compounding/gd-step-7-search-routing.md", "status": "draft" }
  ]
}
```

---

## 13. Machine-Readable: Wave Matrix

```json gd-wave-matrix
{
  "waves": [
    {
      "wave_id": "w1",
      "tracks": [
        { "track_id": "t1", "mode": "execute", "step_ids": ["step-1"], "max_parallel": 2 },
        { "track_id": "t2", "mode": "execute", "step_ids": ["step-2"], "max_parallel": 2 }
      ],
      "blocked_by": []
    },
    {
      "wave_id": "w2",
      "tracks": [
        { "track_id": "t3", "mode": "execute", "step_ids": ["step-3"], "max_parallel": 2 },
        { "track_id": "t4", "mode": "execute", "step_ids": ["step-4"], "max_parallel": 2 }
      ],
      "blocked_by": ["w1"]
    },
    {
      "wave_id": "w3",
      "tracks": [
        { "track_id": "t6", "mode": "execute", "step_ids": ["step-6"], "max_parallel": 2 },
        { "track_id": "t7", "mode": "execute", "step_ids": ["step-7"], "max_parallel": 2 }
      ],
      "blocked_by": ["w2"]
    },
    {
      "wave_id": "w4",
      "tracks": [
        { "track_id": "t5", "mode": "validate", "step_ids": ["step-5"], "max_parallel": 1 }
      ],
      "blocked_by": ["w3"]
    }
  ]
}
```
