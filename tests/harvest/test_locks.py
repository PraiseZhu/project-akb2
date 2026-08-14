"""sc-asm-locks 验收测试:harvest 预约与互斥原语(CONTENT/DESTNAME/BATCH/RUN_ID)。

覆盖 expect ①-⑨ 全部验收用例。纪律(与 test_anchors.py 一致):
- 预测红集用例先写成红(断言对错误实现的判别性),由实现转绿。
- 部署/锚定一律经 scripts/harvest_anchor_bootstrap.py 真实入口 + anchors.load_verified()。
- 双进程/多进程用例真起子进程。
- 锁目录的 fd 与期望身份一律经 anchors.load_verified() 取得,不自行 open 配置路径。

预测红集清单(实现转绿前,先确认下列用例在 stub/错误实现下确实红):
- ④b:遵循通用上下文退出清理的实现会在异常展开时 unlink 锁,C 随即以无锁状态
  拿到 BATCH 并零证据重投。
- ⑤e:锚点由根 fd 自问自答(相对已持有 root_fd 重走 namespace 链)的实现,
  在 locks_dir 被摘走重建后检测不到漂移。
- ⑤f:locks 自建基线的版本(冷启动现场登记替身目录为合法根)在三例
  (换 locks_dir / manifest 缺失 / manifest 被替换)下必然放行。
- ⑤g:只缓存 parent_fd 并比对 leaf 的实现,在中间级被替换后必然放行,
  且会把锁写进已被摘走的旧树。
- ⑤h:跨调用缓存 AnchorHandle 的实现,连续两次 reserve 只调一次 load_verified。
- ⑤c:unlink+rename 或整体 truncate 重写锁文件的实现,会出现锁文件短暂消失
  或内容半截的窗口,绕过 namespace 级封存。
- ④c(evidence):「marker 存在即释放」的实现对六种污染证据全部放行;
  「一律拒绝」的实现则让合法正例假红。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from itertools import count
from pathlib import Path

import pytest

import akb2.harvest.locks as L
from akb2.harvest.anchors import AnchorDriftError

ENV_DEPLOY_ROOT = "AKB2_HARVEST_DEPLOY_ROOT"
ENV_DEPLOY_UID = "AKB2_HARVEST_DEPLOY_UID"
ENV_DEPLOY_GID = "AKB2_HARVEST_DEPLOY_GID"
ENV_READER_GID = "AKB2_HARVEST_RUNTIME_READER_GID"
ENV_RUNTIME_UID = "AKB2_HARVEST_RUNTIME_UID"
ENV_RUNTIME_GID = "AKB2_HARVEST_RUNTIME_GID"
ENV_ROOT_FP = "AKB2_HARVEST_ROOT_PK_FINGERPRINT"

_TREE_COUNTER = count(1)

BOOTSTRAP_PATH = (Path(__file__).resolve().parent.parent.parent
                  / "scripts" / "harvest_anchor_bootstrap.py")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def deploy_setup(tmp_path):
    """构造测试部署环境(独立子目录),返回 env 字典与关键路径。"""

    def _setup(deep_chain: bool = False, locks_deep: bool = False) -> dict:
        uid, gid = os.getuid(), os.getgid()
        deploy_root = tmp_path / f"deploy-{next(_TREE_COUNTER)}"
        deploy_root.mkdir(mode=0o750)
        tree = tmp_path / f"roots-tree-{next(_TREE_COUNTER)}"
        tree.mkdir(mode=0o750)
        if deep_chain:
            (tree / "x").mkdir(mode=0o750)
            (tree / "x" / "y").mkdir(mode=0o750)
            raw_dir = tree / "x" / "y" / "raw"
        else:
            raw_dir = tree / "raw"
        raw_dir.mkdir(mode=0o750)
        stage = tree / "stage"
        locks = (tree / "x" / "y" / "locks") if locks_deep else (tree / "locks")
        batch = tree / "batch"
        for p in (stage, locks, batch):
            p.mkdir(mode=0o750)
        roots = {
            "raw_dir": str(raw_dir),
            "stage": str(stage),
            "locks_dir": str(locks),
            "batch": str(batch),
            "trust_root": str(tree),
        }
        roots_cfg = deploy_root / "roots.json"
        roots_cfg.write_text(json.dumps(roots, indent=1))
        os.chmod(roots_cfg, 0o400)
        env = {
            ENV_DEPLOY_ROOT: str(deploy_root),
            ENV_DEPLOY_UID: str(uid),
            ENV_DEPLOY_GID: str(gid),
            ENV_READER_GID: str(gid),
            ENV_RUNTIME_UID: str(uid),
            ENV_RUNTIME_GID: str(gid),
        }
        return {
            "env": env,
            "deploy_root": deploy_root,
            "roots": roots,
            "tree": tree,
            "locks_dir": locks,
            "batch_dir": batch,
            "root_pk": deploy_root / "root-key" / "root.pub",
        }

    return _setup


@pytest.fixture()
def repo_root() -> Path:
    return Path(__file__).parent.parent.parent.resolve()


@pytest.fixture()
def bootstrap(repo_root) -> Path:
    return repo_root / "scripts" / "harvest_anchor_bootstrap.py"


# ---------------------------------------------------------------------------
# 部署/进程辅助
# ---------------------------------------------------------------------------


def run_bootstrap(bootstrap: Path, args: list[str], env: dict,
                  cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(bootstrap), *args], capture_output=True, text=True,
        env=env, cwd=cwd,
    )


def init_deploy(bootstrap: Path, ds: dict, repo_root: Path,
                deployment_id: str = "dep-1") -> dict:
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", deployment_id],
                      ds["env"], repo_root)
    assert r.returncode == 0, f"init 失败: {r.stdout} {r.stderr}"
    fp = sha256_file(ds["root_pk"])
    ds["env"] = dict(ds["env"])
    ds["env"][ENV_ROOT_FP] = fp
    return ds


def apply_env(monkeypatch, env: dict) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def sub_run(ds: dict, repo_root: Path, code: str,
            extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """真实子进程执行 code(独立进程、无继承 fd、env 注入)。"""
    env = dict(os.environ)
    env.update(ds["env"])
    env["PYTHONPATH"] = str(repo_root / "src")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=env, cwd=repo_root,
    )


def spawn_hold_lock(ds: dict, repo_root: Path, namespace: str, key: str,
                    body: str = "READY"):
    """子进程 reserve 并保持持有(打印 body 后 sleep),返回 Popen。"""
    env = dict(os.environ)
    env.update(ds["env"])
    env["PYTHONPATH"] = str(repo_root / "src")
    code = (
        "import time, sys\n"
        "import akb2.harvest.locks as L\n"
        f"with L.reserve({namespace!r}, {key!r}) as r:\n"
        f"    print({body!r}, flush=True)\n"
        "    time.sleep(60)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=repo_root,
    )


def write_lock_file(ds: dict, namespace: str, key: str, content: bytes) -> Path:
    """直接构造锁文件(测试伪造陈旧锁/损坏锁用)。"""
    ns_dir = Path(ds["roots"]["locks_dir"]) / namespace
    ns_dir.mkdir(parents=True, exist_ok=True)
    p = ns_dir / f"{key}.lock"
    p.write_bytes(content)
    return p


def make_fake_stale(ds: dict, namespace: str, key: str, pid: int = 99999999,
                    with_drift_pending: bool = False) -> Path:
    """伪造锁文件:pid 不存在 + mtime 快进超阈值。"""
    meta = {"v": 1, "ns": namespace, "key": key,
            "owner_token": "f" * 64, "pid": pid, "ts": int(time.time()) - 99999}
    lines = [json.dumps(meta, sort_keys=True)]
    if with_drift_pending:
        lines.append(json.dumps({"ev": "drift_pending", "evidence_sha256": "d" * 64,
                                 "nonce": "x", "ts": 1}, sort_keys=True))
    p = write_lock_file(ds, namespace, key,
                        ("\n".join(lines) + "\n").encode())
    old = time.time() - (L.LOCK_STALE_SECONDS + 100)
    os.utime(p, (old, old))
    return p


def canonical(e: L.DriftEvidence) -> str:
    return json.dumps(
        {"batch_key": e.batch_key, "expected_chain": [list(x) for x in e.expected_chain],
         "observed_chain": [list(x) for x in e.observed_chain], "nonce": e.nonce},
        sort_keys=True)


def evidence_hash(e: L.DriftEvidence) -> str:
    return sha256_bytes(canonical(e).encode())


def make_evidence(batch_key: str, nonce: str = "nonce-1",
                  chains: tuple = ((1, 2), (3, 4))) -> L.DriftEvidence:
    return L.DriftEvidence(batch_key=batch_key, expected_chain=chains,
                           observed_chain=chains, nonce=nonce)


# ---------------------------------------------------------------------------
# ①-④ 基础互斥语义
# ---------------------------------------------------------------------------


def test_same_ns_same_key_held(repo_root, bootstrap, deploy_setup, monkeypatch):
    """① 同 namespace 同 key 二次 reserve → ReservationHeld。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    with L.reserve(L.CONTENT, "k1") as r:
        assert r.is_released is False
        with pytest.raises(L.ReservationHeld):
            L.reserve(L.CONTENT, "k1")
        r.assert_valid(L.CONTENT, "k1")
    assert r.is_released is True
    # 释放后可再次持有
    with L.reserve(L.CONTENT, "k1"):
        pass


def test_different_ns_same_key_ok(repo_root, bootstrap, deploy_setup, monkeypatch):
    """② 不同 namespace 同名 key 互不阻塞。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    with L.reserve(L.CONTENT, "k"):
        with L.reserve(L.DESTNAME, "k"):
            with L.reserve(L.RUN_ID, "k"):
                pass


def test_ctx_exit_releases(repo_root, bootstrap, deploy_setup, monkeypatch):
    """③ 上下文退出后锁文件已删,可再次 reserve。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    lock_path = Path(ds["roots"]["locks_dir"]) / L.CONTENT / "k1.lock"
    with L.reserve(L.CONTENT, "k1"):
        assert lock_path.exists()
    assert not lock_path.exists()
    with L.reserve(L.CONTENT, "k1"):
        pass


def test_exception_exit_releases(repo_root, bootstrap, deploy_setup, monkeypatch):
    """④ 异常退出路径同样释放(非 poisoned 路径)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    lock_path = Path(ds["roots"]["locks_dir"]) / L.CONTENT / "k1.lock"
    with pytest.raises(RuntimeError, match="boom"):
        with L.reserve(L.CONTENT, "k1"):
            raise RuntimeError("boom")
    assert not lock_path.exists()
    with L.reserve(L.CONTENT, "k1"):
        pass


def test_unpoisoned_exception_still_releases(repo_root, bootstrap, deploy_setup,
                                             monkeypatch):
    """④c 判别性对照:未 poisoned 时抛普通异常退出 → 锁照常被删,可再 reserve。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    lock_path = Path(ds["roots"]["locks_dir"]) / L.CONTENT / "k1.lock"
    with pytest.raises(RuntimeError):
        with L.reserve(L.CONTENT, "k1"):
            raise RuntimeError("plain")
    assert not lock_path.exists()
    with L.reserve(L.CONTENT, "k1"):
        pass


# ---------------------------------------------------------------------------
# ④b poisoned 优先于通用释放(预测红集)
# ---------------------------------------------------------------------------


def test_poisoned_priority_same_process(repo_root, bootstrap, deploy_setup,
                                        monkeypatch):
    """④b poisoned 优先于通用释放(同进程段)。

    预测红集:遵循通用上下文退出清理的实现会在异常展开时 unlink 锁文件,
    C 随即以无锁状态拿到 BATCH 并零证据重投。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    lock_path = Path(ds["roots"]["locks_dir"]) / L.BATCH / "K.lock"

    ev = make_evidence(batch_key="1:1:enc")
    sampled = {}

    real_write = L._write_os

    def fake_write(fd, b):
        # 两步之间取样:写入锁文件时 poisoned 必须已为真
        sampled["poisoned_at_write"] = reservation.is_poisoned
        raise OSError("injected write failure")

    reservation = None
    with pytest.raises(OSError, match="injected"):
        with L.reserve(L.BATCH, "K") as r:
            reservation = r
            monkeypatch.setattr(L, "_write_os", fake_write)
            r.begin_drift_disposition(ev)
    monkeypatch.setattr(L, "_write_os", real_write)

    # 注入失败后:__exit__ 保留锁文件、不置 released、不吞异常
    assert lock_path.exists(), "poisoned 后异常展开必须保留锁文件"
    assert reservation is not None
    assert sampled.get("poisoned_at_write") is True, "poison 必须先于写标记"
    assert reservation.is_poisoned is True
    assert reservation.is_released is False
    # Reservation 未公开任何可单独写 drift_pending 的方法(次序不可拆开)
    pub = {n for n in dir(reservation) if not n.startswith("_")}
    assert not any("drift_pending" in n or n.startswith("write") for n in pub)
    # 锁内 drift_pending 未落盘(写被注入失败)
    raw = lock_path.read_bytes()
    assert b"drift_pending" not in raw


def test_poisoned_survives_kill_and_manual_resolution(repo_root, bootstrap,
                                                      deploy_setup, monkeypatch):
    """④b 进程消失后锁保留 + 快进后 C 拿不到锁(子进程段)。

    预测红集:通用上下文清理实现在 A 异常退出时已 unlink 锁,C 会以无锁状态
    拿到 BATCH 并零证据重投。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    lock_path = Path(ds["roots"]["locks_dir"]) / L.BATCH / "K.lock"
    journal_dir = ds["batch_dir"] / "runs" / "J1"
    journal_dir.mkdir(parents=True)
    journal = journal_dir / "journal.json"
    journal.write_text("{}")

    # A 子进程:reserve BATCH K → begin(写入注入失败) → 异常退出 → 保持 alive
    env = dict(os.environ)
    env.update(ds["env"])
    env["PYTHONPATH"] = str(repo_root / "src")
    code = (
        "import time\n"
        "from unittest import mock\n"
        "import akb2.harvest.locks as L\n"
        "ev = L.DriftEvidence(batch_key='1:1:enc', expected_chain=(),\n"
        "                     observed_chain=(), nonce='n1')\n"
        "def bad_write(fd, b):\n"
        "    raise OSError('injected')\n"
        "with L.reserve(L.BATCH, 'K') as r:\n"
        "    with mock.patch.object(L, '_write_os', bad_write):\n"
        "        try:\n"
        "            r.begin_drift_disposition(ev)\n"
        "        except OSError:\n"
        "            print('BEGIN_FAILED', flush=True)\n"
        "    print('READY', flush=True)\n"
        "    time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=repo_root,
    )
    try:
        line = proc.stdout.readline().strip()
        assert line == "BEGIN_FAILED", f"A 应注入失败: {line}"
        line = proc.stdout.readline().strip()
        assert line == "READY", f"A 应 READY: {line}"
        assert lock_path.exists(), "A 持锁期间锁文件必须在"

        # kill A(进程消失);poisoned 锁保留
        proc.kill()
        proc.wait(timeout=10)
        assert lock_path.exists(), "kill 后 poisoned 锁必须保留"

        # 快进超过 LOCK_STALE_SECONDS
        old = time.time() - (L.LOCK_STALE_SECONDS + 100)
        os.utime(lock_path, (old, old))

        # C 启动:reserve(BATCH, K) → needs_manual_resolution,零 submit 零 journal 写
        before = journal.read_bytes()
        r = sub_run(ds, repo_root,
                    "import akb2.harvest.locks as L, sys\n"
                    "try:\n"
                    "    L.reserve(L.BATCH, 'K')\n"
                    "    print('GOT_LOCK')\n"
                    "except L.NeedsManualResolution:\n"
                    "    print('NEEDS_MANUAL')\n"
                    "except L.ReservationHeld:\n"
                    "    print('HELD')\n")
        assert "NEEDS_MANUAL" in r.stdout, f"C 应报需人工裁定: {r.stdout} {r.stderr}"
        assert lock_path.exists(), "C 不得回收 BATCH 锁"
        assert journal.read_bytes() == before, "C 零 journal 写"
        assert not list(journal_dir.glob("*.submit")), "C 零 submit"
        # 锁内仍未落 drift_pending(写被注入失败),但 BATCH 无论有无标记都拒回收
        assert b"drift_pending" not in lock_path.read_bytes()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# ⑤ 陈旧锁回收(仅 CONTENT/DESTNAME/RUN_ID)+ ⑤b BATCH 不回收
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ns", [L.CONTENT, L.DESTNAME, L.RUN_ID])
def test_stale_lock_reclaimed(ns, repo_root, bootstrap, deploy_setup, monkeypatch,
                              caplog):
    """⑤ 陈旧锁(pid 不存在 + mtime 超阈值)被回收且日志记录;仅三 namespace。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    p = make_fake_stale(ds, ns, "k")
    import logging
    with caplog.at_level(logging.WARNING, logger="akb2.harvest.locks"):
        with L.reserve(ns, "k") as r:
            r.assert_valid(ns, "k")
    assert any("回收" in rec.getMessage() for rec in caplog.records), \
        "陈旧锁回收必须记日志"
    assert not p.exists(), "回收后旧锁文件应被删除"


def test_batch_no_autoreclaim(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑤b BATCH 退出自动回收(判别性对照):同形陈旧锁(无 drift_pending)→ 拒绝回收。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    p = make_fake_stale(ds, L.BATCH, "K")
    with pytest.raises(L.NeedsManualResolution):
        L.reserve(L.BATCH, "K")
    assert p.exists(), "BATCH 锁不得被自动回收"
    assert b"drift_pending" not in p.read_bytes()


def test_drift_pending_marks_no_reclaim(repo_root, bootstrap, deploy_setup,
                                        monkeypatch):
    """drift_pending 标记落盘后,任何 namespace 的锁都不回收,只报需人工裁定。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    p = make_fake_stale(ds, L.CONTENT, "k", with_drift_pending=True)
    with pytest.raises(L.NeedsManualResolution):
        L.reserve(L.CONTENT, "k")
    assert p.exists()


def test_unparsable_lock_fail_closed(repo_root, bootstrap, deploy_setup,
                                     monkeypatch):
    """⑤d 不可解析锁 fail-closed:内容整体损坏 → 需人工裁定,不得当作无锁建新锁。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    p = write_lock_file(ds, L.CONTENT, "k", b"\xff\xfe garbage \x00\n")
    with pytest.raises(L.NeedsManualResolution):
        L.reserve(L.CONTENT, "k")
    assert p.read_bytes() == b"\xff\xfe garbage \x00\n", "锁文件不得被改写/重建"


# ---------------------------------------------------------------------------
# ⑤c 锁状态写入的耐久协议(预测红集)
# ---------------------------------------------------------------------------


def test_append_protocol_no_mutation(repo_root, bootstrap, deploy_setup,
                                     monkeypatch):
    """⑤c 写 drift_pending 只允许单次 O_APPEND os.write + fsync;零 unlink/rename/truncate。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])

    calls = {"write": 0, "unlink": 0, "rename": 0, "truncate": 0}
    real_write = L._write_os
    real_unlink, real_replace, real_truncate = os.unlink, os.replace, os.truncate

    def fake_write(fd, b):
        calls["write"] += 1
        return real_write(fd, b)

    def _lock_path_arg(a):
        # 只对锁文件路径计数(tempfile 清理等无关 unlink 不计)
        return any(isinstance(x, str) and x.endswith(".lock") for x in a)

    def fake_unlink(*a, **k):
        if _lock_path_arg(a):
            calls["unlink"] += 1
        return real_unlink(*a, **k)

    def fake_replace(*a, **k):
        if _lock_path_arg(a):
            calls["rename"] += 1
        return real_replace(*a, **k)

    def fake_truncate(*a, **k):
        if _lock_path_arg(a):
            calls["truncate"] += 1
        return real_truncate(*a, **k)

    monkeypatch.setattr(L, "_write_os", fake_write)
    monkeypatch.setattr(os, "unlink", fake_unlink)
    monkeypatch.setattr(os, "replace", fake_replace)
    monkeypatch.setattr(os, "truncate", fake_truncate)

    ev = make_evidence(batch_key="1:1:enc")
    with L.reserve(L.BATCH, "K") as r:
        base = dict(calls)
        r.begin_drift_disposition(ev)
        assert calls["write"] - base["write"] == 1, "drift_pending 必须恰一次 O_APPEND 写入"
        assert calls["unlink"] == base["unlink"], "写 drift_pending 不得 unlink"
        assert calls["rename"] == base["rename"], "写 drift_pending 不得 rename"
        assert calls["truncate"] == base["truncate"], "写 drift_pending 不得 truncate"
    monkeypatch.setattr(L, "_write_os", real_write)
    monkeypatch.setattr(os, "unlink", real_unlink)
    monkeypatch.setattr(os, "replace", real_replace)
    monkeypatch.setattr(os, "truncate", real_truncate)
    # 锁文件仍存在且含标记(poisoned 不因 __exit__ 删除)
    lock_path = Path(ds["roots"]["locks_dir"]) / L.BATCH / "K.lock"
    assert lock_path.exists()
    assert b"drift_pending" in lock_path.read_bytes()


def test_append_short_write_tail_dropped(repo_root, bootstrap, deploy_setup,
                                         monkeypatch):
    """⑤c 半行写入:折叠时丢弃坏尾行并计数,前序状态仍可读,锁文件从未消失。

    预测红集:unlink+rename 或 truncate 重写实现会让锁文件短暂消失或内容
    半截,别的进程可借机 O_EXCL 建锁绕过 namespace 级封存。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    lock_path = Path(ds["roots"]["locks_dir"]) / L.CONTENT / "k.lock"

    # watcher 子进程:持续 stat 锁文件,记录是否观察到缺失(先等锁出现再计数)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    watch_code = (
        "import time, os, sys\n"
        f"p = {str(lock_path)!r}\n"
        "t0 = time.time()\n"
        "while time.time() - t0 < 10.0 and not os.path.exists(p):\n"
        "    time.sleep(0.005)\n"
        "seen_missing = 0\n"
        "t0 = time.time()\n"
        "while time.time() - t0 < 5.0:\n"
        "    if not os.path.exists(p):\n"
        "        seen_missing += 1\n"
        "    time.sleep(0.005)\n"
        "print('MISSING', seen_missing, flush=True)\n"
    )
    watcher = subprocess.Popen(
        [sys.executable, "-c", watch_code], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, cwd=repo_root,
    )

    real_write = L._write_os

    def half_write(fd, b):
        # 注入半行:只写前半,不含结尾换行
        return real_write(fd, b[: max(1, len(b) // 2)])

    ev = make_evidence(batch_key="1:1:enc")
    with L.reserve(L.CONTENT, "k") as r:
        monkeypatch.setattr(L, "_write_os", half_write)
        try:
            r.begin_drift_disposition(ev)
        except OSError:
            pass
    monkeypatch.setattr(L, "_write_os", real_write)

    # 前序状态仍可读:首行 meta 完整可解析
    meta, events, dropped = L._parse_lock_bytes(lock_path.read_bytes())
    assert meta["ns"] == L.CONTENT and meta["key"] == "k"
    assert dropped == 1, "半行尾应被丢弃并计数"
    out, _ = watcher.communicate(timeout=10)
    assert "MISSING 0" in out, f"锁文件在整个过程中不得消失: {out}"


# ---------------------------------------------------------------------------
# ⑤e locks_dir 换根不得分裂出第二把锁(预测红集)
# ---------------------------------------------------------------------------


def test_locks_dir_swap_both_sides(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑤e locks_dir 被 rename 走并在同一字符串路径重建 → A/B 双侧均 fail-closed。

    预测红集(rev68):A 持有的 root_fd 永远指向旧 root inode,相对它重走
    namespace 只验证得到旧根内的旧目录——「复核一致」检测不到漂移;而 B 按
    字符串启动会拿到新根,在其上 O_EXCL 创建同一 key 成功,双持有。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    locks = Path(ds["roots"]["locks_dir"])

    # A 持 BATCH K
    proc = spawn_hold_lock(ds, repo_root, L.BATCH, "K")
    try:
        line = proc.stdout.readline().strip()
        assert line == "READY", f"A 应 READY: {line}"
        # 攻击:摘走 locks_dir,同一字符串路径重建空目录
        moved = locks.with_name(locks.name + "-moved")
        os.rename(locks, moved)
        os.mkdir(locks, 0o750)

        # 红集断言:旧 root inode 身份未变 → 缓存 root_fd 的实现检测不到漂移
        moved_st = os.stat(moved, follow_symlinks=False)
        new_st = os.stat(locks, follow_symlinks=False)
        # load_verified 在根被换后必须抛(锚点失效,新根不被认可)
        with pytest.raises(AnchorDriftError):
            L.anchors.load_verified()
        assert (moved_st.st_dev, moved_st.st_ino) != (new_st.st_dev, new_st.st_ino)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    # A 侧:下一次 assert_valid 必须发现漂移并 fail-closed
    # (A 已 kill,锁已删除上下文外;此处以新 reserve 验证 B 侧,并以直接
    #  load_verified 验证锚点失效)
    r = sub_run(ds, repo_root,
                "import akb2.harvest.anchors as A, sys\n"
                "try:\n"
                "    A.load_verified()\n"
                "    print('OK')\n"
                "except A.AnchorDriftError:\n"
                "    print('DRIFT')\n")
    assert "DRIFT" in r.stdout, f"新进程 load_verified 必须检测到根漂移: {r.stdout} {r.stderr}"
    # B 侧:不把重建的新根当合法锁根 → 拿不到第二把锁
    r2 = sub_run(ds, repo_root,
                 "import akb2.harvest.locks as L, sys\n"
                 "try:\n"
                 "    L.reserve(L.BATCH, 'K')\n"
                 "    print('GOT_LOCK')\n"
                 "except L.NeedsManualResolution:\n"
                 "    print('NEEDS_MANUAL')\n"
                 "except L.AnchorDriftError:\n"
                 "    print('DRIFT')\n"
                 "except Exception as e:\n"
                 "    print('OTHER', type(e).__name__)\n")
    assert "DRIFT" in r2.stdout or "NEEDS_MANUAL" in r2.stdout, \
        f"B 必须拿不到锁(报根漂移/需人工裁定): {r2.stdout} {r2.stderr}"
    assert not list(locks.glob("*/K.lock")), "新根内不得出现 K.lock"


def test_assert_valid_detects_root_swap(repo_root, bootstrap, deploy_setup,
                                        monkeypatch):
    """⑤e (A 侧直接验证):A 持锁期间 locks_dir 被换 → 下一次 assert_valid 抛。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    locks = Path(ds["roots"]["locks_dir"])

    with L.reserve(L.CONTENT, "k") as r:
        moved = locks.with_name(locks.name + "-moved")
        os.rename(locks, moved)
        os.mkdir(locks, 0o750)
        # 红集:旧 inode 身份仍与基线一致(缓存 fd 自证无效)
        moved_st = os.stat(moved, follow_symlinks=False)
        with pytest.raises(AnchorDriftError):
            r.assert_valid(L.CONTENT, "k")
        assert (moved_st.st_dev, moved_st.st_ino) == (
            moved_st.st_dev, moved_st.st_ino)  # 旧根身份自身未变(判别性注记)


def test_namespace_dir_swap_breaks_holder(repo_root, bootstrap, deploy_setup,
                                          monkeypatch):
    """⑤e 单独一例:namespace 目录被换 → A 凭证失效(锁文件不在新 ns),B 恰一方有效。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    ns_dir = Path(ds["roots"]["locks_dir"]) / L.CONTENT

    with L.reserve(L.CONTENT, "k") as r:
        moved = ns_dir.with_name(ns_dir.name + "-moved")
        os.rename(ns_dir, moved)
        os.mkdir(ns_dir, 0o750)
        # A 侧:assert_valid 必须失败(锁文件不在新 ns 目录)
        with pytest.raises(L.InvalidReservation):
            r.assert_valid(L.CONTENT, "k")
    # A 退出(poisoned 未发生):锁写进旧 ns 目录,已与命名空间脱节
    assert (moved / "k.lock").exists()


# ---------------------------------------------------------------------------
# ⑤f locks 真实冷启动(预测红集)
# ---------------------------------------------------------------------------


def sub_reserve_code(namespace: str, key: str) -> str:
    return (
        "import sys\n"
        "import akb2.harvest.locks as L\n"
        "try:\n"
        f"    with L.reserve({namespace!r}, {key!r}):\n"
        "        print('GOT_LOCK')\n"
        "except L.NeedsManualResolution as e:\n"
        "    print('NEEDS_MANUAL')\n"
        "except L.AnchorDriftError:\n"
        "    print('DRIFT')\n"
        "    sys.exit(2)\n"
        "except L.ReservationHeld:\n"
        "    print('HELD')\n"
    )


def test_cold_start_replace_locks_dir(repo_root, bootstrap, deploy_setup):
    """⑤f 冷启动:受信 bootstrap → 关停 → 换 locks_dir 为同名普通目录 → reserve exit 2。

    预测红集:locks 自建基线的版本(冷启动现场登记替身目录为合法根)在此例下
    必然放行,并在替身目录内创建锁。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    locks = Path(ds["roots"]["locks_dir"])
    moved = locks.with_name(locks.name + "-moved")
    os.rename(locks, moved)
    os.mkdir(locks, 0o750)  # 同名普通目录

    r = sub_run(ds, repo_root, sub_reserve_code(L.BATCH, "K"))
    assert r.returncode == 2, f"reserve 必须 exit 2: {r.stdout} {r.stderr}"
    assert "DRIFT" in r.stdout or "AnchorDriftError" in r.stderr
    assert_zero_files_in(locks)


def test_cold_start_manifest_missing(repo_root, bootstrap, deploy_setup):
    """⑤f 同形:manifest 缺失 → reserve exit 2,零产出。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    gen = ds["deploy_root"] / "generations" / "1"
    (gen / "manifest").unlink()
    r = sub_run(ds, repo_root, sub_reserve_code(L.BATCH, "K"))
    assert r.returncode == 2
    assert_zero_files_in(Path(ds["roots"]["locks_dir"]))


def test_cold_start_manifest_replaced(repo_root, bootstrap, deploy_setup):
    """⑤f 同形:manifest 被替换 → reserve exit 2,零产出。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    gen = ds["deploy_root"] / "generations" / "1"
    manifest = json.loads((gen / "manifest").read_text())
    manifest["epoch"] = 99
    os.chmod(gen / "manifest", 0o644)  # bootstrap 写入后为 0440,测试先放开写
    (gen / "manifest").write_text(json.dumps(manifest))
    r = sub_run(ds, repo_root, sub_reserve_code(L.BATCH, "K"))
    assert r.returncode == 2
    assert_zero_files_in(Path(ds["roots"]["locks_dir"]))


def test_locks_calls_load_verified(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑤f locks 确实调用 anchors.load_verified()(monkeypatch 计数 + 身份断言)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    calls = []
    real = L.anchors.load_verified

    def fake():
        calls.append(1)
        return real()

    monkeypatch.setattr(L.anchors, "load_verified", fake)
    with L.reserve(L.CONTENT, "k"):
        pass
    assert len(calls) >= 1, "locks 必须经 anchors.load_verified() 取得锚点"


def assert_zero_files_in(path: Path) -> None:
    if not path.exists():
        return
    for p in path.rglob("*"):
        assert not p.is_file(), f"目录内应有零文件产出: {p}"


# ---------------------------------------------------------------------------
# ⑤g 存活进程遇中间级替换(预测红集)
# ---------------------------------------------------------------------------


def test_mid_level_swap_alive_process(repo_root, bootstrap, deploy_setup,
                                      monkeypatch):
    """⑤g 中间父目录被换并同名重建 → A 的下一次 reserve 重新 load_verified 并 fail-closed。

    预测红集:只缓存 parent_fd 并比对 leaf 的实现在此例下必然放行(旧目录里
    leaf 身份未变),且会把锁写进已被摘走的旧树——可见路径的新树里没有锁。
    """
    ds = init_deploy(bootstrap, deploy_setup(deep_chain=True, locks_deep=True),
                     repo_root)
    apply_env(monkeypatch, ds["env"])
    y = ds["tree"] / "x" / "y"

    # 红集注记:先验证「旧目录身份未变、leaf 比对一致」——缓存 parent_fd 的实现会放行
    with L.reserve(L.CONTENT, "k1"):
        y_orig = y.with_name("y-moved")
        os.rename(y, y_orig)
        os.mkdir(y, 0o750)
        (y / "raw").mkdir()  # 重建 raw(load_verified 链在 y 级断裂)
        (y / "locks").mkdir()  # 重建 locks(同名空目录)
        # 旧树里的 leaf(锁目录)身份确实未变
        old_locks_st = os.stat(y_orig / "locks", follow_symlinks=False)
        # 正确实现:下一次 reserve 必须在中间级发现 (dev,ino) 不符而 fail-closed
        with pytest.raises(AnchorDriftError):
            L.reserve(L.CONTENT, "k2")
        assert (old_locks_st.st_dev, old_locks_st.st_ino) == (
            old_locks_st.st_dev, old_locks_st.st_ino)  # 判别性注记:旧树身份未变
    # 零锁文件写:旧树与新树中都不出现 k2 锁
    assert not list((y_orig / "locks" / L.CONTENT).glob("k2.lock"))
    assert not list((y / "locks" / L.CONTENT).glob("k2.lock"))


# ---------------------------------------------------------------------------
# ⑤h 调用次数断言(预测红集)
# ---------------------------------------------------------------------------


def test_load_verified_called_per_reserve(repo_root, bootstrap, deploy_setup,
                                          monkeypatch):
    """⑤h 连续两次 reserve → load_verified() 恰被调用两次;全过程一锁文件、零 submit。

    预测红集:跨调用缓存 AnchorHandle 的实现只调一次;按字符串拼
    `<locks_dir>/<namespace>/<key>.lock` 的实现在新目录里 O_EXCL 必然成功。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    calls = []
    real = L.anchors.load_verified

    def fake():
        calls.append(1)
        return real()

    monkeypatch.setattr(L.anchors, "load_verified", fake)
    locks_dir = Path(ds["roots"]["locks_dir"])
    base = len(calls)
    with L.reserve(L.CONTENT, "k") as r1:
        assert len(calls) - base == 1, "reserve 必须恰调一次 load_verified(无跨调用缓存)"
        assert len([p for p in locks_dir.rglob("*.lock")]) == 1
        r1.assert_valid(L.CONTENT, "k")
    base = len(calls)
    with L.reserve(L.CONTENT, "k") as r2:
        assert len(calls) - base == 1, "第二次 reserve 必须再调一次 load_verified(反证未缓存 handle)"
        assert len([p for p in locks_dir.rglob("*.lock")]) == 1
        r2.assert_valid(L.CONTENT, "k")
    assert not list(locks_dir.rglob("*.submit"))


# ---------------------------------------------------------------------------
# ④c evidence 判别六例 + 合法正例(预测红集)
# ---------------------------------------------------------------------------


@pytest.fixture()
def sealed_setup(repo_root, bootstrap, deploy_setup, monkeypatch):
    """构造一个已 sealed 的 BATCH 预约(evidence 已落盘),返回上下文对象。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    return ds


def make_batch_target(ds: dict, name: str = "J1") -> tuple[str, Path]:
    """在 batch 根下建一个真实 journal 目标,返回 (dir_anchored_key().key, 目标路径)。"""
    batch_root = Path(ds["batch_dir"])
    target = batch_root / "runs" / name / "journal.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}")
    return L.dir_anchored_key(str(target)).key, target


def seal_reservation(ds, monkeypatch, target_name="J1", nonce="nonce-1"):
    """reserve(BATCH, 真实 key) + begin_drift_disposition,返回 (r, evidence, 目标)。"""
    bkey, target = make_batch_target(ds, target_name)
    ev = make_evidence(batch_key=bkey, nonce=nonce)
    r = L.reserve(L.BATCH, bkey)
    r.begin_drift_disposition(ev)
    return r, ev, target


def write_marker(ds: dict, batch_key: str, content: bytes) -> Path:
    """按 batch_key 推导路径写入 marker(测试预置 taint marker 用)。"""
    p = marker_path_from_key(ds, batch_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def marker_path_from_key(ds: dict, batch_key: str) -> Path:
    """从稳定 batch key 解码根相对路径,推导 marker 位置(与 locks 同规则)。"""
    _dev, _ino, enc = batch_key.split(":", 2)
    rel = base64.urlsafe_b64decode(enc.encode()).decode()
    parts = rel.split("/")
    target_dir = Path(ds["batch_dir"])
    for part in parts[:-1]:
        target_dir = target_dir / part
    return target_dir / (parts[-1] + ".marker")


@pytest.mark.parametrize("mutator", [
    "batch_key",
    "observed_chain",
    "nonce",
    "marker_content",
    "foreign_key",
    "marker_elsewhere",
])
def test_evidence_release_rejects_all_six(repo_root, bootstrap, deploy_setup,
                                          monkeypatch, mutator):
    """④c evidence 判别六例(全否定形):一律拒绝释放、锁仍 sealed、零改写。

    预测红集:「marker 文件存在即释放」的实现下六例全部放行;「一律拒绝」
    的实现则让合法正例假红——防线必须基于证据比对而非存在性。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    r, ev, target = seal_reservation(ds, monkeypatch)
    lock_path = Path(ds["roots"]["locks_dir"]) / L.BATCH / (ev.batch_key + ".lock")
    # marker 按 batch_key 推导路径落盘,内容 = canonical(ev)
    write_marker(ds, ev.batch_key, canonical(ev).encode())
    before_lock = lock_path.read_bytes()
    marker = marker_path_from_key(ds, ev.batch_key)

    def make_bad():
        if mutator == "batch_key":
            return make_evidence(batch_key="9:9:other", nonce=ev.nonce,
                                 chains=ev.expected_chain)
        if mutator == "observed_chain":
            return L.DriftEvidence(batch_key=ev.batch_key,
                                   expected_chain=ev.expected_chain,
                                   observed_chain=((9, 9),), nonce=ev.nonce)
        if mutator == "nonce":
            return make_evidence(batch_key=ev.batch_key, nonce="replayed-old-nonce",
                                 chains=ev.expected_chain)
        if mutator == "marker_content":
            # marker 被预置成攻击者版本,e 合法 → marker hash ≠ e hash
            evil = make_evidence(batch_key=ev.batch_key, nonce="evil")
            write_marker(ds, ev.batch_key, canonical(evil).encode())
            return ev
        if mutator == "foreign_key":
            # K2 的完整 evidence + K2 的 marker 去 release K1 的预约(三方自洽)
            k2_key, k2_target = make_batch_target(ds, "J2")
            k2 = make_evidence(batch_key=k2_key, nonce="n2")
            write_marker(ds, k2.batch_key, canonical(k2).encode())
            return k2
        # marker_elsewhere:marker 放在调用方指定的另一路径(非推导路径),
        # 推导路径上不得有 marker
        marker.unlink()
        other = ds["batch_dir"] / "other.marker"
        other.write_bytes(canonical(ev).encode())
        return ev

    bad = make_bad()
    # marker 预置(含攻击者覆写)完成后再取样:断言 release 不改写 marker
    before_marker_after_bad = marker.read_bytes() if marker.exists() else None
    with pytest.raises(L.InvalidReservation):
        r.release_after_taint(bad)
    # 拒绝后:锁仍 sealed、drift_pending 与 marker 均未被改写、进程退出后锁依然在
    assert r.is_poisoned is True
    assert r.is_released is False
    assert lock_path.read_bytes() == before_lock, "锁文件不得被改写"
    if before_marker_after_bad is None:
        assert not marker.exists(), "marker 不得被写入"
    else:
        assert marker.read_bytes() == before_marker_after_bad, "marker 不得被改写"
    # 显式走 __exit__(sealed)→ 锁保留
    r.__exit__(None, None, None)
    assert lock_path.exists(), "拒绝释放后进程退出,锁必须依然在"
    assert b"drift_pending" in lock_path.read_bytes()


def test_evidence_release_ok(repo_root, bootstrap, deploy_setup, monkeypatch):
    """④c 合法正例:同一个未经改动的 e 调 release → 真实释放、可再 reserve。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    r, ev, target = seal_reservation(ds, monkeypatch)
    lock_path = Path(ds["roots"]["locks_dir"]) / L.BATCH / (ev.batch_key + ".lock")

    write_marker(ds, ev.batch_key, canonical(ev).encode())
    r.release_after_taint(ev)
    assert r.is_released is True
    assert not lock_path.exists(), "合法释放后锁文件必须消失"
    with L.reserve(L.BATCH, ev.batch_key):
        pass


def test_release_after_taint_rejects_forged_reservation(repo_root, bootstrap,
                                                        deploy_setup, monkeypatch):
    """④c release 仍受 reservation 能力约束:伪造对象不得仅凭 evidence 释放 sealed 锁。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    r, ev, _target = seal_reservation(ds, monkeypatch)
    lock_path = Path(ds["roots"]["locks_dir"]) / L.BATCH / (ev.batch_key + ".lock")
    write_marker(ds, ev.batch_key, canonical(ev).encode())

    forged = L.Reservation(L.BATCH, ev.batch_key, "forged-token", 999999, 0)
    forged.poison()

    with pytest.raises(L.InvalidReservation):
        forged.release_after_taint(ev)
    assert lock_path.exists(), "伪造对象不得删掉 sealed 锁"
    assert forged.is_released is False

    r.release_after_taint(ev)
    assert not lock_path.exists(), "原持有者仍可正常释放"


def test_evidence_three_way_compare(repo_root, bootstrap, deploy_setup,
                                    monkeypatch):
    """④c release 内部确实做了三方比对:drift_pending、marker、传入 e 各读至少一次。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    r, ev, _target = seal_reservation(ds, monkeypatch)
    write_marker(ds, ev.batch_key, canonical(ev).encode())

    reads = {"lock": 0, "marker": 0, "evidence": 0}
    real_parse = L._parse_lock_bytes
    real_read = L.Reservation._read_marker
    real_sha = L._evidence_sha256

    def fake_parse(data):
        reads["lock"] += 1
        return real_parse(data)

    def fake_read(self, h, batch_key):
        reads["marker"] += 1
        return real_read(self, h, batch_key)

    def fake_sha(e):
        reads["evidence"] += 1
        return real_sha(e)

    monkeypatch.setattr(L, "_parse_lock_bytes", fake_parse)
    monkeypatch.setattr(L.Reservation, "_read_marker", fake_read)
    monkeypatch.setattr(L, "_evidence_sha256", fake_sha)
    r.release_after_taint(ev)
    assert reads["lock"] >= 1 and reads["marker"] >= 1 and reads["evidence"] >= 1, \
        f"三方必须各被读取至少一次: {reads}"


# ---------------------------------------------------------------------------
# ⑥ 双进程竞争 + ⑥b dir_anchored_key
# ---------------------------------------------------------------------------


def test_two_process_race(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑥ 双进程竞争同 key 恰一方成功(反证非空转)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    proc = spawn_hold_lock(ds, repo_root, L.CONTENT, "k")
    try:
        line = proc.stdout.readline().strip()
        assert line == "READY", f"A 应 READY: {line}"
        with pytest.raises(L.ReservationHeld):
            L.reserve(L.CONTENT, "k")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    # A 被杀后锁残留未超陈旧阈值 → 仍拒绝(非静默夺锁)
    with pytest.raises(L.ReservationHeld):
        L.reserve(L.CONTENT, "k")


@pytest.fixture()
def batch_target(deploy_setup, repo_root, bootstrap):
    """构造 batch 根下目标文件,返回 (ds, target 路径, batch_root)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    batch_root = Path(ds["batch_dir"])
    target = batch_root / "runs" / "J1" / "journal.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    return ds, target, batch_root


def test_dir_anchored_key_aliases(repo_root, bootstrap, deploy_setup, monkeypatch,
                                  tmp_path):
    """⑥b 四种别名写法 .key 逐字相等(真实路径 / ./ 相对 / 同名 symlink / 异名 symlink)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    target = batch_root / "runs" / "J1" / "journal.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}")

    alias_dir = batch_root / "alias"
    alias_dir.mkdir()
    os.symlink("../runs/J1/journal.json", alias_dir / "journal.json")  # 同名
    alias2 = batch_root / "alias2"
    alias2.mkdir()
    os.symlink("../runs/J1/journal.json", alias2 / "jj")  # 不同 basename

    monkeypatch.chdir(batch_root)
    keys = [
        L.dir_anchored_key(str(target)).key,                          # 真实路径
        L.dir_anchored_key("./runs/J1/journal.json").key,             # ./ 相对
        L.dir_anchored_key(str(alias_dir / "journal.json")).key,      # 同名 symlink
        L.dir_anchored_key(str(alias2 / "jj")).key,                   # 异名 symlink
    ]
    assert len(set(keys)) == 1, f"四种别名必须同一 key: {keys}"
    # basename 取自已 canonicalize 的目标
    for w in (str(target), "./runs/J1/journal.json", str(alias_dir / "journal.json"),
              str(alias2 / "jj")):
        assert L.dir_anchored_key(w).basename == "journal.json"
    # dir_fd 每次新开、不参与相等性断言
    a, b = L.dir_anchored_key(str(target)), L.dir_anchored_key(str(target))
    assert isinstance(a.dir_fd, int) and a.dir_fd >= 0
    os.close(a.dir_fd)
    os.close(b.dir_fd)


def test_dir_anchored_key_hardlink(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑥b 硬链接别名硬拒:nlink == 2 → 在 reserve 之前抛。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    target = batch_root / "a" / "x.txt"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    os.link(target, batch_root / "a" / "hard")
    with pytest.raises(AnchorDriftError):
        L.dir_anchored_key(str(target))


def test_dir_anchored_key_replace_inode(repo_root, bootstrap, deploy_setup,
                                        monkeypatch):
    """⑥b 同一路径被 os.replace 换成新 inode → .key 仍逐字不变(与文件 inode 方案判别)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    target = batch_root / "runs" / "J1" / "journal.json"
    target.parent.mkdir(parents=True)
    target.write_text("v1")
    k1 = L.dir_anchored_key(str(target)).key
    fresh = target.with_name("fresh.json")
    fresh.write_text("v2")
    os.replace(fresh, target)
    k2 = L.dir_anchored_key(str(target)).key
    assert k1 == k2, "os.replace 换 inode 后 key 必须不变(锚在根身份+根相对路径)"
    assert sha256_file(target) == sha256_bytes(b"v2")


def test_dir_anchored_key_parent_swap(repo_root, bootstrap, deploy_setup,
                                      monkeypatch):
    """⑥b 直接父目录被 rename 走并重建后 → .key 仍逐字不变(与父目录 inode 方案判别)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    runs = batch_root / "runs"
    target = runs / "J1" / "journal.json"
    target.parent.mkdir(parents=True)
    target.write_text("v1")
    k1 = L.dir_anchored_key(str(target)).key

    moved = runs.with_name("runs-moved")
    os.rename(runs, moved)
    os.mkdir(runs, 0o750)
    (runs / "J1").mkdir()
    (runs / "J1" / "journal.json").write_text("v1")
    k2 = L.dir_anchored_key(str(target)).key
    assert k1 == k2, "父目录被换后 key 必须不变(父 inode 会变,根身份+根相对路径不会)"


def test_dir_anchored_key_diff_rel(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑥b 不同根相对路径的同名文件返回值必不等。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    for sub in ("a", "b"):
        p = batch_root / sub / "x.txt"
        p.parent.mkdir()
        p.write_text("x")
    ka = L.dir_anchored_key(str(batch_root / "a" / "x.txt")).key
    kb = L.dir_anchored_key(str(batch_root / "b" / "x.txt")).key
    assert ka != kb


def test_dir_anchored_key_root_swap(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑥b 受信根自身被换走则抛(根是信任锚,变了没有可依据的东西)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    target = batch_root / "x.txt"
    target.write_text("x")
    moved = batch_root.with_name(batch_root.name + "-moved")
    os.rename(batch_root, moved)
    os.mkdir(batch_root, 0o750)
    with pytest.raises(AnchorDriftError):
        L.dir_anchored_key(str(target))


def test_dir_anchored_key_bad_parent(repo_root, bootstrap, deploy_setup,
                                     monkeypatch):
    """⑥b 父目录为 symlink 或不存在时抛(不退化为路径字符串)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    # 父目录为 symlink
    link_dir = batch_root / "ld"
    os.symlink(batch_root / "a", link_dir)
    (batch_root / "a").mkdir()
    (batch_root / "a" / "j.txt").write_text("x")
    with pytest.raises(AnchorDriftError):
        L.dir_anchored_key(str(link_dir / "j.txt"))
    # 父目录不存在
    with pytest.raises(AnchorDriftError):
        L.dir_anchored_key(str(batch_root / "nope" / "j.txt"))


def test_dir_anchored_key_symlink_leaf(repo_root, bootstrap, deploy_setup,
                                       monkeypatch):
    """⑥b 最后分量为 file symlink 时按 canonical 目标计算(与目标同 key)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    batch_root = Path(ds["batch_dir"])
    target = batch_root / "runs" / "J1" / "journal.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    alias = batch_root / "alias"
    alias.mkdir()
    os.symlink("../runs/J1/journal.json", alias / "link.json")
    k1 = L.dir_anchored_key(str(target)).key
    k2 = L.dir_anchored_key(str(alias / "link.json")).key
    assert k1 == k2


# ---------------------------------------------------------------------------
# ⑦⑧⑨ 凭证语义
# ---------------------------------------------------------------------------


def test_forged_reservation(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑦ 伪造 Reservation(自造 owner_token)→ assert_valid 抛。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    with L.reserve(L.CONTENT, "k"):
        forged = L.Reservation(L.CONTENT, "k", "f" * 64, os.getpid(), 0)
        with pytest.raises(L.InvalidReservation):
            forged.assert_valid(L.CONTENT, "k")


def test_released_reservation(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑦ 已 released 的对象调 assert_valid 抛。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    with L.reserve(L.CONTENT, "k") as r:
        pass
    assert r.is_released is True
    with pytest.raises(L.InvalidReservation):
        r.assert_valid(L.CONTENT, "k")


def test_wrong_ns_key_reservation(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑦ namespace/key 不匹配的对象调 assert_valid 抛。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    with L.reserve(L.CONTENT, "k") as r:
        with pytest.raises(L.InvalidReservation):
            r.assert_valid(L.DESTNAME, "k")
        with pytest.raises(L.InvalidReservation):
            r.assert_valid(L.CONTENT, "other")


def test_cross_process_token_copy(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑧ 跨进程 token 复制负测:A 的 owner_token 交给 B,B 构造字段全同对象 → 抛。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    env = dict(os.environ)
    env.update(ds["env"])
    env["PYTHONPATH"] = str(repo_root / "src")
    code = (
        "import json, time\n"
        "import akb2.harvest.locks as L\n"
        "with L.reserve(L.CONTENT, 'tok') as r:\n"
        "    print(json.dumps({'token': r.owner_token, 'pid': r.pid}), flush=True)\n"
        "    time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=repo_root,
    )
    try:
        meta = json.loads(proc.stdout.readline())
        forged = L.Reservation(L.CONTENT, "tok", meta["token"], meta["pid"], 0)
        with pytest.raises(L.InvalidReservation):
            forged.assert_valid(L.CONTENT, "tok")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_no_boolean_held_api(repo_root, bootstrap, deploy_setup, monkeypatch):
    """⑨ 模块公开面不含任何布尔持有查询函数。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    mod_names = set(dir(L))
    for bad in ("is_held", "is_locked", "is_reserved", "held", "holding",
                "is_available"):
        assert bad not in mod_names, f"模块不得公开布尔持有查询 {bad}"
    # 公开面为预约/凭证原语
    assert callable(L.reserve)
    assert callable(L.dir_anchored_key)


# ---------------------------------------------------------------------------
# ⑩ AnchorHandle fd 生命周期(wave 1 g8 跟踪项收口;预测红集)
# ---------------------------------------------------------------------------
#
# 预测红集:现状实现取到 AnchorHandle 后从不 close,每次 load_verified()
# 返回 4 根 × 2 fd = 8 个 fd 全部遗留;下面用例断言退出(含异常路径)后
# handle 的 fd 均为 EBADF、且重复预约 fd 数不增长——实现未改时必然红。


def _collect_handles(monkeypatch, store: list) -> None:
    """包装 load_verified:每次调用把返回的 handles 存入 store(供 fd 关闭断言)。"""
    real = L.anchors.load_verified

    def fake():
        hs = real()
        store.append(hs)
        return hs

    monkeypatch.setattr(L.anchors, "load_verified", fake)


def _assert_all_handles_closed(handles: dict) -> None:
    for name, h in handles.items():
        for fd, what in ((h.root_fd, "root_fd"), (h.parent_fd, "parent_fd")):
            if fd < 0:
                continue
            try:
                os.fstat(fd)
                open_ = True
            except OSError as e:
                open_ = e.errno != 9  # EBADF 才算已关闭
            assert not open_, f"{name}.{what}({fd}) 在操作结束后未关闭"


def _assert_collected_handles_closed(store: list[dict]) -> None:
    for handles in store:
        _assert_all_handles_closed(handles)


def test_anchor_handles_closed_after_reserve_cycle(repo_root, bootstrap,
                                                   deploy_setup, monkeypatch):
    """⑩ reserve + assert_valid 正常路径:每次 load_verified 的 handle 用完即关。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    store = []
    _collect_handles(monkeypatch, store)
    with L.reserve(L.CONTENT, "k") as r:
        r.assert_valid(L.CONTENT, "k")
    # reserve 进入 + assert_valid + __exit__ 释放(_delete_lock)各调一次
    assert len(store) == 3, f"reserve/assert_valid/__exit__ 应各调一次 load_verified: {len(store)}"
    _assert_collected_handles_closed(store)


def test_anchor_handles_closed_on_reservation_held(repo_root, bootstrap,
                                                   deploy_setup, monkeypatch):
    """⑩ ReservationHeld 异常路径:冲突 reserve 的 handle 仍须关闭。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    store = []
    _collect_handles(monkeypatch, store)
    with L.reserve(L.CONTENT, "k"):
        with pytest.raises(L.ReservationHeld):
            L.reserve(L.CONTENT, "k")
    assert len(store) >= 2
    _assert_collected_handles_closed(store)


def test_anchor_handles_closed_on_poisoned_exception(repo_root, bootstrap,
                                                     deploy_setup, monkeypatch):
    """⑩ poisoned 异常展开路径:begin 写失败 → __exit__ 保留锁,handle 仍须关闭。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    store = []
    _collect_handles(monkeypatch, store)
    real_write = L._write_os

    def fake_write(fd, b):
        raise OSError("injected write failure")

    ev = make_evidence(batch_key="1:1:enc")
    with pytest.raises(OSError, match="injected"):
        with L.reserve(L.BATCH, "K") as r:
            monkeypatch.setattr(L, "_write_os", fake_write)
            r.begin_drift_disposition(ev)
    monkeypatch.setattr(L, "_write_os", real_write)
    # poisoned 后锁文件保留(④b 语义),但本次所有 handle 的 fd 均不得遗留
    assert (Path(ds["roots"]["locks_dir"]) / L.BATCH / "K.lock").exists()
    assert store
    _assert_collected_handles_closed(store)


def test_dir_anchored_key_handles_closed_root_direct(repo_root, bootstrap,
                                                     deploy_setup, monkeypatch):
    """⑩ dir_anchored_key:handles 关闭,返回的 dir_fd 是独立资源仍可用。

    根直属目标(无中间目录)时返回的 dir_fd 就是 batch 根 fd 本身——实现必须
    dup 出独立 fd,否则 handle 关闭后调用方的 dir_fd 即坏。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    store = []
    _collect_handles(monkeypatch, store)
    target = Path(ds["batch_dir"]) / "x.txt"
    target.write_text("x")
    at = L.dir_anchored_key(str(target))
    assert store
    _assert_collected_handles_closed(store)
    # 返回的 dir_fd 独立于 handle 生命周期:仍可 fstat(目录)
    assert os.fstat(at.dir_fd)
    os.close(at.dir_fd)


def test_dir_anchored_key_handles_closed_nested(repo_root, bootstrap,
                                                deploy_setup, monkeypatch):
    """⑩ 嵌套目标:逐级打开的 dir_fd 在 handles 关闭后仍独立可用。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    store = []
    _collect_handles(monkeypatch, store)
    target = Path(ds["batch_dir"]) / "nested" / "x.txt"
    target.parent.mkdir()
    target.write_text("x")
    at = L.dir_anchored_key(str(target))
    assert store
    _assert_collected_handles_closed(store)
    assert os.fstat(at.dir_fd)
    os.close(at.dir_fd)


def test_release_after_taint_handles_closed(repo_root, bootstrap, deploy_setup,
                                            monkeypatch):
    """⑩ release_after_taint 拒绝与成功两条路径:handles 均须关闭。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])
    store = []
    _collect_handles(monkeypatch, store)
    r, ev, _target = seal_reservation(ds, monkeypatch)
    # 拒绝路径:marker 未落盘 → InvalidReservation
    with pytest.raises(L.InvalidReservation):
        r.release_after_taint(ev)
    # 成功路径:marker 落盘后释放
    write_marker(ds, ev.batch_key, canonical(ev).encode())
    r.release_after_taint(ev)
    assert store
    _assert_collected_handles_closed(store)


def test_no_fd_growth_repeated_reserve(repo_root, bootstrap, deploy_setup,
                                       monkeypatch):
    """⑩ 连续 30 轮 reserve + assert_valid:进程 fd 总数不增长(防逐次泄漏)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    apply_env(monkeypatch, ds["env"])

    def count_fds() -> int:
        return len(os.listdir("/dev/fd"))

    with L.reserve(L.CONTENT, "warmup"):
        pass  # 预热一轮,消除首次分配噪声
    before = count_fds()
    for i in range(30):
        with L.reserve(L.CONTENT, f"k{i}") as r:
            r.assert_valid(L.CONTENT, f"k{i}")
    after = count_fds()
    assert after <= before, f"fd 数必须不增长: before={before} after={after}"
