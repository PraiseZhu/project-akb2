"""视频链路幂等去重 + 标题透传 + 时间闸/配额硬上限回归测试。

对应缺陷（2026-07-26）：
- #2  幂等去重接线：collector 阶段（花钱之前）就跳过已处理视频，且写端存在
- #10 标题原样透传，不经 LLM 生成路径
- #12 时间闸与日配额真正生效

每条断言都绑定**可观测行为**（第二轮 LLM 未被调用 / 产出身份不变 / 候选被拒收 /
候选数被硬截断），不是"能跑通"或 mock 装配形状。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video import dedup_store as dedup_store_mod
from src.akb2.video.assembler import Assembler
from src.akb2.video.collector import VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.dedup_store import (
    DEDUP_SEED_HISTORICAL_SENTINEL,
    VideoDedupStore,
    VideoDedupStoreError,
    local_day_start_utc_iso,
    video_identity,
)
from src.akb2.video.extractor import ExtractedFields, KeyFinding, SourceSpan
from src.akb2.video.reasoner import ReasonedLinks
from src.akb2.video.researcher import ResearchOutput
from tests.video_state_isolation import isolate_video_state  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_sources(path: Path, settings: dict | None = None) -> None:
    base_settings = {
        "min_video_duration_minutes": 3,
        "max_video_duration_minutes": 0,
        "days_lookback": 14,
        "dedup_strategy": "none",
        "skip_relevance_filter": True,
        "skip_keyword_filter": True,
        "coldstart": True,
        "coldstart_max_per_day": 5,
    }
    base_settings.update(settings or {})
    path.write_text(json.dumps({
        "creators": [{
            "id": "yt-test", "platform": "youtube",
            "channel_url": "https://www.youtube.com/@TestChannel",
            "name": "Test Channel", "enabled": True,
            "max_per_day": 5, "keywords": [], "tier": "quality",
        }],
        "settings": base_settings,
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def dedup_config(tmp_path) -> VideoConfig:
    """全部状态路径隔离到 tmp_path 的 VideoConfig（不碰真实状态根/vault）。"""
    sources = tmp_path / "video_sources.json"
    _write_sources(sources)
    keywords = tmp_path / "keywords.json"
    keywords.write_text(json.dumps({
        "include": {"core": ["AI", "LLM", "agent"]},
        "exclude": {"non_ai": ["cooking"]},
    }), encoding="utf-8")
    return VideoConfig(
        video_sources_path=sources,
        keywords_path=keywords,
        staging_dir=tmp_path / "staging" / "candidate",
        akb2_root=tmp_path / "vault",
        dedup_db_path=tmp_path / "state" / "video-dedup.sqlite",
    )


def _video(video_id: str = "vid-1", title: str = "AI Agents Deep Dive") -> dict:
    return {
        "platform": "youtube",
        "id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration_minutes": 20,
        "upload_date": datetime.now().strftime("%Y%m%d"),
        "creator": "Test Channel",
    }


class _RecordingLLM:
    """记录调用次数的 LLM adapter（用于断言"第二轮没花钱"）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def chat(self, task: str, system: str, user: str, temperature: float = 0.3) -> str:
        self.calls.append({"task": task, "user": user})
        return json.dumps({"groups": [[0]]})


# ---------------------------------------------------------------------------
# #2 稳定身份
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=M_AmPWmkpwA", "youtube:M_AmPWmkpwA"),
        ("https://youtube.com/watch?v=M_AmPWmkpwA&t=30s", "youtube:M_AmPWmkpwA"),
        ("https://youtu.be/M_AmPWmkpwA", "youtube:M_AmPWmkpwA"),
        ("https://www.youtube.com/shorts/M_AmPWmkpwA", "youtube:M_AmPWmkpwA"),
        ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili:BV1xx411c7mD"),
        ("https://www.bilibili.com/video/BV1xx411c7mD/?spm_id=333", "bilibili:BV1xx411c7mD"),
    ],
)
def test_identity_is_stable_across_url_variants(url: str, expected: str):
    """同一视频的不同 URL 形态必须映射到同一稳定身份（去重键不能被 URL 装饰绕过）。"""
    assert video_identity(url) == expected


def test_identity_ignores_title_entirely():
    """身份只由 URL 决定：标题被 LLM 改写（#10 实证）不得改变去重键。"""
    url = "https://www.youtube.com/watch?v=M_AmPWmkpwA"
    assert video_identity(url) == video_identity(url + "&list=PLxyz")


@pytest.mark.parametrize(
    ("decorated", "plain"),
    [
        # sha256 兜底路径（非 youtube/bilibili host）：跟踪参数不得分裂身份
        ("https://vimeo.com/12345?utm_source=newsletter&utm_medium=email",
         "https://vimeo.com/12345"),
        ("https://vimeo.com/12345?fbclid=abc123", "https://vimeo.com/12345"),
        # 播放位置类参数（时间戳）：同一个视频页，剥掉才不会分裂身份
        ("https://vimeo.com/12345?t=120", "https://vimeo.com/12345"),
        ("https://vimeo.com/12345?start=90", "https://vimeo.com/12345"),
        ("https://vimeo.com/12345?end=200", "https://vimeo.com/12345"),
        ("https://vimeo.com/12345?time_continue=3", "https://vimeo.com/12345"),
        ("https://vimeo.com/12345?start=90&end=200&utm_source=x",
         "https://vimeo.com/12345"),
        # 有意义的参数保留，但顺序无关
        ("https://vimeo.com/12345?a=1&b=2", "https://vimeo.com/12345?b=2&a=1"),
        # bilibili 短链未展开时也走兜底，跟踪参数同样要剥
        ("https://b23.tv/abc123?share_source=copy_web", "https://b23.tv/abc123"),
    ],
)
def test_identity_fallback_strips_tracking_params(decorated: str, plain: str):
    """sha256 兜底身份必须与跟踪参数/参数顺序无关。

    回归前兜底把 query 整串拼进归一化串 → 同一页带 `?utm_source=` 与不带算出两个
    身份，账本对兜底身份形同失效（每次分享链接换个来源标记就重烧一次额度）。
    """
    assert video_identity(decorated).startswith("sha256:")
    assert video_identity(decorated) == video_identity(plain)


def test_identity_fallback_keeps_content_selecting_params():
    """只剥来源/播放位置类参数：能选内容的参数必须仍然区分身份（不得过度合并）。

    这条是"剥参数"的反向护栏，取舍不对称：少剥只多烧一次额度；多剥会把两个**真不同**
    的 URL 并成一个身份 → 新视频被误判已入库、内容永久不产出。所以任何可能选择内容
    的键（分 p、页码、资源 id、视频 id、查询词）都必须保持区分。
    """
    base = "https://example.com/watch"
    assert video_identity(f"{base}?id=1") != video_identity(f"{base}?id=2")
    assert video_identity(f"{base}?p=2") != video_identity(base)
    # 逐个钉住：这些键不得进 _TRACKING_QUERY_KEYS
    for key in ("id", "p", "page", "v", "q", "ep_id", "cid", "list"):
        assert video_identity(f"{base}?{key}=1") != video_identity(f"{base}?{key}=2"), (
            f"参数 {key} 被当成跟踪参数剥掉了 → 两个不同视频会撞成一个身份（丢内容）"
        )
        assert video_identity(f"{base}?{key}=1") != video_identity(base), (
            f"参数 {key} 被剥掉 → 带该参数与不带该参数的不同资源撞成一个身份"
        )


def test_identity_rejects_empty_url():
    """空 URL 不得静默生成假身份（否则两个视频撞成一个）。"""
    with pytest.raises(ValueError):
        video_identity("")


# ---------------------------------------------------------------------------
# #2 去重库读写端
# ---------------------------------------------------------------------------


def test_store_mark_then_is_ingested(tmp_path):
    """写端登记后读端立刻可见，且重复登记幂等（返回 False，不报错）。"""
    store = VideoDedupStore(tmp_path / "s" / "dedup.sqlite")
    url = "https://www.youtube.com/watch?v=abc123"
    assert store.is_ingested(url) is False
    assert store.mark_ingested(url, platform="youtube", title="T") is True
    assert store.is_ingested(url) is True
    assert store.mark_ingested(url, platform="youtube", title="T 改过的标题") is False


def test_store_fail_closed_when_db_unusable(tmp_path):
    """库不可用 → 抛 VideoDedupStoreError，绝不 return False 静默放行。

    回归前：collector 吞掉 sqlite3.OperationalError 直接 return False，
    所有视频都被判"未入库" → 重复产出正是从这里流出去的
    （实测 608 份产出 → 230 个身份；"79%" 是 `source_url:` 子集口径，
    见 `dedup_store` 模块 docstring）。
    """
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"this is not a sqlite database" * 10)
    store = VideoDedupStore(broken)
    with pytest.raises(VideoDedupStoreError):
        store.is_ingested("https://www.youtube.com/watch?v=abc123")


def test_store_seed_from_published_is_idempotent(tmp_path):
    """历史产出回填：已发布笔记的 source_url 进账本，重复调用不重复回填。"""
    published = tmp_path / "Video"
    published.mkdir()
    (published / "video-a-2026-07-01.md").write_text(
        "---\ntitle: \"A\"\nsource_url: https://www.youtube.com/watch?v=hist1\n---\n# A",
        encoding="utf-8",
    )
    (published / "essay-b.md").write_text(
        "---\ntitle: \"B\"\nsource_url: https://example.com/essay\n---\n# B",
        encoding="utf-8",
    )
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    first = store.seed_from_published(published)
    assert first.seeded == 1  # essay 不进视频账本
    assert first.marked_complete is True
    assert store.is_ingested("https://youtu.be/hist1") is True
    assert store.is_ingested("https://example.com/essay") is False
    second = store.seed_from_published(published)
    assert second.already_seeded is True
    assert second.seeded == 0  # 幂等


def test_store_seed_does_not_consume_today_quota(tmp_path):
    """回填用文件 mtime 作为 ingested_at，不得吃掉今天的日配额。"""
    published = tmp_path / "Video"
    published.mkdir()
    old = published / "video-old-2026-01-01.md"
    old.write_text(
        "---\nsource_url: https://www.youtube.com/watch?v=old1\n---\n# old",
        encoding="utf-8",
    )
    old_ts = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    import os
    os.utime(old, (old_ts, old_ts))

    store = VideoDedupStore(tmp_path / "dedup.sqlite")
    store.seed_from_published(published)
    assert store.count_ingested_since(datetime.now(UTC).date().isoformat()) == 0


# ---------------------------------------------------------------------------
# V-1 历史回填：两种 frontmatter 键 + 部分失败可见 + mtime 失败不吃今日配额
# ---------------------------------------------------------------------------

# 真实历史产出的两种 frontmatter 形状（只读采样自
# /Volumes/AKB2/.../AI TRENDS/Video，608 份 md）：
#   video_url:  173 份（旧 researcher 直写的形状，172 个平台身份）
#   source_url: 325 份（assembler 形状，68 个平台身份）
_HISTORICAL_VIDEO_URL_NOTE = """---
title_original: "This 100% Local AI Automation Pipeline Blows My Mind"
source: notebooklm
date: 2026-05-12
tags:
  - research
  - video
  - youtube
type: deep-research
notebook_id: 01554147-9bdc-4feb-a526-7ef1d9525e53
video_url: https://www.youtube.com/watch?v=ydUBYFlwhyk
video_creator: All About AI
---

# 100% 本地 AI 自动化管线
"""

_HISTORICAL_SOURCE_URL_NOTE = """---
page_status: active
source_type: video
template_type: source
title: "Assembler 形状的产出"
source_url: https://www.youtube.com/watch?v=M_AmPWmkpwA
author: Test Channel
gate_decision: active
tags:
  - research
  - video
---

# Assembler 形状的产出
"""


def _write_history(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "本地 AI 自动化管线 - 视频研究 2026-05-12.md").write_text(
        _HISTORICAL_VIDEO_URL_NOTE, encoding="utf-8"
    )
    (directory / "video-assembler-shape-abcd1234-2026-07-01.md").write_text(
        _HISTORICAL_SOURCE_URL_NOTE, encoding="utf-8"
    )


def test_seed_recognizes_both_historical_frontmatter_keys(tmp_path):
    """`source_url:` 与 `video_url:` 两种历史键都必须被回填（缺陷 V-1）。

    回归前只认 `^source_url:` → 325 份命中 68 个身份，而 `video_url:` 的 172 个
    身份里 158 个完全没被覆盖（漏的比覆盖的多一倍多）。这里各造一份真实形状的
    fixture，断言两份都进账本。
    """
    published = tmp_path / "Video"
    _write_history(published)
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    outcome = store.seed_from_published(published)

    assert outcome.seeded == 2, f"两种键都应被回填，实得 {outcome.seeded}"
    assert store.is_ingested("https://youtu.be/ydUBYFlwhyk") is True, (
        "video_url: 形状的历史产出未进账本 → 该视频会被重跑"
    )
    assert store.is_ingested("https://www.youtube.com/watch?v=M_AmPWmkpwA") is True
    assert outcome.marked_complete is True


def test_seed_recognizes_third_key_shapes(tmp_path):
    """实测还存在 `url:` / `video_source:` / `original_talk_url:` 三种少数键。

    只读统计（同目录 608 份，2026-07-26 复核）：`url:` 51 份 → 2 个平台身份、
    `video_source:` 1 份 → 1 个、`original_talk_url:` 1 份 → 1 个 —— 都不在
    source_url/video_url 的覆盖集合里。另有 `original_url:` / `x_url:` 各 1 份，
    值不是视频链接（解析出 0 个平台身份），所以不在本测试的样例里。
    键名判定按"含 url"+ 显式补集，所以将来新出现的键名也自动被认。
    """
    published = tmp_path / "Video"
    published.mkdir()
    (published / "n1.md").write_text(
        "---\ntype: deep-research\nurl: https://www.bilibili.com/video/BV1iVoVBgERD/\n---\n# n1",
        encoding="utf-8",
    )
    (published / "n2.md").write_text(
        "---\nvideo_source: https://www.bilibili.com/video/BV1Ji9aB4Eu2\n---\n# n2",
        encoding="utf-8",
    )
    (published / "n3.md").write_text(
        "---\noriginal_talk_url: https://youtu.be/YHk45NEpspE\n---\n# n3",
        encoding="utf-8",
    )
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    assert store.seed_from_published(published).seeded == 3
    assert store.is_ingested("https://www.bilibili.com/video/BV1iVoVBgERD") is True
    assert store.is_ingested("https://www.bilibili.com/video/BV1Ji9aB4Eu2") is True
    assert store.is_ingested("https://www.youtube.com/watch?v=YHk45NEpspE") is True


# 真实 vault 里**所有**承载视频链接的 frontmatter 键（2026-07-28 只读复核，
# `/Volumes/AKB2/.../AI TRENDS/Video` 608 份 md，按"值能解析出平台原生 id"统计）：
#   source_url 325 份 → 68 身份 | video_url 173 → 172 | url 51 → 2
#   original_url 1 → 0 | video_source 1 → 1 | x_url 1 → 0 | original_talk_url 1 → 1
#   seed 1 → 1        ← 上一轮的"7 种键"清单里没有它，_is_url_key 也不认
# 复现命令见 `test_seed_covers_every_url_key_in_real_vault`（integration）。
_REAL_VAULT_VIDEO_URL_KEYS = (
    "source_url", "video_url", "url", "video_source", "original_talk_url", "seed",
)


def test_seed_recognizes_every_url_key_shape_observed_in_vault(tmp_path):
    """实测键分布里**每一个**承载视频链接的键都必须被回填（覆盖率 = 100%）。

    上一轮把判定从"只认 source_url"扩成"键名含 url + {video_source}"，但真实目录里
    还有一个 `seed:` 键（1 份笔记，`seed: https://www.youtube.com/watch?v=3DlXq9nsQOE`）
    既不含 "url" 也不在补集里 → 该身份漏认。可观测后果：账本里 230 个身份，而目录里
    实际有 231 个 —— 漏的那个视频会被重跑一次（重烧 NLM 深研 + 两次 LLM）。
    """
    published = tmp_path / "Video"
    published.mkdir()
    # 每个键一份笔记、各带一个互不相同的真实身份
    ids = {key: f"vid{idx:08d}" for idx, key in enumerate(_REAL_VAULT_VIDEO_URL_KEYS)}
    for key, vid in ids.items():
        (published / f"{key}-note.md").write_text(
            f"---\ntype: deep-research\n{key}: https://www.youtube.com/watch?v={vid}\n---\n# n",
            encoding="utf-8",
        )
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    outcome = store.seed_from_published(published)

    missed = [
        key for key, vid in ids.items()
        if not store.is_ingested(f"https://www.youtube.com/watch?v={vid}")
    ]
    assert missed == [], f"这些键承载的历史身份没进账本 → 对应视频会被重跑: {missed}"
    assert outcome.seeded == len(ids)
    assert outcome.coverage == 1.0


@pytest.mark.integration
def test_seed_covers_every_url_key_in_real_vault():
    """真机只读：真实已发布目录里**按值**找到的平台身份必须 100% 被键名判定认到。

    这条是"键名清单是否已经追上真实数据"的判别 —— 键名判定（生产用的
    `extract_video_identities`）与值判定（遍历所有 frontmatter 标量键、只看值能否
    解析出平台 id）两个集合必须相等。差集非空 = 又出现了没被认的新键名。
    只读，不写、不改 vault。
    """
    published = Path(
        "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/AI TRENDS/Video"
    )
    if not published.is_dir():
        pytest.skip("AKB2 卷未挂载")

    from src.akb2.video.dedup_store import (
        _FRONTMATTER_SCALAR_RE,
        DEDUP_SEED_HEAD_BYTES,
        _frontmatter_block,
        extract_video_identities,
    )

    by_key: set[str] = set()
    by_value: dict[str, str] = {}
    for md in sorted(published.glob("*.md")):
        head = md.read_text(encoding="utf-8", errors="replace")[:DEDUP_SEED_HEAD_BYTES]
        by_key |= set(extract_video_identities(head))
        for match in _FRONTMATTER_SCALAR_RE.finditer(_frontmatter_block(head)):
            value = match.group("value").strip().strip("\"'")
            try:
                identity = video_identity(value)
            except ValueError:
                continue
            if not identity.startswith("sha256:"):
                by_value.setdefault(identity, match.group("key"))

    missed = {i: by_value[i] for i in set(by_value) - by_key}
    assert not missed, (
        f"这些平台身份在 frontmatter 里有值但键名判定不认（identity → 键名）: {missed}"
    )
    assert len(by_key) >= 231, f"实测下界 231 个身份，实得 {len(by_key)}"


def test_seed_does_not_ingest_non_video_frontmatter_urls(tmp_path):
    """非平台链接（essay / 推文）不得进视频账本，否则真视频会被误判已入库。"""
    published = tmp_path / "Video"
    published.mkdir()
    (published / "essay.md").write_text(
        "---\nsource_url: https://example.com/essay\nx_url: https://x.com/a/status/1\n---\n# e",
        encoding="utf-8",
    )
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    assert store.seed_from_published(published).seeded == 0
    assert store.is_ingested("https://example.com/essay") is False


def test_seed_marker_not_written_on_partial_failure(tmp_path):
    """部分扫描失败 → 不写 seeded 标记、失败可见、下轮可重试（缺陷 V-1）。

    回归前：无条件写 `seeded_from:<dir>`，一次部分扫描就把窗口永久关上，
    日志却打"新登记 N 个视频身份"像是成功（部分失败伪装成成功，违反宪法 P4）。
    """
    published = tmp_path / "Video"
    _write_history(published)
    unreadable = published / "unreadable.md"
    unreadable.write_text(
        "---\nvideo_url: https://www.youtube.com/watch?v=locked1\n---\n# x",
        encoding="utf-8",
    )
    unreadable.chmod(0o000)
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    try:
        outcome = store.seed_from_published(published)
        assert outcome.read_failures == 1, "读取失败数必须被计数并暴露"
        assert outcome.complete is False
        assert outcome.marked_complete is False, (
            "部分失败仍写 seeded 标记 → 漏掉的历史身份永不重试"
        )
        assert outcome.coverage < 1.0
        assert store.is_ingested("https://youtu.be/locked1") is False

        # 下轮可重试：标记没写，所以第二次调用不会被 already_seeded 短路
        unreadable.chmod(0o644)
        retry = store.seed_from_published(published)
    finally:
        unreadable.chmod(0o644)

    assert retry.already_seeded is False, "标记未写入 → 第二轮必须重新扫描"
    assert retry.marked_complete is True
    assert store.is_ingested("https://youtu.be/locked1") is True, (
        "重试必须把上一轮漏掉的身份补上"
    )


def test_seed_truncation_is_visible_and_retryable(tmp_path, monkeypatch):
    """目录超过扫描上限 → 截断可见且不写标记（有界 + 失败可见）。"""
    monkeypatch.setattr(dedup_store_mod, "DEDUP_SEED_MAX_FILES", 1)
    published = tmp_path / "Video"
    _write_history(published)
    store = VideoDedupStore(tmp_path / "dedup.sqlite")

    outcome = store.seed_from_published(published)

    assert outcome.truncated is True
    assert outcome.marked_complete is False
    assert store.seed_from_published(published).already_seeded is False


def test_file_stamp_fallback_does_not_eat_today_quota(tmp_path, monkeypatch):
    """mtime 不可读时用 1970 历史哨兵，不得打上"今天"（缺陷 V-1）。

    回归前 `except OSError: return datetime.now(UTC)` → 历史回填条目算进
    "今日已入库"，静默吃掉今天的 NLM 配额，正是 `_file_stamp` 注释声称要避免的事。
    """
    published = tmp_path / "Video"
    _write_history(published)

    real_stat = Path.stat

    def _boom(self, *args, **kwargs):
        if self.suffix == ".md" and self.parent == published:
            raise OSError("stat unavailable")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _boom)
    store = VideoDedupStore(tmp_path / "dedup.sqlite")
    outcome = store.seed_from_published(published)
    monkeypatch.undo()

    assert outcome.seeded == 2
    assert store.count_ingested_since(local_day_start_utc_iso()) == 0, (
        "mtime 读不到的历史条目被打上今天 → 回填自己吃掉今日配额"
    )
    conn = sqlite3.connect(str(tmp_path / "dedup.sqlite"))
    try:
        stamps = {
            row[0] for row in conn.execute("SELECT ingested_at FROM video_ingested")
        }
    finally:
        conn.close()
    assert stamps == {DEDUP_SEED_HISTORICAL_SENTINEL}


def test_run_full_seeds_both_keys_into_production_ledger(dedup_config, monkeypatch):
    """**生产路径绑定**：`_run_full` 的回填必须认两种键（不是只测 store 方法）。

    缺陷 V-1 的风险不在 store 有没有能力，而在生产入口调的那次回填覆盖了多少。
    这里从 `_run_full` 进（它自己构造 store 并调 seed），跑完后直接查账本。
    """
    from src.akb2.video import __main__ as video_main

    _write_history(dedup_config.promotion_target_path)

    class _StubCollector:
        def __init__(self, config, db=None, llm_adapter=None):
            pass

        def collect(self):
            return []

    monkeypatch.setattr(video_main, "VideoCollector", _StubCollector)
    monkeypatch.setattr(video_main, "VideoResearcher", lambda *a, **k: object())
    monkeypatch.setattr(video_main, "Extractor", lambda *a, **k: object())
    monkeypatch.setattr(video_main, "Reasoner", lambda *a, **k: object())

    assert video_main._run_full(dedup_config, dry_run=True) == 0

    store = VideoDedupStore(dedup_config.dedup_db_path)
    assert store.is_ingested("https://youtu.be/ydUBYFlwhyk") is True, (
        "生产回填漏掉 video_url: 形状的历史产出 → 那些视频今晚会被重跑"
    )
    assert store.is_ingested("https://www.youtube.com/watch?v=M_AmPWmkpwA") is True


# ---------------------------------------------------------------------------
# #2 生产接线：collector 阶段跳过 + 第二轮不花钱
# ---------------------------------------------------------------------------


def test_main_constructs_collector_with_dedup_db(dedup_config, monkeypatch):
    """生产入口 `_run_full` 构造 collector 时 db 必须非 None（#2 根因 2 的护栏）。

    回归前：`VideoCollector(config, llm_adapter=llm_adapter)` 从不传 db →
    `if not self.db: return False` → L1/L7 全空转。
    """
    from src.akb2.video import __main__ as video_main

    captured: dict = {}

    class _StubCollector:
        def __init__(self, config, db=None, llm_adapter=None):
            captured["db"] = db

        def collect(self):
            return []

    monkeypatch.setattr(video_main, "VideoCollector", _StubCollector)
    monkeypatch.setattr(video_main, "VideoResearcher", lambda *a, **k: object())
    monkeypatch.setattr(video_main, "Extractor", lambda *a, **k: object())
    monkeypatch.setattr(video_main, "Reasoner", lambda *a, **k: object())

    assert video_main._run_full(dedup_config, dry_run=True) == 0
    assert captured["db"] is not None, "生产入口必须注入去重库，否则 L1/L7 空转"
    assert isinstance(captured["db"], VideoDedupStore)


def test_same_video_second_run_skipped_at_collector_stage(dedup_config):
    """同一 video_url 连续两轮：第二轮在 collector 阶段就被跳过，且 LLM 未被调用。

    这条断言绑定的可观测行为就是"省下额度"：第二轮 candidates 为空 →
    researcher/extractor/reasoner 一次都不会被触发，LLM 调用计数保持不变。
    """
    _write_sources(dedup_config.video_sources_path, {
        "dedup_strategy": "llm_cluster", "coldstart_max_per_day": 10,
    })
    llm = _RecordingLLM()
    store = VideoDedupStore(dedup_config.dedup_db_path)
    # 必须 ≥2 个候选：`_dedup_by_topic` 只在 len>1 时才调 LLM。单候选时第一轮的
    # LLM 计数就是 0，`assert len(llm.calls) == calls_after_first` 退化成 `0 == 0`
    # 的空断言 —— 把去重改坏也不会破裂。基线必须先是非零。
    videos = [_video("dup-a", title="AI Video A"), _video("dup-b", title="AI Video B")]

    collector = VideoCollector(dedup_config, db=store, llm_adapter=llm)
    with mock.patch.object(
        collector, "_detect_yt_videos", return_value=[dict(v) for v in videos]
    ):
        first = collector.collect()
    assert sorted(r["video"]["id"] for r in first) == ["dup-a", "dup-b"]

    calls_after_first = len(llm.calls)
    assert calls_after_first > 0, (
        "前置条件：第一轮必须真的调过 LLM，否则第二轮的『计数不变』是空断言"
    )

    # 产出落地 → 写端登记（生产由 assembler.run_pipeline 在发布成功后调用）
    for v in videos:
        collector.mark_ingested(v, dedup_config.staging_dir / f"video-{v['id']}.md")

    collector2 = VideoCollector(dedup_config, db=store, llm_adapter=llm)
    with mock.patch.object(
        collector2, "_detect_yt_videos", return_value=[dict(v) for v in videos]
    ):
        second = collector2.collect()

    assert second == [], "第二轮必须在 collector 阶段被跳过（花钱之前）"
    assert len(llm.calls) == calls_after_first, (
        "第二轮不得产生任何 LLM 调用——省额度才是本修复的可观测收益"
    )


def test_run_pipeline_marks_ingested_after_publish(dedup_config):
    """run_pipeline 发布成功后必须登记去重身份（写端接线；#2 根因 3）。"""
    store = VideoDedupStore(dedup_config.dedup_db_path)
    video = _video()
    collector = VideoCollector(dedup_config, db=store)
    assembler = Assembler(dedup_config)
    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)

    class _Researcher:
        def run(self, video_results):
            return [ResearchOutput(
                report_markdown="# LLM 改写过的标题\n\n## 执行摘要\n\n内容。",
                notebook_id="nb-1",
                video_meta=video,
                degraded=False,
            )]

    class _Extractor:
        def extract(self, md):
            return ExtractedFields(
                tldr="要点",
                key_findings=[KeyFinding(
                    assertion="断言", source_span=SourceSpan(line_start=1, line_end=1),
                    confidence=0.9,
                )],
                future_questions=[],
                concepts_mentioned=["AI"],
            )

    class _Reasoner:
        def reason(self, concepts, context=""):
            return ReasonedLinks(related_concept=[], proposed_graph_nodes=[])

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dict(video)]):
        with mock.patch.object(assembler, "_gate_decision", return_value="active"):
            results = assembler.run_pipeline(
                collector, _Researcher(), _Extractor(), _Reasoner(),
            )

    assert [r["status"] for r in results] == ["ok"]
    assert results[0]["dedup_marked"] is True
    assert store.is_ingested(video["url"]) is True, "发布成功后身份必须落账本"


def test_run_pipeline_surfaces_failed_dedup_mark(dedup_config):
    """账本写失败不得静默：结果里必须标 dedup_marked=False（下轮会重跑）。"""
    store = VideoDedupStore(dedup_config.dedup_db_path)
    video = _video()
    collector = VideoCollector(dedup_config, db=store)
    assembler = Assembler(dedup_config)
    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)

    class _Researcher:
        def run(self, video_results):
            return [ResearchOutput(
                report_markdown="# 报告\n\n内容。", notebook_id="nb-1",
                video_meta=video, degraded=False,
            )]

    class _Extractor:
        def extract(self, md):
            return ExtractedFields(
                tldr="要点", key_findings=[], future_questions=[], concepts_mentioned=[],
            )

    class _Reasoner:
        def reason(self, concepts, context=""):
            return ReasonedLinks(related_concept=[], proposed_graph_nodes=[])

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dict(video)]):
        with mock.patch.object(assembler, "_gate_decision", return_value="active"):
            with mock.patch.object(
                collector, "mark_ingested",
                side_effect=VideoDedupStoreError("disk full"),
            ):
                results = assembler.run_pipeline(
                    collector, _Researcher(), _Extractor(), _Reasoner(),
                )

    assert results[0]["dedup_marked"] is False
    from src.akb2.video.__main__ import _report_results
    assert _report_results(results) == 1, "账本未登记必须体现在退出码上"


# ---------------------------------------------------------------------------
# #10 标题透传
# ---------------------------------------------------------------------------


def _assemble_with_report(config: VideoConfig, video_meta: dict, h1: str) -> str:
    assembler = Assembler(config)
    return assembler.assemble(
        video_meta,
        ResearchOutput(
            report_markdown=f"# {h1}\n\n## 执行摘要\n\n内容。",
            notebook_id="nb-1", video_meta=video_meta, degraded=False,
        ),
        ExtractedFields(
            tldr="要点", key_findings=[], future_questions=[], concepts_mentioned=[],
        ),
        ReasonedLinks(related_concept=[], proposed_graph_nodes=[]),
    )


def test_title_comes_from_video_meta_not_report_h1(dedup_config):
    """标题必须是 yt-dlp 原值，不是 LLM 报告的 H1。

    回归前：`report_title = self._extract_h1(report_markdown) or title` →
    实证同一视频标题在 paradigm/paradiagm 之间来回翻转。
    """
    video_meta = _video(title="New AI coding paradigm - OpenAI Symphony")
    md = _assemble_with_report(dedup_config, video_meta, h1="全新 AI 编码范式：OpenAI Symphony")

    assert 'title: "New AI coding paradigm - OpenAI Symphony"' in md
    assert md.split("---\n")[2].lstrip().startswith(
        "# New AI coding paradigm - OpenAI Symphony"
    ), "正文首个 H1 也必须是透传标题"


def test_same_video_different_report_h1_yields_same_identity(dedup_config):
    """同一 video_meta 配两个不同 H1 的报告 → 落地身份（标题 + slug）必须相同。"""
    video_meta = _video(title="New AI coding paradigm - OpenAI Symphony")
    assembler = Assembler(dedup_config)

    md_cn = _assemble_with_report(dedup_config, video_meta, h1="中文标题版本")
    md_en = _assemble_with_report(dedup_config, video_meta, h1="English Title Variant")

    title_line = 'title: "New AI coding paradigm - OpenAI Symphony"'
    assert title_line in md_cn and title_line in md_en
    assert assembler._make_slug(md_cn) == assembler._make_slug(md_en)
    assert (
        assembler._slug_for_video(video_meta)
        == assembler._slug_for_video(dict(video_meta, title=video_meta["title"]))
    )


def test_slug_stable_when_creator_renames_title(dedup_config):
    """作者改标题后 slug 的身份后缀不变（同一视频仍归到同一族文件名）。"""
    assembler = Assembler(dedup_config)
    v1 = _video(title="New AI coding paradiagm - OpenAI Symphony")
    v2 = _video(title="New AI coding paradigm - OpenAI Symphony")  # typo 修好
    suffix1 = assembler._slug_for_video(v1).rsplit("-", 1)[-1]
    suffix2 = assembler._slug_for_video(v2).rsplit("-", 1)[-1]
    assert suffix1 == suffix2 != ""


# ---------------------------------------------------------------------------
# #12 时间闸
# ---------------------------------------------------------------------------


def test_missing_upload_date_is_rejected_not_silently_passed(dedup_config):
    """缺 upload_date 且补取不到 → 拒收（不静默通过时间闸）。

    回归前：`if upload_date:` 守卫让空值直接跳过 days_lookback 与 start_date
    两道闸（实测：608 份产出里只有 1 份 frontmatter 带 published_at）。
    """
    _write_sources(dedup_config.video_sources_path, {"start_date": "2026-04-01"})
    collector = VideoCollector(dedup_config)
    no_date = dict(_video("nodate1"), upload_date="")

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[no_date]):
        with mock.patch.object(collector, "_fill_upload_date") as fill:
            results = collector.collect()

    assert results == [], "无法确定发布日期的视频必须被拒收"
    assert fill.called, "拒收前必须先尝试补取真实发布日期"
    # "0 产出" 与 "全被时间闸拒收" 必须可区分（宪法 P4）
    assert collector._date_unknown_rejected == 1


def test_upload_date_backfill_rescues_candidate(dedup_config):
    """补取到真实发布日期的视频照常通过时间闸（不是一律拒收）。"""
    collector = VideoCollector(dedup_config)
    no_date = dict(_video("filled1"), upload_date="")
    today = datetime.now().strftime("%Y%m%d")

    def _fill(video: dict) -> None:
        video["upload_date"] = today

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[no_date]):
        with mock.patch.object(collector, "_fill_upload_date", side_effect=_fill):
            results = collector.collect()

    assert [r["video"]["id"] for r in results] == ["filled1"]


def test_days_lookback_rejects_old_video_even_without_start_date(dedup_config):
    """days_lookback 必须真正生效：超窗口的老视频被拒（原来只在平台层生效）。"""
    _write_sources(dedup_config.video_sources_path, {"days_lookback": 14})
    collector = VideoCollector(dedup_config)
    old = dict(
        _video("old1"),
        upload_date=(datetime.now() - timedelta(days=400)).strftime("%Y%m%d"),
    )

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[old]):
        results = collector.collect()

    assert results == []


def test_malformed_upload_date_is_rejected(dedup_config):
    """upload_date 格式非法 → 按未知处理并拒收（原来 except ValueError: pass 放行）。"""
    collector = VideoCollector(dedup_config)
    bad = dict(_video("bad1"), upload_date="2026-07-26")  # 非 YYYYMMDD

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[bad]):
        with mock.patch.object(collector, "_fill_upload_date"):
            results = collector.collect()

    assert results == []


# ---------------------------------------------------------------------------
# #12 日配额硬上限
# ---------------------------------------------------------------------------


def test_daily_quota_is_a_hard_cap(dedup_config):
    """候选超过 nlm_daily_quota → 硬截断到配额，超额部分写入 pending 不丢。"""
    _write_sources(dedup_config.video_sources_path, {
        "nlm_daily_quota": 3, "coldstart_max_per_day": 10,
    })
    collector = VideoCollector(dedup_config)
    videos = [_video(f"q{i}", title=f"AI Video {i}") for i in range(8)]

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        results = collector.collect()

    assert len(results) == 3, f"日配额 3 必须是硬上限，实得 {len(results)}"
    pending = json.loads(
        dedup_config.resolve_pending_path().read_text(encoding="utf-8")
    )
    assert len(pending) == 5, "超额候选必须写回 pending，不得静默丢弃"


def test_daily_quota_counts_already_ingested_today(dedup_config):
    """同一天多次触发（launchd backstop）合计不得超配额。"""
    _write_sources(dedup_config.video_sources_path, {
        "nlm_daily_quota": 2, "coldstart_max_per_day": 10,
    })
    store = VideoDedupStore(dedup_config.dedup_db_path)
    store.mark_ingested("https://www.youtube.com/watch?v=done1", platform="youtube")
    collector = VideoCollector(dedup_config, db=store)
    videos = [_video(f"r{i}", title=f"AI Video {i}") for i in range(5)]

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        results = collector.collect()

    assert len(results) == 1, "今日已入库 1 个 → 本轮只剩 1 个额度"


# ---------------------------------------------------------------------------
# V-3 日配额的时间基准：本地日 vs UTC 错位
# ---------------------------------------------------------------------------


@pytest.fixture
def cst_timezone(monkeypatch):
    """把进程时区固定到 CST(UTC+8)，退出时恢复（含 C 层 tzset）。

    CST 下本地 00:00 恒等于**前一 UTC 日** 16:00，所以不需要 mock 时钟就能稳定
    构造"本地今日 / UTC 昨日"的错位条目。
    """
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_local_day_start_utc_iso_crosses_utc_day_boundary():
    """CST 本地 00:30 → 本地日起点落在**前一个 UTC 日**（缺陷 V-3 的根因）。

    `mark_ingested` 存 UTC；用 `date.today().isoformat()`（本地日字符串）比对，
    这段时间入库的条目字典序更小 → 数不到 → 配额被突破。
    """
    cst = timezone(timedelta(hours=8))
    now_local = datetime(2026, 7, 27, 0, 30, tzinfo=cst)

    since = local_day_start_utc_iso(now_local)

    assert since == "2026-07-26T16:00:00+00:00"
    # 缺陷复现：同一本地日 00:30 入库的条目，UTC 时刻是 07-26T16:30
    ingested_at = "2026-07-26T16:30:00+00:00"
    assert ingested_at < "2026-07-27", "旧基准（本地日字符串）会漏算这条"
    assert ingested_at >= since, "新基准（本地日起点换算成 UTC）必须数得到"


# 时钟注入（本节两条生产路径测试的已知边界）：
#   `collector._remaining_daily_quota()` 直接调 `local_day_start_utc_iso()`（无参），
#   读的是墙上时间，生产路径上没有 now 注入点。所以从 `collect()` 进的测试，其入库
#   时间戳必须落在**真实当前本地日**的窗口里 —— 时/分/秒/偏移能写死，UTC 日期部分
#   不能。全字面量（连日期一起写死）的判别见
#   `test_quota_window_counts_local_early_morning_entry_all_literal`。
#
# 测试自己声明的时区假定（**不问被测代码**）：`cst_timezone` fixture 把进程时区钉在
# Asia/Shanghai = UTC+8，所以"本地日 00:00"恒等于**前一 UTC 日 16:00**，
# "本地日 00:30"恒等于前一 UTC 日 16:30。下面两个字面量就是这个换算的结果。
_ASSUMED_LOCAL_UTC_OFFSET = timedelta(hours=8)
_LOCAL_DAY_START_UTC_TIME_LITERAL = "16:00:00+00:00"   # 本地日 00:00 的 UTC 时刻
_LOCAL_EARLY_MORNING_UTC_TIME_LITERAL = "16:30:00+00:00"  # 本地日 00:30 的 UTC 时刻


def _seed_local_early_morning(store: VideoDedupStore, count: int) -> str:
    """按"本地日 00:30"入库 `count` 条，返回它们的 UTC 时间戳。

    时间戳用**写死的字面量 UTC 串**拼装（时/分/秒/`+00:00` 偏移全部字面量，换算
    关系由 `_ASSUMED_LOCAL_UTC_OFFSET` 显式声明），**绝不调用 `local_day_start_utc_iso`**
    —— 原实现用被测函数反推入库时间戳，测试数据与被测逻辑同源，于是把实现改回
    `date.today().isoformat()`（缺陷版）后两边一起偏移、测试照样通过（mutation 已证）。

    只有"UTC 日期"部分取自 stdlib 时钟（`datetime.now().date()`，与被测实现无关）：
    生产路径 `_remaining_daily_quota()` 读墙上时间且没有 now 注入点，所以窗口必须
    落在**真实当前本地日**里，日期部分无法也写死（见上方「时钟注入」说明）。
    """
    local_today = datetime.now().date()  # 进程时区 = CST（cst_timezone fixture 保证）
    utc_date = local_today - timedelta(days=1)  # UTC+8 下本地日起点落在前一 UTC 日
    stamp = f"{utc_date.isoformat()}T{_LOCAL_EARLY_MORNING_UTC_TIME_LITERAL}"
    for i in range(count):
        store.mark_ingested(
            f"https://www.youtube.com/watch?v=early{i}",
            platform="youtube",
            ingested_at=stamp,
        )
    return stamp


def _assert_cst_fixture_effective() -> None:
    """守 fixture：进程时区真的是 UTC+8，否则上面的字面量换算不成立。"""
    actual = datetime.now().astimezone().utcoffset()
    assert actual == _ASSUMED_LOCAL_UTC_OFFSET, (
        f"cst_timezone fixture 未生效（实际偏移 {actual}）→ 字面量时间戳失去意义"
    )


def test_quota_counts_entries_ingested_in_local_early_morning(dedup_config, cst_timezone):
    """**生产路径绑定**：本地凌晨（UTC 前一日）入库的条目必须算进今日配额。

    缺陷 V-3 形态：入库时间戳 UTC `…T16:30+00:00`（= 本地日 00:30），旧实现拿
    `date.today().isoformat()`（本地日字符串）当下界 → 字典序比不到 → 返回 0 →
    当日实际可入库 = 配额 + 漏算数。这条测试从 `collector.collect()` 进（生产路径），
    断言配额真的被扣光；入库时间戳是字面量 UTC 串，不经被测函数反推。
    """
    _write_sources(dedup_config.video_sources_path, {
        "nlm_daily_quota": 3, "coldstart_max_per_day": 10,
    })
    _assert_cst_fixture_effective()
    store = VideoDedupStore(dedup_config.dedup_db_path)
    _seed_local_early_morning(store, 3)

    collector = VideoCollector(dedup_config, db=store)
    with mock.patch.object(collector, "_detect_yt_videos") as detect:
        results = collector.collect()

    assert results == [], "本地凌晨入库的 3 条被漏算 → 日配额被时区错位突破"
    assert not detect.called, "配额已用尽时不该再检测（不烧额度）"


def test_quota_partially_consumed_by_local_early_morning_entries(
    dedup_config, cst_timezone
):
    """漏算是逐条的：配额 3、凌晨已入库 2 → 本轮只剩 1 个额度。

    缺陷版（`date.today().isoformat()` 当下界）下这 2 条数不到 → 剩余额度仍是 3 →
    本轮放行 3 个候选，与断言的 1 个不符。
    """
    _write_sources(dedup_config.video_sources_path, {
        "nlm_daily_quota": 3, "coldstart_max_per_day": 10,
    })
    _assert_cst_fixture_effective()
    store = VideoDedupStore(dedup_config.dedup_db_path)
    _seed_local_early_morning(store, 2)

    collector = VideoCollector(dedup_config, db=store)
    videos = [_video(f"tz{i}", title=f"AI Video {i}") for i in range(5)]
    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        results = collector.collect()

    assert len(results) == 1, f"配额 3 − 凌晨已入库 2 = 1，实得 {len(results)}"


def test_quota_window_counts_local_early_morning_entry_all_literal(tmp_path):
    """配额统计窗口的**全字面量**判别：输入、下界、期望值都写死，不依赖运行时时钟。

    固定输入 now = 本地 2026-07-27 00:30 (UTC+8)：
      正确下界 = `2026-07-26T16:00:00+00:00` → 数到 1 条（同一本地日入库）；
      缺陷下界 = `2026-07-27`（本地日字符串）→ 数到 0 条（漏算 → 配额被突破）。
    两条断言同时钉住"下界值"和"数出来的条数"，缺陷实现连第一条都过不去。
    """
    cst = timezone(timedelta(hours=8))
    store = VideoDedupStore(tmp_path / "quota" / "dedup.sqlite")
    store.mark_ingested(
        "https://www.youtube.com/watch?v=literal1",
        platform="youtube",
        ingested_at="2026-07-26T16:30:00+00:00",
    )

    since = local_day_start_utc_iso(datetime(2026, 7, 27, 0, 30, tzinfo=cst))

    assert since == f"2026-07-26T{_LOCAL_DAY_START_UTC_TIME_LITERAL}"
    assert store.count_ingested_since(since) == 1, "本地日 00:30 入库的条目必须算进当日"
    assert store.count_ingested_since("2026-07-27") == 0, (
        "对照组：本地日字符串当下界就是缺陷版，会把这条漏掉"
    )


def test_quota_exhausted_returns_empty_without_detection(dedup_config):
    """配额已用尽 → 直接返回空，连检测都不做（不烧任何额度）。"""
    _write_sources(dedup_config.video_sources_path, {"nlm_daily_quota": 1})
    store = VideoDedupStore(dedup_config.dedup_db_path)
    store.mark_ingested("https://www.youtube.com/watch?v=used1", platform="youtube")
    collector = VideoCollector(dedup_config, db=store)

    with mock.patch.object(collector, "_detect_yt_videos") as detect:
        results = collector.collect()

    assert results == []
    assert not detect.called, "配额用尽后不应再检测视频"


# ---------------------------------------------------------------------------
# #12 附带：话题聚类未覆盖索引不得静默丢弃
# ---------------------------------------------------------------------------


def test_topic_dedup_keeps_indices_llm_forgot(dedup_config):
    """LLM 分组漏掉的候选必须保留（原实现只遍历 groups → 静默丢失）。"""
    _write_sources(dedup_config.video_sources_path, {
        "dedup_strategy": "llm_cluster", "coldstart_max_per_day": 10,
    })

    class _PartialLLM:
        def chat(self, task, system, user, temperature=0.3):
            return json.dumps({"groups": [[0]]})  # 漏掉索引 1、2

    collector = VideoCollector(dedup_config, llm_adapter=_PartialLLM())
    videos = [_video(f"t{i}", title=f"AI Topic {i}") for i in range(3)]

    with mock.patch.object(collector, "_detect_yt_videos", return_value=videos):
        results = collector.collect()

    ids = sorted(r["video"]["id"] for r in results)
    assert ids == ["t0", "t1", "t2"], f"未被 LLM 提到的候选被静默丢弃: {ids}"


def test_same_video_twice_in_one_run_processed_once(dedup_config):
    """同一视频以两种 URL 形态出现在同一轮 → 只研究一次（账本此刻还没有它，L1/L7 拦不住）。"""
    _write_sources(dedup_config.video_sources_path, {"coldstart_max_per_day": 10})
    collector = VideoCollector(dedup_config)
    today = datetime.now().strftime("%Y%m%d")
    dup_a = dict(_video("same1"), url="https://www.youtube.com/watch?v=same1&t=42s")
    dup_b = dict(_video("same1"), url="https://youtu.be/same1", upload_date=today)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[dup_a, dup_b]):
        results = collector.collect()

    assert len(results) == 1, f"同一视频在同轮内被研究了 {len(results)} 次"


def test_collector_fail_closed_when_dedup_db_broken(dedup_config):
    """去重库损坏 → collect() 报错传播（fail-closed），不是"当作没入库"继续烧钱。"""
    dedup_config.dedup_db_path.parent.mkdir(parents=True, exist_ok=True)
    dedup_config.dedup_db_path.write_bytes(b"corrupted" * 100)
    collector = VideoCollector(dedup_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[_video()]):
        with pytest.raises(VideoDedupStoreError):
            collector.collect()


def test_dedup_db_path_is_outside_synced_scope():
    """默认去重库路径必须落在机器本地状态根（不进 Syncthing/vault，缺陷 #11 约定）。"""
    from src.akb2.common.state_paths import get_state_root, is_synced_path

    config = VideoConfig(akb2_root=Path("/tmp/does-not-matter"))
    assert not is_synced_path(config.dedup_db_path)
    assert str(config.dedup_db_path).startswith(str(get_state_root()))


def test_store_schema_has_identity_primary_key(tmp_path):
    """去重表主键是 identity（稳定身份），不是 title/slug。"""
    store = VideoDedupStore(tmp_path / "dedup.sqlite")
    store.mark_ingested("https://www.youtube.com/watch?v=abc123", platform="youtube")
    conn = sqlite3.connect(str(tmp_path / "dedup.sqlite"))
    try:
        cols = {
            row[1]: row[5]  # name -> pk flag
            for row in conn.execute("PRAGMA table_info(video_ingested)")
        }
    finally:
        conn.close()
    assert cols["identity"] == 1
    assert cols.get("title") == 0


# ---------------------------------------------------------------------------
# V-2 CLI 后门路径：--source / --fixture 也必须查/记账本
# ---------------------------------------------------------------------------


def _stub_pipeline_stages(video_main, monkeypatch, video_meta: dict) -> dict:
    """把 researcher/extractor/reasoner 换成不花钱的 stub，并记录调用次数。"""
    calls = {"research": 0}

    class _Researcher:
        def __init__(self, *a, **k):
            pass

        def run(self, video_results):
            calls["research"] += 1
            return [ResearchOutput(
                report_markdown="# LLM 改写过的标题\n\n## 执行摘要\n\n内容。",
                notebook_id="nb-cli",
                video_meta=video_meta,
                degraded=False,
            )]

    class _Extractor:
        def __init__(self, *a, **k):
            pass

        def extract(self, md):
            return ExtractedFields(
                tldr="要点",
                key_findings=[KeyFinding(
                    assertion="断言", source_span=SourceSpan(line_start=1, line_end=1),
                    confidence=0.9,
                )],
                future_questions=[],
                concepts_mentioned=["AI"],
            )

    class _Reasoner:
        def __init__(self, *a, **k):
            pass

        def reason(self, concepts, context=""):
            return ReasonedLinks(related_concept=[], proposed_graph_nodes=[])

    monkeypatch.setattr(video_main, "VideoResearcher", _Researcher)
    monkeypatch.setattr(video_main, "Extractor", _Extractor)
    monkeypatch.setattr(video_main, "Reasoner", _Reasoner)
    monkeypatch.setattr(video_main, "ConceptStore", lambda *a, **k: object())
    monkeypatch.setattr(video_main, "_build_llm_adapter", lambda config: None)
    monkeypatch.setattr(video_main, "VideoConfig", lambda: _CLI_CONFIG["config"])
    return calls


_CLI_CONFIG: dict = {}


@pytest.fixture
def cli_config(dedup_config):
    """给 `main()` 用的 config（main 自己 new VideoConfig，只能靠类替换注入）。"""
    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)
    _CLI_CONFIG["config"] = dedup_config
    yield dedup_config
    _CLI_CONFIG.clear()


def test_cli_source_path_marks_dedup_ledger(cli_config, monkeypatch):
    """**生产路径绑定**：从 CLI `--source` 进，发布后账本必须被登记（缺陷 V-2）。

    回归前 `_run_single_source` 是裸 `assembler.commit(candidate_md)`：不查账本、
    不登记、commit 不传 slug（产出文件名与主路径不同族）。于是 `--source` 是一条
    官方后门 —— 每次手动跑都重新烧一份 NLM 配额，且账本永远看不到。
    """
    from src.akb2.video import __main__ as video_main

    url = "https://www.youtube.com/watch?v=cliSrc001"
    video_meta = dict(_video("cliSrc001"), url=url, title="CLI Source Video")
    _stub_pipeline_stages(video_main, monkeypatch, video_meta)

    with mock.patch.object(Assembler, "_gate_decision", return_value="active"):
        rc = video_main.main(["--source", url, "--skip-preflight"])

    assert rc == 0
    store = VideoDedupStore(cli_config.dedup_db_path)
    assert store.is_ingested(url) is True, (
        "--source 发布后账本未登记 → 下次手动跑会重复烧 NLM 配额"
    )


def test_cli_source_path_filename_matches_full_pipeline_family(cli_config, monkeypatch):
    """`--source` 产出文件名必须带身份哈希（与主路径同族，缺陷 V-2）。"""
    from src.akb2.video import __main__ as video_main

    url = "https://www.youtube.com/watch?v=cliSrc002"
    video_meta = dict(_video("cliSrc002"), url=url, title="CLI Source Video")
    _stub_pipeline_stages(video_main, monkeypatch, video_meta)

    with mock.patch.object(Assembler, "_gate_decision", return_value="active"):
        assert video_main.main(["--source", url, "--skip-preflight"]) == 0

    published = list(cli_config.promotion_target_path.glob("*.md"))
    assert len(published) == 1
    expected_slug = Assembler(cli_config)._slug_for_video(video_meta)
    assert published[0].name == f"video-{expected_slug}.md", (
        f"文件名与主路径不同族: {published[0].name}"
    )


def test_cli_source_path_skips_already_ingested_before_spending(cli_config, monkeypatch):
    """`--source` 必须在 researcher 之前查账本（花钱之前，缺陷 V-2）。"""
    from src.akb2.video import __main__ as video_main

    url = "https://www.youtube.com/watch?v=cliSrc003"
    video_meta = dict(_video("cliSrc003"), url=url)
    calls = _stub_pipeline_stages(video_main, monkeypatch, video_meta)
    VideoDedupStore(cli_config.dedup_db_path).mark_ingested(url, platform="youtube")

    rc = video_main.main(["--source", url, "--skip-preflight"])

    assert rc == 0
    assert calls["research"] == 0, (
        "已入库视频仍然进了 researcher → NLM 深研配额被白烧一份"
    )
    assert list(cli_config.promotion_target_path.glob("*.md")) == []


def test_cli_source_path_seeds_history_before_spending(cli_config, monkeypatch):
    """`--source` 必须在花钱之前完成历史回填（第二轮复核补的缺口）。

    场景：**账本里没有该视频**（账本实证会缺记录：V-1 部分回填、换机器后账本为空），
    但历史产出目录里已经有它的报告。修复前 `--source` 只查账本、不做 `_seed_history`
    → 查不到 → 先把 NLM 深研 + LLM 额度全烧完，最后才在 `assembler.commit` 撞文件名
    fail-closed 停下。"花钱之前拦"于是只对"账本里已有记录"成立。

    断言绑定可观测行为：researcher 调用次数为 0，且没写出第二份产出。
    """
    from src.akb2.video import __main__ as video_main

    url = "https://www.youtube.com/watch?v=cliSrc004"
    video_meta = dict(_video("cliSrc004"), url=url, title="CLI Source Video")
    calls = _stub_pipeline_stages(video_main, monkeypatch, video_meta)

    # 历史产出已存在（账本为空 —— 故意不 mark_ingested）
    existing = cli_config.promotion_target_path / "video-cli-source-video-deadbeef.md"
    existing.write_text(
        f"---\ntitle: \"CLI Source Video\"\nsource_url: {url}\n---\n# 旧报告\n",
        encoding="utf-8",
    )
    store = VideoDedupStore(cli_config.dedup_db_path)
    assert store.is_ingested(url) is False, "前置条件：账本必须是空的"

    with mock.patch.object(Assembler, "_gate_decision", return_value="active"):
        rc = video_main.main(["--source", url, "--skip-preflight"])

    # 承重断言放最前：花钱这件事本身没发生
    assert calls["research"] == 0, (
        "账本缺记录、但历史产出里已有该视频 → 仍然进了 researcher："
        "NLM 深研 + LLM 额度先被烧掉"
    )
    # 历史文件用的是旧命名（无身份哈希或哈希不同），所以 commit 的文件名 fail-closed
    # 也拦不住 —— 缺陷版会**再写一份**产出。
    assert [p.name for p in cli_config.promotion_target_path.glob("*.md")] == [
        existing.name
    ], "不得写出第二份产出"
    assert rc == 0
    assert store.is_ingested(url) is True, "历史回填应把该身份登进账本"


def test_cli_fixture_path_marks_dedup_ledger(cli_config, monkeypatch):
    """**生产路径绑定**：`--fixture`（非 dry-run）也真写 vault，必须查/记账本。"""
    from src.akb2.video import __main__ as video_main

    monkeypatch.setattr(video_main, "VideoConfig", lambda: cli_config)
    fixture_url = "https://www.youtube.com/watch?v=test123"

    with mock.patch.object(Assembler, "_gate_decision", return_value="active"):
        assert video_main.main(["--fixture", "--skip-preflight"]) == 0

    store = VideoDedupStore(cli_config.dedup_db_path)
    assert store.is_ingested(fixture_url) is True, (
        "--fixture 非 dry-run 写了 vault 却不登记账本 → 主链路明天会重跑同一视频"
    )
    published = [p.name for p in cli_config.promotion_target_path.glob("*.md")]
    assert len(published) == 1
    # 精确文件名：必须与主路径同族 `video-{_slug_for_video(...)}.md`
    # （原来这里是 `endswith(".md")` + `"-" in name` 两条恒真噪音断言，
    #  任何文件名都能过，等于没断言文件名）。
    expected_slug = Assembler(cli_config)._slug_for_video({
        "title": "DeepSeek V4 Architecture Deep Dive",
        "url": fixture_url,
    })
    assert published[0] == f"video-{expected_slug}.md", (
        f"--fixture 产出文件名与主路径不同族: {published[0]}"
    )

    # 第二次跑：账本已有 → 直接跳过，不再产出第二份
    with mock.patch.object(Assembler, "_gate_decision", return_value="active"):
        assert video_main.main(["--fixture", "--skip-preflight"]) == 0
    assert len(list(cli_config.promotion_target_path.glob("*.md"))) == 1, (
        "--fixture 第二次跑产出了重复文件"
    )


# ---------------------------------------------------------------------------
# V-4 正式产出文件名不含日期 → 跨日 fail-closed 真的会触发
# ---------------------------------------------------------------------------


def test_published_filename_has_no_date(dedup_config):
    """正式产出名 = `video-{slug}.md`，不含日期（缺陷 V-4）。"""
    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)
    assembler = Assembler(dedup_config)
    md = "---\ntitle: \"T\"\n---\n# T\n"

    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        published = assembler.commit(md, slug="abc-12345678", date_str="2026-07-26")

    assert published.name == "video-abc-12345678.md", (
        f"正式产出名仍含日期: {published.name} → 跨日重复永不触发 fail-closed"
    )


def test_cross_day_duplicate_blocked_by_filename_fail_closed(dedup_config):
    """同一身份跨日再发布 → `CandidateTargetExists`（账本之外的第二道防线）。

    回归前文件名含当日日期，跨日重复每天都是新名字 → 这道 fail-closed 恒不触发，
    跨日防线只剩账本一个单点（而账本已被 V-1/V-2/V-3 打出三个缺口）。
    """
    from src.akb2.video.assembler import CandidateTargetExists

    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)
    assembler = Assembler(dedup_config)
    video_meta = _video("crossday1")
    slug = assembler._slug_for_video(video_meta)
    md = "---\ntitle: \"T\"\n---\n# T\n"

    with mock.patch.object(assembler, "_gate_decision", return_value="active"):
        assembler.commit(md, slug=slug, date_str="2026-07-26")
        with pytest.raises(CandidateTargetExists):
            assembler.commit(md, slug=slug, date_str="2026-07-27")

    assert len(list(dedup_config.promotion_target_path.glob("*.md"))) == 1


def test_needs_repair_can_still_retry_next_day(dedup_config):
    """staging 中转名保留日期：needs_repair 跨日重试不得被文件名撞死。"""
    assembler = Assembler(dedup_config)
    md = "---\ntitle: \"T\"\n---\n# T\n"

    with mock.patch.object(assembler, "_gate_decision", return_value="needs_repair"):
        first = assembler.commit(md, slug="rep-1234abcd", date_str="2026-07-26")
        second = assembler.commit(md, slug="rep-1234abcd", date_str="2026-07-27")

    assert first != second
    assert first.exists() and second.exists()


# ---------------------------------------------------------------------------
# 关键测试缺口：第二轮 run_pipeline 一次都不碰 researcher（省额度的直接证据）
# ---------------------------------------------------------------------------


def test_second_full_pipeline_run_does_not_call_researcher(dedup_config):
    """同一视频第二轮跑完整 `run_pipeline` → researcher.run 调用次数为 0。

    这是"第二轮不再重复花钱"的唯一直接证据（省下的具体调用次数无遥测、不可考，
    见 `dedup_store` 模块 docstring 的实测口径）：
    真正花钱的 researcher/extractor/reasoner 在 `collector.collect()` 里根本不参与，
    只断言 collect() 返回空、或断言 `_dedup_by_topic` 的 LLM 计数（单候选时恒为 0，
    等于 `0 == 0` 的空断言）都测不到这件事。
    """
    dedup_config.promotion_target_path.mkdir(parents=True, exist_ok=True)
    store = VideoDedupStore(dedup_config.dedup_db_path)
    video = _video("payonce1")
    assembler = Assembler(dedup_config)

    class _CountingResearcher:
        def __init__(self):
            self.calls = 0

        def run(self, video_results):
            self.calls += 1
            return [ResearchOutput(
                report_markdown="# 报告\n\n## 执行摘要\n\n内容。",
                notebook_id="nb-1", video_meta=video, degraded=False,
            )]

    class _Extractor:
        def extract(self, md):
            return ExtractedFields(
                tldr="要点",
                key_findings=[KeyFinding(
                    assertion="断言", source_span=SourceSpan(line_start=1, line_end=1),
                    confidence=0.9,
                )],
                future_questions=[], concepts_mentioned=["AI"],
            )

    class _Reasoner:
        def reason(self, concepts, context=""):
            return ReasonedLinks(related_concept=[], proposed_graph_nodes=[])

    researcher = _CountingResearcher()

    def _run_round():
        collector = VideoCollector(dedup_config, db=store)
        with mock.patch.object(
            collector, "_detect_yt_videos", return_value=[dict(video)]
        ):
            with mock.patch.object(assembler, "_gate_decision", return_value="active"):
                return assembler.run_pipeline(
                    collector, researcher, _Extractor(), _Reasoner(),
                )

    first = _run_round()
    assert [r["status"] for r in first] == ["ok"]
    assert researcher.calls == 1
    calls_after_first = researcher.calls

    second = _run_round()

    assert second == [], "第二轮必须在 collector 阶段止步"
    assert researcher.calls == calls_after_first == 1, (
        f"第二轮又调了 researcher {researcher.calls - calls_after_first} 次 —— "
        "NLM 深研配额被重复烧掉，这正是重复产出（608 份 → 230 个身份）的花钱出口"
    )
    assert len(list(dedup_config.promotion_target_path.glob("*.md"))) == 1


# ---------------------------------------------------------------------------
# 顺带：无 url 候选必须拒收（fail-closed 的那条缝）
# ---------------------------------------------------------------------------


def test_candidate_without_url_is_rejected_at_l1(dedup_config):
    """候选缺 url → 无稳定身份 → 拒收（原实现 warning 后放行）。"""
    _write_sources(dedup_config.video_sources_path, {"coldstart_max_per_day": 10})
    collector = VideoCollector(dedup_config)
    no_url = dict(_video("nourl1"))
    no_url["url"] = ""

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[no_url]):
        results = collector.collect()

    assert results == [], "无 url 候选被放行 → 它永远登记不上，每轮都会重跑"
    assert collector._url_missing_rejected == 1


def test_dedup_by_identity_drops_candidates_without_url(dedup_config):
    """同轮身份去重同样拒收无 url 候选（原实现 `kept.append`）。"""
    collector = VideoCollector(dedup_config)
    kept = collector._dedup_by_identity([
        {"video": {"title": "有 url", "url": "https://youtu.be/keep1"}, "creator": {}},
        {"video": {"title": "无 url", "url": ""}, "creator": {}},
    ])

    assert [c["video"]["title"] for c in kept] == ["有 url"]


def test_mark_ingested_wiring_error_is_not_masked_as_ledger_failure(dedup_config):
    """collector 缺 `mark_ingested`（接线/改名错）不得被降级成"账本写失败"。"""
    assembler = Assembler(dedup_config)

    class _NoMarkCollector:
        pass

    with pytest.raises(AttributeError):
        assembler._mark_ingested(_NoMarkCollector(), _video(), Path("/tmp/x.md"))
