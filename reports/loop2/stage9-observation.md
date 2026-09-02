# loop2 阶段 9 观察脚手架

日期: 2026-09-01
状态: 未开始日历跑

计划 §1.3：S1 影子跑 3 天 → S2 双跑 7 天 → S3 抽检 1 天 → S4 归档（C5）。
本文件只记录入口与过线口径。不把空跑写成过线。

## 2026-09-01 本机实跑（Pro，不加载 launchd，不归档 loop1）

环境：PraisedeMacBook-Pro-2；`/Volumes/AKB2` 未挂；twitter CLI 已认证 `@PraiseZhu52`。
产物只进仓库 gitignore 的 `reports/intake/`，`--vault-root` 指向 `reports/intake/sandbox-vault`。iCloud wiki 未写。loop2 plist 未 bootstrap。

1. **解析坑**：`twitter feed --json` 实际 `ok:true`，但 stderr 有 `Failed to init ClientTransaction`。旧逻辑把该警告当 fatal，整批 0 条。已改：`ok:true` 的 payload 优先。
2. **采集**：source `~/.config/akb2/github-briefing.env` 后 grok 预筛走网关。`fetched=63 kept=15 archived=3 failed=3`（limit 5）。raw 3 篇在 `reports/intake/raw-loop2/`。
3. **runner2 --once ×3**（`AKB2_LOOP2_SHADOW_PROMOTE=1`）：
   - `x-bookmarks--91a6179c9080` → admission 拒：`non_knowledge`（OpenChamber 产品页）
   - `x-bookmarks--d8c803385fa4` → 准入过（design），抽取合同 fail：`evidence_gap` 不含盲区词
   - `x-home-feed--e0856be21973` → 准入过（design），`deepseek-v4-pro` 抽取超时 180s
4. sandbox `wiki/sources` 空；`launchctl` 无 `com.akb2.loop2-*`。

未做：C3 List id、S1 三日日历、C5。loop1 服务未动。

## 2026-09-01 续：本机加载 plist + 影子闭环（仍不归档 loop1）

本机 `launchctl` 已加载 `com.akb2.loop2-intake`（4h）和 `com.akb2.loop2-runner`（KeepAlive）。WorkingDirectory 指向本 worktree；vault 用 iCloud `AKB2.0` 的 `raw-loop2`/`staging-loop2`。plist 不含 API key；intake 经 `scripts/akb2-loop2-intake.sh` source `~/.config/akb2/github-briefing.env`。

- 抽取：盲区词补 `未明确/未指出`；`LOOP2_EXTRACT_SEGMENT_TIMEOUT_S` 180→380（对齐 distill attempt）。
- intake kickstart：`fetched=63 kept=15 archived=1 failed=4` → `wiki` 未写；raw 落 `.../AKB2.0/raw-loop2/x-home-feed--379642deb53f--raw--2026-09-01.md`。
- runner2：准入过 → 抽取成功 → **完整性地板拒** `unanchored evidence: §B/§C/§Q/§T/§W`（合同层把不在 raw 声明集里的锚点当伪造）。`final_status=failed`。`wiki/sources` 最新仍是 2026-08-21。
- 影子闸生效：未调用 `run_promote`，staging-loop2 空。

这台不是 Mini，worktree 随时可能删，生产不要长期绑这里。loop1 无任何 job 被 bootout。

## 2026-09-01 续：短锚 / traces_to / TLDR（仍不归档 loop1）

同一篇 `x-home-feed--379642deb53f` 影子重跑三次，wiki 仍未写（最新 2026-08-21）。

1. 短锚 `§B` → 声明 `§B_INTRODUCTION`：合同 + F-A 补唯一前缀解析。
2. `akb_interpretation` 缺 `traces_to`：合同丢掉飘浮解读，不新开 LLM。
3. `§A_CONTENT_START`：合同 canonicalize 成 `§A`。
4. 抽取+成稿过了；grader blocking=`TLDR 密度`（教程车道被概念组件句盖掉）。compose 现仅 concept 用机制/组件句；tutorial 用去重 claim。abort 补记 `defects`。
5. 盲区词补 `未列出`。
6. TLDR 200 字截断切开反引号 → SG-command-recheck 把 `--no-performance-cru | Add ...` 当编造。截断后再消毒。
7. 最新 abort：适用边界过了；TLDR 两句 159 字，第三条挤不进 200。compose 去掉 provenance 前缀，3 句压进预算。
8. 随后两轮 HTTP 180s 超时。loop2 包装脚本把 `AKB2_EXTRACT_SEGMENT_TIMEOUT_S` 抬到 360，不改 loop1 默认 180。
9. 2026-09-01T11:50:53Z 首次 `shadow_blocked`：抽取/成稿/评分过，Gate-1 `proposed=` 空。原因是 gate CLI 只认 `staging/candidate/`，loop2 写 `staging-loop2/candidate/`。已放行同等路径。wiki 仍未写。
10. 路径放行后 grader 再卡适用边界：两条是产品定义不是使用场景。compose 改成「需要…」场景句。
11. 场景句过了评分，Gate-4 lint 2 条 P1：candidate 缺 `key_findings`、`staging-loop2` 路径。R9 只对 active；候选路径直接豁免 directory mismatch。wiki 仍未写。
12. lint 豁免后评分再卡 TLDR：英文空格切出 `that [raw §A]`。子句切避开悬挂虚词。
13. **2026-09-01T13:17:55Z `shadow_pass`**：抽取/成稿/结构闸/LLM 评分/定点修/模拟五闸全过。`promote.mode=shadow`，`gates_ok=true`。`wiki/sources` 最新仍是 2026-08-21 `towards-data--d4fd95ec11fa.md`。loop1 无 job、未归档。

C3 List id 仍空；日历观察未开。loop1 未动。

## 2026-09-01 Mini 通电 + 影子首轮（仍不归档 loop1）

owner「上 mini」后：`com.akb2.loop2-intake` / `com.akb2.loop2-runner` 已加载。loop1 `com.akb2.main-pipeline` PID 2053、`com.akb2.intake-twitter` PID 85286 未动。代码侧 `C1_LAUNCHD_BOOTIN` 仍 False。

- C2：`skillpack/quality/x-lane-golden-sample.md` `status: active`，正样本是 loop1 Addy Osmani 那篇，不是新 X 长文。
- C3（当时）：wiki 层没有数字 List id，list 关着。
- 首轮 intake：`fetched=63 kept=15 archived=2 skipped_short=8 dropped_prefilter=48 failed=5`。kept 是预筛计数，不是落盘数。
- runner2：3 篇都有终态（1 `shadow_pass` / 1 admission `non_knowledge` / 1 `integrity_floor` design F4）。ledger 跳过，下一轮 300s 不会卡住。
- 抽取超时：distill 段预算已 380/360；共享 `_extract_via_defuddle` 默认仍 30s。Mini 日志 `remixicon.com` 在 30s 被掐。已改成 `AKB2_DEFUDDLE_TIMEOUT_S`，loop2 intake 包装脚本默认 90；loop1 不导出该变量。未重装 plist、未 `kickstart -k` runner。

## 本轮不能假装完成的部分

| 项 | 为什么还没做 |
|----|--------------|
| C3 List id | 2026-09-02 已有 4 个互不重复 List：AKB2 `2095025924823785762`（19）、AI-research `2095044167345037528`（28）、AI-builders `2095044647383171157`（23）、design-eng `2095045077727105347`（7）。四个 timeline 均有帖。补到 50×4 失败：账号级加人锁。16:42 再探针：换人、换 List、REST `screen_name`、新建空 List 再加，全是 403/104；`lists/create`/`destroy` 仍 200。探针 List `2095071011763032118` 已删。生产 List 人数未变。剩余队列 `_tmp/fill-lists-remaining-queue.json`。今天不要再打 |
| S1–S3 日历 | 需要 3+7+1 天真实供料与账本；无法在本会话内过线 |
| C5 / S4 | 用户明确禁止归档 loop1 |
| C6 grok 前缀 | 要对账单才能定 `x-ai/` vs `x-ai-grok/` 折扣路由 |

## S1 影子跑（代码已具备，日历未开）

过线：只写 staging、promote dry-run 零 vault 写入；净供料 ≥500/day；grok JSON 合格率 ≥98%；frontmatter>25 行 = 0；模拟 promote 率 ≥30%。

代码闸：`AKB2_LOOP2_SHADOW_PROMOTE` 默认 `1`。runner2 六闸仍判，但不调用 `run_promote`，不写 `wiki/sources`。显式 `=0` 才允许写 vault（S2 才该开）。

手动开跑（Mini 两 job 已加载；代码常量 `C1_LAUNCHD_BOOTIN` 仍 False。不要再 `launchctl load` / `kickstart -k` 已在跑的 KeepAlive runner）：

```
# 采集一轮（不经 launchd）
PYTHONPATH=src python3 -m akb2.quality.intake_x.orchestrator --dry-run

# runner2 单篇 / 常驻（shadow 默认开）
bash scripts/akb2-loop2-pipeline.sh --once
```

账本：同一份 `quality-ledger.jsonl`，`loop_id=loop2`；intake state 独立 `reports/intake/loop2-intake-state.jsonl`。

哨兵：`python3 -m akb2.quality.inflow_sentinel --loop-id loop2 --dry-run`

## S2 双跑（未开始）

过线：账本 loop_id 可分；duplicate_source 不重复入库；日 promote ≥20 连续 7 天；抽检六闸全过。

前置：Mini 两 plist 已加载。S2 才把 `AKB2_LOOP2_SHADOW_PROMOTE=0` 写入 loop2-runner 运行态。现在仍是影子 `=1`，不写 wiki。

## S3 抽检（未开始）

随机 10 篇：六闸复跑全 pass + 地板 4 项全 pass + 人工过目。任一 fail → S2 重开。

## S4 归档（未开始）

S3 过线 + C5 确认后：bootout loop1 两服务，plist 移 archived/，留 30 天回滚窗。
