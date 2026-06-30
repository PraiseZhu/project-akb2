# AKB2 质量修复执行报告（WP-0..WP-4 + SC-6）

> 执行 worker: GLM-5.2 max (claude-code)。分支 `fix/vault-path-icloud-trap`。
> 权威计划: `reports/akb2-quality-repair/03-repair-plan-v3.md`。
> 执行日期: 2026-06-30。WP-5（SC-5/SC-7 端到端校准）由 lead 接手。

## 0. 分支与 commit 清单

分支: `fix/vault-path-icloud-trap`（基于 7e022a6，含 vault-fix f50ade7 + 我的 WP-0..4）。
**未 push、未开 PR、未碰 main**（lead 后续 rebase 到新 main b684a78 + drop 冗余 + 丢弃 pyproject 冗余）。

| commit | WP | 标题 |
|--------|----|------|
| 6611c6b | WP-0 | 度量校正 + corpus 复核: deduped_funnel + failure_class 全链 + repair_acceptance |
| 2794fbd | WP-1 | 上游 raw 去 chrome: extract 输出后剥 chrome + 注入锚点前剥 chrome |
| da135d5 | WP-2 | admission 机制信号准入门 + 多拒因校准 |
| 86e9241 | WP-3 | distill 提炼内核: 禁 concept fallback 造假机制 + F4 前移 + frontmatter 同源 resolve |
| cbabc28 | WP-4 | repair 收敛护栏: identity 单调 + 空转早停 + 必备节 guard |
| c54f327 | fixup | WP-3 fixup + SC-6 env-stable（scanner broaden + frontmatter 保守化 + env 测试稳定）|

## 1. 改动文件清单（owned_paths 内）

**源码**:
- `src/akb2/quality/chrome_strip.py`（新）— 共享 strip_chrome_blocks helper
- `src/akb2/quality/calibration.py` — deduped_funnel + raw_content_sha256 + failure_class 全链 taxonomy + 独立 repair_acceptance（不动 factory_acceptance/CLI exit）+ compose/inject 注入前剥 chrome
- `src/akb2/quality/intake/extract.py` — _apply_chrome_strip 在 defuddle/scrapling/docling 3 分支 wc 判定前剥 chrome + 重算 wc + fail-closed
- `src/akb2/quality/admission.py` — _check_mechanism_signal（仅 concept，确定性为主+LLM 窄仲裁）+ failed-list 映射 no_mechanism_signal
- `src/akb2/quality/distill.py` — D8 concept fallback 扫真实组件 + F4 前移 + frontmatter 同源 resolve（不写悬空 [[...]]）
- `src/akb2/quality/repair.py` — identity 单调护栏 + effective_rounds + 连续空转早停 + 必备 H3 guard

**语料/测试**:
- `evals/main-pipeline-quality/corpus.yaml` — 20 条 audit 块（17 mechanism_present=true / 1 needs_adjudication=orchestration-tax / 2 negative=no_mechanism）
- `tests/quality/test_calibration_stats.py`（新）、`test_intake_chrome_strip.py`（新）、`test_admission_mechanism_gate.py`（新）、`test_distill_mechanism_extract.py`（新）
- 扩 `test_admission_positive/negative/observability.py`、`test_repair_loop.py`、`test_distill_must_cover.py`
- env 稳定化: `test_intake_extract.py`（scrapling mock defuddle）、`test_intake_orchestrator.py`（feedparser importorskip）、`test_llm_router.py`（vault plist skip）、`test_runner_lifecycle.py`（#5 vault index mock）

## 2. SC 逐条 PASS 证据

### SC-0（度量）— PASS
- `calibration.py` `_init_report` 含 `deduped_funnel` + 每条 `failure_class!=null`（pass→"none"，pipeline 失败按 failed_node/error_class 映射覆盖 admission/distill/repair/grader/promote）+ 独立 `repair_acceptance` 块（与 factory_acceptance/CLI exit 解耦）。
- `corpus.yaml` 20 条 audit 块完整 + 迁移表（17 true / 1 needs_adjudication / 2 negative=no_mechanism）。
- `test_calibration_stats.py`: 去重 hash（同正文不同 fetched_at dedup=1，排除 frontmatter/TOC/anchor/fetched_at/chrome）+ failure_class 全链非 null + repair_acceptance 解耦 + needs_adjudication 不计 negative + procedural 0 通过硬 fail。
- 测试绿（SC-6 内）。

### SC-1（raw 去 chrome）— PASS
- `test_intake_chrome_strip.py`: chrome_strip helper 单元（Go back/Share/(#)/纯图/空href/导航链接块/零回归/idempotent）+ extract production 链（剥 chrome+重算 wc+fail-closed 剥后<200）+ calibration 两链（_compose_raw_content/_inject_anchors_into_raw 注入前剥 chrome，chrome 不进锚点，§A 指向真实正文）。
- 测试绿（SC-6 内）。

### SC-2（admission 机制门）— PASS
- `test_admission_mechanism_gate.py`: 无机制散文 fail 于 mechanism_signal（no_mechanism_signal，可观测 error_class）+ 有机制源过（≥3 命名组件）+ procedural 不受 mechanism_signal 门（how_tendency 不误拒）+ novelty 不误拒真新内容 + LLM 不绕过确定性反信号 + 信号识别不可用 fail-closed + 多拒因独立 fixture。
- 测试绿（SC-6 内）。

### SC-3（distill 提炼内核）— PASS
- `test_distill_mechanism_extract.py`:
  - F4 extract 即判（<3/孤立/重复名 raise，3 唯一+连边过）✓
  - D8 concept no-mechanism 不被 fallback 救活（raw 无≥3 组件→fail-closed，默认 AKB2_MAKER_FALLBACK=1 下）✓
  - concept fallback 用真实组件（非硬编码 loop trigger/ledger）✓
  - idx5 poisoned trace: concept fallback how_steps=[]+params={}，不生成 schema/ledger/max_repair_rounds 假步骤 ✓
  - WP-3.4 frontmatter 同源 resolve: 悬空 frontmatter related 不产 real_link（gate `_graph_readiness`=="none"），≥1 真实 resolved related 产 real_link ✓
  - interpretation_entailment: 假步骤移除（poisoned trace 源头消除，grader entailment 兜底）✓
- 测试绿（SC-6 内）。WP-3.1 局部化到 distill.py（不碰 runner.py，lead 认可）。

### SC-4（repair 收敛护栏）— PASS
- `test_repair_loop.py`（45 tests）:
  - identity 单调（新 identity⊆prev + 总数严格下降才接受；同数换缺陷/新增 identity→回退）✓
  - 必备节保护（`### 操作步骤` H3 被吞→独立 guard 回退，不依赖 _verify_unchanged_sections）✓
  - 空转不计有效预算（effective_rounds 仅严格下降计入；全空转→effective=0）✓
  - 连续空转早停（consecutive_idle≥2→repair_idle_early_stop fail-visible）✓
  - attempts 硬上限（非空转严格下降不早停，耗尽 MAX_REPAIR_ROUNDS→distill_no_converge）✓
- 测试绿（SC-6 内）。

### SC-6（质量门）— PASS
- `pytest -m 'not integration'`: **1785 passed, 11 skipped, 0 failed**（6 deselected integration）。
- `ruff check src tests`: **0 error**。
- 11 skipped 均为 env 依赖（feedparser 未装 / vault 未挂载 / plist 模板 / integration 真机），非代码缺陷。

## 3. SC-5 / SC-7（端到端校准 + 达标实证）— lead 接手

WP-5 由 lead 主导（涉及真实 vault + 抽检真候选 + 跑 calibration）。
- 我已就绪的 WP-5 输入: `corpus.yaml` mechanism_present 标注 + `calibration.py` repair_acceptance（mechanism_present=true 子集 positive_pass_rate≥0.60 + 分层 live/fixture/concept/procedural + procedural 0 通过硬 fail + needs_adjudication 不计分母）。
- 重跑命令: `python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory-postfix.json`（proposal, AKB2_AUTO_PROMOTE=0）。

### SC-5 测量基建就绪验证（worker 已做，不依赖 LLM）

worker env 缺 `DEEPSEEK_API_KEY`（distill/grader 不可用），无法跑实际 calibration（故 SC-5 实跑由 lead 接手）。
但 SC-5 的**测量基建**已用真实 corpus 标签验证（合成结果跑 `_compute_statistics`）：

- corpus audit 块完整: 20/20 条含 `{mechanism_present, review_status, reviewers, ...}`
- mechanism_present 分布: true=17 / false=2(negative no_mechanism) / null=1(needs_adjudication=orchestration-tax idx 11)
- repair_acceptance 计算正确:
  - `mechanism_present_true_total=17`，`positive_pass_rate=0.765`（≥0.60 concept 起步线 ✓）
  - `negative_reject_rate=1.0`（含 no_mechanism 负样本 ✓）
  - 分层全出: live(10/14) / fixture(3/3) / concept(7/9) / procedural(6/8)
  - `procedural_zero_pass_hard_fail=False`（procedural 有通过，不触发硬 fail ✓）
  - `needs_adjudication`: count=1, indices=[11]（orchestration-tax 排除出 mp_true 分母 ✓）
  - `repair_acceptance_gate=True`，`AUTO_PROMOTE=0` ✓
- `deduped_funnel`: unique=20/total=20（合成无重复）
- 每条 `failure_class != null`（全链 taxonomy 生效）

→ lead 拿到 LLM key 跑实际 calibration 时，SC-5 测量基建（deduped_funnel/failure_class/repair_acceptance/分层/needs_adjudication/procedural 硬 fail）已验证就绪，只需替换合成结果为真实 pipeline 结果。

## 4. corpus needs_adjudication 条目（等 lead+审稿确认）

- **index 11 (orchestration-tax)**: notes 描述"trade-off 分析"，未读全文无法确认 ≥3 命名组件+因果边。保持 `expected: ingest` 不移 reject，`review_status: needs_adjudication`，不计入 mechanism_present=true 分母。lead+2 reviewer 读取全文确认后：true→入 mp_true 分母 / false→移 reject+no_mechanism。

## 5. 残余风险与 notes

- **分支整理由 lead 统一做**: fix/vault-path-icloud-trap 基于旧 main 7e022a6，需 rebase 到新 main b684a78 + drop 冗余 f50ade7（vault-fix，若新 main 已含则 drop）+ 丢弃 pyproject 冗余。我未做任何 rebase/push/main 操作。
- **stash 提示**: 切分支时我用 `git stash --include-untracked -m "wp4-wip + main-workflow-uncommitted"` 保存了 main 工作区的未提交改动（含 main 工作流的 pyproject/main_sources 等冗余 + 我的 WP-4 wip）。WP-4 wip 已通过 `git checkout stash@{0} -- repair.py test_repair_loop.py` 取回并提交；stash 里剩余的 main 工作流冗余改动由 lead 处理（可 `git stash drop` 丢弃或按需恢复）。
- **WP-3.4 frontmatter filter 保守化**: `has_resolver=bool(known) or bool(pending)`——空 known（vault 未挂载/无法验证）时走 legacy（_infer_concepts fallback），非空时才严格过滤。生产（vault 挂载+有概念）严格防 false real_link；test env（空 vault）零回归。
- **env 测试稳定化非 WP 引入**: scrapling(defuddle 已装)/feedparser(未装)/vault plist(未挂载) 3 类 env 依赖测试，我用 mock/importorskip/skip 隔离，不改被测源码行为。
- **WP-3 未覆盖**: body 未知 target 降级为普通文本（_build_body 改动）未做——由 A5 self-gate（check_a5_wikilinks）catch 悬空 body wikilinks（fail-closed），现有 A5 测试覆盖。

## 6. 偏航自检（计划 §6）

- AUTO_PROMOTE 全程 0（repair_acceptance.AUTO_PROMOTE 记录 env，未改）✓
- 未为让测试过而改达标标准/rubric（grader 模型族/rubric 权重/weighted_total 未碰）✓
- 未刷 factory 数字（repair_acceptance 独立，factory_acceptance 不动；SC-5 分层+holdout 由 lead）✓
- 路线服务"产出达 wiki 标准"原目标（D8 造假机制根因修掉，frontmatter 不伪装 real_link）✓
