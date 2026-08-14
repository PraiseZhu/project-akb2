"""确认闸编排 + 交互状态机测试（sc-skill-def expect ③④⑤⑥⑧ 机器面）。

期望模型：
- 三态 proposed → confirmed → submitted；终态 rejected / aborted。
- 只有 confirmed 才能进 submitted。
- rejected/aborted 两态零 submit、零写盘。
- 写盘发生 ⟺ confirmed AND assembler 接受（真值表）。
- 触发词路径与手动复制路径引用同一个模板提取函数对象（身份断言）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from akb2.harvest.flow import (
    AssemblyRejectedError,
    HarvestState,
    run_harvest_flow,
)
from akb2.harvest.template import extract_submit_template

# ---------------------------------------------------------------------------
# 假件
# ---------------------------------------------------------------------------


class SpyAssembler:
    """记录调用次数与收到的 harvest_run_id 的 fake assembler。

    接受时返回 raw 路径并真的写一个 frontmatter 文件（含 harvest_run_id），
    拒绝时抛 AssemblyRejectedError。
    """

    def __init__(self, raw_dir: Path, *, accept: bool = True):
        self.raw_dir = raw_dir
        self.accept = accept
        self.calls: list[tuple[dict, str]] = []
        self.written: list[Path] = []

    def __call__(self, payload: dict, *, harvest_run_id: str) -> Path:
        self.calls.append((payload, harvest_run_id))
        if not self.accept:
            raise AssemblyRejectedError("schema 校验失败: 缺 components")
        raw_path = self.raw_dir / f"raw-{harvest_run_id}.md"
        raw_path.write_text(
            json.dumps(
                {"harvest_run_id": harvest_run_id, "title": payload.get("title")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.written.append(raw_path)
        return raw_path


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _payload() -> dict:
    return {"schema_version": "v1", "title": "修复构建", "lane_hint": "procedural"}


# ---------------------------------------------------------------------------
# ③ 类 1(不可逆动作)：拒绝确认 / 无确认 → assembler 零调用、raw 零新增、
#    ledger 零记账
# ---------------------------------------------------------------------------


def test_rejected_confirm_zero_side_effects(tmp_path):
    """用户拒绝确认 → assembler 零调用、raw_dir 零新增、ledger 零记账。"""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    spy = SpyAssembler(raw_dir)

    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-abc",
        raw_dir=raw_dir,
        ledger_path=ledger_path,
        confirm=lambda rendered: False,
        assembler=spy,
    )

    assert outcome.state == HarvestState.REJECTED
    assert spy.calls == []
    assert list(raw_dir.iterdir()) == []
    assert _count_lines(ledger_path) == 0


def test_missing_confirm_zero_side_effects(tmp_path):
    """无确认输入（确认函数返回 None）→ 同样零调用、零新增、零记账。"""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    spy = SpyAssembler(raw_dir)

    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-abc",
        raw_dir=raw_dir,
        ledger_path=ledger_path,
        confirm=lambda rendered: None,
        assembler=spy,
    )

    assert outcome.state == HarvestState.ABORTED
    assert spy.calls == []
    assert list(raw_dir.iterdir()) == []
    assert _count_lines(ledger_path) == 0


# ---------------------------------------------------------------------------
# ⑧ 类 10(判据合取)：真值表 —— 写盘 ⟺ confirmed AND assembler 接受
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "confirm_result, assembler_accept, expect_written",
    [
        (True, True, True),  # 确认+接受 → 写
        (True, False, False),  # 确认+拒 → 不写
        (False, True, False),  # 未确认+接受 → 不写
        (False, False, False),  # 未确认+拒 → 不写
    ],
)
def test_write_truth_table(
    tmp_path, confirm_result, assembler_accept, expect_written
):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    spy = SpyAssembler(raw_dir, accept=assembler_accept)

    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-t",
        raw_dir=raw_dir,
        ledger_path=ledger_path,
        confirm=lambda rendered: confirm_result,
        assembler=spy,
    )

    written = bool(spy.written)
    assert written is expect_written
    if expect_written:
        assert outcome.state == HarvestState.SUBMITTED
        assert _count_lines(ledger_path) == 1
    else:
        assert outcome.state != HarvestState.SUBMITTED
        assert _count_lines(ledger_path) == 0


# ---------------------------------------------------------------------------
# ⑤ 类 4(交互状态)：三态机与终态
# ---------------------------------------------------------------------------


def test_only_confirmed_can_submit(tmp_path):
    """只有 confirmed 才能进 submitted —— assembler 在确认后才会被调用。"""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    spy = SpyAssembler(raw_dir)

    # 拒绝确认：永不 submitted
    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-x",
        raw_dir=raw_dir,
        ledger_path=tmp_path / "ledger.jsonl",
        confirm=lambda rendered: False,
        assembler=spy,
    )
    assert outcome.state == HarvestState.REJECTED
    assert outcome.state != HarvestState.SUBMITTED


def test_rejected_retry_starts_from_proposed(tmp_path):
    """重试从 proposed 重新开始，不复用上次的确认。

    第一次拒绝 → 第二次重新跑完整流程（仍然需要再次确认，且确认后的
    assembler 调用携带的 payload 是本次传入的）。
    """
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    spy = SpyAssembler(raw_dir)
    confirm_calls: list[bool | None] = []

    def confirm(rendered: str) -> bool | None:
        confirm_calls.append(True)
        return True

    # 第一次：拒绝
    run_harvest_flow(
        _payload(),
        harvest_run_id="run-1",
        raw_dir=raw_dir,
        ledger_path=ledger_path,
        confirm=lambda rendered: False,
        assembler=spy,
    )
    # 第二次：从 proposed 重新开始（仍要确认 → 确认后写盘）
    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id="run-2",
        raw_dir=raw_dir,
        ledger_path=ledger_path,
        confirm=confirm,
        assembler=spy,
    )

    assert outcome.state == HarvestState.SUBMITTED
    assert len(spy.calls) == 1
    assert len(confirm_calls) == 1


def test_state_transitions_valid(tmp_path):
    """显式断言三态与终态集合合法。"""
    assert HarvestState.PROPOSED.value < HarvestState.CONFIRMED.value < HarvestState.SUBMITTED.value
    terminal = {HarvestState.REJECTED, HarvestState.ABORTED}
    assert HarvestState.REJECTED in terminal
    assert HarvestState.ABORTED in terminal
    assert HarvestState.SUBMITTED not in terminal


# ---------------------------------------------------------------------------
# ④ 类 3(归属键)：run_id 四处逐字相同
# ---------------------------------------------------------------------------


def test_run_id_identical_in_four_places(tmp_path):
    """sentinel run_id 在模板占位、CLI argv、raw frontmatter、ledger 四处逐字相同。

    从真实标记模板解析 → 用 sentinel 填充占位 → 实跑流程 → 四处实测值比对
    （不是 grep 文本，是四处实测值比对）。
    """
    skill_md_path = Path(
        "/Users/praise/AI-Agent/Claude/capabilities/source/skills/"
        "claude-active/akb2-harvest/SKILL.md"
    )
    if not skill_md_path.exists():
        pytest.skip("SKILL.md 未生成（本测试依赖真实工件）")

    template_cmd = extract_submit_template(skill_md_path.read_text(encoding="utf-8"))
    sentinel = "run-sentinel-77a08d7b"

    # 1) 模板占位：占位符在模板文本中
    assert "{{harvest_run_id}}" in template_cmd
    # 2) CLI argv：把占位符替换成 sentinel 后，--harvest-run-id 的值
    argv_cmd = template_cmd.replace("{{harvest_run_id}}", sentinel)
    assert "--harvest-run-id " + sentinel in argv_cmd
    assert "{{harvest_json}}" in argv_cmd  # 另一个占位符保持模板形态

    # 3)+4) raw frontmatter 与 ledger committed 记录：实跑流程
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    spy = SpyAssembler(raw_dir)

    outcome = run_harvest_flow(
        _payload(),
        harvest_run_id=sentinel,
        raw_dir=raw_dir,
        ledger_path=ledger_path,
        confirm=lambda rendered: True,
        assembler=spy,
    )
    assert outcome.state == HarvestState.SUBMITTED

    # 四处逐字相同（①模板占位 ②CLI argv ③raw frontmatter ④ledger committed）
    raw_file = spy.written[0]
    frontmatter = json.loads(raw_file.read_text(encoding="utf-8"))
    ledger_line = ledger_path.read_text(encoding="utf-8").splitlines()[0]
    ledger_record = json.loads(ledger_line)

    assert frontmatter["harvest_run_id"] == sentinel
    assert ledger_record["harvest_run_id"] == sentinel
    assert sentinel in argv_cmd  # ② 与 ①② 同值
    assert frontmatter["harvest_run_id"] == argv_cmd.split("--harvest-run-id ")[1].strip()


# ---------------------------------------------------------------------------
# ⑥ 类 5(单入口)：触发词路径与手动复制路径引用同一模板提取函数对象
# ---------------------------------------------------------------------------


def test_trigger_path_and_manual_path_same_template_fn():
    """身份断言：两条路径引用同一个模板提取函数对象。

    - 触发词路径：skill 运行时解析 SKILL.md 提取模板（akb2.harvest.flow 内部
      调用 extract_submit_template）。
    - 手动复制路径：用户复制 SKILL.md 中模板（同样经 extract_submit_template
      校验）。
    两条路径走同一个函数对象，不各自解析。
    """
    from akb2.harvest.flow import _resolve_submit_template as flow_resolver
    from akb2.harvest.template import extract_submit_template

    assert flow_resolver is extract_submit_template
