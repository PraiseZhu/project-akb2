#!/usr/bin/env python3
"""harvest_bootstrap 生命周期:部署锁、根配置、manifest、signer keyring 清理。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from ._base import (
    GENERATIONS_DIR,
    ROOT_KEY_DIR,
    ROOT_PK_NAME,
    ROOT_SK_NAME,
    SCHEMA_VERSION,
    SIGNER_KEYS_DIR,
    BootstrapError,
    crash_if,
    err,
    fsync_dir,
    fsync_file,
)
from .crypto import gen_keypair, sign_bytes, sign_file
from .trustlog import entry_body_bytes

# ---------------------------------------------------------------------------
# 部署锁
# ---------------------------------------------------------------------------


def acquire_lock(c: dict) -> Path:
    """O_EXCL 取单实例部署锁。失败按序判定 pid 存活 / 陈旧 / 不可解析。"""
    lock = c["lock_file"]
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            content = lock.read_text()
            meta = json.loads(content)
            if not isinstance(meta, dict):
                raise TypeError("锁内容非对象")
            pid, ts = meta.get("pid"), meta.get("ts")
            if not isinstance(pid, int):
                raise TypeError("pid 字段非整数")
        except (OSError, json.JSONDecodeError, TypeError):
            err(f"部署锁 {lock} 内容不可解析,需人工裁定后处理", code=2)
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True  # 存在但不可信号,视为存活
        if alive:
            err(f"另一部署正在进行(锁 pid={pid}, 获取于 {ts}),不等待不抢占", code=2)
        err(
            f"检出陈旧部署锁(pid={pid}, 获取于 {ts}),需人工确认上次 migrate 的"
            f"落盘状态后删除该锁;不自动清除",
            code=2,
        )
    os.write(fd, json.dumps({"pid": os.getpid(), "ts": int(time.time())}).encode())
    os.fsync(fd)
    os.close(fd)
    return lock


def release_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# 根配置与 manifest
# ---------------------------------------------------------------------------


def load_roots_config(c: dict) -> dict:
    if not c["roots_config"].exists():
        raise BootstrapError(f"根配置缺失:{c['roots_config']}(四根路径只能来自受保护部署配置)")
    try:
        roots = json.loads(c["roots_config"].read_text())
    except json.JSONDecodeError:
        raise BootstrapError(f"根配置无法解析:{c['roots_config']}") from None
    required = {"raw_dir", "stage", "locks_dir", "batch", "trust_root"}
    missing = required - set(roots)
    if missing:
        raise BootstrapError(f"根配置缺少字段:{sorted(missing)}")
    return roots


def record_parent_chain(roots: dict) -> dict:
    """从 trust_root 出发逐级 no-follow 记录四根的完整 parent chain identity。"""
    trust_root = Path(roots["trust_root"])
    result = {}
    for name in ("raw_dir", "stage", "locks_dir", "batch"):
        target = Path(roots[name])
        try:
            rel = target.relative_to(trust_root)
        except ValueError:
            raise BootstrapError(f"根 {name} 不在 trust_root 之下:{target}") from None
        chain = []
        cur = trust_root
        for part in rel.parts:
            cur = cur / part
            try:
                st = os.stat(cur, follow_symlinks=False)
            except OSError as e:
                raise BootstrapError(f"根 {name} 路径不可解析:{cur}({e.strerror})") from None
            chain.append({"name": part, "st_dev": st.st_dev, "st_ino": st.st_ino})
        result[name] = {"path": str(target), "chain": chain}
    return result


def build_manifest(c: dict, roots: dict, epoch: int, deployment_id: str) -> dict:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": deployment_id,
        "epoch": epoch,
        "generation_dir": f"{GENERATIONS_DIR}/{epoch}",
        "roots": record_parent_chain(roots),
    }
    return manifest


def write_bundle(c: dict, epoch: int, manifest: dict, signer_sk: Path, pub_pem: bytes) -> Path:
    """写 generations/<epoch>/ 三件(manifest、签名、公钥),temp+replace+fsync。"""
    gen_dir = c["generations"] / str(epoch)
    try:
        os.mkdir(gen_dir, 0o750)
    except FileExistsError:
        raise BootstrapError(f"generation 目录已存在(拒绝复用):{gen_dir}") from None
    os.chmod(gen_dir, 0o750)
    crash_if(c, "mkdir")
    manifest_path = gen_dir / "manifest"
    tmp = gen_dir / "manifest.tmp"
    tmp.write_text(json.dumps(manifest, indent=1))
    os.replace(tmp, manifest_path)
    fsync_file(manifest_path)
    crash_if(c, "manifest")
    sig_path = gen_dir / "manifest.sig"
    sign_file(signer_sk, manifest_path, sig_path)
    crash_if(c, "sig")
    pub_path = gen_dir / "public_key"
    pub_tmp = gen_dir / "public_key.tmp"
    pub_tmp.write_bytes(pub_pem)
    os.replace(pub_tmp, pub_path)
    fsync_file(pub_path)
    crash_if(c, "pub")
    for f in ("manifest", "manifest.sig", "public_key"):
        p = gen_dir / f
        os.chmod(p, 0o440)
    fsync_dir(gen_dir)
    return gen_dir


# ---------------------------------------------------------------------------
# signer keyring
# ---------------------------------------------------------------------------


def ensure_signer_keys_dir(c: dict) -> None:
    c["signer_keys"].mkdir(mode=0o710, exist_ok=True)
    os.chmod(c["signer_keys"], 0o710)


def current_key_number(c: dict) -> int | None:
    """signer-keys/ 中存在的 K<n>.sk 的最大编号。"""
    nums = []
    for p in c["signer_keys"].glob("K*.sk"):
        try:
            nums.append(int(p.name[1:-3]))
        except ValueError:
            pass
    return max(nums) if nums else None


def cleanup_crash_leftovers(c: dict, tip: dict) -> None:
    """重入规则:signer-keys/ 恢复为有且仅有当代准入的一把私钥,其余就地删除。

    「E_n 已落盘、K_{n-1}.sk 尚未删除」是已定义的合法中间态,崩溃后 keyring 会
    同时存在两把私钥;此外「新代 K_{n+1}.sk 已生成但条目未落」也会留下未准入的
    半成品(openssl genpkey 不覆盖已存在文件,重跑会卡死)。两条都是崩溃窗口残留:
    编号 != 当代准入编号的私钥一律删除并 fsync 目录,不报错、不要求人工介入。
    """
    tip_num = tip.get("admission", {}).get("key_number")
    for p in list(c["signer_keys"].glob("K*.sk")):
        m = int(p.name[1:-3]) if p.name[1:-3].isdigit() else None
        if m is None:
            continue
        if tip_num is None or m != tip_num:
            p.unlink()
    fsync_dir(c["signer_keys"])


def cleanup_orphan_generations(c: dict, keep_epochs: set[int]) -> None:
    """删除未发布的半成品 generation 目录(epoch 不在 keep 集合内)。

    失败的半成品目录留在磁盘上不影响读侧,由人工或下次 init/migrate 清理;
    目录以 O_EXCL 唯一创建且发布后不可修改,故非 keep 集内目录必为半成品。
    """
    if not c["generations"].exists():
        return
    for p in list(c["generations"].iterdir()):
        if not p.is_dir():
            continue
        try:
            n = int(p.name)
        except ValueError:
            continue
        if n not in keep_epochs:
            shutil.rmtree(p)


# ---------------------------------------------------------------------------
# 布局验证与骨架
# ---------------------------------------------------------------------------


def verify_layout(c: dict) -> None:
    """验证部署根目录布局:四目录结构 + 根配置 + root-key 均存在且权限正确。"""
    root = c["deploy_root"]
    if not root.exists():
        raise BootstrapError(f"部署根不存在:{root}")
    if not root.is_dir():
        raise BootstrapError(f"部署根不是目录:{root}")
    for d, mode in [
        (root / ROOT_KEY_DIR, 0o700),
        (root / SIGNER_KEYS_DIR, 0o710),
        (root / GENERATIONS_DIR, 0o750),
        (c["trust_log"].parent, 0o750),
    ]:
        if not d.exists():
            raise BootstrapError(f"部署布局缺少目录:{d}")
        if not d.is_dir():
            raise BootstrapError(f"路径不是目录:{d}")
        st = d.stat()
        if (st.st_mode & 0o777) != mode:
            raise BootstrapError(
                f"目录权限不符:{d}(期望 {mode:04o}, 实际 {st.st_mode & 0o777:04o})"
            )
    if not c["roots_config"].exists():
        raise BootstrapError(f"根配置缺失:{c['roots_config']}")
    sk = root / ROOT_KEY_DIR / ROOT_SK_NAME
    if not sk.exists():
        raise BootstrapError(f"root 私钥缺失:{sk}")


def ensure_skeleton(c: dict) -> None:
    """创建部署骨架目录结构(幂等,已存在不报错)。"""
    root = c["deploy_root"]
    root.mkdir(mode=0o750, exist_ok=True)
    os.chmod(root, 0o750)
    for name, mode in [
        (ROOT_KEY_DIR, 0o700),
        (SIGNER_KEYS_DIR, 0o710),
        (GENERATIONS_DIR, 0o750),
    ]:
        d = root / name
        d.mkdir(mode=mode, exist_ok=True)
        os.chmod(d, mode)
    trust_log_dir = c["trust_log"].parent
    trust_log_dir.mkdir(mode=0o750, exist_ok=True)
    os.chmod(trust_log_dir, 0o750)


# ---------------------------------------------------------------------------
# root 密钥
# ---------------------------------------------------------------------------


def ensure_root_key(c: dict) -> tuple[Path, Path, bytes]:
    """确保 root 密钥对存在:缺失则生成,部分存在则补齐公钥。

    返回 (sk_path, pk_path, pub_pem_bytes)。私钥 mode 0o400,公钥 mode 0o440。
    """
    sk = c["deploy_root"] / ROOT_KEY_DIR / ROOT_SK_NAME
    pk = c["deploy_root"] / ROOT_KEY_DIR / ROOT_PK_NAME
    if sk.exists():
        if pk.exists():
            return sk, pk, pk.read_bytes()
        # 私钥存在但公钥缺失:从私钥导出公钥
        pub = _derive_pub_from_sk(sk)
        pk.write_bytes(pub)
        os.chmod(pk, 0o440)
        fsync_file(pk)
        return sk, pk, pub
    # 全新生成
    pub = gen_keypair(sk, None)
    pk.write_bytes(pub)
    os.chmod(pk, 0o440)
    fsync_file(pk)
    return sk, pk, pub


def _derive_pub_from_sk(sk: Path) -> bytes:
    """从已有私钥导出公钥 PEM。"""
    import subprocess

    r = subprocess.run(
        ["openssl", "pkey", "-in", str(sk), "-pubout"],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return r.stdout


# ---------------------------------------------------------------------------
# trust-log 条目构造
# ---------------------------------------------------------------------------


def build_admission(
    roots: dict, key_number: int, key_id: str, deployment_id: str
) -> dict:
    """构建 trust-log 条目中的 admission 段(四根 parent chain + 密钥身份)。"""
    return {
        "roots": record_parent_chain(roots),
        "key_number": key_number,
        "key_id": key_id,
        "deployment_id": deployment_id,
        "schema_version": SCHEMA_VERSION,
    }


def build_entry(
    c: dict,
    epoch: int,
    admission: dict,
    prev_entry_hash: str | None,
    signer_sk: Path,
) -> dict:
    """构建完整的 trust-log 条目(含签名)。"""
    entry: dict = {
        "epoch": epoch,
        "prev_entry_hash": prev_entry_hash,
        "admission": admission,
    }
    sig = sign_bytes(signer_sk, entry_body_bytes(entry))
    entry["signature"] = sig
    return entry
