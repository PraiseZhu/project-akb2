"""harvest 投递账本事务化(sc-asm-ledger)
—— CONTENT/DESTNAME/RUN_ID 三锁 + pending/committed/reconcile。

公开入口:
- ``content_key(session_id, content_fingerprint) -> str`` — CONTENT 锁键派生
- ``resolve_ledger_path() -> Path`` — 账本路径(经 resolve_state_file)
- ``write_pending(ledger_path, *, run_id, ...) -> dict`` — 写 pending 行 + 预约 RUN_ID/CONTENT
- ``write_committed(ledger_path, *, run_id, ...) -> dict`` — 写 committed 行 + 清预约
- ``lookup(ledger_path, run_id) -> dict | None`` — 按 run_id 查最新行
- ``reconcile(ledger_path) -> list[dict]`` — pending 残留 reconcile

高层面:
- ``run_harvest_with_ledger(ledger_path, *, run_id, ..., assemble_fn) -> dict`` — 含锁编排的完整流程

信任模型:
- 三把独立 O_EXCL 预约(CONTENT/DESTNAME/RUN_ID)经 ``locks.reserve`` 实现;
- 账本行以 O_APPEND + fsync 写入,首次创建 fsync 父目录;
- 同键 committed 拒绝重复;仅同一 pending 可幂等恢复。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
from akb2.harvest.locks import (
    CONTENT,
    DESTNAME,
    RUN_ID,
    Reservation,
    ReservationHeld,
    reserve,
)

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

LEDGER_FILENAME = "harvest-ledger.jsonl"

# 退出码(与 assembler.py 对齐)
EXIT_DESTNAME_CONFLICT = 4
EXIT_ALREADY_COMMITTED = 5  # 已 committed 且不可幂等恢复
EXIT_RUN_ID_CONFLICT = 6    # 同 run_id 但内容不同,硬拒绝

# 重试参数
MAX_RETRIES = 3
RETRY_DELAY_SEC = 0.1

# 单行账本记录字节上限(GPT 质询 rev45-ch4):超限即 fail-closed,宁可不写
# 也不写半行——普通缓冲写可能被拆成多次系统调用,行边界在并发 append 下
# 交织或截断;把单行钉在此上限内是让单次 os.write 保持整行原子的前提。
MAX_LEDGER_LINE_BYTES = 8192

# 事件类型常量
EVENT_PENDING = "pending"
EVENT_COMMITTED = "committed"
EVENT_RECONCILE_CONFLICT = "reconcile_conflict"
EVENT_RECONCILE_COMPLETED = "reconcile_completed"
EVENT_DESTNAME_CONFLICT = "destname_conflict"
EVENT_RUN_ID_CONFLICT = "run_id_conflict"

# 共识字段(event 无关,出现在所有行)
_COMMON_FIELDS = frozenset({
    "event", "harvest_run_id", "session_id", "content_fingerprint",
    "content_key", "dest_filename", "ts",
})
# committed 专属字段
_COMMITTED_FIELDS = frozenset({"raw_abspath", "pipeline_content_hash"})


# ---------------------------------------------------------------------------
# 内容指纹 → CONTENT 锁键
# ---------------------------------------------------------------------------


def content_key(session_id: str, content_fingerprint: str) -> str:
    """CONTENT 锁键 = sha256(session_id + content_fingerprint)。

    归一化:session_id 与 content_fingerprint 均为小写,去两端空白后拼接。
    """
    raw = (session_id.strip().lower() + content_fingerprint.strip().lower())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 账本路径
# ---------------------------------------------------------------------------


def resolve_ledger_path() -> Path:
    """账本路径(经 resolve_state_file,禁止手写 ``<repo>/history/...``)。"""
    return resolve_state_file(LEDGER_FILENAME, subdir=LEDGER_SUBDIR)


# ---------------------------------------------------------------------------
# 账本行读写
# ---------------------------------------------------------------------------


def _now_ts() -> int:
    """当前时间戳(具名函数便于测试注入/断言)。"""
    return int(time.time())


def _jsonl_line(entry: dict) -> str:
    """dict → 单行 JSONL(规范化 sort_keys,尾部换行)。"""
    return json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"


def _parent_fsync(path: Path) -> None:
    """首次创建后 fsync 父目录。"""
    parent_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _append_line(ledger_path: str, entry: dict) -> None:
    """O_APPEND 追加一行 JSONL + fsync。首次创建时 fsync 父目录。

    单行字节数超过 ``MAX_LEDGER_LINE_BYTES`` 即 fail-closed 拒写(宁可不写
    也不写半行);写入本身仍是单次 ``os.write``,不跨多次系统调用拆行,
    以保证并发 append 下行边界不交织。
    """
    path = Path(ledger_path)
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _jsonl_line(entry)
    line_bytes = line.encode("utf-8")
    if len(line_bytes) > MAX_LEDGER_LINE_BYTES:
        raise ValueError(
            f"账本行超长({len(line_bytes)} > {MAX_LEDGER_LINE_BYTES} 字节),"
            f"拒绝写入: {ledger_path}"
        )
    # 有限重试(宪法 P1)
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(path, "a", encoding="utf-8") as fh:
                os.write(fh.fileno(), line_bytes)
                os.fsync(fh.fileno())
            if is_new:
                _parent_fsync(path)
            return
        except OSError as exc:
            last_err = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SEC)
    raise OSError(
        f"账本写入失败(重试 {MAX_RETRIES} 次后): {ledger_path}"
    ) from last_err


def _read_ledger(path: Path) -> list[dict]:
    """读取全部 JSONL 行(跳过空行)。"""
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ---------------------------------------------------------------------------
# 查找
# ---------------------------------------------------------------------------


def lookup(ledger_path: str, run_id: str) -> dict | None:
    """按 harvest_run_id 查最新一条事件行(含所有类型,最晚 ts 优先)。"""
    path = Path(ledger_path)
    rows = _read_ledger(path)
    for row in reversed(rows):
        if row.get("harvest_run_id") == run_id:
            return row
    return None


def _last_committed_for_run_id(rows: list[dict], run_id: str) -> dict | None:
    """从已读行中找 run_id 的最后一条 committed 行。"""
    for row in reversed(rows):
        if row.get("harvest_run_id") == run_id and row.get("event") == EVENT_COMMITTED:
            return row
    return None


def _last_pending_for_run_id(rows: list[dict], run_id: str) -> dict | None:
    """从已读行中找 run_id 的最后一条 pending 行。"""
    for row in reversed(rows):
        if row.get("harvest_run_id") == run_id and row.get("event") == EVENT_PENDING:
            return row
    return None


def _find_pending_by_content(
    rows: list[dict], content_key: str, dest_filename: str
) -> dict | None:
    """按 content_key + dest_filename 找未对应 committed 的 pending 行。"""
    # 找所有 committed 行
    committed_keys: set[str] = set()
    committed_dests: set[str] = set()
    for r in rows:
        if r.get("event") == EVENT_COMMITTED:
            committed_keys.add(r.get("content_key", ""))
            committed_dests.add(r.get("dest_filename", ""))
    # 找 pending 行,排除已 committed 的
    for r in rows:
        if r.get("event") != EVENT_PENDING:
            continue
        if r.get("content_key") != content_key:
            continue
        if r.get("dest_filename") != dest_filename:
            continue
        if r.get("content_key") in committed_keys and r.get("dest_filename") in committed_dests:
            continue
        return r
    return None


# ---------------------------------------------------------------------------
# 写 pending
# ---------------------------------------------------------------------------


def write_pending(
    ledger_path: str,
    *,
    run_id: str,
    session_id: str,
    content_fingerprint: str,
    dest_filename: str,
) -> dict:
    """写 pending 行 + 预约 RUN_ID/CONTENT。

    步骤:
    1. 查 ledger 中 run_id 是否已有 committed 行(已 committed 同 id 硬拒绝,
       除非是同一 pending 可恢复);
    2. 预约 RUN_ID(锁键 = run_id 自身);
    3. 预约 CONTENT(锁键 = content_key(session_id, content_fingerprint));
    4. 追加 pending 行到 JSONL。

    Returns:
        dict: {"outcome": "pending", "entry": {已写入行}}。

    Raises:
        ReservationHeld: RUN_ID 或 CONTENT 已被持有。
        ValueError: 输入参数不合法。
    """
    # 1. 查是否已 committed
    path = Path(ledger_path)
    rows = _read_ledger(path)
    existing = _last_committed_for_run_id(rows, run_id)
    if existing is not None:
        # 已 committed 同 id — 检查是否同一 pending(幂等恢复)
        pend = _last_pending_for_run_id(rows, run_id)
        if pend is not None:
            same = (
                pend.get("content_fingerprint") == content_fingerprint
                and pend.get("session_id") == session_id
                and pend.get("dest_filename") == dest_filename
            )
            if same:
                # 同一 pending,允许恢复
                return {"outcome": "recovered_pending", "entry": pend}
        # 硬拒绝
        raise ReservationHeld(
            f"run_id {run_id} 已 committed(内容不同),硬拒绝")

    # 1b. 未 committed 时,若已有另一条不同身份的 pending → 一律拒绝。
    #     RUN_ID 预约随本函数返回即释放,唯一持久防线是账本里已存在的
    #     pending 行本身;同一身份幂等放行,不同身份必须硬拒绝,否则同一
    #     run_id 可挂多条互不相干的 pending(GPT 质询 rev27-ch7)。
    other_pending = _last_pending_for_run_id(rows, run_id)
    if other_pending is not None:
        same = (
            other_pending.get("content_fingerprint") == content_fingerprint
            and other_pending.get("session_id") == session_id
            and other_pending.get("dest_filename") == dest_filename
        )
        if same:
            return {"outcome": "recovered_pending", "entry": other_pending}
        raise ReservationHeld(
            f"run_id {run_id} 已存在另一条 pending(身份不同),硬拒绝")

    # 2. 预约 RUN_ID
    ck = content_key(session_id, content_fingerprint)
    with reserve(RUN_ID, run_id):
        # 3. 预约 CONTENT
        with reserve(CONTENT, ck):
            # 4. 写 pending 行
            entry = {
                "event": EVENT_PENDING,
                "harvest_run_id": run_id,
                "session_id": session_id,
                "content_fingerprint": content_fingerprint,
                "content_key": ck,
                "dest_filename": dest_filename,
                "ts": _now_ts(),
            }
            _append_line(ledger_path, entry)
    return {"outcome": "pending", "entry": entry}


# ---------------------------------------------------------------------------
# 写 committed
# ---------------------------------------------------------------------------


def write_committed(
    ledger_path: str,
    *,
    run_id: str,
    session_id: str,
    content_fingerprint: str,
    dest_filename: str,
    raw_abspath: str,
    destname_reservation: Reservation,
) -> dict:
    """写 committed 行 + 清预约。

    DESTNAME 预约由调用方传入(在 _place 前后由调用方持有),本函数
    在校验后写 committed 行,不自行获取/释放 DESTNAME。

    ``pipeline_content_hash`` = sha256(raw_abspath utf-8)[:16]。

    Returns:
        dict: {"outcome": "created" | "already_committed", "entry": ...}。

    Raises:
        InvalidReservation: DESTNAME 凭证校验失败。
        ValueError: 输入参数不合法。
    """
    # 校验 DESTNAME 凭证
    destname_reservation.assert_valid(DESTNAME, dest_filename)

    # 查是否已 committed
    path = Path(ledger_path)
    rows = _read_ledger(path)
    existing = _last_committed_for_run_id(rows, run_id)
    if existing is not None:
        return {"outcome": "already_committed", "entry": existing}

    # 计算 pipeline_content_hash
    pipeline_hash = hashlib.sha256(raw_abspath.encode("utf-8")).hexdigest()[:16]
    ck = content_key(session_id, content_fingerprint)

    entry = {
        "event": EVENT_COMMITTED,
        "harvest_run_id": run_id,
        "session_id": session_id,
        "content_fingerprint": content_fingerprint,
        "content_key": ck,
        "dest_filename": dest_filename,
        "raw_abspath": raw_abspath,
        "pipeline_content_hash": pipeline_hash,
        "ts": _now_ts(),
    }
    _append_line(ledger_path, entry)
    return {"outcome": "created", "entry": entry}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


def reconcile(ledger_path: str) -> list[dict]:
    """pending 残留 reconcile:自动补 committed 或记冲突。

    扫描账本中所有 pending 行,对每行:
    - 同 content_key 有 committed 行(指纹相等) → 补 committed 行,
      记 ``reconcile_completed``;
    - 同 run_id 有 committed 行但内容指纹不等 → 记 ``reconcile_conflict``;
    - 不在场(无 pending 对应 committed) → 清 pending,允许重投。

    幂等性:每条 pending 行以其自身 ``ts`` 为来源标识写入结果行的
    ``pending_ts`` 字段;重复调用会先收集账本里已存在的
    ``reconcile_completed`` / ``reconcile_conflict`` / ``pending_cleared``
    行引用过的 ``(harvest_run_id, pending_ts)``,跳过已处理的 pending——
    否则每次调用都对同一批残留 pending 重复追加结果行,账本无限增长且
    审计轨迹自相矛盾(同一 pending 出现多条 pending_cleared)。

    Returns:
        list[dict]: 本次调用新产出的 reconcile 动作结果(不含跳过的)。
    """
    path = Path(ledger_path)
    rows = _read_ledger(path)
    results: list[dict] = []

    # 提取所有 committed 的 content_key 集合
    committed_by_content: dict[str, dict] = {}
    for r in rows:
        if r.get("event") == EVENT_COMMITTED:
            key = r.get("content_key", "")
            if key and key not in committed_by_content:
                committed_by_content[key] = r

    # 提取所有 committed 的 run_id 集合
    committed_by_run_id: dict[str, dict] = {}
    for r in rows:
        if r.get("event") == EVENT_COMMITTED:
            rid = r.get("harvest_run_id", "")
            if rid and rid not in committed_by_run_id:
                committed_by_run_id[rid] = r

    # 已处理过的 pending(按来源行 (run_id, ts) 去重;重复调用不得对同一
    # pending 重复产出结果行)
    processed_pending: set[tuple[str, int]] = set()
    for r in rows:
        if r.get("event") not in (
            EVENT_RECONCILE_COMPLETED, EVENT_RECONCILE_CONFLICT, "pending_cleared",
        ):
            continue
        src_ts = r.get("pending_ts")
        if src_ts is None:
            continue
        processed_pending.add((r.get("harvest_run_id", ""), src_ts))

    # 扫描 pending 行
    for r in rows:
        if r.get("event") != EVENT_PENDING:
            continue
        rid = r.get("harvest_run_id", "")
        ck = r.get("content_key", "")
        cf = r.get("content_fingerprint", "")
        ses = r.get("session_id", "")
        dest = r.get("dest_filename", "")
        pending_ts = r.get("ts")

        if (rid, pending_ts) in processed_pending:
            continue  # 已 reconcile 过,幂等跳过

        # 检查是否已有 committed 行处理此 pending
        if rid in committed_by_run_id:
            comm = committed_by_run_id[rid]
            # 指纹相等:已处理,跳过(committed 本身即证据,无需结果行)
            if comm.get("content_fingerprint") == cf:
                continue
            # 指纹不等:冲突
            result = {
                "event": EVENT_RECONCILE_CONFLICT,
                "harvest_run_id": rid,
                "pending_ts": pending_ts,
                "pending_content_fingerprint": cf,
                "committed_content_fingerprint": comm.get("content_fingerprint", ""),
                "ts": _now_ts(),
            }
            _append_line(ledger_path, result)
            results.append(result)
            processed_pending.add((rid, pending_ts))
            continue

        # 同 content_key 有 committed 行(指纹相等)→ 补 committed
        if ck in committed_by_content:
            comm = committed_by_content[ck]
            if comm.get("content_fingerprint") == cf and comm.get("session_id") == ses:
                # 补 committed 行
                completed = {
                    "event": EVENT_RECONCILE_COMPLETED,
                    "harvest_run_id": rid,
                    "pending_ts": pending_ts,
                    "session_id": ses,
                    "content_fingerprint": cf,
                    "content_key": ck,
                    "dest_filename": dest,
                    "raw_abspath": comm.get("raw_abspath", ""),
                    "pipeline_content_hash": comm.get("pipeline_content_hash", ""),
                    "ts": _now_ts(),
                    "reconciled_from_pending": True,
                }
                _append_line(ledger_path, completed)
                results.append(completed)
                processed_pending.add((rid, pending_ts))
                continue

        # 不在场:清 pending(允许重投)
        # 单纯记录,不修改已有行
        cleared = {
            "event": "pending_cleared",
            "harvest_run_id": rid,
            "pending_ts": pending_ts,
            "content_key": ck,
            "ts": _now_ts(),
        }
        _append_line(ledger_path, cleared)
        results.append(cleared)
        processed_pending.add((rid, pending_ts))

    return results


# ---------------------------------------------------------------------------
# 高层面:含完整锁编排的 harvest 流程
# ---------------------------------------------------------------------------


def run_harvest_with_ledger(
    ledger_path: str,
    *,
    run_id: str,
    session_id: str,
    content_fingerprint: str,
    dest_filename: str,
    raw_dir: str,
    assemble_fn,
) -> dict:
    """含 ledger 事务的完整 harvest 流程。

    编排:
    1. 查是否已 committed(已 committed 同 id 硬拒绝);
    2. 预约 RUN_ID(必须在 archive_raw 之前);
    3. 预约 CONTENT;
    4. 写 pending 行;
    5. 调 assemble_fn (archive_raw + redaction + 落位前处理);
    6. 预约 DESTNAME(在 _place 前后持有);
    7. 调 assemble_fn 的落位部分(rename);
    8. 写 committed 行;
    9. 返回 outcome。

    Args:
        ledger_path: 账本 JSONL 路径。
        run_id: 调用方传入的 harvest_run_id(UUID4)。
        session_id: 内容所属 session。
        content_fingerprint: 归一化正文指纹。
        dest_filename: 最终 raw 文件名。
        raw_dir: raw 输出目录绝对路径。
        assemble_fn: 可调用对象,执行 archive_raw + redaction + _place 等完整落盘。
            接收 (run_id, dest_filename) 作为参数,返回 dict:
            {"raw_abspath": str, "success": bool, "error": str | None}。
            成功时 raw_abspath 为已落位的绝对路径。

    Returns:
        dict: {"outcome": "created" | "already_committed" | "destname_conflict"
               | "run_id_conflict" | "assemble_failed",
               "raw_abspath": str | None, "hash": str | None,
               "filename": str | None}
    """
    ck = content_key(session_id, content_fingerprint)

    # 1. 查是否已 committed
    path = Path(ledger_path)
    rows = _read_ledger(path)
    existing = _last_committed_for_run_id(rows, run_id)
    if existing is not None:
        # 检查同一 pending 可恢复
        pend = _last_pending_for_run_id(rows, run_id)
        if pend is not None:
            same = (
                pend.get("content_fingerprint") == content_fingerprint
                and pend.get("session_id") == session_id
                and pend.get("dest_filename") == dest_filename
            )
            if same:
                return {
                    "outcome": "already_committed",
                    "raw_abspath": None, "hash": None, "filename": None,
                }
        # 已 committed 同 id 但内容不同 → 硬拒绝
        return {
            "outcome": "run_id_conflict",
            "raw_abspath": None, "hash": None, "filename": None,
        }

    # 1b. 未 committed 时,若已有另一条不同身份的 pending(如 A 崩在 commit
    #     之前)→ 在 archive_raw 之前即拒绝,不得让 B 用同一 run_id、不同
    #     内容继续落盘(GPT 质询 rev27-ch7)。
    other_pending = _last_pending_for_run_id(rows, run_id)
    if other_pending is not None:
        same = (
            other_pending.get("content_fingerprint") == content_fingerprint
            and other_pending.get("session_id") == session_id
            and other_pending.get("dest_filename") == dest_filename
        )
        if not same:
            return {
                "outcome": "run_id_conflict",
                "raw_abspath": None, "hash": None, "filename": None,
            }

    # 2. 预约 RUN_ID
    try:
        run_id_rsv = reserve(RUN_ID, run_id)
    except ReservationHeld:
        return {
            "outcome": "run_id_conflict",
            "raw_abspath": None, "hash": None, "filename": None,
        }

    with run_id_rsv:
        # 3. 预约 CONTENT
        try:
            content_rsv = reserve(CONTENT, ck)
        except ReservationHeld:
            return {
                "outcome": "content_conflict",
                "raw_abspath": None, "hash": None, "filename": None,
            }

        with content_rsv:
            # 4. 复用已存在的同一 pending,避免重试时重复追加。
            entry = other_pending
            if entry is None:
                entry = {
                    "event": EVENT_PENDING,
                    "harvest_run_id": run_id,
                    "session_id": session_id,
                    "content_fingerprint": content_fingerprint,
                    "content_key": ck,
                    "dest_filename": dest_filename,
                    "ts": _now_ts(),
                }
                _append_line(ledger_path, entry)

            # 5. 调 assemble_fn(archive_raw + redaction 等)
            try:
                result = assemble_fn(run_id=run_id, dest_filename=dest_filename)
            except Exception as exc:
                return {
                    "outcome": "assemble_failed",
                    "raw_abspath": None, "hash": None, "filename": None,
                    "error": str(exc),
                }
            if not result.get("success"):
                return {
                    "outcome": "assemble_failed",
                    "raw_abspath": None, "hash": None, "filename": None,
                    "error": result.get("error", "assemble_fn 返回 success=False"),
                }

            raw_abspath = result.get("raw_abspath", "")

            # 6. 预约 DESTNAME(在 _place 前后持有)
            try:
                destname_rsv = reserve(DESTNAME, dest_filename)
            except ReservationHeld:
                # DESTNAME 拿不到 → exit 4
                return {
                    "outcome": "destname_conflict",
                    "raw_abspath": None, "hash": None, "filename": None,
                }

            with destname_rsv:
                # 7. 调落位部分(rename)
                try:
                    place_result = assemble_fn(
                        run_id=run_id, dest_filename=dest_filename, phase="place"
                    )
                except Exception as exc:
                    return {
                        "outcome": "place_failed",
                        "raw_abspath": None, "hash": None, "filename": None,
                        "error": str(exc),
                    }
                if not place_result.get("success"):
                    return {
                        "outcome": "place_failed",
                        "raw_abspath": None, "hash": None, "filename": None,
                        "error": place_result.get("error", "落位失败"),
                    }
                raw_abspath = place_result.get("raw_abspath", raw_abspath)

                # 8. 写 committed 行
                pipeline_hash = hashlib.sha256(
                    raw_abspath.encode("utf-8")).hexdigest()[:16]
                comm_entry = {
                    "event": EVENT_COMMITTED,
                    "harvest_run_id": run_id,
                    "session_id": session_id,
                    "content_fingerprint": content_fingerprint,
                    "content_key": ck,
                    "dest_filename": dest_filename,
                    "raw_abspath": raw_abspath,
                    "pipeline_content_hash": pipeline_hash,
                    "ts": _now_ts(),
                }
                _append_line(ledger_path, comm_entry)

    return {
        "outcome": "created",
        "raw_abspath": raw_abspath,
        "hash": pipeline_hash,
        "filename": dest_filename,
    }
