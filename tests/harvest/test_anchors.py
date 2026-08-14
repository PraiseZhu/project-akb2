"""sc-asm-anchors 验收测试:harvest 四根受信锚的读侧身份底座。

覆盖 expect ①-⑨ 全部验收用例。纪律:
- 预测红集用例先写成红(断言对错误实现的判别性),由实现转绿。
- 正例部署动作一律走 scripts/harvest_anchor_bootstrap.py 真实入口;
  对抗输入(回放/伪造/候选条目/票据)由测试用 bootstrap 签名原语构造。
- 双身份(两个真实不同非 root UID)用例本机不可构造(本机唯一普通用户、
  sudo 需密码、非 root 无法 setuid),写成环境探测:CI(免密 sudo + nobody)
  可跑,本机显式 skip 并在报告标注(与 sc-asm-bootstrap 同款处理)。

协议与 scripts/harvest_anchor_bootstrap.py 逐字对齐:
- env 注入:AkB2_HARVEST_DEPLOY_ROOT / DEPLOY_UID / DEPLOY_GID /
  RUNTIME_READER_GID / RUNTIME_UID / RUNTIME_GID / ROOT_PK_FINGERPRINT
- 布局:roots.json、root-key/{root.sk,root.pub}、trust-log/{log,.deploy.lock,
  recovery-log}、generations/<epoch>/{manifest,manifest.sig,public_key}、
  signer-keys/K<n>.sk
- trust-log:JSON lines;条目链 E1 由 ROOT_SK 签、E_n 由 K_{n-1} 签。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from itertools import count
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 常量(与 bootstrap 脚本逐字一致)
# ---------------------------------------------------------------------------

ENV_DEPLOY_ROOT = "AKB2_HARVEST_DEPLOY_ROOT"
ENV_DEPLOY_UID = "AKB2_HARVEST_DEPLOY_UID"
ENV_DEPLOY_GID = "AKB2_HARVEST_DEPLOY_GID"
ENV_READER_GID = "AKB2_HARVEST_RUNTIME_READER_GID"
ENV_RUNTIME_UID = "AKB2_HARVEST_RUNTIME_UID"
ENV_RUNTIME_GID = "AKB2_HARVEST_RUNTIME_GID"
ENV_ROOT_FP = "AKB2_HARVEST_ROOT_PK_FINGERPRINT"
ENV_SHORT_WRITE = "AKB2_HARVEST_SIMULATE_SHORT_WRITE"

# 故障注入双因子门禁的物理标记(与 scripts/harvest_anchor_bootstrap.py 逐字一致)
TEST_MODE_MARKER_NAME = ".akb2-harvest-test-mode"
TEST_MODE_MARKER_MAGIC = "AKB2_HARVEST_TEST_MODE_V1"

_TREE_COUNTER = count(1)

BOOTSTRAP_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "harvest_anchor_bootstrap.py"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def entry_hash(entry: dict) -> str:
    return sha256_bytes(json.dumps(entry, sort_keys=True).encode())


def read_trust_log(path: Path) -> list[dict]:
    """解析 trust-log 为条目列表;坏尾(JSON 失败或末尾无换行终止)丢弃。"""
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


def truncate_trust_log_to(path: Path, entry: dict) -> None:
    """把 trust-log 截断到指定条目末尾(移除其后追加的候选条目)。"""
    raw = path.read_bytes()
    offset = 0
    for raw_line in raw.split(b"\n"):
        line = raw_line.strip()
        if not line:
            offset += len(raw_line) + 1
            continue
        try:
            cur = json.loads(line)
        except json.JSONDecodeError:
            break
        offset += len(raw_line) + 1
        if entry_hash(cur) == entry_hash(entry):
            break
    with open(path, "r+b") as f:
        f.truncate(offset)


def openssl_verify(pub_pem: Path, data: bytes, sig_hex: str) -> bool:
    """openssl 验签(测试侧允许临时文件)。"""
    tmp = pub_pem.with_name(f".t-{os.getpid()}-{next(_TREE_COUNTER)}")
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


def sign_bytes_offline(sk_path: Path, data: bytes) -> str:
    """ROOT_SK/签名私钥离线签名(测试构造对抗输入用)。"""
    tmp = sk_path.with_name(f".s-{os.getpid()}-{next(_TREE_COUNTER)}")
    data_path = tmp.with_suffix(".data")
    out_path = tmp.with_suffix(".sig")
    try:
        data_path.write_bytes(data)
        subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(sk_path),
             "-out", str(out_path), str(data_path)],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        return out_path.read_bytes().hex()
    finally:
        for p in (data_path, out_path):
            if p.exists():
                p.unlink()


def gen_keypair(tmp_path: Path, name: str) -> tuple[Path, Path, bytes]:
    """生成测试用密钥对(对抗材料用),返回 (sk, pk, pk_pem)。"""
    sk = tmp_path / f"{name}.sk"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
         "-out", str(sk)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    pk = tmp_path / f"{name}.pub"
    subprocess.run(
        ["openssl", "pkey", "-in", str(sk), "-pubout", "-out", str(pk)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return sk, pk, pk.read_bytes()


def load_bootstrap_mod() -> object:
    """importlib 加载 bootstrap 脚本(签名/条目构造原语复用,非手写)。"""
    if not BOOTSTRAP_PATH.exists():
        pytest.skip(f"依赖后续模块的脚本未就绪: {BOOTSTRAP_PATH}")
    spec = importlib.util.spec_from_file_location(
        "harvest_anchor_bootstrap", str(BOOTSTRAP_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


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
def deploy_setup(tmp_path):
    """构造测试部署环境(独立子目录),返回 env 字典与关键路径。"""

    def _setup(deep_chain: bool = False, with_fingerprint_env: bool = False) -> dict:
        uid, gid = os.getuid(), os.getgid()
        deploy_root = tmp_path / f"deploy-{next(_TREE_COUNTER)}"
        deploy_root.mkdir(mode=0o750)
        tree = tmp_path / f"roots-tree-{next(_TREE_COUNTER)}"
        tree.mkdir(mode=0o750)
        if deep_chain:
            (tree / "x").mkdir(mode=0o750)
            (tree / "x" / "y").mkdir(mode=0o750)
            (tree / "x" / "y" / "raw").mkdir(mode=0o750)
            raw_dir = tree / "x" / "y" / "raw"
        else:
            raw_dir = tree / "raw"
            raw_dir.mkdir(mode=0o750)
        stage = tree / "stage"
        locks = tree / "locks"
        batch = tree / "batch"
        for p in (stage, locks, batch):
            p.mkdir(mode=0o750)
        roots = {
            "raw_dir": str(raw_dir),
            "stage": str(stage),
            "locks_dir": str(locks),
            "batch": str(batch),
            "trust_root": str(tree),
        }
        roots_cfg = deploy_root / "roots.json"
        roots_cfg.write_text(json.dumps(roots, indent=1))
        os.chmod(roots_cfg, 0o400)
        # 故障注入双因子门禁的物理标记(本文件唯一使用注入的用例是坏尾类;
        # 落盘者是测试进程本身,uid 与部署 uid 一致,与 test_mode_enabled()
        # 的 owner 校验相符)
        marker = deploy_root / TEST_MODE_MARKER_NAME
        marker.write_text(TEST_MODE_MARKER_MAGIC + "\n")
        os.chmod(marker, 0o600)
        env = {
            ENV_DEPLOY_ROOT: str(deploy_root),
            ENV_DEPLOY_UID: str(uid),
            ENV_DEPLOY_GID: str(gid),
            ENV_READER_GID: str(gid),
            ENV_RUNTIME_UID: str(uid),
            ENV_RUNTIME_GID: str(gid),
        }
        return {
            "env": env,
            "deploy_root": deploy_root,
            "roots_config": roots_cfg,
            "roots": roots,
            "trust_log": deploy_root / "trust-log" / "log",
            "lock_file": deploy_root / "trust-log" / ".deploy.lock",
            "recovery_log": deploy_root / "trust-log" / "recovery-log",
            "generations": deploy_root / "generations",
            "signer_keys": deploy_root / "signer-keys",
            "root_sk": deploy_root / "root-key" / "root.sk",
            "root_pk": deploy_root / "root-key" / "root.pub",
            "tmp_path": tmp_path,
            "tree": tree,
        }

    return _setup


@pytest.fixture()
def anchors_mod():
    """akb2.harvest.anchors 模块(本 SC 交付物;实现前直接 import 即红)。"""
    import akb2.harvest.anchors as m
    return m


# ---------------------------------------------------------------------------
# 辅助:真实部署动作 + load_verified 调用
# ---------------------------------------------------------------------------


def run_bootstrap(bootstrap: Path, args: list[str], env: dict,
                  cwd: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(env)
    if extra_env:
        e.update(extra_env)
    return subprocess.run(
        [sys.executable, str(bootstrap), *args], capture_output=True, text=True,
        env=e, cwd=cwd,
    )


def init_deploy(bootstrap: Path, ds: dict, repo_root: Path, deployment_id: str = "dep-1",
                fingerprint: bool = True) -> dict:
    """真实部署动作 init,返回更新后的 ds(指纹钉入 env)。"""
    if not bootstrap.exists():
        pytest.skip(f"依赖后续模块的脚本未就绪: {bootstrap}")
    r = run_bootstrap(bootstrap, ["init", "--deployment-id", deployment_id],
                      ds["env"], repo_root)
    assert r.returncode == 0, f"init 失败: {r.stdout} {r.stderr}"
    if fingerprint:
        fp = sha256_file(ds["root_pk"])
        ds["env"] = dict(ds["env"])
        ds["env"][ENV_ROOT_FP] = fp
    return ds


def migrate_deploy(bootstrap: Path, ds: dict, repo_root: Path,
                   deployment_id: str = "dep-1", extra_env: dict | None = None) -> dict:
    if not bootstrap.exists():
        pytest.skip(f"依赖后续模块的脚本未就绪: {bootstrap}")
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", deployment_id],
                      ds["env"], repo_root, extra_env=extra_env)
    assert r.returncode == 0, f"migrate 失败: {r.stdout} {r.stderr}"
    return ds


def apply_env(monkeypatch, env: dict) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def call_load_verified(monkeypatch, anchors_mod, ds: dict):
    """当前进程调 load_verified(env 由 ds 注入)。"""
    apply_env(monkeypatch, ds["env"])
    return anchors_mod.load_verified()


def sub_load_verified(ds: dict, repo_root: Path) -> subprocess.CompletedProcess:
    """真实子进程调 load_verified(独立进程、无继承 fd)。"""
    env = dict(os.environ)
    env.update(ds["env"])
    env["PYTHONPATH"] = str(repo_root / "src")
    code = (
        "from akb2.harvest.anchors import load_verified;"
        "h = load_verified(); print('OK', sorted(h))"
    )
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env,
        cwd=repo_root,
    )


def assert_zero_files_in(path: Path) -> None:
    """断言目录内零业务产出:不含任何文件(预建的空子目录不算产出)。"""
    if not path.exists():
        return
    for p in path.rglob("*"):
        assert not p.is_file(), f"目录内应有零文件产出: {p}"


def dual_identity_available() -> bool:
    """探测「两个真实不同非 root UID」是否可构造(CI: runner + sudo -n + nobody)。"""
    if os.geteuid() == 0:
        return False
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-u", "nobody", "id", "-u"],
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip().isdigit()


DUAL_IDENTITY = dual_identity_available()


# ---------------------------------------------------------------------------
# ① 正常路径
# ---------------------------------------------------------------------------


def test_normal_path_four_handles(repo_root, bootstrap, deploy_setup, anchors_mod,
                                  monkeypatch):
    """受信 bootstrap 生成 manifest → load_verified() 返回四个 AnchorHandle,
    root_fd 可用且 expected_identity 与磁盘逐字相符。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]
    for name, h in handles.items():
        assert h.leaf_name == Path(ds["roots"][name]).name
        st = os.stat(ds["roots"][name], follow_symlinks=False)
        assert (h.expected_identity["st_dev"], h.expected_identity["st_ino"]) == (
            st.st_dev, st.st_ino)
        # root_fd 可用
        fst = os.fstat(h.root_fd)
        assert (fst.st_dev, fst.st_ino) == (st.st_dev, st.st_ino)


# ---------------------------------------------------------------------------
# ② 运行期禁止自举
# ---------------------------------------------------------------------------


def test_no_self_bootstrap_when_missing(repo_root, bootstrap, deploy_setup,
                                        anchors_mod, monkeypatch):
    """manifest 不存在 → 抛 AnchorDriftError,断言磁盘上仍无 manifest(反证没有创建)。"""
    ds = deploy_setup()
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert not (ds["deploy_root"] / "generations").exists(), "运行期不得自举 generations"
    assert not ds["trust_log"].exists(), "运行期不得创建 trust-log"


def test_no_self_bootstrap_when_root_unconfigured(anchors_mod, monkeypatch, tmp_path):
    """部署常量缺失(无 env)→ AnchorDriftError,零文件产出。"""
    for k in (ENV_DEPLOY_ROOT, ENV_DEPLOY_UID, ENV_DEPLOY_GID, ENV_READER_GID,
              ENV_RUNTIME_UID, ENV_RUNTIME_GID):
        monkeypatch.delenv(k, raising=False)
    before = {str(p.relative_to(tmp_path)): p for p in tmp_path.rglob("*")}
    with pytest.raises(anchors_mod.AnchorDriftError):
        anchors_mod.load_verified()
    after = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")}
    assert set(before) == after, "缺失常量时零文件产出"


# ---------------------------------------------------------------------------
# ③ 真实冷启动替换(真实进程重启,四根各跑一遍)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root_name", ["raw_dir", "stage", "locks_dir", "batch"])
def test_cold_start_replace_rejects(repo_root, bootstrap, deploy_setup,
                                    anchors_mod, root_name):
    """预测红集:把「建立时值」实现成启动时现取/缓存的版本在此例下必然放行。

    bootstrap 生成 manifest → 子进程 load_verified 成功(基线)→ 攻击者 rename
    掉某根并重建同名普通目录 → 新子进程 load_verified 必须抛错、替身目录零产出。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    r = sub_load_verified(ds, repo_root)
    assert r.returncode == 0, f"基线 load_verified 应成功: {r.stdout} {r.stderr}"

    target = Path(ds["roots"][root_name])
    moved = target.with_name(target.name + "-moved")
    os.rename(target, moved)
    os.mkdir(target, 0o750)  # 同名普通目录(非 symlink)

    r2 = sub_load_verified(ds, repo_root)
    assert r2.returncode != 0, "替换根后新进程 load_verified 必须抛错(零缓存/零现取)"
    assert "AnchorDriftError" in r2.stderr
    assert_zero_files_in(target)


# ---------------------------------------------------------------------------
# ④ 中间级替换(反证记录完整链而非只有叶节点)
# ---------------------------------------------------------------------------


def test_mid_chain_replace_rejects(repo_root, bootstrap, deploy_setup,
                                   anchors_mod, monkeypatch):
    """替换 parent chain 中间某一级(叶 inode 不变)→ 仍抛错。"""
    ds = init_deploy(bootstrap, deploy_setup(deep_chain=True), repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]

    y = ds["tree"] / "x" / "y"
    y_orig = y.with_name("y-moved")
    os.rename(y, y_orig)
    os.mkdir(y, 0o750)
    os.rename(y_orig / "raw", y / "raw")  # rename 保持 raw 的 inode 不变
    # 只记叶节点的实现:raw (dev,ino) 未变 → 放行;记完整链的实现:y 已换 → 拒
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)


# ---------------------------------------------------------------------------
# ⑤ manifest 被改写(签名防线的核心用例)
# ---------------------------------------------------------------------------


def _evil_manifest(ds: dict, deployment_id: str, epoch: int) -> dict:
    """内部完全自洽的攻击者 manifest:四根指向攻击者目录(chain 为磁盘真实值)。"""
    evil = ds["tree"] / "evil"
    evil.mkdir(mode=0o750)
    for name in ("raw", "stage", "locks", "batch"):
        (evil / name).mkdir(mode=0o750)
    chain: dict[str, list[dict]] = {}
    for name, sub in (("raw_dir", "raw"), ("stage", "stage"),
                      ("locks_dir", "locks"), ("batch", "batch")):
        target = evil / sub
        st = os.stat(target, follow_symlinks=False)
        chain[name] = {
            "path": str(target),
            "chain": [{"name": "evil", "st_dev": os.stat(evil, follow_symlinks=False).st_dev,
                       "st_ino": os.stat(evil, follow_symlinks=False).st_ino},
                      {"name": sub, "st_dev": st.st_dev, "st_ino": st.st_ino}],
        }
    return {
        "schema_version": 1,
        "deployment_id": deployment_id,
        "epoch": epoch,
        "generation_dir": f"generations/{epoch}",
        "roots": chain,
    }


def test_manifest_tamper_rejected(repo_root, bootstrap, deploy_setup,
                                  anchors_mod, monkeypatch):
    """预测红集:只校验「manifest 自身当前父链」的实现在此例下必然放行。

    攻击者改写 manifest 一个文件(内部完全自洽)→ 因验签失败抛 AnchorDriftError、
    四个 handle 一个不返回、攻击者目录内零产出。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    gen1 = ds["generations"] / "1"
    evil_manifest = _evil_manifest(ds, "dep-1", 1)
    # 攻击者改写 manifest 内容(先解除只读再恢复,保留原签名)
    os.chmod(gen1 / "manifest", 0o600)
    (gen1 / "manifest").write_text(json.dumps(evil_manifest, indent=1))
    os.chmod(gen1 / "manifest", 0o440)
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert_zero_files_in(ds["tree"] / "evil")


def test_manifest_signature_missing_rejected(repo_root, bootstrap, deploy_setup,
                                             anchors_mod, monkeypatch):
    """签名缺失 → 拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    (ds["generations"] / "1" / "manifest.sig").unlink()
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)


def test_public_key_replaced_rejected(repo_root, bootstrap, deploy_setup,
                                      anchors_mod, monkeypatch, tmp_path):
    """公钥文件被替换(删除重建 → mode 非预期)→ 因 owner/mode 校验不符而拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    _, _, pk_pem = gen_keypair(tmp_path, "attacker-pk")
    pub_path = ds["generations"] / "1" / "public_key"
    pub_path.unlink()
    pub_path.write_bytes(pk_pem)  # 新建默认 mode 0644 ≠ 0440
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)


def test_public_key_content_swapped_rejected(repo_root, bootstrap, deploy_setup,
                                             anchors_mod, monkeypatch, tmp_path):
    """公钥内容被替换(保持 mode)→ bundle 摘要比对拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    _, _, pk_pem = gen_keypair(tmp_path, "attacker-pk2")
    pub_path = ds["generations"] / "1" / "public_key"
    os.chmod(pub_path, 0o600)
    pub_path.write_bytes(pk_pem)
    os.chmod(pub_path, 0o440)
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)


def test_owner_mismatch_rejected(repo_root, bootstrap, deploy_setup,
                                 anchors_mod, monkeypatch):
    """owner UID 非预期(部署常量与文件 owner 不符)→ 拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    bad_env = dict(ds["env"])
    bad_env[ENV_DEPLOY_UID] = "424242"  # 不存在的部署 UID → owner 校验必不符
    apply_env(monkeypatch, bad_env)
    with pytest.raises(anchors_mod.AnchorDriftError):
        anchors_mod.load_verified()


def test_group_or_other_writable_rejected(repo_root, bootstrap, deploy_setup,
                                          anchors_mod, monkeypatch):
    """manifest 或公钥组/其他可写 → 拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    os.chmod(ds["generations"] / "1" / "manifest", 0o660)
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)


# ---------------------------------------------------------------------------
# ⑤c 旧代际回放红例(签名有效但代际过期)
# ---------------------------------------------------------------------------


def test_replay_old_generation_rejected(repo_root, bootstrap, deploy_setup,
                                        anchors_mod, monkeypatch):
    """预测红集:只验签不校验代际的实现在此例下必然放行并把写入导向旧树。

    两代部署(epoch 1 → 2,含密钥轮换),保留第一代真实 manifest 与签名,把当前
    manifest 替换回第一代 → 因代际低于 trust-log 末条声明而拒、零写盘;第一代
    公钥已被运行期废止(判据:trust-log 后续完整条目载明 key-id 已废止)。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    migrate_deploy(bootstrap, ds, repo_root)
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 2
    e1, e2 = entries
    # 审计:第一代公钥已被废止 —— 判据 = 后续完整条目载明该 key-id 已废止(逐字)
    assert e2["revocation"]["prev_key_id"] == e1["admission"]["key_id"], \
        "E2 必须载明 K1 的废止声明"
    # 第一代真实生成物
    gen1 = ds["generations"] / "1"
    old_manifest = (gen1 / "manifest").read_bytes()
    old_sig = (gen1 / "manifest.sig").read_bytes()
    # 审计:trust-log 真实读取(计数)
    real_read = anchors_mod._read_trust_log
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return real_read()

    monkeypatch.setattr(anchors_mod, "_read_trust_log", counting)
    # 回放:把当前 manifest 替换回第一代那份(签名完全有效、owner/mode 合法)
    gen2 = ds["generations"] / "2"
    os.chmod(gen2 / "manifest", 0o600)
    os.chmod(gen2 / "manifest.sig", 0o600)
    (gen2 / "manifest").write_bytes(old_manifest)
    (gen2 / "manifest.sig").write_bytes(old_sig)
    os.chmod(gen2 / "manifest", 0o440)
    os.chmod(gen2 / "manifest.sig", 0o440)
    before = sha256_file(ds["trust_log"])
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert calls["n"] >= 1, "trust-log 必须被真实读取"
    assert "epoch" in str(ei.value).lower(), f"拒因必须是代际: {ei.value}"
    assert sha256_file(ds["trust_log"]) == before, "零写盘"
    assert list(gen2.iterdir()), "manifest 仍在(未被运行期改写)"


def test_revocation_independent_rejects(repo_root, bootstrap, deploy_setup,
                                        anchors_mod, monkeypatch, tmp_path):
    """⑤c4 预测红集:只按 effective-at-position 验历史链、却不校验「谁能充当
    当前 tip」的实现在此例下必然放行。

    完整 E1→E2 链后追加候选 E3'(epoch/bundle/准入全部自洽且高于 E2,唯独签名者
    用已被 E2 废止的 K1)→ 因签名者不是末条完整条目所准入的当代密钥而拒;
    因 K1 已被链上 E2 废止、不得充当当前代而拒;三个理由分别断言。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    # 提前保存 K1.sk(migrate 会删除它)
    k1_sk = ds["signer_keys"] / "K1.sk"
    saved_k1 = k1_sk.read_bytes()
    migrate_deploy(bootstrap, ds, repo_root)
    entries = read_trust_log(ds["trust_log"])
    e1, e2 = entries
    bmod = load_bootstrap_mod()
    tmp = tmp_path / "cand"
    tmp.mkdir()
    k3_sk, _, k3_pub = gen_keypair(tmp, "K3cand")
    k2_sk = ds["signer_keys"] / "K2.sk"
    # generations/3/ 候选 bundle(epoch/bundle 摘要自洽)
    gen3 = ds["generations"] / "3"
    gen3.mkdir(mode=0o750)
    manifest3 = json.loads((ds["generations"] / "2" / "manifest").read_text())
    manifest3["epoch"] = 3
    manifest3["generation_dir"] = "generations/3"
    (gen3 / "manifest").write_text(json.dumps(manifest3, indent=1))
    os.chmod(gen3 / "manifest", 0o440)
    subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(k3_sk), "-out", str(gen3 / "manifest.sig"),
         str(gen3 / "manifest")],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    os.chmod(gen3 / "manifest.sig", 0o440)
    (gen3 / "public_key").write_bytes(k3_pub)
    os.chmod(gen3 / "public_key", 0o440)
    # 候选条目:准入 K3'(证书由 K2 签,合法)、签名由已废止的 K1 签
    c = {"generations": ds["generations"]}
    e3_body = {
        "deployment_id": "dep-1",
        "epoch": 3,
        "generation_dir": "generations/3",
        "bundle": {
            "manifest_sha256": sha256_file(gen3 / "manifest"),
            "signature_sha256": sha256_file(gen3 / "manifest.sig"),
            "public_key_sha256": sha256_file(gen3 / "public_key"),
        },
        "admission": bmod.build_admission(c, 3, bmod.key_id_of_pem(k3_pub), k3_pub, k2_sk),
        "revocation": {"prev_key_id": e2["admission"]["key_id"], "revoked_at_epoch": 3},
        "prev_entry_hash": entry_hash(e2),
    }
    saved_k1_path = tmp / "K1.sk"
    saved_k1_path.write_bytes(saved_k1)
    e3_body["signature"] = sign_bytes_offline(saved_k1_path,
                                              bmod.entry_body_bytes(e3_body))
    with open(ds["trust_log"], "ab") as f:
        f.write(json.dumps(e3_body, sort_keys=True).encode() + b"\n")

    before = sha256_file(ds["trust_log"])
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    msg = str(ei.value)
    assert "废止" in msg, f"拒因必须点名 revocation(签名者被后续条目废止): {msg}"
    assert "当代" in msg or "密钥" in msg, f"拒因必须点名签名者非当代: {msg}"
    assert "epoch" not in msg.lower(), f"拒因不得是 epoch 低: {msg}"
    assert sha256_file(ds["trust_log"]) == before, "零写盘"


def test_replay_same_key_two_epochs(repo_root, bootstrap, deploy_setup,
                                    anchors_mod, monkeypatch, tmp_path):
    """⑤c2 预测红集:只验签不读提交日志的实现在此例下必然放行。

    同一把仍然有效的公钥签出两份不同 epoch 的 manifest(测试内受控保留该 key
    为受信验证 key,不轮换),回放低 epoch 那份 → 因代际低于末条声明而拒;
    审计 trust-log 真实读取且 epoch 逐字比较。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    bmod = load_bootstrap_mod()
    # 不轮换:用 K1 签 epoch=2 的 manifest 与条目(准入 K1 自己)
    k1_sk = ds["signer_keys"] / "K1.sk"
    k1_pub = (ds["generations"] / "1" / "public_key").read_bytes()
    e1 = read_trust_log(ds["trust_log"])[0]
    gen2 = ds["generations"] / "2"
    gen2.mkdir(mode=0o750)
    manifest2 = json.loads((ds["generations"] / "1" / "manifest").read_text())
    manifest2["epoch"] = 2
    manifest2["generation_dir"] = "generations/2"
    (gen2 / "manifest").write_text(json.dumps(manifest2, indent=1))
    os.chmod(gen2 / "manifest", 0o440)
    subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(k1_sk), "-out", str(gen2 / "manifest.sig"),
         str(gen2 / "manifest")],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    os.chmod(gen2 / "manifest.sig", 0o440)
    (gen2 / "public_key").write_bytes(k1_pub)
    os.chmod(gen2 / "public_key", 0o440)
    c = {"generations": ds["generations"]}
    e2_body = {
        "deployment_id": "dep-1",
        "epoch": 2,
        "generation_dir": "generations/2",
        "bundle": {
            "manifest_sha256": sha256_file(gen2 / "manifest"),
            "signature_sha256": sha256_file(gen2 / "manifest.sig"),
            "public_key_sha256": sha256_file(gen2 / "public_key"),
        },
        "admission": bmod.build_admission(c, 2, bmod.key_id_of_pem(k1_pub), k1_pub, k1_sk),
        "revocation": None,
        "prev_entry_hash": entry_hash(e1),
    }
    e2_body["signature"] = sign_bytes_offline(k1_sk, bmod.entry_body_bytes(e2_body))
    with open(ds["trust_log"], "ab") as f:
        f.write(json.dumps(e2_body, sort_keys=True).encode() + b"\n")

    real_read = anchors_mod._read_trust_log
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return real_read()

    monkeypatch.setattr(anchors_mod, "_read_trust_log", counting)
    # 回放低 epoch 那份:第一代真实 manifest(仍由受信 K1 签,未轮换故仍有效)
    gen1_manifest = (ds["generations"] / "1" / "manifest").read_bytes()
    gen1_sig = (ds["generations"] / "1" / "manifest.sig").read_bytes()
    os.chmod(gen2 / "manifest", 0o600)
    os.chmod(gen2 / "manifest.sig", 0o600)
    (gen2 / "manifest").write_bytes(gen1_manifest)
    (gen2 / "manifest.sig").write_bytes(gen1_sig)
    os.chmod(gen2 / "manifest", 0o440)
    os.chmod(gen2 / "manifest.sig", 0o440)
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert calls["n"] >= 1, "trust-log 必须被真实读取(open/read ≥1)"
    assert "epoch" in str(ei.value).lower(), f"拒因必须是代际逐字比较: {ei.value}"


# ---------------------------------------------------------------------------
# ⑤c5 / ⑤c6 全栈组合替换与指纹比对
# ---------------------------------------------------------------------------


def test_full_stack_replace_fingerprint_rejects(repo_root, bootstrap, deploy_setup,
                                                anchors_mod, monkeypatch, tmp_path):
    """⑤c5 预测红集:不做指纹比对、只验「链自洽」的实现在此例下必然放行。

    攻击者用自己的一把 root 私钥生成整套材料并一次性全部替换 → 第 0 步指纹
    比对即拒、证书链验证一次都没开始(签名验证函数零调用)、零写盘。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    # 攻击者部署:独立 deploy_root 生成整套材料
    att_ds = deploy_setup()
    # 攻击者不需要指纹钉住,但需要真实 root/链/bundle
    init_deploy(bootstrap, att_ds, repo_root, deployment_id="att-1", fingerprint=False)
    # 一次性替换:root-key、trust-log、generations
    for sub in ("root-key", "trust-log", "generations"):
        src = att_ds["deploy_root"] / sub
        dst = ds["deploy_root"] / sub
        import shutil
        shutil.rmtree(dst)
        shutil.copytree(src, dst)
    # roots.json 保持原样(四根未动)
    real_verify = anchors_mod._verify_signature
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return real_verify(*a, **k)

    monkeypatch.setattr(anchors_mod, "_verify_signature", counting)
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert calls["n"] == 0, f"证书链验证一次都没开始(第 0 步指纹比对即拒),实际 {calls['n']} 次"
    assert "fingerprint" in str(ei.value).lower() or "指纹" in str(ei.value)


@pytest.mark.parametrize("mode", ["deleted", "empty", "wrong"])
def test_fingerprint_unbypassable(repo_root, bootstrap, deploy_setup,
                                  anchors_mod, monkeypatch, tmp_path, mode):
    """⑤c6 指纹比对不可被绕过:删除/空/指纹不符 → 第 0 步拒绝,不进证书链验证。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    root_pk = ds["root_pk"]
    if mode == "deleted":
        root_pk.unlink()
    else:
        os.chmod(root_pk, 0o600)  # 解除只读(攻击者改写)
        if mode == "empty":
            root_pk.write_bytes(b"")
        else:
            _, _, pk_pem = gen_keypair(tmp_path, "wrong-pk")
            root_pk.write_bytes(pk_pem)
    real_verify = anchors_mod._verify_signature
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return real_verify(*a, **k)

    monkeypatch.setattr(anchors_mod, "_verify_signature", counting)
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert calls["n"] == 0, "第 0 步指纹比对即拒,不得进入证书链验证"


# ---------------------------------------------------------------------------
# ⑤c7 签发私钥对运行期不可读
# ---------------------------------------------------------------------------


def test_root_sk_runtime_inaccessible_code_path(repo_root, bootstrap, deploy_setup,
                                                anchors_mod, monkeypatch):
    """运行期代码从不尝试打开 ROOT_SK(代码路径审计)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    opened: list[str] = []
    real_open = os.open

    def spy_open(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr(os, "open", spy_open)
    call_load_verified(monkeypatch, anchors_mod, ds)
    assert all(str(ds["root_sk"]) not in p for p in opened), \
        f"运行期代码不得触碰 ROOT_SK: {opened}"


def test_root_sk_stat_mode_and_owner(repo_root, bootstrap, deploy_setup,
                                     anchors_mod, monkeypatch):
    """ROOT_SK 可 stat、mode 恰为 0400、owner 恰为部署 UID(权限表正向验证)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    st = os.stat(ds["root_sk"])
    assert st.st_mode & 0o777 == 0o400
    assert st.st_uid == os.getuid() == int(ds["env"][ENV_DEPLOY_UID])


@pytest.mark.skipif(not DUAL_IDENTITY, reason="本机构造不了双真实 UID(需免密 sudo)")
def test_root_sk_open_denied_for_runtime_identity(repo_root, bootstrap, deploy_setup,
                                                  anchors_mod, monkeypatch):
    """以真实 runtime UID(非部署 UID)对 ROOT_SK:open/read 一律 PermissionError。"""
    ds = deploy_setup()
    runtime_uid = int(subprocess.run(
        ["sudo", "-n", "-u", "nobody", "id", "-u"], capture_output=True, text=True,
    ).stdout.strip())
    runtime_gid = int(subprocess.run(
        ["sudo", "-n", "-u", "nobody", "id", "-g"], capture_output=True, text=True,
    ).stdout.strip())
    ds["env"][ENV_RUNTIME_UID] = str(runtime_uid)
    ds["env"][ENV_RUNTIME_GID] = str(runtime_gid)
    # 部署身份仍为测试进程(init 由测试进程执行,文件 owner = 测试进程)
    import subprocess as sp
    r = sp.run(["sudo", "-n", "-u", "nobody", sys.executable, "-c",
                "import os; st=os.stat(os.environ['P']); "
                "assert st.st_mode & 0o777 == 0o400, oct(st.st_mode); "
                "assert st.st_uid == int(os.environ['U']), st.st_uid; "
                "os.open(os.environ['P'], os.O_RDONLY)"],
               capture_output=True, text=True,
               env={**os.environ, "P": str(ds["root_sk"]), "U": str(os.getuid())})
    assert "PermissionError" in r.stderr, f"运行期身份 open ROOT_SK 必须 PermissionError: {r.stderr}"


# ---------------------------------------------------------------------------
# ⑤c8 吊销时序有效性(三代;effective-at-position)
# ---------------------------------------------------------------------------


def test_key_rotation_history_remains_valid(repo_root, bootstrap, deploy_setup,
                                            anchors_mod, monkeypatch):
    """预测红集:把废止实现成全局集合(出现在任何后续条目里即失效)的版本
    在 (a) 必红(K_1 被判失效,E_2 验不过,整条历史崩)。

    init → migrate → migrate(三代 K1/K2/K3):
    (a) 历史链仍可完整验证(E2 由 K1 签、E3 由 K2 签,逐条 effective-at-position);
    (b) 末条准入的 K3 是唯一可签发新条目的密钥:用 K3 签一条合法新条目 → 接受;
    (c) 旧 key 不得签发新条目:K1/K2 签格式完全合法的新条目 → 两例均拒,
        拒因是「签名者不是末条所准入的当代密钥」而非「验签失败」。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    migrate_deploy(bootstrap, ds, repo_root)
    migrate_deploy(bootstrap, ds, repo_root)
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 3
    # (a) 历史链:签名者与准入一致(逐字验签)
    bmod = load_bootstrap_mod()
    for i, e in enumerate(entries):
        pub = ds["root_pk"] if i == 0 else ds["generations"] / str(entries[i - 1]["epoch"]) / "public_key"
        assert openssl_verify(pub, bmod.entry_body_bytes(e), e["signature"]), \
            f"E{i+1} 历史链签名验证失败"
    # (a) load_verified 全链通过(三代后历史仍完整)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]
    # (b) 用 K3 签合法新条目 → 接受(真实 migrate 第 4 次)
    migrate_deploy(bootstrap, ds, repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]
    # (c) 旧 key 不得签发新条目:由 test_old_keys_cannot_sign_new_entries 覆盖
    # (该用例须在 migrate 删除旧私钥前保存 K1/K2,故单独成例)


def test_old_keys_cannot_sign_new_entries(repo_root, bootstrap, deploy_setup,
                                          anchors_mod, monkeypatch, tmp_path):
    """⑤c8 (c) 判别用例:K1/K2 签的候选条目 → 拒因「签名者被废止/非当代」而非验签失败。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    saved_k1 = (ds["signer_keys"] / "K1.sk").read_bytes()
    migrate_deploy(bootstrap, ds, repo_root)
    saved_k2 = (ds["signer_keys"] / "K2.sk").read_bytes()
    migrate_deploy(bootstrap, ds, repo_root)
    migrate_deploy(bootstrap, ds, repo_root)  # 第 4 代:K4 当代
    entries = read_trust_log(ds["trust_log"])
    e4 = entries[-1]
    bmod = load_bootstrap_mod()
    tmp = tmp_path / "oldkeys"
    tmp.mkdir()
    k4_sk = ds["signer_keys"] / "K4.sk"  # 当代前驱:合法准入证书签名者
    for old_n, sk_bytes in ((1, saved_k1), (2, saved_k2)):
        # 恢复 trust-log 到 E4 末尾(移除上一轮候选条目,避免链上残留干扰)
        truncate_trust_log_to(ds["trust_log"], e4)
        old_sk = tmp / f"K{old_n}.sk"
        old_sk.write_bytes(sk_bytes)
        gen5 = ds["generations"] / "5"
        if gen5.exists():
            import shutil
            shutil.rmtree(gen5)
        gen5.mkdir(mode=0o750)
        manifest5 = json.loads((ds["generations"] / "4" / "manifest").read_text())
        manifest5["epoch"] = 5
        manifest5["generation_dir"] = "generations/5"
        (gen5 / "manifest").write_text(json.dumps(manifest5, indent=1))
        os.chmod(gen5 / "manifest", 0o440)
        subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(old_sk),
             "-out", str(gen5 / "manifest.sig"), str(gen5 / "manifest")],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        os.chmod(gen5 / "manifest.sig", 0o440)
        _, _, k5_pub = gen_keypair(tmp, f"K5cand{old_n}")
        (gen5 / "public_key").write_bytes(k5_pub)
        os.chmod(gen5 / "public_key", 0o440)
        c = {"generations": ds["generations"]}
        body = {
            "deployment_id": "dep-1",
            "epoch": 5,
            "generation_dir": "generations/5",
            "bundle": {
                "manifest_sha256": sha256_file(gen5 / "manifest"),
                "signature_sha256": sha256_file(gen5 / "manifest.sig"),
                "public_key_sha256": sha256_file(gen5 / "public_key"),
            },
            # 准入证书由当代 K4 签(合法);仅条目签名用已废止的旧 key
            "admission": bmod.build_admission(c, 5, bmod.key_id_of_pem(k5_pub), k5_pub, k4_sk),
            "revocation": {"prev_key_id": e4["admission"]["key_id"], "revoked_at_epoch": 5},
            "prev_entry_hash": entry_hash(e4),
        }
        body["signature"] = sign_bytes_offline(old_sk, bmod.entry_body_bytes(body))
        with open(ds["trust_log"], "ab") as f:
            f.write(json.dumps(body, sort_keys=True).encode() + b"\n")
        with pytest.raises(anchors_mod.AnchorDriftError) as ei:
            call_load_verified(monkeypatch, anchors_mod, ds)
        msg = str(ei.value)
        assert "废止" in msg, f"拒因必须点名 revocation: {msg}"
        assert "验签失败" not in msg, f"拒因不得是验签失败: {msg}"


# ---------------------------------------------------------------------------
# ⑤c9 坏尾与陈旧锁
# ---------------------------------------------------------------------------


def test_bad_tail_short_write_fail_closed(repo_root, bootstrap, deploy_setup,
                                          anchors_mod, monkeypatch):
    """(a) 短写不死进程:写入方当场判定坏尾、fail-closed 退出、不重试续写。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    before = ds["trust_log"].read_bytes()
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                      ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    assert r.returncode == 2, f"短写必须 fail-closed: {r.stdout} {r.stderr}"
    assert "坏尾" in r.stderr
    after = ds["trust_log"].read_bytes()
    added = after[len(before):]
    assert not added.endswith(b"\n"), "坏尾残段不得以换行终止(短写丢掉 \\n)"
    # 不重试:新增字节恰为「完整 E2 条目去掉末尾 1 字节」(N-1),不得出现第二次完整写入
    bmod = load_bootstrap_mod()
    entries = read_trust_log(ds["trust_log"])
    e1 = entries[0]
    c = {"generations": ds["generations"]}
    k2_pub = (ds["generations"] / "2" / "public_key").read_bytes()
    e2_full = bmod.build_entry(c, 2, "dep-1", ds["generations"] / "2", k2_pub,
                               ds["signer_keys"] / "K1.sk", entry_hash(e1),
                               e1["admission"]["key_id"])
    payload = json.dumps(e2_full, sort_keys=True).encode() + b"\n"
    assert added == payload[:-1], \
        f"不重试续写:新增 {len(added)} 字节,应恰为短写 {len(payload)-1} 字节"


def test_bad_tail_blocks_migrate(repo_root, bootstrap, deploy_setup,
                                 anchors_mod, monkeypatch):
    """(b) 坏尾之后拒绝 migrate:exit 2 具名报告坏尾偏移、日志字节数未增加。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                  ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    size_after_short = ds["trust_log"].stat().st_size
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                      ds["env"], repo_root)
    assert r.returncode == 2
    assert "坏尾" in r.stderr and "偏移" in r.stderr
    assert ds["trust_log"].stat().st_size == size_after_short, "坏尾后不得 append"


def test_bad_tail_read_side_still_usable(repo_root, bootstrap, deploy_setup,
                                         anchors_mod, monkeypatch):
    """(c) 坏尾期间读侧仍可用:以最后一条完整条目为准正常返回四个 handle。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                  ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]
    for name, h in handles.items():
        st = os.stat(ds["roots"][name], follow_symlinks=False)
        assert (h.expected_identity["st_dev"], h.expected_identity["st_ino"]) == (
            st.st_dev, st.st_ino)


def _issue_ticket(ds: dict, root_sk: Path, *, entry_hash: str, bad_tail_sha256: str,
                  nonce: str = "n1", deployment_id: str = "dep-1",
                  with_signature: bool = True, signer_sk: Path | None = None) -> dict:
    body = {
        "deployment_id": deployment_id,
        "entry_hash": entry_hash,
        "bad_tail_sha256": bad_tail_sha256,
        "issued_at": 1234567890,
        "nonce": nonce,
    }
    if with_signature:
        body["signature"] = sign_bytes_offline(signer_sk or root_sk,
                                                json.dumps(body, sort_keys=True).encode())
    return body


def test_recover_ticket_correct_restores(repo_root, bootstrap, recover, deploy_setup,
                                         anchors_mod, monkeypatch):
    """(d1) 正确票据 → 成功:按票据指定偏移截断、recovery-log 追加完整条目、
    poison 解除、随后 migrate 成功且链可完整验证。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                  ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    entries = read_trust_log(ds["trust_log"])
    assert len(entries) == 1
    e1 = entries[0]
    raw = ds["trust_log"].read_bytes()
    seg = raw.split(b"\n")
    trailing = seg[-1]
    offset = len(raw) - len(trailing)
    bad_bytes = raw[offset:]
    assert bad_bytes and not raw.endswith(b"\n"), "短写后必须存在坏尾(无换行终止)"
    ticket = _issue_ticket(
        ds, ds["root_sk"], entry_hash=entry_hash(e1),
        bad_tail_sha256=sha256_bytes(bad_bytes),
    )
    tp = ds["tmp_path"] / "ticket.json"
    tp.write_text(json.dumps(ticket))
    r = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                       capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r.returncode == 0, f"恢复失败: {r.stdout} {r.stderr}"
    # 坏尾消失、poison 解除
    assert ds["trust_log"].read_bytes().endswith(b"\n")
    assert len(read_trust_log(ds["trust_log"])) == 1
    # recovery-log 追加完整条目
    rlog = read_trust_log(ds["recovery_log"])
    assert len(rlog) == 1
    entry = rlog[0]
    assert entry["ticket"]["nonce"] == "n1"
    assert "before_sha256" in entry and "after_sha256" in entry
    assert entry["offset"] == offset
    assert "uid" in entry and "gid" in entry
    # migrate 恢复成功且链完整
    migrate_deploy(bootstrap, ds, repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]


def test_recover_ticket_invalid_rejected(repo_root, bootstrap, recover, deploy_setup,
                                         anchors_mod, monkeypatch, tmp_path):
    """(d2) 无票据/未签名/非 ROOT_SK 签 → 三例均拒、trust-log 字节逐字未变、
    poison 仍在、recovery-log 零新增。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                  ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    raw_before = ds["trust_log"].read_bytes()
    entries = read_trust_log(ds["trust_log"])
    seg = raw_before.split(b"\n")
    bad_bytes = seg[-1] if not raw_before.endswith(b"\n") else b""
    assert bad_bytes, "短写后必须存在坏尾"
    bad_sha = sha256_bytes(bad_bytes)
    att_sk, _, _ = gen_keypair(tmp_path, "att-sk")
    cases = [
        ("missing", _issue_ticket(ds, ds["root_sk"], entry_hash=entry_hash(entries[0]),
                                  bad_tail_sha256=bad_sha, with_signature=False)),
        ("unsigned", {k: v for k, v in _issue_ticket(
            ds, ds["root_sk"], entry_hash=entry_hash(entries[0]), bad_tail_sha256=bad_sha
        ).items() if k != "signature"}),
        ("wrong-signer", _issue_ticket(ds, att_sk, entry_hash=entry_hash(entries[0]),
                                       bad_tail_sha256=bad_sha)),
    ]
    for label, ticket in cases:
        tp = tmp_path / f"ticket-{label}.json"
        tp.write_text(json.dumps(ticket))
        r = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                           capture_output=True, text=True, env=ds["env"], cwd=repo_root)
        assert r.returncode == 2, f"{label}: 必须拒绝"
        assert ds["trust_log"].read_bytes() == raw_before, f"{label}: 零字节改动"
        assert not ds["trust_log"].read_bytes().endswith(b"\n"), f"{label}: poison 仍在"
        assert not ds["recovery_log"].exists() or not ds["recovery_log"].read_bytes(), \
            f"{label}: recovery-log 零新增"


def test_recover_ticket_mismatch_rejected(repo_root, bootstrap, recover, deploy_setup,
                                          anchors_mod, monkeypatch, tmp_path):
    """(d3) 票据与磁盘不符(entry_hash 错/bad_tail 错/deployment_id 错/nonce 重放)
    → 四例均拒且零字节改动。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                  ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    raw_before = ds["trust_log"].read_bytes()
    entries = read_trust_log(ds["trust_log"])
    seg = raw_before.split(b"\n")
    bad_bytes = seg[-1] if not raw_before.endswith(b"\n") else b""
    bad_sha = sha256_bytes(bad_bytes)
    good = dict(
        deployment_id="dep-1", entry_hash=entry_hash(entries[0]),
        bad_tail_sha256=bad_sha, issued_at=1, nonce="n1",
    )
    cases = [
        ("entry-hash", dict(good, entry_hash="0" * 64)),
        ("bad-tail", dict(good, bad_tail_sha256="0" * 64)),
        ("deployment-id", dict(good, deployment_id="other-dep")),
    ]
    for label, overrides in cases:
        body = dict(good, **overrides)
        ticket = _issue_ticket(ds, ds["root_sk"], entry_hash=body["entry_hash"],
                               bad_tail_sha256=body["bad_tail_sha256"],
                               nonce=body["nonce"], deployment_id=body["deployment_id"])
        tp = tmp_path / f"ticket-{label}.json"
        tp.write_text(json.dumps(ticket))
        r = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                           capture_output=True, text=True, env=ds["env"], cwd=repo_root)
        assert r.returncode == 2, f"{label}: 必须拒绝"
        assert ds["trust_log"].read_bytes() == raw_before, f"{label}: 零字节改动"
        assert not ds["recovery_log"].exists(), f"{label}: recovery-log 零新增"
    # nonce 重放:先成功恢复一次(nonce=n-first 写入 recovery-log)
    good_ticket = _issue_ticket(ds, ds["root_sk"], entry_hash=good["entry_hash"],
                                bad_tail_sha256=bad_sha, nonce="n-first")
    tp0 = tmp_path / "ticket-first.json"
    tp0.write_text(json.dumps(good_ticket))
    r0 = subprocess.run([sys.executable, str(recover), "--ticket", str(tp0)],
                        capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r0.returncode == 0, r0.stderr
    # 再次制造坏尾(第二次 migrate 短写)
    run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                  ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    raw2 = ds["trust_log"].read_bytes()
    entries2 = read_trust_log(ds["trust_log"])
    seg2 = raw2.split(b"\n")
    bad2 = seg2[-1] if not raw2.endswith(b"\n") else b""
    assert bad2 and len(entries2) == 1, "第二次短写后:仅 E1 完整 + E2 坏尾"
    # 重放票据:除 nonce 外全部与磁盘一致(entry_hash=E2、bad_tail=坏尾2)
    replay = _issue_ticket(ds, ds["root_sk"], entry_hash=entry_hash(entries2[-1]),
                           bad_tail_sha256=sha256_bytes(bad2), nonce="n-first")
    tp2 = tmp_path / "ticket-replay.json"
    tp2.write_text(json.dumps(replay))
    r2 = subprocess.run([sys.executable, str(recover), "--ticket", str(tp2)],
                        capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r2.returncode == 2 and "nonce" in r2.stderr.lower(), \
        f"重放票据必须因 nonce 已使用而拒: {r2.stdout} {r2.stderr}"
    assert ds["trust_log"].read_bytes() == raw2, "nonce 重放:零字节改动"


def test_recover_no_self_inference_and_migrate_cannot_clear_poison(
        repo_root, bootstrap, recover, deploy_setup, anchors_mod, monkeypatch, tmp_path):
    """(d4) 恢复入口隔离:migrate 无法解除 poison;恢复脚本缺 entry_hash 直接拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                  ds["env"], repo_root, extra_env={ENV_SHORT_WRITE: "1"})
    # migrate 自身无法解除 poison
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                      ds["env"], repo_root)
    assert r.returncode == 2 and "坏尾" in r.stderr
    # 恢复脚本不自行扫描推断截断点:缺 entry_hash 即拒
    raw = ds["trust_log"].read_bytes()
    bad = raw[len(raw.rstrip(b"\n")):]
    ticket = _issue_ticket(ds, ds["root_sk"], entry_hash=entry_hash(read_trust_log(ds["trust_log"])[0]),
                           bad_tail_sha256=sha256_bytes(bad))
    del ticket["entry_hash"]
    tp = tmp_path / "ticket-noeh.json"
    tp.write_text(json.dumps(ticket))
    r2 = subprocess.run([sys.executable, str(recover), "--ticket", str(tp)],
                        capture_output=True, text=True, env=ds["env"], cwd=repo_root)
    assert r2.returncode == 2 and "缺少字段" in r2.stderr


def test_stale_deploy_lock_blocks_and_manual_clear(repo_root, bootstrap, deploy_setup,
                                                   anchors_mod, monkeypatch):
    """(e) 陈旧部署锁:kill 持锁进程后 migrate → exit 2 报文含 pid 与获取时刻、
    不自动删锁;人工删锁后 migrate → 成功。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    stale = json.dumps({"pid": 999999, "ts": 1234567890})  # pid 不存在 → 陈旧
    ds["lock_file"].parent.mkdir(mode=0o750, exist_ok=True)
    ds["lock_file"].write_text(stale)
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                      ds["env"], repo_root)
    assert r.returncode == 2
    assert "999999" in r.stderr and "1234567890" in r.stderr
    assert "人工确认" in r.stderr
    assert ds["lock_file"].exists(), "不得自动删除锁文件"
    # 人工删锁 → 成功
    ds["lock_file"].unlink()
    migrate_deploy(bootstrap, ds, repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]


def test_concurrent_deploy_lock_rejected(repo_root, bootstrap, deploy_setup,
                                         anchors_mod, monkeypatch):
    """(f) 真并发:持锁进程存活时第二个 migrate → exit 2「另一部署正在进行」。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    ds["lock_file"].parent.mkdir(mode=0o750, exist_ok=True)
    ds["lock_file"].write_text(json.dumps({"pid": os.getpid(), "ts": 1}))
    r = run_bootstrap(bootstrap, ["migrate", "--deployment-id", "dep-1"],
                      ds["env"], repo_root)
    assert r.returncode == 2
    assert "另一部署正在进行" in r.stderr


# ---------------------------------------------------------------------------
# ⑤b 真实部署动作生成并验签
# ---------------------------------------------------------------------------


def test_real_bootstrap_no_manual_manifest(repo_root, bootstrap, deploy_setup,
                                           anchors_mod, monkeypatch):
    """由真实 bootstrap 入口生成 manifest 与签名 → 关停进程关闭 fd → 攻击者替换
    某个根 → 新进程 load_verified 必须抛错;全程未手工构造 manifest 内容。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    gen1 = ds["generations"] / "1"
    manifest_sha = sha256_file(gen1 / "manifest")
    sig_sha = sha256_file(gen1 / "manifest.sig")
    r = sub_load_verified(ds, repo_root)
    assert r.returncode == 0, f"基线应成功: {r.stdout} {r.stderr}"
    # 攻击者替换某个根(普通目录)
    target = Path(ds["roots"]["batch"])
    os.rename(target, target.with_name("batch-moved"))
    os.mkdir(target, 0o750)
    r2 = sub_load_verified(ds, repo_root)
    assert r2.returncode != 0 and "AnchorDriftError" in r2.stderr
    # 全程未手工构造 manifest 内容:内容 sha256 与 init 生成时逐字一致
    assert sha256_file(gen1 / "manifest") == manifest_sha
    assert sha256_file(gen1 / "manifest.sig") == sig_sha


# ---------------------------------------------------------------------------
# ⑥ 不缓存
# ---------------------------------------------------------------------------


def test_no_caching_across_calls(repo_root, bootstrap, deploy_setup,
                                 anchors_mod, monkeypatch):
    """同一进程内首次 load_verified 成功后替换根,再次调用必须抛错。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]
    target = Path(ds["roots"]["stage"])
    os.rename(target, target.with_name("stage-moved"))
    os.mkdir(target, 0o750)
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)


# ---------------------------------------------------------------------------
# ⑦ 模块公开面
# ---------------------------------------------------------------------------


def test_public_surface_only_read_api(anchors_mod):
    """公开面只暴露读 API:不得暴露任何创建或更新基线的函数(dir() 断言)。"""
    public = {n for n in dir(anchors_mod) if not n.startswith("_")}
    assert {"load_verified", "AnchorHandle", "AnchorDriftError"} <= public
    banned = {n for n in public if any(k in n.lower() for k in
              ("create", "write", "build", "sign", "update", "bootstrap",
               "migrate", "init", "issue", "append", "make", "save"))}
    assert not banned, f"不得暴露创建/更新基线函数: {banned}"
    import dataclasses
    fields = {f.name for f in dataclasses.fields(anchors_mod.AnchorHandle)}
    assert fields == {"root_fd", "parent_fd", "leaf_name", "expected_identity"}, fields


# ---------------------------------------------------------------------------
# ⑧ 运行期身份真实可用(双身份 CI 可跑,本机 skip 标注)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not DUAL_IDENTITY, reason="本机构造不了双真实 UID(需免密 sudo)")
def test_runtime_identity_subprocess_usable(repo_root, bootstrap, deploy_setup,
                                            anchors_mod, monkeypatch):
    """预测红集:把运行期验证材料写成 0400 部署 UID 独占的实现在此例下必然 exit 2。

    部署身份 init 之后,换成运行期身份(非部署 UID、属 runtime reader group)起
    真实子进程调 anchors.load_verified() → 四个 handle 全部正常返回。
    """
    ds = deploy_setup()
    runtime_uid = int(subprocess.run(
        ["sudo", "-n", "-u", "nobody", "id", "-u"], capture_output=True, text=True,
    ).stdout.strip())
    runtime_gid = int(subprocess.run(
        ["sudo", "-n", "-u", "nobody", "id", "-g"], capture_output=True, text=True,
    ).stdout.strip())
    ds["env"][ENV_RUNTIME_UID] = str(runtime_uid)
    ds["env"][ENV_RUNTIME_GID] = str(runtime_gid)
    ds["env"][ENV_READER_GID] = str(runtime_gid)
    init_deploy(bootstrap, ds, repo_root, fingerprint=False)
    fp = sha256_file(ds["root_pk"])
    env = dict(ds["env"])
    env[ENV_ROOT_FP] = fp
    env["PYTHONPATH"] = str(repo_root / "src")
    code = (
        "from akb2.harvest.anchors import load_verified;"
        "h = load_verified(); print('OK', sorted(h))"
    )
    r = subprocess.run(["sudo", "-n", "-u", "nobody", sys.executable, "-c", code],
                       capture_output=True, text=True, env=env, cwd=repo_root)
    assert r.returncode == 0, f"运行期身份 load_verified 必须成功: {r.stdout} {r.stderr}"
    assert "raw_dir" in r.stdout and "locks_dir" in r.stdout


def test_writable_material_rejected(repo_root, bootstrap, deploy_setup,
                                    anchors_mod, monkeypatch):
    """负例③:权限被放宽即拒 —— commit(trust-log)或公钥组可写(0460)→ 拒绝。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    os.chmod(ds["trust_log"], 0o660)
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert "mode" in str(ei.value).lower() or "权限" in str(ei.value) or \
        "owner" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# ⑨ 创世信任根
# ---------------------------------------------------------------------------


def test_replace_root_key_rejected(repo_root, bootstrap, deploy_setup,
                                   anchors_mod, monkeypatch, tmp_path):
    """① 替换 root key(固定路径上的 ROOT_PK 换成攻击者密钥,其余保持原样)→
    因证书链无法回溯至预期 root 而拒(不设指纹 env,直接由链验证判别)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    ds["env"] = {k: v for k, v in ds["env"].items() if k != ENV_ROOT_FP}
    _, _, pk_pem = gen_keypair(tmp_path, "att-root")
    os.chmod(ds["root_pk"], 0o600)  # 解除只读后替换,再恢复 mode 合法
    ds["root_pk"].write_bytes(pk_pem)
    os.chmod(ds["root_pk"], 0o440)  # 保持 mode 合法,让拒因落在「链无法回溯 root」
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert "root" in str(ei.value).lower(), f"拒因必须指向 root 锚定: {ei.value}"


def test_forged_self_referential_commit_rejected(repo_root, bootstrap, deploy_setup,
                                                 anchors_mod, monkeypatch, tmp_path):
    """② 预测红例:从未验证的 bundle 里取公钥来验 commit 的实现,在 ② 下必然放行。

    伪造自证 commit:攻击者密钥签的完整代际(攻击者公钥塞进 bundle,内部完全
    自洽)→ 因该公钥的证书链回溯不到 root 而拒。"""
    ds = deploy_setup()
    # 构造攻击者完整代际:全新 trust-log + generations/1/ + 攻击者 root 对
    att_sk, _, att_pub = gen_keypair(tmp_path, "att-full")
    evil_gen = ds["deploy_root"] / "generations" / "1"
    evil_gen.mkdir(parents=True, mode=0o750)
    evil_manifest = _evil_manifest(ds, "evil-dep", 1)
    (evil_gen / "manifest").write_text(json.dumps(evil_manifest, indent=1))
    os.chmod(evil_gen / "manifest", 0o440)
    subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(att_sk),
         "-out", str(evil_gen / "manifest.sig"), str(evil_gen / "manifest")],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    os.chmod(evil_gen / "manifest.sig", 0o440)
    (evil_gen / "public_key").write_bytes(att_pub)
    os.chmod(evil_gen / "public_key", 0o440)
    bmod = load_bootstrap_mod()
    c = {"generations": ds["deploy_root"] / "generations"}
    e1 = {
        "deployment_id": "evil-dep",
        "epoch": 1,
        "generation_dir": "generations/1",
        "bundle": {
            "manifest_sha256": sha256_file(evil_gen / "manifest"),
            "signature_sha256": sha256_file(evil_gen / "manifest.sig"),
            "public_key_sha256": sha256_file(evil_gen / "public_key"),
        },
        "admission": bmod.build_admission(c, 1, bmod.key_id_of_pem(att_pub), att_pub, att_sk),
        "revocation": None,
        "prev_entry_hash": None,
    }
    e1["signature"] = sign_bytes_offline(att_sk, bmod.entry_body_bytes(e1))
    ds["trust_log"].parent.mkdir(mode=0o750, exist_ok=True)
    with open(ds["trust_log"], "ab") as f:
        f.write(json.dumps(e1, sort_keys=True).encode() + b"\n")
    # 真 root.pub 不存在(攻击者未部署 root)→ 创世签名必须以固定路径 root 验
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert "root" in str(ei.value).lower(), f"拒因必须指向 root 回溯: {ei.value}"


def test_verify_start_point_audited(repo_root, bootstrap, deploy_setup,
                                    anchors_mod, monkeypatch):
    """③ 审计验签起点:root key 文件被真实读取,且证书链每一级都被验证(两代
    时验签调用 ≥ 2×条目数:每条条目的签名 + 每条 admission 证书)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    migrate_deploy(bootstrap, ds, repo_root)
    real_verify = anchors_mod._verify_signature
    calls = {"n": 0}
    pub_paths: list[str] = []

    def counting(pub, data, sig):
        calls["n"] += 1
        pub_paths.append(str(pub))
        return real_verify(pub, data, sig)

    monkeypatch.setattr(anchors_mod, "_verify_signature", counting)
    call_load_verified(monkeypatch, anchors_mod, ds)
    # 验签起点审计:固定路径 root key 必须是验签者之一(创世条目/创世证书)
    assert any(str(ds["root_pk"]) == p for p in pub_paths), \
        f"root key 必须作为验签起点被真实读取: {pub_paths}"
    entries = read_trust_log(ds["trust_log"])
    assert calls["n"] >= len(entries) * 2, \
        f"证书链每一级都必须验证(每条签名 + 每条准入证书),实际 {calls['n']} 次"


def test_unrevoked_but_unadmitted_key_rejected(repo_root, bootstrap, deploy_setup,
                                               anchors_mod, monkeypatch, tmp_path):
    """④ 预测红集:只查「不在废止集合内」而不验准入链的实现在此例下必然放行。

    K_att 从未出现在任何废止声明里(「未被废止」天然成立),但无准入证书 → 拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    migrate_deploy(bootstrap, ds, repo_root)
    entries = read_trust_log(ds["trust_log"])
    e2 = entries[-1]
    att_sk, _, att_pub = gen_keypair(tmp_path, "Katt")
    # 构造 E3'' 由 K_att 签,准入证书由 K_att 自签(无 root 回溯)
    gen3 = ds["generations"] / "3"
    gen3.mkdir(mode=0o750)
    manifest3 = json.loads((ds["generations"] / "2" / "manifest").read_text())
    manifest3["epoch"] = 3
    manifest3["generation_dir"] = "generations/3"
    (gen3 / "manifest").write_text(json.dumps(manifest3, indent=1))
    os.chmod(gen3 / "manifest", 0o440)
    subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(att_sk),
         "-out", str(gen3 / "manifest.sig"), str(gen3 / "manifest")],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    os.chmod(gen3 / "manifest.sig", 0o440)
    (gen3 / "public_key").write_bytes(att_pub)
    os.chmod(gen3 / "public_key", 0o440)
    bmod = load_bootstrap_mod()
    c = {"generations": ds["generations"]}
    body = {
        "deployment_id": "dep-1",
        "epoch": 3,
        "generation_dir": "generations/3",
        "bundle": {
            "manifest_sha256": sha256_file(gen3 / "manifest"),
            "signature_sha256": sha256_file(gen3 / "manifest.sig"),
            "public_key_sha256": sha256_file(gen3 / "public_key"),
        },
        # 准入证书由 K_att 自签(攻击者自证,无回溯)
        "admission": bmod.build_admission(c, 3, bmod.key_id_of_pem(att_pub), att_pub, att_sk),
        "revocation": {"prev_key_id": e2["admission"]["key_id"], "revoked_at_epoch": 3},
        "prev_entry_hash": entry_hash(e2),
    }
    body["signature"] = sign_bytes_offline(att_sk, bmod.entry_body_bytes(body))
    with open(ds["trust_log"], "ab") as f:
        f.write(json.dumps(body, sort_keys=True).encode() + b"\n")
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert "准入" in str(ei.value), f"拒因必须点名准入(未废止不等于已准入): {ei.value}"


def test_admission_cert_missing_rejected(repo_root, bootstrap, deploy_setup,
                                         anchors_mod, monkeypatch, tmp_path):
    """配套:准入证书缺失 → 拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    migrate_deploy(bootstrap, ds, repo_root)
    entries = read_trust_log(ds["trust_log"])
    e2 = entries[-1]
    gen3 = ds["generations"] / "3"
    gen3.mkdir(mode=0o750)
    manifest3 = json.loads((ds["generations"] / "2" / "manifest").read_text())
    manifest3["epoch"] = 3
    manifest3["generation_dir"] = "generations/3"
    (gen3 / "manifest").write_text(json.dumps(manifest3, indent=1))
    os.chmod(gen3 / "manifest", 0o440)
    (gen3 / "manifest.sig").write_bytes(b"\x00" * 10)  # 占位,摘要以磁盘为准
    os.chmod(gen3 / "manifest.sig", 0o440)
    _, _, k3_pub = gen_keypair(tmp_path, "K3miss")
    (gen3 / "public_key").write_bytes(k3_pub)
    os.chmod(gen3 / "public_key", 0o440)
    bmod = load_bootstrap_mod()
    c = {"generations": ds["generations"]}
    admission = bmod.build_admission(c, 3, bmod.key_id_of_pem(k3_pub), k3_pub, e2 and ds["signer_keys"] / "K2.sk")
    del admission["certificate"]  # 准入证书缺失
    body = {
        "deployment_id": "dep-1",
        "epoch": 3,
        "generation_dir": "generations/3",
        "bundle": {
            "manifest_sha256": sha256_file(gen3 / "manifest"),
            "signature_sha256": sha256_file(gen3 / "manifest.sig"),
            "public_key_sha256": sha256_file(gen3 / "public_key"),
        },
        "admission": admission,
        "revocation": {"prev_key_id": e2["admission"]["key_id"], "revoked_at_epoch": 3},
        "prev_entry_hash": entry_hash(e2),
    }
    body["signature"] = sign_bytes_offline(ds["signer_keys"] / "K2.sk", bmod.entry_body_bytes(body))
    with open(ds["trust_log"], "ab") as f:
        f.write(json.dumps(body, sort_keys=True).encode() + b"\n")
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert "准入" in str(ei.value), f"拒因必须点名准入: {ei.value}"


def test_admission_chain_broken_rejected(repo_root, bootstrap, deploy_setup,
                                         anchors_mod, monkeypatch, tmp_path):
    """配套:准入证书链断裂(中间某代证书的前驱 key-id 对不上)→ 拒。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    migrate_deploy(bootstrap, ds, repo_root)
    entries = read_trust_log(ds["trust_log"])
    e2 = entries[-1]
    gen3 = ds["generations"] / "3"
    gen3.mkdir(mode=0o750)
    manifest3 = json.loads((ds["generations"] / "2" / "manifest").read_text())
    manifest3["epoch"] = 3
    manifest3["generation_dir"] = "generations/3"
    (gen3 / "manifest").write_text(json.dumps(manifest3, indent=1))
    os.chmod(gen3 / "manifest", 0o440)
    (gen3 / "manifest.sig").write_bytes(b"\x00" * 10)
    os.chmod(gen3 / "manifest.sig", 0o440)
    _, _, k3_pub = gen_keypair(tmp_path, "K3brk")
    (gen3 / "public_key").write_bytes(k3_pub)
    os.chmod(gen3 / "public_key", 0o440)
    bmod = load_bootstrap_mod()
    c = {"generations": ds["generations"]}
    # 证书由 K2 签(合法)但 admission 声明的 predecessor 指向不存在的 K99 → 链断
    admission = bmod.build_admission(c, 3, bmod.key_id_of_pem(k3_pub), k3_pub,
                                     ds["signer_keys"] / "K2.sk")
    admission["predecessor"] = "K99"
    body = {
        "deployment_id": "dep-1",
        "epoch": 3,
        "generation_dir": "generations/3",
        "bundle": {
            "manifest_sha256": sha256_file(gen3 / "manifest"),
            "signature_sha256": sha256_file(gen3 / "manifest.sig"),
            "public_key_sha256": sha256_file(gen3 / "public_key"),
        },
        "admission": admission,
        "revocation": {"prev_key_id": e2["admission"]["key_id"], "revoked_at_epoch": 3},
        "prev_entry_hash": entry_hash(e2),
    }
    body["signature"] = sign_bytes_offline(ds["signer_keys"] / "K2.sk", bmod.entry_body_bytes(body))
    with open(ds["trust_log"], "ab") as f:
        f.write(json.dumps(body, sort_keys=True).encode() + b"\n")
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert "准入" in str(ei.value), f"拒因必须点名准入链断裂: {ei.value}"


# ---------------------------------------------------------------------------
# ⑤ symlink 替换与普通目录替换同等对待(实现只有 O_NOFOLLOW 不构成防线)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("replacement", ["plain-dir", "symlink-original", "symlink-evil"])
def test_leaf_replaced_any_form_rejected(repo_root, bootstrap, deploy_setup,
                                         anchors_mod, monkeypatch, replacement):
    """预测红集:把「普通目录替换」与「symlink 替换」区别对待的实现,在 symlink
    形态下必然放行。三种形态一律 AnchorDriftError,且零现场重新登记。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    target = Path(ds["roots"]["batch"])
    moved = target.with_name(target.name + "-moved")
    os.rename(target, moved)
    evil = ds["tree"] / "evil-symlink"
    if replacement == "plain-dir":
        os.mkdir(target, 0o750)
    elif replacement == "symlink-original":
        os.symlink(moved, target)
    else:
        evil.mkdir(mode=0o750)
        os.symlink(evil, target)
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)
    if replacement == "symlink-evil":
        assert_zero_files_in(evil)


def test_cold_start_symlink_replace_rejects(repo_root, bootstrap, deploy_setup,
                                            anchors_mod):
    """⑤ 冷启动(symlink 替换根本身):真实子进程重启,与普通目录替换同等对待。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    r = sub_load_verified(ds, repo_root)
    assert r.returncode == 0, f"基线 load_verified 应成功: {r.stdout} {r.stderr}"
    target = Path(ds["roots"]["raw_dir"])
    moved = target.with_name(target.name + "-moved")
    os.rename(target, moved)
    os.symlink(moved, target)
    r2 = sub_load_verified(ds, repo_root)
    assert r2.returncode != 0 and "AnchorDriftError" in r2.stderr


# ---------------------------------------------------------------------------
# ⑥ 四根各自:「存活进程遇中间级替换 → 下一次取用即 fail-closed、零写入」
# ---------------------------------------------------------------------------


def _deepen_all_roots(ds: dict) -> None:
    """把四根各自放入独立中间级目录(每根 chain = [mid-<sub>, <sub>]),更新
    roots.json;bootstrap init 按新布局扫描生成 manifest。"""
    tree = ds["tree"]
    roots = dict(ds["roots"])
    for name, sub in (("raw_dir", "raw"), ("stage", "stage"),
                      ("locks_dir", "locks"), ("batch", "batch")):
        mid = tree / f"mid-{sub}"
        mid.mkdir(mode=0o750)
        os.rename(tree / sub, mid / sub)
        roots[name] = str(mid / sub)
    os.chmod(ds["roots_config"], 0o600)  # 部署侧放开只读后重写,再恢复 0400
    ds["roots_config"].write_text(json.dumps(roots, indent=1))
    os.chmod(ds["roots_config"], 0o400)
    ds["roots"] = roots


@pytest.mark.parametrize("root_name", ["raw_dir", "stage", "locks_dir", "batch"])
def test_live_process_mid_chain_replacement_fail_closed(repo_root, bootstrap,
                                                        deploy_setup, anchors_mod,
                                                        monkeypatch, root_name):
    """预测红集:跨调用缓存任何一级 fd 或验证结果的实现在此例下必然放行。

    存活进程内首次 load_verified 成功后,替换该根 parent chain 的中间级
    (叶 inode 不变)→ 第二次调用必须 fail-closed,且替换后的中间级零写入。
    """
    ds = deploy_setup()
    _deepen_all_roots(ds)
    init_deploy(bootstrap, ds, repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    assert sorted(handles) == ["batch", "locks_dir", "raw_dir", "stage"]
    for h in handles.values():
        h.close()

    sub = {"raw_dir": "raw", "stage": "stage",
           "locks_dir": "locks", "batch": "batch"}[root_name]
    mid = ds["tree"] / f"mid-{sub}"
    os.rename(mid, mid.with_name(mid.name + "-moved"))
    os.mkdir(mid, 0o750)  # 同名普通目录;叶 inode 未变
    with pytest.raises(anchors_mod.AnchorDriftError):
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert_zero_files_in(mid)


# ---------------------------------------------------------------------------
# (3b)⑥ generation 目录名不得逃逸(前缀锚定 + 逐级 no-follow)
# ---------------------------------------------------------------------------


def test_generation_dir_escape_rejected(repo_root, bootstrap, deploy_setup,
                                        anchors_mod, monkeypatch, tmp_path):
    """预测红集:只比对 bundle 摘要、不锚定目录名落在 generations/ 之内的
    实现在此例下必然放行。

    构造签名完全有效、bundle 摘要逐字自洽、唯独 generation_dir 声明逃逸
    (字符串前缀仍在 generations/ 内,路径解析越过边界)的 E2 候选条目 →
    拒因必须点名 generation 目录声明;漏检实现会继续走到 parent chain 并放行。
    """
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    bmod = load_bootstrap_mod()
    e1 = read_trust_log(ds["trust_log"])[0]
    k1_sk = ds["signer_keys"] / "K1.sk"
    _, _, k2_pub = gen_keypair(tmp_path, "K2esc")
    gen2 = ds["generations"] / "2"
    gen2.mkdir(mode=0o750)
    manifest2 = json.loads((ds["generations"] / "1" / "manifest").read_text())
    manifest2["epoch"] = 2
    manifest2["generation_dir"] = "generations/2"
    (gen2 / "manifest").write_text(json.dumps(manifest2, indent=1))
    os.chmod(gen2 / "manifest", 0o440)
    subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(k1_sk),
         "-out", str(gen2 / "manifest.sig"), str(gen2 / "manifest")],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    os.chmod(gen2 / "manifest.sig", 0o440)
    (gen2 / "public_key").write_bytes(k2_pub)
    os.chmod(gen2 / "public_key", 0o440)
    c = {"generations": ds["generations"]}
    body = {
        "deployment_id": "dep-1",
        "epoch": 2,
        # 逃逸声明:前缀在 generations/ 内,但 .. 解析越过 generations/ 边界
        "generation_dir": "generations/2/../evil",
        "bundle": {
            "manifest_sha256": sha256_file(gen2 / "manifest"),
            "signature_sha256": sha256_file(gen2 / "manifest.sig"),
            "public_key_sha256": sha256_file(gen2 / "public_key"),
        },
        "admission": bmod.build_admission(c, 2, bmod.key_id_of_pem(k2_pub),
                                          k2_pub, k1_sk),
        "revocation": {"prev_key_id": e1["admission"]["key_id"],
                       "revoked_at_epoch": 2},
        "prev_entry_hash": entry_hash(e1),
    }
    body["signature"] = sign_bytes_offline(k1_sk, bmod.entry_body_bytes(body))
    with open(ds["trust_log"], "ab") as f:
        f.write(json.dumps(body, sort_keys=True).encode() + b"\n")
    after_append = sha256_file(ds["trust_log"])
    with pytest.raises(anchors_mod.AnchorDriftError) as ei:
        call_load_verified(monkeypatch, anchors_mod, ds)
    assert "generation" in str(ei.value).lower(), \
        f"拒因必须点名 generation 目录声明: {ei.value}"
    assert sha256_file(ds["trust_log"]) == after_append, "零写盘"


# ---------------------------------------------------------------------------
# AnchorHandle fd 生命周期:事务结束即弃用(close / with 块;幂等)
# ---------------------------------------------------------------------------


def test_anchor_handle_close_releases_fds(repo_root, bootstrap, deploy_setup,
                                          anchors_mod, monkeypatch):
    """close() 释放 root_fd / parent_fd;重复 close 幂等。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    for h in handles.values():
        os.fstat(h.root_fd)
        os.fstat(h.parent_fd)
        h.close()
        with pytest.raises(OSError):
            os.fstat(h.root_fd)
        with pytest.raises(OSError):
            os.fstat(h.parent_fd)
        h.close()  # 幂等:重复 close 不抛


def test_anchor_handle_context_manager(repo_root, bootstrap, deploy_setup,
                                       anchors_mod, monkeypatch):
    """with 块退出即关闭 fd(事务边界的惯用写法)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    handles = call_load_verified(monkeypatch, anchors_mod, ds)
    with handles["raw_dir"] as h:
        os.fstat(h.root_fd)
        os.fstat(h.parent_fd)
    with pytest.raises(OSError):
        os.fstat(handles["raw_dir"].root_fd)


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="需 /proc 数 fd(Linux CI)")
def test_no_fd_accumulation_with_close(repo_root, bootstrap, deploy_setup,
                                       anchors_mod, monkeypatch):
    """回归:循环「取用 → close」不累积 fd(长驻循环防泄漏;一次性 CLI 无感知)。"""
    ds = init_deploy(bootstrap, deploy_setup(), repo_root)
    call_load_verified(monkeypatch, anchors_mod, ds)  # 预热(openssl 子进程等一次性开销)
    n0 = len(os.listdir("/proc/self/fd"))
    for _ in range(20):
        for h in call_load_verified(monkeypatch, anchors_mod, ds).values():
            h.close()
    n1 = len(os.listdir("/proc/self/fd"))
    assert n1 - n0 <= 2, f"循环取用+close 后 fd 应基本持平: {n0} → {n1}"
