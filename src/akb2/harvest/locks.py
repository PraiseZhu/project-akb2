"""harvest 预约与互斥原语(sc-asm-locks) —— O_EXCL 预约 + 凭证能力 + 封存处置。

公开面:`reserve(namespace, key)`(上下文管理器,返回 ``Reservation`` 能力对象)、
``Reservation``(assert_valid / begin_drift_disposition / release_after_taint)、
``DriftEvidence`` 值对象、``dir_anchored_key(path) -> AnchoredTarget``、
四 namespace 常量(CONTENT / DESTNAME / BATCH / RUN_ID)。

信任模型(与 sc-asm-anchors 对齐):
- 锁目录的 fd 与期望身份一律经 ``anchors.load_verified()`` 取得;本模块不自行
  open 配置字符串路径、不创建或更新基线。``load_verified()`` 每次调用全量重走
  受信链,因此 **AnchorHandle 不得跨调用复用**——每次 ``reserve`` / ``assert_valid``
  / ``dir_anchored_key`` 之前都重新取用,中间级被替换由锚点链 (st_dev, st_ino)
  逐级比对发现并 fail-closed。
- namespace 子目录的 mkdir/open、锁文件的 O_EXCL 创建、读取、追加与 unlink
  全部相对已持有的 root fd;「根还在不在原位」由受信父目录 fd + leaf name 的
  no-follow stat 回答,不由根 fd 自问自答。

状态机:
- 正常持有:``__exit__``(含普通异常展开)删除锁文件并置 released。
- poisoned(不可逆,``begin_drift_disposition`` 第一步置位):此后任何退出路径
  一律保留锁文件、不置 released、不吞异常。
- sealed = poisoned 且 drift_pending 已落盘:唯一的释放通道是
  ``release_after_taint(evidence)`` —— 三方逐字比对(传入证据 hash == 锁事件
  记录 hash == taint marker 内容 hash,且 nonce 一致),任一不符拒绝释放;
  「marker 文件存在」本身永不构成释放依据。taint 未落盘即崩溃 → 锁永久保留,
  只能人工裁定(对应规格 (iv-b))。

陈旧回收:pid 不存在且 mtime 超 ``LOCK_STALE_SECONDS`` 的锁由 reserve 显式回收
并记日志,但只对 CONTENT / DESTNAME / RUN_ID 三个 namespace 开放;BATCH 整族
在 namespace 层面退出自动回收,任何残留 BATCH 锁一律只报「需人工裁定」;
带 drift_pending 标记的锁(任何 namespace)同样永不自动回收。

锁文件格式(append-only JSON lines,折叠读取):
- 首行: ``{v, ns, key, owner_token, pid, ts}``(O_EXCL 创建时一次写入)
- 事件行: ``{ev: "drift_pending", evidence_sha256, nonce, ts}``(仅
  ``begin_drift_disposition`` 以单次 O_APPEND os.write 追加,绝不 unlink /
  rename / truncate)
- 坏尾行(不以换行终止)读取时丢弃并计数;首行或中间事件行损坏 → 不可解析,
  fail-closed 报「需人工裁定」,绝不视作无锁。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import stat
import time

import akb2.harvest.anchors as anchors
from akb2.harvest.anchors import AnchorDriftError

__all__ = [
    "CONTENT", "DESTNAME", "BATCH", "RUN_ID",
    "LOCK_STALE_SECONDS",
    "reserve", "Reservation", "DriftEvidence", "AnchoredTarget",
    "dir_anchored_key",
    "ReservationHeld", "NeedsManualResolution", "InvalidReservation",
    "AnchorDriftError",
]

log = logging.getLogger("akb2.harvest.locks")

# 锁文件写入的唯一通道(模块别名):O_APPEND 单次写入协议的全部写操作经此
# 函数;测试可经此精确注入短写/失败,不误伤 load_verified 内部的 tempfile 写。
_write_os = os.write

# 四 namespace 常量(BATCH 族整族不参与陈旧自动回收)
CONTENT = "CONTENT"
DESTNAME = "DESTNAME"
BATCH = "BATCH"
RUN_ID = "RUN_ID"

# 陈旧锁阈值:pid 不存在且锁文件 mtime 距今超过该秒数才可回收
LOCK_STALE_SECONDS = 3600

# 自动回收开放集(BATCH 退出回收,drift_pending 标记锁亦退出)
_AUTO_RECLAIM_NS = frozenset({CONTENT, DESTNAME, RUN_ID})


class ReservationHeld(RuntimeError):
    """同 namespace 同 key 已被持有(不等待不重试)。"""


class NeedsManualResolution(RuntimeError):
    """锁状态无法自动裁决(BATCH 残留 / drift_pending 标记 / 内容不可解析)。"""


class InvalidReservation(RuntimeError):
    """凭证校验失败(伪造 / 已释放 / 身份链不符 / 证据不匹配)。"""


class DriftEvidence:
    """漂移处置证据值对象。

    含稳定 batch key(``dir_anchored_key`` 的 .key)、从受信根到 journal 的完整
    expected 身份链、本次观测到的 observed 身份链、一次性 nonce。规范序列化
    (``canonical()``)同时是 taint marker 的内容与证据 hash 的输入。
    """

    __slots__ = ("batch_key", "expected_chain", "observed_chain", "nonce")

    def __init__(self, batch_key: str, expected_chain=(), observed_chain=(),
                 nonce: str = ""):
        self.batch_key = batch_key
        self.expected_chain = tuple(tuple(x) for x in expected_chain)
        self.observed_chain = tuple(tuple(x) for x in observed_chain)
        self.nonce = nonce

    def __eq__(self, other):
        if not isinstance(other, DriftEvidence):
            return NotImplemented
        return (self.batch_key == other.batch_key
                and self.expected_chain == other.expected_chain
                and self.observed_chain == other.observed_chain
                and self.nonce == other.nonce)

    def __hash__(self):
        return hash((self.batch_key, self.expected_chain,
                     self.observed_chain, self.nonce))


class AnchoredTarget:
    """dir_anchored_key 的返回:稳定 key + 新开的父目录 fd + canonical basename。

    ``.dir_fd`` 是每次调用新开的资源,调用方用完必须 close,不参与任何相等性
    断言;别名/替换类断言一律只比 ``.key``。
    """

    __slots__ = ("key", "dir_fd", "basename")

    def __init__(self, key: str, dir_fd: int, basename: str):
        self.key = key
        self.dir_fd = dir_fd
        self.basename = basename


# ---------------------------------------------------------------------------
# 证据序列化
# ---------------------------------------------------------------------------


def canonical(e: DriftEvidence) -> str:
    """DriftEvidence 的规范序列化(taint marker 内容即此,evidence hash 的输入)。"""
    return json.dumps(
        {"batch_key": e.batch_key,
         "expected_chain": [list(x) for x in e.expected_chain],
         "observed_chain": [list(x) for x in e.observed_chain],
         "nonce": e.nonce},
        sort_keys=True)


def _evidence_sha256(e: DriftEvidence) -> str:
    return hashlib.sha256(canonical(e).encode()).hexdigest()


# ---------------------------------------------------------------------------
# 锚点/锁文件底层
# ---------------------------------------------------------------------------


def _verify_root_in_place(h: anchors.AnchorHandle) -> None:
    """以「受信父目录 fd + leaf name」复核根仍在原位(no-follow stat vs 基线)。

    任何被缓存的 fd 都只能证明「它自己没变」,证明不了「它仍挂在原来的位置
    上」——所以根在原位这一问必须由父目录 fd 回答,不能由根 fd 自问自答。
    """
    if h.parent_fd < 0:
        return  # 无父目录(trust_root 直接是根)——链验证已覆盖
    try:
        st = os.stat(h.leaf_name, dir_fd=h.parent_fd, follow_symlinks=False)
    except OSError as e:
        raise AnchorDriftError(
            f"locks 根不可解析(leaf={h.leaf_name}): {e.strerror}") from None
    if (st.st_dev, st.st_ino) != (h.expected_identity.get("st_dev"),
                                  h.expected_identity.get("st_ino")):
        raise AnchorDriftError(
            f"locks 根漂移:leaf={h.leaf_name} 身份 (dev={st.st_dev}, "
            f"ino={st.st_ino}) 与基线不符")


def _open_ns_dir(root_fd: int, namespace: str, create: bool = True) -> int:
    """相对 root fd 打开 namespace 目录(不存在则创建并 fsync 父目录)。

    namespace 必须为单级名(不含 /);O_NOFOLLOW 打开,拒绝 symlink 替换。
    """
    try:
        return os.open(namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                       dir_fd=root_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(namespace, 0o750, dir_fd=root_fd)
        except FileExistsError:
            pass
        os.fsync(root_fd)
        return os.open(namespace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                       dir_fd=root_fd)


def _parse_lock_bytes(data: bytes) -> tuple[dict, list[dict], int]:
    """折叠读取锁文件:返回 (meta, events, dropped_tail)。

    首行必须完整 JSON(否则不可解析,fail-closed);末尾不以换行终止的段为坏尾,
    丢弃并计数;中间事件行损坏 → 不可解析(append-only 协议下不可能发生,
    发生了就是被篡改,保守拒)。
    """
    if not data:
        raise NeedsManualResolution("锁文件为空,不可解析")
    segments = data.split(b"\n")
    dropped = 0
    if segments and segments[-1] == b"":
        segments = segments[:-1]
    else:
        dropped = 1  # 末尾不以换行终止的段 = 坏尾
    if not segments:
        raise NeedsManualResolution("锁文件为空,不可解析")
    try:
        meta = json.loads(segments[0])
    except (json.JSONDecodeError, IndexError):
        raise NeedsManualResolution("锁文件内容不可解析(首行损坏)") from None
    events: list[dict] = []
    for raw in segments[1:]:
        if not raw.strip():
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            if raw is segments[-1] and dropped:
                continue  # 坏尾已计数
            raise NeedsManualResolution("锁文件事件行损坏,不可解析") from None
    return meta, events, dropped


def _read_lock_state(lock_rel: str, ns_fd: int) -> tuple[dict, list[dict], int]:
    """读锁文件并折叠解析(相对 ns fd,O_NOFOLLOW)。"""
    fd = os.open(lock_rel, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=ns_fd)
    try:
        chunks = []
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
    finally:
        os.close(fd)
    return _parse_lock_bytes(b"".join(chunks))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不可信号,视为存活


def _write_initial(fd: int, ns_fd: int, meta: dict) -> None:
    """O_EXCL 创建成功后写首行并 fsync(文件 + namespace 父目录)。

    创建失败路径允许清理(与 drift_pending 追加的「绝不 unlink」条款区分)。
    """
    payload = json.dumps(meta, sort_keys=True).encode() + b"\n"
    try:
        n = _write_os(fd, payload)
        if n != len(payload):
            raise OSError(f"锁首行短写({n} < {len(payload)} 字节)")
        os.fsync(fd)
        os.fsync(ns_fd)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _recover_or_raise(lock_rel: str, ns_fd: int, namespace: str, key: str) -> int:
    """锁文件已存在:读状态 → 回收(仅开放集)或抛 ReservationHeld / NeedsManualResolution。"""
    try:
        meta, events, _dropped = _read_lock_state(lock_rel, ns_fd)
    except FileNotFoundError:
        # 竞态:锁刚消失 → 重试创建
        return _create_excl(lock_rel, ns_fd)
    if any(ev.get("ev") == "drift_pending" for ev in events):
        raise NeedsManualResolution(
            f"锁 {namespace}/{key} 带 drift_pending 标记,永不自动回收,需人工裁定")
    if namespace not in _AUTO_RECLAIM_NS:
        raise NeedsManualResolution(
            f"{namespace} 锁 {key} 残留(pid={meta.get('pid')}),"
            f"该 namespace 不做自动回收,需人工裁定")
    if _pid_alive(meta.get("pid", -1)):
        raise ReservationHeld(
            f"锁 {namespace}/{key} 已被 pid={meta.get('pid')} 持有")
    try:
        st = os.stat(lock_rel, dir_fd=ns_fd, follow_symlinks=False)
        stale = (time.time() - st.st_mtime) > LOCK_STALE_SECONDS
    except OSError:
        stale = True  # 文件消失(竞态)→ 允许重试创建
    if not stale:
        raise ReservationHeld(
            f"锁 {namespace}/{key} 持有中(pid 已退出但未超陈旧阈值)")
    # 显式回收并记日志(不静默夺锁)
    log.warning(
        "回收陈旧锁:namespace=%s key=%s pid=%s 已不存在且超 %ss",
        namespace, key, meta.get("pid"), LOCK_STALE_SECONDS)
    os.unlink(lock_rel, dir_fd=ns_fd)
    os.fsync(ns_fd)
    try:
        return _create_excl(lock_rel, ns_fd)
    except FileExistsError:
        raise ReservationHeld(
            f"锁创建竞争失败(回收后仍冲突): {lock_rel}") from None


def _create_excl(lock_rel: str, ns_fd: int) -> int:
    return os.open(lock_rel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
                   dir_fd=ns_fd)


def _validate_ns_key(namespace: str, key: str) -> None:
    if not namespace or "/" in namespace or "\x00" in namespace:
        raise ValueError(f"namespace 非法: {namespace!r}")
    if not key or "/" in key or "\x00" in key:
        raise ValueError(f"key 非法: {key!r}")


@contextlib.contextmanager
def _fresh_anchor_handles():
    """每次取用都重新 load_verified(),退出时统一关闭全部 handle 的 fd。

    load_verified() 每次返回四根各自的 AnchorHandle(每根 2 个 fd)——handle
    不得跨调用复用,其 fd 是本次调用新开的资源,用完即关(含异常路径),
    否则每次 reserve / assert_valid 泄漏 8 个 fd,长驻循环下逐次耗尽。
    """
    handles = anchors.load_verified()
    try:
        yield handles
    finally:
        for handle in handles.values():
            handle.close()


@contextlib.contextmanager
def _fresh_locks_handle():
    """每次取用都从稳定锚点重走完整链,取 locks_dir 的已验证 handle。

    退出 with 块时由 _fresh_anchor_handles 统一关闭全部 handle 的 fd。
    """
    with _fresh_anchor_handles() as handles:
        try:
            h = handles["locks_dir"]
        except KeyError:
            raise AnchorDriftError("anchors.load_verified() 未返回 locks_dir") from None
        _verify_root_in_place(h)
        yield h


def _fresh_ns_fd(namespace: str, *, create: bool) -> int:
    """重新锚定 locks 根后打开独立的 namespace 目录 fd。"""
    with _fresh_locks_handle() as h:
        return _open_ns_dir(h.root_fd, namespace, create=create)


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------


def reserve(namespace: str, key: str) -> Reservation:
    """O_EXCL 预约:成功返回 Reservation 能力对象,已存在抛 ReservationHeld。

    - 每次调用重新 ``anchors.load_verified()``(handle 不得跨调用复用);
    - namespace 子目录与锁文件全部相对受信 root fd;
    - 陈旧锁(pid 不存在且超阈值)由 CONTENT/DESTNAME/RUN_ID 显式回收并记日志;
      BATCH 族与带 drift_pending 标记的锁一律报「需人工裁定」;
    - 不可解析锁 fail-closed,绝不视作无锁重建。
    """
    _validate_ns_key(namespace, key)
    ns_fd = _fresh_ns_fd(namespace, create=True)
    lock_rel = key + ".lock"
    try:
        try:
            fd = _create_excl(lock_rel, ns_fd)
        except FileExistsError:
            fd = _recover_or_raise(lock_rel, ns_fd, namespace, key)
        meta = {
            "v": 1,
            "ns": namespace,
            "key": key,
            "owner_token": os.urandom(32).hex(),
            "pid": os.getpid(),
            "ts": int(time.time()),
        }
        _write_initial(fd, ns_fd, meta)
        # 锁的持有语义由「文件存在」承载(O_EXCL 创建),不依赖 fd 保持打开;
        # 首行已写入并 fsync,fd 用完即关——否则每次成功 reserve() 都漏一个 fd
        # (Reservation 本身也不持有它,assert_valid/_delete_lock 全走路径重开)。
        os.close(fd)
        return Reservation(namespace=namespace, key=key,
                           owner_token=meta["owner_token"], pid=meta["pid"],
                           ts=meta["ts"])
    finally:
        os.close(ns_fd)


class Reservation:
    """预约能力对象:判据是「我持有」而非「有人持有」。

    能力绑定进程:``assert_valid`` 校验五项全等(未 released / 锁文件仍在 /
    namespace+key 相等 / 文件内 owner_token 逐字相等 / pid 三方一致),使跨进程
    token 复制无效。``__exit__`` 按 poisoned 状态分支:未 poisoned(含普通异常
    退出)删锁并置 released;已 poisoned 一律保留锁文件、不置 released。
    """

    def __init__(self, namespace: str, key: str, owner_token: str, pid: int,
                 ts: int):
        self.namespace = namespace
        self.key = key
        self.owner_token = owner_token
        self.pid = pid
        self.ts = ts
        self._released = False
        self._poisoned = False

    # -- 状态 -----------------------------------------------------------

    @property
    def is_released(self) -> bool:
        return self._released

    @property
    def is_poisoned(self) -> bool:
        return self._poisoned

    # -- 凭证校验 -------------------------------------------------------

    def assert_valid(self, expected_namespace: str, expected_key: str) -> bool:
        """五项全等校验,任一不符抛 InvalidReservation。每次调用重新锚定。"""
        if self._released:
            raise InvalidReservation("reservation 已 released,凭证失效")
        if self.namespace != expected_namespace or self.key != expected_key:
            raise InvalidReservation(
                f"namespace/key 与期望不符:({self.namespace}, {self.key}) "
                f"!= ({expected_namespace}, {expected_key})")
        ns_fd = _fresh_ns_fd(self.namespace, create=False)
        try:
            try:
                os.stat(self.key + ".lock", dir_fd=ns_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise InvalidReservation("锁文件不存在,凭证失效") from None
            meta, _events, _dropped = _read_lock_state(self.key + ".lock", ns_fd)
        finally:
            os.close(ns_fd)
        if meta.get("owner_token") != self.owner_token:
            raise InvalidReservation("锁文件内 owner_token 与对象内不符")
        if meta.get("pid") != self.pid or self.pid != os.getpid():
            raise InvalidReservation(
                f"pid 三方不一致:对象 {self.pid},锁文件 {meta.get('pid')},"
                f"当前 {os.getpid()}")
        return True

    # -- 上下文管理 -----------------------------------------------------

    def __enter__(self) -> Reservation:
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._poisoned:
            # ② 已 poisoned:任何退出路径一律保留锁文件、不置 released、
            # 不吞异常(保留的锁是 BATCH 族「永不自动回收」规则的对象)
            return False
        try:
            self._delete_lock()
        except AnchorDriftError:
            log.warning("释放锁时锚点已漂移,保留锁文件待人工裁定: %s/%s",
                        self.namespace, self.key)
            self._poisoned = True
            return False
        self._released = True
        return False

    # -- 漂移处置 -------------------------------------------------------

    def poison(self) -> None:
        """不可逆置 poisoned(漂移处置的第一步,先于任何标记写入)。"""
        self._poisoned = True

    def begin_drift_disposition(self, evidence: DriftEvidence) -> None:
        """漂移处置:① 先置 poisoned(此后任何退出路径都保留锁文件);
        ② 再向锁文件追加 drift_pending 事件行(单次 O_APPEND os.write + fsync
        文件与父目录);任一步失败都不释放、不返回成功,poisoned 不可撤销。
        本方法是写 drift_pending 的唯一入口,绕过即违约。"""
        if self._released:
            raise InvalidReservation("reservation 已 released")
        self.poison()  # ① 先置 poisoned,不可撤销
        # ② 追加事件行;失败 → 不释放、不吞异常(poisoned 已置,锁保留)
        self._append_event({
            "ev": "drift_pending",
            "evidence_sha256": _evidence_sha256(evidence),
            "nonce": evidence.nonce,
            "ts": int(time.time()),
        })

    def release_after_taint(self, evidence: DriftEvidence) -> None:
        """sealed 锁的唯一释放通道:凭证据释放,不凭「marker 存在」。

        三方逐字比对:传入证据 hash == 锁事件记录 hash == taint marker 内容
        hash,且 nonce 一致;内部先复核 drift_pending 已落盘 + marker 已按
        batch_key 推导路径落盘且内容匹配。任一不符拒绝释放。普通 __exit__、
        异常展开、陈旧回收一律不得删除 sealed 锁。"""
        if self._released:
            raise InvalidReservation("reservation 已 released")
        if not self._poisoned:
            raise InvalidReservation("仅 sealed(已 poisoned)锁可经此释放")
        if evidence.batch_key != self.key:
            raise InvalidReservation(
                "evidence.batch_key 与该 reservation 的 key 不符,拒绝释放")
        # sealed 锁的释放仍必须绑定原 reservation 能力,不能靠伪造对象 +
        # marker/evidence 旁路 owner_token/pid 校验直接删锁。
        self.assert_valid(self.namespace, self.key)
        with _fresh_anchor_handles() as handles:
            locks_h = handles["locks_dir"]
            _verify_root_in_place(locks_h)
            batch_h = handles["batch"]
            ns_fd = _open_ns_dir(locks_h.root_fd, self.namespace, create=False)
            try:
                # 三方之一:锁事件里记录的 evidence hash 与 nonce
                meta, events, _dropped = _read_lock_state(self.key + ".lock", ns_fd)
                pendings = [ev for ev in events if ev.get("ev") == "drift_pending"]
                if not pendings:
                    raise InvalidReservation("drift_pending 未落盘,拒绝释放")
                rec = pendings[-1]
                e_hash = _evidence_sha256(evidence)
                if rec.get("evidence_sha256") != e_hash:
                    raise InvalidReservation("证据 hash 与锁内记录不符,拒绝释放")
                if rec.get("nonce") != evidence.nonce:
                    raise InvalidReservation("nonce 不符,拒绝释放")
                # 三方之二/之三:marker 按 batch_key 推导路径读取,内容 hash 必须相等
                marker_bytes = self._read_marker(batch_h, evidence.batch_key)
                if hashlib.sha256(marker_bytes).hexdigest() != e_hash:
                    raise InvalidReservation("marker 内容与证据 hash 不符,拒绝释放")
                # 全部通过 → 释放(sealed 锁唯一出路)
                os.unlink(self.key + ".lock", dir_fd=ns_fd)
                os.fsync(ns_fd)
                self._released = True
            finally:
                os.close(ns_fd)

    # -- 内部 -----------------------------------------------------------

    def _append_event(self, event: dict) -> None:
        """单次 O_APPEND os.write 追加状态事件行 + fsync(文件与父目录)。

        绝不 unlink / rename / truncate:锁文件因此始终存在且前序内容始终完整。
        """
        ns_fd = _fresh_ns_fd(self.namespace, create=False)
        fd = None
        try:
            fd = os.open(self.key + ".lock",
                         os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=ns_fd)
            payload = json.dumps(event, sort_keys=True).encode() + b"\n"
            n = _write_os(fd, payload)  # 单次写入,含结尾换行
            if n != len(payload):
                raise OSError(f"锁事件行短写({n} < {len(payload)} 字节)")
            os.fsync(fd)
            os.fsync(ns_fd)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            os.close(ns_fd)

    @staticmethod
    def _marker_rel_from_key(batch_key: str) -> tuple[list[str], str]:
        """从稳定 batch key 解码根相对路径,推导 marker 的 (目录级, basename)。"""
        try:
            _dev, _ino, enc = batch_key.split(":", 2)
            rel = base64.urlsafe_b64decode(enc.encode()).decode()
        except Exception:
            raise InvalidReservation(f"batch_key 无法解码: {batch_key!r}") from None
        parts = rel.split("/")
        if not parts or not parts[-1]:
            raise InvalidReservation(f"batch_key 无目标分量: {batch_key!r}")
        return parts[:-1], parts[-1] + ".marker"

    def _read_marker(self, h: anchors.AnchorHandle, batch_key: str) -> bytes:
        """按 batch_key 推导路径读取 taint marker(相对受信 batch 根逐级重走)。

        marker 挂在与目标相同的根相对路径上(同 basename + ".marker"),随
        目标所在目录一起被替换则读不到——封存只认稳定键推导出的位置。
        """
        rel_dirs, marker_name = self._marker_rel_from_key(batch_key)
        fd = h.root_fd
        try:
            for part in rel_dirs:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
                if fd != h.root_fd:
                    os.close(fd)
                fd = nfd
            try:
                mfd = os.open(marker_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                raise InvalidReservation(
                    f"taint marker 未按推导路径落盘: {marker_name}") from None
            try:
                chunks = []
                while True:
                    b = os.read(mfd, 65536)
                    if not b:
                        break
                    chunks.append(b)
            finally:
                os.close(mfd)
            return b"".join(chunks)
        finally:
            # 中间级目录 fd 用外层 finally 统一收口:原实现只在 mfd 打开成功
            # 后才关闭 fd,marker 缺失(FileNotFoundError)或中间级 open 本身
            # 失败都会跳过内层 finally,漏关 fd(release_after_taint 失败重试
            # 场景下反复触发,逐次泄漏)。
            if fd != h.root_fd:
                os.close(fd)

    def _delete_lock(self) -> None:
        ns_fd = _fresh_ns_fd(self.namespace, create=False)
        try:
            try:
                os.unlink(self.key + ".lock", dir_fd=ns_fd)
            except FileNotFoundError:
                pass
            os.fsync(ns_fd)
        finally:
            os.close(ns_fd)


# ---------------------------------------------------------------------------
# BATCH key 契约
# ---------------------------------------------------------------------------


def dir_anchored_key(path) -> AnchoredTarget:
    """把任意路径锚定到「受信 batch 根身份 + canonical 根相对路径」。

    返回 ``AnchoredTarget{key, dir_fd, basename}``:
    - ``.key`` = ``<受信根 st_dev>:<受信根 st_ino>:<根相对路径的安全编码>``,
      锚在不可变的受信根 fd 身份上,不受目标文件 inode 替换或父目录 rename
      影响(父目录 inode 会变,根身份 + 根相对路径不会);
    - ``.dir_fd`` = 目标父目录的新开 fd(调用方用完必须 close,不参与相等性
      断言),供调用方以 openat + O_NOFOLLOW 相对读写;
    - ``.basename`` = 已 canonicalize 的目标 basename。

    别名归一:真实路径 / 含 ``./`` 的相对形式 / 指向目标的 symlink(同名或异名
    basename)均解析到同一 canonical 目标 → 同一 key。硬链接(nlink != 1)、
    非常规文件、父目录为 symlink/不存在、目标不在受信根下、受信根自身被换走
    ——一律抛,不退化为路径字符串,也不退化为文件 inode。
    """
    with _fresh_anchor_handles() as handles:
        batch = handles["batch"]
        _verify_root_in_place(batch)
        root_fd = batch.root_fd
        root_st = os.fstat(root_fd)

        # 1. 解析最后分量 symlink 链,得 canonical 绝对路径(中间级 symlink 不解析,
        #    留给第 3 步逐级 O_NOFOLLOW 重走时拒绝)
        cur = os.path.normpath(os.path.abspath(os.fspath(path)))
        for _ in range(40):
            try:
                lst = os.lstat(cur)
            except OSError as e:
                raise AnchorDriftError(
                    f"dir_anchored_key: 目标不可解析({cur}): {e.strerror}") from None
            if stat.S_ISLNK(lst.st_mode):
                tgt = os.readlink(cur)
                cur = os.path.normpath(
                    tgt if os.path.isabs(tgt) else os.path.join(os.path.dirname(cur), tgt))
            else:
                break
        else:
            raise AnchorDriftError(f"dir_anchored_key: symlink 链过长: {cur}")

        # 2. 从绝对根逐级 no-follow stat,定位「与受信根 fd 身份匹配」的级 ——
        #    根相对路径由此确定(不依赖任何路径字符串基线)
        parts = cur.split(os.sep)
        root_idx = None
        for i in range(1, len(parts) + 1):
            p = os.sep.join(parts[:i])
            if p == "":
                p = os.sep
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                raise AnchorDriftError(
                    f"dir_anchored_key: 路径不可解析: {p}") from None
            if (st.st_dev, st.st_ino) == (root_st.st_dev, root_st.st_ino):
                root_idx = i
                break
        if root_idx is None:
            raise AnchorDriftError(
                f"dir_anchored_key: 目标不在受信 batch 根之下或根已被替换: {cur}")
        rel = parts[root_idx:]
        if not rel:
            raise AnchorDriftError("dir_anchored_key: 目标必须是根下的文件,不能是根自身")

        # 3. 从受信根 fd 逐级 O_NOFOLLOW 重走 rel[:-1](任一节不符/为 symlink → 抛),
        #    最后一级 fstat 校验(常规文件且 nlink == 1)
        fd = root_fd
        try:
            for part in rel[:-1]:
                try:
                    nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=fd)
                except OSError as e:
                    raise AnchorDriftError(
                        f"dir_anchored_key: 父目录链不可解析({part}): {e.strerror}") from None
                if fd != root_fd:
                    os.close(fd)
                fd = nfd
            basename = rel[-1]
            try:
                lst = os.stat(basename, dir_fd=fd, follow_symlinks=False)
            except OSError as e:
                raise AnchorDriftError(
                    f"dir_anchored_key: 目标不可解析({basename}): {e.strerror}") from None
            if not stat.S_ISREG(lst.st_mode):
                raise AnchorDriftError(
                    f"dir_anchored_key: 目标非常规文件,拒绝锚定: {basename}")
            if lst.st_nlink != 1:
                raise AnchorDriftError(
                    f"dir_anchored_key: 目标存在硬链接(nlink={lst.st_nlink}),"
                    f"别名无法 canonicalize,拒绝锚定: {basename}")
        except BaseException:
            if fd != root_fd:
                os.close(fd)
            raise

        enc = base64.urlsafe_b64encode("/".join(rel).encode()).decode()
        key = f"{root_st.st_dev}:{root_st.st_ino}:{enc}"
        # 根直属目标(无中间目录)时 fd 即 batch 根 fd 本身——with 退出会统一
        # 关闭全部 handle,必须 dup 出独立 fd 才能把 dir_fd 交给调用方
        if fd == root_fd:
            fd = os.dup(fd)
        return AnchoredTarget(key=key, dir_fd=fd, basename=basename)
