"""sc-register-concurrent-write 验收：flock+merge CAS 协作写者协议。

覆盖 packet SC 的核心验收点：
  ① 持锁覆盖 read→merge→temp→fsync→os.replace——跨进程互斥：进程 B 拿不到
     锁时数据文件在整段窗口内不被 B 改动（真子进程验证，非 mock）
  ② merge 只增/改本键：其余顶层键、其他 server 条目、嵌套内容原样保留
  ③ 一致性检查在解锁之后（R3-C2）：持锁窗口内的 post-check 时机可被外部
     观测——锁已释放、检查未开始（用真实子进程持锁探测）
  ④ 非协议覆盖 → 有界重试：注入在每次 post-check 前覆盖本键的写者，
     前两次被修复，第三次成功
  ⑤ 重试耗尽 → fail-closed：exit 4、冲突详情（本键状态/期望与观测 hash）、
     退避序列为指数（0.05/0.10）、文件保持被覆盖现状（不强写）
  ⑥ 良性叠加：解锁后其他键被并发写者变更、本键完好 → 成功退出不重试
  ⑦ 配置损坏 → exit 3、文件字节零改动（fail-closed 不改写一个字节）
  ⑧ CLI 端到端：--config/--server-name/--command/--args/--env 全链路 +
     KEY=VALUE 形态校验 exit 2
  ⑨ 原子性：写入过程只在同目录临时文件中可见（.tmp 前缀），结束无残留
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "register_mcp_cas.py"

ENTRY_A: dict = {"command": "npx", "args": ["-y", "akb2-search"]}
ENTRY_B: dict = {"command": "uvx", "args": ["akb2-harvest"], "env": {"A": "1"}}


def load_mod():
    """importlib 加载被测脚本（scripts/ 不在 pythonpath，与 test_anchors.py 同范式）。"""
    spec = importlib.util.spec_from_file_location("register_mcp_cas", str(SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Recorder:
    """替身 sleep：记录退避时长且不真等。"""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, sec: float) -> None:
        self.delays.append(sec)


@pytest.fixture()
def M():
    return load_mod()


@pytest.fixture()
def cfg(tmp_path) -> Path:
    return tmp_path / ".claude.json"


def seed(cfg: Path, servers: dict | None = None, extra: dict | None = None) -> dict:
    doc = {"mcpServers": servers or {}, **(extra or {})}
    cfg.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


# ---------------------------------------------------------------------------
# ① 跨进程互斥：持锁窗口内其他协议写者进不来（真子进程）
# ---------------------------------------------------------------------------

def test_locked_exchange_blocks_second_process_until_unlock(M, cfg):
    """进程 A 持锁期间，子进程 B 的注册必须阻塞到 A 释放后才完成。

    时间线：A 主进程启动注册线程并暂停在锁内 → 启动子进程 B（同脚本注册
    另一键）→ B 必须还在跑（本用例显式断言这一步，而非只看最终状态——
    否则锁完全失效时测试仍会因为"最终结果凑巧一致"而误判通过）→ A 放行
    → B 完成 → 两键最终都在文件里。
    """
    seed(cfg, extra={"userID": "u-1"})
    lock_path = M.lock_path_for(cfg)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    gate = threading.Event()
    released = threading.Event()

    def hold_lock():
        with open(lock_path, "a+", encoding="utf-8") as fh:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            gate.set()
            released.wait(timeout=10)
        # fh 关闭即释放

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert gate.wait(timeout=5), "测试线程未拿到锁（前置异常）"

    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT_PATH), "--config", str(cfg),
         "--server-name", "b-server", "--command", "uvx"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    else:
        released.set()
        holder.join(timeout=5)
        pytest.fail(
            "子进程 B 在 A 持锁期间就已退出（returncode="
            f"{proc.returncode}），未观测到跨进程互斥"
        )

    released.set()
    holder.join(timeout=5)
    stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == 0, stderr
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["mcpServers"]["b-server"]["command"] == "uvx"
    assert doc["userID"] == "u-1"


def test_locked_exchange_holds_during_entire_read_merge_replace(M, cfg, monkeypatch):
    """锁的覆盖范围 = read→merge→temp→fsync→replace 全程：os.replace 执行时锁仍在。

    用探针拦截 os.replace：在其执行瞬间从另一条 fd 尝试 flock LOCK_EX|LOCK_NB，
    必须被拒（证明 replace 在锁内）；随后正常放行。flock 成功返回 None、阻塞抛
    BlockingIOError（不是返回非零），探针按异常语义判定。
    """
    import fcntl
    seed(cfg, servers={"other": {"command": "x"}})
    lock_path = M.lock_path_for(cfg)
    probe_results: dict = {}
    real_replace = os.replace

    def probing_replace(src, dst):
        probe_results["before"] = True
        with open(lock_path, "a+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                probe_results["lockable_during_replace"] = True
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                probe_results["lockable_during_replace"] = False
        return real_replace(src, dst)

    monkeypatch.setattr(M.os, "replace", probing_replace)
    rc = M.register(cfg, "probe-server", ENTRY_A, sleep=lambda _: None)
    assert rc == M.EXIT_OK
    assert probe_results.get("before") is True
    assert probe_results["lockable_during_replace"] is False, \
        "os.replace 执行时 sidecar 锁必须仍被持有（R3-C2 的前提：整段窗口在锁内）"


def test_lock_is_on_sidecar_not_data_file(M, cfg):
    """锁挂 sidecar：数据文件被 os.replace 换 inode 后，锁点依然稳定。"""
    seed(cfg)
    rc = M.register(cfg, "s1", ENTRY_A, sleep=lambda _: None)
    assert rc == M.EXIT_OK
    lock = M.lock_path_for(cfg)
    assert lock.exists() and lock.name == ".claude.json.lock"


# ---------------------------------------------------------------------------
# ② merge 只增/改本键
# ---------------------------------------------------------------------------

def test_merge_preserves_all_other_keys_and_servers(M, cfg):
    other_server = {"command": "keep", "args": ["--stay"], "env": {"TOKEN": "t"}}
    seed(cfg, servers={"other": other_server},
         extra={"userID": "u-9", "numStartups": 42,
                "projects": {"/w/x": {"allowedTools": ["Read"]}},
                "nested": {"deep": {"list": [1, 2, 3]}}})
    rc = M.register(cfg, "akb2", ENTRY_B, sleep=lambda _: None)
    assert rc == M.EXIT_OK
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["mcpServers"]["akb2"] == ENTRY_B
    assert doc["mcpServers"]["other"] == other_server
    assert doc["userID"] == "u-9"
    assert doc["numStartups"] == 42
    assert doc["projects"] == {"/w/x": {"allowedTools": ["Read"]}}
    assert doc["nested"] == {"deep": {"list": [1, 2, 3]}}  # 原样，非空壳


def test_merge_updates_existing_key_and_creates_first_entry(M, cfg):
    seed(cfg, servers={"akb2": {"command": "old"}})
    assert M.register(cfg, "akb2", ENTRY_A, sleep=lambda _: None) == M.EXIT_OK
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["akb2"] == ENTRY_A

    cfg.unlink()
    assert M.register(cfg, "fresh", ENTRY_A, sleep=lambda _: None) == M.EXIT_OK
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["fresh"] == ENTRY_A


def test_merge_rejects_corrupt_mcpservers(M, cfg):
    cfg.write_text(json.dumps({"mcpServers": ["not-a-map"]}), encoding="utf-8")
    assert M.register(cfg, "akb2", ENTRY_A, sleep=lambda _: None) == M.EXIT_CORRUPT


# ---------------------------------------------------------------------------
# ③ 一致性检查在解锁之后（R3-C2）
# ---------------------------------------------------------------------------

def test_post_check_runs_after_unlock(M, cfg, monkeypatch):
    """post-check 执行瞬间锁必须已释放：拦截 _post_check 入口探测锁可拿。

    若检查在锁内（R3-C2 缺陷形态），非协议写者可在 check 通过后、解锁前
    写入而不被发现——本用例锁死该时序。
    """
    import fcntl
    seed(cfg)
    lock_path = M.lock_path_for(cfg)
    real_post = M._post_check

    def probing_post_check(path, name, entry, expected):
        with open(lock_path, "a+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lockable = True
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                lockable = False
        assert lockable, "post-check 运行时锁必须已释放（R3-C2）"
        return real_post(path, name, entry, expected)

    monkeypatch.setattr(M, "_post_check", probing_post_check)
    assert M.register(cfg, "s", ENTRY_A, sleep=lambda _: None) == M.EXIT_OK


def test_post_check_detects_nonprotocol_overwrite_and_returns_kinds(M, cfg):
    """_post_check 三分支：match / 本键被删 / 本键被改。"""
    seed(cfg)
    rc = M.register(cfg, "akb2", ENTRY_A, sleep=lambda _: None)
    assert rc == M.EXIT_OK
    expected = M.sha256_bytes(cfg.read_bytes())

    ok, obs, kind = M._post_check(cfg, "akb2", ENTRY_A, expected)
    assert (ok, kind) == (True, "match") and obs == expected

    doc = json.loads(cfg.read_text(encoding="utf-8"))
    del doc["mcpServers"]["akb2"]
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    ok, obs, kind = M._post_check(cfg, "akb2", ENTRY_A, expected)
    assert (ok, kind) == (False, "key-missing")

    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc["mcpServers"]["akb2"] = {"command": "hijacked"}
    cfg.write_text(json.dumps(doc), encoding="utf-8")
    ok, obs, kind = M._post_check(cfg, "akb2", ENTRY_A, expected)
    assert (ok, kind) == (False, "key-altered")


# ---------------------------------------------------------------------------
# ④ 非协议覆盖 → 有界重试（修复后成功）
# ---------------------------------------------------------------------------

def test_retry_recovers_when_overwriter_stops(M, cfg, monkeypatch, capfd):
    """攻击者覆盖两次后停手：第三轮持锁写入存活 → 成功，退避记录为指数。"""
    seed(cfg)
    state = {"overwrites": 0}
    real_post = M._post_check

    def attacking_post_check(path, name, entry, expected):
        result = real_post(path, name, entry, expected)
        if result[0] and state["overwrites"] < 2:
            # 模拟非协议写者：锁外直接覆盖本键（绕过 sidecar 锁）
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["mcpServers"][name] = {"command": "attacker"}
            path.write_text(json.dumps(doc), encoding="utf-8")
            state["overwrites"] += 1
            return False, M.sha256_bytes(path.read_bytes()), "key-altered"
        return result

    monkeypatch.setattr(M, "_post_check", attacking_post_check)
    rec = Recorder()
    rc = M.register(cfg, "akb2", ENTRY_A, sleep=rec)
    assert rc == M.EXIT_OK
    assert state["overwrites"] == 2
    assert rec.delays == [0.05, 0.10], "指数退避：base * 2**(attempt-1)"
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["akb2"] == ENTRY_A
    out = capfd.readouterr().err
    assert "冲突" not in out  # 最终成功了，没有冲突报告


def test_retry_budget_is_bounded_at_three(M, cfg, monkeypatch):
    """max_retries 默认 3：完整持锁流程恰好执行 3 次，不无限循环。"""
    seed(cfg)
    exchanges = {"n": 0}
    real_exchange = M._locked_exchange
    real_post = M._post_check

    def counting_exchange(path, name, entry):
        exchanges["n"] += 1
        return real_exchange(path, name, entry)

    def always_overwritten(path, name, entry, expected):
        real_post(path, name, entry, expected)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["mcpServers"][name] = {"command": "attacker"}
        path.write_text(json.dumps(doc), encoding="utf-8")
        return False, M.sha256_bytes(path.read_bytes()), "key-altered"

    monkeypatch.setattr(M, "_locked_exchange", counting_exchange)
    monkeypatch.setattr(M, "_post_check", always_overwritten)
    rec = Recorder()
    rc = M.register(cfg, "akb2", ENTRY_A, sleep=rec)
    assert rc == M.EXIT_CONFLICT
    assert exchanges["n"] == 3
    assert rec.delays == [0.05, 0.10]  # 第3轮失败后不再退避


# ---------------------------------------------------------------------------
# ⑤ 重试耗尽 → fail-closed
# ---------------------------------------------------------------------------

def test_exhaustion_fail_closed_reports_details_and_leaves_state(M, cfg, monkeypatch, capfd):
    """耗尽：exit 4 + 冲突详情（本键状态/两个 hash/退避序列）+ 不强写（文件保持被覆盖现状）。"""
    seed(cfg, extra={"keep": "me"})
    attacker_entry = {"command": "attacker"}
    real_post = M._post_check

    def always_overwritten(path, name, entry, expected):
        real_post(path, name, entry, expected)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["mcpServers"][name] = attacker_entry
        path.write_text(json.dumps(doc), encoding="utf-8")
        return False, M.sha256_bytes(path.read_bytes()), "key-altered"

    monkeypatch.setattr(M, "_post_check", always_overwritten)
    rc = M.register(cfg, "akb2", ENTRY_A, sleep=Recorder())
    assert rc == M.EXIT_CONFLICT

    err = capfd.readouterr().err
    assert "fail-closed" in err
    assert "akb2" in err and "key-altered" in err
    assert "观测 hash" in err and "期望 hash" in err
    assert "0.05" in err and "0.10" in err

    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["mcpServers"]["akb2"] == attacker_entry, \
        "fail-closed 不强写：耗尽后磁盘保持攻击者现状，留给人工裁决"
    assert doc["keep"] == "me"


# ---------------------------------------------------------------------------
# ⑥ 良性叠加：其他键变更、本键完好 → 成功
# ---------------------------------------------------------------------------

def test_benign_concurrent_divergence_succeeds(M, cfg, monkeypatch, capfd):
    """解锁后其他键被并发写者改了、本键原样在场 → 成功不重试。"""
    seed(cfg)
    real_post = M._post_check

    def benign_divergence(path, name, entry, expected):
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["numStartups"] = 43  # Claude Code 本体写了自己的键
        path.write_text(json.dumps(doc), encoding="utf-8")
        return real_post(path, name, entry, expected)

    monkeypatch.setattr(M, "_post_check", benign_divergence)
    rec = Recorder()
    rc = M.register(cfg, "akb2", ENTRY_A, sleep=rec)
    assert rc == M.EXIT_OK
    assert rec.delays == [], "良性叠加不触发重试"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["mcpServers"]["akb2"] == ENTRY_A
    assert doc["numStartups"] == 43
    assert "并发叠加" in capfd.readouterr().err


# ---------------------------------------------------------------------------
# ⑦ 配置损坏 fail-closed
# ---------------------------------------------------------------------------

def test_corrupt_config_fail_closed_zero_mutation(M, cfg):
    """损坏 JSON / 顶层非 object：exit 3、字节零改动。"""
    bad = "{ this is not json"
    cfg.write_text(bad, encoding="utf-8")
    before = cfg.read_bytes()
    rc = M.register(cfg, "akb2", ENTRY_A, sleep=lambda _: None)
    assert rc == M.EXIT_CORRUPT
    assert cfg.read_bytes() == before, "fail-closed：不可解析时不改写一个字节"

    cfg.write_text(json.dumps(["top-level-array"]), encoding="utf-8")
    before = cfg.read_bytes()
    assert M.register(cfg, "akb2", ENTRY_A, sleep=lambda _: None) == M.EXIT_CORRUPT
    assert cfg.read_bytes() == before


def test_entry_must_be_dict(M, cfg):
    with pytest.raises(TypeError):
        M.register(cfg, "akb2", ["not-a-dict"], sleep=lambda _: None)


# ---------------------------------------------------------------------------
# ⑧ CLI 端到端
# ---------------------------------------------------------------------------

def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True, text=True, timeout=60,
    )


def test_cli_end_to_end(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"mcpServers": {"keep": {"command": "k"}}}), encoding="utf-8")
    proc = run_cli([
        "--config", str(cfg), "--server-name", "akb2-search",
        "--command", "npx", "--args-json", '["-y", "akb2-search-mcp"]',
        "--env", "AKB2_ROOT=/v/akb2", "--env", "LANG=en_US.UTF-8",
    ])
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["mcpServers"]["akb2-search"] == {
        "command": "npx",
        "args": ["-y", "akb2-search-mcp"],
        "env": {"AKB2_ROOT": "/v/akb2", "LANG": "en_US.UTF-8"},
    }
    assert doc["mcpServers"]["keep"] == {"command": "k"}


def test_cli_args_forms_and_validation(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")

    # --args 普通形态（不以 - 开头）可用
    proc = run_cli(["--config", str(cfg), "--server-name", "s1",
                    "--command", "c", "--args", "a", "b"])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["s1"]["args"] == ["a", "b"]

    # 互斥：--args 与 --args-json 同给 → exit 2
    proc = run_cli(["--config", str(cfg), "--server-name", "s2", "--command", "c",
                    "--args", "a", "--args-json", '["b"]'])
    assert proc.returncode == 2 and "互斥" in proc.stderr

    # --args-json 非数组 / 非字符串数组 → exit 2
    proc = run_cli(["--config", str(cfg), "--server-name", "s3",
                    "--command", "c", "--args-json", '{"x": 1}'])
    assert proc.returncode == 2 and "字符串数组" in proc.stderr
    proc = run_cli(["--config", str(cfg), "--server-name", "s4",
                    "--command", "c", "--args-json", "[1, 2]"])
    assert proc.returncode == 2

    # --args-json 坏 JSON → exit 2
    proc = run_cli(["--config", str(cfg), "--server-name", "s5",
                    "--command", "c", "--args-json", "not-json"])
    assert proc.returncode == 2 and "JSON" in proc.stderr


def test_cli_rejects_malformed_env(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    proc = run_cli(["--config", str(cfg), "--server-name", "s",
                    "--command", "c", "--env", "NO_EQUALS_SIGN"])
    assert proc.returncode == 2
    assert "KEY=VALUE" in proc.stderr


def test_cli_exit_codes_for_corrupt_and_conflict(tmp_path, monkeypatch=None):
    cfg = tmp_path / "config.json"
    cfg.write_text("{ broken", encoding="utf-8")
    proc = run_cli(["--config", str(cfg), "--server-name", "s", "--command", "c"])
    assert proc.returncode == 3
    assert "fail-closed" in proc.stderr


# ---------------------------------------------------------------------------
# ⑨ 原子性与落盘细节
# ---------------------------------------------------------------------------

def test_no_tmp_residue_and_same_dir_temp(M, cfg, monkeypatch):
    """写入只经同目录 .tmp 中转，结束无残留；崩溃中途中止也不留半成品目标文件。"""
    seed(cfg)
    seen_dirs: list[Path] = []
    real_mkstemp = M.tempfile.mkstemp

    def spying_mkstemp(*args, **kwargs):
        seen_dirs.append(Path(kwargs["dir"]))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(M.tempfile, "mkstemp", spying_mkstemp)
    rc = M.register(cfg, "akb2", ENTRY_A, sleep=lambda _: None)
    assert rc == M.EXIT_OK
    assert seen_dirs and seen_dirs[0] == cfg.parent, "临时文件必须与目标同目录（replace 原子性前提）"
    assert not list(cfg.parent.glob("*.tmp")), "成功路径无 .tmp 残留"


def test_failed_replace_cleans_temp_and_reraises(M, cfg, monkeypatch):
    seed(cfg)
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(M.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        M.register(cfg, "akb2", ENTRY_A, sleep=lambda _: None)
    assert not list(cfg.parent.glob("*.tmp")), "replace 失败清理临时文件"
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"] == {}


def test_new_file_gets_private_mode(M, cfg):
    """新建配置默认 0600（~/.claude.json 含 token 场景的私有默认）。"""
    assert M.register(cfg, "s", ENTRY_A, sleep=lambda _: None) == M.EXIT_OK
    assert (os.stat(cfg).st_mode & 0o777) == 0o600


def test_existing_file_mode_preserved(M, cfg):
    seed(cfg)
    os.chmod(cfg, 0o644)
    assert M.register(cfg, "s", ENTRY_A, sleep=lambda _: None) == M.EXIT_OK
    assert (os.stat(cfg).st_mode & 0o777) == 0o644
