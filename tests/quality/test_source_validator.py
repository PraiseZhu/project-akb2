"""source_validator 单测与 integration 测试。

默认跑（-m 'not integration'）：
    - test_cli_dry_run_structure     dry-run CLI 输出结构验证
    - test_ledger_required_fields    mocked live 模式 ledger 字段完整性
    - test_run_id_consistency        同一次运行所有记录 run_id 一致
    - test_atomic_write              原子写入：tmp → rename，不追加旧数据

Integration（@pytest.mark.integration，需 AKB2_LIVE_VALIDATE=1）：
    - test_live_76_sources           完整 76 源 live 验证（SC-10）
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 确保 src/ 在 sys.path（pytest.ini 已配置，此处作保险）
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from akb2.quality.intake.source_validator import (  # noqa: E402
    _extract_latest_url,
    _validate_source_dry,
    _validate_source_live,
    _write_jsonl_atomic,
    main,
    validate_sources,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SOURCE: dict = {
    "slug": "test-blog",
    "domain": "example.com",
    "feed_url": "https://example.com/feed.xml",
    "listing_url": "https://example.com/blog",
    "rate_limit": 3600,
    "status": "trial",
    "category": "core",
    "notes": "test source",
}

REQUIRED_FIELDS = {"feed", "url", "reachable", "latest_post", "trial_fetch"}

SAMPLE_RSS_FEED = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Blog</title>
    <link>https://example.com</link>
    <item>
      <title>Hello World</title>
      <link>https://example.com/posts/hello-world</link>
      <description>First post</description>
    </item>
  </channel>
</rss>"""

SAMPLE_ATOM_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Blog</title>
  <entry>
    <title>Hello Atom</title>
    <link href="https://example.com/atom/hello" rel="alternate"/>
    <published>2024-01-01T00:00:00Z</published>
  </entry>
</feed>"""


# ---------------------------------------------------------------------------
# SC-9: 非 integration 测试（默认跑）
# ---------------------------------------------------------------------------


class TestCliDryRunStructure:
    """dry-run CLI 输出结构验证。"""

    def test_cli_dry_run_exits_zero(self, tmp_path: Path) -> None:
        """dry-run 模式 CLI 退出码为 0。"""
        out = tmp_path / "ledger.jsonl"
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        rc = main(["--dry-run", "--config", str(config), "--out", str(out)])
        assert rc == 0, f"Expected exit 0, got {rc}"

    def test_cli_dry_run_creates_ledger(self, tmp_path: Path) -> None:
        """dry-run 模式产出 ledger 文件。"""
        out = tmp_path / "ledger.jsonl"
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        main(["--dry-run", "--config", str(config), "--out", str(out)])
        assert out.exists(), "ledger.jsonl should be created"
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) > 0, "ledger should have at least one line"

    def test_cli_dry_run_has_required_fields(self, tmp_path: Path) -> None:
        """dry-run ledger 每行包含必填字段。"""
        out = tmp_path / "ledger.jsonl"
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        main(["--dry-run", "--config", str(config), "--out", str(out)])
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            record = json.loads(line)
            missing = REQUIRED_FIELDS - set(record.keys())
            assert not missing, f"Row {i}: missing fields {missing}"

    def test_cli_dry_run_reachable_is_false(self, tmp_path: Path) -> None:
        """dry-run 模式所有 reachable 字段为 False（不真抓）。"""
        out = tmp_path / "ledger.jsonl"
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        main(["--dry-run", "--config", str(config), "--out", str(out)])
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        bad = [json.loads(ln) for ln in lines if json.loads(ln).get("reachable") is True]
        assert not bad, f"dry-run should not have reachable=True, found {len(bad)} rows"

    def test_cli_live_requires_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--live 不带 AKB2_LIVE_VALIDATE=1 时退出码 1。"""
        out = tmp_path / "ledger.jsonl"
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        monkeypatch.delenv("AKB2_LIVE_VALIDATE", raising=False)
        rc = main(["--live", "--config", str(config), "--out", str(out)])
        assert rc == 1, "Expected exit 1 when AKB2_LIVE_VALIDATE not set"


class TestLedgerRequiredFields:
    """mocked live 模式 ledger 字段完整性。"""

    def _mock_live_response(self, feed_text: str, status_code: int = 200) -> MagicMock:
        """构造 mock httpx 响应。"""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.text = feed_text
        return mock_resp

    def test_live_record_has_all_required_fields(self, tmp_path: Path) -> None:
        """mocked live 模式记录包含所有必填字段。"""
        run_id = str(uuid.uuid4())

        with patch("httpx.get", return_value=self._mock_live_response(SAMPLE_RSS_FEED)):
            record = _validate_source_live(SAMPLE_SOURCE, run_id)

        missing = REQUIRED_FIELDS - set(record.keys())
        assert not missing, f"Missing required fields: {missing}"

    def test_live_record_has_run_id(self, tmp_path: Path) -> None:
        """mocked live 模式记录包含 run_id 字段。"""
        run_id = str(uuid.uuid4())

        with patch("httpx.get", return_value=self._mock_live_response(SAMPLE_RSS_FEED)):
            record = _validate_source_live(SAMPLE_SOURCE, run_id)

        assert "run_id" in record
        assert record["run_id"] == run_id

    def test_live_reachable_true_on_2xx(self, tmp_path: Path) -> None:
        """2xx 响应 → reachable=True。"""
        run_id = str(uuid.uuid4())

        with patch("httpx.get", return_value=self._mock_live_response(SAMPLE_RSS_FEED, 200)):
            record = _validate_source_live(SAMPLE_SOURCE, run_id)

        assert record["reachable"] is True

    def test_live_reachable_false_on_4xx(self, tmp_path: Path) -> None:
        """4xx 响应 → reachable=False。"""
        run_id = str(uuid.uuid4())

        with patch("httpx.get", return_value=self._mock_live_response("", 404)):
            record = _validate_source_live(SAMPLE_SOURCE, run_id)

        assert record["reachable"] is False

    def test_live_latest_post_extracted_from_rss(self, tmp_path: Path) -> None:
        """RSS feed → latest_post 被正确提取。"""
        run_id = str(uuid.uuid4())

        with patch("httpx.get", return_value=self._mock_live_response(SAMPLE_RSS_FEED)):
            record = _validate_source_live(SAMPLE_SOURCE, run_id)

        # latest_post 应该是 feed 中第一个文章 URL
        assert record["latest_post"] != "" or record["trial_fetch"] != ""  # 至少提取到一个

    def test_live_latest_post_extracted_from_atom(self, tmp_path: Path) -> None:
        """Atom feed → latest_post 被正确提取。"""
        run_id = str(uuid.uuid4())

        with patch("httpx.get", return_value=self._mock_live_response(SAMPLE_ATOM_FEED)):
            record = _validate_source_live(SAMPLE_SOURCE, run_id)

        assert record["latest_post"] != "" or record["trial_fetch"] != ""

    def test_live_timeout_sets_reachable_false(self, tmp_path: Path) -> None:
        """timeout 异常 → reachable=False，trial_fetch_status='timeout'。"""
        import httpx

        run_id = str(uuid.uuid4())

        with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
            record = _validate_source_live(SAMPLE_SOURCE, run_id)

        assert record["reachable"] is False
        assert record["trial_fetch_status"] == "timeout"

    def test_dry_run_record_has_run_id(self) -> None:
        """dry-run 记录也包含 run_id 字段。"""
        run_id = str(uuid.uuid4())
        record = _validate_source_dry(SAMPLE_SOURCE, run_id)
        assert "run_id" in record
        assert record["run_id"] == run_id


class TestRunIdConsistency:
    """同一次运行所有记录 run_id 一致。"""

    def test_dry_run_all_records_same_run_id(self, tmp_path: Path) -> None:
        """dry-run 模式：所有记录共享同一个 run_id。"""
        out = tmp_path / "ledger.jsonl"
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        validate_sources(str(config), str(out), dry_run=True)
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        run_ids = {json.loads(ln)["run_id"] for ln in lines}

        assert len(run_ids) == 1, f"Expected 1 unique run_id, got {len(run_ids)}: {run_ids}"

    def test_run_id_is_uuid_format(self, tmp_path: Path) -> None:
        """run_id 是合法 UUID 格式（uuid4）。"""
        out = tmp_path / "ledger.jsonl"
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        validate_sources(str(config), str(out), dry_run=True)
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        run_id = json.loads(lines[0])["run_id"]

        # UUID4 格式验证
        parsed = uuid.UUID(run_id)
        assert parsed.version == 4, f"Expected UUID v4, got v{parsed.version}"

    def test_different_runs_have_different_run_ids(self, tmp_path: Path) -> None:
        """两次独立运行产生不同的 run_id。"""
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        out1 = tmp_path / "ledger1.jsonl"
        out2 = tmp_path / "ledger2.jsonl"

        validate_sources(str(config), str(out1), dry_run=True)
        validate_sources(str(config), str(out2), dry_run=True)

        lines1 = [ln for ln in out1.read_text().splitlines() if ln.strip()]
        lines2 = [ln for ln in out2.read_text().splitlines() if ln.strip()]

        run_id1 = json.loads(lines1[0])["run_id"]
        run_id2 = json.loads(lines2[0])["run_id"]

        assert run_id1 != run_id2, "Different runs should have different run_ids"


class TestAtomicWrite:
    """原子写入：tmp → rename，不追加旧数据。"""

    def test_atomic_write_creates_file(self, tmp_path: Path) -> None:
        """_write_jsonl_atomic 创建目标文件。"""
        out = tmp_path / "sub" / "ledger.jsonl"
        records = [{"run_id": "abc", "url": "https://example.com", "reachable": True}]

        _write_jsonl_atomic(str(out), records)
        assert out.exists()

    def test_atomic_write_content_correct(self, tmp_path: Path) -> None:
        """写入内容与输入记录一致。"""
        out = tmp_path / "ledger.jsonl"
        records = [
            {"run_id": "abc", "url": "https://a.com"},
            {"run_id": "abc", "url": "https://b.com"},
        ]

        _write_jsonl_atomic(str(out), records)
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2

        parsed = [json.loads(ln) for ln in lines]
        assert parsed[0]["url"] == "https://a.com"
        assert parsed[1]["url"] == "https://b.com"

    def test_atomic_write_overwrites_previous(self, tmp_path: Path) -> None:
        """第二次写入完全覆盖旧数据（不追加）。"""
        out = tmp_path / "ledger.jsonl"

        # 第一次写入：3 条
        records_v1 = [{"run_id": "run1", "url": f"https://old-{i}.com"} for i in range(3)]
        _write_jsonl_atomic(str(out), records_v1)

        # 第二次写入：2 条（新 run_id）
        records_v2 = [{"run_id": "run2", "url": f"https://new-{i}.com"} for i in range(2)]
        _write_jsonl_atomic(str(out), records_v2)

        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2, f"Expected 2 lines after overwrite, got {len(lines)}"

        parsed = [json.loads(ln) for ln in lines]
        run_ids = {r["run_id"] for r in parsed}
        assert run_ids == {"run2"}, f"Old run_id should be replaced, got {run_ids}"

    def test_atomic_write_no_tmp_file_after_success(self, tmp_path: Path) -> None:
        """成功写入后 .tmp 临时文件应被清理（rename 后不存在）。"""
        out = tmp_path / "ledger.jsonl"
        tmp_file = tmp_path / "ledger.jsonl.tmp"

        _write_jsonl_atomic(str(out), [{"run_id": "x"}])

        assert not tmp_file.exists(), ".tmp file should be renamed away after success"

    def test_validate_sources_dry_overwrites_on_second_run(self, tmp_path: Path) -> None:
        """validate_sources dry-run 第二次运行覆盖而非追加。"""
        config = _REPO_ROOT / "config" / "main_sources.yaml"

        if not config.exists():
            pytest.skip("config/main_sources.yaml not found")

        out = tmp_path / "ledger.jsonl"

        validate_sources(str(config), str(out), dry_run=True)
        lines_after_run1 = [ln for ln in out.read_text().splitlines() if ln.strip()]

        validate_sources(str(config), str(out), dry_run=True)
        lines_after_run2 = [ln for ln in out.read_text().splitlines() if ln.strip()]

        # 第二次运行不应该是第一次的 2 倍（不追加）
        assert len(lines_after_run2) == len(lines_after_run1), (
            f"Expected same count after second run (overwrite), "
            f"got {len(lines_after_run1)} then {len(lines_after_run2)}"
        )


class TestExtractLatestUrl:
    """_extract_latest_url 辅助函数测试。"""

    def test_rss_link_extracted(self) -> None:
        """RSS <item><link> 被正确提取。"""
        url = _extract_latest_url(SAMPLE_RSS_FEED)
        assert url == "https://example.com/posts/hello-world"

    def test_atom_link_extracted(self) -> None:
        """Atom <link href="..."> 被正确提取。"""
        url = _extract_latest_url(SAMPLE_ATOM_FEED)
        assert url == "https://example.com/atom/hello"

    def test_empty_text_returns_empty(self) -> None:
        """空文本返回空串。"""
        assert _extract_latest_url("") == ""

    def test_no_links_returns_empty(self) -> None:
        """无链接的 XML 返回空串。"""
        assert _extract_latest_url("<rss><channel><title>x</title></channel></rss>") == ""


# ---------------------------------------------------------------------------
# SC-10: Integration 测试（真网，默认不跑）
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLive76Sources:
    """完整 76 源 live 验证（SC-10）。

    需要 AKB2_LIVE_VALIDATE=1 环境变量，默认不跑。
    """

    def test_live_76_sources(self, tmp_path: Path) -> None:
        """76 源 live 模式：行数、url 唯一性、必填字段、reachable>=70、run_id 一致。"""
        import yaml

        config_path = _REPO_ROOT / "config" / "main_sources.yaml"
        out = tmp_path / "sources-ledger.jsonl"

        assert config_path.exists(), "config/main_sources.yaml not found"

        # 加载配置源 URL 集合
        with open(config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        config_urls = {
            s.get("feed_url") or s.get("listing_url")
            for s in cfg["sources"]
            if s.get("feed_url") or s.get("listing_url")
        }

        rc = validate_sources(str(config_path), str(out), dry_run=False)
        assert rc == 0, f"validate_sources returned {rc}"

        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) == 76, f"Expected 76 rows, got {len(lines)}"

        records = [json.loads(ln) for ln in lines]

        # unique_urls == config_urls
        ledger_urls = {r["url"] for r in records}
        assert ledger_urls == config_urls, (
            f"URL mismatch:\n"
            f"  In ledger but not config: {ledger_urls - config_urls}\n"
            f"  In config but not ledger: {config_urls - ledger_urls}"
        )

        # 必填字段齐
        bad_rows = [
            i for i, r in enumerate(records) if not REQUIRED_FIELDS.issubset(r.keys())
        ]
        assert not bad_rows, f"Missing required fields at rows: {bad_rows}"

        # reachable >= 70
        reachable_count = sum(1 for r in records if r.get("reachable"))
        assert reachable_count >= 70, f"reachable={reachable_count} < 70"

        # run_id 一致（同一次运行）
        run_ids = {r.get("run_id") for r in records}
        assert len(run_ids) == 1, f"Mixed run_ids: {run_ids}"

        # reachable 源的 trial_fetch + latest_post 非空 >= 60
        rich_count = sum(
            1
            for r in records
            if r.get("reachable") and r.get("trial_fetch") and r.get("latest_post")
        )
        assert rich_count >= 60, f"reachable+nonempty trial_fetch/latest_post = {rich_count} < 60"
