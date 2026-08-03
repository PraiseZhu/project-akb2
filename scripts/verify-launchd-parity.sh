#!/usr/bin/env bash
# verify-launchd-parity.sh — readonly launchd parity check for repo templates vs installed agents

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# manifest 路径可覆写：仅为让「检查真的会咬人」这件事可被端到端反证
# （一个扫真实目录、啥也没找到就绿的检查，与一个坏掉的检查在结果上无法区分）。
# 生产调用不传该变量，行为不变。
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


def _schedule_matches(plist_dict: dict[str, Any], expected: dict[str, Any]) -> bool:
    kind = expected["kind"]
    if kind == "keepalive":
        return plist_dict.get("KeepAlive") is True
    if kind == "interval":
        return plist_dict.get("StartInterval") == expected["seconds"]
    if kind == "calendar":
        actual = plist_dict.get("StartCalendarInterval")
        if isinstance(actual, dict):
            actual_entries = [actual]
        else:
            actual_entries = list(actual or [])
        expected_entries = expected["entries"]
        return _normalize_value(actual_entries) == _normalize_value(expected_entries)
    raise ValueError(f"unknown schedule kind: {kind}")


def _infer_vault_root_base(installed_dict: dict[str, Any], manifest_defaults: dict[str, Any]) -> str:
    env = installed_dict.get("EnvironmentVariables", {})
    if isinstance(env, dict):
        for key in ("AKB2_VAULT_ROOT", "AKB2_ROOT"):
            raw = env.get(key)
            if isinstance(raw, str) and raw:
                suffix = "/Praise Space/AKB2.0"
                return raw[:-len(suffix)] if raw.endswith(suffix) else raw
    args = installed_dict.get("ProgramArguments", [])
    if isinstance(args, list):
        for idx, arg in enumerate(args[:-1]):
            if arg == "--vault-root" and isinstance(args[idx + 1], str):
                return args[idx + 1]
    return str(manifest_defaults["vault_root_base"])


def _canonical_repo_root(path: Path) -> Path:
    parts = path.parts
    if ".cindy-worktrees" in parts:
        idx = parts.index(".cindy-worktrees")
        return Path(*parts[:idx])
    return path


def _infer_repo_root(installed_dict: dict[str, Any], fallback_root: Path) -> Path:
    working_dir = installed_dict.get("WorkingDirectory")
    if isinstance(working_dir, str) and working_dir.startswith("/"):
        return Path(working_dir).resolve()
    args = installed_dict.get("ProgramArguments", [])
    if isinstance(args, list):
        for arg in args:
            if isinstance(arg, str) and arg.startswith("/") and "/scripts/" in arg:
                return Path(arg).resolve().parents[1]
    return fallback_root


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
_ESCAPE_HATCH_ENV_KEYS = ("AKB2_ALLOW_WORKTREE_INSTALL", "AKB2_ALLOW_TMP_VAULT_ROOT")


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


repo_root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
defaults = manifest["defaults"]
default_compare_root = _canonical_repo_root(repo_root)

failures = 0
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
            file_status = "MISSING"
            result = "MISSING"
            details.append("pending_human_install")
        else:
            file_status = "MISSING"
            result = "MISSING"
            details.append("installed_file_missing")

        if file_status != "MISSING":
            assert installed_plist is not None
            vault_root_base = _infer_vault_root_base(installed_plist, defaults)
            render_root = _infer_repo_root(installed_plist, default_compare_root)
            repo_plist = _render_template(
                template_path,
                repo_root=render_root,
                home=Path.home(),
                vault_root_base=vault_root_base,
                manifest_defaults=defaults,
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

            if not _schedule_matches(repo_plist, job["expected_schedule"]):
                file_status = "DRIFT"
                result = "DRIFT"
                details.append("repo_schedule_mismatch")
            if installed_plist and not _schedule_matches(installed_plist, job["expected_schedule"]):
                file_status = "DRIFT"
                result = "DRIFT"
                details.append("installed_schedule_mismatch")

            if file_status == "MATCH":
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
        result = "MISSING"
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

    if result != "MATCH":
        failures += 1

    detail_suffix = ""
    if details:
        detail_suffix = " reason=" + "; ".join(details)
    print(
        f"check={check} result={result} label={label} "
        f"file={file_status} loaded={loaded_status}{detail_suffix}"
    )

raise SystemExit(0 if failures == 0 else 1)
PY
