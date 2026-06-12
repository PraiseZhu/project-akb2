version: v1
date: 2026-06-13
status: active

# 黄金样张引用清单

本文件列出主链路内容质量循环的黄金样张引用，供准入段、提炼段、评分器引用。
全部样张以 vault 文件路径 + 关键特征描述，代码中只读引用，不写回。

## 正样本（质量基准）

### 样张 1: Addy Osmani — Loop Engineering

- **vault raw 路径**: `raw/addy-osmani-loop-engineering--raw--2026-06-11.md`
- **vault active 路径**: `wiki/sources/addy-osmani-loop-engineering.md`
- **vault 候选路径**: `staging/candidate/manual-source-intake-2026-06-11/addy-osmani-loop-engineering.md`
- **来源 URL**: blog post, Addy Osmani 个人站点
- **入库日期**: 2026-06-11
- **关键特征**:
  - 10 个 §A-§J 锚点，全命中
  - 5 条核心论点，全部带 `[raw §X]` 锚点引用
  - How 内容独立成节: "一个完整 loop 的形状(How 模板)"
  - 证据地图 6 行含未覆盖列
  - 区分 source_grounded 与 akb_interpretation 两种 provenance
  - gate 10/10 pass, lint 0 findings
  - 概念回边 3 条: harness-engineering / agent-collaboration / context-engineering
- **14 步验收**: 全部通过，见 `docs/main-pipeline-golden-sample.md`

## 负样本（必须被准入段拒收）

### 负样本 1: Zynd AI X Article（洗稿型）

- **特征**: 对既有内容的改写，无新信息，novelty≈0
- **预期拒收段**: 准入段新颖度判定
- **拒收原因**: 内容重复，零增量

### 负样本 2: 谷歌 I/O 视频简报（新闻型）

- **特征**: 纯新闻摘要，无可执行知识，How≈0
- **预期拒收段**: 准入段 How 倾向判定
- **拒收原因**: 无 How 内容，纯信息播报

## 样张使用约定

1. 所有样张路径为 vault 相对路径，代码中通过 `config/vault.toml` 解析绝对路径
2. 样张文件只读引用，管线不修改样张
3. 样张换代走提案程序（更优内容晋升），换代后校准语料与量规同步重验
4. 新循环设计必须过"样张八要素对账"