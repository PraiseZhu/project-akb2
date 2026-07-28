# Mac Mini Deployment Notes

AKB2.0 social-trends briefing pipeline deployment guide for the company Mac Mini.

## Prerequisites

- Python **3.11+** (verify: `python3 --version`; install via `pyenv install 3.13` or `brew install python@3.13`)
- All project dependencies: `pip install -e .` (installs jsonschema, pyyaml, httpx and all declared deps from `pyproject.toml`)
- Git access to the AKB2 repository
- AKB2 disk mounted and Syncthing folder planned: `/Volumes/AKB2/Obsidian/`

## Steps

### 1. Clone / pull the repository

```bash
git pull origin main
```

### 2. Install the plist

Replace **both** placeholders before loading:

```bash
REPO_ROOT=/path/to/Project-AKB2/worktree-or-clone
VAULT_ROOT="/Volumes/AKB2/Obsidian"

sed -e "s|__AKB2_REPO_ROOT__|${REPO_ROOT}|g" \
    -e "s|__AKB2_VAULT_ROOT__|${VAULT_ROOT}|g" \
    "${REPO_ROOT}/launchd/com.akb2.briefing.plist" \
    > ~/Library/LaunchAgents/com.akb2.briefing.plist
```

Verify no placeholders remain:
```bash
grep "__AKB2_" ~/Library/LaunchAgents/com.akb2.briefing.plist && echo "ERROR: unresolved placeholder" || echo "OK"
```

### 3. Load the launchd agent

```bash
launchctl load ~/Library/LaunchAgents/com.akb2.briefing.plist
```

To verify it is loaded:

```bash
launchctl list | grep com.akb2.briefing
```

### 4. Vault root configuration

The plist passes `--vault-root` to `daily_briefing.sh`. Set it to the Obsidian vault root (the parent of `Praise Space/AKB2.0/`):

```
/Volumes/AKB2/Obsidian
```

Pipeline writes output to:
```
<vault-root>/staging/candidate/<YYYY-MM-DD+1> Daily Briefing.md   (canonical)
<vault-root>/Praise Space/AKB2.0/AI TRENDS/Social/<YYYY-MM-DD+1> Daily Briefing.md  (physical copy)
```

Verify the vault root exists and the AKB2 disk path is accessible before the first scheduled run:
```bash
ls "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/"
```

### 5. Logs

Stdout and stderr are written to:

```
<REPO_ROOT>/logs/briefing-stdout.log
<REPO_ROOT>/logs/briefing-stderr.log
```

Create the logs directory if missing:

```bash
mkdir -p "${REPO_ROOT}/logs"
```

### 6. Scheduled run time

The agent runs daily at **07:30** local time (Asia/Shanghai for date computation). Output is a physical Markdown file written to both `staging/candidate/` and `AKB2.0/AI TRENDS/Social/`.

## Unload / update

```bash
launchctl unload ~/Library/LaunchAgents/com.akb2.briefing.plist
# ... update plist ...
launchctl load ~/Library/LaunchAgents/com.akb2.briefing.plist
```

## Verification (SC-7/SC-8)

After the first scheduled run, verify output:

```bash
VAULT_ROOT="/Volumes/AKB2/Obsidian"

# Check file was written (physical file, not symlink)
ls -la "$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/Social/"
! test -L "$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/Social/"*.md && echo "OK: physical file"

# Check frontmatter contains gate decision
grep "proposed_gate_decision" "$VAULT_ROOT/Praise Space/AKB2.0/AI TRENDS/Social/"*.md

# Check AKB2 disk path is accessible
ls "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/"
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Agent not running | `launchctl list \| grep com.akb2` |
| Python module not found | Verify `PYTHONPATH=<repo>/src` in plist EnvironmentVariables; also confirm Python 3.11+ |
| Python 3.9 found instead of 3.11+ | `daily_briefing.sh` searches pyenv first; confirm pyenv is installed at `~/.pyenv/` |
| Gate CLI fails | Run `PYTHONPATH=src python3 src/akb2/gate/akb2_gate_check.py check --help` with Python 3.11+ |
| Syncthing/new vault not visible | Open Finder and Obsidian, check `/Volumes/AKB2/Obsidian/` |
| Unresolved placeholder（占位符未替换） | Re-run `sed` step replacing both `__AKB2_REPO_ROOT__` and `__AKB2_VAULT_ROOT__` |

## 首次部署 verify 清单

在 Mac Mini 首次部署时按顺序完成以下所有项目。

| 序 | 检查 | 命令 / 动作 | 失败影响 |
|---|---|---|---|
| B1 | 用户名/HOME 验证 | `echo $HOME` 应为 `/Users/praise`，否则改 plist EnvironmentVariables 中的 PATH 和 find-python311.sh 的 HOME 探测 | pyenv 路径硬编码，HOME 不同即失效 |
| B2 | pyenv 装好 3.11+ | `pyenv versions \| grep "3\.1[1-9]"` | 找不到 Python 整个链路死 |
| B3 | `pip install -e .` 成功 | `pip list \| grep -i akb2` | 模块 import 失败 |
| B3a | defuddle CLI 可用 | `defuddle parse --help`。**权威装法见 `docs/runbooks/intake-tools-setup.md` §6**（含包身份陷阱：`defuddle --version` 打印的 `0.1.0` 是 defuddle-cli 写死的字面量，不是包版本，别照它装包）。CI 与本地基线当前钉 `defuddle-cli@0.7.0`；该包已 deprecated，长期迁移目标 `defuddle@0.19.1`（已实测契约等价，见 runbook §6.5）。 | 主链路抓取层静态页抽取失效，intake 会进入 fallback 或 fail-closed |
| B4 | 时区设置 | `sudo systemsetup -gettimezone` = Asia/Shanghai | 07:30 实际触发时间错 |
| B5 | AKB2 磁盘挂载 + Syncthing folder 配好 + Obsidian 打开新 vault | Finder / Obsidian / Syncthing 检查 | 写入成功但其它 Mac 或 Obsidian 看不到 |
| B6 | VAULT_ROOT 父目录存在且可写 | `test -w "/Volumes/AKB2/Obsidian"` | pipeline fail |
| B7 | X_BEARER_TOKEN 配置 + secrets 文件权限 | `test -f ~/.akb2-secrets.sh \|\| echo "FAIL: missing secrets file"` → `test "$(stat -f '%Lp' ~/.akb2-secrets.sh)" = "600" \|\| echo "FAIL: must be chmod 600"` → `source ~/.akb2-secrets.sh; [[ -n "${X_BEARER_TOKEN:-}" ]] && echo "X_BEARER_TOKEN SET" \|\| echo "FAIL: X_BEARER_TOKEN empty"` | SC-3-real fail；secrets 宽权限 → API key 本机泄露风险 |
| B8 | AI_GATEWAY_API_KEY 配置（chmod 600 已在 B7 验证） | `source ~/.akb2-secrets.sh; [[ -n "${AI_GATEWAY_API_KEY:-}" ]] && echo "AI_GATEWAY_API_KEY SET" \|\| echo "FAIL: AI_GATEWAY_API_KEY empty"` → launchd-like env 验证：`env -i HOME="$HOME" PATH="..." bash -c 'source ~/.akb2-secrets.sh; [[ -n "${AI_GATEWAY_API_KEY:-}" ]] && echo OK'` | SC-5-real fail；synthesis 走 fallback，日志有 WARNING |
| B9 | logs/ 目录预创建 | `mkdir -p $REPO_ROOT/logs` | launchctl load 拒绝（StandardOutPath 不可写） |
| B10 | launchctl load 成功 | `launchctl load ~/Library/LaunchAgents/com.akb2.briefing.plist; launchctl list \| grep akb2` | 未注册 |
| B11 | next fire date ≤ 24h | `launchctl print gui/$(id -u)/com.akb2.briefing \| grep "next fire"` | 时区/Hour/Minute 配置错 |
| B12 | 首次手动触发 dry-run | `launchctl kickstart -k gui/$(id -u)/com.akb2.briefing` 后查日志 | 等 24h 才发现错误 |
| B13 | 真实触发后简报落 AKB2 vault | 隔日检查 `/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/AI TRENDS/Social/<次日> Daily Briefing.md` | 端到端断 |
| B14 | Obsidian 客户端看到该文件 | 打开 `/Volumes/AKB2/Obsidian` vault | Obsidian 未切到新 vault 或 Syncthing 未同步 |
| B15 | 简报内容质量人工抽检 | 打开看 focus_discussions 是否合理 | 真实 API 输出可能跑偏 |

## 已知风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| HOME 不是 /Users/praise | 中（取决于 mac mini 用户名） | find-python311.sh / plist PATH 全部失效 | B1 必检；若不同需改 plist 用 `$HOME` 展开 + find-python311.sh 不硬编码 |
| pyenv 未装 3.11+ | 中 | 整个链路死 | B2 必检 |
| API key 注入方式（Codex P1b） | 高 | secrets 泄漏 / launchd 拿不到 | `daily_briefing.sh` 已在 Python 调用前 source `~/.akb2-secrets.sh`（chmod 600）；不打印 secret 值；B7/B8 有可执行验证命令 |
| Syncthing 同步延迟 | 中 | 文件写了但其它 Mac 隔一段时间才看到 | 已知接受；Obsidian 主动刷新和 Syncthing 状态页可辅助排查 |
| mac mini 睡眠时跳过触发 | 高 | 07:30 当时睡眠 → launchd 醒后补跑 OR 跳过 | 加 `RunAtLoad`/电源设置不睡眠/`StartCalendarInterval` 多次配置 |
| 时区漂移 | 低 | 07:30 偏移 | B4 |
| logs 占用磁盘 | 长期 | 几个月后 GB 级 | logrotate 或 plist 加 size 限制（本轮不做） |

## 第二台机器 / 跨机并跑（2026-07-28 加固）

本文上面的部署方式（第二台机器装同一套 plist、指向同一个 Syncthing vault）会让**两台机器
同时处理同一篇 raw**。必须先读完本节再在第二台机器上启用 `com.akb2.main-pipeline`。

### 为什么锁挡不住

| 组件 | 位置 | 跨机效果 |
|------|------|---------|
| 处理锁 `processing.lock` | 机器本地状态根 `~/Library/Application Support/akb2/locks/` | **无效**：`fcntl.flock` 是内核 inode 级语义，两机各有各的锁目录，根本不抢同一把锁 |
| daemon 单实例锁 `runner.lock` | 同上（2026-07-28 从仓库 `staging/locks/` 迁出） | 同上；迁出的原因是仓库在 `~/AI-Agent`（Syncthing folder）内 |
| 入库账本 `quality-ledger.jsonl` | 机器本地状态根 `.../akb2/ledgers/` | 各机独立 → 一台的"已入库"另一台看不见 |
| raw 队列 | `<vault>/raw`（**在同步范围内**） | 两机都看到同一批待处理 raw |

状态**不能**搬回同步目录：`data/star_history.sqlite` 已实证被 Syncthing 分叉丢数据
（live 独有 690 条 / 冲突副本独有 1378 条）。所以跨机互斥只能靠**不需要共享可写介质**的
手段，即静态分片。

### 方案 A（默认）：同一时刻只允许一台机器跑 runner

不配任何分片时就是这个形态。runner 启动会打印一行跨机风险告警（`grep 跨机风险
logs/akb2-quality-runner.log`）。想让它硬闸，在 plist 的 `EnvironmentVariables` 里加：

```xml
<key>AKB2_RUNNER_REQUIRE_SHARDING</key>
<string>1</string>
```

此时未配分片 → runner 直接 FATAL 退出（`launchctl` 会按 ThrottleInterval 重试并在日志留痕），
不会静默双跑。

### 方案 B：分片并跑（两台都要处理队列时）

队列按 raw **文件名**哈希分成 `RUNNER_SHARD_COUNT=16` 片（`src/akb2/quality/runner.py`
`_shard_of`；用文件名而非绝对路径，两机挂载点不同也不会分叉）。给每台机器配**互不相交**的
分片集合：

| 机器 | plist 里加的 env |
|------|-----------------|
| MacBook | `AKB2_RUNNER_SHARDS=0-7` |
| Mac Mini | `AKB2_RUNNER_SHARDS=8-15` |

写法：`all`（或不设）= 独占全部、`3`、`0-7`、`0,3,5-6`。**非法写法直接 FATAL**，不会静默
退化成"独占全部"。启动日志会打印本机分片，两台核对一次即可确认没有交集。

分片有效性的前提是两台的并集覆盖 0..15；漏配的分片没人处理，只能靠下面的接管。

### 单机故障接管（有界）

某台机器长期离线时它的分片会堆积。在**另外一台且只在一台**机器上开：

```xml
<key>AKB2_RUNNER_SHARD_TAKEOVER</key>
<string>1</string>
```

规则（`SHARD_TAKEOVER_AFTER_SEC = 21600`，即 6h）：外机分片的 raw 文件 mtime 滞留超过 6h
才允许接管，每次接管在日志打印 `接管外机分片项: shard=… 已滞留 …s`。未超时的项绝不抢。

⚠️ **两台都开 takeover = 又变成同时抢同一篇**。接管默认关闭，就是为了让这件事必须显式决定。

### intake 游标仍在仓库内（需人工改 plist）

`launchd/com.akb2.intake.plist` 目前把游标和失败账本写在仓库里，两台机器会互相覆盖
（`reports/intake/` 下已有两个 `sweep-probe-result.sync-conflict-*` 实证）：

```xml
<!-- 现状（第 56-57、61 行附近） -->
<string>--state-file</string>
<string>__AKB2_REPO_ROOT__/reports/intake/intake-state.jsonl</string>
...
<string>--failures-path</string>
<string>__AKB2_REPO_ROOT__/logs/intake-failures.jsonl</string>
```

应改成机器本地状态根占位符（渲染脚本已支持 `__AKB2_STATE_ROOT__`，见
`scripts/bootstrap_launchd.sh`；`scripts/bootstrap_intake_launchd.sh` 的 sed 也要补同一条
替换规则）：

```xml
<string>--state-file</string>
<string>__AKB2_STATE_ROOT__/intake/intake-state.jsonl</string>
...
<string>--failures-path</string>
<string>__AKB2_STATE_ROOT__/intake/intake-failures.jsonl</string>
```

首次改完要把既有游标搬过去（**复制**，别删原文件；丢游标 = 全量重抓）：

```bash
mkdir -p "$HOME/Library/Application Support/akb2/intake"
cp reports/intake/intake-state.jsonl "$HOME/Library/Application Support/akb2/intake/"
```

渲染前可先核对替换结果，不注册任何服务：

```bash
bash scripts/bootstrap_launchd.sh --render-only /tmp/check.plist --plist-src launchd/com.akb2.intake.plist
```

### 相关环境变量汇总

| 变量 | 默认 | 作用 |
|------|------|------|
| `AKB2_STATE_ROOT` | `~/Library/Application Support/akb2` | 机器本地状态根（锁/账本/游标）。指进同步范围（`~/AI-Agent`、iCloud、`/Volumes/AKB2`）即 fail-closed |
| `AKB2_RUNNER_SHARDS` | 未设 = 独占全部 | 本机负责的队列分片 |
| `AKB2_RUNNER_SHARD_TAKEOVER` | `0` | 是否接管超时的外机分片项（最多一台开） |
| `AKB2_RUNNER_REQUIRE_SHARDING` | `0` | 队列在同步范围且未配分片时是否拒绝启动 |
| `AKB2_LEGACY_LOCK_FILE` | `<repo>/staging/locks/runner.lock` | 迁移期旧锁位置；仍有活进程持它时 wrapper 让位 |
