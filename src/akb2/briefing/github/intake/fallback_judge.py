"""Three-state fallback judge — Layer1 OK / fail → Layer2 / fail → Layer3 / skip."""

from datetime import date
from pathlib import Path
from typing import Callable, Literal, NamedTuple

from . import Candidate, CollectorResult

FALLBACK_FAILURE_RATE_THRESHOLD = 0.5
MIN_CANDIDATES_LAYER1 = 5
MIN_CANDIDATES_LAYER2 = 10


class FallbackDecision(NamedTuple):
    selected_layer: Literal["layer1", "layer2", "layer3", "skip"]
    reason: str
    candidates: list[Candidate]


def decide(
    layer1_result: CollectorResult,
    layer2_runner: Callable[[], CollectorResult],
    layer3_path: Path | None = None,
) -> FallbackDecision:
    # Layer 1: check if OK
    l1_fail_rate = (
        len(layer1_result.failed_handles) / max(len(layer1_result.failed_handles) + len(layer1_result.candidates), 1)
        if layer1_result.failed_handles
        else 0.0
    )
    # Use the pool size approximation from failed + candidates
    l1_pool_approx = len(layer1_result.failed_handles) + len(layer1_result.candidates)
    l1_fail_rate_pool = len(layer1_result.failed_handles) / max(l1_pool_approx, 1)

    if layer1_result.ok and len(layer1_result.candidates) >= MIN_CANDIDATES_LAYER1:
        return FallbackDecision("layer1", "Layer 1 OK", layer1_result.candidates)

    # Layer 1 failed → try Layer 2
    layer2_result = layer2_runner()
    if layer2_result.ok and len(layer2_result.candidates) >= MIN_CANDIDATES_LAYER2:
        return FallbackDecision("layer2",
                                f"Layer 1 fail_rate={l1_fail_rate_pool:.2f}, candidates={len(layer1_result.candidates)}",
                                layer2_result.candidates)

    # Layer 2 failed → try Layer 3
    if layer3_path is None:
        layer3_path = Path(f"data/manual/{date.today().isoformat()}.json")

    from .manual_json_collector import ManualJSONCollector
    layer3_result = ManualJSONCollector().collect(layer3_path)
    if layer3_result.ok and len(layer3_result.candidates) >= 10:
        return FallbackDecision("layer3",
                                f"Layer 2 failed: {layer2_result.error}",
                                layer3_result.candidates)

    return FallbackDecision("skip",
                            f"All layers exhausted: L1={layer1_result.error}, L2={layer2_result.error}, L3={layer3_result.error}",
                            [])