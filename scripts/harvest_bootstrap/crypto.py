#!/usr/bin/env python3
"""harvest_bootstrap 密码模块:openssl 封装(RSA-3072 + SHA256;外部 CLI 依赖,系统自带)。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ._base import BootstrapError, fsync_file


def gen_keypair(sk_path: Path, pub_pem: bytes) -> bytes:
    """生成 RSA 密钥对:私钥落 sk_path,返回公钥 PEM(供签发材料预置)。"""
    # openssl genpkey 不接受落盘 mode 参数,文件创建瞬间的 mode 完全由 ambient
    # umask 决定(常见 022 即世界可读);在随后 chmod(0o400) 之前存在私钥
    # group/other 可读的窗口。落盘期间临时收紧到 0o177(仅 owner rw),写完立刻
    # 还原 ambient umask,不影响本函数外任何文件(如 trust-log 0640)的落盘 mode。
    old_umask = os.umask(0o177)
    try:
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
             "-out", str(sk_path)],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
    finally:
        os.umask(old_umask)
    os.chmod(sk_path, 0o400)
    fsync_file(sk_path)
    pub_tmp = sk_path.with_name(sk_path.name + ".pub.tmp")
    subprocess.run(
        ["openssl", "pkey", "-in", str(sk_path), "-pubout", "-out", str(pub_tmp)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    got = pub_tmp.read_bytes()
    pub_tmp.unlink()
    if pub_pem is not None and got != pub_pem:
        raise BootstrapError("生成的公钥与预期不符(内部错误)")
    return got


def sign_bytes(sk_path: Path, data: bytes) -> str:
    """用私钥对 data 签名,返回 DER 签名的 hex。

    data 必须落临时文件传给 openssl——openssl dgst 缺输入文件时读 stdin,
    继承的 stdin 可能阻塞或读到空数据产生「对空数据的签名」。
    """
    tmp = sk_path.with_name(f".sign-{os.getpid()}-{time.time_ns()}")
    data_path = tmp.with_suffix(".data")
    out_path = tmp.with_suffix(".sig")
    try:
        data_path.write_bytes(data)
        subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(sk_path),
             "-out", str(out_path), str(data_path)],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        sig = out_path.read_bytes()
    finally:
        for p in (data_path, out_path):
            if p.exists():
                p.unlink()
    return sig.hex()


def sign_file(sk_path: Path, data_path: Path, sig_path: Path) -> None:
    """对 data_path 文件签名,写 sig_path(temp + rename + fsync)。"""
    out_tmp = sig_path.with_name(sig_path.name + f".tmp{os.getpid()}")
    subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(sk_path), "-out", str(out_tmp), str(data_path)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    os.replace(out_tmp, sig_path)
    fsync_file(sig_path)
