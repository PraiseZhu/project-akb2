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

全程不执行真 launchctl、不写真 ~/Library/LaunchAgents：
launchctl 用 PATH stub 假冒，安装目录用 AKB2_LAUNCH_AGENTS_DIR 注入到 tmpdir。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

BRIEFING_LABEL = "com.akb2.briefing"
GITHUB_BRIEFING_LABEL = "com.akb2.github_briefing"

# ── 逃逸口登记表（单一 owner）────────────────────────────────────────────────
# 「写进常驻 env 就能静默改变生产行为」的 env 键全集。两类都必须在册：
#   ① AKB2_ALLOW_*  —— 放行一道 fail-closed 门
#   ② *_PIPELINE_BIN / *_RERUN_SCRIPT / *_READER —— 直接指定要执行什么（更强）
# scripts/verify-launchd-parity.sh 的 _ESCAPE_HATCH_ENV_KEYS 必须与本表逐字相等，
# 且本表必须覆盖脚本里实际存在的全部同形态接缝（两条断言见文件末尾）。
ESCAPE_HATCH_ENV_KEYS: tuple[str, ...] = (
    "AKB2_ALLOW_TMP_VAULT_ROOT",
    "AKB2_ALLOW_WORKTREE_INSTALL",
    "AKB2_BACKSTOP_READER",
    "AKB2_BRIEFING_RERUN_SCRIPT",
    "AKB2_GITHUB_BRIEFING_PIPELINE_BIN",
    "AKB2_GITHUB_RERUN_SCRIPT",
    "AKB2_SOCIAL_BRIEFING_PIPELINE_BIN",
)

# 假 launchctl print 的验证输出：分别喂给两个脚本的 Verification 段。
BRIEFING_PRINT_OK = """com.akb2.briefing = {
	arguments = {
		/bin/bash
		/fake/repo/scripts/daily_briefing.sh
	}
	"Hour" => 7
	minute = 30
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


def _git_init(work: Path) -> None:
    """把假仓根初始化成真 git 仓。

    为什么必须 init：worktree 守卫现在对**git 判定失败**也 fail-closed（SC-R1-22）——
    「问不出来」不能被推断成「这是主仓」。于是假仓根必须是个能回答 rev-parse 的
    真仓，否则测的就不再是各条守卫本身，而全变成 git-unavailable 拒绝路径。
    """
    subprocess.run(
        ["git", "init", "--quiet", str(work)],
        check=True, capture_output=True, text=True,
    )


# plist 解析咽喉（src/akb2/common/plist_parse.py）必须一起进假仓：安装脚本的
# 身份派生与 lint 门都从 ${BASH_SOURCE[0]}/../../src 导入它，缺了就整体 fail-closed
# （那是对的方向，但会把每个用例变成"咽喉不可导入"的拒绝路径，测不到脚本逻辑）。
_CHOKEPOINT_RELS = (
    "src/akb2/__init__.py",
    "src/akb2/common/__init__.py",
    "src/akb2/common/plist_parse.py",
)


def _prepare_fake_repo(
    tmp_path: Path, *, subdir: str = "repo", git_init: bool = True
) -> Path:
    """把安装脚本 + 共用 lib + 解析咽喉 + **全部** plist 模板复制到隔离假仓根。

    `git_init=False` 留给专门测「非 git 环境必须被拒」的用例。

    模板从"手工列 3 个"改成 `launchd/*.plist` 全量（2026-08-04）：
    test_render_only_accepts_every_repo_template 要对每个随仓模板跑 render-only，
    手工清单会让新增模板天然逃过覆盖 —— 而本轮 blocker（feishu-sync /
    inflow-sentinel 装不上）正好就落在那两个不在手工清单里的模板上。
    """
    work = tmp_path / subdir
    (work / "scripts" / "lib").mkdir(parents=True)
    (work / "launchd").mkdir(parents=True)
    (work / "src" / "akb2" / "common").mkdir(parents=True)
    templates = sorted(p.name for p in (REPO / "launchd").glob("*.plist"))
    assert templates, "REPO/launchd 下没有 plist 模板 —— 假仓会是空的"
    for rel in (
        "scripts/bootstrap_launchd.sh",
        "scripts/lib/launchctl.sh",
        "scripts/lib/state-root.sh",
        *_CHOKEPOINT_RELS,
        *(f"launchd/{name}" for name in templates),
    ):
        shutil.copy2(REPO / rel, work / rel)
    if git_init:
        _git_init(work)
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
    # python3 桩：intake preflight 的 `import akb2.quality...` / `import playwright`
    # 一律放行（exit 0），但**plist 相关的调用必须走真 python3** —— 身份派生
    # （akb2_plist_identity）、占位符残留门、plist 合法性门都靠
    # akb2.common.plist_parse，一刀切 exit 0 会让 Label 解析恒空、残留门恒过，
    # 测的就不再是脚本逻辑而是桩的形状。
    # 匹配 `*plist*` 而不是 `*plistlib*`：解析改走咽喉后代码里出现的是
    # `plist_parse` / `parse_plist_file`，钉死 `plistlib` 会让三道门全部落到 exit 0
    # 的静默放行分支（本轮实测：test_malformed_xml_rejected 会假绿）。
    _write_executable(
        fake_bin / "python3",
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        f"    -|*plist*) exec {shutil.which('python3')} \"$@\" ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
    )

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


# ===========================================================================
# SC-2 — 静态：teardown 等待存在 + launchctl stderr 不再被裸 2>/dev/null 吞掉
# ===========================================================================


class TestTeardownWaitStatic:
    @pytest.mark.parametrize(
        "script_rel",
        ["scripts/bootstrap_launchd.sh"],
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
        ["scripts/bootstrap_launchd.sh"],
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
        """共用包装必须同时打出 exit code 与 stderr，且**每次外部调用都有界**。

        断言从"包装里出现某段字面量"改成"包装经 akb2_run_bounded 走 + 仍打 exit/stderr"：
        SC-R1-20 给三个 launchctl 调用都加了 wall-clock 上限，原字面量已不复存在，
        但要守住的不变量没变（失败可见 + 有界）。
        """
        text = (REPO / "scripts" / "lib" / "launchctl.sh").read_text(encoding="utf-8")
        assert "akb2_launchctl_bootstrap()" in text
        assert "akb2_wait_for_launchctl_teardown()" in text
        assert "akb2_run_bounded()" in text
        # 三个外部调用一个都不许裸跑（都必须经有界 runner）。
        # 只看**真正执行** launchctl 的行：printf/echo 里的文案会提到同样的字样。
        for probe in ("launchctl bootout", "launchctl print", "launchctl bootstrap"):
            calls = [
                line.strip()
                for line in text.splitlines()
                if probe in line
                and not line.lstrip().startswith("#")
                and not line.lstrip().startswith(("printf", "echo"))
            ]
            assert calls, f"{probe} 的调用点消失了？"
            for call in calls:
                assert "akb2_run_bounded" in call, (
                    f"{probe} 存在未加 wall-clock 上限的裸调用: {call}"
                )
        # 失败留痕：exit code 与 stderr 都要进错误文案
        assert 'exit=%s' in text
        assert "AKB2_BOUNDED_OUTPUT" in text
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


# ===========================================================================
# SC-5 — 渲染产物双门（占位符残留 + plist 合法性）
# ===========================================================================


# ═══════════════════════════════════════════════════════════════════════════
# SC-R2-01 —— 安装器必须装得上仓库自带的每一个 plist 模板
# 模块级（不进 TestRenderGates）：SC 的 verify nodeid 就是模块级函数名，
# 挪进类里 orchestrator 复跑时收集不到。
# ═══════════════════════════════════════════════════════════════════════════


def test_render_only_accepts_every_repo_template(tmp_path: Path) -> None:
    """SC-R2-01：`--render-only` 对 launchd/ 下**每一个**随仓模板都必须 exit=0。

    R2 blocker（干净 clone 实测）：
        --plist-src launchd/com.akb2.feishu-sync.plist    → exit=2「无法从 plist 解析 Label」
        --plist-src launchd/com.akb2.inflow-sentinel.plist → exit=2（同上）
        --plist-src launchd/com.akb2.briefing.plist        → exit=0（对照组）
    也就是**安装器装不上仓库自带的两个模板**，而 owner 待办里正有一项 launchd 重装。

    两个独立成因，都在同一条 render-only 路径上：
      ① 身份派生 / lint 门用裸 plistlib（严），而 parity 读 installed plist 用剥注释
         回退（宽）—— 两个模板的文档注释含裸 `--`（`--dry-run` / `--target dm`），
         XML 规范禁注释内 `--`，strict expat 拒解，但 plutil 与 launchd 自身接受。
         修法：单一咽喉 akb2.common.plist_parse，4 个解析点全部改调它。
      ② 占位符残留门直接 grep 整文件文本，而 inflow-sentinel 的安装说明注释里就写着
         验证命令 `! grep -q "__AKB2_" <plist>` —— 那串字面量渲染后仍在注释里，
         门恒命中；且 `-o '__AKB2_[..]*__'` 匹配不到裸前缀，报错文案后面是空白。
         修法：残留判定改按剥注释后的结构内容。

    用参数化而不是循环：哪个模板坏了直接看用例名，不用读断言消息。
    清单从磁盘 glob（`_prepare_fake_repo` 已全量复制），新增模板自动进覆盖。
    """
    templates = sorted(p.name for p in (REPO / "launchd").glob("*.plist"))
    assert len(templates) >= 7, f"模板数异常少，断言可能已失去意义: {templates}"

    work = _prepare_fake_repo(tmp_path, subdir="repo-render-every")
    # 假仓是 `git init` 出来的真 git 仓、路径不含 .cindy-worktrees → worktree 守卫
    # 走的是"主仓放行"分支，不靠 AKB2_ALLOW_WORKTREE_INSTALL 绕过。
    assert ".cindy-worktrees" not in str(work)
    vault = tmp_path / "vault-render-every"
    (vault / "Praise Space" / "AKB2.0").mkdir(parents=True)

    failures: list[str] = []
    for name in templates:
        env = os.environ.copy()
        env["VAULT_ROOT"] = str(vault)
        # tmp_path 在 macOS 上就在 /var/folders 下 → VAULT_ROOT 门 3 会拦；
        # 这条用例测的是解析/残留两道门，临时目录门与它无关，显式放行。
        env["AKB2_ALLOW_TMP_VAULT_ROOT"] = "1"
        env["AKB2_STATE_ROOT"] = str(tmp_path / f"state-every-{name}")
        out = tmp_path / f"rendered-every-{name}"
        result = _run(
            work / "scripts" / "bootstrap_launchd.sh",
            env,
            ["--plist-src", str(work / "launchd" / name), "--render-only", str(out)],
        )
        if result.returncode != 0 or not out.exists():
            failures.append(
                f"{name}: exit={result.returncode}\n"
                f"    stdout={result.stdout.strip()}\n    stderr={result.stderr.strip()}"
            )
            continue
        # 非空过：渲染件必须真的是**结构上**无残留的合法 plist
        body = out.read_text(encoding="utf-8")
        structural = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        if "__AKB2_" in structural:
            failures.append(f"{name}: 渲染件结构里仍有占位符残留")

    assert not failures, (
        "以下随仓模板过不了 --render-only（安装器装不上仓库自带的 plist）：\n  "
        + "\n  ".join(failures)
    )

def test_placeholder_gate_separates_comment_from_structure(tmp_path: Path) -> None:
    """占位符残留门：注释里的占位符放过，结构里的必拒（两侧都要守）。

    只修"注释里放过"会让门变成"全放过"——那比误拒更危险：真正残留的
    `__AKB2_REPO_ROOT__/scripts/...` 会被装进 LaunchAgents，job 指向字面量路径，
    launchd 不报错、运行期才炸。所以这条用例两侧同时钉。
    """
    lib = REPO / "scripts" / "lib" / "launchctl.sh"
    rendered = (
        (REPO / "launchd" / f"{BRIEFING_LABEL}.plist")
        .read_text(encoding="utf-8")
        .replace("__AKB2_REPO_ROOT__", str(tmp_path))
        .replace("__AKB2_VAULT_ROOT__", str(tmp_path))
        .replace("__AKB2_STATE_ROOT__", str(tmp_path))
        .replace("__AKB2_HOME__", str(tmp_path))
    )

    def probe(text: str, tag: str) -> subprocess.CompletedProcess:
        target = tmp_path / f"{tag}.plist"
        target.write_text(text, encoding="utf-8")
        return subprocess.run(
            ["bash", "-c", f'. "{lib}"\nakb2_assert_render_clean "{target}"'],
            capture_output=True, text=True, env=os.environ.copy(),
        )

    # ① 注释里带占位符（inflow-sentinel 的真实形态：说明里写着验证命令本身）
    commented = rendered.replace(
        "<dict>",
        '<dict>\n<!-- 装完自检： ! grep -q "__AKB2_" <plist>；'
        "另含 __AKB2_REPO_ROOT__ 占位符 -->",
        1,
    )
    ok = probe(commented, "comment-only")
    assert ok.returncode == 0, (
        "注释里的占位符把门触发了（inflow-sentinel 就是这样装不上的）：\n"
        f"{ok.stdout}{ok.stderr}"
    )

    # ② 结构里带占位符 → 必拒，且**点名**是哪个（旧实现这里会打出空白）
    structural = rendered.replace(
        "<key>WorkingDirectory</key>",
        "<key>AKB2Injected</key>\n    <string>__AKB2_FOO__</string>\n"
        "    <key>WorkingDirectory</key>",
        1,
    )
    bad = probe(structural, "structural")
    assert bad.returncode != 0, f"结构里的占位符被放行了：\n{bad.stdout}{bad.stderr}"
    assert "未替换占位符" in bad.stderr, bad.stderr
    assert "__AKB2_FOO__" in bad.stderr, (
        f"拒绝了但没点名残留的占位符（运维无从下手）：\n{bad.stderr}"
    )


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
        "script_name", ["bootstrap_launchd.sh"]
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
        "script_name", ["bootstrap_launchd.sh"]
    )
    def test_explicit_override_lets_it_through(self, tmp_path: Path, script_name: str) -> None:
        """AKB2_ALLOW_WORKTREE_INSTALL=1 → 放行并继续走后续步骤。"""
        work = _prepare_fake_repo(tmp_path, subdir=".cindy-worktrees/wt-allow")
        verify = BRIEFING_PRINT_OK
        env, launch_agents, _log, _home = _prepare_env(
            tmp_path, verify_out=verify, tag=f"wtok-{script_name}"
        )
        env["AKB2_ALLOW_WORKTREE_INSTALL"] = "1"

        result = _run(work / "scripts" / script_name, env)
        combined = result.stdout + result.stderr

        assert result.returncode == 0, combined
        assert "AKB2_ALLOW_WORKTREE_INSTALL=1" in result.stderr, result.stderr
        label = BRIEFING_LABEL
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


    def test_briefing_verification_fails_when_hour_missing(self, tmp_path: Path) -> None:
        """briefing:print 有 daily_briefing.sh 但调度不是 Hour => 7 → 非 0 退出。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-and-brief")
        env, _la, _log, _home = _prepare_env(
            tmp_path,
            verify_out='com.akb2.briefing = {\n\t/x/scripts/daily_briefing.sh\n\t"Hour" => 3\n}\n',
            tag="and-brief",
        )

        result = _run(work / "scripts" / "bootstrap_launchd.sh", env)

        assert result.returncode != 0, result.stdout + result.stderr
        assert '"Hour" => 7' in result.stderr, result.stderr

    def test_briefing_verification_fails_when_script_missing(self, tmp_path: Path) -> None:
        """briefing:print 有 Hour => 7 但未引用 daily_briefing.sh → 非 0 退出。"""
        work = _prepare_fake_repo(tmp_path, subdir="repo-and-brief2")
        env, _la, _log, _home = _prepare_env(
            tmp_path,
            verify_out='com.akb2.briefing = {\n\t/x/scripts/other.sh\n\t"Hour" => 7\n}\n',
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
        ["scripts/bootstrap_launchd.sh"],
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

    # 两类逃逸口，两类都必须在册（加固清单第 6 类：默认 exact，subset 让漏项通过）：
    #   ① AKB2_ALLOW_*  —— 放行一道 fail-closed 门
    #   ② *_PIPELINE_BIN / *_RERUN_SCRIPT / *_READER —— **直接指定要执行什么**（更强）
    # 此前只登记了 ①：守卫覆盖了弱的、漏了强的。`AKB2_SOCIAL_BRIEFING_PIPELINE_BIN`
    # 能把整条 PIPELINE_CMD（连 --date/--vault-root 一起）替换掉，而 launchd 不报错。
    # 清单与「脚本实际读取的 env 集合」由
    # test_escape_hatch_registry_covers_all_override_envs 对账，不靠人工同步。
    ESCAPE_HATCHES = ESCAPE_HATCH_ENV_KEYS

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


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-15..23 —— 安装事务：身份派生 / 回滚 / 等价 lint / 有界探针 / 三门 fail-closed
# ═══════════════════════════════════════════════════════════════════════════

GITHUB_PRINT_OK = """com.akb2.github_briefing = {
	arguments = {
		/bin/bash
		/fake/repo/scripts/launchd_wrapper.sh
	}
	"Hour" => 5
	minute = 30
}
"""


RETRIEVAL_HTTP_LABEL = "com.akb2.retrieval-http"
RETRIEVAL_HTTP_PRINT_OK = """com.akb2.retrieval-http = {
		arguments = {
			/bin/bash
			/fake/repo/scripts/akb2-retrieval-http.sh
		}
		properties = keepalive | inferred program
	}
	"""


def test_plist_src_derives_identity_from_template_label(tmp_path: Path) -> None:
    """--plist-src 传 github 模板 → 全链身份为 com.akb2.github_briefing，零 social 痕迹。

    这是本组的 blocker：`--plist-src` 是新增参数，而 Label / 目标文件名 / launchctl 域
    标签 / 验证断言在旧版里**全是硬编码的 com.akb2.briefing**。照 INSTALL-MANIFEST 执行
    那条命令的实际序列是：备份并覆写 com.akb2.briefing.plist → bootout social →
    从该路径 bootstrap（注册进去的 Label 却是 github_briefing）→ 按 social 判据验证
    （必然失败）→ 回滚。净效果 = 把 social 主跑 bounce 一遍，github 一次也没装上。
    同一个脚本 2026-07-28 刚把 com.akb2.briefing 卸掉 5 天 22 小时。
    """
    work = _prepare_fake_repo(tmp_path, subdir="repo-identity")
    env, launch_agents, call_log, _home = _prepare_env(
        tmp_path, verify_out=GITHUB_PRINT_OK, tag="identity"
    )

    result = _run(
        work / "scripts" / "bootstrap_launchd.sh",
        env,
        ["--plist-src", str(work / "launchd" / f"{GITHUB_BRIEFING_LABEL}.plist")],
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 0, combined
    # ① 目标文件名 = 模板 Label
    assert (launch_agents / f"{GITHUB_BRIEFING_LABEL}.plist").exists(), combined
    # ② social 的 plist 一个字节都没被碰
    assert not (launch_agents / f"{BRIEFING_LABEL}.plist").exists(), (
        "装 github 却写出/覆写了 social 的 plist"
    )
    # ③ launchctl 的域标签（bootout/bootstrap/print）全部是 github 身份，零 social
    calls = call_log.read_text(encoding="utf-8")
    assert GITHUB_BRIEFING_LABEL in calls, calls
    assert BRIEFING_LABEL not in calls, f"launchctl 被喂了 social 身份:\n{calls}"
    # ④ 验证断言用的是 github 模板派生的 token（launchd_wrapper.sh + "Hour" => 5）
    assert "launchd_wrapper.sh detected" in result.stdout, result.stdout
    assert '"Hour" => 5' in result.stdout, result.stdout
    assert "daily_briefing.sh" not in combined, combined


def test_manifest_installer_line_installs_declared_label(tmp_path: Path) -> None:
    """按 INSTALL-MANIFEST 记载的 installer 原文执行 → 装出的必须是它声明的那个 label。

    操作手册记载的命令必须真能安装该 label，不得覆写另一个生产 job（SC-R1-16）。
    本用例直接读 manifest，不复述命令 —— 手册改了这里会跟着变。
    """
    import shlex

    import yaml

    manifest = yaml.safe_load(
        (REPO / "launchd" / "INSTALL-MANIFEST.yaml").read_text(encoding="utf-8")
    )
    jobs = manifest["jobs"] if isinstance(manifest, dict) and "jobs" in manifest else manifest
    targets = [
        job for job in jobs
        if isinstance(job.get("installer"), str)
        and "bootstrap_launchd.sh" in job["installer"]
        and "--plist-src" in job["installer"]
    ]
    assert targets, "manifest 里没有 --plist-src 形态的 installer —— 断言会恒真"

    verify_by_label = {
        BRIEFING_LABEL: BRIEFING_PRINT_OK,
        GITHUB_BRIEFING_LABEL: GITHUB_PRINT_OK,
        RETRIEVAL_HTTP_LABEL: RETRIEVAL_HTTP_PRINT_OK,
    }
    for index, job in enumerate(targets):
        label = job["label"]
        assert label in verify_by_label, f"manifest 新增了未覆盖的 label: {label}"
        work = _prepare_fake_repo(tmp_path, subdir=f"repo-manifest-{index}")
        env, launch_agents, call_log, _home = _prepare_env(
            tmp_path, verify_out=verify_by_label[label], tag=f"manifest-{index}"
        )
        # 原文里的相对路径按假仓根解析（脚本自身也在假仓根内）
        parts = shlex.split(job["installer"])
        script = work / parts[0].split("scripts/", 1)[1] if False else work / parts[0]
        args = [
            str(work / arg) if arg.startswith("launchd/") else arg
            for arg in parts[1:]
        ]

        result = _run(script, env, args)
        combined = result.stdout + result.stderr

        assert result.returncode == 0, f"manifest 命令跑不通（{label}）:\n{combined}"
        assert (launch_agents / f"{label}.plist").exists(), (
            f"manifest 声明装 {label}，实际产物不是它:\n{combined}"
        )
        installed = sorted(p.name for p in launch_agents.iterdir())
        assert installed == [f"{label}.plist"], (
            f"manifest 命令顺手动了别的 job 的 plist: {installed}"
        )
        assert label in call_log.read_text(encoding="utf-8")


def test_postverify_failure_rolls_back(tmp_path: Path) -> None:
    """后置验证失败 → 恢复执行前 plist + 重新 bootstrap 旧 job + 非 0 退出。

    旧版验证失败是直接 `exit 2`：报告了失败，却把**已知有问题的新 job 留在册**，
    同时 EXIT trap 把 last-good 备份删掉 —— 「新版已装未验证」正是 lane 4 要消掉的态。
    """
    work = _prepare_fake_repo(tmp_path, subdir="repo-postverify")
    env, launch_agents, call_log, _home = _prepare_env(
        tmp_path,
        # print 里 daily_briefing.sh 有、但 hour 是 3（模板是 7）→ 调度断言失败
        verify_out='com.akb2.briefing = {\n\t/x/scripts/daily_briefing.sh\n\t"Hour" => 3\n}\n',
        tag="postverify",
    )
    dst = launch_agents / f"{BRIEFING_LABEL}.plist"
    dst.write_text("<plist>OLD-GOOD</plist>\n", encoding="utf-8")

    result = _run(work / "scripts" / "bootstrap_launchd.sh", env)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, combined
    assert "回滚到执行前状态" in result.stderr, result.stderr
    assert dst.read_text(encoding="utf-8") == "<plist>OLD-GOOD</plist>\n", (
        f"验证失败后没把旧 plist 恢复回去:\n{combined}"
    )
    # 旧 job 被重新 bootstrap（不留"无 job 在册"）
    assert len(_bootstrap_calls(call_log)) >= 2, call_log.read_text(encoding="utf-8")
    # 备份临时文件不该留在 LaunchAgents 里
    leftovers = [p.name for p in launch_agents.iterdir() if ".bak." in p.name]
    assert not leftovers, f"回滚后仍留着备份残留: {leftovers}"


def test_rollback_mv_failure_preserves_backup(tmp_path: Path) -> None:
    """回滚补偿（mv 备份回安装位）失败 → 保住备份 + 打手动恢复指引，绝不静默。

    旧实现是裸 `mv -f`：`set -e` 下失败会**整函数中断**，「手动补装」一行都不打印，
    随后 EXIT trap 因为备份变量仍非空把**唯一的旧 good 副本 rm 掉**。
    留下的态是「新 plist 在册位 + 无 job 在册 + 旧 plist 副本已删」——
    恰好落在 lane 4 要修的那个事故的补偿路径上（加固清单第 1 类第 ⑤ 支）。
    """
    work = _prepare_fake_repo(tmp_path, subdir="repo-mvfail")
    env, launch_agents, _log, _home = _prepare_env(
        tmp_path, verify_out=BRIEFING_PRINT_OK, always_fail_bootstrap=True, tag="mvfail"
    )
    dst = launch_agents / f"{BRIEFING_LABEL}.plist"
    dst.write_text("<plist>OLD-GOOD</plist>\n", encoding="utf-8")
    # 注入 mv 失败：PATH 前置一个恒失败的 mv（脚本里的 mv 只有备份恢复这一处用到）
    fail_bin = tmp_path / "fail-bin-mvfail"
    fail_bin.mkdir()
    # 只让**移备份**那一步失败：脚本里另有一处 `mv -f "$rendered" "$dst"`（原子安装），
    # 一刀切失败会让脚本死在安装步，根本走不到回滚补偿路径。
    _write_executable(
        fail_bin / "mv",
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        "    *.bak.*) echo 'mv: Operation not permitted' >&2; exit 1 ;;\n"
        "  esac\n"
        "done\n"
        "exec /bin/mv \"$@\"\n",
    )
    env["PATH"] = f"{fail_bin}:{env['PATH']}"

    result = _run(work / "scripts" / "bootstrap_launchd.sh", env)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, combined
    assert "回滚补偿失败" in result.stderr, f"补偿失败被静默了:\n{combined}"
    assert "手动恢复" in result.stderr, f"没给人工恢复指引:\n{combined}"
    backups = sorted(p for p in launch_agents.iterdir() if ".bak." in p.name)
    assert backups, f"备份被 EXIT trap 删掉了（唯一的旧 good 副本丢失）:\n{combined}"
    assert backups[0].read_text(encoding="utf-8") == "<plist>OLD-GOOD</plist>\n"
    # 指引里必须含备份的真实路径，否则"手动恢复"无从下手
    assert str(backups[0]) in result.stderr, result.stderr


def _fragment_plist_body() -> str:
    """裸 `<string>` 片段 —— plutil -lint 会说 OK，plistlib 会拒（实测的判定分叉点）。"""
    return "<string>a</string>\n<string>b</string>\n"


def _path_without_plutil(tmp_path: Path, tag: str) -> str:
    """构造一个**不含 plutil** 的最小 PATH（模拟 CI 的 ubuntu runner）。"""
    clean_bin = tmp_path / f"clean-bin-{tag}"
    clean_bin.mkdir(exist_ok=True)
    for tool in (
        "bash", "sed", "grep", "mktemp", "id", "cat", "rm", "mkdir", "cp", "mv",
        "sort", "tr", "sleep", "kill", "dirname", "basename", "git", "python3",
    ):
        found = shutil.which(tool)
        if found and not (clean_bin / tool).exists():
            (clean_bin / tool).symlink_to(found)
    assert shutil.which("plutil", path=str(clean_bin)) is None
    return str(clean_bin)


@pytest.mark.parametrize("with_plutil", [True, False], ids=["with-plutil", "no-plutil"])
def test_lint_gate_rejects_fragment_on_both_platforms(
    tmp_path: Path, with_plutil: bool
) -> None:
    """同一道门在有/无 plutil 两种环境下判定必须一致（都拒裸片段）。

    实测（R1 审查 B2）：`plutil -lint frag.plist` → OK(exit 0)，
    `python3 -c 'plistlib.load(...)'` → InvalidFileException(exit 1)。
    旧实现「优先 plutil，无 plutil 才退 plistlib」于是让**生产平台（macOS）上这道
    fail-closed 门放行了根本不是 plist 的内容**，而 CI（ubuntu，无 plutil）反而更严
    —— 门的强度随主机而变，且声称强度 > 实际强度。

    断言打在**函数级**（直接调 akb2_assert_render_clean）：脚本级的整链里
    `akb2_plist_identity` 排在这道门之前，也会拒掉裸片段 —— 那样即使把 lint 门改回
    plutil 优先，脚本仍然非 0 退出，测试会因为**另一个**原因变绿（本轮实测过这个假绿）。
    """
    lib = REPO / "scripts" / "lib" / "launchctl.sh"
    fragment = tmp_path / f"fragment-{int(with_plutil)}.plist"
    fragment.write_text("<string>a</string>\n<string>b</string>\n", encoding="utf-8")

    if with_plutil:
        plutil = shutil.which("plutil")
        if plutil is None:
            pytest.skip("本机无 plutil（Linux）——no-plutil 分支已覆盖同一判定")
        # 先自证前提：plutil 单独判这个片段确实是 OK（否则本用例证明不了什么）
        lint = subprocess.run(
            [plutil, "-lint", str(fragment)], capture_output=True, text=True
        )
        assert lint.returncode == 0, (
            f"前提不成立：本机 plutil 已经会拒裸片段，用例失去意义\n{lint.stdout}{lint.stderr}"
        )
        path = os.environ["PATH"]
    else:
        path = _path_without_plutil(tmp_path, f"lint-{int(with_plutil)}")

    probe = subprocess.run(
        ["bash", "-c", f'. "{lib}"\nakb2_assert_render_clean "{fragment}"'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": path},
    )
    assert probe.returncode != 0, (
        "裸片段被这道 fail-closed 门放行了"
        f"（with_plutil={with_plutil}）：\n{probe.stdout}{probe.stderr}"
    )
    assert "不合法" in probe.stderr, probe.stderr

    # 反向守卫：合法 plist 在两种环境下都必须放行（别把门做成"全拒"）
    valid = tmp_path / f"valid-{int(with_plutil)}.plist"
    valid.write_text(
        (REPO / "launchd" / f"{BRIEFING_LABEL}.plist").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    ok = subprocess.run(
        ["bash", "-c", f'. "{lib}"\nakb2_assert_render_clean "{valid}"'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": path},
    )
    # 模板里还有占位符 → 会被第一道门（占位符残留）拒，正好也是要守的行为
    assert "未替换占位符" in ok.stderr, ok.stderr
    rendered = tmp_path / f"rendered-{int(with_plutil)}.plist"
    rendered.write_text(
        valid.read_text(encoding="utf-8")
        .replace("__AKB2_REPO_ROOT__", str(tmp_path))
        .replace("__AKB2_VAULT_ROOT__", str(tmp_path))
        .replace("__AKB2_STATE_ROOT__", str(tmp_path))
        .replace("__AKB2_HOME__", str(tmp_path)),
        encoding="utf-8",
    )
    ok2 = subprocess.run(
        ["bash", "-c", f'. "{lib}"\nakb2_assert_render_clean "{rendered}"'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": path},
    )
    assert ok2.returncode == 0, (
        f"合法且已渲染的 plist 被拒（门做成全拒了）：\n{ok2.stdout}{ok2.stderr}"
    )

    # 脚本级同样必须拒（纵深：门在碰 launchctl 之前）
    work = _prepare_fake_repo(tmp_path, subdir=f"repo-lint-{int(with_plutil)}")
    env, _la, call_log, _home = _prepare_env(
        tmp_path, verify_out=BRIEFING_PRINT_OK, tag=f"lint-{int(with_plutil)}"
    )
    env["PATH"] = f"{env['PATH'].split(':')[0]}:{path}"
    result = _run(
        work / "scripts" / "bootstrap_launchd.sh", env, ["--plist-src", str(fragment)]
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not call_log.read_text(encoding="utf-8").strip(), (
        f"非法模板已经动了 launchctl:\n{call_log.read_text(encoding='utf-8')}"
    )


def test_launchctl_probes_have_wall_clock_limit(tmp_path: Path) -> None:
    """launchctl 挂死时探针必须在 wall-clock 上限内返回失败，而不是永久悬挂。

    `AKB2_TEARDOWN_MAX_ATTEMPTS × SLEEP` 限的是**循环次数**，不是单次调用的墙钟：
    一次挂死的 `launchctl print` 就能让脚本永久停在「bootout 已生效 / bootstrap
    还没做」这个破坏性中间态上（CADV-A12）。

    用 subprocess 的 timeout 兜底：没有上限时脚本会等满 stub 的 sleep（600s），
    不设 timeout 的话本用例会**挂住**而不是打红 —— 挂住的测试等于没有测试。
    """
    import time

    work = _prepare_fake_repo(tmp_path, subdir="repo-hang")
    env, _la, _log, _home = _prepare_env(tmp_path, verify_out=BRIEFING_PRINT_OK, tag="hang")
    hang_bin = tmp_path / "hang-bin"
    hang_bin.mkdir()
    # 挂死的 launchctl：sleep 远超上限，不自行退出
    _write_executable(hang_bin / "launchctl", "#!/usr/bin/env bash\nsleep 600\n")
    env["PATH"] = f"{hang_bin}:{env['PATH']}"
    env["AKB2_LAUNCHCTL_TIMEOUT_SEC"] = "1"
    env["AKB2_TEARDOWN_MAX_ATTEMPTS"] = "2"
    env["AKB2_TEARDOWN_SLEEP_SEC"] = "0"

    # 上限 1s × (bootout + 2 次探针 + 回滚里的 bootout/探针) ≈ 个位数秒；给 60s 余量。
    wall_clock_budget_s = 60
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["bash", str(work / "scripts" / "bootstrap_launchd.sh")],
            env=env, capture_output=True, text=True, timeout=wall_clock_budget_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"launchctl 探针没有 wall-clock 上限：脚本在 {wall_clock_budget_s}s 内没返回"
            f"（停在破坏性中间态）。stdout={exc.stdout!r}"
        ) from exc
    elapsed = time.monotonic() - started
    combined = result.stdout + result.stderr

    assert result.returncode != 0, combined
    assert "launchctl_timeout=1" in combined, f"超时没被具名留痕:\n{combined}"
    assert elapsed < wall_clock_budget_s, f"耗时 {elapsed:.1f}s"


def _vault_gate_probe(vault_root: Path, *, prefixes: str) -> subprocess.CompletedProcess:
    """函数级调 akb2_assert_vault_root_sane，前缀表由调用方指定。

    为什么必须函数级：测试沙箱里唯一可写的地方（tmp_path）**本身就在 /var/folders 下**，
    于是"symlink 的原始字符串"和"解析后的真实路径"都命中默认前缀表 —— 脚本级用例
    在「按字符串判」与「按真实路径判」两种实现下**结果相同**，证明不了任何东西
    （本轮实测踩过这个假绿）。这里把前缀表换成只含真实目标的父目录，
    让两种实现的判定必然分叉。
    """
    lib = REPO / "scripts" / "lib" / "launchctl.sh"
    return subprocess.run(
        [
            "bash", "-c",
            f'. "{lib}"\n'
            "unset TMPDIR\n"
            f"AKB2_TMP_PATH_PREFIXES=({prefixes})\n"
            f'akb2_assert_vault_root_sane "{vault_root}"',
        ],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "AKB2_ALLOW_TMP_VAULT_ROOT"},
    )


def test_vault_root_gate_resolves_symlinks(tmp_path: Path) -> None:
    """symlink 指向临时目录的 VAULT_ROOT 必须被门 3 拒绝（按**真实路径**判定）。

    旧实现先跟随 symlink 做 `-d`（门 2），再拿**原始字符串**比前缀（门 3）——
    R1 实测 `~/vault-link → /var/folders/.../real-vault` 一路 guard_rc=0 放行。
    """
    banned_root = tmp_path / "banned-tmp-root"
    real_vault = banned_root / "real-vault"
    (real_vault / "Praise Space" / "AKB2.0").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = outside / "vault-link"
    link.symlink_to(real_vault)

    # ① symlink 原始字符串不在禁用前缀内，真实路径在 → 必须拒
    rejected = _vault_gate_probe(link, prefixes=str(banned_root))
    assert rejected.returncode != 0, (
        "symlink 伪装的临时目录 VAULT_ROOT 被放行（门 3 仍按原始字符串判）：\n"
        f"{rejected.stdout}{rejected.stderr}"
    )
    assert "临时目录" in rejected.stderr, rejected.stderr
    assert str(real_vault) in rejected.stderr, (
        f"错误文案没打出真实路径，运维无从判断为什么被拒:\n{rejected.stderr}"
    )

    # ② 反向守卫：真实路径不在禁用前缀内时，symlink 仍必须放行
    #    （门是"按真实路径判临时目录"，不是"禁 symlink"）
    allowed = _vault_gate_probe(link, prefixes="/no-such-tmp-prefix")
    assert allowed.returncode == 0, (
        f"门变成了禁 symlink：\n{allowed.stdout}{allowed.stderr}"
    )

    # ③ 脚本级纵深：拒绝必须发生在碰 launchctl 之前
    work = _prepare_fake_repo(tmp_path, subdir="repo-symlink")
    env, _la, call_log, _home = _prepare_env(tmp_path, tag="symlink")
    env["VAULT_ROOT"] = str(link)
    env.pop("AKB2_ALLOW_TMP_VAULT_ROOT", None)
    result = _run(work / "scripts" / "bootstrap_launchd.sh", env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not call_log.read_text(encoding="utf-8").strip(), "拒绝前已经动了 launchctl"


@pytest.mark.parametrize(
    "script_rel",
    ["scripts/bootstrap_launchd.sh"],
)
def test_worktree_guard_rejects_on_git_failure(tmp_path: Path, script_rel: str) -> None:
    """git 判定失败（不可用 / 报错 / 非 git 环境）→ 拒绝安装，不当主仓。

    旧写法两次 `rev-parse` 都带 `|| true`，空结果一路走到 `return 0` —— git 不可用 /
    dubious ownership / 只吐一半输出 / 压根不是 git 仓，全被当成「这是主仓，放行」。
    守卫的语义是「确认不在 worktree」，无法确认时不能推断为确认（CADV-F2）。
    """
    script_name = Path(script_rel).name
    # ① 非 git 环境
    work = _prepare_fake_repo(tmp_path, subdir=f"repo-nogit-{script_name}", git_init=False)
    env, _la, call_log, _home = _prepare_env(tmp_path, tag=f"nogit-{script_name}")
    result = _run(work / script_rel, env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, f"非 git 环境被当成主仓放行:\n{combined}"
    assert "rev-parse" in result.stderr or "git" in result.stderr, result.stderr
    assert not call_log.read_text(encoding="utf-8").strip(), "拒绝前已经动了 launchctl"

    # ② git 存在但恒报错（模拟 dubious ownership / 仓库损坏）
    work2 = _prepare_fake_repo(tmp_path, subdir=f"repo-giterr-{script_name}")
    env2, _la2, call_log2, _home2 = _prepare_env(tmp_path, tag=f"giterr-{script_name}")
    err_bin = tmp_path / f"git-err-bin-{script_name}"
    err_bin.mkdir()
    _write_executable(
        err_bin / "git",
        "#!/usr/bin/env bash\n"
        "echo \"fatal: detected dubious ownership in repository\" >&2\nexit 128\n",
    )
    env2["PATH"] = f"{err_bin}:{env2['PATH']}"
    result2 = _run(work2 / script_rel, env2)
    combined2 = result2.stdout + result2.stderr
    assert result2.returncode != 0, f"git 报错被当成主仓放行:\n{combined2}"
    assert "dubious ownership" in result2.stderr or "rev-parse" in result2.stderr, (
        result2.stderr
    )
    assert not call_log2.read_text(encoding="utf-8").strip()

    # ③ 反向守卫：显式放行口仍必须生效（否则守卫变成"无 git 就永远装不了"）
    env3, _la3, _log3, _home3 = _prepare_env(tmp_path, tag=f"nogit-ok-{script_name}")
    env3["AKB2_ALLOW_WORKTREE_INSTALL"] = "1"
    result3 = _run(
        work / script_rel, env3, ["--render-only", str(tmp_path / f"r-{script_name}.plist")]
    ) if script_name == "bootstrap_launchd.sh" else None
    if result3 is not None:
        assert result3.returncode == 0, result3.stdout + result3.stderr


@pytest.mark.parametrize(
    "script_rel",
    ["scripts/bootstrap_launchd.sh"],
)
def test_missing_home_fails_closed(tmp_path: Path, script_rel: str) -> None:
    """unset HOME → 非 0 退出，且不静默替换成任何字面家目录（SC-R1-23）。"""
    work = _prepare_fake_repo(tmp_path, subdir=f"repo-nohome-{Path(script_rel).name}")
    env, _la, call_log, _home = _prepare_env(
        tmp_path, tag=f"nohome-{Path(script_rel).name}"
    )
    env.pop("HOME", None)

    result = _run(work / script_rel, env)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, f"HOME 缺失仍继续安装:\n{combined}"
    assert "HOME 未设置" in result.stderr, result.stderr
    assert not call_log.read_text(encoding="utf-8").strip(), "拒绝前已经动了 launchctl"


def test_no_personal_home_literal_in_installer_scripts() -> None:
    """仓库 scripts/ 的**代码**里不得写死 owner 个人家目录（SC-R1-23 后半）。

    同一提交新增的 tests/test_launchd_plists.py 明令 plist 里不得出现 `/Users/`；
    渲染这些 plist 的脚本自己却带 `${HOME:-/Users/praise}` —— 门画在模板上、漏在渲染器上。
    """
    offenders: list[str] = []
    for script in sorted((REPO / "scripts").rglob("*.sh")):
        for lineno, line in enumerate(
            script.read_text(encoding="utf-8").splitlines(), 1
        ):
            code = line.split("#", 1)[0]
            if "/Users/" in code:
                offenders.append(f"{script.relative_to(REPO)}:{lineno}: {code.strip()}")
    allowed_prefixes = (
        # 安全守卫：判据本身就是"目标是否落在 owner 的 live 目录"，路径是判据不是硬编码
        "scripts/sync-skills-to-claude-backup.sh",
    )
    unexpected = [o for o in offenders if not o.startswith(allowed_prefixes)]
    assert not unexpected, (
        "安装/渲染脚本里出现个人家目录字面量（会被渲染进 plist 或改变解析结果）：\n  "
        + "\n  ".join(unexpected)
    )

# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-32 —— 逃逸口清单必须覆盖脚本里**全部**可替换生产命令的 env 接缝
# ═══════════════════════════════════════════════════════════════════════════

# 判定"这是一个逃逸口"的形态规则（而不是手工枚举）：清单要能自己发现新接缝。
#   AKB2_ALLOW_*        放行一道门
#   *_PIPELINE_BIN      替换整条 pipeline 命令
#   *_RERUN_SCRIPT      替换补跑入口
#   *_READER            替换判据读者
_OVERRIDE_ENV_RE = re.compile(
    r"\bAKB2_(?:ALLOW_[A-Z0-9_]+|[A-Z0-9_]*(?:PIPELINE_BIN|RERUN_SCRIPT|READER))\b"
)
# 测试专用接缝豁免：只被测试注入、不改变**生产执行的是什么**（换日志/锁/探针位置）。
# 豁免必须显式列出并写明理由 —— 豁免是决定，不是遗漏。
_NOT_ESCAPE_HATCHES: dict[str, str] = {}


def _discovered_override_envs() -> set[str]:
    """扫 scripts/ 里实际被读取的逃逸口形态 env（去注释后扫，注释里会引用它们做说明）。"""
    found: set[str] = set()
    scripts = sorted((REPO / "scripts").rglob("*.sh"))
    assert len(scripts) >= 10, f"scripts/*.sh 扫描结果异常少: {len(scripts)}"
    for script in scripts:
        for line in script.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            found.update(_OVERRIDE_ENV_RE.findall(code))
    return found - set(_NOT_ESCAPE_HATCHES)


def test_escape_hatch_registry_covers_all_override_envs() -> None:
    """清单 == 脚本实际接缝集合（exact，不是 subset），且两处登记表逐字一致。

    此前清单只有 AKB2_ALLOW_* 两项，而同一提交往生产脚本里新增了 5 个**更强**的接缝
    （能直接指定要执行什么）——守卫覆盖了弱的、漏了强的。
    这条断言的价值在于：以后再加第 8 个接缝而忘了登记，它会立刻打红。
    """
    discovered = _discovered_override_envs()
    registered = set(ESCAPE_HATCH_ENV_KEYS)

    missing = discovered - registered
    assert not missing, (
        f"脚本里存在未登记的逃逸口（能静默替换生产行为却不被守卫扫描）: {sorted(missing)}"
    )
    stale = registered - discovered
    assert not stale, (
        f"清单里有脚本已不再读取的键（清单必须反映现状，不是历史）: {sorted(stale)}"
    )

    # 两处登记表不得漂移（同一语义两份数据 = 加固清单第 10 类）
    parity_src = (REPO / "scripts" / "verify-launchd-parity.sh").read_text(encoding="utf-8")
    block = re.search(
        r"_ESCAPE_HATCH_ENV_KEYS\s*=\s*\(([^)]*)\)", parity_src, re.S
    )
    assert block, "verify-launchd-parity.sh 里找不到 _ESCAPE_HATCH_ENV_KEYS"
    parity_keys = set(re.findall(r'"([A-Z0-9_]+)"', block.group(1)))
    assert parity_keys == registered, (
        "verify-launchd-parity.sh 与测试侧的逃逸口清单漂移了：\n"
        f"  仅 parity 有: {sorted(parity_keys - registered)}\n"
        f"  仅测试有:   {sorted(registered - parity_keys)}"
    )


def test_registry_scan_actually_finds_the_strong_hatches() -> None:
    """非空过：扫描必须真的把 5 个"直接指定执行什么"的接缝找出来。"""
    discovered = _discovered_override_envs()
    for strong in (
        "AKB2_SOCIAL_BRIEFING_PIPELINE_BIN",
        "AKB2_GITHUB_BRIEFING_PIPELINE_BIN",
        "AKB2_GITHUB_RERUN_SCRIPT",
        "AKB2_BRIEFING_RERUN_SCRIPT",
        "AKB2_BACKSTOP_READER",
    ):
        assert strong in discovered, f"扫描没找到 {strong} —— 形态规则太窄"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
