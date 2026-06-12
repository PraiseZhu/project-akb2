version: v1
date: 2026-06-13
status: active

# 已知坑（黄金样张实测踩到的 5 条）

本文件记录黄金样张实测中踩到的 5 个已知坑，供提炼段、gate 段、评分器引用。
每条坑含: 问题描述 / 触发条件 / 规避方法 / 验证方式。

---

## 坑 1: 候选页预填 gate_decision: active 必被 HC-09 拦

**问题**: 提炼 LLM 输出模板中常带 `gate_decision: active`，导致候选页被 gate 的 HC-09 规则拦截（"没有自动 active"）。

**触发条件**: LLM 生成的 frontmatter 中 `gate_decision` 字段被设为 `active` 或任何非 `null` 值。

**规避方法**:
- `distill.py` compose_candidate 的 frontmatter 由**确定性代码**写死 `gate_decision: null`
- 不依赖 LLM prompt 要求模型输出 null（prompt 不是权限层）
- 测试断言: `test_distill_frontmatter.py` 检查 `gate_decision is None`

**验证**: `python3 -m pytest tests/quality/test_distill_frontmatter.py -q -k "gate_decision"`

---

## 坑 2: source_refs 是硬指标（HC-05），缺则 gate 必拦

**问题**: 候选页 frontmatter 缺少 `source_refs` 字段或为空，gate 的 HC-05-EVIDENCE 规则必拦。

**触发条件**: 提炼 LLM 漏掉 `source_refs` 或填错路径。

**规避方法**:
- `distill.py` compose_candidate 在 frontmatter 中强制写入 `source_refs: [<raw_path>]`
- raw 路径由调用方传入，确保不为空
- 测试断言: `test_distill_frontmatter.py` 检查 `source_refs` 非空且含 raw 路径

**验证**: `python3 -m pytest tests/quality/test_distill_frontmatter.py -q -k "source_refs"`

---

## 坑 3: gate_reason 超 150 字符（lint P1）

**问题**: gate_reason 字段超过 150 字符触发 lint P1 级别告警。

**触发条件**: LLM 生成过长的 gate_reason 文本。

**规避方法**:
- `distill.py` 的 `GATE_REASON_MAX_CHARS = 150` 具名常量
- `_build_frontmatter` 中强制截断: `gate_reason = raw_reason[:150]`
- 测试断言: `test_distill_frontmatter.py` 检查 `len(gate_reason) <= 150`

**验证**: `python3 -m pytest tests/quality/test_distill_frontmatter.py -q -k "gate_reason"`

---

## 坑 4: 维护记录超 5 条（lint P2）

**问题**: 维护记录条目超过 5 条触发 lint P2 告警。

**触发条件**: 回链脚本多次追加维护记录而不合并同日条目。

**规避方法**:
- 初始创建的候选页只写 1 条维护记录（初始创建行）
- 后续步骤追加前先数行数，同日条目合并
- 本坑由 gate/repair 段处理，distill 段只保证初始 ≤1 条

**验证**: `python3 -m pytest tests/quality/test_distill_frontmatter.py -q -k "maintenance"`

---

## 坑 5: 概念页引用不存在节点不能用 wikilink

**问题**: 引用了不存在的概念节点时创建 wikilink 会导致 GRAPH-DEAD-LINK。

**触发条件**: `related_concepts` 中填了 vault 中不存在的概念名，且后续步骤直接创建 wikilink。

**规避方法**:
- 提炼段 `referenced_concepts` 优先引用已有概念（从概念索引注入文件匹配）
- 无匹配的概念保留名但不创建 wikilink
- 落 pending 队列，≥3 篇共指才建节点
- 本坑由 distill 段的概念引用 + gate 段的 GRAPH-LINKING 规则共同处理

**验证**: `python3 -m pytest tests/quality/test_distill_frontmatter.py -q -k "related_concepts"`

---

## 坑的版本管理

- 每条坑有独立编号，新增坑不覆盖已有坑号
- 坑的规避方法在代码中以具名常量或确定性别名体现
- 新坑发现后追加到本文件并更新 version 字段
- 代码中的规避逻辑必须能追溯到本文件中的坑号