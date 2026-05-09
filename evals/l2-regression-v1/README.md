# L2 Regression v1 — 占位 README

**状态**:planning(尚未实施)
**先决条件**:Toolbox L1 Floor Closure 2026-05-08 已 closed
**触发**:用户明确开 L2 regression v1 plan 后再 implement

## 范围

L2 regression 第一批,选 **cc-local-llm + cc-skill-stocktake** 两个 L1 工具:

- 跳过 cc-akb-search(刚刚冻 L1,runtime 表面稳定不到 24h;观察 7 天后再纳入 L2 v2)
- 跳过 cc-executable-plan / cc-review(已是 L2,L2 regression 走 schema-change 触发,不在本批)

## 设计原则

- 每个工具 8-12 fixture,覆盖:成功路径、失败路径、边界条件、空结果、schema 验证、runtime read-only 边界
- **assert-only**:本 v1 只输出 recommendation,不直接改 `eval_level: L2`
- 代码修改(若有)需单独 patch plan 走 review

## 不做的事

- 不直接升 cc-local-llm/cc-skill-stocktake 到 L2(留给后续手动批准)
- 不打破 L1 floor 的 0 写入合约
- 不引入 automation

## 启动条件

用户显式说"开始 L2 regression v1 plan"。

## 链接

- L1 floor closure:`../../akb2-toolbox-closure/2026-05-08-p1e-delta/`
- Phase C report:`AKB2.0/docs/phase-c-reports/toolbox-l1-floor-closure-2026-05-08.md`
