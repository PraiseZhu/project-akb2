# AKB 2.0 Phase C-3 Gate Check (L3 提议层)

> **严格 L3 提议层** — 静态扫描 AKB 2.0 candidate,输出 INTAKE-GATE 6 枚举的 `proposed_gate_decision` + `gate_reason_proposal` 写到 `manifests/ingest/<YYYY-MM-DD>.jsonl`。
>
> **不做的事**(永远):
> - 不创建 wiki / toolbox active 内容
> - 不修改 candidate / wiki / toolbox / 00-Index.md
> - 不调用 LLM
> - 不接旧 fetch / synth / translate / NLM / cron
> - 不实现 L4 candidate → active 提升(必须由 reviewer 在独立授权 plan 中执行)

---

## 安装

```bash
cd "Project AKB2/src/akb2/gate"
python3 -m pip install -r requirements.txt
```

依赖:
- Python 3.9+
- PyYAML >= 6.0(frontmatter parser — 微调 1)

---

## 使用

### dry-run(默认 — 只打印,不写文件)

```bash
python3 akb2_gate_check.py check \
  --akb-root "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0" \
  --input "staging/candidate/phase-b-validation" \
  --date 2026-05-04 \
  --dry-run
```

### 显式写 manifest(双 flag 授权)

```bash
python3 akb2_gate_check.py check \
  --akb-root "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0" \
  --input "staging/candidate/phase-b-validation" \
  --date 2026-05-04 \
  --write-manifest \
  --allow-l3-manifest-write
```

输出 → `<AKB2.0>/manifests/ingest/2026-05-04.jsonl`(每行一条 candidate 的 L3 proposal)。

---

## 退出码

| 码 | 含义 |
|---|---|
| `0` | 扫描成功(允许存在不合格 candidate)|
| `1` | 参数 / 路径 / frontmatter 解析错误 |
| `2` | 越权写入风险 / manifest 写失败(扫描或写入期间 wiki/toolbox/staging/00-Index.md mtime 被改)|
| `3` | 活锁拒绝(`.akb-runtime/akb-write.lock` 被活跃 PID 持有)|

---

## 10 条硬检查 → 上游标准对照表(微调 3)

> 每条 hard check 都映射到 AKB 2.0 已 ready 标准的具体规则源。

| Hard Check | 含义 | 上游标准源 |
|---|---|---|
| `HC-01-CANDIDATE-ONLY` | 文件必须在 `staging/candidate/` 下 | INTAKE-GATE §1 不变量 1("所有入口先进 candidate")+ AUTOMATION-BOUNDARY §11.1 "候选只允许在 staging/" |
| `HC-02-PAGE-STATUS` | 必须 `page_status: candidate`(容忍旧 `status: candidate`,标 P3)| CONTENT-STANDARD §13 命名空间分离(page_status vs document_status)+ H6 backlog(内容页 status → page_status 迁移)|
| `HC-03-SOURCE-TYPE` | 必须在 SOURCE-TYPE-POLICY 12 枚举(paper/docs/course/research/blog/video/design/work-note/tool/forum/reddit/news;Phase B 临时类 phase-b-sample-validation 允许)| SOURCE-TYPE-POLICY `source_type` 12 枚举 |
| `HC-04-TEMPLATE-TYPE` | 必须在 PAGE-TEMPLATES §4 12 active 枚举(concept/source/entity/synthesis/project/design/tool-understanding/tool-card/workflow/prompt/checklist/agent-procedure)| PAGE-TEMPLATES §4 12 类 active + 不变量 1 |
| `HC-05-EVIDENCE` | 必填 ≥1 条 `evidence_paths` / `evidence` / `source_refs` | INTAKE-GATE §4 硬指标 #1 来源可追溯 + PAGE-TEMPLATES §3 evidence 字段 |
| `HC-06-FUTURE-QUESTIONS` | 必填 2-3 条具体 future_questions | **INTAKE-GATE §4 硬指标 #3** + CONTENT-STANDARD §3 第 6 条 + PAGE-TEMPLATES §3.2.1 第 5 + LINT-RULES §11 `GATE-MISSING-FUTURE-QUESTIONS` (P1) |
| `HC-07-GRAPH-LINK` | 至少 1 个 12 `related_*` 字段非空 OR `proposed_graph_nodes` 非空(L4 必须 materialize)| **INTAKE-GATE §4 硬指标 #4** + GRAPH-LINKING §9.3 12 字段 + LINT-RULES §11 `GRAPH-NO-RELATED-LINK`(P1)+ phase-b-sample-register `proposed_graph_nodes` 边界声明 |
| `HC-08-BODY-STRUCTURE` | 正文必须有 `## TLDR`;source 模板必须有 `## 关键发现`(H7 双轨)| **PAGE-TEMPLATES §3.2.1 第 1 条** + §4.2 source 正文结构 + **PAGE-TEMPLATES §4.2 H7 双轨规则**(frontmatter `key_findings` array 必填 + 正文 `## 关键发现` 章节是配套展开,不互相替代)+ LINT-RULES `SOURCE-KEY-FINDINGS-MISSING-PROSE` (P2) |
| `HC-09-NO-AUTO-ACTIVE` | candidate frontmatter `gate_decision` 必须 null;不允许预填 `active` | **INTAKE-GATE 不变量 3 "没有自动 active"** + QUERY-WRITEBACK §5.1 candidate frontmatter `gate_decision: null` + LINT-RULES `GATE-MISSING-DECISION` 反向(active 直写 = P0)|
| `HC-10-TOOLBOX-HANDOFF` | 工具类 candidate(template_type ∈ {tool-card, tool-understanding})自动转交 TOOL-INTAKE-GATE | TOOLBOX-CONTRACT § 工具库主线;TOOL-INTAKE-GATE 接收 |

---

## 6 决策矩阵 → INTAKE-GATE §3 6 gate_decision

| 触发条件 | proposed_gate_decision | decision_confidence | 推荐 reviewer 动作 |
|---|---|---|---|
| 9 条 critical hard checks 全过(HC-01~09)+ graph_readiness ∈ {real_link, proposed_node_only} | **active** | high(real_link)/ medium(proposed_node_only) | reviewer 复核 L4 授权;若 proposed_node_only 必须先 materialize_graph_link |
| 9 条 critical hard checks 部分失败(structural / field 缺失但内容有价值)| **needs_repair** | medium | reviewer 看 repair_actions 决定补字段后重审 |
| `template_type ∈ {tool-card, tool-understanding}` | **needs_human_review** | medium | 转交 TOOL-INTAKE-GATE(独立工具库门控)|
| `source_type ∈ {forum, reddit}` | **needs_human_review** | low | strict_gate(SOURCE-TYPE-POLICY);reviewer 验高价值 + 可追溯 |
| (本工具 v1 不主动判)| **brief_only** | — | 由 reviewer 显式标记(时效内容)|
| (本工具 v1 不主动判)| **archive** | — | 由 reviewer 显式标记(归档无新价值)|
| (本工具 v1 不主动判)| **reject** | — | 由 reviewer 显式标记(无源 / 风险源)|

> **brief_only / archive / reject 不在 v1 自动判定范围** — 它们涉及主观价值判断 / 时效性 / 风险评估,工具不替代 reviewer。

---

## Manifest schema(微调 5:双层)

每行 JSONL 同时包含两层字段:

### 全局层(承接 [[AKB-2.0-AUTOMATION-BOUNDARY]] §6.3 11 字段最小集)

- `manifest_type: ingest`(承接 §6.2 8 类 manifest 路径)
- `operation_id` — 唯一 ID
- `actor: akb_gate_check`
- `started_at` / `finished_at` — ISO timestamp
- `source` — 触发来源描述
- `reason` — 人类可读原因
- `affected_files: [候选路径]`
- `decision_refs: []`(L3 不引 reviewer decision — 那是 L4)
- `outcome: proposed`
- `rollback: rolled_back: false (read-only L3 proposal)`

### C-3 子 schema(akb-gate-proposal-v1)

- `schema_version: akb-gate-proposal-v1`
- `proposal_level: L3`
- `operation: gate_check_proposal`
- `candidate_path` / `source_type` / `template_type` / `page_status`
- `proposed_gate_decision` — INTAKE-GATE §3 6 枚举之一
- `proposed_lifecycle_status` — LIFECYCLE §3 mapping
- `decision_confidence: high | medium | low`
- `hard_checks: [...]` — 10 条硬检查每条 `{rule, pass, detail}`
- `gate_reason_proposal` / `proposed_gate_reason` — 拟门控理由
- `repair_actions: [...]`
- `graph_readiness: real_link | proposed_node_only | none`
- `l4_requirements: [materialize_graph_link, ...]`
- `reviewer_required: true`
- `forbidden_active_write_guard: true`
- `not_active_promotion: true`

---

## Lock 协议(微调 4:承接 AUTOMATION-BOUNDARY §5.4)

写 manifest 时使用 `<AKB2.0>/.akb-runtime/akb-write.lock`(承接 §5.2 lock 路径)。

| 状态 | 行为 |
|---|---|
| Lock 不存在 | 创建 lock(写入 `pid` + `operation_id` + `started_at` + `heartbeat_at`)|
| Lock 存在 + holder PID 活跃(`os.kill(pid, 0)` 成功)| 退出码 3 — 不写 manifest 不报错 |
| Lock 存在 + holder PID 死(`ProcessLookupError`)| 报告 stale 但**不自动删除**(承接 §5.4 不依赖纯 mtime;reviewer 手动检查)|
| 释放 lock | **只删自己持有的 lock**(读 lock 文件 PID 字段比对,避免删新持有者 lock)|

> ⚠️ 不依赖 mtime 判 stale(承接 AUTOMATION-BOUNDARY §5.4 stale lock 检测策略 + 补丁 1)。

---

## 越权写入护栏

- 扫描前 snapshot `wiki/` `toolbox/` `staging/candidate/` `00-Index.md` mtime
- 扫描后 + 写 manifest 后再 snapshot,比对差异
- 任何 mtime 变化 → 退出码 2(护栏告警)
- 写 manifest 只能在 `manifests/ingest/<DATE>.jsonl`,不能写其他位置

---

## 与 Phase C 后续工作的关系

| 工具 | 关系 |
|---|---|
| **本 C-3 Gate L3 提议层(本工具)** | 输出 `manifests/ingest/<DATE>.jsonl` proposal manifest;**不**升 active |
| reviewer L4 授权独立 plan(后续) | 读本工具 manifest → 复核 → 显式授权 → write lock + manifest + 单写者实际创建 wiki/<file>.md(active 提升)|
| C-2 Index updater(后续) | reviewer L4 升 active 后,index 才有内容可更新 |
| C-1 Lint CLI(后续) | 全量规则扫描(本工具复用 LINT-RULES GATE-* 域 + GRAPH-* + SOURCE-* 部分;C-1 是全量)|
| C-4 Graph Context Pack(后续) | 最依赖 active 数据 + graph 关系 — 本工具不涉及 |

---

## 限制

1. **不调用 LLM**:HC-08 body structure 检查只验证 H2 章节存在性,不验证内容质量(LLM 评估留 C-1 全量 lint 或后续 quality scorer)
2. **6 决策矩阵 v1 只覆盖 active / needs_repair / needs_human_review**:brief_only / archive / reject 由 reviewer 标记,工具不主动判定
3. **HC-08 关键发现 H2 检查只用字符串匹配**:支持 "关键发现" / "key_findings" 中英标题,不支持自定义别名
4. **frontmatter parser 用 PyYAML safe_load**:超复杂 YAML(自定义 tag / anchor 嵌套等)不解析 — Phase B candidate 范围内未触发问题

---

## 测试 fixtures

`tests/fixtures/` 下:

| Fixture | 期望 proposed_gate_decision |
|---|---|
| `active-ready.md` | active(high)|
| `active-proposed-only.md` | active(medium,proposed_node_only)|
| `needs-repair-no-future-q.md` | needs_repair(HC-06 fail)|
| `needs-repair-no-graph.md` | needs_repair(HC-07 fail)|
| `needs-human-review-toolbox.md` | needs_human_review(HC-10 → toolbox handoff)|
| `needs-human-review-forum.md` | needs_human_review(source_type=forum)|
| `legacy-status-candidate.md` | needs_repair / active(P3 容忍 + 其他规则)|
| `auto-active-bug.md` | active(HC-09 fail — 实际场景 reviewer 应警觉)|
| `non-staging-path.md` | (HC-01 fail,但 path guard 在 collect_candidates 之前已拦)|
| `parse-error.md` | parse_errors 命中,可能 needs_human_review |

---

## 修订历史

| 日期 | 改动 | 作者 |
|---|---|---|
| 2026-05-04 | 初始 v1 — 严格 L3 提议层 + PyYAML frontmatter parser + 10 硬检查 + 6 决策矩阵 + 越权护栏 + AUTOMATION §5.4 lock 协议 + AUTOMATION §6.3 双层 manifest schema + 6 决策 v1 范围(active/needs_repair/needs_human_review,brief_only/archive/reject 留 reviewer)+ Phase B 10 candidate dry-run 验证;承接用户 plan + 5 微调全部合入(PyYAML / docs/phase-c-reports/00-Index.md / hard check 对照表 / lock PID+heartbeat / manifest 双层 schema)| Codex / Claude Code |
