#!/usr/bin/env python3
"""e2e_precheck — 端到端前置环境闸（sc-e2e-preconditions）。

按解析出的 vault 根做存在性与可遍历性检查，用退出码分开三种终态：
  exit 0  E2E_PRECHECK_OK root=<路径> md_files=<N>   环境就绪（N>0）
  exit 3  E2E_INFRA_FAIL                               环境未就绪（未挂载/
                                                            根为空/不可遍历）
                                                            ——停止本轮验收
                                                            但不判任务失败
  exit 1  E2E_CONFIG_FAIL                              配置本身有误（文件
                                                            缺失、TOML 坏、
                                                            akb2_root 未配置）

『未挂载算 infra_fail 不算任务失败』由退出码协议承载（台账
silenced-checkout-error-yields-phantom-zero-measurement：错误被吞会产出
假 0/假绿）；环境未就绪时绝不继续跑检索——零结果与未挂载必须可区分。

只读契约：本脚本不写任何文件（诊断走 stdout）。
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

EXIT_OK = 0
EXIT_CONFIG_FAIL = 1
EXIT_INFRA_FAIL = 3

_AKB2_RELATIVE_PARTS = ("Praise Space", "AKB2.0")


def _normalise(raw: str) -> Path:
    expanded = Path(os.path.expandvars(raw)).expanduser()
    if expanded.name == "AKB2.0":
        return expanded.resolve()
    return expanded.joinpath(*_AKB2_RELATIVE_PARTS).resolve()


def resolve_root_from_config(config_path: Path) -> Path:
    """读 TOML 配置解析 akb2_root（env 未设时的兜底口径与 paths.py 一致）。

    配置错误抛 PrecheckConfigError（→ exit 1）。
    """
    if not config_path.is_file():
        raise PrecheckConfigError(f"配置文件不存在: {config_path}")
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise PrecheckConfigError(f"TOML 解析失败: {config_path}: {exc}") from exc
    vault = config.get("vault", {})
    if not isinstance(vault, dict):
        raise PrecheckConfigError("[vault] 段不是 table")
    raw = str(vault.get("akb2_root", "")).strip()
    if not raw:
        raise PrecheckConfigError(
            f"{config_path}: [vault] akb2_root 未设置，无法解析 vault 根"
        )
    return _normalise(raw)


class PrecheckConfigError(RuntimeError):
    """配置级错误（exit 1）：文件缺失 / TOML 坏 / akb2_root 未配置。"""


class PrecheckInfraError(RuntimeError):
    """环境级错误（exit 3）：未挂载 / 根为空 / 不可遍历。"""


def check_root(root: Path) -> int:
    """对已解析的根做存在性/可遍历性检查，返回 md 文件数。

    环境未就绪抛 PrecheckInfraError。
    """
    # 未挂载卷检测（与 paths._assert_volume_mounted 同口径）
    parts = root.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        vol_root = Path("/", parts[1], parts[2])
        if not vol_root.exists():
            raise PrecheckInfraError(
                f"vault 根指向未挂载的卷 {vol_root}（root={root}）"
            )
    if not root.exists():
        raise PrecheckInfraError(f"vault 根不存在: {root}")
    if not root.is_dir():
        raise PrecheckInfraError(f"vault 根不是目录: {root}")

    sources = root / "wiki" / "sources"
    try:
        md_files = sum(1 for _ in sources.rglob("*.md"))
    except OSError as exc:
        raise PrecheckInfraError(f"vault 不可遍历: {sources}: {exc}") from exc
    if md_files == 0:
        raise PrecheckInfraError(
            f"wiki/sources 为空（md_files=0，root={root}）——零结果与未就绪必须可区分"
        )
    return md_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="e2e_precheck",
        description="端到端前置环境闸：0=就绪 / 3=infra_fail / 1=config_fail",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="vault TOML 配置路径（默认仓内 config/vault.toml）",
    )
    args = parser.parse_args(argv)

    try:
        if args.config is not None:
            config_path = Path(args.config).expanduser()
        else:
            config_path = Path(__file__).resolve().parents[1] / "config" / "vault.toml"
        root = resolve_root_from_config(config_path)
    except PrecheckConfigError as exc:
        print(f"E2E_CONFIG_FAIL {exc}")
        return EXIT_CONFIG_FAIL

    try:
        md_files = check_root(root)
    except PrecheckInfraError as exc:
        print(f"E2E_INFRA_FAIL {exc}")
        return EXIT_INFRA_FAIL

    print(f"E2E_PRECHECK_OK root={root} md_files={md_files}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
