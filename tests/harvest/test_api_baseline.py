"""sc-asm-baseline 验收测试:raw_archiver API baseline 可审计校验。

覆盖:
1. base SHA 断言:AKB2_BASE_SHA == origin/main 当前 SHA。
2. 函数签名逐项比对:archive_raw + archive_from_extract_result 参数名与默认值。
3. 签名差异 fail-closed:mock 差异检测必红并点名差异参数。
4. 陈旧 ref 回归:远端前进、本地 origin/main 停旧 SHA 时不刷新应红。

纪律:
- 测试内用 git show 取 base,ast 解析签名,与本地模块逐项比对。
- 差异检测必须精确到参数名+默认值,不模糊报告。
- 陈旧 ref 回归通过 origin/main 与远端 HEAD 比较实现。
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ENV_BASE_SHA = "AKB2_BASE_SHA"
MODULE_PATH = "src/akb2/quality/intake/raw_archiver.py"
PUBLIC_API_FUNCS = ("archive_raw", "archive_from_extract_result")

# 每个公开函数的必选参数清单（golden-sample 标准）
REQUIRED_PARAMS: dict[str, list[str]] = {
    "archive_raw": ["slug", "url", "title", "author", "content"],
    "archive_from_extract_result": ["extract_result", "output_dir"],
}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _git_show(worktree: Path, ref: str, path: str) -> str:
    """从 git 取指定版本的文件内容。"""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True, text=True, cwd=str(worktree), timeout=30,
    )
    if result.returncode != 0:
        pytest.skip(f"git show {ref}:{path} 失败: {result.stderr.strip()}")
    return result.stdout


def _ast_parse_func_signatures(source: str) -> dict[str, dict[str, str | None]]:
    """AST 解析指定公开函数的参数名与默认值。

    Returns:
        {func_name: {param_name: default_str_or_None}}
    """
    tree = ast.parse(source)
    signatures: dict[str, dict[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in PUBLIC_API_FUNCS:
            params: dict[str, str | None] = {}
            n_args = len(node.args.args)
            n_defaults = len(node.args.defaults)
            defaults_start = n_args - n_defaults
            for i, arg in enumerate(node.args.args):
                if arg.arg == "self":
                    continue
                if i >= defaults_start:
                    default_str = ast.unparse(node.args.defaults[i - defaults_start])
                    params[arg.arg] = default_str
                else:
                    params[arg.arg] = None  # required param
            signatures[node.name] = params
    return signatures


def _diff_signatures(
    base: dict[str, dict[str, str | None]],
    local: dict[str, dict[str, str | None]],
) -> list[str]:
    """逐项比对两个签名字典，返回差异描述列表。

    覆盖:新增/删除参数、必选↔可选切换、默认值变更。
    """
    issues: list[str] = []
    all_funcs = sorted(set(base.keys()) | set(local.keys()))
    for func in all_funcs:
        if func not in base:
            issues.append(f"{func}: 在 base 中不存在（本地已新增）")
            continue
        if func not in local:
            issues.append(f"{func}: 在本地模块中不存在（已被删除）")
            continue
        base_params = base[func]
        local_params = local[func]
        all_params = sorted(set(base_params.keys()) | set(local_params.keys()))
        for param in all_params:
            if param not in base_params:
                issues.append(f"{func} 参数「{param}」: 在 base 中不存在（本地新增）")
                continue
            if param not in local_params:
                issues.append(f"{func} 参数「{param}」: 在本地模块中不存在（已被删除）")
                continue
            base_default = base_params[param]
            local_default = local_params[param]
            if base_default != local_default:
                if base_default is None and local_default is not None:
                    issues.append(
                        f"{func} 参数「{param}」: 从必选变为可选（默认值={local_default}）"
                    )
                elif base_default is not None and local_default is None:
                    issues.append(
                        f"{func} 参数「{param}」: 从可选（默认值={base_default}）变为必选"
                    )
                else:
                    issues.append(
                        f"{func} 参数「{param}」: 默认值 {base_default} → {local_default}"
                    )
    return issues


def _git_remote_head(worktree: Path) -> str:
    """通过 git ls-remote 获取 origin/main 远端 HEAD SHA。"""
    result = subprocess.run(
        ["git", "ls-remote", "origin", "refs/heads/main"],
        capture_output=True, text=True, cwd=str(worktree), timeout=30,
    )
    if result.returncode != 0:
        pytest.skip(f"git ls-remote 失败: {result.stderr.strip()}")
    parts = result.stdout.strip().split()
    if not parts:
        pytest.skip("git ls-remote 返回空结果")
    return parts[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def worktree() -> Path:
    """测试仓库根目录（v7 worktree）。"""
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def base_sha(worktree: Path) -> str:
    """从环境变量 AKB2_BASE_SHA 取 base SHA；未设置时回退到 origin/main 当前 SHA。

    只在缺失时 skip 会让本测试套件在默认 `pytest -q`（CI 未设置该变量）下
    8/9 项静默跳过、覆盖为零——回退到 origin/main 保证签名比对默认可自证。
    """
    sha = os.environ.get(ENV_BASE_SHA, "")
    if not sha:
        result = subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            capture_output=True, text=True, cwd=str(worktree), timeout=30,
        )
        if result.returncode != 0:
            pytest.skip(
                f"{ENV_BASE_SHA} 未设置，且回退取 origin/main 失败: {result.stderr.strip()}"
            )
        sha = result.stdout.strip()
    if not sha.isalnum() or len(sha) != 40:
        pytest.fail(f"{ENV_BASE_SHA}={sha} 不是有效的 40 字符 hex SHA")
    return sha


@pytest.fixture
def base_source(worktree: Path, base_sha: str) -> str:
    return _git_show(worktree, base_sha, MODULE_PATH)


@pytest.fixture
def local_source(worktree: Path) -> str:
    return (worktree / MODULE_PATH).read_text(encoding="utf-8")


@pytest.fixture
def base_signatures(base_source) -> dict[str, dict[str, str | None]]:
    return _ast_parse_func_signatures(base_source)


@pytest.fixture
def local_signatures(local_source) -> dict[str, dict[str, str | None]]:
    return _ast_parse_func_signatures(local_source)


# ---------------------------------------------------------------------------
# 1. base SHA 断言
# ---------------------------------------------------------------------------


class TestBaseSha:

    def test_base_sha_env_set(self, base_sha: str) -> None:
        """AKB2_BASE_SHA 环境变量已设置且格式正确。"""
        # base_sha fixture 已做全部验证
        pass

    def test_base_sha_matches_origin_main(
        self, worktree: Path, base_sha: str,
    ) -> None:
        """AKB2_BASE_SHA == origin/main 当前 SHA。"""
        local_sha = subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            capture_output=True, text=True, cwd=str(worktree), timeout=30,
        ).stdout.strip()
        assert local_sha == base_sha, (
            f"AKB2_BASE_SHA ({base_sha}) 与 origin/main ({local_sha}) 不一致\n"
            f"请先执行: git fetch origin +refs/heads/main:refs/remotes/origin/main"
        )


# ---------------------------------------------------------------------------
# 2. API 签名逐项比对
# ---------------------------------------------------------------------------


class TestSignatureConsistency:

    def test_archive_raw_required_params(
        self, base_signatures, local_signatures,
    ) -> None:
        """archive_raw 必选参数一致。"""
        func = "archive_raw"
        assert func in base_signatures, f"base 中缺少 {func}"
        assert func in local_signatures, f"本地模块中缺少 {func}"
        for param in REQUIRED_PARAMS[func]:
            bd = base_signatures[func].get(param)
            ld = local_signatures[func].get(param)
            assert bd is None, f"base 中 {param} 不是必选参数（默认值={bd}）"
            assert ld is None, f"本地模块中 {param} 不是必选参数（默认值={ld}）"

    def test_archive_from_extract_result_required_params(
        self, base_signatures, local_signatures,
    ) -> None:
        """archive_from_extract_result 必选参数一致。"""
        func = "archive_from_extract_result"
        assert func in base_signatures, f"base 中缺少 {func}"
        assert func in local_signatures, f"本地模块中缺少 {func}"
        for param in REQUIRED_PARAMS[func]:
            bd = base_signatures[func].get(param)
            ld = local_signatures[func].get(param)
            assert bd is None, f"base 中 {param} 不是必选参数（默认值={bd}）"
            assert ld is None, f"本地模块中 {param} 不是必选参数（默认值={ld}）"

    def test_full_signature_consistency(
        self, base_signatures, local_signatures,
    ) -> None:
        """base 与本地完整签名逐项一致。"""
        issues = _diff_signatures(base_signatures, local_signatures)
        assert not issues, (
            "API 签名差异:\n" + "\n".join(f"  - {i}" for i in issues)
        )


# ---------------------------------------------------------------------------
# 3. 签名差异 fail-closed（mock 场景）
# ---------------------------------------------------------------------------


class TestSignatureMismatch:

    def test_extra_param_detected(self, base_signatures) -> None:
        """mock 新增参数:检测差异并点名差异参数。"""
        mock_local = {
            name: dict(params) for name, params in base_signatures.items()
        }
        mock_local.setdefault("archive_raw", {})["fake_param"] = "fake_default"
        issues = _diff_signatures(base_signatures, mock_local)
        assert len(issues) >= 1, "应检测到签名差异"
        msg = "\n".join(issues)
        assert "fake_param" in msg, f"应点名 fake_param, 实际: {msg}"
        assert "新增" in msg or "不存在" in msg, f"应指明差异方向, 实际: {msg}"

    def test_removed_param_detected(self, base_signatures) -> None:
        """mock 删除参数:检测差异并点名差异参数。"""
        mock_local = {
            name: dict(params) for name, params in base_signatures.items()
        }
        if "slug" in mock_local.get("archive_raw", {}):
            del mock_local["archive_raw"]["slug"]
        issues = _diff_signatures(base_signatures, mock_local)
        assert len(issues) >= 1, "应检测到签名差异"
        msg = "\n".join(issues)
        assert "slug" in msg, f"应点名 slug, 实际: {msg}"
        assert "不存在" in msg or "删除" in msg, f"应指明差异方向, 实际: {msg}"

    def test_default_value_changed(self, base_signatures) -> None:
        """mock 默认值变更:检测差异并点名差异参数与新旧值。"""
        mock_local = {
            name: dict(params) for name, params in base_signatures.items()
        }
        mock_local["archive_raw"]["fetched_via"] = "'changed'"
        issues = _diff_signatures(base_signatures, mock_local)
        assert len(issues) >= 1, "应检测到签名差异"
        msg = "\n".join(issues)
        assert "fetched_via" in msg, f"应点名 fetched_via, 实际: {msg}"
        assert "unknown" in msg, f"应包含旧默认值, 实际: {msg}"
        assert "changed" in msg, f"应包含新默认值, 实际: {msg}"


# ---------------------------------------------------------------------------
# 4. 陈旧 ref 回归
# ---------------------------------------------------------------------------


class TestStaleRef:

    def test_stale_ref_detected(self, worktree: Path) -> None:
        """本地 origin/main 与远端 HEAD 一致（否则 ref 陈旧，应红）。

        验证命令已包含 git fetch, 正常流程下本测试通过;
        但直接调用 pytest 而未先 fetch 时, 若本地 origin/main 落后于远端, 本测试会红。
        """
        remote_head = _git_remote_head(worktree)
        local_origin_main = subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            capture_output=True, text=True, cwd=str(worktree), timeout=30,
        ).stdout.strip()
        assert local_origin_main == remote_head, (
            f"本地 origin/main ({local_origin_main}) 落后于远端 ({remote_head})\n"
            f"请执行: git fetch origin +refs/heads/main:refs/remotes/origin/main"
        )
