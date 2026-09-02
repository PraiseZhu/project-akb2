"""loop2 X 源册加载与校验。不是 briefing 的 intake_sources.yaml。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

X_PER_CHANNEL_PER_RUN_CAP = 40
X_BOOKMARKS_PER_RUN_CAP = 30
X_BOOKMARKS_PER_DAY_CAP = 60
X_RUNS_PER_DAY = 6
X_CLI_TIMEOUT_SEC = 60
X_CLI_PACING_SEC = 3.0
X_MIN_ARTICLE_WORDS = 400
X_CHANNEL_PER_RUN_CAP_MAX = 100
ALLOWED_CHANNELS = frozenset({"list", "feed", "bookmarks"})
FORBIDDEN_CONFIG_FIELDS = frozenset({"user_posts", "user-posts", "search"})

DEFAULT_SOURCES_CONFIG = Path("config/x_sources.yaml")


class XSourcesConfigError(ValueError):
    """x_sources.yaml 校验失败（fail-closed）。"""


@dataclass(frozen=True)
class XChannel:
    channel: str
    slug: str
    enabled: bool
    list_id: str = ""
    per_run_cap: int = X_PER_CHANNEL_PER_RUN_CAP
    per_day_cap: int | None = None
    topic_hint: list[str] = field(default_factory=list)
    prefilter_weight: float = 1.0
    notes: str = ""
    pacing_sec: float = X_CLI_PACING_SEC
    min_article_words: int = X_MIN_ARTICLE_WORDS


@dataclass(frozen=True)
class XSourcesConfig:
    version: int
    defaults: dict[str, Any]
    channels: tuple[XChannel, ...]


def _as_dict(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise XSourcesConfigError(f"{where} must be a mapping")
    return value


def _reject_forbidden_fields(payload: dict[str, Any], *, where: str) -> None:
    hits = sorted(key for key in payload if key in FORBIDDEN_CONFIG_FIELDS)
    if hits:
        raise XSourcesConfigError(
            f"{where} contains forbidden field(s): {', '.join(hits)}"
        )


def _coerce_int(value: Any, *, default: int, where: str) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise XSourcesConfigError(f"{where} must be int") from exc
    return number


def _coerce_float(value: Any, *, default: float, where: str) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise XSourcesConfigError(f"{where} must be float") from exc


def _coerce_str_list(value: Any, *, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise XSourcesConfigError(f"{where} must be a list of strings")
    return list(value)


def load_x_sources(path: str | Path = DEFAULT_SOURCES_CONFIG) -> XSourcesConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise XSourcesConfigError(f"x_sources config not found: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload = _as_dict(raw, where="x_sources")
    _reject_forbidden_fields(payload, where="x_sources")

    version = _coerce_int(payload.get("version"), default=0, where="version")
    if version != 1:
        raise XSourcesConfigError(f"unsupported x_sources version: {version}")

    defaults = _as_dict(payload.get("defaults") or {}, where="defaults")
    _reject_forbidden_fields(defaults, where="defaults")
    default_cap = _coerce_int(
        defaults.get("per_run_cap"),
        default=X_PER_CHANNEL_PER_RUN_CAP,
        where="defaults.per_run_cap",
    )
    default_pacing = _coerce_float(
        defaults.get("pacing_sec"),
        default=X_CLI_PACING_SEC,
        where="defaults.pacing_sec",
    )
    default_min_words = _coerce_int(
        defaults.get("min_article_words"),
        default=X_MIN_ARTICLE_WORDS,
        where="defaults.min_article_words",
    )
    if default_cap < 1 or default_cap > X_CHANNEL_PER_RUN_CAP_MAX:
        raise XSourcesConfigError(
            f"defaults.per_run_cap must be 1..{X_CHANNEL_PER_RUN_CAP_MAX}"
        )

    channels_raw = payload.get("channels")
    if not isinstance(channels_raw, list) or not channels_raw:
        raise XSourcesConfigError("channels must be a non-empty list")

    parsed: list[XChannel] = []
    slugs: set[str] = set()
    list_ids: set[str] = set()
    for index, item in enumerate(channels_raw):
        where = f"channels[{index}]"
        channel_payload = _as_dict(item, where=where)
        _reject_forbidden_fields(channel_payload, where=where)

        kind = str(channel_payload.get("channel") or "").strip()
        if kind not in ALLOWED_CHANNELS:
            raise XSourcesConfigError(f"{where}.channel must be one of {sorted(ALLOWED_CHANNELS)}")

        slug = str(channel_payload.get("slug") or "").strip()
        if not slug:
            raise XSourcesConfigError(f"{where}.slug is required")
        if slug in slugs:
            raise XSourcesConfigError(f"duplicate slug: {slug}")
        slugs.add(slug)

        enabled = bool(channel_payload.get("enabled", False))
        list_id = str(channel_payload.get("list_id") or "").strip()
        if kind == "list" and enabled and not list_id:
            raise XSourcesConfigError(f"{where}.list_id is required when list channel is enabled")
        if kind == "list" and enabled and not list_id.isdigit():
            raise XSourcesConfigError(f"{where}.list_id must be numeric")
        if kind == "list" and enabled:
            if list_id in list_ids:
                raise XSourcesConfigError(f"duplicate list_id: {list_id}")
            list_ids.add(list_id)
        if kind != "list" and "list_id" in channel_payload and list_id:
            raise XSourcesConfigError(f"{where}.list_id is only valid for list channels")

        per_run_cap = _coerce_int(
            channel_payload.get("per_run_cap"),
            default=X_BOOKMARKS_PER_RUN_CAP if kind == "bookmarks" else default_cap,
            where=f"{where}.per_run_cap",
        )
        if per_run_cap < 1 or per_run_cap > X_CHANNEL_PER_RUN_CAP_MAX:
            raise XSourcesConfigError(
                f"{where}.per_run_cap must be 1..{X_CHANNEL_PER_RUN_CAP_MAX}"
            )

        per_day_raw = channel_payload.get("per_day_cap")
        per_day_cap: int | None
        if per_day_raw is None:
            per_day_cap = X_BOOKMARKS_PER_DAY_CAP if kind == "bookmarks" else None
        else:
            per_day_cap = _coerce_int(per_day_raw, default=0, where=f"{where}.per_day_cap")
            if per_day_cap < 1:
                raise XSourcesConfigError(f"{where}.per_day_cap must be >= 1")

        parsed.append(
            XChannel(
                channel=kind,
                slug=slug,
                enabled=enabled,
                list_id=list_id,
                per_run_cap=per_run_cap,
                per_day_cap=per_day_cap,
                topic_hint=_coerce_str_list(
                    channel_payload.get("topic_hint"), where=f"{where}.topic_hint"
                ),
                prefilter_weight=_coerce_float(
                    channel_payload.get("prefilter_weight"),
                    default=1.0,
                    where=f"{where}.prefilter_weight",
                ),
                notes=str(channel_payload.get("notes") or ""),
                pacing_sec=_coerce_float(
                    channel_payload.get("pacing_sec"),
                    default=default_pacing,
                    where=f"{where}.pacing_sec",
                ),
                min_article_words=_coerce_int(
                    channel_payload.get("min_article_words"),
                    default=default_min_words,
                    where=f"{where}.min_article_words",
                ),
            )
        )

    return XSourcesConfig(
        version=version,
        defaults={
            "per_run_cap": default_cap,
            "pacing_sec": default_pacing,
            "min_article_words": default_min_words,
        },
        channels=tuple(parsed),
    )


def enabled_channels(config: XSourcesConfig) -> tuple[XChannel, ...]:
    return tuple(channel for channel in config.channels if channel.enabled)
