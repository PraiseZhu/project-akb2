"""loop2 X 三通道采集：list / feed / bookmarks。禁止 user-posts 与 search。"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

from src.akb2.briefing.intake.l0_collector import match_upstream_fatal
from src.akb2.quality.intake.x_account_discovery import (
    XAccountAuthError,
    _run_twitter,
    _twitter_bin,
)
from src.akb2.quality.intake_x.sources import XChannel

logger = logging.getLogger(__name__)

FORBIDDEN_ARGV_TOKENS = frozenset({"user-posts", "search"})
_AUTH_MARKERS = ("not_authenticated", "not authenticated", "login required")


def _assert_allowed_argv(cmd: list[str]) -> None:
    for token in cmd:
        if token in FORBIDDEN_ARGV_TOKENS:
            raise RuntimeError(f"forbidden twitter argv token: {token}")


def _sample_text(combined: str) -> str:
    return combined[:300].replace("\n", " | ")


def _parse_twitter_payload(
    result: subprocess.CompletedProcess[str],
) -> tuple[list[dict[str, Any]], str | None, str]:
    combined = (result.stderr or "") + (result.stdout or "")
    sample = _sample_text(combined)

    lowered = combined.lower()
    if any(marker in lowered for marker in _AUTH_MARKERS):
        raise XAccountAuthError(f"twitter-cli 未认证: {combined[:200]}")

    payload: Any = None
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout or "")
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("ok"):
            raw = payload.get("data") or []
            if isinstance(raw, list):
                # stderr 里的 ClientTransaction 警告不能盖掉 ok:true 的抓取。
                return [item for item in raw if isinstance(item, dict)], None, ""

    fatal = match_upstream_fatal(combined)
    if fatal:
        return [], fatal, sample

    if result.returncode != 0:
        logger.warning("twitter-cli exit %s: %s", result.returncode, sample)
        return [], None, sample or f"exit {result.returncode}"
    if payload is None:
        return [], None, sample or "non-json"
    if not isinstance(payload, dict):
        return [], None, "payload not object"
    return [], None, "payload.ok=false"


def fetch_channel_argv(
    result: subprocess.CompletedProcess[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None, str]:
    """解析 twitter JSON payload。timeout/非 JSON/ok=false → ([], fatal_or_None, sample)。"""
    if result is None:
        return [], None, "empty-result"
    return _parse_twitter_payload(result)


def _run_channel(cmd: list[str]) -> tuple[list[dict[str, Any]], str | None, str]:
    _assert_allowed_argv(cmd)
    try:
        result = _run_twitter(cmd)
    except subprocess.TimeoutExpired:
        return [], None, "timeout"
    except FileNotFoundError as exc:
        raise XAccountAuthError(f"twitter-cli 不存在: {exc}") from exc
    return _parse_twitter_payload(result)


def fetch_list(list_id: str, cap: int) -> tuple[list[dict[str, Any]], str | None, str]:
    cmd = [_twitter_bin(), "list", str(list_id), "-n", str(cap), "--json"]
    return _run_channel(cmd)


def fetch_feed(
    cap: int,
    timeline: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, str]:
    cmd = [_twitter_bin(), "feed"]
    if timeline:
        cmd.extend(["-t", str(timeline)])
    cmd.extend(["-n", str(cap), "--json"])
    return _run_channel(cmd)


def fetch_bookmarks(cap: int) -> tuple[list[dict[str, Any]], str | None, str]:
    cmd = [_twitter_bin(), "bookmarks", "-n", str(cap), "--json"]
    return _run_channel(cmd)


def fetch_channel(
    channel: XChannel,
    *,
    last_fetch_monotonic: float | None = None,
) -> tuple[list[dict[str, Any]], str | None, str]:
    """按通道抓取。pacing 由调用方传入上次 monotonic 时间戳。"""
    if last_fetch_monotonic is not None:
        wait = channel.pacing_sec - (time.monotonic() - last_fetch_monotonic)
        if wait > 0:
            time.sleep(wait)

    if channel.channel == "list":
        return fetch_list(channel.list_id, channel.per_run_cap)
    if channel.channel == "feed":
        return fetch_feed(channel.per_run_cap)
    if channel.channel == "bookmarks":
        return fetch_bookmarks(channel.per_run_cap)
    raise ValueError(f"unknown channel: {channel.channel}")
