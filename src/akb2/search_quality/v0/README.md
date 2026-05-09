# Search Quality v0 — cc-akb-search Quality Enhancement 占位 README

**状态**:planning(尚未实施)
**先决条件**:cc-akb-search 在 L1 active 后观察 7 天稳定(2026-05-15 之后)
**触发**:用户明确开 search quality v0 plan 后再 implement

## 范围

cc-akb-search 搜索质量评估与 ranking 调整:

- 建 20-30 条 query set,覆盖中文、英文、模糊、空结果、同义词、工具名精确匹配
- 测 quality baseline(当前 ranking 召回率/可用率)
- 决定是否改 ranking 或 query normalization

## query set 来源(必须明确,不可省)

至少 50% 来自用户实际查询日志(`index/sessions.db` 或 chat handler 日志);其余可人工编造覆盖边界。query 来源比例必须在 plan 中声明,否则 baseline 不代表性。

## rollback path(必须明确,不可省)

如果改 ranking 后发现召回率 regression:

1. 撤销 ranking 改动(代码 git revert)
2. 在 cc-akb-search 五件套补 `rollback_at`/`rollback_reason` 字段
3. 在 phase report 写明 rollback 原因
4. 不改 cc-akb-search status(仍 active/L1)

## 不做的事

- 不在本 plan 改 cc-akb-search 的 L1 floor 状态
- 不改 readonly contract
- 不影响 5 工具 active 状态

## 时间表

- 2026-05-15 之后:开始观察期满评估
- L2 v2 与 search quality v0 可并行(L2 v2 也包含 cc-akb-search)

## 启动条件

用户显式说"开始 search quality v0 plan"。

## 链接

- L1 floor closure:`../../akb2-toolbox-closure/2026-05-08-p1e-delta/`
- A4 fixture pack(L1 baseline):`../../akb2-toolbox-evals/p1e-cc-akb-search-a4/`
