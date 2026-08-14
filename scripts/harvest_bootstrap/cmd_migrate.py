#!/usr/bin/env python3
"""harvest_bootstrap cmd_migrate:迁移到下一代(签发新 signer 密钥、废止旧密钥、
追加 trust-log 条目)。

本模块从 lifecycle 提取为独立切片;依赖 _base / crypto / trustlog / lifecycle 的导入符号,
这些模块由其他切片提供,不在本切片内。
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from ._base import (
    ENV_PAUSE_CAS,
    GENERATIONS_DIR,
    err,
    fsync_dir,
    test_mode_enabled,
)
from .crypto import gen_keypair
from .lifecycle import (
    acquire_lock,
    build_entry,
    build_manifest,
    cleanup_crash_leftovers,
    cleanup_orphan_generations,
    ensure_skeleton,
    load_roots_config,
    release_lock,
    verify_layout,
    write_bundle,
)
from .trustlog import (
    append_entry,
    check_bad_tail,
    read_tip,
    verify_chain_from_root,
)


def cmd_migrate(c: dict, deployment_id: str) -> None:
    """迁移到下一代:签发新 signer 密钥、废止旧密钥、追加 trust-log 条目."""
    roots = load_roots_config(c)
    ensure_skeleton(c)
    lock = acquire_lock(c)
    try:
        entries, tip, bad, h1 = read_tip(c)
        if not entries or tip is None:
            err("未初始化:trust-log 为空,migrate 需先 init")
        check_bad_tail(c, entries, bad, tip)
        n = tip["epoch"]
        if tip["deployment_id"] != deployment_id:
            err(
                f"deployment_id 不符:trust-log 末条为 "
                f"{tip['deployment_id']!r},传入 {deployment_id!r}"
            )
        if os.environ.get(ENV_PAUSE_CAS) and test_mode_enabled(c):
            time.sleep(1.0)
        entries2, tip2, bad2, h2 = read_tip(c)
        if bad2 is not None:
            check_bad_tail(c, entries2, bad2, tip2)
        if tip2 is None or tip2["epoch"] != n or h2 != h1:
            err(
                f"CAS mismatch:取锁时末条为 (epoch={n}, entry_hash={h1}),"
                f"写前复核为 (epoch={tip2['epoch'] if tip2 else None}, entry_hash={h2});"
                f"trust-log 零追加,中止"
            )
        verify_chain_from_root(c, entries)
        cleanup_crash_leftovers(c, tip)
        cleanup_orphan_generations(c, set(range(1, n + 1)))
        prev_sk = c["signer_keys"] / f"K{n}.sk"
        if not prev_sk.exists():
            err(f"上一代签名私钥缺失:{prev_sk}(不回退 ROOT_SK 代签;需人工核验部署状态)")
        new_n = n + 1
        new_sk = c["signer_keys"] / f"K{new_n}.sk"
        gen_keypair(new_sk, None)
        new_pub = subprocess.run(
            ["openssl", "pkey", "-in", str(new_sk), "-pubout"],
            capture_output=True, stdin=subprocess.DEVNULL, check=True,
        ).stdout
        gen_dir = write_bundle(
            c, new_n, build_manifest(c, roots, new_n, deployment_id), new_sk, new_pub
        )
        # E_{n+1}:由 K_n 签(K_{n+1} 准入 / K_n 废止)
        e_new = build_entry(
            c, new_n, deployment_id, gen_dir, new_pub, prev_sk,
            prev_hash=h1, prev_key_id=tip["admission"]["key_id"],
        )
        verify_layout(c, roots)
        append_entry(c, e_new)
        prev_sk.unlink()
        fsync_dir(c["signer_keys"])
        append_ok = len(json.dumps(e_new, sort_keys=True).encode())
        print(f"[migrate] epoch={new_n} generation={GENERATIONS_DIR}/{new_n} append_ok={append_ok}")
    finally:
        release_lock(lock)
