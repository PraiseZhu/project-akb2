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
