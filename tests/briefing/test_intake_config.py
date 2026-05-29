"""无网络 intake 配置与模块加载测试。"""
import json
import pytest
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config/intake_sources.yaml"
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"


def test_config_loads():
    """YAML 配置可加载且含 l0 source 键(当前唯一真实数据源)。"""
    config = yaml.safe_load(open(CONFIG_PATH))
    assert "l0" in config["sources"]


def test_l0_collector_importable():
    """L0 collector 可 import。"""
    from akb2.briefing.intake.l0_collector import L0Collector

    assert L0Collector is not None


def test_l0_dry_run_reads_fixture():
    """L0 dry-run 模式读取 fixture 返回非空 tweets。"""
    from akb2.briefing.intake.l0_collector import L0Collector

    collector = L0Collector()
    tweets = collector.collect(dry_run=True)
    assert len(tweets) > 0
    assert all(
        "id" in t and "text" in t and "author" in t and "created_at" in t
        for t in tweets
    )
