# Step 2: 抓取与 raw(源配置/发现层/抽取层/raw 锚点化/源验证报告) v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-step-plan

日期:2026-06-12 | 状态:draft | 负责人:Claude 执行;Codex cross-review

---

## 1. 目标链(继承 + 当前 task goal)

```text
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路,提升复杂任务的计划、审查、执行、验收效率,并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL:   用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准,保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL:   ref master plan(plans/gd/2026-06-12-content-quality-loop/master-plan.md §1)
TASK_GOAL:    交付循环 1 前两段:76 源配置 + 发现层(RSS/Atom 优先、指纹去重、产量隔离) + 抽取层(defuddle→Scrapling→Docling 决策树、抽取成功硬判定) + raw 锚点化归档(9 字段 frontmatter、§A-§Z 锚点 assert 全命中、fail-closed) + 逐源验证报告 sources-ledger.jsonl(≥70 源有记录,SC-8)。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN:`ai_infra`
- REVIEW_FOCUS:`raw 锚点 fail-closed(P3,锚点不齐不出 raw); 抓取跳过/失败显式计数(P4); 指纹去重与产量隔离区(FR-017); 抽取成功硬判定不降级; 单测禁真网(fixture-only)`
- Domain-specific notes:黄金样张 14 步清单的步 1-2"标准"列是逐项验收基准(步 1:无认证通道/元数据齐/失败显式报错;步 2:文件名规范/9 字段/锚点 assert/目录表/原文不翻译);源验证是唯一允许真网的执行期动作,结果只落 reports/,不入测试。

---

## 3. 前置条件

- blocked_by:`step-1`(signatures 模块已完成,fetch_fail 签名落账依赖之)
- 必须的 baseline / artifact:`schema/failure-signature.schema.json`、`src/akb2/loops/`(step-1 产物);`docs/main-pipeline-golden-sample.md`(步 1-2 标准列);`docs/loops-design-decisions.md` §11(76 源清单来源)
- Hard-stop 条件:step-1 的 failure-signature schema 不存在或 `python3 -m json.tool` 不过 → 停止,不得自造签名格式

---

## 4. 成功标准(SC,本 step 内的)

- [ ] SC-2.1:`config/main_sources.yaml` 含 76 源条目,每条具备 domain + (feed_url 或 listing_url) + rate_limit + status: trial
  - verify (method: command): `python3 -c "import yaml;d=yaml.safe_load(open('config/main_sources.yaml'));ss=d['sources'];assert len(ss)==76 and all(('feed_url' in s or 'listing_url' in s) and s['status']=='trial' and 'domain' in s and 'rate_limit' in s for s in ss);print('PASS')"`
  - expect: `PASS`
- [ ] SC-2.2:发现层指纹去重幂等 + 单源单日产量超 `QUARANTINE_MULTIPLIER` 倍中位数触发隔离区,正反路径单测全过
  - verify (method: test): `python3 -m pytest tests/quality/test_intake_discovery.py -q`
  - expect: exit 0
- [ ] SC-2.3:抽取决策树(defuddle→Scrapling(含浏览器模式)→Docling→fetch_fail 签名)+ 抽取成功硬判定(字数核对 + title/author/published/url 4 件齐)单测全过
  - verify (method: test): `python3 -m pytest tests/quality/test_intake_extract.py -q`
  - expect: exit 0
- [ ] SC-2.4:raw 归档 fail-closed——任一锚点 assert 未命中或 frontmatter 9 字段不齐 → 不产出 raw 文件且落 failure 签名;命中路径产出含锚点目录表的 `<slug>--raw--<date>.md`
  - verify (method: test): `python3 -m pytest tests/quality/test_intake_raw.py -q`
  - expect: exit 0
- [ ] SC-2.5(= master SC-8):逐源验证产出 sources-ledger 初版,76 源全覆盖且每行含必填字段（与 master SC-8 对齐：config 源数==76、ledger 行数==76、req_fields 全集、reachable>=70）
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python3 -c "import json,yaml; sources=yaml.safe_load(open('config/main_sources.yaml'))['sources']; assert len(sources)==76,f'config sources={len(sources)}!=76'; rs=[json.loads(l) for l in open('reports/source-validation/sources-ledger.jsonl')]; assert len(rs)==76,f'ledger rows={len(rs)}!=76'; req_fields={'feed','url','reachable','latest_post','trial_fetch'}; bad=[str(i) for i,r in enumerate(rs) if not req_fields.issubset(r.keys())]; assert not bad,f'MISSING FIELDS at rows: {bad}'; ok=sum(1 for r in rs if r.get('reachable')); assert ok>=70,f'reachable={ok}<70'; print('PASS')"`
  - expect: `PASS`
- [ ] SC-2.6:intake 全部测试 + lint 绿
  - verify (method: command): `python3 -m pytest tests/quality/ -q -k intake && ruff check src/akb2/quality/intake tests/quality && echo PASS`
  - expect: `PASS`

---

## 5. 非目标

- 准入/提炼/评估/升格(step 3-5 范围);不调用任何 LLM
- 源池扩充到 200(master 非目标);死源缺口补充属后续运营批次
- 翻译(原文保持原语言,黄金样张步 2 ⑤)
- launchd 调度与常驻 runner(step 6)

---

## 6. 实现步骤

```text
Step.1
  WHERE: config/main_sources.yaml(新建)
  WHAT:  定义 schema(sources[].slug/domain/feed_url|listing_url/rate_limit/status)并填入 76 源条目(status 全 trial),清单取自 docs/loops-design-decisions.md §11
  WHY:   发现层与源验证共用的唯一源清单,配置结构先行才能让 discovery 与 validator 解耦于具体源
  VERIFY: SC-2.1 命令输出 PASS
Step.2
  WHERE: src/akb2/quality/intake/discovery.py(新建,含 __init__.py)
  WHAT:  实现 RSS/Atom 轮询优先、无 feed 走 Scrapling 列表页;正文 SHA-256 内容指纹幂等去重;单源单日产量 > QUARANTINE_MULTIPLIER × 中位数 → 该批进隔离区(具名常量,P1/P4)
  WHY:   循环 1 的入口段;去重与隔离是 FR-017 硬要求,缺失会放任重复与灌水源污染下游
  VERIFY: python3 -m pytest tests/quality/test_intake_discovery.py -q → exit 0
Step.3
  WHERE: src/akb2/quality/intake/extract.py(新建)
  WHAT:  实现决策树 defuddle → 失败/反爬/JS 转 Scrapling(含浏览器模式) → PDF 走 Docling → 全失败调 step-1 signatures 模块落 fetch_fail 签名;成功硬判定=正文字数与页面声明核对 + 元数据 4 件齐(title/author/published/url)
  WHY:   黄金样张步 1 标准③要求失败显式报错不静默跳过;硬判定防"抓到半页也算成功"的降级
  VERIFY: python3 -m pytest tests/quality/test_intake_extract.py -q → exit 0
Step.4
  WHERE: src/akb2/quality/intake/raw_archiver.py(新建)
  WHAT:  生成 <slug>--raw--<date>.md:frontmatter 9 字段(artifact_type/source_slug/source_url/source_title/source_authors/source_publisher/source_published_at/fetched_at/fetched_via) + §A-§Z 锚点注入且每个替换 assert 命中 + 锚点目录表 + 原文不翻译;任一 assert 失败 → 不写文件,落签名(P3 fail-closed)
  WHY:   raw 锚点是 step-3 提炼"[raw §X] 无锚点论断不许出现"的地基,锚点静默漏注会让下游引用全部失真
  VERIFY: python3 -m pytest tests/quality/test_intake_raw.py -q → exit 0
Step.5
  WHERE: src/akb2/quality/intake/source_validator.py(新建) + reports/source-validation/
  WHAT:  实现逐源验证 CLI(python3 -m akb2.quality.intake.source_validator):feed 可达性 + 最近发文 + 试抓一篇,逐源追加 jsonl 记录(url/feed/reachable/latest_post/trial_fetch);feed 字段写入 feed_url（无 feed 源则为 listing_url）的实测值;死源记录淘汰原因不删条目
  WHY:   SC-8 要求 76 源实测台账;无台账则源池质量不可审计,死源会拖垮日常轮询
  VERIFY: 执行期真网跑后 SC-2.5 命令输出 PASS(开发期先以 --dry-run 验证 CLI 可运行)
Step.6
  WHERE: tests/quality/test_intake_discovery.py / test_intake_extract.py / test_intake_raw.py + tests/quality/fixtures/intake/(本地 HTML/RSS fixture)
  WHAT:  全 fixture 单测不打真网;覆盖:去重幂等、隔离触发与不触发、决策树各分支与 fetch_fail 签名、硬判定拒收、锚点 assert 失败不出 raw、9 字段缺一拒收
  WHY:   变更纪律要求循环/门控改动带行为断言测试;真网测试不可重复,fixture 才能回归
  VERIFY: python3 -m pytest tests/quality/ -q -k intake && ruff check src/akb2/quality/intake tests/quality → 全绿
```

---

## 7. Task Packet 拆分

| task_id | agent_role | owned_paths | blocked_by | can_parallel_with |
|---------|-----------|------------|-----------|-------------------|
| step2-intake-raw | child_executor | `config/main_sources.yaml`, `src/akb2/quality/intake/`, `tests/quality/test_intake*.py`, `tests/quality/fixtures/intake/`, `reports/source-validation/` | step-1 | step-3(owned_paths 无交叉) |

Task packet(关键字段):
- task_id: `step2-intake-raw`
- task_goal: 同 §1 TASK_GOAL
- owned_paths: 同上表(相对 TARGET=Project AKB2)
- required_context: `plans/gd/2026-06-12-content-quality-loop/master-plan.md` §3/§5/§6、`docs/main-pipeline-golden-sample.md` 步 1-2 标准列、`schema/failure-signature.schema.json`(step-1 产物)、`docs/loops-design-decisions.md` §11
- sc_refs: SC-2.1 ~ SC-2.6(SC-2.5 = master SC-8)
- 完成定义: §4 全部 SC verify 通过 + §10 测试计划全绿

---

## 8. 边界(修改 / 不修改)

修改:
- `config/main_sources.yaml`、`src/akb2/quality/intake/`(均新建)
- `tests/quality/test_intake*.py`、`tests/quality/fixtures/intake/`
- `reports/source-validation/`(执行期产物)

不修改:
- 旧 `/rev` 任何 artifact;`/Users/praise/.claude/**`
- 其他 step 的 owned_paths(含 `src/akb2/loops/`、`schema/*` —— 只读引用 step-1 产物)
- vault 任何文件;既有 briefing/video/feishu_sync 链路;`src/akb2/gate/`、`src/akb2/lint/`

---

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| 真网源验证遇大量死源/限流,76 进池不足 | SC-2.5 阈值 ≥70;淘汰原因写 ledger 不删条目;缺口由后续挖掘批次补(master 非目标);逐源遵守 rate_limit |
| 原文改版导致锚点替换静默漏注 | 每个锚点替换强制 assert 命中,失败即不出 raw + 落签名(P3);单测覆盖 assert 失败路径 |
| 单测误打真网导致不稳定/不可回归 | 全部用本地 fixture HTML/RSS;source_validator 真网逻辑不进默认 pytest(标 integration 或仅 CLI 路径),SC-2.6 只跑 fixture 测试 |

---

## 10. 测试计划

```bash
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2"
python3 -m pytest tests/quality/ -q -k intake                  # SC-2.2/2.3/2.4 fixture 单测
ruff check src/akb2/quality/intake tests/quality                # lint
python3 -c "import yaml;d=yaml.safe_load(open('config/main_sources.yaml'));assert len(d['sources'])==76;print('PASS')"  # SC-2.1
python3 -m akb2.quality.intake.source_validator --dry-run       # CLI 可运行性(开发期)
# 执行期真网: python3 -m akb2.quality.intake.source_validator --out reports/source-validation/sources-ledger.jsonl → SC-2.5
```

---

## 11. Assumptions

- defuddle / Scrapling 0.4.8 / Docling 已实机在装(master §9);Firecrawl 已裁撤,不引入
- step-1 的 signatures 模块与 failure-signature schema 已完成并可 import(blocked_by 已声明)
- 76 源清单以 `docs/loops-design-decisions.md` §11 + 会话记录为准;配置结构先行,源条目逐条验证在执行期完成
- 源验证真网动作仅写 reports/,不写 vault、不触碰 git(master §7 防护)
