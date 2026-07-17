"""test_f9a_normalize_unified.py — F9-A 归一化单入口不变式测试。

probe-p7:ag2 primary 抽取失败走 fallback,但 fallback 跳过归一化 → H3 残留。
F9-A:建 _normalize_extract_payload 单入口,LLM 成功路径 + fallback 都过。

不变式(两路径产物都满足):
    - 组件无黑名单形态(祈使句/泛节名)
    - 有 evidence 的组件句子逐字在 raw
    - 边端点 ∈ 组件表 name 集合且命名 canonical(与组件表 name 一致)
    - 无编造命令(how_steps command 在 raw 或为空)
ag2 fallback 离线验证:组件 evidence 回填非 0。
"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.distill import (
    _is_garbage_component_name,
    _normalize_extract_payload,
    build_deterministic_extract_result,
)
from src.akb2.quality.raw_anchors import declared_anchor_set

_WT = Path(__file__).resolve().parents[2]
_AG2_RAW_PATH = (
    _WT / "reports/akb2-quality-repair/_tmp/raw-store/"
    "2fb4f618c181edf17f8ebbf7f51ef41b1fe4bb73c3525eb857aeb68832359894.md"
)


def _assert_invariants(data: dict, raw: str, *, is_fallback: bool) -> None:
    """两路径产物都须满足的不变式。"""
    decl = declared_anchor_set(raw)
    # 1. 组件无黑名单形态
    comps = data.get("mechanism_components", []) or []
    for c in comps:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if name:
            assert not _is_garbage_component_name(name), (
                f"组件 {name!r} 命中黑名单(应被滤)"
            )
    # 2. 有 evidence 的组件句子逐字在 raw + 锚∈声明集
    for c in comps:
        if not isinstance(c, dict):
            continue
        es = str(c.get("evidence_sentence", "")).strip()
        ea = str(c.get("evidence_anchor", "")).strip()
        if es:
            assert es in raw, f"组件 evidence_sentence 非逐字在 raw: {es[:50]!r}"
        if ea:
            assert ea in decl, f"组件 evidence_anchor {ea} 不在声明集"
    # 3. 边端点 ∈ 组件表 name 集合且 canonical
    comp_names = {
        str(c.get("name", "")).strip()
        for c in comps
        if isinstance(c, dict) and str(c.get("name", "")).strip()
    }
    for ek in ("causal_edges", "graph_edges"):
        for e in data.get(ek, []) or []:
            if not isinstance(e, dict):
                continue
            for ep_field in ("from", "to"):
                ep = str(e.get(ep_field, "")).strip()
                if ep:
                    assert ep in comp_names, (
                        f"edge.{ep_field}={ep!r} 不在组件表 name 集合(命名不归一)"
                    )
            # edge evidence_anchor ∈ 声明集(若有)
            ea = str(e.get("evidence_anchor", "")).strip()
            if ea:
                assert ea in decl, f"edge evidence_anchor {ea} 不在声明集"
    # 4. 无编造命令(how_steps command 在 raw 或为空)
    # fallback 跳过 F3-B(is_fallback=True),其 how_steps 命令由 render-time 处理,
    # 此不变式只对 LLM 路径(is_fallback=False)严格校验。
    if not is_fallback:
        for step in data.get("how_steps", []) or []:
            if not isinstance(step, dict):
                continue
            cmd = str(step.get("command", "") or "").strip()
            if cmd:
                # 命令字面量须在 raw(剥反引号后)或为 reserved
                assert cmd in raw or _is_reserved_or_in_raw(cmd, raw), (
                    f"how_step command {cmd!r} 不在 raw(编造)"
                )


def _is_reserved_or_in_raw(cmd: str, raw: str) -> bool:
    """简化:命令字面量在 raw 中出现即算接地(F3-B 同口径的简化版)。"""
    return cmd in raw


class TestF9AUnifiedEntryExists:
    def test_normalize_payload_signature(self):
        """_normalize_extract_payload 存在且接受 is_fallback kw-only 参数。"""
        import inspect

        sig = inspect.signature(_normalize_extract_payload)
        params = sig.parameters
        assert "is_fallback" in params
        assert params["is_fallback"].kind == inspect.Parameter.KEYWORD_ONLY


class TestF9ALLMPathInvariants:
    """LLM 成功路径(is_fallback=False)产物不变式。"""

    def _llm_data(self, raw: str) -> dict:
        """模拟 LLM 产:含垃圾组件 + 小写边端点 + 空 evidence + 编造命令。"""
        return {
            "claims": [
                {"text": "The Captain agent holds the goal.", "anchor": "§A",
                 "provenance": "source_grounded", "evidence_gap": "未量化"},
            ],
            "argumentation": [],
            "mechanism_components": [
                {"name": "Captain", "role": "coord"},
                {"name": "Executor Pool", "role": "exec"},
                {"name": "Navigate to the directory to find config", "role": "garbage"},
                {"name": "Crew Attributes", "role": "garbage"},
            ],
            "causal_edges": [
                # lowercase 端点 → 须归一为 canonical
                {"from": "captain", "to": "executor pool", "relation": "dispatches",
                 "edge_kind": "source_causal", "evidence": "Captain dispatches",
                 "evidence_anchor": "§A"},
            ],
            "graph_edges": [],
            "how_steps": [
                {"instruction": "Run the captain", "command": "make deploy",
                 "executable": True, "anchor": "§A", "how_level": "H1",
                 "provenance": "akb_interpretation"},
                {"instruction": "Fake cmd", "command": "FAKE_CMD_xyz_not_in_raw",
                 "executable": True, "anchor": "§A", "how_level": "H1",
                 "provenance": "akb_interpretation"},
            ],
            "tldr": "The Captain agent holds the goal.",
            "params": {},
            "referenced_concepts": [],
            "applicability_boundary": "",
        }

    def test_llm_path_invariants_hold(self):
        raw = (
            "§A_CONTENT_START\n\n"
            "The Captain agent holds the goal. make deploy is the command.\n"
            "The Executor Pool dispatches tasks.\n"
        )
        data = self._llm_data(raw)
        diag = _normalize_extract_payload(data, raw, "concept", is_fallback=False)
        # 垃圾组件被滤(2 个)
        assert diag["components_filtered"] >= 2
        # 组件 evidence 回填(Captain/Executor Pool)
        assert diag["components_evidence_backfilled"] >= 1
        _assert_invariants(data, raw, is_fallback=False)
        # 编造命令被剥(FAKE_CMD 不在 raw → command 空)
        cmds = [s.get("command", "") for s in data["how_steps"]]
        assert "FAKE_CMD_xyz_not_in_raw" not in cmds

    def test_llm_path_edge_endpoints_canonical(self):
        """LLM 路径边端点 lowercase → canonical(与组件表 name 一致)。"""
        raw = (
            "§A_CONTENT_START\n\n"
            "The Captain agent holds the goal. The Executor Pool dispatches.\n"
        )
        data = self._llm_data(raw)
        _normalize_extract_payload(data, raw, "concept", is_fallback=False)
        comp_names = {c["name"] for c in data["mechanism_components"]}
        for e in data["causal_edges"]:
            assert e["from"] in comp_names
            assert e["to"] in comp_names
        # lowercase 'captain'/'executor pool' 已改写为 canonical
        assert data["causal_edges"][0]["from"] == "Captain"
        assert data["causal_edges"][0]["to"] == "Executor Pool"


class TestF9AFallbackPathInvariants:
    """deterministic fallback 路径(is_fallback=True)产物不变式 + ag2 backfill 非 0。"""

    def test_fallback_path_invariants_hold(self):
        raw = (
            "§A_CONTENT_START\n\n"
            "# Captain Agent Architecture\n\n"
            "## 1. Captain\n\nThe Captain dispatches tasks to the Executor Pool.\n\n"
            "## 2. Executor Pool\n\nThe Executor Pool feeds results to the Task Graph.\n\n"
            "## 3. Task Graph\n\nThe Task Graph tracks state.\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success, result.error
        _assert_invariants(result.data, raw, is_fallback=True)
        # fallback 组件 evidence 回填非 0(F9-A 核心修复)
        assert result.diagnostics.get("components_evidence_backfilled", 0) > 0, (
            "fallback 路径组件 evidence 回填应为非 0(F9-A 核心修复)"
        )

    def test_ag2_fallback_backfill_nonzero(self):
        """ag2 raw 离线走 fallback:组件 evidence 回填非 0(probe-p7 缺口验证)。"""
        if not _AG2_RAW_PATH.exists():
            import pytest
            pytest.skip("ag2 raw fixture 不存在")
        raw = _AG2_RAW_PATH.read_text()
        result = build_deterministic_extract_result(raw, content_lane="concept")
        # ag2 fallback 应成功(≥3 组件 + eligible 边)
        assert result.success, f"ag2 fallback 应成功: {result.error}"
        # 组件 evidence 回填非 0(probe-p7 旧版=0,F9-A 修复后>0)
        assert result.diagnostics.get("components_evidence_backfilled", 0) > 0
        # 不变式
        _assert_invariants(result.data, raw, is_fallback=True)

    def test_fallback_no_garbage_components(self):
        """fallback 路径组件无黑名单形态(F8-C 在 fallback 也生效)。"""
        raw = (
            "§A_CONTENT_START\n\n"
            "## 1. Captain\n\nThe Captain dispatches to the Executor Pool.\n"
            "## 2. Executor Pool\n\nThe pool feeds the Task Graph.\n"
            "## 3. Task Graph\n\nThe graph tracks state.\n"
        )
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success, result.error
        for c in result.data.get("mechanism_components", []):
            assert not _is_garbage_component_name(c.get("name", ""))


class TestF9ABothPathsSameChokePoint:
    """两路径都过 _normalize_extract_payload(同一入口)。"""

    def test_both_paths_produce_canonical_edges(self):
        """同一 raw,两路径产物边端点都 ∈ 组件表 name(canonical)。"""
        raw = (
            "§A_CONTENT_START\n\n"
            "## 1. Captain\n\nThe Captain dispatches to the Executor Pool.\n"
            "## 2. Executor Pool\n\nThe pool feeds the Task Graph.\n"
            "## 3. Task Graph\n\nThe graph tracks state.\n"
        )
        # fallback 路径
        fb = build_deterministic_extract_result(raw, content_lane="concept")
        assert fb.success
        # LLM 路径(模拟)
        llm_data = {
            "claims": [{"text": "Captain coordinates.", "anchor": "§A",
                        "provenance": "source_grounded", "evidence_gap": "未量化"}],
            "argumentation": [],
            "mechanism_components": [
                {"name": "Captain", "role": "x"},
                {"name": "Executor Pool", "role": "y"},
                {"name": "Task Graph", "role": "z"},
            ],
            "causal_edges": [
                {"from": "captain", "to": "executor pool", "relation": "r",
                 "edge_kind": "source_causal", "evidence": "ev",
                 "evidence_anchor": "§A"},
            ],
            "graph_edges": [], "how_steps": [], "tldr": "Captain coordinates.",
            "params": {}, "referenced_concepts": [], "applicability_boundary": "",
        }
        _normalize_extract_payload(llm_data, raw, "concept", is_fallback=False)
        # 两路径边端点都 canonical
        for data, label in [(fb.data, "fallback"), (llm_data, "llm")]:
            comp_names = {
                c["name"] for c in data.get("mechanism_components", [])
                if isinstance(c, dict)
            }
            for e in data.get("causal_edges", []):
                assert e.get("from") in comp_names, f"{label}: from 不 canonical"
                assert e.get("to") in comp_names, f"{label}: to 不 canonical"
