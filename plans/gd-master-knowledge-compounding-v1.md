# 知识复利（Knowledge Compounding）v1

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

日期：2026-05-12
状态：draft
负责人：Claude 执行

---

## 1. 目标链

```text
PROJECT_GOAL: 在 AKB2.0 中建立知识复利机制，使查询综合结果能低摩擦地回写为 wiki 页面，让知识库越用越厚。
CHAIN_GOAL:   让每次有价值的查询都产出一个可被未来查询发现的持久化知识页面，消除"问完就忘"的信息耗散。
PHASE_GOAL:   实现端到端 query→save→discover 闭环：index 生成 + query-result 模板 + compound 保存协议 + lint 覆盖。
```

---

## 2. Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS：`index 生成正确性; query-result 模板与现有 12 模板兼容; compound 保存不绕过质量底线; lint 新规则无误报; 端到端闭环可复现`
- Domain-specific notes：vault.toml `allow_runtime_in_vault = false` 约束不变；所有新 Python 代码在 `Project AKB2/src/`，vault 内只增加 Markdown 数据文件

---

## 3. 成功标准（SC）

- [ ] SC-1：`wiki/_index.md` 由 `python3 src/akb2/index/generate_index.py` 生成，覆盖所有 `page_status: active` 的 wiki 页面，每页含 title + TLDR 一句话 + tags
  - verify: `python3 src/akb2/index/generate_index.py --dry-run | head -5` 输出有效 Markdown 且 exit 0
- [ ] SC-2：vault 内容标准 `AKB-2.0-CONTENT-STANDARD.md` 新增 `query-result` 模板定义，含 `derived_from`、`query`、`confidence` 必填字段
  - verify: `grep -c 'template_type.*query-result' "${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md"` >= 1
- [ ] SC-3：`akb2_lint.py` 对 `template_type: query-result` 页面检查 `derived_from` 引用的页面存在、`confidence` 枚举合法（high/medium/low），无误报
  - verify: `python3 src/akb2/lint/akb2_lint.py --dry-run` 对 fixture query-result 页面 exit 0
- [ ] SC-4：`akb2_gate_check.py` 对 `template_type: query-result` 应用简化 HC 集（跳过 HC-04 source_type 外部源验证、HC-05 原始证据字段），其余 HC 正常执行
  - verify: `python3 src/akb2/gate/akb2_gate_check.py --dry-run` 对 fixture query-result candidate exit 0
- [ ] SC-5：端到端闭环可执行——手动创建一个 query-result 页面 → lint 通过 → index 重新生成后包含该页
  - verify: 创建 fixture → 跑 lint → 跑 index → grep 新页标题出现在 index 输出中

---

## 4. 非目标（NON_GOALS）

- 不建 MCP `akb_save` 写入工具（Claude Code 的 Write 工具已够用，不需要额外 MCP 层）
- 不建自动 LLM 语义 lint（矛盾检测属后续增量，本期只做结构性 lint）
- 不改 `akb_search` MCP 服务端（index.md 由 Claude Code 直接 Read，不走 MCP 检索）
- 不建 log.md 自动追踪（操作日志属锦上添花，不影响核心复利闭环）
- 不改 L4 人工授权流程（query-result 仍走 staging→gate，但 gate 路径简化）
- 不接 qmd 或向量检索（当前 wiki 规模 ~100 页，index.md + 上下文窗口够用）

---

## 5. Step 拆分

| Step | 名称 | owned_paths | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------|-----------|-------------------|---------|
| 1 | query-result 模板定义 | vault `AKB-2.0-CONTENT-STANDARD.md` 相关章节 | — | step 2 | SC-2 |
| 2 | index.md 生成器 | `src/akb2/index/`, `tests/test_index_*.py` | — | step 1 | SC-1 |
| 3 | gate check 简化路径 | `src/akb2/gate/akb2_gate_check.py`, `tests/test_gate_*.py` | step 1 | — | SC-4 |
| 4 | lint 规则扩展 | `src/akb2/lint/akb2_lint.py`, `tests/test_lint_*.py` | step 1 | step 3 | SC-3 |
| 5 | E2E 集成验证 | `tests/test_compound_e2e.py`, fixture 文件 | step 1-4 | — | SC-5 |

---

## 6. 边界（修改 / 不修改）

修改：
- `Project AKB2/src/akb2/index/`（新建）
- `Project AKB2/src/akb2/gate/akb2_gate_check.py`（增加 query-result 简化路径）
- `Project AKB2/src/akb2/lint/akb2_lint.py`（增加 query-result 规则）
- `Project AKB2/tests/`（新增测试文件）
- vault `AKB-2.0-CONTENT-STANDARD.md`（新增 query-result 模板章节）

不修改：
- `toolbox/cc-akb-search/mcp_server/server.py`（MCP 服务端不动）
- `/Users/praise/.claude/**`
- `Project AKB2/config/vault.toml`（路径契约不变）
- 现有 12 种模板的 gate/lint 逻辑
- 旧 AKB 1.0 任何文件

---

## 7. 风险与防护

| 风险 | 防护 |
|------|------|
| query-result 幻觉固化：LLM 综合答案含错误，保存后被未来查询引用 | `confidence` 字段强制标注；`derived_from` 可追溯源页；lint 检查源页存在性 |
| index.md 过大溢出上下文 | 每页只含 title + TLDR（~50 字符），100 页 index ≈ 5KB，远低于上下文限制 |
| query-result 泛滥降低 wiki 信噪比 | 保留 staging→gate 流程（虽简化），不允许直接写 active；`confidence: low` 的页面 lint 警告 |
| 简化 gate 路径被非 query-result 页面利用 | gate check 按 `template_type` 分派，非 query-result 走原完整 HC 集 |

---

## 8. 测试计划

```bash
# SC-1: index 生成器
python3 src/akb2/index/generate_index.py --dry-run | head -5
# 期望: 有效 Markdown，exit 0

# SC-2: 内容标准包含 query-result 定义
grep -c 'query-result' "${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md"
# 期望: >= 1

# SC-3: lint 对 query-result 无误报
python3 src/akb2/lint/akb2_lint.py --dry-run tests/fixtures/compound/valid-query-result.md
# 期望: exit 0

# SC-4: gate 简化路径
python3 src/akb2/gate/akb2_gate_check.py --dry-run tests/fixtures/compound/candidate-query-result.md
# 期望: exit 0，gate_decision != reject

# SC-5: E2E
python3 -m pytest tests/test_compound_e2e.py -v
# 期望: all passed
```

---

## 9. Assumptions

- 当前 wiki 规模 ≤ 200 页，index.md 可完整放入 LLM 上下文（~10KB 以内）
- Claude Code 可直接 Read vault 内 Markdown 文件（已验证可行）
- `AKB-2.0-CONTENT-STANDARD.md` 的修改权在用户（本计划提供具体 diff，用户确认后执行）
- query-result 页面的创建者是 Claude Code agent（通过 Write 工具），不需要独立 CLI

---

## 10. Machine-Readable: Work Items

```json gd-work-items
{
  "work_items": [
    {
      "id": "wi-1",
      "description": "在 AKB-2.0-CONTENT-STANDARD.md 追加 query-result 模板章节，定义 derived_from/query/confidence 三个必填字段和 frontmatter schema",
      "sc_refs": ["SC-2"],
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "grep -c 'template_type.*query-result' AKB-2.0-CONTENT-STANDARD.md → >= 1"
      }
    },
    {
      "id": "wi-2",
      "description": "实现 src/akb2/index/generate_index.py，读取 vault wiki/ 下所有 active 页面的 frontmatter，输出结构化 _index.md",
      "sc_refs": ["SC-1"],
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "python3 src/akb2/index/generate_index.py --dry-run → exit 0 且输出有效 Markdown"
      }
    },
    {
      "id": "wi-3",
      "description": "修改 akb2_gate_check.py，当 template_type=query-result 时跳过 HC-04 和 HC-05，其余 HC 正常执行",
      "sc_refs": ["SC-4"],
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "python3 src/akb2/gate/akb2_gate_check.py --dry-run fixture/candidate-query-result.md → exit 0"
      }
    },
    {
      "id": "wi-4",
      "description": "修改 akb2_lint.py 新增 3 条 query-result 专用规则：R-QR-01 derived_from 引用页存在、R-QR-02 confidence 枚举合法、R-QR-03 query 字段非空",
      "sc_refs": ["SC-3"],
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "python3 src/akb2/lint/akb2_lint.py --dry-run fixture/valid-query-result.md → exit 0 无 P0/P1"
      }
    },
    {
      "id": "wi-5",
      "description": "创建 E2E 测试 test_compound_e2e.py：写 fixture query-result → lint pass → index 生成包含该页",
      "sc_refs": ["SC-5"],
      "evidence_anchor": {
        "kind": "command_expect",
        "ref": "python3 -m pytest tests/test_compound_e2e.py -v → all passed"
      }
    }
  ]
}
```

---

## 11. Machine-Readable: Step Clusters

```json gd-step-clusters
{
  "clusters": [
    {
      "cluster_id": "c1",
      "step_ids": ["step-1", "step-2"],
      "clustering_reason": {
        "type": "performance",
        "description": "模板定义和 index 生成器互不依赖，可并行开发"
      },
      "alternatives_rejected": [
        {
          "option": "step-2 等 step-1 完成后再开始",
          "reason": "index 生成器不需要知道 query-result 模板细节，只需读 frontmatter 通用字段"
        }
      ],
      "chosen": "step-1 和 step-2 并行，各自独立交付",
      "removal_proof": "index 生成器读取 page_status 和 title/tldr，不依赖 template_type 枚举扩展"
    },
    {
      "cluster_id": "c2",
      "step_ids": ["step-3", "step-4"],
      "clustering_reason": {
        "type": "dependency",
        "description": "gate 和 lint 都依赖 step-1 的模板定义，但彼此独立"
      },
      "alternatives_rejected": [
        {
          "option": "gate 和 lint 合并为一个 step",
          "reason": "gate_check.py 和 lint.py 是独立文件，修改范围不重叠，分开更易 review"
        }
      ],
      "chosen": "step-3 和 step-4 并行，都在 step-1 完成后启动",
      "removal_proof": "gate_check.py 修改 HC 分派逻辑，lint.py 增加新规则，文件无交叉修改"
    }
  ]
}
```

---

## 12. Machine-Readable: Step Plan Inventory

```json gd-step-plan-inventory
{
  "step_plans": [
    {
      "step_id": "step-1",
      "file": "plans/gd-step-1-query-result-template.md",
      "status": "draft",
      "task_packets": ["t1"]
    },
    {
      "step_id": "step-2",
      "file": "plans/gd-step-2-index-generator.md",
      "status": "draft",
      "task_packets": ["t2"]
    },
    {
      "step_id": "step-3",
      "file": "plans/gd-step-3-gate-simplification.md",
      "status": "draft",
      "task_packets": ["t3"]
    },
    {
      "step_id": "step-4",
      "file": "plans/gd-step-4-lint-extension.md",
      "status": "draft",
      "task_packets": ["t4"]
    },
    {
      "step_id": "step-5",
      "file": "plans/gd-step-5-e2e-validation.md",
      "status": "draft",
      "task_packets": ["t5"]
    }
  ]
}
```

---

## 13. Machine-Readable: Wave Matrix

```json gd-wave-matrix
{
  "waves": [
    {
      "wave_id": "w1",
      "tracks": [
        {
          "track_id": "t1",
          "mode": "execute",
          "step_ids": ["step-1"],
          "max_parallel": 2
        },
        {
          "track_id": "t2",
          "mode": "execute",
          "step_ids": ["step-2"],
          "max_parallel": 2
        }
      ],
      "blocked_by": []
    },
    {
      "wave_id": "w2",
      "tracks": [
        {
          "track_id": "t3",
          "mode": "execute",
          "step_ids": ["step-3"],
          "max_parallel": 2
        },
        {
          "track_id": "t4",
          "mode": "execute",
          "step_ids": ["step-4"],
          "max_parallel": 2
        }
      ],
      "blocked_by": ["w1"]
    },
    {
      "wave_id": "w3",
      "tracks": [
        {
          "track_id": "t5",
          "mode": "validate",
          "step_ids": ["step-5"],
          "max_parallel": 1
        }
      ],
      "blocked_by": ["w2"]
    }
  ]
}
```

---

## 附录：Karpathy LLM Wiki 对照

本计划从 Karpathy LLM Wiki（2026-04-04 Gist）提取了以下机制并适配 AKB2.0：

| Karpathy 机制 | AKB2.0 适配 | 适配原因 |
|---------------|------------|---------|
| `index.md` 目录 | `wiki/_index.md` 自动生成 | 直接复用，解决 LLM 导航效率问题 |
| `--save` 查询回写 | Claude Code Write → `staging/compound/` → 简化 gate | 保留质量门控但降低摩擦 |
| `type: query-result` | `template_type: query-result` 新模板 | 与现有 12 模板并列，复用 frontmatter schema |
| `log.md` 操作日志 | 本期不做（非目标） | 当前规模不需要，后续可加 |
| Phase 4 LLM lint | 本期不做（非目标） | 语义 lint 是增量功能，先做结构性 lint |
| qmd 混合检索 | 本期不做（非目标） | ~100 页规模 index.md 够用 |
| 微调终局 | 不在路线图内 | 当前阶段专注知识积累，微调是远期方向 |

**核心设计决策**：Karpathy 的系统让 LLM 直接写 wiki/（零门控），AKB2.0 保留 staging→gate 但简化 HC 集（减门控不去门控），在复利效率和内容可靠性之间取平衡。
