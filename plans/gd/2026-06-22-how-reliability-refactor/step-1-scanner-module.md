# Step 1: source_ops_scanners 模块 + golden 基准 v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-23 | 状态: draft | 对应 master-plan: P0-1

---

## 1. 目标链

```text
PHASE_GOAL: 把"确定性 How"从 LLM 选择变代码强制渲染（见 master-plan）
TASK_GOAL:  建独立 scanner 模块，代码扫 raw 产 evidence inventory 三类 must_cover
            （source_operation / source_boundary / source_conclusion），每项带 raw_anchor_span；
            产 test-fresh golden 清单。验"原文协议/CLI/代码操作 0 漏"。
```

## 2. Review 对齐

- REVIEW_DOMAIN: `ai_infra`
- REVIEW_FOCUS: `scanner 精确回查原文(raw_anchor_span 可定位回 raw 行); 三类 must_cover 全覆盖; 吸收复用现有 _SOURCE_PROTOCOL_COMMANDS/_raw_source_how_steps 不另起炉灶; golden 基准防"0 漏"空泛打勾`

## 3. Task Packet

- blocked_by: `—`
- can_parallel_with: `—`（step-6 属 P1，须 P0 完成后启动；P0 step 不与 P1 并行——分期纪律，Codex r4 问题5）
- owned_paths: `src/akb2/quality/source_ops_scanners/**`、`tests/quality/test_source_ops_scanners.py`、`tests/quality/fixtures/test-fresh-golden-ops.json`
- forbidden_paths: `src/akb2/quality/distill.py`（step-2 owned；本 step 只新建可被 import 的模块，不改 distill）、`checks_structural.py`、`grader.py`
- required_context:
  - `src/akb2/quality/distill.py:2412`（`_SOURCE_PROTOCOL_COMMANDS` 8 个 MCP 命令，迁移源）
  - `src/akb2/quality/distill.py:2612`（`_raw_source_how_steps` 扫描逻辑参考）
  - `/Users/praise/Obsidian/Praise Space/AKB2.0/raw/test-fresh-entry--raw--2026-06-21.md`（标注语料）
- deliverables: scanner 模块（`__init__.py` + 5 类 scanner + 三类 must_cover 聚合）、golden fixture、单测
- Hard-stop: 无（scanner 用 raw_anchor_span 字符/行范围定位，不强依赖 §X；"raw 必须 §X 锚点化"是提炼链路契约，由链路层 SG-raw-anchor 管，不在 scanner 层 hard-stop——Codex r5 剩余风险）

## 4. 成功标准（SC）

- [ ] SC-1.1（三类捞全）: scanner 对 test-fresh 的 inventory 对 operation/boundary/conclusion 三类分别 ⊇ golden set
  - verify (method: test): `PYTHONPATH=src python -m pytest tests/quality/test_source_ops_scanners.py -q`
  - expect: exit 0；三类漏检数均为 0
- [ ] SC-1.2（可追溯）: 每个 evidence item 的 `raw_anchor_span` 能在 raw 原文定位到对应行/字符
  - verify (method: test): 断言 `raw[span.start:span.end]` 含该 item 的 `literal`
  - expect: 每 item span 命中

## 5. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/source_ops_scanners/__init__.py（新建）
  WHAT:  定义 EvidenceItem dataclass(kind/literal/raw_anchor_span/must_cover_class) + scan_all(raw_text)->list[EvidenceItem] 聚合入口
  WHY:   现无任何"原文有哪些操作"的确定性清单，LLM 漏了无从知
  VERIFY: python -c "from akb2.quality.source_ops_scanners import scan_all"

Step.2
  WHERE: src/akb2/quality/source_ops_scanners/operations.py（新建）
  WHAT:  cli_commands($ cmd / fenced bash) + code_blocks(fenced 内命令) + api_signatures(func(...)/obj.method(...)) + numbered_steps(原文编号步骤) + mcp_protocol(迁移 distill.py:2412 _SOURCE_PROTOCOL_COMMANDS)，全部产 kind=source_operation
  WHY:   现仅扫写死 8 个 MCP 命令，CLI/代码/API/步骤全漏
  VERIFY: python -m pytest tests/quality/test_source_ops_scanners.py -k operation -q

Step.3
  WHERE: src/akb2/quality/source_ops_scanners/boundaries.py + conclusions.py（新建）
  WHAT:  boundary scanner 识别"仅适用/前提/要求/不支持/限于"明示边界句→source_boundary；conclusion scanner 识别原文明确结论句→source_conclusion
  WHY:   master-plan Q2 三类 must_cover，boundary/conclusion 不能只停在 operation
  VERIFY: python -m pytest tests/quality/test_source_ops_scanners.py -k "boundary or conclusion" -q

Step.4
  WHERE: tests/quality/fixtures/test-fresh-golden-ops.json（新建）
  WHAT:  人工标注 test-fresh raw 的三类 must_cover 项 + 各自 raw_anchor，作为 SC-1 验收基准
  WHY:   无 golden 基准则"0 漏"无法判定（Codex r2 finding P1 / 规则 C）
  VERIFY: test -f tests/quality/fixtures/test-fresh-golden-ops.json && python -c "import json;json.load(open('tests/quality/fixtures/test-fresh-golden-ops.json'))"

Step.5
  WHERE: tests/quality/test_source_ops_scanners.py（新建）
  WHAT:  正样本(test-fresh inventory ⊇ golden 三类) + 负样本(空文本=空 inventory) + span 可追溯断言
  WHY:   SC-1.1/1.2 验收
  VERIFY: python -m pytest tests/quality/test_source_ops_scanners.py -q
```

## 6. 边界

修改：仅 `source_ops_scanners/**` + 对应测试/fixture
不修改：`distill.py`（step-2 迁移调用归 step-2）、`checks_structural.py`（step-3）、`grader.py`、`/Users/praise/.claude/**`、其他 step owned_paths

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| scanner 正则误抓/漏抓 | golden fixture 比对 + 负样本测试；正则保守起步，宁缺勿滥 |
| boundary/conclusion 语义识别不准 | 这两类起步用保守 pattern（明示连接词），不确定的不进 must_cover，留 LLM |

## 8. 检查点
单独 commit（scanner 模块自包含，不动主链路）。
