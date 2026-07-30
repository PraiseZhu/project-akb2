# 视频链路凭证恢复 Runbook

## 何时用这份

`logs/video-blocked.json` 里出现下列任一情形：

| 字段 | 值 | 含义 |
|------|-----|------|
| `reason` | `credential_expired` | 凭证被**探测判死**（yt-dlp 缺失 / cookie 文件不存在 / NLM auth 过期）且症状吻合 |
| `reason` | `credential_suspect` | 只有兜底 cookies 配置 + 症状吻合 → 高度疑似，但未经探测确证 |
| `credential_health.overall` | `dead` / `degraded` | 启动预检发现的凭证问题（即便本轮仍有产出） |

`credential_health.fix_commands` 里就是该跑的命令；本文档给出每条的完整步骤。

> 也可能出现的相邻 reason：`date_unresolvable` / `date_backfill_all_failed` /
> `list_stage_empty`。它们是**症状**（取不到日期 / 列表空），当凭证健康时才会作为
> 真因出现 —— 若同时看到 `credential_health` 块，先按本文档修凭证。

### 2026-07-30 事故就是这个形态

漏斗：源 21 → 列表 246 → 候选 2 → **发布 0**，连续 2 轮 blocked。三处症状同一个根因：

1. 日期补取 11 次里 9 次失败 —— yt-dlp 撞 YouTube 机器人校验
   （`Sign in to confirm you're not a bot`）→ 9 个候选被时间闸 fail-closed 拒收；
2. NLM auth 过期（`profile 'default' 不可用`）→ 研究降级；
3. 降级路径的 yt-dlp 字幕获取撞同一堵墙 → 研究 0/2。

当时落盘的 `reason` 只有 `date_unresolvable`，看不出"凭证已死"。现在这三处会被归成
一条凭证真因并附修复命令。

---

## 1. NLM 登录态恢复（`nlm login`）

**症状**：`credential_health.items.nlm_auth.status = dead`；日志
`健康检查: auth 过期 — profile 'default' 不可用，终端运行: nlm login`。

**必须在交互式终端里做**（会开浏览器走 Google OAuth，agent 无法代劳）：

```bash
# 1. 确认当前登录态（只读，不改任何东西）
nlm login --check

# 2. 重新登录默认 profile（会开浏览器；profile 名取自 config/video_sources.json 的 nlm_profile）
nlm login --profile default

# 3. 复验：能列出 notebook 即恢复
nlm notebook list --profile default | head -3
```

要点：

- profile 名以 `config/video_sources.json` 的 `settings.nlm_profile` 为准（当前 `default`），
  链路的健康检查探的就是这一个。备用 profile 见 `nlm_fallback_profile`。
- 登录态过期**不会**让链路停摆：researcher 会降级到「yt-dlp 字幕 + LLM 摘要」，
  产出带 `> [!warning] 降级研究` 标记、深度不足。降级路径同样需要 §2 的 cookies。
- `network_error`（而非 `auth_expired`）判 `degraded` 不判死 —— 那是网络抖动，
  候选会转 pending 下轮重试，**不要**因此去重新登录。

---

## 2. 导出 YouTube cookies（两种方式）

**症状**：`credential_health.items.ytdlp_cookies` 非 `ok`；日志里成批出现
`发布日期补取无输出 ... 常见真因：平台限频（"Sign in to confirm you're not a bot"）`。

### 方式 A：导出 cookie 文件（推荐，7×24 链路稳定）

浏览器 cookies 会被 macOS 钥匙串锁、浏览器升级后读取失败，导出成文件最稳。

```bash
# 用 yt-dlp 自己从浏览器导出（不需要额外扩展）
yt-dlp --cookies-from-browser chrome --cookies ~/.akb2/yt-cookies.txt \
       --skip-download --no-warnings "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# 权限收紧：cookie = 登录凭证，等同密码
chmod 600 ~/.akb2/yt-cookies.txt

# 验证文件非空且是 Netscape 格式
head -1 ~/.akb2/yt-cookies.txt   # 应为 "# Netscape HTTP Cookie File"
```

也可以用浏览器扩展（如 "Get cookies.txt LOCALLY"）在**已登录 YouTube** 的标签页导出，
存成同样的 Netscape 格式文件。

> cookie 文件绝不入 git（`docs/`、`config/` 下的 `*cookies*.txt` 请自行确认已被
> `.gitignore` 覆盖，或存到仓库外如 `~/.akb2/`）。

### 方式 B：直接从浏览器读

不落文件，但要求该浏览器已登录 YouTube 且未被钥匙串锁住：

```bash
# 先手测能不能读出来（读不出来就用方式 A）
yt-dlp --cookies-from-browser chrome --skip-download --dump-json \
       "https://www.youtube.com/watch?v=jNQXAC9IVRw" | head -c 200
```

支持值：`chrome` / `chromium` / `firefox` / `safari` / `edge` / `brave` / `opera` / `vivaldi`。

### 复验（确认 bot-check 已解）

```bash
# 日期补取用的就是这条 per-video --dump-json，也是最容易撞 bot-check 的一层
yt-dlp --cookies ~/.akb2/yt-cookies.txt --dump-json --skip-download \
       "https://www.youtube.com/watch?v=jNQXAC9IVRw" | python3 -c \
  "import json,sys; print('upload_date =', json.loads(sys.stdin.readline())['upload_date'])"
```

打印出 `upload_date = ...` 即恢复；仍报 `Sign in to confirm you're not a bot` →
cookies 无效（换已登录账号重导，或改用方式 B）。

---

## 3. 配置 env（两个 yt-dlp 调用点统一生效）

链路有**两处** yt-dlp 调用：collector 的列表 + 日期补取、researcher 的字幕降级。
下面的 env 一次配置对两处同时生效（改动前 researcher 完全不带 cookies，
降级路径注定撞同一堵墙）。

**二选一，文件优先**（两个都配会告警后取文件）：

| env | 值 | 对应 yt-dlp 参数 |
|-----|-----|-----------------|
| `AKB2_YTDLP_COOKIES` | cookie 文件绝对路径 | `--cookies <path>` |
| `AKB2_YTDLP_COOKIES_FROM_BROWSER` | 浏览器名，如 `chrome` | `--cookies-from-browser chrome` |

### 3.1 手动 / 交互式运行

```bash
export AKB2_YTDLP_COOKIES="$HOME/.akb2/yt-cookies.txt"
# 或
export AKB2_YTDLP_COOKIES_FROM_BROWSER=chrome
```

### 3.2 生产（launchd 定时任务，每天 02:00）

视频链路由 `~/Library/LaunchAgents/com.akb2.video.plist` 驱动。**launchd 不继承登录
shell 的环境变量**，`.zshrc` 里的 export 对它无效 —— 必须写进 plist 的
`EnvironmentVariables`：

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>AKB2_YTDLP_COOKIES</key>
    <string>/Users/praise/.akb2/yt-cookies.txt</string>
</dict>
```

改完重新加载（**这一步需人工执行**，agent 不动 launchd）：

```bash
launchctl unload ~/Library/LaunchAgents/com.akb2.video.plist
launchctl load  ~/Library/LaunchAgents/com.akb2.video.plist
```

> 该 plist **不在仓库 `launchd/` 内、未入 git**（2026-07-28 查实），改动只存在于本机。

### 3.3 未配置时的行为（改动前行为，保持不变）

两个 env 都不设 → 命令行与改动前逐字一致：

- collector：`config/yt-cookies.txt` 存在且非空 → 用它；否则回退
  `--cookies-from-browser chrome`（来自 `video_sources.json` 的 `ytdlp_cookies_browser`）；
- researcher 字幕降级：**不带任何 cookies 参数**。

所以「什么都不配」是能跑的，只是 cookies 状态会被预检判 `degraded`，一旦出现
补取成批失败就会归因成 `credential_suspect`。

### 3.4 配置写错时会怎样

`AKB2_YTDLP_COOKIES` 指向的文件不存在或为空 → **不注入**该参数（把
`--cookies /nonexistent` 递给 yt-dlp 会让它整体报错，连兜底路径一起打死），
记 ERROR 并把 `ytdlp_cookies.status` 标为 `dead`、`source` 标为 `env_file_missing`。

---

## 4. 修完之后

```bash
# 只验凭证与链路装配，不真跑全量采集（不烧 NLM / LLM 额度）
VAULT_ROOT=/Volumes/AKB2/Obsidian python3 -m akb2.video --dry-run --fixture
```

真实恢复验证等下一次 02:00 定时跑，然后看：

```bash
python3 -c "import json;d=json.load(open('logs/video-blocked.json'));\
print(d['status'], d.get('reason',''), d.get('credential_health',{}).get('overall',''))"
```

`status=ok`（或 `zero_yield` 且无 `credential_health` 块）= 凭证已恢复。

> 手动补跑全量链路（`python3 -m akb2.video`）会真花 NLM 深研 + LLM 额度，
> 且 `nlm_daily_quota=15` 是日硬上限 —— 只在确有必要时人工执行。

---

## 相关

- 归因与落盘实现：`src/akb2/video/blocked.py`、`src/akb2/video/external_credentials.py`
- 回归测试：`tests/test_video_credentials.py`
- yt-dlp / defuddle 等外部 CLI 的安装：`docs/runbooks/intake-tools-setup.md`
