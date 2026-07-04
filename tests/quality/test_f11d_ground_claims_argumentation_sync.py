"""test_f11d_ground_claims_argumentation_sync.py — F11-D argumentation claim_anchor 同步测试。

probe-p9b B 臂 idx11 orchestration-tax distill_extract_failed:
"deterministic_maker_fallback_failed: argumentation[0] claim_anchor '§A' 不在 claims anchors 中"。

根因:F9-A _normalize_extract_payload 调 _ground_claims_to_raw,claims[0].anchor §A 被接地
到 §G_EXAMPLES(claim 文本实际在 §G,§A 不在 stripped body 的 declared_set → 弱匹配 remap),
但 argumentation[0].claim_anchor 仍 §A(未跟)→ validation "claim_anchor 不在 claims anchors" fail。
修:_ground_claims_to_raw 记录 anchor_remap(old→new),argumentation.claim_anchor 全 provenance 同步。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.distill import (
    _build_deterministic_extract_data,
    _ground_claims_to_raw,
    _normalize_extract_payload,
    build_deterministic_extract_result,
)

_WT = Path(__file__).resolve().parents[2]
_IDX11_RAW = (
    _WT / "reports/akb2-quality-repair/_tmp/raw-store/"
    "377720355a67c84e254f483c99b24190d4c106f9f99b87ba779ead5c68653717.md"
)


class TestF11DArgumentationClaimAnchorSync:
    def test_idx11_no_claim_anchor_mismatch(self):
        """idx11 fallback 不再 'claim_anchor 不在 claims anchors'(validation 过)。"""
        if not _IDX11_RAW.exists():
            import pytest
            pytest.skip("idx11 raw fixture 不存在")
        raw = _IDX11_RAW.read_text()
        res = build_deterministic_extract_result(raw, content_lane="concept")
        # 不应有 argumentation claim_anchor 不匹配错误(修复后转到别的 fail 或成功)
        assert "claim_anchor" not in (res.error or ""), (
            f"仍有 argumentation claim_anchor 不匹配: {res.error}"
        )
        assert "不在 claims anchors" not in (res.error or ""), res.error

    def test_argumentation_claim_anchor_syncs_on_remap(self):
        """claim 锚被接地 remap → argumentation.claim_anchor 同步,ground 后全 ∈ claims anchors。"""
        raw = (
            "---\nsource_slug: test\n---\n\n"
            "## 锚点目录\n\n| §A | x |\n\n---\n\n"
            "§A_CONTENT_START\n\n"
            "§G_EXAMPLES\n\nThe loop engineering claim text is grounded here.\n"
        )
        data = _build_deterministic_extract_data(raw, "test.md", content_lane="concept")
        _ground_claims_to_raw(data, raw)
        # ground 后:所有 argumentation.claim_anchor ∈ claims anchors(不变式)
        claim_anchors = {c.get("anchor") for c in data.get("claims", [])}
        for i, arg in enumerate(data.get("argumentation", []) or []):
            ca = arg.get("claim_anchor")
            if ca:
                assert ca in claim_anchors, (
                    f"argumentation[{i}] claim_anchor {ca!r} 不在 claims anchors(未同步)"
                )

    def test_normalize_payload_no_argumentation_mismatch(self):
        """_normalize_extract_payload 后 argumentation.claim_anchor 全 ∈ claims anchors。"""
        if not _IDX11_RAW.exists():
            import pytest
            pytest.skip("idx11 raw fixture 不存在")
        raw = _IDX11_RAW.read_text()
        data = _build_deterministic_extract_data(raw, "test.md", content_lane="concept")
        _normalize_extract_payload(data, raw, "concept", is_fallback=True)
        claim_anchors = {c.get("anchor") for c in data.get("claims", [])}
        for i, arg in enumerate(data.get("argumentation", []) or []):
            ca = arg.get("claim_anchor")
            if ca:
                assert ca in claim_anchors, (
                    f"argumentation[{i}] claim_anchor {ca!r} 不在 claims anchors {claim_anchors}"
                )
