"""test_f8c_llm_component_garbage_filter.py — F8-C 组件表垃圾条目全源头过滤测试。

probe-p6 crewai 缺陷:LLM 产组件混入 'Navigate to the directory...'(祈使句)/
'Crew Attributes'(泛节名)。F5-C 只过滤确定性 plain-title 扫描,LLM 组件没过滤。
F8-C:extract 归一化处统一过滤所有来源组件。

覆盖:
    - LLM 产祈使句组件被滤('Navigate to the directory to find config')
    - LLM 产泛节名被滤('Crew Attributes')
    - LLM 产收缩词/助动词句子被滤
    - 正常组件名不动(Captain/Executor Pool/Task Graph and Checkpointing/Sub-Agent Lifecycle)
    - diag components_filtered 计数
    - 滤后 <3 不在此救(留给 fail-closed/enrichment,函数不补足)
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    _filter_llm_component_names,
    _is_garbage_component_name,
)


class TestF8CGarbageNameDetection:
    def test_imperative_sentence_navigate(self):
        """'Navigate to the directory...' 祈使句 → 垃圾(navigate 在 starters)。"""
        assert _is_garbage_component_name("Navigate to the directory to find config")

    def test_imperative_invoke(self):
        assert _is_garbage_component_name("Invoke subagents explicitly")

    def test_generic_section_crew_attributes(self):
        """'Crew Attributes' 泛节名/配置节 → 垃圾。"""
        assert _is_garbage_component_name("Crew Attributes")

    def test_generic_section_configuration(self):
        assert _is_garbage_component_name("Configuration")
        assert _is_garbage_component_name("Settings")

    def test_contraction_sentence(self):
        """收缩词(isn't/it's)→ 句子 → 垃圾。"""
        assert _is_garbage_component_name("It's a component")
        assert _is_garbage_component_name("Don't run this")

    def test_aux_verb_sentence(self):
        """小写助动词(is/are/...)→ 句子 → 垃圾。"""
        assert _is_garbage_component_name("Is the executor running")
        assert _is_garbage_component_name("Are components stateless")

    def test_overlong_sentence(self):
        """>6 词 → likely 句子 → 垃圾。"""
        assert _is_garbage_component_name(
            "Navigate to the directory and open the config file to edit"
        )

    def test_empty_name(self):
        assert _is_garbage_component_name("")

    def test_valid_components_not_garbage(self):
        """正常组件名(命名短语)→ 非垃圾,保留。"""
        for name in (
            "Captain",
            "Executor Pool",
            "Task Graph and Checkpointing",
            "Sub-Agent Lifecycle",
            "Work Unit Selector",
            "Checkpoint Store",
        ):
            assert not _is_garbage_component_name(name), f"{name!r} 误判垃圾"

    def test_lowercase_valid_name_not_garbage(self):
        """小写命名短语(executor pool)→ 非垃圾(LLM 可能小写,只要是命名短语保留;
        不要求 title-case,避免误杀)。"""
        assert not _is_garbage_component_name("executor pool")
        assert not _is_garbage_component_name("task graph")


class TestF8CFilterFunction:
    def _data(self, *names: str) -> dict:
        return {
            "mechanism_components": [
                {"name": n, "role": "x"} for n in names
            ],
            "claims": [],
            "causal_edges": [],
            "how_steps": [],
        }

    def test_imperative_filtered_normal_kept(self):
        """LLM 产祈使句被滤 + 正常组件保留。"""
        data = self._data(
            "Captain",
            "Navigate to the directory to find config",  # 滤
            "Executor Pool",
            "Crew Attributes",  # 滤
        )
        filtered = _filter_llm_component_names(data)
        assert filtered == 2
        names = [c["name"] for c in data["mechanism_components"]]
        assert "Captain" in names
        assert "Executor Pool" in names
        assert "Navigate to the directory to find config" not in names
        assert "Crew Attributes" not in names

    def test_all_normal_kept_filtered_zero(self):
        """全正常组件 → 滤 0,全保留。"""
        data = self._data("Captain", "Executor Pool", "Task Graph")
        filtered = _filter_llm_component_names(data)
        assert filtered == 0
        assert len(data["mechanism_components"]) == 3

    def test_all_garbage_filtered(self):
        """全垃圾 → 全滤(滤后 <3 留给 fail-closed/enrichment,本函数不补足)。"""
        data = self._data(
            "Navigate to the directory",
            "Crew Attributes",
            "Is the system running",
        )
        filtered = _filter_llm_component_names(data)
        assert filtered == 3
        assert data["mechanism_components"] == []  # 全滤,不补足

    def test_empty_name_kept_for_validate(self):
        """空名组件不在此滤(留给 _validate_extract_output 处理)。"""
        data = {
            "mechanism_components": [
                {"name": "", "role": "x"},
                {"name": "Captain", "role": "x"},
            ],
            "claims": [],
            "causal_edges": [],
            "how_steps": [],
        }
        filtered = _filter_llm_component_names(data)
        assert filtered == 0  # 空名不计
        assert len(data["mechanism_components"]) == 2  # 空名保留

    def test_no_components_noop(self):
        """无组件 → 0。"""
        data = {"mechanism_components": [], "claims": [], "causal_edges": [], "how_steps": []}
        assert _filter_llm_component_names(data) == 0

    def test_preserves_role_and_aliases(self):
        """保留组件的 role/aliases 等其他字段。"""
        data = {
            "mechanism_components": [
                {"name": "Captain", "role": "coordinator", "aliases": ["captain"]},
                {"name": "Crew Attributes", "role": "config", "aliases": ["attrs"]},
            ],
            "claims": [],
            "causal_edges": [],
            "how_steps": [],
        }
        filtered = _filter_llm_component_names(data)
        assert filtered == 1
        kept = data["mechanism_components"][0]
        assert kept["name"] == "Captain"
        assert kept["role"] == "coordinator"
        assert kept["aliases"] == ["captain"]
