# Runbook — 入库断流哨兵（inflow sentinel）

> 触发器分类：**schedule**（每日 2 次，09:00 / 21:00 CST）— **当前未注册**，见下方「安装」。

## 它解决什么问题

2026-07-24 → 07-30，主质量管线连续 6 天零入库：上游 LLM API 报 400
（`response_format type is unavailable`），准入四判定全部"调用失败"，账本把它记成
`admission failed` → `abort`。**故障伪装成了质量拒绝**——链路每一段都"正常"（有进料、
有判定、有终态），没有任何 fail-closed 门会被触发，于是 6 天零告警，最后靠人工询问才发现。

段内门控看不见"整条链路产出为零"。这个哨兵只看账本的进出比，不关心每篇为什么被拒。

## 判定

窗口 = 近 `--window-hours` 小时（默认 48）。窗口边界闭左端（`ts >= now - window`）。

| 窗口内 intake_raw | 窗口内 finalize | verdict | 含义 | 排查入口 |
|---|---|---|---|---|
| > 0 | == 0 | `stalled` | **有进无出** = 入库断流 | `logs/akb2-quality-runner.log` |
| == 0 | == 0 | `no_intake` | 无新进料且零入库 = 采集停摆 **或** runner 死了 | `logs/intake.log` + `logs/akb2-quality-runner.log` |
| 任意 | > 0 | `healthy` | 有入库，不告警 | — |

两种告警文案与排查入口不同，因为现场不同。注意 `intake_raw` 事件是 **runner** 落账的
（runner 从 `raw/` 取件时写），所以"零 intake_raw"有两种成因：上游没落 raw，或 runner
daemon 死了根本没在取件。`no_intake` 文案因此同时给出两个现场——只指 intake 会把人引错方向。

告警正文含：窗口区间、三项计数、上次入库/上次进料距今小时数、窗口内 top-3 中止原因
（直接指向根因，省掉一轮翻账本）、账本损坏行/ts 不可解析行计数。

## 幂等抑制

同一次断流只告警一次。"同一次"的身份 `episode_key = verdict + 参照时刻 + 窗口长度`：
`stalled` 取账本里最后一次 `finalize` 的时刻，`no_intake` 取最后一次 `intake_raw` 的时刻。
只要没有新的 finalize 落账就还是同一次 → 抑制；恢复入库后再断，参照时刻变了 → 重新告警。

抑制状态：`~/Library/Application Support/akb2/inflow-sentinel-state.json`（经
`common/state_paths.py` 解析，**机器本地、不进 Syncthing 同步区**——落同步区两台机器会互相
覆盖抑制状态，结果要么重复轰炸要么双双静默）。状态只在**发送成功后**才写。

## 用法

```bash
# 只看判定与文案，不发送（排查 / 验收）
bash scripts/inflow_sentinel.sh --dry-run

# 指定账本快照 + 自定义窗口
bash scripts/inflow_sentinel.sh --ledger /path/to/quality-ledger.jsonl --window-hours 72 --dry-run

# 忽略抑制强制重发（手动验证告警通路）
bash scripts/inflow_sentinel.sh --force
```

退出码：`0` 检查完成（健康 / 已告警 / 已抑制 / dry-run）；`1` 检查无法完成（账本不可读、
状态根落在同步区被 fail-closed 拒绝）或告警发送失败。

## 安装（人工执行）

宪法：新增自动化触发器必须人工确认。`launchd/com.akb2.inflow-sentinel.plist` 只是模板，
仓库内**未 bootstrap**。完整安装命令写在该 plist 的头部注释里，摘要：

```bash
REPO_ROOT="$HOME/AI-Agent/Claude/projects/Project AKB2"
bash "$REPO_ROOT/scripts/inflow_sentinel.sh" --dry-run          # 1) 先验判定
sed -e "s|__AKB2_REPO_ROOT__|$REPO_ROOT|g" -e "s|__AKB2_HOME__|$HOME|g" \
    "$REPO_ROOT/launchd/com.akb2.inflow-sentinel.plist" \
    > "$HOME/Library/LaunchAgents/com.akb2.inflow-sentinel.plist"   # 2) 渲染
plutil -lint "$HOME/Library/LaunchAgents/com.akb2.inflow-sentinel.plist"  # 3) 校验
launchctl bootstrap "gui/$(id -u)" \
    "$HOME/Library/LaunchAgents/com.akb2.inflow-sentinel.plist"      # 4) 加载
launchctl kickstart -p "gui/$(id -u)/com.akb2.inflow-sentinel"       # 5) 首跑
```

卸载：`launchctl bootout gui/$(id -u)/com.akb2.inflow-sentinel`

## 凭证

`scripts/inflow_sentinel.sh` 负责 `source ~/.akb2-secrets.sh`（`FEISHU_*`），范式与
`scripts/feishu_sync.sh` 一致。plist 不含任何凭证。飞书发送走
`feishu_sync/feishu/send.py::send_text_dm`（自带 9499/429 退避重试）。

飞书不可达时降级：stderr 打印完整告警文案 + macOS 本地通知 + 退出码 1，不静默。

## 已知边界

- **intake 停超过 48h 会把 `stalled` 翻成 `no_intake`**：判定只看窗口内计数。这是刻意的
  ——那时不只是入库断了，取件本身也停了，`no_intake` 给的两个现场才是对的排查起点。
- 账本里 ts 不可解析的行不参与判定（计数后打 stderr 并写进告警文案）。这让判定偏保守：
  一条 ts 损坏的 finalize 不会把真断流盖成"健康"。
- 哨兵**只读**账本，不写、不继承旧账本（`resolve_state_file` 不传 `legacy_path`）。
