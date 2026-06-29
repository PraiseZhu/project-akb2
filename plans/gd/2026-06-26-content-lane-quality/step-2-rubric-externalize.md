# Step 2: 量规外置（标准抽成机器可读文件 + grader 解耦读取）

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: docs/constitution.md v1.2.0 + specs/content-lane-quality/spec.md
TEMPLATE_KIND: gd-step-plan

日期：2026-06-26 ｜ 状态：draft（child_planner t1 起草 + 主 agent 合并）

## 1. 目标链

```text
PROJECT_GOAL: 让循环1 从命令清单库变成能同时收录概念型与操作型知识的概念图谱知识库
CHAIN_GOAL:   把宪法 v1.2.0「完整性地板 + 车道感知加权质量分」实现为循环1 判定代码（原则 6/8）
PHASE_GOAL:   产出包 A『改标准』计划套装
TASK_GOAL:    把判定标准（车道/完整性地板/维度定义/权重/阈值）抽成机器可读文件，grader 改读该文件而非写死 REQUIRED_RUBRIC_VERSION+10维 enum；grader 输出 schema 扩展为「逐地板 pass/fail + 逐维 score + weighted_total + threshold + 可定位 defects」；本 step 不改判定逻辑本身（旧维度等价不回归，对齐 spec SC-005）
```

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`标准与代码分离正确性（改文件即改判分、不改判定代码即生效，spec SC-005）; 旧 10 维等价（同稿件新读取路径下 pass/fail 与缺陷集与 step-1 后一致）; grader fail-closed 保持（gpt-5.4 canary + gemini fallback 不被外置破坏，禁假 0 缺陷）; schema 扩展不破坏 FR-006/008（缺 location 整批拒、双不可用挂起）; 完整性地板在数据文件中标 hard=true 不进加权`
- 边界说明：判定算法本身（10 维如何 pass/fail、缺陷如何成形）保持现状。新增 weighted_total/threshold 仅「产出并随稿记录」，加权过阈是否取代现行判定留给 step-3；本 step 用「阈值设为旧行为等价」的保守配置确保零回归。

## 3. 前置条件

- blocked_by：`step-1`（lane 已穿线，grader 已能接 content_lane 形参）
- baseline：`skillpack/quality/rubric/10-dimensions.md`（v1.0.0）、`src/akb2/quality/grader.py`（_load_rubric_text / REQUIRED_RUBRIC_VERSION / RUBRIC_DIMENSIONS / _build_grader_schema / _parse_grader_output）、step-1 全绿
- Hard-stop：step-1 未合入则停（依赖 grader 已具 content_lane 形参）

## 4. 成功标准（SC）

- [ ] SC-1：标准外置——`skillpack/quality/rubric/rubric.yaml` 含 rubric_version/lanes/integrity_floor/dimensions(含 weight)/threshold，可解析；**`lanes.concept.dimensions` 维名集 exact-equal 7 个预期全名（逐字）、权重和==10、含 `threshold`+`integrity_floor`**。verify(command): `python3 -c "import yaml,pathlib;d=yaml.safe_load(pathlib.Path('skillpack/quality/rubric/rubric.yaml').read_text());lc=d['lanes']['concept'];names={x['name'] for x in lc['dimensions']};exp={'概念机制清晰度(H3)','实现机制说明(H2)','图谱可调用性','适用边界','TLDR密度','Future Questions质量','新颖增量'};print(all(k in d for k in ('rubric_version','lanes','integrity_floor','dimensions','threshold')) and names==exp and abs(sum(x['weight'] for x in lc['dimensions'])-10)<1e-6 and 'threshold' in lc and 'integrity_floor' in lc)"` → True
  > round-3 A3-7 修正：旧版用 `not ({'How密度','How可执行性'} & names)` 排除 How——但真实维名带空格（`How 密度`/`How 可执行性`，见 10-dimensions.md:21,33、grader.py:50-61），无空格名与维名集**永不相交**，该子句恒真=假绿。改 **exact-set 比较**（`names==exp`）：How 两维不在预期 7 名集即被天然排除，同时抓住「漏维/多维/错名」三类偏差。预期 7 名为 step-3 grader concept 维度集的逐字真相源（A3-6：step-3 动态派生、不硬编码）。
- [ ] SC-2：grader 不写死维度 enum/版本号（从文件读）。verify(assertion): `grep -nE '^(RUBRIC_DIMENSIONS|REQUIRED_RUBRIC_VERSION)\b[^=]*=\s*(\[|"1\.0\.0")' src/akb2/quality/grader.py | wc -l` → 0（正则 `\b[^=]*=` 容忍 `: list[str]` 类型注解——旧 `\s*=` 漏匹配 `RUBRIC_DIMENSIONS: list[str] =`、只抓版本行恒返 1、升版后→0 假绿，round-2 P2）；行为门：对「保留硬编码维度列表」反例应返 ≥1（抓得住未删的硬编码）
- [ ] SC-3：改 YAML 即改判分——改 weight/threshold 后 grader 输出 weighted_total/threshold 随之变化，改回复原（不改 .py 即生效）。verify(test): `pytest tests/test_grader_rubric_external.py -k rubric_change_changes_scoring -q` → passed
- [ ] SC-4：grader 输出 schema 扩展（逐 floor pass/fail + 逐维 score + weighted_total + threshold + 可定位 defects）；缺 location 整批拒（FR-006）；双模型不可用挂起（FR-008）。verify(test): `pytest tests/test_grader_rubric_external.py -k "schema_extended or missing_location_rejected or dual_unavailable_suspended" -q` → 3 passed
- [ ] SC-5（零回归）：同组稿件 10 维 pass/fail 与缺陷集与 step-1 后一致。verify(command): `pytest -m 'not integration' -q && pytest -m proposal_mode -q` → 均 passed，grader 用例不减
- [ ] SC-6：rubric_version 升级 + canary 守卫——版本断言匹配新值；gpt-5.4 canary 实跑真通（不静默降 gemini 假 0 缺陷）。verify(test): `pytest tests/test_grader_rubric_external.py -k "version_match or primary_canary_real" -q` → 2 passed

## 5. 非目标

- 不用 weighted_total 取代现行 10 维 pass/fail（本 step 只产出记录，切换判据是 step-3）
- 概念车道的维度集/权重/阈值的**结构与初始值**由本 step 写入 rubric.yaml（作为外置真相源，供 step-3 只读消费）；但**不固定真实校准值**——初始值标 `calibration_status: initial`，真实数值由后续满仓校准回填（spec FR-010）。不在本 step 接判据切换（属 step-3）。
- 不改 admission lane 分类器（step-1）/不改 checks_structural A 层判定

## 6. 实现步骤

```text
Step.1  WHERE: skillpack/quality/rubric/rubric.yaml（新建）+ 10-dimensions.md（保留说明）
  WHAT: 把 10 维名+判据要点+weight+threshold+integrity_floor +rubric_version 写成结构化 YAML；**lanes(concept/procedural) 各含 dimensions + 逐维 weight + threshold + integrity_floor**：procedural=现 10 维「等价旧行为」保守占位。**concept 唯一权威模型（round-2 P2 对账，step-3 只读此表）**：`lanes.concept.dimensions` = 恰 7 个加权质量维（全名=grader 输出维名，和=10）：`概念机制清晰度(H3)=3.5 / 实现机制说明(H2)=0.5 / 图谱可调用性=2.0 / 适用边界=1.5 / TLDR密度=1.0 / Future Questions质量=1.0 / 新颖增量=0.5`；How密度/How可执行性/H1 **从 concept dimensions 移除（absent，非零权重）**。`lanes.concept.integrity_floor` = 4 个二值地板维（hard=true、不进 dimensions/不加权）：`论断锚定率(raw-anchor) / 证据诚实 / 解读分离(F3溯源) / 车道核心价值(F4：H3≥3 命名组件含因果边)`。**每条地板维带 `evaluated_by` 标签（Decision-1 落地：标明该地板由哪一族裁决）**：`论断锚定率/证据诚实/车道核心价值 → evaluated_by: structural`（确定性 A 层代码、pre-grader）；`解读分离 → evaluated_by: [structural, grader]`（两层：层① structural=每条 akb_interpretation 的 traces_to 指向的 anchor 真实存在于 claims；层② grader=异族 GPT-5.4 判推导真成立、拒 poisoned-trace，**生产模型 deepseek 不自证**——step-3 Step.3/Step.6 实现）。边强度匹配 concept 下并入 A 层「图谱可调用性」检查（step-3 Step.5）、**不作 grader 维**。threshold=6.5；全部数值标 `calibration_status: initial`（待校准回填，非终值）；md 顶部注「机器可读源为 rubric.yaml」
  VERIFY: python3 -c "import yaml,pathlib;d=yaml.safe_load(pathlib.Path('skillpack/quality/rubric/rubric.yaml').read_text());print(all(k in d for k in ('rubric_version','lanes','integrity_floor','dimensions','threshold')))" → True
Step.2  WHERE: grader.py 新增 _load_rubric_spec() + RUBRIC_DIMENSIONS/REQUIRED_RUBRIC_VERSION 改从 spec 读
  WHAT: 解析 rubric.yaml→dict（dimensions+weights+threshold+floor+version）；维度名由 spec 派生；版本比较读 spec；文件/字段缺失→RuntimeError（fail-closed）
  VERIFY: grep -nE '^(RUBRIC_DIMENSIONS|REQUIRED_RUBRIC_VERSION)\b[^=]*=\s*(\[|"1\.0\.0")' src/akb2/quality/grader.py | wc -l → 0（容忍 `: list[str]` 类型注解，round-2 P2）
Step.3  WHERE: grader.py _build_grader_schema() + _build_grader_prompt()
  WHAT: schema 维度 enum 改从 spec 派生；输出对象扩展 score/weighted_total/threshold/integrity_floor 逐项；prompt 注入 floor 与 weight 说明
  VERIFY: pytest tests/test_grader_rubric_external.py -k schema_extended -q → passed
Step.4  WHERE: grader.py _parse_grader_output() + GraderResult
  WHAT: 解析扩展字段写入 GraderResult；保留缺 location 整批拒 + 维度集须等于 spec 维度集；weighted_total 仅记录不参与本 step 判定
  VERIFY: pytest tests/test_grader_rubric_external.py -k "missing_location_rejected or dual_unavailable_suspended" -q → 2 passed（round-2 P2：and→or，否则选 0 测试）
Step.5  WHERE: rubric.yaml rubric_version + grader 版本断言
  WHAT: 版本升 1.1.0；断言改匹配 spec 内 version（去掉对 "1.0.0" 硬比较）
  VERIFY: pytest tests/test_grader_rubric_external.py -k version_match -q → passed
Step.6  WHERE: tests/test_grader_rubric_external.py（新建）+ 既有 grader/calibration 套件
  WHAT: 覆盖 SC-1..6（含「改 weight→输出变、改回→复原」对账，fixture route_llm 固定维度判定只验读取路径）；全量回归 SC-5
  VERIFY: pytest -m 'not integration' -q && pytest -m proposal_mode -q → 均 passed，grader 用例不减
```

Anti-fill：全部步骤为新建 YAML/替换硬编码读取/扩展 schema 字段/解析对账。

## 7. Execution Checkpoint：rubric-externalization

- agent_role: implementer ｜ blocked_by: [step-1] ｜ can_parallel_with: [] ｜ execution_packet: `content-lane-quality-execution`（本节是单一 execution packet 内部 checkpoint，不单独作为 §6 task packet 调度）
- owned_paths: `skillpack/quality/rubric/rubric.yaml`、`skillpack/quality/rubric/10-dimensions.md`、`src/akb2/quality/grader.py`、`tests/test_grader_rubric_external.py`
- forbidden_paths: `src/akb2/quality/admission.py`、`docs/constitution.md`、`/Users/praise/.claude/**`
- required_context: `specs/content-lane-quality/spec.md`、`docs/constitution.md`、`src/akb2/quality/checks_structural.py`（owned 的 10-dimensions.md/grader.py 为本 packet 自有，执行时直接读改，按标准 §6 不重列入 required_context 以免与 owned_paths 重叠）
- deliverables: `skillpack/quality/rubric/rubric.yaml`（新建）、`src/akb2/quality/grader.py`（去硬编码+读 spec+schema 扩展）、`tests/test_grader_rubric_external.py`（新建）、step-2-result.md
- verify: 本 step §4 SC-1~6 的 verify 命令（rubric.yaml 可解析 + concept 权重和==10 / 无硬编码维度 enum / 改 YAML 即改判分 / schema 扩展 / 零回归 / version+canary 实跑）
- 说明：YAML 与 grader 读取强耦合（schema/版本/维度三处同时切数据源），切分会产生「文件已建但代码仍读旧硬编码」断裂态，单 packet 串行。
- handoff: result → `plans/gd/2026-06-26-content-lane-quality/results/step-2-result.md`

## 8. 边界

修改：rubric.yaml（新建）/ 10-dimensions.md（仅加注释，维度文本不动）/ grader.py（去硬编码+读 spec+schema 扩展+解析扩展）/ test_grader_rubric_external.py（新建）。
不修改：admission.py、checks_structural A 层判定、任何维度 pass/fail 算法本身、docs/constitution.md、/Users/praise/.claude/**。

## 9. 风险与防护

| 风险 | 防护 |
|------|------|
| 外置后静默退化 gemini 假 0 缺陷（历史 grader tapsvc 401 降级事故） | SC-6 保留 gpt-5.4 canary 实跑守卫；新字段不碰 fallback fail-closed 路径 |
| YAML 解析失败被静默吞跑空标准 | 文件/字段缺失即 RuntimeError（fail-closed），不给空标准 |
| weighted_total 误被当判据致旧行为漂移 | 本 step 明确不接入判定；SC-5 全量回归 + grader 用例不减对账 |
| 维度 enum 从 spec 派生后名称与旧不一致 | spec 维度名与现 RUBRIC_DIMENSIONS 逐字一致（从 md 原样搬），_canonical_dimension_name 行为不变 |
| rubric_version 升级未同步致 grader 拒跑 | Step.5 同提 YAML 版本与断言；SC-6 钉死 version_match |

## 10. Assumptions

- procedural 权重/阈值为「等价旧行为」保守占位；**concept 车道维度集/权重/阈值的结构与初始值由本 step 写入 rubric.yaml（外置真相源），step-3 只读消费、不回写**；真实校准数值由后续满仓校准回填（spec FR-010，不在本 step 固定）；YAML 优于 JSON（人读+注释，依赖已在）；integrity_floor 以 `hard: true` 标记表达「不可被加权补偿」（原则 8(a)）。
- **concept 维名逐字契约（A3-6 前置）**：`lanes.concept.dimensions[].name` 必须逐字写为 `概念机制清晰度(H3)`/`实现机制说明(H2)`/`图谱可调用性`/`适用边界`/`TLDR密度`/`Future Questions质量`/`新颖增量`（含 `(H3)`/`(H2)` 后缀），作为 step-3 `RUBRIC_DIMENSIONS_BY_LANE` **动态派生**（`_load_rubric_spec()`）的唯一真相源——step-3 不得在 .py 手写这组维名（否则破坏 SC-5 外置 + A3-6 硬编码回流）。
