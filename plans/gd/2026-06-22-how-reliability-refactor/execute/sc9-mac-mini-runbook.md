# SC-9 端到端黄金验收 — mac mini 操作清单（deferred）

> SC-9 是 how-reliability-refactor 主计划的**最终判据**（"验整车上路"，区别于 SC-1~8 的"验零件"）。
> 本机（开发机）跑不了真：maker=deepseek 无 `DEEPSEEK_API_KEY`，官方 `OPENAI_API_KEY` 打 tapsvc 网关返 401
> → grader 降级到假 0 缺陷的 gemini 兜底。**必须**在有 LLM gateway + 正确 key + vault 挂载的公司 mac mini 上跑。
>
> 与 memory 一致：`project_akb2_deployment_split`（本机只开发，pipeline 部署在 mac mini）+
> `project_akb2_grader_tapsvc_env_conflict`（官方 key 打 tapsvc 401 → 假 0 缺陷）。
>
> **诚实底线**：本 runbook 不在本机产任何 evidence。SC-9 在 mac mini 真跑通前，outcome 里 SC-9 永远是 `not_run`。
> 不得为过 SC-9 降量规、不得伪造 grader/gate/lint 结果。

---

## 0. 前置条件（缺任一即停，不得跳过）

| 前置 | 检查命令 | 期望 |
|------|---------|------|
| LLM gateway 可达 | `curl -s -o /dev/null -w "%{http_code}\n" --max-time 6 "$AI_GATEWAY_BASE_URL"` | 非 401/000（网关在线且 key 有效） |
| maker key 就位 | `[ -n "$DEEPSEEK_API_KEY" ] && echo OK` | `OK`（deepseek 是 maker，runner 产候选页靠它） |
| checker key 就位 | grader 走 gpt-5.4，确认对应 key（gateway key 或 OPENAI 走网关）有效，**非外部官方 key 打 tapsvc** | 解析成功、`fallback_used=False` |
| vault 挂载 | `ls "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0" >/dev/null && echo OK` | `OK` |
| test-fresh raw 存在 | `ls "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/test-fresh-entry--raw--2026-06-21.md"` | 文件存在 |
| 分支正确 | `git -C <repo> rev-parse --abbrev-ref HEAD` | `feature/how-reliability-refactor`（或目标分支） |
| 隔离目录干净 | `rm -rf /tmp/akb2-how-p0 && mkdir -p /tmp/akb2-how-p0/{vault,staging}` | 全新隔离目录 |

> 任一前置不满足 → 停止，回填 outcome SC-9 仍为 `not_run`，记录哪个前置缺失。

---

## 1. 端到端隔离跑（产出候选页）

在 repo 根（`Project AKB2/`）执行。`AKB2_AUTO_PROMOTE=0` 防止误写真 vault；`--staging-dir` 必带。

```bash
RAW_DIR="/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw"   # test-fresh 所在目录
# 若只想跑 test-fresh 单篇，把它单独 copy 到一个隔离 raw 目录再指过去：
mkdir -p /tmp/akb2-how-p0/raw
cp "$RAW_DIR/test-fresh-entry--raw--2026-06-21.md" /tmp/akb2-how-p0/raw/

AKB2_AUTO_PROMOTE=0 PYTHONPATH=src python3 -m akb2.quality.runner --once \
  --raw-dir /tmp/akb2-how-p0/raw \
  --vault-root /tmp/akb2-how-p0/vault \
  --staging-dir /tmp/akb2-how-p0/staging
```

**期望**：runner 产出 1 个候选页（成稿 .md），How 段含原文操作 0 漏，`compose_candidate` 不返回 None。
**失败立即停**：若 runner 报 `route_llm RuntimeError` / 候选页为空 / compose None → gateway 或 key 仍不对，回到前置 0。

记下产出的候选页路径（下文记作 `$CANDIDATE`），通常在 `--staging-dir` 下：

```bash
CANDIDATE=$(find /tmp/akb2-how-p0/staging -name '*.md' -type f | head -1)
echo "候选页: $CANDIDATE"
```

---

## 2. 三道运行时守卫门槛（全过才算 SC-9 pass）

### 2a. grader 10 维量规

```bash
PYTHONPATH=src python3 -m akb2.quality.grader "$CANDIDATE"
```

- **期望**：grader 返回 **10 维全 pass**，且 `fallback_used=False`、`suspended=False`（degraded 护栏不放水）。
- **若 `fallback_used=True`** → grader 降级了，结果无效，回前置 0 修 key，**不得**当 pass。

### 2b. gate CLI 10 硬检查

```bash
PYTHONPATH=src python3 -m akb2.gate.akb2_gate_check "$CANDIDATE"
```

- **期望**：10/10 硬检查全过，gate 判定 PASS。

### 2c. lint 0 findings

```bash
PYTHONPATH=src python3 -m akb2.lint.akb2_lint "$CANDIDATE"
```

- **期望**：0 findings。

> 三道全过 = SC-9 pass。任一不过 → 按下方诚实分流，**不得**降量规凑过。

---

## 3. 诚实分流（fail 时按维度，照搬主计划 §SC-9）

| fail 落点 | 含义 | 动作 |
|-----------|------|------|
| How 维（1 锚定 / 2 How 密度 / 3 How 可执行） | P0/P1 机制还有漏 | 回修 scanner / must_cover 渲染 / SG / 机器回查；属本计划范围 |
| 语义 5 维（4 证据诚实 / 6 TLDR / 7 新颖 / 8 边强度 / 9 Future Questions） | **非本计划机制缺陷**，是 maker(deepseek) 语义产出未达标 | 触发"换 maker 评估"（押后单元 deepseek→codex/gpt-5.4 提前），**本计划不在此处降量规** |

任何情况：**不得为过 SC-9 降量规、不得伪造 grader/gate/lint 输出**。

---

## 4. 回填 outcome SC-9（仅在 mac mini 真跑通后）

真跑通且 2a/2b/2c 全过后，编辑
`plans/gd/2026-06-22-how-reliability-refactor/execute/aggregate-outcome.json`：

1. `sc_acceptance` 中 SC-9 条目：`status: "not_run"` → `"pass"`；清空 `not_run_reason`；
   `evidence` 写真实结果，例如：
   `"mac mini 端到端(gateway 在线, fallback_used=False)：grader 10 维全 pass + gate 10/10 + lint 0；候选页 <路径>，run 时间 <ISO>"`
2. 同步更新 sidecar `verify-evidence.json` 里 SC-9 的 `actual_exit` / `status` / `evidence`（填真实退出码，删 `not_run_reason`）。
3. `known_limitations` 中删除 SC-9 deferred 那条。
4. `handoff.blockers` 删除 "SC-9 端到端需 mac mini gateway"。
5. 重跑 schema + validator 确认仍 exit 0：
   ```bash
   python3 "/Users/praise/AI-Agent/Claude/projects/Project GD/scripts/gd-validate-execution-outcome.py" \
     plans/gd/2026-06-22-how-reliability-refactor/execute/aggregate-outcome.json
   ```
6. 重跑 `/gd review` 复核（此时 11/11 SC 全有真实判据）。

> 若 mac mini 跑出语义维缺口（第 3 节右栏），SC-9 仍标 `not_run` 或如实标 `fail` + 触发换 maker 评估，
> **不要**改成 pass。SC-9 的价值就是把"机制对 ≠ 成品黄金"的真相显形。
