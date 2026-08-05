"""SC-R6-05 — 变异证据口径闸（scripts/check_mutation_log.py）的坏样本矩阵。

规矩此前只活在 mutation-log-r4.md 的正文里（无 owner，R5 轮 lead 自己违反了它）。
本模块用**构造的坏样本**证明闸对四类违规各自翻红、对合规样本放行，且每条判据
单独有牙（删掉任一判据都有一个只违反该判据的样本会漏检 → 对应断言翻红）。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER = PROJECT_ROOT / "scripts" / "check_mutation_log.py"

SC_IDS = "SC-T-01,SC-T-02"
EQUIVALENCE_CLAIMS = ("exact", "exactly", "精确", "恰好", "恰为", "完全一致")


def _entry(sc_id: str, *, scope: str = "两个 backstop 测试文件全量", predicted: str = "2 条矩阵 nodeid") -> str:
    return (
        f"## {sc_id}\n\n"
        f"- 变异: 删除关键行\n"
        f"- 选择范围: {scope}\n"
        f"- 预测红集: {predicted}\n"
        f"- 实测红集: 与预测一致（同选择范围）\n"
        f"- 判定: 匹配\n\n"
    )


COMPLIANT = "# 变异对照\n\n" + _entry("SC-T-01") + _entry("SC-T-02")


def _write_manifest(
    path: Path, sc_ids: tuple[str, ...] = ("SC-T-01", "SC-T-02")
) -> Path:
    path.write_text(
        json.dumps({"schema_version": 1, "scs": [{"id": sc_id} for sc_id in sc_ids]}),
        encoding="utf-8",
    )
    return path


def _run(
    log_path: Path,
    sc_ids: str | None = SC_IDS,
    manifest_path: Path | None = None,
) -> subprocess.CompletedProcess:
    if manifest_path is None:
        manifest_path = _write_manifest(log_path.parent / "sc-manifest.json")
    command = [
        sys.executable,
        str(CHECKER),
        "--log",
        str(log_path),
        "--sc-manifest",
        str(manifest_path),
    ]
    if sc_ids is not None:
        command.extend(("--sc-ids", sc_ids))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_rejects_missing_scope_and_bare_exact(tmp_path: Path) -> None:
    """四类坏样本各自非零退出并指名违规条目；合规样本返回 0。"""
    good = tmp_path / "good.md"
    good.write_text(COMPLIANT, encoding="utf-8")
    result = _run(good)
    assert result.returncode == 0, result.stderr

    # 坏样本 1：缺一条 SC 的条目（判据②）
    missing_entry = tmp_path / "missing-entry.md"
    missing_entry.write_text("# 变异对照\n\n" + _entry("SC-T-01"), encoding="utf-8")
    result = _run(missing_entry)
    assert result.returncode == 1
    assert "SC-T-02" in result.stderr and "判据②" in result.stderr

    # 坏样本 2：预测红集列为空（判据③）
    empty_predicted = tmp_path / "empty-predicted.md"
    empty_predicted.write_text(
        "# 变异对照\n\n"
        + _entry("SC-T-01")
        + _entry("SC-T-02").replace("- 预测红集: 2 条矩阵 nodeid\n", "- 预测红集:\n"),
        encoding="utf-8",
    )
    result = _run(empty_predicted)
    assert result.returncode == 1
    assert "SC-T-02" in result.stderr and "预测红集" in result.stderr

    # 坏样本 3：无范围的裸 exact（判据④）—— R4 被审出的"精确 N 条"失真形态
    bare_exact = tmp_path / "bare-exact.md"
    bare_exact.write_text(
        COMPLIANT + "\n附注：红集与预测 exact 一致。\n", encoding="utf-8"
    )
    result = _run(bare_exact)
    assert result.returncode == 1
    assert "裸 exact" in result.stderr

    # 坏样本 4：日志文件不存在（判据①）
    result = _run(tmp_path / "does-not-exist.md")
    assert result.returncode == 2
    assert "不存在" in result.stderr


def test_gate_itself_has_teeth(tmp_path: Path) -> None:
    """每条判据单独有牙：只违反单一判据的最小样本必被拒，其合规孪生放行。

    尤其判据④：样本除一行裸 exact 外全合规 —— 若判据④被删，该样本会拿到 0，
    本断言当场翻红（这正是"删掉判据④后坏样本检出必须失败"的机器化表达）。
    """
    # ④ 单独有牙：唯一违规点是裸 exact
    only_bare_exact = tmp_path / "only-bare-exact.md"
    only_bare_exact.write_text(COMPLIANT + "\n红集 exact 命中。\n", encoding="utf-8")
    result = _run(only_bare_exact)
    assert result.returncode == 1, (
        "只含裸 exact 的样本被放行 —— 判据④没有牙（被删或失效）"
    )
    assert "判据④" in result.stderr

    # 同一行若带选择范围则不是"裸" exact —— 边界钉住，防止判据④误伤合规表述
    scoped_exact = tmp_path / "scoped-exact.md"
    scoped_exact.write_text(
        COMPLIANT + "\n红集 exact 一致（选择范围：两文件矩阵 nodeid）。\n",
        encoding="utf-8",
    )
    result = _run(scoped_exact)
    assert result.returncode == 0, result.stderr

    # ③ 单独有牙：唯一违规点是选择范围字段缺失
    no_scope = tmp_path / "no-scope.md"
    no_scope.write_text(
        "# 变异对照\n\n"
        + _entry("SC-T-01")
        + _entry("SC-T-02").replace("- 选择范围: 两个 backstop 测试文件全量\n", ""),
        encoding="utf-8",
    )
    result = _run(no_scope)
    assert result.returncode == 1
    assert "选择范围" in result.stderr and "SC-T-02" in result.stderr

    # ② 单独有牙：SC id 出现在正文但没有自己的标题条目，仍算缺条目
    mention_only = tmp_path / "mention-only.md"
    mention_only.write_text(
        "# 变异对照\n\n" + _entry("SC-T-01") + "正文顺带提到 SC-T-02 但无条目。\n",
        encoding="utf-8",
    )
    result = _run(mention_only)
    assert result.returncode == 1
    assert "SC-T-02" in result.stderr and "判据②" in result.stderr


def test_rejects_chinese_equivalence_claims_without_scope(tmp_path: Path) -> None:
    """每个冻结的等价声称词都拒绝无范围表述，并放行同行带范围的孪生样本。"""
    for index, claim in enumerate(EQUIVALENCE_CLAIMS):
        unscoped = tmp_path / f"unscoped-{index}.md"
        unscoped.write_text(
            COMPLIANT + f"\n附注：红集{claim}预测。\n",
            encoding="utf-8",
        )
        result = _run(unscoped)
        assert result.returncode == 1, f"无范围等价声称被放行: {claim}"
        assert "判据④" in result.stderr

        scoped = tmp_path / f"scoped-{index}.md"
        scoped.write_text(
            COMPLIANT + f"\n附注：红集{claim}预测（选择范围：两个测试文件全量）。\n",
            encoding="utf-8",
        )
        result = _run(scoped)
        assert result.returncode == 0, f"同行带选择范围的表述被误拒: {claim}\n{result.stderr}"


def test_sc_ids_derived_from_manifest_not_caller(tmp_path: Path) -> None:
    """manifest 是 SC 全集 owner；不传 caller 清单仍验全集，传子集则 fail-closed。"""
    manifest = _write_manifest(tmp_path / "sc-manifest.json")
    missing_second = tmp_path / "missing-second.md"
    missing_second.write_text(
        "# 变异对照\n\n" + _entry("SC-T-01"),
        encoding="utf-8",
    )

    derived = _run(missing_second, sc_ids=None, manifest_path=manifest)
    assert derived.returncode == 1
    assert "SC-T-02" in derived.stderr and "判据②" in derived.stderr

    complete = tmp_path / "complete.md"
    complete.write_text(COMPLIANT, encoding="utf-8")
    narrowed = _run(complete, sc_ids="SC-T-01", manifest_path=manifest)
    assert narrowed.returncode == 1
    assert "SC-T-02" in narrowed.stderr and "manifest" in narrowed.stderr


def test_docstring_claims_match_implementation_coverage() -> None:
    """判据④ docstring 清单与实现常量逐项相等，且两侧都不得缩小冻结词集合。"""
    tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
    docstring = ast.get_docstring(tree, clean=False) or ""
    match = re.search(r"等价声称词（机械对账）[:：]\s*([^\n]+)", docstring)
    assert match is not None, "docstring 缺少可机械对账的等价声称词清单"
    documented = tuple(part.strip() for part in match.group(1).rstrip("。").split("、"))

    assignment = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "EQUIVALENCE_CLAIMS"
                for target in node.targets
            )
        ),
        None,
    )
    assert assignment is not None, "实现缺少 EQUIVALENCE_CLAIMS 常量"
    implemented = ast.literal_eval(assignment.value)

    assert documented == implemented, "docstring 与实现的等价声称词覆盖面不一致"
    assert implemented == EQUIVALENCE_CLAIMS, "等价声称词覆盖面小于 R7 冻结集合"
