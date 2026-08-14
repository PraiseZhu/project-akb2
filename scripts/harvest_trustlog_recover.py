#!/usr/bin/env python3
"""harvest trust-log 受控恢复 —— 坏尾 poison 的唯一解除入口。

协议(与 sc-asm-bootstrap (5d3)(w3) 逐条对应):

- 恢复入口独立,不在常规路径上:bootstrap 的 init/migrate 一律不得调用本脚本
  (bootstrap 模块不 import 本脚本;本脚本也不得自行扫描推断截断点)。
- 授权凭证 = 一份由 ROOT_SK 离线签名的恢复票据:
    {deployment_id, entry_hash(截断目标:保留的最后一条完整条目的内容 hash),
     bad_tail_sha256(待截除字节的 sha256), issued_at, nonce, signature}
  signature = ROOT_SK 对除 signature 外全部字段的签名(RSA-SHA256, DER hex)。
- 校验次序:ROOT_PK 验签 → deployment_id 与 trust-log 内一致 → 磁盘实测
  (entry_hash 匹配的条目末尾偏移、坏尾字节 sha256)逐项比对 → 任一不符即拒,
  零字节改动。
- 截断边界由票据指定,不由脚本推断:按 entry_hash 在 trust-log 中定位
  「该条目末尾」为截断偏移;自行扫描「找最后一条完整条目」是被禁止的
  (缺 entry_hash 即拒)。
- nonce 防重放:recovery-log 中已存在同 nonce 的条目 → 拒。
- 审计:每次恢复向 trust-log 同级的 append-only recovery-log 追加一条完整条目
  {ticket 全文, before_sha256, after_sha256, offset, ts, uid, gid},
  O_APPEND 单次写 + fsync。
- 恢复完成后坏尾消失,poison 解除,migrate 方可继续;(w2) 的拒绝态只由
  本脚本按票据解除,migrate 自身无权解除。

信任边界声明(同 bootstrap):ROOT_SK 持有者是受信主体,离线签发不在对抗范围
内;本机制防的是「误操作与自动化误清」,不防「持有 ROOT_SK 的内部人有意为之」。

部署常量注入与 bootstrap 相同(env):AKB2_HARVEST_DEPLOY_ROOT / DEPLOY_UID /
DEPLOY_GID / RUNTIME_READER_GID / RUNTIME_UID / RUNTIME_GID / ROOT_PK_FINGERPRINT。
执行身份校验(仅部署 UID/GID)在读取任何文件之前完成。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ENV_DEPLOY_ROOT = "AKB2_HARVEST_DEPLOY_ROOT"
ENV_DEPLOY_UID = "AKB2_HARVEST_DEPLOY_UID"
ENV_DEPLOY_GID = "AKB2_HARVEST_DEPLOY_GID"
ENV_ROOT_FP = "AKB2_HARVEST_ROOT_PK_FINGERPRINT"

ROOT_KEY_DIR = "root-key"
ROOT_SK_NAME = "root.sk"
ROOT_PK_NAME = "root.pub"
TRUST_LOG_DIR = "trust-log"
TRUST_LOG_NAME = "log"
RECOVERY_LOG_NAME = "recovery-log"

DEFAULT_ROOT_PK_FINGERPRINT = ""


class RecoverError(Exception):
    """业务失败:打印 stderr 后 exit 非零。"""


def err(msg: str, code: int = 2) -> None:
    print(f"harvest recover: {msg}", file=sys.stderr)
    sys.exit(code)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def entry_hash(entry: dict) -> str:
    return sha256_bytes(json.dumps(entry, sort_keys=True).encode())


def scan_trust_log_bytes(raw: bytes) -> tuple[list[tuple[dict, int]], bytes | None]:
    """扫描 trust-log 原始字节,返回 (完整条目及其末尾偏移, 坏尾字节)。"""
    segments = raw.split(b"\n")
    trailing = b"" if segments[-1] == b"" else segments.pop()
    entries: list[tuple[dict, int]] = []
    offset = 0
    for raw_line in segments:
        line_bytes = raw_line + b"\n"
        line = raw_line.strip()
        if not line:
            offset += len(line_bytes)
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return entries, raw[offset:]
        offset += len(line_bytes)
        entries.append((entry, offset))
    if trailing:
        return entries, trailing
    return entries, None


def parse_trust_log(path: Path) -> tuple[list[dict], bytes | None]:
    """解析 trust-log,返回 (完整条目列表, 坏尾字节)。语义与 bootstrap 一致。"""
    if not path.exists():
        return [], None
    parsed, bad = scan_trust_log_bytes(path.read_bytes())
    return [entry for entry, _offset in parsed], bad


def load_constants() -> dict:
    root = os.environ.get(ENV_DEPLOY_ROOT)
    if not root:
        raise RecoverError(f"部署根未配置:需经 {ENV_DEPLOY_ROOT} 提供")
    deploy_root = Path(root)
    req = {ENV_DEPLOY_UID: "deploy_uid", ENV_DEPLOY_GID: "deploy_gid"}
    vals = {}
    for env_name, key in req.items():
        raw = os.environ.get(env_name)
        if not raw or not raw.isdigit():
            raise RecoverError(f"部署常量缺失或非法:{env_name}={raw!r}")
        vals[key] = int(raw)
    return {
        "deploy_root": deploy_root,
        "root_pk": deploy_root / ROOT_KEY_DIR / ROOT_PK_NAME,
        "trust_log": deploy_root / TRUST_LOG_DIR / TRUST_LOG_NAME,
        "recovery_log": deploy_root / TRUST_LOG_DIR / RECOVERY_LOG_NAME,
        "deploy_uid": vals["deploy_uid"],
        "deploy_gid": vals["deploy_gid"],
        "root_fp": os.environ.get(ENV_ROOT_FP) or DEFAULT_ROOT_PK_FINGERPRINT,
    }


def enforce_identity(c: dict) -> None:
    euid, egid = os.geteuid(), os.getegid()
    sys.audit("akb2_harvest.identity_check", euid, egid)
    if euid != c["deploy_uid"] or egid != c["deploy_gid"]:
        raise RecoverError(
            f"拒绝:进程有效身份 ({euid},{egid}) 不属于专用部署身份 "
            f"({c['deploy_uid']},{c['deploy_gid']})"
        )


def openssl_verify(pub_pem: Path, data: bytes, sig_hex: str) -> bool:
    try:
        sig_bytes = bytes.fromhex(sig_hex)
    except (TypeError, ValueError):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="akb2-harvest-recover-") as tmp_dir:
            tmp = Path(tmp_dir)
            data_path = tmp / "ticket.data"
            sig_path = tmp / "ticket.sig"
            data_path.write_bytes(data)
            sig_path.write_bytes(sig_bytes)
            r = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(pub_pem),
                    "-signature",
                    str(sig_path),
                    str(data_path),
                ],
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            return r.returncode == 0
    except OSError as e:
        raise RecoverError(f"验签阶段执行失败:{e}") from None


def validate_recovery_target(
    parsed_entries: list[tuple[dict, int]], bad_tail: bytes | None, target_hash: str
) -> tuple[int, bytes]:
    """票据边界必须指向最后一条完整条目,不得把有效记录算进坏尾。"""
    if not parsed_entries:
        raise RecoverError("trust-log 为空或不可读,无条目可恢复")
    if not bad_tail:
        raise RecoverError("trust-log 未检测到坏尾,拒绝无效恢复票据")
    target_offset = None
    for entry, end_offset in parsed_entries:
        if entry_hash(entry) == target_hash:
            target_offset = end_offset
    if target_offset is None:
        raise RecoverError("票据 entry_hash 在 trust-log 中无匹配条目,拒绝(边界必须由票据指定)")
    last_complete_offset = parsed_entries[-1][1]
    if target_offset != last_complete_offset:
        raise RecoverError("票据 entry_hash 不是最后一条完整条目,拒绝截断有效记录")
    return target_offset, bad_tail


def _rollback_trust_log(fd: int, trust_log: Path, bad_bytes: bytes, before_sha256: str) -> None:
    try:
        n = os.write(fd, bad_bytes)
        if n != len(bad_bytes):
            raise RecoverError(f"trust-log 回滚短写:{n}/{len(bad_bytes)}")
        os.fsync(fd)
    except OSError as e:
        raise RecoverError(f"trust-log 回滚失败:{e}") from None
    restored = sha256_file(trust_log)
    if restored != before_sha256:
        raise RecoverError(
            f"trust-log 回滚后哈希不符:期望 {before_sha256},实测 {restored}"
        )


def truncate_with_audit(
    trust_log: Path,
    recovery_log: Path,
    target_offset: int,
    bad_bytes: bytes,
    payload: bytes,
    before_sha256: str,
    after_sha256: str,
) -> None:
    """先预检 recovery-log 可写,再截断;审计落盘失败时回滚 trust-log。"""
    try:
        tfd = os.open(trust_log, os.O_WRONLY | os.O_APPEND)
    except OSError as e:
        raise RecoverError(f"无法打开 trust-log 进行恢复:{e}") from None
    try:
        try:
            rfd = os.open(recovery_log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        except OSError as e:
            raise RecoverError(f"无法打开 recovery-log:{e}") from None
        try:
            try:
                os.ftruncate(tfd, target_offset)
                os.fsync(tfd)
                n = os.write(rfd, payload)
                if n != len(payload):
                    raise OSError(f"recovery-log 追加短写:{n}/{len(payload)}")
                os.fsync(rfd)
            except OSError as e:
                try:
                    _rollback_trust_log(tfd, trust_log, bad_bytes, before_sha256)
                except RecoverError as rollback_err:
                    raise RecoverError(
                        f"recovery-log 追加失败且 trust-log 回滚失败:{e}; {rollback_err}"
                    ) from None
                raise RecoverError(f"recovery-log 追加失败,已回滚 trust-log:{e}") from None
        finally:
            os.close(rfd)
    finally:
        os.close(tfd)
    if sha256_file(trust_log) != after_sha256:
        raise RecoverError(
            f"trust-log 截断后哈希不符:期望 {after_sha256},实测 {sha256_file(trust_log)}"
        )


def nonce_seen(recovery_log: Path, nonce: str) -> bool:
    """recovery-log 必须保持可解析;损坏时 fail-closed。"""
    if not recovery_log.exists():
        return False
    for line in recovery_log.read_bytes().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            prev = json.loads(line)
        except json.JSONDecodeError as e:
            raise RecoverError(f"recovery-log 含损坏条目,拒绝继续恢复:{e}") from None
        if prev.get("ticket", {}).get("nonce") == nonce:
            return True
    return False


def check_root_pk(c: dict) -> None:
    """第 0 步:ROOT_PK 指纹与部署常量逐字比对(与 bootstrap 同锚)。"""
    if not c["root_pk"].exists():
        raise RecoverError(f"ROOT_PK 缺失:{c['root_pk']}")
    fp = sha256_file(c["root_pk"])
    if c["root_fp"] and c["root_fp"] != fp:
        raise RecoverError(
            f"ROOT_PK 指纹不符:部署常量钉住 {c['root_fp']},实际 {fp}"
        )


def load_ticket(ticket_path: Path) -> dict:
    try:
        ticket = json.loads(ticket_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise RecoverError(f"票据无法读取或解析:{ticket_path}({e})") from None
    required = {"deployment_id", "entry_hash", "bad_tail_sha256", "issued_at", "nonce", "signature"}
    missing = required - set(ticket)
    if missing:
        raise RecoverError(f"票据缺少字段:{sorted(missing)}")
    return ticket


def main() -> None:
    argv = sys.argv[1:]
    if argv in (["--help"], ["-h"]):
        print(
            "usage: harvest_trustlog_recover.py --ticket <票据文件>\n\n"
            "受控恢复入口:凭 ROOT_SK 离线的签名票据按票据指定偏移截断 trust-log 坏尾。\n"
            "票据字段:{deployment_id, entry_hash, bad_tail_sha256, issued_at, nonce, signature}。\n"
            "边界由票据指定,脚本不自行推断截断点;恢复审计写入 recovery-log。",
            end="",
        )
        sys.exit(0)
    if len(argv) != 2 or argv[0] != "--ticket":
        err("用法错误:--ticket <票据文件>;--help 查看用法")
    ticket_path = Path(argv[1])
    try:
        c = load_constants()
        enforce_identity(c)
        check_root_pk(c)
        ticket = load_ticket(ticket_path)
        # 票据验签:必须由 ROOT_PK 对应的 ROOT_SK 离线签发
        body = {k: v for k, v in ticket.items() if k != "signature"}
        if not openssl_verify(
            c["root_pk"], json.dumps(body, sort_keys=True).encode(), ticket["signature"]
        ):
            err("票据签名无效(非 ROOT_SK 签发或已被篡改),拒绝且零字节改动")
        raw = c["trust_log"].read_bytes()
        parsed_entries, bad = scan_trust_log_bytes(raw)
        entries = [entry for entry, _offset in parsed_entries]
        if not entries:
            err("trust-log 为空或不可读,无条目可恢复")
        # 票据字段逐项与磁盘实测比对(任一不符即拒,零改动)
        if ticket["deployment_id"] != entries[0].get("deployment_id"):
            err(
                f"票据 deployment_id={ticket['deployment_id']!r} 与 trust-log "
                f"{entries[0].get('deployment_id')!r} 不符,拒绝"
            )
        target_offset, bad_bytes = validate_recovery_target(
            parsed_entries, bad, ticket["entry_hash"]
        )
        if sha256_bytes(bad_bytes) != ticket["bad_tail_sha256"]:
            err(
                f"票据 bad_tail_sha256 与磁盘实测不符(实测 {sha256_bytes(bad_bytes)}),拒绝"
            )
        # nonce 防重放:recovery-log 已含同 nonce → 拒
        if nonce_seen(c["recovery_log"], ticket["nonce"]):
            err(f"票据 nonce={ticket['nonce']!r} 已使用过(重放),拒绝")
        before = sha256_bytes(raw)
        after = sha256_bytes(raw[:target_offset])
        audit_entry = {
            "ticket": ticket,
            "before_sha256": before,
            "after_sha256": after,
            "offset": target_offset,
            "ts": int(time.time()),
            "uid": os.geteuid(),
            "gid": os.getegid(),
        }
        payload = json.dumps(audit_entry, sort_keys=True).encode() + b"\n"
        truncate_with_audit(
            c["trust_log"],
            c["recovery_log"],
            target_offset,
            bad_bytes,
            payload,
            before,
            after,
        )
        print(
            f"[recover] offset={target_offset} before={before[:16]} after={after[:16]} "
            f"bad_tail={len(bad_bytes)} bytes removed;poison 解除,migrate 可继续"
        )
    except RecoverError as e:
        err(str(e))
    except OSError as e:
        err(f"文件操作失败:{e}")


if __name__ == "__main__":
    main()
