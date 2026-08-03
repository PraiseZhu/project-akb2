from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from src.akb2.quality.smoke_validator import (
    SmokeValidationError,
    validate_env_parity,
    validate_launchd_static,
)

REPO = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = REPO / "launchd"
PLIST_NAMES = [
    "com.akb2.main-pipeline.plist",
    "com.akb2.briefing.plist",
    "com.akb2.briefing.backstop.plist",
    "com.akb2.feishu-sync.plist",
    "com.akb2.github_briefing.plist",
    "com.akb2.github_briefing.backstop.plist",
    "com.akb2.inflow-sentinel.plist",
    "com.akb2.intake.plist",
    "com.akb2.video.plist",
]
XFAIL_REASONS: dict[tuple[str, str], str] = {}
VAULT_ROOT_BASE = "/Volumes/AKB2/Obsidian"


def _maybe_xfail(plist_name: str, check_name: str) -> None:
    reason = XFAIL_REASONS.get((plist_name, check_name))
    if reason:
        pytest.xfail(reason)


def _render_plist_text(path: Path, *, repo_root: Path = REPO, home: Path | None = None) -> str:
    home_root = home or Path.home()
    text = path.read_text(encoding="utf-8")
    replacements = {
        "__AKB2_REPO_ROOT__": str(repo_root),
        "__AKB2_HOME__": str(home_root),
        "__AKB2_VAULT_ROOT__": VAULT_ROOT_BASE,
    }
    for placeholder, replacement in replacements.items():
        text = text.replace(placeholder, replacement)
    return text


def _load_rendered_plist(path: Path) -> dict[str, Any]:
    return plistlib.loads(_render_plist_text(path).encode("utf-8"))


def _write_plist(
    path: Path,
    script: Path,
    workdir: Path,
    log_path: Path,
    *,
    auto_promote: str = "0",
    amendment: str = "0",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": "com.akb2.main-pipeline",
        "ProgramArguments": ["/bin/bash", str(script)],
        "WorkingDirectory": str(workdir),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {
            "AKB2_AUTO_PROMOTE": auto_promote,
            "AKB2_AMENDMENT_APPLIED": amendment,
        },
    }
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)


def _program_target(data: dict[str, Any]) -> tuple[str, Path | str]:
    args = data["ProgramArguments"]
    if len(args) >= 3 and args[1] == "-m":
        return ("module", str(args[2]))
    for arg in args[1:]:
        if isinstance(arg, str) and arg.startswith(str(REPO)):
            return ("path", Path(arg))
    raise AssertionError(f"could not resolve program target from args: {args}")


def _module_target_exists(module_name: str) -> bool:
    rel = Path(*module_name.split("."))
    return (
        (REPO / "src" / rel).is_dir()
        or (REPO / "src" / rel.with_suffix(".py")).exists()
        or (REPO / "src" / rel / "__init__.py").exists()
    )


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_lints_with_plutil(plist_name: str) -> None:
    _maybe_xfail(plist_name, "lint")
    plist_path = LAUNCHD_DIR / plist_name
    result = subprocess.run(
        ["plutil", "-lint", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_label_matches_filename(plist_name: str) -> None:
    _maybe_xfail(plist_name, "label")
    data = _load_rendered_plist(LAUNCHD_DIR / plist_name)
    assert data["Label"] == plist_name.removesuffix(".plist")


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_standard_log_parents_exist_after_render(plist_name: str) -> None:
    _maybe_xfail(plist_name, "log-parent")
    data = _load_rendered_plist(LAUNCHD_DIR / plist_name)
    stdout_parent = Path(data["StandardOutPath"]).parent
    stderr_parent = Path(data["StandardErrorPath"]).parent
    assert stdout_parent.exists(), stdout_parent
    assert stderr_parent.exists(), stderr_parent


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_program_targets_exist_after_render(plist_name: str) -> None:
    _maybe_xfail(plist_name, "program-target")
    data = _load_rendered_plist(LAUNCHD_DIR / plist_name)
    target_type, target = _program_target(data)
    if target_type == "path":
        assert isinstance(target, Path)
        assert target.exists(), target
    else:
        assert isinstance(target, str)
        assert _module_target_exists(target), target


@pytest.mark.parametrize("plist_name", PLIST_NAMES)
def test_plist_has_no_hardcoded_machine_paths(plist_name: str) -> None:
    _maybe_xfail(plist_name, "hardcoded-paths")
    text = (LAUNCHD_DIR / plist_name).read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "/Volumes/" not in text


def test_smoke_validator_help_defaults_to_installed_launchagent_path() -> None:
    env = {**os.environ, "PYTHONPATH": "src"}
    result = subprocess.run(
        [sys.executable, "-m", "akb2.quality.smoke_validator", "--help"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    compact_stdout = "".join(result.stdout.split())
    assert "Library/LaunchAgents/com.akb2.main-pipeline.plist" in compact_stdout
    assert "launchd/com.akb2.main-pipeline.plist" not in compact_stdout


def test_validate_env_parity_detects_repo_vs_installed_promote_drift(tmp_path: Path) -> None:
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    repo_plist = tmp_path / "repo.plist"
    installed_plist = tmp_path / "installed.plist"
    _write_plist(repo_plist, script, tmp_path, tmp_path / "logs" / "repo.log", auto_promote="0")
    _write_plist(
        installed_plist,
        script,
        tmp_path,
        tmp_path / "logs" / "installed.log",
        auto_promote="1",
        amendment="0",
    )

    with pytest.raises(SmokeValidationError) as exc_info:
        validate_env_parity(
            installed_plist,
            smoke_auto_promote="1",
            smoke_amendment_applied="0",
            repo_plist_path=repo_plist,
        )

    message = str(exc_info.value)
    assert "AKB2_AUTO_PROMOTE repo=0 installed=1" in message
    assert str(repo_plist) in message
    assert str(installed_plist) in message


def test_validate_launchd_static_uses_auto_promote_only_for_active_mode(tmp_path: Path) -> None:
    script = tmp_path / "akb2-main-pipeline.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(script, 0o755)
    plist = tmp_path / "com.akb2.main-pipeline.plist"
    _write_plist(
        plist,
        script,
        tmp_path,
        tmp_path / "logs" / "runner.log",
        auto_promote="1",
        amendment="0",
    )

    result = validate_launchd_static(plist, expected_mode="active")
    assert result["passed"] is True
    assert result["mode"] == "active"


def test_xfail_reasons_are_non_empty() -> None:
    for reason in XFAIL_REASONS.values():
        assert reason.strip()


class TestBackstopScheduleWithinDeadline:
    """每个补跑时段必须早于脚本 deadline —— 否则该时段永远只能巡检、不能补跑。

    2026-08-03 之前的真实状态：两个 backstop 的第二时段是 **12:05**，而脚本 deadline
    默认 **12:00**。那个时段因此**每天必然走 deadline 分支、一次都没真正补跑过**
    （旧版还把它记成 FAILED，污染告警）。仓库 plist 的注释早在 07-30 就认账了这个矛盾，
    但没有任何测试守它 —— 所以它活了下来。这条就是那道缺失的门。

    deadline 默认值从脚本里读，不写死：改脚本默认值时这条会自动跟着变，不会各自漂移。
    """

    _BACKSTOPS = {
        "com.akb2.briefing.backstop": ("scripts/briefing_backstop.sh", "AKB2_BRIEFING_DEADLINE"),
        "com.akb2.github_briefing.backstop": (
            "scripts/github_briefing_backstop.sh", "AKB2_GITHUB_DEADLINE",
        ),
    }

    @staticmethod
    def _deadline_minutes(script_rel: str, prefix: str) -> int:
        """从脚本里解析 `${PREFIX_HOUR:-12}` / `${PREFIX_MINUTE:-0}` 的默认值。"""
        text = (REPO / script_rel).read_text(encoding="utf-8")
        hour = re.search(rf"\{{{prefix}_HOUR:-(\d+)\}}", text)
        minute = re.search(rf"\{{{prefix}_MINUTE:-(\d+)\}}", text)
        assert hour and minute, f"{script_rel}: 解析不到 {prefix}_HOUR/_MINUTE 默认值"
        return int(hour.group(1)) * 60 + int(minute.group(1))

    @pytest.mark.parametrize("label", sorted(_BACKSTOPS))
    def test_every_slot_is_earlier_than_deadline(self, label: str) -> None:
        script_rel, prefix = self._BACKSTOPS[label]
        deadline = self._deadline_minutes(script_rel, prefix)

        plist = _load_rendered_plist(LAUNCHD_DIR / f"{label}.plist")
        slots = plist["StartCalendarInterval"]
        if isinstance(slots, dict):
            slots = [slots]
        assert slots, f"{label}: 没有 StartCalendarInterval，断言失去意义"

        late = [
            f"{s.get('Hour', 0):02d}:{s.get('Minute', 0):02d}"
            for s in slots
            if s.get("Hour", 0) * 60 + s.get("Minute", 0) >= deadline
        ]
        assert not late, (
            f"{label} 的这些时段不早于 deadline "
            f"{deadline // 60:02d}:{deadline % 60:02d}，只能巡检不能补跑: {late}"
        )
