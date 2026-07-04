"""test_scan_component_table.py — P1b-2 fallback 组件表行抽取测试。

覆盖:
    - 表格 header 命中组件类关键词(component/组件/模块…)→ 取该列单元作组件名
    - 非组件 header(步骤/说明)→ 不抽取
    - 锚点目录块内的表 → 排除
    - 剥 **bold** / 反引号 / 行首 §X 锚点前缀
    - _scan_real_mechanism_components 全局上限 FALLBACK_MAX_COMPONENTS=12
"""

from __future__ import annotations

from src.akb2.quality.distill import (
    FALLBACK_MAX_COMPONENTS,
    _scan_component_table_rows,
    _scan_real_mechanism_components,
)


class TestScanComponentTableRows:
    def test_component_column_extracted(self):
        """| 组件 | 描述 | 表 → 取组件列单元。"""
        raw = (
            "## §A Overview\n\n"
            "| 组件 | 描述 |\n|------|------|\n"
            "| Executor Pool | 调度执行 |\n"
            "| Task Graph | 任务依赖图 |\n"
            "| Lifecycle | 生命周期 |\n"
        )
        names = _scan_component_table_rows(raw)
        assert "Executor Pool" in names
        assert "Task Graph" in names
        assert "Lifecycle" in names

    def test_english_component_header(self):
        """英文 header 'Component' 也命中。"""
        raw = "| Component | Role |\n|---|---|\n| Captain | coord |\n| Worker | exec |\n"
        names = _scan_component_table_rows(raw)
        assert "Captain" in names
        assert "Worker" in names

    def test_non_component_header_not_extracted(self):
        """| 步骤 | 说明 | 表 → 无组件列,不抽取。"""
        raw = "| 步骤 | 说明 |\n|------|------|\n| 1. clone | 克隆仓库 |\n| 2. build | 构建 |\n"
        assert _scan_component_table_rows(raw) == []

    def test_directory_block_table_excluded(self):
        """锚点目录块内的表(| §X | desc |)→ 排除(strip_directory_block 去除)。"""
        raw = (
            "## 锚点目录\n\n"
            "| 组件 | 内容 |\n|------|------|\n"
            "| §A | CONTENT_START |\n"
            "| §I | LIMITATIONS |\n\n"
            "---\n\n"
            "正文无表。"
        )
        assert _scan_component_table_rows(raw) == []

    def test_bold_backtick_and_anchor_prefix_stripped(self):
        """单元剥 **bold** / 反引号 / 行首 §X 锚点前缀。"""
        raw = (
            "| 组件 | desc |\n|---|---|\n"
            "| **Executor Pool** | x |\n"
            "| `Task Graph` | y |\n"
            "| §B Dispatcher | z |\n"
        )
        names = _scan_component_table_rows(raw)
        assert "Executor Pool" in names
        assert "Task Graph" in names
        assert "Dispatcher" in names  # §B 前缀剥掉
        assert not any("§" in n for n in names)  # 无锚点残留

    def test_separator_row_skipped(self):
        """分隔行 |:---|---:| 不算组件。"""
        raw = "| 组件 |\n|:---:|\n| Executor |\n|:----:|\n| Pool |\n"
        names = _scan_component_table_rows(raw)
        assert "Executor" in names
        assert "Pool" in names


class TestScanRealMechanismComponentsCap:
    def test_cap_at_fallback_max(self):
        """编号标题 >12 → 截到 FALLBACK_MAX_COMPONENTS=12(有界预算)。"""
        headings = "\n".join(
            f"### {i}. Component{i} name" for i in range(1, 15)
        )  # 14 个编号标题
        raw = f"## §A\n\n{headings}\n"
        components = _scan_real_mechanism_components(raw)
        assert len(components) == FALLBACK_MAX_COMPONENTS
        assert FALLBACK_MAX_COMPONENTS == 12

    def test_table_fills_components_when_headings_absent(self):
        """无编号标题时,表格组件列补足组件(达 CONCEPT_MIN=3)。"""
        raw = (
            "## §A Overview\n\n一些散文无编号标题。\n\n"
            "| 组件 | desc |\n|---|---|\n"
            "| Executor Pool | x |\n"
            "| Task Graph | y |\n"
            "| Lifecycle | z |\n"
        )
        components = _scan_real_mechanism_components(raw)
        names = [c["name"] for c in components]
        assert len(components) >= 3
        assert "Executor Pool" in names
        assert "Task Graph" in names
