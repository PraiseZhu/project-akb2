"""飞书 Bot Token 认证 — 获取 tenant_access_token（httpx + 内联重试）。

移植自 Project Doki feishu/auth.py：requests→httpx，config 全局→akb2.feishu_sync.config。
"""

from __future__ import annotations

import time

import httpx

from .. import config

_token_cache: dict = {"token": "", "expires_at": 0.0}

_MAX_RETRIES = 3
_BASE_DELAY = 1.0


def get_token() -> str:
    """获取 tenant_access_token，自动缓存和刷新（有效期 2 小时，留 60s 缓冲）。"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.post(
                f"{config.FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": config.app_id(), "app_secret": config.app_secret()},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"获取 token 失败: code={data.get('code')} msg={data.get('msg')}"
                )
            _token_cache["token"] = data["tenant_access_token"]
            _token_cache["expires_at"] = now + data.get("expire", 7200)
            return _token_cache["token"]
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_BASE_DELAY * (2**attempt))

    raise RuntimeError(f"获取 token 失败（重试 {_MAX_RETRIES} 次耗尽）: {last_exc}")


def auth_headers() -> dict:
    """返回带 Authorization 的请求头。"""
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def reset_token_cache() -> None:
    """清空 token 缓存（测试用）。"""
    _token_cache["token"] = ""
    _token_cache["expires_at"] = 0.0
