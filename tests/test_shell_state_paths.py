"""残余风险 P2（状态仍落在同步范围内）：shell 侧状态根路径。

背景：`PROJECT_ROOT` 在 `~/AI-Agent`（Syncthing folder）内，状态（锁 / 账本 / 游标）放进
仓库就会跨机同步分叉。python 侧用 `common/state_paths.get_state_root()`（机器本地状态根），
shell 侧（`scripts/lib/state-root.sh`）必须同语义：`AKB2_STATE_ROOT` > `$HOME/Library/Application Support/akb2`。

主链路 runner wrapper（scripts/akb2-main-pipeline.sh）与 deploy-mac-mini.sh 已于 2026-10-24
删除，原先针对它们单实例锁的用例随之移除。

断言的可观测行为（真跑脚本，不做源码文本匹配）：
- `bootstrap_launchd.sh` 能把 `__AKB2_STATE_ROOT__` 占位符渲染成机器本地状态根，
  且拒绝指进同步范围的状态根
- shell 与 python 两侧的状态根解析顺序一致
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _wrap_as_plist(body: str) -> str:
    """把 `<string>…</string>` 片段包成**合法 plist**（含 Label + ProgramArguments）。

    为什么必须包（2026-08-04，R1 审查 B2 修复后）：
      ① 渲染合法性门以 plistlib 为唯一判据（此前"优先 plutil"让 macOS 上放行裸片段，
         而无 plutil 的 CI 反而更严 —— 门的强度随主机而变）。裸片段两个平台一致被拒。
      ② `--plist-src` 的身份现在从模板的 `Label` 键派生，缺 Label 直接 fail-closed。
    本 helper 要验的是 `__AKB2_STATE_ROOT__` 的渲染结果，不是这两道门，故给个合法外壳。
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key><string>com.akb2.state-root-render-probe</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        "    <string>/bin/bash</string>\n"
        f"    {body.strip()}\n"
        "  </array>\n"
        "  <key>StartInterval</key><integer>900</integer>\n"
        "</dict>\n"
        "</plist>\n"
    )


class TestBootstrapStateRootPlaceholder:
    def _render(
        self, tmp_path: Path, template: str, extra_env: dict[str, str] | None = None
    ) -> tuple[subprocess.CompletedProcess, Path]:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        src = tmp_path / "template.plist"
        src.write_text(_wrap_as_plist(template), encoding="utf-8")
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
            "<string>__AKB2_REPO_ROOT__/config/intake_sources.yaml</string>\n"
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
        assert str(REPO_ROOT / "config" / "intake_sources.yaml") in rendered, (
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


class TestStateRootLibrary:
    """共用库 scripts/lib/state-root.sh 的状态根解析顺序。"""

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
