"""concept 加权质量分（过阈不排序）——宪法原则 8：地板之上加权分，过阈二值裁决。

step-3 SC-3d：读 rubric.yaml lanes.<lane>.dimensions 权重，加权合成单一分，过阈(6.5)=pass。
  - 分数仅用于过阈二值裁决 NOT 排序（无 sort/rank）——宪法原则 8「过阈不排序」。
  - H3 深度评分独立于地板：最小合规（3 组件 1 因果边）H3 深度 ≤1.0，总分<6.5 仍拒。
  - dim→weight 按全名 join；维名不在 lane 权重表 → KeyError（不静默零权重）。
  - 地板维（integrity_floor hard=true）不进加权池——地板 fail 时 runner early-return，
    加权分函数 NOT 调用（宪法 8(a) 不可补偿，见 runner._run_quality_segments）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.akb2.quality.grader import _load_rubric_spec

# concept 车道默认过阈（spec FR-010 真实校准值由 rubric.yaml threshold 给，此处仅兜底）
DEFAULT_THRESHOLD: float = 6.5


@dataclass
class ScoreResult:
    """加权质量分结果（过阈二值裁决）。

    Attributes:
        score: 加权总分（Σ score_i × weight_i）。
        threshold: 过阈值（rubric.yaml threshold）。
        passed: score >= threshold。
        lane: 评估车道。
        dimension_weights: {维名: weight}（debug/审计，机读不面向用户）。
    """

    score: float
    threshold: float
    passed: bool
    lane: str = ""
    dimension_weights: dict[str, float] = field(default_factory=dict)


def _lane_weight_map(spec: dict, content_lane: str) -> dict[str, float]:
    """从 spec.lanes.<lane>.dimensions 构建 {维名: weight}。

    lane 缺省/未知 → 回退 procedural（零回归）；concept 用 lanes.concept 7 维权重集。
    """
    lane = str(content_lane or "").strip().lower()
    lanes = spec.get("lanes", {}) or {}
    lane_spec = lanes.get(lane)
    if lane_spec is None:
        lane_spec = lanes.get("procedural") or {}
    weights: dict[str, float] = {}
    for dim in lane_spec.get("dimensions", []) or []:
        if isinstance(dim, dict) and "name" in dim:
            w = dim.get("weight", 0)
            weights[str(dim["name"])] = float(w) if isinstance(w, (int, float)) else 0.0
    return weights


def _lane_threshold(spec: dict, content_lane: str) -> float:
    """按 lane 读阈值（codex worker）：lanes.<lane>.threshold → 顶层 threshold → DEFAULT_THRESHOLD。

    改 rubric.yaml lanes.concept.threshold 即改 concept 判定，不被顶层 threshold 覆盖。
    """
    lane = str(content_lane or "").strip().lower()
    lane_spec = (spec.get("lanes", {}) or {}).get(lane) or {}
    t = lane_spec.get("threshold")
    if isinstance(t, (int, float)):
        return float(t)
    top = spec.get("threshold")
    if isinstance(top, (int, float)):
        return float(top)
    return DEFAULT_THRESHOLD


def compute_weighted_score(
    dimension_scores: dict[str, float],
    content_lane: str = "",
) -> ScoreResult:
    """计算加权质量分（过阈二值裁决，不排序）。

    Args:
        dimension_scores: {维名: score}（来自 grader 逐维评分，GraderResult.dimension_scores）。
        content_lane: concept/procedural（选 lane 维度集+权重；concept 用 7 维）。

    Returns:
        ScoreResult(score, threshold, passed)。

    Raises:
        KeyError: dimension_scores 含维名不在 lane 权重表（不静默零权重，防漏维）。
    """
    spec = _load_rubric_spec()
    threshold = _lane_threshold(spec, content_lane)
    weights = _lane_weight_map(spec, content_lane)

    # dim→weight 按全名 join；维名不在权重表 → KeyError（不静默零权重）
    is_concept = str(content_lane or "").strip().lower() == "concept"
    score = 0.0
    for name, sc in dimension_scores.items():
        if name not in weights:
            raise KeyError(
                f"维度 {name!r} 不在 {content_lane or 'procedural'} 车道 rubric 权重表"
                f"（已知权重维: {sorted(weights)}）"
            )
        # SC-1（p0-1）：concept 车道每维 score 必须是合法 0~1 数值
        # （拒 bool/非数值/越界 fail-closed，不静默当 0 放行）；procedural 保持旧口径零回归。
        if isinstance(sc, bool) or not isinstance(sc, (int, float)):
            if is_concept:
                return ScoreResult(
                    score=0.0, threshold=threshold, passed=False,
                    lane=content_lane, dimension_weights=weights,
                )
            s = 0.0
        elif is_concept and (sc < 0 or sc > 1):
            return ScoreResult(
                score=0.0, threshold=threshold, passed=False,
                lane=content_lane, dimension_weights=weights,
            )
        else:
            s = sc
        score += s * weights[name]

    # concept: 要求所有 lane 维度都有 score（缺维 = 评估不完整 → fail-closed，不静默放行）。
    # codex worker #2：之前只累加 present 维，缺维被静默忽略，高 present 分可过阈。
    missing_dims: list[str] = []
    if str(content_lane or "").strip().lower() == "concept":
        missing_dims = sorted(set(weights.keys()) - set(dimension_scores.keys()))
        if missing_dims:
            # 缺维 → 评估不完整，fail-closed（score=0 拒，不靠 present 维高分放行）
            return ScoreResult(
                score=0.0,
                threshold=threshold,
                passed=False,
                lane=content_lane,
                dimension_weights=weights,
            )

    return ScoreResult(
        score=score,
        threshold=threshold,
        passed=score >= threshold,
        lane=content_lane,
        dimension_weights=weights,
    )
