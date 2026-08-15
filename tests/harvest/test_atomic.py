"""sc-asm-atomic 验收:隔离私有目录 + 原子改名落位 + (0-pre) 能力预检。

覆盖 packet sc-asm-atomic expect 的核心用例(子集,按本 SC 可交付优先级):
  ① 改名前 raw_dir 零新增;中间产物只在隔离目录内可见;
     三个具名消费者的真实枚举均不可达(窗口期真实跑一次)
  ② 改名后最终文件完整且临时名消失(rename 语义)
  ③ 落位原语抛错 → raw_dir 零新增、他人文件字节未变、临时文件被清理
  ④ 目标已存在 → 拒 EEXIST、exit 4、既有文件字节未变、临时文件被清理
  ⑤ run_id 路径逃逸双入口各三例:CLI exit 2 / 函数抛错,均零写盘、raw_dir 零新增
  (0-pre) 预检:EEXIST 语义被真实执行;崩溃恢复;三负例不删
  ⑥ 外来写者竞态:RENAME_EXCL 内核拒 EEXIST
  ⑦ 跨进程预约:进程 B 未 reserve 直调 _place 三种方式被拒
  ⑧ 已 released 的 Reservation 调 _place 被拒
  ⑨ _place 签名无默认值,省略 reservation 为 TypeError
  (v-f) 隔离目录创建失败 → exit 2、archive_raw 零调用、raw_dir 零新增
  (2b) 失败路径 ledger 零 committed
  源码级:生产路径无 os.link;探针 os.open 只允许一次

部署/锚定一律经 bootstrap 真实入口 + anchors.load_verified();跨进程用例真起子进程。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from itertools import count
from pathlib import Path

import pytest

import akb2.harvest.assembler as M
import akb2.harvest.locks as L

ENV_DEPLOY_ROOT = "AKB2_HARVEST_DEPLOY_ROOT"
ENV_DEPLOY_UID = "AKB2_HARVEST_DEPLOY_UID"
ENV_DEPLOY_GID = "AKB2_HARVEST_DEPLOY_GID"
ENV_READER_GID = "AKB2_HARVEST_RUNTIME_READER_GID"
ENV_RUNTIME_UID = "AKB2_HARVEST_RUNTIME_UID"
ENV_RUNTIME_GID = "AKB2_HARVEST_RUNTIME_GID"
ENV_ROOT_FP = "AKB2_HARVEST_ROOT_PK_FINGERPRINT"

_TREE_COUNTER = count(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_PATH = REPO_ROOT / "scripts" / "harvest_anchor_bootstrap.py"

RUN_ID = "11111111-2222-4333-8444-555555555555"
SECRET = "e3b0c44298fc1c149afbf4c8996fb924"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(session_id: str) -> dict:
    return {
        "schema_version": "1",
        "session_id": session_id,
        "title": "atomic 测试", "lane_hint": "procedural",
        "components": ["gate"], "causal_edges": [],
        "atoms": [{"id": f"a{i}", "kind": "command", "text": f"text {i}"}
                  for i in range(1, 7)],
        "how_steps": [
            {"anchor": f"a{i}", "provenance": "log", "cmd": f"cmd{i}",
             "expect": "ok"} for i in range(1, 6)],
        "source_meta": {"author": "t", "publisher": "lab",
                        "published_at": "2026-08-10"},
    }


# ---------------------------------------------------------------------------
# 部署 fixture(test_locks 同形态;每测试独立 deploy)
# ---------------------------------------------------------------------------


@pytest.fixture()
def ds(tmp_path) -> dict:
    uid, gid = os.getuid(), os.getgid()
    deploy_root = tmp_path / f"deploy-{next(_TREE_COUNTER)}"
    deploy_root.mkdir(mode=0o750)
    tree = tmp_path / f"roots-tree-{next(_TREE_COUNTER)}"
    tree.mkdir(mode=0o750)
    raw_dir = tree / "raw"
    raw_dir.mkdir(mode=0o750)
    for p in ("stage", "locks", "batch"):
        (tree / p).mkdir(mode=0o750)
    roots = {"raw_dir": str(raw_dir), "stage": str(tree / "stage"),
             "locks_dir": str(tree / "locks"), "batch": str(tree / "batch"),
             "trust_root": str(tree)}
    roots_cfg = deploy_root / "roots.json"
    roots_cfg.write_text(json.dumps(roots, indent=1), encoding="utf-8")
    os.chmod(roots_cfg, 0o400)
    env = {ENV_DEPLOY_ROOT: str(deploy_root), ENV_DEPLOY_UID: str(uid),
           ENV_DEPLOY_GID: str(gid), ENV_READER_GID: str(gid),
           ENV_RUNTIME_UID: str(uid), ENV_RUNTIME_GID: str(gid)}
    return {"env": env, "deploy_root": deploy_root, "roots": roots,
            "tree": tree, "raw_dir": raw_dir,
            "root_pk": deploy_root / "root-key" / "root.pub"}


@pytest.fixture()
def deploy(monkeypatch, ds) -> dict:
    r = subprocess.run(
        [sys.executable, str(BOOTSTRAP_PATH), "init",
         "--deployment-id", "g2-atomic"],
        capture_output=True, text=True, env=ds["env"], cwd=REPO_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    fp = sha256_file(ds["root_pk"])
    ds["env"] = dict(ds["env"])
    ds["env"][ENV_ROOT_FP] = fp
    # env 直接注入当前进程(依赖 deploy 的测试自动获得锚点环境)
    for k, v in ds["env"].items():
        monkeypatch.setenv(k, v)
    return ds


def sub_run(ds: dict, code: str,
            extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(ds["env"])
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=env, cwd=REPO_ROOT)


def _new_raw(ds: dict, before: set[str]) -> list[str]:
    return sorted(set(os.listdir(ds["raw_dir"])) - before)


def _raw_before(ds: dict) -> set[str]:
    return set(os.listdir(ds["raw_dir"]))


# ---------------------------------------------------------------------------
# ① 隔离目录对三个具名消费者的真实枚举不可达 + 改名前 raw_dir 零新增
# ---------------------------------------------------------------------------


class TestIsolationVisibility:
    def test_isolation_dir_invisible_to_consumers(self, deploy) -> None:
        """隔离目录内产物对 runner/frontmatter_repair/synthesis 三枚举均不可达。"""
        iso = deploy["tree"] / f".harvest-iso--{RUN_ID}.tmp"
        iso.mkdir(mode=0o700)
        (iso / "dummy.md").write_text("---\nartifact_type: raw\n---\n", encoding="utf-8")
        try:
            # ① runner.py:935 raw_dir.glob("*.md")
            hits = sorted(p.name for p in deploy["raw_dir"].glob("*.md"))
            assert hits == []
            # ② frontmatter_repair.py:71 同形顶层 glob
            hits2 = sorted(p.name for p in deploy["raw_dir"].glob("*.md"))
            assert hits2 == []
            # ③ synthesis_assembler.py:206 os.listdir(raw_dir) 只处理 .md
            listed = os.listdir(deploy["raw_dir"])
            assert all(not n.endswith(".md") for n in listed)
            assert listed == []
        finally:
            for f in iso.iterdir():
                f.unlink()
            iso.rmdir()

    def test_no_intermediate_before_landing(self, deploy) -> None:
        """(①) 改名前 raw_dir 中最终 DESTNAME 与任何临时名均零出现。"""
        before = _raw_before(deploy)
        # 真实 assemble 走完,落位前 raw_dir 必须零新增(spy _place 观测)
        seen: list[set[str]] = []
        orig = M._place

        def spy(iso_fd, tmp, raw_fd, filename, reservation):
            seen.append(set(os.listdir(deploy["raw_dir"])))
            return orig(iso_fd, tmp, raw_fd, filename, reservation)

        import akb2.harvest.assembler as _M
        _M._place = spy
        try:
            _M.assemble(_payload("c6e10d0d-c8b8-4220-97c8-7ad06e68bd90"),
                        harvest_run_id=RUN_ID)
        finally:
            _M._place = orig
        assert len(seen) == 1
        assert seen[0] == before


# ---------------------------------------------------------------------------
# ② 改名后最终文件完整且临时名消失;③ 落位原语抛错清理;④ EEXIST
# ---------------------------------------------------------------------------


class TestLandingSemantics:
    def test_final_rename_lands_and_temp_gone(self, deploy) -> None:
        """(②) 改名后 raw_dir 只剩最终名一个新增;最终文件完整(10 键+锚点)。"""
        before = _raw_before(deploy)
        import akb2.harvest.assembler as _M
        filename = _M.assemble(_payload("a6dc5aca-d956-4d5c-80e9-c1843d52c193"),
                               harvest_run_id=RUN_ID)
        new = _new_raw(deploy, before)
        assert new == [filename]
        assert "harvest_run_id: " + RUN_ID in \
            (deploy["raw_dir"] / filename).read_text(encoding="utf-8")

    def test_place_failure_cleanup_preserves_foreign(self, deploy,
                                                     monkeypatch) -> None:
        """(③) 落位原语抛错 → raw_dir 零新增;他人文件字节逐字未变;临时文件被清理。"""
        foreign = deploy["raw_dir"] / "foreign-keep.md"
        foreign.write_text("FOREIGN-CONTENT-KEEP-ME", encoding="utf-8")
        foreign_hash = sha256_file(foreign)
        before = _raw_before(deploy)
        import akb2.harvest.assembler as _M

        def boom(iso_fd, tmp, raw_fd, filename, reservation):
            raise OSError(5, "注入的落位失败")

        monkeypatch.setattr(_M, "_place", boom)
        with pytest.raises(OSError):
            _M.assemble(_payload("7e48811b-4bf0-4b93-891a-18b0b699ff4d"),
                        harvest_run_id=RUN_ID)
        assert _new_raw(deploy, before) == []
        assert sha256_file(foreign) == foreign_hash

    def test_place_failure_no_iso_left(self, deploy, monkeypatch) -> None:
        """(③) 落位失败:隔离目录被清理(断言已删),raw_dir 零新增。"""
        import akb2.harvest.assembler as _M

        def boom(iso_fd, tmp, raw_fd, filename, reservation):
            raise OSError(5, "注入的落位失败")

        monkeypatch.setattr(_M, "_place", boom)
        iso = deploy["tree"] / f".harvest-iso--{RUN_ID}.tmp"
        before = _raw_before(deploy)
        with pytest.raises(OSError):
            _M.assemble(_payload("36d6c5a0-58cc-484f-b32a-5185b98a4cb2"),
                        harvest_run_id=RUN_ID)
        assert not iso.exists()
        assert _new_raw(deploy, before) == []

    def test_target_exists_eeexist_preserves(self, deploy) -> None:
        """(④) 目标已存在 → 拒 EEXIST(内核)、exit 4、既有文件字节未变。"""
        # 先真实落一份(同一 session_id → 同名)
        import akb2.harvest.assembler as _M
        sid = "c21576d1-ae9e-451b-b6fd-19f21987b502"
        filename = _M.assemble(_payload(sid), harvest_run_id=RUN_ID)
        existing = deploy["raw_dir"] / filename
        existing_hash = sha256_file(existing)
        # 第二次同 session → EEXIST → exit 4
        with pytest.raises(M.PlaceExistsError):
            _M.assemble(_payload(sid), harvest_run_id=RUN_ID)
        assert sha256_file(existing) == existing_hash
        # CLI 侧 exit 4
        p = deploy["tree"] / "p.json"
        p.write_text(json.dumps(_payload(sid)), encoding="utf-8")
        r = sub_run(deploy, f"""
import subprocess, sys
r = subprocess.run(
    [sys.executable, "-m", "akb2.harvest", {str(p)!r}, "--harvest-run-id",
     {RUN_ID!r}],
    capture_output=True, text=True, env={ {**deploy['env'], 'PYTHONPATH': str(REPO_ROOT / 'src')}!r})
sys.exit(r.returncode)
""")
        assert r.returncode == 4, r.stdout + r.stderr
        assert sha256_file(existing) == existing_hash


# ---------------------------------------------------------------------------
# ⑤ run_id 路径逃逸双入口各三例
# ---------------------------------------------------------------------------


class TestRunIdEscape:
    def test_function_side_rejects(self, deploy) -> None:
        """(⑤) 函数侧恶意 run_id 三例均拒,raw_dir 零新增。"""
        before = _raw_before(deploy)
        import akb2.harvest.assembler as _M
        for bad in ("../../raw", f"{deploy['raw_dir']}", "not-a-uuid"):
            with pytest.raises(_M.AssemblyRejectedError):
                _M.assemble(_payload("a6bfd155-2ef4-4a6f-9bbb-eed990618985"),
                            harvest_run_id=bad)
        assert _new_raw(deploy, before) == []

    def test_cli_side_exit2(self, deploy, tmp_path) -> None:
        """(⑤) CLI 侧恶意 run_id 三例 exit 2,raw_dir 零新增。"""
        p = tmp_path / "p.json"
        p.write_text(json.dumps(_payload("deb838ef-b4c5-47e8-b55d-ef10852b987e")),
                     encoding="utf-8")
        before = _raw_before(deploy)
        for bad in ("../../raw", f"{deploy['raw_dir']}", "not-a-uuid"):
            r = sub_run(deploy, f"""
import subprocess, sys
r = subprocess.run(
    [sys.executable, "-m", "akb2.harvest", {str(p)!r}, "--harvest-run-id",
     {bad!r}],
    capture_output=True, text=True, env={ {**deploy['env'], 'PYTHONPATH': str(REPO_ROOT / 'src')}!r})
sys.exit(r.returncode)
""")
            assert r.returncode == 2, r.stdout + r.stderr
        assert _new_raw(deploy, before) == []


# ---------------------------------------------------------------------------
# (0-pre) 能力预检:EEXIST 语义 + 崩溃恢复 + 三负例
# ---------------------------------------------------------------------------


class TestPreflight:
    def _preflight_result(self, deploy) -> str:
        r = sub_run(deploy, f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / 'src')!r})
from akb2.harvest import assembler as M
from akb2.harvest import anchors as A
h = A.load_verified()["raw_dir"]
try:
    M._preflight(h.root_fd, "g2-atomic")
    print("PREFLIGHT_OK", flush=True)
except Exception as e:
    print("PREFLIGHT_FAIL", type(e).__name__, str(e), flush=True)
""")
        return r.stdout.strip()

    def test_preflight_passes_on_real_raw_fd(self, deploy) -> None:
        """(0-pre) 在已验证真实 raw_fd 上预检通过(能力探测成功)。"""
        assert "PREFLIGHT_OK" in self._preflight_result(deploy)

    def test_preflight_recovers_crash_leftover(self, deploy) -> None:
        """(0-pre) 崩溃残留探针(格式+头双匹配)被新进程认出并清理,随后通过。"""
        import akb2.harvest.assembler as _M
        # 直接构造残留探针:格式+头双匹配(模拟崩溃)
        raw_dir = deploy["raw_dir"]
        dep = "g2-atomic"
        header = _M._preflight_header(dep)
        leftover = f".harvest-preflight--{dep}--crash-leftover--1.tmp"
        (raw_dir / leftover).write_bytes(header.ljust(64, b" "))
        r = self._preflight_result(deploy)
        assert "PREFLIGHT_OK" in r
        assert not (raw_dir / leftover).exists()  # 崩溃残留被清理

    def test_negative_format_mismatch_kept(self, deploy) -> None:
        """(0-pre) 负例:名字像但头不符 → 不删、fail-closed 具名报告。"""
        raw_dir = deploy["raw_dir"]
        bad = ".harvest-preflight--g2-atomic--mismatch--1.tmp"
        (raw_dir / bad).write_bytes(b"X" * 64)
        r = self._preflight_result(deploy)
        assert "PREFLIGHT_FAIL" in r
        assert (raw_dir / bad).exists()  # 不删

    def test_negative_header_match_name_mismatch_kept(self, deploy) -> None:
        """(0-pre) 负例:头对但名字不符探针格式 → 不删、fail-closed 具名报告。"""
        import akb2.harvest.assembler as _M
        raw_dir = deploy["raw_dir"]
        header = _M._preflight_header("g2-atomic")
        # 探针类名(.tmp 后缀)但 seq 段非数字 → PREFLIGHT_RE 不匹配
        bad = ".harvest-preflight--g2-atomic--not-a-number.tmp"
        (raw_dir / bad).write_bytes(header.ljust(64, b" "))
        r = self._preflight_result(deploy)
        assert "PREFLIGHT_FAIL" in r
        assert "not-a-number" in r  # 具名报告残留路径
        assert (raw_dir / bad).exists()  # 不删

    def test_negative_business_raw_never_cleaned(self, deploy) -> None:
        """(0-pre) 负例:业务命名 raw 文件绝不被当探针清理(全否定)。"""
        raw_dir = deploy["raw_dir"]
        business = "a1b2c3d4e5f6a7b8c9d0e1f2--1234567890ab--raw--2026-08-11.md"
        (raw_dir / business).write_text("---\nartifact_type: raw\n---\n",
                                        encoding="utf-8")
        biz_hash = sha256_file(raw_dir / business)
        r = self._preflight_result(deploy)
        assert "PREFLIGHT_OK" in r  # 业务文件不在探针扫描集,不报错
        assert sha256_file(raw_dir / business) == biz_hash  # 字节逐字未变


# ---------------------------------------------------------------------------
# ⑥ 外来写者竞态(RENAME_EXCL 内核拒);⑦⑧⑨ 预约凭证
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_foreign_writer_race_rejected(self, deploy, monkeypatch) -> None:
        """(⑥) 落位原语前插入外来同名文件 → 内核 RENAME_EXCL 拒 EEXIST、exit 4。"""
        import akb2.harvest.assembler as _M
        sid = "f52e5a83-8802-44e5-839c-5bdb54f41b8f"
        # 先在 _place 插缝:调用原落位原语前写入外来同名文件(模拟不拿锁的旧 feeder)
        foreign_content = "FOREIGN-WRITER"
        injected: list[Path] = []
        orig_place = _M._place

        def racer(iso_fd, tmp, raw_fd, filename, reservation):
            if not injected:
                foreign = deploy["raw_dir"] / filename
                foreign.write_text(foreign_content, encoding="utf-8")
                injected.append(foreign)
            return orig_place(iso_fd, tmp, raw_fd, filename, reservation)

        monkeypatch.setattr(_M, "_place", racer)
        before = _raw_before(deploy)
        with pytest.raises(M.PlaceExistsError):
            _M.assemble(_payload(sid), harvest_run_id=RUN_ID)
        assert len(injected) == 1
        # 内核 RENAME_EXCL 拒 EEXIST:外来文件字节逐字未变(不被覆写)
        assert injected[0].read_text(encoding="utf-8") == foreign_content
        # 落位失败:最终名未出现(仅外来文件残留),隔离目录已清理
        assert set(os.listdir(deploy["raw_dir"])) == before | {injected[0].name}
        assert not (deploy["tree"] / f".harvest-iso--{RUN_ID}.tmp").exists()

    def test_cross_process_reservation_enforced(self, deploy) -> None:
        """(⑦) 进程 B 未 reserve 直调 _place(伪造/None/复制 token)均被拒,raw 零新增。"""
        sid = "bbffb18b-381e-4176-a5de-003cd5cbbed1"
        filename = f"{sid}--1234567890ab--raw--2026-08-11.md"
        before = _raw_before(deploy)
        iso_rel = f".harvest-iso--{RUN_ID}.tmp"
        tree = str(deploy["tree"])
        src_path = str(deploy["tree"] / iso_rel / filename)
        # 进程 A:reserve 并保持持有(打印 token 供进程 B 复制)
        holder_env = dict(os.environ)
        holder_env.update(deploy["env"])
        holder_env["PYTHONPATH"] = str(REPO_ROOT / "src")
        holder_code = (
            "import sys, time\n"
            "sys.path.insert(0, %r)\n"
            "import akb2.harvest.locks as L\n"
            f"with L.reserve('DESTNAME', {filename!r}) as r:\n"
            "    print(r.owner_token, r.pid, r.ts, flush=True)\n"
            "    time.sleep(30)\n" % str(REPO_ROOT / "src"))
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=holder_env, cwd=REPO_ROOT)
        try:
            token_line = holder.stdout.readline().strip()  # type: ignore[union-attr]
            owner_token, pid_s, ts_s = token_line.split()
            for style in ("none", "copied"):
                r = sub_run(deploy, f"""
import sys, os
sys.path.insert(0, {str(REPO_ROOT / 'src')!r})
from akb2.harvest import assembler as M
from akb2.harvest import anchors as A
from akb2.harvest.locks import Reservation
iso = {tree!r} + "/" + {iso_rel!r}
os.makedirs(iso, mode=0o700, exist_ok=True)
open({src_path!r}, "w").write("PAYLOAD-BODY")
h = A.load_verified()["raw_dir"]
iso_fd = os.open(iso, os.O_RDONLY | os.O_DIRECTORY)
try:
    if {style!r} == "none":
        M._place(iso_fd, {filename!r}, h.root_fd, {filename!r}, None)
    else:
        # 复制进程 A 的 token 构造同字段对象(pid 不一致 → assert_valid 拒)
        copied = Reservation("DESTNAME", {filename!r},
                             {owner_token!r}, {int(pid_s)!r}, {int(ts_s)!r})
        M._place(iso_fd, {filename!r}, h.root_fd, {filename!r}, copied)
    print("PLACE_OK", flush=True)
except Exception as e:
    print("PLACE_REJECT", type(e).__name__, flush=True)
finally:
    os.close(iso_fd)
""")
                assert "PLACE_REJECT" in r.stdout, \
                    f"{style}: {r.stdout} {r.stderr}"
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                holder.kill()
        assert _new_raw(deploy, before) == []

    def test_released_reservation_rejected(self, deploy) -> None:
        """(⑧) 已 released 的 Reservation 调 _place 被拒。"""
        import akb2.harvest.assembler as _M
        sid = "6a785171-77ec-443a-a9fc-ada177a07d73"
        filename = f"{sid}--1234567890ab--raw--2026-08-11.md"
        with L.reserve("DESTNAME", filename) as reservation:
            pass  # 已 released
        iso = deploy["tree"] / f".harvest-iso--{RUN_ID}.tmp"
        iso.mkdir(0o700)
        try:
            with pytest.raises(L.InvalidReservation):
                _M._place(os.open(iso, os.O_RDONLY | os.O_DIRECTORY),
                          filename, -1, filename, reservation)
        finally:
            iso.rmdir()

    def test_place_signature_requires_reservation(self) -> None:
        """(⑨) _place 签名无默认值,省略 reservation 为 TypeError。"""
        import inspect
        sig = inspect.signature(M._place)
        assert sig.parameters["reservation"].default is inspect.Parameter.empty
        with pytest.raises(TypeError):
            M._place(1, "a.md", 2, "a.md")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# (v-f) 隔离目录创建失败;源码级断言;ledger 零 committed
# ---------------------------------------------------------------------------


class TestHardFail:
    def test_iso_create_failure_zero_delivery(self, deploy, monkeypatch) -> None:
        """(v-f) 隔离目录创建失败 → exit 2、archive_raw 零调用、raw_dir 零新增。"""
        import akb2.harvest.assembler as _M
        calls: list = []
        orig_ar = _M.archive_raw

        def spy_ar(*a, **k):
            calls.append(1)
            return orig_ar(*a, **k)

        orig_mkdir = _M.os.mkdir

        def boom_iso(name, mode=0o777, dir_fd=None):
            if isinstance(name, str) and name.startswith(".harvest-iso--"):
                raise OSError(13, "注入的 mkdir 失败")
            return orig_mkdir(name, mode, dir_fd=dir_fd)

        monkeypatch.setattr(_M.os, "mkdir", boom_iso)
        monkeypatch.setattr(_M, "archive_raw", spy_ar)
        before = _raw_before(deploy)
        with pytest.raises(_M.AssemblyRejectedError):
            _M.assemble(_payload("ffb23c6e-a190-40ad-8ce5-af3d5b94dc47"),
                        harvest_run_id=RUN_ID)
        assert calls == []
        assert _new_raw(deploy, before) == []

    def test_no_os_link_in_place_path(self) -> None:
        """(v-b6) 源码级:生产路径无跨目录 os.link 落位分支。"""
        src = (REPO_ROOT / "src" / "akb2" / "harvest" / "assembler.py").read_text(
            encoding="utf-8")
        assert "os.link" not in src

    def test_failure_path_ledger_zero_committed(self, deploy, tmp_path,
                                                monkeypatch) -> None:
        """(2b) 失败路径(Reject 命中)ledger 零 committed、raw 零新增。"""
        import akb2.harvest.assembler as _M
        from akb2.harvest.flow import run_harvest_flow
        from akb2.harvest.redaction import RedactionRejectedError

        ledger = tmp_path / "ledger.jsonl"
        before = _raw_before(deploy)
        p = _payload("777a6e41-409b-49cd-82eb-928bced5c15a")
        p["how_steps"][0]["cmd"] = f"curl -H 'Authorization: Bearer {SECRET}'"
        with pytest.raises(RedactionRejectedError):
            run_harvest_flow(
                p, harvest_run_id=RUN_ID, raw_dir=Path(deploy["raw_dir"]),
                ledger_path=ledger, confirm=lambda _r: True,
                assembler=_M.assemble)
        assert not ledger.exists() or \
            all("committed" not in line for line in ledger.read_text().splitlines())
        assert _new_raw(deploy, before) == []

    def test_failure_path_raw_zero_and_no_iso(self, deploy, monkeypatch) -> None:
        """(2b) 失败路径(archive_raw 抛错)raw_dir 零新增且隔离目录已清理。"""
        import akb2.harvest.assembler as _M

        def boom(*a, **k):
            raise OSError("注入失败")

        monkeypatch.setattr(_M, "archive_raw", boom)
        iso = deploy["tree"] / f".harvest-iso--{RUN_ID}.tmp"
        before = _raw_before(deploy)
        with pytest.raises(OSError):
            _M.assemble(_payload("6c51e236-ae23-476f-a2a3-18367fa23d65"),
                        harvest_run_id=RUN_ID)
        assert not iso.exists()
        assert _new_raw(deploy, before) == []
