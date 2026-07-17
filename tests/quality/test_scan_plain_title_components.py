"""test_scan_plain_title_components.py — F-B fallback 普通标题组件抽取测试。

覆盖:
    - 正:title-case 命名短语标题(Tool Registration/Loop Structure)→ 组件
    - 正:含机制实体关键词标题(AGENTS.md 含 agent)→ 组件
    - 负:Introduction/Conclusion/FAQ/Overview/Summary 泛节名 → 拒
    - 负:祈使句/疑问句标题(How it works/Choose where...)→ 拒
    - 负:含收缩词(isn't/it's)或小写助动词(is/are)的句子标题 → 拒
    - 集成:plain H2 标题只作为候选信号,最终 deterministic fallback 不把它当组件
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    _build_deterministic_extract_data,
    _build_fallback_quality_basis,
    _is_garbage_component_name,
    _is_plain_title_component,
    _scan_plain_title_components,
    _scan_real_mechanism_components,
    build_deterministic_extract_result,
)


class TestIsPlainTitleComponent:
    def test_title_case_naming_phrase_accepted(self):
        assert _is_plain_title_component("Tool Registration")
        assert _is_plain_title_component("Loop Structure")
        assert _is_plain_title_component("Storage location")  # 首词大写,命名短语

    def test_entity_keyword_accepted(self):
        """含机制实体关键词(AGENTS.md 含 agent)→ 接受(无论 title-case)。"""
        assert _is_plain_title_component("AGENTS.md")  # 'agent' keyword
        assert _is_plain_title_component("Step counter module")  # step/module

    def test_generic_section_names_rejected(self):
        """Introduction/Conclusion/FAQ/Overview/Summary 泛节名 → 拒。"""
        for name in ("Introduction", "Conclusion", "FAQ", "Overview", "Summary",
                     "Background", "References", "Appendix"):
            assert not _is_plain_title_component(name), f"{name} 应被拒"

    def test_imperative_sentence_rejected(self):
        """祈使句/疑问句标题(How/When/Choose/Write 开头)→ 拒。"""
        for name in ("How it works", "When to add to CLAUDE.md",
                     "Choose where to put files", "Write effective instructions",
                     "Manage CLAUDE.md for large teams", "Troubleshoot memory issues"):
            assert not _is_plain_title_component(name), f"{name!r} 应被拒(句子)"

    def test_f5c_imperative_words_rejected(self):
        """F5-C: 祈使词 Invoke/Disable/Debug 开头 → 拒(probe-p4 'Invoke subagents explicitly')。"""
        for name in ("Invoke subagents explicitly", "Disable auto memory",
                     "Debug memory issues"):
            assert not _is_plain_title_component(name), f"{name!r} 应被拒(祈使句)"

    def test_f5c_generic_section_names_rejected(self):
        """F5-C: 泛节名 Quick Start/Getting Started/Next Steps 等 → 拒(probe-p4 'Quick Start')。"""
        for name in ("Quick Start", "Getting Started", "Get started",
                     "Next Steps", "Prerequisites", "Examples", "Usage",
                     "Installation", "Troubleshooting", "See also", "Learn more"):
            assert not _is_plain_title_component(name), f"{name!r} 应被拒(泛节名)"

    def test_f5c_real_component_not_killed(self):
        """F5-C: 真组件 'Task Graph and Checkpointing' 含动名词但非祈使起头 → 不误杀。"""
        assert _is_plain_title_component("Task Graph and Checkpointing")
        assert _is_plain_title_component("Executor Pool")
        assert _is_plain_title_component("Multi-Step Loop")

    def test_contraction_rejected(self):
        """含收缩词(isn't/it's,含 curly ')→ 拒。"""
        assert not _is_plain_title_component("Claude isn't following my CLAUDE.md")
        assert not _is_plain_title_component("It's not working")

    def test_lowercase_aux_verb_rejected(self):
        """含小写助动词(is/are/seem)的句子 → 拒。"""
        assert not _is_plain_title_component("My CLAUDE.md is too large")
        assert not _is_plain_title_component("Instructions seem lost after compact")

    def test_lowercase_first_word_rejected(self):
        """首词小写且无实体关键词(foo bar)→ 拒(非 title-case 命名短语)。"""
        assert not _is_plain_title_component("foo bar budget")
        # 注:'maxSteps Budget' 因含 'step' 实体关键词被接受(entity-keyword 路径优先)


class TestF49ComponentHygieneNoFalseKill:
    """F49 刀2(reviewer 返工 P2-1/P2-2/P2-3):定位为「防虚高组件计数的质量卫生」,
    **非 yield 修复**(reviewer 回放证明 0-edge 死案真死因不在垃圾端点,而在无
    source_causal 边/超时/内容真不行;宽泛 article/gerund 拒判把 maarten 死案从
    旧 5 组件/0 边变成 0/0,删了真组件却没提升 yield)。

    P2-1:撤宽泛 article/endswith('ing') 结构拒判(误杀 The Vision Encoder/Sliding
    Window Attention/Ring Buffer Allocation Strategy/The Residual Stream 等真机制组件)。
    P2-2:泛节名清单拆出 plain-title 专用集合(_PLAIN_TITLE_EXTRA_GENERIC_NAMES),
    不进 LLM 主路径(防 'Author' 角色组件被 _filter_llm_component_names 删)。
    P2-3:测试用 H3(真实死案层级,不被 _is_heading_shaped_component_name 的 H2 同名
    剔除),断言 _build_fallback_quality_basis 最终可观测结果。本测试在宽泛拒判的
    旧实现上 FAIL(真组件被杀→0 组件),在收窄实现上 PASS——断言绑定修复行为。
    """

    def test_p2_1_real_mechanism_titles_not_killed(self):
        """P2-1:无 entity、article/gerund 起首的真机制标题不许被宽泛结构拒判误杀。
        reviewer 实锤反例(全部须 True,宽泛实现全 False)。"""
        for name in ("The Vision Encoder", "The Audio Encoder", "The Residual Stream",
                     "Sliding Window Attention", "Ring Buffer Allocation Strategy"):
            assert _is_plain_title_component(name), f"{name!r} 不许误杀(真机制组件)"

    def test_plain_title_generic_section_names_rejected(self):
        """P2-1/P2-2:泛节名(Acknowledgements/Authors/Biographies)plain-title 扫描拒。"""
        for name in ("Acknowledgements", "Acknowledgement", "Authors", "Author",
                     "About the Authors", "Author Bios", "Biographies", "Biography"):
            assert not _is_plain_title_component(name), f"{name!r} 应拒(plain-title 泛节名)"

    def test_p2_2_author_role_not_filtered_in_llm_path(self):
        """P2-2:'Author' 作合法角色组件(Author/Editor/Reviewer 工作流)在 LLM 主路径
        (_is_garbage_component_name→_filter_llm_component_names)不许被删。泛节名清单已
        拆出 plain-title 专用集合,不进 LLM 黑名单。"""
        assert not _is_garbage_component_name("Author"), "'Author' 角色组件不许被 LLM 路径删"
        assert not _is_garbage_component_name("Authors"), "'Authors' 不许被 LLM 路径删"

    def test_p2_3_h3_real_components_survive_in_deterministic_fallback(self):
        """P2-3:H3(真实死案层级)真机制组件不被误杀,泛节名被拒;最终
        _build_fallback_quality_basis 可观测。镜像 maarten-grootendorst--e9942fdace88
        死案 raw(07-10,旧实现 5 组件/0 边,宽泛拒判实现 0/0——本测试在宽泛实现上
        names 不含 'The Vision Encoder' → FAIL,绑定修复)。"""
        raw = (
            "## 锚点目录\n\n- §A: START\n\n---\n\n"
            "§A_CONTENT_START\n\n"
            "### The Vision Encoder\n\nThe vision encoder encodes image tokens.\n\n"
            "### The Audio Encoder\n\nThe audio encoder processes audio.\n\n"
            "### Sliding Window Attention\n\n"
            "Sliding window attention feeds into the audio encoder.\n\n"
            "### Acknowledgements\n\nThanks to everyone.\n\n"
            "### Authors\n\nXuchao Zhang, Molly Xia.\n"
        )
        comps = _scan_real_mechanism_components(raw)
        names = [c["name"] for c in comps]
        # P2-1:真机制组件(H3,无 entity,article/gerund 起首)不许误杀
        assert "The Vision Encoder" in names, f"真组件被误杀: {names}"
        assert "The Audio Encoder" in names, f"真组件被误杀: {names}"
        assert "Sliding Window Attention" in names, f"真组件被误杀: {names}"
        # P2-2:泛节名拒
        assert "Acknowledgements" not in names
        assert "Authors" not in names
        # P2-3:最终可观测结果——basis component_count 反映真组件(非 0,非虚高)
        data = _build_deterministic_extract_data(raw, content_lane="concept")
        basis = _build_fallback_quality_basis(data)
        assert basis["component_count"] >= 3, f"组件数虚低: {basis}"
        # build_deterministic_extract_result 不崩,携 basis 可观测(成败皆可,关键是真组件在)
        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.data.get("fallback_quality_basis") is not None or result.success



class TestScanPlainTitleComponents:
    def test_title_type_raw_extracts_components(self):
        """title-case 标题型 raw → 抽出组件(去重保序,跳过 §-锚/编号/表格)。"""
        raw = (
            "## 锚点目录\n\n- §A: x\n\n---\n\n"
            "## Tool Registration\n\nreg body.\n\n"
            "## Tool Invocation Flow\n\nflow body.\n\n"
            "### Loop Structure\n\nloop body.\n\n"
            "## Introduction\n\nintro.\n\n"
            "## How it works\n\nhow.\n"
        )
        names = _scan_plain_title_components(raw)
        assert "Tool Registration" in names
        assert "Tool Invocation Flow" in names
        assert "Loop Structure" in names
        assert "Introduction" not in names  # 泛节名拒
        assert "How it works" not in names  # 疑问句拒
        assert "锚点目录" not in names  # 目录标题拒

    def test_skips_anchor_and_numbered_headings(self):
        """§-锚标题与编号标题不重复扫(别处处理)。"""
        raw = "## §A Captain\n\n### 1. Executor\n\n## Real Plain Title\n"
        names = _scan_plain_title_components(raw)
        assert names == ["Real Plain Title"]


class TestIntegrationWithCausalEdges:
    def test_plain_h2_titles_do_not_become_real_components(self):
        """普通 H2 标题候选可被扫描到,但最终不作为机制组件入 fallback。"""
        raw = (
            "## 锚点目录\n\n- §A: START\n- §B: FLOW\n\n---\n\n"
            "§A_CONTENT_START\n\n"
            "## Tool Registration\n\nThe Tool Registration feeds into the Tool Invocation Flow.\n\n"
            "## Tool Invocation Flow\n\nThe flow dispatches to the Loop Structure.\n\n"
            "## Loop Structure\n\nloop body.\n"
        )
        plain = _scan_plain_title_components(raw)
        assert "Tool Registration" in plain
        assert "Tool Invocation Flow" in plain
        comps = _scan_real_mechanism_components(raw)
        names = [c["name"] for c in comps]
        assert "Tool Registration" not in names
        assert "Tool Invocation Flow" not in names
        assert "Loop Structure" not in names

    def test_colon_heading_components_are_filtered_and_fail_closed(self):
        """冒号列举式 H2 标题被过滤,滤后 <3 组件走既有 concept fail-closed。"""
        raw = (
            "---\nartifact_type: raw\n---\n\n"
            "## §A_INTRO\n"
            "This article explains long-running agent loops with enough substantive "
            "source text to create grounded fallback claims.\n\n"
            "1. Call the agent\n"
            "2. Go back to step 1\n\n"
            "## Anthropic: harnesses, then the brain/hands/session split\n"
            "Anthropic discusses the brain, hands, and session mechanics.\n\n"
            "## Cursor: planners, workers, judges\n"
            "Cursor discusses planners, workers, and judges.\n\n"
            "## Google: long-running agents on the Agent Platform\n"
            "Google discusses platform support for long-running agents.\n\n"
            "## §B_MORE\n"
            "Budgets, fallback policy, checker separation, and repair limits are "
            "covered as production constraints.\n"
        )

        data = _build_deterministic_extract_data(raw, content_lane="concept")
        names = [c["name"] for c in data["mechanism_components"]]
        assert names == ["Call the agent", "Go back to step 1"]
        basis = _build_fallback_quality_basis(data)
        assert basis["component_count"] == 2

        result = build_deterministic_extract_result(raw, content_lane="concept")
        assert result.success is False
        assert result.data["fallback_quality_basis"]["component_count"] < 3
        assert "concept deterministic fallback fail-closed" in result.error
