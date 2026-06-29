"""无网络 intake 配置与模块加载测试。"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config/intake_sources.yaml"
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"


def test_config_loads():
    """YAML 配置可加载且含 4 路 source 键。"""
    config = yaml.safe_load(open(CONFIG_PATH))
    assert set(["l0", "l0_prime", "l3", "l4"]).issubset(config["sources"].keys())


def test_l3_has_follow_graph_account():
    """L3 accounts 中至少一个 follow_graph=true。"""
    config = yaml.safe_load(open(CONFIG_PATH))
    accounts = config["sources"]["l3"].get("accounts", [])
    assert any(a.get("follow_graph") is True for a in accounts)


def test_l4_disabled_by_default():
    """L4 默认 disabled。"""
    config = yaml.safe_load(open(CONFIG_PATH))
    assert config["sources"]["l4"]["enabled"] is False


def test_l0_collector_importable():
    """L0 collector 可 import。"""
    from akb2.briefing.intake.l0_collector import L0Collector

    assert L0Collector is not None


def test_l0_prime_collector_importable():
    """L0' collector 可 import。"""
    from akb2.briefing.intake.l0_prime_collector import L0PrimeCollector

    assert L0PrimeCollector is not None


def test_l3_collector_importable():
    """L3 collector 可 import。"""
    from akb2.briefing.intake.l3_collector import L3Collector

    assert L3Collector is not None


def test_l4_collector_importable():
    """L4 collector 可 import。"""
    from akb2.briefing.intake.l4_collector import L4Collector

    assert L4Collector is not None


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
