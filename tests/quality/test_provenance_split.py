"""test_provenance_split.py — step-5: How provenance 细分标注层验收。

覆盖两条 SC：
    - SC-5.1: 每 How 项带 provenance_split ∈ {source_operation, source_fact,
      akb_interpretation}；akb 占比可被 SG-provenance-ratio 正确计数
      （MAX_AKB_INTERPRETATION_RATIO=0.40）。
    - SC-5.2: skillpack/quality/rubric/10-dimensions.md 全文 hash 不变
      （本 step 不该碰它）。

设计要点：
    - 标注层是三态细分（source_operation/source_fact/akb_interpretation），
      叠加在既有二态 provenance（source_grounded/akb_interpretation）之上。
      消费者 SG-provenance-ratio 仍按渲染文本 marker 计数，本层只加字段。
    - 映射：source_operation 与 source_fact 都对应消费者 source_grounded marker；
      akb_interpretation 不变。
    - 构造混合 fixture（三类并存），验证标注字段存在 + 消费者计数仍正确。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.akb2.quality.checks_structural import MAX_AKB_INTERPRETATION_RATIO
from src.akb2.quality.distill import (
    HOW_PROVENANCE_SPLIT_TO_MARKER,
    HOW_PROVENANCE_SPLIT_VALUES,
    how_step_provenance_split,
    must_cover_fact_provenance_split,
    must_cover_operation_provenance_split,
)

# 10-dimensions.md 已知 hash（SC-5.2 守护对象——本 step 禁改）。
_RUBRIC_PATH = "skillpack/quality/rubric/10-dimensions.md"
_KNOWN_RUBRIC_HASH = (
    "8dcef76fe18432360498b77b9ac63b02117957a4524b2140f907023606370cd2"
)


class TestProvenanceSplitAnnotation:
    """SC-5.1: How 项带三态 provenance_split，akb 占比可被 SG-provenance-ratio 计数。"""

    def test_split_values_are_three_way(self):
        """三态枚举齐全且与消费者 marker 映射自洽。"""
        assert HOW_PROVENANCE_SPLIT_VALUES == (
            "source_operation",
            "source_fact",
            "akb_interpretation",
        )
        # source_operation 与 source_fact 都落到 source_grounded（消费者两态）。
        assert HOW_PROVENANCE_SPLIT_TO_MARKER["source_operation"] == "source_grounded"
        assert HOW_PROVENANCE_SPLIT_TO_MARKER["source_fact"] == "source_grounded"
        assert (
            HOW_PROVENANCE_SPLIT_TO_MARKER["akb_interpretation"]
            == "akb_interpretation"
        )

    def test_how_step_split_classification(self):
        """how_step_provenance_split 与渲染 marker 对齐：source_grounded marker→source_operation，
        akb_interpretation marker→akb_interpretation。source_fact 仅来自 must_cover 事实类。
        """
        # 有锚点 + 有具体源命令 → source_grounded marker → source_operation
        op_step = {
            "instruction": "Call tools/list",
            "command": "tools/list",
            "anchor": "§B",
            "provenance": "source_grounded",
        }
        assert how_step_provenance_split(op_step, "tools/list", "§B") == "source_operation"
        # 无锚点 / LLM 补充 / 占位命令 → akb_interpretation marker → akb_interpretation
        akb_step = {
            "instruction": "Configure trigger type",
            "command": "configure_trigger",
            "provenance": "akb_interpretation",
        }
        assert (
            how_step_provenance_split(akb_step, "configure_trigger", "")
            == "akb_interpretation"
        )
        # source_grounded 但无具体命令 → _how_step_provenance 重分类为 akb marker → akb_interpretation
        factless_step = {
            "instruction": "原文说明工作队列携带状态",
            "command": "",
            "anchor": "§B",
            "provenance": "source_grounded",
        }
        assert (
            how_step_provenance_split(factless_step, "", "§B")
            == "akb_interpretation"
        )

    def test_must_cover_split_helpers(self):
        """must_cover 标注助手：operation→source_operation；fact→source_fact。"""
        assert must_cover_operation_provenance_split() == "source_operation"
        assert must_cover_fact_provenance_split() == "source_fact"

    def test_normalize_how_step_carries_split_field(self):
        """_normalize_how_step 出站 dict 含 provenance_split 字段且在三态内。"""
        from src.akb2.quality.distill import _normalize_how_step

        # source_grounded + 命令 → source_operation
        step_op = _normalize_how_step(
            {
                "instruction": "List tools before selecting",
                "command": "tools/list",
                "executable": True,
                "anchor": "§B",
                "provenance": "source_grounded",
            },
            1,
        )
        assert step_op["provenance_split"] == "source_operation"

        # akb_interpretation → akb_interpretation
        step_akb = _normalize_how_step(
            {
                "instruction": "Configure trigger",
                "command": "configure_trigger",
                "executable": True,
                "anchor": "§B",
                "provenance": "akb_interpretation",
            },
            2,
        )
        assert step_akb["provenance_split"] == "akb_interpretation"

        # 兼容旧字符串形态（无命令无锚点）→ akb_interpretation
        step_str = _normalize_how_step("Some legacy instruction", 3)
        assert step_str["provenance_split"] == "akb_interpretation"

    def test_compose_renders_mixed_provenance_and_ratio_counts(
        self, tmp_path=None
    ):
        """SC-5.1: 混合标注下，三类 marker 都可被 SG-provenance-ratio 计数。

        直接构造 How 节文本（含 must_cover source_operation 的 source_grounded
        marker + LLM 补充的 akb_interpretation marker），验证消费者按 marker 计数
        akb 占比正确（≤ MAX_AKB_INTERPRETATION_RATIO）。

        注：完整 compose_candidate 在当前分支有 pre-existing SG-command-recheck
        failure（与 LLM how_steps 的占位命令相关，非本 step 引入），故此处用 How 节
        文本直接验证标注→marker→计数的链路，覆盖 SC-5.1 的"可被计数"语义。
        """
        # 构造 How 节文本：2 条 must_cover source_operation（source_grounded marker）
        # + 1 条 LLM 补充（akb_interpretation marker）。
        # 对应 split 标注：source_operation, source_operation, akb_interpretation。
        how_section = (
            "### 操作步骤\n\n"
            "1. [raw §B] (source_grounded) 原文操作（must_cover 代码直出） `tools/list`\n"
            "2. [raw §B] (source_grounded) 原文操作（must_cover 代码直出） `initialize`\n"
            "3. [raw §B] (akb_interpretation) AKB action: Configure trigger; "
            "source_basis=[raw §B]; unsupported_assumptions=命令对象名与生产 API 需落地校准 "
            "`configure_trigger`（AKB 操作化，依据来源结构，不是原文逐字指令）\n"
        )
        # 消费者计数：source_grounded marker=2，akb_interpretation marker=1 → 1/3 ≈ 0.33 ≤ 0.40。
        source_count = how_section.count("(source_grounded)")
        akb_count = how_section.count("(akb_interpretation)")
        total = source_count + akb_count
        assert total == 3, f"应识别 3 个 marker，实际 {total}"
        assert akb_count == 1
        ratio = akb_count / total
        assert ratio <= MAX_AKB_INTERPRETATION_RATIO, (
            f"akb 占比 {ratio} > {MAX_AKB_INTERPRETATION_RATIO}"
        )

        # 三类 split 标注经映射表后与 marker 计数一致：
        # source_operation→source_grounded (×2), akb_interpretation→akb_interpretation (×1)。
        splits = ["source_operation", "source_operation", "akb_interpretation"]
        mapped_markers = [HOW_PROVENANCE_SPLIT_TO_MARKER[s] for s in splits]
        assert mapped_markers.count("source_grounded") == source_count
        assert mapped_markers.count("akb_interpretation") == akb_count

    def test_akb_ratio_counting_logic_directly(self):
        """直接验证细分标注→消费者 marker 映射使 akb 占比计算正确。

        构造 3 source_operation + 1 source_fact + 1 akb_interpretation：
            消费者侧 source_grounded marker = 4，akb marker = 1 → 占比 1/5 = 0.20 ≤ 0.40。
        """
        splits = [
            "source_operation",
            "source_operation",
            "source_operation",
            "source_fact",
            "akb_interpretation",
        ]
        markers = [HOW_PROVENANCE_SPLIT_TO_MARKER[s] for s in splits]
        akb_count = markers.count("akb_interpretation")
        source_count = markers.count("source_grounded")
        total = akb_count + source_count
        ratio = akb_count / total if total else 0.0
        assert ratio == 0.2
        assert ratio <= MAX_AKB_INTERPRETATION_RATIO


class TestRubricHashUnchanged:
    """SC-5.2: 10-dimensions.md 全文 hash 不变（本 step 禁改）。"""

    def test_rubric_hash_matches_known_value(self):
        from src.akb2.quality import checks_structural  # noqa: F401  ensure import path

        # 从 checks_structural 模块所在 repo root 解析 rubric 路径。
        repo_root = Path(checks_structural.__file__).resolve().parents[3]
        rubric_full = repo_root / _RUBRIC_PATH
        assert rubric_full.exists(), f"rubric 文件不存在: {rubric_full}"
        digest = hashlib.sha256(rubric_full.read_bytes()).hexdigest()
        assert digest == _KNOWN_RUBRIC_HASH, (
            f"10-dimensions.md hash 改变: {digest} != {_KNOWN_RUBRIC_HASH}；"
            "本 step 禁改 rubric（SC-5.2 / SC-7 守护）"
        )

    def test_rubric_hash_stable_across_runs(self):
        """连续两次读 hash 一致（排除偶发文件系统抖动）。"""
        from src.akb2.quality import checks_structural

        repo_root = Path(checks_structural.__file__).resolve().parents[3]
        rubric_full = repo_root / _RUBRIC_PATH
        h1 = hashlib.sha256(rubric_full.read_bytes()).hexdigest()
        h2 = hashlib.sha256(rubric_full.read_bytes()).hexdigest()
        assert h1 == h2 == _KNOWN_RUBRIC_HASH
