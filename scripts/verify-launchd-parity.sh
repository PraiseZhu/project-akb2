#!/usr/bin/env bash
# verify-launchd-parity.sh — readonly launchd parity check for repo templates vs installed agents
#
# ── 退出码（宪法 P4：不同语义不得共用一个码）─────────────────────────────────
#   0 = 全部 MATCH（或只有已批准的 pending，见下）
#   1 = 真 DRIFT / 非预期 MISSING —— 需要修
#   3 = 只有**已批准的 pending_human_install**，其余全 MATCH。
#       为什么单列一个码：manifest 把 inflow-sentinel 登记为 pending_human_install
#       且 docs/allowed-residuals.md 已批准其未安装，而 post-change-pipeline --write
#       把本脚本当硬门 —— 旧实现对 pending 无条件计 failure，于是**任何**无关的 write
#       pipeline 都结构性失败，合法状态下 gate 无解（CADV-G4）。
#       pending 必须可诊断（打印出来、带 pending_days）但不能让无关 gate 永久无解。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# manifest 路径可覆写：仅为让「检查真的会咬人」这件事可被端到端反证
# （一个扫真实目录、啥也没找到就绿的检查，与一个坏掉的检查在结果上无法区分）。
# 生产调用不传该变量，行为不变。反证测试在 tests/test_verify_launchd_parity.py。
MANIFEST="${AKB2_PARITY_MANIFEST:-$ROOT/launchd/INSTALL-MANIFEST.yaml}"

python3 - "$ROOT" "$MANIFEST" <<'PY'
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import yaml


def _strip_xml_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _load_plist(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return plistlib.loads(text.encode("utf-8"))
    except Exception:
        return plistlib.loads(_strip_xml_comments(text).encode("utf-8"))


def _normalize_string(value: str) -> str:
    if value != "/" and value.endswith("/"):
        return value.rstrip("/")
    return value


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, str):
        return _normalize_string(value)
    return value


def _env_subset(
    plist_dict: dict[str, Any],
    *,
    ignore_keys: set[str],
) -> dict[str, str]:
    env = plist_dict.get("EnvironmentVariables", {})
    if not isinstance(env, dict):
        return {}
    trimmed = {k: str(v) for k, v in env.items() if k not in ignore_keys}
    return {key: trimmed[key] for key in sorted(trimmed)}


def _diff_dicts(repo_dict: dict[str, Any], installed_dict: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    all_keys = sorted(set(repo_dict) | set(installed_dict))
    for key in all_keys:
        repo_value = repo_dict.get(key, "<missing>")
        installed_value = installed_dict.get(key, "<missing>")
        if repo_value != installed_value:
            if key == "EnvironmentVariables" and isinstance(repo_value, dict) and isinstance(
                installed_value, dict
            ):
                env_keys = sorted(set(repo_value) | set(installed_value))
                for env_key in env_keys:
                    repo_env = repo_value.get(env_key, "<missing>")
                    installed_env = installed_value.get(env_key, "<missing>")
                    if repo_env != installed_env:
                        differences.append(
                            f"{env_key} repo={repo_env} installed={installed_env}"
                        )
            else:
                differences.append(f"{key} repo={repo_value!r} installed={installed_value!r}")
    return differences


def _schedule_of(plist_dict: dict[str, Any], kind: str) -> Any:
    """按 kind 抽出该 plist 的调度值（用于三方归因输出，不做判定）。"""
    if kind == "keepalive":
        return plist_dict.get("KeepAlive")
    if kind == "interval":
        return plist_dict.get("StartInterval")
    if kind == "calendar":
        actual = plist_dict.get("StartCalendarInterval")
        entries = [actual] if isinstance(actual, dict) else list(actual or [])
        return _normalize_value(entries)
    raise ValueError(f"unknown schedule kind: {kind}")


def _expected_schedule_value(expected: dict[str, Any]) -> Any:
    kind = expected["kind"]
    if kind == "keepalive":
        return True
    if kind == "interval":
        return expected["seconds"]
    if kind == "calendar":
        return _normalize_value(expected["entries"])
    raise ValueError(f"unknown schedule kind: {kind}")


def _schedule_matches(plist_dict: dict[str, Any], expected: dict[str, Any]) -> bool:
    return _schedule_of(plist_dict, expected["kind"]) == _expected_schedule_value(expected)


def _drift_direction(repo: Any, manifest: Any, installed: Any) -> str:
    """三方比对的**方向词** —— 让归因不再反向指责仓库。

    旧实现只打 `repo_schedule_mismatch` / `installed_schedule_mismatch` 两个标签，
    且一旦 file_status 变 DRIFT 就跳过真实 diff 的计算。于是「模板已改 11:00、
    manifest 与运行态还是 12:05」这种情况下：
      · installed 与 manifest 相符 → `installed_schedule_mismatch` **不触发**；
      · `repo_schedule_mismatch` 触发 → 操作员看到的字面结论是「**仓库模板错了**」；
      · 真正要看的 `repo=11:00 installed=12:05` 永远不被打印。
    一个专为「运行态与仓库不一致且无人发现」而造的检测器，在自己交付的第一天
    把归因指向了反方向，并把真实 diff 吞掉。照它的提示"修好" = 撤销事故修复。
    """
    if repo == manifest == installed:
        return "aligned"
    if repo == manifest and installed != repo:
        return "installed_stale"        # 模板与期望一致 → 该重装运行态
    if repo == installed and manifest != repo:
        return "manifest_stale"        # 运行态与模板一致 → 该更新 manifest
    if manifest == installed and repo != manifest:
        return "repo_ahead"            # 模板领先 → manifest + 运行态都要跟上
    return "all_diverged"


def _render_template(
    template_path: Path,
    repo_root: Path,
    home: Path,
    vault_root_base: str,
    manifest_defaults: dict[str, Any],
) -> dict[str, Any]:
    text = template_path.read_text(encoding="utf-8")
    replacements = {
        manifest_defaults["repo_root_placeholder"]: str(repo_root),
        manifest_defaults["home_placeholder"]: str(home),
        manifest_defaults["vault_root_placeholder"]: vault_root_base,
    }
    for placeholder, replacement in replacements.items():
        text = text.replace(str(placeholder), replacement)
    return plistlib.loads(text.encode("utf-8"))


# 放行口不得进入**运行态**的 EnvironmentVariables —— 一旦进了，安装脚本的
# fail-closed 门（worktree 守卫 / VAULT_ROOT 三门）对生产永久失效，而 launchctl 不报错、
# 运行期也看不出异常。仓库侧已有静态检查（tests/test_bootstrap_launchd.py::
# TestEscapeHatchesNotPersisted），但那只看仓库模板；本次故障的母题恰恰是
# 「运行态与仓库不一致且无人发现」，所以运行态这一侧必须由 parity 检查覆盖。
# 逃逸口 = 「写进常驻 env 就能静默改变生产行为」的 env 键。
# 分两类，两类都必须在册（加固清单第 6 类：默认 exact，subset 会让漏项通过）：
#   ① AKB2_ALLOW_*      —— 放行一道 fail-closed 门
#   ② *_PIPELINE_BIN / *_RERUN_SCRIPT / *_READER —— **直接指定要执行什么**（更强）
# 此前只登记了 ①，漏掉的正是更强的 ②：`AKB2_SOCIAL_BRIEFING_PIPELINE_BIN` 能把整条
# PIPELINE_CMD（连 --date/--vault-root 一起）替换掉，而 launchd 不会报错。
# 与 tests/test_bootstrap_launchd.py 的 ESCAPE_HATCHES 有逐字相等断言（两处不得漂移）。
_ESCAPE_HATCH_ENV_KEYS = (
    "AKB2_ALLOW_TMP_VAULT_ROOT",
    "AKB2_ALLOW_WORKTREE_INSTALL",
    "AKB2_BACKSTOP_READER",
    "AKB2_BRIEFING_RERUN_SCRIPT",
    "AKB2_GITHUB_BRIEFING_PIPELINE_BIN",
    "AKB2_GITHUB_RERUN_SCRIPT",
    "AKB2_SOCIAL_BRIEFING_PIPELINE_BIN",
)


def _escape_hatches_in(plist_dict: dict[str, Any]) -> list[str]:
    env = plist_dict.get("EnvironmentVariables") or {}
    if not isinstance(env, dict):
        return []
    return [k for k in _ESCAPE_HATCH_ENV_KEYS if k in env]


def _launchctl_loaded(label: str) -> bool:
    proc = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _canonical_repo_root(path: Path) -> Path:
    parts = path.parts
    if ".cindy-worktrees" in parts:
        idx = parts.index(".cindy-worktrees")
        return Path(*parts[:idx])
    return path


def _installed_repo_root(installed_dict: dict[str, Any]) -> str:
    """installed plist **自称**的 repo 根 —— 只作 actual，绝不当期望值。"""
    working_dir = installed_dict.get("WorkingDirectory")
    if isinstance(working_dir, str) and working_dir.startswith("/"):
        return _normalize_string(working_dir)
    args = installed_dict.get("ProgramArguments", [])
    if isinstance(args, list):
        for arg in args:
            if isinstance(arg, str) and arg.startswith("/") and "/scripts/" in arg:
                return _normalize_string(str(Path(arg).parents[1]))
    return "<unknown>"


def _installed_vault_root(installed_dict: dict[str, Any]) -> str:
    """installed plist **自称**的 vault 根 —— 只作 actual，绝不当期望值。"""
    env = installed_dict.get("EnvironmentVariables", {})
    if isinstance(env, dict):
        for key in ("AKB2_VAULT_ROOT", "AKB2_ROOT"):
            raw = env.get(key)
            if isinstance(raw, str) and raw:
                suffix = "/Praise Space/AKB2.0"
                return _normalize_string(raw[:-len(suffix)] if raw.endswith(suffix) else raw)
    args = installed_dict.get("ProgramArguments", [])
    if isinstance(args, list):
        for idx, arg in enumerate(args[:-1]):
            if arg == "--vault-root" and isinstance(args[idx + 1], str):
                return _normalize_string(args[idx + 1])
    return "<unknown>"


repo_root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
defaults = manifest["defaults"]

# ── 可信期望根**只**来自期望配置，不由待审 installed plist 自举（CADV-G3）───────
# 旧实现先从 installed plist 反推 repo/vault 根，再用它渲染 repo 模板去和 installed
# 比 —— 于是一个把 vault 指到 mktemp 目录、或把 repo 指到已删 worktree 的危险 installed
# 可以**自证 MATCH**（拿自己的值和自己比）。parity 的信任方向必须是单向的：
# 期望来自仓库配置 + manifest defaults，installed 只能作为 actual。
EXPECTED_REPO_ROOT = _canonical_repo_root(repo_root)
EXPECTED_VAULT_ROOT_BASE = _normalize_string(str(defaults["vault_root_base"]))

# 退出码语义见文件头
PARITY_PENDING_EXIT_CODE = 3

failures = 0
pending_only = 0
today = date.today()

for job in manifest["jobs"]:
    label = job["label"]
    check = f"launchd:{label}"
    template_path = repo_root / job["template"]
    installed_path = Path(job["installed"]).expanduser()
    pending = bool(job.get("pending_human_install", False))
    should_load = bool(job.get("should_load", False))
    ignore_installed_env_keys = set(job.get("ignore_installed_env_keys", []))

    file_status = "MATCH"
    result = "MATCH"
    details: list[str] = []

    repo_plist: dict[str, Any] | None = None
    installed_plist: dict[str, Any] | None = None

    try:
        if installed_path.exists():
            installed_plist = _load_plist(installed_path)
        elif pending:
            # 已批准的待人工安装：**独立结果值**（不是 MISSING/DRIFT），可诊断但不判红
            file_status = "PENDING"
            result = "EXPECTED_PENDING"
            details.append("pending_human_install")
        else:
            file_status = "MISSING"
            result = "UNEXPECTED_MISSING"
            details.append("installed_file_missing")

        if file_status not in ("MISSING", "PENDING"):
            assert installed_plist is not None
            # 期望值来自仓库配置 + manifest defaults（不由 installed 自举）
            repo_plist = _render_template(
                template_path,
                repo_root=EXPECTED_REPO_ROOT,
                home=Path.home(),
                vault_root_base=EXPECTED_VAULT_ROOT_BASE,
                manifest_defaults=defaults,
            )
            # installed 自称的根只作 actual：不一致时**显式点名**，
            # 而不是让它在下面变成十几行零散路径 diff（那样运维读不出真因）
            installed_repo_root = _installed_repo_root(installed_plist)
            if installed_repo_root not in ("<unknown>", str(EXPECTED_REPO_ROOT)):
                file_status = "DRIFT"
                result = "DRIFT"
                details.append(
                    f"installed_repo_root={installed_repo_root} "
                    f"expected={EXPECTED_REPO_ROOT} (期望来自仓库配置，不由 installed 自举)"
                )
            installed_vault_root = _installed_vault_root(installed_plist)
            if installed_vault_root not in ("<unknown>", EXPECTED_VAULT_ROOT_BASE):
                file_status = "DRIFT"
                result = "DRIFT"
                details.append(
                    f"installed_vault_root={installed_vault_root} "
                    f"expected={EXPECTED_VAULT_ROOT_BASE} (期望来自 manifest defaults)"
                )
            repo_norm = _normalize_value(deepcopy(repo_plist))
            installed_norm = _normalize_value(deepcopy(installed_plist))
            repo_norm["EnvironmentVariables"] = _env_subset(
                repo_plist,
                ignore_keys=set(),
            )
            installed_norm["EnvironmentVariables"] = _env_subset(
                installed_plist,
                ignore_keys=ignore_installed_env_keys,
            )

            expected_env = sorted(job.get("expected_env_keys", []))
            if sorted(repo_norm["EnvironmentVariables"]) != expected_env:
                file_status = "DRIFT"
                result = "DRIFT"
                details.append(
                    "repo_env_keys="
                    + ",".join(sorted(repo_norm["EnvironmentVariables"]))
                    + " expected="
                    + ",".join(expected_env)
                )
            if sorted(installed_norm["EnvironmentVariables"]) != expected_env:
                file_status = "DRIFT"
                result = "DRIFT"
                details.append(
                    "installed_env_keys="
                    + ",".join(sorted(installed_norm["EnvironmentVariables"]))
                    + " expected="
                    + ",".join(expected_env)
                )

            # ── 调度三方归因（SC-R1-13/14）────────────────────────────────
            # 三个值全打出来 + 明示差异**方向**，而不是只贴一个会被误读成
            # 「仓库错了」的 repo_schedule_mismatch 标签。
            schedule_kind = job["expected_schedule"]["kind"]
            repo_schedule = _schedule_of(repo_plist, schedule_kind)
            installed_schedule = _schedule_of(installed_plist, schedule_kind)
            manifest_schedule = _expected_schedule_value(job["expected_schedule"])
            direction = _drift_direction(
                repo_schedule, manifest_schedule, installed_schedule
            )
            if direction != "aligned":
                file_status = "DRIFT"
                result = "DRIFT"
                details.append(
                    f"schedule_drift={direction} "
                    f"repo={repo_schedule} manifest={manifest_schedule} "
                    f"installed={installed_schedule}"
                )

            # diff 计算不再被 file_status 短路：真实差异必须始终被算出来并打印。
            # 旧实现 `if file_status == "MATCH":` 让「已经 DRIFT」的 job 永远看不到
            # 真正的 repo↔installed diff —— 检测器把自己要报的东西吞掉了。
            differences = _diff_dicts(repo_norm, installed_norm)
            if differences:
                file_status = "DRIFT"
                result = "DRIFT"
                details.extend(differences[:4])
    except Exception as exc:
        file_status = "DRIFT"
        result = "DRIFT"
        details.append(f"parse_error={exc}")

    loaded = _launchctl_loaded(label)
    if pending:
        pending_since_raw = job["pending_since"]
        if isinstance(pending_since_raw, date):
            pending_since = pending_since_raw
        else:
            pending_since = date.fromisoformat(str(pending_since_raw))
        pending_days = (today - pending_since).days
        loaded_status = "PENDING"
        details.append(f"pending_days={pending_days}")
        # pending 但**文件竟然存在** = 批准的前提已不成立（已被人装上却没更新 manifest）
        # —— 那是真 DRIFT，不能继续按"已批准未安装"放过。
        if result != "EXPECTED_PENDING":
            result = "DRIFT"
            details.append(
                "pending_but_installed_exists (manifest 的 pending 登记已过期，需更新)"
            )
    elif loaded:
        loaded_status = "YES"
    else:
        loaded_status = "NO"
        if should_load:
            result = "DRIFT" if result == "MATCH" else result
            details.append("installed_not_loaded")

    if installed_plist is not None:
        hatches = _escape_hatches_in(installed_plist)
        if hatches:
            result = "DRIFT"
            details.append(
                "escape_hatch_in_installed_env=" + ",".join(hatches)
                + " (fail-closed 门对生产已失效)"
            )

    if result == "EXPECTED_PENDING":
        pending_only += 1
    elif result != "MATCH":
        failures += 1

    detail_suffix = ""
    if details:
        detail_suffix = " reason=" + "; ".join(details)
    print(
        f"check={check} result={result} label={label} "
        f"file={file_status} loaded={loaded_status}{detail_suffix}"
    )

print(f"summary drift_failures={failures} expected_pending={pending_only}")
if failures:
    raise SystemExit(1)
if pending_only:
    # 专用码：可诊断（调用方能把它当"通过但有待办"）而不让无关 write gate 永久无解
    print(
        f"NOTICE: {pending_only} 个已批准的 pending_human_install 未安装；"
        f"其余全部 MATCH → exit {PARITY_PENDING_EXIT_CODE}（非失败）"
    )
    raise SystemExit(PARITY_PENDING_EXIT_CODE)
raise SystemExit(0)
PY
