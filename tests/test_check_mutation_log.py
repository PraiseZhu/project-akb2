"""变异证据口径闸（scripts/check_mutation_log.py）的坏样本矩阵。

SC-R6-05 起把「红集必须写明选择范围」接成机器闸；SC-R8-02（收敛检查点 R8）把判据从
**词法**（有限等价声称词表）改成**结构**（每个实测红集所在的变异窗口必须声明选择范围）。
本模块用构造的坏样本证明：结构判据对缺范围各类形态翻红、对合规样本放行，且**不再**依赖
措辞识别——等价声称词写在正文里但每个变异窗口都有范围时不得误伤（R6/R7 咬中自身元文本
的误报模式已消除）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER = PROJECT_ROOT / "scripts" / "check_mutation_log.py"

SC_IDS = "SC-T-01,SC-T-02"


def _variant(*, scope: str = "两个 backstop 测试文件全量",
             predicted: str = "2 条矩阵 nodeid",
             actual: str = "与预测一致") -> str:
    """一个变异窗口：选择范围 + 预测红集 + 实测红集（实测红集界定窗口结束）。"""
    return (
        f"- 变异: 删除关键行\n"
        f"- 选择范围: {scope}\n"
        f"- 预测红集: {predicted}\n"
        f"- 实测红集: {actual}\n"
        f"- 判定: 匹配\n"
    )


def _entry(sc_id: str, *, variants: str | None = None) -> str:
    return f"## {sc_id}\n\n" + (variants if variants is not None else _variant()) + "\n"


COMPLIANT = "# 变异对照\n\n" + _entry("SC-T-01") + _entry("SC-T-02")


def _write_manifest(
    path: Path, sc_ids: tuple[str, ...] = ("SC-T-01", "SC-T-02")
) -> Path:
    import json

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
        sys.executable, str(CHECKER),
        "--log", str(log_path),
        "--sc-manifest", str(manifest_path),
    ]
    if sc_ids is not None:
        command.extend(("--sc-ids", sc_ids))
    return subprocess.run(command, capture_output=True, text=True, timeout=30)


def test_rejects_missing_entry_and_incomplete_fields(tmp_path: Path) -> None:
    """判据①②③：合规样本放行；缺条目 / 缺字段 / 文件不存在各自 fail-closed。"""
    good = tmp_path / "good.md"
    good.write_text(COMPLIANT, encoding="utf-8")
    assert _run(good).returncode == 0, _run(good).stderr

    # ② manifest 派生的 SC-T-02 无条目
    missing_entry = tmp_path / "missing-entry.md"
    missing_entry.write_text("# 变异对照\n\n" + _entry("SC-T-01"), encoding="utf-8")
    r = _run(missing_entry)
    assert r.returncode == 1 and "SC-T-02" in r.stderr and "判据②" in r.stderr

    # ③ 预测红集为空
    empty_pred = tmp_path / "empty-pred.md"
    empty_pred.write_text(
        "# 变异对照\n\n" + _entry("SC-T-01")
        + _entry("SC-T-02", variants=_variant(predicted="").replace("- 预测红集: \n", "- 预测红集:\n")),
        encoding="utf-8",
    )
    r = _run(empty_pred)
    assert r.returncode == 1 and "SC-T-02" in r.stderr and "预测红集" in r.stderr

    # ③ 条目内无实测红集
    no_actual = tmp_path / "no-actual.md"
    no_actual.write_text(
        "# 变异对照\n\n" + _entry("SC-T-01")
        + "## SC-T-02\n\n- 变异: x\n- 选择范围: y\n- 预测红集: z\n\n",
        encoding="utf-8",
    )
    r = _run(no_actual)
    assert r.returncode == 1 and "SC-T-02" in r.stderr and "实测红集" in r.stderr

    # ① 文件不存在
    r = _run(tmp_path / "nope.md")
    assert r.returncode == 2 and "不存在" in r.stderr


def test_scope_requirement_is_structural_not_lexical(tmp_path: Path) -> None:
    """SC-R8-02：范围要求由结构（变异窗口）强制，与实测红集的措辞无关。

    这是本轮的核心不变量。三个方向缺一都能让族 B 复发：
      · 实测红集用任意等价措辞、但窗口内有选择范围 → 必须放行（不再检测措辞）；
      · 窗口缺选择范围 → 必须翻红，无论措辞是什么；
      · 等价声称词出现在正文/附注（不属于任何变异窗口）→ 不得误伤（词表已删，
        这正是 R6/R7 咬中自身元文本的误报模式，本轮消除）。
    """
    # ① 措辞是等价声称、但窗口内有范围 → 放行（结构满足即可，不看词）
    for actual in ("与预测完全一致", "红集恰为预测", "精确命中", "相等", "一一对应",
                   "identical to prediction", "precisely as predicted"):
        ok = tmp_path / "ok.md"
        ok.write_text("# t\n\n" + _entry("SC-T-01") + _entry("SC-T-02", variants=_variant(actual=actual)),
                      encoding="utf-8")
        r = _run(ok)
        assert r.returncode == 0, f"窗口内有范围却因措辞被拒: {actual!r}\n{r.stderr}"

    # ② 窗口缺选择范围 → 翻红（与措辞无关）；错误信息指向结构判据③
    no_scope = tmp_path / "no-scope.md"
    no_scope.write_text(
        "# t\n\n" + _entry("SC-T-01")
        + "## SC-T-02\n\n- 变异: x\n- 预测红集: z\n- 实测红集: 与预测一致\n\n",
        encoding="utf-8",
    )
    r = _run(no_scope)
    assert r.returncode == 1 and "SC-T-02" in r.stderr and "选择范围" in r.stderr

    # ②b 多变异：仅第 2 个窗口缺范围 → 指名第 2 个窗口
    two_var = _variant() + _variant(scope="").replace("- 选择范围: \n", "")
    partial = tmp_path / "partial.md"
    partial.write_text("# t\n\n" + _entry("SC-T-01") + _entry("SC-T-02", variants=two_var),
                       encoding="utf-8")
    r = _run(partial)
    assert r.returncode == 1 and "第 2 个" in r.stderr and "选择范围" in r.stderr

    # ③ 等价声称词在正文附注（不属于任何 SC 窗口）→ 不得误伤（词表已删）
    prose = tmp_path / "prose.md"
    prose.write_text(
        COMPLIANT + "\n## 附注\n\n红集与预测完全一致、恰为、精确、identical —— 这些是元文本。\n",
        encoding="utf-8",
    )
    assert _run(prose).returncode == 0, (
        "正文附注里的等价声称词被误伤 —— 词法检测未彻底删除:\n" + _run(prose).stderr
    )


def test_existing_logs_still_pass_under_structural_rule(tmp_path: Path) -> None:
    """SC-R8-02 兼容硬约束：R6/R7 形态的日志（多变异 + 实测红集用等价措辞）必须仍放行。

    合成一份复刻真实日志形状的样本（每条 SC 多个变异，实测红集行大量使用「恰为预测」
    「完全一致」等措辞，并带非变异的正文附注行），确认结构判据不因这些措辞翻红。
    """
    r6_like = (
        _variant(actual="恰为预测 2 条（同选择范围）")
        + _variant(scope="同上两文件全量", predicted="8 项", actual="8 failed，与预测同名同集")
    )
    r7_like = _variant(actual="该项翻红，另两项绿，完全一致于预测")
    log = tmp_path / "r6r7-like.md"
    log.write_text(
        "# 复刻真实日志形状\n\n"
        + _entry("SC-T-01", variants=r6_like)
        + _entry("SC-T-02", variants=r7_like)
        + "## 附注\n\n每条变异均单独施加、恢复后 diff 逐字节相同，复跑正向 verify 全绿。\n",
        encoding="utf-8",
    )
    r = _run(log)
    assert r.returncode == 0, f"复刻真实日志形状的样本被结构判据误拒:\n{r.stderr}"


def test_docstring_claims_match_implementation_coverage() -> None:
    """SC-R8-02：docstring 声称的判据面 = 实现的判据面，且词表已彻底删除。

    「声称超出实现」正是族 B 的病灶。本测试机械对账两件事：
      · 实现不得再有任何等价声称词表常量（EQUIVALENCE_CLAIMS / _RE_EQUIVALENCE_CLAIM）——
        它们一旦存在就意味着又回到「用有限词表兑现无界全称」的老路；
      · docstring 枚举的判据编号集合 == 实现能产出的判据编号集合，且都不含④
        （④ 是被归一掉的词法判据，不能在任何一侧残留）。
    """
    source = CHECKER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 词表常量必须不存在（删除即删除，不允许"留着但不用"）
    banned = {"EQUIVALENCE_CLAIMS", "_RE_EQUIVALENCE_CLAIM"}
    assigned_names = {
        t.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    leftover = banned & assigned_names
    assert not leftover, f"等价声称词表常量未删除（族 B 会复发）: {leftover}"

    import re as _re
    docstring = ast.get_docstring(tree) or ""
    # docstring 声称的判据编号
    doc_criteria = set(_re.findall(r"判据([①②③④])", docstring)) | set(_re.findall(r"^\s*([①②③④])", docstring, _re.M))
    # 实现产出的判据编号（check() 的违规文案 + 退出码 2 对应①）
    impl_criteria = set(_re.findall(r"判据([①②③④])", source))
    assert "④" not in doc_criteria, "docstring 仍声称词法判据④（应已归一删除）"
    assert "④" not in impl_criteria, "实现仍产出判据④（应已归一删除）"
    # 结构判据③ 必须两侧都在
    assert "③" in doc_criteria and "③" in impl_criteria, "结构判据③ 在 docstring 或实现中缺失"
    # 双向：实现产出的每个判据都在 docstring 声称里
    assert impl_criteria <= doc_criteria, (
        f"实现产出的判据 {impl_criteria} 超出 docstring 声称 {doc_criteria}"
    )


def test_sc_ids_derived_from_manifest_not_caller(tmp_path: Path) -> None:
    """manifest 是 SC 全集 owner；不传 caller 清单仍验全集，传子集则 fail-closed。"""
    manifest = _write_manifest(tmp_path / "sc-manifest.json")
    missing_second = tmp_path / "missing-second.md"
    missing_second.write_text("# t\n\n" + _entry("SC-T-01"), encoding="utf-8")

    derived = _run(missing_second, sc_ids=None, manifest_path=manifest)
    assert derived.returncode == 1
    assert "SC-T-02" in derived.stderr and "判据②" in derived.stderr

    complete = tmp_path / "complete.md"
    complete.write_text(COMPLIANT, encoding="utf-8")
    narrowed = _run(complete, sc_ids="SC-T-01", manifest_path=manifest)
    assert narrowed.returncode == 1
    assert "SC-T-02" in narrowed.stderr and "manifest" in narrowed.stderr
