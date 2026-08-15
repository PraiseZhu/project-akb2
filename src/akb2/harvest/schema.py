"""harvest JSON v1 契约 + 纯函数校验器 — 组装链路的第一道门。

本模块定义组装器消费的 harvest JSON v1 payload 契约，并提供两个同等
fail-closed 的入口：

- ``validate(payload) -> list[str]`` — 纯函数校验器，返回违规清单（空 = 合规）
- ``python3 -m akb2.harvest.schema <file>`` — CLI 入口，违规即 exit 2，零文件产出

契约要点（对齐 sc-asm-schema 定稿）：

- 顶层必填: schema_version / session_id / title / lane_hint / components /
  causal_edges / atoms / how_steps / source_meta
- ``harvest_run_id`` 刻意不入 payload schema —— 它是调用参数（由
  ``--harvest-run-id`` / ``assemble(*, harvest_run_id)`` 强制），不是内容字段；
  若两处都必填，payload 与参数不一致时反而要再定优先级，徒增二源分歧。
- atoms[].kind 枚举固定 6 值；source_meta 为宽松 dict（字段未枚举，不造约束）
- ``session_id`` 必须是小写 canonical UUID4（复用 assembler 的
  ``validate_harvest_run_id`` 同一谓词，不另写第二份正则）；
  ``source_meta.session_id`` 存在时必须与顶层逐字相等（同源闸，缺失不违规）
- 函数入口不因「进程内调用」放行任何 CLI 会拒的输入（硬化类 5）

依赖: 标准库 only（json/argparse），无 LLM、无 IO 副作用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 契约常量 — 单一定义点（sc-skill-fixtures 的阈值复用断言以此为锚）
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1"

# lane 枚举
LANE_CONCEPT = "concept"
LANE_PROCEDURAL = "procedural"
LANE_HINTS = (LANE_CONCEPT, LANE_PROCEDURAL)

# atoms[].kind 枚举（6 值）
ATOM_KINDS = ("command", "output", "error", "diff", "judgment", "context")

# 车道证据阈值（exact 计数，非 subset 启发）
LANE_PROCEDURAL_MIN_HOW_STEPS = 5
LANE_CONCEPT_MIN_COMPONENTS = 3
LANE_CONCEPT_MIN_CAUSAL_EDGES = 1

# CLI exit codes（fail-closed）
CLI_EXIT_OK = 0
CLI_EXIT_INVALID = 2          # schema 违规（缺字段 / 枚举非法）
CLI_EXIT_IO = 2               # 文件不可读 / JSON 非法（同样 fail-closed）
LANE_EVIDENCE_INSUFFICIENT_EXIT = 2  # 声明车道证据不足
LANE_AMBIGUOUS_EXIT = 2              # 两车道证据同时满足 / 同时不满足 → 交人

# ---------------------------------------------------------------------------
# 纯函数校验器 — validate(payload) -> violations: list[str]
# ---------------------------------------------------------------------------


def _nonempty_str(value: Any, label: str) -> list[str]:
    """单值非空字符串检查；返回违规消息列表。"""
    if not isinstance(value, str):
        return [f"{label}: 期望字符串,实际 {type(value).__name__}"]
    if not value.strip():
        return [f"{label}: 期望非空字符串,实际为空"]
    return []


def _load_run_id_predicate():
    """加载 assembler.validate_harvest_run_id（同一函数对象，不复制谓词）。

    assembler 顶层 import 本模块，直接顶层反向 import 构成顺序敏感循环，
    故延迟到调用点。部分子进程入口（scripts/trial_batch.py）只保证
    ``PYTHONPATH=<repo>/src``，而 assembler 顶层的
    ``from src.akb2.quality.intake.raw_archiver import ...`` 要求 repo root
    也在 sys.path——此时首次 import 报 ModuleNotFoundError。这里从本文件
    位置推导 repo root 补进 sys.path 后重试同一 import，仍失败则如实抛出
    （fail-closed，不降级、不写第二份正则兜底）。
    """
    try:
        from akb2.harvest.assembler import validate_harvest_run_id
    except ModuleNotFoundError:
        repo_root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(repo_root))
        from akb2.harvest.assembler import validate_harvest_run_id
    return validate_harvest_run_id


def _validate_session_id(session_id: Any) -> list[str]:
    """session_id 格式闸：小写 canonical UUID4（与 run_id 同一谓词）。

    谓词复用 assembler.validate_harvest_run_id 同一函数对象（不另写第二份
    正则）。非字符串经谓词自身的 isinstance 检查拒绝，消息含期望/实际形态、
    不输出 payload 全文。
    """
    validate_harvest_run_id = _load_run_id_predicate()

    if not validate_harvest_run_id(session_id):
        actual = (
            repr(session_id) if isinstance(session_id, str) else type(session_id).__name__
        )
        return [
            "session_id: 期望小写 canonical UUID4"
            f"(如 4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64),实际 {actual}"
        ]
    return []


def _validate_session_id_same_origin(payload: dict) -> list[str]:
    """同源闸：source_meta.session_id 存在时必须与顶层 session_id 逐字相等。

    键缺失不违规（source_meta 为宽松 dict 契约）；分叉即拒。消息只含两值
    的截断指纹（前 8 字符）而非全文，防 PII 泄漏进违规清单。
    """
    source_meta = payload.get("source_meta")
    if not isinstance(source_meta, dict) or "session_id" not in source_meta:
        return []
    # 顶层 session_id 缺失已由必填字段闸点名，此处不叠加同源违规
    if "session_id" not in payload:
        return []
    top = payload.get("session_id")
    nested = source_meta["session_id"]

    def _fingerprint(value: Any) -> str:
        if isinstance(value, str):
            return f"str:{value[:8]}…"
        return type(value).__name__

    if top != nested:
        return [
            "source_meta.session_id: 期望与顶层 session_id 逐字相等,实际分叉"
            f"(顶层={_fingerprint(top)},source_meta={_fingerprint(nested)})"
        ]
    return []


def _field_type(value: Any, expected: type, label: str) -> list[str]:
    """单值类型检查；返回违规消息列表。"""
    if not isinstance(value, expected):
        return [f"{label}: 期望 {expected.__name__},实际 {type(value).__name__}"]
    return []


def _validate_atoms(atoms: Any) -> list[str]:
    """atoms[] 结构校验：元素必为 dict，id/text 非空，kind 在枚举内。"""
    violations: list[str] = []
    if not isinstance(atoms, list):
        return _field_type(atoms, list, "atoms")
    if not atoms:
        return ["atoms: 期望非空列表,实际为空"]
    for idx, atom in enumerate(atoms):
        label = f"atoms[{idx}]"
        if not isinstance(atom, dict):
            violations.append(f"{label}: 期望对象,实际 {type(atom).__name__}")
            continue
        violations.extend(_nonempty_str(atom.get("id"), f"{label}.id"))
        kind = atom.get("kind")
        if kind not in ATOM_KINDS:
            expected = "|".join(ATOM_KINDS)
            violations.append(
                f"{label}.kind: 期望值 ∈ {{{expected}}},实际 {kind!r}"
            )
        violations.extend(_nonempty_str(atom.get("text"), f"{label}.text"))
    return violations


def _validate_how_steps(how_steps: Any, atoms: Any) -> list[str]:
    """how_steps[] 结构校验：anchor/provenance/cmd/expect 四字段全，cmd 非空。

    另校验 anchor 指向存在的 atom id（锚点可解析性，供下游消费）。
    """
    violations: list[str] = []
    if not isinstance(how_steps, list):
        return _field_type(how_steps, list, "how_steps")
    if not how_steps:
        return ["how_steps: 期望非空列表,实际为空"]
    atom_ids: set[str] = set()
    if isinstance(atoms, list):
        for atom in atoms:
            if isinstance(atom, dict) and isinstance(atom.get("id"), str):
                atom_ids.add(atom["id"])
    for idx, step in enumerate(how_steps):
        label = f"how_steps[{idx}]"
        if not isinstance(step, dict):
            violations.append(f"{label}: 期望对象,实际 {type(step).__name__}")
            continue
        anchor = step.get("anchor")
        violations.extend(_nonempty_str(anchor, f"{label}.anchor"))
        if isinstance(anchor, str) and anchor and anchor not in atom_ids:
            violations.append(
                f"{label}.anchor: 期望指向存在的 atom id,实际 {anchor!r}"
            )
        violations.extend(_nonempty_str(step.get("provenance"), f"{label}.provenance"))
        violations.extend(_nonempty_str(step.get("cmd"), f"{label}.cmd"))
        violations.extend(_nonempty_str(step.get("expect"), f"{label}.expect"))
    return violations


def validate(payload: Any) -> list[str]:
    """harvest JSON v1 纯函数校验器。

    Args:
        payload: 待校验的 harvest JSON（dict）或任意值（非 dict 一律违规）

    Returns:
        违规清单（list[str]）；空列表 = 契约合规。每项消息含期望值+实际值，
        不含 PII（不输出 payload 全文、不输出 session 内容）。
    """
    violations: list[str] = []
    if not isinstance(payload, dict):
        return [f"payload: 期望对象,实际 {type(payload).__name__}"]

    # 顶层必填字段
    required_top = (
        "schema_version",
        "session_id",
        "title",
        "lane_hint",
        "components",
        "causal_edges",
        "atoms",
        "how_steps",
        "source_meta",
    )
    for field in required_top:
        if field not in payload:
            violations.append(f"{field}: 期望字段,实际缺失")

    # schema_version 必须等于契约版本
    sv = payload.get("schema_version")
    if sv != SCHEMA_VERSION:
        violations.append(
            f"schema_version: 期望值 {SCHEMA_VERSION!r},实际 {sv!r}"
        )

    # session_id 格式闸（小写 canonical UUID4，缺字段已点名则不重复报）
    if "session_id" in payload:
        violations.extend(_validate_session_id(payload["session_id"]))

    # 同源闸（source_meta 非法形态由下方结构闸点名，此处只比对可读 dict）
    violations.extend(_validate_session_id_same_origin(payload))

    # lane_hint 枚举
    lane = payload.get("lane_hint")
    if lane not in LANE_HINTS:
        expected = "|".join(LANE_HINTS)
        violations.append(f"lane_hint: 期望值 ∈ {{{expected}}},实际 {lane!r}")

    # 结构字段（缺字段时不重复报类型错，缺字段已点名）。
    # components / causal_edges 必填但允许为空列表：procedural 车道内容天然
    # 没有 concept 证据（components=0、causal_edges=0），若要求非空则合法
    # procedural payload 无法构造，车道判定（sc-asm-lane 的 exact 计数）会
    # 在 components/causal_edges 计数上正常工作（0 视为证据不足）。
    if "components" in payload:
        violations.extend(_field_type(payload["components"], list, "components"))
    if "causal_edges" in payload:
        violations.extend(_field_type(payload["causal_edges"], list, "causal_edges"))
    if "atoms" in payload:
        violations.extend(_validate_atoms(payload["atoms"]))
    if "how_steps" in payload:
        violations.extend(_validate_how_steps(payload["how_steps"], payload.get("atoms")))
    if "source_meta" in payload:
        violations.extend(_field_type(payload["source_meta"], dict, "source_meta"))
        if isinstance(payload["source_meta"], dict) and not payload["source_meta"]:
            violations.append("source_meta: 期望非空对象,实际为空")

    return violations


# ---------------------------------------------------------------------------
# 车道-证据一致性判定 — run_lane_check(payload) -> (ok, exit_code, message)
# ---------------------------------------------------------------------------


def _count_concept_evidence(payload: dict) -> tuple[int, int]:
    """concept 车道证据计数：components 数 + causal_edges 数（exact，不判结构）。"""
    components = payload.get("components")
    causal_edges = payload.get("causal_edges")
    n_components = len(components) if isinstance(components, list) else 0
    n_edges = len(causal_edges) if isinstance(causal_edges, list) else 0
    return n_components, n_edges


def _count_proc_evidence(payload: dict) -> tuple[int, int]:
    """procedural 车道证据计数：how_steps 总数 + cmd 非空的条数。

    非空 cmd 判定：how_steps 为 list 时逐条检查 ``cmd`` 是否为非空字符串；
    非 list 一律计 0。
    """
    how_steps = payload.get("how_steps")
    if not isinstance(how_steps, list):
        return 0, 0
    nonempty = 0
    for step in how_steps:
        cmd = step.get("cmd") if isinstance(step, dict) else None
        if isinstance(cmd, str) and cmd.strip():
            nonempty += 1
    return len(how_steps), nonempty


def run_lane_check(payload: dict) -> tuple[bool, int, str]:
    """车道-证据一致性判定（纯确定性计数，无 LLM）。

    规则（exact 计数，非 subset 启发；不自动改道，歧义一律交人）：

    - lane_hint=procedural → how_steps 总数 ≥ 5 且每条 cmd 非空
    - lane_hint=concept → components ≥ 3 且 causal_edges ≥ 1
    - 声明车道证据不足 → exit 2，点名缺口（期望计数+实际计数）
    - 两车道证据同时满足 / 同时不满足 → exit 2，要求人工改 JSON 后重投

    Args:
        payload: 已通过 schema 校验的 payload（schema 违规应由 validate 先行拦截）

    Returns:
        (ok, exit_code, message)：ok=True 且 exit_code=0 表示判定通过；
        否则 ok=False，exit_code=2，message 含两车道各自的证据计数。
    """
    lane = payload.get("lane_hint")

    n_components, n_edges = _count_concept_evidence(payload)
    n_steps, n_cmd_nonempty = _count_proc_evidence(payload)
    concept_ok = (
        n_components >= LANE_CONCEPT_MIN_COMPONENTS
        and n_edges >= LANE_CONCEPT_MIN_CAUSAL_EDGES
    )
    proc_ok = n_steps >= LANE_PROCEDURAL_MIN_HOW_STEPS and n_cmd_nonempty == n_steps

    # 双满足 / 双不满足 → 歧义交人
    if concept_ok and proc_ok:
        return (
            False,
            LANE_AMBIGUOUS_EXIT,
            "lane 歧义:concept 与 procedural 证据同时满足"
            f"(components={n_components},causal_edges={n_edges},"
            f"how_steps={n_steps}/{n_cmd_nonempty} cmd 非空),"
            "请人工改 JSON 的 lane_hint 或内容后重投",
        )
    if not concept_ok and not proc_ok:
        return (
            False,
            LANE_AMBIGUOUS_EXIT,
            "lane 歧义:concept 与 procedural 证据同时不足"
            f"(components={n_components},causal_edges={n_edges},"
            f"how_steps={n_steps}/{n_cmd_nonempty} cmd 非空),"
            "请人工改 JSON 的 lane_hint 或内容后重投",
        )

    # 声明车道证据不足 → 点名缺口（期望计数+实际计数）
    if lane == LANE_PROCEDURAL:
        gaps: list[str] = []
        if n_steps < LANE_PROCEDURAL_MIN_HOW_STEPS:
            gaps.append(
                f"how_steps 期望 ≥{LANE_PROCEDURAL_MIN_HOW_STEPS} 条,实际 {n_steps} 条"
            )
        if n_cmd_nonempty != n_steps:
            gaps.append(f"cmd 期望全部非空,实际 {n_steps - n_cmd_nonempty} 条为空")
        if gaps:
            return (
                False,
                LANE_EVIDENCE_INSUFFICIENT_EXIT,
                "lane_hint=procedural 证据不足:"
                + ";".join(gaps)
                + f"(components={n_components},causal_edges={n_edges})",
            )
        return True, CLI_EXIT_OK, ""

    if lane == LANE_CONCEPT:
        gaps = []
        if n_components < LANE_CONCEPT_MIN_COMPONENTS:
            gaps.append(
                f"components 期望 ≥{LANE_CONCEPT_MIN_COMPONENTS} 个,实际 {n_components} 个"
            )
        if n_edges < LANE_CONCEPT_MIN_CAUSAL_EDGES:
            gaps.append(
                f"causal_edges 期望 ≥{LANE_CONCEPT_MIN_CAUSAL_EDGES} 条,实际 {n_edges} 条"
            )
        if gaps:
            return (
                False,
                LANE_EVIDENCE_INSUFFICIENT_EXIT,
                "lane_hint=concept 证据不足:"
                + ";".join(gaps)
                + f"(how_steps={n_steps}/{n_cmd_nonempty} cmd 非空)",
            )
        return True, CLI_EXIT_OK, ""

    # lane_hint 非法（schema 违规应已拦截，这里兜底 fail-closed）
    return (
        False,
        LANE_EVIDENCE_INSUFFICIENT_EXIT,
        f"lane_hint: 期望值 ∈ {{{'|'.join(LANE_HINTS)}}},实际 {lane!r}",
    )


def check_lane(payload: dict) -> tuple[bool, int, str]:
    """run_lane_check 的兼容别名（供下游 SC 引用；判定语义完全相同）。"""
    return run_lane_check(payload)


# ---------------------------------------------------------------------------
# CLI — python3 -m akb2.harvest.schema <file>
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：读 harvest JSON → schema + 车道校验 → fail-closed。

    Returns:
        exit code：0 = 通过；2 = 任一校验失败（逐条点名到 stderr，零文件产出）。
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m akb2.harvest.schema",
        description="harvest JSON v1 契约与车道一致性校验（fail-closed，零产出）",
    )
    parser.add_argument("file", help="harvest JSON 文件路径")
    args = parser.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        print(f"harvest schema: JSON 非法: {exc}", file=sys.stderr)
        return CLI_EXIT_IO
    except (OSError, UnicodeDecodeError) as exc:
        print(f"harvest schema: 文件不可读: {args.file} ({exc})", file=sys.stderr)
        return CLI_EXIT_IO

    violations = validate(payload)
    if violations:
        for v in violations:
            print(f"harvest schema: {v}", file=sys.stderr)
        print("harvest schema: 校验失败,零文件产出", file=sys.stderr)
        return CLI_EXIT_INVALID

    ok, code, msg = run_lane_check(payload)
    if not ok:
        print(f"harvest schema: {msg}", file=sys.stderr)
        print("harvest schema: 车道校验失败,零文件产出", file=sys.stderr)
        return code

    print("harvest schema: 校验通过", file=sys.stderr)
    return CLI_EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
