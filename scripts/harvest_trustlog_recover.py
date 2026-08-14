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


def parse_trust_log(path: Path) -> tuple[list[dict], bytes | None]:
    """解析 trust-log,返回 (完整条目列表, 坏尾字节)。语义与 bootstrap 一致。"""
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
    tmp = pub_pem.with_name(f".recv-{os.getpid()}-{time.time_ns()}")
    data_path = tmp.with_suffix(".data")
    sig_path = tmp.with_suffix(".sig")
    try:
        data_path.write_bytes(data)
        sig_path.write_bytes(bytes.fromhex(sig_hex))
        r = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(pub_pem),
             "-signature", str(sig_path), str(data_path)],
            capture_output=True, stdin=subprocess.DEVNULL,
        )
        return r.returncode == 0
    finally:
        for p in (data_path, sig_path):
            if p.exists():
                p.unlink()


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
        entries, bad = parse_trust_log(c["trust_log"])
        if not entries:
            err("trust-log 为空或不可读,无条目可恢复")
        # 票据字段逐项与磁盘实测比对(任一不符即拒,零改动)
        if ticket["deployment_id"] != entries[0].get("deployment_id"):
            err(
                f"票据 deployment_id={ticket['deployment_id']!r} 与 trust-log "
                f"{entries[0].get('deployment_id')!r} 不符,拒绝"
            )
        # 定位截断点:内容 hash == 票据 entry_hash 的条目末尾(禁止自行推断)
        target_offset = None
        raw = c["trust_log"].read_bytes()
        offset = 0
        for raw_line in raw.split(b"\n"):
            line = raw_line.strip()
            if not line:
                offset += len(raw_line) + 1
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                break
            offset += len(raw_line) + 1
            if entry_hash(entry) == ticket["entry_hash"]:
                target_offset = offset
        if target_offset is None:
            err("票据 entry_hash 在 trust-log 中无匹配条目,拒绝(边界必须由票据指定)")
        bad_bytes = raw[target_offset:]
        if sha256_bytes(bad_bytes) != ticket["bad_tail_sha256"]:
            err(
                f"票据 bad_tail_sha256 与磁盘实测不符(实测 {sha256_bytes(bad_bytes)}),拒绝"
            )
        # nonce 防重放:recovery-log 已含同 nonce → 拒
        if c["recovery_log"].exists():
            for line in c["recovery_log"].read_bytes().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    prev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if prev.get("ticket", {}).get("nonce") == ticket["nonce"]:
                    err(f"票据 nonce={ticket['nonce']!r} 已使用过(重放),拒绝")
        before = sha256_file(c["trust_log"])
        # 截断(票据指定的偏移;O_APPEND 追加审计)
        fd = os.open(c["trust_log"], os.O_WRONLY | os.O_APPEND)
        try:
            os.ftruncate(fd, target_offset)
            os.fsync(fd)
        finally:
            os.close(fd)
        after = sha256_file(c["trust_log"])
        audit_entry = {
            "ticket": ticket,
            "before_sha256": before,
            "after_sha256": after,
            "offset": target_offset,
            "ts": int(time.time()),
            "uid": os.getuid(),
            "gid": os.getgid(),
        }
        payload = json.dumps(audit_entry, sort_keys=True).encode() + b"\n"
        rfd = os.open(c["recovery_log"], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            n = os.write(rfd, payload)
            os.fsync(rfd)
        finally:
            os.close(rfd)
        if n != len(payload):
            err("recovery-log 追加短写,需人工核验")
        print(
            f"[recover] offset={target_offset} before={before[:16]} after={after[:16]} "
            f"bad_tail={len(bad_bytes)} bytes removed;poison 解除,migrate 可继续"
        )
    except RecoverError as e:
        err(str(e))


if __name__ == "__main__":
    main()
