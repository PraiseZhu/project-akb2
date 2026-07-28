"""机器本地状态根解析（把账本 / 锁 / 游标挪出跨机同步范围）。

背景（缺陷 #11，2026-07-26）：
    本仓库与 Obsidian vault 都在 Syncthing 同步范围内（`~/AI-Agent/.stfolder` 实证），
    且 `docs/mac-mini-deployment-notes.md` 指导第二台机器（Mac Mini）装同一套 launchd
    plist、指向同一个同步 vault。**状态文件一旦落在同步范围内，两台机器就会各自
    read-modify-write 同一份文件**，Syncthing 只能产出 `.sync-conflict-*` 副本并双向
    分叉——`data/star_history.sqlite` 已实证丢数据（live 独有 690 条 / 冲突副本独有
    1378 条，分叉区间 2026-07-12 → 07-24）。

    因此机器本地状态（账本、锁、游标）必须放同步范围外：
        `AKB2_STATE_ROOT`（显式覆盖，部署与测试隔离用）
        > `~/Library/Application Support/akb2/`（macOS 默认，不被 Syncthing/iCloud 同步）

公开接口::

    get_state_root()                        -> Path   状态根
    get_ledger_dir()                        -> Path   <state_root>/ledgers
    get_lock_dir()                          -> Path   <state_root>/locks
    state_file_path(name, subdir=None)      -> Path   逻辑名 → 状态文件路径
    resolve_state_file(name, legacy_path=…) -> Path   路径 + 旧位置继承 + 分裂告警
    is_synced_path(path)                    -> bool   路径是否落在已知同步范围内

约定：

- 纯路径计算函数（`get_*` / `state_file_path`）**不建目录**、无副作用；只有
  `resolve_state_file` 在需要继承旧状态时建父目录。
- fail-closed（宪法 P3）：`AKB2_STATE_ROOT` 指到已知同步范围内 → 直接抛
  `RuntimeError`，不静默接受一个会再次分叉的位置。参照 `loops/paths.py`
  `_assert_volume_mounted` 的同类处理。
- 旧状态**绝不静默丢弃**（丢账本 = 全量重跑、重复入库）：新位置缺失且旧位置存在
  → **复制**继承（保留旧文件，历史即证据，不删），并向 stderr 打印一行可见提示。
- 迁移后旧位置仍在被写（mtime 更新）→ 打印账本分裂告警（宪法 P4 失败可见），
  因为那意味着还有模块没切到本 helper。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

# 状态根覆盖环境变量（部署与测试隔离统一走它）
STATE_ROOT_ENV = "AKB2_STATE_ROOT"

# macOS 默认状态根（相对 HOME），不在 Syncthing / iCloud 同步范围内
DEFAULT_STATE_ROOT_PARTS: tuple[str, ...] = ("Library", "Application Support", "akb2")

# 状态子目录名
LEDGER_SUBDIR = "ledgers"
LOCK_SUBDIR = "locks"

# 已知会被跨机同步的路径前缀（相对 HOME 的用 _synced_prefixes() 展开）
_SYNCED_HOME_RELATIVE: tuple[tuple[str, ...], ...] = (
    ("AI-Agent",),                      # Syncthing folder（~/AI-Agent/.stfolder 实证）
    ("Library", "Mobile Documents"),    # iCloud Drive
)
_SYNCED_ABSOLUTE: tuple[str, ...] = (
    "/Volumes/AKB2",                    # Obsidian vault 所在外接卷，也在同步范围内
)
# vault 路径也可能由这些环境变量指定（同样是同步目标）
_VAULT_ENV_NAMES: tuple[str, ...] = ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT")


# ---------------------------------------------------------------------------
# 同步范围判定
# ---------------------------------------------------------------------------


def _synced_prefixes() -> tuple[Path, ...]:
    """展开已知同步范围前缀（每次计算，尊重测试对 HOME/env 的覆盖）。"""
    home = Path.home()
    prefixes: list[Path] = [home.joinpath(*parts) for parts in _SYNCED_HOME_RELATIVE]
    prefixes.extend(Path(p) for p in _SYNCED_ABSOLUTE)
    for name in _VAULT_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            prefixes.append(Path(value).expanduser())
    return tuple(prefixes)


def is_synced_path(path: str | Path) -> bool:
    """path 是否落在已知跨机同步范围内（Syncthing folder / iCloud / vault 卷）。"""
    candidate = Path(path).expanduser()
    # 不用 resolve()：目标常常还不存在，且 resolve 会跟随 symlink 改变语义判断。
    candidate = Path(os.path.abspath(candidate))
    for prefix in _synced_prefixes():
        prefix_abs = Path(os.path.abspath(prefix.expanduser()))
        if candidate == prefix_abs or candidate.is_relative_to(prefix_abs):
            return True
    return False


# ---------------------------------------------------------------------------
# 状态路径
# ---------------------------------------------------------------------------


def get_state_root() -> Path:
    """机器本地状态根（绝对路径，不建目录）。

    解析顺序：`AKB2_STATE_ROOT` > `~/Library/Application Support/akb2`。

    Raises:
        RuntimeError: 解析结果落在已知同步范围内（fail-closed，宪法 P3）——
            那等于把刚搬出来的状态又搬回冲突现场。
    """
    override = os.environ.get(STATE_ROOT_ENV, "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        root = Path.home().joinpath(*DEFAULT_STATE_ROOT_PARTS)
    root = Path(os.path.abspath(root))

    if is_synced_path(root):
        raise RuntimeError(
            f"{STATE_ROOT_ENV}={root} 落在跨机同步范围内"
            f"（{', '.join(str(p) for p in _synced_prefixes())}）；"
            "状态文件在同步目录会被 Syncthing 分叉丢数据，拒绝使用（fail-closed）。"
        )
    return root


def get_ledger_dir() -> Path:
    """账本目录 `<state_root>/ledgers`（不建目录）。"""
    return get_state_root() / LEDGER_SUBDIR


def get_lock_dir() -> Path:
    """锁目录 `<state_root>/locks`（不建目录）。"""
    return get_state_root() / LOCK_SUBDIR


def state_file_path(name: str, subdir: str | None = None) -> Path:
    """给定逻辑名返回状态文件路径（不建目录、不迁移）。

    Args:
        name: 状态文件名，例如 ``"quality-ledger.jsonl"``。禁止路径分隔符。
        subdir: 状态根下的子目录，例如 ``LEDGER_SUBDIR``；None 表示直接放状态根。

    Raises:
        ValueError: name 含路径分隔符或为空（防路径穿越）。
    """
    cleaned = name.strip()
    if not cleaned or "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"非法状态文件逻辑名: {name!r}（须为单段文件名）")
    base = get_state_root()
    if subdir:
        base = base / subdir
    return base / cleaned


def resolve_state_file(
    name: str,
    *,
    subdir: str | None = None,
    legacy_path: str | Path | None = None,
) -> Path:
    """返回状态文件路径，并在需要时从旧位置继承（复制，不删旧文件）。

    行为（宪法 P4 失败/降级可见）：

    - 新位置不存在、`legacy_path` 存在 → 复制过来（`shutil.copy2` 保留 mtime），
      stderr 打印继承提示。**不删旧文件**：历史是证据，且回滚时还要用。
    - 新旧都存在、且旧位置 mtime 更新 → 打印账本分裂告警：说明还有别的模块
      在写旧位置（例如尚未迁移的 promote/requeue），静默忽略会导致判定按半份
      账本走（重复入库 / requeue 失效）。

    Args:
        name: 逻辑名（单段文件名）。
        subdir: 状态根下子目录。
        legacy_path: 旧位置（通常是 `<vault>/staging/<name>`）。

    Returns:
        新位置的绝对路径（父目录在发生继承时已创建）。
    """
    target = state_file_path(name, subdir=subdir)
    if legacy_path is None:
        return target

    legacy = Path(legacy_path).expanduser()
    try:
        legacy_exists = legacy.is_file()
    except OSError as exc:  # 卷未挂载等 → 明示，不静默当作"没有旧状态"
        print(
            f"[state_paths] 警告: 无法访问旧状态 {legacy}: {exc}；"
            f"按「无旧状态」继续，若旧状态实际存在将导致重跑",
            file=sys.stderr,
        )
        return target

    if not legacy_exists:
        return target

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, target)
        print(
            f"[state_paths] 已继承旧状态: {legacy} → {target}"
            f"（复制，旧文件保留不删）",
            file=sys.stderr,
        )
        return target

    try:
        if legacy.stat().st_mtime > target.stat().st_mtime:
            print(
                f"[state_paths] 警告: 旧状态 {legacy} 比本机状态 {target} 更新"
                f"——仍有模块在写旧位置（状态分裂），判定可能只看到半份数据",
                file=sys.stderr,
            )
    except OSError as exc:
        print(f"[state_paths] 警告: 状态 mtime 比对失败: {exc}", file=sys.stderr)
    return target
