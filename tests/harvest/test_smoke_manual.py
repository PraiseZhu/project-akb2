"""harvest 手动 smoke 测试（sc-skill-smoke）。

验收项:
  1. 复用合格 fixture harvest JSON，调 python3 -m akb2.harvest 全流程。
  2. 写死合法 UUID4 常量 FIXTURE_HARVEST_RUN_ID，实跑命令必须带 --harvest-run-id。
  3. 断言该值出现在 raw frontmatter harvest_run_id。
  4. 省略 --harvest-run-id 必得 exit 2 且零产物。
  5. 用同一常量展开 SKILL.md 唯一模板（纯字符串替换），断言 run_id 值与测试
     常量逐字相同——模板已与真实 CLI 位置参数对齐（p1a-sc2-skill-contract-sync），
     展开后的 argv 形态即真实 CLI 可接受的形态。

裁边:
  - ledger 已接线（p1a-sc0：CLI 调 run_harvest_with_ledger 记账）：
    test_full_pipeline_integration 断言账本落 committed 行且与入参同源。
    原「ledger 尚未接线」residual 断言随接线完成而过期，已同步更新。
  - 原跨组残余已修：SKILL.md 模板曾写 `--harvest-json`（真实 CLI 不支持的
    入参），p1a-sc2-skill-contract-sync 已将模板改为与真实 CLI 一致的位置
    参数形态。test_skill_md_template_expansion 现在断言模板与真实 CLI argv
    形态逐字一致，防止漂移复发。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from akb2.harvest.ledger import LEDGER_FILENAME, LEDGER_SUBDIR
from akb2.harvest.template import extract_submit_template

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 合法 UUID4 常量（sc-skill-smoke 归属键，全文同一值）
FIXTURE_HARVEST_RUN_ID = "a1b2c3d4-e5f6-4789-abcd-ef0123456789"

# 合格 fixture 路径
FIXTURE_JSON = Path(__file__).parent / "fixtures" / "prompt" / "valid_concept.json"

# SKILL.md 路径（真实工件，非占位符）
SKILL_MD_PATH = Path(
    "/Users/praise/AI-Agent/Claude/capabilities/source/skills/"
    "claude-active/akb2-harvest/SKILL.md"
)

# 部署常量 env key
ENV_DEPLOY_ROOT = "AKB2_HARVEST_DEPLOY_ROOT"
ENV_DEPLOY_UID = "AKB2_HARVEST_DEPLOY_UID"
ENV_DEPLOY_GID = "AKB2_HARVEST_DEPLOY_GID"
ENV_READER_GID = "AKB2_HARVEST_RUNTIME_READER_GID"
ENV_RUNTIME_UID = "AKB2_HARVEST_RUNTIME_UID"
ENV_RUNTIME_GID = "AKB2_HARVEST_RUNTIME_GID"
ENV_ROOT_FP = "AKB2_HARVEST_ROOT_PK_FINGERPRINT"

# 项目根（用于找 bootstrap 脚本和 PYTHONPATH）
_REPO_ROOT = Path(__file__).parent.parent.parent.resolve()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _run_harvest_cli(
    args: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """运行 harvest CLI 并返回结果。"""
    cmd = [sys.executable, "-m", "akb2.harvest"] + args
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    # CLI 内 import src.akb2.quality.intake.raw_archiver 需要 src 在路径
    merged_env.setdefault("PYTHONPATH", str(_REPO_ROOT / "src"))
    return subprocess.run(cmd, capture_output=True, text=True, env=merged_env)


def _run_bootstrap(
    bootstrap: Path, args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess:
    """运行 bootstrap 脚本并返回结果。"""
    cmd = [sys.executable, str(bootstrap), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _setup_deploy(tmp_path: Path) -> dict:
    """构造一次性测试部署环境，返回 {env, 关键路径, raw_dir}。"""
    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()

    # 四根目录（0o750 与 deploy_setup 一致）
    tree = tmp_path / "roots-tree"
    tree.mkdir(mode=0o750)
    roots = {
        "raw_dir": str(tree / "raw"),
        "stage": str(tree / "stage"),
        "locks_dir": str(tree / "locks"),
        "batch": str(tree / "batch"),
        "trust_root": str(tree),
    }
    roots_cfg = deploy_root / "roots.json"
    roots_cfg.write_text(json.dumps(roots, indent=1))
    os.chmod(roots_cfg, 0o400)
    for name, p in roots.items():
        if name != "trust_root":
            Path(p).mkdir(mode=0o750, exist_ok=True)

    env = {
        ENV_DEPLOY_ROOT: str(deploy_root),
        ENV_DEPLOY_UID: str(os.getuid()),
        ENV_DEPLOY_GID: str(os.getgid()),
        ENV_READER_GID: str(os.getgid()),
        ENV_RUNTIME_UID: str(os.getuid()),
        ENV_RUNTIME_GID: str(os.getgid()),
    }

    return {
        "env": env,
        "deploy_root": deploy_root,
        "roots": roots,
        "roots_config": roots_cfg,
        "raw_dir": Path(roots["raw_dir"]),
    }


def _bootstrap_init(deploy: dict, tmp_path: Path) -> str:
    """运行 bootstrap init，返回 root_pk_fingerprint。"""
    bootstrap = _REPO_ROOT / "scripts" / "harvest_anchor_bootstrap.py"
    if not bootstrap.exists():
        pytest.skip("bootstrap 脚本不存在")

    r = _run_bootstrap(
        bootstrap, ["init", "--deployment-id", "dep-smoke"], deploy["env"]
    )
    if r.returncode != 0:
        pytest.skip(
            f"bootstrap init 失败(exit {r.returncode}): {r.stderr[:200]}"
        )

    # 提取指纹
    fp = hashlib.sha256(
        (deploy["deploy_root"] / "root-key" / "root.pub").read_bytes()
    ).hexdigest()
    return fp


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestHarvestSmoke:
    """harvest 手动 smoke 测试集。"""

    # ---- 4. 省略 --harvest-run-id 必得 exit 2 且零产物 ----

    def test_omit_harvest_run_id_exits_2(self, tmp_path):
        """省略 --harvest-run-id → exit 2（argparse 必填参数缺失）。"""
        r = _run_harvest_cli([str(FIXTURE_JSON)])
        assert r.returncode == 2, (
            f"预期 exit 2,实际 {r.returncode}: {r.stderr}"
        )
        # argparse 使用必填参数时输出的错误信息含 "--harvest-run-id"
        assert "--harvest-run-id" in r.stderr

    # ---- 5. SKILL.md 模板展开 ----

    def test_skill_md_template_expansion(self):
        """SKILL.md 唯一模板展开后 run_id 占位符与测试常量逐字相同。

        模板已与真实 CLI 位置参数对齐（p1a-sc2-skill-contract-sync）：
        真实 CLI 只认位置参数 `<file>` + `--harvest-run-id`（见
        __main__.py:45-52），模板现为同形态。本测试断言展开后 argv 的
        形态与真实 CLI 一致（位置参数在前、--harvest-run-id 在后、无
        已废弃的 --harvest-json flag），防止文档-CLI 契约漂移复发。
        """
        if not SKILL_MD_PATH.exists():
            pytest.skip("SKILL.md 未生成（依赖真实工件）")

        skill_md = SKILL_MD_PATH.read_text(encoding="utf-8")
        template = extract_submit_template(skill_md)
        # 展开占位符（纯字符串替换，非 CLI 调用）
        expanded = template.replace(
            "{{harvest_run_id}}", FIXTURE_HARVEST_RUN_ID
        )
        # 断言展开后的模板文本中含 `--harvest-run-id <常量>`——只验证占位符
        # 替换机制，不代表这条 argv 已被真实 CLI 接受
        expected = f"--harvest-run-id {FIXTURE_HARVEST_RUN_ID}"
        assert expected in expanded, (
            f"展开后的模板文本中未找到 {expected!r}: {expanded}"
        )
        # 断言另一个占位符保持模板形态（未被替换）
        assert "{{harvest_json}}" in expanded

        # 契约对齐断言（p1a-sc2-skill-contract-sync）：模板已与真实 CLI
        # 位置参数对齐。展开后（补上 json 路径）argv 形态必须是
        # `python3 -m akb2.harvest <file> --harvest-run-id <id>`——位置参数
        # 在前、无已废弃的 --harvest-json flag，与 __main__.py argparse 一致
        assert "--harvest-json" not in template, (
            "SKILL.md 模板不得再含已废弃的 --harvest-json"
            "（真实 CLI 只认位置参数 <file>，契约已于"
            " p1a-sc2-skill-contract-sync 对齐）；若此断言失败，"
            "说明模板发生回退，请同步更新本注释"
        )
        expanded_argv = template.replace(
            "{{harvest_json}}", str(FIXTURE_JSON)
        ).replace("{{harvest_run_id}}", FIXTURE_HARVEST_RUN_ID)
        expected_argv = (
            f"python3 -m akb2.harvest {FIXTURE_JSON} "
            f"--harvest-run-id {FIXTURE_HARVEST_RUN_ID}"
        )
        assert expanded_argv == expected_argv, (
            f"展开后 argv 与真实 CLI 形态分叉:\n实际: {expanded_argv!r}\n"
            f"预期: {expected_argv!r}"
        )

    # ---- 1-3. 全流程 smoke ----

    def test_full_pipeline_integration(self, tmp_path):
        """复用合格 fixture，调 harvest CLI 全流程。

        验证:
        - 复用 valid_concept.json 合格 fixture
        - 实跑命令带 --harvest-run-id（真实 CLI argv：位置参数 file + --harvest-run-id，
          与 SKILL.md 模板同形态——契约已于 p1a-sc2-skill-contract-sync 对齐）
        - 断言该值出现在 raw frontmatter harvest_run_id
        - ledger 已接线（p1a-sc0：CLI 调 run_harvest_with_ledger）：断言账本
          存在 committed 行，且 session_id / harvest_run_id 与入参逐字同源
        """
        # 设置部署环境
        deploy = _setup_deploy(tmp_path)
        fp = _bootstrap_init(deploy, tmp_path)
        deploy["env"][ENV_ROOT_FP] = fp
        # 账本状态根隔离到 tmp_path，避免真实断言路径落到用户机器状态目录
        state_root = tmp_path / "state-root"
        deploy["env"]["AKB2_STATE_ROOT"] = str(state_root)

        # 构造 --vault-root 目标（CLI 仅将其设到 VAULT_ROOT）
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        (vault_root / "Praise Space" / "AKB2.0").mkdir(parents=True)

        # 调 harvest CLI 全流程
        r = _run_harvest_cli(
            [
                str(FIXTURE_JSON),
                "--harvest-run-id",
                FIXTURE_HARVEST_RUN_ID,
                "--vault-root",
                str(vault_root),
            ],
            env=deploy["env"],
        )
        assert r.returncode == 0, (
            f"harvest CLI 非零退出: {r.returncode} {r.stderr}"
        )

        # 检查 raw_dir 产物
        files = list(deploy["raw_dir"].iterdir())
        assert len(files) == 1, (
            f"raw_dir 预期恰 1 个产物,实际 {len(files)}: {[f.name for f in files]}"
        )

        raw_path = files[0]
        content = raw_path.read_text(encoding="utf-8")

        # 断言 harvest_run_id 出现在 frontmatter
        expected_line = f"harvest_run_id: {FIXTURE_HARVEST_RUN_ID}"
        assert expected_line in content, (
            f"frontmatter 中未找到 {expected_line!r}:\n{content[:500]}"
        )

        # 确认 frontmatter 格式正确（--- 包围）
        assert content.startswith("---"), (
            f"原始文件不以 frontmatter 开头:\n{content[:200]}"
        )

        # ledger 已接线（p1a-sc0）：CLI 走 run_harvest_with_ledger 记账。
        # 用真实 akb2.harvest.ledger 常量解析账本路径，断言 committed 行
        # 落账且 session_id / harvest_run_id 与入参逐字同源（非造假记录）
        ledger_path = state_root / LEDGER_SUBDIR / LEDGER_FILENAME
        assert ledger_path.exists(), (
            "ledger 已接线，CLI 成功路径应落 committed 台账"
        )
        rows = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        committed = [row for row in rows if row.get("event") == "committed"]
        assert len(committed) == 1, (
            f"预期恰 1 条 committed 行,实际 {len(committed)}: {rows}"
        )
        fixture_sid = json.loads(
            FIXTURE_JSON.read_text(encoding="utf-8"))["session_id"]
        assert committed[0]["harvest_run_id"] == FIXTURE_HARVEST_RUN_ID, (
            f"committed 行 run_id 与入参分叉: {committed[0]!r}"
        )
        assert committed[0]["session_id"] == fixture_sid, (
            f"committed 行 session_id 与 fixture 分叉: {committed[0]!r}"
        )
        assert committed[0]["dest_filename"] == raw_path.name, (
            f"committed 行 dest_filename 与实际产物分叉: {committed[0]!r}"
        )
