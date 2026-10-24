# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Project AKB2

AKB2.0 工程根：lint / gate / intake / briefing / video / feishu_sync 等管线 + CLI + MCP wrapper + eval harness。**不含知识库本体**——知识库在 Obsidian vault（`/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/`），本工程通过 `config/vault.toml` 解析 vault 路径，禁止硬编码。

> **主链路已于 2026-10-24 删除，待重建**：循环1 主质量管线、循环2 X 采集、主链路采集（intake / intake-twitter / inflow-sentinel）的代码、脚本、自启动、测试、评测与运行态都已移除。保留的标准文档：`docs/constitution.md`、`docs/main-pipeline-golden-sample.md`、`docs/loops-design-decisions.md`。日报、GitHub 日报、视频、飞书同步、检索、harvest 不受影响。

路径契约：`VAULT_ROOT` / `--vault-root` 指向 `/Volumes/AKB2/Obsidian`；`AKB2_ROOT` / `AKB2_VAULT_ROOT` / `vault.akb2_root` 指向 `/Volumes/AKB2/Obsidian/Praise Space/AKB2.0`。不要再把 AKB2 运行链路指回 iCloud vault。

## 常用命令

```bash
# ⚠ 环境现状（2026-07-28 核实）：仓库内的 `.venv` **已损坏，不要用**。
#   它的基础解释器 Python 3.13.12 已从 pyenv 移除（`~/.pyenv/versions/` 只剩
#   3.12.13），`.venv/bin/python3` 是指向不存在路径的死符号链接，任何调用报
#   `no such file or directory`。目录本体（~1.6G）保留未删，仅为留证据。
#   **本机实际在用的是全局 `python3`（pyenv shims → 3.12.13）**，pytest /
#   jsonschema / yaml / lxml 等依赖齐备，全量测试可跑通；生产 launchd 三条链路
#   （briefing / github_briefing / video）用的也是 `~/.pyenv/shims/python3`，
#   即 .venv 从未进入生产路径。
#   重建（需要时）：`pyenv install 3.13.12` 后再执行下面两行；或直接用全局 python3。
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # 首次搭建（约 1.6G）
.venv/bin/pip install -r constraints-akb2-2026-07-25.txt     # 还原到已验证版本组合

# 测试（默认排除 integration 标记的真机测试）
# ⚠ 未挂载 /Volumes/AKB2 时必须给 VAULT_ROOT，否则 44 项测试报「无法解析 AKB2_ROOT」
VAULT_ROOT="$(mktemp -d)" python3 -m pytest -q               # 全量（-m 'not integration' 已配置）
python3 -m pytest tests/test_gate.py                         # 单文件
python3 -m pytest tests/test_gate.py::test_name              # 单测试
python3 -m pytest -m integration                             # 真机集成（需 NLM 登录态 + gateway）

# Lint
ruff check src tests            # line-length=100, py311, select E/F/W/I/B/UP

# 管线入口
python3 -m akb2.briefing.pipeline --date YYYY-MM-DD --vault-root <path> [--dry-run-intake]
python3 -m akb2.video --dry-run --fixture       # video 链路（⚠ 实际是 schedule：每天 02:00）
```

- `requires-python >= 3.11`。**本机实测跑绿过的是 3.12.13（全局）**；CI `tests` job 用 `['3.11', '3.13']` matrix（fail-fast: false）兑现下界承诺。3.11 是否在云端全绿以合入后的 matrix job 为准，不得靠收窄 `requires-python` 豁免
- **外部 CLI 依赖 `defuddle`**（CI 仍装它；原先真调它的 extract 测试已随主链路删除）：装法与「包身份陷阱」见 `docs/runbooks/intake-tools-setup.md` §6 —— 钉的是 `defuddle-cli@0.7.0`，**不要**按 `defuddle --version` 打印的 `0.1.0` 去装同名包 `defuddle@0.1.0`（那个包没有 bin）。缺失时本地 skip、CI 靠 `AKB2_REQUIRE_DEFUDDLE=1` 强制 fail
- **CI 历史坑（2026-07-28 查实）**：pytest job 在 2026-06-27 被删除（commit `755d5cf`，理由"消除远端长期红"），直到 2026-07-25 才加回、07-27 首次真跑。期间约一个月的 "CI success" 因单 job + `continue-on-error` 而与实际结论脱钩（抽样到 job 全 failure 但 run 报 success）。`ruff` 的 `continue-on-error` 已于 2026-07-28 摘除转硬闸。**不要把历史绿当作回归基线**
- `pythonpath = ["src", "src/akb2/toolbox_scan"]` 由 pytest 配置注入，无需安装即可跑测试
- 依赖约束在 `pyproject.toml`：下界=实测通过版本、上界=下一 major 边界（防新版自动装入）。CI `tests` job 是这条防线的闸门

## 架构

### 模块布局（`src/akb2/`）

| 模块 | 职责 |
|------|------|
| `briefing/` | 社媒日报主链路：intake → scoring → synthesis → gate → 写 vault。含 `github/` 子链路、audit_fix 收敛循环、translate、embeddings |
| `retrieval/` | 只读检索：promoted 笔记关键词引擎 + CLI（search / serve）+ token 保护的 HTTP（给同事 Cindy 插件直连 Mini，不是网页） |
| `quality/` | 主链路已删（2026-10-24），只剩 `llm_router`（OpenAI 兼容三族路由，GitHub 早报在用）与 `intake/models`、`intake/raw_archiver`（raw 归档与身份契约，harvest 在用） |
| `loops/` | `ledger`（幂等账本追加）/ `paths`（隔离区与 staging 路径常量）；`signatures` 已随主链路删除 |
| `video/` | 视频报告链路：collector → extractor → reasoner → assembler，LLM 经 `llm_adapter`。**触发方式 = `schedule`（每天 02:00）**，由 `~/Library/LaunchAgents/com.akb2.video.plist` 驱动；该 plist **不在仓库 `launchd/` 内、未入 git**（2026-07-28 查实，此前文档误记为"CLI 手动触发"） |
| `gate/` | `akb2_gate_check.py` — fail-closed 质量门控 CLI |
| `lint/` | `akb2_lint.py` + `rules/` — vault 笔记 lint |
| `feishu_sync/` | vault → 飞书同步：discover → render → gate → notify，带 idempotency/state |
| `automation_gate/` | 能力提升为自动化前的门控（带 fixtures 正/负样例） |
| `promotion/`、`promotion_gate/` | toolbox 工具晋升流程与门控 |
| `toolbox_scan/`、`matrix/`、`search_quality/`、`index/`、`closure/`、`backlinks/` | 工具库扫描 / 评测矩阵 / 搜索质量 / 索引 / 收口快照 |
| `mcp_servers/` | MCP wrapper（占位） |

### 关键数据流

```
vault（Obsidian）→ 工程 lint/gate → 回写 vault 内容/toolbox 元数据   （单向，见 constitution 原则 5）
```

briefing pipeline 的三重断言保证：stdout 的 `proposed_gate_decision` == 输出文件 frontmatter == gate CLI 对最终文件的判定（gate 是确定性的）。

### 自动化拓扑

- `launchd/*.plist`：briefing / github_briefing / feishu-sync 定时 + backstop 兜底（`scripts/bootstrap_launchd.sh` 安装）
- 触发器分类必须标注 `manual` / `event` / `schedule`；**新增自动化触发器必须人工确认，agent 不得自行注册**

## 治理约束（docs/constitution.md，违反需在 docs/allowed-residuals.md 登记豁免）

8 条原则的操作要点（constitution v1.1.0）：

1. **有界自动化** — 循环必须有显式停止条件与预算
2. **收敛优先于重跑** — 修复循环（如 audit_fix）以收敛为目标，不整体重跑
3. **Fail-closed 门控** — gate 失败即阻断，不降级放行；主链路全链七段贯穿，前段不过后段不启动
4. **预算显式 / 失败可见** — LLM/外部额度循环的预算用具名常量；部分失败（"队列只清了 60%"）必须明示，不得报成功
5. **工程与 vault 解耦** — 禁止向 vault 写入任何 `.py`/`.sh`；vault 路径只经 `config/vault.toml` 解析
6. **黄金样张质量锚定** — 入库产出必须达样张级（机判：14 步清单 + 量规）；负样本必拒；新循环设计必须过八要素对账
7. **Maker/Checker 异族分离** — 生产与裁决不同模型家族（deepseek 提炼 / gpt-5.6-luna 复核 / gemini-flash 兜底）；任何机制不自评自批；7×24 管线禁用 -preview 模型
8. **实测价值裁决** — 价值由调用数据定（星级 0-3 封顶）；入库用硬指标 pass/fail，无连续分数排序

循环工程详规：`docs/loops-design-decisions.md`（7 循环 4 Plan）+ `docs/main-pipeline-golden-sample.md`（14 步验收基准）

变更纪律：循环/门控/入库逻辑改动**必须带测试**，且断言绑定可观测行为（收敛/门控/预算效果），不是"能跑通"；形状相似但停止条件不同的循环（briefing 质量收敛 vs video 队列清空）不强行抽象合并。

## 测试约定

- `tests/conftest.py` 提供 `repo_root` / `run_script`（HOME 覆盖跑 bash 脚本）/ `sha256_file` / `tree_sha256` fixtures
- bash 脚本测试用 `AKB2_TEST_HOME` 模式隔离
- pytest marker（见 pyproject）：`integration`（真机，默认 deselect）；黄金样张回放用的 `proposal_mode` / `post_amendment` 已随主链路删除
- `evals/` 存放各阶段评测产物（l2-regression、briefing-scoring 等；main-pipeline-quality 已随主链路删除），`lint_evals/` 为 lint 评测

## 其他

- 工作流：`创建 PR` → 开发 → `提交代码` → `提交 PR`（10 维度审查）→ GitHub 手动 merge；版本规范见 `VERSIONING.md`
- `docs/runbooks/`、`docs/mac-mini-deployment-notes.md` 为部署运维参考
- 敏感文件（`.env`、`*.key`、token）绝不入 git；`config/*.env.example` 为模板；`data/` 大文件加 `.nosync` 后缀；`history/` 不入 git
