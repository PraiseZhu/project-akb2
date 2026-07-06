# F27 机制叙述编织 + anchor 归一 + TLDR 清洗报告

工单 F27:distill 把机制组件织成带证据的因果叙述(H3 稳过最后一刀)+ 修 2 个确定性 compose bug。分支 `fix/distill-mechanism-narrative`(worktree `_akb2-narrative-wt`,从 origin/main 4e31cb7 开)。未 push、未建 PR。

## 1. 现状渲染代码钉点(file:line)

- **机制叙述**:`distill.py:6744 _render_mechanism_narrative`(F8-D 用 eligible 边生成连贯段落)。旧版只用 `source_causal` 边(行 6762 `edge_kind=="source_causal"`),不用组件;addy 边多为 inferred_from_structure → eligible=0 → 机制叙述空 → grader H3 抱怨"机制叙述仅一句/组件不进叙述"。
- **机制证据表**:`distill.py:6652 _build_mechanism_evidence_section`。旧版行 6668 直接用 `c.get('evidence_anchor')` 不归一 → §D 缩写进表,与正文 §D_ARGUMENT 不一致(bug A)。
- **TLDR**:`distill.py:6914-6968` TLDR 渲染(tldr_parts = mech_sent + comp_sent + claims)。表格碎片(分号数字对 "2;0"/"13;7")混入(bug B)。
- **A3 检查**:`checks_structural.py:467 check_a3_raw_anchors` 用 `canonicalize_anchor`(不解析前缀)→ §E ≠ §E_CLAIMS → A3 fail。LLM 产缩写 §E/§D/§P 散落在 claims.text/evidence_sentence/explanation/graph_edges.evidence/how_steps 等字段。

## 2. 三刀改动说明(反造假守恒论证)

### 主刀:机制叙述编织(`distill.py:_render_mechanism_narrative`)

- eligible 边扩到 `llm_evidence`(不只 source_causal,两类都带 evidence+anchor)
- **带证据组件无边时渲染"组件(职责,证据)"句**——组件有 name+role+evidence_sentence+anchor 即织进叙述(消"组件只进表不进叙述")
- 组件已在边(from/to)不重复渲染
- **宁缺勿假**:evidence/anchor/from/to/name/role 全来自已校验结构字段;anchor 走 canonicalize+resolve ∈ raw 声明集,不在则跳过(不造假);无 eligible 无带证据组件 → 空(fail-closed);≤8 句(有界)

### bug A:anchor 缩写归一(`distill.py:_normalize_anchors_in_extract_data` + `_build_mechanism_evidence_section`)

- 新增 `_normalize_anchors_in_extract_data(extract_data, raw_content)` 在 `_build_body` 开头调用(in-place 归一)
- 归一 **所有字段所有 [raw §X]**:claims(anchor/text/evidence_gap) + argumentation(claim_anchor/explanation) + mechanism_components(evidence_anchor/evidence_sentence/role) + causal_edges(evidence_anchor/evidence) + graph_edges(anchor/evidence) + how_steps(anchor/instruction/command) + tldr + applicability_boundary
- 双层归一:anchor 字段用 canonicalize+resolve(§E→§E_CLAIMS);文本里 [raw §X] 用 `_norm_text` 正则替换
- `_build_mechanism_evidence_section` 加 raw_content 参数 + `_norm_anchor`(归一表内 anchor)
- **反造假守恒**:只归一到 raw 声明全称(不造假 anchor);不在声明集的 anchor 保持原值(§Z_FAKE 不归一);raw_content=None 退原值(兼容)

### bug B:TLDR 清洗(`distill.py:_clean_tldr_noise`)

- 新增 `_clean_tldr_noise(tldr)` 在 TLDR 消毒后调用(行 7038)
- 去分号数字对(`\d+;\d+` 表格碎片,如 "Terminal Bench 2;0"→"Terminal Bench")+ 去孤立分号 + 压空白
- 不改 TLDR ≤3 句判据(只清洗噪音)

### 标准零点不动

rubric/threshold/H3/integrity_floor/五闸/admission/grader/A4 假边四条件/F4/entailment 零改动。改动只增强产出质量(机制叙述编织 + anchor 归一 + TLDR 清洗),不绕过/放宽判定。A4/A3/F4 仍 fail-closed。

## 3. 测试清单+结果

新增 `tests/quality/test_distill_mechanism_narrative.py`(27 测试):

| 类 | 测试 | 覆盖 |
|----|------|------|
| TestMechanismNarrativeEdges(3) | source_causal 边因果句 / llm_evidence 也织 / inferred 弱边不织(不造假) | 主刀 eligible 边 |
| TestMechanismNarrativeComponents(4) | 组件无边渲染职责+证据 / 组件在边不重复 / 无证据空(fail-closed) / anchor 不在声明集跳过 | 主刀组件编织+反造假 |
| TestAnchorNormalization(3) | §D→§D_ARGUMENT / §J→§J_DISCUSSION / 无 raw_content 退原值 | bug A 表归一 |
| TestNormalizeAnchorsInExtractData(8) | claims/argumentation/components/edges anchor 归一 / 全称不变 / §A 保持 / 无 raw_content noop / §Z_FAKE 不归一 | bug A 全量归一 |
| TestNormTextInFields(5) | claim text/evidence_sentence/explanation/edge evidence/tldr 里 [raw §X] 归一 | bug A 文本归一 |
| TestTLDRClean(4) | 分号数字对清洗 / 多个噪音 / 正常 TLDR 不破坏 / 空白压 | bug B |

**全量 pytest -m 'not integration'**:**2345 passed, 16 skipped**(2318 + 27 新;flaky test_concurrent_single_writer 单独跑过,pre-existing)。ruff 改动文件全过。

## 4. 两篇复放对照(叙述文本贴样+评分)

配置:AUTO_PROMOTE=0 + 不设行为开关 + 真实 LLM(deepseek extract + gpt-5.4 grade) + 隔离 staging + raw 只读。

| 篇目 | 改前(baseline/无归一) | 改后 fixed4(全归一) |
|------|------|------|
| addy long-running | compose A3 fail:§E/§D 不存在 | extract OK(6组件/6边);**compose A3 过**(§E/§D 归一);compose A4-图谱resolve fail(loop-engineering 未 resolve) |
| new-sw-lifecycle | compose A3 fail:§E/§D/§P 不存在 | extract OK(9组件/8边);**compose A3 过**(§E/§D/§P 归一);compose A4-图谱resolve fail(Agent/Harness 未 resolve) |

**bug A 修复证据**:改前 compose A3 fail(§E/§D/§P 缩写不在 raw 声明集),改后 fixed4 compose A3 **过**(anchor 归一到全称)。

**机制叙述节文本**:compose A4 fail 没产 candidate,机制叙述节未贴真机文本。但单元测试(TestMechanismNarrativeEdges/Components)覆盖渲染行为:eligible 边 → "from rel to(证据:[raw anchor] ev)";组件无边 → "name(role,证据:[raw anchor] ev)"。

**A4-图谱resolve fail 根因(复放配置,非代码 bug)**:复放脚本没传 known_concepts(compose_candidate 参数),graph_edges target(loop-engineering/Agent 等)未 resolve 到已知概念 → A4 fail。生产 daemon 传 vault loader 的 known_concepts(runner.py:1611 `known_concepts=vault_index.get("known_concepts")`),应过 A4。

**grader defects + 最终过闸**:复放 compose A4 fail(known_concepts 缺)没到 grade 阶段,grader defects 数 + 最终过闸未取。**需生产 daemon 重审验证**(传真实 known_concepts)。

## 5. 范围外(如实列)

- **A4-图谱resolve**:复放 known_concepts 缺导致 fail,非 F27 范围(graph_edges target resolve 是另一问题);生产 daemon 传 known_concepts 应过
- **grade defects 2 次取较差**:复放没到 grade(compose A4 fail),未取;需生产 daemon 重审
- **addy extract 偶发 fail**(fixed3 how_params 未返 how_steps):deepseek LLM 方差,非代码 bug,重试可过(fixed4 过)
- **不动标准**:rubric/threshold/H3/integrity_floor/五闸/admission/grader/A4/F4/entailment 零改动
- **live daemon/plist/AUTO_PROMOTE**:未碰

## 6. 一句总判

三刀改完(机制叙述编织 eligible 边+带证据组件 / anchor 全量归一 §E→§E_CLAIMS 消 A3 fail / TLDR 表格碎片清洗),27 新测试 + 全量 2345 passed;复放 bug A 修复证据确凿(compose A3 从 fail→过),但 compose A4-图谱resolve fail(复放 known_concepts 缺,生产 daemon 传应过),没到 grade 阶段——**标准零点未碰,两篇是否稳过未最终确认(需生产 daemon 重审传 known_concepts)**;未 push 未建 PR,等 lead 验收。
