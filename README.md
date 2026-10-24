<p align="center">
  <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://github.com/PraiseZhu/project-akb2/actions/workflows/ci.yml"><img src="https://github.com/PraiseZhu/project-akb2/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+" /></a>
  <a href="https://github.com/PraiseZhu/project-akb2/actions/workflows/gitleaks.yml"><img src="https://github.com/PraiseZhu/project-akb2/actions/workflows/gitleaks.yml/badge.svg" alt="gitleaks" /></a>
</p>

AKB2 是知识库的工程根，不是知识库本身。

它把抓取、提炼、门控、升格和定时投递收进同一套可验证的管线：质量不过关的内容进不了 vault，失败必须看得见，循环必须有停下来的条件。vault 本体在外接盘上的 Obsidian 库里；本仓只放执行源、契约、测试和评测。

一开始就能跑测试和门控，之后再按需接 launchd、MCP 和 toolbox。开箱即用，不是开箱即定型。

## 本仓做什么

知识库要能长期用，不能只靠一次写得好：

- **它怎么进库**——主链路（循环1 主质量管线 + 循环2 X 采集）已于 2026-10-24 删除，待重建；入库标准文档（宪法、黄金样张、循环设计）保留。
- **它怎么守门（gate / lint）**——候选页不过关不升格；存量页按规则扫 frontmatter、图谱和证据。
- **它怎么定时产出（briefing / video / feishu_sync）**——社媒日报、GitHub 日报、视频报告、飞书投递；未交付即非零退出。
- **它怎么被调用（toolbox / MCP）**——工具权威源在 `toolbox/`，改完再 sync 出去；搜索 MCP 只读。
- **它怎么被验证（tests / evals / CI）**——pytest + ruff 是硬闸；云端再加 gitleaks / actionlint / zizmor / pip-audit。

vault 路径一律经 `config/vault.toml` 解析，禁止硬编码。本仓不得向 vault 写入任何 `.py` / `.sh`。

## 本仓包含什么

| 路径 | 说明 |
| --- | --- |
| `src/akb2/quality/` | 主链路已删，只剩 GitHub 日报用的 `llm_router` 与 harvest 用的 `intake/`（raw 归档、身份契约） |
| `src/akb2/briefing/` | 社媒日报；`github/` 是 GitHub 日报子链路 |
| `src/akb2/video/` | 视频报告：采集 → 抽取 → 推理 → 组装 |
| `src/akb2/feishu_sync/` | vault → 飞书：发现 → 渲染 → 门控 → 通知 |
| `src/akb2/gate/` · `src/akb2/lint/` | fail-closed 门控 CLI；vault 笔记 lint |
| `src/akb2/harvest/` | harvest JSON → raw 组装落盘（受信根 / 锁 / 脱敏） |
| `src/akb2/loops/` | 账本（`ledger`）与路径常量（`paths`） |
| `toolbox/` | 工具权威源（5 个 `cc-*` skill + 搜索 MCP） |
| `launchd/` · `scripts/` | 定时任务模板与安装脚本 |
| `evals/` · `tests/` | 评测夹具与回归测试 |
| `docs/` | 宪法、循环设计、runbook、部署笔记 |

**知识库不在本仓库：** 本体在 `/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/`。`VAULT_ROOT` / `--vault-root` 指向 `/Volumes/AKB2/Obsidian`；`AKB2_ROOT` / `AKB2_VAULT_ROOT` / `vault.akb2_root` 指向上述 AKB2.0 子库。不要把运行链路指回 iCloud 老库。

| 使用方式 | 需要什么 | 可用范围 |
| --- | --- | --- |
| 本地开发 | Python 3.11+、本仓源码 | 测试、lint、dry-run 管线；不写生产 vault |
| 生产定时 | 外接 vault + launchd + 本机凭证 | briefing / video / feishu |
| 只读检索 | 已 sync 的 `cc-akb-search` MCP | 搜知识库，不写库 |

## 前置要求

- **Python** 3.11+（本机常用 3.12.13；CI `tests` 跑 3.11 与 3.13）
- **ruff** / **pytest**（`pip install -e ".[dev]"`）
- **defuddle-cli@0.7.0**（仅 CI 仍安装，当前无代码调用；装法见 [`docs/runbooks/intake-tools-setup.md`](docs/runbooks/intake-tools-setup.md) §6）
- 跑会写 vault 的管线时：**已挂载** `/Volumes/AKB2`

仓库内若残留 `.venv`，不要用——它可能指向已删除的解释器。本机与生产 launchd 都走全局 `python3`（pyenv shims）。

## 开始开发

最短入口：

```bash
git clone git@github.com:PraiseZhu/project-akb2.git
cd project-akb2
python3 -m pip install -e ".[dev]"
npm install -g defuddle-cli@0.7.0
```

未挂载 vault 时必须给 `VAULT_ROOT`，否则路径解析测试会红：

```bash
VAULT_ROOT="$(mktemp -d)" python3 -m pytest -q
ruff check src tests
```

## 开发入口

```bash
# 社媒日报
python3 -m akb2.briefing.pipeline --date YYYY-MM-DD --vault-root /Volumes/AKB2/Obsidian --dry-run-intake

# GitHub 日报
python3 -m akb2.briefing.github.pipeline --mock

# 视频报告
python3 -m akb2.video --dry-run --fixture

# 飞书同步（默认 dm，防误发群）
python3 -m akb2.feishu_sync.cli --vault-root /Volumes/AKB2/Obsidian --dry-run

# harvest 组装落盘
python3 -m akb2.harvest <file.json> --harvest-run-id <uuid4>

# 门控 / lint（对 vault 候选或存量页）
python3 src/akb2/gate/akb2_gate_check.py check --akb-root /Volumes/AKB2/Obsidian/Praise\ Space/AKB2.0 --input staging/candidate --dry-run
python3 src/akb2/lint/akb2_lint.py --vault-root /Volumes/AKB2/Obsidian/Praise\ Space/AKB2.0 --dry-run
```

默认 `pytest` 排除 `integration`（真机、要 NLM 登录态）。`pythonpath` 已由 pytest 注入，不必先 install 也能跑测试。

**关于默认路径：** 客户端约里的 vault 根写在 [`config/vault.toml`](config/vault.toml)。这是有意的——脚本不该记住某台机器的绝对路径。未挂载外接卷时用 `--vault-root` / `VAULT_ROOT` 指到临时目录即可跑测试。

定时任务不要手改已装 plist。用 [`scripts/bootstrap_launchd.sh`](scripts/bootstrap_launchd.sh) 从 `launchd/` 模板渲染安装；新增触发器必须人工确认。对照表见 [`launchd/INSTALL-MANIFEST.yaml`](launchd/INSTALL-MANIFEST.yaml)。

## 架构

- [`docs/constitution.md`](docs/constitution.md) —— 八条原则：有界自动化、收敛优先、fail-closed、预算可见、工程与 vault 解耦、黄金样张、Maker/Checker 异族、实测价值
- [`docs/loops-design-decisions.md`](docs/loops-design-decisions.md) —— 循环设计与停止条件
- [`docs/main-pipeline-golden-sample.md`](docs/main-pipeline-golden-sample.md) —— 主链路 14 步验收（旧主链路已删，作为重建基准保留）
- [`docs/install-topology.md`](docs/install-topology.md) —— toolbox 权威源 → Codex / MCP 分发
- [`docs/ci-budget.md`](docs/ci-budget.md) —— Actions 预算与频率对照
- [`docs/mac-mini-deployment-notes.md`](docs/mac-mini-deployment-notes.md) —— 生产机部署
- [`docs/runbooks/`](docs/runbooks/) —— lint / gate / MCP / intake 工具
- [`CLAUDE.md`](./CLAUDE.md) —— 给 agent 的模块地图与命令
- [`VERSIONING.md`](./VERSIONING.md) —— 提交、分支、不可入库红线

## 贡献

改动通过 pull request 合入 `main`。功能改动先走本仓既定审查（AKB2 为 GPT 单审），再开正式 PR；测试、文档、纯配置且不改运行时行为的小 PR 可在验证通过后直接提。

循环 / 门控 / 入库逻辑必须带测试，断言绑在可观测行为上（收敛、阻断、预算），不是「能跑通」。违反宪法的例外登记在 [`docs/allowed-residuals.md`](docs/allowed-residuals.md)。

## 安全

`.env`、密钥、token 不得进 git。模板只放 `config/*.env.example`。发现凭证泄漏先作废再清历史，不要开一张把密钥写进去的 issue。

云端扫描：`gitleaks` 拦提交里的秘密；`pip-audit` 扫直接依赖；`zizmor` / `actionlint` 审 workflow。私有仓没有 GitHub 原生 CodeQL / 分支保护，这些 job 就是替代闸门。

## 许可证 / License

本仓库目前未附独立许可证文件。知识库正文、模型权重、提示词与第三方组件各按原出处授权，不因本仓存在而被覆盖。
