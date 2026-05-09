# AKB2 Engineering Root Migration Closure — 2026-05-09

> **双轨证明声明（v3 改进 — Residual #2）**：
> Project AKB2 的 git 历史只追踪本仓库内文件。
> 外部路径（install target / mirror / 旧原位副本）的删除/写入由
> `cleanup-manifest.tsv` + `pre-delete-sha256.tsv` + `before/after scan` + `cleanup-execution.log`
> 共同证明，不进入 git diff。

---

## 1. Step 1–8 完成摘要

| Step | 内容 | 代表提交 | Codex Watch verdict |
|------|------|----------|---------------------|
| Step 0 | 初始化 Project AKB2 scaffold | `417fc04` | APPROVED |
| Step 1–2 | 工具库 + 源码全量导入（lint / gate / closure / matrix 等） | `1672a68`–`28533bc` | APPROVED |
| Step 3 | 文档合约 + config 权威化（allowed-residuals / file-map schema） | `68e38cd` | APPROVED |
| Step 4 | 实现 5 个 sync/drift 脚本 | `4999f45` | APPROVED |
| Step 5 | 60 tests 全替换占位 skip，激活真实 assertions | `1ab2fe2` | APPROVED |
| Step 6+7 | 三路 sync（codex / claude-backup / claude-active）17/17 MATCH | `3d20a67` | APPROVED |
| Step 8 | 旧原位副本清理（本次），24 项 delete，0 RESIDUAL | *本次 commit* | *等用户 `/review code`* |

---

## 2. Project AKB2 当前权威目录

```
Project AKB2/
├── toolbox/cc-{executable-plan,review,local-llm,skill-stocktake,akb-search}/  ← 5 工具权威源
├── src/akb2/{lint,gate,toolbox_scan,automation_gate,closure,matrix,
│              promotion_gate,search_quality}/                                   ← 工程实现
├── tests/                ← 60 tests，全部实断言
├── scripts/              ← 5 sync/drift 脚本（sync-to-codex/claude-backup/mcp + verify + pipeline）
├── evals/                ← eval 套件
├── lint_evals/           ← lint eval 套件
├── reports/migration/    ← 迁移证据（file-map / allowed-residuals / final report）
└── reports/backups/      ← step 备份（pre-step8-cleanup-*/）
```

Project AKB2 是 AKB2 工程文件和工具库的**唯一权威维护目录**，取代迁移前的旧 Codex 容器。

---

## 3. Obsidian 展示层边界

Obsidian AKB2.0 vault 仅作为知识库展示层，不承载工程实现。

- **`.akb-runtime/lint/`** 旧 runtime 已删除（本次 cleanup）
- lint 权威实现：`src/akb2/lint/akb2_lint.py`（已通过 `tests/test_lint.py` 静态验证）
- vault 正文目录（`wiki/ docs/ toolbox/ raw/ manifests/`）**未触碰**

---

## 4. Install target / Mirror 说明

| 类型 | 路径 | 维护方式 |
|------|------|---------|
| Codex install | `~/.codex/skills/cc-{5 工具}/` | `scripts/sync-skills-to-codex.sh` |
| Claude backup mirror | `Claude Code/skills/cc-{5 工具}/` | `scripts/sync-skills-to-claude-backup.sh` |
| Claude active mirror | `Claude Code/skills/claude-active/cc-{5 工具}/` | `scripts/sync-skills-to-claude-backup.sh` |
| MCP wrapper target | `Codex/plugins/.../akb-search-readonly/server.py` | `scripts/sync-mcp-server.sh` |

所有 install target 在本次 cleanup 后验证 **100% 保留**（after-scan 0 MISSING）。

---

## 5. External Readonly Dependency 白名单

参见 `docs/allowed-residuals.md` 7-enum 的 `external_readonly_dependency` 5 条：

1. `Codex/ai-infra/akb2/` 残余 — `Project AKB/index/catalog.db`（AKB 1.0 backend）
2. `Project AKB/scripts/core/search.py`（旧 AKB search backend）
3. `Project AKB/scripts/utils/db.py`
4. `Project AKB/scripts/main.py`
5. `~/.claude/projects/...`（Claude transcript dir，外部 readonly）

以上均未删除，after-scan 确认全部保留。

---

## 6. Cleanup Manifest 统计

| classification | count | action |
|----------------|-------|--------|
| `delete_strong_evidence` | **24** | delete |
| `keep_install_target` | 2 | keep |
| `keep_external_readonly_dependency` | 0 | keep |
| `keep_historical_evidence` | 0 | keep |
| `keep_defer_no_strong_evidence` | **21** | defer |
| `keep_forbidden_live_runtime` | 0 | keep |
| **合计** | **47** | |

Evidence type 分布（delete 项）：

| evidence_type | count |
|---------------|-------|
| `file_map_ok` | 23 |
| `file_map_grouped` | 1 |
| `sha_identical` | 0 |

---

## 7. 删除清单（24 项，全部 post_state=deleted）

| path | evidence_type | post_state |
|------|---------------|------------|
| `Claude Code/ai-infra/evals/skill-routing/akb-search.json` | file_map_ok | deleted |
| `Claude Code/plans/akb-autoresearch-v1-sorted-nova.md` | file_map_ok | deleted |
| `Claude Code/plans/akb-graph-context-snazzy-horizon.md` | file_map_ok | deleted |
| `Claude Code/plans/akb-llama-swap-migration-v2.md` | file_map_ok | deleted |
| `Claude Code/plans/akb2-followup-v2-1-condensed.md` | file_map_ok | deleted |
| `Claude Code/scripts/akb-pre-commit.sh` | file_map_ok | deleted |
| `Codex/ai-infra/akb2/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2-toolbox-automation-gate/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2-toolbox-closure/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2-toolbox-evals/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2-toolbox-evals/c4b/cc-akb-search` | file_map_ok | deleted (with parent) |
| `Codex/ai-infra/akb2-toolbox-evals/p1e-cc-akb-search-a2` | file_map_ok | deleted (with parent) |
| `Codex/ai-infra/akb2-toolbox-evals/p1e-cc-akb-search-a3` | file_map_grouped | deleted (with parent) |
| `Codex/ai-infra/akb2-toolbox-evals/p1e-cc-akb-search-a4` | file_map_ok | deleted (with parent) |
| `Codex/ai-infra/akb2-toolbox-evals/p1e-cc-akb-search-c5` | file_map_ok | deleted (with parent) |
| `Codex/ai-infra/akb2-toolbox-evals/p1e-cc-akb-search-review2` | file_map_ok | deleted (with parent) |
| `Codex/ai-infra/akb2-toolbox-evals/p1e-cc-akb-search-unblock` | file_map_ok | deleted (with parent) |
| `Codex/ai-infra/akb2-toolbox-lint/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2-toolbox-matrix/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2-toolbox-promotion-gate/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2-toolbox-search-quality/` | file_map_ok | deleted |
| `Codex/ai-infra/akb2/toolbox_scan/akb2_toolbox_scan` | file_map_ok | deleted (with parent) |
| `Obsidian/AKB2.0/.akb-runtime/` | file_map_ok | deleted |
| `Obsidian/AKB2.0/.akb-runtime/lint/akb2-lint.py` | file_map_ok | deleted (with parent) |

*路径均为 iCloud 根下相对路径，完整绝对路径见 `cleanup-manifest.tsv`。*

---

## 8. 保留与 Defer 清单

| path | classification | 原因 |
|------|----------------|------|
| `Claude Code/skills/cc-akb-search` | keep_install_target | Claude backup mirror |
| `Claude Code/skills/claude-active/cc-akb-search` | keep_install_target | Claude active mirror |
| `Claude Code/ai-infra/akb2-gate-checker/` | keep_defer_no_strong_evidence | 10 个文件未命中 file-map |
| `Codex/ai-infra/akb2/gate_check/akb2_gate_check` | keep_defer_no_strong_evidence | 6 个文件未命中 file-map |
| `Claude Code/skills/Project_AKB/akb-{collector,curator,synthesizer,translator}/` | keep_defer_no_strong_evidence | legacy AKB 1.0 skill，无迁移证据 |
| `Claude Code/skills/akb-{collector,curator,monitor,search,status,synthesizer,translator}/` | keep_defer_no_strong_evidence | legacy AKB 1.0 skill，无迁移证据 |
| `Claude Code/skills/claude-active/akb-{collector,curator,monitor,search,status,synthesizer,translator}/` | keep_defer_no_strong_evidence | legacy AKB 1.0 skill，无迁移证据 |
| `Obsidian/AKB2.0/.akb-runtime/lint/akb2-lint.cpython-313.pyc` | keep_defer_no_strong_evidence | .pyc 生成产物，随 .akb-runtime 父目录一并删除 |

> **注**：21 个 defer 项中，.pyc 文件随父目录 `.akb-runtime` 一并删除；其余 legacy akb-* skills 属于 AKB 1.0 运营技能（非工程源代码），不在本次迁移范围内，留给后续单独决策。

---

## 9. 备份与 SHA 清单

| 文件 | 入 git | 说明 |
|------|--------|------|
| `pre-delete-sha256.tsv` | ✅ | 24 个删除项的 SHA 基线，可追溯 |
| `pre-delete-archive.tar.gz` | ❌（本地） | 469 KB，完整 tar 归档，可 rollback |
| `obsidian-lint-snapshot.tar.gz` | ❌（本地） | 38 KB，Obsidian lint 目录单独快照 |
| `candidates-raw.txt` | ✅ | 47 行候选扫描原始结果 |
| `cleanup-manifest.tsv` | ✅ | 47 行 8 字段，分类 + 证据 + post_state |
| `before-scan.txt` | ✅ | 6 个扫描根的 before 快照 |
| `after-scan.txt` | ✅ | 0 RESIDUAL，0 MISSING |
| `cleanup-execution.log` | ✅ | deleted:24，failed:0 |
| `tar.log` | ✅ | tar 命令输出（正常警告 Removing leading '/'） |

---

## 10. Before/After 验证命令与结果

### Before（Step 1 preflight）

| 检查 | 结果 |
|------|------|
| git status | clean（无 dirty 文件） |
| Step 6+7 commit 在位 | `3d20a67` ✅ |
| verify-no-drift.sh | 17/17 MATCH, exit 0 |
| post-change-pipeline.sh | exit 0, dry_run_no_write=true |
| pytest -q | 60 passed, exit 0 |
| 候选扫描 | 47 项（≤50 上限 ✅） |
| akb-pre-commit.sh 消费者 | cron/settings.json 均无引用 |
| Obsidian lint gate | `tests/test_lint.py` 引用 `src/akb2/lint/akb2_lint.py` ✅ |

### After（Step 5 验证）

| 检查 | 结果 |
|------|------|
| verify-no-drift.sh | 17/17 MATCH, exit 0 |
| post-change-pipeline.sh | exit 0, dry_run_no_write=true |
| pytest -q | 60 passed, exit 0 |
| after-scan RESIDUAL | 0 |
| after-scan MISSING | 0 |

---

## X. Rollback 命令（失败时直接照抄）

```bash
ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/Project AKB2"
BACKUP="$ROOT/reports/backups/pre-step8-cleanup-2026-05-09"

# 全量 rollback：从 tar 解压恢复所有已删除项
# 注意：tar 归档时已去掉 leading '/'，需要解压到 /
tar -xzf "$BACKUP/pre-delete-archive.tar.gz" -C /

# Obsidian lint 单独快照（若需单独恢复）
OBSIDIAN_RUNTIME="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0/.akb-runtime"
[[ -f "$BACKUP/obsidian-lint-snapshot.tar.gz" ]] && \
  tar -xzf "$BACKUP/obsidian-lint-snapshot.tar.gz" \
    -C "$(dirname "$OBSIDIAN_RUNTIME")"

# iCloud 沉淀 + 验证
sleep 5
bash "$ROOT/scripts/verify-no-drift.sh"
```

> 注意：sync 脚本本身不影响 cleanup；rollback 仅恢复被删除的旧原位副本，不改 install/mirror target。

---

## 11. 明确未执行（边界）

| 项目 | 状态 |
|------|------|
| 删除 install target / mirror / MCP wrapper target | ❌ 未执行 |
| 删除旧 Project AKB external readonly dependency | ❌ 未执行 |
| 修改 `~/.claude/**` | ❌ 未执行 |
| 启用 MCP / hook / cron / LaunchAgent | ❌ 未执行 |
| 修改 Obsidian AKB2 vault 正文（wiki/ docs/ toolbox/ raw/ manifests/） | ❌ 未执行 |
| 删除无强证据的命名相似项（21 个 defer 项） | ❌ 未执行 |
| akb-* legacy AKB 1.0 skill 清理 | ❌ 未执行（defer，后续单独决策） |
