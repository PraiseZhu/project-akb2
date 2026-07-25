---
anchor: a9f496d6e05fd6dd8804aec069b76c190c98db8b
generated: 2026-07-06
---
# Project AKB2 全景简报

## 定位
AKB2.0 的**工程根**（v0.3.3，Python ≥3.11）：为 Obsidian vault 知识库（`/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/`）提供 lint / gate / intake / 质量管线 / briefing / video / 飞书同步等自动化链路。工程本身**不含知识库本体**，vault 路径一律经 `config/vault.toml` 解析。核心交互范式：launchd 定时器驱动的无人值守管线 + 手动 CLI，全链 fail-closed。

## 功能地图（现状基线）
**循环1 主质量管线（唯一入库出口）**
- 24h 常驻串行 runner daemon，七段生命线：admission（三硬判定）→ distill（DeepSeek 两段式提炼）→ A 层 6 项结构校验 → B 层 10 维 LLM 评分（GPT-5.4）→ repair 定点修复循环 → promote 五闸升格（`#17` 实现 0%→稳定入库，真机九轮验证）
- 概念型知识可收录，不再只收命令清单（`#1` `#7` 车道判定）
- primary 机制抽取深度：命名组件定义 + 逐字证据 + 反造假 prompt（`#28`，仅在 origin）
- 长文抽取超时分级 210s/360s + 兜底标题组件过滤（`#27`，仅在 origin）
- grader schema 防御：schema-mismatch 有界重试 + jsonschema 诊断 hint（`#12` `#18`）
- llm_router 三族模型路由，按模型族过滤 enable_thinking，400 不可重试立即 break（`#20`）
- fallback/compose 确定性渲染"宁缺勿假"（`#19`）
- 满仓端到端校准 + 出厂判定 factory_acceptance（`#2` `#7`）

**intake 抓取链路**
- launchd 定时抓源落 raw，每 2 小时一轮（`#21` `#24`），`--limit 30` 有界
- 每源每轮限额 cap=5 + 轮转公平，修单源垄断；空轮死因诊断（`#26`）
- 82 个抓取源（`#15` 扩充 AI-Agent/eval 源），preflight + source_validator 校验

**部署与守护**
- mac mini 一键部署脚本（fail-closed preflight + 原子 plist bootstrap + 回滚，`#10` `#11` `#12`）
- vault 卷未挂载 fail-closed，铲除 iCloud 回退陷阱（`#14`）
- 失败可见化 + 限流自动重试 + 缺件不静默（`#13`）；watchdog 脚本兜底

**其他链路**
- briefing 社媒日报（intake→scoring→synthesis→gate→写 vault，三重断言）+ github 子链路，各带 backstop 兜底定时器
- feishu_sync（vault→飞书，幂等）、video 报告链路（manual CLI）、toolbox 晋升门控、vault lint / gate CLI

## 代码地图
| 模块（src/akb2/） | 职责 |
|---|---|
| `quality/` | 主质量管线全部：runner(2201行)/distill(7233行,最大)/repair(2198)/checks_structural(1691)/admission(1305)/promote(1166)/grader(1047)/calibration(1574)/llm_router/smoke_validator |
| `quality/intake/` | 抓取链路：orchestrator/discovery/extract/raw_archiver/source_validator/preflight |
| `briefing/` | 社媒日报主链路 + github 子链路 + audit_fix 收敛循环 |
| `lint/` `gate/` | vault 笔记 lint（akb2_lint 1526行）与 fail-closed 门控 CLI（953行） |
| `loops/` | 循环骨架：ledger 幂等账本 / signatures 故障签名 / paths 隔离区常量 |
| `feishu_sync/` `video/` `promotion*/` `toolbox_scan/` | 飞书同步 / 视频报告 / 工具晋升 / 工具库扫描 |

数据流：vault → 工程 lint/gate → 回写 vault（单向）；质量管线状态在 ledger + staging 隔离区，launchd 驱动。
热区：`distill.py`（7.2K 行，两段式提炼 + prompt 本体）、`runner.py`、`repair.py`、`checks_structural.py`。

## 命令与状态
- 测试 `pytest`（默认排除 integration）；lint `ruff check src tests`；管线 `bash scripts/akb2-main-pipeline.sh [--once|--spot-check]`；校准 `python3 -m akb2.quality.calibration`
- 分支 `main` @ `a9f496d`（2026-07-05）；⚠️ **本地落后 origin/main 4 个提交**（PR #27 #28 已远端合并未拉回，可跑「同步项目 github」）
- launchd 7 个 plist：main-pipeline / intake / briefing×2 / github_briefing×2 / feishu-sync
- CI：ci.yml + gitleaks + shellcheck（test/mypy 已移除 `#9`）；tests/ 46 个文件

## 延伸阅读
- `docs/constitution.md` — 改循环/门控/入库逻辑前必读（8 原则，违反需登记豁免）
- `docs/loops-design-decisions.md` — 设计新循环或改停止条件时读（7 循环 4 Plan）
- `docs/main-pipeline-golden-sample.md` — 判定产出质量是否达标时读（14 步验收基准）
