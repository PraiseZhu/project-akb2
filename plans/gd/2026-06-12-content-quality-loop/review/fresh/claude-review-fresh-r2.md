# Claude Self-Review (Fresh Round 2)

```
REVIEWER: claude_subagent_fresh_r2
REVIEW_TARGET: plans/gd/2026-06-12-content-quality-loop/ (round 2)
REVIEW_KIND: plan
REVIEW_RUN_STATUS: completed
GD_REVIEW_DECISION: REQUIRES_CHANGES
```

---

## 修复验证（Round 1 发现的 5 项）

### F-NEW-1（P1）：step-3 owned_paths 明确排除 rubric/ 子目录
**状态：✅ VERIFIED**

- step-3 §7 表格行（L106）已标注"rubric/ 子目录归 step-4"
- step-3 §8 修改节（L125）已写明"skillpack/quality/rubric/ 子目录归 step-4 owned，本 step 仅新建 rubric/ 以外的 5 件文件"
- master §5 step-3 owned_paths（L90）已写"skillpack/quality/（适配卡及 rubric/ 子目录除外）"
- step-4 §7 task packet owned_paths 保持 `skillpack/quality/rubric/`，与 step-3 无重叠

### F-NEW-2（P2）：step-6 required_context 补充 llm_router.md
**状态：✅ VERIFIED**

- step-6 §7 required_context（L131）已补充 `docs/interface-contracts/llm_router.md`（step-3 产物，接口签名）

### Codex SC-3（P2）：master SC-3 verify 补充 total/negative_count/repair_rounds 断言
**状态：✅ VERIFIED**

- master SC-3 verify 命令（L51）已补：
  - `assert r.get('total_samples',0)==20,'total!=20'`
  - `assert r.get('negative_count',0)==2,'negative!=2'`
  - `assert all(e.get('repair_rounds',0)<=3 for e in r.get('entries',[])),'repair_rounds exceeded 3'`

### Codex SC-8（P2）：master SC-8 verify 补充 len(sources)==76 和 len(ledger_ids)==76
**状态：✅ VERIFIED**

- master SC-8 verify 命令（L66）已补：
  - `assert len(sources)==76, f'sources={len(sources)}!=76'`
  - `assert len(ledger_ids)==76, f'ledger_ids={len(ledger_ids)}!=76'`

### master §5 step-3 owned_paths 更新排除 rubric/
**状态：✅ VERIFIED**

- master §5 step-3 行（L90）owned_paths 已写"skillpack/quality/（适配卡及 rubric/ 子目录除外）"

---

## 全量扫描 Findings

### R2-1（P2）：step-3 §7 yaml 块 owned_paths 未同步 rubric/ 排除

**文件：** step-3-admission-distill.md §7（L114）

**问题描述：**

§7 有两处声明：表格行（L106）与 yaml 块（L114）。
- 表格行（L106）：`skillpack/quality/(适配卡 repair-adapter-card.yaml 除外，rubric/ 子目录归 step-4)`——已正确排除
- yaml 块（L114）：`owned_paths: [... skillpack/quality/, ...]`——仍为泛指全目录，**未包含 rubric/ 排除说明**

Task packet 的 yaml 块是 `/gd execute` 派发执行者时机读取的主要格式（dispatch_map.json 引用 step 文件路径，执行者按 yaml 块解析边界约束）。若 yaml 块与表格行不一致，执行者可能依据 yaml 块认为 `skillpack/quality/` 整目录归本 step 所有，与 step-4 的 `rubric/` 产生运行时边界混淆。

**修复建议：**
将 step-3 §7 yaml 块 owned_paths 行修改为与表格行一致：
```yaml
owned_paths: [src/akb2/quality/admission.py, src/akb2/quality/distill.py, src/akb2/quality/llm_router.py, "skillpack/quality/(repair-adapter-card.yaml 和 rubric/ 子目录除外)", tests/quality/test_admission*.py, tests/quality/test_distill*.py]
```
或拆成注释说明。确保 yaml 块与表格行边界声明字面一致。

---

### R2-2（P2）：step-6 SC-6.4 verify 未同步 master SC-3 新增的 total/negative_count/repair_rounds 断言

**文件：** step-6-runner-calibration.md §4 SC-6.4（L50）

**问题描述：**

step-6 SC-6.4 明确注为"（master SC-3）"，是 master SC-3 在 step-6 的本地落点。master SC-3 在 Round 1 修复中已补入：
- `total==20`
- `negative_count==2`
- `all repair_rounds<=3`

但 step-6 SC-6.4 verify 命令（L50）仍为旧版：
```python
assert r['positive_pass_rate']>=0.9 and r['negative_reject_rate']==1.0
```
未包含三条新断言。若校准程序报告 total=15 且修复轮超限，step-6 SC-6.4 仍会 PASS，但 master SC-3 会 FAIL，出现 step-6 通过而 master 不通过的矛盾——执行审查时无法收口。

**修复建议：**
将 step-6 SC-6.4 verify 命令与 master SC-3 verify 命令对齐，补充相同的三条断言：
```python
assert r.get('total_samples',0)==20,'total!=20'
assert r.get('negative_count',0)==2,'negative!=2'
assert all(e.get('repair_rounds',0)<=3 for e in r.get('entries',[])),'repair_rounds exceeded 3'
```

---

## 已确认无问题项

- master SC-1~8 全部有可执行 verify 命令 + 输出断言 ✅
- master §5 wave matrix owned_paths 不重叠（step-3 rubric.md 文件 ≠ step-4 rubric/ 目录，路径不冲突）✅
- step-3 SC-3.6 verify 已逐件检查 5 件文件（两轮 for 循环）✅
- step-3 §8 rubric/ 排除说明已同步 §7 表格行与 §8 修改节 ✅
- step-4 §7 required_context 引用 `docs/interface-contracts/llm_router.md` ✅
- step-6 §7 required_context 补充 `docs/interface-contracts/llm_router.md` ✅（F-NEW-2 已修）
- step-1~6 blocked_by / can_parallel_with 与 wave matrix 一致 ✅
- launchd 不自行注册，SC-6.6 未加载断言钉死 ✅
- constitution P1 有界循环（具名常量）全部 step 均有 ✅
- maker/checker 异族分离声明完整（deepseek 提炼/gpt-5.4 复核/gemini-flash 兜底）✅
- 升格提议模式开关（AKB2_AUTO_PROMOTE=0）SC-7 测试钉死 ✅
- SC-3.6 yaml 块 `forbidden` 字段含 `repair-adapter-card.yaml` ✅
- step-2 §8 修改节已含 `tests/quality/fixtures/intake/`（前轮 F-NEW-3 P3 建议修复已在文件中，见 L126）✅

---

## 总结

| Finding | 严重级别 | 文件 | 状态 |
|---------|---------|------|------|
| F-NEW-1（前轮）step-3/step-4 rubric 边界重叠 | P1 | step-3 §7§8 / master §5 | ✅ 已修复 |
| F-NEW-2（前轮）step-6 required_context 缺 llm_router 路径 | P2 | step-6 §7 | ✅ 已修复 |
| Codex SC-3（前轮）master SC-3 缺三条断言 | P2 | master §3 | ✅ 已修复 |
| Codex SC-8（前轮）master SC-8 缺数量断言 | P2 | master §3 | ✅ 已修复 |
| master §5 step-3 owned_paths 同步更新 | P1 | master §5 | ✅ 已修复 |
| R2-1 step-3 §7 yaml 块 owned_paths 未同步 rubric/ 排除 | P2 | step-3 §7 L114 | ❌ 需修复 |
| R2-2 step-6 SC-6.4 verify 未同步 master SC-3 新断言 | P2 | step-6 §4 L50 | ❌ 需修复 |

**GD_REVIEW_DECISION: REQUIRES_CHANGES**

R2-1 和 R2-2 均为 P2，建议同批修复后进入最终 review 收口。无新 P1 发现。
