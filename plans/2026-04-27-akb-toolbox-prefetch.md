# Plan: AKB 工具库预读增强

日期：2026-04-27
状态：ready-for-implementation
负责人：Codex

## Review 对齐

REVIEW_DOMAIN：app_code

REVIEW_FOCUS：
- API/contracts：`toolbox prefetch` CLI、JSON schema、函数合约是否稳定且可消费。
- data/schema：`tools_fts` 旧 schema 迁移是否只影响索引层，且不修改 `tools` 主表数据。
- tests/validation：单测和 CLI 验证是否隔离真实 DB、真实 `~/.claude` 和 live runtime。
- deployment/runtime：source-only `akb-prefetch.sh` 集成是否有 fallback，且不会默认启用 live hook/script。
- security/performance：query 特殊字符不触发 FTS crash，评分/检索不引入 LLM 或网络调用。

Domain-specific notes：
- ai_infra：涉及 source-only prefetch 脚本和协议文档；不修改 live `~/.claude`，不启用 hook，不注册 MCP。
- app_code：涉及 AKB Python CLI、SQLite FTS schema、测试和命令输出合约，是本计划主 review domain。
- docs_content：涉及 `akb-prefetch-protocol.md` 文档更新；只作为实现同步说明，不是主交付物。
- other：无。

## 目标与验收

用户目标：
- 让 AI 在出方案或写代码前高效预读 AKB 工具库，读取方式从扫描 Toolbox Markdown 改为基于 `toolbox.db` 的任务能力检索、候选重排和决策卡压缩。

成功标准：
- `python3 scripts/main.py toolbox prefetch "MCP browser automation" --output json | python3 -m json.tool` 输出合法 JSON。
- `python3 scripts/main.py toolbox prefetch "Figma code connect"` 输出人类可读 top 工具候选和风险标记。
- 在临时 `HOME` 中运行 source-only `akb-prefetch.sh "MCP browser automation"` 时，Toolbox 阶段输出具体工具候选，而不是只输出分类 Markdown 文件名。
- 临时 `HOME/.claude/.akb-prefetch-result.md` 记录 Toolbox 命中数量，并包含 DB-backed prefetch JSON 摘要；DB 不可用时记录 fallback Markdown grep 结果。
- 单元测试覆盖精确匹配、重排、风险标记、archived 默认过滤、JSON schema、FTS 旧 schema 迁移。

非目标：
- 不做大规模工具库清洗、分类、trim 或版本巡检。
- 不改变 `tools` 主表语义，不删除工具数据。
- 不修改 live `~/.claude/scripts/akb-prefetch.sh`、`~/.claude` hooks 或任何 live runtime 配置。
- 不引入 LLM 参与工具候选重排。
- 不把 Obsidian `Toolbox/*.md` 重新定义为权威源；它继续只是展示层。

## 当前状态

相关事实：
- AKB 项目路径：`/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB`。
- AKB 工作树已有无关改动：`scripts/commands/synthesize.py`、`docs/PLAN-mainline-recovery.md`，实现时不得触碰。
- `index/toolbox.db` 当前有 4325 个工具，其中 `active=2508`、`archived=1817`。
- `tools_fts` 当前旧 schema 只有 `tool_id/name/category/description/tags` 5 列。
- `scripts/main.py` 当前 `toolbox` 子命令没有 `prefetch`。
- `scripts/commands/knowledge.py` 当前 toolbox 输出主要使用 `logger.info`，不适合纯 JSON stdout。
- Codex 工作区源副本 `plugins/claude-code-ai-infra/references/claude-runtime/scripts/akb-prefetch.sh` 当前 Toolbox 阶段用 `grep` 搜 Obsidian Markdown。

现有入口：
- CLI parser：`scripts/main.py`
- toolbox command handler：`scripts/commands/knowledge.py::cmd_toolbox`
- toolbox DB：`scripts/toolbox/db.py::ToolboxDB`
- toolbox curate：`scripts/toolbox/curator.py::ToolCurator.run_full`
- AKB prefetch 源副本：`plugins/claude-code-ai-infra/references/claude-runtime/scripts/akb-prefetch.sh`
- 预读协议源副本：`plugins/claude-code-ai-infra/references/claude-workspace/rules-lib/akb-prefetch-protocol.md`

约束：
- 用 `apply_patch` 做文件编辑。
- 不修改 live `~/.claude`，只改 Codex 工作区里的 source-only 规则/脚本副本。
- 单测必须使用临时 SQLite DB；不得读写真实 `index/toolbox.db`。
- CLI 只读验证可以读取真实 DB；会触发 FTS index 迁移/重建的命令必须在最终说明里明确。
- source-only `akb-prefetch.sh` 集成验证必须使用临时 `HOME`，不得写真实 `~/.claude/.akb-gate-token` 或 `~/.claude/.akb-prefetch-result.md`。
- `toolbox curate` 会改真实 DB/Markdown 展示层，除非用户明确授权，不作为本次自动验证命令。

## 代码执行方向

新增：
- `scripts/toolbox/prefetch.py`：纯逻辑模块，负责 query tokenization、FTS 安全查询、候选收集、确定性评分、风险标记、决策卡生成、text/json 格式化。
- `tests/test_toolbox_prefetch.py`：使用临时 `ToolboxDB(db_path=tmp_path / "toolbox.db")` 覆盖预读逻辑、FTS 迁移和 CLI JSON 关键行为。

修改：
- `scripts/toolbox/db.py`：扩展 FTS schema，增加旧 FTS schema 自动迁移；`fts_rebuild()` 聚合更多字段；`fts_search()` 支持安全参数和返回预读所需字段。
- `scripts/main.py`：新增 `toolbox prefetch` parser。
- `scripts/commands/knowledge.py`：新增 `cmd_toolbox` 的 `prefetch` 分支；`--output json` 必须只向 stdout 打印纯 JSON。
- `plugins/claude-code-ai-infra/references/claude-runtime/scripts/akb-prefetch.sh`：Toolbox 阶段优先调用 DB-backed prefetch；失败时 fallback 到旧 Markdown grep。
- `plugins/claude-code-ai-infra/references/claude-workspace/rules-lib/akb-prefetch-protocol.md`：更新 L2 Toolbox 预读协议为 DB-backed 决策卡模式。

不修改：
- `/Users/praise/.claude/**`：live runtime，需单独授权。
- AKB 项目已有无关改动文件：`scripts/commands/synthesize.py`、`docs/PLAN-mainline-recovery.md`。
- `index/toolbox.db` 主表数据：本次代码实现不执行清洗、trim、classify、scout、check-versions。
- Obsidian `Praise Space/Toolbox/*.md`：实现不直接编辑；只有用户后续授权运行 `toolbox curate` 时才由既有生成器更新。

## 接口与数据合约

CLI/API 合约：
- 命令：`python3 scripts/main.py toolbox prefetch "<query>"`
- 参数：
  - `query`：必填，自然语言关键词。
  - `--output text|json`：默认 `text`。
  - `--limit N`：默认 `10`，返回候选上限。
  - `--detail-limit N`：默认 `5`，text 模式详细展示上限；JSON 仍返回 `limit` 条。
  - `--include-archived`：默认 false；开启后 archived/deprecated 可参与但强降权。
- text 输出：打印 query、候选总数、top `detail_limit` 决策卡；每张卡包含名称、分类、match reason、版本、stars、mention_count、risk flags、source_url。
- json 输出：只用 `print(json.dumps(..., ensure_ascii=False))` 输出纯 JSON，不混入 logger。
- 错误行为：空 query 由 argparse 拦截；FTS syntax error 不退出，降级到 LIKE fallback；DB 不可用时 CLI 返回非零并输出错误，`akb-prefetch.sh` 捕获后走 Markdown fallback。

JSON 输出结构：

```json
{
  "query": "MCP browser automation",
  "generated_at": "2026-04-27T00:00:00Z",
  "source": "toolbox.db",
  "limit": 10,
  "detail_limit": 5,
  "include_archived": false,
  "match_count": 12,
  "returned_count": 10,
  "results": [
    {
      "name": "Inspector Jake",
      "category": "MCP",
      "match_reason": "tag:mcp; description:browser; has_install",
      "description": "...",
      "source_url": "https://github.com/...",
      "install_cmd": "...",
      "invoke_example": "...",
      "current_version": "1.2.3",
      "stars": 1234,
      "mention_count": 4,
      "tags": ["mcp", "browser-automation"],
      "use_cases": ["..."],
      "compatible_with": ["Claude Code"],
      "risk_flags": ["missing_version"]
    }
  ]
}
```

函数/模块合约：
- `prefetch_tools(query: str, db: ToolboxDB | None = None, limit: int = 10, detail_limit: int = 5, include_archived: bool = False) -> dict`：主入口，返回上述 JSON dict。
- `tokenize_query(query: str) -> list[str]`：提取中英文、数字和常见包名 token；去重、lowercase；空 query 返回 `[]`。
- `build_fts_queries(tokens: list[str]) -> list[str]`：先返回 AND 查询，再返回 OR 查询；token 全部双引号包裹，避免特殊字符破坏 MATCH。
- `collect_candidates(db: ToolboxDB, query: str, tokens: list[str], include_archived: bool) -> list[dict]`：按 exact/category/FTS/LIKE 收集候选，用 `id` 去重。
- `score_tool(tool: dict, query: str, tokens: list[str], include_archived: bool) -> tuple[float, list[str]]`：返回确定性分数和 reason 列表。
- `tool_to_card(tool: dict, score: float, reasons: list[str]) -> dict`：生成固定字段决策卡。
- `risk_flags(tool: dict) -> list[str]`：生成固定风险标记。
- `format_text(result: dict) -> str`：生成 text 模式输出。

数据/Schema 变更：
- `tools` 主表不变。
- `tools_fts` virtual table 迁移为：
  - `tool_id UNINDEXED`
  - `name`
  - `category`
  - `description`
  - `tags`
  - `search_text`
- `search_text` 聚合 `use_cases/install_cmd/invoke_example/compatible_with/notes/homepage/source_url`。
- `ToolboxDB.init_tables()` 必须先执行 `tools` migrations，再检查 `tools_fts` 列名；若不是预期列名，则 drop/recreate `tools_fts` 并立即 rebuild。
- 旧数据兼容：旧 DB 首次初始化会重建 FTS 索引，但不改 `tools` 主表数据；缺字段按空字符串处理；JSON 字段解析失败按空数组处理。
- 测试辅助：`ToolboxDB.__init__` 可支持 `AKB_TOOLBOX_DB_PATH` 环境变量作为默认 DB override；显式 `db_path` 参数优先级最高。该环境变量仅用于测试/CLI 隔离，不在生产脚本中设置。

评分规则：
- exact full name：+1000
- name contains full query：+300
- name token hit：每个 +80
- category key/label/alias hit：+120
- tag hit：每个 +90
- use_cases hit：每个 +70
- compatible_with hit：每个 +60
- description/search_text hit：每个 +30
- `install_cmd` 存在：+25
- `invoke_example` 存在：+25
- `current_version` 存在：+20
- `mention_count`：`min(90, mention_count * 3)`
- `stars`：`min(60, log10(stars + 1) * 20)`
- `category IS NULL` 或 `category == "Other"`：-80
- description 缺失：-60
- version 缺失：-20
- install 缺失：-15
- `mention_count <= 1 AND stars <= 0`：-25
- source_type 为 `manual`：-10
- deprecated：-150
- archived：-250

固定风险标记：
- `missing_version`
- `missing_install`
- `missing_description`
- `low_internal_signal`
- `uncategorized`
- `manual_source`

## 实现步骤

1. 准备/审计步骤
   - 在 AKB 项目运行 `git status --short`，确认只存在既有无关改动，后续不触碰。
   - 读取 `scripts/toolbox/db.py`、`scripts/main.py`、`scripts/commands/knowledge.py`、`akb-prefetch.sh` 相关段落作为修改基线。

2. 核心实现步骤
   - 新增 `scripts/toolbox/prefetch.py`，实现函数合约、评分规则、风险标记、text/json 格式化。
   - 修改 `ToolboxDB.init_tables()`：迁移顺序改为 `SCHEMA -> MIGRATIONS -> ensure_fts_schema`。
   - 修改 `FTS_SCHEMA` 和 `fts_rebuild()`：使用 `search_text` 聚合新增字段。
   - 修改 `fts_search()`：返回 `install_cmd/use_cases/invoke_example/compatible_with/mention_count/tags/notes/homepage` 等字段；增加 `include_archived` 可选参数；捕获 FTS syntax error 的 fallback 由 `prefetch.py` 处理。
   - 增加 JSON 字段解析 helper，所有 `tags/use_cases/compatible_with` 返回 list，不把原始 JSON 字符串泄漏给调用方。

3. 集成步骤
   - 在 `scripts/main.py` 添加：
     - `toolbox prefetch`
     - positional `query`
     - `--output {text,json}`
     - `--limit`
     - `--detail-limit`
     - `--include-archived`
   - 在 `cmd_toolbox()` 添加 `prefetch` 分支；json 模式只走 stdout，text 模式打印 `format_text()` 结果。
   - 修改 Codex source-only `akb-prefetch.sh`：
     - 先调用 `python3 scripts/main.py toolbox prefetch "$QUERY" --output json --limit 10 --detail-limit 5`。
     - 用 `python3 -m json.tool` 验证 JSON。
     - JSON 有效时展示 top 5：工具名、分类、match reason、risk flags、source_url。
     - JSON 无效或命令失败时走旧 `grep "$TOOLBOX_DIR"/*.md` fallback。
     - `.akb-prefetch-result.md` 写入 Toolbox hits，并在 `## Toolbox prefetch` 下包含 JSON code fence 或 fallback 文件列表。
     - 保留 `TOKEN_PATH` 和 `RESULT_PATH` 使用 `$HOME` 的行为；验证时通过临时 `HOME` 隔离，不在脚本内写死临时路径。
   - 更新 `akb-prefetch-protocol.md` 的 L2：明确 DB 是权威源，Markdown 是 fallback/展示层。

4. 收尾步骤
   - 不自动运行 `toolbox curate`。
   - 运行单测和只读 CLI 验证。
   - 最终说明中列出是否发生 FTS index lazy migration；如未运行真实 CLI，则说明未触碰真实 DB。

## 边界与失败模式

必须处理：
- query 包含 `@`、`/`、`-`、`:`、`.`、包名 scope 或中文时，不能让 FTS MATCH 崩溃。
- query tokens 为空时返回合法 JSON：`match_count=0`、`results=[]`。
- 旧 `tools_fts` schema 必须自动迁移并 rebuild。
- `tags/use_cases/compatible_with` 为空、非法 JSON、纯字符串时都要稳定转为 list。
- archived/deprecated 默认不返回；`--include-archived` 开启时可返回但强降权并带风险 reason。
- DB-backed prefetch 失败时，`akb-prefetch.sh` 必须 fallback 到旧 Markdown grep，不阻塞 AKB 知识预读整体流程。
- `--output json` 不能混入日志、warning 或人类文本。

明确不处理：
- 不提升工具数据质量，不批量补 tags/use_cases。
- 不修复 `ToolCurator._generate_index()` 造成的 Obsidian 展示层滞后问题；只保证 `curate` 后仍能用新 FTS rebuild。
- 不新增 MCP tool；仅增强 CLI 和 source-only prefetch 脚本。
- 不把新脚本同步到 live `~/.claude`。

风险与防护：
- 风险：首次真实 CLI 调用会迁移并重建 `tools_fts`，属于索引层写入。
  防护：只 drop/recreate FTS virtual table，不改 `tools` 主表；实现前后可用 `SELECT COUNT(*) FROM tools` 验证不变。
- 风险：FTS AND 查询过窄导致召回不足。
  防护：`build_fts_queries()` 先 AND，0 结果再 OR，最后 LIKE fallback。
- 风险：高 stars 但低相关工具压过任务相关工具。
  防护：任务字段命中权重大于 stars；`stars` 上限 60。
- 风险：json 模式被 logger 污染。
  防护：json branch 使用 `print()` 且不调用 logger 输出结果；测试用 `python3 -m json.tool` 验证。

## 测试计划

单元测试：
- 新增 `tests/test_toolbox_prefetch.py`，使用 `tempfile.TemporaryDirectory()` 或 pytest `tmp_path` 创建临时 DB。
- 测试 `ToolboxDB(db_path=temp_db)` 初始化旧 FTS schema 后会迁移到新列并 rebuild。
- 测试精确名称命中优先于 FTS：`LangGraph` 精确结果排名第一。
- 测试 category/tags/use_cases 命中提升排名：低 stars 但 tag/use_case 匹配的 MCP 工具高于高 stars 泛用工具。
- 测试 `Other`、缺描述、缺版本、缺安装命令会降权并生成 risk flags。
- 测试 archived 默认不返回，`include_archived=True` 时返回但排名降低。
- 测试 query 特殊字符：`@upstash/context7`、`Figma-to-code`、`MCP Server/CLI` 不抛异常。
- 测试 JSON schema：`prefetch_tools()` 返回固定顶层字段，`results[0]` 包含固定候选卡字段。

集成/CLI 验证：
- `AKB_TOOLBOX_DB_PATH=<tmp-db> python3 scripts/main.py toolbox prefetch "MCP browser automation" --output json | python3 -m json.tool`
- 期望：合法 JSON，`results` 为数组，字段完整。
- `python3 scripts/main.py toolbox prefetch "Figma code connect"`
- 期望：text 模式能输出候选卡；该命令可能读取真实 DB 并迁移 FTS 索引，执行前需在说明中标注。
- `TMP_HOME=$(mktemp -d) HOME="$TMP_HOME" bash plugins/claude-code-ai-infra/references/claude-runtime/scripts/akb-prefetch.sh "MCP browser automation"`
- 期望：AKB/Wiki 阶段仍正常；Toolbox 阶段展示具体工具；`$TMP_HOME/.claude/.akb-prefetch-result.md` 包含 Toolbox JSON 或 fallback 列表；真实 `~/.claude` 不被写入。

测试隔离：
- 单测和 CLI JSON 隔离测试必须使用临时 DB 或 `AKB_TOOLBOX_DB_PATH`。
- 不用真实 DB 的 mtime 作为硬验收，因为 SQLite 打开 WAL/SHM 可能改变 mtime。
- 真实 DB 只允许做只读/索引层验证；不得执行 `scout`、`check-versions`、`classify`、`trim`、`curate`。
- source-only `akb-prefetch.sh` 集成验证必须设置临时 `HOME`；不得在真实 `$HOME/.claude` 下创建 token 或 result 文件。

暂不执行的验证：
- 不自动运行 `python3 scripts/main.py toolbox curate`，因为它会修改真实 DB 和 Markdown 展示层。
- 不验证 live `~/.claude/scripts/akb-prefetch.sh`，因为本次不修改 live runtime。

## 待确认

- 无。默认只实现 source-only prefetch 集成；live runtime 同步另行授权。

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚
- [x] 新增/修改/不修改的文件范围清楚
- [x] CLI/API/函数输出合约清楚
- [x] 数据迁移和旧数据兼容清楚
- [x] 边界情况和失败模式清楚
- [x] 测试夹具不会污染真实数据
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改

## 自审修订记录

第 1 轮自审：
- 发现计划未按新版模板填写 `Review 对齐`。已补充 `REVIEW_DOMAIN=app_code`，并列出 API/contracts、data/schema、tests/validation、deployment/runtime、security/performance 五个重点检查面。
- 发现原方案没有锁定 FTS 旧 schema 迁移方式。已修订为 `tools_fts` 列名检查，不匹配则 drop/recreate 并立即 rebuild。
- 发现原方案没有锁定 JSON stdout 纯净性。已修订为 json 模式只允许 `print(json.dumps(...))`。
- 发现原方案没有明确 live runtime 边界。已修订为只改 Codex source-only 副本，live `~/.claude` 不动。
- 发现原方案没有测试 DB 隔离机制。已修订为新增 `AKB_TOOLBOX_DB_PATH` override 和临时 DB 单测要求。

第 2 轮自审：
- 发现 source-only `akb-prefetch.sh` 直接运行会写真实 `$HOME/.claude` token/result，与 live runtime 边界冲突。已修订为集成验证必须使用临时 `HOME`。
- 发现 FTS 查询策略未说明 AND/OR。已修订为先 AND、0 结果再 OR、最后 LIKE fallback。
- 发现原测试验收用真实 DB mtime 不可靠。已修订为不用 mtime 作硬验收，单测用临时 DB，真实 CLI 只做标注后的索引层验证。
- 发现 `toolbox curate` 属于会写真实 DB/Markdown 的命令。已修订为不自动执行，作为后续授权操作。
- 发现 JSON `results` 与 `detail_limit` 可能歧义。已修订为 JSON 返回 top `limit`，text 只详细展示 top `detail_limit`。
