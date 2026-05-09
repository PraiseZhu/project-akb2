# Fixture 01 — mandatory: docs-content plan

## Goal

为 `docs/AKB-2.0-NAMING-CONVENTION.md` 增补一节 §6 "工具库扩展命名"(`tools/<slug>.md` / `interfaces/<slug>.md` / `evals/<slug>.md` / `registry/<slug>.md` slug 命名规则与 reserved prefix 列表),不动其它节,不写代码。

## Context

- 标准文件:`AKB2.0/docs/AKB-2.0-NAMING-CONVENTION.md`(已 ready)
- 工具库 5 件套 slug 实例(参考):cc-executable-plan / cc-review / cc-akb-search 等
- reserved prefix 候选:`cc-` (Codex / Claude Code skill)、`mcp-`(MCP server)、`hook-`(hook script)
- 不需要新增 CLI 或代码

## Expected behavior(skill 应输出)

- 符合 plan-template.md 11 节模板
- `REVIEW_DOMAIN: docs_content`
- 不越界到代码实现 / 不引入 mutation 风险段落
- 实现步骤集中在文档 Edit + 标准文档自检
