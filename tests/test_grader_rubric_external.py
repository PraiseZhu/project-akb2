"""test_grader_rubric_external.py — step-2 量规外置（标准与代码分离）测试。

覆盖 SC-1..6:
    - SC-3 rubric_change_changes_scoring: 改 YAML weight/threshold → 输出变，改回复原（不改 .py）
    - SC-4 schema_extended / missing_location_rejected / dual_unavailable_suspended
    - SC-6 version_match / primary_canary_real（真机 canary，本机无 gpt-5.4 → skip not-run）

测试不打真 API（除 canary 显式标注）：用 fixture LLMResponse + monkeypatch RUBRIC_DIR
指向 tmp rubric.yaml 验「改文件即改判分」。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.akb2.quality import grader
from src.akb2.quality.llm_router import LLMResponse, RouterConfigError

# ---------------------------------------------------------------------------
# 工具：构造逐维 score 的 grader 响应（全 pass + 逐维 score）
# ---------------------------------------------------------------------------


def _all_pass_with_scores(score: float = 1.0) -> dict:
    """构造全 pass + 每维带 score 的 grader 输出（维名取自 spec 全局 dimensions）。"""
    dims = grader.RUBRIC_DIMENSIONS
    return {
        "dimensions": [
            {"dimension": d, "pass": True, "defects": [], "score": score} for d in dims
        ]
    }


def _mock_route_returns(content: dict | str):
    def _mock(model_family, messages, response_schema=None, **kwargs):
        return LLMResponse(content=content, model_used="gpt-5.4")

    return _mock


def _write_tmp_rubric(tmp_dir: Path, weight: float, threshold: float) -> None:
    """在 tmp_dir 写一份改了全局 weight/threshold 的 rubric.yaml + 配套 10-dimensions.md。"""
    dims = grader.RUBRIC_DIMENSIONS

    def _dim_block(indent: str) -> str:
        return "\n".join(
            f'{indent}- name: "{d}"\n{indent}  weight: {weight}' for d in dims
        )

    rubric = f"""rubric_version: "{grader.REQUIRED_RUBRIC_VERSION}"
dimensions:
{_dim_block("  ")}
integrity_floor:
  - name: "论断锚定率(raw-anchor)"
    hard: true
    evaluated_by: structural
threshold: {threshold}
lanes:
  procedural:
    threshold: {threshold}
    dimensions:
{_dim_block("      ")}
    integrity_floor:
      - name: "论断锚定率(raw-anchor)"
        hard: true
        evaluated_by: structural
"""
    (tmp_dir / "rubric.yaml").write_text(rubric, encoding="utf-8")
    # 配套 md（_load_rubric_text 读它验版本 = REQUIRED_RUBRIC_VERSION + 维名在文本里）
    md = f"# rubric\n#\n# 版本: {grader.REQUIRED_RUBRIC_VERSION}\n\n" + "\n".join(f"## {d}\n判据。" for d in dims)
    (tmp_dir / "10-dimensions.md").write_text(md, encoding="utf-8")


# ---------------------------------------------------------------------------
# SC-3: 改 YAML 即改判分
# ---------------------------------------------------------------------------


class TestRubricChangeChangesScoring:
    def test_rubric_change_changes_scoring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """改 rubric.yaml weight/threshold → grader 输出 weighted_total/threshold 变化，改回复原。"""
        mock_fn = _mock_route_returns(_all_pass_with_scores(score=2.0))

        # 配置 A：weight=1.0, threshold=6.5
        dir_a = tmp_path / "rubric_a"
        dir_a.mkdir()
        _write_tmp_rubric(dir_a, weight=1.0, threshold=6.5)
        monkeypatch.setattr(grader, "RUBRIC_DIR", dir_a)
        result_a = grader.run_grader_with_fixture_detailed("candidate text", mock_fn)
        wt_a = result_a.weighted_total
        th_a = result_a.threshold

        # 配置 B：weight=3.0, threshold=9.0（仅改 YAML，不改 .py）
        dir_b = tmp_path / "rubric_b"
        dir_b.mkdir()
        _write_tmp_rubric(dir_b, weight=3.0, threshold=9.0)
        monkeypatch.setattr(grader, "RUBRIC_DIR", dir_b)
        result_b = grader.run_grader_with_fixture_detailed("candidate text", mock_fn)
        wt_b = result_b.weighted_total
        th_b = result_b.threshold

        # 改 weight → weighted_total 变；改 threshold → threshold 变
        assert wt_a is not None and wt_b is not None
        assert wt_b > wt_a, f"改大 weight 后 weighted_total 应增大: {wt_a} -> {wt_b}"
        assert th_a == 6.5 and th_b == 9.0, "threshold 应随 YAML 变化"

        # 改回配置 A → 复原
        monkeypatch.setattr(grader, "RUBRIC_DIR", dir_a)
        result_a2 = grader.run_grader_with_fixture_detailed("candidate text", mock_fn)
        assert result_a2.weighted_total == wt_a, "改回 YAML 后 weighted_total 复原"
        assert result_a2.threshold == th_a


# ---------------------------------------------------------------------------
# SC-4: schema 扩展 + 缺 location 整批拒 + 双不可用挂起
# ---------------------------------------------------------------------------


class TestSchemaExtended:
    def test_schema_extended(self) -> None:
        """grader schema 含 weighted_total + threshold + integrity_floor（Plan B 已移除逐维 score）。

        Plan B（用户选定）：为修 Azure strict structured output 的 required==properties 约束
        导致的 400，从 schema properties 移除逐维 score（concept weighted 由逐维 pass 派生、
        不读 score）。G3/Step.3 意图保留：score 不 required、不读 score、weighted 由 pass 派生。
        此改是因设计刻意变更（Plan B 移除 score）做的测试维护，非为掩盖回归。
        """
        schema = grader._build_grader_schema()
        props = schema["properties"]
        assert "weighted_total" in props, "schema 应含 weighted_total"
        assert "threshold" in props, "schema 应含 threshold"
        assert "integrity_floor" in props, "schema 应含 integrity_floor 逐项"
        dim_props = props["dimensions"]["items"]["properties"]
        assert "score" not in dim_props, "Plan B: 维度对象已移除 score 字段"


class TestMissingLocationRejected:
    def test_missing_location_rejected(self) -> None:
        """缺 location 的缺陷条目整批拒（FR-006），即便其他字段齐全。"""
        bad = {
            "dimensions": [
                {
                    "dimension": grader.RUBRIC_DIMENSIONS[0],
                    "pass": False,
                    "defects": [{"location": "", "defect": "x", "suggestion": "y"}],
                }
            ]
        }
        defects, ok = grader._parse_grader_output(bad)
        assert ok is False, "缺 location（空串）应整批拒"
        assert defects == []


class TestDualUnavailableSuspended:
    def test_dual_unavailable_suspended(self) -> None:
        """GPT-5.4 + gemini 双不可用 → suspended（FR-008）。"""

        def _both_raise(model_family, messages, response_schema=None, **kwargs):
            raise RouterConfigError(f"{model_family} unavailable")

        result = grader.run_grader_with_fixture_detailed("candidate text", _both_raise)
        assert result.suspended is True, "双模型不可用应挂起"
        assert result.model_used == "none"


# ---------------------------------------------------------------------------
# SC-6: version_match + primary_canary_real
# ---------------------------------------------------------------------------


class TestVersionMatch:
    def test_version_match(self) -> None:
        """REQUIRED_RUBRIC_VERSION 从 spec 读，与 rubric.yaml 内 version 一致（动态自跟随）。"""
        import yaml

        spec = yaml.safe_load(
            (grader.RUBRIC_DIR / "rubric.yaml").read_text(encoding="utf-8")
        )
        assert grader.REQUIRED_RUBRIC_VERSION == spec["rubric_version"]
        # 动态自跟随：不写死版本号，bump 后 REQUIRED 与 spec 自动同步（防下次 bump 又红）
        assert grader.REQUIRED_RUBRIC_VERSION == spec["rubric_version"]
        # _load_rubric_text 断言读 spec version（10-dimensions.md 头部含当前版本，不靠历史残留蒙混）
        text = grader._load_rubric_text()
        assert grader.REQUIRED_RUBRIC_VERSION in text, (
            f"10-dimensions.md 应含当前版本 {grader.REQUIRED_RUBRIC_VERSION}"
        )


class TestPrimaryCanaryReal:
    @pytest.mark.skipif(
        not os.environ.get("AKB2_GRADER_CANARY_LIVE"),
        reason="真机 canary 需 gpt-5.4 gateway + AKB2_GRADER_CANARY_LIVE=1（本机无，step-3 真机门）",
    )
    def test_primary_canary_real(self) -> None:
        """gpt-5.4 canary 实跑真通（不静默降 gemini 假 0 缺陷）。

        真机门：本机无 gpt-5.4 gateway 时 skip（不计 pass，fail-closed 语义）。
        实跑时断言主模型是 gpt-5.4、非 fallback。
        """
        result = grader.run_grader_detailed("# candidate\n\nbody with substance\n")
        assert result.primary_model == grader.PRIMARY_GRADER_MODEL
        assert result.fallback_used is False, "canary 必须主模型真通，不降级 gemini"
        assert result.suspended is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
