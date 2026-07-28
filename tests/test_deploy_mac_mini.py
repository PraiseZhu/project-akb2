"""deploy-mac-mini.sh 回归测试。

覆盖：defuddle CLI 硬门与 launchctl teardown 等待逻辑。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _prepare_deploy_repo(tmp_path: Path, repo_root: Path) -> tuple[Path, Path, Path]:
    work = tmp_path / "deploy-repo"
    scripts = work / "scripts"
    launchd = work / "launchd"
    scripts.mkdir(parents=True)
    launchd.mkdir()
    shutil.copy2(repo_root / "scripts" / "deploy-mac-mini.sh", scripts / "deploy-mac-mini.sh")
    (launchd / "com.akb2.main-pipeline.plist").write_text("<plist></plist>\n", encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    _write_executable(
        fake_python,
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"--version\" ]]; then echo 'Python 3.12.0'; exit 0; fi\n"
        "exit 0\n",
    )
    _write_executable(scripts / "find-python311.sh", f"#!/usr/bin/env bash\necho {fake_python}\n")

    subprocess.run(["git", "init"], cwd=work, check=True, capture_output=True, text=True)
    # 显式把 fixture 仓的分支定成 main,不吃环境里的 init.defaultBranch:
    # macOS 的 Apple git 默认 main,而 Linux(CI runner)上游 git 仍默认 master
    # → deploy 脚本 B3「必须在 main 分支」会失败,FAIL_COUNT>0 让整个 B-dep 段被跳过,
    # 于是下面断言的 defuddle 字符串永不出现(CI-only 假红)。
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "AKB2 Test"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "scripts", "launchd"], cwd=work, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    return work, fake_bin, fake_python


def _prepare_home(tmp_path: Path, *, env_mode: int = 0o600) -> tuple[Path, Path]:
    home = tmp_path / "home"
    env_dir = home / ".config" / "akb2"
    env_dir.mkdir(parents=True)
    env_file = env_dir / "github-briefing.env"
    env_file.write_text("AI_GATEWAY_API_KEY=test\n", encoding="utf-8")
    env_file.chmod(env_mode)

    vault_root = tmp_path / "vault"
    (vault_root / "Praise Space" / "AKB2.0" / "raw").mkdir(parents=True)
    return home, vault_root


def _run_deploy_dry_run(
    work: Path,
    fake_bin: Path,
    home: Path,
    vault_root: Path,
    *,
    include_defuddle: bool,
) -> subprocess.CompletedProcess[str]:
    if include_defuddle:
        _write_executable(
            fake_bin / "defuddle",
            "#!/usr/bin/env bash\n[[ \"$1 $2\" == \"parse --help\" ]] && exit 0\nexit 1\n",
        )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    if not include_defuddle:
        # PATH 已收窄到 fake_bin + 系统目录,本机/CI 真装的 defuddle(npm 全局 bin)不该落进来。
        # 万一 npm prefix 就是 /usr,这里要给出明确原因,而不是让下游断言报一句莫名的失败。
        leaked = shutil.which("defuddle", path=env["PATH"])
        assert leaked is None, f"fixture 期望 defuddle 缺失,但 PATH 上仍能解析到:{leaked}"
    return subprocess.run(
        ["bash", "scripts/deploy-mac-mini.sh", "--dry-run", "--vault-root", str(vault_root)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )


def test_deploy_dry_run_fails_closed_without_defuddle(tmp_path, repo_root):
    """defuddle 缺失时 dry-run 必须 fail-closed，不能只 warn 后继续部署。"""
    work, fake_bin, _fake_python = _prepare_deploy_repo(tmp_path, repo_root)
    home, vault_root = _prepare_home(tmp_path)

    result = _run_deploy_dry_run(
        work, fake_bin, home, vault_root, include_defuddle=False
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "B-dep defuddle CLI 缺失" in combined


def test_deploy_dry_run_passes_with_defuddle(tmp_path, repo_root):
    """defuddle parse --help 可执行时 dry-run 前置检查通过。"""
    work, fake_bin, _fake_python = _prepare_deploy_repo(tmp_path, repo_root)
    home, vault_root = _prepare_home(tmp_path)

    result = _run_deploy_dry_run(
        work, fake_bin, home, vault_root, include_defuddle=True
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "B-dep defuddle CLI 可用" in combined
    assert "dry-run 完成" in combined


def test_deploy_dry_run_fails_closed_on_wide_env_permissions(tmp_path, repo_root):
    """env 文件宽权限(644,Linux checkout 默认)必须被 B7 判失败并中止。

    同时钉住权限探测的跨平台正确性:B7 只有真读到八进制权限位才能区分 600/644,
    stat 的 BSD/GNU 分派若坏掉(如误判成 '?'),另一项 600 用例会立刻变红。
    """
    work, fake_bin, _fake_python = _prepare_deploy_repo(tmp_path, repo_root)
    home, vault_root = _prepare_home(tmp_path, env_mode=0o644)

    result = _run_deploy_dry_run(work, fake_bin, home, vault_root, include_defuddle=True)

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "B7 env 文件权限 = 644" in combined
    assert "dry-run 完成" not in combined


def test_deploy_script_waits_for_launchctl_teardown(repo_root):
    """重部署路径必须在 bootout 后等待 teardown，再 bootstrap，避免 launchctl 竞态。"""
    script = (repo_root / "scripts" / "deploy-mac-mini.sh").read_text(encoding="utf-8")
    assert "wait_for_launchctl_teardown()" in script
    assert "bootstrap_launchagent()" in script
    assert "if ! wait_for_launchctl_teardown; then" in script
    assert "bootstrap_launchagent" in script
    assert "2>/dev/null" not in script.split("# bootout 旧 + 等待 teardown + bootstrap 新", 1)[1].split(
        "ok \"bootstrap 成功", 1
    )[0]
