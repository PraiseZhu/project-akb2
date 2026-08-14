"""确认闸编排 + 交互状态机（sc-skill-def expect ③④⑤⑥⑧ 契约面）。

模型：
- 三态 proposed → confirmed → submitted；终态 rejected / aborted。
- **只有 confirmed 才能进 submitted**——assembler 在用户确认后才被调用。
- rejected/aborted 两态零 submit、零写盘、零记账。
- 写盘发生 ⟺ confirmed AND assembler 接受（判据合取真值表）。
- harvest_run_id 由调用方给出（试投场景 sentinel），本模块**只透传不生成**
  （GPT 质询 rev16-ch2：自造即两侧 id 不等，恢复锚点永久失效且测试仍绿）。
- 触发词路径与手动复制路径都经 `_resolve_submit_template` 解析（单入口）。

真正的落盘 assembler 由 sc-asm-cli 实现（`assemble(payload, *, harvest_run_id,
policy=...)`）；本模块通过注入的 callable 调用它，以便确认门被机器验证
（assembler 零调用 / raw 零新增 / ledger 零记账）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from akb2.harvest.template import extract_submit_template
from akb2.loops.ledger import append_event

# 具名常量（宪法 P4：预算/阈值用具名常量）
LEDGER_LOOP_ID = "akb2-harvest"
LEDGER_NODE = "submit"
LEDGER_EVENT_COMMITTED = "committed"


class HarvestState(IntEnum):
    """交互状态机：三态 + 两终态。

    数值顺序即允许的推进方向（PROPOSED < CONFIRMED < SUBMITTED），
    终态（REJECTED/ABORTED）数值最大、不可再推进。
    """

    PROPOSED = 1
    CONFIRMED = 2
    SUBMITTED = 3
    REJECTED = 4
    ABORTED = 5


class AssemblyRejectedError(RuntimeError):
    """assembler 拒绝（内容准入失败，如 schema/车道校验不过）。

    用户确认不等于内容准入：assembler 是内容准入的唯一裁决方
    （expect ⑧ 判据合取），拒绝时零写盘、零记账，状态回到 proposed
    供修复后重投。
    """


@dataclass(frozen=True)
class HarvestOutcome:
    """一次 run_harvest_flow 的结果。"""

    state: HarvestState
    rendered: str
    submitted: bool = False


# 单入口：触发词路径（skill 运行时）与手动复制路径（用户复制模板）解析到
# 同一个模板提取函数对象（expect ⑥ 身份断言）。
_resolve_submit_template = extract_submit_template

# 确认闸函数类型：接收渲染稿，返回 True（确认）/ False（拒绝）/ None（无确认输入）
ConfirmFn = Callable[[str], bool | None]
# assembler 类型：接收 payload 与必填具名关键字参数 harvest_run_id（真实实现
# sc-asm-cli 的 assemble() 即如此，harvest_run_id 为 keyword-only），返回落位
# 文件名（str，非 Path——Callable[[...], ...] 无法表达 keyword-only 形参，
# 用 ... 如实反映调用约定，避免误导未来把 functools.partial(assemble, ...)
# 接进来时假设返回 Path）。拒绝时抛 AssemblyRejectedError。
AssemblerFn = Callable[..., str]


def render_payload(payload: dict) -> str:
    """渲染稿：把 harvest JSON 转成用户可读的展示文本（落盘前展示）。"""
    title = payload.get("title", "(无标题)")
    lane = payload.get("lane_hint", "unknown")
    n_atoms = len(payload.get("atoms", []))
    n_steps = len(payload.get("how_steps", []))
    n_components = len(payload.get("components", []))
    return (
        f"【harvest 渲染稿】\n"
        f"标题: {title}\n车道: {lane}\n"
        f"atoms: {n_atoms} 段 / how_steps: {n_steps} 条 / components: {n_components} 个\n"
        f"——请确认是否落盘（确认才写）"
    )


def run_harvest_flow(
    payload: dict,
    *,
    harvest_run_id: str,
    raw_dir: Path,
    ledger_path: Path,
    confirm: ConfirmFn,
    assembler: AssemblerFn,
    render: Callable[[dict], str] = render_payload,
) -> HarvestOutcome:
    """执行「渲染稿展示 → 用户确认 → assembler 组装 → ledger 记账」完整流程。

    Args:
        payload: harvest JSON v1（内容准入由 assembler 裁决）。
        harvest_run_id: 调用方给出的归属键，**只透传不生成**。
        raw_dir: raw 输出目录（写盘由 assembler 完成）。
        ledger_path: 账本路径（committed 记录经 loops.ledger.append_event 追加）。
        confirm: 确认闸函数（渲染稿 → True/False/None）。
        assembler: 组装函数（注入；生产环境为 sc-asm-cli 的 assemble）。
        render: 渲染稿函数（默认 render_payload）。

    Returns:
        HarvestOutcome。状态机保证：rejected/aborted 零 submit、零写盘、
        零记账；submitted 仅当 confirmed AND assembler 接受。
    """
    rendered = render(payload)

    # 确认闸：用户确认是状态变更的唯一闸（expect ⑧）
    decision = confirm(rendered)
    if decision is False:
        return HarvestOutcome(state=HarvestState.REJECTED, rendered=rendered)
    if decision is None:
        return HarvestOutcome(state=HarvestState.ABORTED, rendered=rendered)
    # decision is True → PROPOSED → CONFIRMED

    # 内容准入：assembler schema 是唯一裁决方（expect ⑧）
    try:
        assembler(payload, harvest_run_id=harvest_run_id)
    except AssemblyRejectedError:
        return HarvestOutcome(state=HarvestState.PROPOSED, rendered=rendered)

    # ledger 记账（幂等追加；重复行自动跳过）
    append_event(
        str(ledger_path),
        {
            "loop_id": LEDGER_LOOP_ID,
            "node": LEDGER_NODE,
            "event": LEDGER_EVENT_COMMITTED,
            "harvest_run_id": harvest_run_id,
            "content_hash": _content_hash(payload),
            "ts": _epoch_ts(),
        },
    )
    return HarvestOutcome(
        state=HarvestState.SUBMITTED, rendered=rendered, submitted=True
    )


def _content_hash(payload: dict) -> str:
    """payload 规范化 JSON 的 SHA-256（幂等去重 + 归属键校验用）。"""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _epoch_ts() -> int:
    """当前时间戳（具名函数便于测试注入/断言）。"""
    import time

    return int(time.time())
