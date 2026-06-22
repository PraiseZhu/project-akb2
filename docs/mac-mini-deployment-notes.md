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
