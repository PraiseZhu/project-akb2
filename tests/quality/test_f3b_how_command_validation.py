"""test_f3b_how_command_validation.py — F3-B how_steps 命令确定性校验测试。

覆盖:
    - 编造命令(不在 raw 原文)→ command 剥空 + executable=False
    - 真命令(在 raw 原文 verbatim)→ 保留
    - 危险命令(在 raw 但危险)→ 剥(同 SG-command-recheck 口径)
    - 空 command → 不动
    - procedural 跌破 HOW_STEPS_MIN_EXECUTABLE → _validate_extract_output fail-closed
"""

from __future__ import annotations

import pytest

from src.akb2.quality.distill import (
    HOW_STEPS_MIN_EXECUTABLE,
    _strip_fabricated_how_commands,
    _validate_extract_output,
)

_RAW = """\
## 锤点目录

| §A | x |

---

§A_CONTENT_START

Run the real command `make deploy` to build.

```bash
python -m akb2.quality.runner --once
```
"""


def _step(command: str, executable: bool = True, instruction: str = "do") -> dict:
    return {"instruction": instruction, "command": command, "executable": executable}


class TestStripFabricatedHowCommands:
    def test_fabricated_command_stripped(self):
        """编造命令(不在 raw)→ command 剥空 + executable=False。"""
        data = {"how_steps": [_step("；claude/rules/")]}  # 全角分号垃圾片段(probe-p2 memory 死因)
        _strip_fabricated_how_commands(data, _RAW)
        assert data["how_steps"][0]["command"] == ""
        assert data["how_steps"][0]["executable"] is False

    def test_real_command_preserved(self):
        """真命令(在 raw verbatim)→ 保留 command + executable 不变。"""
        data = {"how_steps": [_step("make deploy")]}
        _strip_fabricated_how_commands(data, _RAW)
        assert data["how_steps"][0]["command"] == "make deploy"
        assert data["how_steps"][0]["executable"] is True

    def test_real_command_with_backticks_in_raw_preserved(self):
        """raw 里 `python -m akb2.quality.runner --once` 命令(command 含路径)→ 保留。"""
        data = {"how_steps": [_step("python -m akb2.quality.runner --once")]}
        _strip_fabricated_how_commands(data, _RAW)
        assert data["how_steps"][0]["command"] == "python -m akb2.quality.runner --once"

    def test_dangerous_command_stripped(self):
        """危险命令(如 rm -rf /,即便在 raw)→ 剥(同 SG-command-recheck 口径)。"""
        raw_with_dangerous = "run rm -rf / to clean\n"
        data = {"how_steps": [_step("rm -rf /")]}
        _strip_fabricated_how_commands(data, raw_with_dangerous)
        assert data["how_steps"][0]["command"] == ""
        assert data["how_steps"][0]["executable"] is False

    def test_empty_command_not_touched(self):
        """空 command → 不动(无字段可校)。"""
        data = {"how_steps": [_step("", executable=False)]}
        _strip_fabricated_how_commands(data, _RAW)
        assert data["how_steps"][0]["command"] == ""

    def test_no_raw_content_noop(self):
        """无 raw_content → 不动(fail-closed 不造假)。"""
        data = {"how_steps": [_step("make deploy")]}
        _strip_fabricated_how_commands(data, None)
        assert data["how_steps"][0]["command"] == "make deploy"  # 未改


class TestProceduralDropsBelowMinExecutableFailClosed:
    def test_procedural_below_min_executable_fails(self):
        """procedural lane 编造命令被剥后 executable<3 → _validate_extract_output fail-closed。"""
        # 3 步全编造(不在 raw)→ 剥后全 executable=False → 0 < HOW_STEPS_MIN_EXECUTABLE(3)
        data = {
            "how_steps": [
                _step("；fake1"), _step("；fake2"), _step("；fake3"),
            ],
            "claims": [
                {"text": "t", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [],
            "mechanism_components": [],
            "causal_edges": [],
            "params": {},
            "referenced_concepts": [],
            "tldr": "满足 schema 的最小 extract fixture 用于测 procedural how 可执行下限。",
            "applicability_boundary": "适用边界:本 fixture 仅用于可执行下限测试,非真实内容。",
            "graph_edges": [],
        }
        _strip_fabricated_how_commands(data, _RAW)
        # 全剥后 0 executable
        assert sum(1 for s in data["how_steps"] if s.get("executable")) == 0
        # procedural validate 拒(executable < MIN)
        with pytest.raises(ValueError, match="executable|可执行"):
            _validate_extract_output(data, content_lane="procedural")

    def test_procedural_with_real_commands_passes(self):
        """procedural lane 真命令保留 → executable≥3 → validate 过。"""
        raw = "make deploy\npython -m akb2.quality.runner --once\nmake test\n"
        data = {
            "how_steps": [
                _step("make deploy"), _step("python -m akb2.quality.runner --once"),
                _step("make test"),
            ],
            "claims": [
                {"text": "t", "anchor": "§A", "provenance": "source_grounded",
                 "evidence_gap": "未提供 X"},
            ],
            "argumentation": [
                {"claim_anchor": "§A",
                 "explanation": "A 与 B 的因果链路构成反馈循环的核心机制说明。",
                 "provenance": "akb_interpretation"},
            ],
            "mechanism_components": [],
            "causal_edges": [],
            "params": {},
            "referenced_concepts": [],
            "tldr": "满足 schema 的最小 extract fixture 用于测 procedural how 可执行下限。",
            "applicability_boundary": "适用边界:本 fixture 仅用于可执行下限测试,非真实内容。",
            "graph_edges": [],
        }
        _strip_fabricated_how_commands(data, raw)
        # 真命令保留 → 3 executable
        assert sum(1 for s in data["how_steps"] if s.get("executable")) >= HOW_STEPS_MIN_EXECUTABLE
        _validate_extract_output(data, content_lane="procedural")  # 不 raise
