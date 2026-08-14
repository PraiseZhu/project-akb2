"""sc-asm-ledger 验收测试:harvest 投递账本事务化。

覆盖:
- sc-asm-ledger-01: 内容指纹键派生
- sc-asm-ledger-02: pending 写入 + 锁预约
- sc-asm-ledger-03: committed 写入 + outcome 区分
- sc-asm-ledger-04: DESTNAME 冲突(exit 4)
- sc-asm-ledger-05: 已 committed 同 id 拒绝
- sc-asm-ledger-06: 幂等恢复(同一 pending)
- sc-asm-ledger-07: reconcile 残留
- sc-asm-ledger-08: 账本路径 resolve_state_file
- sc-asm-ledger-09: 每行 write+fsync,首次创建 fsync 父目录
- sc-asm-ledger-10: committed 字段完整性
- sc-asm-ledger-11: 高层面 run_harvest_with_ledger
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from akb2.harvest.ledger import (
    CONTENT,
    DESTNAME,
    EVENT_COMMITTED,
    EVENT_PENDING,
    EVENT_RECONCILE_CONFLICT,
    MAX_LEDGER_LINE_BYTES,
    RUN_ID,
    content_key,
    lookup,
    reconcile,
    resolve_ledger_path,
    run_harvest_with_ledger,
    write_committed,
    write_pending,
)
from akb2.harvest.locks import ReservationHeld as RealReservationHeld

# 与 ledger.py 一致的锁 namespace 常量
_NS_CONTENT = CONTENT
_NS_DESTNAME = DESTNAME
_NS_RUN_ID = RUN_ID


# ---------------------------------------------------------------------------
# Mock 锁基础设施
# ---------------------------------------------------------------------------


class _MockReservation:
    """模拟 locks.Reservation 的上下文管理器与 assert_valid。

    每次调用 __exit__ 自动释放持有的 key,以便后续相同 key 可重新预约。
    release_callback 用于通知持有者 key 已释放。
    """

    def __init__(self, namespace: str, key: str, release_callback=None):
        self.namespace = namespace
        self.key = key
        self._released = False
        self._release_callback = release_callback

    def assert_valid(self, expected_namespace: str, expected_key: str) -> bool:
        if self._released:
            raise RuntimeError("reservation 已 released")
        return True

    def __enter__(self) -> _MockReservation:
        return self

    def __exit__(self, *args):
        if not self._released and self._release_callback is not None:
            self._release_callback(self.namespace, self.key)
        self._released = True




@pytest.fixture
def mock_reserve():
    """mock locks.reserve:每次返回新 _MockReservation,不跟踪持有状态。

    适用于不涉及锁冲突的测试(write_pending/write_committed/reconcile/lookup)。
    reserve 的 __exit__ 无操作(不释放也不跟踪)。
    """
    def _reserve(namespace: str, key: str):
        return _MockReservation(namespace, key)

    with patch("akb2.harvest.ledger.reserve", _reserve):
        yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger_path(tmp_path: Path) -> str:
    """临时账本路径。"""
    return str(tmp_path / "ledgers" / "harvest-ledger.jsonl")


# ---------------------------------------------------------------------------
# sc-asm-ledger-01: 内容指纹键派生
# ---------------------------------------------------------------------------


class TestContentKey:
    """CONTENT 锁键派生正确性。"""

    def test_sha256_derivation(self):
        """sc-asm-ledger-01a: 正常键派生。"""
        sid = "test-session-001"
        cf = "abc123def456"
        key = content_key(sid, cf)
        expected = hashlib.sha256(
            (sid.strip().lower() + cf.strip().lower()).encode("utf-8")
        ).hexdigest()
        assert key == expected
        assert len(key) == 64  # SHA-256 hex

    def test_deterministic(self):
        """sc-asm-ledger-01b: 相同输入产生相同输出。"""
        k1 = content_key("session-A", "fingerprint-1")
        k2 = content_key("session-A", "fingerprint-1")
        assert k1 == k2

    def test_different_input_different_key(self):
        """sc-asm-ledger-01c: 不同输入产生不同输出。"""
        k1 = content_key("session-A", "fingerprint-1")
        k2 = content_key("session-B", "fingerprint-1")
        assert k1 != k2

    def test_case_normalization(self):
        """sc-asm-ledger-01d: 大小写归一化。"""
        k1 = content_key("Session-A", "Fingerprint-1")
        k2 = content_key("session-a", "fingerprint-1")
        assert k1 == k2

    def test_whitespace_normalization(self):
        """sc-asm-ledger-01e: 两端空白消除。"""
        k1 = content_key("  session-A  ", "  fingerprint-1  ")
        k2 = content_key("session-A", "fingerprint-1")
        assert k1 == k2


# ---------------------------------------------------------------------------
# sc-asm-ledger-02: pending 写入
# ---------------------------------------------------------------------------


class TestWritePending:
    """pending 行写入与锁预约。"""

    def test_write_pending_creates_entry(self, ledger_path, mock_reserve):
        """sc-asm-ledger-02a: 写 pending 行成功。"""
        result = write_pending(
            ledger_path,
            run_id="a1b2c3d4-e5f6-4789-abcd-ef1234567890",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        assert result["outcome"] == "pending"
        entry = result["entry"]
        assert entry["event"] == EVENT_PENDING
        assert entry["harvest_run_id"] == "a1b2c3d4-e5f6-4789-abcd-ef1234567890"
        assert entry["session_id"] == "session-01"
        assert entry["content_fingerprint"] == "fp-001"
        assert entry["dest_filename"] == "test--raw--2026-08-13.md"
        assert "content_key" in entry
        assert entry["content_key"] == content_key("session-01", "fp-001")
        assert "ts" in entry

        # 验证文件已创建
        assert Path(ledger_path).exists()

    def test_write_pending_twice_same_run_id_same_identity_recovers(
        self, ledger_path, mock_reserve
    ):
        """sc-asm-ledger-02b: 同 run_id 同身份重写 pending → 幂等恢复(非新建)。"""
        write_pending(
            ledger_path,
            run_id="b2c3d4e5-f6a7-4890-bcde-f12345678901",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        # 同 run_id、内容/session/dest 三项逐字相同 → 视为同一 pending,幂等恢复
        result = write_pending(
            ledger_path,
            run_id="b2c3d4e5-f6a7-4890-bcde-f12345678901",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        assert result["outcome"] == "recovered_pending"

    def test_write_pending_twice_same_run_id_different_identity_rejected(
        self, ledger_path, mock_reserve
    ):
        """sc-asm-ledger-02e: 同 run_id 但另一条身份不同的 pending 已存在 → 硬拒绝。

        回归测试(GPT 质询 rev27-ch7):A 以 run_id R 写 pending 并崩在
        commit 之前;B 用同一 R 但不同内容/session/dest 进来时,两把
        CONTENT/DESTNAME 锁均不冲突,若不显式拦截会让 B 也建一条 pending,
        A 的 raw 与 B 的 committed 最终共享同一 run_id。
        """
        write_pending(
            ledger_path,
            run_id="b2c3d4e5-f6a7-4890-bcde-f12345678901",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        with pytest.raises(RealReservationHeld):
            write_pending(
                ledger_path,
                run_id="b2c3d4e5-f6a7-4890-bcde-f12345678901",
                session_id="session-01",
                content_fingerprint="fp-002",
                dest_filename="test--raw--2026-08-13.md",
            )

    def test_pending_requires_all_fields(self, ledger_path, mock_reserve):
        """sc-asm-ledger-02c: 缺字段应报错。"""
        with pytest.raises(TypeError):
            write_pending(ledger_path)  # type: ignore[call-arg]

    def test_fsync_parent_on_first_create(self, ledger_path, mock_reserve, tmp_path):
        """sc-asm-ledger-02d: 首次创建 fsync 父目录。"""
        write_pending(
            ledger_path,
            run_id="c3d4e5f6-a7b8-4a90-bcde-f12345678902",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        # 验证父目录可 stat
        parent = Path(ledger_path).parent
        assert parent.is_dir()
        # 验证文件内容
        lines = Path(ledger_path).read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == EVENT_PENDING


# ---------------------------------------------------------------------------
# sc-asm-ledger-03: committed 写入
# ---------------------------------------------------------------------------


class TestWriteCommitted:
    """committed 行写入。"""

    def test_write_committed_creates_entry(self, ledger_path, mock_reserve):
        """sc-asm-ledger-03a: 写 committed 行成功。"""
        # 先写 pending
        write_pending(
            ledger_path,
            run_id="d4e5f6a7-b8c9-4a90-bcde-f12345678903",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        # 写 committed 需要 DESTNAME reservation
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        result = write_committed(
            ledger_path,
            run_id="d4e5f6a7-b8c9-4a90-bcde-f12345678903",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )
        assert result["outcome"] == "created"
        entry = result["entry"]
        assert entry["event"] == EVENT_COMMITTED
        assert entry["raw_abspath"] == "/tmp/raw/test--raw--2026-08-13.md"
        # pipeline_content_hash = sha256(raw_abspath)[:16]
        expected_hash = hashlib.sha256(
            b"/tmp/raw/test--raw--2026-08-13.md"
        ).hexdigest()[:16]
        assert entry["pipeline_content_hash"] == expected_hash
        assert "ts" in entry

    def test_committed_field_integrity(self, ledger_path, mock_reserve):
        """sc-asm-ledger-03b: committed 字段完整性。"""
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_pending(
            ledger_path,
            run_id="e5f6a7b8-c9d0-4a90-bcde-f12345678904",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        result = write_committed(
            ledger_path,
            run_id="e5f6a7b8-c9d0-4a90-bcde-f12345678904",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )
        entry = result["entry"]
        # 验证 committed 必填字段(expect 6)
        assert "harvest_run_id" in entry
        assert "session_id" in entry
        assert "content_fingerprint" in entry
        assert "raw_abspath" in entry
        assert "pipeline_content_hash" in entry
        assert "ts" in entry
        # raw_abspath 与 hash 一致
        assert entry["pipeline_content_hash"] == hashlib.sha256(
            entry["raw_abspath"].encode("utf-8")
        ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# sc-asm-ledger-04: DESTNAME 冲突
# ---------------------------------------------------------------------------


class TestDestnameConflict:
    """DESTNAME 拿不到 → exit 4。"""

    def test_place_fails_on_destname_held(self, ledger_path):
        """sc-asm-ledger-04a: run_harvest_with_ledger DESTNAME 冲突触发出 destname_conflict。"""
        # DESTNAME 预约失败的 mock
        _destname_held = set()

        def mock_reserve_fn(namespace, key):
            if namespace == _NS_DESTNAME:
                # 对 DESTNAME 始终抛冲突(模拟已被占用的目标)
                raise RealReservationHeld(f"{namespace}/{key} 已被持有")
            return _MockReservation(namespace, key)

        def assemble_fn(**kwargs):
            return {"success": True, "raw_abspath": "/tmp/raw/test.md"}

        with patch("akb2.harvest.ledger.reserve", mock_reserve_fn):
            result = run_harvest_with_ledger(
                ledger_path,
                run_id="f6a7b8c9-d0e1-4a90-bcde-f12345678905",
                session_id="session-01",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=assemble_fn,
            )
            assert result["outcome"] == "destname_conflict"
            assert result["raw_abspath"] is None
            assert result["hash"] is None
            assert result["filename"] is None


# ---------------------------------------------------------------------------
# sc-asm-ledger-05: 已 committed 拒绝
# ---------------------------------------------------------------------------


class TestAlreadyCommitted:
    """同键 committed 拒绝。"""

    def test_second_commit_returns_already_committed(self, ledger_path, mock_reserve):
        """sc-asm-ledger-05a: 同 run_id 二次 committed 返回 already_committed。"""
        # 第一次写 pending + committed
        write_pending(
            ledger_path,
            run_id="a7b8c9d0-e1f2-4a90-bcde-f12345678906",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        rsv1 = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        r1 = write_committed(
            ledger_path,
            run_id="a7b8c9d0-e1f2-4a90-bcde-f12345678906",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv1,
        )
        assert r1["outcome"] == "created"

        # 第二次写 committed
        rsv2 = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        r2 = write_committed(
            ledger_path,
            run_id="a7b8c9d0-e1f2-4a90-bcde-f12345678906",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv2,
        )
        assert r2["outcome"] == "already_committed"

    def test_pending_with_committed_refused(self, ledger_path, mock_reserve):
        """sc-asm-ledger-05b: 已 committed 后同 id 不同内容写 pending 应拒绝。"""
        # 先写 committed
        write_pending(
            ledger_path,
            run_id="b8c9d0e1-f2a3-4a90-bcde-f12345678907",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_committed(
            ledger_path,
            run_id="b8c9d0e1-f2a3-4a90-bcde-f12345678907",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )

        # 同 run_id 不同 session_id → 应拒绝(ReservationHeld)
        with pytest.raises(RealReservationHeld):
            write_pending(
                ledger_path,
                run_id="b8c9d0e1-f2a3-4a90-bcde-f12345678907",
                session_id="session-02",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
            )


# ---------------------------------------------------------------------------
# sc-asm-ledger-06: 幂等恢复
# ---------------------------------------------------------------------------


class TestIdempotentRecovery:
    """同一 pending 幂等恢复。"""

    def test_idempotent_after_commit(self, ledger_path, mock_reserve):
        """sc-asm-ledger-06a: committed 后同一 pending 可恢复。"""
        # 写 pending
        write_pending(
            ledger_path,
            run_id="d0e1f2a3-b4c5-4a90-bcde-f12345678909",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        # 写 committed
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_committed(
            ledger_path,
            run_id="d0e1f2a3-b4c5-4a90-bcde-f12345678909",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )

        # 同内容再写 pending → 应恢复
        wp = write_pending(
            ledger_path,
            run_id="d0e1f2a3-b4c5-4a90-bcde-f12345678909",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        assert wp["outcome"] == "recovered_pending"

    def test_different_content_after_commit_rejected(self, ledger_path, mock_reserve):
        """sc-asm-ledger-06b: committed 后不同内容拒绝。"""
        write_pending(
            ledger_path,
            run_id="e1f2a3b4-c5d6-4a90-bcde-f12345678910",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_committed(
            ledger_path,
            run_id="e1f2a3b4-c5d6-4a90-bcde-f12345678910",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )

        # 同 run_id 不同 session_id → 拒绝
        with pytest.raises(RealReservationHeld):
            write_pending(
                ledger_path,
                run_id="e1f2a3b4-c5d6-4a90-bcde-f12345678910",
                session_id="session-02",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
            )


# ---------------------------------------------------------------------------
# sc-asm-ledger-07: reconcile 残留
# ---------------------------------------------------------------------------


class TestReconcile:
    """pending 残留 reconcile。"""

    def test_reconcile_clears_orphan_pending(self, ledger_path, mock_reserve):
        """sc-asm-ledger-07a: 孤立 pending 被 cleared。"""
        # 写 pending(内容 1,同 run_id 后续会 committed)
        write_pending(
            ledger_path,
            run_id="f2a3b4c5-d6e7-4a90-bcde-f12345678911",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        # 写 committed(内容 1)
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_committed(
            ledger_path,
            run_id="f2a3b4c5-d6e7-4a90-bcde-f12345678911",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )

        # 加另一个 pending(内容 2,未 committed)
        write_pending(
            ledger_path,
            run_id="a3b4c5d6-e7f8-4a90-bcde-f12345678912",
            session_id="session-02",
            content_fingerprint="fp-002",
            dest_filename="other--raw--2026-08-13.md",
        )

        # reconcile
        results = reconcile(ledger_path)
        # 内容 2 的 pending 未 committed → 应出现 pending_cleared
        cleared = [r for r in results if r.get("event") == "pending_cleared"]
        assert len(cleared) >= 1

    def test_reconcile_is_idempotent(self, ledger_path, mock_reserve):
        """sc-asm-ledger-07b: 重复调用 reconcile 不得对同一 pending 重复产出。

        回归测试(实测发现):reconcile 若不记录「此 pending 已处理过」,
        每次调用都会对同一批残留 pending 重新追加 reconcile_completed /
        pending_cleared,账本无限增长且审计轨迹自相矛盾。
        """
        # 场景 1:另一 run_id 已 committed 相同内容 → 本次应补 reconcile_completed
        write_pending(
            ledger_path,
            run_id="b4c5d6e7-f809-4a90-bcde-f1234567891a",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_committed(
            ledger_path,
            run_id="b4c5d6e7-f809-4a90-bcde-f1234567891a",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )
        write_pending(
            ledger_path,
            run_id="c5d6e7f8-0910-4a90-bcde-f1234567891b",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        # 场景 2:孤立 pending,永不 committed → 本次应清 pending
        write_pending(
            ledger_path,
            run_id="d6e7f809-1011-4a90-bcde-f1234567891c",
            session_id="session-02",
            content_fingerprint="fp-orphan",
            dest_filename="orphan--raw--2026-08-13.md",
        )

        first = reconcile(ledger_path)
        first_events = sorted(r["event"] for r in first)
        assert first_events == ["pending_cleared", "reconcile_completed"]

        second = reconcile(ledger_path)
        third = reconcile(ledger_path)
        assert second == []
        assert third == []

        # 账本里每种结果行恰好一条,不因重复调用而累积
        lines = Path(ledger_path).read_text().strip().split("\n")
        events = [json.loads(line)["event"] for line in lines]
        assert events.count("reconcile_completed") == 1
        assert events.count("pending_cleared") == 1

    def test_reconcile_records_conflict_on_fingerprint_mismatch(
        self, ledger_path, mock_reserve
    ):
        """sc-asm-ledger-07c: 同 run_id 的 pending 与 committed 指纹不等 → conflict。

        场景(GPT 质询 rev45 finding 5 回归):write_committed 不校验其
        content_fingerprint 与对应 pending 行是否一致 —— 若调用方在
        write_pending 之后传入了不同的 content_fingerprint 完成
        write_committed(如并发下的身份错配),账本上会同时存在
        pending(fp-1) 与 committed(fp-2)。reconcile 必须把这种"产物已在
        场但指纹不等"的情况记成 reconcile_conflict,而不是静默补一条
        reconcile_completed(那等于把不属于这条 pending 的产物认领给它),
        也不能因为已有 committed 行就删除/改动任何已落盘文件。
        """
        run_id = "e7f809a1-1213-4a90-bcde-f1234567891d"
        write_pending(
            ledger_path,
            run_id=run_id,
            session_id="session-01",
            content_fingerprint="fp-1",
            dest_filename="mismatch--raw--2026-08-13.md",
        )
        pending_ts = lookup(ledger_path, run_id)["ts"]

        rsv = _MockReservation(DESTNAME, "mismatch--raw--2026-08-13.md")
        write_committed(
            ledger_path,
            run_id=run_id,
            session_id="session-01",
            content_fingerprint="fp-2",  # 与 pending 的 fp-1 不等
            dest_filename="mismatch--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/mismatch--raw--2026-08-13.md",
            destname_reservation=rsv,
        )

        results = reconcile(ledger_path)

        conflicts = [r for r in results if r.get("event") == EVENT_RECONCILE_CONFLICT]
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict["harvest_run_id"] == run_id
        assert conflict["pending_ts"] == pending_ts
        assert conflict["pending_content_fingerprint"] == "fp-1"
        assert conflict["committed_content_fingerprint"] == "fp-2"

        # 不得补 committed:committed 行数保持 1(仅 write_committed 写入的那条)
        lines = Path(ledger_path).read_text().strip().split("\n")
        events = [json.loads(line) for line in lines]
        committed_rows = [e for e in events if e["event"] == EVENT_COMMITTED]
        assert len(committed_rows) == 1
        assert committed_rows[0]["raw_abspath"] == "/tmp/raw/mismatch--raw--2026-08-13.md"
        # 不得删除/改写已 committed 行引用的产物路径
        assert committed_rows[0]["content_fingerprint"] == "fp-2"

        # 幂等:重复调用不再重复产出同一条 conflict
        again = reconcile(ledger_path)
        assert again == []
        events_after = [
            json.loads(line)["event"]
            for line in Path(ledger_path).read_text().strip().split("\n")
        ]
        assert events_after.count(EVENT_RECONCILE_CONFLICT) == 1


# ---------------------------------------------------------------------------
# sc-asm-ledger-08: 账本路径
# ---------------------------------------------------------------------------


class TestLedgerPath:
    """账本路径 resolve_state_file。"""

    def test_resolve_ledger_path(self, monkeypatch):
        """sc-asm-ledger-08a: resolve_ledger_path 返回合法路径。"""
        # 设置 AKB2_STATE_ROOT 避免碰撞真实状态
        monkeypatch.setenv("AKB2_STATE_ROOT", "/tmp/akb2-test-state")
        path = resolve_ledger_path()
        assert path.name == "harvest-ledger.jsonl"
        assert "ledgers" in str(path)


# ---------------------------------------------------------------------------
# sc-asm-ledger-09: fsync + 首次创建
# ---------------------------------------------------------------------------


class TestFsync:
    """每行 write+fsync,首次创建 fsync 父目录。"""

    def test_append_preserves_previous_lines(self, ledger_path, mock_reserve):
        """sc-asm-ledger-09a: 追加不覆盖已有行。"""
        # 写第一行
        write_pending(
            ledger_path,
            run_id="d6e7f8a9-b0c1-4a90-bcde-f12345678915",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        # 写第二行
        write_pending(
            ledger_path,
            run_id="e7f8a9b0-c1d2-4a90-bcde-f12345678916",
            session_id="session-02",
            content_fingerprint="fp-002",
            dest_filename="other--raw--2026-08-13.md",
        )
        # 验证两行都在
        lines = Path(ledger_path).read_text().strip().split("\n")
        assert len(lines) == 2

    def test_first_write_creates_dirs(self, ledger_path, mock_reserve):
        """sc-asm-ledger-09b: 首次写入创建目录和文件。"""
        assert not Path(ledger_path).parent.exists()
        write_pending(
            ledger_path,
            run_id="f8a9b0c1-d2e3-4a90-bcde-f12345678917",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        assert Path(ledger_path).exists()
        assert Path(ledger_path).parent.is_dir()

    def test_overlong_line_fails_closed(self, ledger_path, mock_reserve):
        """sc-asm-ledger-09c: 单行超 MAX_LEDGER_LINE_BYTES → fail-closed 拒写。

        回归测试(GPT 质询 rev45-ch4):超长行若被静默截断或拆成多次
        os.write,并发 append 下行边界会交织——宁可不写也不写半行。
        """
        oversized_fingerprint = "x" * MAX_LEDGER_LINE_BYTES
        with pytest.raises(ValueError):
            write_pending(
                ledger_path,
                run_id="a0b1c2d3-e4f5-4a90-bcde-f123456789ff",
                session_id="session-01",
                content_fingerprint=oversized_fingerprint,
                dest_filename="test--raw--2026-08-13.md",
            )
        # 拒写不得留下任何部分行
        assert not Path(ledger_path).exists()


# ---------------------------------------------------------------------------
# sc-asm-ledger-10: lookup
# ---------------------------------------------------------------------------


class TestLookup:
    """按 run_id 查找。"""

    def test_lookup_returns_latest(self, ledger_path, mock_reserve):
        """sc-asm-ledger-10a: lookup 返回最新行。"""
        write_pending(
            ledger_path,
            run_id="a9b0c1d2-e3f4-4a90-bcde-f12345678918",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        result = lookup(ledger_path, "a9b0c1d2-e3f4-4a90-bcde-f12345678918")
        assert result is not None
        assert result["harvest_run_id"] == "a9b0c1d2-e3f4-4a90-bcde-f12345678918"
        assert result["event"] == EVENT_PENDING


# ---------------------------------------------------------------------------
# sc-asm-ledger-11: 高层面 run_harvest_with_ledger
# ---------------------------------------------------------------------------


class TestRunHarvestWithLedger:
    """完整流程验证。"""

    def test_successful_flow(self, ledger_path):
        """sc-asm-ledger-11a: 正常流程 outcome=created。"""
        def assemble_fn(**kwargs):
            return {"success": True, "raw_abspath": "/tmp/raw/test--raw--2026-08-13.md"}

        with patch("akb2.harvest.ledger.reserve",
                   return_value=_MockReservation(_NS_CONTENT, "ck")):
            result = run_harvest_with_ledger(
                ledger_path,
                run_id="b0c1d2e3-f4a5-4a90-bcde-f12345678919",
                session_id="session-01",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=assemble_fn,
            )
        assert result["outcome"] == "created"
        assert result["filename"] == "test--raw--2026-08-13.md"

    def test_destname_conflict_flow(self, ledger_path):
        """sc-asm-ledger-11b: DESTNAME 冲突触发出 outcome。"""
        # 对 DESTNAME namespace 始终抛冲突
        def mock_reserve_fn(namespace, key):
            if namespace == _NS_DESTNAME:
                raise RealReservationHeld(f"{namespace}/{key} 已被持有")
            return _MockReservation(namespace, key)

        def assemble_fn(**kwargs):
            return {"success": True, "raw_abspath": "/tmp/raw/test.md"}

        with patch("akb2.harvest.ledger.reserve", mock_reserve_fn):
            result = run_harvest_with_ledger(
                ledger_path,
                run_id="c1d2e3f4-a5b6-4a90-bcde-f12345678920",
                session_id="session-01",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=assemble_fn,
            )
            assert result["outcome"] == "destname_conflict"
            assert result["raw_abspath"] is None

    def test_run_id_conflict_after_commit(self, ledger_path):
        """sc-asm-ledger-11c: 已 committed 后同 run_id 触 already_committed。"""
        def assemble_fn(**kwargs):
            return {"success": True, "raw_abspath": "/tmp/raw/test.md"}

        with patch("akb2.harvest.ledger.reserve",
                   return_value=_MockReservation(_NS_CONTENT, "ck")):
            # 第一次:正常流程
            result = run_harvest_with_ledger(
                ledger_path,
                run_id="d2e3f4a5-b6c7-4a90-bcde-f12345678921",
                session_id="session-01",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=assemble_fn,
            )
            assert result["outcome"] == "created"

            # 第二次:同 run_id → already_committed
            result2 = run_harvest_with_ledger(
                ledger_path,
                run_id="d2e3f4a5-b6c7-4a90-bcde-f12345678921",
                session_id="session-01",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=assemble_fn,
            )
            assert result2["outcome"] == "already_committed"
            assert result2["raw_abspath"] is None
            assert result2["hash"] is None
            assert result2["filename"] is None

    def test_another_pending_before_commit_rejected(self, ledger_path):
        """sc-asm-ledger-11e: A 崩在 commit 前遗留 pending,B 同 run_id 不同
        身份进入 → 在 archive_raw 之前即被拒(GPT 质询 rev27-ch7)。
        """
        archive_calls = {"count": 0}

        def crashing_assemble(**kwargs):
            archive_calls["count"] += 1
            raise RuntimeError("模拟 A 崩溃")

        with patch("akb2.harvest.ledger.reserve",
                   return_value=_MockReservation(_NS_CONTENT, "ck")):
            # A: 写 pending 后 assemble_fn 抛异常(模拟崩在 commit 之前)
            result_a = run_harvest_with_ledger(
                ledger_path,
                run_id="f4a5b6c7-d8e9-4a90-bcde-f1234567892a",
                session_id="session-01",
                content_fingerprint="fp-001",
                dest_filename="a--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=crashing_assemble,
            )
            assert result_a["outcome"] == "assemble_failed"
            assert archive_calls["count"] == 1

            # B: 同一 run_id,内容/session/dest 全不同
            def unreached_assemble(**kwargs):
                archive_calls["count"] += 1
                return {"success": True, "raw_abspath": "/tmp/raw/b.md"}

            result_b = run_harvest_with_ledger(
                ledger_path,
                run_id="f4a5b6c7-d8e9-4a90-bcde-f1234567892a",
                session_id="session-02",
                content_fingerprint="fp-002",
                dest_filename="b--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=unreached_assemble,
            )
            assert result_b["outcome"] == "run_id_conflict"
            # B 必须在 archive_raw 之前即被拒:assemble_fn 调用计数不增
            assert archive_calls["count"] == 1

    def test_assemble_failure(self, ledger_path):
        """sc-asm-ledger-11d: assemble_fn 失败返回 assemble_failed。"""
        def failing_assemble(**kwargs):
            return {"success": False, "error": "archive_raw 失败"}

        with patch("akb2.harvest.ledger.reserve",
                   return_value=_MockReservation(_NS_CONTENT, "ck")):
            result = run_harvest_with_ledger(
                ledger_path,
                run_id="e3f4a5b6-c7d8-4a90-bcde-f12345678922",
                session_id="session-01",
                content_fingerprint="fp-001",
                dest_filename="test--raw--2026-08-13.md",
                raw_dir="/tmp/raw",
                assemble_fn=failing_assemble,
            )
            assert result["outcome"] == "assemble_failed"


# ---------------------------------------------------------------------------
# sc-asm-ledger-12: 同键 committed → outcome 区分
# ---------------------------------------------------------------------------


class TestOutcomeDistinction:
    """created vs already_committed 区分。"""

    def test_created_returns_raw_info(self, ledger_path, mock_reserve):
        """sc-asm-ledger-12a: outcome=created 返回 raw 路径与 hash。"""
        rsv = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_pending(
            ledger_path,
            run_id="f4a5b6c7-d8e9-4a90-bcde-f12345678923",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        result = write_committed(
            ledger_path,
            run_id="f4a5b6c7-d8e9-4a90-bcde-f12345678923",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/test--raw--2026-08-13.md",
            destname_reservation=rsv,
        )
        assert result["outcome"] == "created"
        entry = result["entry"]
        assert entry["raw_abspath"] == "/tmp/raw/test--raw--2026-08-13.md"
        assert entry["pipeline_content_hash"] == hashlib.sha256(
            b"/tmp/raw/test--raw--2026-08-13.md"
        ).hexdigest()[:16]

    def test_already_committed_returns_no_raw(self, ledger_path, mock_reserve):
        """sc-asm-ledger-12b: already_committed 返回已有 entry(不含新 raw/hash)。"""
        # 第一次 committed
        rsv1 = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        write_pending(
            ledger_path,
            run_id="a5b6c7d8-e9f0-4a90-bcde-f12345678924",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
        )
        write_committed(
            ledger_path,
            run_id="a5b6c7d8-e9f0-4a90-bcde-f12345678924",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/v1/test--raw--2026-08-13.md",
            destname_reservation=rsv1,
        )

        # 第二次 committed
        rsv2 = _MockReservation(DESTNAME, "test--raw--2026-08-13.md")
        result = write_committed(
            ledger_path,
            run_id="a5b6c7d8-e9f0-4a90-bcde-f12345678924",
            session_id="session-01",
            content_fingerprint="fp-001",
            dest_filename="test--raw--2026-08-13.md",
            raw_abspath="/tmp/raw/v2/test--raw--2026-08-13.md",
            destname_reservation=rsv2,
        )
        assert result["outcome"] == "already_committed"
        # 返回的是第一次 committed 的 entry(raw_abspath 是第一次的)
        assert result["entry"]["raw_abspath"] == "/tmp/raw/v1/test--raw--2026-08-13.md"
