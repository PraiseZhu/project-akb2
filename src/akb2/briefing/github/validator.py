"""GitHub briefing schema validator — jsonschema draft-07 CLI wrapper."""

import json
import sys
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "github_briefing_v1.json"


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def validate(file_path: str) -> list[str]:
    schema = load_schema()
    with open(file_path) as f:
        instance = json.load(f)
    errors = list(jsonschema.Draft7Validator(schema).iter_errors(instance))
    return [e.message for e in errors]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m akb2.briefing.github.validator <file.json>", file=sys.stderr)
        sys.exit(2)

    errors = validate(sys.argv[1])
    if errors:
        for msg in errors:
            print(f"ERR {msg}")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
