# Plan: AKB 图谱检索健康改进 V3.2 (final)

日期：2026-05-01
状态：ready-for-implementation
负责人：Claude Code 执行，Codex 复核

> **历史脉络**
> - V1（已上线，Codex APPROVED, baseline af366c4d33fe）：MCP `akb_search` 接入 `graph_context` + `graph-status` 只读健康命令 + best_note P0 strict（only `note_path`）
> - V2 Plan A：7 phase 一锅端，Codex 找出 3 个 P2 finding（restore 命令 / alias 反查 / 全量 sample）
> - V3：以 Plan B 为 Round 1 骨架尝试整合，但 verification 路径有错（用 `scripts.main search` 验 `graph_context` 实际不返回）
> - V3.1（用户输入）：撤掉 schema migration 改用 config-driven，MCP smoke 改直接调 `search_knowledge()`，FTS audit 落实为内容比较
> - **V3.2 = V3.1 + 6 条建议合入**：generic fallback / pre-post baseline diff / MCP smoke 阈值升级 / FTS audit 分两级 / alias 子串测试 / Codex finding 对照表

## Review 对齐

REVIEW_DOMAIN：ai_infra

REVIEW_FOCUS：
- AI-facing 检索是否继续暴露翻译污染
- `graph_context` 是否被 generic hub 污染
- 验证路径是否真实覆盖 MCP `akb_search`
- 生产 FTS rebuild 是否有明确人工确认边界
- prune / 数据清理指标是否自洽
- generic 过滤行为在不同部署机器之间是否一致
- alias 反查是否引入子串误匹配

Domain-specific notes：
- ai_infra：MCP search、FTS 内容源、概念图谱上下文、只读健康报告、生产索引 rebuild 门控
- 不动：模型路由、LaunchAgent、密钥、外部服务、Obsidian vault、translator/verifier prompt

## 目标与验收

用户目标：
- 修正 AKB 图谱检索的 AI-facing 输入污染，让 Claude/Codex 使用 `akb_search` 时优先拿干净英文 note、可用图谱上下文、不过度受 generic 概念刷屏。

成功标准：
- [ ] `CatalogDB.fts_rebuild()` 代码改为 strict `note_path` only；缺 note 时 body 为空但 title/tags 仍索引
- [ ] Round 1 不新增 DB schema，不写生产 DB；generic 过滤先由 `config/concept_generics.json` 动态驱动
- [ ] config 缺失时**回退到代码内 `_DEFAULT_GENERICS` 常量**（11 个 id）；不同机器行为一致；报告显示 `effective_generics_source: builtin | config | merged`
- [ ] MCP path smoke 直接调用 `scripts.core.search.search_knowledge()`，5 个 query 全部 5 字段验收通过（详见步骤 7）
- [ ] `best_note.path_type` 只允许 `note_path` 或空，不允许 `translated_path`
- [ ] `graph_context` 有字段时结构完整，无概念命中时安全为空
- [ ] `graph-status` 默认输出轻量 FTS body source proxy；显式 `--audit-fts-body-sources-full` 才做全量文件 IO 比较
- [ ] Round 1 收尾时 pre/post baseline diff 机械化对比 5 项指标（详见步骤 9）
- [ ] 生产 `items_fts` rebuild 不随代码实现自动执行，必须用户单独确认
- [ ] V1 既有测试不回归（`test_search_best_note_priority.py` 7/7 + V1 `test_search_graph_context.py` 16/16 + V1 `test_graph_status.py` 6/6）

非目标：
- 不做 Round 2 prune apply
- 不做 cooc backfill
- 不跑 80 条或 1442 条 LLM 重抽
- 不改 wiki 阈值
- 不修 Obsidian 断链
- 不动翻译链路
- 不在 Round 1 自动跑生产 `fts_rebuild()`

## 当前状态

相关事实（live DB，2026-05-01）：
- `scripts/utils/db.py:735-760` 的 `fts_rebuild()` 源顺序是 `translated_path → note_path → raw_path`
- `scripts/commands/knowledge.py:54-61` 的 `scripts.main search --output json` 输出 `curator.search()` 列表，**不包含** `graph_context`
- MCP path 是 `scripts.mcp_server.py` 调 `scripts.core.search.search_knowledge()`
- `concepts` 表没有 `is_generic` 列（V3.2 不加）
- `graph-status` 只有 sample 输出，没有全量 prune candidates dump
- Active concepts 1407 / Isolated 1149 / Single-mention orphan 1050 / OK 无概念 81

现有入口：
- MCP/search 核心：`scripts.core.search.search_knowledge()`
- FTS：`CatalogDB.fts_rebuild()` / `CatalogDB.fts_search()`
- 图谱健康：`scripts.main graph-status`
- 现有 DB：`index/catalog.db`

约束：
- Round 1 代码实现和测试可改 repo 文件，但不自动改生产 DB 数据
- 生产 FTS rebuild 是本地数据重建，必须单独确认后执行
- 所有 live audit 默认只读，报告可写到 `index/diagnostics/`

## 代码执行方向

新增：
- `config/concept_generics.json`：generic concept id/name 列表，运行时过滤，不写 DB
- 测试文件：FTS note-only / FTS source audit / MCP smoke / generic 过滤（含 fallback）/ prune dump 全量 / alias 子串防误匹配

修改：
- `scripts/utils/db.py`：
  - `fts_rebuild()` 改为只读 `note_path` 正文
  - `search_concepts()` 增强 alias / canonical / merged / 复合 query 召回
  - 新增只读 helper：FTS body source audit（两级）、全量 isolated single prune candidates、全量 ok-note-without-concepts list
- `scripts/core/search.py`：
  - `_get_graph_context()` 按 generics 过滤 hub / expanded / neighbor seed
  - `result_explanations[].matched_concepts` 保留 generic
  - semantic 信号 `>=0.6` 为 `semantic`，`0<sim<0.6` 为 `weak_semantic`
- `scripts/commands/graph_status.py` + `scripts/main.py`：
  - 新增 `--dump-prune-candidates PATH`
  - 新增 `--list-ok-notes-without-concepts PATH`
  - 新增 `--audit-fts-body-sources-full`（默认轻量 proxy + sample，显式 full 才全文件 IO）
  - 新增 `--baseline-snapshot {pre|post}`：写 `index/diagnostics/baseline-round1-{pre|post}-YYYYMMDD.json`
  - 输出 raw hubs / filtered hubs / `effective_generics_source`

新增模块：
- `scripts/utils/generics.py`：
  - `_DEFAULT_GENERICS = ["agent","tool","task","memory","component","react","planning","flow","file","code","event"]`
  - `load_generics(config_path) -> tuple[set[str], str]`：返回 `(generics_set, source)`，source 取值 `builtin | config | merged`
  - 行为：config 不存在 → builtin；存在且只列覆盖 → config；存在且 `mode=merge` → merged

不修改：
- 不新增 `concepts.is_generic` schema
- 不新增 `mark-generics` 命令
- 不改 `scripts/commands/translate.py`、`scripts/pipeline/trans_verifier.py`
- 不改 LaunchAgents、model lock、router、CLAUDE.md

## 接口与数据合约

CLI/API：

`python -m scripts.main graph-status --output json`
- 输出新增字段：
  - `summary.generic_concepts_count`（基于 effective generics）
  - `summary.single_mention_orphan_active`
  - `summary.fts_body_source_proxy`（轻量 proxy，详见下文）
  - `summary.effective_generics_source`：`builtin | config | merged`
  - `summary.effective_generics_count`
  - `top_hubs_filtered`（默认排除 generic）
  - `top_hubs_including_generics`（审视用）

`python -m scripts.main graph-status --dump-prune-candidates PATH --output json`
- 写 JSONL 全量 prune 候选；写失败退出非 0；不修改 DB

`python -m scripts.main graph-status --list-ok-notes-without-concepts PATH --output json`
- 写 JSONL 全量 ok-note-without-concepts；不受 sample 上限影响

`python -m scripts.main graph-status --audit-fts-body-sources-full --output json`
- 默认不开。开启后逐条比对 `items_fts.note_content` 与 `note_path/translated_path/raw_path` 前 5000 字
- 输出 `summary.fts_body_source_audit_full`：5 类 counts + 每类 sample 路径

`python -m scripts.main graph-status --baseline-snapshot {pre|post}`
- 写 `index/diagnostics/baseline-round1-{pre|post}-YYYYMMDD.json`，含 graph-status JSON 全文 + 5 query MCP smoke 结果

函数：

`CatalogDB.fts_rebuild() -> int`
- 只从 `note_path` 读 `note_content`
- `note_path` 缺失或不可读时 `note_content=""`，仍索引 title/tags

`CatalogDB.search_concepts(query, limit=5) -> list[dict]`
- 支持 active id/name + alias 反查 + canonical/merged 跳转 + 复合 query 分词
- 仅返回 active canonical concept；找不到 active canonical 时不返回 pruned/merged
- alias 反查使用 `json_each(concepts.aliases)` 精确匹配（不用 `LIKE '%query%'`，避免子串误命中）

`CatalogDB.audit_fts_body_sources_proxy() -> dict`（轻量，默认）
- 纯 SQL，不读文件
- 返回基于 `items_fts.note_content` 是否为空、length、与 items 表 note_path/translated_path 是否存在的代理统计
- 字段：`empty_body_count` / `nonempty_body_count` / `nonempty_no_note_path_count`（说明 body 必非来自 note_path）/ `nonempty_with_note_path_count`
- 性能：单次 SQL，毫秒级

`CatalogDB.audit_fts_body_sources_full(sample_limit=10) -> dict`（重，显式调）
- 逐条读 note_path / translated_path / raw_path 前 5000 字与 `items_fts.note_content` 前 5000 字精确比较
- 返回 5 类 counts：`note_body / translated_body / raw_body / empty_body / unknown_body`
- 每类附 `sample_limit` 条 `(item_id, path)` 示例
- 性能：N×3 次文件 IO，秒级

`_get_graph_context(query, results, db) -> dict`
- generic 不进入 `hub_concepts` / `expanded_concepts` / `neighbor seed`
- query 无概念命中时返回 `{}`
- 失败时返回 `{}`，不影响 search

数据/Schema：
- Round 1 无 schema migration
- 旧数据兼容：不依赖 `is_generic` 列，纯运行时过滤
- 生产 `items_fts` rebuild 不属于代码 PR 自动步骤

## 实现步骤

### 1. 基线与防线

- 确认工作树状态（`git status` 干净）
- **写 pre baseline**：`python -m scripts.main graph-status --output json --baseline-snapshot pre`
  - 落盘到 `index/diagnostics/baseline-round1-pre-YYYYMMDD.json`
  - 必含字段：graph-status JSON 全文 + 5 query MCP smoke 结果（步骤 7 跑一次提前留底）
- 不启动 LLM，不跑 synth/translate/retry

### 2. 修 FTS 内容源 + 两级 audit

- 改 `fts_rebuild()` 为 note-only
- 加 `audit_fts_body_sources_proxy()`：轻量 SQL-only，默认在 graph-status 里输出
- 加 `audit_fts_body_sources_full(sample_limit=10)`：显式调用，文件 IO 内容比较
- graph-status 行为：
  - 默认 `summary.fts_body_source_proxy`（轻量代理）
  - `--audit-fts-body-sources-full` 时附加 `summary.fts_body_source_audit_full`（重）
- 文档化：proxy 字段含义 + 何时该跑 full

### 3. 修 graph_context generic 污染（含 fallback）

- 新增 `scripts/utils/generics.py` 含 `_DEFAULT_GENERICS` + `load_generics(config_path)`
- 新增 `config/concept_generics.json`（与 `_DEFAULT_GENERICS` 内容一致，作为模板）
- `_get_graph_context()` 调用 `load_generics()`：
  - config 不存在 → 用 `_DEFAULT_GENERICS`，source = `builtin`
  - config 存在且 `{"mode": "override", "generics": [...]}` → 用 config，source = `config`
  - config 存在且 `{"mode": "merge", "generics": [...]}` → builtin ∪ config，source = `merged`
- graph-status 输出 `summary.effective_generics_source` 与 `summary.effective_generics_count`
- 测试三种情况都覆盖

### 4. 修 concept 召回（消化 Codex F2）

- `search_concepts()` 增强：
  - 反向 alias 扫描：`SELECT id FROM concepts, json_each(concepts.aliases) WHERE COALESCE(status,'active')='active' AND json_each.value = ?`（精确匹配，**不用 LIKE 子串**）
  - canonical 解析：对每个候选 id 用 `_resolve_canonical(conn, id)` 跳到 active canonical（`max_depth=5` 防循环）
  - 复合 query 分词：`query.split()` 后逐词召回再去重
  - 仅返回 active canonical 概念
- 子串防误匹配测试：alias 含 `Tool` 的 concept，搜 `Toolkit` 不应命中

### 5. 补全只读 dump

- `--dump-prune-candidates PATH` 输出全量候选，不复用 sample 上限
- 候选规则（5 条 SQL 实现）：active + mention=1 + no relation + no wiki_page + age≥30 + not primary in ok note
- `--list-ok-notes-without-concepts PATH` 输出全量 ok-note-without-concepts JSONL（消化 Codex F3）

### 6. semantic 阈值

- `_get_graph_context()` semantic 信号：
  ```python
  sim = r.get("similarity") or r.get("score") or r.get("semantic_score") or 0
  if sim >= 0.6:
      signals.append("semantic")
  elif sim > 0:
      signals.append("weak_semantic")
  ```
- 测试覆盖阈值上下边界

### 7. MCP path smoke（验收阈值升级）

- **不用** `scripts.main search` 验 `graph_context`（不返回该字段）
- 直接 Python 调用 `scripts.core.search.search_knowledge()`
- 5 个 query：`MCP` / `Agent` / `OpenAI` / `LangGraph` / `Figma MCP`
- 每个 query 5 字段验收：
  1. `search_knowledge()` 不抛错
  2. `result_count > 0`
  3. `best_note.path_type` 只能是 `"note_path"` 或空（**不允许** `"translated_path"`）
  4. `graph_context` 字段存在或安全空值（`{}`）
  5. `len(graph_context.get("matched_concepts", [])) >= 1`

- expected_empty 例外机制：
  - 若某条 query 合理无 matched_concepts，**必须在报告里显式列为 `expected_empty`** 并说明原因（如"该 query 不在库内"），不能静默 PASS
  - 5 个 query 默认全部期望 ≥1 matched_concept；若 `LangGraph` / `Figma MCP` 仍空 → 必须排查 alias 反查/canonical 解析路径，不能直接通过

- 输出格式（每条 query）：
  ```json
  {
    "query": "LangGraph",
    "result_count": 3,
    "best_note_path_type": "note_path",
    "graph_context_keys": ["matched_concepts","expanded_concepts","hub_concepts","result_explanations"],
    "matched_concepts_count": 2,
    "errors": null,
    "verdict": "PASS|FAIL|EXPECTED_EMPTY",
    "expected_empty_reason": null
  }
  ```

### 8. 生产 FTS rebuild 门控

- 代码与测试通过后，只报告当前 `summary.fts_body_source_proxy` + `summary.fts_body_source_audit_full`（如开启）
- **等用户确认后**才执行：
  - 先备份：`sqlite3 index/catalog.db ".backup index/backups/catalog-pre-fts-rebuild-YYYYMMDD.db"`
  - 再执行 `python -c "from scripts.utils.db import CatalogDB; CatalogDB().fts_rebuild()"`
  - 重新跑 `--audit-fts-body-sources-full`：要求 `translated_body=0` / `raw_body=0` / `unknown_body=0` 或 unknown 有明确样本原因
- 用户不确认 → 把"生产 search snippet/ranking 仍可能受旧 FTS 影响"列入剩余风险

### 9. 收尾：post baseline + diff 报告（消化建议 2）

- 写 post baseline：`python -m scripts.main graph-status --output json --baseline-snapshot post`
  - 落盘到 `index/diagnostics/baseline-round1-post-YYYYMMDD.json`
  - 含 graph-status JSON + 5 query MCP smoke 重跑
- 收尾报告必须**机械化**对比 5 项指标（不靠描述，直接 jq）：
  1. `summary.fts_body_source_proxy`（pre vs post）
  2. `top_hubs_filtered`（pre vs post，generic 应已踢出）
  3. `top_hubs_including_generics`（pre vs post，generic 应仍在）
  4. `summary.generic_concepts_count` + `summary.effective_generics_source`
  5. 5 query MCP smoke 结果对比（pre 各 query verdict vs post 各 query verdict）
- 报告示例命令：
  ```bash
  diff <(jq '.summary' index/diagnostics/baseline-round1-pre-YYYYMMDD.json) \
       <(jq '.summary' index/diagnostics/baseline-round1-post-YYYYMMDD.json)
  ```

### 10. Round 2 指标修正（仅文档，Round 1 不实施）

Round 2 指标表（V3.2 修正）：
| 维度 | Round 1 后 | Round 2 后 |
|---|---:|---:|
| `total_concepts`（含 pruned） | 1407 | 1407（不变） |
| `active_concepts` | 1407 | ~360（1407 - ~1050 prune） |
| `pruned_concepts` | 0 | ~1050 |
| `isolated_active_concepts` | 1149 | < 80 |
| `concept_relations` | 1332 | 1332 + ~80（cooc backfill） |
| `ok_notes_without_concepts` | 81 | < 10 |

prune apply / restore-pruned-concept / cooc backfill 全部保留到后续计划，不进入 Round 1。

## 边界与失败模式

必须处理：
- `concept_generics.json` 缺失：用 `_DEFAULT_GENERICS`，source=`builtin`，graph-status 输出明示（**不只是 warning**）
- FTS audit 读文件失败：计入 `unknown_body` 并输出样本（仅 full audit 模式）
- alias 命中过多：每词 limit 限制，总体去重后再截断
- alias 子串误匹配：用 `json_each` 精确匹配，**不用 `LIKE '%query%'`**
- canonical 循环：max_depth=5 后停止，跳过该候选
- dump 路径不可写：退出非 0，不修改 DB
- graph_context 构建失败：返回 `{}`，不影响主搜索结果
- MCP smoke 某 query 合理无命中：必须 `expected_empty` + 原因，不能静默 PASS
- pre/post baseline 同日多次跑：文件名加 timestamp 后缀（如 `baseline-round1-pre-YYYYMMDD-HHMMSS.json`）

明确不处理：
- 不自动清理 generic concepts
- 不自动 prune concepts
- 不自动补 concept_relations
- 不自动 rebuild 生产 FTS
- 不修改 Obsidian vault

风险与防护：
- 风险：note-only FTS 降低正文召回
  防护：title/tags 仍索引；graph_context/embedding 补召回；生产 rebuild 需单独确认
- 风险：generic 过滤过度
  防护：保留 raw 与 filtered 对照；config 可回滚到 builtin
- 风险：generic config 在不同机器不一致
  防护：fallback 到代码内 `_DEFAULT_GENERICS`；effective_generics_source 显式输出来源
- 风险：audit 分类误判
  防护：proxy 仅做粗判；full audit 用前 5000 字精确比较 + 输出 unknown samples
- 风险：MCP smoke 假绿（errors=0 但实际无用）
  防护：5 字段验收 + expected_empty 例外机制
- 风险：Round 1 收尾人工评估失真
  防护：pre/post baseline 落盘 + 机械化 jq diff

## 测试计划

单元测试：
- `tests/test_fts_note_only.py`：note 优先、无 note 空 body、title/tags 保留
- `tests/test_fts_body_source_audit.py`：proxy 5 字段 + full 5 类分类（note / translated / raw / empty / unknown）
- `tests/test_search_graph_context.py`（扩）：
  - generic 过滤（builtin / config / merged 三模式）
  - generic 不作扩展种子
  - item.matched_concepts 仍含 generic
  - alias-only 反查命中 canonical（消化 Codex F2）
  - **alias 子串防误匹配**：alias 含 `Tool` 的 concept，搜 `Toolkit` 不应命中（建议 5）
  - merged canonical 解析（max_depth 防循环）
  - 复合 query 分词
  - semantic / weak_semantic 阈值上下边界
- `tests/test_graph_status.py`（扩）：
  - 新增字段（`generic_concepts_count` / `effective_generics_source` / `fts_body_source_proxy`）
  - 全量 dump（消化 Codex F3）
  - 不修改 DB
  - `--baseline-snapshot pre|post` 落盘到正确路径
- `tests/test_search_best_note_priority.py`：确认 P0 strict note-only 不回归（V1 既有 7 个测试）
- `tests/test_generics.py`（新）：
  - config 缺失 → builtin
  - config override → 用 config
  - config merge → builtin ∪ config
  - source 字段正确

集成/CLI 验证：
```bash
ruff check scripts/utils/db.py scripts/utils/generics.py scripts/core/search.py \
           scripts/commands/graph_status.py scripts/main.py \
           tests/test_fts_note_only.py tests/test_fts_body_source_audit.py \
           tests/test_search_graph_context.py tests/test_graph_status.py \
           tests/test_search_best_note_priority.py tests/test_generics.py

python -m pytest \
  tests/test_search_best_note_priority.py \
  tests/test_search_graph_context.py \
  tests/test_graph_status.py \
  tests/test_fts_note_only.py \
  tests/test_fts_body_source_audit.py \
  tests/test_generics.py -v

# Pre baseline
python -m scripts.main graph-status --output json --baseline-snapshot pre
ls index/diagnostics/baseline-round1-pre-*.json

# Round 1 实施完成后
python -m scripts.main graph-status --output json --baseline-snapshot post
diff <(jq '.summary' index/diagnostics/baseline-round1-pre-*.json) \
     <(jq '.summary' index/diagnostics/baseline-round1-post-*.json)

# Prune 候选 + ok 无概念全量 dump
python -m scripts.main graph-status --dump-prune-candidates /tmp/akb-prune.jsonl --output json
python -m scripts.main graph-status --list-ok-notes-without-concepts /tmp/akb-ok-no-concepts.jsonl --output json
wc -l /tmp/akb-prune.jsonl /tmp/akb-ok-no-concepts.jsonl

# 显式 full FTS audit（重）
python -m scripts.main graph-status --audit-fts-body-sources-full --output json | jq '.summary.fts_body_source_audit_full'
```

测试隔离：
- schema / FTS / graph tests 使用临时 DB 与临时文件
- 生产 DB 只做 read-only audit
- 不执行生产 `fts_rebuild()`，除非用户单独确认

暂不执行的验证：
- 全量 pytest 暂不强制，避免既有 unrelated `pipeline/test_e2e_gate.py` 顶层 `sys.exit` 收集问题
- 不跑 LLM 任务
- 不跑生产 rebuild

## 待确认

- 无。默认采用"Round 1 无 schema migration / generic config + builtin fallback / 生产 rebuild 单独确认"的保守方案。

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚
- [x] 新增/修改/不修改的文件范围清楚
- [x] CLI/API/函数输出合约清楚
- [x] 数据迁移和旧数据兼容清楚（无 migration）
- [x] 边界情况和失败模式清楚
- [x] 测试夹具不会污染真实数据
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改
- [x] generic 过滤在 config 缺失时有明确 fallback 行为
- [x] Round 1 收尾有机械化 diff 路径
- [x] MCP smoke 验收能区分"修好"和"假绿"

---

## Codex Finding 消化对照表（建议 6）

| Finding | V2 Plan A 处理（错） | V3.2 Round 处理 | 状态 |
|---|---|---|---|
| F1: prune 回滚命令缺失（unmerge-concept 只处理 merged） | 计划说复用 `unmerge-concept` | **Round 2** 新增 `restore-pruned-concept` 独立命令；Round 1 只 dump 不 apply 所以 F1 在 Round 1 不适用 | 推 Round 2 |
| F2: alias-only 查询不命中 canonical | 设计 `alias_resolver` 但调 `search_concepts` 链条断裂 | **Round 1 步骤 4** 直接扩 `db.search_concepts()` 内函数，加 `json_each` 精确反向 alias 扫描 + canonical 解析 | Round 1 解决 |
| F3: Phase D 只回填 10 条 sample（受 `_TOP_OK_NO_CONCEPT=10` 限制） | 计划读 `ok_notes_without_concepts_sample` | **Round 1 步骤 5** 新增 `--list-ok-notes-without-concepts PATH` 全量 JSONL；Round 2 backfill 读全量 | Round 1 解决（dump），Round 2 用 |

未来 reviewer 看本计划时直接对照本表确认每条 finding 的归属与状态，避免重复发同样 finding。

---

## Round 2 与 Round 3（仅占位，本计划不实施）

### Round 2 — 数据债务清理

触发条件：Round 1 已 APPROVED + 生产 fts_rebuild 已确认 + Round 1 dump 出的 prune 候选 JSONL 已人审 + cooc backfill 候选已人审

- prune apply（`--apply-file PATH`） + 新增 `restore-pruned-concept` / `batch-restore-pruned`（消化 F1）
- cooc backfill：103 条缺关系自动补 `see_also`，跳过 generic-generic，`source_items` 标 `cooc_backfill_YYYYMMDD`
- 80 条 ok-note 定向 LLM 重抽：`backfill-concepts --item-ids ...` 读 Round 1 全量 JSONL

### Round 3 — 低优周边

- Wiki threshold 8 → 5（mention 5-9 档补 wiki）
- semantic 阈值微调（如有需要）
- Obsidian Wiki 断链（独立任务）

---

> **审查提醒**: 计划完成后可手动 `/review plan` 触发 Codex 审查。不自动触发。
