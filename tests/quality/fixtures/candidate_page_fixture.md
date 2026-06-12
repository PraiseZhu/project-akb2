# Candidate Page Contract Fixture
#
# 定义候选页 frontmatter 必填字段 + staging/candidate/ 路径格式。
# 供 step-4 A2 检查（checks_structural.py）作为输入契约引用。
#
# 契约版本: v1
# 对应 pipeline: distill.py compose_candidate

## Frontmatter 必填字段

| 字段 | 类型 | 必填 | 约束 | 来源 |
|------|------|------|------|------|
| `status` | `string` | 是 | 固定值 `candidate` | distill.py 确定性代码写死 |
| `gate_decision` | `null` | 是 | 必须为 `null`（已知坑 1） | distill.py 确定性代码写死 |
| `source_refs` | `list[string]` | 是 | 至少 1 项，含 raw 路径（已知坑 2） | distill.py 确定性代码写死 |
| `gate_reason` | `string` | 是 | ≤150 字符（已知坑 3） | distill.py 截断 |
| `future_questions` | `list[string]` | 是 | 2-3 条 | distill.py 确定性生成 |
| `related_concepts` | `list[string]` | 是 | ≥2 条 | distill.py 确定性生成 |
| `artifact_type` | `string` | 是 | 固定值 `wiki` | distill.py 确定性代码写死 |
| `pipeline_version` | `string` | 是 | 固定值 `v1` | distill.py 确定性代码写死 |

## staging/candidate/ 路径格式

```
staging/candidate/<batch_id>/<slug>.md
```

- `batch_id`: 批次标识，格式 `YYYY-MM-DD-NNN`（如 `2026-06-13-001`）
- `slug`: 从 raw 文件名派生，去掉 `--raw--<date>.md` 后缀

## 正文 10 节模板

1. TLDR
2. 关键发现
3. 入选理由
4. 证据地图（含未覆盖列）
5. 核心论点（每条带 `[raw §X]` 锚点）
6. 论证与解释
7. 适用边界
8. 图谱关系
9. Future Questions
10. 维护记录

## 契约验证

step-4 A2（checks_structural.py）读取候选页后，逐字段对照本契约检查：
- 必填字段缺失 → HC-05 拦截
- gate_decision 非 null → HC-09 拦截
- gate_reason 超 150 字符 → 记录但不断链（lint P1 由后续 gate 处理）
- 正文缺节 → 记录到 repair_actions