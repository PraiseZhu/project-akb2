# Step 3: SG-must-cover fail-closed + 调用点审计 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft | 对应 master-plan: P0-3

---

## 1. 目标链

```text
TASK_GOAL: run_candidate_self_gate 加 SG-must-cover：内部用 scanner 扫 raw_content 得 must_cover →
           比对正文 → 缺失即 Defect；raw_content=None 也 fail-closed（不静默跳过）。
           审计全部 self-gate 调用点确保传真实 raw 或显式 fail-closed。
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `SG-must-cover 漏即 fail-closed; raw_content=None 不静默跳过(Codex r2 P1); 全调用点审计(Codex r3 问题3); 复用现有 Defect + SG 检查模式`

## 3. Task Packet

- blocked_by: `step-1`（用 `scan_all`）
- can_parallel_with: `step-2`（改 distill，与 checks_structural 不重叠）
- owned_paths: `src/akb2/quality/checks_structural.py`（`run_candidate_self_gate` 内 SG-must-cover）、`tests/quality/test_checks_structural.py`（新增用例）
- forbidden_paths: `source_ops_scanners/**`（step-1，只 import）、`distill.py`（step-2）、`repair.py`/`runner.py`/`smoke_validator.py`（仅审计，不改其逻辑——现状已传 raw）
- required_context:
  - `checks_structural.py:977`（run_candidate_self_gate 签名 raw_content 默认 None L979）、`:1087`（return defects 接入点）、`:35`（Defect dataclass）、`:993-1086`（现有 7 SG 检查模式）
  - 调用点（**现状已确认全传真实 raw**）：`distill.py:1481`、`repair.py:1125/1248`、`runner.py:629`、`smoke_validator.py:441`
- deliverables: SG-must-cover 检查 + raw=None fail-closed + 调用点静态审计测试
- Hard-stop: 旧 `test_checks_structural.py` raw-optional 用例若因 fail-closed 变红 → 分离语义（不得为过测试把 SG-must-cover 做成可跳过）

## 4. 成功标准（SC）

- [ ] SC-3.1（漏即挡）: 原文有操作但正文漏渲染 → 返回 `dimension="SG-must-cover"` Defect；不漏 → 不产
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_checks_structural.py -k must_cover -q`
  - expect: 漏→defect，不漏→空
- [ ] SC-3.2（raw 缺失 fail-closed）: `run_candidate_self_gate(text, raw_content=None)` 进 SG-must-cover → 产 fail-closed Defect
  - verify (method: test): 断言 raw_content=None 时返回含 SG-must-cover 的 Defect
  - expect: 不静默跳过
- [ ] SC-3.3（调用点 provenance 审计，Codex r4 问题2）: 5 处调用点逐个证明"真实同源 raw"或"显式 fail-closed"——不只验非 None，还查 raw 来源变量 + fixture 证明 `scan_all(raw)` 能扫出该候选预期 must_cover
  - verify (method: test): 对 `distill.py:1481`/`repair.py:1125`/`repair.py:1248`/`runner.py:629`/`smoke_validator.py:441` 逐点：raw 来源可追溯到原始 raw 文件（非截断/非异源）+ `scan_all(raw)` 非空且含预期 must_cover
  - expect: 5 点全有"真实同源 raw"证据；审计失败 → 允许最小修改该 raw 传递（归对应文件 owner）或 hard-stop，**不得标 pass**

## 5. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/checks_structural.py:run_candidate_self_gate，return defects(L1087) 之前
  WHAT:  raw_content 非空 → scan_all(raw_content) 得 must_cover → 比对 body → 缺失项 append Defect(dimension="SG-must-cover", location=对应节, defect=漏的字面量)
  WHY:   现无任何机制保证"原文有的必进正文"
  VERIFY: python -m pytest tests/quality/test_checks_structural.py -k must_cover -q

Step.2
  WHERE: 同函数，SG-must-cover 块开头
  WHAT:  raw_content is None → append Defect(dimension="SG-must-cover", defect="raw_content 缺失，无法验证 must_cover 覆盖")，不跳过
  WHY:   Codex r2 P1：optional raw 静默跳过会打穿 fail-closed
  VERIFY: python -m pytest tests/quality/test_checks_structural.py::test_must_cover_raw_none_failclosed -q

Step.3
  WHERE: tests/quality/test_checks_structural.py（新增）
  WHAT:  逐调用点 raw provenance 审计：对 5 处（distill.py:1481 / repair.py:1125 / repair.py:1248 / runner.py:629 / smoke_validator.py:441）证明 raw 来源可追溯到原始 raw 文件（非截断/异源）+ fixture 证明 scan_all(raw) 非空且命中预期 must_cover；旧 raw-optional 语义(check_a3 raw=None)与新 SG-must-cover fail-closed 分开断言
  WHY:   Codex r3 问题3：调用点写窄了，需逐点确认
  VERIFY: python -m pytest tests/quality/test_checks_structural.py -k "must_cover or callsite" -q
```

## 6. 边界

修改：仅 `checks_structural.py` 的 self-gate + 新测试
默认不改调用点（现状 5 处已传真实 raw）；**若 provenance 审计发现某调用点传错/截断/异源 raw → 允许最小修复该 raw 传递（归对应文件 owner）或 hard-stop，不得标 pass**（Codex r5 问题1）。
不修改：scanner 模块、distill 抽取、grader

## 7. 检查点
与 step-2 一起 commit。
