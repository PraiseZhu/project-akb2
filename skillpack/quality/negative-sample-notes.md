version: v1
date: 2026-06-13
status: active

# 负样本对照说明

本文件描述主链路内容质量循环的负样本定义与对照规则，供准入判定与评分器引用。

## 负样本定义

负样本是必须被准入段（admission.py）三硬判定拒收的内容。它们由"不放什么"共同定义质量级别。

## 负样本类型

### 类型 1: 洗稿型（Plagiarism / Rewrite）

**定义**: 对既有内容的改写或重组，无实质性增量信息。

**特征**:
- 内容与既有知识库中已有条目高度重叠
- 无新视角、新方法、新代码、新数据
- 可能改换措辞但核心论点完全一致
- 典型来源: 聚合站、AI 摘要站、转载站

**拒收判定**: 准入段新颖度（novelty）判定 → pass=false

**对照规则**:
- 新颖度判定 prompt 要求 LLM 对比既有 wiki 摘要
- 若新内容与既有知识重叠度 > 80% → 拒收
- 若新内容提供新视角但核心论点相同 → 仍拒收（无增量）

### 类型 2: 新闻型（News / Briefing）

**定义**: 纯信息播报，无可执行知识（How 内容）。

**特征**:
- 内容为事件报道、产品发布、融资动态、benchmark 刷分
- 无具体步骤、代码示例、工作流、配置模板
- 无设计决策理由或架构模式分析
- 典型来源: 科技媒体、官方博客的产品发布页

**拒收判定**: 准入段 How 倾向（how_tendency）判定 → pass=false

**对照规则**:
- How 倾向判定 prompt 要求 LLM 识别可执行知识
- 若内容中无可识别步骤/代码/配置 → 拒收
- "介绍了 X 功能"不算 How，"如何用 X 实现 Y"才算

### 类型 3: 不相关型（Off-Domain）

**定义**: 内容主题不在领域 taxonomy 范围内。

**特征**:
- 与第二大脑领域（设计/UI/AI Agent/Loop Engineering 等）无交集
- 可能是通用技术文章但涉及领域外的主题

**拒收判定**: 准入段主题相关性（topic_relevance）判定 → pass=false

## 负样本预期行为

| 负样本类型 | 拒收段 | 提炼调用 | 账本记录 |
|-----------|--------|---------|---------|
| 洗稿型 | admission.novelty | 0 | rejection event |
| 新闻型 | admission.how_tendency | 0 | rejection event |
| 不相关型 | admission.topic_relevance | 0 | rejection event |

## 测试覆盖

- `tests/quality/test_admission_negative.py`: 2 个负样本（洗稿型/新闻型）100% 拒收
- 负样本拒收后断言 ledger 零提炼调用（承载 master SC-2）
- fixture LLM 响应模拟三判定输出，不依赖真 API