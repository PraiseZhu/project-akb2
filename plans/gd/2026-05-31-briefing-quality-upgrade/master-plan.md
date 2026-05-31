# AKB2 简报质量与捕获升级 Master Plan v2

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2 — 社交日报系统（本计划目标源为 AKB2，非 GD 自身）
TEMPLATE_KIND: gd-master-plan

日期：2026-05-31
状态：draft
负责人：Claude 执行；Codex 可选 cross-review
版本说明：v2 整合对抗式自审的缺漏/冲突/阻塞修订（排序修复、eval 新建、需求补全、维度 note）

---

## 1. 目标链

```text
PROJECT_GOAL: AKB2 社交日报真实体现 AI 社区高质量内容，且高质量内容第一时间被捕获
CHAIN_GOAL:   把简报从「白名单熟人 + 16 词关键词堆词」双窄门，换成「语义相关度 + 社区反响」驱动；并把已修复代码上线到调度链路
PHASE_GOAL:   见各 Phase / Step
```

> 背景：本 session 已修复并验证 8 项 bug（P0 主题映射 / P1 句数 / P2a 链接 / P2b 翻译模型链 / P3a lang 分流 / P3b frontmatter / P3c reserve 配置 / 时间窗 2→3），共 43 commit 在 worktree 分支 `worktree-akb2-briefing-zh-pipeline`，**已验证可 ff 干净合并到调度分支**，尚未上线。全链路盘查另发现 scoring 本体两道窄门（author_authority 看脸 + topic_relevance 堆词）+ BANNED 误杀 + novelty 精确去重 + engagement 累计值 等耦合点。

---

## 2. Review 对齐

- REVIEW_DOMAIN：`app_code`
- REVIEW_FOCUS：打分算法正确性（语义替换+权重重平衡）; 嵌入链路可靠性与降级; 多语言/社区覆盖; 调度上线安全; eval 量化闸门
- Domain-specific notes：动 scoring 本体（author_authority / topic_relevance / BANNED_KEYWORDS / novelty / engagement）属重新定义产品行为，需用户显式授权解锁；改算法必须用 eval harness 量化「新排序优于旧」，不可凭主观；P1 多步同改 `scoring.py`，owned_paths 重叠 → 强制串行单一工作流（见 §5a）。

---

## 3. 成功标准（SC，anti-fill 绑定）

- [ ] **SC-1**：worktree 分支 ff 合入调度分支 `feature/automation-pipeline-v2`；下次 07:30 自动跑产出 frontmatter `evidence_paths: social/account_collector`（非 dry_run）、全中文、各主题挂不同推文
  - verify (command): 次日 `grep -E "evidence_paths|dry_run" "<vault>/AI TRENDS/Social/<date> Daily Briefing.md"`
  - expect: 含 `social/account_collector`，不含 `dry_run`
- [ ] **SC-2**：`src/akb2/briefing/embeddings.py` 批量调网关 `/v1/embeddings` + 文本 hash 磁盘缓存 + 失败抛错
  - verify (test): `pytest tests/briefing/test_embeddings.py`（mock 网关）覆盖批量/缓存命中/失败抛错
  - expect: 全绿
- [ ] **SC-3**：离线对比脚本对今日真实 30 条输出「关键词版 vs 语义版」排序差异表 + 写入标定后归一化区间常量
  - verify (path): `reports/scoring-semantic-offline-compare-*.md` 存在且含两列排序
  - expect: 文件存在，人读确认语义版把中文/无 buzzword 高质量内容排前
- [ ] **SC-4**：`topic_relevance` 改语义（锚点 max cosine 归一化），签名不变；批量预嵌入（ScoreEngine 批量化）
  - verify (test): `pytest tests/briefing/test_scoring.py -k semantic`
  - expect: 中文/无 buzzword AI 内容 relevance > 离题内容；无逐条联网
- [ ] **SC-5**：`author_authority` 权重 0.30→≤0.10、engagement 升权；`DEFAULT_WEIGHTS` 之和 == 1.0
  - verify (assertion): `sum(DEFAULT_WEIGHTS.values())==1.0 and DEFAULT_WEIGHTS['author_authority']<=0.10`
  - expect: True
- [ ] **SC-6**：嵌入失败时 scoring 自动回退关键词版 `_topic_relevance_keywords`，pipeline 不崩
  - verify (test): 注入嵌入失败单测，`run_scoring` 仍返回分数
  - expect: 不抛异常
- [ ] **SC-7**：`BANNED_KEYWORDS` 不误杀含 exploit/hack 的 AI 安全内容
  - verify (test): AI 安全推文（含 "exploit"）`safety` ≠ 0
  - expect: safety=1.0（除非真违禁）
- [ ] **SC-8-1**：新建简报打分排序 eval harness + 标注集（~30 AI + ~15 离题）
  - verify (path): `evals/briefing-scoring/` 含 runner + 标注 fixtures
  - expect: 可一键跑出排序质量指标
- [ ] **SC-8**：eval 量化新算法排序 ≥ 关键词版（nDCG 或 top-k 命中）
  - verify (path): `reports/scoring-eval-*.md` 含新旧对比
  - expect: 新版 ≥ 旧版
- [ ] **SC-9**：scoring 旧关键词/白名单测试重写为语义/降权后断言；连带依赖分数排序的测试一并核对
  - verify (command): `pytest tests/`
  - expect: 全仓库绿
- [ ] **SC-10**：抓取层 fetch en+zh 合并（`_DEFAULT_TIER` 不再仅 en）
  - verify (test): `test_account_collector` 断言 collect 含 en+zh
  - expect: 绿；真跑含中文大号内容
- [ ] **SC-11**：`novelty` 改语义近似去重（cosine>阈值判重），复用 P1-1 嵌入缓存
  - verify (test): 改写转发的同义推文 novelty=0
  - expect: 绿
- [ ] **SC-12**：`engagement` 加 velocity（赞/小时）维度
  - verify (test): 高速新帖 > 同总量老爆款
  - expect: 绿
- [ ] **SC-13**：清理 06-01 英文残报；正文孤立 `🔗` 残留清零
  - verify (command): vault 无旧英文残报；`grep -c "🔗"` 新简报为 0
  - expect: 符合
- [ ] **SC-14**：needs_repair 根因调查报告（查清 gate 为何每次 needs_repair，决定修/不修）
  - verify (path): `reports/gate-needs-repair-diagnosis.md` 存在含根因
  - expect: 根因明确 + 处置建议

---

## 4. 非目标（NON_GOALS）

- 不修 gate 规则本体（仅 SC-14 调查根因，不改 gate 算法）
- 不做实时哨兵 / 盘中多次跑（产品边界决策，另立计划）
- 不做发现层突破固定名单（同上）
- 不改 schema、不写 `~/.claude/**`、不动 secrets / 调度时间

---

## 5. Step 拆分（优先级 = Phase；blocked_by 已修排序）

| Step | 名称 | owned_paths | blocked_by | 主要 SC | 价值 |
|------|------|------------|-----------|---------|------|
| **P0-1** | 上线现有修复（ff 合并到调度分支）| git 分支操作 | — | SC-1 | 最高 |
| P0-2 | 清理 06-01 残报 + 🔗 残留 + needs_repair 调查 | vault 清理 / `pipeline.py` render / `reports/` | — | SC-13,14 | 中（防 silent 退化）|
| P1-1 | 嵌入客户端 + 缓存 + 降级 | `src/akb2/briefing/embeddings.py`、`tests/briefing/test_embeddings.py` | 授权 | SC-2,6 | 高（地基）|
| P1-2 | 离线对比 + 标定 | `scripts/`、`reports/` | P1-1 | SC-3 | 高（去风险）|
| P1-3 | ScoreEngine 批量化（批量预嵌入）| `src/akb2/briefing/scoring.py` | P1-1 | SC-4 | 高（P1-4 性能前置）|
| P1-4 | 语义 topic_relevance | `src/akb2/briefing/scoring.py` | **P1-2, P1-3** | SC-4 | 高 |
| P1-5 | author_authority 降权 | `src/akb2/briefing/scoring.py` | P1-4 | SC-5 | 高（与 P1-4 同 PR 捆绑）|
| P1-6 | BANNED 精确化 | `src/akb2/briefing/scoring.py` | P1-5 | SC-7 | 高（防误杀）|
| P1-7 | 新建 eval harness + 标注集 | `evals/briefing-scoring/` | — | SC-8-1 | 高（验收前提，可早开工）|
| P1-8 | eval 验收 + 测试重写 | `tests/`、`reports/` | P1-4, P1-5, P1-6, P1-7 | SC-8,9 | 高（正确性闸门）|
| P2-1 | 接入 zh 抓取合并（**不需 scoring 授权，可独立先做**）| `account_collector.py`、`pipeline.py` | zh 账号复验(前置) | SC-10 | 中-高 |
| P2-2 | novelty 语义去重（共用 P1-1 缓存）| `src/akb2/briefing/scoring.py` | P1-6 | SC-11 | 中-高 |
| P2-3 | engagement velocity | `src/akb2/briefing/scoring.py` | P2-2 | SC-12 | 中-高（时效）|
| **P3-1** | P1/P2 完成后再次 ff 合并上线 | git 分支操作 | P1-8, P2-1, P2-3 | SC-1（再验）| 高（部署 cadence）|

---

## 5a. Dispatch Map / Wave Contract + 串行约束

```
DISPATCH_MAP_PATH: n_a
VALIDATE_CMD: n_a
```

> **scoring.py 串行硬约束**：P1-3 / P1-4 / P1-5 / P1-6 + P2-2 / P2-3 **全部改同一文件 `scoring.py`，owned_paths 重叠** → 必须作为**一条串行工作流（顺序改、可合为一个或少数几个 PR）**，**不得**拆成独立并行 task packet（会撞文件）。故 DISPATCH_MAP_PATH = n_a，不伪造并行 dispatch。
> **依赖图落实串行（自审 P1⑤ 修订）**：单一无分叉链 P1-1 → P1-3 → P1-4 → P1-5 → P1-6 → **P2-2** → **P2-3**。P2-2 `blocked_by: P1-6`（不再是 P1-1）、P2-3 `blocked_by: P2-2`，使所有改 `scoring.py` 的 task 在依赖图上也串成一条线，杜绝 P1-3 与 P2-2 并发写同文件。
> 可真正并行的只有跨文件的：P1-1（embeddings.py）、P1-7（evals/）、P2-1（account_collector/pipeline）—— 但本计划仍按串行执行，避免协调成本。

---

## 6. 边界与失败模式

必须处理：
- 嵌入网关超时（翻译阶段已遇过）→ SC-6 降级关键词版兜底，绝不因嵌入失败让简报 fail
- **P1-4（语义 relevance）与 P1-5（authority 降权）只做一半 → 效果互相抵消**（盘查结论），必须同 PR
- **P1-4 必须在 P1-3（批量化）之后** —— 否则语义 relevance 逐条联网，慢到不可用（排序修复）
- 改 scoring 本体 → 必须 eval 量化优于旧版（SC-8），否则属盲改
- **嵌入缓存共享**：P1-1 缓存 + P1-4 relevance + P2-2 novelty 嵌入同一批推文文本 → 每条只嵌一次、三处复用，不重复花钱

明确不处理：
- gate `needs_repair` 的**修复**（仅 SC-14 调查根因）
- 实时哨兵 / 发现层（产品边界，另立计划）

维度 note（深挖发现）：
- **window=3 与 recency 衰减张力**：窗口放宽到 3 天，但 recency 72h≈0.1（权重 0.10）会压低第 3 天内容（约损 0.09 分）；P1 改打分时可顺带软化 recency 衰减
- **fixture 分数陈旧**：`scored-sample.json` 是关键词时代分数，单测用存量分数不破，但 eval 必须用新算法重打分，禁用旧 fixture 分数做评估
- **部署 cadence**：P0-1 先上线已修 8 项；P1/P2 是新改动，由 P3-1 统一再次合并上线（不是一次性 deploy）

---

## 7. 阻塞与风险排序（诚实）

阻塞：
1. **「改 scoring 本体」授权**（你之前锁的 NON_GOAL）→ 卡住整个 P1 + P2-2/P2-3；需你显式解锁
2. **zh 11 账号"待复验"** → P2-1 内置 precondition（首步先真跑验证产量，见 P2-1 packet），不是独立阻塞 task；§5 表 P2-1 blocked_by 标「zh 账号复验(前置)」与此一致（Codex F1 修订）
3. ~~eval harness 不存在~~ → 已转为 P1-7 显式新建任务

风险排序：
- **P0-1 上线 + P0-2 清理：独立最高优先，零 scoring 授权依赖，已验证 ff 干净** —— 可立即做
- **P2-1 zh 抓取：不碰 scoring，不受授权阻塞，可与 P0 并行先做**
- **P1 换语义内核：受授权阻塞**；其中 P1-3→P1-4→P1-5 顺序与捆绑是硬约束
- **P2-2/P2-3：受授权阻塞**（改 scoring）

---

## 实施前 Review Checklist

- [ ] 目标链、SC、NON_GOALS 清楚
- [ ] REVIEW_DOMAIN / REVIEW_FOCUS 清楚
- [ ] owned_paths 串行约束已声明（scoring.py 多步不并行）
- [ ] 改 scoring 本体已获用户授权
- [ ] 嵌入失败降级（SC-6）+ eval 量化闸门（SC-8）已覆盖
- [ ] P1-4 排在 P1-3 之后、与 P1-5 捆绑（排序与耦合修复）
- [ ] 部署 cadence（P0-1 先上线、P3-1 再上线）清楚
