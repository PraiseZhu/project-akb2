# F19 fallback/compose 确定性渲染缺陷修复报告

工单 F19:修 fallback/compose 链路的确定性渲染缺陷。分支 `fix/fallback-render-quality`(worktree `_akb2-fallback-render-wt`,从 origin/main 54434d6 开)。未 push、未建 PR,等 lead 验收。

## 1. 三个根因(file:line + 账本证据)

### 根因 1:must_cover 归一化 bug(code.claude.com 3 条 must_cover 误报)

- **账本证据**:content_hash `f0495455b3435fd0`(code.claude.com-docs-en-sub-agents, concept),L2578/L2785/L2864 等,defect `must_cover 字面量未在对应节渲染: 1. The [CLAUDE_CODE_SUBAGENT_MODEL](/docs/en/model-config#environment-variables) environment variable...` ×3,repair 3 轮不收敛(`修复不收敛: 3 项缺陷残留`)。
- **根因**:`_normalize_must_cover_text`(`checks_structural.py:1405`)旧版 `re.sub(r"[`'*#>_]", " ", s)` 把 ASCII 单引号 `'` 变**空格**,但**不去 U+2019 typographic quote**。raw 原文用 U+2019(`definition’s`),渲染侧 `_sanitize_must_cover_literal_for_command`(`distill.py:6343`)把反引号换 ASCII `'`(`cleaned.replace("`", "'")`)。归一化侧 ASCII `'` 变空格(`definition s`),U+2019 保留(`definition’s`)→ 编码不一致 → candidate 含字面量但 SG 误报缺失。
- **验证**(归一化实验):candidate `### 操作步骤` 含 `['CLAUDE_CODE_SUBAGENT_MODEL']`(单引号,来自 _sanitize),但 SG 报未渲染。`literal(ASCII ') vs section(U+2019 ')` 不匹配;`literal(U+2019) vs section(U+2019)` 匹配。**这不是渲染缺失,是归一化 bug**(candidate 含字面量,SG 误报)。

### 根因 2:占位文本 1.1/2.2(49fcc2f0)

- **账本证据**:content_hash `49fcc2f003a595a0`(openai-blog--3d99cf87d464),L2004 grade_llm defect `"操作步骤"中存在低劣的占位文本(如 1. 1 和 2. 2)`。
- **根因**:`scan_numbered_steps`(`source_ops_scanners/operations.py:280`)用 `_NUMBERED_LIST_RE = r"(?m)^(?:>\s*)?\"?(?P<item>\d+\.\s+[^\n\"]+)"` 扫 `N. item`,item group 含编号+内容。raw 里的空编号步骤 `1. 1`/`2. 2`(编号即内容)被扫为 source_operation,literal=`1. 1`/`2. 2`。candidate `### 操作步骤` 渲染 `1. [raw §S] ... `1. 1`` / `2. [raw §S] ... `2. 2``。

### 根因 3:fallback 产导航/短文本 claim(c234 'Share'/导航 claim)

- **账本证据**:c234 = langchain-blog--c234f0aa9f55(content_hash `9b65ac0d92eb0bad`),L2032 `err: deterministic_maker_fallback_failed: schema 校验失败: 'Share' is too short`。c234 raw §A=`[ Go back to blog`(导航)、§C_CONTEXT=`Share`(5 字符,'Share' 来源)、§D_ARGUMENT=图片 URL、§E_CLAIMS=真实内容。
- **根因**:`_build_deterministic_extract_data`(`distill.py:4158`)旧版 `primary = anchors[0]`(§A 导航),claim 循环 `text = section_text if section_text else fallback_text` + `provenance="source_grounded"`。fallback 把 raw §A 导航文本 `[ Go back to blog` / §C `Share` 当 source_grounded claim(填充凑数 + provenance 造假)。`Share`(5 字符)过不了 schema minLength=20 → abort。
- **当前代码** c234 fallback 不产 'Share'(tldr/applicability_boundary 已改硬编码长文本),但 claims 仍产 `[ Go back to blog`(导航,17 字符,通过 schema 但内容垃圾)。

## 2. 改动说明 + 宁缺勿假证明

### 改动 1(根因 1):`_normalize_must_cover_text` 归一化两侧同口径

`checks_structural.py:1405`:反引号 + ASCII 单引号 + U+2018/2019 typographic quote 都**去掉**(`re.sub(r"[`'‘’]", "", s)`,不变空格),`*#>` 仍变空格。两侧同口径,已渲染字面量被正确识别。真缺失仍 fail(严格度不降)。

### 改动 2(根因 2):`scan_numbered_steps` 过滤空步骤

`source_ops_scanners/operations.py:280`:加 `_is_vacuous_numbered_step`,剥 `N. `/`Step N: ` 前缀后是纯数字/太短(<3 字符)的空步骤跳过。宁缺勿假:不扫描(不渲染),走既有 fail 路径,不填充凑数。

### 改动 3(根因 3):fallback 不产导航/短文本 claim

`distill.py:4158`:加 `_is_vacuous_raw_text`(导航 `[ Go back to blog` / 图片 `![](url)` / 视频 `![[id]]` / 短文本<20 字符 / 纯标题)。`primary` 选第一个非 vacuous anchor。claim text vacuous 时**跳过**(不产该 claim)。claims 空 `raise ValueError` fail-closed。**不用 fallback_text 编造内容**(fallback_text 是 loop-engineering 模板,与 raw 主题不相关,产了是编造)。

### 宁缺勿假证明

- **占位文本**:scan 过滤(不产 `1.1`/`2.2`),不渲染,不走 fallback_text 编造
- **fallback claim**:raw anchor vacuous 时跳过(不产),不用 fallback_text 编造,claims 空 fail-closed
- **标准零点不动**:rubric/threshold/integrity_floor/五闸/admission/SG 判定逻辑不变。归一化修复是修比对 bug(不是弱化判定,真缺失仍 fail)。minLength 校验不放宽,修的是 fallback 别产短字段(过滤不产,fail-closed 保持)

## 3. 测试清单 + 结果

新增 `tests/quality/test_f19_fallback_render_quality.py`(15 测试):

| 类 | 测试 | 覆盖 |
|----|------|------|
| TestNormalizeUnicodeQuotes | test_ascii_and_unicode_single_quote_both_stripped | ASCII ' 与 U+2019 同口径去掉 |
| TestNormalizeUnicodeQuotes | test_rendered_single_quote_literal_matches_original_backtick | literal 含反引号,渲染含单引号,归一化匹配 |
| TestNormalizeUnicodeQuotes | test_no_extra_inner_spaces_in_brackets | ['MODEL'] 归一化后 [model] 无内侧空格 |
| TestNormalizeUnicodeQuotes | test_real_missing_still_fails | 真缺失仍 fail(严格度不降) |
| TestScanVacuousSteps | test_vacuous_numbered_step_detection | 1.1/2.2/Step 1: 1 是空步骤 |
| TestScanVacuousSteps | test_real_steps_not_vacuous | 真步骤(剥编号后有内容)不是空步骤 |
| TestScanVacuousSteps | test_scan_filters_vacuous_steps | scan 过滤 1.1/2.2,保留真步骤 |
| TestScanVacuousSteps | test_scan_keeps_real_numbered_steps | 无空步骤时真步骤全保留(零回归) |
| TestVacuousRawText | test_navigation/image/short/real_content | 导航/图片/视频/短文本检测 |
| TestDeterministicFallbackVacuousClaims | test_fallback_skips_vacuous_claims_c234 | c234 fallback 不产 vacuous claim |
| TestDeterministicFallbackVacuousClaims | test_fallback_fails_when_all_anchors_vacuous | raw 全 vacuous → raise fail-closed |
| TestDeterministicFallbackVacuousClaims | test_fallback_picks_non_vacuous_primary | primary 选第一个非 vacuous anchor |

**全量 pytest -m 'not integration'**:**2247 passed, 16 skipped**(2232 + 15 新)。ruff 改动文件全过(5 pre-existing E501 在 distill.py,主 repo origin/main 同样有,非我引入)。

## 4. 复放对照表(deterministic 链路,AUTO_PROMOTE=0)

配置:不设行为开关 + AUTO_PROMOTE=0 + 不调 LLM(`_build_body` 是确定性脚本) + 只读 raw。对每条 raw 跑 scan + fallback extract + compose `_build_body` + SG `run_candidate_self_gate`。

| 条目 | 车道 | 改前 fallback claims | 改前 short claims(<20) | 改前 mc_defects | 改后 fallback claims | 改后 short claims | 改后 mc_defects |
|------|------|------|------|------|------|------|------|
| c234 (langchain) | procedural | 5 (`[ Go back to blog` ×5) | 5 | 0 | 1 (§E 真实内容) | 0 | 0 |
| addyosmani | procedural | 5 | 0 | 0 | 5 | 0 | 0 |
| ag2-captain-fixture | concept | 5 | 0 | 0 | 5 | 0 | 0 |

- **c234**:改前 fallback 产 5 条 `[ Go back to blog` 导航 claim(填充凑数 + provenance=source_grounded 造假),改后产 1 条真实 claim(§E 内容 `In this conversation with Harrison Chase...`)→ **根因 3 端到端验证**
- addyosmani/ag2-captain 改前/改后 mc_defects=0(无 must_cover 缺陷)
- 根因 1(归一化)+ 根因 2(占位)由 15 单元测试覆盖(code.claude.com/openai-blog raw 不在,无法真机复放,见 §5)

复放脚本与原始 JSON:`reports/akb2-quality-repair/_tmp/f19-fallback-render/replay.py` / `replay-baseline.json` / `replay-fixed.json`。

## 5. 范围外清单(如实列,不顺手修)

- **code.claude.com sub-agents raw 不在本机**:corpus.yaml 引用 URL `https://code.claude.com/docs/en/sub-agents` 但 raw 未落地(raw 目录 28 文件无此条,fixtures 目录无此条)→ 无法真机复放 f0495455b3435fd0。归一化修复由单元测试 + addyosmani candidate 归一化实验覆盖(addyosmani 5 条 `**bold**` 匹配,验证归一化正确)
- **openai-blog--3d99 raw 不在本机** → 无法真机复放 49fcc2f0 占位文本。占位过滤由单元测试覆盖(构造 `1. 1`/`2. 2` raw,验证 scan 过滤)
- **distill 链 enable_thinking 400**(账本 2026-07-03 大量 `distill_extract` 节点 400 `Unknown parameter: 'enable_thinking'`)—— 另立工单,不在 F19 范围(工单明确修 fallback/compose 渲染,非 distill LLM 参数)
- **F17 jitter 分支**、**compose SG curl|bash 安全护栏** —— 不在范围
- **addyosmani.com 真缺失**(scanner 误扫域名当 source_operation)—— 非工单根因,`_build_body` 渲染它进 `### 操作步骤`(SG 通过),未修 scanner(避免扩大范围)。c234 `addyosmani.com` 是 URL 域名被扫为 source_operation,但字面量渲染了,SG 通过(非 must_cover 缺陷)
- **真机 LLM 复放限制**:本复放走 deterministic 链路(`_build_body` 不调 LLM)。真机 LLM distill_extract 受 code.claude.com/openai-blog raw 不在限制无法复放; c234 raw 在但 LLM 路径抖动 fail-closed 后走 fallback(deterministic 路径,已由本复放覆盖)

## 6. 一句总判

三个根因钉死(归一化 U+2019/ASCII 单引号不一致致 SG 误报 / scan 扫编号即内容空步骤 / fallback 产导航短文本 claim 凑数),改动 3 处(checks_structural 归一化两侧同口径 + operations scan 过滤空步骤 + distill fallback 选非 vacuous anchor + claims 空 fail-closed),15 新测试 + 全量 2247 passed,deterministic 复放 c234 fallback 从 5 条导航 claim 改为 1 条真实 claim(根因 3 端到端验证);标准零点未碰(归一化修比对 bug 不弱化判定,scan/fallback 宁缺勿假不填充凑数,minLength 不放宽);code.claude.com/openai-blog raw 不在限真机复放由单元测试覆盖;未 push 未建 PR,等 lead 验收。
