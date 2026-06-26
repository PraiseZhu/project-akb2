"""L0' paper-bot List 占位器 collector。"""
import argparse
import json

from .base import REPO_ROOT, BaseCollector

FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"


class L0PrimeCollector(BaseCollector):
    source_name = "l0_prime_paper_bot"

    def collect(self, dry_run: bool = True) -> list[dict]:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        tweets = data.get("tweets", [])
        return tweets[:3]  # paper-bot subset


def main():
    parser = argparse.ArgumentParser(description="L0' paper-bot collector")
    parser.add_argument("--output", default="/tmp/l0p-out.json")
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()
    L0PrimeCollector().run_cli(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
