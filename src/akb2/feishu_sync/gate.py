"""发布门控。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft7Validator

from .state import Briefing

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "briefing"
    / "github"
    / "schemas"
    / "github_briefing_v1.json"
)


@lru_cache(maxsize=1)
def _github_validator() -> Draft7Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft7Validator(schema)


def gate_decision(b: Briefing) -> tuple[bool, str]:
    if b.kind == "ai_trends":
        fm = b.frontmatter
        passed = fm.get("audit_verdict") == "pass" and fm.get("proposed_gate_decision") == "active"
        return passed, "audit/gate 未过"

    if b.kind != "github":
        return True, ""

    if b.frontmatter.get("type") != "github-briefing":
        return False, "frontmatter.type 不是 github-briefing"
    if not isinstance(b.json_data, dict):
        return False, "缺少 GitHub Briefing.json"
    if b.json_data.get("date") != b.date:
        return False, f"json date 不匹配: {b.json_data.get('date')!r}"
    try:
        validator = _github_validator()
    except Exception as exc:  # noqa: BLE001
        return False, f"github schema 装载失败: {exc}"
    errors = sorted(validator.iter_errors(b.json_data), key=lambda err: list(err.path))
    if errors:
        return False, f"github schema 校验失败: {errors[0].message}"
    return True, ""


def passes_gate(b: Briefing) -> bool:
    return gate_decision(b)[0]
