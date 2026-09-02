version: v1
date: 2026-09-01
status: active
# C2 已定版。正样本沿用 loop1 Addy Osmani 黄金样张，不另造 X 长文。量规零改。

# loop2 X 车道黄金样张

正 1 负 2。量规零改；本卡只定 X 长文车道的「放进什么 / 拒掉什么」。
runner2 影子跑按本文件做对照，不写 wiki。

## 正样本（质量基准）

### 样张 X1: Addy Osmani — Loop Engineering

- **形态**: 可抽机制/步骤的长文（concept / tutorial），不是短帖水文
- **预期车道**: `concept` 或 `tutorial`（由准入段判，不在预筛里定死）
- **vault raw 路径**: `raw/addy-osmani-loop-engineering--raw--2026-06-11.md`
- **vault active 路径**: `wiki/sources/addy-osmani-loop-engineering.md`
- **vault 候选路径**: `staging/candidate/manual-source-intake-2026-06-11/addy-osmani-loop-engineering.md`
- **来源 URL**: blog post, Addy Osmani 个人站点
- **入库日期**: 2026-06-11
- **必须有**:
  - 可定位的核心论点 + `[raw §X]` 锚点
  - How 可执行（步骤 / 配置 / 失败可见点），不是口号
  - 证据句能在原文定位，禁止无据补全
- **必须过**: 结构六闸、地板 4 项、车道量规过阈、升格五闸（影子跑只模拟，不写 wiki）
- **对照**: 路径与 `skillpack/quality/golden-sample-refs.md` 样张 1 相同；14 步验收见 `docs/main-pipeline-golden-sample.md`

## 负样本（必须被预筛或准入拒收）

### 负样本 X-N1: 短帖 / 无外链水文

- **特征**: 推文正文 <400 词，无外链或外链打不开；或纯表情/转发评论
- **预期拒收**: `intake_x.normalize` 短帖跳过，或 grok 预筛 `keep=false`
- **不得进入**: distill / grader / promote

### 负样本 X-N2: 新闻型 / 洗稿型长文

- **特征**: 产品发布会摘要、融资快讯、对既有 wiki 条目的改写，无可执行 How 或 novelty≈0
- **预期拒收**: 准入 `how_tendency` 或 `novelty`（与 `negative-sample-notes.md` 类型 1/2 同闸，不另开后门）
- **不得进入**: 提炼调用计数必须为 0

## 使用约定

1. `status: active`；正样本路径与 loop1 黄金样张一致
2. 量规文件（`skillpack/quality/rubric/`）本轮零改
3. 样张换代走提案程序，换代后校准语料同步重验
