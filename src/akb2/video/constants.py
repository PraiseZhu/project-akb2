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