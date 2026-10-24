"""Quality 模块共享测试 fixtures（主链路删除后只服务 llm_router 与 raw 归档测试）。

所有 fixture LLM 响应，不打真 API。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_quality_state(tmp_path_factory, monkeypatch):
    """状态隔离（autouse）：把机器本地状态根指向 tmp，防污染生产账本。

    2026-07-28：promote / requeue / synthesis_assembler 的账本路径统一改走
    `common.state_paths`（修账本分裂）后，它们不再落在测试传入的 tmp vault，
    而是落在真实状态根 `~/Library/Application Support/akb2/ledgers/`。
    实测后果：跑一次全量就往生产账本追加了 4 条 `mock grader pass` 事件 —— 把
    fixture 内容标成"已入库"，会让真实管线跳过或误判。

    与 `tests/briefing/conftest.py` 同一范式（红线 11：convention beats novelty）。
    """
    monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path_factory.mktemp("akb2-state")))
    yield


@pytest.fixture(scope="session")
def intake_fixtures_dir() -> Path:
    """Path to the intake test fixtures directory."""
    return Path(__file__).parent / "fixtures" / "intake"


@pytest.fixture()
def sample_article_md(intake_fixtures_dir: Path) -> str:
    """Full article markdown content."""
    return (intake_fixtures_dir / "article_loop_engineering.md").read_text(encoding="utf-8")


@pytest.fixture()
def sample_short_md(intake_fixtures_dir: Path) -> str:
    """Short article markdown."""
    return (intake_fixtures_dir / "article_short.md").read_text(encoding="utf-8")


@pytest.fixture()
def temp_failures_path(tmp_path: Path) -> Path:
    """Temporary failures jsonl path for signature testing."""
    return tmp_path / "failures.jsonl"


@pytest.fixture()
def temp_output_dir(tmp_path: Path) -> Path:
    """Temporary output directory for raw archiver."""
    out = tmp_path / "raw"
    out.mkdir(parents=True, exist_ok=True)
    return out
