"""残余风险 P2（状态仍落在同步范围内）：shell 侧锁 / 状态根路径。

背景：`PROJECT_ROOT` 在 `~/AI-Agent`（Syncthing folder，`~/AI-Agent/.stfolder` 实证）
内，所以 `scripts/akb2-main-pipeline.sh` 把 daemon 单实例锁默认放
`$PROJECT_ROOT/staging/locks/runner.lock` 时，锁本身就在跨机同步范围里——两台机器
会互看/互删对方的锁 PID 文件，`kill -0` 判活对另一台的 PID 毫无意义。
python 侧已经把锁挪到 `common/state_paths.get_lock_dir()`（机器本地状态根），
shell 侧必须同语义：`AKB2_STATE_ROOT` > `$HOME/Library/Application Support/akb2`。

断言的可观测行为（真跑脚本，不做源码文本匹配）：
- 默认锁文件落 `$HOME/Library/Application Support/akb2/locks/runner.lock`，
  且不在仓库内
- `AKB2_STATE_ROOT` 覆盖被采纳
- 状态根被指进 `$HOME/AI-Agent`（同步范围）→ fail-closed 非 0 退出
- `bootstrap_launchd.sh` 能把 `__AKB2_STATE_ROOT__` 占位符渲染成机器本地状态根
  （intake plist 的 `--state-file` 将来要引用它，见 docs/mac-mini-deployment-notes.md）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 假 python：只回答版本探测，其余一律成功退出。
# 脚本末尾是 `exec "$PYTHON_BIN" -m ...`，exec 会替换 shell → EXIT trap 不跑 →
# 锁文件留在盘上可断言（真实 daemon 存活期间锁也是这个状态）。
_FAKE_PYTHON = """#!/bin/bash
if [ "$1" = "-c" ]; then
    echo "3.12"
    exit 0
fi
exit 0
"""


@pytest.fixture()
def fake_python(tmp_path: Path) -> Path:
    shim = tmp_path / "fake-python3"
    shim.write_text(_FAKE_PYTHON, encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _run_pipeline(
    tmp_path: Path, fake_python: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    log_dir = tmp_path / "logs"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "AKB2_PYTHON_BIN": str(fake_python),
            "AKB2_LOG_DIR": str(log_dir),
            "AKB2_RUNNER_LOG": str(log_dir / "runner.log"),
        }
    )
    env.pop("AKB2_STATE_ROOT", None)
    env.pop("AKB2_LOCK_FILE", None)
    env.pop("AKB2_VAULT_ROOT", None)
    # 迁移期旧锁默认指向仓库内那把真锁（本机 daemon 正持有它）。路径类断言里把它
    # 指到一个不存在的位置，否则脚本会正确地"让位"给生产 daemon 而不建新锁。
    env.setdefault("AKB2_LEGACY_LOCK_FILE", str(tmp_path / "absent-legacy.lock"))
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "akb2-main-pipeline.sh")],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestPipelineLockPath:
    def test_default_lock_lands_in_machine_local_state_root(
        self, tmp_path: Path, fake_python: Path
    ) -> None:
        """默认锁文件落机器本地状态根，不落仓库（仓库在 Syncthing folder 内）。"""
        result = _run_pipeline(tmp_path, fake_python)
        assert result.returncode == 0, result.stderr

        expected = (
            tmp_path
            / "home"
            / "Library"
            / "Application Support"
            / "akb2"
            / "locks"
            / "runner.lock"
        )
        assert expected.is_file(), (
            f"锁未落机器本地状态根: {expected} 不存在\nstderr={result.stderr}"
        )
        assert expected.read_text(encoding="utf-8").strip().isdigit(), "锁内容不是 PID"

    def test_run_does_not_write_lock_into_repo(
        self, tmp_path: Path, fake_python: Path
    ) -> None:
        """跑一遍不得往仓库(同步范围)写/更新锁文件。

        仓库里可能残留迁移前那把锁（本机 daemon 2026-07-25 起就持有它），所以断言
        的是"本次运行没碰它"：不存在则运行后仍不存在，存在则 mtime 不变。
        """
        repo_lock = REPO_ROOT / "staging" / "locks" / "runner.lock"
        before = repo_lock.stat().st_mtime_ns if repo_lock.exists() else None

        result = _run_pipeline(tmp_path, fake_python)
        assert result.returncode == 0, result.stderr

        after = repo_lock.stat().st_mtime_ns if repo_lock.exists() else None
        assert after == before, (
            f"运行往仓库锁文件写了东西（同步范围内）: {repo_lock} {before} → {after}"
        )
        # 并且脚本自报的锁路径不得落在仓库内（仓库 = Syncthing folder）
        log = (tmp_path / "logs" / "runner.log").read_text(encoding="utf-8")
        used = next(
            ln.split("runner lock: ", 1)[1].split(" (state_root=")[0]
            for ln in log.splitlines()
            if "runner lock: " in ln
        )
        assert not used.startswith(str(REPO_ROOT)), f"锁路径在仓库内: {used}"

    def test_live_legacy_lock_makes_wrapper_yield(
        self, tmp_path: Path, fake_python: Path
    ) -> None:
        """迁移期兜底：旧位置锁里的 PID 还活着 → 让位，不起第二个 daemon。"""
        legacy = tmp_path / "legacy" / "runner.lock"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(str(os.getpid()), encoding="utf-8")  # pytest 自己，必活

        result = _run_pipeline(
            tmp_path, fake_python, {"AKB2_LEGACY_LOCK_FILE": str(legacy)}
        )
        assert result.returncode == 0
        assert "旧锁" in result.stderr, f"让位时没说明原因: {result.stderr!r}"
        new_lock = (
            tmp_path / "home" / "Library" / "Application Support" / "akb2"
            / "locks" / "runner.lock"
        )
        assert not new_lock.exists(), (
            "旧锁持有者还活着却仍起了新 daemon → 单机双跑"
        )

    def test_state_root_env_override_is_honored(
        self, tmp_path: Path, fake_python: Path
    ) -> None:
        """AKB2_STATE_ROOT 覆盖生效（与 python 侧 state_paths 同语义）。"""
        state_root = tmp_path / "custom-state"
        result = _run_pipeline(
            tmp_path, fake_python, {"AKB2_STATE_ROOT": str(state_root)}
        )
        assert result.returncode == 0, result.stderr
        assert (state_root / "locks" / "runner.lock").is_file(), (
            f"AKB2_STATE_ROOT 未被采纳\nstderr={result.stderr}"
        )

    def test_state_root_inside_synced_folder_is_rejected(
        self, tmp_path: Path, fake_python: Path
    ) -> None:
        """状态根指进 $HOME/AI-Agent（Syncthing folder）→ fail-closed 非 0 退出。"""
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        result = _run_pipeline(
            tmp_path,
            fake_python,
            {"AKB2_STATE_ROOT": str(home / "AI-Agent" / "state")},
        )
        assert result.returncode != 0, (
            "状态根落在同步目录仍被接受 → 会重演跨机覆盖丢状态"
        )
        assert "AKB2_STATE_ROOT" in result.stderr, (
            f"拒绝时未说明原因（失败不可见）: {result.stderr!r}"
        )
        assert not (home / "AI-Agent" / "state" / "locks").exists(), (
            "已经在同步目录里建了锁目录"
        )

    def test_lock_path_logged_for_operator(
        self, tmp_path: Path, fake_python: Path
    ) -> None:
        """启动日志里能看到实际锁路径（否则运维无法核对锁到底落在哪）。"""
        result = _run_pipeline(tmp_path, fake_python)
        assert result.returncode == 0, result.stderr
        log = (tmp_path / "logs" / "runner.log").read_text(encoding="utf-8")
        assert "runner lock:" in log, f"启动日志没有锁路径: {log!r}"
        assert "Application Support/akb2/locks/runner.lock" in log


class TestBootstrapStateRootPlaceholder:
    def _render(
        self, tmp_path: Path, template: str, extra_env: dict[str, str] | None = None
    ) -> tuple[subprocess.CompletedProcess, Path]:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        src = tmp_path / "template.plist"
        src.write_text(template, encoding="utf-8")
        out = tmp_path / "rendered.plist"
        env = os.environ.copy()
        env["HOME"] = str(home)
        env.pop("AKB2_STATE_ROOT", None)
        # 2026-08-03(Lane 4 门生效后补):bootstrap_launchd.sh 现有两道 fail-closed 门排在渲染之前
        #   - worktree 守卫:本机 checkout 就是 worktree,不放行会先被它拦下
        #   - VAULT_ROOT 三门:门 3 拒绝落在 /var/folders 下的 VAULT_ROOT,而本仓标准测试命令
        #     `VAULT_ROOT="$(mktemp -d)" pytest` 继承进来的正是这种值(注意:主仓同样会红,
        #     不只是 worktree),tmp_path 在 macOS 上也落在 /var/folders
        # 本 helper 要验的是 __AKB2_STATE_ROOT__ 的渲染结果,不是那两道门,故显式喂合规值。
        # 放在 env.update(extra_env) **之前**,让调用方仍可按需覆写。
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        env["VAULT_ROOT"] = str(vault)
        env["AKB2_ALLOW_TMP_VAULT_ROOT"] = "1"
        env["AKB2_ALLOW_WORKTREE_INSTALL"] = "1"
        env.update(extra_env or {})
        proc = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "scripts" / "bootstrap_launchd.sh"),
                "--render-only",
                str(out),
                "--plist-src",
                str(src),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc, out

    def test_state_root_placeholder_is_rendered(self, tmp_path: Path) -> None:
        """__AKB2_STATE_ROOT__ 被替换成机器本地状态根（intake 游标要靠它出仓库）。"""
        template = (
            "<string>__AKB2_STATE_ROOT__/intake/intake-state.jsonl</string>\n"
            "<string>__AKB2_REPO_ROOT__/config/main_sources.yaml</string>\n"
        )
        # 两道 Lane 4 门（worktree 守卫 / VAULT_ROOT 三门）的合规值由 `_render` 统一喂，
        # 不在这里重复一遍 —— 同一件事两处实现只会漂移。
        proc, out = self._render(tmp_path, template)
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
        rendered = out.read_text(encoding="utf-8")
        assert "__AKB2_STATE_ROOT__" not in rendered, (
            f"占位符未被替换，plist 会指向字面量路径: {rendered!r}"
        )
        expected = str(
            tmp_path / "home" / "Library" / "Application Support" / "akb2"
        )
        assert f"{expected}/intake/intake-state.jsonl" in rendered, rendered
        assert str(REPO_ROOT / "config" / "main_sources.yaml") in rendered, (
            "既有 __AKB2_REPO_ROOT__ 替换被改坏"
        )

    def test_render_rejects_synced_state_root(self, tmp_path: Path) -> None:
        """状态根被指进同步范围 → 渲染阶段就拒绝，不产出错的 plist。"""
        home = tmp_path / "home"
        proc, out = self._render(
            tmp_path,
            "<string>__AKB2_STATE_ROOT__/x.jsonl</string>\n",
            {"AKB2_STATE_ROOT": str(home / "AI-Agent" / "state")},
        )
        assert proc.returncode != 0, "同步范围内的状态根被接受"
        assert not out.exists(), "拒绝后仍写出了 plist"


class TestDeployScriptLockDir:
    """deploy-mac-mini.sh 的 B9 建目录步骤不得再在仓库内建锁目录。

    该脚本的 dry-run 也要求 git 工作区干净 + 真 vault 挂载，无法在测试里跑到 B9，
    因此这里断言的是脚本源码里的建目录目标（弱于运行时断言，已在报告登记）。
    """

    def test_b9_creates_locks_under_state_root_not_repo(self) -> None:
        text = (REPO_ROOT / "scripts" / "deploy-mac-mini.sh").read_text(
            encoding="utf-8"
        )
        assert '"$REPO_ROOT/staging/locks"' not in text, (
            "仍在仓库(Syncthing folder)内建锁目录"
        )
        assert '"$STATE_ROOT/locks"' in text, "未改成在机器本地状态根建锁目录"

    def test_state_root_resolution_order_is_behavioural(self, tmp_path) -> None:
        """解析顺序断言改为**跑共用库**，不再匹配脚本源码字面量。

        原断言是 `'STATE_ROOT="${AKB2_STATE_ROOT:-' in text` —— 脚本一改写法
        （本轮正是把三份复制收口成 `scripts/lib/state-root.sh`）它就失效，
        属 /code-review 标为 MEDIUM 的源码文本断言。现在断言可观测行为。
        """
        lib = REPO_ROOT / "scripts" / "lib" / "state-root.sh"
        home = tmp_path / "home"
        home.mkdir()

        def _resolve(env_extra: dict[str, str]) -> str:
            proc = subprocess.run(
                ["bash", "-c", f'. "{lib}"\nakb2_resolve_state_root\nprintf %s "$STATE_ROOT"'],
                capture_output=True, text=True, timeout=30,
                env={"PATH": os.environ["PATH"], "HOME": str(home), **env_extra},
            )
            assert proc.returncode == 0, proc.stderr
            return proc.stdout

        # AKB2_STATE_ROOT 优先
        assert _resolve({"AKB2_STATE_ROOT": "/tmp/explicit-root"}) == "/tmp/explicit-root"
        # 未设时回落 HOME 下的 macOS 标准位置
        assert _resolve({}) == f"{home}/Library/Application Support/akb2"


class TestShellPythonParity:
    """shell 侧同步范围判据必须与 python 侧一致（2026-07-28 /simplify 复核）。

    分叉的成因就是没有这层守卫：三个脚本各复制一份 case 分支，三份都漏了 python
    侧 `state_paths._VAULT_ENV_NAMES`（AKB2_ROOT / AKB2_VAULT_ROOT / VAULT_ROOT）
    指定的 vault 前缀，而三处注释都写着「与 python 侧一致」。
    判据是安全判定：漏判一次 = 状态又落回 Syncthing（`data/star_history.sqlite`
    已实证被同步覆盖丢过 690 行）。
    """

    LIB = "scripts/lib/state-root.sh"

    def _shell_says_synced(self, repo_root, target: str, env: dict[str, str]) -> bool:
        """跑共用库判定 target 是否在同步范围内（返回码 0 = 在范围内）。"""
        script = (
            f'. "{repo_root}/{self.LIB}"\n'
            f'STATE_ROOT="{target}"\n'
            "akb2_state_root_is_synced && echo SYNCED || echo SAFE\n"
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30,
            env={"PATH": os.environ["PATH"], **env},
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip() == "SYNCED"

    def _python_says_synced(self, target: str, env: dict[str, str]) -> bool:
        script = (
            "import sys; sys.path.insert(0, 'src')\n"
            "from akb2.common.state_paths import is_synced_path\n"
            f"print('SYNCED' if is_synced_path({target!r}) else 'SAFE')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT),
            env={"PATH": os.environ["PATH"], **env},
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip() == "SYNCED"

    @pytest.mark.parametrize(
        ("target_tpl", "env_extra"),
        [
            # 已知同步前缀（三份旧拷贝都覆盖到的）
            ("{home}/AI-Agent/x/state", {}),
            ("{home}/Library/Mobile Documents/state", {}),
            ("/Volumes/AKB2/state", {}),
            # 安全位置
            ("{home}/Library/Application Support/akb2", {}),
            ("/tmp/akb2-state", {}),
            # **三份旧拷贝漏掉的**：vault 由环境变量指定
            ("/mnt/vault-a/state", {"VAULT_ROOT": "/mnt/vault-a"}),
            ("/mnt/vault-b/state", {"AKB2_ROOT": "/mnt/vault-b"}),
            ("/mnt/vault-c/state", {"AKB2_VAULT_ROOT": "/mnt/vault-c"}),
        ],
    )
    def test_shell_matches_python(self, tmp_path, target_tpl, env_extra):
        home = str(tmp_path / "home")
        (tmp_path / "home").mkdir(exist_ok=True)
        target = target_tpl.format(home=home)
        env = {"HOME": home, **env_extra}
        shell = self._shell_says_synced(REPO_ROOT, target, env)
        py = self._python_says_synced(target, env)
        assert shell == py, (
            f"shell 与 python 对 {target}（env={env_extra}）判定不一致："
            f"shell={'SYNCED' if shell else 'SAFE'} python={'SYNCED' if py else 'SAFE'}"
        )
