"""视频管线常量 — Video pipeline constants.

从旧 AKB scripts/utils/constants.py 迁移，独立于 CatalogDB。
"""

# 视频采集预算（秒）：每天最多花这么多时间采集
VIDEO_BUDGET_SECONDS = 900  # 15 分钟

# 回溯天数：采集近 N 天的视频
VIDEO_DAYS_LOOKBACK = 14

# 视频时长限制（分钟）
VIDEO_MIN_DURATION_MIN = 3
VIDEO_MAX_DURATION_MIN = 0  # 0 = 不限

# NLM 研究最大等待时间（秒）
VIDEO_NLM_MAX_WAIT = 900  # 15 分钟

# 永久失败阈值：retry_count 达到此值后归档
VIDEO_PERMANENT_FAILURE_THRESHOLD = 5

# pending_research.json 路径（相对于项目根，由 config 注入可覆盖）
VIDEO_PENDING_FILENAME = "pending_research.json"

# 去重账本文件名（落在机器本地状态根，见 common/state_paths；缺陷 #2）
VIDEO_DEDUP_DB_FILENAME = "video-dedup.sqlite"

# NLM 每日深研配额硬上限（缺陷 #12：原来只在 backfill 分支被读，从不作为上限生效）
VIDEO_NLM_DAILY_QUOTA = 15

# 单轮最多为多少个候选补取真实发布日期（yt-dlp per-video 调用，有界；缺陷 #12）
VIDEO_MAX_DATE_BACKFILL_PER_RUN = 40

# 补取发布日期时的请求间隔（秒），避免触发平台限频
VIDEO_DATE_BACKFILL_SLEEP_SEC = 1.2

# ---------------------------------------------------------------------------
# 已发布产出的身份索引（残余风险「视频存量覆盖」：608 份历史产出全是旧命名，
# 文件名层的 fail-closed 对它们恒不命中；见 published_index.py 模块 docstring）
# ---------------------------------------------------------------------------

# 单次索引最多扫描的 markdown 份数（有界，宪法 P1）。目录现状 608 份，留足余量；
# 超过即不完整并打 ERROR，不静默截断。
PUBLISHED_INDEX_MAX_FILES = 5000

# 每份产出读取的字节上限（只要 frontmatter，正文不需要）
PUBLISHED_INDEX_HEAD_BYTES = 4000

# 索引缓存有效期（秒）：一次采集轮的总预算是 VIDEO_BUDGET_SECONDS=900s，取 600s
# 使单轮内最多重建一次；本进程自己发布的产出走 `mark_published()` 增量并入，
# 不依赖 TTL 过期。禁止的形态是"每个候选全目录重扫 608 份"。
PUBLISHED_INDEX_TTL_SEC = 600.0

# ---------------------------------------------------------------------------
# 短链展开（残余风险 2：b23.tv/<code> 解析不到 BV 号 → 与展开后的完整 URL 分裂成
# 两个身份）。展开只在**采集阶段**做（有网络、每个候选一次），`video_identity`
# 保持纯函数。
# ---------------------------------------------------------------------------

# 单次展开的网络超时（秒）
VIDEO_SHORTLINK_TIMEOUT_SEC = 5.0

# 单个短链最多跟随的重定向跳数（有界，防重定向环）
VIDEO_SHORTLINK_MAX_HOPS = 3

# 单轮最多展开多少个短链（有界预算，宪法 P1）
VIDEO_SHORTLINK_MAX_PER_RUN = 40
