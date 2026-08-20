"""R2/wA#2: grader concept prompt 修复 —— 解锁满仓 concept 条目"grader 不可用"。

满仓 #1/3/4/5/9/11/14 (全 concept lane) 报 grader_fallback_not_allowed, 误判成
环境问题。实测探针证明: gpt-5.4 在线能调通, 但 prompt 有两个 bug 让它输出过不了
parser → 退 gemini → runner 守卫拒收:
  ① concept prompt 灌的是 procedural 10 维 rubric+权重表 (concept 维度根本不在
     10-dimensions.md), 模型按 procedural 维度名输出 → 不在 concept 维度 enum;
  ② 网关 (codex/gpt-5.4) 不强制 strict json_schema, 而 prompt 从没写死字段名,
     模型自由发挥用 {verdict, defect} 而非 parser 要的 {pass(布尔), defects(数组)}。

本测试 (离线) 锁 prompt 行为契约:
- concept prompt 只含本车道维度, 不混 procedural 专属维度;
- prompt 显式写死 JSON 字段名 (pass/defects), 并禁用 verdict 等;
- parser 容差能匹配 (H3)/(H2) 后缀与全角括号, 但仍拒错形状。
(gpt-5.4 真实输出过 parser 属 live/integration, 不在离线套。)
"""

from __future__ import annotations

from src.akb2.quality.grader import (
    _build_grader_prompt,
    _build_grader_schema,
    _build_grader_weight_floor_note,
    _canonical_dimension_name,
    _dimensions_for_lane,
    _load_rubric_text,
    _parse_grader_output,
)


def _concept_system_prompt() -> str:
    msgs = _build_grader_prompt("候选页正文", _load_rubric_text(), content_lane="concept")
    return next(m["content"] for m in msgs if m["role"] == "system")


def _is_object_schema(schema: dict) -> bool:
    schema_type = schema.get("type")
    return schema_type == "object" or (
        isinstance(schema_type, list) and "object" in schema_type
    )


def _strict_objects(schema: dict):
    """遍历 schema 中所有 additionalProperties:false 的 object。"""
    if isinstance(schema, dict):
        if _is_object_schema(schema) and schema.get("additionalProperties") is False:
            yield schema
        for value in schema.values():
            yield from _strict_objects(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _strict_objects(item)


def test_grader_schema_required_covers_all_strict_object_properties():
    """Azure strict response_format：strict object 的 required 必须覆盖全部 properties。"""
    schema = _build_grader_schema("concept")
    strict_objects = list(_strict_objects(schema))
    assert strict_objects, "测试应至少覆盖顶层与嵌套 strict object"
    for obj in strict_objects:
        properties = set(obj.get("properties", {}))
        required = set(obj.get("required", []))
        assert required == properties


def test_parser_accepts_nullable_strict_schema_optionals():
    """schema 为 Azure strict 必填 nullable 后，parser 应把 null 当旧语义缺省处理。"""
    dims = _dimensions_for_lane("concept")
    data = {
        "dimensions": [
            {"dimension": d, "pass": True, "score": None, "defects": []} for d in dims
        ],
        "weighted_total": None,
        "threshold": None,
        "integrity_floor": None,
        "interpretation_entailment": None,
    }
    _defects, ok = _parse_grader_output(data, content_lane="concept")
    assert ok is True

def test_concept_prompt_lists_all_concept_dims():
    """concept prompt 必须逐字列出当前 5 个 concept 维度名 (含 (H3)/(H2) 后缀)。"""
    sp = _concept_system_prompt()
    for dim in _dimensions_for_lane("concept"):
        assert dim in sp, f"concept prompt 缺维度 {dim}"


def test_concept_prompt_excludes_procedural_only_dims():
    """concept prompt 不得混入 procedural 专属维度 (wA#2: 否则模型按 10 维输出)。"""
    sp = _concept_system_prompt()
    # How 密度 / How 可执行性 既非 concept 维度也非完整性地板名 → 不该出现
    assert "How 密度" not in sp
    assert "How 可执行性" not in sp


def test_concept_prompt_pins_json_field_names():
    """prompt 必须写死字段名 (pass/defects), 并禁用 verdict —— 网关不强制 schema。"""
    sp = _concept_system_prompt()
    assert '"pass"' in sp and '"defects"' in sp
    assert "verdict" in sp  # 以"不得用 verdict"形式出现 (禁用说明)
    assert "布尔" in sp


def test_weight_note_lane_filtered():
    """concept 权重表只列 concept 维度, 不列 procedural 的 How 密度。"""
    note = _build_grader_weight_floor_note("concept")
    assert "概念机制清晰度(H3)" in note
    assert "How 密度" not in note


def test_canonical_dimension_name_tolerates_suffix_and_fullwidth():
    """容差: 丢 (H3) 后缀 / 全角括号 仍归一到 canonical。"""
    # 丢后缀
    assert _canonical_dimension_name("概念机制清晰度", "concept") == "概念机制清晰度(H3)"
    # 全角括号
    assert _canonical_dimension_name("实现机制说明（H2）", "concept") == "实现机制说明(H2)"
    # 旧 advisory 维度已从当前 concept 集退役，必须拒绝。
    assert _canonical_dimension_name("图谱可调用性", "concept") is None
    # 真未知维度仍拒 (不放松到误配)
    assert _canonical_dimension_name("How 密度", "concept") is None


def test_parser_accepts_correct_concept_shape():
    """全 5 维 + 正确字段形状 → parse 成功。"""
    dims = _dimensions_for_lane("concept")
    data = {
        "dimensions": [
            {"dimension": d, "pass": True, "defects": [], "score": 0.8} for d in dims
        ],
        "interpretation_entailment": True,
    }
    _defects, ok = _parse_grader_output(data, content_lane="concept")
    assert ok is True


def test_parser_rejects_verdict_defect_shape():
    """{verdict, defect} 错形状 → parse 失败 (契约不放松, 逼 prompt 出对结构)。"""
    dims = _dimensions_for_lane("concept")
    data = {
        "dimensions": [
            {"dimension": d, "verdict": "pass", "defect": ""} for d in dims
        ],
    }
    _defects, ok = _parse_grader_output(data, content_lane="concept")
    assert ok is False
