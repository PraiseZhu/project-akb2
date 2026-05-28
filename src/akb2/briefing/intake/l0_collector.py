"""L0 公共时间线 collector。

CLI: python -m akb2.briefing.intake.l0_collector [--output <path>] [--dry-run]
     默认使用 --dry-run（读 tests/fixtures/intake/dry-run-2026-05-27/sample.json）
"""
import argparse
import json
import os
from pathlib import Path

from .base import BaseCollector, REPO_ROOT

FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"


class L0Collector(BaseCollector):
    source_name = "l0_public_timeline"

    def collect(self, dry_run: bool = True) -> list[dict]:
        if dry_run or not self._has_credentials():
            return self._read_fixture()
        return self._fetch_real()

    def _has_credentials(self) -> bool:
        return bool(
            os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")
        )

    def _read_fixture(self) -> list[dict]:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        return data.get("tweets", [])

    def _fetch_real(self) -> list[dict]:
        # real X API call placeholder
        raise NotImplementedError("Real X API collection not implemented in MVP")


def main():
    parser = argparse.ArgumentParser(description="L0 public timeline collector")
    parser.add_argument("--output", default="/tmp/l0-out.json", help="Output JSON path")
    parser.add_argument(
        "--dry-run", action="store_true", default=True, help="Use fixture data"
    )
    args = parser.parse_args()
    collector = L0Collector()
    collector.run_cli(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
