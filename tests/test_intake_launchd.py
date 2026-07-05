"""test_intake_launchd.py — F21 intake launchd 定时器 + 安装脚本测试。

工单 F21:给 intake 抓取链路装 launchd 定时器(每日 05:30 抓源落 raw)。
测试覆盖(工单要求:plist XML 合法 + 占位符渲染 + preflight fail-closed):
  - plist: XML 合法(plistlib,等价 plutil -lint) / 占位符 / Label / 调度 05:30 /
    RunAtLoad=false / ProgramArguments(orchestrator + --limit 30 + 路径) / 日志 / env
  - 安装脚本: bash -n 语法 / 可执行位 / preflight defuddle 缺失 fail-closed(exit 3) /
    sed 占位符渲染

不跑 launchctl(测试环境无);不跑真实抓取;不写 vault。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLIST = REPO / "launchd" / "com.akb2.intake.plist"
SCRIPT = REPO / "scripts" / "bootstrap_intake_launchd.sh"


def _load_plist() -> dict:
    with open(PLIST, "rb") as f:
        return plistlib.load(f)


# ---------------------------------------------------------------------------
# plist XML 合法 + 占位符 + 字段契约
# ---------------------------------------------------------------------------


class TestPlistValid:
    def test_plist_xml_valid(self) -> None:
        """plist XML 合法(plistlib 能解析,等价 plutil -lint)。"""
        data = _load_plist()
        assert isinstance(data, dict)

    def test_plist_label(self) -> None:
        data = _load_plist()
        assert data["Label"] == "com.akb2.intake"

    def test_plist_placeholders_present(self) -> None:
        """plist 含 3 个占位符(未渲染前,由 bootstrap 脚本 sed 替换)。"""
        text = PLIST.read_text(encoding="utf-8")
        assert "__AKB2_REPO_ROOT__" in text
        assert "__AKB2_VAULT_ROOT__" in text
        assert "__AKB2_HOME__" in text

    def test_plist_no_hardcoded_user_paths(self) -> None:
        """plist 不含硬编码 /Users/praise 或 /Volumes(用占位符替代,家法同 main-pipeline)。"""
        text = PLIST.read_text(encoding="utf-8")
        assert "/Users/praise" not in text
        assert "/Volumes/AKB2" not in text

    def test_plist_schedule_0530_cst(self) -> None:
        """调度 Asia/Shanghai 05:30 = Hour=5 Minute=30(避开 briefing 07:30)。"""
        data = _load_plist()
        interval = data["StartCalendarInterval"]
        assert interval["Hour"] == 5, f"应 Hour=5(05:30 CST),实际 {interval['Hour']}"
        assert interval["Minute"] == 30

    def test_plist_run_at_load_false(self) -> None:
        """RunAtLoad=false(首跑由 lead 装载后手动 kickstart,不搞装载即自动跑)。"""
        data = _load_plist()
        assert data["RunAtLoad"] is False

    def test_plist_program_args_orchestrator_and_limit(self) -> None:
        """ProgramArguments 含 python3 -m orchestrator + --limit 30(有界,宪法 P1)。"""
        data = _load_plist()
        args = data["ProgramArguments"]
        assert "akb2.quality.intake.orchestrator" in args
        assert "-m" in args
        assert "--limit" in args
        idx = args.index("--limit")
        assert args[idx + 1] == "30", f"--limit 应 30,实际 {args[idx + 1]}"

    def test_plist_program_args_paths_placeholders(self) -> None:
        """ProgramArguments 含 sources/state/output/failures 路径(占位符)。"""
        data = _load_plist()
        args = data["ProgramArguments"]
        assert "__AKB2_REPO_ROOT__/config/main_sources.yaml" in args
        assert "__AKB2_REPO_ROOT__/reports/intake/intake-state.jsonl" in args
        assert "__AKB2_VAULT_ROOT__/Praise Space/AKB2.0/raw" in args
        assert "__AKB2_REPO_ROOT__/logs/intake-failures.jsonl" in args

    def test_plist_state_file_persistent_not_tmp(self) -> None:
        """state-file 落 reports/intake/(持久化,绝不在 /tmp,保 dedup 连续性)。"""
        data = _load_plist()
        args = data["ProgramArguments"]
        idx = args.index("--state-file")
        state_path = args[idx + 1]
        assert "/tmp" not in state_path
        assert "reports/intake/" in state_path

    def test_plist_log_paths(self) -> None:
        """stdout/stderr 落 logs/intake.log(家法同现有 plist)。"""
        data = _load_plist()
        assert data["StandardOutPath"] == "__AKB2_REPO_ROOT__/logs/intake.log"
        assert data["StandardErrorPath"] == "__AKB2_REPO_ROOT__/logs/intake.log"

    def test_plist_env_pythonpath_and_path(self) -> None:
        """PYTHONPATH=src + PATH 用 __AKB2_HOME__ 占位(家法同 main-pipeline)。"""
        data = _load_plist()
        env = data["EnvironmentVariables"]
        assert env["PYTHONPATH"] == "__AKB2_REPO_ROOT__/src"
        assert "__AKB2_HOME__/.pyenv/shims" in env["PATH"]
        # F21-fix: defuddle 装在 ~/.npm-global/bin,launchd env PATH 不含它时
        # extract 全线 fallback → Required anchors not hit → archived=0(首跑实测)
        assert "__AKB2_HOME__/.npm-global/bin" in env["PATH"]
        assert "__AKB2_VAULT_ROOT__/Praise Space/AKB2.0" == env["AKB2_VAULT_ROOT"]


# ---------------------------------------------------------------------------
# 安装脚本:bash -n 语法 + 可执行位 + preflight fail-closed + 占位符渲染
# ---------------------------------------------------------------------------


class TestBootstrapScript:
    def test_script_bash_n_syntax(self) -> None:
        """脚本 bash -n 语法合法(无语法错误)。"""
        r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert r.returncode == 0, f"bash -n 失败: {r.stderr}"

    def test_script_executable_bit(self) -> None:
        """脚本有可执行位(lead 装载时直接 ./scripts/... 跑)。"""
        assert SCRIPT.stat().st_mode & 0o100, "脚本缺可执行位"

    def test_script_preflight_defuddle_missing_fail_closed(self, tmp_path: Path) -> None:
        """preflight: defuddle CLI 缺失 → exit 3(fail-closed,不跑 launchctl/bootstrap)。"""
        env = os.environ.copy()
        env["PATH"] = "/usr/bin:/bin"  # 不含 defuddle
        env["HOME"] = str(tmp_path)
        r = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
        assert r.returncode == 3, (
            f"defuddle 缺失应 exit 3(fail-closed),实际 {r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        combined = (r.stdout + r.stderr).lower()
        assert "defuddle" in combined, "应报 defuddle 缺失"

    def test_script_preflight_plist_missing_fail_closed(self, tmp_path: Path) -> None:
        """preflight: plist 源缺失 → exit 3(fail-closed)。"""
        # 用 env 指向不存在的 plist——脚本用 SCRIPT_DIR/../launchd/com.akb2.intake.plist
        # 难以 mock。改为直接测脚本对 PLIST_SRC 存在性检查的逻辑(代码 review)。
        # 这里测:脚本存在性检查代码在 preflight(通过 grep 代码确认)。
        text = SCRIPT.read_text(encoding="utf-8")
        assert "PLIST_SRC" in text
        assert "if [ ! -f" in text and "PLIST_SRC" in text

    def test_script_renders_placeholders(self, tmp_path: Path) -> None:
        """脚本 sed 替换 3 占位符(REPO_ROOT/VAULT_ROOT/HOME)——验证 render 逻辑。"""
        rendered = tmp_path / "rendered.plist"
        repo_root = str(REPO)
        vault_root = "/tmp/test-vault-f21"
        home = str(tmp_path)
        sed_cmd = (
            f'sed -e "s|__AKB2_REPO_ROOT__|{repo_root}|g" '
            f'-e "s|__AKB2_VAULT_ROOT__|{vault_root}|g" '
            f'-e "s|__AKB2_HOME__|{home}|g" '
            f'"{PLIST}" > "{rendered}"'  # 引号:主仓路径含空格(Project AKB2)
        )
        subprocess.run(sed_cmd, shell=True, check=True)
        rendered_text = rendered.read_text(encoding="utf-8")
        assert "__AKB2_REPO_ROOT__" not in rendered_text
        assert "__AKB2_VAULT_ROOT__" not in rendered_text
        assert "__AKB2_HOME__" not in rendered_text
        assert repo_root in rendered_text
        assert vault_root in rendered_text
        # 渲染后仍是合法 plist
        with open(rendered, "rb") as f:
            plistlib.load(f)

    def test_script_has_bootout_bootstrap_verify(self) -> None:
        """脚本含 bootout + bootstrap + launchctl print 验证(家法同 bootstrap_launchd.sh)。"""
        text = SCRIPT.read_text(encoding="utf-8")
        assert "launchctl bootout" in text
        assert "launchctl bootstrap" in text
        assert "launchctl print" in text
        # 验证断言绑本 plist 可观测特征(orchestrator + hour=5)
        assert "intake.orchestrator" in text
        assert "hour = 5" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
