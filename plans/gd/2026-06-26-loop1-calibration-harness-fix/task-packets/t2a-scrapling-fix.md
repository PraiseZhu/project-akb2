# Task Packet: t2a-scrapling-fix

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project AKB2/docs/constitution.md (v1.2.0)
TEMPLATE_KIND: gd-task-packet

> 自包含合约：子 agent 只读本 packet + `required_context`，不读其他对话内容。

---

## 1. 标识

```yaml
task_id: t2a-scrapling-fix
agent_role: implementer
parent_step: step-2-intake-corpus
parent_plan: plans/gd/2026-06-26-loop1-calibration-harness-fix/step-2-intake-corpus.md
created_at: 2026-06-26T07:06:31Z
```

---

## 2. 目标链

```text
PROJECT_GOAL: 让 AKB2 循环1 能跑一次可信的满仓校准（宪法 v1.2.0 原则 3/4/6）
CHAIN_GOAL:   只修测试侧 harness，不动生产 admission/distill/checks_structural/grader/repair
PHASE_GOAL:   活语料端到端薄切片流过 intake_raw（抽取器不再因 API 失配静默失败）
TASK_GOAL:    修复 extract.py 的 Scrapling fallback 调用（scrapling 0.4.8 正确 API），加不回归测试覆盖 adaptive/browser/抛错三路径
```

---

## 3. 依赖与并发

```yaml
blocked_by: []
can_parallel_with:
  - t2b-preflight-corpus
required_context:
  # 注：owned 的 extract.py 为本 packet 自有，执行时直接读改，按标准 §6 不列入 required_context
  - src/akb2/quality/intake/models.py
```

---

## 4. 路径权限

```yaml
owned_paths:
  - src/akb2/quality/intake/extract.py
  - tests/test_extract_scrapling_fallback.py
forbidden_paths:
  - 旧 /rev artifacts
  - "/Users/praise/.claude/**"
  - src/akb2/quality/calibration.py
  - src/akb2/quality/intake/preflight.py
  - evals/main-pipeline-quality/
  - config/main_sources.yaml
  - src/akb2/quality/admission.py
  - src/akb2/quality/distill.py
  - src/akb2/quality/checks_structural.py
  - src/akb2/quality/grader.py
  - src/akb2/quality/repair.py
```

> 注意：`extract.py` 仅允许改 `_extract_via_scrapling`（:226-259）的 Scrapling 调用点；不得改抽取决策树 / `_validate_metadata` / `_apply_feed_fallback` / ExtractResult 结构。

---

## 5. 成功标准（SC）

- [ ] SC-5：`_extract_via_scrapling` 用 scrapling 0.4.8 正确 API（`Fetcher.get` / `StealthyFetcher.fetch`），无 `'Fetcher' object has no attribute 'fetch'`；fallback 失败仍 fail-closed 回落 docling/签名（不回归）

---

## 6. 交付物

```yaml
deliverables:
  - path: src/akb2/quality/intake/extract.py
    kind: file
    must_exist: true
    description: _extract_via_scrapling :241-249 改用 Fetcher.get / StealthyFetcher.fetch（classmethod），保留 except 兜底与 fail-closed 回落
  - path: tests/test_extract_scrapling_fallback.py
    kind: file
    must_exist: true
    description: adaptive 成功 / browser 路径 / 抛错 fail-closed 三类直测，全程 mock 无网络
```

---

## 7. 验证（Anti-fill 硬约束）

```yaml
verify:
  - sc_ref: SC-5
    method: test
    cmd: "pytest tests/test_extract_scrapling_fallback.py -q"
    expect: "passed"
  - sc_ref: SC-5
    method: assertion
    cmd: "grep -c 'Fetcher.get' src/akb2/quality/intake/extract.py"
    expect: ">=1"
```

---

## 8. Handoff 输出

```yaml
handoff_output:
  result_path: plans/gd/2026-06-26-loop1-calibration-harness-fix/results/t2a-scrapling-fix-result.md
  status_field: <见 gd-execution-status.schema.json>
  summary: Scrapling fallback 改用 0.4.8 classmethod API + 三路径不回归测试通过
  blockers: 无（实测 0.4.8 API 已确认，纯 call-site 修复）
```

---

## 9. 范围禁令

- 禁止写入 `calibration.py`/`preflight.py`/`evals/`/`config/main_sources.yaml`/包 A。
- Scrapling 修复必须保留 `except Exception` 兜底与抽取全失败时 fail-closed 回落 docling/签名签名链路。
- 禁止 pip install / 升级 / pin scrapling；禁止访问 `/Users/praise/.claude/**`；禁止启动 daemon/hook/cron。
