VERDICT: REQUIRES_CHANGES

# 覆盖维度清单

- 目标链：已核对 `PROJECT_GOAL` / `CHAIN_GOAL` / `PHASE_GOAL` 是否锚定 AKB2 宪法与 spec，而非 master plan 自证。
- GOAL_SOURCE 偏离：已核对 GD 标准源、master 偏离说明与 AKB2 真相源是否能支撑本计划目标链。
- SC 可验证性：逐条核对 master SC-1..SC-9 是否绑定命令 / 路径 / 输出断言 / 人工验收，重点看是否可被机器复验。
- anti-fill：按 `/Users/praise/AI-Agent/Claude/projects/Project GD/fixtures/anti-fill/banned-generic-tokens.json` 扫描 master / step / task packet 的 SC 与实现步骤。
- 宪法对齐：逐条核对原则 3/6/7/8，重点核 P0-1 加权分、P0-4 deepseek 准入、"够格全过 + 该拒全拒" fail-closed 定义。
- NON_GOALS 与 spec 覆盖：核对 update / 升星 / 抓取层修复 / 不放宽地板，以及 content-lane SC-001/003/004 与 multi-source SC-1..5 覆盖。

# Findings

## [P1] master 缺机器可读 step inventory，GD master-plan consistency preflight 被跳过

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:72-90`
- `/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-master-plan-consistency.py:342-352`

问题：
master plan 只写了 `DISPATCH_MAP_PATH` / `VALIDATE_CMD` 和表格式 wave，但没有 ` ```json gd-step-plan-inventory ` / ` ```json gd-wave-matrix ` 机器块。实际运行：

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-master-plan-consistency.py plans/gd/2026-06-28-loop1-content-quality/master-plan.md --dispatch-map plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json
```

输出：

```text
PREFLIGHT_RESULT: SKIPPED_LEGACY_PLAN (no gd-step-plan-inventory block found)
```

这使 `SC_VERIFY_MISSING`、`DELIVERABLE_NOT_OWNED`、`MASTER_PLAN_DISPATCH_DRIFT` 等合约检查完全没有消费到本 master。GD 侧已有设计记录指出缺 inventory 不应被当成成功跳过（`Project GD/plans/gd/2026-06-16-fix-review-chain-bugs/proposals/l3-internal/proposal.md:125`）。

可直接落地的修法：
在 master `## 5a` 后补两个 fenced JSON block，至少包含 4 个 step 的 `step_id/status/task_packet_mode/task_packets/blocked_by/owned_paths/forbidden_paths/required_context/deliverables/sc_refs/verify`，并让 `gd-wave-matrix` 与 dispatch map 的 track/step id 对齐。最小形状可按 `/Users/praise/AI-Agent/Claude/projects/Project GD/fixtures/preflight/positive-clean-plan.md:7-91`。

补完后必须让该命令返回 `PREFLIGHT_PASSED`：

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-master-plan-consistency.py plans/gd/2026-06-28-loop1-content-quality/master-plan.md --dispatch-map plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json
```

## [P1] content-lane 核心 SC-001/003/004 未在 master SC 中被硬绑定，`positive_pass_rate > 0` 与 `negative_reject_rate == 1.0` 不足以证明目标达成

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:38-45`
- `specs/content-lane-quality/spec.md:77-82`

问题：
content-lane spec 的关键验收不是泛化的通过率：
- SC-001 要证明"至少一篇纯概念型内容，0 可执行命令，机制阐述清晰"走完循环1并入库。
- SC-003 要证明三类负样本分别 100% 被拒，且能定位拦截项。
- SC-004 要证明"核心价值不足但次要维度高"的对抗假阳被拒。

master 现在用 SC-6/SC-8 的 `positive_pass_rate > 0`、`negative_reject_rate == 1.0`、"负样本 100%" 聚合指标承接这些目标。该写法无法排除以下失败：通过的是操作型样本而非纯概念样本；负样本聚合全拒但缺某类拦截定位；地板 fail 的对抗样本被加权分补偿漏放。代码里已有更贴近 spec 的测试锚点，例如 `tests/quality/test_concept_judgment_e2e.py` 含 positive / negatives / adversarial 用例，master 未把它纳入 SC。

可直接落地的修法：
把 master SC 增补为可区分样本身份与拦截项的断言。建议直接追加 SC-6 的子断言，或新增 SC-6a/6b/6c：

```text
SC-6a：纯概念 0-command 正样本入库。verify：
PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "concept_positive"
expect：passed；并在 reports/calibration/factory.json 中指定 fixture_id 的 content_lane=="concept"、passed==true、weighted_score>=threshold。

SC-6b：三类低质负样本分别拒收且有拦截项。verify：
PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "negatives"
expect：机制糊清单 / 无据读后感 / 洗稿分别 rejected，失败原因为 core_value_f4 / traceability / novelty 或对应门。

SC-6c：地板不可补偿对抗样本被拒。verify：
PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 -m pytest -p no:cacheprovider -q tests/quality/test_concept_judgment_e2e.py -k "adversarial"
expect：floor_failed 含 core_value_f4，weighted_score_not_computed==true 或等价证据。
```

## [P1] SC-8 的"可达上限 / 够格全过 + 该拒全拒"没有机器可判的目标集合，执行后无法独立复验

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:45`
- `plans/gd/2026-06-28-loop1-content-quality/step-3-p2-classified-remediation.md:31-34`

问题：
SC-8 写"factory.json 达可达上限 + 负样本 100% + `/gd review` execution-plus-code 路径 Codex APPROVED"。这里有两处不可复验：
1. "可达上限"没有绑定 P1 审计后的机器可读目标集合。没有一个 manifest 说明哪些正样本被用户确认"该过"，哪些被确认"该拒"，所以 reviewer 只能读叙述，不能用命令判断 `factory.json` 是否真的达到 "够格全过 + 该拒全拒"。
2. `/gd review execution-plus-code` 是路线描述，不是可执行命令。实际脚本入口支持 `gd-review-suite-controller.py --kind combined ...`，但 master 未给 outcome artifact / code target / out-dir / expected output。

可直接落地的修法：
在 P1 出口定义一个机器可读 manifest，例如 `reports/loop1-audit/decision-manifest.json`：

```json
{
  "entries": [
    {"fixture_id": "factory-04", "verdict": "救", "expected_after_p2": "ingest"},
    {"fixture_id": "factory-11", "verdict": "拒", "expected_after_p2": "reject"}
  ]
}
```

然后把 SC-8 改成：

```text
verify:
  cmd: "python3 scripts/verify_loop1_reachable_ceiling.py --factory reports/calibration/factory.json --decisions reports/loop1-audit/decision-manifest.json"
  expect: "所有 expected_after_p2=ingest 的 fixture passed==true；所有 expected_after_p2=reject 的 fixture rejected==true；negative_reject_rate==1.0"
```

同时把 Codex 异族审写成真实命令形状，例如：

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-review-suite-controller.py \
  --target-set-id loop1-content-quality-p2 \
  --kind combined \
  --cwd /Users/praise/AI-Agent/Claude/projects/akb2-integ-wt \
  --target execution_artifact=plans/gd/2026-06-28-loop1-content-quality/reports/<execution-result>.json \
  --target code_diff=plans/gd/2026-06-28-loop1-content-quality/reports/<diff>.patch \
  --out-dir plans/gd/2026-06-28-loop1-content-quality/reviews/codex-p2 \
  --live-transport --compat-v1
```

并断言 controller/aggregate 输出的 `aggregate_bucket == "codex_approved"` 或 `GD_REVIEW_DECISION: APPROVED`。

## [P1] SC-9 聚合 v2 spec SC-1~SC-5，但 master 未拆出真实命令与输出断言

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:46`
- `specs/multi-source-distill/spec.md:75-81`
- `plans/gd/2026-06-28-loop1-content-quality/step-4-v2-multisource-distill.md:31-37`

问题：
SC-9 在 master 只写"v2 eval harness 跑通且 spec SC-1~5 全过"。这是把验收外包给 spec 名称，没有在 master SC 里绑定真实命令、路径和输出断言。child draft/step-4 里其实已经有可执行命令，但 master 没吸收，导致 master 自身的 SC-9 不满足 GD anti-fill 规则 A/C。

可直接落地的修法：
把 SC-9 拆成 5 个子断言，直接采用 step-4 的命令并补输出断言：

```text
SC-9a：薄源 ×K 合成出过门新页。
verify：pytest -k "multisource and synthesis" -q
expect：单源 floor fail；N 源合成后 F4 pass 且 weighted_score >= threshold。

SC-9b：不相干混料被 F4 拒。
verify：pytest -k "multisource and incoherent" -q
expect：evaluate_integrity_floor failed_floors 含 core_value_f4，未入库。

SC-9c：raw 门不误杀薄但有效源，零贡献拒。
verify：pytest -k "raw_gate" -q
expect：薄但 >=1 合格贡献进池；新闻/营销/零贡献 rejected。

SC-9d：厚源走 1:1 快路径。
verify：pytest -k "fast_path_1to1" -q
expect：route == "fast_1to1"，不写 pending 槽。

SC-9e：v1 不改写已入库 wiki 页。
verify：pytest -k "multisource and no_wiki_mutation" -q
expect：wiki/**/*.md 既有页 mtime/content snapshot 不变。
```

同时在 master 的 inventory 里让 step 4 的 `sc_refs` 覆盖 `SC-9a..SC-9e`，而不是只写聚合 `SC-9`。

## [P2] master Step 1 owned_paths 有不存在文件且漏列真实判定文件，执行派发会误导越界判断

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:61-66`
- `plans/gd/2026-06-28-loop1-content-quality/step-1-p0-pipeline-honesty.md:43-46`
- 本地文件列表：`src/akb2/quality/` 下有 `distill.py`，没有 `compose.py`

问题：
master Step 1 的 owned_paths 写 `src/akb2/quality/{weighted_score,grader,compose,checks_structural,admission,repair,runner}.py`。但 P0-2 的真实修点是 `distill.py`，`compose.py` 不存在；P0-3 的真实修点 `src/akb2/quality/source_ops_scanners/raw_verify.py` 也未在 master Step 1 owned_paths 中出现。step-1 自己已经修正了这一点，但 master 没同步。

这会造成两个实际风险：实现者按 master 找不到 `compose.py`；审查/派发按 master owned_paths 判断时，`distill.py` 和 `raw_verify.py` 修改会显得越界。

可直接落地的修法：
把 master Step 1 owned_paths 改成：

```text
src/akb2/quality/{weighted_score,grader,distill,checks_structural,admission,repair,runner}.py、
src/akb2/quality/source_ops_scanners/raw_verify.py、
skillpack/quality/rubric/、
tests/**、
reports/calibration/
```

并在 `gd-step-plan-inventory` 中逐 task 细分 owned_paths，避免 `p0-4` 与 `p0-5` 同改 `admission.py` 被错误并行。

## [P3] NON_GOALS 已覆盖 lead 点名四项，但建议补上 multi-source spec 的两个边界以免 v2 执行期漂移

位置：
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:50-56`
- `specs/multi-source-distill/spec.md:62-67`

问题：
master NON_GOALS 已明确排除 update、升星、内容抓取层修复、不放宽地板，满足本次点名项。但 multi-source spec 还把 "embedding 做最终归组聚类" 排除在外，并要求 v1 不改写已入库 wiki 页。master 只在 SC-9 文本中提到 v1 不改写已入库页，没有在 NON_GOALS 中显式排除这两个边界。

可直接落地的修法：
在 NON_GOALS 增加两条：

```text
- 不用 embedding 相似度做最终归组/聚类；embedding 仅可作为新概念去重召回，最终同一概念判定仍由 deepseek 按离散概念身份裁决。
- v2 v1 仅 create 新页，不改写任何已入库 wiki 页；update/merge 已有页属于后续版本。
```

# 已核对通过的关键点

- 目标链方向成立：master `PROJECT_GOAL` 锚到宪法原则 3/6/7/8，`CHAIN_GOAL` 锚到 content-lane spec，`PHASE_GOAL` 绑定满仓指标、P0/P1/P2/v2 阶段与 Codex 异族审。
- GOAL_SOURCE 偏离有说明：master 明确不使用 GD v7 反填表目标，改锚 AKB2 宪法 + content-lane spec + multi-source spec；在本计划语境下合理。
- 宪法原则 8 对齐方向正确：P0-1 修的是 score 必填、0~1 尺度、阈值 6.0 与缺 score fail-closed；master 风险栏明确不动完整性地板，符合"地板不可被加权分补偿"。
- 宪法原则 7 对齐方向可接受：P0-4 把准入切 deepseek，master 论证为"准入判源不判提炼产物，grader 仍 gpt-5.4 独立硬判"，没有把 maker 产物交给同族 checker 自评。
- 宪法原则 3 对齐方向正确："够格全过 + 该拒全拒"不是放水，前提是 P1 审计后的"该拒"集合可被机器化记录并复验；当前缺的是 SC-8 的可执行承接。
- anti-fill 禁词扫描未发现阻断性泛化动词：命中主要在目标层 `自动化`、NON_GOALS `深化`、以及"execute 阶段细化"等非 SC/非实现动词语境；阻断问题来自 verify 绑定弱，而不是禁词堆砌。

# 实际运行过的验证命令

```bash
pwd && git rev-parse --show-toplevel && git branch --show-current && git status --short
```

结果：cwd/worktree 正确，分支为 `integration/loop1-content-quality`；存在既有 dirty/untracked 计划与报告文件。

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-dispatch.py plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json
```

结果：`校验通过：dispatch_id=loop1-content-quality-plan, tracks=2, waves=1`。

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-stage-dispatch-ledger.py plans/gd/2026-06-28-loop1-content-quality/ledger/stage-dispatch-ledger.json
```

结果：`LEDGER_VALID`，但提示 `jsonschema unavailable`，走 manual fail-closed validator。

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-controller-report.py plans/gd/2026-06-28-loop1-content-quality/reports/controller-report.json
```

结果：`CONTROLLER_REPORT_VALID`。

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-child-proposal.py plans/gd/2026-06-28-loop1-content-quality/child-drafts/p0-draft.md
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-child-proposal.py plans/gd/2026-06-28-loop1-content-quality/child-drafts/v2-draft.md
```

结果：两份 child proposal 均校验通过。

```bash
python3 /Users/praise/AI-Agent/Claude/projects/Project\ GD/scripts/gd-validate-master-plan-consistency.py plans/gd/2026-06-28-loop1-content-quality/master-plan.md --dispatch-map plans/gd/2026-06-28-loop1-content-quality/dispatch-map.json
```

结果：`PREFLIGHT_RESULT: SKIPPED_LEGACY_PLAN (no gd-step-plan-inventory block found)`，构成 P1 finding。

```bash
rg -n "优化|完善|改进|提升|增强|重构|梳理|调整|补充|扩展|丰富|细化|深化|强化|健全|规范|标准化|自动化|智能化|模块化|抽象化|参数化|可配置化|全面优化|系统性优化|深度优化|进一步优化|持续优化|逐步完善|全面提升|整体增强" plans/gd/2026-06-28-loop1-content-quality/master-plan.md plans/gd/2026-06-28-loop1-content-quality/step-*.md plans/gd/2026-06-28-loop1-content-quality/task-packets/*.md
```

结果：仅命中目标层/非目标/状态描述等非阻断语境；未发现 SC 或实现步骤只靠泛化动词成立。
