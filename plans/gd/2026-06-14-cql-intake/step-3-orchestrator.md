# Step 3: orchestrator CLI + intake state ledger

GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-14 | 状态: draft

---

## 1. 目标链

```text
TASK_GOAL: 新建 orchestrator.py（读 main_sources.yaml → discover_articles → extract → archive）；intake state ledger 状态外置；rate_limit/quarantine skip；CLI --dry-run。discovery listing 实现归 step-5，orchestrator 只作为消费方调用 discover_articles。
```

REVIEW_DOMAIN: ai_infra
REVIEW_FOCUS: `orchestrator 状态外置设计(F7); rate_limit/quarantine 逻辑(F7 SC); article_id 幂等(F3); dry-run 不打真网`

---

## 3. 成功标准（SC）

- [ ] SC-5（article_id 唯一身份与幂等归档）: 同源同日两篇不覆盖；同输入重跑跳过
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_orchestrator.py -q -k "article_id or idempotent"`
  - expect: exit 0; "同源同日两篇产 2 个不同文件名；重跑同 URL 仅 1 个文件"

- [ ] SC-7（orchestrator dry-run）: CLI --dry-run 无错退出；stdout 含 discovered/skipped/archived 计数；不写任何 raw 文件
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && PYTHONPATH=src python -m akb2.quality.intake.orchestrator --dry-run --limit 3 --sources-config config/main_sources.yaml && echo PASS`
  - expect: PASS; stdout 包含 "discovered=" "skipped=" "archived=" 三个字段且 archived=0

- [ ] SC-8（intake state ledger）: ledger 含 run_id/source_slug/article_id/status；rate_limit 超限 skip；quarantine 命中不写 raw
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_orchestrator.py -q -k "rate_limit or quarantine or ledger"`
  - expect: exit 0; "rate_limit/quarantine 跳过断言、ledger 记录 run_id 和 status 断言全过"

---

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/intake/orchestrator.py（新建）
  WHAT:  读 main_sources.yaml → discover_articles（RSS/listing）→ extract_content（传 ExtractionContext）→ archive_from_extract_result；状态外置到 reports/intake/intake-state.jsonl
  WHY:   F7 无状态则 rate_limit/quarantine 失效；F3 幂等依赖 ledger
  VERIFY: PYTHONPATH=src python -m akb2.quality.intake.orchestrator --dry-run --limit 3 --sources-config config/main_sources.yaml && echo PASS

Step.2
  WHERE: src/akb2/quality/intake/orchestrator.py（rate_limit + quarantine 逻辑）
  WHAT:  rate_limit 超限 → status=skipped_rate_limit，不调 extract；quarantine 命中 → status=skipped_quarantine，不写 raw；daily_count 写 ledger
  WHY:   check_quarantine/rate_limit 已在 discovery.py 实现但无人调
  VERIFY: python -m pytest tests/quality/test_intake_orchestrator.py -q -k "rate_limit or quarantine"

Step.3
  WHERE: tests/quality/test_intake_orchestrator.py（新建）
  WHAT:  dry-run、article_id 唯一、幂等重跑、rate_limit 跳过、quarantine 跳过、ledger 字段完整各 1 个 fixture 用例
  WHY:   SC-5/SC-7/SC-8 测试覆盖
  VERIFY: python -m pytest tests/quality/test_intake_orchestrator.py -q
```
