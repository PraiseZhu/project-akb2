# 知识复利 Plan 3 — Live Activation + Quality v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

日期：2026-05-17
状态：draft
负责人：Claude 执行
Supersedes: 无（新 Plan，Plan 2 v3 是基础设施层）

---

## 1. 目标链

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路，提升复杂任务的计划、审查、执行、验收效率，并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准，保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   在 Plan 2 基础设施（query-result 模板/lint/gate/index/search 路由）之上，打通对话→存储→晋升→检索的 live 闭环：用户说"存一下"自动写候选页 → 跑 lint+gate → 通过后晋升到 wiki/ → 下次查询能被发现；同时补全 P1 体验缺口（预览、去重、智能 derived_from、反向索引）和 P2 生命周期管理（触发词扩展、低质归档、操作日志）。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`save-query-result skill 自动提取正确; staging→wiki 提升不绕过质量门; 预览/去重判定准确; derived_from 智能筛选不丢源; lifecycle 命令不破坏现有页面; E2E live 链路可复现`
- Domain-specific notes：依赖 Plan 2 v3 全部代码已实装（query-result lint R23-R25 + gate 简化路径 + index 生成器 + akb-search 路由）；本计划只新增 skill 层 + 提升脚本，不改 src/akb2/lint|gate|index 既有行为

---

## 3. 成功标准（SC）

- [ ] SC-1：knowledge-ops skill 新增 `save-query-result` 子命令——用户说"存一下"等触发词后，skill 自动从对话提取 query/derived_from/confidence，写候选页到 `staging/candidate/<slug>.md`，自动跑 lint+gate 并报告结果
  - verify: `grep -c 'save-query-result' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 3 且 `grep -c '存一下' ~/.claude/rules/skill-trigger-scan.md` >= 1
- [ ] SC-2：新建 `src/akb2/promotion/promote.py`——读 staging/candidate/ 中 `gate_status: pass` 的页面，按 frontmatter slug 复制到 wiki/<template_type>/，更新 page_status=active，写 promotion log；`gate_status` 非 pass 的候选必须跳过（输出 skip 原因），不得写入 wiki/；promotion 完成后必须调用 `generate_index.py --rebuild`（不改 index 源码，仅重算）
  - verify: `python3 src/akb2/promotion/promote.py --dry-run --staging-root <tmp>/staging/candidate --wiki-root <tmp>/wiki` exit 0 且输出含"would promote"；且 `python3 -m pytest tests/test_promotion.py -k gate_fail -v` 断言 gate_status!=pass 候选不产生 wiki/ 写入
- [ ] SC-3：save-query-result skill 在写入前向用户展示 frontmatter + 正文 diff preview，并执行 slug 冲突检查（已存在则提示选择"替换/合并/取消"）
  - verify: `grep -c 'frontmatter\|diff preview\|替换.*合并.*取消\|slug 冲突' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"` >= 4；`grep -c '## Preview' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"` >= 1 且 `grep -c '## Deduplication' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"` >= 1
- [ ] SC-4：derived_from 智能筛选——save-query-result 不再全量取对话中所有 akb_search 调用，而是按"最近一个用户问句 + 该问句之后 Claude 回答引用过的源页"筛选；并写入反向索引 `data/backlinks.jsonl`（每行 `{"source_slug": "...", "referenced_by": ["query-result-slug-1", ...]}`）
  - verify: `grep -c 'backlinks' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 2 且 `python3 src/akb2/backlinks/update_backlinks.py --help` exit 0
- [ ] SC-5：knowledge-ops skill 新增 3 个 lifecycle 子命令：`update-query-result`（修订已有页内容）、`deprecate-query-result`（标记 status=deprecated 并归档到 archive/）、`expire-low-confidence`（批量归档 confidence:low 且 >30 天的页面）
  - verify: `grep -c 'update-query-result\|deprecate-query-result\|expire-low-confidence' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 3
- [ ] SC-6：`wiki/log.md` 自动追踪——save/promote/update/deprecate 任何操作后追加一行 `YYYY-MM-DD HH:MM | <action> | <slug> | <actor>`；skill 命令完成时自动写入，使用 fcntl.flock 保证并发安全
  - verify: `grep -c 'fcntl\|flock\|追加一行\|YYYY-MM-DD HH:MM' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"` >= 3；且 `grep -c 'log.md' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"` >= 2
- [ ] SC-7：E2E live 闭环测试——构造临时 vault，模拟"问→答→存→提升→重检索"全链路，断言 query-result 写入 staging、gate pass、复制到 wiki/、index 更新含该页、backlinks.jsonl 含反向引用
  - verify: `python3 -m pytest tests/test_live_compound_e2e.py -v` all passed

---

## 4. 非目标（NON_GOALS）

- 不重 plan Plan 2 v3 已有的 Step 2-5/7 代码部分（应直接 execute，本计划只新增）
- 不改 MCP `akb_search` server.py（路由仍在 skill 层；server 端路由属未来 Plan）
- 不接 LLM 语义去重（本期只做 slug + query 字符串完全匹配的去重）
- 不接向量检索/qmd（wiki 规模 < 50 页时不必要）
- 不修复 bridge mapper v1/v2 兼容（Review Trust 契约说明已无害）
- 不改 R1-R22/HC-01-HC-10 现有 lint/gate 规则
- 不自动执行 expire-low-confidence（必须用户手动触发，避免静默归档）

---

## 5. Step 拆分

| Step | 名称 | owned_paths | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------|-----------|-------------------|---------|
| 1 | save-query-result skill + 触发词 | `${WORKSPACE}/skills/knowledge-ops/SKILL.md`, `~/.claude/rules/skill-trigger-scan.md` | — | step 2 | SC-1 |
| 2 | staging→wiki 自动提升 | `src/akb2/promotion/`, `tests/test_promotion.py` | — | step 1 | SC-2 |
| 3 | save 预览 + 去重 + SKILL.md backlinks 段落 | `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（preview/dedup/derived_from筛选 段落） | step 1 | step 4 | SC-3, SC-4（SKILL.md 部分） |
| 4 | derived_from 反向索引 Python 模块 | `src/akb2/backlinks/`, `data/backlinks.jsonl`（schema 文档） | step 1 | step 3 | SC-4（代码部分） |
| 5 | lifecycle 命令 + log 追踪 | `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（lifecycle 段落） | step 1, 2 | — | SC-5, SC-6 |
| 6 | E2E live 链路验证 | `tests/test_live_compound_e2e.py` | step 1-5 | — | SC-7 |

---

## 6. 边界（修改 / 不修改）

修改：
- `${WORKSPACE}/skills/knowledge-ops/SKILL.md` — 新增 save-query-result / preview / dedup / 智能筛选 / lifecycle / log 段落
- `~/.claude/rules/skill-trigger-scan.md` — 追加触发词行
- `src/akb2/promotion/` — 新建提升脚本模块
- `src/akb2/backlinks/` — 新建反向索引脚本模块
- `data/backlinks.jsonl` — 新建反向索引数据文件（schema 文档；运行时由脚本生成）
- `tests/test_promotion.py` / `tests/test_live_compound_e2e.py` — 新增测试

不修改：
- `src/akb2/lint/akb2_lint.py`（Plan 2 v3 已覆盖）
- `src/akb2/gate/akb2_gate_check.py`（同上）
- `src/akb2/index/`（同上）
- `${WORKSPACE}/skills/akb-search/SKILL.md`（Plan 2 v3 已覆盖路由）
- `toolbox/cc-akb-search/mcp_server/server.py`
- vault `AKB-2.0-CONTENT-STANDARD.md`（已注册 query-result）
- 现有 R1-R25 lint 规则对现有 13 模板的行为
- 现有 HC-01..HC-10 对 query-result 之外页面的行为

---

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| save 触发词误触发（用户随口说"存一下别的"）| 触发后强制 preview + 询问 confidence，用户取消则不写 |
| 提升脚本误删/覆盖现有 wiki 页 | promote.py 默认 --dry-run；--apply 模式 slug 冲突直接 exit 1 不覆盖 |
| 去重判定假阴性（同问题不同表述生成两页） | 本期只做 slug 字符匹配；语义去重列入 future plan，避免错误合并 |
| 智能 derived_from 漏取源页 | 兜底机制：用户在 preview 阶段可手动追加 derived_from slug |
| backlinks.jsonl 与 wiki/ 状态不同步 | update_backlinks.py 提供 --rebuild 全量重算选项 |
| lifecycle 命令误归档活跃页 | deprecate/expire 操作前打印目标列表，用户确认后才执行 |
| log.md 并发写冲突 | 用文件锁（fcntl.flock）保证原子追加 |

---

## 8. 测试计划

```bash
WORKSPACE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code"
AKB2_ROOT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0"

# SC-1
grep -c 'save-query-result' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"
grep -c '存一下' ~/.claude/rules/skill-trigger-scan.md

# SC-2
python3 src/akb2/promotion/promote.py --dry-run --staging-root /tmp/test/staging/candidate --wiki-root /tmp/test/wiki

# SC-3
grep -c '## Preview' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"
grep -c '## Deduplication' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"

# SC-4
grep -c 'backlinks' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"
python3 src/akb2/backlinks/update_backlinks.py --help

# SC-5
grep -c 'update-query-result\|deprecate-query-result\|expire-low-confidence' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"

# SC-6
grep -c '自动追加\|log.md' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"

# SC-7
python3 -m pytest tests/test_live_compound_e2e.py -v
```

---

## 9. Assumptions

- Plan 2 v3 全部代码（Step 2-5/7）已 execute 完毕，src/akb2/lint|gate|index 已支持 query-result
- `${WORKSPACE}/skills/knowledge-ops/SKILL.md` 已存在且可扩展
- vault 已注册 query-result 模板（Plan 2 v3 Step 1 已实装）
- backlinks 数据文件位置 `data/backlinks.jsonl`（项目根下），不放 vault
- log.md 位置 `${AKB2_ROOT}/wiki/log.md`（已存在，手动维护切换到自动追加）
- `${WORKSPACE}` = `~/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/`
- `${AKB2_ROOT}` = `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0`

---

## 10. Machine-Readable: Work Items

```json gd-work-items
{
  "work_items": [
    {
      "id": "wi-1",
      "description": "在 ${WORKSPACE}/skills/knowledge-ops/SKILL.md 新增 ## save-query-result 段落：定义触发条件(存一下/save to wiki)、自动提取流程(query/derived_from/confidence)、写入 staging/candidate/ 路径、自动 lint+gate 调用；同步在 ~/.claude/rules/skill-trigger-scan.md 追加触发词行",
      "sc_refs": ["SC-1"],
      "owned_paths": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md", "~/.claude/rules/skill-trigger-scan.md"],
      "forbidden_paths": ["src/", "tests/", "data/", "src/akb2/"],
      "required_context": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md"],
      "deliverables": ["SKILL.md 新增 ## save-query-result 段落（含触发条件/提取流程/路径/lint+gate）", "skill-trigger-scan.md 新增触发词行（存一下/save to wiki）"],
      "verify": "grep -c 'save-query-result' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 3 且 grep -c '存一下' ~/.claude/rules/skill-trigger-scan.md → >= 1",
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "grep -c 'save-query-result' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 3"
      }
    },
    {
      "id": "wi-2",
      "description": "新建 src/akb2/promotion/promote.py：只读取 staging/candidate/ 中 frontmatter gate_status=pass 的页面；gate_status 非 pass 候选跳过并记录 skip 原因（不写 wiki/）；按 slug 复制到 wiki/<template_type>/，更新 page_status=active，写 promotion log；promotion 完成后调用 generate_index.py --rebuild 更新检索索引；支持 --dry-run/--apply 两模式；slug 冲突 --apply 模式 exit 1",
      "sc_refs": ["SC-2", "SC-7"],
      "owned_paths": ["src/akb2/promotion/", "tests/test_promotion.py"],
      "forbidden_paths": ["${WORKSPACE}/skills/", "~/.claude/", "src/akb2/lint/", "src/akb2/gate/", "src/akb2/index/generate_index.py"],
      "required_context": ["src/akb2/gate/akb2_gate_check.py", "src/akb2/index/generate_index.py"],
      "deliverables": ["src/akb2/promotion/promote.py（含 gate_status 检查 + index rebuild + dry-run/apply 模式）", "tests/test_promotion.py（含 gate_fail 负例断言）"],
      "verify": "python3 src/akb2/promotion/promote.py --dry-run --staging-root <tmp>/staging --wiki-root <tmp>/wiki → exit 0 且含'would promote'；python3 -m pytest tests/test_promotion.py -k gate_fail -v → gate 未过候选不产生 wiki/ 写入",
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "python3 src/akb2/promotion/promote.py --dry-run --staging-root <tmp>/staging --wiki-root <tmp>/wiki → exit 0 且输出含'would promote'；python3 -m pytest tests/test_promotion.py -k gate_fail -v → 断言 gate 未过候选不产生 wiki/ 写入"
      }
    },
    {
      "id": "wi-3",
      "description": "knowledge-ops skill 新增 ## Preview 和 ## Deduplication 段落：save 前展示 frontmatter+正文 diff；slug 冲突时提示用户选择'替换/合并/取消'，query 字符串完全匹配时提示'已存在类似条目'",
      "sc_refs": ["SC-3"],
      "owned_paths": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md"],
      "forbidden_paths": ["src/", "tests/", "data/", "~/.claude/"],
      "required_context": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md"],
      "deliverables": ["SKILL.md 新增 ## Preview 段落（含 frontmatter+diff 展示逻辑）", "SKILL.md 新增 ## Deduplication 段落（含替换/合并/取消提示）"],
      "verify": "grep -c 'frontmatter\\|diff preview\\|替换.*合并.*取消\\|slug 冲突' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 4",
      "evidence_anchor": {
        "kind": "doc_section",
        "ref": "${WORKSPACE}/skills/knowledge-ops/SKILL.md#preview"
      }
    },
    {
      "id": "wi-3b",
      "description": "knowledge-ops SKILL.md 新增 derived_from 筛选段落：定义筛选逻辑'最近一个用户问句 + 该问句之后 Claude 回答引用过的源页'；新增 backlinks 文档段落说明 backlinks.jsonl 结构和 update_backlinks.py 调用方式（step-3 owned 范围，与 wi-3 合并在 SKILL.md 同次编辑）",
      "sc_refs": ["SC-4"],
      "owned_paths": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md"],
      "forbidden_paths": ["src/", "tests/", "data/", "~/.claude/"],
      "required_context": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md"],
      "deliverables": ["SKILL.md 新增 derived_from 筛选段落（含筛选逻辑定义）", "SKILL.md 新增 backlinks 文档段落（含 backlinks.jsonl 结构说明）"],
      "verify": "grep -c 'backlinks' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 2",
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "grep -c 'backlinks' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 2"
      }
    },
    {
      "id": "wi-4",
      "description": "新建 src/akb2/backlinks/update_backlinks.py：维护 data/backlinks.jsonl（每行 {source_slug, referenced_by[]}）；支持 --rebuild 全量重算；不触及 SKILL.md（SKILL.md 段落由 wi-3b 负责）",
      "sc_refs": ["SC-4"],
      "owned_paths": ["src/akb2/backlinks/", "data/backlinks.jsonl"],
      "forbidden_paths": ["${WORKSPACE}/skills/", "~/.claude/", "src/akb2/lint/", "src/akb2/gate/", "src/akb2/index/"],
      "required_context": [],
      "deliverables": ["src/akb2/backlinks/update_backlinks.py（含 --rebuild 选项）", "data/backlinks.jsonl（schema 文档，运行时由脚本生成）"],
      "verify": "python3 src/akb2/backlinks/update_backlinks.py --help → exit 0",
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "python3 src/akb2/backlinks/update_backlinks.py --help → exit 0"
      }
    },
    {
      "id": "wi-5",
      "description": "knowledge-ops skill 新增 3 个 lifecycle 子命令：update-query-result(修订内容)、deprecate-query-result(标 status=deprecated + 移到 archive/)、expire-low-confidence(批量归档 confidence:low 且 >30 天)；所有命令操作前打印目标列表用户确认",
      "sc_refs": ["SC-5"],
      "owned_paths": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md"],
      "forbidden_paths": ["src/akb2/lint/", "src/akb2/gate/", "src/akb2/index/", "~/.claude/", "src/akb2/promotion/"],
      "required_context": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md", "src/akb2/promotion/promote.py"],
      "deliverables": ["SKILL.md lifecycle 段落（含 update/deprecate/expire 子命令定义及操作前确认流程）"],
      "verify": "grep -c 'update-query-result\\|deprecate-query-result\\|expire-low-confidence' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 3",
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "grep -c 'update-query-result\\|deprecate-query-result\\|expire-low-confidence' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 3"
      }
    },
    {
      "id": "wi-6",
      "description": "wiki/log.md 自动追踪：knowledge-ops skill 在 save/promote/update/deprecate 任何操作后自动追加一行 'YYYY-MM-DD HH:MM | <action> | <slug> | <actor>' 到 ${AKB2_ROOT}/wiki/log.md；使用 fcntl.flock 保证并发安全",
      "sc_refs": ["SC-6"],
      "owned_paths": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md"],
      "forbidden_paths": ["src/akb2/", "tests/"],
      "required_context": ["${WORKSPACE}/skills/knowledge-ops/SKILL.md", "${AKB2_ROOT}/wiki/log.md"],
      "deliverables": ["SKILL.md log 追踪段落（含 fcntl.flock 原子追加实现说明及格式定义）"],
      "verify": "grep -c 'fcntl\\|flock\\|追加一行\\|YYYY-MM-DD HH:MM' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 3 且 grep -c 'log.md' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 2",
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "grep -c '自动追加\\|log.md' ${WORKSPACE}/skills/knowledge-ops/SKILL.md → >= 2"
      }
    },
    {
      "id": "wi-7",
      "description": "新建 tests/test_live_compound_e2e.py：用 pytest tmp_path 构造临时 vault，模拟问→答→存→提升→重检索全链路；断言 staging 写入、gate pass、wiki/ 复制成功、index 更新含该页、backlinks.jsonl 反向引用正确",
      "sc_refs": ["SC-7"],
      "owned_paths": ["tests/test_live_compound_e2e.py"],
      "forbidden_paths": ["src/", "${WORKSPACE}/skills/", "~/.claude/"],
      "required_context": ["src/akb2/gate/akb2_gate_check.py", "src/akb2/index/generate_index.py", "src/akb2/promotion/promote.py", "src/akb2/backlinks/update_backlinks.py"],
      "deliverables": ["tests/test_live_compound_e2e.py（含全链路断言：staging/gate/wiki/index/backlinks）"],
      "verify": "python3 -m pytest tests/test_live_compound_e2e.py -v → all passed",
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "python3 -m pytest tests/test_live_compound_e2e.py -v → all passed"
      }
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
      "clustering_reason": {
        "type": "performance",
        "description": "save skill(改 SKILL.md+触发词)和 promotion 脚本(新建 Python 模块)修改不同文件，无数据依赖"
      },
      "alternatives_rejected": [
        {
          "option": "step-2 串行等 step-1",
          "reason": "promote.py 读 staging/candidate/ 不依赖 skill 是否激活；可独立开发独立测试"
        }
      ],
      "chosen": "step-1 step-2 并行 W1 同时交付",
      "removal_proof": "grep 'knowledge-ops\\|skills/' src/akb2/promotion/promote.py → 0；promote.py 完全不引用 skill 文件"
    },
    {
      "cluster_id": "c2",
      "step_ids": ["step-3", "step-4"],
      "clustering_reason": {
        "type": "domain",
        "description": "step-3（SKILL.md preview/dedup/筛选段落）和 step-4（src/akb2/backlinks/ + data/backlinks.jsonl）修改完全不同的文件，无数据依赖，可并行；SKILL.md 的 backlinks 文档段落已归入 step-3 owned_paths，step-4 不再触及 SKILL.md"
      },
      "alternatives_rejected": [
        {
          "option": "step-3 step-4 合并一个 step",
          "reason": "preview/dedup 和 backlinks Python 模块是不同交付物：step-3 只写 SKILL.md；step-4 只写 src/akb2/backlinks/ 和 data/；分开保持单一职责"
        },
        {
          "option": "step-4 保留 SKILL.md 筛选段落 owned_path",
          "reason": "GD review standard §6 要求 owned_paths 无重叠；两个并行 step 共有同一文件会导致写入竞争；将 SKILL.md 筛选段落归入 step-3 完全解决重叠"
        }
      ],
      "chosen": "step-3 step-4 W2 并行，step-3 独占 SKILL.md，step-4 独占 backlinks/ 和 data/",
      "removal_proof": "step-3 owned_paths = SKILL.md；step-4 owned_paths = src/akb2/backlinks/ + data/backlinks.jsonl；文件级 0 重叠（grep 'SKILL.md' step-4-owned-paths → 0）"
    },
    {
      "cluster_id": "c3",
      "step_ids": ["step-5"],
      "clustering_reason": {
        "type": "sequencing",
        "description": "lifecycle 命令需要 step-1 (save 基线) 和 step-2 (promote 脚本) 都就绪——update/deprecate 操作 wiki/ 内页面，依赖 promote 已建立的目录结构"
      },
      "alternatives_rejected": [
        {
          "option": "step-5 与 step-3/4 同 wave 并行",
          "reason": "lifecycle 命令依赖 promotion 已实装的 wiki/ slug 路径约定；step-2 未完成时 deprecate 无法定位目标页"
        }
      ],
      "chosen": "step-5 独占 W3，blocked by W1 (step-1, step-2)",
      "removal_proof": "lifecycle 命令调用 promote.py 提供的 slug→path 解析函数；step-2 未交付该函数则 step-5 无法实装"
    }
  ]
}
```

---

## 12. Machine-Readable: Step Plan Inventory

```json gd-step-plan-inventory
{
  "step_plans": [
    {
      "step_id": "step-1",
      "file": "plans/gd/2026-05-17-knowledge-compounding-live-activation/gd-step-1-save-query-result-skill.md",
      "status": "draft"
    },
    {
      "step_id": "step-2",
      "file": "plans/gd/2026-05-17-knowledge-compounding-live-activation/gd-step-2-staging-to-wiki-promotion.md",
      "status": "draft"
    },
    {
      "step_id": "step-3",
      "file": "plans/gd/2026-05-17-knowledge-compounding-live-activation/gd-step-3-save-preview-dedup.md",
      "status": "draft"
    },
    {
      "step_id": "step-4",
      "file": "plans/gd/2026-05-17-knowledge-compounding-live-activation/gd-step-4-derived-from-backlinks.md",
      "status": "draft"
    },
    {
      "step_id": "step-5",
      "file": "plans/gd/2026-05-17-knowledge-compounding-live-activation/gd-step-5-lifecycle-and-log.md",
      "status": "draft"
    },
    {
      "step_id": "step-6",
      "file": "plans/gd/2026-05-17-knowledge-compounding-live-activation/gd-step-6-e2e-live-validation.md",
      "status": "draft"
    }
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
        { "track_id": "t5", "mode": "execute", "step_ids": ["step-5"], "max_parallel": 1 }
      ],
      "blocked_by": ["w1"]
    },
    {
      "wave_id": "w4",
      "tracks": [
        { "track_id": "t6", "mode": "validate", "step_ids": ["step-6"], "max_parallel": 1 }
      ],
      "blocked_by": ["w2", "w3"]
    }
  ]
}
```
