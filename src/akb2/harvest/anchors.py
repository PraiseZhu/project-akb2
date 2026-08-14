"""harvest 四根受信锚 —— 运行期只读身份底座(sc-asm-anchors)。

四个受信根(raw_dir / stage / locks_dir / batch)的身份唯一来源,跨模块消费:
`load_verified()` 返回各根 typed ``AnchorHandle{root_fd, parent_fd, leaf_name,
expected_identity}``。本模块是 Wave 2 前置;locks / atomic / ledger 全部经它取 fd。

**只读契约(运行期禁止自举)**:基线只能由受信部署动作(bootstrap 脚本 /
recover 脚本)写入;``load_verified()`` 在任何情况下不得创建或更新 manifest,
任何失败路径零写盘、零现场重新登记。

**信任模型**(与 scripts/ 下受信部署入口(init/migrate 脚本)逐字对齐):
- 部署常量经 env 注入(AKB2_HARVEST_DEPLOY_ROOT / DEPLOY_UID /
  RUNTIME_READER_GID / ROOT_PK_FINGERPRINT);argv 无路径参数。
- 布局相对 deploy_root:roots.json、root-key/root.pub、trust-log/log、
  generations/<epoch>/{manifest,manifest.sig,public_key}。
- trust-log 为 append-only JSON lines;每条 E_i 由 K_{i-1} 签(创世由 ROOT_SK),
  同一条目内准入 K_i、废止 K_{i-1};坏尾丢弃,末条完整条目为准。
- 防回放:manifest 的 deployment_id 与 epoch 必须与 trust-log 末条完整条目
  逐字相等;低于末条 epoch 的旧代际一律拒绝。
- 防伪造:manifest 签名由部署密钥签发;ROOT_PK 指纹由部署常量钉住
  (第 0 步锚定,指纹不符不进证书链验证)。
- effective-at-position:验 E_i 用 E_{i-1} 准入的公钥,与废止声明无关
  (K_{i-1} 被 E_i 废止不影响它签署 E_i);「谁能签发新条目」只看末条准入
  的当代密钥,两者是不同问题。

读取次序(唯一入口,每次调用全量重走,不缓存任何一级验证结果):
  0. 部署常量 + ROOT_PK 存在性 / owner/mode / 指纹(第 0 步锚定)
  1. roots.json(owner/mode + 必需字段)
  2. trust-log(owner/mode + 从头验链:前驱 hash、准入证书链、条目签名)
  3. generations/<epoch>/ 目录锚定(no-follow 逐级,不逃逸)
  4. manifest 的 epoch / deployment_id 与末条逐字比较(先于摘要)
  5. bundle 三件摘要与条目声明逐字比较;manifest 签名用当代公钥验证
  6. 四根 parent chain 逐级 O_NOFOLLOW 打开并与记录值 (st_dev, st_ino)
     逐字比对(普通目录替换与 symlink 替换同等对待)
任一步不符 → ``AnchorDriftError``,由调用方 exit 2。

公开面仅 ``load_verified`` / ``AnchorHandle`` / ``AnchorDriftError``。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = ["load_verified", "AnchorHandle", "AnchorDriftError"]

# 部署常量(env 注入,与 bootstrap 脚本逐字一致)
ENV_DEPLOY_ROOT = "AKB2_HARVEST_DEPLOY_ROOT"
ENV_DEPLOY_UID = "AKB2_HARVEST_DEPLOY_UID"
ENV_READER_GID = "AKB2_HARVEST_RUNTIME_READER_GID"
ENV_ROOT_FP = "AKB2_HARVEST_ROOT_PK_FINGERPRINT"

# 布局常量(相对 deploy_root,代码写死)
ROOTS_CONFIG_NAME = "roots.json"
ROOT_KEY_DIR = "root-key"
ROOT_PK_NAME = "root.pub"
TRUST_LOG_DIR = "trust-log"
TRUST_LOG_NAME = "log"
GENERATIONS_DIR = "generations"

SCHEMA_VERSION = 1
ROOT_NAMES = ("raw_dir", "stage", "locks_dir", "batch")


class AnchorDriftError(RuntimeError):
    """身份漂移:manifest / 信任链 / 目录身份任一不符,调用方应 exit 2。"""


@dataclass(frozen=True)
class AnchorHandle:
    """单个受信根的已验证身份。fd 仅在单次不可分割事务内有效,
    不得跨调用缓存或复用(中间级被换走后缓存 fd 无法自证仍挂原处)。

    生命周期:事务结束即弃用 —— 调用 ``close()``(或退出 ``with`` 块)释放
    持有的两个 fd;重复 close 幂等。one-shot CLI 不 close 也无实际影响,
    长驻循环必须显式 close 防 fd 耗尽。
    """

    root_fd: int
    parent_fd: int
    leaf_name: str
    expected_identity: dict

    def close(self) -> None:
        """释放本 handle 持有的 fd(事务结束即弃用)。重复调用安全(幂等)。"""
        for fd in (self.parent_fd, self.root_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> AnchorHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _entry_hash(entry: dict) -> str:
    return _sha256_bytes(json.dumps(entry, sort_keys=True).encode())


def _entry_body_bytes(entry: dict) -> bytes:
    """条目中参与签名的字节(除 signature 字段外全部)。"""
    body = {k: v for k, v in entry.items() if k != "signature"}
    return json.dumps(body, sort_keys=True).encode()


def _key_id_of_pem(pem: bytes) -> str:
    return _sha256_bytes(pem)[:16]


def _parse_trust_log(path: Path) -> list[dict]:
    """解析 trust-log 为完整条目列表;坏尾(JSON 失败或末尾无换行终止)丢弃。

    与 bootstrap 的 parse_trust_log 语义一致:正常追加总是 json + \\n;
    末尾不以 \\n 终止的段即使 JSON 可解析也不是完整条目。
    """
    if not path.exists():
        return []
    raw = path.read_bytes()
    segments = raw.split(b"\n")
    trailing = b"" if segments[-1] == b"" else segments[-1]
    if trailing:
        segments = segments[:-1]
    entries: list[dict] = []
    for raw_line in segments:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return entries


def _verify_signature(pub_pem: Path, data: bytes, sig_hex: str) -> bool:
    """用公钥文件验 RSA-SHA256 签名(DER hex)。全程零落盘到部署树:
    临时文件仅放系统临时目录,验证材料目录保持只读。"""
    if not sig_hex:
        return False
    with tempfile.TemporaryDirectory(prefix="akb2-anchor-verify-") as td:
        data_path = Path(td) / "data"
        sig_path = Path(td) / "sig"
        data_path.write_bytes(data)
        sig_path.write_bytes(bytes.fromhex(sig_hex))
        r = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(pub_pem),
             "-signature", str(sig_path), str(data_path)],
            capture_output=True, stdin=subprocess.DEVNULL,
        )
        return r.returncode == 0


# ---------------------------------------------------------------------------
# 部署常量与文件身份校验
# ---------------------------------------------------------------------------


def _load_constants() -> dict:
    root = os.environ.get(ENV_DEPLOY_ROOT)
    if not root:
        raise AnchorDriftError(f"部署根未配置:需经 {ENV_DEPLOY_ROOT} 提供")
    deploy_root = Path(root)
    req = {ENV_DEPLOY_UID: "deploy_uid", ENV_READER_GID: "reader_gid"}
    vals = {}
    for env, key in req.items():
        raw = os.environ.get(env)
        if not raw or not raw.isdigit():
            raise AnchorDriftError(f"部署常量缺失或非法:{env}={raw!r}")
        vals[key] = int(raw)
    return {
        "deploy_root": deploy_root,
        "roots_config": deploy_root / ROOTS_CONFIG_NAME,
        "root_pk": deploy_root / ROOT_KEY_DIR / ROOT_PK_NAME,
        "trust_log": deploy_root / TRUST_LOG_DIR / TRUST_LOG_NAME,
        "generations": deploy_root / GENERATIONS_DIR,
        "deploy_uid": vals["deploy_uid"],
        "reader_gid": vals["reader_gid"],
        "root_fp": os.environ.get(ENV_ROOT_FP) or "",
    }


def _check_owner_mode(path: Path, *, owner: int, mode: int,
                      group: int | None = None, what: str) -> None:
    """owner UID/GID 与 mode 校验;非预期 owner、或组/其他可写 → 拒。"""
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError as e:
        raise AnchorDriftError(f"{what} 不可解析: {path} ({e.strerror})") from None
    if st.st_uid != owner:
        raise AnchorDriftError(
            f"{what} owner UID 非预期(实际 {st.st_uid},期望 {owner}): {path}")
    if group is not None and st.st_gid != group:
        raise AnchorDriftError(
            f"{what} group 非预期(实际 {st.st_gid},期望 {group}): {path}")
    if st.st_mode & 0o777 != mode:
        raise AnchorDriftError(
            f"{what} mode 非预期(实际 {oct(st.st_mode & 0o777)},期望 {oct(mode)}): {path}")


def _load_roots_config(c: dict) -> dict:
    path = c["roots_config"]
    _check_owner_mode(path, owner=c["deploy_uid"], mode=0o400, what="根配置")
    try:
        roots = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise AnchorDriftError(f"根配置无法解析: {path}") from None
    required = {"raw_dir", "stage", "locks_dir", "batch", "trust_root"}
    missing = required - set(roots)
    if missing:
        raise AnchorDriftError(f"根配置缺少字段: {sorted(missing)}")
    return roots


def _read_trust_log() -> list[dict]:
    """读取并解析 trust-log(完整条目列表,坏尾丢弃)。"""
    c = _load_constants()
    return _parse_trust_log(c["trust_log"])


# ---------------------------------------------------------------------------
# 信任链验证(effective-at-position;准入与未废止是两件事)
# ---------------------------------------------------------------------------


def _verify_admission(c: dict, entries: list[dict], i: int, e: dict) -> None:
    """准入证书链:每代公钥必须可回溯至 root(证书由前驱私钥签发)。

    验证书的签名者 = 该条 predecessor 声明的密钥;前驱 key-id 与上一代 epoch
    对齐不一致即链断裂。证书缺失/自签/无法回溯 → 拒。
    """
    adm = e.get("admission")
    if not isinstance(adm, dict) or not adm.get("certificate"):
        raise AnchorDriftError(f"trust-log 第 {i + 1} 条准入证书缺失")
    expected_prev = "root" if i == 0 else f"K{entries[i - 1]['epoch']}"
    if adm.get("predecessor") != expected_prev:
        raise AnchorDriftError(
            f"trust-log 第 {i + 1} 条准入链断裂:前驱 key-id 不符"
            f"(声明 {adm.get('predecessor')!r},期望 {expected_prev!r})")
    if adm.get("key_number") != e.get("epoch"):
        raise AnchorDriftError(f"trust-log 第 {i + 1} 条准入 key_number 与条目 epoch 不符")
    if i == 0:
        cert_pub = c["root_pk"]
    else:
        cert_pub = c["generations"] / str(entries[i - 1]["epoch"]) / "public_key"
        _check_owner_mode(cert_pub, owner=c["deploy_uid"], group=c["reader_gid"],
                          mode=0o440, what="准入证书签名者公钥")
    # 证书签名数据 = 本代公钥内容 + 条目 epoch(admission 不保留 public_key_pem,
    # 从 bundle 的本代 public_key 文件取;篡改该文件会导致证书验证失败)
    cert_body = {
        "key_id": adm.get("key_id"),
        "public_key_pem": (c["generations"] / str(e["epoch"]) / "public_key").read_text(),
        "epoch": e.get("epoch"),
        "predecessor": adm.get("predecessor"),
    }
    if not _verify_signature(cert_pub, json.dumps(cert_body, sort_keys=True).encode(),
                             adm["certificate"]):
        raise AnchorDriftError(
            f"trust-log 第 {i + 1} 条准入证书无法回溯至 root(由未准入密钥签发)")


def _verify_entry_signature(c: dict, entries: list[dict], i: int, e: dict) -> None:
    """条目签名验证:逐条前推 —— E_1 由固定路径 ROOT_PK 验,其后由前一条目
    准入的公钥验(与废止声明无关)。签名者不是当代密钥时点名废止。"""
    sig = e.get("signature", "")
    if i == 0:
        if not _verify_signature(c["root_pk"], _entry_body_bytes(e), sig):
            raise AnchorDriftError(
                "trust-log 创世条目签名无法由固定路径 ROOT_PK 验证(root 锚定失败)")
        return
    prev_epoch = entries[i - 1]["epoch"]
    pub = c["generations"] / str(prev_epoch) / "public_key"
    if _verify_signature(pub, _entry_body_bytes(e), sig):
        return
    # 签名者分类:历史代公钥能验过 → 被废止/非当代;否则验签失败
    for hist_epoch in range(1, prev_epoch):
        hp = c["generations"] / str(hist_epoch) / "public_key"
        if hp.exists() and _verify_signature(hp, _entry_body_bytes(e), sig):
            raise AnchorDriftError(
                f"trust-log 第 {i + 1} 条签名者不是当代密钥:已被链上后续条目废止")
    if _verify_signature(c["root_pk"], _entry_body_bytes(e), sig):
        raise AnchorDriftError(
            f"trust-log 第 {i + 1} 条签名者不是当代密钥:root 不签署代际条目")
    raise AnchorDriftError(f"trust-log 第 {i + 1} 条条目签名验证失败")


def _verify_chain(c: dict, entries: list[dict]) -> None:
    """从头验证条目链:每条前驱 hash + 准入证书链 + 条目签名。"""
    prev_hash = None
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or e.get("prev_entry_hash") != prev_hash:
            raise AnchorDriftError(f"trust-log 链断裂:第 {i + 1} 条前驱 hash 不符")
        _verify_admission(c, entries, i, e)
        _verify_entry_signature(c, entries, i, e)
        prev_hash = _entry_hash(e)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def load_verified() -> dict[str, AnchorHandle]:
    """验证四根身份并返回各根 AnchorHandle(唯一入口,每次调用全量重走)。

    只读、零自举:任何失败路径都不创建/更新文件。任一步不符 → AnchorDriftError。
    """
    c = _load_constants()

    # 0. ROOT_PK 锚定:存在性 + owner/mode + 指纹(不进证书链验证)
    if not c["root_pk"].exists():
        raise AnchorDriftError(f"ROOT_PK 缺失(root 锚定不可用): {c['root_pk']}")
    _check_owner_mode(c["root_pk"], owner=c["deploy_uid"], group=c["reader_gid"],
                      mode=0o440, what="ROOT_PK")
    if c["root_fp"]:
        actual = _sha256_file(c["root_pk"])
        if actual != c["root_fp"]:
            raise AnchorDriftError(
                f"ROOT_PK 指纹不符(第 0 步锚定):部署常量 {c['root_fp'][:16]}…,"
                f"实际 {actual[:16]}…")

    # 1. 根配置(owner/mode + 必需字段)
    roots = _load_roots_config(c)

    # 2. trust-log:owner/mode + 从头验链,末条完整条目为准
    _check_owner_mode(c["trust_log"], owner=c["deploy_uid"], group=c["reader_gid"],
                      mode=0o640, what="trust-log")
    entries = _read_trust_log()
    if not entries:
        raise AnchorDriftError("trust-log 缺失或为空(未初始化)")
    _verify_chain(c, entries)
    tip = entries[-1]

    # 3. generations/<epoch>/ 目录锚定(no-follow 逐级,不逃逸)
    gen_rel = str(tip.get("generation_dir") or "")
    if gen_rel != f"{GENERATIONS_DIR}/{tip['epoch']}":
        raise AnchorDriftError(f"generation 目录声明非法: {gen_rel!r}")
    cur = c["deploy_root"]
    for part in gen_rel.split("/"):
        cur = cur / part
        try:
            fd = os.open(cur, os.O_RDONLY | os.O_NOFOLLOW)
            os.close(fd)
        except OSError as e:
            raise AnchorDriftError(f"generation 目录不可解析: {cur} ({e.strerror})") from None

    # 4. bundle 三件 owner/mode + 摘要 + manifest 字段逐字比较
    gen_dir = c["deploy_root"] / gen_rel
    manifest_path = gen_dir / "manifest"
    sig_path = gen_dir / "manifest.sig"
    pub_path = gen_dir / "public_key"
    _check_owner_mode(manifest_path, owner=c["deploy_uid"], group=c["reader_gid"],
                      mode=0o440, what="manifest")
    _check_owner_mode(sig_path, owner=c["deploy_uid"], group=c["reader_gid"],
                      mode=0o440, what="manifest 签名")
    _check_owner_mode(pub_path, owner=c["deploy_uid"], group=c["reader_gid"],
                      mode=0o440, what="当代公钥")
    # manifest 内容:epoch / deployment_id 与末条逐字比较(先于摘要比对,
    # 保证「旧代际回放」的拒因是代际而非摘要)
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        raise AnchorDriftError(f"manifest 无法解析: {manifest_path}") from None
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise AnchorDriftError(
            f"manifest schema_version 非预期: {manifest.get('schema_version')!r}")
    if manifest.get("epoch") != tip["epoch"]:
        raise AnchorDriftError(
            f"manifest epoch 与 trust-log 末条声明不符({manifest.get('epoch')!r} "
            f"!= {tip['epoch']!r})")
    if manifest.get("deployment_id") != tip.get("deployment_id"):
        raise AnchorDriftError("manifest deployment_id 与 trust-log 末条声明不符")
    # bundle 三件摘要与条目声明逐字比较
    bundle = tip.get("bundle") or {}
    if _sha256_file(manifest_path) != bundle.get("manifest_sha256"):
        raise AnchorDriftError("manifest 摘要与 trust-log 声明不符")
    if _sha256_file(sig_path) != bundle.get("signature_sha256"):
        raise AnchorDriftError("manifest 签名摘要与 trust-log 声明不符")
    if _sha256_file(pub_path) != bundle.get("public_key_sha256"):
        raise AnchorDriftError("当代公钥摘要与 trust-log 声明不符")
    # manifest 签名:用当代公钥(经摘要锚定);签名文件为二进制 DER
    if not _verify_signature(pub_path, manifest_path.read_bytes(),
                             sig_path.read_bytes().hex()):
        raise AnchorDriftError("manifest 签名验证失败")

    # 5. 四根 parent chain:逐级 O_NOFOLLOW 打开并与记录值逐字比对(dir_fd 相对
    #    上一级重走,不重新解析路径字符串——中间级被替换必须由下一级的 dir_fd
    #    open 直接捕获,不能靠"最终身份对不上"间接兜底)
    mroots = manifest.get("roots")
    if not isinstance(mroots, dict):
        raise AnchorDriftError("manifest 缺少 roots 字段")
    trust_root = Path(roots["trust_root"])
    handles: dict[str, AnchorHandle] = {}
    try:
        for name in ROOT_NAMES:
            entry = mroots.get(name)
            if not isinstance(entry, dict) or not isinstance(entry.get("chain"), list) \
                    or not entry["chain"]:
                raise AnchorDriftError(f"manifest 缺少根 {name} 的 parent chain")
            chain = entry["chain"]
            try:
                trust_root_fd = os.open(
                    trust_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except OSError as e:
                raise AnchorDriftError(
                    f"根 {name} trust_root 不可解析: {trust_root} ({e.strerror})") from None
            fds: list[int] = [trust_root_fd]
            try:
                for level in chain:
                    if not isinstance(level, dict) or not level.get("name"):
                        raise AnchorDriftError(f"根 {name} 的 chain 层级非法")
                    try:
                        fd = os.open(level["name"], os.O_RDONLY | os.O_NOFOLLOW,
                                    dir_fd=fds[-1])
                    except OSError as e:
                        raise AnchorDriftError(
                            f"根 {name} 路径不可解析(leaf={level['name']}): "
                            f"{e.strerror}") from None
                    try:
                        st = os.fstat(fd)
                    except OSError:
                        os.close(fd)
                        raise AnchorDriftError(
                            f"根 {name} 路径不可解析: {level['name']}") from None
                    if (st.st_dev, st.st_ino) != (level.get("st_dev"), level.get("st_ino")):
                        os.close(fd)
                        raise AnchorDriftError(
                            f"根 {name} parent chain 身份不符: {level['name']}")
                    fds.append(fd)
            except BaseException:
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                raise
            # chain 非空 → fds 至少 [trust_root_fd, leaf_fd] 两项;只保留最后两级
            # (parent_fd/root_fd),中间级用完即关,避免每次 load_verified() 泄漏 fd
            root_fd = fds[-1]
            parent_fd = fds[-2]
            for fd in fds[:-2]:
                try:
                    os.close(fd)
                except OSError:
                    pass
            last = chain[-1]
            handles[name] = AnchorHandle(
                root_fd=root_fd,
                parent_fd=parent_fd,
                leaf_name=last["name"],
                expected_identity={"st_dev": last["st_dev"], "st_ino": last["st_ino"]},
            )
    except BaseException:
        # 某根失败前,更早的根可能已建好 handle(root_fd/parent_fd 已持有)——
        # 逐根异常处理只保证单根内部零泄漏,不保证跨根;这里补上外层兜底,
        # 把已建好的 handle 一并关闭,防止「前几根成功、后一根失败」时静默泄漏。
        for h in handles.values():
            h.close()
        raise
    return handles
