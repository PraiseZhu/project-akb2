"""config：缺失变量检测、必需变量读取。"""

from __future__ import annotations

import pytest

from akb2.feishu_sync import config


def test_missing_all(monkeypatch):
    for k in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_PUSH_CHAT_ID", "FEISHU_DM_OPEN_ID"]:
        monkeypatch.delenv(k, raising=False)
    assert set(config.missing_feishu_vars()) == {
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_PUSH_CHAT_ID",
        "FEISHU_DM_OPEN_ID",
    }


def test_present(feishu_env):
    assert config.missing_feishu_vars() == []
    assert config.app_id() == "app_dummy"
    assert config.push_chat_id() == "oc_group_dummy"
    assert config.dm_open_id() == "ou_me_dummy"


def test_require_raises_without_value(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    with pytest.raises(RuntimeError):
        config.app_id()


def test_api_base_default():
    assert config.FEISHU_API_BASE.startswith("https://")


def test_missing_policy_defaults_to_partial_ok(monkeypatch):
    monkeypatch.delenv(config.MISSING_POLICY_ENV, raising=False)
    assert config.missing_policy() == config.MISSING_POLICY_PARTIAL_OK_RECOVERABLE


def test_missing_env_hint_distinguishes_secrets_not_loaded(monkeypatch):
    monkeypatch.setenv(config.SECRETS_STATUS_ENV, "missing_file")
    monkeypatch.setenv(config.SECRETS_FILE_ENV, "/tmp/.akb2-secrets.sh")
    hint = config.missing_env_hint(["FEISHU_APP_ID"])
    assert "未装载" in hint
    assert "/tmp/.akb2-secrets.sh" in hint


def test_missing_env_hint_distinguishes_runtime_env_loss(monkeypatch):
    monkeypatch.setenv(config.SECRETS_STATUS_ENV, config.SECRETS_STATUS_LOADED)
    hint = config.missing_env_hint(["FEISHU_APP_ID"])
    assert "运行期 env 仍缺失" in hint
