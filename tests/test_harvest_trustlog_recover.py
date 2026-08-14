"""harvest_trustlog_recover.py 的协议守卫测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PY = _REPO_ROOT / "scripts" / "harvest_trustlog_recover.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("harvest_trustlog_recover", _SCRIPT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


recover = _load_module()


def _entry(deployment_id: str, seq: int) -> dict[str, object]:
    return {"deployment_id": deployment_id, "seq": seq}


def test_validate_recovery_target_rejects_earlier_complete_entry() -> None:
    """票据只能指向最后一条完整条目，不能把后面的有效记录当坏尾截掉。"""
    first = _entry("dep-1", 1)
    second = _entry("dep-1", 2)
    raw = (
        recover.json.dumps(first, sort_keys=True).encode()
        + b"\n"
        + recover.json.dumps(second, sort_keys=True).encode()
        + b"\n"
        + b'{"broken":'
    )
    parsed, bad = recover.scan_trust_log_bytes(raw)

    with pytest.raises(recover.RecoverError, match="最后一条完整条目"):
        recover.validate_recovery_target(parsed, bad, recover.entry_hash(first))


def test_truncate_with_audit_rolls_back_on_audit_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """截断后若 recovery-log 追加失败，trust-log 必须回滚到原字节级内容。"""
    good = recover.json.dumps(_entry("dep-1", 1), sort_keys=True).encode() + b"\n"
    bad = b'{"broken":'
    raw = good + bad
    trust_log = tmp_path / "log"
    recovery_log = tmp_path / "recovery-log"
    trust_log.write_bytes(raw)
    payload = b'{"ticket":{"nonce":"n1"}}\n'

    real_write = recover.os.write

    def fake_write(fd: int, data: bytes) -> int:
        if data == payload:
            raise OSError("disk full")
        return real_write(fd, data)

    monkeypatch.setattr(recover.os, "write", fake_write)

    with pytest.raises(recover.RecoverError, match="已回滚 trust-log"):
        recover.truncate_with_audit(
            trust_log=trust_log,
            recovery_log=recovery_log,
            target_offset=len(good),
            bad_bytes=bad,
            payload=payload,
            before_sha256=recover.sha256_bytes(raw),
            after_sha256=recover.sha256_bytes(good),
        )

    assert trust_log.read_bytes() == raw


def test_nonce_seen_fails_closed_on_corrupt_recovery_log(tmp_path: Path) -> None:
    """recovery-log 自身损坏时必须拒绝继续恢复，不能静默跳过重放检查。"""
    recovery_log = tmp_path / "recovery-log"
    recovery_log.write_bytes(b'{"ticket":{"nonce":"ok"}}\n{"broken":\n')

    with pytest.raises(recover.RecoverError, match="recovery-log 含损坏条目"):
        recover.nonce_seen(recovery_log, "new-nonce")
