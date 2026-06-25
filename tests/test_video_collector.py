"""视频采集器测试 — VideoCollector 迁移验证。

覆盖：L1-L7 + L4.5 每层独立测试 + yt-dlp failure path。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.video.collector import CatalogDBAdapter, VideoCollector
from src.akb2.video.config import VideoConfig
from src.akb2.video.llm_adapter import LLMAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_config() -> VideoConfig:
    """使用临时目录的 VideoConfig，避免污染真实配置。"""
    with tempfile.TemporaryDirectory() as tmp:
        sources_path = Path(tmp) / "video_sources.json"
        keywords_path = Path(tmp) / "keywords.json"
        # 写入最小 video_sources.json
        sources_path.write_text(json.dumps({
            "creators": [
                {
                    "id": "yt-test",
                    "platform": "youtube",
                    "channel_url": "https://www.youtube.com/@TestChannel",
                    "name": "Test Channel",
                    "enabled": True,
                    "max_per_day": 1,
                    "keywords": [],
                    "tier": "quality",
                },
            ],
            "settings": {
                "min_video_duration_minutes": 3,
                "max_video_duration_minutes": 0,
                "days_lookback": 14,
                "dedup_strategy": "llm_cluster",
                "skip_relevance_filter": False,
                "skip_keyword_filter": True,
            },
        }, ensure_ascii=False))
        # 写入最小 keywords.json
        keywords_path.write_text(json.dumps({
            "include": {"core": ["AI", "LLM", "agent"]},
            "exclude": {"non_ai": ["cooking", "gaming"]},
        }, ensure_ascii=False))
        config = VideoConfig(
            video_sources_path=sources_path,
            keywords_path=keywords_path,
        )
        yield config


@pytest.fixture
def mock_ytdlp_output():
    """模拟 yt-dlp --flat-playlist --dump-json 输出。"""
    return [
        {
            "id": "abc123",
            "title": "How to Build AI Agents with LangGraph",
            "url": "https://www.youtube.com/watch?v=abc123",
            "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "duration": 1200,  # 20 min
            "upload_date": "20260525",
        },
        {
            "id": "def456",
            "title": "DeepSeek V4 Architecture Deep Dive",
            "url": "https://www.youtube.com/watch?v=def456",
            "webpage_url": "https://www.youtube.com/watch?v=def456",
            "duration": 2400,  # 40 min
            "upload_date": "20260528",
        },
        {
            "id": "ghi789",
            "title": "Cooking Pasta at Home",
            "url": "https://www.youtube.com/watch?v=ghi789",
            "webpage_url": "https://www.youtube.com/watch?v=ghi789",
            "duration": 600,  # 10 min
            "upload_date": "20260527",
        },
    ]


class MockLLMAdapter:
    """L5 测试用的 mock LLM adapter — 验证调用但不调真实 API。"""

    def __init__(self, groups: list[list[int]] | None = None):
        self.groups = groups or [[0], [1], [2]]
        self.calls: list[dict] = []

    def chat(self, task: str, system: str, user: str, temperature: float = 0.3) -> str:
        self.calls.append({"task": task, "system": system, "user": user, "temperature": temperature})
        return json.dumps({"groups": self.groups})


# ---------------------------------------------------------------------------
# L1: URL 去重测试
# ---------------------------------------------------------------------------


def test_L1_url_dedup(temp_config, mock_ytdlp_output):
    """L1: URL 去重 — mock catalog.db 已有 abc123，验证该视频被过滤。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "catalog.db"
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS items (url TEXT)")
        conn.execute("INSERT INTO items (url) VALUES (?)", ("https://www.youtube.com/watch?v=abc123",))
        conn.commit()
        conn.close()

        db = CatalogDBAdapter(db_path)
        collector = VideoCollector(temp_config, db=db)

        with mock.patch.object(collector, "_detect_yt_videos", return_value=[
            {
                "platform": "youtube", "id": "abc123",
                "title": "How to Build AI Agents with LangGraph",
                "url": "https://www.youtube.com/watch?v=abc123",
                "duration_minutes": 20, "upload_date": "20260525", "creator": "Test Channel",
            },
            {
                "platform": "youtube", "id": "def456",
                "title": "DeepSeek V4 Architecture Deep Dive",
                "url": "https://www.youtube.com/watch?v=def456",
                "duration_minutes": 40, "upload_date": "20260528", "creator": "Test Channel",
            },
        ]):
            results = collector.collect()

        urls = [r["video"]["url"] for r in results]
        assert "https://www.youtube.com/watch?v=abc123" not in urls


# ---------------------------------------------------------------------------
# L2: start_date 过滤
# ---------------------------------------------------------------------------


def test_L2_start_date_filter(temp_config, mock_ytdlp_output):
    """L2: start_date 过滤 — video_sources.json start_date 为 2026-05-28，旧视频应被过滤。"""
    temp_config.video_sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources = json.loads(temp_config.video_sources_path.read_text())
    sources["settings"]["start_date"] = "2026-05-28"
    sources["settings"]["coldstart"] = True  # 禁用 backfill，避免读取真实 pending
    sources["settings"]["coldstart_max_per_day"] = 5
    temp_config.video_sources_path.write_text(json.dumps(sources))

    collector = VideoCollector(temp_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[
        {
            "platform": "youtube", "id": "old1",
            "title": "Old AI Video", "url": "https://youtube.com/watch?v=old1",
            "duration_minutes": 15, "upload_date": "20260520", "creator": "Test Channel",
        },
        {
            "platform": "youtube", "id": "new1",
            "title": "New AI Video", "url": "https://youtube.com/watch?v=new1",
            "duration_minutes": 20, "upload_date": "20260528", "creator": "Test Channel",
        },
    ]):
        results = collector.collect()

    assert len(results) == 1
    assert results[0]["video"]["id"] == "new1"


# ---------------------------------------------------------------------------
# L3: duration 过滤
# ---------------------------------------------------------------------------


def test_L3_duration_filter(temp_config):
    """L3: 时长过滤 — 小于 VIDEO_MIN_DURATION_MIN (3min) 的视频应被排除。"""
    temp_config.video_sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources = json.loads(temp_config.video_sources_path.read_text())
    sources["settings"]["coldstart"] = True  # 禁用 backfill，避免读取真实 pending
    sources["settings"]["coldstart_max_per_day"] = 5
    temp_config.video_sources_path.write_text(json.dumps(sources))

    collector = VideoCollector(temp_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[
        {
            "platform": "youtube", "id": "short1",
            "title": "AI Short", "url": "https://youtube.com/watch?v=short1",
            "duration_minutes": 0.5, "upload_date": "20260528", "creator": "Test Channel",
        },
        {
            "platform": "youtube", "id": "long1",
            "title": "Long AI Tutorial", "url": "https://youtube.com/watch?v=long1",
            "duration_minutes": 25, "upload_date": "20260528", "creator": "Test Channel",
        },
    ]):
        results = collector.collect()

    assert len(results) >= 1
    ids = [r["video"]["id"] for r in results]
    assert "short1" not in ids
    assert "long1" in ids


# ---------------------------------------------------------------------------
# L4: keyword 过滤
# ---------------------------------------------------------------------------


def test_L4_keyword_filter(temp_config):
    """L4: 关键词过滤 — creator 设了 keywords 的，标题不匹配应过滤。"""
    temp_config.video_sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources = json.loads(temp_config.video_sources_path.read_text())
    sources["settings"]["skip_keyword_filter"] = False  # 启用 L4
    sources["settings"]["skip_relevance_filter"] = True  # 禁用 L4.5 避免干扰
    sources["settings"]["coldstart"] = True  # 禁用 backfill，避免读取真实 pending
    sources["settings"]["coldstart_max_per_day"] = 5
    sources["creators"][0]["keywords"] = ["Claude", "GPT"]
    temp_config.video_sources_path.write_text(json.dumps(sources))

    collector = VideoCollector(temp_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[
        {
            "platform": "youtube", "id": "match1",
            "title": "Claude Code New Features", "url": "https://youtube.com/watch?v=match1",
            "duration_minutes": 10, "upload_date": "20260528", "creator": "Test Channel",
        },
        {
            "platform": "youtube", "id": "nomatch1",
            "title": "History of Computing", "url": "https://youtube.com/watch?v=nomatch1",
            "duration_minutes": 15, "upload_date": "20260528", "creator": "Test Channel",
        },
    ]):
        results = collector.collect()

    ids = [r["video"]["id"] for r in results]
    assert "match1" in ids
    assert "nomatch1" not in ids


# ---------------------------------------------------------------------------
# L4.5: AI 相关性过滤
# ---------------------------------------------------------------------------


def test_L4_5_ai_relevance(temp_config):
    """L4.5: AI 关键词相关性 — 非 AI 视频标题应被过滤。"""
    temp_config.video_sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources = json.loads(temp_config.video_sources_path.read_text())
    sources["settings"]["skip_relevance_filter"] = False
    sources["settings"]["coldstart"] = True  # 禁用 backfill，避免读取真实 pending
    sources["settings"]["coldstart_max_per_day"] = 5
    temp_config.video_sources_path.write_text(json.dumps(sources))

    collector = VideoCollector(temp_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[
        {
            "platform": "youtube", "id": "ai1",
            "title": "Building LLM Applications in 2026", "url": "https://youtube.com/watch?v=ai1",
            "duration_minutes": 20, "upload_date": "20260528", "creator": "Test Channel",
        },
        {
            "platform": "youtube", "id": "cooking1",
            "title": "Easy Pasta Recipes for Dinner", "url": "https://youtube.com/watch?v=cooking1",
            "duration_minutes": 12, "upload_date": "20260528", "creator": "Test Channel",
        },
    ]):
        results = collector.collect()

    ids = [r["video"]["id"] for r in results]
    assert "ai1" in ids
    assert "cooking1" not in ids


# ---------------------------------------------------------------------------
# L5: 话题聚类去重
# ---------------------------------------------------------------------------


def test_L5_topic_dedup(temp_config):
    """L5: 话题去重 — 同话题视频去重，只保留最长。"""
    temp_config.video_sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources = json.loads(temp_config.video_sources_path.read_text())
    sources["settings"]["skip_relevance_filter"] = True  # 禁用 L4.5 避免干扰
    sources["settings"]["coldstart"] = True  # 禁用配额回填避免绕过 L6
    sources["settings"]["coldstart_max_per_day"] = 3  # L6 允许 3 个视频进入 L5 去重
    sources["settings"]["dedup_strategy"] = "llm_cluster"
    temp_config.video_sources_path.write_text(json.dumps(sources))

    mock_llm = MockLLMAdapter(groups=[[0, 1], [2]])  # 前两个同一话题

    collector = VideoCollector(temp_config, llm_adapter=mock_llm)

    # disable L1/L2/L3/L4 for this test
    with mock.patch.object(collector, "_is_video_ingested", return_value=False):
        with mock.patch.object(collector, "_detect_yt_videos", return_value=[
            {
                "platform": "youtube", "id": "dup1", "title": "DeepSeek V4 Review (short)",
                "url": "https://youtube.com/watch?v=dup1", "duration_minutes": 5,
                "upload_date": "20260528", "creator": "Test Channel",
            },
            {
                "platform": "youtube", "id": "dup2", "title": "DeepSeek V4 Full Analysis",
                "url": "https://youtube.com/watch?v=dup2", "duration_minutes": 45,
                "upload_date": "20260528", "creator": "Test Channel",
            },
            {
                "platform": "youtube", "id": "unique1", "title": "MCP Protocol Tutorial",
                "url": "https://youtube.com/watch?v=unique1", "duration_minutes": 30,
                "upload_date": "20260528", "creator": "Test Channel",
            },
        ]):
            results = collector.collect()

    # Should have 2 results: best from group 0 (dup2, 45min) + unique1
    assert len(results) == 2
    ids = [r["video"]["id"] for r in results]
    assert "dup2" in ids
    assert "unique1" in ids
    assert "dup1" not in ids  # shorter duplicate
    assert len(mock_llm.calls) == 1  # LLM was called


# ---------------------------------------------------------------------------
# L6: per-creator cap
# ---------------------------------------------------------------------------


def test_L6_creator_cap(temp_config):
    """L6: per-creator 上限 — 同一 creator 只取 max_per_day 个。"""
    temp_config.video_sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources = json.loads(temp_config.video_sources_path.read_text())
    sources["settings"]["skip_relevance_filter"] = True
    sources["settings"]["coldstart"] = True  # 禁用配额回填
    sources["settings"]["coldstart_max_per_day"] = 2  # L6 上限
    sources["settings"]["dedup_strategy"] = "none"  # disable L5
    temp_config.video_sources_path.write_text(json.dumps(sources))

    collector = VideoCollector(temp_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[
        {
            "platform": "youtube", "id": f"v{i}", "title": f"AI Video {i}",
            "url": f"https://youtube.com/watch?v=v{i}", "duration_minutes": 10 + i,
            "upload_date": "20260528", "creator": "Test Channel",
        }
        for i in range(5)
    ]):
        results = collector.collect()

    # Should cap at 2 (max_per_day)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# L7: catalog 去重
# ---------------------------------------------------------------------------


def test_L7_catalog_dedup(temp_config):
    """L7: catalog.db 去重 — 已入库 URL 在最后阶段过滤。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "catalog.db"
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS items (url TEXT)")
        conn.execute("INSERT INTO items (url) VALUES (?)", ("https://youtube.com/watch?v=ingested1",))
        conn.commit()
        conn.close()

        db = CatalogDBAdapter(db_path)
        temp_config.video_sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources = json.loads(temp_config.video_sources_path.read_text())
        sources["settings"]["skip_relevance_filter"] = True
        sources["settings"]["dedup_strategy"] = "none"
        sources["settings"]["coldstart"] = True  # 禁用 backfill
        sources["settings"]["coldstart_max_per_day"] = 5
        temp_config.video_sources_path.write_text(json.dumps(sources))

        collector = VideoCollector(temp_config, db=db)

        with mock.patch.object(collector, "_detect_yt_videos", return_value=[
            {
                "platform": "youtube", "id": "ingested1", "title": "Already Ingested",
                "url": "https://youtube.com/watch?v=ingested1", "duration_minutes": 15,
                "upload_date": "20260528", "creator": "Test Channel",
            },
            {
                "platform": "youtube", "id": "new1", "title": "New AI Video",
                "url": "https://youtube.com/watch?v=new1", "duration_minutes": 20,
                "upload_date": "20260528", "creator": "Test Channel",
            },
        ]):
            results = collector.collect()

        # L7 dedup: only new1 should survive
        assert len(results) >= 1
        ids = [r["video"]["id"] for r in results]
        assert "new1" in ids


# ---------------------------------------------------------------------------
# yt-dlp failure 容错
# ---------------------------------------------------------------------------


def test_ytdlp_failure_graceful(temp_config):
    """yt-dlp 失败容错 — subprocess 超时/非零退出/JSON 解析失败不崩溃。"""
    collector = VideoCollector(temp_config)

    # Case 1: TimeoutExpired
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=120)):
        results = collector._detect_yt_videos(
            {"platform": "youtube", "channel_url": "https://youtube.com/@test", "name": "Test"},
            days=14,
        )
        assert results == []

    # Case 2: non-zero exit + malformed JSON
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=1, stdout="not valid json\n", stderr="error"
        )
        results = collector._detect_yt_videos(
            {"platform": "youtube", "channel_url": "https://youtube.com/@test", "name": "Test"},
            days=14,
        )
        assert results == []


# ---------------------------------------------------------------------------
# 配置注入验证
# ---------------------------------------------------------------------------


def test_ytdlp_config_injection():
    """验证 yt-dlp 路径通过 config 注入，collector 代码中不含硬编码绝对路径。"""
    collector_src = Path(__file__).parent.parent / "src" / "akb2" / "video" / "collector.py"
    src = collector_src.read_text()
    # 不含有裸 'yt-dlp' 字符串（除了 ytdlp_bin 属性引用）
    # 不含有 Path.home() 直接调用
    assert "Path.home()" not in src, "collector.py 不应直接调用 Path.home()"
    # 验证 config.ytdlp_bin 被使用
    assert "self.config.ytdlp_bin" in src or "config.ytdlp_bin" in src


def test_config_import():
    """验证 VideoConfig 可导入且有默认值。"""
    from src.akb2.video.config import VideoConfig
    c = VideoConfig()
    assert c.ytdlp_bin
    assert len(c.ytdlp_bin) > 0


def test_collector_import():
    """验证 VideoCollector 可独立 import。"""
    from src.akb2.video.collector import VideoCollector
    assert VideoCollector is not None


def test_collect_runs(temp_config):
    """验证 collector.collect() 可运行（mock yt-dlp）。"""
    collector = VideoCollector(temp_config)

    with mock.patch.object(collector, "_detect_yt_videos", return_value=[
        {
            "platform": "youtube", "id": "test1", "title": "AI Agents Tutorial",
            "url": "https://youtube.com/watch?v=test1", "duration_minutes": 15,
            "upload_date": "20260528", "creator": "Test Channel",
        },
    ]):
        results = collector.collect()

    assert len(results) >= 0  # collect() should not crash


def test_flock_usage():
    """验证 collector 使用了 fcntl.flock 保护 pending_research.json。"""
    collector_src = Path(__file__).parent.parent / "src" / "akb2" / "video" / "collector.py"
    src = collector_src.read_text()
    assert "fcntl" in src or "flock" in src or "LOCK_EX" in src, "collector.py 必须使用文件锁"


def test_corrupt_pending_is_backed_up_not_silently_dropped(temp_config, tmp_path):
    """损坏的 pending_research.json（git 冲突标记）必须备份历史，绝不静默清空。

    回归前：except JSONDecodeError → pass → existing=[] → truncate+dump 全量覆盖，
    历史视频队列无声丢失。修复后：原文备份到 .corrupt-*，历史可追回。
    """
    collector = VideoCollector(temp_config)
    # 隔离 pending 路径到 tmp_path，避免污染工作区 staging/。
    pending_path = tmp_path / "pending_research.json"

    # 写入「含真实历史 + git 冲突标记」的损坏文件。
    corrupt_content = (
        "[\n"
        '  {"url": "https://youtube.com/watch?v=HIST1", "title": "历史视频1", '
        '"platform": "youtube", "creator": "C", "retry_count": 2},\n'
        "<<<<<<< HEAD\n"
        '  {"url": "https://youtube.com/watch?v=HIST2", "title": "历史视频2"}\n'
        "=======\n"
        "]\n"
    )
    pending_path.write_text(corrupt_content, encoding="utf-8")

    # 追加一个新失败视频。
    new_result = {
        "video": {
            "url": "https://youtube.com/watch?v=NEW1",
            "title": "新失败视频",
            "platform": "youtube",
            "creator": "C",
        }
    }
    with mock.patch.object(collector, "_pending_path", return_value=pending_path):
        collector._save_pending([new_result])

    # 1) 新文件必须是合法 JSON 数组，且含新视频。
    saved = json.loads(pending_path.read_text(encoding="utf-8"))
    assert isinstance(saved, list)
    saved_urls = {item.get("url") for item in saved}
    assert "https://youtube.com/watch?v=NEW1" in saved_urls

    # 2) 历史必须被保全到 .corrupt-* 备份（不是无声丢失）。
    backups = list(pending_path.parent.glob("pending_research.corrupt-*.json"))
    assert backups, "损坏的 pending 历史必须备份，而非静默清空"
    backup_text = backups[0].read_text(encoding="utf-8")
    assert "HIST1" in backup_text, "备份必须保留原始历史内容"
    assert "<<<<<<< HEAD" in backup_text, "备份必须是原始损坏原文"
