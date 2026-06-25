# AKB2 内容质量循环主链路 Master Plan v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

日期:2026-06-12
状态:draft（2026-06-17 实测修订见文末「实测修订」节：step-4 repair 需重写为 agent 爬坡，SC-3 受 repair 阻塞待解锁）
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
  - verify (method: test, build-gate): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_golden_replay.py --collect-only -q`
  - expect (build-gate): 至少 1 用例可收集（no errors）
  - verify (method: test, execution-gate-proposal): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && AKB2_RUN_INTEGRATION=1 python -m pytest tests/quality/test_golden_replay.py -q -m "integration and proposal_mode"` —— **默认门（AKB2_AUTO_PROMOTE=0）**：断言产出 proposal manifest、active 目录 tree_sha256 与运行前相同（零 active 写入），对齐 SC-7
  - expect (execution-gate-proposal): exit 0
  - verify (method: test, execution-gate-active): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && AKB2_RUN_INTEGRATION=1 AKB2_AUTO_PROMOTE=1 AKB2_AMENDMENT_APPLIED=1 python -m pytest tests/quality/test_golden_replay.py -q -m "integration and post_amendment"` —— **出厂正式门（修宪文本应用到 vault + AKB2_AUTO_PROMOTE=1 + AKB2_AMENDMENT_APPLIED=1 后必跑，非可选）**：断言 14 步结构等价含 active、索引摘要行、概念回边、lint 0
  - expect (execution-gate-active): exit 0（修宪已获用户预批——2026-06-13 会话决策：五闸即黄金样张标准机判，满足即自动入 active；出厂序列=校准通过→应用修宪→开开关→跑本门）
- [ ] SC-2(spec SC-002):2 个负样本 100% 拒于准入段,且账本显示零提炼调用
  - verify (method: test): `python -m pytest tests/quality/test_negative_samples.py -q`
  - expect: exit 0
- [ ] SC-3(spec SC-001):校准语料端到端,≥90% 正样本在 ≤3 修复轮内走完全链；校准报告明示运行模式与修宪状态
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && PYTHONPATH=src python -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json && python -c "import json;r=json.load(open('reports/calibration/factory.json'));assert r.get('total_samples',0)==20,'total!=20';assert r.get('negative_count',0)==2,'negative!=2';assert r['positive_pass_rate']>=0.9;assert r['negative_reject_rate']==1.0;assert all(e.get('repair_rounds',0)<=3 for e in r.get('entries',[])),'repair_rounds exceeded 3';assert 'promotion_mode' in r,'missing promotion_mode field';assert r.get('promotion_mode') in ('proposal','active'),'invalid promotion_mode';assert 'amendment_status' in r,'missing amendment_status field';assert r.get('amendment_status') in ('not_applied','applied'),'invalid amendment_status';assert not (r.get('promotion_mode')=='active' and r.get('amendment_status')!='applied'),'active mode requires amendment_status=applied';assert not (r.get('promotion_mode')=='proposal' and r.get('amendment_status')!='not_applied'),'proposal mode requires amendment_status=not_applied';print('PASS')"`
  - expect: `PASS`（build 期校准 promotion_mode=proposal+not_applied；出厂终态校准 promotion_mode=active+applied）
- [ ] SC-9(真实源专项):从 sources-ledger 取 3 个 reachable 真实源各抓 1 篇新内容跑全链,评估达黄金样张标准所需修复轮数
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && PYTHONPATH=src python -m akb2.quality.calibration --live-sample 3 --report-out reports/calibration/live-sample.json && python -c "import json;r=json.load(open('reports/calibration/live-sample.json'));es=r['entries'];assert len(es)==3,f'entries={len(es)}!=3';bad=[e['url'] for e in es if e.get('final_status') not in ('promoted','proposal_passed')];assert not bad,f'not reached golden standard: {bad}';assert all(e.get('repair_rounds',99)<=3 for e in es),'repair_rounds exceeded 3';print('PASS');print('rounds:',[ (e['url'],e['repair_rounds']) for e in es])"`
  - expect: `PASS` + 每篇 url 与实际修复轮数清单（作为"循环几次达标"的实测数据）
- [ ] SC-4(spec SC-004/005):每篇内容七段生命线完整落账,无人工介入事件;七段任一失败时后续段调用计数为 0 且账本落对应失败终态(fail-closed 停链)
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/loops/test_ledger_lifeline.py tests/quality/test_runner_lifecycle.py -q`
  - expect: exit 0（test_runner_lifecycle.py 必须包含 7 个停链用例：抓取/raw/准入/提炼/评估/升格/收尾各自失败时断言后续段调用计数==0 + ledger 终态为对应 failure event）
- [ ] SC-5(循环 7 骨架):账本 schema、故障签名 schema、适配卡格式 spec、循环 1 适配卡 v1、watchdog 全部存在且 schema 自洽；适配卡实例与 schema 自洽
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && test -f schema/loop-ledger.schema.json && test -f schema/failure-signature.schema.json && test -f schema/repair-adapter-card.schema.json && python -m json.tool schema/loop-ledger.schema.json >/dev/null && python -m json.tool schema/failure-signature.schema.json >/dev/null && python -m json.tool schema/repair-adapter-card.schema.json >/dev/null && test -f skillpack/quality/repair-adapter-card.yaml && python -c "import yaml,json,jsonschema; schema=json.load(open('schema/repair-adapter-card.schema.json')); instance=yaml.safe_load(open('skillpack/quality/repair-adapter-card.yaml')); jsonschema.validate(instance,schema); print('adapter-card schema OK')" && test -x scripts/akb2-watchdog.sh && bash scripts/akb2-watchdog.sh --self-test && echo PASS`
  - expect: `PASS`
  - note: 本 SC verify 命令需 step-6 完成后方可运行（repair-adapter-card.yaml 为 step-6 交付物；step-1 只交付 schema 骨架，承接 SC-5 schema 骨架部分；step-6 收口适配卡实例+全量 verify）
- [ ] SC-6(工程门):测试与 lint 全绿
  - verify (method: command): `python -m pytest tests/loops/ tests/quality/ -q && ruff check src/akb2/loops/ src/akb2/quality/ tests/loops/ tests/quality/ && echo PASS`
  - expect: `PASS`
  - note: 使用 `python`（pyenv shim）而非 `/usr/bin/env python3`（系统 3.9.6，无 pytest）
- [ ] SC-7(FR-009 修宪开关):vault 修宪未生效时升格停在提议模式
  - verify (method: test): `python -m pytest tests/quality/test_promote_gate_switch.py -q`
  - expect: exit 0
- [ ] SC-8(源验证报告):76 源逐源 feed 验证 + 试抓产出 sources-ledger 初版；每行含试抓状态字段
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -c "import json,yaml; sources=yaml.safe_load(open('config/main_sources.yaml'))['sources']; assert len(sources)==76,f'config sources={len(sources)}!=76'; config_urls={s.get('feed_url') or s.get('listing_url') for s in sources} - {None,''}; rs=[json.loads(l) for l in open('reports/source-validation/sources-ledger.jsonl')]; assert len(rs)==76,f'ledger rows={len(rs)}!=76'; unique_urls={r['url'] for r in rs}; assert len(unique_urls)==76,f'duplicate ledger urls: unique={len(unique_urls)}!=76'; assert unique_urls==config_urls,f'ledger url mismatch: extra={unique_urls-config_urls}, missing={config_urls-unique_urls}'; req_fields={'feed','url','reachable','latest_post','trial_fetch'}; bad=[str(i) for i,r in enumerate(rs) if not req_fields.issubset(r.keys())]; assert not bad,f'MISSING FIELDS at rows: {bad}'; ok=sum(1 for r in rs if r.get('reachable')); assert ok>=70,f'reachable={ok}<70'; print('PASS')"`
  - expect: `PASS`

---

## 4. 非目标(NON_GOALS)

- 循环 2/3/4/5/6 的实现(Plan 2-4 范围;本 plan 只交付循环 7 的契约+骨架,不交付修复 agent 行为引擎)
- 翻译(2 星才触发,休眠)
- API 查询日志与检索效用(Plan 3)
- 源池扩充到 200(冷启动挖掘批次属 Plan 1 后运营任务,本 plan 只交付 76 源验证)
- launchd plist 的实际加载(产出 plist 文件 + 安装命令文档,加载由用户执行——agent 不得自行注册自动化触发器)
- ~~修宪文本的最终批准~~ → **已获用户预批(2026-06-13 会话决策)**:五闸=黄金样张标准机判,满足即自动入 active。执行期仍产草案+Sync Impact 留痕,出厂序列中应用修宪文本并默认 AKB2_AUTO_PROMOTE=1(见 step-6 SC-6.10);前 50 条快照+回滚 CLI(FR-015)作为试运行兜底不变

---

## 5. Step 拆分

> **Task Packet 完整字段说明**：本表为概览（owned_paths / blocked_by / can_parallel_with / 主要 SC）。每个 step 的完整 task packet 字段（`forbidden_paths` / `required_context` / `deliverables` / `verify`）详见各 step 文件 §7，dispatch_map.json 通过 step 文件路径引用。
> **字段别名规则**：各 step §7 task packet 中 `forbidden` / `forbidden_paths` / `禁改` 语义等价；`完成定义` / `definition_of_done` 语义等价。dispatch_map 解析器须兼容上述别名，不得以字段名不一致为由拒绝解析。

| Step | 名称 | owned_paths(相对 TARGET) | blocked_by | can_parallel_with | 主要 SC |
|------|------|--------------------------|-----------|-------------------|---------|
| 1 | 循环 7 契约骨架(账本/签名/适配卡 schema + watchdog + 隔离区) | `schema/loop-*.json`, `schema/failure-signature.schema.json`, `schema/repair-adapter-card.schema.json`, `src/akb2/loops/`, `scripts/akb2-watchdog.sh`, `tests/loops/` | — | — | SC-4, SC-5(schema 骨架) |
| 2 | 抓取与 raw(源配置/发现层/抽取层/raw 锚点化/源验证报告) | `config/main_sources.yaml`, `src/akb2/quality/intake/`, `tests/quality/test_intake*.py`, `tests/quality/fixtures/intake/`, `reports/source-validation/` | step 1 | step 3 | SC-8 |
| 3 | 准入与提炼(准入硬判定/DeepSeek 两段式/skill 包) | `src/akb2/quality/admission.py`, `src/akb2/quality/distill.py`, `src/akb2/quality/llm_router.py`, `skillpack/quality/`(适配卡及 rubric/ 子目录除外), `docs/interface-contracts/llm_router.md`, `tests/quality/test_admission*.py`, `tests/quality/test_distill*.py` | step 1 | step 2 | SC-2 |
| 4 | 质量评估与修复(A 结构校验/B 评分器/定点修复循环) | `src/akb2/quality/checks_structural.py`, `src/akb2/quality/grader.py`, `src/akb2/quality/repair.py`, `skillpack/quality/rubric/`, `tests/quality/test_checks*.py`, `tests/quality/test_grader*.py`, `tests/quality/test_repair*.py` | step 1 | step 5 | SC-1(部分) |
| 5 | 升格与收尾(五闸/修宪草案/提议模式开关/索引回链/快照) | `src/akb2/quality/promote.py`, `tests/quality/test_promote*.py`, `docs/vault-amendment-draft.md`, `snapshots/` | step 1 | step 4 | SC-7 |
| 6 | 常驻调度与校准出厂(daemon/launchd 产物/校准语料/出厂验收/适配卡 v1) | `src/akb2/quality/runner.py`, `src/akb2/quality/calibration.py`, `scripts/akb2-main-pipeline.sh`, `launchd/com.akb2.main-pipeline.plist`, `evals/main-pipeline-quality/`, `skillpack/quality/repair-adapter-card.yaml`, `tests/quality/test_runner*.py`, `tests/quality/test_golden_replay.py`, `tests/quality/test_negative_samples.py`, `reports/calibration/` | step 2, 3, 4, 5 | — | SC-1, SC-3, SC-5(适配卡实例+全量 verify), SC-6, SC-9 |

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

### Wave Matrix(execution)e2 并行前提补充

step-2 与 step-3 在 e2 wave 可并行的根本前提：**step-3 build 期通过黄金样张格式 fixture 完全解耦 step-2 运行时数据**。step-3 §11 Assumptions 已锁定"raw 文件格式由黄金样张 14 步预先锁定，step-2 未完成不阻塞本 step build（fixture 解耦）"；fixture 来源：`docs/main-pipeline-golden-sample.md` 与 vault 黄金样张 raw/wiki 文件（只读）。执行者无需等待 step-2 完成即可从 fixture 验证 step-3 全部 SC。

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
- `evals/main-pipeline-quality/**`、`tests/quality/**`、`tests/loops/**`、`reports/source-validation/**`、`reports/calibration/**`(SC-3 出厂验收产物，calibration.py 写出)
- `docs/interface-contracts/`(step-3 交付的 llm_router 接口规格文档目录,新建)
- `snapshots/**`(step-5 运行期快照,SC-5.3 产物,不入 git 大文件范围)
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
| 修宪文本未应用就开自动升格,违反 vault 不变量 3 | promote 模块默认提议模式(`AKB2_AUTO_PROMOTE=0`),开关翻转前置条件=出厂校准通过+修宪文本应用到 vault(SC-7 测试钉死;SC-3/SC-9 报告 amendment_status 交叉断言兜底);用户预批仅授权"满足五闸即自动",不豁免应用顺序 |
| 主链路抓取与 briefing/github_briefing/feishu-sync 三条时效性链路抢资源(Docling CPU 峰值/共享 API key 配额) | runner 实现避让窗口具名常量 AVOID_WINDOWS(三链路 launchd 触发时刻 ±15min 暂停取队列);plist 注释附三链路触发时间对照表;LLM 全走远程 API 无本地推理,串行单工自带限流 |
| DeepSeek/GPT-5.4 API 波动导致测试不稳定 | 全部单测用 fixture LLM 响应;真 API 只在校准跑(SC-3)与冒烟;裁决方不可用挂起逻辑单独测(FR-008) |
| 源验证发现大量死源,76 进池数不足 | SC-8 阈值定 ≥70;淘汰原因写 sources-ledger;缺口由后续挖掘批次补(非本 plan 范围) |
| 串行单工 daemon 写文件与 EOD 系统脚本冲突 | 管线只写自身 owned 目录 + vault staging;不碰 git;launchd 产物不自动加载 |
| 量规/评分器把校准语料全打回(出厂死锁) | 校准报告区分"管线缺陷"与"语料不当";语料替换需记录理由,不许为达标降量规 |

---

## 8. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python -m pytest tests/loops tests/quality -q          # 单元+契约测试(fixture LLM)
ruff check src tests                                     # lint
python -m pytest tests/quality/test_golden_replay.py -q # SC-1 样张回归
python -m pytest tests/quality/test_negative_samples.py -q  # SC-2 负样本拒收
PYTHONPATH=src python -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json  # SC-3 出厂(真 API)
bash scripts/akb2-watchdog.sh --self-test                # SC-5 watchdog 自检
```

---

## 9. Assumptions

- 模型阵容已锁:deepseek-v4-pro(reasoning_effort=max,即官方 Think Max 档,称 DeepSeek-V4-Pro-Max)提炼 / gpt-5.4 全量复核 / gemini-flash 兜底;经 litellm 风格 OpenAI 兼容端点路由,key 由用户环境提供。注意:litellm 已知 bug(#27439)会丢弃 reasoning_effort 统一压成 thinking enabled,llm_router 必须验证 max 档真实透传(见 step-3)
- 黄金样张 6 文件已在 vault 与 14 步文档中固化,作为 fixture 引用(只读)
- 抽取工具 defuddle / Scrapling 0.4.8 / Docling 已实机验证在装;Firecrawl 已裁撤
- 部署目标 Mac mini 24h 常驻、串行单工;launchd 加载由用户执行
- 前 50 条自动入库留快照(FR-015);抽检阈值 80%/周 3 篇为初值,参数级可调
- 76 源清单以 `docs/loops-design-decisions.md` §11 为唯一书面权威（该文档 §11 含完整源 ID、类型、分类、优先级），`config/main_sources.yaml` 必须从该文档派生；验证报告以实测为准

---

## 实测修订（2026-06-17 · Plan 1 进度与方向校准）

> 权威细节见 `docs/loops-design-decisions.md` §12 与 `specs/content-quality-loop/spec.md` 顶部修订块。本节只记本 Plan 1 受影响处。

**进度实测（真 API · tapsvc 网关）**
- step-1（循环7 骨架）/ step-2（抓取 raw）/ step-3（准入提炼）/ step-5（升格）/ step-6（daemon 骨架）：地基实测可用，~80% 贯通。intake archived 0→4。
- **step-4（质量评估与修复）= 唯一撞墙**：grader（B 层）可用，**repair 子循环空转**——黄金样张 addy 卡 grade 7→修后 10→12，3 轮不收敛。
- **repair 空转 4 根因**：① grader location 词表 ≠ repair 小节定位 ② 只认 H2 不认 H3 ③「整页重写+逐字节 diff」自相矛盾→整轮丢弃 ④ 超时静默吞（`repair.py:246`）。

**方向校准：C+ → B（不推倒重来）**
- **C+（最小可出货，授权改 `repair.py`）**：重写 step-4 的 repair 节点为「缺陷清单 + 黄金样本 → agent 定点爬坡 → grader 作 `/goal` 停止条件」；统一 grader↔repair location 契约；认 H3；超时不静默。`grader.py`/量规零改动（forbidden 不变）。
- **B（根治，C+ 收敛后）**：范式分层 + 重估架构裁决（驱动/收敛/修复还权给模型）+ 修三缺口 + 补 2-3 张真实卡。

**SC 影响**
- **SC-3**（≥90% 正样本 ≤3 轮走完全链）：当前实测受 repair 阻塞，**C+ 让 addy 卡收敛是 SC-3 解锁前提**。
- **SC-1/SC-9**（样张回归 / 真实源专项）：同受 repair 阻塞。
- **收敛口径**：「≤3 轮收敛=零缺陷」与「目标驱动=足够接近阈值」矛盾（三缺口③），碰宪法 P3，需修宪（L4 待批）；纪律=先让 C+ 实测能否到零，不能才据数据提案（原则 8）。
