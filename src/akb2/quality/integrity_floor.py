"""完整性地板（integrity floor）——四成员二值硬卡，不可被加权分补偿（宪法原则 8(a)）。

step-3 SC-3b/SC-3h/A3-2/A3-5：循环1 在 distill 段评估结构化 extract 的四个完整性地板成员，
任一 fail → FloorResult.passed=False，runner 评分段 early-return 直接 reject、加权分函数 NOT 调用。

四成员（concept，全 evaluated_by=structural）：
  ① claim_anchor_rate     — 每条 source_grounded claim 都带真实 anchor（覆盖率<100% fail，A3-2，
                            区别于 SG-raw-anchor 的「有任意锚点」）
  ② evidence_honesty      — 证据地图含「未覆盖」列且 ≥1 行标真实盲区（裸来源断言 fail，A3-2）
  ③ interpretation_traceability(层①) — 每条 akb_interpretation 带 traces_to 指向 claims 中真实
                            存在的 source_grounded anchor（确定性 pre-grader 地板；F3 层② entailment
                            由 grader 异族硬判，见 grader.interpretation_entailment）
  ④ F4 车道核心价值        — concept: len(mechanism_components)≥3 且每组件在 causal_edges 有出/入边
                            （只读结构字段，不反解 H3 标签，A3-5）；procedural: H1≥1

设计原则：
- 二值（pass/fail），无连续分（宪法原则 8）。
- 不可补偿：任一 fail 即 reject，与加权质量分无关（加权分只在地板全 pass 后才算）。
- 结构真相源：F4 只读 mechanism_components/causal_edges 结构字段，不从自然语言/H3 标签反解。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 地板成员名（与 rubric.yaml integrity_floor 对齐；机读 key，不面向用户）
FLOOR_CLAIM_ANCHOR_RATE = "claim_anchor_rate"
FLOOR_EVIDENCE_HONESTY = "evidence_honesty"
FLOOR_INTERPRETATION_TRACEABILITY = "interpretation_traceability"
FLOOR_CORE_VALUE_F4 = "core_value_f4"

# concept F4 命名机制组件下限（与 distill.CONCEPT_MIN_MECHANISM_COMPONENTS 同语义）
CONCEPT_MIN_MECHANISM_COMPONENTS: int = 3

# 证据盲区词（与 distill._validate_extract_output 的 gap_words 同源）——
# evidence_honesty 要求 ≥1 条 claim 标注真实盲区，盲区文本须含这些词之一。
EVIDENCE_GAP_WORDS: tuple[str, ...] = (
    "未提供",
    "未量化",
    "未覆盖",
    "缺少",
    "没有",
    "未验证",
    "未比较",
)

_VALID_PROVENANCES = {"source_grounded", "akb_interpretation"}


@dataclass
class FloorResult:
    """完整性地板评估结果。

    Attributes:
        passed: 四成员是否全过（任一 fail → False）。
        failed_floors: fail 的成员名列表（机读 key）。
        reason: 人读失败原因（passed=True 时为空）。
        content_lane: 评估时的车道。
    """

    passed: bool
    failed_floors: list[str] = field(default_factory=list)
    reason: str = ""
    content_lane: str = ""
    meta: dict = field(default_factory=dict)


def _is_concept_lane(content_lane: str) -> bool:
    return str(content_lane or "").strip().lower() == "concept"


def _claim_anchor_rate_failed(extract_data: dict) -> tuple[bool, str]:
    """① 每条 source_grounded claim 都带真实 anchor（§X 格式）。

    覆盖率 <100% 即 fail（A3-2，区别于「有任意锚点」）。空 claims 视为 fail（无据可锚）。
    """
    claims = extract_data.get("claims", []) or []
    grounded = [
        c
        for c in claims
        if isinstance(c, dict) and c.get("provenance") == "source_grounded"
    ]
    if not grounded:
        return True, "无 source_grounded claim（无据可锚定）"
    missing = [
        c
        for c in grounded
        if not str(c.get("anchor", "")).strip().startswith("§")
    ]
    if missing:
        return True, (
            f"{len(missing)}/{len(grounded)} 条 source_grounded claim 缺真实 anchor"
            "（论断锚定率 <100%）"
        )
    return False, ""


def _evidence_honesty_failed(extract_data: dict) -> tuple[bool, str]:
    """② 证据地图含「未覆盖」列且 ≥1 行标真实盲区。

    extract 层用 claims 的 evidence_gap 作证据地图「未覆盖」列：每条 claim 须有非空
    evidence_gap，且 ≥1 条含真实盲区词（裸来源断言/复述 claim 不算盲区 → fail）。
    """
    claims = extract_data.get("claims", []) or []
    if not claims:
        return True, "无 claims，证据地图为空"
    gaps = [str(c.get("evidence_gap", "")).strip() for c in claims if isinstance(c, dict)]
    if any(not g for g in gaps):
        return True, "存在 claim 缺证据盲区（证据地图「未覆盖」列不完整）"
    has_real_blind_spot = any(
        any(w in g for w in EVIDENCE_GAP_WORDS) for g in gaps
    )
    if not has_real_blind_spot:
        return True, "证据地图无任一行标注真实盲区（裸来源断言，证据不诚实）"
    return False, ""


def _interpretation_traceability_failed(extract_data: dict) -> tuple[bool, str, bool]:
    """③ 解读分离层①：每条 akb_interpretation 带 traces_to 指向真实存在的 source_grounded anchor。

    确定性 pre-grader 地板——只判「溯源标注存在且指向真实锚点」，不判「推导是否成立」
    （后者由 grader 异族 entailment 硬判，Decision-1 层②）。

    akb_interpretation 来源：claims（provenance=akb_interpretation 的条目）+ argumentation
    （provenance=akb_interpretation 的条目）。每条须带 traces_to 列表，列表中 ≥1 个 anchor
    在 claims 的 source_grounded anchor 集合里真实存在。

    Returns:
        (failed, reason, skipped_no_grounded) — 第 3 项 SC-5：grounded 空且有 interpretation
        时 traceability 被豁免（不可满足），供 evaluate 写 meta 治诊断变弱。
    """
    claims = extract_data.get("claims", []) or []
    argumentation = extract_data.get("argumentation", []) or []

    grounded_anchors = {
        str(c.get("anchor", "")).strip()
        for c in claims
        if isinstance(c, dict) and c.get("provenance") == "source_grounded"
    }
    grounded_anchors.discard("")

    interpretations: list[dict] = []
    for c in claims:
        if isinstance(c, dict) and c.get("provenance") == "akb_interpretation":
            interpretations.append(c)
    for a in argumentation:
        if isinstance(a, dict) and a.get("provenance") == "akb_interpretation":
            interpretations.append(a)

    for i, item in enumerate(interpretations):
        traces = item.get("traces_to")
        trace_anchors: list[str] = []
        if isinstance(traces, list):
            trace_anchors = [str(t).strip() for t in traces if str(t).strip()]
        elif isinstance(traces, str) and traces.strip():
            trace_anchors = [traces.strip()]
        if not trace_anchors:
            return True, (
                f"akb_interpretation[{i}] 缺 traces_to 溯源标注（无据飘浮读后感）"
            ), False
        # SC-5：grounded_anchors 空时豁免——"≥1 traces_to 命中真实 grounded anchor"
        # 在无 source_grounded anchor 时不可满足（无源页 fixture 等场景，extraction 产不出
        # grounded anchor），floor ③ 不应误判 fail；缺 grounded 的真问题由 floor ①
        # claim_anchor_rate 抓（test_fail_no_grounded_claims 已锁）。
        if grounded_anchors and not any(t in grounded_anchors for t in trace_anchors):
            return True, (
                f"akb_interpretation[{i}] traces_to {trace_anchors} 未指向任何真实存在的"
                " source_grounded anchor"
            ), False
    # SC-5：grounded 空且有 interpretation → traceability 豁免（不可满足），返 skipped 标志
    # 供 evaluate_integrity_floor 写 meta（治诊断变弱——失败归因从 traceability 转
    # claim_anchor_rate，需可见，防未来弱化 floor ① 变 false negative）。
    skipped_no_grounded = (not grounded_anchors) and bool(interpretations)
    return False, "", skipped_no_grounded


def _trusted_raw_anchors(
    extract_data: dict, raw_anchors: set[str] | None = None
) -> set[str]:
    """§0.2/F1 + F1-followup + F1-regex：可信 raw anchor 集——raw 真实声明 §-anchor 全集。

    **只认 raw_anchors**（runner 从 raw_content 解析的真实 §X 全集，认 #{1,6} 多#格式），
    **不回落 grounded claims anchors**——codex 查出反方向放水缝：LLM 可把 raw 里不存在的
    §Z 同时写进 source_grounded claim 和 edge，§Z 进 claims → 进 trusted（旧回落口径）→
    伪边计 eligible 混过 F4。根治：trusted = raw 真实声明全集 → §Z 不在 raw 声明集 → 不 trusted。

    raw_anchors 为 None/空 → 返空集（fail-closed，所有边 untrusted → 0 eligible → F4 fail）。
    调用方必须传 raw_anchors（runner 已传；测试需显式传）。
    """
    if raw_anchors:
        return set(raw_anchors)
    # §0.2/F1-regex 根治：不回落 claims anchors（防 LLM 伪造 §Z 进 claim+edge 混过 F4）
    return set()


def _core_value_f4_failed(
    extract_data: dict,
    content_lane: str,
    raw_anchors: set[str] | None = None,
) -> tuple[bool, str]:
    """④ 车道核心价值（F4）。

    §0.2/D1 + §0.2/F1：F4 只认 eligible edge = ``edge_kind ∈ {source_causal, llm_evidence}``
    + ``evidence`` 非空 + ``evidence_anchor`` 非空 **且 ∈ 可信 raw anchor 集**
    （grounded claims 的 §X anchor）。伪 §Z / 合成 §raw 不计 eligible（防伪边过 F4）。
    ``inferred_from_structure`` 弱边**一律不计入 F4**（只渲染进正文表 + 交 grader H3
    审语义）。0 eligible edge → F4 fail（runner 据需 retake 信号补抽，仍 0 才 fail-closed）。

    - concept: ≥3 唯一命名组件 + ≥1 eligible edge（anchor 必须真实）。
    - procedural: how_steps 中至少 1 条 how_level==H1（命令级），即 H1≥1。
    """
    if _is_concept_lane(content_lane):
        components = extract_data.get("mechanism_components", []) or []
        named = [
            str(c.get("name", "")).strip()
            for c in components
            if isinstance(c, dict) and str(c.get("name", "")).strip()
        ]
        # codex worker：先去重再计数，防 [A,A,B] 用重复名伪装 3 组件过 F4
        named_set = set(named)
        if len(named_set) < CONCEPT_MIN_MECHANISM_COMPONENTS:
            return True, (
                f"concept F4：唯一命名机制组件 {len(named_set)} < "
                f"{CONCEPT_MIN_MECHANISM_COMPONENTS}"
                f"（输入 {len(named)} 条，去重后 {len(named_set)}）"
            )
        edges = extract_data.get("causal_edges", []) or []
        # §0.2/D1 + F1 + F1-followup：统计 eligible edge（弱边不计；
        # evidence_anchor 须 ∈ raw 真实 anchor 全集，不只 grounded claims）
        eligible_kinds = {"source_causal", "llm_evidence"}
        trusted_anchors = _trusted_raw_anchors(extract_data, raw_anchors)
        eligible_count = 0
        weak_count = 0
        untrusted_anchor_count = 0
        eligible_components: set[str] = set()
        for e in edges:
            if not isinstance(e, dict):
                continue
            src = str(e.get("from", "")).strip()
            dst = str(e.get("to", "")).strip()
            if src not in named_set or dst not in named_set or src == dst:
                continue
            kind = str(e.get("edge_kind", "")).strip()
            evidence = str(e.get("evidence", "")).strip()
            evidence_anchor = str(e.get("evidence_anchor", "")).strip()
            if kind in eligible_kinds and evidence and evidence_anchor:
                # §0.2/F1：evidence_anchor 必须是真实 raw anchor（∈ grounded claims anchors）
                if evidence_anchor in trusted_anchors:
                    eligible_count += 1
                    eligible_components.add(src)
                    eligible_components.add(dst)
                else:
                    untrusted_anchor_count += 1
            elif kind == "inferred_from_structure":
                weak_count += 1
        if eligible_count == 0:
            return True, (
                f"concept F4：0 eligible 因果边（source_causal/llm_evidence 带 evidence + "
                f"evidence_anchor ∈ 真实 raw anchor 集）；{weak_count} 条 inferred 弱边不计 F4；"
                f"{untrusted_anchor_count} 条边 evidence_anchor 不在可信 anchor 集"
                f"（伪 §Z/合成 §raw 不计）"
                f"（§0.2 D1+F1：弱边/伪 anchor 不救 F4）"
            )
        return False, ""

    # procedural F4：H1≥1
    how_steps = extract_data.get("how_steps", []) or []
    h1_count = sum(
        1 for s in how_steps if isinstance(s, dict) and s.get("how_level") == "H1"
    )
    if h1_count < 1:
        return True, "procedural F4：无 H1 命令级步骤（H1<1）"
    return False, ""


def evaluate_integrity_floor(
    extract_data: dict,
    content_lane: str = "",
    raw_anchors: set[str] | None = None,
) -> FloorResult:
    """评估完整性地板四成员（concept）/ 对应 procedural 成员。

    任一成员 fail → passed=False；runner 评分段须先看本结果 early-return。

    Args:
        extract_data: 结构化 extract 输出（claims/argumentation/mechanism_components/
            causal_edges/how_steps）。
        content_lane: 内容车道（concept/procedural，默认 procedural 兼容）。
        raw_anchors: §0.2/F1-followup：raw 真实 §-anchor 全集（runner 从 raw_content 解析）。
            F4 eligible edge 的 evidence_anchor 须在此集内（不只 grounded claims）；
            None 时回落 grounded claims anchors（兼容旧测试）。

    Returns:
        FloorResult: 二值 passed + failed_floors + reason。
    """
    failed: list[str] = []
    reasons: list[str] = []

    anchor_failed, anchor_reason = _claim_anchor_rate_failed(extract_data)
    if anchor_failed:
        failed.append(FLOOR_CLAIM_ANCHOR_RATE)
        reasons.append(anchor_reason)

    honesty_failed, honesty_reason = _evidence_honesty_failed(extract_data)
    if honesty_failed:
        failed.append(FLOOR_EVIDENCE_HONESTY)
        reasons.append(honesty_reason)

    trace_failed, trace_reason, trace_skipped = _interpretation_traceability_failed(
        extract_data
    )
    if trace_failed:
        failed.append(FLOOR_INTERPRETATION_TRACEABILITY)
        reasons.append(trace_reason)
    trace_meta: dict = {}
    if trace_skipped:
        # SC-5：traceability 因 grounded 空被豁免——写可见 meta，治诊断变弱
        trace_meta["traceability_skipped_no_grounded_anchor"] = True

    f4_failed, f4_reason = _core_value_f4_failed(
        extract_data, content_lane, raw_anchors=raw_anchors
    )
    if f4_failed:
        failed.append(FLOOR_CORE_VALUE_F4)
        reasons.append(f4_reason)
        # §0.2/F1 太脆：concept F4 因 0 eligible edge 失败（非 <3 组件）+ 组件≥3 →
        # 标 needs_causal_edge_retake，runner 据此补抽 causal edges（仍 0 才 fail-closed），
        # 不静默 F4 早死救不回真机漏字段。
        if _is_concept_lane(content_lane):
            components = extract_data.get("mechanism_components", []) or []
            named_set = {
                str(c.get("name", "")).strip()
                for c in components
                if isinstance(c, dict) and str(c.get("name", "")).strip()
            }
            if (
                len(named_set) >= CONCEPT_MIN_MECHANISM_COMPONENTS
                and "0 eligible" in f4_reason
            ):
                trace_meta["needs_causal_edge_retake"] = True

    return FloorResult(
        passed=not failed,
        failed_floors=failed,
        reason="; ".join(reasons),
        content_lane=content_lane,
        meta=trace_meta,
    )
