"""sc-register-helper-contract 黑盒验收：注册辅助脚本的外部驱动契约。

黑盒口径：只经 subprocess 驱动 scripts/register_mcp_cas.py（注册器）与
scripts/verify_akb2_retrieval_registration.py（三段校验器），不 import 被测
模块内部符号——内部协议（flock 窗口/指数退避/原子替换）的白盒覆盖在
test_register_cas.py，本文件只锁「两个脚本作为外部命令」的契约面。

六格（SC 契约）：
  ① --help           两脚本 usage 正常、exit 0
  ② 目标文件不存在    注册器视为空配置完成首次注册（exit 0）；校验器前置失败 exit 1
  ③ JSON 坏          注册器 fail-closed exit 3 且字节零改动；校验器前置失败 exit 1
  ④ 权威源不一致      校验器①段 exit 2——含关键反证：args/env/README/command 全部
                     在场且结构合法（「键都在」），唯独 args[0] 指向内容与权威源
                     不符的 server 文件 → 必须红，防「键存在即算过」
  ⑤ 其它键被篡改      校验器②段 exit 3（pre/post 剥本键后 deep diff 抓到篡改）
  ⑥ 握手子进程失败    校验器③段 exit 4（握手进程读到 initialize 响应前退出）

绿锚对照（④的反证前提）：同样的条目形状、args[0] 换成权威源内容副本 →
三段全绿 exit 0。没有这条绿锚，上面所有「必须红」都无法排除校验器
「永远红」的退化形态。

安全契约（SC 硬约束：禁止触真实 ~/.claude.json）：
  - 每个子进程的 HOME 都指向 tmp_path——即便脚本回落默认路径也落在沙箱里；
  - --config / --backup-dir 永远显式传 tmp 路径，helper 层再断言目标不等于
    真实 home 下的对应路径，双保险；
  - 真实权威源 server.py 只做 SHA 对照素材（复制进 tmp），从不被执行；
    握手进程一律是本文件在 tmp 里造的假 responder / 假失败脚本。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTER_SCRIPT = REPO_ROOT / "scripts" / "register_mcp_cas.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_akb2_retrieval_registration.py"
AUTHORITY_SERVER = REPO_ROOT / "toolbox" / "cc-akb2-retrieval" / "mcp_server" / "server.py"

SERVER_KEY = "akb2-retrieval"
REAL_CLAUDE_JSON = Path.home() / ".claude.json"
REAL_BACKUP_DIR = Path.home() / ".claude.json.reg-backup"

# 假 MCP responder：按 MCP stdio（换行分隔 JSON）读 initialize、应答
# serverInfo.name == "akb2-retrieval"，其余消息忽略，stdin 关闭后退出。
# 只用 stdlib，绝不依赖 mcp 包——保证零 skip 的确定性。
RESPONDER_BODY = """\
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"serverInfo": {"name": "akb2-retrieval", "version": "1.0"}},
        }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
"""


# ---------------------------------------------------------------------------
# helpers：沙箱环境 + 安全护栏
# ---------------------------------------------------------------------------

def sandbox_env(tmp_path: Path) -> dict[str, str]:
    """子进程环境：HOME 指向 tmp，杜绝任何默认路径回落触碰真实 home。"""
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    return env


def assert_not_real_home_target(path: Path | str) -> Path:
    """护栏：被驱动的目标路径绝不能是真实 ~/.claude.json（或其备份目录）。"""
    p = Path(path).resolve()
    assert p != REAL_CLAUDE_JSON.resolve(), f"禁止在真实 ~/.claude.json 上运行: {p}"
    assert p != REAL_BACKUP_DIR.resolve(), f"禁止在真实注册备份目录上运行: {p}"
    return p


def run_register(cfg: Path, cli_args: list[str], tmp_path: Path,
                 timeout: float = 120.0) -> subprocess.CompletedProcess:
    """subprocess 驱动注册器；--config 永远显式指向 tmp 内的假 claude.json。"""
    cfg = assert_not_real_home_target(cfg)
    return subprocess.run(
        [sys.executable, str(REGISTER_SCRIPT), "--config", str(cfg), *cli_args],
        capture_output=True, text=True, timeout=timeout, env=sandbox_env(tmp_path),
    )


def run_verify(cfg: Path, backup_dir: Path, tmp_path: Path,
               timeout: float = 180.0) -> subprocess.CompletedProcess:
    """subprocess 驱动三段校验器；--config/--backup-dir/--repo-root 全显式。"""
    cfg = assert_not_real_home_target(cfg)
    backup_dir = assert_not_real_home_target(backup_dir)
    return subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT),
         "--config", str(cfg), "--backup-dir", str(backup_dir),
         "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=timeout, env=sandbox_env(tmp_path),
    )


def make_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def make_responder(tmp_path: Path) -> Path:
    return make_executable(tmp_path / "fake-responder.py",
                           "#!/usr/bin/env python3\n" + RESPONDER_BODY)


def copy_authority(tmp_path: Path) -> Path:
    """权威源内容副本（SHA 对照素材，从不被执行）。"""
    dst = tmp_path / "authority-copy.py"
    shutil.copyfile(AUTHORITY_SERVER, dst)
    return dst


def write_config(path: Path, servers: dict, extra: dict | None = None) -> None:
    doc = {"mcpServers": servers, **(extra or {})}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def full_entry(command: str, server_path: Path) -> dict:
    """与真实 akb2-retrieval 条目同形状的完整注册条目（三件套齐备）。"""
    return {
        "command": command,
        "args": [str(server_path)],
        "env": {"PYTHONPATH": str(REPO_ROOT / "src")},
    }


def register_and_snapshot(cfg: Path, backup_dir: Path, tmp_path: Path,
                          command: str, server_path: Path,
                          seed_extra: dict | None = None) -> None:
    """黑盒真链路：seed 配置 → pre 快照 → subprocess 注册 → post 快照。

    与生产注册流程同款快照约定（pre=注册前、post=注册后），供②段 exact diff。
    """
    write_config(cfg, {}, seed_extra)
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cfg, backup_dir / "pre.json")
    proc = run_register(cfg, [
        "--server-name", SERVER_KEY,
        "--command", command,
        "--args-json", json.dumps([str(server_path)]),
        "--env", f"PYTHONPATH={REPO_ROOT / 'src'}",
    ], tmp_path)
    assert proc.returncode == 0, proc.stderr
    shutil.copyfile(cfg, backup_dir / "post.json")


# ---------------------------------------------------------------------------
# ① --help
# ---------------------------------------------------------------------------

def test_help_exits_zero_for_both_scripts(tmp_path):
    """两脚本都是 argparse CLI：--help 打印 usage 且 exit 0（外部可发现契约）。"""
    for script in (REGISTER_SCRIPT, VERIFY_SCRIPT):
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=60, env=sandbox_env(tmp_path),
        )
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"
        assert "usage" in proc.stdout.lower(), f"{script.name} 缺 usage 输出"


# ---------------------------------------------------------------------------
# ② 目标文件不存在
# ---------------------------------------------------------------------------

def test_register_missing_target_creates_first_entry(tmp_path):
    """注册器对缺失文件的契约：视为空配置完成首次注册（exit 0）。"""
    cfg = tmp_path / "fake-home" / ".claude.json"  # 连父目录都不存在
    proc = run_register(cfg, ["--server-name", "s", "--command", "c"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["mcpServers"]["s"] == {"command": "c", "args": []}


def test_verify_missing_config_precondition(tmp_path):
    """校验器对缺失配置的契约：前置失败 exit 1（fail-closed，不进入三段）。"""
    cfg = tmp_path / "absent.json"
    backup = tmp_path / "backup"
    backup.mkdir()
    proc = run_verify(cfg, backup, tmp_path)
    assert proc.returncode == 1
    assert "文件不存在" in proc.stderr


# ---------------------------------------------------------------------------
# ③ JSON 坏
# ---------------------------------------------------------------------------

def test_register_corrupt_json_fail_closed_zero_mutation(tmp_path):
    """注册器对坏 JSON 的契约：exit 3 且字节零改动（不改写一个字节）。"""
    cfg = tmp_path / ".claude.json"
    cfg.write_text("{ this is not json", encoding="utf-8")
    before = cfg.read_bytes()
    proc = run_register(cfg, ["--server-name", "s", "--command", "c"], tmp_path)
    assert proc.returncode == 3
    assert "fail-closed" in proc.stderr
    assert cfg.read_bytes() == before, "fail-closed：不可解析时不改写一个字节"


def test_verify_corrupt_json_precondition(tmp_path):
    """校验器对坏 JSON 的契约：前置失败 exit 1。"""
    cfg = tmp_path / ".claude.json"
    cfg.write_text("[1, 2", encoding="utf-8")
    backup = tmp_path / "backup"
    backup.mkdir()
    proc = run_verify(cfg, backup, tmp_path)
    assert proc.returncode == 1
    assert "JSON" in proc.stderr


# ---------------------------------------------------------------------------
# ④ 权威源与注册条目不一致 —— 含关键反证
# ---------------------------------------------------------------------------

def test_counter_proof_keys_all_present_wrong_server_path_must_be_red(tmp_path):
    """关键反证：键都在但 args 指错误 server 路径，verify 必须红。

    条目三件套齐备且结构合法（command 可执行、args 非空字符串数组、
    env.PYTHONPATH 指向权威 repo 的 src、README 登记行真实在场），唯独
    args[0] 指向内容与权威源不符的文件。校验器必须 exit 2 且失败点落在
    内容锚定（SHA 对照）上——证明它做的是「逐字一致」而不是「键存在即算过」。
    """
    cfg = tmp_path / ".claude.json"
    backup = tmp_path / "backup"
    backup.mkdir()
    responder = make_responder(tmp_path)
    wrong_server = tmp_path / "wrong-server.py"
    wrong_server.write_text('"""结构合法但内容不是权威源。"""\n', encoding="utf-8")
    write_config(cfg, {SERVER_KEY: full_entry(str(responder), wrong_server)})

    proc = run_verify(cfg, backup, tmp_path)

    assert proc.returncode == 2, (
        "全键在场、仅 args[0] 内容不符时必须红在①段（exit 2），"
        f"got exit {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "内容不一致" in proc.stderr, "失败点必须是 SHA 内容对照，不是形状检查"
    # 反证完整性：args/环境/命令的形状检查都没有报错——红点只在内容锚定
    assert "args 不是非空字符串数组" not in proc.stderr
    assert "缺 env.PYTHONPATH" not in proc.stderr
    assert "不是可执行文件" not in proc.stderr


def test_green_path_register_then_verify_all_stages(tmp_path):
    """绿锚对照：args[0] 换成权威源内容副本 → 注册 + 三段校验全绿 exit 0。

    这是④反证的前提：先证明同形状条目在内容正确时校验器真能走通三段，
    「错误路径必须红」才构成有效反证（排除校验器「永远红」的退化形态）。
    走的是黑盒真链路：seed → pre 快照 → subprocess 注册 → post 快照 → 校验。
    """
    cfg = tmp_path / ".claude.json"
    backup = tmp_path / "backup"
    responder = make_responder(tmp_path)
    authority_copy = copy_authority(tmp_path)
    register_and_snapshot(cfg, backup, tmp_path, str(responder), authority_copy,
                          seed_extra={"userID": "u-1", "numStartups": 7})

    # 注册器侧契约复核：其它键原样保留、本键三件套落盘
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["userID"] == "u-1" and doc["numStartups"] == 7
    assert doc["mcpServers"][SERVER_KEY] == full_entry(str(responder), authority_copy)

    proc = run_verify(cfg, backup, tmp_path)
    assert proc.returncode == 0, (
        f"绿锚路径必须三段全绿\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "REG_AUTHORITY_MATCH" in proc.stdout
    assert "REG_OTHER_KEYS_INTACT" in proc.stdout
    assert "REG_HANDSHAKE_OK akb2-retrieval" in proc.stdout


# ---------------------------------------------------------------------------
# ⑤ 其它键被篡改
# ---------------------------------------------------------------------------

def test_verify_other_keys_tampered_red(tmp_path):
    """②段契约：非协议写者在注册窗口外篡改其它键（pre/post 如实记录）→ exit 3。

    pre 快照 userID=u-1、注册后 live 与 post 快照都是被篡改值——剥掉本键后
    pre/post 不相等，exact diff 必须抓到「其它键被改动」。
    """
    cfg = tmp_path / ".claude.json"
    backup = tmp_path / "backup"
    responder = make_responder(tmp_path)
    authority_copy = copy_authority(tmp_path)
    register_and_snapshot(cfg, backup, tmp_path, str(responder), authority_copy,
                          seed_extra={"userID": "u-1"})

    doc = json.loads(cfg.read_text(encoding="utf-8"))
    doc["userID"] = "EVIL-TAMPERED"
    cfg.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(cfg, backup / "post.json")  # post 如实记录被篡改状态

    proc = run_verify(cfg, backup, tmp_path)
    assert proc.returncode == 3, (
        f"其它键被篡改必须红在②段（exit 3），got exit {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "userID" in proc.stderr


# ---------------------------------------------------------------------------
# ⑥ 握手子进程失败
# ---------------------------------------------------------------------------

def test_verify_handshake_subprocess_failure_red(tmp_path):
    """③段契约：握手进程在应答 initialize 前退出 → exit 4（fail-closed）。

    ①②都是绿的（args 指权威源副本、快照诚实），红点只能来自握手本身——
    证明三段是串联的独立闸门，任何一段单独失败都有独立退出码。
    """
    cfg = tmp_path / ".claude.json"
    backup = tmp_path / "backup"
    early_exit = make_executable(tmp_path / "early-exit.sh", "#!/bin/sh\nexit 7\n")
    authority_copy = copy_authority(tmp_path)
    register_and_snapshot(cfg, backup, tmp_path, str(early_exit), authority_copy)

    proc = run_verify(cfg, backup, tmp_path)
    assert proc.returncode == 4, (
        f"握手子进程失败必须红在③段（exit 4），got exit {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "握手" in proc.stderr
