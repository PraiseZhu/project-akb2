"""test_f43_procedural_h1_backfill.py — F43 procedural H1 命令级步骤兜底测试。

覆盖离线复现暴露的 procedural floor-death 根因（dino game pt.4/pt.5 体裁）：
LLM extract 产 how_steps 漏标 how_level=H1（"无 H1 命令级步骤 H1<1"），即便 raw 有
fenced shell 命令（`deno run --tunnel dev`）与 inline backtick 命令（`deno task dev`）。

F43 修法：_backfill_procedural_h1_steps 扫 raw fenced shell 命令 + inline backtick
命令 token，逐字作 how_level=H1 的 how_step 并入 extract_data.how_steps → F4 floor 过。

断言绑定可观测行为：H1 步骤数从 0 → ≥1（F4 floor 过），command 逐字来自 raw，anchor 真实；
JS `export function` 不被误当 shell 命令；无真命令 → fail-closed 不变。
"""

from __future__ import annotations

from src.akb2.quality.distill import _backfill_procedural_h1_steps
from src.akb2.quality.integrity_floor import evaluate_integrity_floor
from src.akb2.quality.raw_anchors import declared_anchor_set

# raw:dino game pt.4 体裁——procedural how-to，fenced 代码块含 shell 命令 + JS 代码（噪声）。
_RAW_DINO_LIKE = """\
## 锚点目录

| §A | x |
| §B | y |
| §C | z |
| §L | w |

---

## §A Intro

Build a leaderboard with Deno.

## §B Steps

Simply use the run command with the `--tunnel` flag.

## §C Run

```
deno run --tunnel dev
```

## §B Code

```ts
export function getDatabase(): Pool {
  return pool;
}
```

Run `deno task dev` locally to start the server.
"""

# raw:无 shell 命令（纯概念说明）→ fail-closed，不造 H1。
_RAW_NO_COMMANDS = """\
## 锚点目录

| §A | x |
| §B | y |

---

## §A Intro

This article explains the concept of loops conceptually.

## §B Theory

A loop repeats work until a condition is met. No commands are run here.
"""


class TestBackfillProceduralH1:
    """_backfill_procedural_h1_steps：fenced shell + inline backtick 命令 → H1 how_step。"""

    def test_fenced_shell_command_extracted_as_h1(self) -> None:
        raw = _RAW_DINO_LIKE
        data: dict = {"how_steps": [], "mechanism_components": [], "causal_edges": [], "graph_edges": []}
        diag = _backfill_procedural_h1_steps(data, raw)
        assert diag["commands_found"] >= 1
        assert diag["h1_added"] >= 1
        h1_steps = [s for s in data["how_steps"] if s.get("how_level") == "H1"]
        assert h1_steps, "expected ≥1 H1 how_step after backfill"
        # fenced 命令须逐字捕获
        cmds = [s.get("command", "") for s in h1_steps]
        assert any("deno run --tunnel dev" in c for c in cmds), f"fenced cmd missing: {cmds}"

    def test_inline_backtick_command_extracted_as_h1(self) -> None:
        raw = _RAW_DINO_LIKE
        data: dict = {"how_steps": [], "mechanism_components": [], "causal_edges": [], "graph_edges": []}
        _backfill_procedural_h1_steps(data, raw)
        cmds = [s.get("command", "") for s in data["how_steps"] if s.get("how_level") == "H1"]
        assert any("deno task dev" in c for c in cmds), f"inline cmd missing: {cmds}"

    def test_js_export_function_not_matched_as_shell(self) -> None:
        """JS `export function` / `return ctx` 不被误当 shell 命令（F43 prototype 噪声）。"""
        raw = _RAW_DINO_LIKE
        data: dict = {"how_steps": [], "mechanism_components": [], "causal_edges": [], "graph_edges": []}
        _backfill_procedural_h1_steps(data, raw)
        cmds = [s.get("command", "") for s in data["how_steps"] if s.get("how_level") == "H1"]
        # 不得把 JS export 语句当 H1 命令
        assert not any(c.startswith("export function") for c in cmds), f"JS noise leaked: {cmds}"
        assert not any(c.startswith("return") for c in cmds), f"JS noise leaked: {cmds}"

    def test_h1_step_has_real_anchor_and_verbatim(self) -> None:
        raw = _RAW_DINO_LIKE
        anchors = declared_anchor_set(raw)
        data: dict = {"how_steps": [], "mechanism_components": [], "causal_edges": [], "graph_edges": []}
        _backfill_procedural_h1_steps(data, raw)
        for s in data["how_steps"]:
            if s.get("how_level") == "H1":
                anchor = s.get("anchor", "")
                assert anchor in anchors, f"H1 step anchor {anchor!r} not in real raw anchors"
                assert s.get("provenance") == "source_grounded"
                assert s.get("command"), "H1 step must carry verbatim command"

    def test_no_commands_fail_closed(self) -> None:
        """无 shell 命令 → 0 H1 加 → floor 仍 fail（不造假蒙混）。"""
        raw = _RAW_NO_COMMANDS
        data: dict = {"how_steps": [], "mechanism_components": [], "causal_edges": [], "graph_edges": []}
        diag = _backfill_procedural_h1_steps(data, raw)
        assert diag["h1_added"] == 0
        assert data["how_steps"] == []

    def test_floor_passes_after_backfill(self) -> None:
        """端到端：procedural F4 floor 修前 fail（H1<1）→ 修后 H1≥1 过。"""
        raw = _RAW_DINO_LIKE
        anchors = declared_anchor_set(raw)
        # 给 claims 让 claim_anchor_rate/evidence_honesty 不干扰（聚焦 F4 H1 断言）
        data: dict = {
            "how_steps": [],
            "mechanism_components": [],
            "causal_edges": [],
            "graph_edges": [],
            "claims": [
                {"text": "Build a leaderboard with Deno.", "anchor": "§A", "provenance": "source_grounded"},
            ],
            "argumentation": [],
        }
        floor_before = evaluate_integrity_floor(data, "procedural", raw_anchors=anchors)
        assert "core_value_f4" in floor_before.failed_floors  # 修前 H1<1 fail

        _backfill_procedural_h1_steps(data, raw)
        floor_after = evaluate_integrity_floor(data, "procedural", raw_anchors=anchors)
        assert "core_value_f4" not in floor_after.failed_floors  # 修后 H1≥1 过

    def test_backfill_idempotent_and_dedup(self) -> None:
        """重复跑不重复加同命令；已含的命令不重复补。"""
        raw = _RAW_DINO_LIKE
        data: dict = {"how_steps": [], "mechanism_components": [], "causal_edges": [], "graph_edges": []}
        _backfill_procedural_h1_steps(data, raw)
        first_count = len(data["how_steps"])
        # 再跑一次
        _backfill_procedural_h1_steps(data, raw)
        assert len(data["how_steps"]) == first_count, "backfill must dedup, not double-add"
