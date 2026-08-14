#!/usr/bin/env python3
"""harvest_bootstrap 生命周期:部署锁、根配置、manifest、signer keyring 清理。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from ._base import (
    GENERATIONS_DIR,
    K_ID_PREFIX,
    ROOT_KEY_DIR,
    SCHEMA_VERSION,
    SIGNER_KEYS_DIR,
    BootstrapError,
    crash_if,
    err,
    fsync_dir,
    fsync_file,
    key_id_of_pem,
    sha256_bytes,
    sha256_file,
)
from .crypto import gen_keypair, sign_bytes, sign_file
from .trustlog import (
    append_entry,
    check_bad_tail,
    entry_body_bytes,
    read_tip,
)

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


def verify_layout(c: dict, roots: dict) -> None:
    """部署侧权限表校验(init/migrate 生效前;签发材料与运行期写面两栏都验)。

    文件按「存在才校验」处理:init 在 append 前验时 trust-log/recovery-log
    可能尚未创建(append_entry 才落盘),缺文件不构成布局失败;目录仍要求
    在场(ensure_skeleton 已建齐,缺目录即骨架异常,失败)。
    """
    uid = c["deploy_uid"]
    reader = c["reader_gid"]
    runtime_uid = c["runtime_uid"]

    def _owner_mode(path: Path, owner: int, mode: int, group: int | None = None,
                    what: str = "") -> None:
        try:
            st = path.stat()
        except FileNotFoundError:
            return
        if st.st_uid != owner:
            raise BootstrapError(f"权限校验失败:{what} owner={st.st_uid},期望 {owner}")
        if group is not None and st.st_gid != group:
            raise BootstrapError(f"权限校验失败:{what} group={st.st_gid},期望 {group}")
        if st.st_mode & 0o777 != mode:
            raise BootstrapError(
                f"权限校验失败:{what} mode={oct(st.st_mode & 0o777)},期望 {oct(mode)}"
            )

    for d in (c["root_key_dir"], c["trust_log_dir"], c["generations"], c["signer_keys"]):
        if not d.exists():
            raise BootstrapError(f"部署布局缺少目录:{d}")
        if not d.is_dir():
            raise BootstrapError(f"路径不是目录:{d}")
    # 签发材料:owner 部署 UID、mode 0400、运行期无任何权限
    _owner_mode(c["root_sk"], uid, 0o400, what="ROOT_SK")
    _owner_mode(c["roots_config"], uid, 0o400, what="根配置")
    # root key 验证公钥:bundle 栏(0440,reader 组只读)
    _owner_mode(c["root_pk"], uid, 0o440, reader, what="ROOT_PK")
    # 追加型权威记录:0640(部署 UID O_APPEND 追加、runtime 只读)、父目录 0750
    _owner_mode(c["trust_log_dir"], uid, 0o750, what="trust-log 父目录")
    # (0660 组可写必须拒:权威日志的组位只允许读)
    _owner_mode(c["trust_log"], uid, 0o640, reader, what="trust-log")
    _owner_mode(c["recovery_log"], uid, 0o640, reader, what="recovery-log")
    # generations 树:bundle 0440/reader 组;目录 0750
    _owner_mode(c["generations"], uid, 0o750, what="generations/")
    for gen_dir in (
        sorted(c["generations"].iterdir(), key=lambda p: p.name)
        if c["generations"].exists() else []
    ):
        if not gen_dir.is_dir():
            continue
        _owner_mode(gen_dir, uid, 0o750, what=f"generations/{gen_dir.name}")
        for f in ("manifest", "manifest.sig", "public_key"):
            _owner_mode(gen_dir / f, uid, 0o440, reader, what=f"generations/{gen_dir.name}/{f}")
    # signer keyring:目录 0710(组仅 search),私钥 0400/部署 owner
    _owner_mode(c["signer_keys"], uid, 0o710, reader, what="signer-keys/")
    for k in c["signer_keys"].glob("K*.sk"):
        _owner_mode(k, uid, 0o400, what=f"signer-keys/{k.name}")
    # 运行期写面:存在 → owner runtime UID、0750;不存在 → 父链每级对 runtime 至少 x
    trust_root = Path(roots["trust_root"])
    for name in ("raw_dir", "stage", "locks_dir", "batch"):
        target = Path(roots[name])
        rel = target.relative_to(trust_root)
        cur = trust_root
        for part in rel.parts:
            cur = cur / part
            if not cur.exists():
                break
            st = cur.stat()
            if st.st_mode & 0o007:
                raise BootstrapError(f"权限校验失败:根 {name} 父链 {cur} 其他位可写")
            if not (st.st_mode & 0o010):
                raise BootstrapError(f"权限校验失败:根 {name} 父链 {cur} 对 runtime 无 x")
        if target.exists():
            _owner_mode(target, runtime_uid, 0o750, what=f"运行期根 {name}")


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


def ensure_root_key(c: dict) -> str:
    """ROOT_SK 缺失则生成(部署/离线持有;init 自举),返回 ROOT_PK 指纹。

    部署常量钉住指纹(root_fp 非空)时与实际指纹比对,不符即 fail-closed。
    """
    if not c["root_sk"].exists():
        gen_keypair(c["root_sk"], None)
        os.chmod(c["root_sk"], 0o400)
        pub = subprocess.run(
            ["openssl", "pkey", "-in", str(c["root_sk"]), "-pubout"],
            capture_output=True, stdin=subprocess.DEVNULL, check=True,
        ).stdout
        pub_tmp = c["root_pk"].with_name(c["root_pk"].name + f".tmp{os.getpid()}")
        pub_tmp.write_bytes(pub)
        os.replace(pub_tmp, c["root_pk"])
        os.chmod(c["root_pk"], 0o440)
        fsync_file(c["root_pk"])
        fsync_dir(c["root_key_dir"])
    else:
        if not c["root_pk"].exists():
            raise BootstrapError("ROOT_SK 存在但 ROOT_PK 缺失,部署状态不一致")
        pub = c["root_pk"].read_bytes()
    fp = sha256_bytes(pub)
    if c["root_fp"]:
        if c["root_fp"] != fp:
            raise BootstrapError(
                f"ROOT_PK 指纹不符:部署常量钉住 {c['root_fp']},实际 {fp}——"
                f"带外人工流程核对后修正部署常量"
            )
    return fp


# ---------------------------------------------------------------------------
# trust-log 条目构造
# ---------------------------------------------------------------------------


def build_admission(
    c: dict, epoch: int, key_id: str, pub_pem: bytes, signer_sk: Path
) -> dict:
    """准入证书:由前驱私钥对 {key_id, public_key_pem, epoch, predecessor} 签名。

    key_number 标明本代密钥编号(epoch),供崩溃残留清理判定「当代准入编号」。
    """
    predecessor = "root" if epoch == 1 else f"K{epoch - 1}"
    body = {
        "key_id": key_id,
        "public_key_pem": pub_pem.decode(),
        "epoch": epoch,
        "predecessor": predecessor,
    }
    cert = sign_bytes(signer_sk, json.dumps(body, sort_keys=True).encode())
    return {
        "key_id": key_id,
        "key_number": epoch,
        "public_key_id": key_id,
        "public_key_sha256": sha256_bytes(pub_pem),
        "predecessor": predecessor,
        "certificate": cert,
    }


def build_entry(
    c: dict,
    epoch: int,
    deployment_id: str,
    gen_dir: Path,
    pub_pem: bytes,
    signer_sk: Path,
    prev_hash: str | None,
    prev_key_id: str | None,
) -> dict:
    """构建完整的 trust-log 条目(含 bundle 摘要、准入证书与签名)。"""
    key_id = key_id_of_pem(pub_pem)
    entry = {
        "deployment_id": deployment_id,
        "epoch": epoch,
        "generation_dir": f"{GENERATIONS_DIR}/{epoch}",
        "bundle": {
            "manifest_sha256": sha256_file(gen_dir / "manifest"),
            "signature_sha256": sha256_file(gen_dir / "manifest.sig"),
            "public_key_sha256": sha256_file(gen_dir / "public_key"),
        },
        "admission": build_admission(c, epoch, key_id, pub_pem, signer_sk),
        "revocation": (
            {"prev_key_id": prev_key_id, "revoked_at_epoch": epoch} if prev_key_id else None
        ),
        "prev_entry_hash": prev_hash,
    }
    entry["signature"] = sign_bytes(signer_sk, entry_body_bytes(entry))
    return entry


# ---------------------------------------------------------------------------
# init / migrate
# ---------------------------------------------------------------------------


def cmd_init(c: dict, deployment_id: str) -> None:
    """初始化 harvest 部署:创世 trust-log 条目(epoch 1)。

    由 ROOT_SK 对 K1 准入签名,落盘 generations/1/ 三件与 trust-log 首条。
    生效前校验已落盘布局;校验失败 → trust-log 仍为空,残留可被下次 init 回收。
    """
    roots = load_roots_config(c)
    ensure_skeleton(c)
    lock = acquire_lock(c)
    try:
        entries, tip, bad, h = read_tip(c)
        if entries:
            err("部署已初始化(trust-log 非空);重新 init 只能由带外人工流程清空状态后执行")
        check_bad_tail(c, entries, bad, tip)
        cleanup_orphan_generations(c, set())
        # 生成或复用 root key
        fp = ensure_root_key(c)
        # 生成 K1 signer key
        k1_sk = c["signer_keys"] / "K1.sk"
        if not k1_sk.exists():
            gen_keypair(k1_sk, None)
        pub_pem = subprocess.run(
            ["openssl", "pkey", "-in", str(k1_sk), "-pubout"],
            capture_output=True, stdin=subprocess.DEVNULL, check=True,
        ).stdout
        gen_dir = write_bundle(
            c, 1, build_manifest(c, roots, 1, deployment_id), k1_sk, pub_pem
        )
        # 创世条目:由 ROOT_SK 签(K1 准入)
        e1 = build_entry(c, 1, deployment_id, gen_dir, pub_pem, c["root_sk"], None, None)
        # 生效前校验已落盘布局;校验失败 → trust-log 仍为空,残留由下次 init 回收
        verify_layout(c, roots)
        append_entry(c, e1)
        print(
            f"[init] epoch=1 generation={GENERATIONS_DIR}/1 "
            f"root_pk_fingerprint={fp} root_pk_key_id={fp[:K_ID_PREFIX]} "
            f"append_ok={len(json.dumps(e1, sort_keys=True).encode())}"
        )
    finally:
        release_lock(lock)
