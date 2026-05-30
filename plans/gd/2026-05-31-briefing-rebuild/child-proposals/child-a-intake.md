```yaml
template_kind: gd-child-plan-output
parent_dispatch_id: akb2-briefing-rebuild
parent_track_id: t-intake
agent_role: child_planner
output_status: completed
```

---

# Step 1: P0 抓取重建

## 目标链

```
PROJECT_GOAL: 在 Claude Code 中建设 /gd Goal-Driven 多 Agent 主链路，提升复杂任务的计划、审查、执行、验收效率，并通过 Codex 作为 cross-review sidecar 降低填表式计划与执行遗漏风险。
CHAIN_GOAL: 用 shared core 固定目标链、SC、任务包、review contract 和 anti-fill 标准，保证后续 /gd command、multi-agent dispatch、execution review、Codex cross-review 都引用同一套契约。
PHASE_GOAL: AKB2 社交日报抓取从"关键词广搜"重建为"按策展博主清单抓时间线"，并与现有后半段（scoring→synthesis→translate→audit→render）无缝对接，不改对接 dict 形状。
TASK_GOAL: 产出 P0 抓取重建的 GD step plan + task packets，覆盖 SC-1~SC-6，每条 SC 绑定可执行 verify。
```

## Review 对齐

- **REVIEW_DOMAIN**：`app_code`
- **REVIEW_FOCUS**（3-5 项）：
  1. **账号抓取正确性** — `account_collector` 按 curator fetch tier 逐账号 `twitter search --from <h>` 抓时间线（无关键词），字段经 `_twitter_cli_to_akb2` adapter 映射为对接 dict 形状，作者归属正确。
  2. **跨账号去重 + 对接契约不变** — 多账号合并后按 tweet `id` 去重（保留首次出现），输出严格为 `{"tweets":[...], "_fallback_used"?: bool}`，下游 scoring/synthesis 无需改动。
  3. **抓取边界确定性** — tier 账号数上限、单账号 `cap`、全局总条数上限、账号间 pacing sleep 全部用确定性代码（非模型）实现，且均可 env 覆盖；时间窗 since/until 与 pipeline 现有 Asia/Shanghai 语义一致（`--until` = date+1，排他上界）。
  4. **降级链 fail-visible** — 单账号失败/0 条 → stderr 告警跳过不中断整批；全账号失败 → 降级关键词（旧 L0）；再失败 → fixture 并置 `_fallback_used=true`；降级路径在 stderr 可见，不静默吞错。
  5. **pipeline 切换无回归** — `run_intake` subprocess 目标改 `account_collector`，保持 `--since/--until` 传参与返回 dict 形状；`AKB2_INTAKE_MODE=keyword` 可回退旧 L0；`--dry-run-intake` 端到端不破。

Domain-specific notes（app_code）：
- changed files：`src/akb2/briefing/intake/account_collector.py`（新增）、`src/akb2/briefing/intake/base.py`（adapter 提取共用）、`config/curator-accounts.yaml`（加 fetch tier + 去 OCR 错）、`config/intake_sources.yaml`（account source 配置）、`src/akb2/briefing/pipeline.py`（run_intake 切换 + env 回退）、`tests/briefing/`（新增账号抓取/去重/上限/降级/tier/wire 测试）
- API contracts：intake 对接 dict 形状 **不变**（`tweets` + 可选 `_fallback_used`）
- data schema：无 schema 变更（复用 social_trends_v1_1 tweets.items）
- tests：mock subprocess 覆盖去重/上限/降级/单账号失败；mock yaml 覆盖 tier 加载；mock subprocess 覆盖 run_intake 目标切换
- security/performance：抓取调用量 = tier账号数 × 单账号cap，必须 tier上限 + cap + 全局上限 + pacing 四重天花板；不记录 cookie/key；stderr 告警脱敏
- deployment runtime：仍由 daily_briefing.sh / LaunchAgent 调用，不改调度；env 默认值可被运维覆盖

## 前置条件

1. `twitter` CLI 已安装且 cookie auth 可用（`shutil.which("twitter")` 或 `~/.local/bin/twitter` 存在）。
2. `twitter search --from <handle> -t latest --since --until --json -n` 已验证无需关键词即可拉账号时间线（计划文档已确认，步骤 1 真跑复核）。
3. `config/curator-accounts.yaml` 101 handle 全集已留档（含 3 个疑似 OCR 错误标注）。
4. 现有 `tests/briefing/` 基线全绿（步骤前 `pytest tests/briefing/ -q` 确认）。
5. 对接 dict 形状以 `pipeline.run_intake`（pipeline.py:115）+ `_filter_recent`（pipeline.py:62）+ `_fallback_used` 跳过逻辑（pipeline.py:475）为准，不得变更。

## SC（每条带 verify method + cmd + expect）

- **SC-1**：`account_collector` 能读 curator fetch tier 并逐账号抓时间线（`--from` 无需关键词）。
  - verify method：`test`
  - cmd：`pytest tests/briefing/test_account_collector.py::test_account_collect_reads_tier_and_fetches -q`
  - expect：mock subprocess 下，collector 对 tier 内每个 handle 各发一次 `twitter search --from <h>`（断言 cmd 含 `--from` 且不含关键词 positional arg），返回经 adapter 的对接 dict。

- **SC-2**：跨账号按 `id` 去重，输出对接契约 dict 形状（`tweets` + 可选 `_fallback_used`）。
  - verify method：`test`
  - cmd：`pytest tests/briefing/test_account_collector.py::test_account_collect_dedup -q`
  - expect：两账号返回含同一 `id` 的推文，合并结果该 id 仅一条；输出顶层 key 恰为 `{"tweets"}`（成功路径无 `_fallback_used`）；每条 tweet 含 `id/text/author/created_at/url/metrics{likes,retweets,replies}`。

- **SC-3**：硬边界 — tier 账号数 / 单账号 cap / 全局上限 + 账号间确定性 pacing，均 env 可调。
  - verify method：`test`
  - cmd：`pytest tests/briefing/test_account_collector.py::test_account_collect_caps -q`
  - expect：env 设 `AKB2_ACCOUNT_TIER_MAX=2`/`AKB2_ACCOUNT_CAP=3`/`AKB2_ACCOUNT_GLOBAL_MAX=4`，mock 每账号返回 10 条 → 实际只调用 ≤2 账号、单账号传 `-n 3`、合并后 ≤4 条；pacing 通过 mock 的 `time.sleep` 断言被调用且参数 = env `AKB2_ACCOUNT_PACING_SEC`。

- **SC-4**：降级链 — 单账号失败跳过告警不中断；全失败→关键词→fixture 标 `_fallback_used`。
  - verify method：`test`
  - cmd：`pytest tests/briefing/test_account_collector.py::test_account_collect_fallback_chain tests/briefing/test_account_collector.py::test_account_collect_skips_failed_account -q`
  - expect：(a) 单账号 subprocess 抛错/返回 0 条 → 其余账号仍被抓、合并非空、stderr 含该 handle WARNING；(b) 全账号失败 → 走关键词 collector（mock 命中 `_fetch_real` 路径）；关键词也失败 → 返回 fixture 推文且 `_fallback_used is True`。

- **SC-5**：curator fetch tier 选定（剔除 3 个 OCR 错、修 mckaywrigley）。
  - verify method：`test`
  - cmd：`pytest tests/briefing/test_curator_tier.py::test_curator_fetch_tier_loaded -q`
  - expect：`curator-accounts.yaml` 含 `fetch_tier` 列表，长度 = 选定 Top-N（默认 30）；不含 `mickaywiggley`/`somolicick`/`somonie`；含修正后的 `mckaywrigley`；tier 内所有 handle 为非空字符串且属于 `accounts` 全集（修正项除外）。

- **SC-6**：`run_intake` 切 `account_collector`，保持传参/返回形状，env 可回退。
  - verify method：`test`
  - cmd：`pytest tests/briefing/test_pipeline_intake_wire.py::test_run_intake_uses_account_collector tests/briefing/test_pipeline_intake_wire.py::test_run_intake_keyword_fallback_env -q`
  - expect：(a) 默认 `run_intake` 的 subprocess cmd `-m` 目标为 `akb2.briefing.intake.account_collector`，且仍传 `--since/--until/--output`，返回 dict 形状不变；(b) 设 `AKB2_INTAKE_MODE=keyword` 时 subprocess `-m` 目标回退为 `akb2.briefing.intake.l0_collector`。

## 非目标（本 Step 明确不做）

- P2 结构精炼（SC-7~SC-11）— 属结构组 track，本 step 不碰 `synthesis.py`/`translate.py`/`audit.py`/渲染函数。
- SC-13 真实 key+cookie 全量跑（分支手动单跑，非自动化验证范围）。
- L1/L2 信任传播、L0' X List、L3 arxiv/HF、自进化清单、新评分维度。
- scoring 评分算法本体、validator/schema、gate、调度、secrets 常驻。
- fetch tier 规模 / cap / N / 全局上限的最终调优（先定默认，env 可调，真实跑后调）。

## 实现步骤（每步 WHERE / WHAT / WHY / VERIFY）

### 步骤 1.1：抓取可行性复核 + adapter 提取到 base.py
- **WHERE**：`src/akb2/briefing/intake/base.py`；`src/akb2/briefing/intake/l0_collector.py`（改 import）
- **WHAT**：把 `_twitter_cli_to_akb2`（l0_collector.py:25-45）整体迁到 `base.py` 作模块级共用函数；`l0_collector` 改为 `from .base import _twitter_cli_to_akb2`；真跑一条 `twitter search --from karpathy -t latest --since <d-2> --until <d+1> --json -n 5` 复核 `--from` 无需关键词、字段（id/text/author.screenName/createdAtISO/metrics）齐全。
- **WHY**：account_collector 与 l0_collector 共用同一 adapter，避免两份逻辑漂移。
- **VERIFY**：`pytest tests/briefing/test_intake_config.py -q` 现有绿不破（adapter 迁移不改行为）；真跑返回该账号近期推文 JSON（`payload.ok=true`、`data` 非空）。

### 步骤 1.2：实现 account_collector 抓取 + 去重 + 上限 + pacing
- **WHERE**：`src/akb2/briefing/intake/account_collector.py`（新增）；`src/akb2/briefing/intake/base.py`（复用 adapter）
- **WHAT**：新增 `AccountCollector(BaseCollector)`：(a) 读 `curator-accounts.yaml` 的 `fetch_tier`，截断到 `AKB2_ACCOUNT_TIER_MAX`（默认 30）；(b) 逐 handle subprocess `twitter search --from <h> -t latest --since --until --json -n <cap>`（`cap`=`AKB2_ACCOUNT_CAP` 默认 10），复用 `_twitter_cli_to_akb2`；(c) 账号间 `time.sleep(AKB2_ACCOUNT_PACING_SEC)`（默认 1.5）；(d) 合并后按 `id` 去重（保留首现，确定性顺序）；(e) 全局截断到 `AKB2_ACCOUNT_GLOBAL_MAX`（默认 200）。CLI 与 l0_collector 对齐（`--output/--since/--until/--dry-run`），输出 `{"tweets":[...]}`（+ 降级时 `_fallback_used`）。
- **WHY**：核心新件，把抓取从关键词广搜改为按账号时间线（PHASE_GOAL）。
- **VERIFY**：`pytest tests/briefing/test_account_collector.py::test_account_collect_dedup tests/briefing/test_account_collector.py::test_account_collect_caps -q`（SC-2/SC-3）。

### 步骤 1.3：实现三级降级链 + 单账号容错
- **WHERE**：`src/akb2/briefing/intake/account_collector.py`
- **WHAT**：单账号 subprocess 超时/exit≠0/JSON 解析失败/payload.ok=false/0 条 → stderr `WARNING: account <h> ...`，continue 下一账号（不中断）。所有账号抓完合并为空 → 调用关键词降级（实例化 `L0Collector` 走 `_fetch_real`，复用其内部 fixture 兜底）；关键词降级也最终落 fixture 时，确保顶层 `_fallback_used=True`（透传 `L0Collector.used_fallback`）。
- **WHY**：fail-visible，抓取空不产空报、不静默；与 pipeline 的 `_fallback_used` 跳过日期过滤逻辑（pipeline.py:475）对齐。
- **VERIFY**：`pytest tests/briefing/test_account_collector.py::test_account_collect_fallback_chain tests/briefing/test_account_collector.py::test_account_collect_skips_failed_account -q`（SC-4）。

### 步骤 1.4：curator fetch tier 人工核对 + intake_sources account 配置
- **WHERE**：`config/curator-accounts.yaml`；`config/intake_sources.yaml`
- **WHAT**：(a) `curator-accounts.yaml` 增 `fetch_tier:` 列表（从 `accounts` 选 Top 30 高信号 AI 研究/工程账号；剔除 `mickaywiggley`/`somolicick`/`somonie`；以 `mckaywrigley` 替换；注释标注分层依据）；保留 `accounts` 全集留档。(b) `intake_sources.yaml` 增 `account` source 段（`enabled`、`tier_ref`、`cap`、`pacing_sec`、`tier_max`、`global_max`），与现有 l0/l0_prime/l3/l4 并列（不删旧键，避免破坏 `test_intake_config.py` 现有断言）。
- **WHY**：101 全量 × 抓取超 rate-limit，需可控 Top-N 子集 + OCR 错剔除（SC-5）。
- **VERIFY**：`pytest tests/briefing/test_curator_tier.py::test_curator_fetch_tier_loaded -q`（SC-5）；`pytest tests/briefing/test_intake_config.py -q` 旧断言仍绿（l0/l0_prime/l3/l4 键保留）。

### 步骤 1.5：pipeline.run_intake 切 account_collector + env 回退
- **WHERE**：`src/akb2/briefing/pipeline.py`（`run_intake`，pipeline.py:115-144）
- **WHAT**：`run_intake` 的 subprocess `-m` 目标由 `akb2.briefing.intake.l0_collector` 改为读 env：`AKB2_INTAKE_MODE`（默认 `account` → `account_collector`；`keyword` → `l0_collector`）；保持 `--output/--since/--until/--dry-run` 传参与返回 dict 形状不变；保持现有 PYTHONPATH/cwd/错误处理逻辑。
- **WHY**：把重建的抓取接进主链路，保持对接契约且提供回退开关（SC-6）。
- **VERIFY**：`pytest tests/briefing/test_pipeline_intake_wire.py -q`（SC-6）；端到端 `python -m akb2.briefing.pipeline --date <today> --vault-root <tmp> --dry-run-intake` 不破。

## Task Packet 拆分表

| task_id | 范围 | owned_paths | blocked_by | can_parallel_with |
|---------|------|-------------|------------|-------------------|
| `p0-account-collector-impl` | T1+T2：adapter 提取 + account_collector 抓取/去重/上限/pacing/降级链 | `intake/account_collector.py`、`intake/base.py`、`intake/l0_collector.py`、`tests/briefing/test_account_collector.py` | — | `p0-curator-fetch-tier` |
| `p0-curator-fetch-tier` | T3：fetch tier 选定 + OCR 错剔除 + intake_sources account 段 | `config/curator-accounts.yaml`、`config/intake_sources.yaml`、`tests/briefing/test_curator_tier.py` | — | `p0-account-collector-impl` |
| `p0-pipeline-wire-account-collector` | T4：run_intake 切 account_collector + env 回退 | `src/akb2/briefing/pipeline.py`、`tests/briefing/test_pipeline_intake_wire.py` | `p0-account-collector-impl`、`p0-curator-fetch-tier` | （无 — 与结构组 pipeline 改动冲突，见 packet 内声明） |

## 边界

- 抓取调用量天花板：tier_max(30) × cap(10) = 300 次上限，全局合并截断 200 条；账号间 pacing 1.5s（确定性，非模型）。
- 时区：since/until 沿用 pipeline 现有 Asia/Shanghai 语义，`--until` 用 date+1（排他上界，沿用 v2 修复）— account_collector 只透传 pipeline 传入的 `--since/--until`，不自行计算时间窗。
- 去重确定性：按 tier 顺序 + 账号内返回顺序合并，同 id 保留首现，保证可复现。
- 不碰 P2 文件，不改对接 dict 形状，不改 scoring/gate/schema/调度。
- 不读写真实 vault、不提交 secret、不记录 cookie/key。

## 风险

| 风险 | 触发条件 | 防护 |
|------|---------|------|
| 抓取调用量炸 rate-limit | tier 过大 × cap 过高无 pacing | tier_max + cap + global_max + pacing 四重天花板，全 env 可调 |
| 策展账号近期无 AI 干货 → 内容稀 | 时间窗 N 太短 | N 由 pipeline 控（可放宽）+ 关键词 fallback 兜底不产空报 |
| adapter 提取引入回归 | l0_collector import 路径改动 | 步骤 1.1 仅迁移不改行为，`test_intake_config` 现有绿守护 |
| run_intake 切换破坏 dry-run / 下游对接 | subprocess 目标改动 | 形状不变断言 + `--dry-run-intake` 端到端验证 + env 回退开关 |
| pipeline.py 与结构组渲染改动冲突 | 两 track 同时改 pipeline.py | `p0-pipeline-wire` packet 声明与结构组 pipeline 改动**不可并行**，需串行或 rebase |

## 测试计划

- 新增 `tests/briefing/test_account_collector.py`：`test_account_collect_reads_tier_and_fetches`(SC-1)、`test_account_collect_dedup`(SC-2)、`test_account_collect_caps`(SC-3)、`test_account_collect_fallback_chain`(SC-4)、`test_account_collect_skips_failed_account`(SC-4)。全程 mock `subprocess.run` + mock `time.sleep` + tmp_path，不触网。
- 新增 `tests/briefing/test_curator_tier.py`：`test_curator_fetch_tier_loaded`(SC-5)。
- 新增 `tests/briefing/test_pipeline_intake_wire.py`：`test_run_intake_uses_account_collector`(SC-6)、`test_run_intake_keyword_fallback_env`(SC-6)。
- 回归：`pytest tests/briefing/test_intake_config.py -q`。
- 全量：`pytest tests/briefing/ -q` 全绿（SC-12 的 P0 部分）。
- 隔离：mock 所有 subprocess/sleep；tmp_path 造输出；绝不写真实 vault；不提交 secret。

## Assumptions

1. `templates/gd-step-plan-template.md` / `gd-task-packet-template.md` 在本 worktree 不存在（属 GD 运行时，位于禁止访问的 `~/.claude/`）— 按字段结构产出，未阻塞。
2. fetch tier 默认 Top 30、单账号 cap 10、全局 200、pacing 1.5s、时间窗 N=2（沿用计划文档默认值，全部 env 可覆盖）。
3. env 变量命名采用 `AKB2_ACCOUNT_*` 前缀（与现有 `AKB2_INTAKE_WINDOW_DAYS`/`AKB2_ORPHAN_TOP_K` 风格一致）；intake 模式开关用 `AKB2_INTAKE_MODE`。
4. 关键词降级复用现有 `L0Collector._fetch_real`（含其内部 fixture 兜底）。
5. `intake_sources.yaml` 保留 l0/l0_prime/l3/l4 旧键（仅追加 account 段）。

---

## Task Packets（详细见下，task_id / owned_paths / blocked_by / verify 与下方 proposal block 一致）

> 3 个 task packet：`p0-account-collector-impl`（T1+T2）、`p0-curator-fetch-tier`（T3）、`p0-pipeline-wire-account-collector`（T4）。
> 各 packet 自包含合约：goal_chain（含 TASK_GOAL）/ required_context / owned_paths / forbidden_paths（含 `/Users/praise/.claude/**` + 其他 track owned）/ SC / deliverables / verify（sc_ref+method+cmd+expect）/ handoff_output。
> owned_paths 互不重叠：collector 任务独占 `account_collector.py`/`base.py`/`l0_collector.py`、tier 任务独占两份 config、wire 任务独占 `pipeline.py`。wire 任务 blocked_by 前两者；与结构组 pipeline 改动声明不可并行。

## 中文结论摘要

按 `docs/briefing-rebuild-plan.md` P0 段产出「Step 1: P0 抓取重建」step plan + T1~T4 三个自包含 task packet，覆盖 SC-1~SC-6，每条 SC 绑定可执行 verify。owned_paths 互不重叠，wire 任务 blocked_by 前两者、与结构组 pipeline 改动不可并行。已知缺口：GD templates 不在 worktree（按字段规范产出，未阻塞）；fetch tier 规模等参数取默认值（env 可调）。

<!-- gd-child-plan-proposal-json:start -->
```json
{
  "proposal_id": "akb2-briefing-rebuild-t-intake-p0a1",
  "parent_dispatch_id": "akb2-briefing-rebuild",
  "parent_track_id": "t-intake",
  "agent_role": "child_planner",
  "output_status": "completed",
  "summary_cn": "P0 抓取重建 step plan + T1~T4 三个 task packet，覆盖 SC-1~SC-6，owned_paths 互不重叠，pipeline wire 与结构组冲突已声明不可并行。",
  "task_packets": [
    {
      "task_id": "p0-account-collector-impl",
      "owned_paths": [
        "src/akb2/briefing/intake/account_collector.py",
        "src/akb2/briefing/intake/base.py",
        "src/akb2/briefing/intake/l0_collector.py",
        "tests/briefing/test_account_collector.py"
      ],
      "required_context": [
        "docs/briefing-rebuild-plan.md",
        "src/akb2/briefing/intake/l0_collector.py",
        "src/akb2/briefing/intake/base.py",
        "config/curator-accounts.yaml",
        "config/intake_sources.yaml"
      ]
    },
    {
      "task_id": "p0-curator-fetch-tier",
      "owned_paths": [
        "config/curator-accounts.yaml",
        "config/intake_sources.yaml",
        "tests/briefing/test_curator_tier.py"
      ],
      "required_context": [
        "docs/briefing-rebuild-plan.md",
        "config/curator-accounts.yaml",
        "config/intake_sources.yaml",
        "tests/briefing/test_intake_config.py"
      ]
    },
    {
      "task_id": "p0-pipeline-wire-account-collector",
      "owned_paths": [
        "src/akb2/briefing/pipeline.py",
        "tests/briefing/test_pipeline_intake_wire.py"
      ],
      "required_context": [
        "docs/briefing-rebuild-plan.md",
        "src/akb2/briefing/pipeline.py",
        "src/akb2/briefing/intake/account_collector.py",
        "tests/briefing/test_date_filter.py"
      ]
    }
  ],
  "sc_refs": ["SC-1", "SC-2", "SC-3", "SC-4", "SC-5", "SC-6"],
  "verify": [
    {"sc_ref": "SC-1", "method": "command", "cmd": "pytest tests/briefing/test_account_collector.py::test_account_collect_reads_tier_and_fetches -q"},
    {"sc_ref": "SC-2", "method": "command", "cmd": "pytest tests/briefing/test_account_collector.py::test_account_collect_dedup -q"},
    {"sc_ref": "SC-3", "method": "command", "cmd": "pytest tests/briefing/test_account_collector.py::test_account_collect_caps -q"},
    {"sc_ref": "SC-4", "method": "command", "cmd": "pytest tests/briefing/test_account_collector.py::test_account_collect_fallback_chain tests/briefing/test_account_collector.py::test_account_collect_skips_failed_account -q"},
    {"sc_ref": "SC-5", "method": "command", "cmd": "pytest tests/briefing/test_curator_tier.py::test_curator_fetch_tier_loaded -q"},
    {"sc_ref": "SC-6", "method": "command", "cmd": "pytest tests/briefing/test_pipeline_intake_wire.py::test_run_intake_uses_account_collector tests/briefing/test_pipeline_intake_wire.py::test_run_intake_keyword_fallback_env -q"}
  ],
  "blocked_reason": null
}
```
<!-- gd-child-plan-proposal-json:end -->
