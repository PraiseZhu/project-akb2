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
