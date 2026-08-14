#!/usr/bin/env python3
"""harvest 信任锚 bootstrap —— 受保护的部署入口,anchor manifest 的唯一签发者。

协议(与 sc-asm-bootstrap 规格逐条对应,供 sc-asm-anchors(wave 2)对齐):

- 子命令:``init --deployment-id <id>`` / ``migrate --deployment-id <id>``。
  argv 写死,不含任何路径参数;``--help`` 打印用法。任何其他参数
  (含已被方案废除的 ``--epoch-state`` / ``--active``)一律拒收 exit 2。
- 执行身份:仅专用部署 UID/GID 可执行。身份校验在任何文件访问之前完成
  (审计事件 ``akb2_harvest.identity_check`` 是脚本侧第一个事件)。
- 部署常量:一律经环境变量注入(部署配置的一部分),argv 层无法覆盖:
  AKB2_HARVEST_DEPLOY_ROOT / DEPLOY_UID / DEPLOY_GID / RUNTIME_READER_GID /
  RUNTIME_UID / RUNTIME_GID / ROOT_PK_FINGERPRINT。攻击者(运行期身份)即使
  控制 env 也会先被身份校验拒绝——env 覆盖只对部署身份生效,部署身份属受信主体。
  布局相对 deploy_root(代码常量,不可覆盖):
    roots.json            根配置(四根路径 + trust_root),mode 0400
    root-key/root.sk      ROOT_SK(离线签名私钥),mode 0400,运行期无任何权限
    root-key/root.pub     ROOT_PK(pinned 验证公钥),mode 0440(runtime reader 组只读)
    trust-log/log         append-only 提交日志,mode 0640(部署 UID O_APPEND 追加)
    trust-log/.deploy.lock  O_EXCL 单实例部署锁
    trust-log/recovery-log  受控恢复审计日志,同 trust-log 规格
    generations/          mode 0750;每代 generations/<epoch>/ O_EXCL 唯一创建
    signer-keys/          mode 0710(组仅 search);K<n>.sk mode 0400
- trust-log 条目(JSON lines,单行一条,整条一次 O_APPEND 写入):
  {deployment_id, epoch, generation_dir, bundle:{manifest/signature/public_key 摘要},
   admission:{key_id, public_key_id, public_key_sha256, predecessor, certificate},
   revocation:{prev_key_id, revoked_at_epoch} | null, prev_entry_hash, signature}
  - E1(创世)由 ROOT_SK 签;E_n(n>1)由 K_{n-1}.sk 签。
  - admission.certificate 由前驱私钥(创世为 ROOT_SK)对
    {key_id, public_key_pem, epoch, predecessor} 的签名,构成可回溯至 root 的链。
  - 条目内容 hash = sha256(整行 UTF-8 字节)。
  - (w1) 单次 os.write 必须 exact-count:返回值 != 长度即判坏尾,不重试续写。
  - (w2) 坏尾持久化 poison:检出坏尾后写入方 fail-closed,后续 migrate 拒绝,
    直到受控恢复(scripts/harvest_trustlog_recover.py)完成。
- 发布 = generation-dir 写齐并 fsync 后,对 trust-log 一次 O_APPEND 单次 write
  + fsync(文件与父目录)。准入/废止/代际切换在同一条目内生效,无中间态。
- 部署锁:内容 {pid, ts} + fsync。取锁失败:pid 存活 → exit 2「另一部署正在进行」;
  pid 已死 → exit 2「陈旧锁,需人工确认后删除」;内容不可解析 → exit 2「需人工裁定」。
- migrate 的 CAS:写入前复核 trust-log 末条完整条目的 (epoch, entry_hash) 与
  取锁时读到的完全一致,任一不符 exit 2 并同时报出两个 hash。
- 崩溃语义:提交条目落盘前崩溃 → 完整停留旧代;落盘后崩溃 → 完整进入新代。
  signer-keys/ 的崩溃残留(K_m.sk 且当代准入编号 > m)重跑时就地删除。
- 测试注入(仅部署身份生效,env):AKB2_HARVEST_CRASH_STAGE(阶段崩溃点,
  os._exit(137))、AKB2_HARVEST_SIMULATE_SHORT_WRITE(短写注入)与
  AKB2_HARVEST_PAUSE_BEFORE_CAS(CAS 复核前暂停)。双因子门禁(见
  test_mode_enabled):仅设置上述任一环境变量不生效,必须同时在 deploy_root
  下存在物理标记文件(owner=部署 UID、mode 0600、内容匹配),缺一律禁用——
  生产部署身份的持久环境即使意外混入这些变量,也不会触发注入。

信任边界声明(与规格 (5c)②a 一致):ROOT_PK 指纹与代码同源,随代码分发、随 PR
评审变更;若攻击者能改代码则一切免谈,代码完整性不在本方案对抗范围内。
ROOT_SK 持有者是受信主体,离线签发不在对抗范围内。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from harvest_bootstrap._base import (  # noqa: E402
    DEFAULT_ROOT_PK_FINGERPRINT,
    ENV_DEPLOY_GID,
    ENV_DEPLOY_ROOT,
    ENV_DEPLOY_UID,
    ENV_READER_GID,
    ENV_ROOT_FP,
    ENV_RUNTIME_GID,
    ENV_RUNTIME_UID,
    GENERATIONS_DIR,
    LOCK_NAME,
    RECOVERY_LOG_NAME,
    ROOT_KEY_DIR,
    ROOT_PK_NAME,
    ROOT_SK_NAME,
    ROOTS_CONFIG_NAME,
    SIGNER_KEYS_DIR,
    TRUST_LOG_DIR,
    TRUST_LOG_NAME,
    BootstrapError,
    err,
    key_id_of_pem,  # noqa: E402,F401
)
from harvest_bootstrap.cmd_migrate import cmd_migrate  # noqa: E402
from harvest_bootstrap.lifecycle import (  # noqa: E402,F401
    build_admission,
    build_entry,
    cmd_init,
)
from harvest_bootstrap.trustlog import entry_body_bytes  # noqa: E402,F401

# 测试经 importlib 加载本文件后,以本模块命名空间取 bootstrap 签名原语
# (build_admission / build_entry / entry_body_bytes / key_id_of_pem)构造
# 对抗输入;此处仅重导出,实现一律在 harvest_bootstrap 包内。

# ---------------------------------------------------------------------------
# 部署常量解析与身份校验
# ---------------------------------------------------------------------------


def load_deploy_constants() -> dict:
    root = os.environ.get(ENV_DEPLOY_ROOT)
    if not root:
        raise BootstrapError(f"部署根未配置:需经 {ENV_DEPLOY_ROOT} 提供")
    deploy_root = Path(root)
    req = {
        "deploy_uid": ENV_DEPLOY_UID,
        "deploy_gid": ENV_DEPLOY_GID,
        "reader_gid": ENV_READER_GID,
        "runtime_uid": ENV_RUNTIME_UID,
        "runtime_gid": ENV_RUNTIME_GID,
    }
    vals = {}
    for key, env in req.items():
        raw = os.environ.get(env)
        if not raw or not raw.isdigit():
            raise BootstrapError(f"部署常量缺失或非法:{env}={raw!r}")
        vals[key] = int(raw)
    return {
        "deploy_root": deploy_root,
        "roots_config": deploy_root / ROOTS_CONFIG_NAME,
        "root_key_dir": deploy_root / ROOT_KEY_DIR,
        "root_sk": deploy_root / ROOT_KEY_DIR / ROOT_SK_NAME,
        "root_pk": deploy_root / ROOT_KEY_DIR / ROOT_PK_NAME,
        "trust_log_dir": deploy_root / TRUST_LOG_DIR,
        "trust_log": deploy_root / TRUST_LOG_DIR / TRUST_LOG_NAME,
        "lock_file": deploy_root / TRUST_LOG_DIR / LOCK_NAME,
        "recovery_log": deploy_root / TRUST_LOG_DIR / RECOVERY_LOG_NAME,
        "generations": deploy_root / GENERATIONS_DIR,
        "signer_keys": deploy_root / SIGNER_KEYS_DIR,
        "deploy_uid": vals["deploy_uid"],
        "deploy_gid": vals["deploy_gid"],
        "reader_gid": vals["reader_gid"],
        "runtime_uid": vals["runtime_uid"],
        "runtime_gid": vals["runtime_gid"],
        "root_fp": os.environ.get(ENV_ROOT_FP) or DEFAULT_ROOT_PK_FINGERPRINT,
    }


def enforce_identity(c: dict) -> None:
    """执行身份硬校验:在读取私钥与写任何文件之前完成。

    审计事件先于一切文件访问发出(脚本侧第一个 audit 事件)。
    """
    euid, egid = os.geteuid(), os.getegid()
    sys.audit("akb2_harvest.identity_check", euid, egid)
    if euid != c["deploy_uid"] or egid != c["deploy_gid"]:
        raise BootstrapError(
            f"拒绝:进程有效身份 ({euid},{egid}) 不属于专用部署身份 "
            f"({c['deploy_uid']},{c['deploy_gid']})"
        )


# ---------------------------------------------------------------------------
# argv 解析:合法参数集合恰为 {--deployment-id}
# ---------------------------------------------------------------------------

USAGE = """usage: harvest_anchor_bootstrap.py {init|migrate} --deployment-id <id>

受保护的部署入口,anchor manifest 的唯一签发者。argv 不含任何路径参数——
根配置、ROOT_SK、ROOT_PK、trust-log、generations、signer-keys 的位置一律由
部署常量唯一定位,任何试图以命令行覆盖其中任一路径的参数一律拒收。

  init      初始化部署:生成 root key 与第一代(epoch 1)manifest,
            以 ROOT_SK 签发创世 trust-log 条目并准入 K1。
  migrate   推进一代 epoch:生成新代 manifest、轮换密钥(K_n 准入 / K_{n-1} 废止),
            由 K_{n-1} 签发一条 trust-log 条目完成代际切换。
  --deployment-id <id>   本次部署的部署标识(必填,逐字写入 manifest 与条目)。
  --help    显示本用法。
"""


def parse_args(argv: list[str]) -> tuple[str, str]:
    if len(argv) >= 1 and argv[0] in ("--help", "-h"):
        print(USAGE, end="")
        sys.exit(0)
    if not argv or argv[0] not in ("init", "migrate"):
        raise BootstrapError("用法错误:首参必须是 init 或 migrate;--help 查看用法")
    cmd = argv[0]
    deployment_id = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--deployment-id":
            if i + 1 >= len(argv):
                raise BootstrapError("用法错误:--deployment-id 缺少值")
            if deployment_id is not None:
                raise BootstrapError("用法错误:--deployment-id 重复")
            deployment_id = argv[i + 1]
            i += 2
        else:
            raise BootstrapError(f"参数拒收:{a}(argv 只接受 --deployment-id;路径参数一律拒绝)")
    if deployment_id is None:
        raise BootstrapError("用法错误:缺少必填参数 --deployment-id")
    return cmd, deployment_id


def main() -> None:
    try:
        cmd, deployment_id = parse_args(sys.argv[1:])
    except BootstrapError as e:
        err(str(e))
    try:
        c = load_deploy_constants()
        enforce_identity(c)
        if cmd == "init":
            cmd_init(c, deployment_id)
        else:
            cmd_migrate(c, deployment_id)
    except BootstrapError as e:
        err(str(e))
    except KeyboardInterrupt:
        err("中断")


if __name__ == "__main__":
    main()
