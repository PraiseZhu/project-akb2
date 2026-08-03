"""飞书告警必须带上上游阻断的真因（2026-07-28 事故回归）。

事故实况：社媒链路连续两天被硬阻断，真因已落盘到 logs/briefing-social-blocked.json，
但飞书告警只发「ai_trends 缺失（期望路径: …）」，运维两天无法从告警定位成因。
"""

from __future__ import annotations

import json

from akb2.feishu_sync import agent as agent_mod
from akb2.feishu_sync import blocked


def _write(root, rel: str, payload: dict) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_blocking_reason_reads_social_diag(tmp_path):
    _write(
        tmp_path,
        "logs/briefing-social-blocked.json",
        {
            "status": "blocked",
            "reason": "intake_fixture_fallback",
            "date": "2026-07-28",
            "detail": "collector 降级到测试 fixture",
        },
    )
    got = blocked.blocking_reason("ai_trends", "2026-07-28", repo_root=tmp_path)
    assert got is not None
    assert "intake_fixture_fallback" in got
    assert "blocked" in got


def test_blocking_reason_rejects_stale_diag(tmp_path):
    """诊断文件是覆盖写的单份文件，日期不匹配必须忽略。

    把昨天的原因贴到今天的告警上，比不给原因更糟（会把运维引向错误方向）。
    """
    _write(
        tmp_path,
        "logs/briefing-social-blocked.json",
        {"status": "blocked", "reason": "intake_fixture_fallback", "date": "2026-07-27"},
    )
    assert blocked.blocking_reason("ai_trends", "2026-07-28", repo_root=tmp_path) is None


def test_blocking_reason_github_uses_generated_at_when_no_date(tmp_path):
    """github 链路的诊断没有 date 字段，退回 generated_at 前缀。"""
    _write(
        tmp_path,
        "briefing-failed.json",
        {
            "status": "failed",
            "reason": "synthesis_failed: Unterminated string",
            "generated_at": "2026-07-28T22:01:15.053131+00:00",
        },
    )
    got = blocked.blocking_reason("github", "2026-07-28", repo_root=tmp_path)
    assert got is not None and "synthesis_failed" in got


def test_blocking_reason_tolerates_missing_and_corrupt(tmp_path):
    """告警链路自身绝不能因为诊断文件坏了而失败。"""
    # 文件完全不存在
    assert blocked.blocking_reason("ai_trends", "2026-07-28", repo_root=tmp_path) is None
    # 空 dict（无 reason）+ 非法 JSON
    _write(tmp_path, "logs/briefing-social-blocked.json", {})
    (tmp_path / "logs" / "briefing-social-failed.json").write_text(
        "{not json", encoding="utf-8"
    )
    assert blocked.blocking_reason("ai_trends", "2026-07-28", repo_root=tmp_path) is None


def test_discover_node_alert_carries_reason(tmp_path, monkeypatch):
    """生产路径：strict 模式缺失告警文案里必须出现真因。

    缺陷版只有「ai_trends 缺失（期望路径: …）」。
    """
    _write(
        tmp_path,
        "logs/briefing-social-blocked.json",
        {
            "status": "blocked",
            "reason": "intake_fixture_fallback",
            "date": "2026-07-28",
            "detail": "collector 降级到测试 fixture",
        },
    )
    monkeypatch.setattr(blocked, "REPO_ROOT", tmp_path)

    monkeypatch.setattr(
        agent_mod.discover_mod,
        "discover",
        lambda vault, date, expected=None: (
            [],
            [],
            [("ai_trends", "/vault/AI TRENDS/Social/2026-07-28 Daily Briefing.md")],
        ),
    )

    out = agent_mod.discover_node(
        {
            "vault_root": str(tmp_path),
            "date": "2026-07-28",
            "expected": ("ai_trends",),
            "strict": True,
        }
    )

    assert out["errors"], "strict 模式缺失必须产生错误"
    msg = out["errors"][0]
    assert "缺失" in msg
    assert "真因" in msg and "intake_fixture_fallback" in msg


def test_next_step_command_mentions_backstop_then_reconcile(tmp_path):
    vault = tmp_path / "vault"
    cmd = blocked.next_step_command("github", "2026-08-02", vault)
    assert cmd is not None
    assert "github_briefing_backstop.sh" in cmd
    assert "--reconcile-missing" in cmd


def test_next_step_command_reconcile_only_when_product_exists(tmp_path):
    vault = tmp_path / "vault"
    out = vault / "Praise Space" / "AKB2.0" / "AI TRENDS" / "GitHub"
    out.mkdir(parents=True, exist_ok=True)
    (out / "2026-08-02 GitHub Briefing.md").write_text("# done", encoding="utf-8")

    cmd = blocked.next_step_command("github", "2026-08-02", vault)
    assert cmd is not None
    assert "github_briefing_backstop.sh" not in cmd
    assert "--reconcile-missing" in cmd
