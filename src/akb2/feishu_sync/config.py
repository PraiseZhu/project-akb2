"""feishu_sync 配置 — 从环境变量读取 PROJECT DOKI 飞书凭证。

凭证来源：`~/.akb2-secrets.sh`（shell 端 source 后注入环境）。
代码只 `os.environ.get`，绝不硬编码、绝不打印值。
"""

from __future__ import annotations

import os

# 飞书开放平台 API base（与 Doki 一致，硬编码非密）
FEISHU_API_BASE = os.environ.get(
    "FEISHU_API_BASE", "https://open.feishu.cn/open-apis"
).rstrip("/")

# HTTP 超时（秒）
HTTP_TIMEOUT = float(os.environ.get("FEISHU_HTTP_TIMEOUT", "15"))


def _require(name: str) -> str:
    """读取必需环境变量，缺失时抛出（错误信息只含变量名，不含值）。"""
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"缺少必需环境变量: {name}（请确认 ~/.akb2-secrets.sh 已 source）")
    return val


def app_id() -> str:
    return _require("FEISHU_APP_ID")


def app_secret() -> str:
    return _require("FEISHU_APP_SECRET")


def push_chat_id() -> str:
    """XD WEB 群 chat_id（prod 目标）。"""
    return _require("FEISHU_PUSH_CHAT_ID")


def dm_open_id() -> str:
    """测试期私信目标 open_id（朱赞）。"""
    return _require("FEISHU_DM_OPEN_ID")


def missing_feishu_vars() -> list[str]:
    """返回缺失的飞书相关环境变量名列表（用于启动前自检，不打印值）。"""
    req = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_PUSH_CHAT_ID", "FEISHU_DM_OPEN_ID"]
    return [k for k in req if not os.environ.get(k)]
