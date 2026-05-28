"""L4 arxiv stub collector（默认 disabled）。"""
import argparse
import json

from .base import BaseCollector


class L4Collector(BaseCollector):
    source_name = "l4_arxiv"

    def collect(self, dry_run: bool = True) -> list[dict]:
        cfg = self.config.get("sources", {}).get("l4", {})
        if not cfg.get("enabled", False):
            return []
        raise NotImplementedError("L4 arxiv collection not implemented")


def main():
    parser = argparse.ArgumentParser(description="L4 arxiv stub collector")
    parser.add_argument("--output", default="/tmp/l4-out.json")
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()
    result = {"date": "2026-05-27", "source": "l4_arxiv", "tweets": []}
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    print(f"L4 disabled, written empty result to {args.output}")


if __name__ == "__main__":
    main()
