"""L3 关注图谱 collector。"""
import argparse
import json

from .base import REPO_ROOT, BaseCollector

FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"


class L3Collector(BaseCollector):
    source_name = "l3_follow_graph"

    def collect(self, dry_run: bool = True) -> list[dict]:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        tweets = data.get("tweets", [])
        return tweets[-5:] if len(tweets) >= 5 else tweets


def main():
    parser = argparse.ArgumentParser(description="L3 follow graph collector")
    parser.add_argument("--output", default="/tmp/l3-out.json")
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()
    L3Collector().run_cli(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
