# Allowed Residuals — Step 5c（Step 6 7-enum 重标签）

> 原始生成日期：2026-05-09（Step 5c）  
> 7-enum 重标签：2026-05-09（Step 6）  
> 来源：`reports/backups/stale-refs-classification.tsv`（Step 5a 扫描结果）  
> 状态：已审批（Step 5b 中 5 个 must_change 全部修复；7-enum 分类由 v4 plan 批准）

---

## 7-enum 分类说明

| enum 值 | 含义 |
|---------|------|
| `fixture_must_keep` | 旧路径**是测试数据本身**，修改会破坏 lint 规则测试 |
| `sha_locked_record` | SHA 锁定的历史封闭文件，改动即触发 automation-gate `blocked_no_rollback_target` |
| `baseline_snapshot` | 评估基准快照，仅在重设基准时才更新 |
| `historical_evidence` | 历史运行记录与输出（run-evidence.md / outputs/*），永不修改 |
| `install_target` | 引用 `~/.codex/skills/cc-*/` install target，Step 6 sync 后仍有效 |
| `allowed_doc_reference` | 文档/断言/案例清单中的叙述性旧路径引用，无执行路径 |
| `external_readonly_dependency` | 外部只读依赖（非 Project AKB2 文件，属 file-map group rows，不在 stale-ref 扫描范围）|

---

## 背景

Step 5a 对 3 类 stale-ref 模式（ai-infra/akb2 路径 / Codex 绝对路径 / .codex/skills/cc- install-target）扫描，发现 55 条命中，分三类：

| 分类 | 数量 | 处理 |
|------|------|------|
| `must_change` | 5 | Step 5b 已全部修复 + commit `06d418a` |
| `must_keep_fixture` | 1 | 本文档记录（→ `fixture_must_keep`） |
| `keep_residual` | 48 | 本文档记录（→ 5 种 enum 细分） |

---

## fixture_must_keep（1 条）

这类文件中的旧路径**是测试数据本身**，不是损坏的引用。修改会破坏对应的 lint 规则测试。

| enum | 文件 | 匹配模式 | 原因 |
|------|------|----------|------|
| `fixture_must_keep` | `lint_evals/p2-lint-v0/fixtures/r04-sha-drift/toolbox/interfaces/fixture-tool.md` | pattern 2（Codex 绝对路径） | `workspace_path` 字段是 r04-SHA-drift 规则的测试输入，旧路径 IS 测试数据；改动会使 lint 测试失去正向覆盖 |

**修复条件**：仅当 r04 规则从"基于 workspace_path" 改为其他机制时才需更新。

---

## sha_locked_record（9 条）

SHA 锁定的历史封闭文件。automation-gate 以封闭 SHA 为基准校验，改动即触发 `blocked_no_rollback_target`，工程生命周期内永不触碰。

| enum | 文件 | 原因 |
|------|------|------|
| `sha_locked_record` | `src/akb2/closure/2026-05-07/closure-manifest.json` | SHA `0fbd53703ff58802299ba8cb04f5290e4b973c124729376dc341f862f06f90cc` 锁定；automation-gate 以此为基准校验 |
| `sha_locked_record` | `src/akb2/closure/2026-05-07/file-sha256.txt` | 封闭快照的文件 SHA 清单，同上 |
| `sha_locked_record` | `src/akb2/closure/2026-05-07/handoff.md` | 封闭交接文档，历史上下文，无执行路径 |
| `sha_locked_record` | `src/akb2/closure/2026-05-07/README.md` | 封闭目录文档（已在 TSV 中，属文档类） |
| `sha_locked_record` | `src/akb2/closure/2026-05-08-p1e-delta/delta-manifest.json` | p1e-delta 清单，历史记录 |
| `sha_locked_record` | `src/akb2/closure/2026-05-08-p1e-delta/effective-manifest.json` | 生效清单快照 |
| `sha_locked_record` | `src/akb2/closure/2026-05-08-p1e-delta/regression/bad-delta-cc-review.json` | 回归 fixture 文件 |
| `sha_locked_record` | `src/akb2/closure/2026-05-08-p1e-delta/regression/discovery-update-fixture-good.json` | 回归 fixture 文件 |
| `sha_locked_record` | `src/akb2/closure/2026-05-08-p1e-delta/regression/run-evidence.md` | 回归历史运行记录 |

---

## baseline_snapshot（5 条）

评估基准快照。改动会破坏 gate 基准对比，仅在重设基准时才同步更新。

| enum | 文件 | 原因 |
|------|------|------|
| `baseline_snapshot` | `src/akb2/automation_gate/baseline-snapshot.md` | automation-gate 基准快照；改动会破坏 gate 基准对比 |
| `baseline_snapshot` | `evals/l2-regression-v1/cc-local-llm/baseline-snapshot.md` | cc-local-llm L2 回归基准；应保持历史状态 |
| `baseline_snapshot` | `evals/l2-regression-v1/cc-skill-stocktake/baseline-snapshot.md` | cc-skill-stocktake L2 回归基准 |
| `baseline_snapshot` | `evals/p1d-cc-skill-stocktake/baseline-snapshot.md` | p1d 评估基准快照 |
| `baseline_snapshot` | `lint_evals/p2-lint-v0/baseline-snapshot.md` | lint-v0 基准快照 |

---

## historical_evidence（20 条）

历史运行记录（`run-evidence.md` / `outputs/*`）。这类文件反映**评估执行时的工程状态**，改动会扭曲历史轨迹。均无执行路径，永不修改。

| enum | 文件 | 原因 |
|------|------|------|
| `historical_evidence` | `evals/c4a/cc-review/run-evidence.md` | C4a 阶段历史运行记录 |
| `historical_evidence` | `evals/c4a/cc-executable-plan/run-evidence.md` | C4a 历史运行记录 |
| `historical_evidence` | `evals/c4b/cc-akb-search/run-evidence.md` | C4b 历史运行记录 |
| `historical_evidence` | `evals/c5b/cc-review/run-evidence.md` | C5b 历史运行记录 |
| `historical_evidence` | `evals/c5b/cc-executable-plan/run-evidence.md` | C5b 历史运行记录 |
| `historical_evidence` | `evals/p1b-l2-regression/cc-review/run-evidence.md` | p1b 历史运行记录 |
| `historical_evidence` | `evals/p1b-l2-regression/cc-executable-plan/run-evidence.md` | p1b 历史运行记录 |
| `historical_evidence` | `evals/p1c-cc-local-llm/run-evidence.md` | p1c 历史运行记录 |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/run-evidence.md` | p1d 历史运行记录 |
| `historical_evidence` | `evals/p1e-cc-akb-search-a3/run-evidence.md` | p1e A3 历史运行记录 |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/preflight.json` | p1d 历史 preflight 输出 |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/01-scan/raw-output.json` | p1d 扫描历史输出 |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/01-scan/stderr.txt` | p1d 扫描历史 stderr |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/02-quickdiff-nochange/stderr.txt` | p1d quickdiff 历史输出 |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/03-quickdiff-changed/raw-output.json` | p1d quickdiff 历史输出 |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/03-quickdiff-changed/stderr.txt` | p1d quickdiff 历史 stderr |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/04-save-results/results.json` | p1d 保存结果历史输出 |
| `historical_evidence` | `evals/p1d-cc-skill-stocktake/outputs/06-isolation-failure/6b1-err.txt` | p1d 隔离失败历史错误 |
| `historical_evidence` | `src/akb2/automation_gate/outputs/current/gate-result.json` | automation-gate 最近一次运行输出（运行时产物） |
| `historical_evidence` | `lint_evals/p2-lint-v0/outputs/lint-current-5-tools.json` | lint-v0 最近一次运行输出 |

---

## install_target（7 条）

这些文件引用 `~/.codex/skills/cc-*/` 路径。该路径是 **install target**，不是旧工程位置——Step 6 sync 脚本将持续维护此镜像。引用有效，不需修改。

| enum | 文件 | 原因 |
|------|------|------|
| `install_target` | `evals/c4a/cc-review/fixture-positive.md` | fixture 中引用 `~/.codex/skills/cc-review/SKILL.md`；Step 6 后该路径仍存在 |
| `install_target` | `evals/c4a/cc-review/fixture-negative.md` | 同上 |
| `install_target` | `evals/c4a/cc-executable-plan/fixture.md` | 引用 install-target；有效 |
| `install_target` | `evals/c4b/cc-akb-search/preflight.md` | 引用 install-target；有效 |
| `install_target` | `evals/p1e-cc-akb-search-a2/a2-preflight.md` | 历史 preflight 文档，引用 install-target |
| `install_target` | `evals/l2-regression-v1/cc-local-llm/run-l2.sh` | L50 行检查 install-target SHA；Step 6 后仍有效 |
| `install_target` | `evals/p1d-cc-skill-stocktake/run-smoke.sh` | L182/408/428 是测试断言，检查 scan 输出是否包含 install-target 路径（行为正确） |

---

## allowed_doc_reference（8 条）

文档/断言/案例清单中的叙述性旧路径引用，无执行路径。对应模块重构时才需更新。

| enum | 文件 | 原因 |
|------|------|------|
| `allowed_doc_reference` | `src/akb2/gate/README.md` | 文档，历史路径作为上下文说明 |
| `allowed_doc_reference` | `src/akb2/matrix/README.md` | 文档 |
| `allowed_doc_reference` | `src/akb2/promotion_gate/v0/README.md` | 文档 |
| `allowed_doc_reference` | `src/akb2/closure/README.md` | 文档（TSV 中作为 gate/README 记录，此处归一） |
| `allowed_doc_reference` | `evals/p1d-cc-skill-stocktake/assertions.md` | 测试断言描述文档 |
| `allowed_doc_reference` | `evals/p1b-l2-regression/cc-review/case-register.md` | 历史测试用例清单 |
| `allowed_doc_reference` | `evals/p1b-l2-regression/cc-executable-plan/case-register.md` | 历史测试用例清单 |
| `allowed_doc_reference` | `evals/p1b-l2-regression/cc-executable-plan/outputs/05-edge-review-handoff-plan.md` | 历史输出文档 |

---

## external_readonly_dependency（5 条）

这些文件属于 **Project AKB（旧）**，由 AKB2 工具在运行时读取，永不迁移入 Project AKB2。记录于 `file-map-2026-05-09.tsv` group rows，不在 stale-ref 扫描范围内，此处列入白名单供工程审计。

| enum | 文件 | 消费者 | 原因 |
|------|------|--------|------|
| `external_readonly_dependency` | `Claude Code/Project AKB/index/catalog.db` | `mcp_server/server.py` (CatalogDB readonly=True) + `run-search-quality.sh` | SQLite AKB 知识库；运行时只读；永不迁移入 Project AKB2 |
| `external_readonly_dependency` | `Claude Code/Project AKB/scripts/core/search.py` | `mcp_server/server.py` import `scripts.core.search` via PYTHONPATH | AKB 搜索后端模块；MCP wrapper 的核心依赖 |
| `external_readonly_dependency` | `Claude Code/Project AKB/scripts/utils/db.py` | `mcp_server/server.py` import `scripts.utils.db` (CatalogDB) | AKB DB 工具类；与 catalog.db 配合使用 |
| `external_readonly_dependency` | `Claude Code/Project AKB/scripts/main.py` | `run-search-quality.sh --baseline`（双路径对照评估） | AKB CLI 入口；search-quality eval 使用，非 MCP 路径 |
| `external_readonly_dependency` | `~/.claude/projects/-Users-praise-.../` (Claude transcript dir) | `run-search-quality.sh --collect-queries` SQ-1 | Claude 会话 transcript；SQ-1 扫描 `*.jsonl` 收集 akb_search 调用记录 |

**清理触发条件**：本轮 Step 8 不清理旧 Project AKB。上述 1-4 条作为 external_readonly_dependency 保留，仅在未来单独启动旧 AKB backend 替换/退役计划时才迁移或重指向新位置。transcript dir 为系统目录，永久有效。

---

## 汇总

| enum | 数量 | 修复触发条件 |
|------|------|------------|
| `sha_locked_record` | 9 | 不触发（SHA 不变原则，工程生命周期内永不触碰） |
| `baseline_snapshot` | 5 | 基准重设时同步更新 |
| `historical_evidence` | 20 | 永不修改（历史证据） |
| `install_target` | 7 | Step 6 失效或 install-target 路径变更时 |
| `allowed_doc_reference` | 8 | 对应模块重构时 |
| `fixture_must_keep` | 1 | r04 规则机制变更时 |
| `external_readonly_dependency` | 5 | 本轮不触发；未来旧 AKB backend 替换/退役计划时处理 |
| **合计（stale-ref 范围内）** | **50** | |
| **合计（含 external）** | **55** | |

> **注**：原 Step 5c 文档汇总合计为 49，`allowed_doc_reference`（原 E 类）实际有 8 条，此处修正为 50。`external_readonly_dependency` 5 条不在 stale-ref 扫描范围，额外列入工程白名单。

---

## Step 6 后的 stale-ref 状态预测

Step 6 实现 sync 脚本后，`~/.codex/skills/cc-*/` install-target 将持续由 Project AKB2 的 `toolbox/cc-*/` 维护。`install_target` 7 条引用永久有效。

Step 8 清理 Codex 原位文件后，`baseline_snapshot` / `historical_evidence` / `allowed_doc_reference` 类历史文件中的旧路径仍是"历史记录"，不需追溯修改。

`sha_locked_record`（9 条）在工程生命周期内永不触碰。

本轮 Step 8 不清理旧 Project AKB。`external_readonly_dependency` 前 4 条（catalog.db + search.py + db.py + main.py）作为外部只读依赖保留，仅在未来单独启动旧 AKB backend 替换/退役计划时才迁移或重指向新实现。

---

## rubric_version_bump（content-lane-quality step-2，2026-06-26）

> 来源：`plans/gd/2026-06-26-content-lane-quality/step-2-rubric-externalize.md`（Step.1 md 加「机器可读源为 rubric.yaml」注释 + Step.5 量规版本 1.0.0→1.1.0）。

step-2 量规外置正当升级 `skillpack/quality/rubric/10-dimensions.md`（版本 1.0.0→1.1.0 + 顶部加机器可读源注释），导致历史 hash 守护测试 `tests/quality/test_provenance_split.py::TestRubricHashUnchanged`（`_KNOWN_RUBRIC_HASH`，原属 reliability refactor SC-5.2「本 step 禁改 rubric」守护）的锁定值过时而 fail。

| 项 | 值 |
|----|----|
| 失效测试 | `test_provenance_split.py::TestRubricHashUnchanged::{test_rubric_hash_matches_known_value, test_rubric_hash_stable_across_runs}` |
| 根因 | rubric 合法版本升级（spec FR-010 标准外置 + SC-6 version_match 要求 md/spec 同步到 1.1.0） |
| 旧 `_KNOWN_RUBRIC_HASH` | `34a...`（1.0.0 版 md） |
| 新 md sha256 | `5f2791b9e4f3256e3c43355d9216998a53c335e9bce6fe4d03eee2891f9544c8` |
| 处理 | `test_provenance_split.py` 不在本 step-2 packet owned_paths 内（属 step-3/主 agent 范围），故本 packet **不改该测试**；`_KNOWN_RUBRIC_HASH` 应在有权改该文件时更新为上方新值 |
| 修复触发条件 | rubric 版本升级时同步更新 `_KNOWN_RUBRIC_HASH`（这是 rubric 演进的必要配套，非真回归——hash 守护本就该随 rubric 合法升级刷新基准） |

> 说明：这是**预期的、由计划明确要求的 rubric 升级**引起的 hash 漂移，不是判分行为回归——grader 的 10 维 pass/fail 与缺陷集对同稿件不变（step-2 SC-5 验证）。仅 md 文件字节 hash 因加注释 + 版本号变化。

---

## launchd_residuals_2026_08_03（lane-5：promote parity / launchd drift）

### 来源不明的带外改写：main-pipeline autopromote 运行态

| 项 | 值 |
|----|----|
| 发现日期 | `2026-08-03` |
| 影响文件 | `~/Library/LaunchAgents/com.akb2.main-pipeline.plist` |
| 运行态指纹 | `sha256=e35e04f704b22adfba9dc6fe1402b03a012597cbfb93071b1141d62c1492d71d`, `size=1433`, `mtime=2026-07-04 21:56:22` |
| 证据备份 1 | `~/.com.akb2.main-pipeline.autopromote-bak.20260704-215514.plist`, `sha256=a9f14a9f1927a1203620da6dd6d9721db25c3ad237a20ad358d5a9917632a4df`, `size=4206`, `mtime=2026-07-04 21:55:14` |
| 证据备份 2 | `~/.com.akb2.main-pipeline.autopromote-bak.20260704-215622.plist`, `sha256=a9f14a9f1927a1203620da6dd6d9721db25c3ad237a20ad358d5a9917632a4df`, `size=4206`, `mtime=2026-07-04 21:56:22` |
| 结论 | `2026-07-04 21:55–21:56` 发生过一次来源不明的带外程序化改写；运行态 `AKB2_AUTO_PROMOTE=1` 与仓库模板 `=0` 漂移被 lane-5 parity 检测正式登记 |
| 处理边界 | **不删除**两份 `.autopromote-bak` 备份；它们是审计证据 |

### GitHub briefing 原 SC-8 已废弃

| 项 | 值 |
|----|----|
| 原契约 | `plans/gd/2026-05-27-akb2-github-curator-mvp/master-plan.md` 的 `SC-8` 要求 `launchd/com.akb2.github_briefing.plist` 不含 `EnvironmentVariables` |
| 现状 | 生产运行态依赖 plist 级 `PYTHONPATH` 与 `PATH`（含 `~/.local/bin`），wrapper 注入不足以覆盖 |
| owner 方向 | `2026-08-03` lane-5 按 owner 既定方向将现状回灌入仓库模板，并保留 parity 检测对其余漂移的告警能力 |
| 残留说明 | 旧 `SC-8` 不再作为现行验收线；后续若恢复 wrapper-only 注入，需要单独计划替换并重写验收 |

### F58 已交付但未部署：inflow-sentinel pending human install

| 项 | 值 |
|----|----|
| 交付提交 | `fc1d9f9817c842ee1fb550e0ef0747aa725c25be` |
| 提交日期 | `2026-07-30` |
| 当前状态 | `launchd/com.akb2.inflow-sentinel.plist` 已入仓，但 `~/Library/LaunchAgents/com.akb2.inflow-sentinel.plist` 尚不存在，`launchctl print gui/$(id -u)/com.akb2.inflow-sentinel` 不在册 |
| 风险 | 入库断流告警能力仍为 0；pending 状态已由 `launchd/INSTALL-MANIFEST.yaml` 和 `scripts/verify-launchd-parity.sh` 显式报出，不再静默 |
| 后续动作 | 保持人工确认注册的宪法边界；待 owner/人工批准后再安装，不由 agent 私自 bootstrap |

### loop2 两触发器已在 Mini 通电：4 个 List 已开、S1 日历未完成（2026-09-02）

| 项 | 值 |
|----|----|
| 影响 label | `com.akb2.loop2-intake`、`com.akb2.loop2-runner` |
| 当前状态 | owner 2026-09-01 批准 Mini 通电；两 job 已加载。`x_sources.yaml` 现有 4 个启用 List：`2095025924823785762`（AKB2，19）、`2095044167345037528`（AI-research，28）、`2095044647383171157`（AI-builders，23）、`2095045077727105347`（design-eng，7；后续加人被 X 403/104 拦住）。四个 `twitter list --json` 均有帖。影子升格 `AKB2_LOOP2_SHADOW_PROMOTE=1`，不写 wiki |
| 风险 | 计划下限 4 个 List 已齐，但 design-eng 只有 7 人；S1 三天观察日历今天起不完。loop1 并行保留，未归档 |
| 后续动作 | 限流过后再补 design-eng 成员。S1/S2/S3 按日历走 |

---

### backstop 第二时段 11:00：模板已改，运行态待 owner 重装（2026-08-04 登记）

| 项 | 值 |
|---|---|
| 影响 label | `com.akb2.briefing.backstop`、`com.akb2.github_briefing.backstop` |
| 仓库模板 | 第二时段已由 `12:05` 改为 `11:00`（owner 2026-08-03 确认） |
| manifest | `launchd/INSTALL-MANIFEST.yaml` 的 `expected_schedule` 已同步为 `11:00`（2026-08-04） |
| 运行态 | `~/Library/LaunchAgents/*.backstop.plist` 仍为 `12:05` |
| 为什么留 | 两个 label 的 `installer` 是 `manual_existing_runtime` —— 本仓**没有任何脚本**会重装它们；宪法边界要求人工确认注册，agent 不得私自 bootstrap |
| 可见性 | `scripts/verify-launchd-parity.sh` 会报 `schedule_drift=installed_stale` 并同时打出 repo/manifest/installed 三个值 + 方向词，归因指向**运行态**而不是仓库（这正是 2026-08-04 修掉的反向指责缺陷） |
| 风险 | 「UTC 预算日重置后唯一有额度的补跑窗口」在生产上仍不存在（现有第二时段 12:05 晚于 12:00 deadline，只能巡检不能补跑）；这是事故核心修复**尚未生效**的部分 |
| 后续动作 | owner 手工重装这两个 label（或在 manifest 里给它们提供真实 installer），重装后 parity 自动回到 MATCH，本条残留即可删除 |

（运行态已于 2026-08-05 生效，本条目待下一 PR 删除）

### `.stignore` 入仓但按其自身生效前提不生效（2026-08-04 登记）

| 项 | 值 |
|---|---|
| 影响文件 | 仓库根 `.stignore` |
| 生效前提 | Syncthing 只读**folder 根**的 `.stignore` |
| 实测 folder 根 | `~/AI-Agent/.stfolder` 存在；`~/AI-Agent/Claude/.stfolder` 与本仓根均不存在 → folder 根是 `~/AI-Agent`，真正起作用的 `.stignore` 在那里 |
| 结论 | 入仓这份位于**非** folder 根，Syncthing 不读它 —— 按其自身判据不生效 |
| 为什么仍入仓 | 它是模式清单的权威来源与评审对象（含 `briefing-failed.json` / `briefing-skipped.json` 这类告警唯一归因素材）；owner 合入 `~/AI-Agent/.stignore` 后即生效 |
| 后续动作 | owner 把本文件的模式合入 `~/AI-Agent/.stignore`（该文件不在本仓管理范围内，agent 不写工作区外文件）；合入后本条残留即可删除 |

（folder 根 `~/AI-Agent/.stignore` 已于 2026-08-05 经 owner 授权合入 AKB2 模式块并隔离既有
冲突副本 —— 生效前提已满足，不再是活残余；本条目待下一 PR 删除）

## llm_resilience_lane1（2026-08-03，LLM 调用韧性修复批次 1）

> 来源：`_tmp/fix-plan/lane-1-llm-resilience.md`（2026-08-02 GitHub 简报未交付的分赛道排查结论）。
> 本节 2 条残留均因**改动边界**（并行 7 个 worker，各持独占文件清单）而非技术困难未在本轮消掉，
> 逐条给出修复触发条件与需要的文件权限。
> （原为 3 条；`github_synthesis_model_not_in_frontmatter` 已于 2026-08-03 修复，
> 降级为本节末尾的"另注（非残留，已修）"，条数随之更正 —— 本节条数与 `###` 级条目数
> 的一致性由 `tests/test_render_residuals_summary.py::test_section_count_matches_rendered_entries`
> 机器核对，不再手抄。）

### synthesis_no_key_fallback

| 项 | 值 |
|---|---|
| 位置 | `src/akb2/briefing/synthesis.py::synthesize`（无 `AI_GATEWAY_API_KEY` 分支） |
| 为什么留 | 本轮按计划只摘「**API 失败** → `_fallback_synthesis`」这条自动路径；**无 key** 时仍自动产出降级假简报（`source.name=synthesis_fallback`，summary 明文含 "fallback summary generated when the synthesis model is unavailable"）。改它会让 `tests/briefing/test_synthesis.py::test_happy_path_fallback` 与 `::test_synthesize_output_file`（CLI e2e，无 key 环境跑）双双失败，而该测试文件不在本轮独占清单内 |
| 修复条件 | 有权改 `tests/briefing/test_synthesis.py` 时，照 github 侧 `allow_mock` 形态把它改成显式 opt-in（`synthesize(..., allow_fallback=True)`），生产链路缺 key 直接硬失败。**需 owner 复核**：生产 env 已配 key，该路径在生产不可达，但它是 P3 fail-closed 的一个缺口 |

### http_retry_duplication_in_send_py

| 项 | 值 |
|---|---|
| 位置 | `src/akb2/feishu_sync/feishu/send.py:67-90` 与 `src/akb2/common/http_retry.py` |
| 为什么留 | 退避范式已提取到共享模块并被 github synthesis 采用，但 `send.py` 的三个私有函数（`_parse_retry_after` / `_backoff_delay` / `_is_retryable_http`）仍是本地副本 —— `send.py` 的调用点不在本轮独占清单内，按计划「新模块提供实现，`send.py` 暂不改」 |
| 修复条件 | 有权改 `send.py` 时替换为 `from akb2.common.http_retry import ...` 并删本地实现（其 `_BASE_BACKOFF` / `_MAX_BACKOFF` env 覆盖以 `base` / `cap` 参数传入，行为不变）。当前重复是**已知且有意**的，不是漂移 |

> 另注（**非**残留，已修）：`briefing/synthesis.py` 的 `-preview` 默认模型此前属**无豁免登记的违宪**
> （宪法 P7 / `docs/constitution.md:95`）。本轮已把默认值改为正式版 `deepseek/deepseek-v4-pro`
> 并加调用期硬校验，故**不需要**豁免条目 —— 此处仅记录该违规曾存在且已消除，防止日后 grep
> `preview` 又回到零命中而误判「从未发生」。

> 另注（**非**残留，已修）：`github_synthesis_model_not_in_frontmatter` 曾登记于此
> —— 族降级后实际使用的模型只在 `synthesize()` 返回值与 stdout 的 `SYNTHESIS_FALLBACK` 行可见，
> 没有接进 vault 产出，读简报的人与事后审计的人看不出这份是谁产的（违反宪法 P4「失败可见」的原意：
> 降级必须留在**产物**里，而 stdout 在 launchd 下只落日志文件、无人常看）。
> 2026-08-03 已接线完成：`schemas/github_briefing_v1.json` 加 `synthesis_model`（optional，
> `minLength:1`，不进 required 以免既有产物重校验被破）；`pipeline.py::_assemble_briefing`
> 非空才写入（避免未传参的调用方产出 schema 不合法的 briefing）；`_render_markdown` 输出
> `synthesis_model:` frontmatter 行。回归锁见
> `tests/briefing/github/test_pipeline_exit_codes.py::TestSynthesisModelRecordedInArtifact`
> （两条均经变异验证：注入"永不输出该行"与"空值也写入"各自精确打红对应断言）。
> 此处仅记录该缺口曾存在且已消除，防止日后误判「从未发生」。

---

## r2_allowed_residuals_2026_08_04（backstop / LLM / 配置）

### A7-b — main/backstop 不共锁

`backstop` 与 daily 主跑不共享同一把互斥锁。主跑（`daily_briefing.sh` /
`daily_github_briefing.sh`）不持锁，backstop 侧仅有 `pgrep -f` 软守卫（其“全机器模糊匹配 +
与 pipeline 启动之间无原子性”两项局限已在 `scripts/briefing_backstop.sh` 如实记录）。因此主跑
耗时超过 07:30→08:00 的 30 分钟间隔时，存在 main×backstop 并发双跑、双花额度的窗口。

本 PR 不修的理由：让主跑持硬锁会把“双花额度”风险替换成“主跑因锁残留/陈旧判定误判而完全
不出简报”的风险，后者正是本 PR 所修事故的形态。真正的修法需要主跑侧的锁生命周期设计与独立
测试矩阵，另 PR 处理。

### A11 — 同步锁根只告警不拒跑

锁目录落在跨机同步范围内时（`akb2_state_root_is_synced` 命中），backstop 只打印
`lock_sync_risk=1` 并继续，不 fail-closed。生产默认配置下该分支不可达（实测
`STATE_ROOT=~/Library/Application Support/akb2`，`is_synced=NO`），仅在显式覆盖
`AKB2_STATE_ROOT` / `AKB2_BACKSTOP_LOCK_FILE` 指向同步目录时触发。“回落到机器本地目录”已经是
默认行为；不改为拒跑的理由，是为一个非默认 env 覆盖关闭整个补跑安全网，代价高于收益。跨机
互斥超出 PID 文件的能力边界（异机 PID 对 `kill -0` 无意义），需要共享租约机制，另议。

### F14 — llm_router 的 SDK 层仍自动重试 429

`llm_router` 在 provider 返回 429 后仍允许 SDK 层自动重试；已知被共享预算闸阻断的调用会在
`record_budget_event` 生效前产生额外请求并额外消耗额度。接受理由：共享写闸在首击后会阻断后续
consumer，且单个 consumer 调用内的 SDK 重试增量有界。本轮保留现状；若要把 fail-fast 扩展到
SDK 内部尝试，需要另行调整 `llm_router` 的重试所有权与相应回归测试。

### s5 — missing_policy 静默回落

`config.py` 的 `missing_policy` 在取值非法或缺失时静默回落到默认值，未按宪法 P4（失败可见）
明示。`config.py` 不在本 PR 改动集内，本轮不修。影响：配置写错时不报错，按默认行为运行且无告警。

补充：非法值会静默落到两个取值里**较宽松的那个**（`partial_ok_recoverable`，见
`src/akb2/feishu_sync/config.py:21,61-65`），因此运维想收紧成 `fail_all` 而拼错时，系统按更宽松
模式运行且无任何告警。改动 `config.py` 的下一个 PR 应一并修掉，届时删除本条。

---

## pip_audit_2026_08_15（云端 CI 依赖扫描首跑）

| 项 | 值 |
|---|---|
| 扫描命令 | 与 CI 相同：从 `pyproject.toml` 导出直接依赖 + `pip-audit --no-deps -r direct-deps.txt` |
| 首跑（环境树） | `pip-audit .` 扫已解析的 136 个包报 0 洞，但云端 action 默认解析传递依赖后爆出 cryptography 等一串 CVE，口径不可用 |
| 收窄后实测 | 直接依赖清单命中 **1** 项：`pytest 8.4.2 / PYSEC-2026-1845`，修复版 9.0.3 |
| workflow 豁免 | `ignore-vulns: PYSEC-2026-1845`。理由与 `pyproject.toml` 注释一致：修这个洞必须跨 major，而本仓封顶 `<9`；漏洞是 `/tmp/pytest-of-{user}` 本地 DoS，需同机其他用户配合 |
| 复审条件 | 下次扫描若出现清单外 CVE：改依赖或升级，不得把新 ID 塞进同一豁免行了事。pytest 升到 9.x 并在本仓跑绿后删除本豁免 |

