#!/usr/bin/env python3
"""harvest_bootstrap trust-log 模块:链式校验、读写追加、坏尾检测。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ._base import (
    ENV_SHORT_WRITE,
    BootstrapError,
    crash_if,
    err,
    fsync_dir,
    fsync_file,
    sha256_bytes,
    test_mode_enabled,
)


def parse_trust_log(path: Path) -> tuple[list[dict], str | None]:
    """解析 trust-log。返回 (完整条目列表, 坏尾字节串或 None)。

    坏尾 = (a) 末尾无法解析为完整 JSON 行的字节(截断/短写残留),
    或 (b) 末尾不以换行终止的段——短写恰好去掉尾部 \\n 时 JSON 仍可解析,
    但该行不是一条完整条目(正常追加总是 json + \\n),仍必须判为坏尾。
    """
    if not path.exists():
        return [], None
    raw = path.read_bytes()
    segments = raw.split(b"\n")
    trailing = b"" if segments[-1] == b"" else segments[-1]
    if trailing:
        segments = segments[:-1]
    entries: list[dict] = []
    offset = 0
    for raw_line in segments:
        offset += len(raw_line) + 1
        line = raw_line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            return entries, raw[offset:]
    if trailing:
        return entries, trailing
    return entries, None


def entry_hash(entry: dict) -> str:
    return sha256_bytes(json.dumps(entry, sort_keys=True).encode())


def entry_body_bytes(entry: dict) -> bytes:
    """条目中参与签名的字节(除 signature 字段外全部)。"""
    body = {k: v for k, v in entry.items() if k != "signature"}
    return json.dumps(body, sort_keys=True).encode()


def verify_entry_signature(entry: dict, signer_pub: Path) -> bool:
    """用 signer_pub 验证条目签名。"""
    data = entry_body_bytes(entry)
    tmp = signer_pub.with_name(f".verify-{os.getpid()}-{id(entry)}")
    data_path = tmp.with_suffix(".data")
    sig_path = tmp.with_suffix(".sig")
    try:
        data_path.write_bytes(data)
        sig_path.write_bytes(bytes.fromhex(entry["signature"]))
        r = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(signer_pub),
             "-signature", str(sig_path), str(data_path)],
            capture_output=True, stdin=subprocess.DEVNULL,
        )
        return r.returncode == 0
    finally:
        for p in (data_path, sig_path):
            if p.exists():
                p.unlink()


def verify_chain_from_root(c: dict, entries: list[dict]) -> None:
    """从头验证条目链:每条的前驱 hash 与签名(创世由 ROOT_SK,其后由前代密钥)。"""
    if not entries:
        raise BootstrapError("trust-log 为空,无法验证链")
    prev_hash = None
    for i, e in enumerate(entries):
        if e.get("prev_entry_hash") != prev_hash:
            raise BootstrapError(f"链断裂:条目 {i+1} 的前驱 hash 不符")
        # 签名者:创世 = ROOT_SK;其后 = 前代公钥(条目内准入的 key 对应的 generations/<n>/public_key)
        if i == 0:
            pub = c["root_pk"]
        else:
            prev_epoch = entries[i - 1]["epoch"]
            pub = c["generations"] / str(prev_epoch) / "public_key"
        if not verify_entry_signature(e, pub):
            raise BootstrapError(f"条目 {i+1}(epoch={e.get('epoch')}) 签名验证失败")
        prev_hash = entry_hash(e)


def read_tip(c: dict) -> tuple[list[dict], dict | None, str | None, str | None]:
    """读 trust-log,返回 (entries, 末条完整条目, 坏尾字节, 末条 hash)。"""
    entries, bad = parse_trust_log(c["trust_log"])
    if not entries:
        return entries, None, bad, None
    tip = entries[-1]
    return entries, tip, bad, entry_hash(tip)


def append_entry(c: dict, entry: dict) -> int:
    """对 trust-log 做一次 O_APPEND 单次 os.write + fsync(文件与父目录)。

    返回写入字节数。返回值 != 条目长度 → 当场判定坏尾,不重试续写,exit 2。
    """
    payload = json.dumps(entry, sort_keys=True).encode() + b"\n"
    fd = os.open(c["trust_log"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        if os.environ.get(ENV_SHORT_WRITE) == "1" and test_mode_enabled(c):
            # 测试注入:模拟短写(请求写 N 字节,内核只写 N-1)
            n = os.write(fd, payload[:-1])
        else:
            n = os.write(fd, payload)
    finally:
        os.close(fd)
    if n != len(payload):
        err(
            f"坏尾:trust-log 单次追加短写({n} < {len(payload)} 字节),不重试续写;"
            f"需人工受控恢复(scripts/harvest_trustlog_recover.py)",
            code=2,
        )
    crash_if(c, "pre-fsync")
    fsync_file(c["trust_log"])
    fsync_dir(c["trust_log"].parent)
    return n


def check_bad_tail(c: dict, entries: list[dict], bad: str | None, tip: dict | None) -> None:
    """写侧坏尾检查:存在坏尾 → fail-closed,拒绝任何写入。"""
    if bad is not None:
        pos = c["trust_log"].stat().st_size - len(bad)
        err(
            f"坏尾:trust-log 尾部存在 {len(bad)} 字节不完整条目(偏移 {pos}),"
            f"拒绝追加;需人工受控恢复(scripts/harvest_trustlog_recover.py)",
            code=2,
        )
