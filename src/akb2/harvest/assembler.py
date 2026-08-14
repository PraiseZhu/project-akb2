"""harvest 组装落盘主链路(sc-asm-cli / sc-asm-atomic / sc-asm-sourceurl)。

落盘协议(按实测源码定死,主路线唯一):
  ① 在受信 raw_fd 的同一挂载点下(raw_dir 直接父目录)创建一次性隔离私有
     目录(0700);② 以该目录为 output_dir 调 ``archive_raw`` 恰一次(它是
     writer 不是 renderer,产物在其返回时已落在隔离目录磁盘上);③ 读回产物
     以 (payload, markdown) 为扫描对象调 ``scan_and_redact``,就地重写回隔离
     目录内同一文件;④ 终检;⑤ 追加 ``harvest_run_id`` 一行;⑥ 由 ``_place``
     用 ``renameatx_np(..., RENAME_EXCL)``(Linux 为 ``renameat2(..., RENAME_NOREPLACE)``)
     跨目录原子改名落位到 raw_dir;⑦ 隔离目录无论成败都必须清理并断言已删。

安全性由「隔离目录不在 runner / frontmatter_repair / synthesis_assembler
三个具名消费者的非递归顶层枚举范围内、落位是最后一步」保证(0700 只按
UID/GID 隔离,不声称进程级保密 —— 威胁模型见 sc-asm-atomic 规格)。

信任边界:raw_dir / stage / locks_dir / batch 四根写权限与从稳定受信锚点到
四根的全部可变父命名空间写权限属受信主体,不在对抗范围内;本模块防的是
非受信主体的路径穿越 / 换链 / 身份伪装 —— 经 ``anchors.load_verified()``
每次全量重走受信链 + 逐级 no-follow 锚定 + ``RENAME_EXCL`` 目标侧拒绝。

公开面:``assemble`` / ``validate_harvest_run_id`` / ``AssemblyRejectedError``
/ ``RedactionRejectedError`` / ``PlaceExistsError`` / ``_place``。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import akb2.harvest.anchors as anchors
import akb2.harvest.locks as locks
import akb2.harvest.schema as schema
from akb2.harvest.anchors import AnchorDriftError, AnchorHandle
from akb2.harvest.redaction import (
    RedactionPolicy,
    RedactionRejectedError,
    _scan_markdown,
    scan_and_redact,
)

# raw_archiver 用 src.akb2.* 前缀 import(全仓惯例,测试/CLI 均已在 sys.path)
from src.akb2.quality.intake.raw_archiver import archive_raw

__all__ = [
    "assemble",
    "validate_harvest_run_id",
    "AssemblyRejectedError",
    "RedactionRejectedError",
    "PlaceExistsError",
    "PreflightError",
    "RedactionPolicy",
    "_place",
]

log = logging.getLogger("akb2.harvest.assembler")

# ---------------------------------------------------------------------------
# run_id 校验(共享纯函数:assemble 第一行与 CLI 解析共用同一对象)
# ---------------------------------------------------------------------------

# 小写 canonical UUID4(与 sc-asm-sourceurl 对 session_id 的同一谓词)
UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def validate_harvest_run_id(run_id: str) -> bool:
    """run_id 严格解析:恰为小写 canonical UUID4。

    空串 / 绝对路径 / 含 `/` / 含 `.` / 含分隔符 / 非 UUID4 一律 False。
    run_id 参与隔离目录名派生,必须在任何路径拼接之前过此闸。
    """
    if not isinstance(run_id, str):
        return False
    return UUID4_RE.match(run_id) is not None


# ---------------------------------------------------------------------------
# 异常(退出码映射:2 = 内容/锚点/预检拒绝;3 = redaction 命中;4 = 目标已存在)
# ---------------------------------------------------------------------------


class AssemblyRejectedError(RuntimeError):
    """内容准入失败(schema / 车道 / archive_raw 失败 / 隔离目录创建失败)。

    调用方映射 exit 2。零落位。
    """


class PlaceExistsError(RuntimeError):
    """落位目标已存在:内核 RENAME_EXCL/NOREPLACE 拒 EEXIST。调用方映射 exit 4。"""


class PreflightError(RuntimeError):
    """落盘能力预检失败(平台不支持 / 残留无法安全清理)。调用方映射 exit 2。"""


# ---------------------------------------------------------------------------
# 原子改名原语(renameatx_np / renameat2 的唯一封装;预检与 _place 共用)
# ---------------------------------------------------------------------------

RENAME_EXCL = 0x00000004        # macOS renameatx_np
RENAME_NOREPLACE = 1            # Linux renameat2

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_renameatx_np = getattr(_libc, "renameatx_np", None)
_renameat2 = getattr(_libc, "renameat2", None)
if _renameatx_np is not None:
    _renameatx_np.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    _renameatx_np.restype = ctypes.c_int
if _renameat2 is not None:
    _renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    _renameat2.restype = ctypes.c_int


def _rename_noreplace(src_fd: int, src_name: str,
                      dst_fd: int, dst_name: str) -> None:
    """跨目录原子改名,目标侧排他(RENAME_EXCL / RENAME_NOREPLACE)。

    - 目标已存在 → OSError(EEXIST),不覆写、不保留半成品;
    - 目标名预置为 symlink → 同样 EEXIST(内核行为);
    - 跨设备 → OSError(EXDEV),不静默降级;
    - 平台无原语 → PreflightError(能力探测失败即 exit 2)。
    """
    if sys.platform == "darwin" and _renameatx_np is not None:
        rc = _renameatx_np(src_fd, src_name.encode(), dst_fd, dst_name.encode(),
                           RENAME_EXCL)
    elif _renameat2 is not None:
        rc = _renameat2(src_fd, src_name.encode(), dst_fd, dst_name.encode(),
                        RENAME_NOREPLACE)
    else:
        raise PreflightError(
            "平台不支持 renameatx_np/renameat2,原子落位能力不可用")
    if rc != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))


# ---------------------------------------------------------------------------
# 部署元数据(只读提取,不创建不更新基线)
# ---------------------------------------------------------------------------


def _read_deployment_id() -> str:
    """从受信部署根只读提取当前代 deployment_id(anchors 已验证过一致性)。"""
    root = os.environ.get("AKB2_HARVEST_DEPLOY_ROOT")
    if not root:
        raise PreflightError("部署根未配置(AKB2_HARVEST_DEPLOY_ROOT)")
    deploy = Path(root)
    try:
        raw = (deploy / "trust-log" / "log").read_text(encoding="utf-8")
    except OSError as e:
        raise PreflightError(f"trust-log 不可读: {e}") from None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            tip = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(tip, dict):
            continue
        dep = tip.get("deployment_id")
        if dep:
            return str(dep)
    raise PreflightError("trust-log 无完整条目,deployment_id 不可确定")


# ---------------------------------------------------------------------------
# (0-pre) 落盘能力预检 —— 在已验证的真实 raw_fd 上执行
# ---------------------------------------------------------------------------

_PREFLIGHT_MAGIC = b"AKB2HARVEST-PREFLIGHT-1"
# 留足冗余:MAGIC(23)+两个分隔符+ts(~10)+deployment_id;旧值 64 字节在
# deployment_id 超过约 38 字节(如 UUID4、主机名+日期)时会静默截断,
# 导致崩溃恢复读回的头与刚写入的不再逐字相等,把自己的合法残留误判为
# 「无法安全清理」而永久 fail-closed(需人工介入)。
_PREFLIGHT_HEADER_LEN = 256
# 探针命名格式(与业务命名不可能混淆,且不匹配 runner 的 *.md 拾取正则)
_PREFLIGHT_RE = re.compile(
    r"^\.harvest-preflight--([^/]+)--([^/]+)--(\d+)\.tmp$")
_PREFLIGHT_SENTINEL_BYTES = b"SENTINEL-KNOWN-BYTES-0123456789"


def _preflight_header(deployment_id: str) -> bytes:
    """自认证头:固定魔数 + deployment_id + 写入时刻,填充到 _PREFLIGHT_HEADER_LEN 字节。

    deployment_id 过长导致 body 超出缓冲区时显式拒绝,不静默截断——截断会
    让头内容与实际 deployment_id 不再逐字相等,自认证校验永久失效。
    """
    body = (_PREFLIGHT_MAGIC + b"|" + deployment_id.encode("utf-8", "replace")
            + b"|" + str(int(time.time())).encode())
    if len(body) > _PREFLIGHT_HEADER_LEN:
        raise PreflightError(
            f"deployment_id 过长,自认证头缓冲区不足({len(body)} > "
            f"{_PREFLIGHT_HEADER_LEN} 字节): {deployment_id!r}")
    return body.ljust(_PREFLIGHT_HEADER_LEN, b" ")


def _header_matches(data: bytes, deployment_id: str) -> bool:
    """自认证头校验:魔数 + deployment_id 逐字相等 + 时刻可解析。

    头格式为 ``MAGIC + "|" + deployment_id + "|" + ts``(见 _preflight_header),
    切片后首个元素是空串(前导 ``|`` 被包含),故字段从 parts[1]/parts[2] 取。
    """
    head = data[:_PREFLIGHT_HEADER_LEN]
    if not head.startswith(_PREFLIGHT_MAGIC):
        return False
    rest = head[len(_PREFLIGHT_MAGIC):]
    parts = rest.split(b"|")
    if len(parts) < 3:
        return False
    dep, ts = parts[1].strip(), parts[2].strip()
    return (dep.decode("utf-8", "replace") == deployment_id and ts.isdigit())


def _read_head(dir_fd: int, name: str, n: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        return os.read(fd, n)
    finally:
        os.close(fd)


def _recover_preflight_leftovers(raw_fd: int, deployment_id: str) -> None:
    """重启恢复:清理「命名格式 + 自认证头」双匹配的残留探针。

    - 格式匹配但头不符 → 不删, fail-closed 具名报告(宁可留着让人看);
    - 头匹配但格式不符 → 不删, fail-closed 具名报告;
    - 业务命名文件(.md 等)不在扫描集内,绝不当探针清理。
    """
    removed: list[str] = []
    for name in os.listdir(raw_fd):
        if not (name.startswith(".") and name.endswith(".tmp")):
            continue  # 业务文件(含 .md)不在探针扫描集
        m = _PREFLIGHT_RE.match(name)
        matches_format = m is not None and m.group(1) == deployment_id
        try:
            data = _read_head(raw_fd, name, _PREFLIGHT_HEADER_LEN)
        except OSError:
            continue
        matches_header = _header_matches(data, deployment_id)
        if matches_format and matches_header:
            os.unlink(name, dir_fd=raw_fd)
            removed.append(name)
        elif matches_format or matches_header:
            kind = ("格式匹配但自认证头不符" if matches_format
                    else "自认证头匹配但命名不符格式")
            raise PreflightError(
                f"预检残留无法安全清理,需人工裁定(不删除): {name!r} [{kind}]")
    if removed:
        log.warning("预检恢复:清理 %d 个崩溃残留探针: %s",
                    len(removed), ", ".join(removed))
        os.fsync(raw_fd)


def _assert_absent(dir_fd: int, names: list[str]) -> None:
    """fstatat(no-follow)断言全部探针名 ENOENT;残留即 fail-closed 具名。"""
    for name in names:
        try:
            os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise PreflightError(f"预检清理未生效,残留探针(未删除): {name!r}")


def _preflight_capability(raw_fd: int, deployment_id: str) -> None:
    """能力探测:EEXIST 语义必须被真实执行到(与 _place 同一 rename 原语)。

    序列:创建源探针 → 预置 sentinel(已知字节)→ rename 到已占用目标必须
    EEXIST(源仍在、sentinel 字节未变)→ 删 sentinel → rename 到空目标必须
    成功 → 按状态机清理(rename 成功只删目标;失败只删源)→ fstatat 断言
    双 ENOENT → fsync 父目录。任一失败即 PreflightError(exit 2)。
    """
    tag = f"{os.getpid()}x{int(time.time() * 1000) % 10**9}"
    src = f".harvest-preflight--{deployment_id}--{tag}--1.tmp"
    sentinel = f".harvest-preflight--{deployment_id}--{tag}--2.tmp"
    try:
        fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=raw_fd)
    except OSError as e:
        raise PreflightError(f"预检探针创建失败: {e}") from None
    try:
        header = _preflight_header(deployment_id)
        n = os.write(fd, header)
        if n != len(header):
            raise PreflightError(
                f"预检探针短写({n} < {len(header)} 字节)")
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        sfd = os.open(sentinel, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                      | os.O_NOFOLLOW, 0o600, dir_fd=raw_fd)
    except OSError as e:
        os.unlink(src, dir_fd=raw_fd)
        raise PreflightError(f"预检 sentinel 创建失败: {e}") from None
    try:
        os.write(sfd, _PREFLIGHT_SENTINEL_BYTES)
        os.fsync(sfd)
    finally:
        os.close(sfd)

    rename_ok = False
    try:
        # 1. 改名到已占用目标:必须 EEXIST,且 sentinel 字节逐字未变、源仍在
        try:
            _rename_noreplace(raw_fd, src, raw_fd, sentinel)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise PreflightError(
                    f"预检 EEXIST 语义验证失败(errno={e.errno}): {e}") from None
        else:
            raise PreflightError(
                "预检失败:改名未拒绝已存在目标(EEXIST 语义缺失)")
        if _read_head(raw_fd, sentinel, len(_PREFLIGHT_SENTINEL_BYTES)) \
                != _PREFLIGHT_SENTINEL_BYTES:
            raise PreflightError("预检失败:sentinel 字节被改名覆写")
        try:
            os.stat(src, dir_fd=raw_fd, follow_symlinks=False)
        except FileNotFoundError:
            raise PreflightError("预检失败:EEXIST 后源探针意外消失") from None
        # 2. 删 sentinel,对空目标重做:必须成功
        os.unlink(sentinel, dir_fd=raw_fd)
        try:
            _rename_noreplace(raw_fd, src, raw_fd, sentinel)
        except OSError as e:
            raise PreflightError(f"预检 rename 空目标失败: {e}") from None
        rename_ok = True
    finally:
        # 清理状态机:rename 成功 → 只删目标;未成功 → 只删源
        try:
            os.unlink(sentinel if rename_ok else src, dir_fd=raw_fd)
        except FileNotFoundError:
            pass
    _assert_absent(raw_fd, [src, sentinel])
    os.fsync(raw_fd)


def _preflight(raw_fd: int, deployment_id: str) -> None:
    """完整落盘协议预检(恢复 + 能力探测)。任一失败抛 PreflightError。"""
    _recover_preflight_leftovers(raw_fd, deployment_id)
    _preflight_capability(raw_fd, deployment_id)


# ---------------------------------------------------------------------------
# 落位原语
# ---------------------------------------------------------------------------


def _verify_leaf_in_place(h: AnchorHandle) -> None:
    """以「受信父目录 fd + 叶名 + no-follow stat」复核根仍在原位。

    绝不对持有中的根 fd 做 fstat(那永远观察不到命名路径被换掉)。
    """
    if h.parent_fd < 0:
        return
    try:
        st = os.stat(h.leaf_name, dir_fd=h.parent_fd, follow_symlinks=False)
    except OSError as e:
        raise AnchorDriftError(
            f"raw_dir 不可解析(leaf={h.leaf_name}): {e.strerror}") from None
    if (st.st_dev, st.st_ino) != (h.expected_identity.get("st_dev"),
                                  h.expected_identity.get("st_ino")):
        raise AnchorDriftError(
            f"raw_dir 命名漂移:leaf={h.leaf_name} 身份 (dev={st.st_dev}, "
            f"ino={st.st_ino}) 与基线不符")


def _place(iso_fd: int, tmp_basename: str, raw_fd: int, filename: str,
           reservation: locks.Reservation) -> None:
    """跨目录原子改名落位(唯一生产落盘路径,写盘所有权归 assembler)。

    入口第一行校验凭证:未 released + 锁文件在场 + owner_token 逐字相等 +
    pid 三方一致,拿不出自己的凭证在签名层就调不动。随后
    ``renameatx_np(iso_fd, tmp_basename, raw_fd, filename, RENAME_EXCL)``
    (Linux 为 renameat2 RENAME_NOREPLACE) 原子改名:源在隔离私有目录、
    目标在经 anchor manifest 验证过的 raw_fd 之下,两者同挂载点(创建隔离
    目录时选址保证,跨设备由 EXDEV 硬失败)。目标已存在由内核拒 EEXIST →
    调用方 exit 4;不覆写、不保留半成品。失败时清理只发生在隔离目录侧
    (unlinkat 相对 iso_fd 删本次产物),raw_dir 侧零删除动作。
    """
    reservation.assert_valid(locks.DESTNAME, filename)
    try:
        _rename_noreplace(iso_fd, tmp_basename, raw_fd, filename)
    except OSError as e:
        if e.errno == errno.EEXIST:
            raise PlaceExistsError(
                f"落位目标已存在(RENAME_EXCL 拒 EEXIST): {filename!r}") from None
        raise


# ---------------------------------------------------------------------------
# frontmatter 追加与正文渲染(确定性,无随机性)
# ---------------------------------------------------------------------------

_FM_BLOCK_RE = re.compile(r"^(---\n.*?\n)---\n", re.DOTALL)

_FETCHED_VIA = "cindy-harvest"


def _append_harvest_run_id(text: str, run_id: str) -> str:
    """在 frontmatter 9 字段之后追加 harvest_run_id 一行(仅此一键)。"""
    m = _FM_BLOCK_RE.match(text)
    if not m:
        raise AssemblyRejectedError("archive_raw 产物缺少 frontmatter 块")
    return m.group(1) + f"harvest_run_id: {run_id}\n---\n" + text[m.end():]


def _render_body(payload: dict) -> str:
    """从 atoms + how_steps 渲染正文 markdown(段落数 ≥5,锚点 AEHMZ 可全中)。"""
    parts: list[str] = []
    for atom in payload.get("atoms", []):
        parts.append(f"{atom.get('id', '?')} — {atom.get('kind', '?')}\n\n"
                     f"{atom.get('text', '')}")
    for step in payload.get("how_steps", []):
        parts.append(f"step {step.get('anchor', '?')}\n\n"
                     f"cmd: {step.get('cmd', '')}\n\n"
                     f"expect: {step.get('expect', '')}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 隔离目录读写(相对 fd,不走字符串路径)
# ---------------------------------------------------------------------------


def _read_text(dir_fd: int, name: str) -> str:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        chunks = []
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(fd)


def _write_text(dir_fd: int, name: str, text: str) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        n = os.write(fd, text.encode("utf-8"))
        if n != len(text.encode("utf-8")):
            raise OSError(f"隔离目录重写短写({n})")
        os.fsync(fd)
    finally:
        os.close(fd)


def _cleanup_iso(iso_fd: int, iso_name: str, parent_fd: int, filename: str,
                 parent_exc: BaseException | None) -> None:
    """隔离目录清理:删本次产物(unlinkat 相对 iso_fd)→ 删目录 → fsync 父。

    只删「本次自建的具名临时文件」,绝不递归、绝不重新解析字符串路径、
    绝不碰 raw_dir 侧任何文件。清理失败:正常路径抛(隔离目录泄漏必须
    可见);已有异常路径不遮蔽主异常,stderr 具名报告。
    """
    try:
        try:
            os.unlink(filename, dir_fd=iso_fd)
        except FileNotFoundError:
            pass  # 已落位(rename 移走)或本未产生
        except OSError as e:
            if parent_exc is None:
                raise AssemblyRejectedError(
                    f"隔离目录清理失败(需人工检查 {iso_name!r}): {e}") from None
            print(f"[harvest] 隔离目录清理失败(已有异常 {parent_exc.__class__.__name__}): "
                  f"{iso_name!r}: {e}", file=sys.stderr)
    finally:
        try:
            os.close(iso_fd)
        except OSError:
            pass
    try:
        try:
            os.rmdir(iso_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.fsync(parent_fd)
    except OSError as e:
        if parent_exc is None:
            raise AssemblyRejectedError(
                f"隔离目录清理失败(需人工检查 {iso_name!r}): {e}") from None
        print(f"[harvest] 隔离目录清理失败(已有异常 {parent_exc.__class__.__name__}): "
              f"{iso_name!r}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def assemble(
    payload: dict,
    *,
    harvest_run_id: str,
    policy: RedactionPolicy = RedactionPolicy.Reject,
) -> str:
    """组装 harvest JSON → 隔离目录内 archive_raw → 脱敏 → 落位 → 返回文件名。

    Args:
        payload: harvest JSON v1(schema + 车道校验先行,fail-closed)。
        harvest_run_id: 必填具名参数,只透传不生成;严格 UUID4。
        policy: RedactionPolicy.Reject(默认)或 Redact;只选策略、不能关闭扫描。

    Returns:
        最终落位文件名(如 ``<slug>--<article_id>--raw--<date>.md``)。

    Raises:
        AssemblyRejectedError: schema/车道/隔离目录/终检/漂移复核失败(exit 2)。
        RedactionRejectedError: policy=Reject 且扫描命中(exit 3)。
        PlaceExistsError: 落位目标已存在(exit 4)。
        AnchorDriftError: 锚点身份漂移(exit 2)。
        PreflightError: 落盘能力预检失败(exit 2)。
    """
    # 0. run_id 严格解析:任何路径拼接之前的第一行(CLI 复用同一函数)
    if not validate_harvest_run_id(harvest_run_id):
        raise AssemblyRejectedError(
            f"harvest_run_id 非法(须为小写 canonical UUID4): {harvest_run_id!r}")

    # 1. schema + 车道校验(内容准入)
    violations = schema.validate(payload)
    if violations:
        raise AssemblyRejectedError("schema 校验失败: " + "; ".join(violations[:5]))
    ok, _code, msg = schema.run_lane_check(payload)
    if not ok:
        raise AssemblyRejectedError(msg)
    if not isinstance(policy, RedactionPolicy):
        raise TypeError(f"policy 必须是 RedactionPolicy,实际 {policy!r}")

    # 2. 锚点验证(每次调用全量重走受信链)
    handles = anchors.load_verified()
    raw_h = handles["raw_dir"]
    _verify_leaf_in_place(raw_h)

    # 3. (0-pre) 落盘能力预检(在已验证的真实 raw_fd 上;失败即 exit 2)
    deployment_id = _read_deployment_id()
    _preflight(raw_h.root_fd, deployment_id)

    # 4. 一次性隔离私有目录(0700;选址在 raw_dir 直接父目录 = 同挂载点,
    #    不在任何消费者枚举范围内)。
    #    绝对路径与 dir_fd 必须指向同一目录:隔离目录经 raw_h.parent_fd
    #    创建/打开/清理,archive_raw 的 output_dir 却是路径字符串——两者
    #    各自独立构造,raw_dir 嵌套(trust_root/x/y/raw)时必然错位:归档
    #    写到父目录之外的路径、fd 侧读回失败、清理删错位置,父目录外残留
    #    未脱敏 markdown。故 output_dir 一律取 dirname(raw_dir) 拼隔离目录
    #    名,并在创建前用 (st_dev, st_ino) 将该路径与 parent_fd 绑定为同
    #    一目录(身份不符 → 零创建 fail-closed),创建后仍以 fd 为准读写。
    iso_name = f".harvest-iso--{harvest_run_id}.tmp"
    # raw_dir 真实路径:anchors 已在受信链第 1 步验证 roots.json(owner/mode
    # 与必需字段),load_verified 全量走完,此处读取与 anchors 同源的配置。
    roots_cfg = Path(os.environ.get("AKB2_HARVEST_DEPLOY_ROOT", "")) / "roots.json"
    try:
        _roots = json.loads(roots_cfg.read_text())
        parent_path = Path(_roots["raw_dir"]).parent
    except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError) as e:
        raise AnchorDriftError(
            f"raw_dir 不可解析(anchors 已通过,此处仅取路径): {e}") from None
    # 路径 ↔ fd 同源绑定:路径解析出的身份必须恰为 parent_fd 持有的目录。
    #    纯 fd→path(macOS 无 /proc,readlink /dev/fd 报 EINVAL,F_GETPATH
    #    经 fcntl/ioctl/ctypes 均不可用)不可移植,故反向以身份校验闭合。
    try:
        _pst = os.stat(parent_path)
    except OSError as e:
        raise AnchorDriftError(
            f"隔离目录父路径不可解析(与 parent_fd 绑定失败): {parent_path} "
            f"({e.strerror})") from None
    _fst = os.fstat(raw_h.parent_fd)
    if (_pst.st_dev, _pst.st_ino) != (_fst.st_dev, _fst.st_ino):
        raise AnchorDriftError(
            f"隔离目录父路径与 parent_fd 身份不符(嵌套 raw_dir 或根被换): "
            f"{parent_path} (dev={_pst.st_dev}, ino={_pst.st_ino}) != "
            f"fd (dev={_fst.st_dev}, ino={_fst.st_ino})")
    iso_path = parent_path / iso_name
    iso_fd = -1
    try:
        os.mkdir(iso_name, 0o700, dir_fd=raw_h.parent_fd)
        os.fsync(raw_h.parent_fd)
        iso_fd = os.open(iso_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=raw_h.parent_fd)
    except OSError as e:
        if iso_fd >= 0:
            try:
                os.close(iso_fd)
            except OSError:
                pass
        try:
            os.rmdir(iso_name, dir_fd=raw_h.parent_fd)
        except OSError:
            pass
        try:
            os.fsync(raw_h.parent_fd)
        except OSError:
            pass
        raise AssemblyRejectedError(
            f"隔离私有目录创建失败: {e}") from None

    filename = ""
    try:
        # 5. 以隔离目录为 output_dir 调 archive_raw 恰一次(writer 非 renderer)。
        #    output_dir = dirname(raw_dir) 拼隔离目录名,创建前已与 parent_fd
        #    做过 (dev,ino) 同源绑定(见第 4 步);归档产物因此落在 fd 侧读写
        #    与清理的同一目录内。
        sm = payload.get("source_meta") or {}
        slug = payload["session_id"]
        url = "cindy://session/" + payload["session_id"]  # sc-asm-sourceurl 定案
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        result = archive_raw(
            slug=slug,
            url=url,
            title=payload["title"],
            author=str(sm.get("author") or "unknown"),
            content=_render_body(payload),
            published_at=str(sm.get("published_at") or "unknown"),
            publisher=str(sm.get("publisher") or "unknown"),
            fetched_via=_FETCHED_VIA,
            output_dir=iso_path,
            date_str=date_str,
        )
        filename = Path(result.output_path).name if result.success else ""
        if not result.success:
            raise AssemblyRejectedError(
                result.error or "archive_raw 失败(锚点/字段断言)")

        # 6. 读回产物 → (payload, markdown) 为对象强制扫描/脱敏
        md = _read_text(iso_fd, filename)
        redacted, _replacements = scan_and_redact(payload, md, policy=policy)
        if redacted != md:
            _write_text(iso_fd, filename, redacted)  # 就地重写(仍隔离目录内)

        # 7. 追加 harvest_run_id 一行(9 字段之后;所有权凭证)
        current = _read_text(iso_fd, filename)
        final = _append_harvest_run_id(current, harvest_run_id)
        _write_text(iso_fd, filename, final)

        # 8. 终检:Redact 支再扫一遍落盘文本,必须零命中
        if policy is RedactionPolicy.Redact:
            check = _scan_markdown(_read_text(iso_fd, filename))
            if check:
                raise AssemblyRejectedError(
                    "redaction 终检未清零(落盘文本仍含凭证形态)")

        # 9. 写完边界 + 落位前:重新 load_verified 复核完整链(每边界现取)
        handles2 = anchors.load_verified()
        raw_h2 = handles2["raw_dir"]
        _verify_leaf_in_place(raw_h2)

        try:
            # 10. 落位:DESTNAME 预约 + _place 原子改名(唯一落盘路径)
            with locks.reserve(locks.DESTNAME, filename) as reservation:
                _place(iso_fd, filename, raw_h2.root_fd, filename, reservation)
            os.fsync(raw_h2.root_fd)
            return filename
        finally:
            # 关闭第二组 handle(落位完毕或失败,root_fd 不再需要)
            for h in handles2.values():
                h.close()
    finally:
        # 11. 隔离目录无论成败都必须清理(断言已删由调用方/测试执行)
        parent_exc = sys.exc_info()[1]
        _cleanup_iso(iso_fd, iso_name, raw_h.parent_fd, filename, parent_exc)
        # 关闭第一组 handle(隔离目录已清理,parent_fd 不再需要)
        for h in handles.values():
            h.close()
