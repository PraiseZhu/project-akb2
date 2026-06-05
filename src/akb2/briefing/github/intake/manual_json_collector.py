"""Layer 3: 读 data/manual/<YYYY-MM-DD>.json 人工注入 candidates."""

import json
import time
from datetime import date
from pathlib import Path

from ..url_normalizer import normalize
from . import _ZERO_RECENCY_STATS, Candidate, CollectorResult

MANUAL_DIR = Path("data/manual")


class ManualJSONCollector:
    def __init__(self, manual_dir: Path | None = None):
        self._manual_dir = manual_dir or MANUAL_DIR

    def collect(self, manual_json_path: Path | None = None) -> CollectorResult:
        t0 = time.monotonic()

        if manual_json_path is None:
            today = date.today().isoformat()
            manual_json_path = self._manual_dir / f"{today}.json"

        if not manual_json_path.exists():
            return CollectorResult("layer3", [], [], time.monotonic() - t0, False,
                                   f"manual JSON not found: {manual_json_path}",
                                   _ZERO_RECENCY_STATS)

        try:
            with open(manual_json_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return CollectorResult("layer3", [], [], time.monotonic() - t0, False,
                                   f"failed to read manual JSON: {e}",
                                   _ZERO_RECENCY_STATS)

        entries = data if isinstance(data, list) else data.get("candidates", [])

        candidates = []
        for entry in entries:
            repo_url = entry.get("repo_url")
            mentioner = entry.get("mentioner")
            mentioned_at = entry.get("mentioned_at")

            # Schema contract: repo_url + mentioner + mentioned_at all required
            if not repo_url or not mentioner or not mentioned_at:
                return CollectorResult("layer3", [], [], time.monotonic() - t0, False,
                                       f"manual entry missing required field: {entry}",
                                       _ZERO_RECENCY_STATS)

            normalized = normalize(repo_url)
            if normalized is None:
                continue

            candidates.append(Candidate(
                repo_url=normalized,
                mentioner=str(mentioner),
                tweet_id=entry.get("tweet_id"),
                mentioned_at=str(mentioned_at),
                tweet_text=entry.get("tweet_text"),
            ))

        return CollectorResult("layer3", candidates, [], time.monotonic() - t0,
                               len(candidates) > 0,
                               (None if len(candidates) > 0
                                else "no valid candidates in manual JSON"),
                               _ZERO_RECENCY_STATS)
