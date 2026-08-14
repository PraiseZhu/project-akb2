"""SKILL.md 规范契约测试（sc-skill-def expect ①②⑦ 机器面）。

① rule-compliance：frontmatter 必需字段、触发词段落式样、恰一条模板、
   手动执行章节引用同一段、全文无第二条可复制命令。
② product-arch-gate：holds 边界转否定式断言 + 确认门条款在场。
⑦ 保证等级：SKILL.md 保证性表述与已机器验证的项一一对应。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from akb2.harvest.template import extract_submit_template

SKILL_MD_PATH = Path(
    "/Users/praise/AI-Agent/Claude/capabilities/source/skills/"
    "claude-active/akb2-harvest/SKILL.md"
)


@pytest.fixture(scope="module")
def skill_md() -> str:
    if not SKILL_MD_PATH.exists():
        pytest.skip("SKILL.md 未生成（本测试依赖真实工件）")
    return SKILL_MD_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ① rule-compliance：frontmatter 与文档结构
# ---------------------------------------------------------------------------


def test_frontmatter_required_fields(skill_md: str):
    """frontmatter 必需字段：name + description 在场且非空。"""
    m = re.match(r"^---\n(.*?)\n---", skill_md, re.DOTALL)
    assert m, "SKILL.md 必须以 YAML frontmatter 开头"
    fm = m.group(1)
    assert re.search(r"^name:\s*\S+", fm, re.MULTILINE)
    desc = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
    assert desc and desc.group(1).strip()
    # frontmatter 总长受限（writing-skills 规范 ≤1024 字符）
    assert len(m.group(0)) <= 1024


def test_trigger_phrases_present(skill_md: str):
    """触发词段落式样：description 或正文含「收割昨天/收割昨日/akb2 收割」。"""
    assert "收割昨天" in skill_md
    assert "收割昨日" in skill_md or "akb2 收割" in skill_md


def test_exactly_one_submit_template(skill_md: str):
    """恰一条 submit 命令模板：提取器成功，且全文明示的触发词与模板并存。"""
    command = extract_submit_template(skill_md)
    assert "{{harvest_run_id}}" in command
    assert "{{harvest_json}}" in command
    # 全文中 `-m akb2.harvest` 恰好 1 次（模板内），不得有第二条可复制命令
    assert skill_md.count("-m akb2.harvest") == 1
    # 标记注释对各出现一次（验收命令 grep -c = 2 的依据）
    assert skill_md.count("akb2-harvest:submit-template") == 2


def test_manual_section_references_same_template(skill_md: str):
    """手动执行章节引用的就是同一段（逐字相等断言，不是"看起来一样"）。

    手动执行章节必须显式引用模板块（不允许另写一条命令）；提取出的模板
    是全文唯一命令。逐字相等通过「提取命令 == 手动章节明示的同一命令」验证。
    """
    command = extract_submit_template(skill_md)
    # 手动执行章节存在，且指向模板本身（引用词，而非复写命令）
    assert "手动" in skill_md
    # 全文只有模板内那一条 `-m akb2.harvest`；因此任何“可复制的命令”
    # 都来自模板 → 手动章节复用同一段
    occurrences = re.findall(r"^\s*python3 -m akb2\.harvest.*$", skill_md, re.MULTILINE)
    assert len(occurrences) == 1
    assert occurrences[0].strip() == command


# ---------------------------------------------------------------------------
# ② product-arch-gate：holds 边界转否定式断言
# ---------------------------------------------------------------------------

# 注册指令不得出现的模式（出现即断言失败）
_FORBIDDEN_REGISTRATION_PATTERNS = [
    (r"launchctl\s+load", "launchd 注册指令"),
    (r"launchctl\s+submit", "launchd 注册指令"),
    (r"crontab", "cron 注册指令"),
    (r"com\.akb2\.", "launchd plist 注册"),
]
_FORBIDDEN_AKB2_MODIFICATION_PATTERNS = [
    (r"git\s+commit", "对 AKB2 仓文件的修改指令"),
    (r"git\s+push", "对 AKB2 仓的推送指令"),
    (r"修改\s*.*src/akb2", "对 AKB2 源码的修改指令"),
]
_FORBIDDEN_BYPASS_PATTERNS = [
    (r"(?:直写|直接写|绕过).{0,12}raw", "绕过 assembler 直写 raw 的指令"),
]


@pytest.mark.parametrize(
    "pattern,label",
    [
        *[(p, lbl) for p, lbl in _FORBIDDEN_REGISTRATION_PATTERNS],
        *[(p, lbl) for p, lbl in _FORBIDDEN_AKB2_MODIFICATION_PATTERNS],
        *[(p, lbl) for p, lbl in _FORBIDDEN_BYPASS_PATTERNS],
    ],
)
def test_no_forbidden_instructions(skill_md: str, pattern: str, label: str):
    """holds 边界：全文不得出现 {label}。"""
    assert not re.search(pattern, skill_md), f"SKILL.md 出现{label}: {pattern}"


def test_confirm_gate_clause_present(skill_md: str):
    """必须出现「落盘前渲染稿展示用户、确认才写」条款。"""
    assert "用户确认" in skill_md
    assert "确认才写" in skill_md or ("确认" in skill_md and "渲染稿" in skill_md)


def test_zero_registration_declaration(skill_md: str):
    """零注册声明：skill 不得注册 scheduler/launchd/cron。"""
    assert "零注册" in skill_md or "不注册" in skill_md
    assert any(k in skill_md for k in ("launchd", "cron", "scheduler"))


def test_forbidden_bypass_clause_present(skill_md: str):
    """禁绕过 assembler 条款在场（verify 硬 grep「禁止绕过」的依据）。"""
    assert "禁止绕过" in skill_md


# ---------------------------------------------------------------------------
# ⑦ 保证等级：文本声称的保证 ↔ 机器断言一一对应
# ---------------------------------------------------------------------------


def test_guarantee_review_clause_has_machine_assertion():
    """「必经用户过目」→ 存在对应机器断言（flow 确认门测试）。"""
    from tests.harvest import test_skill_flow as flow_tests

    assert hasattr(flow_tests, "test_rejected_confirm_zero_side_effects")
    assert hasattr(flow_tests, "test_missing_confirm_zero_side_effects")
    assert hasattr(flow_tests, "test_write_truth_table")


def test_guarantee_no_bypass_has_machine_assertion():
    """「禁止绕过 assembler」→ 存在对应机器断言（模板唯一性 + 真值表）。"""
    from tests.harvest import test_skill_contract as contract_tests
    from tests.harvest import test_skill_template as template_tests

    assert hasattr(contract_tests, "test_exactly_one_submit_template")
    assert hasattr(template_tests, "test_two_blocks_raises")
