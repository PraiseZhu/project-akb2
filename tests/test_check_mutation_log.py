"""SC-R6-05 — 变异证据口径闸（scripts/check_mutation_log.py）的坏样本矩阵。

规矩此前只活在 mutation-log-r4.md 的正文里（无 owner，R5 轮 lead 自己违反了它）。
本模块用**构造的坏样本**证明闸对四类违规各自翻红、对合规样本放行，且每条判据
单独有牙（删掉任一判据都有一个只违反该判据的样本会漏检 → 对应断言翻红）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER = PROJECT_ROOT / "scripts" / "check_mutation_log.py"

SC_IDS = "SC-T-01,SC-T-02"


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


def _run(log_path: Path, sc_ids: str = SC_IDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--log", str(log_path), "--sc-ids", sc_ids],
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
