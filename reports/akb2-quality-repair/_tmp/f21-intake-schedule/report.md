# F21 intake launchd 定时器报告

工单 F21:给 intake 抓取链路装 launchd 定时器(每日自动抓源落 raw)。分支 `feat/intake-launchd-schedule`(worktree `_akb2-intake-schedule-wt`,从 origin/main 58c573d 开)。未 push、未建 PR、**未装载**(装载由 lead 做)。

## 1. state-file 落点考证(6-29 那次的痕迹)

- **state-file 现状**:`reports/intake/intake-state.jsonl` 存在(31 行,Jun 30 02:25 更新),last 条目字段 `article_id/daily_count/last_fetch_at/quarantine_status/run_id`——**6-29 那次跑的 state 就落在此**(orchestrator 默认 `DEFAULT_STATE_FILE=reports/intake/intake-state.jsonl`)。
- **6-29 vault raw 痕迹**:`/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/` 有 6 个 `langchain-blog--*_raw--2026-06-29.md`——6-29 跑用了 vault raw(非默认 output-dir `reports/intake/raw/`)。
- **续用决策**:state-file 续用 `reports/intake/intake-state.jsonl`(repo 内,`.gitignore` 但磁盘持久化,保 dedup 连续性,绝不在 /tmp);output-dir 用 vault raw `__AKB2_VAULT_ROOT__/Praise Space/AKB2.0/raw`(与 6-29 同);failures-path 落 `__AKB2_REPO_ROOT__/logs/intake-failures.jsonl`。

## 2. plist + 安装脚本设计说明(占位符/预算/调度)

### `launchd/com.akb2.intake.plist`(抄 briefing/main-pipeline 家法)

- **Label**:`com.akb2.intake`
- **ProgramArguments**:`__AKB2_HOME__/.pyenv/shims/python3 -m akb2.quality.intake.orchestrator --sources-config __AKB2_REPO_ROOT__/config/main_sources.yaml --state-file __AKB2_REPO_ROOT__/reports/intake/intake-state.jsonl --output-dir __AKB2_VAULT_ROOT__/Praise Space/AKB2.0/raw --failures-path __AKB2_REPO_ROOT__/logs/intake-failures.jsonl --limit 30`
  - **有界(宪法 P1)**:`--limit 30` 每次最多抓 30 篇(跨 164 源),超时由 launchd/脚本侧兜底,无界跑不允许
- **WorkingDirectory**:`__AKB2_REPO_ROOT__`
- **StartCalendarInterval**:`Hour=5 Minute=30`(Asia/Shanghai 05:30 CST = UTC 21:30 前日,避开 briefing 07:30;抓完料 main-pipeline daemon KeepAlive 自然消化)
  - **注意**:与 github_briefing 05:30 同时(两者抓取链路独立,网络并行可接受;main-pipeline AVOID_WINDOW 21:15-21:45 UTC 内暂停取新队列,21:45 后消化 intake 料)——lead 设计钉死 05:30,我按工单,此处列出供 lead 验收确认
- **RunAtLoad**:`false`(首跑由 lead 装载后手动 kickstart 验证,不搞装载即自动跑)
- **StandardOutPath/StandardErrorPath**:`__AKB2_REPO_ROOT__/logs/intake.log`(家法同现有 plist)
- **EnvironmentVariables**:`PYTHONPATH=__AKB2_REPO_ROOT__/src` + `PATH=__AKB2_HOME__/.pyenv/shims:__AKB2_HOME__/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`(用 `__AKB2_HOME__` 占位,家法同 main-pipeline,比 briefing 硬编码 /Users/praise 更好) + `AKB2_VAULT_ROOT=__AKB2_VAULT_ROOT__/Praise Space/AKB2.0`
- **占位符**:`__AKB2_REPO_ROOT__` / `__AKB2_VAULT_ROOT__` / `__AKB2_HOME__`(3 个,由 bootstrap 脚本 sed 替换)
- **用户授权**:注释写明"用户新增自动化触发器已明确批准"(宪法"新增自动化触发器必须人工确认"已满足)

### `scripts/bootstrap_intake_launchd.sh`(抄 bootstrap_launchd.sh 结构)

- **preflight(fail-closed)**:
  - plist 源存在 → 否则 exit 3
  - `command -v defuddle` → 否则 exit 3(orchestrator 必需软依赖)
  - `PYTHONPATH=src python3 -c "import akb2.quality.intake.orchestrator"` → 否则 exit 3
  - playwright 检查:缺失只 warn 不 fail(软依赖,装载阶段 lead 处理)
- **render**:`sed` 替换 3 占位符 → `plutil -lint` 验证 XML 合法 → 否则 exit 3
- **bootout**:`launchctl bootout gui/$(id -u)/com.akb2.intake`(ignore errors,幂等)
- **bootstrap**:`launchctl bootstrap gui/$(id -u) $PLIST_TMP`
- **verify**:`launchctl print gui/$(id -u)/com.akb2.intake | grep -E 'intake\.orchestrator|hour = 5'`(断言绑本 plist 可观测特征) → 否则 exit 2
- **脚本只写不跑**:worker 不装载,lead 装载时执行

## 3. 测试清单 + 结果

新增 `tests/test_intake_launchd.py`(17 测试):

| 类 | 测试 | 覆盖 |
|----|------|------|
| TestPlistValid | test_plist_xml_valid | plist XML 合法(plistlib,等价 plutil -lint) |
| TestPlistValid | test_plist_label | Label=com.akb2.intake |
| TestPlistValid | test_plist_placeholders_present | 含 3 占位符 |
| TestPlistValid | test_plist_no_hardcoded_user_paths | 不含 /Users/praise /Volumes |
| TestPlistValid | test_plist_schedule_0530_cst | Hour=5 Minute=30 |
| TestPlistValid | test_plist_run_at_load_false | RunAtLoad=false |
| TestPlistValid | test_plist_program_args_orchestrator_and_limit | orchestrator + --limit 30 |
| TestPlistValid | test_plist_program_args_paths_placeholders | sources/state/output/failures 路径 |
| TestPlistValid | test_plist_state_file_persistent_not_tmp | state-file 不在 /tmp,落 reports/intake/ |
| TestPlistValid | test_plist_log_paths | 日志落 logs/intake.log |
| TestPlistValid | test_plist_env_pythonpath_and_path | PYTHONPATH=src + PATH 用 __AKB2_HOME__ |
| TestBootstrapScript | test_script_bash_n_syntax | 脚本 bash -n 语法合法 |
| TestBootstrapScript | test_script_executable_bit | 脚本有可执行位 |
| TestBootstrapScript | test_script_preflight_defuddle_missing_fail_closed | defuddle 缺失 → exit 3(fail-closed) |
| TestBootstrapScript | test_script_preflight_plist_missing_fail_closed | plist 源缺失检查代码在 preflight |
| TestBootstrapScript | test_script_renders_placeholders | sed 替换 3 占位符 + 渲染后仍合法 plist |
| TestBootstrapScript | test_script_has_bootout_bootstrap_verify | 含 bootout+bootstrap+launchctl print+orchestrator+hour=5 |

**全量 pytest -m 'not integration'**:**2277 passed, 16 skipped**(2260 + 17 新)。ruff 改动文件全过。`plutil -lint launchd/com.akb2.intake.plist` → OK。

## 4. lead 装载步骤清单(含 playwright 是否必须)

1. **(可选)装 playwright chromium**(StealthyFetcher 软依赖):
   - `pip install playwright && python3 -m playwright install chromium`
   - **是否必须**:164 源里若有需 JS 渲染的(SPA),StealthyFetcher 需 chromium;静态 HTML 源用 Fetcher(scrapling)够。按需装——缺失时该 fetcher 不可用,preflight 只 warn 不 fail
2. **跑安装脚本**(render + bootout + bootstrap + verify):
   - `bash scripts/bootstrap_intake_launchd.sh`
   - 验证:stdout 含 `intake.orchestrator detected + hour = 5`
3. **手动 kickstart 首跑**(RunAtLoad=false,不搞装载即自动跑):
   - `launchctl kickstart -k gui/$(id -u)/com.akb2.intake`
4. **看日志**:`tail -f logs/intake.log`(stdout 报 discovered/skipped/archived 计数)
5. **看 state**:`reports/intake/intake-state.jsonl`(dedup 连续性)
6. **看 raw**:`/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/`(新抓的 raw)
7. **失败排查**:discovered=0 且 discovery_errors>0 时 orchestrator exit 1(全源死链不算成功,宪法 P4);failures 落 `logs/intake-failures.jsonl`

## 5. 范围外(如实列)

- **不动**:rubric/admission/五闸/runner/main-pipeline.plist/briefing.plist
- **不碰**:live daemon(重启后的 PID 71777)/plist/AUTO_PROMOTE
- **不自行**:bootstrap/装载任何 plist(脚本只写不跑,装载由 lead)
- **不装**:全局依赖(playwright chromium 装载阶段 lead 处理)
- **不跑**:真实抓取(网络抓取由 lead 首跑验证)
- **05:30 与 github_briefing 同时**:lead 设计钉死,我按工单;若 lead 验收时认为该错峰,改 Hour=4 或 Hour=6 即可( plist 单点改)
- **state-file 在 repo 内(.gitignore)**:reports/intake/ 磁盘持久化但 git 不跟踪;若 lead 认为该放 vault staging 保跨机持久,改 plist `--state-file` 路径即可

## 6. 一句总判

intake launchd 定时器 + 安装脚本按仓库家法实现(plist 3 占位符 + 05:30 CST 调度 + --limit 30 有界 + RunAtLoad=false + preflight defuddle/import fail-closed + render+bootout+bootstrap+verify),state-file 续用 reports/intake/intake-state.jsonl 保 dedup 连续性(6-29 跑同路径),17 新测试 + 全量 2277 passed + plutil -lint OK;lead 装载步骤含 playwright 软依赖说明 + 手动 kickstart 首跑;未 push 未建 PR 未装载,等 lead 验收 + 装载。
