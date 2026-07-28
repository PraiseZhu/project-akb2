r"""已发布产出的**身份**索引 — 文件名之外的第二道防线。

为什么需要（残余风险「视频存量覆盖」，2026-07-28）：

`assembler.commit` 的 fail-closed 第二道防线是 `promotion_path.exists()`，判据是
**文件名**。新命名 `video-<title-slug>-<身份短哈希>.md` 里带身份，所以对新产出有效；
但 vault 里的历史产出没有一份是新命名（2026-07-28 只读实测，命令可复现）：

    $ cd "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/AI TRENDS/Video"
    $ ls *.md | wc -l                                                   # 608 份
    $ ls *.md | grep -cE '^video-.*-[0-9a-f]{8}\.md$'                   #   0 新命名
    $ ls *.md | grep -cE '^video-.*-20[0-9]{2}-[0-9]{2}-[0-9]{2}\.md$'  # 324 旧 video-<slug>-<日期>
    $ ls *.md | grep -cE ' - 视频研究 20[0-9]{2}-[0-9]{2}-[0-9]{2}\.md$'  # 177 旧「… - 视频研究」
                                                                        # 其余 107 份是别的旧形态

于是存量 230 个视频身份的跨日防线只剩"账本"这个单点。账本失守的形态是具体的：
`AKB2_STATE_ROOT` 被改指向空目录、sqlite 损坏被重建、`seeded_from:` 标记误置成
已 seed 而表是空的 —— 任一发生，存量视频会被整批重跑（每个重烧一次 NLM 深研 +
两次 LLM 调用）。

本模块把第二道防线从"文件名"抬到"**身份**"：产出落地前只读扫一次已发布目录的
frontmatter，按 `video_identity()` 建 identity → 笔记名 的索引，与命名规则无关，
因此新旧命名一起覆盖。

有界（宪法 P1）：

- `PUBLISHED_INDEX_MAX_FILES` 限单次索引扫描的 markdown 份数；
- `PUBLISHED_INDEX_HEAD_BYTES` 限每份读取字节（只要 frontmatter）；
- 索引**按实例缓存**，`PUBLISHED_INDEX_TTL_SEC` 内复用 —— 逐候选全目录重扫
  （608 份 × 每候选）是明确禁止的形态，`files_read_total` 计数器让测试能钉住这点；
- 自己发布的产出用 `mark_published()` 增量并入索引，不触发重建。

只读契约：本模块只 `glob` + `read_text`，绝不写、改、删 vault 里任何文件
（历史产出是用户明确保留的证据，不重命名、不迁移）。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from src.akb2.video.constants import (
    PUBLISHED_INDEX_HEAD_BYTES,
    PUBLISHED_INDEX_MAX_FILES,
    PUBLISHED_INDEX_TTL_SEC,
)
from src.akb2.video.dedup_store import extract_video_identities, video_identity

logger = logging.getLogger("akb2.video.published_index")


class PublishedIdentityIndex:
    """已发布目录的 identity → 笔记名 索引（只读、有界、带缓存）。

    与账本互补：账本是机器本地状态（可能被清空/损坏/误标 seeded），本索引的真值
    来源就是 vault 里那 608 份产出本身 —— 产出还在，防线就在。
    """

    def __init__(
        self,
        published_dir: str | Path,
        *,
        ttl_sec: float = PUBLISHED_INDEX_TTL_SEC,
        max_files: int = PUBLISHED_INDEX_MAX_FILES,
        head_bytes: int = PUBLISHED_INDEX_HEAD_BYTES,
    ):
        self.published_dir = Path(published_dir)
        self.ttl_sec = ttl_sec
        self.max_files = max_files
        self.head_bytes = head_bytes

        # identity → 出处笔记名
        self._identities: dict[str, str] = {}
        self._built_at: float | None = None

        # 可观测计数器（测试用来钉"扫描有界 / 缓存生效"，也便于排障）
        self.build_count = 0
        self.files_read_total = 0
        self.files_total = 0
        self.truncated = False
        self.read_failures = 0
        self.dir_unavailable = False

    # ------------------------------------------------------------------
    # 索引构建
    # ------------------------------------------------------------------

    def _is_fresh(self) -> bool:
        if self._built_at is None:
            return False
        return (time.monotonic() - self._built_at) < self.ttl_sec

    def ensure_built(self) -> None:
        """必要时构建索引（TTL 内直接复用，不重扫目录）。"""
        if self._is_fresh():
            return
        self._build()

    def _build(self) -> None:
        self.build_count += 1
        self._built_at = time.monotonic()
        identities: dict[str, str] = {}
        self.truncated = False
        self.read_failures = 0

        if not self.published_dir.is_dir():
            # 目录不可用（vault 未挂载）→ 本层防线本轮不生效，必须可见（宪法 P4）。
            self.dir_unavailable = True
            self.files_total = 0
            self._identities = identities
            logger.warning(
                "[身份索引] 已发布目录不可用 %s —— 本轮身份层防线不生效，"
                "存量视频的跨日保护只剩账本单点",
                self.published_dir,
            )
            return

        self.dir_unavailable = False
        all_files = sorted(self.published_dir.glob("*.md"))
        self.files_total = len(all_files)
        self.truncated = self.files_total > self.max_files
        for md in all_files[: self.max_files]:
            try:
                # 只读前 head_bytes 字节，不整份读完再切 —— frontmatter 恒在头部。
                # 真 vault 608 份实测：整份 read_text 0.064s / 解码 5.55MB；
                # 只读前 4000 字节 0.008s / 2.32MB（8 倍）。多字节字符被字节边界
                # 切断时由 errors="replace" 吸收，与整份读再切的语义一致。
                with md.open("rb") as fh:
                    head = fh.read(self.head_bytes).decode("utf-8", errors="replace")
            except OSError as exc:
                self.read_failures += 1
                logger.warning("[身份索引] 读取失败 %s: %s", md.name, exc)
                continue
            self.files_read_total += 1
            for identity in extract_video_identities(head):
                identities.setdefault(identity, md.name)

        self._identities = identities
        if self.truncated or self.read_failures:
            logger.error(
                "[身份索引] 构建**不完整**（目录 %d 份，上限 %d，读取失败 %d）: "
                "未覆盖的历史产出在身份层不受保护 (%s)",
                self.files_total, self.max_files, self.read_failures,
                self.published_dir,
            )
        else:
            logger.info(
                "[身份索引] 已建立: %d 份产出 → %d 个视频身份 (%s)",
                self.files_total, len(identities), self.published_dir,
            )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def lookup(self, url: str) -> str | None:
        """该 URL 的身份是否已有历史产出；命中返回出处笔记名。

        Returns:
            命中 → 出处笔记名；未命中或身份无法解析出平台原生 id → None。

        Raises:
            ValueError: url 为空（无身份可查，调用方必须显式拒收）。
        """
        identity = video_identity(url)
        if identity.startswith("sha256:"):
            # 索引只收平台原生身份（`extract_video_identities` 的同一取舍：同目录下
            # 还有 essay / 论文笔记，把它们的链接当视频身份会让新视频被误判已入库
            # → 丢内容）。sha256 兜底身份因此不在本层覆盖内，仍由账本 + 文件名兜。
            return None
        self.ensure_built()
        return self._identities.get(identity)

    def contains(self, url: str) -> bool:
        """该 URL 是否已有历史产出（身份层判定，与文件名无关）。"""
        return self.lookup(url) is not None

    def mark_published(self, url: str, note_path: str | Path) -> None:
        """把本进程刚发布的产出并入索引（增量，不触发重建）。

        没有这一步，同一轮里"先发布 A、后又拿到 A 的另一个 URL 形态"会因索引是
        发布前的快照而漏判；重建整目录只为了一条新记录也不划算。
        """
        try:
            identity = video_identity(url)
        except ValueError:
            logger.warning("[身份索引] 无法登记（url 为空）: %s", note_path)
            return
        if identity.startswith("sha256:"):
            return
        self.ensure_built()
        self._identities.setdefault(identity, Path(note_path).name)

    @property
    def identity_count(self) -> int:
        """当前索引里的身份数（未构建时先构建）。"""
        self.ensure_built()
        return len(self._identities)
