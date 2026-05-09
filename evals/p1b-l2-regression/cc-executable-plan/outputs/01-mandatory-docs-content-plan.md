<!-- PLAN_TEMPLATE_V3 -->

# Plan: AKB-2.0-NAMING-CONVENTION 增补 §6 工具库扩展命名

日期:2026-05-06
状态:ready-for-implementation
负责人:Claude Code(per cc-executable-plan skill)

## Review 对齐

REVIEW_DOMAIN:docs_content

REVIEW_FOCUS:
- §6 措辞与既有 §1-§5 的术语 / 编号风格一致
- reserved prefix 列表完整(cc- / mcp- / hook- 三者最少)且边界清晰(slug 不冲突 / 大小写规则)
- 不越界到工具库实现细节(slug 命名只管文件名,不管 schema / status 字段)

Domain-specific notes:
- docs_content:source material = 既有 §1-§5 + 工具库 5 件套实例;generated output = §6 新节 ~30-50 行;links = 内部锚点 `[5 件套结构](#...)` 等;metadata = frontmatter 不动;publish path = 文件原位 wiki/standards/ 下;policy compliance = 命名约定标准内部一致;validation = grep 检查 §6 标题 + reserved prefix 列表存在

## 目标与验收

用户目标:
- 让工具库 5 件套(`tools/<slug>.md` / `interfaces/<slug>.md` / `evals/<slug>.md` / `registry/<slug>.md` / `wiki/tools/<slug>.md`)在添加新工具时有明确 slug 命名规则可查,避免 reviewer 临时打回 + 减少未来 lint 规则歧义

成功标准:
- [ ] `AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md` 含一节 `## §6 工具库扩展命名`
- [ ] §6 含 5 件套 slug 一致性规则(同一工具 5 个文件的 slug 必须 byte-identical)
- [ ] §6 含 reserved prefix 列表(`cc-` / `mcp-` / `hook-`)+ 每个 prefix 的语义说明
- [ ] §6 含 slug 字符约束(小写 ASCII / 数字 / 连字符 / 长度 3-40)
- [ ] §6 含至少 3 个示例 slug(cc-executable-plan / mcp-akb-search / hook-secret-detection 等)
- [ ] §1-§5 任一字符未改(diff 验证)
- [ ] frontmatter `status: ready` 保持不变
- [ ] 文件大小净增加(只新增 §6,不删既有内容)

非目标:
- 不修改 §1-§5 任何内容
- 不引入新 status / schema / lint 规则(那是 P2.1 lint plan 范围)
- 不创建新文件,不动其他 standards
- 不写代码 / 不接 CI / 不接 hook

## 当前状态

相关事实:
- `AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md` 文件存在,frontmatter `status: ready`
- 既有 §1-§5 覆盖 source / concept / design / standard / phase-report 五类页面命名;工具库 slug 当前未在该文件描述
- 工具库 5 件套实例:cc-executable-plan / cc-review / cc-akb-search / cc-local-llm / cc-skill-stocktake — 5 个 slug 已 100% 满足 reserved prefix `cc-` 约定但口径未文档化
- reserved prefix 候选 `cc-` / `mcp-` / `hook-` 来自工具库 v2 plan §1(讨论中,尚未文档化)

现有入口:
- 文档浏览:Obsidian / `cat` 直读
- 写入:Edit 工具(本轮唯一 mutation)

约束:
- 不修改 `Project AKB/**` / 不触发任何旧 fetch / synth / translate / NLM 链路
- 不动 wiki/tools / toolbox/ 既有 5 件套文件
- 不修改 `~/.codex/skills/**` 任何 SKILL.md
- 文件 frontmatter `status: ready` 保持

## 代码执行方向

新增:
- 无新增文件

修改:
- `AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`:在文件末尾(或 §5 之后,§参考链接 之前)插入 §6 节,~30-50 行

不修改:
- §1-§5 既有内容(diff 必须 0 改动)
- 文件 frontmatter
- 任何 5 件套实例文件(本 plan 只立规则不审已有 slug)
- 任何代码文件 / 任何 hook / 任何 settings.json
- 任何旧 AKB 文件

## 接口与数据合约

CLI/API 合约:
- 无 CLI / 无 API(纯文档变更)

函数/模块合约:
- 无函数变更

数据/Schema 变更:
- 无 schema 变更
- 旧数据兼容:既有 5 件套 slug(cc-executable-plan 等)100% 已符合本 §6 规则,无 backfill / 迁移

文档段落合约(§6 必含 7 条款):
1. **slug 一致性**:同一工具的 5 个文件,slug 必须 byte-identical(`grep -c "^slug: cc-xxx" toolbox/{tools,interfaces,evals,registry}/cc-xxx.md wiki/tools/cc-xxx.md` 应返回 5)
2. **字符集**:`^[a-z][a-z0-9-]{2,39}$`(小写起头,小写字母 / 数字 / 连字符,长度 3-40)
3. **reserved prefix `cc-`**:Codex / Claude Code 调用入口 skill(SKILL.md 在 `~/.codex/skills/<slug>/` 或 `~/.claude/skills/<slug>/`)
4. **reserved prefix `mcp-`**:MCP server 调用入口(SKILL.md 不存在,只有 server 配置)
5. **reserved prefix `hook-`**:hook script(在 `~/.claude/scripts/hooks/` 下)
6. **示例**:cc-executable-plan / cc-review / mcp-akb-search / hook-secret-detection
7. **保留禁忌**:不允许使用单字 slug;不允许全数字;不允许 trailing 连字符

## 实现步骤

1. 准备/审计步骤
   - Read `AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md` 获取当前 §1-§5 末尾行号 + frontmatter 范围
   - `grep -nE "^## §[0-9]" AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md` 确认现有节编号(预期 §1-§5)
   - 确认无 §6 已存在(`grep -c "^## §6" ...` 应为 0)

2. 核心实现步骤
   - 在 §5 之后(§参考链接 / 维护记录 之前)用 Edit 工具插入 §6 整节
   - §6 标题:`## §6 工具库扩展命名`
   - 包含 7 条款(见 §接口与数据合约 文档段落合约)
   - 至少 3 示例 slug + 1 反例 slug(说明被拒原因)

3. 集成步骤
   - 无集成动作(纯文档,无下游)
   - 在文档末尾 ## 维护记录(若存在)追加 1 行:`| <YYYY-MM-DD> | 增补 §6 工具库扩展命名 | Claude Code |`

4. 收尾步骤
   - `grep -c "^## §6 " AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md` 应为 1
   - `grep -cE "^- \\\`(cc|mcp|hook)-" AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md` 应 ≥ 3(reserved prefix 列表命中)
   - `git diff --stat AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`(若 git 可用)显示净增加,不显示既有节删除
   - 不执行任何运行时副作用

## 边界与失败模式

必须处理:
- §6 编号冲突:若实证发现 §6 已存在(改名 / 历史插入),改用 §7 并在维护记录注明
- frontmatter 行被误改:Edit 范围严格在 ## §5 之后,不动 frontmatter
- 维护记录节不存在:若 ## 维护记录 节不存在,在文档末尾新建该节后再追加 row

明确不处理:
- 既有 5 件套 slug 是否合规审计(本 plan 只立规则,不 enforce)
- lint 实现(P2.1 单独 plan)
- reserved prefix 扩展(只立 cc- / mcp- / hook- 三个,其它留未来 PR)
- §1-§5 任何措辞优化

风险与防护:
- 风险:Edit 误改既有节
  防护:Edit `old_string` 锚定 §5 末尾或文件末尾的稳定字符串(如"维护记录"表头),不用模糊匹配
- 风险:§6 与未来 lint 规则口径漂移
  防护:§6 措辞与工具库 v2 plan §P2.1 8 类 lint 一致;P2.1 实施时反查 §6
- 风险:示例 slug 未来重命名
  防护:示例只用 reserved prefix,不绑定具体 active 工具版本

## 测试计划

单元测试:
- 无(纯文档)

集成/CLI 验证:
- `grep -c "^## §6 工具库扩展命名" AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`
  期望:1
- `grep -cE "^- \\\`cc-" AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`
  期望:≥ 1
- `grep -cE "^- \\\`mcp-" AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`
  期望:≥ 1
- `grep -cE "^- \\\`hook-" AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`
  期望:≥ 1
- `awk '/^---$/{c++;if(c==2){print NR;exit}}' AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`
  期望:与改前 byte-identical(frontmatter 未动)

测试隔离:
- 仅本计划列出 1 文件 Edit;0 数据库 / 0 临时目录 / 0 mock
- 不动 candidate / staging / raw / toolbox / 任何 SKILL.md

暂不执行的验证:
- lint 规则实际运行(P2.1 plan)
- 既有工具 slug 合规批量审计(P0 状态切片已隐式覆盖)

## 待确认

- §6 是否需要追加"slug 命名变更流程"小节(如已 active 工具 rename 流程)?默认:不追加(留独立 deprecation plan)
- §6 reserved prefix 列表是否需要锁版本号 / 锁日期?默认:不锁(reserved prefix 是约定不是 schema)

## 实施前 Review Checklist

- [x] 目标、成功标准、非目标清楚
- [x] REVIEW_DOMAIN 和 REVIEW_FOCUS 清楚
- [x] 新增/修改/不修改的文件范围清楚(只 Edit 1 文件)
- [x] CLI/API/函数输出合约清楚(无 CLI;§6 文档段落合约 7 条款)
- [x] 数据迁移和旧数据兼容清楚(无 schema 变;既有 slug 100% 兼容)
- [x] 边界情况和失败模式清楚(§6 编号冲突 / Edit 误改 / 示例 slug 漂移)
- [x] 测试夹具不会污染真实数据(纯 grep 验证)
- [x] live runtime、密钥、外部服务、持久配置没有被默认修改

---

> **审查提醒**: 计划完成后可手动 `/review plan` 触发 Codex 审查。不自动触发。
