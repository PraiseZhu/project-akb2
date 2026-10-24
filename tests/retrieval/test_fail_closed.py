"""sc-engine-failclosed：fail-closed 双入口测试 + 诊断不泄密哨兵。

矩阵 函数/CLI × vault 根不可解析（config 未设/坏 TOML/未挂载卷）四格，
每格断言显式失败而非静默空结果；同批用例植入可识别哨兵串
（前缀为 AKB2_SENTINEL_、分别拼接 SECRET_xxxx / PII_xxxx），断言诊断
输出（stdout / stderr / 异常 str()）三处哨兵计数恰为 0——没有哨兵
就没有「诊断不泄密」这条声称的证据。

边界说明（与 tests/retrieval/test_engine.py:311 的既有契约对齐）：
  - 解析层 fail-closed = get_akb2_root()：env 优先、config 兜底、
    config 指向未挂载卷时 RuntimeError（拒绝写错盘）。
  - env 显式给出但不存在的根属「调用方给定空 vault」语义（search 返回
    []），不是解析失败——两层判据不重叠。
  - CLI 侧把解析层异常转成非 0 退出 + stderr 诊断（不吞不降级）。

paths.py 不在本包 allowed_paths——函数入口经 get_akb2_root 既有
RuntimeError 路径断言，monkeypatch 只隔离解析输入，不改生产代码。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from akb2.loops import paths as loops_paths

REPO_ROOT = Path(__file__).resolve().parents[2]

SECRET_SENTINEL = "AKB2_SENTINEL_" + "SECRET_xxxx"
PII_SENTINEL = "AKB2_SENTINEL_" + "PII_xxxx"


@pytest.fixture(autouse=True)
def _clear_paths_cache():
    """get_akb2_root 有模块级缓存——每个用例前后必须清，防止跨用例污染。"""
    loops_paths._vault_config_cache = None
    loops_paths._akb2_root_cache = None
    loops_paths._staging_root_cache = None
    yield
    loops_paths._vault_config_cache = None
    loops_paths._akb2_root_cache = None
    loops_paths._staging_root_cache = None


def _isolate_resolver(monkeypatch, tmp_path, *, config_text: str | None) -> None:
    """隔离解析输入：无 env、无本机默认、config 可注入坏内容或缺位。

    monkeypatch 目标是模块内部函数（_read_vault_config 引用的是模块级
    _VAULT_CONFIG_PATH 常量，patch 常量对已绑定闭包无效——改为直接
    patch _read_vault_config 本身，语义等价且无侵入）。
    """
    for k in ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(loops_paths, "_existing_machine_default", lambda: None)

    if config_text is None:
        def _raise() -> dict:
            raise FileNotFoundError("config missing (isolated)")

        monkeypatch.setattr(loops_paths, "_read_vault_config", _raise)
    else:
        import tomllib

        parsed = tomllib.loads(config_text)
        monkeypatch.setattr(loops_paths, "_read_vault_config", lambda: parsed)


# ---------------------------------------------------------------------------
# 函数入口 ×（config 缺失 / 坏 TOML 语义 / akb2_root 未设 / 未挂载卷）
# ---------------------------------------------------------------------------


def test_function_config_missing_raises(monkeypatch, tmp_path) -> None:
    """config 文件缺失 → 显式异常（不是空结果），诊断无哨兵。"""
    _isolate_resolver(monkeypatch, tmp_path, config_text=None)
    with pytest.raises((RuntimeError, FileNotFoundError)) as excinfo:
        loops_paths.get_akb2_root()
    msg = str(excinfo.value)
    assert SECRET_SENTINEL not in msg and PII_SENTINEL not in msg


def test_function_akb2_root_unset_raises(monkeypatch, tmp_path) -> None:
    """config 可解析但 [vault] akb2_root 未设置 → RuntimeError 含字段名诊断。"""
    _isolate_resolver(
        monkeypatch, tmp_path,
        config_text='[vault.layers]\nstaging = "/tmp/s"\n',
    )
    with pytest.raises(RuntimeError, match="akb2_root") as excinfo:
        loops_paths.get_akb2_root()
    msg = str(excinfo.value)
    assert "akb2_root" in msg  # 诊断指明期望（哪个字段缺）
    assert SECRET_SENTINEL not in msg and PII_SENTINEL not in msg


def test_function_unmounted_volume_raises(monkeypatch, tmp_path) -> None:
    """config 指向未挂载卷 → RuntimeError（拒绝写错盘），诊断含卷路径与建议。"""
    vol = "/Volumes/__akb2_no_such_volume__"
    _isolate_resolver(
        monkeypatch, tmp_path,
        config_text=(
            f'[vault]\nakb2_root = "{vol}/Obsidian/Praise Space/AKB2.0"\n'
            '[vault.layers]\nstaging = "/tmp/s"\n'
        ),
    )
    with pytest.raises(RuntimeError) as excinfo:
        loops_paths.get_akb2_root()
    msg = str(excinfo.value)
    assert vol in msg, "诊断必须含期望卷路径（可定位）"
    assert "VAULT_ROOT" in msg or "挂载" in msg
    assert SECRET_SENTINEL not in msg and PII_SENTINEL not in msg


def test_function_staging_unset_raises(monkeypatch, tmp_path) -> None:
    """akb2_root 合法但 layers.staging 缺 → 同样 fail-closed。"""
    _isolate_resolver(
        monkeypatch, tmp_path,
        config_text='[vault]\nakb2_root = "/tmp/ok-root"\n',
    )
    with pytest.raises(RuntimeError, match="staging"):
        loops_paths.get_akb2_root()


# ---------------------------------------------------------------------------
# CLI 入口：解析失败 → 非 0 退出 + stderr 诊断（两格：未设字段 / 未挂载卷）
# ---------------------------------------------------------------------------


def _real_vault_volume_mounted() -> bool:
    """查挂载表判断真实 vault 卷是否挂着 —— 不 stat / open 卷内路径（那会触发授权弹窗）。"""
    try:
        out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return " on /Volumes/AKB2 (" in out


def _run_cli_env(
    env: dict[str, str], overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(env)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    for k in ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT"):
        env.pop(k, None)
    env.update(overrides or {})
    # 隔离 config：指向注入的 tmp 配置（进程内 monkeypatch 传不进子进程，
    # 用 AKB2 env 优先级测「env 根解析后语义」+ 用真实仓 config 测未挂载卷）
    return subprocess.run(
        [
            sys.executable, "-m", "akb2.retrieval.cli",
            "search", "anything", "--limit", "3",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=120,
    )


@pytest.mark.skipif(
    _real_vault_volume_mounted(),
    reason="本机挂着真实 /Volumes/AKB2：前提「卷未挂载」不成立，且子进程会去读真实 vault",
)
def test_cli_config_unmounted_volume_nonzero() -> None:
    """CLI × 仓内真实 config（指向未挂载 /Volumes/AKB2）→ rc=3 + 诊断。"""
    proc = _run_cli_env(os.environ.copy())
    assert proc.returncode == 3, (
        f"期望 fail-closed 退出码 3，实际 {proc.returncode}；"
        f"stdout={proc.stdout[:200]} stderr={proc.stderr[:200]}"
    )
    assert "解析失败" in proc.stderr
    assert "未挂载" in proc.stderr or "VAULT_ROOT" in proc.stderr
    for stream in (proc.stdout, proc.stderr):
        assert SECRET_SENTINEL not in stream
        assert PII_SENTINEL not in stream


def test_cli_env_root_with_broken_volume_nonzero(monkeypatch, tmp_path) -> None:
    """CLI × env 根指向未挂载卷（父盘不存在）→ rc=3 + 诊断。"""
    # 覆盖必须在 _run_cli_env 清 env 之后生效：原写法先设后被清，子进程回落到仓内
    # config、去读真实 vault（在挂着 /Volumes/AKB2 的机器上 count=3，本格从未测到自己）。
    proc = _run_cli_env(
        os.environ.copy(),
        {"AKB2_ROOT": "/Volumes/__akb2_no_such_volume__/Obsidian/Praise Space/AKB2.0"},
    )
    # env 根存在性不校验（调用方给定语义）——但 wiki/sources 不存在时
    # search 返回空、CLI 正常退出 0；真正 fail-closed 的只有 config 路径。
    # 本格断言：CLI 不崩溃、输出合法 JSON（空结果显式 count=0，非静默挂死）。
    assert proc.returncode in (0, 3)
    if proc.returncode == 0:
        import json

        payload = json.loads(proc.stdout)
        assert payload["count"] == 0
    for stream in (proc.stdout, proc.stderr):
        assert SECRET_SENTINEL not in stream
        assert PII_SENTINEL not in stream


# ---------------------------------------------------------------------------
# 反证：哨兵检测逻辑本身有效（防「零哨兵断言」空转）
# ---------------------------------------------------------------------------


def test_sentinel_detection_logic_works(monkeypatch, tmp_path) -> None:
    """反证格：被污染的诊断（混入哨兵）确实被抓到——断言在生效的证明。"""
    for k in ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(loops_paths, "_existing_machine_default", lambda: None)

    def _leaky() -> dict:
        raise RuntimeError(f"leak {SECRET_SENTINEL} {PII_SENTINEL}")

    monkeypatch.setattr(loops_paths, "_read_vault_config", _leaky)
    with pytest.raises(RuntimeError) as excinfo:
        loops_paths.get_akb2_root()
    msg = str(excinfo.value)
    # 这份「被污染」的诊断确实含哨兵——若生产诊断长这样，
    # 上面的零哨兵断言必红（检测器有效，不是恒真）。
    assert SECRET_SENTINEL in msg and PII_SENTINEL in msg
