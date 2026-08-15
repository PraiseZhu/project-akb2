"""akb2-retrieval 注册条目三段校验（sc-register-mcp 的机器闸门）。

被校验对象：~/.claude.json 全局 mcpServers 下键名 ``akb2-retrieval`` 的注册条目
（由 scripts/register_mcp_cas.py 按 flock+merge CAS 协议写入）。

三段契约（对应 SC sc-register-mcp，任一段失败即非 0 退出，fail-closed）：
    ① REG_AUTHORITY_MATCH   注册条目与仓内权威源逐字一致：
       - args[0] 文件 SHA-256 == <repo>/toolbox/cc-akb2-retrieval/mcp_server/server.py
         （内容锚定、位置无关：条目指向任何同内容 checkout 均可，worktree/主仓通吃）
       - env.PYTHONPATH 解析后 == <repo>/src（与 README 登记行的
         「PYTHONPATH=<repo>/src 直跑权威源（stdio）」逐字同口径）
       - src/akb2/mcp_servers/README.md 存在 akb2-retrieval 登记行且指向权威源路径
       - command 与既有 akb-search 条目的 command 逐字相等（克隆注册模式的机器判定；
         akb-search 不在场时降级为存在+可执行）
    ② REG_OTHER_KEYS_INTACT 注册写入前后，除 mcpServers.akb2-retrieval 外的全部
       键值对 exact diff：pre/post 备份快照剥掉本键后逐键 deep-equal。
       幂等重注册（pre 已含本键）在剥键视角下天然相等。
    ③ REG_HANDSHAKE_OK      用注册条目原样 command/args/env 起子进程，按 MCP stdio
       协议（换行分隔 JSON）发 initialize 消息，校验响应 JSON-RPC id 匹配且
       result.serverInfo.name == "akb2-retrieval"。
       lifespan 启动期 fail-closed 解析 vault 根（AKB2_ROOT 等 env 缺失即进程退出），
       因此本段同时承载「按注册条目操作真能跑」。

快照约定：注册流程在写入前/后各拍一份 ~/.claude.json 备份（含 token，权限 0600，
绝不入 git），默认目录 ~/.claude.json.reg-backup/akb2-retrieval/{pre,post}.json。

退出码：
    0  三段全绿
    1  前置失败（配置不可读 / 条目缺失 / 快照缺失）
    2  ①段失败（与权威源不一致）
    3  ②段失败（其它键被改动）
    4  ③段失败（真实握手失败）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量（宪法 P1/P4：显式、可诊断）
# ---------------------------------------------------------------------------

SERVER_KEY = "akb2-retrieval"
EXPECTED_SERVER_NAME = "akb2-retrieval"
PATTERN_REFERENCE_KEY = "akb-search"  # 注册模式的克隆基准（command 逐字同源）

# 权威源在仓内的固定相对位置（src/akb2/mcp_servers/README.md 登记行同款口径）
AUTHORITY_SERVER_REL = Path("toolbox/cc-akb2-retrieval/mcp_server/server.py")
MCP_SERVERS_README_REL = Path("src/akb2/mcp_servers/README.md")

DEFAULT_CONFIG_PATH = Path.home() / ".claude.json"
DEFAULT_BACKUP_DIR = Path.home() / ".claude.json.reg-backup" / SERVER_KEY

INITIALIZE_TIMEOUT_SEC = 30.0  # 含解释器启动 + lifespan vault 根解析 + 响应读取
SHUTDOWN_TIMEOUT_SEC = 10.0

EXIT_OK = 0
EXIT_PRECONDITION = 1
EXIT_AUTHORITY = 2
EXIT_OTHER_KEYS = 3
EXIT_HANDSHAKE = 4

INITIALIZE_PROTOCOL_VERSION = "2024-11-05"  # 最保守兼容版本，服务端可协商回退
CLIENT_INFO = {"name": "verify-akb2-retrieval", "version": "1.0"}


# ---------------------------------------------------------------------------
# 前置：读取配置与注册条目
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise PreconditionError(f"文件不存在: {path}") from None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreconditionError(f"{path} 不是合法 JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise PreconditionError(f"{path} 顶层不是 JSON object") from None
    return obj


class PreconditionError(Exception):
    """前置条件不满足（配置/条目/快照缺失）——重试无意义，fail-closed。"""


def get_registered_entry(config: dict[str, Any]) -> dict[str, Any]:
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise PreconditionError("mcpServers 缺失或不是 object")
    entry = servers.get(SERVER_KEY)
    if not isinstance(entry, dict):
        raise PreconditionError(f"mcpServers.{SERVER_KEY} 未注册（先跑 register_mcp_cas.py）")
    return entry


# ---------------------------------------------------------------------------
# ① 权威源一致
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_authority(repo_root: Path, config: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    """①段：注册条目三件套与仓内权威源逐字一致。返回失败原因列表（空 = 通过）。"""
    problems: list[str] = []

    authority = repo_root / AUTHORITY_SERVER_REL
    if not authority.is_file():
        return [f"仓内权威源缺失: {authority}"]

    args_list = entry.get("args")
    if not isinstance(args_list, list) or not args_list or not all(
        isinstance(a, str) for a in args_list
    ):
        return ["注册条目 args 不是非空字符串数组"]
    server_path = Path(args_list[0]).expanduser()

    if not server_path.is_file():
        problems.append(f"条目 args[0] 文件不存在: {server_path}")
    elif sha256_file(server_path) != sha256_file(authority):
        problems.append(
            f"条目 args[0] 与权威源内容不一致 (sha 不等): "
            f"entry={server_path} authority={authority}"
        )

    # env.PYTHONPATH 必须逐字指向权威源所在 repo 的 src/
    env = entry.get("env")
    if not isinstance(env, dict) or "PYTHONPATH" not in env:
        problems.append("注册条目缺 env.PYTHONPATH")
    else:
        expected_src = (repo_root / "src").resolve()
        got_src = Path(env["PYTHONPATH"]).expanduser().resolve()
        if got_src != expected_src:
            problems.append(
                f"env.PYTHONPATH 与权威源 repo 不符: got={got_src} expected={expected_src}"
            )

    # README 登记行：akb2-retrieval 行必须存在且指向权威源相对路径
    readme = repo_root / MCP_SERVERS_README_REL
    if not readme.is_file():
        problems.append(f"工程层登记文件缺失: {readme}")
    else:
        readme_text = readme.read_text(encoding="utf-8")
        reg_lines = [
            line for line in readme_text.splitlines()
            if SERVER_KEY in line and str(AUTHORITY_SERVER_REL) in line
        ]
        if not reg_lines:
            problems.append(
                f"{MCP_SERVERS_README_REL} 缺 {SERVER_KEY} 登记行"
                f"（或未指向 {AUTHORITY_SERVER_REL}）"
            )

    # command：克隆既有 akb-search 注册模式（在场时逐字相等）
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        problems.append("注册条目缺 command")
    else:
        cmd_path = Path(command).expanduser()
        if not (cmd_path.is_file() and os.access(cmd_path, os.X_OK)):
            problems.append(f"command 不是可执行文件: {command}")
        ref_entry = config.get("mcpServers", {}).get(PATTERN_REFERENCE_KEY)
        if isinstance(ref_entry, dict) and isinstance(ref_entry.get("command"), str):
            if command != ref_entry["command"]:
                problems.append(
                    f"command 未克隆 {PATTERN_REFERENCE_KEY} 注册模式: "
                    f"got={command} expected={ref_entry['command']}"
                )

    return problems


# ---------------------------------------------------------------------------
# ② 其它键 exact diff（pre/post 快照剥本键后逐键相等）
# ---------------------------------------------------------------------------

def _strip_server_key(config: dict[str, Any]) -> dict[str, Any]:
    """深拷贝并剥掉 mcpServers.akb2-retrieval，其余键原样保留。"""
    stripped = json.loads(json.dumps(config))  # 深拷贝（JSON 值域内安全）
    servers = stripped.get("mcpServers")
    if isinstance(servers, dict):
        servers.pop(SERVER_KEY, None)
    return stripped


def check_other_keys(
    pre: dict[str, Any], post: dict[str, Any], live_config: dict[str, Any]
) -> list[str]:
    """②段：剥掉本键后 pre/post 必须逐键 deep-equal，且 post 必须是当前 --config 的真实快照。

    只比对 pre/post 快照对本身自洽是不够的：快照文件与本次校验对象（live_config，
    即 --config 实际读到的文件）之间没有任何绑定，陈旧或张冠李戴的快照对可以在
    自洽的同时完全不代表这次的真实改动（可复现：live 改了 other_key，backup/
    仍是旧的 pre/post，②照样报 intact）。因此额外要求剥本键后 post 与
    live_config 逐键相等——只有这样「其它键保留」才是对本次校验对象的断言，
    而不是对两个可能无关文件的自证。
    返回失败原因列表（空 = 通过）。
    """
    problems: list[str] = []
    pre_stripped = _strip_server_key(pre)
    post_stripped = _strip_server_key(post)

    if pre_stripped != post_stripped:
        pre_keys, post_keys = set(pre_stripped), set(post_stripped)
        for gone in sorted(pre_keys - post_keys):
            problems.append(f"注册写入丢失既有顶层键: {gone}")
        for added in sorted(post_keys - pre_keys):
            problems.append(f"注册写入新增了非约定顶层键: {added}")
        for k in sorted(pre_keys & post_keys):
            if pre_stripped[k] != post_stripped[k]:
                problems.append(f"注册写入改动了既有键的值: {k}")

    servers = post.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_KEY not in servers:
        problems.append(f"post 快照缺 mcpServers.{SERVER_KEY}（注册未生效？）")

    live_stripped = _strip_server_key(live_config)
    if post_stripped != live_stripped:
        problems.append(
            "post 快照与当前 --config 内容不一致（剥掉本键后不相等）："
            "快照陈旧或与本次校验对象不对应，无法证明本次未改动其它键"
        )
        post_keys, live_keys = set(post_stripped), set(live_stripped)
        for gone in sorted(post_keys - live_keys):
            problems.append(f"  post 独有顶层键（live 已不含）: {gone}")
        for added in sorted(live_keys - post_keys):
            problems.append(f"  live 独有顶层键（post 未含）: {added}")
        for k in sorted(post_keys & live_keys):
            if post_stripped[k] != live_stripped[k]:
                problems.append(f"  post/live 值不同的顶层键: {k}")
    return problems


# ---------------------------------------------------------------------------
# ③ 真实 MCP 握手（stdio = 换行分隔 JSON，MCP 规范；不是 LSP 的 Content-Length 分帧）
# ---------------------------------------------------------------------------

class HandshakeTimeout(Exception):
    """握手响应读超时（子进程存活但 deadline 前未产出任何数据）。"""


class HandshakeDecodeError(Exception):
    """握手响应行不是合法 JSON。"""


def _write_message(stream: Any, payload: dict[str, Any]) -> None:
    stream.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    stream.flush()


def _read_message(stdout: Any, deadline: float) -> dict[str, Any] | None:
    """读一行 JSON-RPC 消息，deadline（``time.monotonic()`` 绝对时刻）前无数据即超时。

    EOF 返回 None（服务端崩溃由调用方按进程状态消歧）；非法 JSON 行抛
    HandshakeDecodeError，不放行未捕获异常污染进程退出码语义（会与
    EXIT_PRECONDITION=1 撞码）。
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HandshakeTimeout(f"读取响应超时（{INITIALIZE_TIMEOUT_SEC}s 内无数据）")
    sel = selectors.DefaultSelector()
    sel.register(stdout, selectors.EVENT_READ)
    try:
        if not sel.select(timeout=remaining):
            raise HandshakeTimeout(f"读取响应超时（{INITIALIZE_TIMEOUT_SEC}s 内无数据）")
    finally:
        sel.close()
    line = stdout.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise HandshakeDecodeError(f"响应不是合法 JSON: {line!r} ({exc})") from exc


# 宿主 vault/env 变量不得泄入握手子进程：条目缺该变量而宿主有时，子进程会
# 拿宿主路径继续跑，把「条目自足性」验证变成假绿。以下变量一律先剥离，
# 仅允许 entry.env 显式叠回。
_SCRUBBED_ENV_KEYS = (
    "AKB2_ROOT",
    "AKB2_VAULT_ROOT",
    "VAULT_ROOT",
    "PYTHONPATH",
)


def _controlled_child_env(env_overlay: dict[str, Any]) -> dict[str, str]:
    """构造受控子进程 env：继承启动所需基础变量，剥离 vault 路径类变量后叠加 entry.env。

    保留 PATH/HOME/LANG/TZ/TMPDIR/SSL 证书类——python 解释器启动与 https
    出站都依赖它们；剥离的是会改变被测 server 行为的 vault/env 泄漏项。
    """
    child_env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _SCRUBBED_ENV_KEYS:
            continue
        if key.startswith("PYTHON"):
            continue  # PYTHONHOME/PYTHONSTARTUP 等解释器注入项同剥
        child_env[key] = value
    if isinstance(env_overlay, dict):
        for key, value in env_overlay.items():
            child_env[str(key)] = str(value)
    return child_env


def check_handshake(entry: dict[str, Any]) -> tuple[list[str], str]:
    """③段：注册条目原样起子进程完成 initialize 握手。返回 (失败原因, serverInfo.name)。"""
    problems: list[str] = []
    command = entry.get("command")
    args_list = entry.get("args", [])
    env_overlay = entry.get("env", {})
    if not isinstance(command, str) or not isinstance(args_list, list):
        return ["条目缺 command/args，无法起握手进程"], ""

    child_env = _controlled_child_env(env_overlay)

    request_id = 1
    initialize_request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": INITIALIZE_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        },
    }

    try:
        proc = subprocess.Popen(
            [command, *args_list],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # lifespan 日志走 stderr，不污染协议流
            env=child_env,
        )
    except OSError as exc:
        return [f"握手进程启动失败: {exc}"], ""

    server_name = ""
    read_deadline = time.monotonic() + INITIALIZE_TIMEOUT_SEC
    try:
        assert proc.stdin is not None and proc.stdout is not None
        try:
            _write_message(proc.stdin, initialize_request)

            # 单请求同步读：每次 readline 前先用 selector 等到 deadline，
            # 子进程存活但一直不产出数据时不会无限阻塞（原实现的缺口：
            # 外层 wait(timeout) 只在读循环结束后才生效，读不到数据就永远
            # 到不了那一行）。
            result_seen = False
            while not result_seen:
                message = _read_message(proc.stdout, read_deadline)
                if message is None:
                    if proc.poll() is not None:
                        problems.append(
                            f"握手进程在读到 initialize 响应前退出 (code={proc.returncode})"
                        )
                    else:
                        problems.append("握手进程 stdout 提前 EOF")
                    break
                if message.get("id") != request_id:
                    continue  # 跳过服务端通知（如 startup 日志回显），只认本请求的应答
                result_seen = True
                if "error" in message:
                    problems.append(f"initialize 响应返回错误: {message['error']}")
                    break
                result = message.get("result")
                info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
                server_name = str(info.get("name", ""))
                if server_name != EXPECTED_SERVER_NAME:
                    problems.append(
                        f"serverInfo.name 不符: got={server_name!r} "
                        f"expected={EXPECTED_SERVER_NAME!r}"
                    )

            if not problems:
                # 通知服务端初始化完成，随后关 stdin 让其正常退出
                _write_message(proc.stdin, {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                })
        except BrokenPipeError:
            problems.append("握手进程 stdin 已关闭（服务端启动即崩溃？检查 env fail-closed）")
        except HandshakeTimeout as exc:
            problems.append(f"握手响应读超时: {exc}")
        except HandshakeDecodeError as exc:
            problems.append(f"握手响应解析失败: {exc}")
        finally:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        try:
            proc.wait(timeout=INITIALIZE_TIMEOUT_SEC + SHUTDOWN_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            problems.append(
                f"握手进程在 stdin 关闭后 "
                f"{INITIALIZE_TIMEOUT_SEC + SHUTDOWN_TIMEOUT_SEC}s 内未退出（已 kill）"
            )
    finally:
        for stream in (proc.stdin, proc.stdout):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass

    return problems, server_name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verify_akb2_retrieval_registration.py",
        description="三段校验 akb2-retrieval 的 ~/.claude.json 注册（权威源/其它键/真实握手）",
    )
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                   help="目标配置文件（默认 ~/.claude.json；测试用 tmp fixture）")
    p.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR),
                   help="pre/post 备份快照目录（默认 ~/.claude.json.reg-backup/akb2-retrieval）")
    p.add_argument("--repo-root", default=None,
                   help="权威源所在 repo 根（默认：脚本自身所在 repo）")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    repo_root = (
        Path(ns.repo_root).expanduser().resolve()
        if ns.repo_root
        else Path(__file__).resolve().parents[1]
    )

    try:
        config = _load_json(Path(ns.config).expanduser())
        entry = get_registered_entry(config)
    except PreconditionError as exc:
        print(f"[verify-registration] 前置失败: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION

    # ① 权威源一致
    authority_problems = check_authority(repo_root, config, entry)
    if authority_problems:
        for line in authority_problems:
            print(f"[verify-registration] ①权威源不一致: {line}", file=sys.stderr)
        return EXIT_AUTHORITY
    print("REG_AUTHORITY_MATCH")

    # ② 其它键 exact diff
    backup_dir = Path(ns.backup_dir).expanduser()
    try:
        pre = _load_json(backup_dir / "pre.json")
        post = _load_json(backup_dir / "post.json")
    except PreconditionError as exc:
        print(f"[verify-registration] ②快照缺失: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    other_keys_problems = check_other_keys(pre, post, config)
    if other_keys_problems:
        for line in other_keys_problems:
            print(f"[verify-registration] ②其它键被改动: {line}", file=sys.stderr)
        return EXIT_OTHER_KEYS
    stripped = _strip_server_key(post)
    print(f"REG_OTHER_KEYS_INTACT {len(stripped)}")

    # ③ 真实 MCP 握手
    handshake_problems, server_name = check_handshake(entry)
    if handshake_problems:
        for line in handshake_problems:
            print(f"[verify-registration] ③握手失败: {line}", file=sys.stderr)
        return EXIT_HANDSHAKE
    print(f"REG_HANDSHAKE_OK {server_name}")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
