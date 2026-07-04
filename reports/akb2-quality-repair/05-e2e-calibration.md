# E2E 端到端校准报告（WP-5 / SC-5 / SC-7）

> 分支 fix/vault-path-icloud-trap @ a270e3b。真实 pipeline 跑（deepseek distill → gpt-5.4 grader → repair → promote）。
> LLM key: `~/.config/akb2/github-briefing.env`（AI_GATEWAY_API_KEY，与线上 daemon 同源）。
> 命令: `AKB2_AUTO_PROMOTE=0 python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory-postfix.json`
> 日期: 2026-07-01。

## 1. 真实结果（不美化）

**SC-5 未达**。`repair_acceptance`:
- `positive_pass_rate = 0.000`（mechanism_present=true 子集 18 条，**0 通过**）——目标 ≥0.60，**未达**。
- `negative_reject_rate = 1.0`（2/2 负样本全拒）✓。
- `AUTO_PROMOTE = 0`（proposal 模式，不写 active）✓。
- `procedural_zero_pass_hard_fail = False`（但 procedural total=0——**无 procedural 候选产生**，所有正样本要么 admission 拒要么 distill fail，未到 lane 分流的 procedural 路径）。
- 分层: live 0/15、fixture 0/3、concept 0/15、procedural 0/0。

`factory_acceptance = False`（出厂判定 FAIL）。

## 2. failure_class 分布

| failure_class | 条数 | 说明 |
|------|------|------|
| distill_extract_failed | 12 | LLM extract 失败 + fallback 也 fail |
| admission_reject | 5 | 准入拒（no_mechanism_signal / novelty / topic_relevance / non_knowledge）|
| extraction_failure | 1 | m3 material design fetch_failed（defuddle/network）|
| none | 2 | 负样本正确拒收（passed=True）|

## 3. 根因分析（真实 e2e 暴露，单元测试用 mock LLM+构造数据未覆盖）

### 3.1 distill_extract_failed 12/18（主导失败）—— 两层叠加

**层 1: LLM extract 失败（预存，非本轮 WP 改动）**
- `maker_primary_error`: "语义 segment 抖动 fail-closed（2 次交集为空）: how_params 多取（2 次）交集为空，语义 segment 连续不一致"。
- 即 `extract_claims` 的 SEMANTIC_RETAKE 机制：2 次 LLM 抽取输出交集为空 → fail-closed。LLM 非确定性导致，pre-existing。

**层 2: P1-A fallback 太严（本轮回归点）**
- LLM extract 失败后调 `build_deterministic_extract_result`（maker fallback）。
- P1-A 修复后 fallback 要求：raw 有 ≥3 含机制实体名的编号组件 + **真实因果/依赖/触发语言**才建边（禁排版顺序边）。
- 真实内容不满足:
  - ag2 captain fixture: 4 命名组件（Captain/Executor Pool/Task Graph/Sub-Agent Lifecycle）但无显式"X dispatches to Y"因果语言连接全部 → `F4：命名组件无因果边连接` → fail。
  - live 文章（addyosmani/lilianweng/crewai/...）: fetched 内容无编号 H2/H3 组件标题 + 无连续编号实体列表项 → `raw 仅 0 命名机制组件 < 3` → fail。
- **P1-A 是回归**: P1-A 前 fallback 用硬编码 loop 组件（trigger/selector/executor/ledger）+ 排版顺序边能"救活"这些（D8 造假机制 bug）；P1-A 修正过度 → 真内容也救不活。c54f327（loose，救活政策承诺）→ P1-A（strict，真机制也 fail）——两极，需中间态。

### 3.2 admission_reject 5

- `no_mechanism_signal` 2 条（idx 0 addyosmani long-running-agents, idx 8 orchestration-tax）: WP-2 mechanism_signal 确定性扫描误拒真机制源（fetched 内容的机制组件不以编号标题/连续编号列表形态出现 → scanner 信号不足 → fail）。
- `novelty` 2 条（idx 6, 10）: 新颖度误拒（pre-existing 倾向 + LLM 判定）。
- `topic_relevance` 1 条（idx 8 组合）。

### 3.3 extraction_failure 1
- idx 15 m3 material design: `fetch_failed`（defuddle "Unterminated string" + 网络）。

## 4. SC-7 抽检

**未达**。0 真实通过候选（18 正样本全 fail/reject），无法抽 ≥3。procedural 候选 0 产生（procedural 0 通过硬 fail 未触发，因无 procedural 候选到达）。

## 5. 与合成 0.765 的对比

| 维度 | 合成（SC-5 基建验证）| 真实 e2e |
|------|------|------|
| positive_pass_rate | 0.765（假设 pass）| **0.000** |
| 分母 | 18（corpus 标签）| 18（真实）|
| 差异原因 | 合成假设 mp=true 正样本 pass（除 i%5==0）| 真实 LLM extract 语义抖动 fail + P1-A fallback 太严 → 全 fail |
| 结论 | 基建计算逻辑正确 | 真实 pipeline 满足不了严格 F4+fallback，需放宽 |

合成验证的是**测量基建正确**（repair_acceptance 字段/分层/gate 判定对）；真实 e2e 暴露的是**pipeline 满足不了严格门**——两件事，合成不能替代真实。

## 6. 达标结论

- **SC-5 未达**（positive_pass_rate=0.000 < 0.60）。
- **SC-7 未达**（0 真候选 < 3）。
- **负样本拒收正常**（1.0）+ **AUTO_PROMOTE=0**（不写 active）✓。

## 7. 修复方向（供 lead 裁决，不在本轮 e2e 改）

1. **P1-A fallback 中间态**: fallback 的真实因果边要求过严。建议: raw 有 ≥3 含实体名编号组件时，**允许顺序连边作为保守结构推断**（c54f327 形态）但**仅限实体名组件**（区别政策承诺动词列表）——即撤销 P1-A 的"禁排版顺序边"，保留"实体名过滤"。这样 ag2 captain（4 实体组件）能过 fallback，政策承诺（0 实体组件）仍 fail。
2. **F4 连通性对 LLM extract 放宽**: LLM extract 产出的 causal_edges 常不全连通；可考虑 F4 对 LLM extract 只要求 ≥3 组件（不卡连通），连通性留给下游 integrity_floor（现状）——即撤销 WP-3.2 F4 前移的连通性部分，保留计数。
3. **mechanism_signal 放宽**: 扫描信号不足时交 LLM 窄仲裁（现状已有），但确定性反信号阈值可能需调（fetched 内容机制组件形态多样）。
4. **LLM extract 语义抖动**（pre-existing）: SEMANTIC_RETAKE 交集空 → fail-closed；可考虑放宽重取策略或增加 retake 次数。

## 8. 数据产物

- `reports/calibration/factory-postfix.json`（真实 20 条逐条结果 + repair_acceptance + deduped_funnel + failure_class 全链）。
- staging ledger（`<staging>/quality-ledger.jsonl`）含 79 条 distill_extract w/ maker_primary_error 事件。
