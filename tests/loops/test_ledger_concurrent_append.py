"""缺陷 #11c: 账本追加必须是真 append（并发不丢行）+ 损坏行可见。

原实现是「读全文 → 写 tmp → os.replace」：两个写者各读到 N 行、各写 N+1 行，
最后 rename 的覆盖前者 → 丢行。Mac Mini 部署（同一 vault、两台机器各跑一份
runner）已让 docstring 里"串行单工不设锁"的前提失效。

断言的可观测行为：
- 两个进程各追加 N 条不同事件 → 最终账本恰好 2N 行，且每条都能被读回
- 损坏 JSON 行不再静默 continue：打印带行号的告警（否则幂等判定按"未处理"走 → 重跑）
- 幂等语义不变：同内容重复写入仍返回 False 且不增行
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from akb2.loops.ledger import append_event

APPENDS_PER_WORKER = 150

_CHILD_SCRIPT = """
import sys
sys.path.insert(0, {repo!r})
sys.path.insert(0, {src!r})
from akb2.loops.ledger import append_event

ledger_path = sys.argv[1]
worker = sys.argv[2]
count = int(sys.argv[3])
for i in range(count):
    append_event(ledger_path, {{
        "loop_id": "loop1",
        "node": "intake_raw",
        "event": "intake_raw",
        "content_hash": f"{{worker}}-{{i}}",
        "ts": "2026-07-26T00:00:00Z",
    }})
"""


def _read_lines(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_concurrent_append_from_two_processes_loses_no_line(tmp_path: Path) -> None:
    """两进程各追加 N 条 → 2N 行全在（旧 read-modify-write 实现会丢行）。"""
    repo_root = Path(__file__).resolve().parents[2]
    script = _CHILD_SCRIPT.format(
        repo=str(repo_root), src=str(repo_root / "src")
    )
    ledger = tmp_path / "quality-ledger.jsonl"

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(ledger), worker, str(APPENDS_PER_WORKER)],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for worker in ("w1", "w2")
    ]
    for proc in procs:
        out, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, f"子进程失败: {err or out}"

    events = _read_lines(ledger)
    hashes = {e["content_hash"] for e in events}
    expected = {
        f"{worker}-{i}" for worker in ("w1", "w2") for i in range(APPENDS_PER_WORKER)
    }
    assert len(events) == 2 * APPENDS_PER_WORKER, (
        f"并发追加丢行: 期望 {2 * APPENDS_PER_WORKER} 行，实际 {len(events)} 行"
    )
    assert hashes == expected, f"丢失的事件: {sorted(expected - hashes)[:10]}"


def test_idempotent_duplicate_still_skipped(tmp_path: Path) -> None:
    """幂等语义不变：同内容第二次写入返回 False 且不增行。"""
    ledger = tmp_path / "quality-ledger.jsonl"
    event = {
        "loop_id": "loop1",
        "node": "intake_raw",
        "event": "intake_raw",
        "content_hash": "abc123",
        "ts": "2026-07-26T00:00:00Z",
    }
    assert append_event(str(ledger), event) is True
    assert append_event(str(ledger), event) is False
    assert len(_read_lines(ledger)) == 1


def test_corrupt_line_is_reported_not_silently_skipped(
    tmp_path: Path, capsys
) -> None:
    """损坏行必须可见（宪法 P4）：静默跳过 → 幂等判定误判"未处理" → 重跑。"""
    ledger = tmp_path / "quality-ledger.jsonl"
    event = {
        "loop_id": "loop1",
        "node": "intake_raw",
        "event": "intake_raw",
        "content_hash": "abc123",
        "ts": "2026-07-26T00:00:00Z",
    }
    ledger.write_text(
        json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
        + "{半行损坏 JSON\n",
        encoding="utf-8",
    )

    # 已存在的同内容行仍被去重（损坏行不影响幂等判定的正确部分）
    assert append_event(str(ledger), event) is False

    err = capsys.readouterr().err
    assert "损坏" in err, f"损坏行被静默跳过，stderr 无告警: {err!r}"
    assert "1" in err, f"告警未给出损坏行计数/行号: {err!r}"
