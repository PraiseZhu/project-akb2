"""test_distill_lane_threading.py — content_lane 穿过 distill/抽取子进程 + 三车道内芯。

覆盖 SC-5 / SC-8 + intake-rebuild p5（sc-p5-add-pattern-design）:
    - SC-5: content_lane 出现在 candidate frontmatter（p5 起 procedural 别名落盘归一
      为 tutorial canonical）
    - SC-8: content_lane 真正穿过 extract 子进程边界
        * worker_payload_carries_lane: _run_extract_claims_worker 传携 lane 的 JSON payload
        * worker_main_forwards_lane: distill_worker.main() 读 JSON → extract_claims(content_lane=)
        * extract_funcs_accept_lane: extract_claims / build_deterministic / _validate_extract_output
          均接 content_lane 形参
    - sc-p5-add-pattern-design: 三车道切内芯、9 节骨架不变
        * 抽取 prompt：how_params 段按 tutorial/pattern/design 注入车道内芯，
          concept/空 lane 不注入；procedural 别名归一 tutorial 不注入
        * validate 门：tutorial（含别名/默认）保留 executable≥3 门；pattern/design
          不卡该门（车道核心价值由 floor F4 H1≥1 裁决，extract 不早判）
        * 成稿骨架：三车道 9 节标题完全一致（TLDR→维护记录），只有内芯差异
"""

from __future__ import annotations

import inspect
import io
import json

import pytest

from src.akb2.quality import distill, distill_worker

# ---------------------------------------------------------------------------
# sc-p5: 车道常量与归一
# ---------------------------------------------------------------------------


class TestLaneNormalize:
    def test_lane_constants_defined(self) -> None:
        """三车道枚举常量：tutorial canonical + pattern/design + procedural 别名。"""
        assert distill.CONTENT_LANE_TUTORIAL == "tutorial"
        assert distill.CONTENT_LANE_PATTERN == "pattern"
        assert distill.CONTENT_LANE_DESIGN == "design"
        assert distill.CONTENT_LANE_PROCEDURAL_ALIAS == "procedural"

    def test_procedural_alias_normalizes_to_tutorial(self) -> None:
        """procedural 别名归一为 tutorial；其余值原样（含空串，兼容默认路径）。"""
        assert distill._normalize_content_lane("procedural") == "tutorial"
        assert distill._normalize_content_lane("PROCEDURAL ") == "tutorial"
        assert distill._normalize_content_lane("tutorial") == "tutorial"
        assert distill._normalize_content_lane("pattern") == "pattern"
        assert distill._normalize_content_lane("design") == "design"
        assert distill._normalize_content_lane("") == ""
        assert distill._normalize_content_lane(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SC-5: content_lane 进 candidate frontmatter
# ---------------------------------------------------------------------------


class TestFrontmatterCarriesLane:
    def test_frontmatter_carries_lane(self) -> None:
        """_build_frontmatter 写入 content_lane 键，值为透传车道。"""
        extract_data = {
            "claims": [
                {
                    "text": "Loop engineering designs bounded convergent loops",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未量化：原文未给可度量判据",
                }
            ],
            "referenced_concepts": ["loop-engineering", "harness-engineering"],
            "how_steps": [],
        }
        fm = distill._build_frontmatter(
            "raw/concept--raw--2026-06-26.md",
            extract_data,
            content_lane="concept",
        )
        assert "content_lane" in fm, "candidate frontmatter 应含 content_lane 键"
        assert fm["content_lane"] == "concept"

    def test_frontmatter_lane_defaults_empty(self) -> None:
        """不传 content_lane 时键存在且为空（旧调用兼容、键稳定）。"""
        extract_data = {
            "claims": [
                {
                    "text": "x",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未提供",
                }
            ],
            "referenced_concepts": ["a", "b"],
            "how_steps": [],
        }
        fm = distill._build_frontmatter("raw/x--raw--2026-06-26.md", extract_data)
        assert fm.get("content_lane", "") == ""

    def test_frontmatter_normalizes_procedural_alias(self) -> None:
        """p5：procedural 别名落盘前归一为 tutorial（不再产生 procedural 终值）。"""
        extract_data = {
            "claims": [
                {
                    "text": "x",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                    "evidence_gap": "未提供",
                }
            ],
            "referenced_concepts": ["a", "b"],
            "how_steps": [],
        }
        fm = distill._build_frontmatter(
            "raw/x--raw--2026-06-26.md", extract_data, content_lane="procedural"
        )
        assert fm["content_lane"] == "tutorial", (
            "frontmatter 落盘应为 tutorial canonical，不得出现 procedural 终值"
        )


# ---------------------------------------------------------------------------
# SC-8: 抽取子进程过界
# ---------------------------------------------------------------------------


class TestExtractFuncsAcceptLane:
    def test_extract_funcs_accept_lane(self) -> None:
        """extract_claims / build_deterministic / _validate_extract_output 接 content_lane。"""
        assert "content_lane" in inspect.signature(distill.extract_claims).parameters
        assert (
            "content_lane"
            in inspect.signature(distill.build_deterministic_extract_result).parameters
        )
        assert (
            "content_lane"
            in inspect.signature(distill._validate_extract_output).parameters
        )

    def test_validate_extract_output_lane_behavior_unchanged(self) -> None:
        """_validate_extract_output 接 content_lane；§0.2/A2 后 concept 不再 extract 判 F4。

        本 step 不改 claims 空校验：各车道对同一非法数据（claims 空）应抛
        同样的 ValueError。F4（mechanism≥3/连边 / 非 concept H1≥1）由
        integrity_floor 裁决（见 test_integrity_floor TestCoreValueF4）。
        """
        bad_data: dict = {"claims": []}  # claims 为空 → 必抛 ValueError
        for lane in ("concept", "tutorial", "pattern", "design", ""):
            with pytest.raises(ValueError):
                distill._validate_extract_output(bad_data, content_lane=lane)


class TestWorkerMainForwardsLane:
    def test_worker_main_forwards_lane(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """distill_worker.main() 读 JSON payload → 转 extract_claims(content_lane=...)。"""
        captured: dict = {}

        class _FakeResult:
            success = True
            data = {"ok": 1}
            retries_used = 0
            error = ""

        def _fake_extract(raw_content, content_lane=""):
            captured["raw_content"] = raw_content
            captured["content_lane"] = content_lane
            return _FakeResult()

        monkeypatch.setattr(distill_worker, "extract_claims", _fake_extract)
        payload = json.dumps(
            {"raw_content": "some raw body", "content_lane": "concept"},
            ensure_ascii=False,
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        rc = distill_worker.main()
        assert rc == 0
        assert captured["raw_content"] == "some raw body"
        assert captured["content_lane"] == "concept", "worker 必须把 JSON 中的 lane 转给 extract_claims"

    def test_worker_main_backward_compat_json_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """向后兼容：JSON 字符串 stdin（json.dumps(raw)）视为 raw_content、lane=''。"""
        captured: dict = {}

        class _FakeResult:
            success = True
            data = {}
            retries_used = 0
            error = ""

        def _fake_extract(raw_content, content_lane=""):
            captured["raw_content"] = raw_content
            captured["content_lane"] = content_lane
            return _FakeResult()

        monkeypatch.setattr(distill_worker, "extract_claims", _fake_extract)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps("legacy raw text")))

        rc = distill_worker.main()
        assert rc == 0
        assert captured["raw_content"] == "legacy raw text"
        assert captured["content_lane"] == ""


class TestWorkerPayloadCarriesLane:
    def test_worker_payload_carries_lane(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_run_extract_claims_worker 向子进程传携 content_lane 的 JSON payload（非裸 raw）。"""
        from src.akb2.quality import runner

        captured: dict = {}

        class _FakeProc:
            returncode = 0
            pid = 99999

            def communicate(self, input=None, timeout=None):
                captured["input"] = input
                # 返回合法 worker payload
                return (
                    json.dumps(
                        {"success": True, "data": {}, "retries_used": 0, "error": ""}
                    ),
                    "",
                )

            def poll(self):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeProc()

        monkeypatch.setattr(runner.subprocess, "Popen", _fake_popen)

        runner._run_extract_claims_worker("RAW BODY HERE", content_lane="tutorial")

        assert "input" in captured, "worker 应通过 stdin 传 payload"
        # 传的是 JSON payload（非裸 raw_content）
        payload = json.loads(captured["input"])
        assert isinstance(payload, dict)
        assert payload["raw_content"] == "RAW BODY HERE"
        assert payload["content_lane"] == "tutorial", "payload 必须携 content_lane 穿过子进程边界"


# ---------------------------------------------------------------------------
# sc-p5-add-pattern-design: 抽取 prompt 三车道内芯
# ---------------------------------------------------------------------------


class TestExtractPromptLaneCore:
    def test_how_params_prompt_injects_lane_core_for_pattern_design(self) -> None:
        """how_params 段对 pattern/design 注入车道专属内芯指令。"""
        for lane, marker in (
            ("pattern", "pattern 车道内芯"),
            ("design", "design 车道内芯"),
        ):
            msgs = distill._build_extract_segment_messages(
                "how_params", "原文内容", content_lane=lane
            )
            sys_text = msgs[0]["content"]
            assert marker in sys_text, f"{lane} 车道内芯缺失"

    def test_how_params_prompt_no_core_for_other_lanes(self) -> None:
        """tutorial/concept/空 lane 不注入车道内芯（零回归）；procedural 别名归一 tutorial。"""
        for lane in ("tutorial", "concept", "", "procedural"):
            msgs = distill._build_extract_segment_messages(
                "how_params", "原文内容", content_lane=lane
            )
            assert "车道内芯" not in msgs[0]["content"], (
                f"lane={lane!r} 不应注入 pattern/design 内芯"
            )

    def test_other_segments_ignorant_of_lane(self) -> None:
        """claims/meta/argumentation/graph 段与车道无关——内芯只切 how_params。"""
        for segment in ("claims", "meta", "argumentation", "graph"):
            msgs = distill._build_extract_segment_messages(
                segment, "原文内容", content_lane="pattern"
            )
            assert "pattern 车道内芯" not in msgs[0]["content"], (
                f"{segment} 段不应携带车道内芯"
            )

    def test_extract_claims_forwards_lane_to_segment_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """extract_claims 把 content_lane 透传给 _extract_segment_once（内芯穿线）。"""
        captured: dict = {}

        def _fake_segment_once(
            segment_name,
            content_snippet,
            route_llm_fn,
            beta_tool_enabled,
            json_mode_enabled,
            previous_segments,
            content_lane="",
        ):
            captured[segment_name] = content_lane
            if segment_name == "claims":
                return {
                    "claims": [
                        {
                            "text": "Patterns need explicit stop conditions.",
                            "anchor": "§A",
                            "provenance": "source_grounded",
                            "evidence_gap": "未提供：原文未给量化判据",
                        }
                    ],
                    "tldr": "t" * 25,
                }
            if segment_name == "how_params":
                return {
                    "how_steps": [],
                    "mechanism_components": [],
                    "causal_edges": [],
                    "param_entries": [],
                }
            raise AssertionError(f"unexpected segment {segment_name}")

        monkeypatch.setenv("AKB2_DISTILL_SEMANTIC_RETAKE", "0")
        monkeypatch.setattr(distill, "_extract_segment_once", _fake_segment_once)

        # pattern 车道 claims 空 how_steps 可过 validate（不卡 executable 门）
        result = distill.extract_claims("RAW", content_lane="pattern")
        assert result.success is True, f"pattern 车道应通过: {result.error}"
        assert captured["claims"] == "pattern"
        assert captured["how_params"] == "pattern", (
            "lane 必须穿到 segment 调用（prompt 内芯注入点）"
        )


# ---------------------------------------------------------------------------
# sc-p5-add-pattern-design: validate 门三车道分派
# ---------------------------------------------------------------------------


def _minimal_valid_extract_data(how_steps: list[dict]) -> dict:
    """构造除 how_steps 外全字段合法的 extract 数据（validate 门聚焦 how_steps）。"""
    return {
        "claims": [
            {
                "text": "Pattern collections need explicit stop conditions.",
                "anchor": "§A",
                "provenance": "source_grounded",
                "evidence_gap": "未量化：原文未给模式适用性的度量判据",
            }
        ],
        "how_steps": how_steps,
        "mechanism_components": [],
        "causal_edges": [],
        "params": {},
        "referenced_concepts": ["agent-patterns"],
        "tldr": "A reusable pattern collection for bounded agent loops." * 1,
        "argumentation": [
            {
                "claim_anchor": "§A",
                "explanation": "Pattern collections need explicit stop conditions.",
                "provenance": "source_grounded",
            }
        ],
        "applicability_boundary": (
            "适用场景：重复触发且需要模式复用的 agent 系统；"
            "不适用场景：一次性问答；约束条件：模式须有明确适用条件。"
        ),
        "graph_edges": [],
    }


class TestValidateLaneDispatch:
    def test_tutorial_keeps_executable_gate(self) -> None:
        """tutorial 保留 executable≥3 门（操作型内芯，零回归）。"""
        data = _minimal_valid_extract_data(
            [
                {
                    "instruction": "no command step",
                    "command": "",
                    "executable": False,
                    "how_level": "H3",
                }
            ]
        )
        with pytest.raises(ValueError, match="可执行步骤"):
            distill._validate_extract_output(data, content_lane="tutorial")

    def test_procedural_alias_same_gate_as_tutorial(self) -> None:
        """procedural 别名与 tutorial 同门（归一后同判）。"""
        data = _minimal_valid_extract_data([])
        with pytest.raises(ValueError, match="可执行步骤"):
            distill._validate_extract_output(data, content_lane="procedural")

    def test_default_empty_lane_keeps_gate(self) -> None:
        """空 lane（旧调用方默认）保留 executable≥3 门（零回归）。"""
        data = _minimal_valid_extract_data([])
        with pytest.raises(ValueError, match="可执行步骤"):
            distill._validate_extract_output(data, content_lane="")

    def test_pattern_no_executable_gate(self) -> None:
        """pattern 不卡 executable 门——模式集天然无命令，F4 由 floor 裁决。"""
        data = _minimal_valid_extract_data(
            [
                {
                    "instruction": "Bounded loop with explicit stop conditions",
                    "command": "",
                    "executable": False,
                    "how_level": "H3",
                }
            ]
        )
        distill._validate_extract_output(data, content_lane="pattern")  # 不抛

    def test_design_no_executable_gate(self) -> None:
        """design 不卡 executable 门——设计决策天然无命令，F4 由 floor 裁决。"""
        data = _minimal_valid_extract_data(
            [
                {
                    "instruction": "Visual hierarchy puts status first",
                    "command": "",
                    "executable": False,
                    "how_level": "H2",
                }
            ]
        )
        distill._validate_extract_output(data, content_lane="design")  # 不抛

    def test_concept_still_skips_gate(self) -> None:
        """concept 仍不卡 executable 门（既有行为，A3-1）。"""
        data = _minimal_valid_extract_data([])
        distill._validate_extract_output(data, content_lane="concept")  # 不抛


# ---------------------------------------------------------------------------
# sc-p5-add-pattern-design: deterministic fallback 不注入假操作步骤
# ---------------------------------------------------------------------------


class TestDeterministicFallbackLaneCore:
    def test_pattern_design_fallback_no_fake_how_steps(self) -> None:
        """pattern/design fallback 不注入 AKB 管线操作化步骤（防 poisoned trace）。"""
        for lane in ("pattern", "design"):
            data = distill._build_deterministic_extract_data(
                "# §A Some pattern content with meaningful text here.\n\n"
                "§A A collection of reusable patterns needs explicit conditions.",
                "raw/x--raw--2026-06-26.md",
                content_lane=lane,
            )
            assert data["how_steps"] == [], f"{lane} fallback 不应注入假操作步骤"
            assert data["params"] == {}, f"{lane} fallback 不应注入管线参数"

    def test_tutorial_fallback_keeps_how_steps(self) -> None:
        """tutorial（含默认）fallback 保留既有注入（零回归）。"""
        data = distill._build_deterministic_extract_data(
            "# §A Some tutorial content with meaningful text here.\n\n"
            "§A Follow these steps to configure the pipeline.",
            "raw/x--raw--2026-06-26.md",
            content_lane="tutorial",
        )
        assert data["how_steps"], "tutorial fallback 应保留既有 how_steps 注入"


# ---------------------------------------------------------------------------
# sc-p5-add-pattern-design: 9 节骨架三车道一致
# ---------------------------------------------------------------------------

# 9 节 + 维护记录骨架（PAGE-TEMPLATES source 模板节标题，全车道不变）
_EXPECTED_SECTION_TITLES = (
    "## TLDR",
    "## 关键发现",
    "## 入选理由",
    "## 证据地图",
    "## 核心论点",
    "## 论证与解释",
    "## 新颖增量",
    "## 适用边界",
    "## 图谱关系",
    "## Future Questions",
    "## 维护记录",
)


def _build_body_for_lane(lane: str) -> str:
    """用最小合法 extract_data 走 _build_body（确定性脚本，不调 LLM）。"""
    extract_data = _minimal_valid_extract_data(
        [
            {
                "instruction": "Bounded loop with explicit stop conditions",
                "command": "",
                "executable": False,
                "how_level": "H3",
                "anchor": "§A",
                "provenance": "source_grounded",
            }
        ]
    )
    if lane == "concept":
        # concept 车道无 how 双空担忧——机制证据节由 concept 分支自产
        pass
    return distill._build_body(
        extract_data,
        "raw/x--raw--2026-06-26.md",
        route_llm_fn=None,
        raw_content=(
            "# Title\n\n§A A collection of reusable patterns needs explicit"
            " stop conditions and named components."
        ),
        content_lane=lane,
    )


class TestNineSectionSkeletonInvariant:
    @pytest.mark.parametrize("lane", ["tutorial", "pattern", "design", "concept"])
    def test_skeleton_identical_across_lanes(self, lane: str) -> None:
        """三车道（含 concept 对照）9 节骨架标题完全一致——切内芯不换骨架。"""
        body = _build_body_for_lane(lane)
        for title in _EXPECTED_SECTION_TITLES:
            assert title in body, f"lane={lane} 骨架缺节 {title}"
        # 节序一致（骨架顺序不因车道重排）
        positions = [body.index(t) for t in _EXPECTED_SECTION_TITLES]
        assert positions == sorted(positions), (
            f"lane={lane} 节序应保持模板顺序"
        )

    def test_pattern_how_core_differs_from_tutorial(self) -> None:
        """内芯差异可鉴别：pattern 的抽取 prompt/validate 门与 tutorial 分道。

        成稿层骨架一致（上一测试锁定），内芯差异在抽取层：
        1. how_params prompt 注入 pattern 内芯（tutorial 无）；
        2. validate 门 pattern 不卡 executable≥3（tutorial 卡）。
        """
        msgs_p = distill._build_extract_segment_messages(
            "how_params", "x", content_lane="pattern"
        )
        msgs_t = distill._build_extract_segment_messages(
            "how_params", "x", content_lane="tutorial"
        )
        assert msgs_p[0]["content"] != msgs_t[0]["content"], (
            "pattern 与 tutorial 的抽取内芯指令应有差异"
        )
        assert "pattern 车道内芯" in msgs_p[0]["content"]

    def test_design_how_core_walkthrough_on_double_empty(self) -> None:
        """design 内芯：how 双空（无 claims 衍生步骤可用）走车道 walkthrough 分支。

        _build_body 的 claims 衍生兜底会把带文本 claim 变成步骤；本用例构造
        无 command 且 instruction 无法衍生的最小 claims，使 supported_how_steps
        为空 → design 分支产车道诚实说明（不落 tutorial 的 FORBIDDEN 占位）。
        直接单测 _render_lane_core_walkthrough 双空行为 + _build_body 分派。
        """
        walkthrough = distill._render_lane_core_walkthrough({}, "design", None)
        assert "设计决策" in walkthrough, "design walkthrough 应含车道标签"
        assert "原文无可安全锚定的操作步骤" not in walkthrough, (
            "design walkthrough 不得含 FORBIDDEN marker（SG-how-executability 会自杀）"
        )
        walkthrough_p = distill._render_lane_core_walkthrough({}, "pattern", None)
        assert "模式" in walkthrough_p

    def test_lane_walkthrough_renders_anchored_steps(self) -> None:
        """pattern walkthrough 从带真实锚的模式条目渲染内芯行（零编造）。"""
        extract_data = {
            "how_steps": [
                {
                    "instruction": "Bounded loop with explicit stop conditions",
                    "command": "",
                    "executable": False,
                    "how_level": "H3",
                    "anchor": "§A",
                    "provenance": "source_grounded",
                }
            ]
        }
        raw = "# T\n\n§A A collection of reusable patterns needs conditions."
        out = distill._render_lane_core_walkthrough(extract_data, "pattern", raw)
        assert "[raw §A] (source_grounded) 模式:Bounded loop" in out, (
            "walkthrough 应渲染锚定模式条目行"
        )

    def test_lane_walkthrough_skips_fake_anchor(self) -> None:
        """锚不在 raw 声明集 → 跳过该条（不造假 anchor）。"""
        extract_data = {
            "how_steps": [
                {
                    "instruction": "Bounded loop with explicit stop conditions",
                    "command": "",
                    "executable": False,
                    "how_level": "H3",
                    "anchor": "§Z",
                    "provenance": "source_grounded",
                }
            ]
        }
        raw = "# T\n\n§A A collection of reusable patterns needs conditions."
        out = distill._render_lane_core_walkthrough(extract_data, "pattern", raw)
        assert "§Z" not in out, "伪锚不得渲染"
        assert "模式条目" in out or "无可锚定" in out, "无可用条目应落诚实说明"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
