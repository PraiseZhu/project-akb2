"""sc-harness-mutation: mutation_harness.py 测试。

断言绑定可观测行为（宪法测试约定）：红集 exact 比对、finally 还原 + sha256
复核、机器可读前缀、fail-closed 路径（未知变异 / 锚点漂移 / 空收集 / 禁改路径）。
红集 ground truth 来自双源互证：逻辑推导 + harness 一次真实运行（RED_ACTUAL 行）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

# harness 位于 scripts/，不在 pythonpath；按文件相对定位导入
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mutation_harness as mh  # noqa: E402

PY = sys.executable
LEDGER = REPO_ROOT / "src/akb2/loops/ledger.py"
SIGNATURES = REPO_ROOT / "src/akb2/loops/signatures.py"
TIMEOUT = "120"

# 双源互证的 ground truth（harness 实跑 RED_ACTUAL == 逐测试语义推导）
LEDGER_RED = [
    "tests.loops.test_ledger_lifeline.test_duplicate_event_returns_false",
    "tests.loops.test_ledger_lifeline.test_multiple_events_no_duplicates",
]
SIGNATURES_RED = [
    "tests.loops.test_signatures.test_different_signatures_independent",
    "tests.loops.test_signatures.test_escalation_threshold_is_three",
    "tests.loops.test_signatures.test_third_record_triggers_escalation",
]

# live skill 源是 host-local 真相源（~/AI-Agent/Claude/... 元工作区），不随
# 本仓 checkout 保证存在——CI runner 与其他开发者机器上必然缺失。依赖它的
# 测试须 skipif，否则在这些环境上会硬失败而非"没有该真相源所以跳过"。
_SKILL_ROUTING_TARGET = mh.resolve_mutation_file(
    mh.REGISTRY["skill-routing-broken-path"].file
)
_skill_routing_target_available = _SKILL_ROUTING_TARGET.is_file()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_harness(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        [PY, str(REPO_ROOT / "scripts/mutation_harness.py"), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_signatures(predicted: str) -> tuple[int, str, str]:
    return _run_harness(
        [
            "--mutation", "signature_escalation_threshold_4",
            "--tests", "tests/loops/test_signatures.py",
            "--predicted-red", predicted,
            "--timeout", TIMEOUT,
        ]
    )


def _prefixes(stdout: str) -> set[str]:
    return {line.split(" ", 1)[0] for line in stdout.splitlines() if line}


# ---------------------------------------------------------------------------
# 正常路径：红集 exact 匹配 / 不匹配 + 源文件还原
# ---------------------------------------------------------------------------


def test_match_exact_red_set_and_restore() -> None:
    """正确预测红集：rc=0，MATCH 前缀，ledger.py 的 sha256 与运行前相等。"""
    sha_before = _sha(LEDGER)
    rc, out, err = _run_harness(
        [
            "--mutation", "ledger_dedup_disabled",
            "--tests", "tests/loops/test_ledger_lifeline.py",
            "--predicted-red", ",".join(LEDGER_RED),
            "--timeout", TIMEOUT,
        ]
    )
    assert rc == mh.EXIT_MATCH, out + err
    assert "MUTATION_RED_SET_MATCH actual=2 predicted=2" in out
    assert _sha(LEDGER) == sha_before
    assert "SOURCE_RESTORED" in _prefixes(out)


def test_second_mutation_matches_its_own_red_set() -> None:
    """阈值变异的完整红集匹配：rc=0（覆盖第二个注册表条目），signatures.py 还原。"""
    sha_before = _sha(SIGNATURES)
    rc, out, err = _run_signatures(",".join(SIGNATURES_RED))
    assert rc == mh.EXIT_MATCH, out + err
    assert "MUTATION_RED_SET_MATCH actual=3 predicted=3" in out
    assert _sha(SIGNATURES) == sha_before
    assert "SOURCE_RESTORED" in _prefixes(out)


def test_mismatch_on_incomplete_prediction() -> None:
    """缺两个红测试的预测：rc=1，缺项全落在 only_actual，only_predicted 为空。"""
    rc, out, err = _run_signatures(SIGNATURES_RED[1])
    assert rc == mh.EXIT_MISMATCH, out + err
    assert "MUTATION_RED_SET_MISMATCH actual=3 predicted=1" in out
    assert (
        "only_actual=['tests.loops.test_signatures.test_different_signatures_independent', "
        "'tests.loops.test_signatures.test_third_record_triggers_escalation']" in out
    )
    assert "only_predicted=[]" in out


def test_mismatch_on_extra_prediction() -> None:
    """预测多出一条：rc=1，多出项落在 only_predicted。"""
    extra = "tests.loops.test_signatures.test_result_contains_signature_string"
    rc, out, err = _run_signatures(",".join(SIGNATURES_RED) + "," + extra)
    assert rc == mh.EXIT_MISMATCH, out + err
    assert "actual=3 predicted=4" in out
    assert f"only_predicted=['{extra}']" in out


def test_restore_even_when_mismatch() -> None:
    """红集不匹配（预测为空）时源文件仍被还原。"""
    sha_before = _sha(SIGNATURES)
    rc, out, err = _run_signatures("")
    assert rc == mh.EXIT_MISMATCH
    assert "MUTATION_RED_SET_MISMATCH actual=3 predicted=0" in out
    assert _sha(SIGNATURES) == sha_before
    assert "SOURCE_RESTORED" in _prefixes(out)


# ---------------------------------------------------------------------------
# fail-closed 路径（CLI 层）
# ---------------------------------------------------------------------------


def test_unknown_mutation_fails_closed() -> None:
    """未知变异名：rc=2，不碰任何源文件，不产生红集判定。"""
    sha_before = _sha(LEDGER)
    rc, out, err = _run_harness(
        ["--mutation", "nope", "--tests", "tests/loops/test_signatures.py", "--predicted-red", ""]
    )
    assert rc == mh.EXIT_ERROR
    assert "未知变异" in err
    assert _sha(LEDGER) == sha_before
    assert "MUTATION_RED_SET" not in out
    assert "SOURCE_RESTORED" not in out  # 未应用即失败，无还原语义


def test_empty_collection_is_error(tmp_path: Path) -> None:
    """收集 0 个测试：rc=2（拒绝空集静默匹配），失败路径仍还原源文件。"""
    sha_before = _sha(LEDGER)
    empty = tmp_path / "empty_mod.py"
    empty.write_text("# 收集不到任何测试\n")
    rc, out, err = _run_harness(
        [
            "--mutation", "ledger_dedup_disabled",
            "--tests", str(empty),
            "--predicted-red", "",
            "--timeout", TIMEOUT,
        ]
    )
    assert rc == mh.EXIT_ERROR, out + err
    assert "0 个测试" in err
    assert "MUTATION_RED_SET" not in out
    assert _sha(LEDGER) == sha_before
    assert "SOURCE_RESTORED" in _prefixes(out)


def test_forbidden_registry_entry_rejected_cli(monkeypatch, capsys) -> None:
    """CLI 层：注册表条目指向 src/akb2/quality/ → 启动校验拒收 rc=2，可诊断。"""
    monkeypatch.setitem(
        mh.REGISTRY,
        "bad_quality",
        mh.Mutation(
            name="bad_quality",
            file="src/akb2/quality/runner.py",
            anchor="x",
            replacement="y",
            description="probe",
        ),
    )
    rc = mh.main(
        ["--mutation", "bad_quality", "--tests", "tests/loops/test_signatures.py",
         "--predicted-red", ""]
    )
    captured = capsys.readouterr()
    assert rc == mh.EXIT_ERROR
    assert "禁改路径" in captured.err
    assert "MUTATION_RED_SET" not in captured.out


# ---------------------------------------------------------------------------
# 单元级：apply_mutation / parse 函数 / 注册表健康
# ---------------------------------------------------------------------------


def test_anchor_drift_rejected(tmp_path: Path) -> None:
    """锚点漂移（出现 2 次）：apply_mutation 拒绝应用且不写文件。"""
    target = tmp_path / "dup.py"
    target.write_bytes(b"import hashlib\nimport hashlib\n")
    mut = mh.Mutation(
        name="drift_probe",
        file=str(target),
        anchor="import hashlib",
        replacement="import hashlibXX",
        description="probe",
    )
    with pytest.raises(mh.HarnessError, match="出现 2 次"):
        mh.apply_mutation(target, mut)
    assert target.read_bytes() == b"import hashlib\nimport hashlib\n"  # 未被改动


def test_anchor_missing_rejected(tmp_path: Path) -> None:
    """锚点不存在（出现 0 次）：同样拒绝。"""
    target = tmp_path / "none.py"
    target.write_bytes(b"pass\n")
    mut = mh.Mutation(
        name="missing_probe",
        file=str(target),
        anchor="not-there",
        replacement="still-not-there",
        description="probe",
    )
    with pytest.raises(mh.HarnessError, match="出现 0 次"):
        mh.apply_mutation(target, mut)


def test_registry_entries_are_unique_and_anchored() -> None:
    """注册表健康：键名一致、锚点非空且异于替换串、目标文件真实存在。

    仓外绝对路径条目（host-local 真相源）本机缺失时不计入存在性断言——
    该条目的可用性由 test_skill_routing_broken_path_end_to_end 的 skipif
    单独把关，这里只保证"仓内条目一定存在 + 全体条目结构合法"。
    """
    assert len(mh.REGISTRY) >= 2
    for name, mut in mh.REGISTRY.items():
        assert mut.name == name
        assert mut.anchor and mut.replacement
        assert mut.anchor != mut.replacement
        target = mh.resolve_mutation_file(mut.file)
        if not Path(mut.file).is_absolute():
            assert target.is_file()


@pytest.mark.skipif(
    not _skill_routing_target_available,
    reason="live skill 源不在本机（host-local 真相源，CI 上必然缺失）",
)
def test_skill_routing_broken_path_end_to_end() -> None:
    """skill 路由变异完整周期：live SKILL.md 变异 → 路由测试红（nodeid 预测）
    → 字节级还原。反证 R1-C8：命令模块路径改坏后路由测试必须红。"""
    skill = mh.resolve_mutation_file(mh.REGISTRY["skill-routing-broken-path"].file)
    sha_before = _sha(skill)
    try:
        rc, out, err = _run_harness(
            [
                "--mutation", "skill-routing-broken-path",
                "--tests", "tests/register/test_akb_search_routing.py",
                # nodeid 写法：派工包格式，harness 归一化后与 junit 对齐
                "--predicted-red",
                "tests/register/test_akb_search_routing.py::test_routed_commands_runnable",
                "--timeout", TIMEOUT,
            ]
        )
        assert rc == mh.EXIT_MATCH, out + err
        assert "MUTATION_RED_SET_MATCH actual=1 predicted=1" in out
        assert "SOURCE_RESTORED" in _prefixes(out)
        # 变异命中执行段而非静态段：静态路由测试不在红集
        assert "test_skill_routes_to_akb2_entry" not in out
    finally:
        assert _sha(skill) == sha_before, "live SKILL.md 未还原到变异前字节"


def test_forbidden_absolute_path_rejected(monkeypatch) -> None:
    """绝对路径指到仓内禁改区：注册表校验仍拒收（防前缀绕过）。"""
    quality_abs = REPO_ROOT / "src/akb2/quality/runner.py"
    monkeypatch.setitem(
        mh.REGISTRY,
        "bad_quality_abs",
        mh.Mutation(
            name="bad_quality_abs",
            file=str(quality_abs),
            anchor="x",
            replacement="y",
            description="probe",
        ),
    )
    problems = mh._validate_registry()
    assert any(
        "禁改路径" in p and "bad_quality_abs" in p for p in problems
    ), problems


def test_parse_predicted_red() -> None:
    """逗号分隔解析：空白条目丢弃、空串=无红。"""
    assert mh.parse_predicted_red("a.b, c.d ,,") == {"a.b", "c.d"}
    assert mh.parse_predicted_red("") == set()


def test_parse_predicted_red_accepts_nodeid_form() -> None:
    """nodeid（path::test）与 classname.name 是同一红集 id 的等价写法。"""
    nodeid = "tests/register/test_akb_search_routing.py::test_routed_commands_runnable"
    dotted = "tests.register.test_akb_search_routing.test_routed_commands_runnable"
    assert mh.parse_predicted_red(nodeid) == {dotted}
    # 两种写法混用也归一到同一命名空间
    assert mh.parse_predicted_red(f"{nodeid},{dotted}") == {dotted}
    # 参数化 / 类内测试：:: 后各段以 . 连接
    assert mh.parse_predicted_red("tests/x/test_y.py::TestZ::test_w[p1]") == {
        "tests.x.test_y.TestZ.test_w[p1]"
    }


def test_parse_red_set_from_junit_fixture(tmp_path: Path) -> None:
    """junit 解析：failure/error 计红，pass 不计。"""
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuite name="p" tests="3">'
        '<testcase classname="m" name="red1"><failure message="boom"/></testcase>'
        '<testcase classname="m" name="red2"><error message="err"/></testcase>'
        '<testcase classname="m" name="green"/>'
        "</testsuite>"
    )
    assert mh.parse_red_set(junit) == {"m.red1", "m.red2"}


def test_parse_red_set_missing_junit_is_error(tmp_path: Path) -> None:
    """junit 缺失：HarnessError（fail-closed）。"""
    with pytest.raises(mh.HarnessError, match="junitxml 未生成"):
        mh.parse_red_set(tmp_path / "nope.xml")


def test_parse_red_set_zero_collection_is_error(tmp_path: Path) -> None:
    """junit 存在但收集 0 测试：拒绝空集静默匹配。"""
    junit = tmp_path / "zero.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuite name="p" tests="0" errors="0" failures="0" skipped="0"></testsuite>'
    )
    with pytest.raises(mh.HarnessError, match="0 个测试"):
        mh.parse_red_set(junit)


# ---------------------------------------------------------------------------
# v2b 新增注册表条目：retrieval / mcp 边界变异（结构性断言 + 端到端周期）
# ---------------------------------------------------------------------------

_V2B_MUTATIONS = (
    "retrieval-readonly-open",
    "mcp-dynamic-import-bypass",
    "mcp-readonly-open",
)


def test_v2b_mutations_registered_and_anchored() -> None:
    """三条 v2b 变异已注册、目标仓内文件存在、锚点在其中恰好出现一次。"""
    for name in _V2B_MUTATIONS:
        assert name in mh.REGISTRY, f"注册表缺 {name}"
        mut = mh.REGISTRY[name]
        target = mh.resolve_mutation_file(mut.file)
        assert target.is_file(), f"{name} 目标缺失: {mut.file}"
        assert target.read_bytes().count(mut.anchor.encode("utf-8")) == 1, (
            f"{name} 锚点在 {mut.file} 中出现次数 != 1（源码漂移）"
        )


def test_v2b_mutation_files_all_inside_repo() -> None:
    """v2b 变异目标全部是仓内相对路径（CI 任何环境都可执行，无 host-local 依赖）。"""
    for name in _V2B_MUTATIONS:
        assert not Path(mh.REGISTRY[name].file).is_absolute(), name


def test_retrieval_readonly_open_mutation_cycle() -> None:
    """retrieval-readonly-open 完整周期：引擎写 vault → 只读契约测试双入口红。

    预测红集 = verify 命令钉死的两条 nodeid（engine 入口 + CLI 入口）。
    """
    rc, out, err = _run_harness(
        [
            "--mutation", "retrieval-readonly-open",
            "--tests", "tests/retrieval/test_readonly_contract.py",
            "--predicted-red",
            "tests/retrieval/test_readonly_contract.py::test_engine_entry_no_write,"
            "tests/retrieval/test_readonly_contract.py::test_cli_entry_no_write",
            "--timeout", TIMEOUT,
        ]
    )
    assert rc == mh.EXIT_MATCH, out + err
    assert "MUTATION_RED_SET_MATCH actual=2 predicted=2" in out
    assert "SOURCE_RESTORED" in _prefixes(out)
