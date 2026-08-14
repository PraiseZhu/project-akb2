#!/usr/bin/env python3
"""harvest_bootstrap 基础模块:部署常量,异常,基本工具函数。"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 部署常量(env 注入,argv 不可覆盖;身份校验前置使其只对部署身份生效)
# ---------------------------------------------------------------------------

ENV_DEPLOY_ROOT = "AKB2_HARVEST_DEPLOY_ROOT"
ENV_DEPLOY_UID = "AKB2_HARVEST_DEPLOY_UID"
ENV_DEPLOY_GID = "AKB2_HARVEST_DEPLOY_GID"
ENV_READER_GID = "AKB2_HARVEST_RUNTIME_READER_GID"
ENV_RUNTIME_UID = "AKB2_HARVEST_RUNTIME_UID"
ENV_RUNTIME_GID = "AKB2_HARVEST_RUNTIME_GID"
ENV_ROOT_FP = "AKB2_HARVEST_ROOT_PK_FINGERPRINT"
ENV_CRASH = "AKB2_HARVEST_CRASH_STAGE"
ENV_SHORT_WRITE = "AKB2_HARVEST_SIMULATE_SHORT_WRITE"
ENV_PAUSE_CAS = "AKB2_HARVEST_PAUSE_BEFORE_CAS"

# 故障注入双因子门禁:仅设置上述 env 不足以触发任何注入钩子,还必须在
# deploy_root 下存在这个物理标记(owner=部署 UID、mode 0600、内容逐字匹配)。
# 单一环境变量意外混入某部署身份的持久环境是可以想见的运维事故,但让该
# 环境同时长出一个 owner 精确匹配、mode 精确匹配、内容精确匹配的标记文件
# 不是"意外"能达成的——这把"可用性地雷"的触发条件从一个环境变量提高到
# 两个独立信道同时失守,且标记文件本身不出现在任何生产部署 skeleton
# (ensure_skeleton)里,只由测试 fixture 显式创建。
TEST_MODE_MARKER_NAME = ".akb2-harvest-test-mode"
TEST_MODE_MARKER_MAGIC = "AKB2_HARVEST_TEST_MODE_V1"

# 布局常量(相对 deploy_root,代码写死,不接受任何命令行覆盖)
ROOTS_CONFIG_NAME = "roots.json"
ROOT_KEY_DIR = "root-key"
ROOT_SK_NAME = "root.sk"
ROOT_PK_NAME = "root.pub"
TRUST_LOG_DIR = "trust-log"
TRUST_LOG_NAME = "log"
LOCK_NAME = ".deploy.lock"
RECOVERY_LOG_NAME = "recovery-log"
GENERATIONS_DIR = "generations"
SIGNER_KEYS_DIR = "signer-keys"

# ROOT_PK 指纹常量:硬编码默认值(部署方首次 init 拿到指纹后填入;env 可钉住)
DEFAULT_ROOT_PK_FINGERPRINT = ""

K_ID_PREFIX = 16  # key-id = 公钥内容 sha256 的前 16 hex

SCHEMA_VERSION = 1


class BootstrapError(Exception):
    """业务失败:打印 stderr 后 exit 2。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def err(msg: str, code: int = 2) -> None:
    print(f"harvest bootstrap: {msg}", file=sys.stderr)
    sys.exit(code)


def test_mode_enabled(c: dict) -> bool:
    """双因子门禁:env 钩子是否真的启用,还要求 deploy_root 下存在物理标记。

    标记文件本身必须 owner=部署 UID、mode 0600(仅 owner 可读写)、内容逐字
    匹配 TEST_MODE_MARKER_MAGIC——三者任一不符即视为未启用,不打印细节
    (故障注入门禁本身不应成为额外的信息泄露面)。生产 ensure_skeleton()
    从不创建该文件,只有测试 fixture 会显式落盘它。
    """
    marker = c["deploy_root"] / TEST_MODE_MARKER_NAME
    try:
        st = marker.stat()
    except OSError:
        return False
    if st.st_uid != c["deploy_uid"] or (st.st_mode & 0o777) != 0o600:
        return False
    try:
        content = marker.read_text().strip()
    except OSError:
        return False
    return content == TEST_MODE_MARKER_MAGIC


def crash_if(c: dict, stage: str) -> None:
    """测试注入的崩溃点:指定阶段 os._exit(137),模拟进程被杀。

    双因子门禁:仅 AKB2_HARVEST_CRASH_STAGE 匹配不够,还需 test_mode_enabled(c)。
    """
    if os.environ.get(ENV_CRASH) == stage and test_mode_enabled(c):
        os._exit(137)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def key_id_of_pem(pem: bytes) -> str:
    return sha256_bytes(pem)[:K_ID_PREFIX]
