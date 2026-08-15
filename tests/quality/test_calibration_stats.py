"""test_calibration_stats.py — WP-0 度量校正测试 (SC-0)。

覆盖:
    - 去重 funnel: 按 chrome-stripped body content hash（同正文不同 fetched_at → dedup=1）
    - hash 输入排除 frontmatter / TOC / 锚点声明行 / volatile fetched_at / chrome
    - failure_class 全链 taxonomy 非 null（pass→"none"；pipeline 失败按 failed_node 映射）
    - repair_acceptance 与 factory_acceptance 解耦（SC-5 修复门，§0.2/D5 起步 0.30）
    - needs_adjudication 不计 negative 也不计 mechanism_present=true 分母
    - procedural 0 通过 = 硬 fail

mock 边界：合成 report/entry dict，不发网络/不跑 pipeline/不调 LLM。
断言绑可观测行为（dedup=1 / failure_class 非 null / gate 翻转），不是字段形状。
"""

from __future__ import annotations

import os
from typing import Any

from src.akb2.quality.calibration import (
    _classify_failure_class_chain,
    _compute_raw_content_sha256,
    _compute_statistics,
    _default_config,
    _extract_body_for_hash,
    _init_report,
)

# ---------------------------------------------------------------------------
# 辅助：构造 raw_content
# ---------------------------------------------------------------------------

_BODY = "This is the real body content about prompt caching mechanism. It has components."


def _make_raw(fetched_at: str, body: str = _BODY, chrome: str = "") -> str:
    """构造 raw_content（frontmatter + 锚点目录 + 锚点声明行 + 可选 chrome + 正文）。"""
    fm = (
        "---\n"
        "artifact_type: raw\n"
        "source_slug: test-slug\n"
        "source_url: https://example.com/article\n"
        f"fetched_at: {fetched_at}\n"
        "fetched_via: defuddle\n"
        "---\n\n"
    )
    toc = "## 锚点目录\n\n- §A: CONTENT_START\n\n"
    anchor = "§A_CONTENT_START\n\n"
    chrome_block = f"{chrome}\n\n" if chrome else ""
    return f"{fm}{toc}{anchor}{chrome_block}{body}\n"


# ---------------------------------------------------------------------------
# SC-0: 去重 hash — 同正文不同 fetched_at → dedup 为 1
# ---------------------------------------------------------------------------


class TestDedupHash:
    """去重 hash 输入排除 frontmatter/TOC/锚点/fetched_at/chrome。"""

    def test_same_body_different_fetched_at_same_hash(self):
        """同正文、不同 fetched_at → 同 sha256（hash 排除 frontmatter 的 fetched_at）。"""
        h1 = _compute_raw_content_sha256(_make_raw("2026-06-29T10:00:00Z"))
        h2 = _compute_raw_content_sha256(_make_raw("2026-06-30T12:00:00Z"))
        assert h1 is not None and h2 is not None
        assert h1 == h2, "同正文不同 fetched_at 必须 dedup 为 1（hash 排除 fetched_at）"

    def test_different_body_different_hash(self):
        """不同正文 → 不同 sha256。"""
        h1 = _compute_raw_content_sha256(_make_raw("2026-06-29T10:00:00Z", body="body A content"))
        h2 = _compute_raw_content_sha256(_make_raw("2026-06-29T10:00:00Z", body="body B content"))
        assert h1 != h2

    def test_hash_excludes_frontmatter_toc_anchors(self):
        """hash 输入排除 frontmatter / 锚点目录 / 锚点声明行（_extract_body_for_hash 不含它们）。"""
        body = _extract_body_for_hash(_make_raw("2026-06-29T10:00:00Z"))
        # body 不含 frontmatter 字段
        assert "fetched_at" not in body
        assert "artifact_type" not in body
        # body 不含锚点目录/声明行
        assert "## 锚点目录" not in body
        assert "§A_CONTENT_START" not in body
        assert "- §A:" not in body
        # body 含真实正文
        assert "prompt caching" in body

    def test_hash_excludes_chrome(self):
        """同正文 + 导航 chrome → 同 hash（chrome 被 strip_chrome_blocks 剥离）。"""
        h_clean = _compute_raw_content_sha256(_make_raw("2026-06-29T10:00:00Z"))
        h_with_chrome = _compute_raw_content_sha256(
            _make_raw("2026-06-29T10:00:00Z", chrome="Go back to blog")
        )
        assert h_clean == h_with_chrome, "chrome 残留不应改变去重 hash"

    def test_none_raw_returns_none(self):
        """无 raw_content（早退路径）→ None，不计入 deduped_funnel.total。"""
        assert _compute_raw_content_sha256(None) is None
        assert _compute_raw_content_sha256("") is None


class TestDedupFunnelInStatistics:
    """_compute_statistics 产 deduped_funnel，同正文不同 fetched_at 聚为 1。"""

    def test_dedup_funnel_same_body_dedup_to_one(self):
        """3 条同正文不同 fetched_at → unique_bodies=1, duplicate_count=2。"""
        body = _BODY
        h = _compute_raw_content_sha256(_make_raw("2026-06-29T10:00:00Z", body=body))
        assert h is not None
        entries = [
            {
                "index": 0, "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
                "raw_content_sha256": h,
            },
            {
                "index": 1, "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
                "raw_content_sha256": h,  # 同 hash
            },
            {
                "index": 2, "expected": "ingest", "source": "live", "passed": True,
                "failure_segment": None, "repair_rounds": 0,
                "raw_content_sha256": h,  # 同 hash
            },
        ]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        funnel = report["deduped_funnel"]
        assert funnel["total_entries"] == 3
        assert funnel["unique_bodies"] == 1
        assert funnel["duplicate_count"] == 2
        assert len(funnel["duplicate_groups"]) == 1
        assert funnel["duplicate_groups"][0]["count"] == 3

    def test_dedup_funnel_missing_hash_excluded(self):
        """缺 raw_content_sha256 的 entry 不计入 deduped_funnel.total。"""
        entries = [
            {
                "index": 0, "expected": "ingest", "source": "live", "passed": False,
                "failure_segment": "intake_raw", "repair_rounds": 0,
                # 无 raw_content_sha256（早退路径）
            },
        ]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        assert report["deduped_funnel"]["total_entries"] == 0
        assert report["deduped_funnel"]["unique_bodies"] == 0


# ---------------------------------------------------------------------------
# SC-0: failure_class 全链 taxonomy 非 null
# ---------------------------------------------------------------------------


class TestFailureClassChain:
    """全链 failure_class：pass→"none"；pipeline 失败按 failed_node/error_class 映射。"""

    def test_pass_is_none_sentinel(self):
        """pass（正样本通过 / 负样本正确拒收）→ "none"。"""
        assert _classify_failure_class_chain({"passed": True}) == "none"
        # 负样本正确拒收也算 pass
        assert _classify_failure_class_chain(
            {"passed": True, "expected": "reject", "final_status": "rejected_correctly"}
        ) == "none"

    def test_negative_not_rejected(self):
        """负样本未被拒收 → "negative_not_rejected"。"""
        assert _classify_failure_class_chain(
            {"passed": False, "expected": "reject", "final_status": "proposal_passed"}
        ) == "negative_not_rejected"

    def test_intake_raw_failure_kept(self):
        """intake_raw 段已有具体 failure_class（dead_link/metadata_failure/extraction_failure）保留。"""
        for fc in ("dead_link", "metadata_failure", "extraction_failure"):
            assert _classify_failure_class_chain(
                {"passed": False, "failure_class": fc, "final_status": "fetch_failed"}
            ) == fc

    def test_admission_reject(self):
        """admission 拒收（novelty/how_tendency/no_mechanism_signal）→ "admission_reject"。"""
        assert _classify_failure_class_chain(
            {"passed": False, "failed_node": "admission", "error_class": "novelty",
             "final_status": "rejected"}
        ) == "admission_reject"
        assert _classify_failure_class_chain(
            {"passed": False, "failed_node": "admission", "error_class": "no_mechanism_signal",
             "final_status": "rejected"}
        ) == "admission_reject"

    def test_poisoned_trace_entailment(self):
        """D7 poisoned_trace_entailment → "poisoned_trace_entailment"。"""
        assert _classify_failure_class_chain(
            {"passed": False, "error_class": "poisoned_trace_entailment",
             "final_status": "failed"}
        ) == "poisoned_trace_entailment"

    def test_repair_no_converge(self):
        """distill_no_converge → "repair_no_converge"。"""
        assert _classify_failure_class_chain(
            {"passed": False, "error_class": "loop1/repair_loop/distill_no_converge",
             "final_status": "quarantined"}
        ) == "repair_no_converge"

    def test_integrity_floor_failed(self):
        """integrity_floor 拒 → "integrity_floor_failed"。"""
        assert _classify_failure_class_chain(
            {"passed": False, "failed_node": "integrity_floor",
             "error_class": "integrity_floor_failed", "final_status": "rejected"}
        ) == "integrity_floor_failed"

    def test_distill_extract_failed(self):
        assert _classify_failure_class_chain(
            {"passed": False, "failed_node": "distill_extract", "final_status": "failed"}
        ) == "distill_extract_failed"

    def test_grader_unavailable_vs_failed(self):
        """grader 环境不可用单列 grader_unavailable；其余 grader 失败 → grader_failed。"""
        assert _classify_failure_class_chain(
            {"passed": False, "failed_node": "grader",
             "error_class": "grader_fallback_not_allowed", "final_status": "failed"}
        ) == "grader_unavailable"
        assert _classify_failure_class_chain(
            {"passed": False, "failed_node": "grader",
             "error_class": "grader_defects", "final_status": "failed"}
        ) == "grader_failed"

    def test_all_entries_non_null_after_statistics(self):
        """_compute_statistics 后每条 entry 的 failure_class 非 null。"""
        entries = [
            {"index": 0, "expected": "ingest", "passed": True, "source": "live",
             "failure_segment": None, "repair_rounds": 0},  # → none
            {"index": 1, "expected": "ingest", "passed": False, "source": "live",
             "failure_segment": "repair_loop", "repair_rounds": 3,
             "failed_node": "repair", "error_class": "loop1/repair_loop/distill_no_converge",
             "final_status": "quarantined"},  # → repair_no_converge
            {"index": 2, "expected": "reject", "passed": True, "source": "fixture",
             "failure_segment": None, "repair_rounds": 0},  # → none
        ]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        for e in report["entries"]:
            assert e["failure_class"] is not None, f"entry {e['index']} failure_class 仍为 null"
        assert report["entries"][0]["failure_class"] == "none"
        assert report["entries"][1]["failure_class"] == "repair_no_converge"
        assert report["entries"][2]["failure_class"] == "none"


# ---------------------------------------------------------------------------
# SC-0/SC-5: repair_acceptance 与 factory_acceptance 解耦
# ---------------------------------------------------------------------------


def _mp_entry(idx: int, passed: bool, source: str = "live",
              lane: str = "concept") -> dict[str, Any]:
    """构造 mechanism_present=true 正样本 entry。"""
    return {
        "index": idx, "expected": "ingest", "passed": passed, "source": source,
        "failure_segment": None if passed else "repair_loop", "repair_rounds": 0,
        "mechanism_present": True, "review_status": "confirmed",
        "content_lane": lane,
    }


def _neg_entry(idx: int, rejected: bool = True) -> dict[str, Any]:
    """构造 no_mechanism 负样本 entry。"""
    return {
        "index": idx, "expected": "reject", "passed": rejected, "source": "fixture",
        "failure_segment": None, "repair_rounds": 0,
        "mechanism_present": False, "reject_reason": "no_mechanism",
        "review_status": "confirmed",
    }


# ---------------------------------------------------------------------------
# sc-p2-unify-mode-judgment: 运行模式判定唯一权威
# ---------------------------------------------------------------------------


class TestUnifyModeJudgment:
    """calibration 的 promotion_mode 必须与 smoke_validator 权威函数同源。

    - calibration 不得再独立解释 AKB2_AUTO_PROMOTE / AKB2_AMENDMENT_APPLIED；
    - AKB2_AMENDMENT_APPLIED 是修宪留痕标记，只透传为 amendment_status，
      不得驱动运行时模式（AUTO_PROMOTE=1 + AMENDMENT=0 也必须是 active，
      与 promote.py / runner.py 实际执行一致——旧代码在此组合回退 proposal，
      造成 calibration 与 runtime 对同一 env 各说各话）。
    """

    def test_mode_matches_authoritative_function_all_env_combos(self, monkeypatch):
        """四组 env 组合下 _init_report 的 promotion_mode == _launchd_mode_from_env。"""
        from src.akb2.quality.smoke_validator import _launchd_mode_from_env

        for auto, amend in [("0", "0"), ("0", "1"), ("1", "0"), ("1", "1")]:
            monkeypatch.setenv("AKB2_AUTO_PROMOTE", auto)
            monkeypatch.setenv("AKB2_AMENDMENT_APPLIED", amend)
            report = _init_report(0, 0)
            expected = _launchd_mode_from_env(dict(os.environ))
            assert report["promotion_mode"] == expected, (
                f"AUTO_PROMOTE={auto} AMENDMENT={amend}: "
                f"calibration={report['promotion_mode']} != authoritative={expected}"
            )

    def test_amendment_marker_does_not_drive_mode(self, monkeypatch):
        """AMENDMENT 标记不驱动模式：AUTO_PROMOTE=1 + AMENDMENT=0 → active（非回退 proposal）。"""
        monkeypatch.setenv("AKB2_AUTO_PROMOTE", "1")
        monkeypatch.setenv("AKB2_AMENDMENT_APPLIED", "0")
        report = _init_report(0, 0)
        assert report["promotion_mode"] == "active", (
            "amendment 标记不得被当成运行时模式：AUTO_PROMOTE=1 即 active"
        )
        assert report["amendment_status"] == "not_applied", (
            "amendment_status 只是留痕透传（env=0 → not_applied）"
        )

    def test_amendment_status_passthrough_only(self, monkeypatch):
        """amendment_status 纯透传 env：AUTO_PROMOTE=1 + AMENDMENT=1 → active/applied。"""
        monkeypatch.setenv("AKB2_AUTO_PROMOTE", "1")
        monkeypatch.setenv("AKB2_AMENDMENT_APPLIED", "1")
        report = _init_report(0, 0)
        assert report["promotion_mode"] == "active"
        assert report["amendment_status"] == "applied"

    def test_no_second_env_interpretation_in_source(self):
        """calibration.py 源码不再含第二套 env→模式分支（AST 级锁）。"""
        import ast
        from pathlib import Path

        src_path = (
            Path(__file__).resolve().parents[2]
            / "src" / "akb2" / "quality" / "calibration.py"
        )
        tree = ast.parse(src_path.read_text(encoding="utf-8"))

        # 收集 _init_report / _default_config 函数体内的 os.environ 比较表达式
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name == "_init_report"):
                continue
            for sub in ast.walk(node):
                # 禁止 Compare 里直接出现 "AKB2_AUTO_PROMOTE" 字面量
                if isinstance(sub, ast.Compare):
                    for cmp_node in ast.walk(sub):
                        if (
                            isinstance(cmp_node, ast.Constant)
                            and cmp_node.value == "AKB2_AUTO_PROMOTE"
                        ):
                            raise AssertionError(
                                "_init_report 内不得再出现 AKB2_AUTO_PROMOTE 字面量比较"
                                "（第二套模式解释）；模式判定应来自 smoke_validator 权威函数"
                            )

    def test_default_config_auto_promote_same_authority(self, monkeypatch):
        """_default_config 的 auto_promote 布尔与权威函数同源（active ⇔ True）。"""
        from src.akb2.quality.smoke_validator import _launchd_mode_from_env

        for auto, amend in [("0", "0"), ("0", "1"), ("1", "0"), ("1", "1")]:
            monkeypatch.setenv("AKB2_AUTO_PROMOTE", auto)
            monkeypatch.setenv("AKB2_AMENDMENT_APPLIED", amend)
            config = _default_config()
            expected_active = _launchd_mode_from_env(dict(os.environ)) == "active"
            assert config["auto_promote"] == expected_active, (
                f"AUTO_PROMOTE={auto} AMENDMENT={amend}: "
                f"auto_promote={config['auto_promote']} != authoritative active={expected_active}"
            )

    def test_default_config_promote_off_default(self, monkeypatch):
        """默认 env（无 AUTO_PROMOTE）→ auto_promote=False（proposal 回放不变）。"""
        monkeypatch.delenv("AKB2_AUTO_PROMOTE", raising=False)
        monkeypatch.delenv("AKB2_AMENDMENT_APPLIED", raising=False)
        config = _default_config()
        assert config["auto_promote"] is False
        report = _init_report(0, 0)
        assert report["promotion_mode"] == "proposal"
        assert report["amendment_status"] == "not_applied"





class TestRepairAcceptance:
    """repair_acceptance: mechanism_present=true 子集 + 分层 + procedural 硬 fail。"""

    def test_decoupled_from_factory_acceptance(self):
        """repair_acceptance 独立计算；factory_acceptance 不被 mechanism_present 影响。

        合成 5 条 mp=true 正样本全过 + 1 负样本拒 → repair_acceptance_gate=True，
        但 factory_acceptance 仍由 run_calibration 的 0.9 混池门判定（此处手工设 False
        以证明二者解耦：repair gate 不替 factory 背书）。
        """
        entries = [_mp_entry(i, True) for i in range(5)] + [_neg_entry(5)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        assert ra["positive_pass_rate"] == 1.0
        assert ra["negative_reject_rate"] == 1.0
        assert ra["repair_acceptance_gate"] is True
        # factory_acceptance 不由 _compute_statistics 设（在 run_calibration :831 设），
        # 此处证明二者解耦：_compute_statistics 不染指 factory_acceptance
        assert "factory_acceptance" not in report, (
            "_compute_statistics 不应设 factory_acceptance（repair_acceptance 与之解耦）"
        )
        # repair_acceptance 字段独立存在且含 AUTO_PROMOTE
        assert "AUTO_PROMOTE" in ra

    def test_mp_true_subset_only(self):
        """positive_pass_rate 只数 mechanism_present=true+ingest；mp=false/null 不入分母。"""
        entries = [
            _mp_entry(0, True),   # mp=true pass
            _mp_entry(1, False),  # mp=true fail
            {**_neg_entry(2, False), "expected": "ingest", "mechanism_present": False},  # mp=false 不入分母
            {**_mp_entry(3, True), "mechanism_present": None, "review_status": "needs_adjudication"},  # null 不入分母
        ]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        # mp_true = 2 条 (idx 0,1), pass=1 → 0.5
        assert ra["mechanism_present_true_total"] == 2
        assert ra["mechanism_present_true_passed"] == 1
        assert ra["positive_pass_rate"] == 0.5

    def test_gate_passes_at_030_threshold(self):
        """§0.2/D5：quality-repair 起步线 0.30——mp_true 10 条 3 过(0.30) + 负样本全拒 → gate=True。"""
        from src.akb2.quality.calibration import QUALITY_REPAIR_ACCEPTANCE_FLOOR

        assert QUALITY_REPAIR_ACCEPTANCE_FLOOR == 0.30
        entries = [
            _mp_entry(0, True), _mp_entry(1, True), _mp_entry(2, True),
            _mp_entry(3, False), _mp_entry(4, False), _mp_entry(5, False),
            _mp_entry(6, False), _mp_entry(7, False), _mp_entry(8, False),
            _mp_entry(9, False),
        ] + [_neg_entry(10)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        assert ra["positive_pass_rate"] == 0.3
        assert ra["repair_acceptance_gate"] is True

    def test_gate_fails_below_030(self):
        """§0.2/D5：mp_true 5 条 1 过(0.2) → gate=False（未达 0.30 起步线）。"""
        entries = [
            _mp_entry(0, True),
            _mp_entry(1, False), _mp_entry(2, False), _mp_entry(3, False), _mp_entry(4, False),
        ] + [_neg_entry(5)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        assert ra["positive_pass_rate"] == 0.2
        assert ra["repair_acceptance_gate"] is False

    def test_gate_fails_if_negative_not_rejected(self):
        """负样本未拒（negative_reject_rate<1.0）→ gate=False。"""
        entries = [_mp_entry(0, True) for _ in range(3)] + [_neg_entry(3, rejected=False)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        assert ra["negative_reject_rate"] == 0.0
        assert ra["repair_acceptance_gate"] is False

    def test_procedural_zero_pass_hard_fail(self):
        """procedural 子集 >0 且 0 通过 → procedural_zero_pass_hard_fail=True, gate=False。"""
        entries = [
            _mp_entry(0, True, lane="concept"),
            _mp_entry(1, False, lane="procedural"),
            _mp_entry(2, False, lane="procedural"),
        ] + [_neg_entry(3)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        assert ra["layers"]["procedural"]["total"] == 2
        assert ra["layers"]["procedural"]["passed"] == 0
        assert ra["procedural_zero_pass_hard_fail"] is True
        assert ra["repair_acceptance_gate"] is False

    def test_procedural_no_entries_no_hard_fail(self):
        """无 procedural entry（全 concept）→ procedural_zero_pass_hard_fail=False（不硬 fail）。"""
        entries = [_mp_entry(0, True, lane="concept")] * 3 + [_neg_entry(3)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        assert ra["layers"]["procedural"]["total"] == 0
        assert ra["procedural_zero_pass_hard_fail"] is False

    def test_needs_adjudication_not_counted_as_negative(self):
        """needs_adjudication 条目（mechanism_present=null）不计入 negative 也不入 mp_true 分母。"""
        entries = [
            _mp_entry(0, True),
            {
                "index": 1, "expected": "ingest", "passed": False, "source": "live",
                "failure_segment": "repair_loop", "repair_rounds": 3,
                "mechanism_present": None, "review_status": "needs_adjudication",
                "content_lane": "concept",
            },
        ] + [_neg_entry(2)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        ra = report["repair_acceptance"]
        # needs_adjudication 不入 mp_true 分母
        assert ra["mechanism_present_true_total"] == 1
        # needs_adjudication 不入 negative
        assert ra["negative_total"] == 1
        # needs_adjudication 计数 + 索引记录
        assert ra["needs_adjudication_count"] == 1
        assert 1 in ra["needs_adjudication_indices"]

    def test_layers_live_fixture_concept_procedural(self):
        """分层报告含 live/fixture/concept/procedural 四层 pass_rate。"""
        entries = [
            _mp_entry(0, True, source="live", lane="concept"),
            _mp_entry(1, False, source="live", lane="procedural"),
            _mp_entry(2, True, source="fixture", lane="concept"),
        ] + [_neg_entry(3)]
        report: dict[str, Any] = {"entries": entries}
        _compute_statistics(report)
        layers = report["repair_acceptance"]["layers"]
        assert set(layers.keys()) == {"live", "fixture", "concept", "procedural"}
        assert layers["live"]["total"] == 2
        assert layers["fixture"]["total"] == 1
        assert layers["concept"]["total"] == 2
        assert layers["concept"]["passed"] == 2
        assert layers["procedural"]["total"] == 1

    def test_auto_promote_recorded_zero(self):
        """repair_acceptance 记录 AUTO_PROMOTE 环境值（全程 0）。"""
        import os
        assert report_auto_promote() == os.environ.get("AKB2_AUTO_PROMOTE", "0")


def report_auto_promote() -> str:
    """读 _compute_repair_acceptance 在空 entries 下的 AUTO_PROMOTE 字段。"""
    report: dict[str, Any] = {"entries": []}
    _compute_statistics(report)
    return report["repair_acceptance"]["AUTO_PROMOTE"]


# ---------------------------------------------------------------------------
# §0.2/D5：CLI --acceptance-mode exit 切换（factory vs quality-repair）
# ---------------------------------------------------------------------------


class TestAcceptanceModeExit:
    """§0.2/D5：CLI exit 口径可切换——factory（出厂混池 0.9）vs quality-repair（0.30）。

    factory_acceptance 与 repair_acceptance_gate 解耦：30% pass 时 factory mode exit 1、
    quality-repair mode exit 0。验收只读对应口径，避免 30% 仍 exit 1 失真。
    """

    def test_quality_repair_mode_exits_zero_when_repair_gate_true(self, monkeypatch):
        """repair_acceptance_gate=True + factory_acceptance=False → quality-repair mode exit 0。"""
        from src.akb2.quality import calibration as calib

        def _fake_run(**kwargs):
            return {
                "factory_acceptance": False,  # 出厂混池 0.9 未达
                "repair_acceptance": {"repair_acceptance_gate": True},  # 本轮 0.30 达
            }

        monkeypatch.setattr(calib, "run_calibration", _fake_run)
        rc = calib.main(["--corpus", "x.yaml", "--report-out", "/tmp/_t.json", "--acceptance-mode", "quality-repair"])
        assert rc == 0, "quality-repair mode 应跟 repair_acceptance_gate（True → exit 0）"

    def test_factory_mode_exits_one_when_factory_acceptance_false(self, monkeypatch):
        """同 report（factory=False, repair_gate=True）→ factory mode exit 1（默认口径）。"""
        from src.akb2.quality import calibration as calib

        def _fake_run(**kwargs):
            return {
                "factory_acceptance": False,
                "repair_acceptance": {"repair_acceptance_gate": True},
            }

        monkeypatch.setattr(calib, "run_calibration", _fake_run)
        rc = calib.main(["--corpus", "x.yaml", "--report-out", "/tmp/_t.json"])  # 默认 factory mode
        assert rc == 1, "factory mode 应跟 factory_acceptance（False → exit 1）"

    def test_quality_repair_mode_exits_one_when_repair_gate_false(self, monkeypatch):
        """repair_acceptance_gate=False → quality-repair mode exit 1（即便 factory True）。"""
        from src.akb2.quality import calibration as calib

        def _fake_run(**kwargs):
            return {
                "factory_acceptance": True,
                "repair_acceptance": {"repair_acceptance_gate": False},
            }

        monkeypatch.setattr(calib, "run_calibration", _fake_run)
        rc = calib.main(["--corpus", "x.yaml", "--report-out", "/tmp/_t.json", "--acceptance-mode", "quality-repair"])
        assert rc == 1, "quality-repair mode gate=False → exit 1"


# ---------------------------------------------------------------------------
# §0.2/D4：promotion_outcome 三分类 + §0.2/D12：repair_quality_funnel counters
# ---------------------------------------------------------------------------


class TestD4PromotionOutcome:
    """§0.2/D4：promotion_outcome 三分类（active_promoted/proposal_passed/promotion_blocked）。

    calibration 只计 active_promoted + proposal_passed 为 positive pass；
    promotion_blocked 不计（防 proposal_passed 虚高）。
    """

    def _entry(self, outcome, expected="ingest", mechanism_present=True):
        return {
            "index": 0, "expected": expected, "passed": outcome in ("active_promoted", "proposal_passed"),
            "promotion_outcome": outcome,
            "final_status": "promoted" if outcome == "active_promoted" else (
                "proposal_passed" if outcome == "proposal_passed" else "failed"
            ),
            "failure_segment": None if outcome in ("active_promoted", "proposal_passed") else "promotion_blocked",
            "audit": {"mechanism_present": mechanism_present},
        }

    def test_active_promoted_counts_as_pass(self):
        e = self._entry("active_promoted")
        report: dict[str, Any] = {"entries": [e] + [_neg_entry(1)]}
        _compute_statistics(report)
        assert report["entries"][0]["passed"] is True

    def test_proposal_passed_counts_as_pass(self):
        e = self._entry("proposal_passed")
        report: dict[str, Any] = {"entries": [e] + [_neg_entry(1)]}
        _compute_statistics(report)
        assert report["entries"][0]["passed"] is True

    def test_promotion_blocked_not_counted_as_pass(self):
        """promotion_blocked 不计 positive pass（防虚高）。"""
        e = self._entry("promotion_blocked")
        # promotion_blocked 的 final_status="failed"，expected=ingest → passed=False
        report: dict[str, Any] = {"entries": [e] + [_neg_entry(1)]}
        _compute_statistics(report)
        assert report["entries"][0]["passed"] is False
        assert report["entries"][0].get("failure_segment") == "promotion_blocked"


class TestD12RepairQualityFunnel:
    """§0.2/D12：repair_quality_funnel counters（旧早死模式修复后应全 0）。"""

    def _mech_entry(self, *, error_class="", failed_node="", error="", passed=True):
        return {
            "index": 0, "expected": "ingest", "passed": passed,
            "error_class": error_class, "failed_node": failed_node,
            "failure_segment": None if passed else failed_node,
            "error": error,
            "mechanism_present": True,
            "final_status": "promoted" if passed else "failed",
        }

    def test_funnel_all_zero_when_no_old_early_death(self):
        """修复后正常路径 → 旧早死 counters 全 0。"""
        e = self._mech_entry(passed=True)
        report: dict[str, Any] = {"entries": [e]}
        _compute_statistics(report)
        funnel = report["repair_quality_funnel"]
        assert funnel["old_no_mechanism_signal_on_h2h3_count"] == 0
        assert funnel["distill_f4_precompose_death_count"] == 0
        assert funnel["a2_related_only_cold_start_death_count"] == 0
        assert funnel["hidden_mechanism_edges_count"] == 0

    def test_funnel_scoped_to_mechanism_present_ingest(self):
        """scope: mechanism_present=true / expected=ingest（负样本/非机制不入 scope）。"""
        e_mech = self._mech_entry(passed=True)
        e_neg = {"index": 1, "expected": "reject", "passed": True, "final_status": "rejected_correctly",
                 "mechanism_present": False,}
        e_nonmech = self._mech_entry(passed=False)
        e_nonmech["mechanism_present"] = False
        report: dict[str, Any] = {"entries": [e_mech, e_neg, e_nonmech]}
        _compute_statistics(report)
        funnel = report["repair_quality_funnel"]
        # 只 mechanism_present=true + expected=ingest 入 scope（e_mech 一条）
        assert funnel["scoped_total"] == 1

    def test_funnel_detects_old_no_mechanism_on_h2h3(self):
        """admission no_mechanism + mechanism_present=true → old_no_mechanism counter。"""
        e = self._mech_entry(
            passed=False, error_class="no_mechanism_signal", failed_node="admission"
        )
        report: dict[str, Any] = {"entries": [e]}
        _compute_statistics(report)
        funnel = report["repair_quality_funnel"]
        assert funnel["old_no_mechanism_signal_on_h2h3_count"] == 1

    def test_funnel_top_failure_class_share(self):
        """top_failure_class_share <= 1.0（单 bucket 占 100% 时 = 1.0）。"""
        e = self._mech_entry(passed=False, failed_node="admission", error_class="no_mechanism_signal")
        report: dict[str, Any] = {"entries": [e]}
        _compute_statistics(report)
        funnel = report["repair_quality_funnel"]
        assert 0.0 <= funnel["top_failure_class_share"] <= 1.0
        assert isinstance(funnel["failure_class_distribution"], dict)

    def test_f3_hidden_mechanism_edges_detected_when_edge_not_rendered(self):
        """§0.2/F3：extract 有 eligible edge 但正文未渲染机制表 → hidden_mechanism_edges>0。"""
        e = self._mech_entry(passed=True)
        e["mechanism_rendering"] = {
            "mechanism_section_rendered": False,  # 正文缺机制表
            "rendered_edge_count": 0,
            "extract_source_causal_edge_count": 2,  # extract 有 2 条 eligible edge
            "hidden": True,
        }
        report: dict[str, Any] = {"entries": [e]}
        _compute_statistics(report)
        funnel = report["repair_quality_funnel"]
        assert funnel["hidden_mechanism_edges_count"] == 1, (
            "extract 有 edge 但正文未渲染 → hidden>0（F3 真算，非永远 0）"
        )

    def test_f3_hidden_zero_when_edge_rendered(self):
        """§0.2/F3：extract 有 edge 且正文渲染了机制表 → hidden=0。"""
        e = self._mech_entry(passed=True)
        e["mechanism_rendering"] = {
            "mechanism_section_rendered": True,
            "rendered_edge_count": 2,
            "extract_source_causal_edge_count": 2,
            "hidden": False,
        }
        report: dict[str, Any] = {"entries": [e]}
        _compute_statistics(report)
        funnel = report["repair_quality_funnel"]
        assert funnel["hidden_mechanism_edges_count"] == 0
