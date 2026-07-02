"""test_ground_claims_to_raw.py — P1b-1 claims/argumentation 接地校正测试。

覆盖:
    - 弱匹配:source_grounded claim 假锚(§Z)→ 就近 remap 真实声明锚(不升级 provenance)
    - 强匹配:akb_interpretation claim 近全文转写(连续片段覆盖≥60%)→ 升级 source_grounded
      + remap anchor + 补 traces_to
    - paraphrase 反例:主题词共现但谓词非原文 → 不达 60% 连续覆盖 → 不升级(保持 interpretation)
    - [B2] argumentation 补填:claim_anchor 指向已接地 source_grounded claim → 填 traces_to
    - [B2] argumentation 不补填:claim_anchor 不在 grounded → 不补(fail-closed)
    - golden 幂等:已接地/已正确者 no-op;二调 no-op
    - 无 raw/无声明锚 → fail-closed no-op
"""

from __future__ import annotations

from src.akb2.quality.distill import _ground_claims_to_raw

# ---------------------------------------------------------------------------
# 测试用 raw(heading 形态声明 §A/§B + 两段正文)
# ---------------------------------------------------------------------------

_RAW = """\
## §A Overview

The model that produces content must be different from the model that judges it.

## §B Budgets

Every loop must have explicit budgets for max repair rounds and timeout per model call.

## §C Paraphrase Region

Executor Pool dispatches tasks to the Task Queue for scheduling.
"""


def _claim(text: str, anchor: str, prov: str, **extra) -> dict:
    c = {"text": text, "anchor": anchor, "provenance": prov,
         "evidence_gap": "未提供 具体数据"}
    c.update(extra)
    return c


# ---------------------------------------------------------------------------
# 弱匹配:假锚校正
# ---------------------------------------------------------------------------


class TestWeakMatchFalseAnchorCorrection:
    def test_source_grounded_false_anchor_remapped_no_upgrade(self):
        """source_grounded claim 锚=§Z(不在声明集)但文本弱匹配 §B 正文 → remap §B,
        provenance 仍 source_grounded(弱匹配不升级)。"""
        merged = {
            "claims": [
                _claim(
                    "Every loop must have explicit budgets for max repair rounds and timeout per model call.",
                    "§Z",  # 假锚(§Z 不在 raw 声明集 {§A,§B,§C})
                    "source_grounded",
                ),
            ],
            "argumentation": [],
        }
        _ground_claims_to_raw(merged, _RAW)
        c = merged["claims"][0]
        assert c["anchor"] == "§B"  # remap 到就近真实声明锚
        assert c["provenance"] == "source_grounded"  # 不升级(本就 grounded)

    def test_weak_match_no_match_leaves_anchor_unchanged(self):
        """source_grounded 假锚 + 文本不在 raw → 不改(fail-closed,留原锚由 A3/floor 裁)。"""
        merged = {
            "claims": [
                _claim("完全不在原文出现的独特论述内容 xyz unique unrelated claim.",
                       "§Z", "source_grounded"),
            ],
            "argumentation": [],
        }
        _ground_claims_to_raw(merged, _RAW)
        assert merged["claims"][0]["anchor"] == "§Z"  # 未改
        assert merged["claims"][0]["provenance"] == "source_grounded"


# ---------------------------------------------------------------------------
# 强匹配:interpretation→grounded 升级
# ---------------------------------------------------------------------------


class TestStrongMatchUpgrade:
    def test_near_verbatim_interpretation_upgraded_to_grounded(self):
        """akb_interpretation claim 近全文转写(连续片段覆盖≥60%)→ 升级 source_grounded
        + remap anchor + 补 traces_to。"""
        merged = {
            "claims": [
                _claim(
                    "The model that produces content must be different from the model that judges it.",
                    "§A",  # 锚正确但在 interpretation 状态
                    "akb_interpretation",
                ),
            ],
            "argumentation": [],
        }
        _ground_claims_to_raw(merged, _RAW)
        c = merged["claims"][0]
        assert c["provenance"] == "source_grounded"  # 升级
        assert c["anchor"] == "§A"
        assert c.get("traces_to") == ["§A"]  # 补 traces_to


class TestParaphraseReverse:
    def test_paraphrase_not_upgraded(self):
        """[B1] paraphrase 反例:主题词(Executor Pool/Task Queue)共现但谓词非原文
        (claim: 'guarantees fairness' vs raw: 'dispatches tasks')→ 不达 60% 连续覆盖
        → 不升级,保持 akb_interpretation。"""
        merged = {
            "claims": [
                _claim(
                    "Executor Pool guarantees fairness in Task Queue",
                    "§C",
                    "akb_interpretation",
                ),
            ],
            "argumentation": [],
        }
        _ground_claims_to_raw(merged, _RAW)
        c = merged["claims"][0]
        assert c["provenance"] == "akb_interpretation"  # 不升级
        assert c["anchor"] == "§C"  # 未 remap
        assert not c.get("traces_to")  # 不补造


# ---------------------------------------------------------------------------
# [B2] argumentation 保守补填
# ---------------------------------------------------------------------------


class TestArgumentationBackfill:
    def test_backfill_when_claim_anchor_is_grounded(self):
        """argumentation akb_interpretation traces_to 空 + claim_anchor 指向已严格接地
        source_grounded claim → 填 traces_to=[claim_anchor]。"""
        merged = {
            "claims": [
                # 已正确接地的 claim(§A 在声明集)
                _claim("The model that produces content must be different from the model that judges it.",
                       "§A", "source_grounded"),
            ],
            "argumentation": [
                {"claim_anchor": "§A", "explanation": "maker/checker 分离是 loop 工程的硬约束。",
                 "provenance": "akb_interpretation"},
            ],
        }
        _ground_claims_to_raw(merged, _RAW)
        assert merged["argumentation"][0]["traces_to"] == ["§A"]

    def test_no_backfill_when_claim_anchor_not_grounded(self):
        """argumentation claim_anchor=§Z(不在 grounded)→ 不补 traces_to(fail-closed)。"""
        merged = {
            "claims": [
                _claim("The model that produces content must be different from the model that judges it.",
                       "§A", "source_grounded"),
            ],
            "argumentation": [
                {"claim_anchor": "§Z", "explanation": "某解读指向不存在的锚。",
                 "provenance": "akb_interpretation"},
            ],
        }
        _ground_claims_to_raw(merged, _RAW)
        assert not merged["argumentation"][0].get("traces_to")

    def test_no_backfill_when_traces_to_already_set(self):
        """argumentation 已有 traces_to → 不动(幂等)。"""
        merged = {
            "claims": [
                _claim("The model that produces content must be different from the model that judges it.",
                       "§A", "source_grounded"),
            ],
            "argumentation": [
                {"claim_anchor": "§A", "explanation": "已有溯源的解读。",
                 "provenance": "akb_interpretation", "traces_to": ["§A"]},
            ],
        }
        _ground_claims_to_raw(merged, _RAW)
        assert merged["argumentation"][0]["traces_to"] == ["§A"]  # 未改


# ---------------------------------------------------------------------------
# golden 幂等
# ---------------------------------------------------------------------------


class TestGoldenIdempotent:
    def test_fully_grounded_extract_no_changes(self):
        """全 source_grounded + 正确锚 + argumentation 已有 traces_to → no-op。"""
        merged = {
            "claims": [
                _claim("The model that produces content must be different from the model that judges it.",
                       "§A", "source_grounded"),
                _claim("Every loop must have explicit budgets for max repair rounds and timeout per model call.",
                       "§B", "source_grounded"),
            ],
            "argumentation": [
                {"claim_anchor": "§A", "explanation": "maker/checker 分离约束。",
                 "provenance": "akb_interpretation", "traces_to": ["§A"]},
            ],
        }
        before = {
            "claims": [dict(c) for c in merged["claims"]],
            "argumentation": [dict(a) for a in merged["argumentation"]],
        }
        _ground_claims_to_raw(merged, _RAW)
        assert merged["claims"] == before["claims"]
        assert merged["argumentation"] == before["argumentation"]

    def test_second_run_is_noop(self):
        """二调幂等:第一次补填 traces_to 后,第二次 no-op。"""
        merged = {
            "claims": [
                _claim("The model that produces content must be different from the model that judges it.",
                       "§A", "source_grounded"),
            ],
            "argumentation": [
                {"claim_anchor": "§A", "explanation": "解读。",
                 "provenance": "akb_interpretation"},
            ],
        }
        _ground_claims_to_raw(merged, _RAW)
        after_first = {
            "claims": [dict(c) for c in merged["claims"]],
            "argumentation": [dict(a) for a in merged["argumentation"]],
        }
        _ground_claims_to_raw(merged, _RAW)  # 二调
        assert merged["claims"] == after_first["claims"]
        assert merged["argumentation"] == after_first["argumentation"]


# ---------------------------------------------------------------------------
# fail-closed:无 raw / 无声明锚
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_no_raw_content_noop(self):
        merged = {"claims": [_claim("foo", "§A", "source_grounded")], "argumentation": []}
        _ground_claims_to_raw(merged, None)
        assert merged["claims"][0]["anchor"] == "§A"  # 未改

    def test_raw_no_declared_anchors_noop(self):
        """raw 无任何声明锚 → 无法接地,no-op(fail-closed)。"""
        raw = "plain text without any anchor declarations at all."
        merged = {"claims": [_claim("plain text", "§A", "akb_interpretation")],
                  "argumentation": []}
        _ground_claims_to_raw(merged, raw)
        assert merged["claims"][0]["provenance"] == "akb_interpretation"  # 未升级
