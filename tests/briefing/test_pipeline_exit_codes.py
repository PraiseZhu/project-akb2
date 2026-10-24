"""退出码诚实性（social 链路）+ 仓库卫生回归锁。

1. social pipeline「未交付即非零」—— 每个 BLOCKED / reject / audit-fail 分支的退出码
   都具名且非零，且与保留码位 {124, 125} 无交集。
2. `.stignore` —— Syncthing 此前在同步 `.git/` 与 `*.sqlite`，实测已产生 13,076 个
   冲突副本（含 5 份 `.git/index.sync-conflict-*`）。
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from akb2.briefing import pipeline as social_pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ═══ 1. social pipeline 退出码 ══════════════════════════════════════════════

def test_named_exit_codes_are_all_nonzero_except_delivered():
    """未交付即非零：EXIT_CODES 里只有 EXIT_DELIVERED 是 0。"""
    assert social_pipeline.EXIT_DELIVERED == 0
    not_delivered = social_pipeline.EXIT_CODES - {social_pipeline.EXIT_DELIVERED}
    assert not_delivered, "至少要有一个未交付码位"
    assert all(code != 0 for code in not_delivered), not_delivered


def test_exit_codes_do_not_collide_with_reserved_codes():
    """124（run_with_deadline 的 deadline 码）/ 125（backstop 主动放弃码）不得被占用。

    撞上任一个，backstop 与 launchd 的归因就会把「pipeline 自身失败」读成
    「超时」或「backstop 放弃」—— 退出码从诚实变成误导。
    """
    assert social_pipeline.RESERVED_EXIT_CODES == frozenset({124, 125})
    assert (
        social_pipeline.EXIT_CODES & social_pipeline.RESERVED_EXIT_CODES == frozenset()
    ), social_pipeline.EXIT_CODES


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("EXIT_DELIVERED", 0),
        ("EXIT_NOT_DELIVERED", 1),
        ("EXIT_AUDIT_FAIL", 2),
        ("EXIT_INTAKE_FIXTURE_BLOCKED", 3),
        ("EXIT_GATE_NOT_CONVERGED", 4),
        ("EXIT_DEDUP_STORE_UNAVAILABLE", 5),
    ],
)
def test_exit_code_values_are_frozen(name, expected):
    """数值锁定：scripts/briefing_backstop.sh 与运维记录按这些数字判读，改值会静默改变归因。"""
    assert getattr(social_pipeline, name) == expected


def test_no_bare_literal_exit_codes_in_pipeline_module():
    """模块内不得再出现裸字面量退出码（宪法 P4：退出码必须具名）。

    只扫**代码行**：注释里为了解释这条规则本身会写出反例形态，那不是调用点。
    """
    code_lines = [
        ln for ln in Path(social_pipeline.__file__).read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    ]
    bare = re.findall(r"sys\.exit\(\s*-?\d+\s*\)", "\n".join(code_lines))
    assert bare == [], f"仍有裸字面量退出码: {bare}"


def test_main_returns_named_delivered_code():
    """交付路径返回具名 EXIT_DELIVERED，而不是裸 0。"""
    source = inspect.getsource(social_pipeline.main)
    assert "return EXIT_DELIVERED" in source
    assert not re.search(r"^\s*return\s+0\s*$", source, flags=re.MULTILINE)


def test_intake_fixture_fallback_blocks_with_nonzero_exit(tmp_path, monkeypatch, capsys):
    """SC-1 行为级：intake 降级到 fixture（07-27 / 07-28 的真实形状）→ BLOCKED + 非零退出。

    这是 social 侧唯一在生产真正触发过的 BLOCKED 分支；断言绑的是可观测行为
    （SystemExit 的码 + stdout 的 BLOCKED 行 + 诊断 JSON 落盘），不是内部调用。
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    diag_dir = tmp_path / "diag"
    monkeypatch.setenv("AKB2_BRIEFING_DIAG_DIR", str(diag_dir))

    with patch(
        "akb2.briefing.pipeline.run_intake",
        return_value={"tweets": [{"id": "1925001000000000001"}], "_fallback_used": True},
    ):
        with pytest.raises(SystemExit) as excinfo:
            social_pipeline.main(["--date", "2026-08-03", "--vault-root", str(vault)])

    assert excinfo.value.code == social_pipeline.EXIT_INTAKE_FIXTURE_BLOCKED
    assert excinfo.value.code != 0, "fixture 降级是零交付，退出码不能是 0"
    out = capsys.readouterr().out
    assert "briefing: BLOCKED (intake_fixture_fallback)" in out, out
    assert (diag_dir / "briefing-social-blocked.json").exists(), "诊断 JSON 必须落盘供归因"
    # vault 零写入：Social 目录不该被建出来，更不该有简报
    assert list(vault.rglob("*Daily Briefing.md")) == []


def test_vault_root_missing_blocks_with_nonzero_exit(tmp_path, capsys):
    """--vault-root 不存在 → 在烧任何 LLM 额度前非零退出（fail-closed 前置闸）。"""
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit) as excinfo:
        social_pipeline.main(["--date", "2026-08-03", "--vault-root", str(missing)])
    assert excinfo.value.code == social_pipeline.EXIT_NOT_DELIVERED
    assert "does not exist" in capsys.readouterr().err


# ═══ 2. .stignore（L6-4 / SC-8）════════════════════════════════════════════

_STIGNORE = PROJECT_ROOT / ".stignore"


def _stignore_patterns() -> list[str]:
    lines = _STIGNORE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("//")]


def test_stignore_exists():
    assert _STIGNORE.is_file(), f"缺少 {_STIGNORE}"


@pytest.mark.parametrize(
    ("pattern", "why"),
    [
        (".git/**", "git 索引跨机同步 = 仓库损坏（实测 5 份 .git/index.sync-conflict-*）"),
        ("*.sqlite", "SQLite 并发 read-modify-write 静默丢写入（star_history 实测丢 690 行）"),
        ("*.sqlite-*", "WAL / journal 侧车文件与主库必须同进同出"),
        ("logs", "本机日志按机器各自累积，无跨机合并语义"),
        ("_tmp", "临时诊断产物"),
        (".venv", "体积大、平台相关、可重建"),
        ("__pycache__", "字节码缓存"),
        ("*.sync-conflict-*", "不让已产生的冲突副本继续跨机繁殖"),
    ],
)
def test_stignore_covers_required_pattern(pattern, why):
    assert pattern in _stignore_patterns(), f"缺模式 {pattern!r} —— {why}"


def test_stignore_has_git_prefixed_line():
    """至少一行以 `.git/` 开头（SC-8 的机读判据）。"""
    lines = _STIGNORE.read_text(encoding="utf-8").splitlines()
    assert sum(1 for ln in lines if ln.startswith(".git/")) >= 1


def test_stignore_uses_only_valid_syncthing_syntax():
    """每个非注释行都是单个模式，不含 Syncthing 不支持的 shell 语法。"""
    for pattern in _stignore_patterns():
        assert " " not in pattern, f"模式含空格（Syncthing 按整行匹配）: {pattern!r}"
        assert not pattern.startswith("#"), f"注释应用 `//` 而非 `#`: {pattern!r}"


def test_stignore_does_not_exclude_source_or_tests():
    """反向锚点：不能把源码/测试/配置模板排除掉，否则同步的意义就没了。"""
    patterns = _stignore_patterns()
    for must_sync in ("src", "tests", "scripts", "config", "docs", "*.py", "*.md"):
        assert must_sync not in patterns, f"不该排除 {must_sync!r}"


def test_stignore_documents_folder_root_caveat():
    """必须写清生效前提：`.stignore` 只在该目录是 Syncthing folder 根时被读取。

    否则 owner 会以为文件一放上就生效了 —— 而 folder 根在仓库外时它完全不起作用。
    """
    text = _STIGNORE.read_text(encoding="utf-8")
    assert "folder 根" in text
    assert "sync-conflict" in text, "应说明本文件不清理已存在的冲突副本"


def test_stignore_is_not_gitignored():
    """`.stignore` 必须真的入 git —— 否则第二台机器拿不到它。"""
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".stignore"],
        cwd=str(PROJECT_ROOT), capture_output=True,
    )
    assert result.returncode != 0, ".stignore 被 .gitignore 排除了，第二台机器同步不到"


def test_no_conflict_copies_deleted_by_this_change():
    """守栏：本次修复**不得**删除任何现存 `.sync-conflict-*`（它们是证据 + 待 owner 比对）。

    这里只断言 .stignore 里没有任何 `(?d)`（Syncthing 的"可删除"前缀）——
    加了它 Syncthing 会在清理父目录时把冲突副本一并删掉。
    """
    assert "(?d)" not in _STIGNORE.read_text(encoding="utf-8"), (
        "不要给冲突副本/状态库加 (?d)：那会让 Syncthing 有权删除它们"
    )


def test_conflict_inventory_is_reproducible():
    """冲突清单可复现（报告里的数字来自这条命令，owner 可自行复核）。"""
    result = subprocess.run(
        ["find", ".", "-name", "*sync-conflict*"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
    )
    # 只断言命令可跑通并给出可解析输出；数量随 owner 处置而变，不锁死数字
    assert result.returncode == 0
    paths = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # 分组统计能落地成结构（报告里用的就是这个分组）
    groups: dict[str, int] = {}
    for p in paths:
        top = p.removeprefix("./").split("/")[0]
        groups[top] = groups.get(top, 0) + 1
    assert isinstance(json.dumps(groups), str)
