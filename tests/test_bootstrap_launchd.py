"""test_bootstrap_launchd.py — 安装脚本安全回归（Lane 4，2026-08-03）。

事故驱动：2026-07-28 16:34 `bootstrap_launchd.sh` 被跑了两次，两次都是
「bootout 成功 + bootstrap 失败 + 不回滚」，`com.akb2.briefing`（social 简报主跑）
被卸载 5 天 22 小时无人发现。硬证据是 $TMPDIR 里两份内容完全相同的渲染残留
（`com.akb2.briefing.XXXXXX.plist.{ALyRNbRLBO,W1i0OsbLkP}`，1423B / Jul 28 16:34，
`cmp` → IDENTICAL）：脚本必然死在 `launchctl bootstrap`（它之前唯一能中断的语句），
而 bootout 已经生效。

本文件的断言全部绑「可观测行为」：
  - bootstrap 失败时进程非 0 退出 + 回滚**真的发生**（fake launchctl 收到第二次
    bootstrap，且用的是 LaunchAgents 真实路径）
  - 成功路径下 plist **落在 LaunchAgents**（而不是装完就 rm 的 mktemp 路径）
  - VAULT_ROOT 三门 / worktree 守卫 / 渲染双门 各自 fail-closed
  - intake 验证断言的 AND 语义真的在起作用（旧版 OR + 死值 7200）

全程不执行真 launchctl、不写真 ~/Library/LaunchAgents：
launchctl 用 PATH stub 假冒，安装目录用 AKB2_LAUNCH_AGENTS_DIR 注入到 tmpdir。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

BRIEFING_LABEL = "com.akb2.briefing"
INTAKE_LABEL = "com.akb2.intake"

# 假 launchctl print 的验证输出：分别喂给两个脚本的 Verification 段。
BRIEFING_PRINT_OK = """com.akb2.briefing = {
	arguments = {
		/bin/bash
		/fake/repo/scripts/daily_briefing.sh
	}
	hour = 7
	minute = 30
}
"""

INTAKE_PRINT_OK = """com.akb2.intake = {
	arguments = {
		python3
		-m
		akb2.quality.intake.orchestrator
	}
	run interval = 900
}
"""

# 缺 900 的 intake print（旧值 7200 也在里面，证明"引用了 orchestrator"不足以放行）
INTAKE_PRINT_MISSING_INTERVAL = """com.akb2.intake = {
	arguments = {
		python3
		-m
		akb2.quality.intake.orchestrator
	}
	run interval = 7200
}
"""


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


FAKE_LAUNCHCTL = r"""#!/usr/bin/env bash
# 假 launchctl:记录每次调用,按 state 目录模拟"服务是否已加载"。
LOG="${FAKE_LAUNCHCTL_LOG:?FAKE_LAUNCHCTL_LOG 未设}"
STATE="${FAKE_LAUNCHCTL_STATE:?FAKE_LAUNCHCTL_STATE 未设}"
mkdir -p "$STATE"
printf '%s\n' "$*" >> "$LOG"
sub="${1:-}"
case "$sub" in
  bootout)
    rm -f "$STATE/loaded"
    exit 0
    ;;
  bootstrap)
    n=$(( $(cat "$STATE/bootstrap_count" 2>/dev/null || echo 0) + 1 ))
    printf '%s' "$n" > "$STATE/bootstrap_count"
    if [ "${FAKE_BOOTSTRAP_ALWAYS_FAIL:-0}" = "1" ] \
       || { [ "${FAKE_BOOTSTRAP_FAIL_FIRST:-0}" = "1" ] && [ "$n" = "1" ]; }; then
      echo "Bootstrap failed: 37: Operation already in progress" >&2
      exit 37
    fi
    : > "$STATE/loaded"
    exit 0
    ;;
  print)
    if [ -f "$STATE/loaded" ] && [ -f "$STATE/verify_out" ]; then
      cat "$STATE/verify_out"
      exit 0
    fi
    echo "Could not find service \"${2:-}\" in domain" >&2
    exit 113
    ;;
  *)
    exit 0
    ;;
esac
"""


def _prepare_fake_repo(tmp_path: Path, *, subdir: str = "repo") -> Path:
    """把安装脚本 + 共用 lib + plist 模板复制到一个隔离的假仓根。

    假仓根不是 git 仓,`git rev-parse` 返回空 → worktree 守卫的 git 判据自然跳过,
    正好让我们单独测路径判据与 git 判据。
    """
    work = tmp_path / subdir
    (work / "scripts" / "lib").mkdir(parents=True)
    (work / "launchd").mkdir(parents=True)
    for rel in (
        "scripts/bootstrap_launchd.sh",
        "scripts/bootstrap_intake_launchd.sh",
        "scripts/lib/launchctl.sh",
        "scripts/lib/state-root.sh",
        f"launchd/{BRIEFING_LABEL}.plist",
        f"launchd/{INTAKE_LABEL}.plist",
    ):
        shutil.copy2(REPO / rel, work / rel)
    return work


def _prepare_env(
    tmp_path: Path,
    *,
    verify_out: str | None = None,
    fail_first_bootstrap: bool = False,
    always_fail_bootstrap: bool = False,
    tag: str = "run",
) -> tuple[dict[str, str], Path, Path, Path]:
    """构造 fake launchctl + 注入目录,返回 (env, launch_agents_dir, call_log, home)。"""
    fake_bin = tmp_path / f"fake-bin-{tag}"
    fake_bin.mkdir()
    _write_executable(fake_bin / "launchctl", FAKE_LAUNCHCTL)
    # intake preflight 需要的两个外部依赖桩
    _write_executable(fake_bin / "defuddle", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "python3", "#!/usr/bin/env bash\nexit 0\n")

    state = tmp_path / f"launchctl-state-{tag}"
    state.mkdir()
    if verify_out is not None:
        (state / "verify_out").write_text(verify_out, encoding="utf-8")
    call_log = tmp_path / f"launchctl-calls-{tag}.log"
    call_log.write_text("", encoding="utf-8")

    launch_agents = tmp_path / f"LaunchAgents-{tag}"
    launch_agents.mkdir()

    home = tmp_path / f"home-{tag}"
    home.mkdir()
    vault = tmp_path / f"vault-{tag}"
    (vault / "Praise Space" / "AKB2.0").mkdir(parents=True)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    env["HOME"] = str(home)
    env["VAULT_ROOT"] = str(vault)
    env["AKB2_LAUNCH_AGENTS_DIR"] = str(launch_agents)
    # tmp_path 在 macOS 上就在 /var/folders 下,VAULT_ROOT 门 3 会拦；成功路径显式放行
    env["AKB2_ALLOW_TMP_VAULT_ROOT"] = "1"
    env["AKB2_STATE_ROOT"] = str(tmp_path / f"state-root-{tag}")
    env["AKB2_TEARDOWN_SLEEP_SEC"] = "0"
    env["FAKE_LAUNCHCTL_LOG"] = str(call_log)
    env["FAKE_LAUNCHCTL_STATE"] = str(state)
    if fail_first_bootstrap:
        env["FAKE_BOOTSTRAP_FAIL_FIRST"] = "1"
    if always_fail_bootstrap:
        env["FAKE_BOOTSTRAP_ALWAYS_FAIL"] = "1"
    return env, launch_agents, call_log, home


def _run(script: Path, env: dict[str, str], args: list[str] | None = None):
    return subprocess.run(
        ["bash", str(script)] + (args or []),
        env=env,
        capture_output=True,
        text=True,
    )


def _bootstrap_calls(call_log: Path) -> list[str]:
    return [
        line for line in call_log.read_text(encoding="utf-8").splitlines()
        if line.startswith("bootstrap ")
    ]


# ===========================================================================
# SC-1 — bootstrap 失败 → 非 0 退出 + 回滚真的执行(用 LaunchAgents 真实路径)
# ===========================================================================


class TestBootstrapFailureRollsBack:
    def test_rollback_reinstalls_previous_plist_from_launch_agents(self, tmp_path: Path) -> None:
        """已装过的服务:bootstrap 失败 → 非 0 退出 + 回滚 + 二次 bootstrap 用 DST 路径。

        这是 2026-07-28 的真实形态(briefing 当时已在册),旧版在此路径下
        「bootout 已生效 / 重装未发生 / 无回滚」= 服务净消失。
        """
        work = _prepare_fake_repo(tmp_path)
        env, launch_agents, call_log, _home = _prepare_env(
            tmp_path, verify_out=BRIEFING_PRINT_OK, fail_first_bootstrap=True
        )
        dst = launch_agents / f"{BRIEFING_LABEL}.plist"
        dst.write_text("<plist>OLD-GOOD</plist>\n", encoding="utf-8")

        result = _run(work / "scripts" / "bootstrap_launchd.sh", env)
        combined = result.stdout + result.stderr

        # (a) 非 0 退出
        assert result.returncode != 0, combined
        # (b) stderr 明确"bootstrap 失败 → 回滚",且 launchctl 的 errno 有留痕
        assert "bootstrap 失败" in result.stderr, result.stderr
        assert "回滚" in result.stderr, result.stderr
        assert "37" in result.stderr, f"launchctl exit code/stderr 未留痕: {result.stderr}"
        assert "Operation already in progress" in result.stderr, result.stderr
        # (c) 回滚真的执行:第二次 bootstrap 存在,且两次都用 LaunchAgents 真实路径
        calls = _bootstrap_calls(call_log)
        assert len(calls) == 2, f"应有 2 次 bootstrap(首次失败 + 回滚重装),实际: {calls}"
        for call in calls:
            assert call.endswith(f" {dst}"), f"bootstrap 未用 LaunchAgents 真实路径: {call}"
        # 旧 plist 内容被恢复(不是新渲染的半安装态)
        assert dst.read_text(encoding="utf-8") == "<plist>OLD-GOOD</plist>\n"
        assert "已恢复旧 plist 并重新 bootstrap 成功" in result.stderr, result.stderr

    def test_rollback_first_install_leaves_no_half_state(self, tmp_path: Path) -> None:
        """首装场景:无旧 plist 可恢复 → 移除新 plist + 明示"当前无 job 在册"。

        断言"失败可见"(宪法 P4):不允许 exit 0,也不允许留下一个装了却没加载的 plist。
        """
        work = _prepare_fake_repo(tmp_path)
        env, launch_agents, call_log, _home = _prepare_env(
            tmp_path, verify_out=BRIEFING_PRINT_OK, always_fail_bootstrap=True
        )
        dst = launch_agents / f"{BRIEFING_LABEL}.plist"
        assert not dst.exists()

        result = _run(work / "scripts" / "bootstrap_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "此前无旧 plist" in result.stderr, result.stderr
        assert "无 job 在册" in result.stderr, result.stderr
        assert not dst.exists(), "首装失败后不得留下半安装态 plist"
        assert len(_bootstrap_calls(call_log)) == 1, _bootstrap_calls(call_log)

    def test_intake_bootstrap_failure_rolls_back(self, tmp_path: Path) -> None:
        """intake 脚本同样必须回滚(旧版也是 bootout + bootstrap 裸奔)。"""
        work = _prepare_fake_repo(tmp_path)
        env, launch_agents, call_log, _home = _prepare_env(
            tmp_path, verify_out=INTAKE_PRINT_OK, fail_first_bootstrap=True, tag="intake-rb"
        )
        dst = launch_agents / f"{INTAKE_LABEL}.plist"
        dst.write_text("<plist>OLD-INTAKE</plist>\n", encoding="utf-8")

        result = _run(work / "scripts" / "bootstrap_intake_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "bootstrap 失败" in result.stderr and "回滚" in result.stderr, result.stderr
        calls = _bootstrap_calls(call_log)
        assert len(calls) == 2, calls
        for call in calls:
            assert call.endswith(f" {dst}"), call
        assert dst.read_text(encoding="utf-8") == "<plist>OLD-INTAKE</plist>\n"


# ===========================================================================
# SC-2 — 静态：teardown 等待存在 + launchctl stderr 不再被裸 2>/dev/null 吞掉
# ===========================================================================


class TestTeardownWaitStatic:
    @pytest.mark.parametrize(
        "script_rel",
        ["scripts/bootstrap_launchd.sh", "scripts/bootstrap_intake_launchd.sh"],
    )
    def test_bootout_then_wait_then_bootstrap(self, script_rel: str) -> None:
        """bootout 与 bootstrap 之间必须有 teardown 等待调用（避免 launchctl 竞态）。

        断言范式照 tests/test_deploy_mac_mini.py:173
        (test_deploy_script_waits_for_launchctl_teardown)。
        """
        text = (REPO / script_rel).read_text(encoding="utf-8")
        assert "akb2_launchctl_bootout " in text
        assert "akb2_wait_for_launchctl_teardown " in text
        assert "akb2_launchctl_bootstrap " in text
        assert 'if ! akb2_wait_for_launchctl_teardown "$DOMAIN_LABEL"; then' in text
        # 顺序：bootout → wait → bootstrap（取安装段最后一次出现的位置）
        i_bootout = text.rindex('akb2_launchctl_bootout "$DOMAIN_LABEL"')
        i_wait = text.rindex('if ! akb2_wait_for_launchctl_teardown "$DOMAIN_LABEL"; then')
        i_boot = text.rindex('if ! akb2_launchctl_bootstrap "$DOMAIN" "$PLIST_DST"; then')
        assert i_bootout < i_wait < i_boot, (i_bootout, i_wait, i_boot)

    @pytest.mark.parametrize(
        "script_rel",
        ["scripts/bootstrap_launchd.sh", "scripts/bootstrap_intake_launchd.sh"],
    )
    def test_no_raw_stderr_swallow_around_launchctl(self, script_rel: str) -> None:
        """脚本里不再有 `launchctl ... 2>/dev/null` 把失败原因吞掉。

        旧版 `launchctl bootout ... 2>/dev/null || true` + `set -e` 直接吞掉 bootstrap
        的 errno,导致 2026-07-28 事故的死因至今 [未证实]。
        """
        text = (REPO / script_rel).read_text(encoding="utf-8")
        offenders = [
            line
            for line in text.splitlines()
            if "launchctl" in line
            and "2>/dev/null" in line
            and not line.lstrip().startswith("#")
        ]
        assert not offenders, f"launchctl 调用仍在吞 stderr: {offenders}"

    def test_lib_bootstrap_wrapper_reports_code_and_stderr(self) -> None:
        """共用包装必须同时打出 exit code 与 stderr（宪法 P4「失败可见」）。"""
        text = (REPO / "scripts" / "lib" / "launchctl.sh").read_text(encoding="utf-8")
        assert "akb2_launchctl_bootstrap()" in text
        assert "akb2_wait_for_launchctl_teardown()" in text
        assert 'launchctl bootstrap "$domain" "$plist" 2>&1 >/dev/null' in text
        assert "exit=%s" in text

    def test_teardown_budget_is_named_not_literal(self) -> None:
        """重试预算用具名常量（宪法 P4「预算显式」），不是循环里的裸字面量。"""
        text = (REPO / "scripts" / "lib" / "launchctl.sh").read_text(encoding="utf-8")
        assert 'AKB2_TEARDOWN_MAX_ATTEMPTS:=10' in text
        assert 'AKB2_TEARDOWN_SLEEP_SEC:=1' in text
        assert '-le "$AKB2_TEARDOWN_MAX_ATTEMPTS"' in text
        assert 'sleep "$AKB2_TEARDOWN_SLEEP_SEC"' in text


# ===========================================================================
# SC-3 — 成功路径：plist 原子落在 LaunchAgents，bootstrap 用该真实路径
# ===========================================================================


class TestAtomicInstallToLaunchAgents:
    def test_success_path_installs_plist_and_bootstraps_from_it(self, tmp_path: Path) -> None:
        """成功路径:DST 存在且内容 = 渲染结果,bootstrap 参数就是 DST(不再是 mktemp 路径)。"""
        work = _prepare_fake_repo(tmp_path)
        env, launch_agents, call_log, _home = _prepare_env(
            tmp_path, verify_out=BRIEFING_PRINT_OK
        )

        result = _run(work / "scripts" / "bootstrap_launchd.sh", env)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined

        dst = launch_agents / f"{BRIEFING_LABEL}.plist"
        assert dst.exists(), f"plist 未落到 LaunchAgents: {combined}"
        rendered = dst.read_text(encoding="utf-8")
        # 内容 = 渲染结果：占位符全替换、指向本次的 repo / vault
        assert "__AKB2_" not in rendered
        assert str(work) in rendered
        assert env["VAULT_ROOT"] in rendered

        calls = _bootstrap_calls(call_log)
        assert len(calls) == 1, calls
        assert calls[0] == f"bootstrap gui/{os.getuid()} {dst}", calls[0]
        # 不再从临时路径装
        assert ".plist." not in calls[0], f"bootstrap 仍在用 mktemp 产物: {calls[0]}"

    def test_intake_success_path_installs_plist(self, tmp_path: Path) -> None:
        """intake 脚本同样把 plist 装到 LaunchAgents 并从该路径 bootstrap。"""
        work = _prepare_fake_repo(tmp_path)
        env, launch_agents, call_log, _home = _prepare_env(
            tmp_path, verify_out=INTAKE_PRINT_OK, tag="intake-ok"
        )

        result = _run(work / "scripts" / "bootstrap_intake_launchd.sh", env)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined

        dst = launch_agents / f"{INTAKE_LABEL}.plist"
        assert dst.exists(), combined
        assert "__AKB2_" not in dst.read_text(encoding="utf-8")
        calls = _bootstrap_calls(call_log)
        assert calls == [f"bootstrap gui/{os.getuid()} {dst}"], calls

    def test_no_temp_plist_left_behind_on_failure(self, tmp_path: Path) -> None:
        """失败退出也不留 mktemp 残留(旧版正是靠残留才被考古出来的)。"""
        work = _prepare_fake_repo(tmp_path)
        env, _launch_agents, _call_log, _home = _prepare_env(
            tmp_path, verify_out=BRIEFING_PRINT_OK, always_fail_bootstrap=True, tag="noresidue"
        )
        own_tmp = tmp_path / "tmpdir"
        own_tmp.mkdir()
        env["TMPDIR"] = str(own_tmp)

        result = _run(work / "scripts" / "bootstrap_launchd.sh", env)

        assert result.returncode != 0
        residue = list(own_tmp.glob(f"{BRIEFING_LABEL}.*"))
        assert residue == [], f"失败路径留下临时渲染残留: {residue}"


# ===========================================================================
# SC-4 — VAULT_ROOT 三门 fail-closed
# ===========================================================================


class TestVaultRootGates:
    def _render_only_run(self, tmp_path: Path, vault_root: str, *, tag: str):
        work = _prepare_fake_repo(tmp_path, subdir=f"repo-{tag}")
        env, _la, _log, _home = _prepare_env(tmp_path, tag=tag)
        env["VAULT_ROOT"] = vault_root
        # 三门必须在 render-only 路径上同样生效(否则 --render-only 就成了绕门后门)
        del env["AKB2_ALLOW_TMP_VAULT_ROOT"]
        out = tmp_path / f"rendered-{tag}.plist"
        return _run(
            work / "scripts" / "bootstrap_launchd.sh", env, ["--render-only", str(out)]
        ), out

    def test_tmp_vault_root_rejected(self, tmp_path: Path) -> None:
        """VAULT_ROOT 落在临时目录 → 非 0 退出（2026-07-28 实际现场）。

        残留 temp 里 --vault-root 的值是 /var/folders/.../T/tmp.MAVoliqX9M ——
        本仓标准测试命令 `VAULT_ROOT="$(mktemp -d)" pytest` 的产物。
        """
        tmp_vault = subprocess.run(
            ["mktemp", "-d"], capture_output=True, text=True, check=True
        ).stdout.strip()
        try:
            result, out = self._render_only_run(tmp_path, tmp_vault, tag="tmpvault")
            assert result.returncode != 0, result.stdout + result.stderr
            assert "临时目录" in result.stderr, result.stderr
            assert tmp_vault in result.stderr, result.stderr
            assert not out.exists(), "被拒绝时不该产出渲染件"
        finally:
            shutil.rmtree(tmp_vault, ignore_errors=True)

    def test_relative_vault_root_rejected(self, tmp_path: Path) -> None:
        """VAULT_ROOT 相对路径 → 非 0 退出（对齐 launchd_wrapper.sh:14-17 的绝对路径门）。"""
        result, out = self._render_only_run(tmp_path, "relative/path", tag="relvault")
        assert result.returncode != 0, result.stdout + result.stderr
        assert "绝对路径" in result.stderr, result.stderr
        assert not out.exists()

    def test_nonexistent_vault_root_rejected(self, tmp_path: Path) -> None:
        """VAULT_ROOT 绝对但不存在 → 非 0 退出。"""
        result, out = self._render_only_run(
            tmp_path, "/nonexistent/abs/path-lane4", tag="ghostvault"
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert "不存在" in result.stderr, result.stderr
        assert not out.exists()

    def test_empty_vault_root_falls_back_then_still_gated(self, tmp_path: Path) -> None:
        """VAULT_ROOT 置空 → 落回文档化默认值，再由「目录存在」门裁决。

        `${VAULT_ROOT:-…}` 把空串也当未设（两个脚本的既有语义，本轮不改），所以
        空值不会以空路径进入渲染；真正兜底的是门 2。两种机器状态都要成立：
        默认 vault 已挂载 → 用默认值渲染成功；未挂载 → 门 2 fail-closed。
        """
        default_vault = Path("/Volumes/AKB2/Obsidian")
        result, out = self._render_only_run(tmp_path, "", tag="emptyvault")
        combined = result.stdout + result.stderr

        if default_vault.is_dir():
            assert result.returncode == 0, combined
            assert f"Vault root: {default_vault}" in result.stdout, combined
            assert str(default_vault) in out.read_text(encoding="utf-8")
        else:
            assert result.returncode != 0, combined
            assert "不存在" in result.stderr, result.stderr
            assert not out.exists()

    def test_intake_rejects_tmp_vault_root_before_dependency_preflight(
        self, tmp_path: Path
    ) -> None:
        """intake 的 VAULT_ROOT 门也生效,且排在依赖 preflight 之前。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-intake-vault")
        env, _la, _log, _home = _prepare_env(tmp_path, tag="intake-vault")
        del env["AKB2_ALLOW_TMP_VAULT_ROOT"]
        tmp_vault = subprocess.run(
            ["mktemp", "-d"], capture_output=True, text=True, check=True
        ).stdout.strip()
        try:
            env["VAULT_ROOT"] = tmp_vault
            result = _run(work / "scripts" / "bootstrap_intake_launchd.sh", env)
            assert result.returncode != 0, result.stdout + result.stderr
            assert "临时目录" in result.stderr, result.stderr
            # 门在依赖 preflight 之前 → 还没走到 orchestrator import 那步
            assert "orchestrator import 可用" not in result.stdout, result.stdout
        finally:
            shutil.rmtree(tmp_vault, ignore_errors=True)


# ===========================================================================
# SC-5 — 渲染产物双门（占位符残留 + plist 合法性）
# ===========================================================================


class TestRenderGates:
    def test_render_only_output_passes_both_gates(self, tmp_path: Path) -> None:
        """正常渲染件:`__AKB2_` 计数 == 0 且 plutil -lint OK。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-render-ok")
        # 用真实 PATH:让 plutil(macOS)/python3 plistlib 真的参与 lint,不是桩
        env = os.environ.copy()
        vault = tmp_path / "vault-render"
        vault.mkdir()
        env["VAULT_ROOT"] = str(vault)
        env["AKB2_ALLOW_TMP_VAULT_ROOT"] = "1"
        env["AKB2_STATE_ROOT"] = str(tmp_path / "state-render")
        out = tmp_path / "rendered-ok.plist"

        result = _run(
            work / "scripts" / "bootstrap_launchd.sh", env, ["--render-only", str(out)]
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert out.exists(), combined

        text = out.read_text(encoding="utf-8")
        assert text.count("__AKB2_") == 0
        if shutil.which("plutil"):
            lint = subprocess.run(
                ["plutil", "-lint", str(out)], capture_output=True, text=True
            )
            assert lint.returncode == 0, lint.stdout + lint.stderr
            assert "OK" in lint.stdout, lint.stdout

    def test_leftover_placeholder_rejected(self, tmp_path: Path) -> None:
        """模板里人为留一个 __AKB2_FOO__ → 非 0 退出,并点名残留的占位符。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-render-bad")
        tampered = work / "launchd" / "tampered.plist"
        original = (work / "launchd" / f"{BRIEFING_LABEL}.plist").read_text(encoding="utf-8")
        tampered.write_text(
            original.replace(
                "<key>WorkingDirectory</key>",
                "<key>AKB2Injected</key>\n    <string>__AKB2_FOO__</string>\n"
                "    <key>WorkingDirectory</key>",
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        vault = tmp_path / "vault-render-bad"
        vault.mkdir()
        env["VAULT_ROOT"] = str(vault)
        env["AKB2_ALLOW_TMP_VAULT_ROOT"] = "1"
        env["AKB2_STATE_ROOT"] = str(tmp_path / "state-render-bad")
        out = tmp_path / "rendered-bad.plist"

        result = _run(
            work / "scripts" / "bootstrap_launchd.sh",
            env,
            ["--plist-src", str(tampered), "--render-only", str(out)],
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert "未替换占位符" in result.stderr, result.stderr
        assert "__AKB2_FOO__" in result.stderr, result.stderr
        assert not out.exists(), "双门拒绝时不该产出渲染件"

    def test_malformed_xml_rejected(self, tmp_path: Path) -> None:
        """渲染后 XML 不合法 → 非 0 退出（plutil/plistlib 门真的在跑）。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-render-xml")
        broken = work / "launchd" / "broken.plist"
        broken.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n'
            "<dict><key>Label</key><string>x</string>\n",  # 未闭合
            encoding="utf-8",
        )
        env = os.environ.copy()
        vault = tmp_path / "vault-render-xml"
        vault.mkdir()
        env["VAULT_ROOT"] = str(vault)
        env["AKB2_ALLOW_TMP_VAULT_ROOT"] = "1"
        env["AKB2_STATE_ROOT"] = str(tmp_path / "state-render-xml")
        out = tmp_path / "rendered-xml.plist"

        result = _run(
            work / "scripts" / "bootstrap_launchd.sh",
            env,
            ["--plist-src", str(broken), "--render-only", str(out)],
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert "不合法" in result.stderr, result.stderr
        assert not out.exists()

    def test_render_only_never_touches_launchctl(self, tmp_path: Path) -> None:
        """--render-only 全程不调 launchctl（fake launchctl 调用日志为空）。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-render-noctl")
        env, launch_agents, call_log, _home = _prepare_env(tmp_path, tag="render-noctl")
        out = tmp_path / "rendered-noctl.plist"

        result = _run(
            work / "scripts" / "bootstrap_launchd.sh", env, ["--render-only", str(out)]
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert call_log.read_text(encoding="utf-8") == ""
        assert not (launch_agents / f"{BRIEFING_LABEL}.plist").exists()


# ===========================================================================
# SC-6 — worktree 守卫
# ===========================================================================


class TestWorktreeGuard:
    @pytest.mark.parametrize(
        "script_name", ["bootstrap_launchd.sh", "bootstrap_intake_launchd.sh"]
    )
    def test_cindy_worktree_path_rejected(self, tmp_path: Path, script_name: str) -> None:
        """脚本位于 .cindy-worktrees/<any>/ 下 → 非 0 退出 + stderr 有 worktree 提示。"""
        work = _prepare_fake_repo(tmp_path, subdir=".cindy-worktrees/wt-guard")
        env, _la, call_log, _home = _prepare_env(tmp_path, tag=f"wt-{script_name}")

        result = _run(work / "scripts" / script_name, env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "worktree" in result.stderr, result.stderr
        assert str(work) in result.stderr, result.stderr
        # 守卫在任何 launchctl 之前
        assert call_log.read_text(encoding="utf-8") == ""

    @pytest.mark.parametrize(
        "script_name", ["bootstrap_launchd.sh", "bootstrap_intake_launchd.sh"]
    )
    def test_explicit_override_lets_it_through(self, tmp_path: Path, script_name: str) -> None:
        """AKB2_ALLOW_WORKTREE_INSTALL=1 → 放行并继续走后续步骤。"""
        work = _prepare_fake_repo(tmp_path, subdir=".cindy-worktrees/wt-allow")
        verify = INTAKE_PRINT_OK if "intake" in script_name else BRIEFING_PRINT_OK
        env, launch_agents, _log, _home = _prepare_env(
            tmp_path, verify_out=verify, tag=f"wtok-{script_name}"
        )
        env["AKB2_ALLOW_WORKTREE_INSTALL"] = "1"

        result = _run(work / "scripts" / script_name, env)
        combined = result.stdout + result.stderr

        assert result.returncode == 0, combined
        assert "AKB2_ALLOW_WORKTREE_INSTALL=1" in result.stderr, result.stderr
        label = INTAKE_LABEL if "intake" in script_name else BRIEFING_LABEL
        assert (launch_agents / f"{label}.plist").exists(), combined

    def test_git_linked_worktree_rejected_without_path_hint(self, tmp_path: Path) -> None:
        """git linked worktree（路径不含 .cindy-worktrees）也必须被 git 判据拦住。

        判据：`git rev-parse --git-dir` != `--git-common-dir`。
        """
        main_repo = _prepare_fake_repo(tmp_path, subdir="git-main")
        for cmd in (
            ["git", "init", "--initial-branch=main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "AKB2 Test"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "fixture"],
        ):
            subprocess.run(cmd, cwd=main_repo, check=True, capture_output=True, text=True)
        wt = tmp_path / "plain-worktree"
        subprocess.run(
            ["git", "worktree", "add", str(wt), "-b", "wt-branch"],
            cwd=main_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert ".cindy-worktrees" not in str(wt)

        env, _la, call_log, _home = _prepare_env(tmp_path, tag="gitwt")
        result = _run(wt / "scripts" / "bootstrap_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "git worktree" in result.stderr, result.stderr
        assert "git-common-dir" in result.stderr, result.stderr
        assert call_log.read_text(encoding="utf-8") == ""

    def test_main_repo_checkout_is_not_flagged(self, tmp_path: Path) -> None:
        """主仓 checkout（git-dir == git-common-dir）不得被误判成 worktree。"""
        main_repo = _prepare_fake_repo(tmp_path, subdir="git-main-ok")
        for cmd in (
            ["git", "init", "--initial-branch=main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "AKB2 Test"],
            ["git", "add", "-A"],
            ["git", "commit", "-m", "fixture"],
        ):
            subprocess.run(cmd, cwd=main_repo, check=True, capture_output=True, text=True)

        env, launch_agents, _log, _home = _prepare_env(
            tmp_path, verify_out=BRIEFING_PRINT_OK, tag="gitmain"
        )
        result = _run(main_repo / "scripts" / "bootstrap_launchd.sh", env)

        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "worktree" not in result.stderr, result.stderr
        assert (launch_agents / f"{BRIEFING_LABEL}.plist").exists(), combined


# ===========================================================================
# SC-7 — 验证断言的 AND 语义（旧版 OR + 死值 7200）
# ===========================================================================


class TestVerificationAndSemantics:
    def test_no_stale_7200_literal(self) -> None:
        """`7200` 在 intake 安装脚本里零命中（commit 115554f 已把调度改成 900）。"""
        text = (REPO / "scripts" / "bootstrap_intake_launchd.sh").read_text(encoding="utf-8")
        assert "7200" not in text, "陈旧的 7200 仍在脚本里"

    def test_intake_verification_fails_when_interval_missing(self, tmp_path: Path) -> None:
        """launchctl print 缺 900（仍含 orchestrator + 旧值 7200）→ 非 0 退出。

        这条直接证明 AND 语义生效：旧版 OR 会因为 `intake.orchestrator` 命中而放行。
        """
        work = _prepare_fake_repo(tmp_path, subdir="repo-and-intake")
        env, launch_agents, _log, _home = _prepare_env(
            tmp_path, verify_out=INTAKE_PRINT_MISSING_INTERVAL, tag="and-intake"
        )

        result = _run(work / "scripts" / "bootstrap_intake_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "StartInterval=900" in result.stderr, result.stderr
        # bootstrap 本身是成功的 → 失败纯粹来自验证断言
        assert (launch_agents / f"{INTAKE_LABEL}.plist").exists()

    def test_intake_verification_fails_when_orchestrator_missing(self, tmp_path: Path) -> None:
        """launchctl print 有 900 但无 orchestrator → 同样非 0 退出。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-and-intake2")
        env, _la, _log, _home = _prepare_env(
            tmp_path,
            verify_out="com.akb2.intake = {\n\trun interval = 900\n}\n",
            tag="and-intake2",
        )

        result = _run(work / "scripts" / "bootstrap_intake_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "intake.orchestrator" in result.stderr, result.stderr

    def test_briefing_verification_fails_when_hour_missing(self, tmp_path: Path) -> None:
        """briefing:print 有 daily_briefing.sh 但调度不是 hour = 7 → 非 0 退出。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-and-brief")
        env, _la, _log, _home = _prepare_env(
            tmp_path,
            verify_out="com.akb2.briefing = {\n\t/x/scripts/daily_briefing.sh\n\thour = 3\n}\n",
            tag="and-brief",
        )

        result = _run(work / "scripts" / "bootstrap_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "hour = 7" in result.stderr, result.stderr

    def test_briefing_verification_fails_when_script_missing(self, tmp_path: Path) -> None:
        """briefing:print 有 hour = 7 但未引用 daily_briefing.sh → 非 0 退出。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-and-brief2")
        env, _la, _log, _home = _prepare_env(
            tmp_path,
            verify_out="com.akb2.briefing = {\n\t/x/scripts/other.sh\n\thour = 7\n}\n",
            tag="and-brief2",
        )

        result = _run(work / "scripts" / "bootstrap_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert "daily_briefing.sh" in result.stderr, result.stderr


# ===========================================================================
# 语法 / 可执行位
# ===========================================================================


class TestScriptHygiene:
    @pytest.mark.parametrize(
        "script_rel",
        [
            "scripts/bootstrap_launchd.sh",
            "scripts/bootstrap_intake_launchd.sh",
            "scripts/lib/launchctl.sh",
        ],
    )
    def test_bash_n(self, script_rel: str) -> None:
        r = subprocess.run(
            ["bash", "-n", str(REPO / script_rel)], capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize(
        "script_rel",
        ["scripts/bootstrap_launchd.sh", "scripts/bootstrap_intake_launchd.sh"],
    )
    def test_executable_bit(self, script_rel: str) -> None:
        assert (REPO / script_rel).stat().st_mode & 0o100, "脚本缺可执行位"


class TestEscapeHatchesNotPersisted:
    """放行口不得被固化成常驻 env —— 否则 fail-closed 门等于永久失效且无人知道。

    `AKB2_ALLOW_WORKTREE_INSTALL` 与 `AKB2_ALLOW_TMP_VAULT_ROOT` 是给测试用的人为放行口，
    触发时会往 stderr 打 ⚠️ 明示。但"明示"只在有人看日志时有效：一旦被写进某个 plist 的
    `EnvironmentVariables`，门就对生产永久失效，而 launchd 不会报错、运行期也看不出异常。

    这正是本次故障的母题 —— 运行态与仓库不一致、且没有任何机制会发现。
    2026-07-28 的事故（bootstrap 把 mktemp 目录写进生产 plist 的 --vault-root）就是门缺失的后果；
    这条守卫防的是"门装好之后又被静默绕开"。
    """

    ESCAPE_HATCHES = ("AKB2_ALLOW_WORKTREE_INSTALL", "AKB2_ALLOW_TMP_VAULT_ROOT")

    @classmethod
    def _scan(cls, plist_dir: Path) -> list[str]:
        """返回 plist_dir 下所有夹带放行口的 (文件名, 变量) 描述。

        抽成单一实现,好让下面那条种入违规的反证跑的是**同一段扫描代码**——
        否则"真实目录扫不出东西"与"扫描本身坏了"在结果上无法区分。
        """
        offenders: list[str] = []
        plists = sorted(plist_dir.glob("*.plist"))
        assert plists, f"{plist_dir} 下没有 plist,断言失去意义"
        for plist in plists:
            text = plist.read_text(encoding="utf-8")
            for hatch in cls.ESCAPE_HATCHES:
                if hatch in text:
                    offenders.append(f"{plist.name} 含 {hatch}")
        return offenders

    def test_no_escape_hatch_in_any_plist(self) -> None:
        offenders = self._scan(REPO / "launchd")
        assert not offenders, (
            "放行口被写进 plist，fail-closed 门对生产已失效：\n  " + "\n  ".join(offenders)
        )

    def test_scan_actually_catches_a_planted_hatch(self, tmp_path: Path) -> None:
        """反证:把放行口种进 plist 的 EnvironmentVariables → 扫描必须逐条抓出来。

        没有这条,上面那个"真实 launchd/ 干净"的断言可能只是扫描坏掉后的假绿。
        """
        clean = tmp_path / "com.akb2.clean.plist"
        clean.write_text(
            (REPO / "launchd" / f"{BRIEFING_LABEL}.plist").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        for hatch in self.ESCAPE_HATCHES:
            planted = tmp_path / f"com.akb2.planted-{hatch.lower()}.plist"
            planted.write_text(
                "<plist><dict>\n"
                "  <key>EnvironmentVariables</key>\n"
                f"  <dict><key>{hatch}</key><string>1</string></dict>\n"
                "</dict></plist>\n",
                encoding="utf-8",
            )

        offenders = self._scan(tmp_path)

        for hatch in self.ESCAPE_HATCHES:
            assert any(hatch in o for o in offenders), f"扫描漏掉了种入的 {hatch}: {offenders}"
        assert not any("clean" in o for o in offenders), f"干净 plist 被误报: {offenders}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
