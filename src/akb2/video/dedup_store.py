"""视频去重库 — VideoDedupStore（L1/L7 去重的读端 + 写端）。

缺陷 #2（2026-07-26）修复背景：
    `collector.py` 的 L1（URL 去重）与 L7（catalog 去重）**键是对的、逻辑是对的，
    但两端都空转**：

    1. 生产唯一构造点 `video/__main__.py` 从不传 `db` → `if not self.db: return False`
       直接放行，两层去重等于不存在；
    2. 即使传了 db 也仍然无效——本链路**从来没有写端**（`grep INSERT src/akb2/video/`
       为空），去重表永远读不到自己的产出。

    实测代价（2026-07-26 只读统计 `/Volumes/AKB2/.../AI TRENDS/Video`，608 份 md）：
        产出 608 份 → 只有 230 个平台身份，即 271 份是重复产出（62%）；
        单个视频最高 26 份（`youtube:EWvNQjAaOHw`）。
        只看 `source_url:` 键那一子集是 325 份 → 68 个身份（重复 79.1%，最高 25 份）
        —— 早期注释写的"324 份 / 68 个 / 79%"是这个**子集**口径，不是整目录。
        NotebookLM：245 份笔记记了 `notebook_id`，245 个 id 互不相同、只覆盖 175 个
        身份 → **至少 70 个** notebook 是给已研究过的视频重开的（下界：只有 245/608
        份笔记记了 notebook_id，其余无从统计）。
    LLM 调用次数**无遥测、不可考**：本链路从未记录调用计数，任何具体次数（早期注释
    写的"≈1766 次"）都不是实测值。按代码路径能给的下界是 271 份冗余产出 ×
    （extractor 1 次 + reasoner 1 次）≈ 542 次，仅为推算下界，不作为实证数据引用。

本模块是这条链路唯一的去重账本，同时提供读端与写端：

    video_identity(url)                 -> str    稳定身份（平台原生 id，兜底 sha256）
    VideoDedupStore.is_ingested(url)    -> bool   L1/L7 读端
    VideoDedupStore.mark_ingested(...)  -> bool   写端（产出落地后调用）
    VideoDedupStore.count_ingested_since(...)     日配额硬上限用（缺陷 #12）
    VideoDedupStore.seed_from_published(dir)      一次性回填历史产出，避免已发布视频重跑

设计约束：

- **去重键绝不用标题或 slug**（缺陷 #10：标题由 LLM 产出、会被改写甚至翻转），
  只用平台原生 videoId；无法解析平台 id 时退到 `sha256(规范化 url)`。
- **fail-closed（宪法 P3）**：库不可用（损坏 / 权限 / 磁盘）时抛
  `VideoDedupStoreError`，绝不 `return False` 静默放行——静默放行的代价就是
  上面那 271 份冗余产出（每份都重跑一遍 extractor/reasoner，并可能重开一个
  NotebookLM notebook）。
- 状态文件走 `common/state_paths`（机器本地状态根，避开 Syncthing 同步范围，
  缺陷 #11），不落在 vault 或仓库里。
- 写入用 `INSERT OR IGNORE` + `identity` 主键，天然幂等（重复标记不报错、不重复计数）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("akb2.video.dedup_store")

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4：预算 / 限额显式）
# ---------------------------------------------------------------------------

# sqlite busy timeout（秒）：与 launchd backstop 并发触发时不立刻 database is locked
DEDUP_DB_TIMEOUT_SEC = 10.0

# seed_from_published 单次最多扫描的 markdown 文件数（有界，宪法 P1）
DEDUP_SEED_MAX_FILES = 5000

# seed_from_published 每份笔记读取的字节上限（frontmatter 足够，正文不需要）
DEDUP_SEED_HEAD_BYTES = 4000

# 已 seed 标记在 meta 表中的 key 前缀
_SEED_META_KEY_PREFIX = "seeded_from:"

# 回填条目在文件 mtime 不可读时使用的**历史哨兵**时间戳。
# 缺陷 V-1：原实现 `except OSError: return datetime.now(UTC)` 会把历史条目打上
# "今天"，于是回填自己吃掉今日 NLM 配额 —— 恰好是 `_file_stamp` 注释声称要避免
# 的事。哨兵值远早于任何真实运行日，永远不会落进"今日已入库"的统计窗口。
DEDUP_SEED_HISTORICAL_SENTINEL = "1970-01-01T00:00:00+00:00"

# frontmatter 里承载视频 URL 的键名判定（缺陷 V-1）。
#
# 实测 `/Volumes/AKB2/.../AI TRENDS/Video`（608 份 md，只读统计，2026-07-28 复核）：
#   source_url:         325 份 →  68 个平台身份（其中仅 14 个与 video_url 重叠）
#   video_url:          173 份 → 172 个平台身份
#   url:                 51 份 →   2 个平台身份
#   original_url:         1 份 →   0 个平台身份（值不是视频链接）
#   video_source:         1 份 →   1 个平台身份
#   x_url:                1 份 →   0 个平台身份（值不是视频链接）
#   original_talk_url:    1 份 →   1 个平台身份
#   seed:                 1 份 →   1 个平台身份（2026-07-28 新发现，见下）
#   union                            231 个平台身份
# 原实现只认 `^source_url:` → 只覆盖 68 个，漏掉 162 个（漏的比覆盖的多一倍多）。
# 所以判定不写死键名清单，而是按"键名含 url" + 一个显式补充集合，
# 让将来新出现的键名自动被覆盖（漏认的代价是重复烧 NLM 配额，
# 多认的代价只是同目录里的一条链接被当成已研究 —— 不对称，取宽）。
#
# 2026-07-28 复核补 `seed`：上一轮声称"从 1 种键扩到 7 种"，但按**值**（而不是键名）
# 重扫真实目录发现第 8 种键 —— `seed: https://www.youtube.com/watch?v=3DlXq9nsQOE`
# （《Harness Engineering 深度评估 - 研究报告 2026-04-21.md》，该笔记的产出正是从这个
# 视频来的）。键名既不含 "url" 也不在补集里 → 该身份漏认，账本 230 个身份 vs 目录实际
# 231 个。判别命令写成了测试：`test_seed_covers_every_url_key_in_real_vault`
# （integration，只读扫真实目录，按值找的身份集合必须被键名判定全覆盖）。
_SEED_EXTRA_URL_KEYS = frozenset({"video_source", "seed"})

# frontmatter 里的标量键行：`key: value`（value 不含空白，即单个 URL）
_FRONTMATTER_SCALAR_RE = re.compile(
    r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*):[ \t]*(?P<value>\S+)[ \t]*$", re.MULTILINE
)


def _is_url_key(key: str) -> bool:
    """该 frontmatter 键是否可能承载 URL。"""
    return "url" in key.lower() or key in _SEED_EXTRA_URL_KEYS


def _frontmatter_block(text: str) -> str:
    """截出开头的 `---` frontmatter 块；没有则返回空串。

    只扫 frontmatter，避免正文里的 `http://...` 行被当成身份键。
    """
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end > 0 else text[3:]


def extract_video_identities(text: str) -> dict[str, str]:
    """从一份已发布笔记的文本里取出所有**平台原生**视频身份 → 原始 URL。

    只认能解析出 ``youtube:`` / ``bilibili:`` 的值；``sha256:`` 兜底身份一律丢弃
    —— 同目录下还有 essay / 论文 / 推文类笔记，把它们的链接塞进视频账本会让
    真视频被误判已入库。
    """
    found: dict[str, str] = {}
    for match in _FRONTMATTER_SCALAR_RE.finditer(_frontmatter_block(text)):
        if not _is_url_key(match.group("key")):
            continue
        value = match.group("value").strip().strip("\"'")
        try:
            identity = video_identity(value)
        except ValueError:
            continue
        if identity.startswith("sha256:"):
            continue
        found.setdefault(identity, value)
    return found


class SeedOutcome(NamedTuple):
    """``seed_from_published`` 的结果（部分失败必须可见，宪法 P4）。

    Attributes:
        seeded: 本次新登记的身份数。
        scanned: 本次实际读成功的笔记数。
        files_total: 目录里的 markdown 总数（截断前）。
        identities_found: 扫到的去重后平台身份数。
        read_failures: 读取失败的笔记数（这些笔记的身份没进账本）。
        truncated: 是否被 ``DEDUP_SEED_MAX_FILES`` 截断。
        already_seeded: 该目录之前已完整 seed 过，本次直接跳过。
        dir_unavailable: 目录不存在（vault 未挂载）。
        marked_complete: 是否写入了 ``seeded_from:`` 标记（= 本次完整成功）。
    """

    seeded: int = 0
    scanned: int = 0
    files_total: int = 0
    identities_found: int = 0
    read_failures: int = 0
    truncated: bool = False
    already_seeded: bool = False
    dir_unavailable: bool = False
    marked_complete: bool = False

    @property
    def complete(self) -> bool:
        """本次扫描是否覆盖了目录里每一份笔记。"""
        return (
            not self.dir_unavailable
            and not self.truncated
            and self.read_failures == 0
        )

    @property
    def coverage(self) -> float:
        """读成功笔记数 / 目录 markdown 总数（1.0 = 全覆盖）。"""
        if self.files_total <= 0:
            return 1.0
        return self.scanned / self.files_total


def local_day_start_utc_iso(now: datetime | None = None) -> str:
    """"本地日起点"换算成 UTC ISO8601 字符串（日配额比对基准，缺陷 V-3）。

    ``mark_ingested`` 存的是 ``datetime.now(UTC).isoformat()``；用
    ``date.today().isoformat()``（本地日）去比对是**两个不同时间基准**：
    CST(UTC+8) 下本地 00:00–08:00 入库的条目 UTC 日期还是前一天，字典序小于
    本地日字符串 → 同一本地日后续轮次数不到它们 → 当日实际入库量 = 配额 + 漏算数。

    这里把本地日的 00:00 换算成 UTC 时刻，两侧统一到 UTC 基准后再比字典序
    （ISO8601 带固定 ``+00:00`` 偏移时字典序 == 时间序）。
    """
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    local_midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC).isoformat()

# 平台 host → 平台名
_YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
})
_BILIBILI_HOSTS = frozenset({
    "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv",
})

# bilibili 原生 id：BV 号或 av 号
_BILI_ID_RE = re.compile(r"^(BV[0-9A-Za-z]{8,12}|av\d+)$")

# sha256 兜底身份归一化时剥离的**跟踪参数**（第二轮复核指出的身份缝隙）。
#
# 兜底路径原来把 query 整串拼进归一化串：同一个视频页，一次带
# `?utm_source=newsletter`、一次不带，就算出两个身份 → 去重对兜底身份失效
# （平台 id 路径不受影响，那条路只取 `v=` / BV 号）。
#
# 取舍是不对称的：**少剥**只多烧一次额度，**多剥**会把两个真不同的 URL 并成一个
# 身份、让新视频被误判已入库（丢内容）。所以只列语义上确定"只标记来源/播放位置"
# 的键，不含任何可能选择内容的键（如 `id` / `p` / `page` / `q` / `source`）。
#
# 2026-07-28 复核补 `start` / `end` / `time_continue`：`t` / `start_time` / `timestamp`
# 上一轮已剥（`?t=120` 现在不分裂身份，见参数化测试），但同为"播放位置"的另外三种写法
# 还在分裂 —— YouTube 的 `?start=`/`?end=`（嵌入播放器起止秒）与 `?time_continue=`
# （续播位置）都只指定从哪一秒放，不选择内容。反向护栏见
# `test_identity_fallback_keeps_content_selecting_params`：`id`/`p`/`page`/`v`/`q`/
# `ep_id`/`cid`/`list` 逐个钉住"不得被剥"（剥了会把两个真不同的 URL 并成一个身份 →
# 新视频被误判已入库 = 丢内容，比多烧一次额度严重得多）。
_TRACKING_QUERY_PREFIXES: tuple[str, ...] = ("utm_", "spm_", "share_", "from_")
_TRACKING_QUERY_KEYS = frozenset({
    "spm", "fbclid", "gclid", "msclkid", "igshid", "si", "feature",
    "ref", "referrer", "referer", "from", "vd_source", "unique_k",
    "buvid", "seid", "t", "start_time", "timestamp",
    "start", "end", "time_continue",
})


def _is_tracking_param(key: str) -> bool:
    """该 query 键是否只承载来源/播放位置（可从身份里剥掉）。"""
    lowered = key.lower()
    return lowered in _TRACKING_QUERY_KEYS or lowered.startswith(
        _TRACKING_QUERY_PREFIXES
    )


def _canonical_query(query: str) -> str:
    """剥掉跟踪参数并排序，得到与参数顺序/装饰无关的 query 串。"""
    kept = sorted(
        (k, v)
        for k, values in parse_qs(query, keep_blank_values=True).items()
        if not _is_tracking_param(k)
        for v in values
    )
    return "&".join(f"{k}={v}" for k, v in kept)


class VideoDedupStoreError(RuntimeError):
    """去重库不可用（fail-closed，不得降级为"未入库"放行）。"""


# ---------------------------------------------------------------------------
# 稳定身份
# ---------------------------------------------------------------------------


def video_identity(url: str) -> str:
    """把视频 URL 归一为稳定身份键。

    返回值形如 ``youtube:M_AmPWmkpwA`` / ``bilibili:BV1xx411c7mD`` /
    ``sha256:<64hex>``（无法解析平台原生 id 时的兜底）。

    只依赖 URL——**不碰标题、不碰 slug、不碰 LLM 产出**（缺陷 #10 实证 LLM 会
    改写标题，用标题做键等于没有键）。

    sha256 兜底会先剥掉跟踪参数并排序剩余参数（见 `_canonical_query`），所以
    `?utm_source=` / 参数换序不会把同一页分裂成两个身份。

    **已知残余缝隙（未修，需网络）**：`https://b23.tv/<code>` 这类短链不含 BV 号，
    解析不到平台 id → 落到 sha256 兜底，与展开后的完整 bilibili URL 是两个身份。
    修它需要真发一次 HTTP 请求跟随重定向（本函数当前是纯函数、无 IO，且在
    collector 热路径上被逐候选调用），因此本轮只登记不修。

    Raises:
        ValueError: url 为空。空 URL 说明上游元数据缺失，静默给个假身份会让
            两个不同视频撞成一个（宪法 P4 失败可见）。
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("video_identity: url 为空，无法计算稳定身份")

    parsed = urlparse(cleaned if "//" in cleaned else f"https://{cleaned}")
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if host in _YOUTUBE_HOSTS:
        video_id = ""
        if host.endswith("youtu.be"):
            video_id = path.strip("/").split("/")[0]
        else:
            qs = parse_qs(parsed.query)
            video_id = (qs.get("v") or [""])[0]
            if not video_id:
                # /shorts/<id>、/live/<id>、/embed/<id>
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed", "v"}:
                    video_id = parts[1]
        if video_id:
            return f"youtube:{video_id}"

    if host in _BILIBILI_HOSTS:
        parts = [p for p in path.split("/") if p]
        for part in parts:
            if _BILI_ID_RE.match(part):
                return f"bilibili:{part}"

    # 兜底：规范化 URL 的 sha256（去 scheme 大小写差异、尾斜杠、跟踪参数、参数顺序）
    normalized = f"{host}{path.rstrip('/')}"
    canonical_query = _canonical_query(parsed.query)
    if canonical_query:
        normalized = f"{normalized}?{canonical_query}"
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


# ---------------------------------------------------------------------------
# 去重库
# ---------------------------------------------------------------------------


class VideoDedupStore:
    """视频去重账本（sqlite）。读端给 L1/L7，写端给产出落地后的身份登记。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._schema_ready = False

    # ------------------------------------------------------------------
    # 连接与 schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """打开连接并确保 schema 就绪。

        Raises:
            VideoDedupStoreError: 建目录 / 连接 / 建表失败（fail-closed）。
        """
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=DEDUP_DB_TIMEOUT_SEC)
            conn.row_factory = sqlite3.Row
        except (OSError, sqlite3.Error) as exc:
            raise VideoDedupStoreError(
                f"视频去重库不可用: {self.db_path}: {exc}；"
                "拒绝按「未入库」继续（fail-closed），否则会重复烧 LLM/NLM 额度"
            ) from exc

        if not self._schema_ready:
            try:
                with conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS video_ingested ("
                        "  identity TEXT PRIMARY KEY,"
                        "  video_url TEXT NOT NULL,"
                        "  platform TEXT,"
                        "  title TEXT,"
                        "  output_path TEXT,"
                        "  ingested_at TEXT NOT NULL"
                        ")"
                    )
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS dedup_meta ("
                        "  key TEXT PRIMARY KEY,"
                        "  value TEXT NOT NULL"
                        ")"
                    )
            except sqlite3.Error as exc:
                conn.close()
                raise VideoDedupStoreError(
                    f"视频去重库 schema 初始化失败: {self.db_path}: {exc}"
                ) from exc
            self._schema_ready = True
        return conn

    # ------------------------------------------------------------------
    # 读端
    # ------------------------------------------------------------------

    def is_ingested(self, url: str) -> bool:
        """该视频身份是否已入库（L1/L7 读端）。

        Raises:
            VideoDedupStoreError: 库不可用（不降级放行）。
            ValueError: url 为空。
        """
        identity = video_identity(url)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM video_ingested WHERE identity = ?", (identity,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise VideoDedupStoreError(
                f"视频去重库查询失败 (identity={identity}): {exc}"
            ) from exc
        finally:
            conn.close()
        return row is not None

    def count_ingested_since(self, since_iso: str) -> int:
        """统计 ``ingested_at >= since_iso`` 的入库条数（日配额硬上限用）。

        ``ingested_at`` 存 ISO8601 字符串，字典序与时间序一致，所以传
        ``"2026-07-26"`` 即"今天已入库多少"。
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM video_ingested WHERE ingested_at >= ?",
                (since_iso,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise VideoDedupStoreError(f"视频去重库计数失败: {exc}") from exc
        finally:
            conn.close()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # 写端
    # ------------------------------------------------------------------

    def mark_ingested(
        self,
        url: str,
        *,
        platform: str = "",
        title: str = "",
        output_path: str | Path | None = None,
        ingested_at: str | None = None,
    ) -> bool:
        """登记"该视频的产出已落地"（写端）。

        幂等：同一 identity 重复登记返回 False，不报错、不覆盖首次记录。

        调用时机契约（重要）：**必须在产出真正落地之后调用**，绝不提前。
        - 先登记后落地：崩溃窗口里视频被永久标记已处理 → 报告永远不会产出（丢内容）。
        - 先落地后登记：崩溃窗口里最多下一轮重跑一次（多花一次额度，内容不丢）。
        选后者，并在登记失败时把错误暴露到调用方（见 `assembler.run_pipeline`）。

        Returns:
            True 新登记，False 之前已登记过。
        """
        identity = video_identity(url)
        stamp = ingested_at or datetime.now(UTC).isoformat()
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO video_ingested "
                    "(identity, video_url, platform, title, output_path, ingested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        identity,
                        url,
                        platform,
                        title,
                        str(output_path) if output_path is not None else None,
                        stamp,
                    ),
                )
                inserted = cur.rowcount > 0
        except sqlite3.Error as exc:
            raise VideoDedupStoreError(
                f"视频去重库写入失败 (identity={identity}): {exc}；"
                "产出已落地但账本未记，下轮会重跑该视频"
            ) from exc
        finally:
            conn.close()

        if inserted:
            logger.info("[去重库] 已登记: %s (%s)", identity, title or url)
        return inserted

    # ------------------------------------------------------------------
    # 历史回填
    # ------------------------------------------------------------------

    def seed_from_published(self, published_dir: str | Path) -> SeedOutcome:
        """从已发布目录一次性回填历史身份（幂等，按目录记 seeded 标记）。

        没有这一步，修复上线后账本是空的 → 每一个**已经发布过**的视频都会被再跑
        一遍（再烧一份 NLM 深研配额）。只读扫描 frontmatter 里所有可能承载 URL 的
        键（见 `_is_url_key` 的实测键分布），不写、不改、不删 vault 里任何文件。

        只回填能解析出平台原生 id 的条目（``youtube:`` / ``bilibili:``），
        避免把同目录下的非视频笔记（论文、essay）也当成视频身份。

        部分失败可见（宪法 P4，缺陷 V-1）：只有**完整成功**（目录可用 + 未被
        `DEDUP_SEED_MAX_FILES` 截断 + 零读取失败）才写 ``seeded_from:`` 标记。
        原实现无条件写标记 → 一次部分扫描就把窗口永久关上，日志却打"新登记 N 个"
        像是成功。现在部分失败会打 ERROR 并保留重试机会。

        Returns:
            SeedOutcome（含覆盖率、读取失败数、是否写入标记）。
        """
        directory = Path(published_dir)
        meta_key = f"{_SEED_META_KEY_PREFIX}{directory}"

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM dedup_meta WHERE key = ?", (meta_key,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise VideoDedupStoreError(f"视频去重库 meta 查询失败: {exc}") from exc
        finally:
            conn.close()
        if row is not None:
            return SeedOutcome(already_seeded=True)

        if not directory.is_dir():
            # 不写 seeded 标记：vault 未挂载只是暂时不可用，下轮还要再试。
            logger.warning(
                "[去重库] 历史回填跳过：已发布目录不可用 %s —— "
                "本轮无历史身份保护，已发布视频可能被重跑",
                directory,
            )
            return SeedOutcome(dir_unavailable=True)

        all_files = sorted(directory.glob("*.md"))
        files_total = len(all_files)
        truncated = files_total > DEDUP_SEED_MAX_FILES
        files = all_files[:DEDUP_SEED_MAX_FILES]

        seeded = 0
        scanned = 0
        read_failures = 0
        # identity → (原始 url, 出处笔记)。同一身份在多份笔记/多个键里出现时留首见。
        identities: dict[str, tuple[str, Path]] = {}
        for md in files:
            try:
                head = md.read_text(encoding="utf-8", errors="replace")[
                    :DEDUP_SEED_HEAD_BYTES
                ]
            except OSError as exc:
                read_failures += 1
                logger.warning("[去重库] 历史回填读取失败 %s: %s", md.name, exc)
                continue
            scanned += 1
            for identity, url in extract_video_identities(head).items():
                identities.setdefault(identity, (url, md))

        for identity, (url, md) in identities.items():
            if self.mark_ingested(
                url,
                platform=identity.partition(":")[0],
                title=md.stem,
                output_path=md,
                ingested_at=self._file_stamp(md),
            ):
                seeded += 1

        complete = read_failures == 0 and not truncated
        if complete:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO dedup_meta (key, value) VALUES (?, ?)",
                        (meta_key, datetime.now(UTC).isoformat()),
                    )
            except sqlite3.Error as exc:
                raise VideoDedupStoreError(f"视频去重库 meta 写入失败: {exc}") from exc
            finally:
                conn.close()
            logger.info(
                "[去重库] 历史回填完成（完整覆盖 %d/%d 份）: 扫到 %d 个视频身份，"
                "新登记 %d 个 (%s)",
                scanned, files_total, len(identities), seeded, directory,
            )
        else:
            logger.error(
                "[去重库] 历史回填**部分失败**（覆盖 %d/%d 份，读取失败 %d，截断=%s）: "
                "扫到 %d 个身份、新登记 %d 个；**不写 seeded 标记**，下轮会重试。"
                "未覆盖的已发布视频本轮仍可能被重跑 (%s)",
                scanned, files_total, read_failures, truncated,
                len(identities), seeded, directory,
            )

        return SeedOutcome(
            seeded=seeded,
            scanned=scanned,
            files_total=files_total,
            identities_found=len(identities),
            read_failures=read_failures,
            truncated=truncated,
            marked_complete=complete,
        )

    @staticmethod
    def _file_stamp(path: Path) -> str:
        """用文件 mtime 作为历史条目的 ingested_at（别让回填吃掉今天的日配额）。

        mtime 不可读时返回 `DEDUP_SEED_HISTORICAL_SENTINEL`（1970 哨兵）而不是
        `now()` —— 缺陷 V-1：`now()` 会让历史条目被算进"今日已入库"，静默扣掉
        今天的 NLM 配额，正是本方法要避免的事。
        """
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        except OSError as exc:
            logger.warning(
                "[去重库] 历史回填无法读取 mtime，改用历史哨兵时间戳 %s (%s: %s)",
                DEDUP_SEED_HISTORICAL_SENTINEL, path.name, exc,
            )
            return DEDUP_SEED_HISTORICAL_SENTINEL
