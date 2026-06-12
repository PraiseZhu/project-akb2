# AKB2 内容质量循环主链路 Master Plan v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

日期:2026-06-12
状态:draft
负责人:Claude 执行;Codex cross-review

> 规格输入(参照 spec1-2):`docs/constitution.md` v1.1.0(8 原则)+ `specs/content-quality-loop/spec.md`(FR-001~018 / SC-001~008 / 4 轮澄清)。
> 设计权威:`docs/loops-design-decisions.md`(7 循环 4 Plan + §7 循环 1/7 + §11 源策略)+ `docs/main-pipeline-golden-sample.md`(14 步验收基准)。
> 本 plan = 4 Plan 拆分中的 **Plan 1**:循环 1 全链 + 循环 7 契约骨架 + 修宪 + 校准出厂。

---

## 1. 目标链

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路,提升复杂任务的计划、审查、执行、验收效率,并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准,保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   AKB2 内容质量循环(循环 1)全链上线:URL 入队 → 抓取 → raw 锚点化 → 准入 → 提炼 → 三层质量评估 → 五闸升格 → 收尾回链,全程无人工介入;循环 7 契约骨架(账本/签名/适配卡/watchdog/隔离区)同期就位;以 20 条校准语料(含 2 负样本)出厂验收,产出与黄金样张同级。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN:`ai_infra`
- REVIEW_FOCUS:`fail-closed 七段链完整性(constitution P3); 量规三层与缺陷清单可定位性(P6); maker/checker 异族分离与兜底(P7); 有界循环与预算具名常量(P1/P4); vault 写边界与修宪前提议模式(P5/FR-009)`
- Domain-specific notes:
  - 全部 SC 验收必须对照 `docs/main-pipeline-golden-sample.md` 14 步"标准"列
  - 升格逻辑在 vault 修宪生效前必须停在提议状态(运行时开关,默认 off)
  - 新增 launchd plist 必须人工确认安装,agent 不得自行注册(constitution 自动化纪律)

---

## 3. 成功标准(SC)

> Anti-fill:每条 SC 绑定命令/路径/输出断言;对应 spec SC-001~008 的可执行化翻译。

- [ ] SC-1(spec SC-003):黄金样张 URL 端到端重跑产出结构等价
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python3 -m pytest tests/quality/test_golden_replay.py -q`
  - expect: exit 0
- [ ] SC-2(spec SC-002):2 个负样本 100% 拒于准入段,且账本显示零提炼调用
  - verify (method: test): `python3 -m pytest tests/quality/test_negative_samples.py -q`
  - expect: exit 0
- [ ] SC-3(spec SC-001):校准语料端到端,≥90% 正样本在 ≤3 修复轮内走完全链
  - verify (method: command): `python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python3 -c "import json;r=json.load(open('reports/calibration/factory.json'));assert r['positive_pass_rate']>=0.9 and r['negative_reject_rate']==1.0;print('PASS')"`
  - expect: `PASS`
- [ ] SC-4(spec SC-004/005):每篇内容七段生命线完整落账,无人工介入事件
  - verify (method: assertion): `python3 -m pytest tests/loops/test_ledger_lifeline.py -q`
  - expect: exit 0
- [ ] SC-5(循环 7 骨架):账本 schema、故障签名 schema、适配卡格式 spec、循环 1 适配卡 v1、watchdog 全部存在且 schema 自洽
  - verify (method: command): `test -f schema/loop-ledger.schema.json && test -f schema/failure-signature.schema.json && test -f schema/repair-adapter-card.schema.json && python3 -m json.tool schema/loop-ledger.schema.json >/dev/null && test -f skillpack/quality/repair-adapter-card.yaml && test -x scripts/akb2-watchdog.sh && echo PASS`
  - expect: `PASS`
- [ ] SC-6(工程门):测试与 lint 全绿
  - verify (method: command): `python3 -m pytest -q && ruff check src tests && echo PASS`
  - expect: `PASS`
- [ ] SC-7(FR-009 修宪开关):vault 修宪未生效时升格停在提议模式
  - verify (method: test): `python3 -m pytest tests/quality/test_promote_gate_switch.py -q`
  - expect: exit 0
- [ ] SC-8(源验证报告):76 源逐源 feed 验证 + 试抓产出 sources-ledger 初版
  - verify (method: command): `python3 -c "import json;rs=[json.loads(l) for l in open('reports/source-validation/sources-ledger.jsonl')];assert len(rs)>=70;print('PASS')"`
  - expect: `PASS`

---

## 4. 非目标(NON_GOALS)

- 循环 2/3/4/5/6 的实现(Plan 2-4 范围;本 plan 只交付循环 7 的契约+骨架,不交付修复 agent 行为引擎)
- 翻译(2 星才触发,休眠)
- API 查询日志与检索效用(Plan 3)
- 源池扩充到 200(冷启动挖掘批次属 Plan 1 后运营任务,本 plan 只交付 76 源验证)
- launchd plist 的实际加载(产出 plist 文件 + 安装命令文档,加载由用户执行——agent 不得自行注册自动化触发器)
- 修宪文本的最终批准(产出修订草案 + Sync Impact,批准权在用户)

---

## 5. Step 拆分

| Step | 名称 | owned_paths(相对 TARGET) | blocked_by | can_parallel_with | 主要 SC |
|------|------|--------------------------|-----------|-------------------|---------|
| 1 | 循环 7 契约骨架(账本/签名/适配卡 schema + watchdog + 隔离区) | `schema/loop-*.json`, `schema/failure-signature.schema.json`, `schema/repair-adapter-card.schema.json`, `src/akb2/loops/`, `scripts/akb2-watchdog.sh`, `tests/loops/` | — | — | SC-4, SC-5 |
| 2 | 抓取与 raw(源配置/发现层/抽取层/raw 锚点化/源验证报告) | `config/main_sources.yaml`, `src/akb2/quality/intake/`, `tests/quality/test_intake*.py`, `reports/source-validation/` | step 1 | step 3 | SC-8 |
| 3 | 准入与提炼(准入硬判定/DeepSeek 两段式/skill 包) | `src/akb2/quality/admission.py`, `src/akb2/quality/distill.py`, `src/akb2/quality/llm_router.py`, `skillpack/quality/`(适配卡除外), `tests/quality/test_admission*.py`, `tests/quality/test_distill*.py` | step 1 | step 2 | SC-2 |
| 4 | 质量评估与修复(A 结构校验/B 评分器/定点修复循环) | `src/akb2/quality/checks_structural.py`, `src/akb2/quality/grader.py`, `src/akb2/quality/repair.py`, `tests/quality/test_checks*.py`, `tests/quality/test_grader*.py`, `tests/quality/test_repair*.py` | step 1 | step 5 | SC-1(部分) |
| 5 | 升格与收尾(五闸/修宪草案/提议模式开关/索引回链/快照) | `src/akb2/quality/promote.py`, `tests/quality/test_promote*.py`, `docs/vault-amendment-draft.md` | step 1 | step 4 | SC-7 |
| 6 | 常驻调度与校准出厂(daemon/launchd 产物/校准语料/出厂验收/适配卡 v1) | `src/akb2/quality/runner.py`, `src/akb2/quality/calibration.py`, `scripts/akb2-main-pipeline.sh`, `launchd/com.akb2.main-pipeline.plist`, `evals/main-pipeline-quality/`, `skillpack/quality/repair-adapter-card.yaml`, `tests/quality/test_runner*.py`, `tests/quality/test_golden_replay.py`, `tests/quality/test_negative_samples.py` | step 2, 3, 4, 5 | — | SC-1, SC-3, SC-6 |

---

## 5a. Dispatch Map / Wave Contract(MANDATORY)

### Planning dispatch(本次 /gd plan 阶段)

```
DISPATCH_MAP_PATH: plans/gd/2026-06-12-content-quality-loop/dispatch/dispatch_map.json
VALIDATE_CMD: python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-dispatch.py" plans/gd/2026-06-12-content-quality-loop/dispatch/dispatch_map.json
```

### Wave Matrix(planning)

| Wave | Tracks(同 wave 可并行) | 并行前提验证 |
|------|------------------------|-------------|
| w1 | t1-draft-steps-1-3, t2-draft-steps-4-6(并行,child_agent_count=2) | owned_paths 不重叠(各管各的 step plan 草稿);can_parallel_with 双向声明;无 blocked_by |

### Wave Matrix(execution,供后续 /gd execute 引用)

| Wave | Steps | 并行前提验证 |
|------|-------|-------------|
| e1 | step-1(串行,child_agent_count=1) | 契约地基,一切的前置 |
| e2 | step-2, step-3(并行,child_agent_count=2) | owned_paths 不重叠;raw 文件格式由黄金样张 14 步预先锁定,接口无交叉写 |
| e3 | step-4, step-5(并行,child_agent_count=2) | owned_paths 不重叠;两者均以 step-1 schema + 黄金样张 fixture 为输入 |
| e4 | step-6(串行,child_agent_count=1) | 集成段,blocked_by 全部前置 step |

---

## 6. 边界(修改 / 不修改)

修改:
- `src/akb2/quality/**`、`src/akb2/loops/**`(新建)
- `schema/loop-ledger.schema.json`、`schema/failure-signature.schema.json`、`schema/repair-adapter-card.schema.json`(新建)
- `config/main_sources.yaml`(新建)、`skillpack/quality/**`(新建)
- `scripts/akb2-watchdog.sh`、`scripts/akb2-main-pipeline.sh`、`launchd/com.akb2.main-pipeline.plist`(新建,不加载)
- `evals/main-pipeline-quality/**`、`tests/quality/**`、`tests/loops/**`、`reports/source-validation/**`
- `docs/vault-amendment-draft.md`(修宪草案,批准后由用户侧程序应用到 vault)

不修改:
- 旧 `/rev` 任何 artifact
- `/Users/praise/.claude/**`
- vault 任何文件(本 plan 产草案不直接写 vault;管线运行期写 staging/raw 属运行时行为,遵循 vault.toml,不在本 plan 的 build 改动内)
- 既有 briefing / video / feishu_sync 链路代码
- `src/akb2/gate/`、`src/akb2/lint/`(五闸复用现有 CLI,不改其判定逻辑)

---

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| 修宪未获批,自动升格违反 vault 不变量 3 | promote 模块默认提议模式(`AKB2_AUTO_PROMOTE=0`),开关翻转前置条件=修宪文本进入 vault(SC-7 测试钉死) |
| DeepSeek/GPT-5.4 API 波动导致测试不稳定 | 全部单测用 fixture LLM 响应;真 API 只在校准跑(SC-3)与冒烟;裁决方不可用挂起逻辑单独测(FR-008) |
| 源验证发现大量死源,76 进池数不足 | SC-8 阈值定 ≥70;淘汰原因写 sources-ledger;缺口由后续挖掘批次补(非本 plan 范围) |
| 串行单工 daemon 写文件与 EOD 系统脚本冲突 | 管线只写自身 owned 目录 + vault staging;不碰 git;launchd 产物不自动加载 |
| 量规/评分器把校准语料全打回(出厂死锁) | 校准报告区分"管线缺陷"与"语料不当";语料替换需记录理由,不许为达标降量规 |

---

## 8. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python3 -m pytest tests/loops tests/quality -q          # 单元+契约测试(fixture LLM)
ruff check src tests                                     # lint
python3 -m pytest tests/quality/test_golden_replay.py -q # SC-1 样张回归
python3 -m pytest tests/quality/test_negative_samples.py -q  # SC-2 负样本拒收
python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json  # SC-3 出厂(真 API)
bash scripts/akb2-watchdog.sh --self-test                # SC-5 watchdog 自检
```

---

## 9. Assumptions

- 模型阵容已锁:deepseek-v4-pro 提炼 / gpt-5.4 全量复核 / gemini-flash 兜底;经 litellm 风格 OpenAI 兼容端点路由,key 由用户环境提供
- 黄金样张 6 文件已在 vault 与 14 步文档中固化,作为 fixture 引用(只读)
- 抽取工具 defuddle / Scrapling 0.4.8 / Docling 已实机验证在装;Firecrawl 已裁撤
- 部署目标 Mac mini 24h 常驻、串行单工;launchd 加载由用户执行
- 前 50 条自动入库留快照(FR-015);抽检阈值 80%/周 3 篇为初值,参数级可调
- 76 源清单以 `docs/loops-design-decisions.md` §11 + 会话记录为准,验证报告以实测为准
