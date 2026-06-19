# Claude Self-Review Fresh R4（最终验证轮，只审不修）

```
REVIEWER: claude_subagent_fresh_r4
REVIEW_TARGET: master-plan.md SC-5/SC-8 final verification
REVIEW_KIND: plan
REVIEW_RUN_STATUS: completed
GD_REVIEW_DECISION: REQUIRES_CHANGES
```

## 审查范围

Round 3 与 Codex R3 时序竞争期间落入 master-plan.md 的两项新修复：

1. SC-5 verify 追加 `jsonschema.validate` 对 `skillpack/quality/repair-adapter-card.yaml` 的实例校验（master L56-57）
2. SC-8 verify 的 `req_fields` 加入 `trial_fetch` 字段断言（master L65-66）

验证方法：逐字拷贝两条 `python -c` 片段，在 `_tmp/debug/` 沙箱内用合成 fixture 实跑正/负路径（已清理）。

---

## 验证结果

### 改动 1：SC-5 实例校验 — 命令本身 PASS

| 检查项 | 结果 | 证据 |
|--------|------|------|
| `python -c` 语法（引号嵌套/分号串联） | pass | 沙箱实跑输出 `adapter-card schema OK`，exit 0 |
| `jsonschema.validate(instance, schema)` 参数顺序 | pass | 位置参数 (instance, schema) 与库签名一致 |
| `yaml.safe_load` 加载实例 | pass | 实测正常 |
| 依赖可用性 | pass | `jsonschema>=4.0`、`pyyaml>=6.0` 均在 pyproject dependencies（本机实测 4.26.0 / 6.0.3） |
| 与 step-1 SC-s1 一致性 | pass | SC-s1 只管 schema 自洽；step-1 §5 非目标明确实例归 step-6；step-1 §11 声明"适配卡 v1 实例由 step-6 收口"——SC-5 扩展与该分工不冲突 |
| 与 step-6 SC-6.7 等价性 | pass | SC-6.7（test_adapter_card_schema.py 按 step-1 schema 校验 9 字段）与 SC-5 内联 jsonschema 校验语义等价，互为 pytest 封装/直接命令两种形态 |

观察（不计 finding）：`yaml.safe_load` 会把无引号日期字面量解析为 `datetime` 对象，若 schema 对应字段 `type: string` 会校验失败。适配卡 v1 写作时日期类值需引号化。

### 改动 2：SC-8 trial_fetch 断言 — 断言本身 PASS，但暴露既有跨文件字段命名冲突

| 检查项 | 结果 | 证据 |
|--------|------|------|
| `python -c` 语法（f-string 嵌套引号） | pass | 沙箱 happy path 输出 `PASS`，exit 0 |
| 负路径有效性 | pass | 删除单行 `trial_fetch` 后断言失败并输出 `MISSING FIELDS in: ['src-005']`，可定位 |
| 与 step-2 SC-2.5 字段集一致（trial_fetch） | pass | step-2 L53 字段集 `{'url','feed','reachable','latest_post','trial_fetch'}` 已含；Step.5 WHAT（L95）逐源记录字段亦含 trial_fetch |
| 与 step-2 config/ledger 字段命名一致 | **fail** | 见 F-R4-1 |

---

## Findings

### F-R4-1 [P2] master SC-8 依赖的 `id`/`source_id` 字段在 step-2 契约中不存在，按 step-2 交付必然 KeyError

- 位置：master-plan.md L66（SC-8 verify）↔ step-2-intake-raw.md L41（SC-2.1）、L53（SC-2.5）、L75/L95（Step.1/Step.5）
- 问题：master SC-8 verify 使用 `expected_ids={s['id'] for s in sources}` 与 `r['source_id']`，但 step-2 SC-2.1 定义的 config schema 为 `sources[].slug/domain/feed_url|listing_url/rate_limit/status`（**无 `id`**）；step-2 Step.5 的 ledger 行字段为 `url/feed/reachable/latest_post/trial_fetch`（**无 `source_id`**），SC-2.5 verify 的字段集同样不含 `source_id`。
- 实证：沙箱用 step-2 契约（slug）生成 config 后执行 master SC-8 片段，第一个表达式即 `KeyError: 'id'`（连可读 assert 消息都到不了）。
- 来源：R2 轮 F-C2 修复在 master 侧扩展了源 ID 对账断言，但未同步 step-2 契约；R3 验证范围（R2-1/R2-2）未覆盖此处。
- 最小修复（二选一对齐）：
  - a) master SC-8 改用 `s['slug']`，ledger 对账键改 `source_slug`，并在 step-2 Step.5/SC-2.5 增补该键；或
  - b) step-2 SC-2.1/Step.1 schema 增 `id` 字段、Step.5/SC-2.5 ledger 增 `source_id` 字段。
- verify：修复后按 step-2 契约生成 76 源 config + 76 行 ledger，master SC-8 命令应输出 `PASS`；删除任一源记录应输出 `MISSING: {...}`。

### F-R4-2 [P3] step-2 SC-2.5 自称"= master SC-8"但断言强度未同步

- 位置：step-2-intake-raw.md L52-53 ↔ master-plan.md L65-66
- 问题：SC-2.5 只断言 `len(rs)>=70` + 5 字段子集；master SC-8 要求 76 源 ledger 全覆盖（死源也留痕）+ 源 ID 对账 + `reachable>=70`。执行者按 SC-2.5 本地自验通过（如 72 行）仍会在 master 验收翻车。与 R2-2（step-6 SC-6.4 ↔ master SC-3 同步）同类问题。
- 降级理由（P3 而非 P2）：master 门是最终验收兜底，弱化的 step SC 不会放错误产物出厂，只造成执行者反馈延迟；且修复 F-R4-1 时顺手同步即可。
- 最小修复：SC-2.5 verify 与 master SC-8 逐字对齐（含 76 覆盖断言），或注明"本 SC 为开发期弱门，出厂以 master SC-8 为准"。

### F-R4-3 [P3] master §5 概览表 step-6 行"主要 SC"列未含 SC-5

- 位置：master-plan.md L93
- 问题：SC-5 verify 现在显式依赖 step-6 交付物（`skillpack/quality/repair-adapter-card.yaml` 实例），step-6 §7 sc_refs 已声明"SC-5(适配卡份额)"、step-1 §11 也声明"实例由 step-6 收口"，但 master §5 表 step-6 行仅列 "SC-1, SC-3, SC-6"。仅追溯性缺口，不影响执行正确性。
- 最小修复：step-6 行"主要 SC"列补 `SC-5(适配卡)`。

---

## 残余观察（前轮已登记，不重复计 finding）

- R-1（fresh r1）：`trial_fetch` 只断言 key 存在，未断言非空/状态枚举——属已知可接受残余。

## 结论

两项新改动的命令语法、断言逻辑、jsonschema 调用、yaml 加载本身全部实证通过；trial_fetch 与 step-2 字段集一致；SC-5 与 step-1/step-6 分工及 SC-6.7 等价性成立。但 SC-8 的源 ID 对账断言（R2 引入、本轮首次跨文件核对）与 step-2 的 `slug` 字段约定冲突，按 step-2 契约交付时 master SC-8 必然 KeyError，故判 REQUIRES_CHANGES。修复为纯字段命名对齐，不涉及设计变更。

日期：2026-06-12
