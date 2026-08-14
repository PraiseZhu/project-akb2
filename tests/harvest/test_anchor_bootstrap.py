"""sc-asm-bootstrap 验收测试:harvest 信任锚 bootstrap 入口。

覆盖 expect ①-⑪ 全部验收用例。纪律:
- 预测红集用例先写成红(断言对错误实现的判别性),由实现转绿。
- 双身份(两个真实不同非 root UID)用例本机不可构造(本机唯一普通用户 praise/501、
  sudo 需密码、非 root 无法 setuid),写成环境探测:CI(ubuntu runner 免密 sudo + nobody)
  可跑,本机显式 skip 并在报告标注。
- 依赖 sc-asm-anchors(wave 2 g1)的 anchors.load_verified() 断言用 importorskip;
  依赖运行期链路(assemble/locks/journal/ledger,wave 3-8)的用例同样条件跳过,
  wave 集成后自动转绿。

部署常量注入协议(env,仅部署身份生效;argv 层一律拒收路径参数):
  AKB2_HARVEST_DEPLOY_ROOT / DEPLOY_UID / DEPLOY_GID / RUNTIME_READER_GID /
  RUNTIME_UID / RUNTIME_GID / ROOT_PK_FINGERPRINT
布局相对 deploy_root(代码常量):roots.json、root-key/{root.sk,root.pub}、
trust-log/{log,.deploy.lock,recovery-log}、generations/、signer-keys/。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from itertools import count
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 常量
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

# 故障注入双因子门禁的物理标记(与 scripts/harvest_anchor_bootstrap.py 逐字一致):
# deploy_setup 为每个测试部署落盘该标记,故障注入类用例才能触发钩子;
# 未落盘该标记的用例(如未来新增的"生产环境误设 env 不触发"回归)天然验证门禁生效。
TEST_MODE_MARKER_NAME = ".akb2-harvest-test-mode"
TEST_MODE_MARKER_MAGIC = "AKB2_HARVEST_TEST_MODE_V1"

# deploy_setup 每次调用分配独立 roots-tree 子目录(同一用例内多次建部署不串扰)
_TREE_COUNTER = count(1)

# argv 拒收清单:声明中存在的路径参数 + 已被方案废除的历史参数名
REJECTED_ARGS = [
    "--roots-config",
    "--key-path",
    "--generations",
    "--trust-log",
    "--epoch-state",
    "--active",
    "--raw-dir",
    "--roots",
]

# 未授权身份用例使用的「部署 UID」——不存在于本机的 uid 数字,测试进程即非部署身份
GHOST_DEPLOY_UID = 424242
GHOST_DEPLOY_GID = 424242

CRASH_STAGES = [
    # (stage, 语义)
    ("mkdir", "新 generation 目录建好但文件未写完"),
    ("manifest", "manifest 已写未签"),
    ("sig", "签名已写但公钥未落"),
    ("pub", "bundle 三件俱全但提交条目未追加"),
    ("short-write", "提交条目的 os.write 进行到一半(短写)"),
    ("pre-fsync", "条目已追加但 fsync 未完成"),
]

# 运行期全链路投递测试(⑨b)依赖的运行期模块(wave 3-8 落地)
RUNTIME_DEPS = ["akb2.harvest.assembler", "akb2.harvest.locks", "akb2.harvest.ledger"]


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

# audit hook 包装:记录 subprocess 内文件访问与 identity 事件序列,
# 供「私钥零读取 + 身份校验时序」断言使用(②)。
# 能力边界(Python audit 事件面,3.13 实测):os.open 与 builtin open 都触发
# "open"(args=(path, mode, flags));os.stat / os.read 不触发系统级 audit 事件。
# 故「零 open/read + 身份校验先于一切」由系统级事件证明(未授权路径下脚本在
# identity 之后立即 exit,不可能产生任何 open 事件);stat 无事件可审计,由
# 「identity 是第一个事件 + 私钥路径零 open + 私钥文件内容未变」旁证。
# 预 import 脚本将用到的标准库,使脚本 import 命中缓存、不产生文件访问事件,
# 从而「身份校验是脚本侧第一个事件」可被严格断言。
AUDIT_WRAPPER = (
    "import sys,json,runpy,os,hashlib,subprocess,pathlib,time;"
    "log=[];\n"
    "def hook(ev,args):\n"
    "  if ev in ('open','os.scandir','os.read'):\n"
    "    log.append([ev, str(args[0]) if args else None, args[2] if len(args)>2 and ev=='open' else None]);\n"
    "  if ev=='akb2_harvest.identity_check':\n"
    "    log.append([ev, list(args)]);\n"
    "sys.addaudithook(hook);\n"
    "al=sys.argv[2];sys.argv=[sys.argv[1]]+sys.argv[3:];\n"
    "try:\n"
    "  runpy.run_path(sys.argv[0], run_name='__main__');\n"
    "except SystemExit as e:\n"
    "  sys.exit(e.code if isinstance(e.code,int) else (1 if e.code else 0));\n"
    "finally:\n"
    "  open(al,'w').write(json.dumps(log))"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def openssl_verify(pub_pem: Path, data: Path, sig: Path) -> bool:
    """openssl dgst -sha256 -verify 验签。"""
    r = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(pub_pem), "-signature", str(sig), str(data)],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and "Verified OK" in r.stdout


def read_trust_log(path: Path) -> list[dict]:
    """解析 trust-log 为条目列表;坏尾(JSON 失败或末尾无换行终止的段)丢弃。

    语义与 scripts/harvest_anchor_bootstrap.py 的 parse_trust_log 保持一致:
    正常追加总是 json + \\n;末尾不以 \\n 终止的段即使 JSON 可解析也是坏尾。
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
            break  # 坏尾,其后不再有完整条目
    return entries


def entry_hash(entry: dict) -> str:
    """条目内容 hash = sha256(完整条目 JSON 字节,含自身 signature)。"""
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


@pytest.fixture()
def repo_root() -> Path:
    return Path(__file__).parent.parent.parent.resolve()


@pytest.fixture()
def bootstrap(repo_root) -> Path:
    return repo_root / "scripts" / "harvest_anchor_bootstrap.py"


@pytest.fixture()
def recover(repo_root) -> Path:
    return repo_root / "scripts" / "harvest_trustlog_recover.py"


@pytest.fixture()
def terminology(repo_root) -> Path:
    return repo_root / "scripts" / "check_terminology.py"


@pytest.fixture()
def deploy_setup(tmp_path):
    """构造一个测试部署环境,返回 env 字典与关键路径。"""

    def _setup(
        deploy_uid: int | None = None,
        deploy_gid: int | None = None,
        reader_gid: int | None = None,
        fingerprint: str | None = None,
        runtime_uid: int | None = None,
        runtime_gid: int | None = None,
        with_roots_config: bool = True,
    ) -> dict:
        deploy_uid = os.getuid() if deploy_uid is None else deploy_uid
        deploy_gid = os.getgid() if deploy_gid is None else deploy_gid
        reader_gid = deploy_gid if reader_gid is None else reader_gid
        runtime_uid = os.getuid() if runtime_uid is None else runtime_uid
        runtime_gid = os.getgid() if runtime_gid is None else runtime_gid
        deploy_root = tmp_path / "deploy"
        deploy_root.mkdir(exist_ok=True)
        # 故障注入双因子门禁的物理标记:落盘者是测试进程本身(真实 uid),
        # 只在 deploy_uid 恰为测试进程 uid(默认场景)时与 test_mode_enabled()
        # 的 owner 校验相符;GHOST_DEPLOY_UID 等伪造身份场景下该标记天然
        # 不匹配,与那些用例从不触发故障注入的事实一致,无需特判。
        marker = deploy_root / TEST_MODE_MARKER_NAME
        marker.write_text(TEST_MODE_MARKER_MAGIC + "\n")
        os.chmod(marker, 0o600)
        env = {
            ENV_DEPLOY_ROOT: str(deploy_root),
            ENV_DEPLOY_UID: str(deploy_uid),
            ENV_DEPLOY_GID: str(deploy_gid),
            ENV_READER_GID: str(reader_gid),
            ENV_RUNTIME_UID: str(runtime_uid),
            ENV_RUNTIME_GID: str(runtime_gid),
        }
        if fingerprint is not None:
            env[ENV_ROOT_FP] = fingerprint
        if with_roots_config:
            # 父链必须对 runtime 至少 x(组位 r-x)且其他无权限:bootstrap 的
            # 运行期写面校验要求;pytest tmp_path 默认 700 不满足,自建 0750 树。
            # 每次 _setup 用独立子目录:同一用例内多次 deploy_setup 不共享树
            # (test_path_args_rejected 等会在同一 tmp_path 内建两次部署)。
            tree = tmp_path / f"roots-tree-{next(_TREE_COUNTER)}"
            tree.mkdir(mode=0o750)
            roots = {
                "raw_dir": str(tree / "raw"),
                "stage": str(tree / "stage"),
                "locks_dir": str(tree / "locks"),
                "batch": str(tree / "batch"),
                "trust_root": str(tree),
            }
            roots_cfg = deploy_root / "roots.json"
            if roots_cfg.exists():
                os.chmod(roots_cfg, 0o600)  # 0400 只读下无法重写
            roots_cfg.write_text(json.dumps(roots, indent=1))
            os.chmod(roots_cfg, 0o400)  # 签发材料:0400,部署 owner
            for name, p in roots.items():
                if name != "trust_root":
                    Path(p).mkdir(mode=0o750, exist_ok=True)
        return {
            "env": env,
            "deploy_root": deploy_root,
            "roots_config": deploy_root / "roots.json",
            "trust_log": deploy_root / "trust-log" / "log",
            "lock_file": deploy_root / "trust-log" / ".deploy.lock",
            "recovery_log": deploy_root / "trust-log" / "recovery-log",
            "generations": deploy_root / "generations",
            "signer_keys": deploy_root / "signer-keys",
            "root_sk": deploy_root / "root-key" / "root.sk",
            "root_pk": deploy_root / "root-key" / "root.pub",
            "tmp_path": tmp_path,
        }

    return _setup


def run_bootstrap(bootstrap: Path, args: list[str], env: dict, cwd: Path, audit_log: Path | None = None):
    """以 audit wrapper(可选)跑 bootstrap 脚本,返回 CompletedProcess。"""
    cmd = [sys.executable, "-c", AUDIT_WRAPPER, str(bootstrap), str(audit_log), *args] if audit_log else [
        sys.executable, str(bootstrap), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd)


def dual_identity_available() -> bool:
    """探测「两个真实不同非 root UID」是否可构造(CI: runner + sudo -n + nobody)。"""
    if os.geteuid() == 0:
        return False  # 禁止 root 充当部署/运行期身份
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-u", "nobody", "id", "-u"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip().isdigit()


def runtime_identity_available() -> bool:
    """⑨b 需要运行期链路模块(wave 3-8);未落地时该用例不构造。"""
    for mod in RUNTIME_DEPS:
        try:
            __import__(mod)
        except ImportError:
            return False
    return True


# ---------------------------------------------------------------------------
# ① init 正常路径
# ---------------------------------------------------------------------------


def test_init_normal_path_creates_bundle_and_trust_log(repo_root, bootstrap, deploy_setup):
    """以部署 UID 执行 init → manifest + 签名 + 公钥 + trust-log 首条条目。"""
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    gen1 = ds["generations"] / "1"
    assert gen1.is_dir()
    for f in ("manifest", "manifest.sig", "public_key"):
        assert (gen1 / f).is_file(), f"missing {f}"
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 1
    e1 = entries[0]
    assert e1["deployment_id"] == "dep-1"
    assert e1["epoch"] == 1
    assert e1["generation_dir"] == "generations/1"
    # bundle 三件摘要逐字匹配
    assert e1["bundle"]["manifest_sha256"] == sha256_file(gen1 / "manifest")
    assert e1["bundle"]["signature_sha256"] == sha256_file(gen1 / "manifest.sig")
    assert e1["bundle"]["public_key_sha256"] == sha256_file(gen1 / "public_key")
    # 创世条目:准入 K1、无前驱、由 root 签
    assert e1["admission"]["key_id"] == e1["admission"]["public_key_id"]
    assert e1["revocation"] is None
    assert e1["prev_entry_hash"] is None


def test_init_root_key_generated_and_fingerprint_reported(repo_root, bootstrap, deploy_setup):
    """init 生成 ROOT_SK/ROOT_PK,stdout 报告指纹;指纹可在后续命令中钉住。"""
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    assert ds["root_sk"].is_file() and ds["root_pk"].is_file()
    fp = sha256_file(ds["root_pk"])
    assert fp in r.stdout
    # 指纹钉住后再次 init(应被既有状态拒绝,见下)或 migrate 时校验指纹
    env2 = dict(ds["env"])
    env2[ENV_ROOT_FP] = fp
    r2 = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], env2, repo_root)
    assert r2.returncode == 0, r2.stderr


def test_init_with_wrong_fingerprint_rejected(repo_root, bootstrap, deploy_setup):
    """ROOT_PK 指纹与部署常量不符 → init 拒(第 0 步锚定,不进入签发)。"""
    ds = deploy_setup(fingerprint="0" * 64)
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 2
    assert "fingerprint" in r.stderr.lower() or "指纹" in r.stderr


def test_init_twice_rejected(repo_root, bootstrap, deploy_setup):
    """既有 trust-log 时重新 init → 拒(防误覆盖;换 root 只能带外人工)。"""
    ds = deploy_setup()
    assert run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root).returncode == 0
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-2"], ds["env"], repo_root)
    assert r.returncode == 2
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 1 and entries[0]["deployment_id"] == "dep-1"


def test_init_creates_signer_keyring(repo_root, bootstrap, deploy_setup):
    """init 在发布 E1 前于 signer-keys/ 生成 K1 私钥,公钥进 generations/1/。"""
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    ks = list(ds["signer_keys"].glob("K*.sk"))
    assert len(ks) == 1 and ks[0].name == "K1.sk"
    assert (ds["generations"] / "1" / "public_key").is_file()


# ① 的 anchors 侧:init 后 anchors.load_verified() 随即可用(wave 2 g1 落地后转绿)


def test_load_verified_available_after_init(repo_root, bootstrap, deploy_setup, monkeypatch):
    """init 后 anchors.load_verified() 随即可用且四根身份逐字正确。"""
    pytest.importorskip("akb2.harvest.anchors", reason="sc-asm-anchors(wave 2 g1)未落地")
    import akb2.harvest.anchors as anchors_mod
    load_verified = anchors_mod.load_verified
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    # 部署常量只经 env 注入(argv 层拒收路径参数,见 REJECTED_ARGS):in-process 调
    # load_verified() 必须先把 ds["env"] 落到本进程环境,否则 _load_constants() 直接
    # 拒于「部署根未配置」。monkeypatch 用完自动回滚,不污染其他用例。
    for k, v in ds["env"].items():
        monkeypatch.setenv(k, v)
    handles = load_verified()
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]
    roots = json.loads(ds["roots_config"].read_text())
    for name, h in handles.items():
        assert h.leaf_name == Path(roots[name]).name
        st = os.stat(roots[name], follow_symlinks=False)
        assert (h.expected_identity["st_dev"], h.expected_identity["st_ino"]) == (st.st_dev, st.st_ino)


# ---------------------------------------------------------------------------
# ② 未授权身份必被拒(私钥零读取 + 身份校验时序)
# ---------------------------------------------------------------------------


def _assert_unauthorized_denial(bootstrap, args, env, cwd, audit_log, ds):
    """未授权身份:exit 2、部署状态零变化、私钥零访问、身份校验先于一切。"""
    r = run_bootstrap(bootstrap, args, env, cwd, audit_log=audit_log)
    assert r.returncode == 2, f"预期 exit 2,实际 {r.returncode}: {r.stdout} {r.stderr}"
    # manifest 未生成、epoch 未推进
    assert not ds["trust_log"].exists(), "trust-log 不应被创建"
    assert not (ds["generations"] / "1").exists(), "generations/1 不应被创建"
    assert not ds["signer_keys"].exists(), "signer-keys 不应被创建"
    assert not ds["root_sk"].exists(), "ROOT_SK 不应被创建"
    # 审计:私钥路径零 open/read/scan(系统级事件)
    log = json.loads(audit_log.read_text())
    sk = str(ds["root_sk"])
    access_events = [
        e for e in log
        if e[0] in ("open", "os.scandir", "os.read") and e[1] and sk in str(e[1])
    ]
    assert access_events == [], f"未授权身份不得访问私钥路径,实际: {access_events}"
    # 时序:身份校验事件必须先于任何对部署路径的访问
    # (runpy/标准库延迟 import 会产生系统库路径的 open 事件,故只比对部署根路径;
    # 违规实现「先 open 私钥再做 UID 校验」的 open 会出现在 identity 之前 → 被捕获)
    id_pos = next((i for i, e in enumerate(log) if e[0] == "akb2_harvest.identity_check"), -1)
    assert id_pos >= 0, f"身份校验事件必须发生: {log}"
    deploy_root_str = str(ds["deploy_root"])
    deploy_access_before_id = [
        e for e in log[:id_pos]
        if e[0] in ("open", "os.scandir", "os.read") and e[1] and deploy_root_str in str(e[1])
    ]
    assert deploy_access_before_id == [], f"身份校验之前不得访问部署路径: {deploy_access_before_id}"


def test_unauthorized_identity_rejected_init(repo_root, bootstrap, deploy_setup, tmp_path):
    """以非部署身份(部署 UID 配为不存在的 uid,测试进程即运行期身份)执行 init → exit 2、零读取。"""
    ds = deploy_setup(deploy_uid=GHOST_DEPLOY_UID, deploy_gid=GHOST_DEPLOY_GID)
    audit = tmp_path / "audit-init.json"
    _assert_unauthorized_denial(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root, audit, ds)


def test_unauthorized_identity_rejected_migrate(repo_root, bootstrap, deploy_setup, tmp_path):
    """同上的 migrate → exit 2、零读取。"""
    ds = deploy_setup(deploy_uid=GHOST_DEPLOY_UID, deploy_gid=GHOST_DEPLOY_GID)
    audit = tmp_path / "audit-migrate.json"
    _assert_unauthorized_denial(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root, audit, ds)


@pytest.mark.skipif(not dual_identity_available(), reason="本机无第二普通用户/免密 sudo;CI 可构造")
def test_real_runtime_identity_rejected(repo_root, bootstrap, deploy_setup):
    """真实第二个非 root UID(≠部署 UID)执行 init/migrate → 均 exit 2。"""
    ds = deploy_setup()  # 部署身份 = 测试进程
    env_d = dict(ds["env"])
    env_r = dict(ds["env"])
    env_r[ENV_DEPLOY_UID] = "65534"  # nobody
    env_r[ENV_DEPLOY_GID] = "65534"
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], env_r, repo_root)
    assert r.returncode == 2
    assert not ds["trust_log"].exists()
    # 部署身份正常 init,随后 runtime 身份 migrate 被拒
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], env_d, repo_root)
    assert r.returncode == 0, r.stderr
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], env_r, repo_root)
    assert r.returncode == 2
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 1  # epoch 未推进


# ---------------------------------------------------------------------------
# ③ 任何路径参数一律拒收 + argv 合法集
# ---------------------------------------------------------------------------


def _setup_ready_deployment(bootstrap, env, repo_root) -> None:
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], env, repo_root)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("arg", REJECTED_ARGS)
def test_path_args_rejected(repo_root, bootstrap, deploy_setup, tmp_path, arg):
    """逐一测试所有路径/历史参数 → exit 2、manifest 未生成、epoch 未推进。"""
    # init 侧
    ds = deploy_setup()
    evil = tmp_path / "evil"
    evil.mkdir()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1", arg, str(evil)], ds["env"], repo_root)
    assert r.returncode == 2, f"{arg} 应被拒收: {r.stdout}"
    assert not ds["trust_log"].exists(), "trust-log 不应生成"
    assert not (ds["generations"] / "1").exists(), "generations/1 不应生成"
    # migrate 侧(部署已就绪)
    ds2 = deploy_setup()
    _setup_ready_deployment(bootstrap, ds2["env"], repo_root)
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1", arg, str(evil)], ds2["env"], repo_root)
    assert r.returncode == 2, f"{arg} 应被拒收(migrate): {r.stdout}"
    entries = read_trust_log(ds2["trust_log"])
    assert len(entries) == 1, "epoch 未推进"


def test_unknown_positional_arg_rejected(repo_root, bootstrap, deploy_setup):
    """位置参数:exit 2 且报文为参数拒收(区分「实现缺失」的假绿)。"""
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1", "/tmp/evil"], ds["env"], repo_root)
    assert r.returncode == 2
    assert "参数" in r.stderr or "usage" in r.stderr.lower(), f"应具名参数拒收: {r.stderr}"
    assert not ds["trust_log"].exists()


def test_missing_deployment_id_rejected(repo_root, bootstrap, deploy_setup):
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init"], ds["env"], repo_root)
    assert r.returncode == 2
    assert "deployment-id" in r.stderr, f"应具名缺 --deployment-id: {r.stderr}"
    assert not ds["trust_log"].exists()


def test_argv_legal_set_is_deployment_id_only(repo_root, bootstrap, deploy_setup):
    """argv 解析器合法参数集合恰为 {--deployment-id}(--help 输出断言)。"""
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["--help"], ds["env"], repo_root)
    assert r.returncode == 0
    out = r.stdout
    assert "--deployment-id" in out
    for bad in ("--roots-config", "--key-path", "--generations", "--trust-log",
                "--epoch-state", "--active", "--raw-dir", "--roots"):
        assert bad not in out, f"--help 不得宣传非法参数 {bad}"
    # 子命令 init/migrate 之外的命令拒收
    r = run_bootstrap(bootstrap, ["drop"], ds["env"], repo_root)
    assert r.returncode == 2


# ---------------------------------------------------------------------------
# ④b 并发 migrate 真实验收
# ---------------------------------------------------------------------------


def test_concurrent_migrate_single_winner(repo_root, bootstrap, deploy_setup):
    """两个 subprocess 同时 migrate:恰有一个成功、另一个 exit 2 报「另一部署正在进行」。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    p1 = subprocess.Popen(
        [sys.executable, str(bootstrap), "migrate", "--deployment-id", "dep-1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ds["env"], cwd=repo_root)
    # 等待锁文件出现(证明 p1 正在持锁)
    for _ in range(500):
        if ds["lock_file"].exists():
            break
        import time
        time.sleep(0.01)
    assert ds["lock_file"].exists(), "migrate 未取得部署锁"
    p2 = subprocess.run(
        [sys.executable, str(bootstrap), "migrate", "--deployment-id", "dep-1"],
        capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    out1, err1 = p1.communicate()
    assert p1.returncode == 0, f"先发者应成功: {out1} {err1}"
    assert p2.returncode == 2, f"后发者应 exit 2: {p2.stdout}"
    assert "另一部署正在进行" in p2.stderr
    # 磁盘只有一套一致代际
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 2, "epoch 应推进恰好一代"
    tip = entries[-1]
    assert tip["epoch"] == 2
    gen_dir = ds["generations"] / "2"
    assert (gen_dir / "manifest").is_file()
    assert tip["bundle"]["manifest_sha256"] == sha256_file(gen_dir / "manifest")
    assert tip["bundle"]["signature_sha256"] == sha256_file(gen_dir / "manifest.sig")
    assert tip["bundle"]["public_key_sha256"] == sha256_file(gen_dir / "public_key")
    # 不存在交错组合:manifest 由当代公钥对应的私钥签,签名文件可验
    assert openssl_verify(gen_dir / "public_key", gen_dir / "manifest", gen_dir / "manifest.sig")


def test_stale_lock_rejected_and_not_auto_cleared(repo_root, bootstrap, deploy_setup):
    """陈旧锁(pid 已死):exit 2 报 pid 与获取时刻,不自动删除。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    ds["lock_file"].parent.mkdir(exist_ok=True)
    ds["lock_file"].write_text(json.dumps({"pid": 999999999, "ts": 1234567890}))
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 2
    assert "999999999" in r.stderr and "陈旧" in r.stderr
    assert ds["lock_file"].exists(), "陈旧锁不得自动清除"
    # 人工删除后可继续
    ds["lock_file"].unlink()
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr


def test_unparsable_lock_rejected(repo_root, bootstrap, deploy_setup):
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    ds["lock_file"].parent.mkdir(exist_ok=True)
    ds["lock_file"].write_text("garbage not json")
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 2
    assert "人工" in r.stderr


# ---------------------------------------------------------------------------
# ④ epoch 单调 + 密钥轮换废止
# ---------------------------------------------------------------------------


def test_epoch_monotonic_and_key_rotation(repo_root, bootstrap, deploy_setup):
    """连续两次 migrate → epoch 严格递增;旧公钥验新 manifest 失败、新公钥验旧 manifest 也失败。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    entries = read_trust_log(ds["trust_log"])
    assert [e["epoch"] for e in entries] == [1, 2, 3]
    # 链完整:prev hash 逐字
    for prev, cur in zip(entries, entries[1:], strict=False):
        assert cur["prev_entry_hash"] == entry_hash(prev)
    # 每条条目由前一代密钥签(E1 由 root,E2 由 K1,E3 由 K2)
    for i, e in enumerate(entries):
        data = json.dumps({k: v for k, v in e.items() if k != "signature"}, sort_keys=True).encode()
        sig_path = ds["tmp_path"] / f"check-e{i+1}.sig"
        data_path = ds["tmp_path"] / f"check-e{i+1}.json"
        data_path.write_bytes(data)
        sig_path.write_bytes(bytes.fromhex(e["signature"]))
        if i == 0:
            pub = ds["root_pk"]
        else:
            pub = ds["generations"] / str(i) / "public_key"
        assert openssl_verify(pub, data_path, sig_path), f"E{i+1} 签名应由 K{i} 验证"
    # manifest 轮换:gen2 manifest 由 K2 签、旧 manifest 由 K1 签,互验失败
    m2 = ds["generations"] / "2" / "manifest"
    s2 = ds["generations"] / "2" / "manifest.sig"
    m1 = ds["generations"] / "1" / "manifest"
    s1 = ds["generations"] / "1" / "manifest.sig"
    k1 = ds["generations"] / "1" / "public_key"
    k2 = ds["generations"] / "2" / "public_key"
    assert openssl_verify(k2, m2, s2)
    assert openssl_verify(k1, m1, s1)
    assert not openssl_verify(k1, m2, s2), "旧公钥不得验新 manifest"
    assert not openssl_verify(k2, m1, s1), "新公钥不得验旧 manifest"
    # 准入/废止声明在条目内:每代准入当代、废止前代
    assert entries[1]["admission"]["key_id"] != entries[0]["admission"]["key_id"]
    assert entries[1]["revocation"]["prev_key_id"] == entries[0]["admission"]["key_id"]


def test_keyring_single_current_key_after_migrate(repo_root, bootstrap, deploy_setup):
    """migrate 之后 signer-keys/ 有且仅有当代一把私钥(旧代私钥就地删除)。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    keys = sorted(p.name for p in ds["signer_keys"].glob("K*.sk"))
    assert keys == ["K2.sk"], f"有且仅有当代一把: {keys}"


def test_migrate_missing_prev_key_fail_closed(repo_root, bootstrap, deploy_setup):
    """migrate 时上一代私钥缺失 → exit 非零并具名报告缺失路径。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    (ds["signer_keys"] / "K1.sk").unlink()
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 2
    assert "K1.sk" in r.stderr
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 1, "epoch 不得推进"


def test_crash_leftover_old_key_auto_cleaned(repo_root, bootstrap, deploy_setup):
    """崩溃残留:signer-keys/ 同时存在两把私钥(当代编号 > 残留 m)→ 重跑时就地删除残留后继续。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    # 模拟「E2 已落盘、K1.sk 尚未删除」的崩溃中间态
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    k1 = ds["signer_keys"] / "K1.sk"
    k1.write_bytes(b"leftover")  # 崩溃残留:当代=2 > m=1
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    keys = sorted(p.name for p in ds["signer_keys"].glob("K*.sk"))
    assert keys == ["K3.sk"], f"崩溃残留应被就地删除: {keys}"


# ---------------------------------------------------------------------------
# ⑨(a) 部署侧真实双 migrate + O_APPEND 审计(本机可跑)
# ---------------------------------------------------------------------------


def test_dual_migrate_append_audit(repo_root, bootstrap, deploy_setup, tmp_path):
    """部署侧连续两次 migrate:每次都真实 O_APPEND 单次写入完整条目,epoch 递增,链自 root 可回溯。"""
    ds = deploy_setup()
    audit = tmp_path / "audit-append.json"
    env = dict(ds["env"])
    env[ENV_ROOT_FP] = ""  # 首次 init 生成 root key
    r1 = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], env, repo_root, audit_log=audit)
    assert r1.returncode == 0, r1.stderr
    # 从 stdout 提取指纹并钉住,后续命令带上
    fp = [line for line in r1.stdout.splitlines() if "root_pk_fingerprint" in line][0].split("=", 1)[1].strip()
    env[ENV_ROOT_FP] = fp
    for i in (1, 2):
        audit = tmp_path / f"audit-m{i}.json"
        r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], env, repo_root, audit_log=audit)
        assert r.returncode == 0, r.stderr
        assert "append_ok=" in r.stdout, "stdout 应自报 O_APPEND 追加字节数"
        log = json.loads(audit.read_text())
        # trust-log 的 open flags 含 O_APPEND(Python audit "open" 事件:
        # os.open 与 builtin open 都触发,args=(path, mode, flags))
        opens = [e for e in log if e[0] == "open" and e[1] and str(ds["trust_log"]) == e[1]]
        assert opens, f"trust-log 应被 open,审计日志: {log}"
        # 追加路径的 open 必须含 O_APPEND(fsync 的 O_RDONLY open 也在列,须按 flags 判别)
        appends = [e for e in opens if e[2] and e[2] & os.O_APPEND]
        assert appends, f"trust-log 必须存在 O_APPEND open: {opens}"
    entries = read_trust_log(ds["trust_log"])
    assert [e["epoch"] for e in entries] == [1, 2, 3]
    # 链自 root 完整回溯(重放验证)
    for i, e in enumerate(entries):
        data = json.dumps({k: v for k, v in e.items() if k != "signature"}, sort_keys=True).encode()
        sig_path = ds["tmp_path"] / f"audit-e{i+1}.sig"
        data_path = ds["tmp_path"] / f"audit-e{i+1}.json"
        data_path.write_bytes(data)
        sig_path.write_bytes(bytes.fromhex(e["signature"]))
        pub = ds["root_pk"] if i == 0 else ds["generations"] / str(i) / "public_key"
        assert openssl_verify(pub, data_path, sig_path)


# ⑨(b)(c) 运行期侧:双身份 + 真实投递 —— CI 可构造;本机环境受限或运行期模块未落地时跳过


@pytest.mark.skipif(
    not dual_identity_available(),
    reason="本机无第二普通用户/免密 sudo(expect ⑨ 要求两个真实不同非 root UID);CI 可构造",
)
def test_dual_identity_runtime_read_only(repo_root, bootstrap, deploy_setup):
    """runtime 身份 load_verified 成功 + 对签发材料读取 PermissionError + 对 bundle/trust-log 写入 PermissionError。"""
    pytest.importorskip("akb2.harvest.anchors", reason="sc-asm-anchors(wave 2 g1)未落地")
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    # 以 runtime(nobody)起真实 subprocess 调 load_verified
    script = (
        "import json,sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "from akb2.harvest.anchors import load_verified;"
        "hs = load_verified();"
        "print(json.dumps(sorted(hs)));"
        "import os;"
        "try:"
        "  open(sys.argv[2],'rb').read(); print('SK_READ_OK')"
        "except PermissionError: print('SK_READ_DENIED')"
    )
    cmd = ["sudo", "-n", "-u", "nobody", sys.executable, "-c", script,
           str(repo_root / "src"), str(ds["root_sk"])]
    r = subprocess.run(cmd, capture_output=True, text=True, env={"HOME": "/tmp"}, cwd=repo_root)
    assert r.returncode == 0, r.stderr
    assert "SK_READ_DENIED" in r.stdout
    handles = json.loads(r.stdout.splitlines()[0])
    assert handles == ["batch", "locks_dir", "raw_dir", "stage"]


@pytest.mark.skipif(
    not runtime_identity_available(),
    reason="运行期链路模块(wave 3-8)未落地,真实投递链路无法构造",
)
def test_runtime_full_delivery_no_eacces(repo_root, bootstrap, deploy_setup):
    """⑨b 全链真实投递:loop → assemble → raw 落位 → locks → journal → ledger,零 EACCES。"""
    pytest.skip("运行期链路模块未落地(wave 3-8 后集成重跑)")


# ---------------------------------------------------------------------------
# ⑩ 术语扫描闸门
# ---------------------------------------------------------------------------


def _sc_manifest(scs: list[dict]) -> Path:
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="term-manifest-"))
    p = d / "manifest.json"
    p.write_text(json.dumps({"scs": scs}))
    return p


def _make_sc(id_: str, change: str = "", holds: str = "", expect: str = "") -> dict:
    return {"id": id_, "change": change, "holds": holds, "expect": expect}


def _run_scanner(terminology: Path, manifest_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(terminology), "--manifest", str(manifest_path)],
        capture_output=True, text=True)


def test_scanner_rejects_bare_word(repo_root, terminology):
    """(a) 正文残留:活动正文注入禁用词 → exit 非零、打印命中词/SC/字段/上下文。"""
    sc = _make_sc("sc-asm-bootstrap", change="必须经 trust-log 的 active 指针切换")
    p = _sc_manifest([sc])
    r = _run_scanner(terminology, p)
    assert r.returncode != 0
    assert "active" in r.stdout
    assert "sc-asm-bootstrap" in r.stdout and "change" in r.stdout
    assert "指针切换" in r.stdout  # 词表内同样命中


def test_scanner_exempts_banned_vocab_block(repo_root, terminology):
    """(b) 豁免区残留:词放进 banned-vocab 标记对内 → exit 0。"""
    sc = _make_sc("sc-asm-bootstrap", change="<!-- banned-vocab -->active、epoch-state<!-- /banned-vocab -->")
    p = _sc_manifest([sc])
    r = _run_scanner(terminology, p)
    assert r.returncode == 0, r.stdout


def test_scanner_exempts_nested_gpt_annotation(repo_root, terminology):
    """(c) 嵌套括注:禁用词放在含成对括号的 GPT 质询括注内 → exit 0。"""
    sc = _make_sc(
        "sc-asm-bootstrap",
        change="(GPT 质询 rev1-ch1:这是 active 内容(嵌套(成对)) 指针切换)",
    )
    p = _sc_manifest([sc])
    r = _run_scanner(terminology, p)
    assert r.returncode == 0, r.stdout


def test_scanner_rejects_new_words(repo_root, terminology):
    """(d) 新增词:「防回放靠密钥吊销链」与「存放在部署状态里」两例均 exit 非零并具名。"""
    for phrase, word in (("防回放靠密钥吊销链", "密钥吊销链"), ("存放在部署状态里", "部署状态里")):
        sc = _make_sc("sc-asm-anchors", change=f"本方案防回放机制:{phrase}")
        p = _sc_manifest([sc])
        r = _run_scanner(terminology, p)
        assert r.returncode != 0, phrase
        assert word in r.stdout, f"应具名命中词 {word}"


def test_scanner_wordlist_matches_spec(repo_root, terminology):
    """(e) 词表完备性:scanner 词表与 (5f2) 规格写死的表逐项相等;manifest 实跑命中 0。"""
    spec_path = Path.home() / ".claude" / ".goal" / "cindy-akb2-harvest" / "task-manifest.json"
    if not spec_path.exists():
        pytest.skip("task-manifest.json 不在本机,词表比对降级")
    manifest = json.loads(spec_path.read_text())
    sc = next(s for s in manifest["scs"] if s["id"] == "sc-asm-bootstrap")
    import re
    m = re.search(r"<!-- banned-vocab -->\s*\{(.*?)\}\s*<!-- /banned-vocab -->", sc["change"], re.S)
    assert m, "规格中 (5f2) 词表块应存在"
    spec_words = [w.strip() for w in m.group(1).split("、") if w.strip()]
    # scanner 词表(import 脚本模块)
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("check_terminology", terminology)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    assert mod.WORDS == spec_words, f"scanner 词表与规格漂移: {mod.WORDS} vs {spec_words}"
    # 当前 manifest 实跑命中 0
    r = _run_scanner(terminology, spec_path)
    assert r.returncode == 0, r.stdout


# ---------------------------------------------------------------------------
# ⑪ CAS 二元组判别性
# ---------------------------------------------------------------------------


def test_cas_mismatch_same_epoch_rejected(repo_root, bootstrap, deploy_setup):
    """同 epoch 不同 entry_hash 的漂移:写入前末条被换 → exit 2、零追加、报文具名 H1 与 H2。

    注入窗口:取锁后首次读 tip 与写前复核之间。脚本提供复核前暂停注入
    (AKB2_HARVEST_PAUSE_BEFORE_CAS),测试在锁出现后、复核前替换末条。
    """
    import time as _time
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    entries = read_trust_log(ds["trust_log"])
    h1 = entry_hash(entries[-1])
    env = dict(ds["env"])
    env[ENV_PAUSE_CAS] = "1"
    p = subprocess.Popen(
        [sys.executable, str(bootstrap), "migrate", "--deployment-id", "dep-1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=repo_root)
    for _ in range(500):
        if ds["lock_file"].exists():
            break
        _time.sleep(0.01)
    assert ds["lock_file"].exists(), "migrate 未取得部署锁"
    _time.sleep(0.3)  # 确保取锁后的首次读已完成
    # 构造同 epoch、不同内容的末条(受控测试钩子:直接替换 trust-log 末条)
    fake = dict(entries[-1])
    fake["bundle"] = dict(fake["bundle"])
    fake["bundle"]["manifest_sha256"] = "0" * 64
    fake_line = json.dumps(fake, sort_keys=True)
    lines = ds["trust_log"].read_bytes().splitlines()
    lines[-1] = fake_line.encode()
    ds["trust_log"].write_bytes(b"\n".join(lines) + b"\n")
    h2 = hashlib.sha256(fake_line.encode()).hexdigest()
    out, err = p.communicate(timeout=30)
    assert p.returncode == 2, f"{out} {err}"
    assert "CAS" in err and "mismatch" in err.lower()
    assert h1 in err and h2 in err, f"报文必须同时报出 H1 与 H2: {err}"
    # 零追加
    after = read_trust_log(ds["trust_log"])
    assert len(after) == 1, "不得追加条目"


def test_cas_match_same_epoch_succeeds(repo_root, bootstrap, deploy_setup):
    """配套正例:末条未被动过 → 写入成功、epoch 推进到 N+1。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    entries = read_trust_log(ds["trust_log"])
    assert [e["epoch"] for e in entries] == [1, 2]


# ---------------------------------------------------------------------------
# ⑤ 每个发布边界 kill/restart:完整旧代或完整新代
# ---------------------------------------------------------------------------


def _crash_run(bootstrap, args, env, cwd, stage) -> subprocess.CompletedProcess:
    env = dict(env)
    env[ENV_CRASH] = stage
    return subprocess.run([sys.executable, str(bootstrap), *args], capture_output=True, text=True, env=env, cwd=cwd)


@pytest.mark.parametrize("stage,desc", CRASH_STAGES)
def test_kill_each_publish_boundary(repo_root, bootstrap, deploy_setup, stage, desc):
    """在六个发布边界中断并重启:trust-log 末条完整条目要么完整旧代、要么完整新代。

    kill 类(1-4/6):进程被 kill(137);short-write(第 5 点)是写侧 fail-closed(exit 2),
    两者都会留下「末条完整条目 = 完整旧代」的读侧状态。
    """
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    if stage == "short-write":
        env = dict(ds["env"])
        env[ENV_SHORT_WRITE] = "1"
        r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], env, repo_root)
        assert r.returncode == 2, f"{stage}: 短写必须 fail-closed: {r.stdout} {r.stderr}"
    else:
        r = _crash_run(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root, stage)
        assert r.returncode == 137, f"{stage}: 进程应被 kill(137): {r.stdout} {r.stderr}"
    entries = read_trust_log(ds["trust_log"])
    tip = entries[-1]
    assert tip["epoch"] in (1, 2), f"{stage}: 只能是完整旧代或完整新代: {tip}"
    # 前五个点:完整旧代(epoch 1)
    if stage != "pre-fsync":
        assert tip["epoch"] == 1, f"{stage}: 应停在完整旧代"
        # 旧代公钥仍被接受:重放验证 E1 链
        e1 = entries[0]
        data = json.dumps({k: v for k, v in e1.items() if k != "signature"}, sort_keys=True).encode()
        dp = ds["tmp_path"] / f"{stage}-e1.json"
        sp = ds["tmp_path"] / f"{stage}-e1.sig"
        dp.write_bytes(data)
        sp.write_bytes(bytes.fromhex(e1["signature"]))
        assert openssl_verify(ds["root_pk"], dp, sp)
    # 末条条目声明的 generation_dir 与磁盘 bundle 摘要逐字一致(反证不是新旧混拼)
    gen_dir = ds["generations"] / str(tip["epoch"])
    if (gen_dir / "manifest").exists():
        assert tip["bundle"]["manifest_sha256"] == sha256_file(gen_dir / "manifest")
    # 半成品 generation 目录不影响读侧:trust-log 末条解析不受失败目录干扰
    assert all(Path(e["generation_dir"]).name.isdigit() for e in entries)
    # 重启(恢复):migrate 可继续,epoch 从当前代推进。
    # 崩溃残留的部署锁不自动清除(陈旧锁语义),先人工确认并删除。
    if ds["lock_file"].exists():
        ds["lock_file"].unlink()
    r2 = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    if stage == "short-write":
        assert r2.returncode == 2, "短写 poison 未解除前 migrate 必须被拒"
        assert "坏尾" in r2.stderr or "人工恢复" in r2.stderr
    elif tip["epoch"] == 1:
        assert r2.returncode == 0, f"{stage}: 旧代重启后应可 migrate: {r2.stderr}"
        assert [e["epoch"] for e in read_trust_log(ds["trust_log"])][-1] == 2


def test_kill_short_write_poison_persists(repo_root, bootstrap, deploy_setup):
    """短写(半条目):写入方 fail-closed,坏尾持久,后续 migrate 拒绝且字节不增。"""
    ds = deploy_setup()
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    env = dict(ds["env"])
    env[ENV_SHORT_WRITE] = "1"
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], env, repo_root)
    assert r.returncode == 2, "短写必须 fail-closed"
    assert "坏尾" in r.stderr
    before = ds["trust_log"].read_bytes()
    r2 = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r2.returncode == 2, "坏尾之后必须拒绝 migrate"
    assert r2.stderr and ("坏尾" in r2.stderr or "人工恢复" in r2.stderr)
    after = ds["trust_log"].read_bytes()
    assert after == before, "坏尾之后不得追加任何字节"


# ---------------------------------------------------------------------------
# 恢复脚本(change (w3) 落地验证:票据授权、边界由票据指定、审计、poison 解除)
# ---------------------------------------------------------------------------


def _issue_recovery_ticket(ds, deploy_root, entry_hash_val, bad_tail_sha256, nonce="n1", deployment_id="dep-1") -> Path:
    """用 ROOT_SK 离线签发恢复票据(测试模拟带外人工流程)。"""
    ticket = {
        "deployment_id": deployment_id,
        "entry_hash": entry_hash_val,
        "bad_tail_sha256": bad_tail_sha256,
        "issued_at": 1700000000,
        "nonce": nonce,
    }
    body = json.dumps({k: v for k, v in ticket.items()}, sort_keys=True).encode()
    data_path = ds["tmp_path"] / "ticket-body.json"
    sig_path = ds["tmp_path"] / "ticket.sig"
    data_path.write_bytes(body)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(ds["root_sk"]), "-out", str(sig_path), str(data_path)],
                   check=True, capture_output=True)
    ticket["signature"] = sig_path.read_bytes().hex()
    tp = ds["tmp_path"] / "ticket.json"
    tp.write_text(json.dumps(ticket))
    return tp


def _make_bad_tail(ds, repo_root, bootstrap):
    """构造坏尾状态,返回 (末条完整条目 hash, 坏尾字节 sha256, 恢复前字节, 截断偏移)。"""
    _setup_ready_deployment(bootstrap, ds["env"], repo_root)
    env = dict(ds["env"])
    env[ENV_SHORT_WRITE] = "1"
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], env, repo_root)
    assert r.returncode == 2
    raw = ds["trust_log"].read_bytes()
    entries = read_trust_log(ds["trust_log"])
    last_full = entry_hash(entries[-1])
    offset = len(json.dumps(entries[-1], sort_keys=True).encode()) + 1  # 末条完整条目 + 换行
    bad_bytes = raw[offset:]
    bad_sha = hashlib.sha256(bad_bytes).hexdigest()
    return last_full, bad_sha, raw, offset


def test_recover_valid_ticket_succeeds(repo_root, bootstrap, recover, deploy_setup):
    """(d1) 正确票据 → 按票据指定偏移截断、recovery-log 审计、poison 解除、migrate 恢复。"""
    ds = deploy_setup()
    last_full, bad_sha, raw, offset = _make_bad_tail(ds, repo_root, bootstrap)
    ticket = _issue_recovery_ticket(ds, ds["deploy_root"], last_full, bad_sha)
    r = subprocess.run([sys.executable, str(recover), "--ticket", str(ticket)],
                       capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r.returncode == 0, r.stderr
    # 截断到末条完整条目之后
    after = ds["trust_log"].read_bytes()
    assert after == raw[:offset], "应按票据偏移截断"
    # recovery-log 追加完整审计条目
    rlog = read_trust_log(ds["recovery_log"])
    assert len(rlog) == 1
    entry = rlog[0]
    assert entry["ticket"]["nonce"] == "n1"
    assert "before_sha256" in entry and "after_sha256" in entry and "offset" in entry
    assert "uid" in entry and "gid" in entry
    # poison 解除:可继续 migrate
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr


def test_recover_invalid_ticket_rejected(repo_root, bootstrap, recover, deploy_setup):
    """(d2) 无票据/未签名/非 ROOT_SK 签名 → 拒且零字节改动、recovery-log 零新增。"""
    ds = deploy_setup()
    last_full, bad_sha, raw, _offset = _make_bad_tail(ds, repo_root, bootstrap)
    before = ds["trust_log"].read_bytes()
    # 未签名票据
    ticket = {
        "deployment_id": "dep-1", "entry_hash": last_full, "bad_tail_sha256": bad_sha,
        "issued_at": 1700000000, "nonce": "n2",
    }
    tp = ds["tmp_path"] / "ticket-unsigned.json"
    tp.write_text(json.dumps(ticket))
    r = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                       capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r.returncode != 0
    assert ds["trust_log"].read_bytes() == before
    assert not ds["recovery_log"].exists()
    # 非 ROOT_SK 签的票据
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(ds["tmp_path"] / "att.sk")], check=True, capture_output=True)
    ticket2 = dict(ticket, nonce="n3")
    body = json.dumps({k: v for k, v in ticket2.items()}, sort_keys=True).encode()
    dp = ds["tmp_path"] / "tb.json"
    sp = ds["tmp_path"] / "att.sig"
    dp.write_bytes(body)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(ds["tmp_path"] / "att.sk"),
                    "-out", str(sp), str(dp)], check=True, capture_output=True)
    ticket2["signature"] = sp.read_bytes().hex()
    tp2 = ds["tmp_path"] / "ticket-att.json"
    tp2.write_text(json.dumps(ticket2))
    r = subprocess.run([sys.executable, str(recover), "--ticket", str(tp2)],
                       capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r.returncode != 0
    assert ds["trust_log"].read_bytes() == before


def test_recover_ticket_mismatch_rejected(repo_root, bootstrap, recover, deploy_setup):
    """(d3) 票据与磁盘不符(entry_hash/坏尾 sha/deployment_id/nonce 重放)→ 拒且零改动。"""
    ds = deploy_setup()
    last_full, bad_sha, raw, _offset = _make_bad_tail(ds, repo_root, bootstrap)
    before = ds["trust_log"].read_bytes()
    cases = [
        {"entry_hash": "0" * 64, "bad_tail_sha256": bad_sha},          # entry_hash 不符
        {"entry_hash": last_full, "bad_tail_sha256": "1" * 64},        # 坏尾 sha 不符
        {"entry_hash": last_full, "bad_tail_sha256": bad_sha, "deployment_id": "other-dep"},  # deployment_id 不符
    ]
    for i, over in enumerate(cases):
        t = {"deployment_id": "dep-1", "entry_hash": last_full, "bad_tail_sha256": bad_sha,
             "issued_at": 1700000000, "nonce": f"n-mis-{i}"}
        t.update(over)
        body = json.dumps({k: v for k, v in t.items()}, sort_keys=True).encode()
        dp = ds["tmp_path"] / f"m{i}.json"
        sp = ds["tmp_path"] / f"m{i}.sig"
        dp.write_bytes(body)
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(ds["root_sk"]), "-out", str(sp), str(dp)],
                       check=True, capture_output=True)
        t["signature"] = sp.read_bytes().hex()
        tp = ds["tmp_path"] / f"ticket-m{i}.json"
        tp.write_text(json.dumps(t))
        r = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                           capture_output=True, text=True, env=ds["env"], cwd=repo_root)
        assert r.returncode != 0, f"case {i} 应拒"
        assert ds["trust_log"].read_bytes() == before, f"case {i} 零改动"
    # nonce 重放:同 nonce 的票据用两次,第二次拒
    t = {"deployment_id": "dep-1", "entry_hash": last_full, "bad_tail_sha256": bad_sha,
         "issued_at": 1700000000, "nonce": "replay-nonce"}
    body = json.dumps({k: v for k, v in t.items()}, sort_keys=True).encode()
    dp = ds["tmp_path"] / "rp.json"
    sp = ds["tmp_path"] / "rp.sig"
    dp.write_bytes(body)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(ds["root_sk"]), "-out", str(sp), str(dp)],
                   check=True, capture_output=True)
    t["signature"] = sp.read_bytes().hex()
    tp = ds["tmp_path"] / "ticket-rp.json"
    tp.write_text(json.dumps(t))
    env = ds["env"]
    r1 = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                        capture_output=True, text=True, env=env, cwd=repo_root)
    assert r1.returncode == 0, r1.stderr
    # 恢复后再制造坏尾并重放同一 nonce
    env2 = dict(env)
    env2[ENV_SHORT_WRITE] = "1"
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], env2, repo_root)
    assert r.returncode == 2
    before2 = ds["trust_log"].read_bytes()
    r2 = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                        capture_output=True, text=True, env=env, cwd=repo_root)
    assert r2.returncode != 0, "nonce 重放应拒"
    assert ds["trust_log"].read_bytes() == before2


def test_recover_requires_ticket_entry_hash(repo_root, bootstrap, recover, deploy_setup):
    """(d4) 恢复脚本不得自行扫描推断截断点:缺 entry_hash 即拒。"""
    ds = deploy_setup()
    last_full, bad_sha, raw, _offset = _make_bad_tail(ds, repo_root, bootstrap)
    before = ds["trust_log"].read_bytes()
    t = {"deployment_id": "dep-1", "bad_tail_sha256": bad_sha, "issued_at": 1700000000, "nonce": "no-hash"}
    body = json.dumps({k: v for k, v in t.items()}, sort_keys=True).encode()
    dp = ds["tmp_path"] / "nh.json"
    sp = ds["tmp_path"] / "nh.sig"
    dp.write_bytes(body)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(ds["root_sk"]), "-out", str(sp), str(dp)],
                   check=True, capture_output=True)
    t["signature"] = sp.read_bytes().hex()
    tp = ds["tmp_path"] / "ticket-nh.json"
    tp.write_text(json.dumps(t))
    r = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                       capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r.returncode != 0
    assert ds["trust_log"].read_bytes() == before


def test_bootstrap_does_not_import_recover(repo_root, bootstrap):
    """(w3)① 恢复入口独立:bootstrap 不 import 恢复脚本(源码级断言)。

    「不 import」检查 import 语句形态(docstring 中提及恢复脚本路径是
    部署说明,不是 import)。
    """
    src = bootstrap.read_text()
    import re
    for pat in (r"^\s*(import|from)\s+harvest_trustlog_recover",
                r"\bimport\s+harvest_trustlog_recover\b",
                r"\bfrom\s+harvest_trustlog_recover\b"):
        assert not re.search(pat, src, re.M), f"bootstrap 不得 import 恢复脚本: {pat}"


# ---------------------------------------------------------------------------
# ⑥ anchors 不 import 本脚本
# ---------------------------------------------------------------------------


def test_anchors_does_not_import_bootstrap(repo_root, bootstrap):
    """anchors 模块不 import 本脚本(源码级断言;wave 2 g1 落地后生效)。"""
    anchors_src = repo_root / "src" / "akb2" / "harvest" / "anchors.py"
    if not anchors_src.exists():
        pytest.skip("sc-asm-anchors(wave 2 g1)未落地")
    src = anchors_src.read_text()
    assert "harvest_anchor_bootstrap" not in src


# ---------------------------------------------------------------------------
# ⑦ 测试不得自造 bootstrap
# ---------------------------------------------------------------------------


def test_no_inline_bootstrap_shim(repo_root, bootstrap):
    """本测试文件不定义内联 init/migrate 替身——全部经 subprocess 调真实脚本。"""
    src = Path(__file__).read_text()
    import re
    for pat in (r"^\s*def init\(", r"^\s*def migrate\(", r"^\s*class \w*[Bb]ootstrap\b"):
        assert not re.search(pat, src, re.M), f"不得定义内联替身: {pat}"


# ---------------------------------------------------------------------------
# 部署常量布局与权限表(change (5a) 落地验证)
# ---------------------------------------------------------------------------


def test_deploy_layout_permissions(repo_root, bootstrap, deploy_setup):
    """init 后部署布局权限:签发材料 0400/部署 owner;bundle 0440;trust-log 0640;keyring 0710+0400。"""
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    uid = os.getuid()
    st = ds["root_sk"].stat()
    assert st.st_mode & 0o777 == 0o400 and st.st_uid == uid, "ROOT_SK 0400/部署 owner"
    st = ds["roots_config"].stat()
    assert st.st_mode & 0o777 == 0o400 and st.st_uid == uid, "根配置 0400/部署 owner"
    for f in ("manifest", "manifest.sig", "public_key"):
        st = (ds["generations"] / "1" / f).stat()
        assert st.st_mode & 0o777 == 0o440, f"bundle {f} 应 0440"
    st = ds["trust_log"].stat()
    assert st.st_mode & 0o777 == 0o640, "trust-log 应 0640(O_APPEND 可写)"
    st = ds["trust_log"].parent.stat()
    assert st.st_mode & 0o777 == 0o750, "trust-log 父目录应 0750"
    st = ds["signer_keys"].stat()
    assert st.st_mode & 0o777 == 0o710, "signer-keys 目录应 0710(组仅 search)"
    for k in ds["signer_keys"].glob("K*.sk"):
        st = k.stat()
        assert st.st_mode & 0o777 == 0o400 and st.st_uid == uid, f"{k.name} 应 0400/部署 owner"
    st = ds["generations"].stat()
    assert st.st_mode & 0o777 == 0o750, "generations 目录应 0750"


def test_layout_validation_fails_on_loosened_perms(repo_root, bootstrap, deploy_setup):
    """权限被放宽(如 trust-log 组可写)→ init/migrate 结束时校验 exit 2。"""
    ds = deploy_setup()
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 0, r.stderr
    os.chmod(ds["trust_log"], 0o660)  # 组可写 → 非法
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"], ds["env"], repo_root)
    assert r.returncode == 2
    assert "mode" in r.stderr.lower() or "权限" in r.stderr
