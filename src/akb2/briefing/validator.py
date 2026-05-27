#!/usr/bin/env python3
"""validator.py — 校验 social_trends_v1 payload 或单条 tweet。

CLI: python -m akb2.briefing.validator <path>
  - 成功：stdout 输出 "OK: <path> passes social_trends_v1_1"，退出 0
  - 失败：stderr 输出所有错误，退出 1

函数接口：
  validate_payload(payload: dict) -> None  # 校验全量 payload；失败 raise ValidationError
  validate_tweet(tweet: dict) -> None      # 只校验单条 tweet；失败 raise ValidationError
"""
import json
import sys
from pathlib import Path

import jsonschema

_SCHEMA_PATH = Path(__file__).parent / "schemas" / "social_trends_v1.json"


def _load_schema() -> dict:
    with open(_SCHEMA_PATH) as f:
        return json.load(f)


def validate_payload(payload: dict) -> None:
    """校验全量 payload；失败 raise jsonschema.ValidationError。"""
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        parts = []
        for e in errors:
            location = ".".join(str(p) for p in e.absolute_path) or "(root)"
            parts.append(f"[{location}] {e.message}")
        raise jsonschema.ValidationError("; ".join(parts))


def validate_tweet(tweet: dict) -> None:
    """只校验单条 tweet；失败 raise jsonschema.ValidationError。"""
    schema = _load_schema()
    tweet_schema = schema["properties"]["tweets"]["items"]
    validator = jsonschema.Draft202012Validator(tweet_schema)
    errors = list(validator.iter_errors(tweet))
    if errors:
        parts = []
        for e in errors:
            location = ".".join(str(p) for p in e.absolute_path) or "(root)"
            parts.append(f"[{location}] {e.message}")
        raise jsonschema.ValidationError("; ".join(parts))


def _cli(path: str) -> int:
    with open(path) as f:
        payload = json.load(f)
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        for e in errors:
            location = ".".join(str(p) for p in e.absolute_path) or "(root)"
            print(f"ERROR at {location}: {e.message}", file=sys.stderr)
        return 1
    print(f"OK: {path} passes social_trends_v1_1")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m akb2.briefing.validator <path>", file=sys.stderr)
        sys.exit(2)
    sys.exit(_cli(sys.argv[1]))
