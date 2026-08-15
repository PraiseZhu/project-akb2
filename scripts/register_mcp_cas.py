"""~/.claude.json MCP 注册的协作写者协议（flock + merge CAS）。

背景（为什么不能用「读→改→写」裸写）：
    ~/.claude.json 会被多个写者并发触碰——本注册器、Claude Code 本体、其他
    配置工具。裸 read-modify-write 会让两个写者各基于旧快照合并、后写覆盖
    先写，注册条目静默丢失。范式与 loops/ledger.py 的缺陷 #11c 教训一致。

协议（本模块是协议写者，只对同样持锁的写者提供互斥）：
    1. 在 sidecar 锁文件 <config>.lock 上 fcntl.flock(LOCK_EX)。
       **为什么不锁数据文件本身**：os.replace 会换 inode，锁在旧 inode 上
       的 flock 拦不住下一个写者打开新 inode——sidecar 是唯一跨 replace
       稳定的互斥点。
    2. 持锁覆盖整段 read → merge（只增/改 mcpServers.<name>，其余键原样
       保留）→ 写同目录临时文件 → fsync → os.replace。
    3. **解锁之后**才做最终一致性检查（R3-C2）：重读文件比对 SHA-256。
       检查若放在锁内，非协议写者仍可在 check 通过后、解锁前写入而不被
       发现；只有解锁后重读才能覆盖到这个窗口。
    4. hash 失配时消歧：本键仍原样在场 → 判定为并发写者叠加（其他键变更
       与本注册无冲突），成功返回；本键缺失/被改 → 非协议写者覆盖，按有
       界重试（MAX_WRITE_RETRIES=3，指数退避）重新走完整持锁流程。

保证边界（如实声明，无全称双键保证）：
    存在不持锁的非协议写者时，本协议不能阻止其覆盖，只能**检测 → 有界
    重试 → 耗尽 fail-closed 退出**并打印冲突详情与最后一次观测 hash。
    重试耗尽不回写、不强覆盖——fail-closed 意味着把现状留给人工裁决。

退出码：
    0  注册成功（含「检测到并发叠加、本键完好」的成功路径）
    2  CLI 用法错误（argparse）
    3  配置文件不可解析 / 顶层不是 JSON object——fail-closed，不改写一个字节
    4  有界重试耗尽仍被非协议写者覆盖——fail-closed，打印冲突详情
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 具名预算常量（宪法 P1/P4：有界、显式）
# ---------------------------------------------------------------------------

MAX_WRITE_RETRIES = 3          # 完整持锁流程最多执行 3 次（首次 + 2 重试）
RETRY_BACKOFF_BASE_SEC = 0.05  # 指数退避基数：base * 2**(attempt-1)

DEFAULT_CONFIG_PATH = Path.home() / ".claude.json"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CORRUPT = 3
EXIT_CONFLICT = 4


class ConfigCorruptError(Exception):
    """配置文件存在但不可解析（或顶层不是 object）——重试无意义，直接 fail-closed。"""


# ---------------------------------------------------------------------------
# 基础原语
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lock_path_for(config_path: Path) -> Path:
    """sidecar 锁文件路径：<config>.lock（与数据文件同目录、跨 replace 稳定）。"""
    return config_path.with_name(config_path.name + ".lock")


def _read_config_under_lock(config_path: Path) -> dict[str, Any]:
    """读取配置；文件缺失视为空配置（首次注册）。

    Raises:
        ConfigCorruptError: 文件存在但 JSON 不可解析，或顶层不是 object。
    """
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError:
        return {}
    if not raw.strip():
        return {}
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigCorruptError(f"{config_path} 不是合法 JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigCorruptError(f"{config_path} 顶层不是 JSON object，拒绝改写")
    return config


def merge_server_entry(
    config: dict[str, Any], server_name: str, entry: dict[str, Any]
) -> dict[str, Any]:
    """合并：只增/改 mcpServers.<server_name>，其余键（含其他 server 条目）原样保留。"""
    servers = config.get("mcpServers")
    if servers is None:
        servers = {}
        config["mcpServers"] = servers
    elif not isinstance(servers, dict):
        raise ConfigCorruptError("mcpServers 不是 JSON object，拒绝改写")
    servers[server_name] = copy.deepcopy(entry)
    return config


# ---------------------------------------------------------------------------
# 持锁交换（read → merge → temp → fsync → replace，全程 flock）
# ---------------------------------------------------------------------------

def _locked_exchange(config_path: Path, server_name: str, entry: dict[str, Any]) -> str:
    """持 flock 完成整段 read→merge→temp→fsync→os.replace。

    Returns:
        本次写入内容的 SHA-256（post-check 的 expected hash）。

    锁在函数返回时已释放——最终一致性检查必须在锁外做（R3-C2）。
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = lock_path_for(config_path)

    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            config = merge_server_entry(
                _read_config_under_lock(config_path), server_name, entry
            )
            payload = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

            # 保留既有文件权限；新建则 0600（~/.claude.json 含 token，私有为默认）
            try:
                mode = os.stat(config_path).st_mode & 0o777
            except FileNotFoundError:
                mode = 0o600

            fd, tmp_name = tempfile.mkstemp(
                dir=str(config_path.parent), prefix=f".{config_path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(payload)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(tmp_name, mode)
                os.replace(tmp_name, config_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise

            # 目录 fsync：让 replace 本身在崩溃后也可见
            dir_fd = os.open(str(config_path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

            return sha256_bytes(payload)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# 解锁后一致性检查（R3-C2：必须在锁释放之后）
# ---------------------------------------------------------------------------

def _post_check(
    config_path: Path, server_name: str, entry: dict[str, Any], expected_hash: str
) -> tuple[bool, str, str]:
    """解锁后重读磁盘，比对整文件 hash；失配时按本键状态消歧。

    Returns:
        (ok, observed_hash, kind)
        ok=True  kind ∈ {"match", "benign-divergence"}
        ok=False kind ∈ {"key-missing", "key-altered", "unreadable"}
    """
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError:
        return False, "", "key-missing"
    observed = sha256_bytes(raw)
    if observed == expected_hash:
        return True, observed, "match"
    try:
        current = json.loads(raw).get("mcpServers", {}).get(server_name)
    except (json.JSONDecodeError, AttributeError):
        return False, observed, "unreadable"
    if current == entry:
        # 整文件变了但本键完好：并发写者叠加（可能同样是协议写者），非冲突
        return True, observed, "benign-divergence"
    return False, observed, "key-missing" if current is None else "key-altered"


# ---------------------------------------------------------------------------
# 注册主流程：持锁写入 → 解锁后检查 → 有界重试 → 耗尽 fail-closed
# ---------------------------------------------------------------------------

def register(
    config_path: str | Path,
    server_name: str,
    entry: dict[str, Any],
    *,
    max_retries: int = MAX_WRITE_RETRIES,
    backoff_base_sec: float = RETRY_BACKOFF_BASE_SEC,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """按协作写者协议注册一个 MCP server 条目，返回进程退出码。

    Args:
        config_path: 目标配置文件（测试用 tmp fixture；生产为 ~/.claude.json）。
        server_name: mcpServers 下的键名。
        entry: 条目内容（{"command": ..., "args": [...], "env": {...}}）。
        max_retries: 完整持锁流程的最大执行次数（含首次）。
        backoff_base_sec: 指数退避基数。
        sleep: 退避休眠函数（测试注入记录器；默认 time.sleep）。
    """
    if not isinstance(entry, dict):
        raise TypeError(f"entry 必须是 dict，得到 {type(entry).__name__}")
    path = Path(config_path)
    delays: list[float] = []
    last_expected = ""
    last_observed = ""
    last_kind = ""

    for attempt in range(1, max_retries + 1):
        try:
            last_expected = _locked_exchange(path, server_name, entry)
        except ConfigCorruptError as exc:
            print(f"[register-mcp-cas] 配置不可解析，fail-closed 不改写: {exc}", file=sys.stderr)
            return EXIT_CORRUPT

        ok, last_observed, last_kind = _post_check(path, server_name, entry, last_expected)
        if ok:
            if last_kind == "benign-divergence":
                print(
                    f"[register-mcp-cas] 提示: 解锁后检测到其他写者变更，"
                    f"本键 {server_name} 完好，判定为并发叠加而非冲突"
                    f"（observed={last_observed}）",
                    file=sys.stderr,
                )
            return EXIT_OK
        if attempt < max_retries:
            delay = backoff_base_sec * (2 ** (attempt - 1))
            delays.append(delay)
            sleep(delay)

    schedule = "/".join(f"{d:.2f}s" for d in delays) if delays else "无"
    print(
        f"[register-mcp-cas] 冲突: 非协议写者在 {max_retries} 次有界重试后仍覆盖注册结果，"
        f"fail-closed 退出\n"
        f"  config: {path}\n"
        f"  server: {server_name}\n"
        f"  本键状态: {last_kind}\n"
        f"  退避: {schedule}\n"
        f"  期望 hash（最后一次我们写入）: {last_expected}\n"
        f"  观测 hash（最后一次磁盘现状）: {last_observed}",
        file=sys.stderr,
    )
    return EXIT_CONFLICT


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="register_mcp_cas.py",
        description="按 flock+merge CAS 协议向配置注册 MCP server 条目（协作写者协议）",
    )
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                   help="目标配置文件路径（默认 ~/.claude.json）")
    p.add_argument("--server-name", required=True, help="mcpServers 下的键名")
    p.add_argument("--command", required=True, help="server 启动命令")
    p.add_argument("--args", nargs="*", default=[], metavar="ARG",
                   help="传给 server 命令的参数（可多个；以 - 开头的参数请改用 --args-json）")
    p.add_argument("--args-json", default=None, metavar="JSON",
                   help='args 的 JSON 数组形式（全保真，如 \'["-y", "pkg"]\'），与 --args 互斥')
    p.add_argument("--env", action="append", default=None, metavar="KEY=VALUE",
                   help="server 环境变量，每条一个（可重复：--env K1=V1 --env K2=V2）")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)

    args_list: list[str]
    if ns.args_json is not None:
        if ns.args:
            print("[register-mcp-cas] --args 与 --args-json 互斥，只能给一个", file=sys.stderr)
            return EXIT_USAGE
        try:
            parsed = json.loads(ns.args_json)
        except json.JSONDecodeError as exc:
            print(f"[register-mcp-cas] --args-json 不是合法 JSON: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if not isinstance(parsed, list) or not all(isinstance(a, str) for a in parsed):
            print("[register-mcp-cas] --args-json 必须是字符串数组（如 '[\"-y\", \"pkg\"]'）",
                  file=sys.stderr)
            return EXIT_USAGE
        args_list = list(parsed)
    else:
        args_list = list(ns.args)

    env: dict[str, str] = {}
    for pair in ns.env or []:
        if "=" not in pair:
            print(f"[register-mcp-cas] --env 需要 KEY=VALUE 形式，得到: {pair!r}", file=sys.stderr)
            return EXIT_USAGE
        key, _, value = pair.partition("=")
        env[key] = value
    entry: dict[str, Any] = {"command": ns.command, "args": args_list}
    if env:
        entry["env"] = env
    return register(ns.config, ns.server_name, entry)


if __name__ == "__main__":
    sys.exit(main())
