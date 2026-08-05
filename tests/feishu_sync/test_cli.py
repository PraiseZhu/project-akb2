"""CLI / wrapper 行为。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from akb2.feishu_sync import cli, paths

from .conftest import write_github

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _redirect_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path)


def test_reconcile_missing_default_today_does_not_backfill_yesterday(
    monkeypatch, tmp_path, vault, feishu_env
):
    _redirect_logs(monkeypatch, tmp_path)
    write_github(vault, "2026-08-02")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        """
{
  "2026-08-02": {
    "pending": {
      "github:group": {
        "status": "missing_expected",
        "kind": "github",
        "date": "2026-08-02",
        "target": "group",
        "expected_path": "/tmp/vault/2026-08-02 GitHub Briefing.md",
        "reason": "failed: synthesis_failed"
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_today", lambda: "2026-08-03")
    sent = []
    monkeypatch.setattr(cli, "run_sync", lambda **kwargs: sent.append(kwargs) or 0)

    rc = cli.main([
        "--vault-root", str(vault),
        "--target", "group",
        "--state-path", str(state_path),
        "--reconcile-missing",
    ])

    assert rc == 0
    assert sent[0]["date"] == "2026-08-03"
    assert sent[0]["reconcile_missing"] is True


def test_reconcile_missing_with_explicit_date_can_backfill_yesterday(
    monkeypatch, tmp_path, vault, feishu_env
):
    _redirect_logs(monkeypatch, tmp_path)
    write_github(vault, "2026-08-02")
    sent = []
    monkeypatch.setattr(cli, "run_sync", lambda **kwargs: sent.append(kwargs) or 0)

    rc = cli.main([
        "--vault-root", str(vault),
        "--target", "group",
        "--date", "2026-08-02",
        "--reconcile-missing",
    ])

    assert rc == 0
    assert sent[0]["date"] == "2026-08-02"
    assert sent[0]["reconcile_missing"] is True


def test_cli_missing_env_reports_not_loaded(monkeypatch, tmp_path):
    _redirect_logs(monkeypatch, tmp_path)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_PUSH_CHAT_ID", raising=False)
    monkeypatch.delenv("FEISHU_DM_OPEN_ID", raising=False)
    monkeypatch.setenv("AKB2_FEISHU_SECRETS_STATUS", "missing_file")
    monkeypatch.setenv("AKB2_FEISHU_SECRETS_FILE", "/tmp/.akb2-secrets.sh")
    alerts = []
    monkeypatch.setattr(cli.notify, "alert_failure", lambda msg: alerts.append(msg))

    rc = cli.main(["--vault-root", str(tmp_path)])

    assert rc == 1
    assert alerts and "未装载" in alerts[0]
    assert "FEISHU_APP_ID" in alerts[0]


def test_wrapper_source_failure_exits_without_leaking_values(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".akb2-secrets.sh").write_text("echo TOPSECRET\nfalse\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "feishu_sync.sh"), "--vault-root", str(vault), "--dry-run"],
        cwd=PROJECT_ROOT,
        env={
            "HOME": str(home),
            "PATH": str(Path("/usr/bin")) + ":" + str(Path("/bin")) + ":" + str(Path("/usr/local/bin")),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "failed to source" in combined
    assert "TOPSECRET" not in combined
