"""视频去重库 — VideoDedupStore（L1/L7 去重的读端 + 写端）。

缺陷 #2（2026-07-26）修复背景：
    `collector.py` 的 L1（URL 去重）与 L7（catalog 去重）**键是对的、逻辑是对的，
    但两端都空转**：

    1. 生产唯一构造点 `video/__main__.py` 从不传 `db` → `if not self.db: return False`
       直接放行，两层去重等于不存在；
    2. 即使传了 db 也仍然无效——本链路**从来没有写端**（`grep INSERT src/akb2/video/`
       为空），去重表永远读不到自己的产出。

    实证代价：`AI TRENDS/Video/` 324 份产出只对应 68 个唯一视频（重复 79%，
    单个视频最高 25 份），≈1766 次 LLM 调用 + ≈179 个 NotebookLM 深研配额被烧掉。

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
  上面那 1766 次 LLM 调用。
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
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("akb2.video.dedup_store")

# ---------------------------------------------------------------------------
# 具名常量（宪法 P4：预算 / 限额显式）
# ---------------------------------------------------------------------------

# sqlite busy timeout（秒）：与 launchd backstop 并发触发时不立刻 database is locked
DEDUP_DB_TIMEOUT_SEC = 10.0

# seed_from_published 单次最多扫描的 markdown 文件数（有界，宪法 P1）
DEDUP_SEED_MAX_FILES = 5000

# 已 seed 标记在 meta 表中的 key 前缀
_SEED_META_KEY_PREFIX = "seeded_from:"

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

    # 兜底：规范化 URL 的 sha256（去掉 scheme 大小写差异与尾斜杠）
    normalized = f"{host}{path.rstrip('/')}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
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

    def seed_from_published(self, published_dir: str | Path) -> int:
        """从已发布目录一次性回填历史身份（幂等，按目录记 seeded 标记）。

        没有这一步，修复上线后账本是空的 → 68 个**已经发布过**的视频会被再跑一遍
        （再烧 68 份 NLM 深研配额）。只读扫描 frontmatter 的 ``source_url``，
        不写、不改、不删 vault 里任何文件。

        只回填能解析出平台原生 id 的条目（``youtube:`` / ``bilibili:``），
        避免把同目录下的非视频笔记（论文、essay）也当成视频身份。

        Returns:
            本次新登记的条数；已 seed 过或目录不可用时返回 0。
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
            return 0

        if not directory.is_dir():
            # 不写 seeded 标记：vault 未挂载只是暂时不可用，下轮还要再试。
            logger.warning(
                "[去重库] 历史回填跳过：已发布目录不可用 %s —— "
                "本轮无历史身份保护，已发布视频可能被重跑",
                directory,
            )
            return 0

        files = sorted(directory.glob("*.md"))[:DEDUP_SEED_MAX_FILES]
        seeded = 0
        scanned = 0
        for md in files:
            scanned += 1
            try:
                head = md.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError as exc:
                logger.warning("[去重库] 历史回填读取失败 %s: %s", md.name, exc)
                continue
            match = re.search(r"^source_url:\s*(\S+)\s*$", head, re.MULTILINE)
            if not match:
                continue
            url = match.group(1).strip().strip("\"'")
            try:
                identity = video_identity(url)
            except ValueError:
                continue
            if identity.startswith("sha256:"):
                continue  # 非视频平台链接（essay / 论文），不进视频去重账本
            if self.mark_ingested(
                url,
                platform=identity.split(":", 1)[0],
                title=md.stem,
                output_path=md,
                ingested_at=self._file_stamp(md),
            ):
                seeded += 1

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
            "[去重库] 历史回填完成: 扫描 %d 份已发布笔记，新登记 %d 个视频身份 (%s)",
            scanned, seeded, directory,
        )
        return seeded

    @staticmethod
    def _file_stamp(path: Path) -> str:
        """用文件 mtime 作为历史条目的 ingested_at（别让回填吃掉今天的日配额）。"""
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        except OSError:
            return datetime.now(UTC).isoformat()
