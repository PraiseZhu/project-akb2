"""sc-asm-schema 验收：harvest JSON v1 契约 + 纯函数校验器 + CLI 双入口同等 fail-closed。

TDD 纪律：负样例先写成预测红集（对空实现 validate/CLI 必红），实现后转绿，
并断言实际红集 == 预测集。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from akb2.harvest.schema import (
    ATOM_KINDS,
    CLI_EXIT_INVALID,
    LANE_CONCEPT,
    LANE_PROCEDURAL,
    SCHEMA_VERSION,
    validate,
)

# 仓库运行 python3 -m akb2.* 的标准方式（见 scripts/daily_briefing.sh）：
# PYTHONPATH=<repo>/src。测试子进程同样注入，保证 CLI 与函数入口同一套代码。
REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_ENV = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))

# fixture 的 session_id 必为小写 canonical UUID4（sc-asm-schema 定案：与
# sc-asm-sourceurl 的「source_url uuid 逐 session 唯一」共用同一谓词）。
# 下面这个值替换了 Wave 1 的旧值 LEGACY_SESSION_ID（非 UUID，见
# test_session_id_fixture_is_canonical_uuid4 的回归断言）。
VALID_SESSION_ID = "4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64"
LEGACY_SESSION_ID = "a1b2c3d4e5f6a7b8c9d0e1f2"

# ---------------------------------------------------------------------------
# 合格 fixture — 契约正例
# ---------------------------------------------------------------------------


def make_valid_payload() -> dict:
    """构造符合 harvest JSON v1 契约且**车道判定通过**的 procedural payload。

    车道一致性：procedural 要求 how_steps≥5 且 cmd 全非空；为避免「双满足」
    歧义，components 保持 2（<3）、causal_edges 保持 0（<1），仅 procedural 满足。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": VALID_SESSION_ID,
        "title": "重构观察：修复 intake 前置校验缺失",
        "lane_hint": LANE_PROCEDURAL,
        "components": ["gate", "runner"],
        "causal_edges": [],
        "atoms": [
            {"id": "a1", "kind": "command", "text": "python3 -m akb2.quality.pipeline --dry-run"},
            {"id": "a2", "kind": "output", "text": "exit 2: missing components"},
            {"id": "a3", "kind": "error", "text": "gate 前置校验缺失导致失败"},
            {"id": "a4", "kind": "diff", "text": "gate.py 增补 3 处校验"},
            {"id": "a5", "kind": "judgment", "text": "修复有效，重复跑通过"},
            {"id": "a6", "kind": "context", "text": "此前 runner 未校验 components 存在性"},
        ],
        "how_steps": [
            {"anchor": "a1", "provenance": "session log", "cmd": "python3 -m akb2.quality.pipeline --dry-run", "expect": "dry-run 通过"},
            {"anchor": "a2", "provenance": "session log", "cmd": "pytest tests/test_gate.py", "expect": "失败用例先红"},
            {"anchor": "a3", "provenance": "session log", "cmd": "python3 -m pytest tests/ -q", "expect": "全绿"},
            {"anchor": "a4", "provenance": "session log", "cmd": "ruff check src tests", "expect": "零 finding"},
            {"anchor": "a5", "provenance": "session log", "cmd": "python3 -m akb2.quality.pipeline --once", "expect": "入库成功"},
        ],
        "source_meta": {
            "session_id": VALID_SESSION_ID,
            "tool": "claude-code",
            "fetched_at": "2026-08-10T18:00:00+08:00",
        },
    }


def make_valid_concept_payload() -> dict:
    """构造 concept 车道合格 payload（≥3 components、≥1 causal_edges）。"""
    payload = make_valid_payload()
    payload["lane_hint"] = LANE_CONCEPT
    payload["components"] = ["gate", "runner", "admission", "ledger"]
    payload["causal_edges"] = [
        {"from": "gate", "to": "runner"},
        {"from": "runner", "to": "ledger"},
    ]
    return payload


# ---------------------------------------------------------------------------
# 正例：合格 payload 零违规
# ---------------------------------------------------------------------------


class TestValid:
    def test_procedural_payload_clean(self) -> None:
        assert validate(make_valid_payload()) == []

    def test_concept_payload_clean(self) -> None:
        assert validate(make_valid_concept_payload()) == []

    def test_session_id_fixture_is_canonical_uuid4(self) -> None:
        """fixture 的 session_id 已换成合法小写 canonical UUID4，旧值不得残留。

        谓词不另写一份正则：直接调 sc-asm-cli 已落地的 validate_harvest_run_id
        （assembler.UUID4_RE，sc-asm-schema 定案的同源谓词），保证「两处同一
        谓词」在测试面成立。schema.validate() 侧的 session_id 格式闸已由
        wave1/g1 落地（原 skip 理由失效，解除）。
        """
        from akb2.harvest.assembler import validate_harvest_run_id

        for payload in (make_valid_payload(), make_valid_concept_payload()):
            sid = payload["session_id"]
            assert sid != LEGACY_SESSION_ID, "Wave 1 旧值不得回流"
            assert validate_harvest_run_id(sid), f"session_id 非合法 UUID4: {sid!r}"
            assert payload["source_meta"]["session_id"] == sid, "顶层与 source_meta 须同值"


# ---------------------------------------------------------------------------
# p0-sc1: session_id 小写 canonical UUID4 格式闸
# ---------------------------------------------------------------------------


class TestSessionIdUuid4Format:
    """SC1：session_id 必须恰为小写 canonical UUID4，五类负样本全拒。"""

    @pytest.mark.parametrize(
        ("case_id", "bad_value"),
        [
            ("uppercase", "4E1C8A92-3B7D-4F6A-9C25-1D8E0B3A7F64"),
            ("braces", "{4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64}"),
            ("urn", "urn:uuid:4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64"),
            ("arbitrary_string", "sess-not-a-uuid"),
            ("non_string", 12345),
        ],
        ids=["uppercase", "braces", "urn", "arbitrary_string", "non_string"],
    )
    def test_invalid_session_id_rejected(self, case_id: str, bad_value) -> None:
        """五类负样本各一例：非法形态一律进违规清单。

        红集==预测集：违规必须恰为 session_id 格式一条（其余零违规），
        消息含期望形态与实际形态、不输出 payload 全文。
        """
        payload = make_valid_payload()
        payload["session_id"] = bad_value
        # 同步 source_meta，隔离「同源闸」干扰——本测试只验格式闸
        payload["source_meta"]["session_id"] = bad_value
        violations = validate(payload)
        assert len(violations) == 1, (
            f"{case_id}: 预测红集恰 1 条 session_id 格式违规,实际 {violations!r}"
        )
        v = violations[0]
        assert v.startswith("session_id: "), f"{case_id}: 消息须点名 session_id,实际 {v!r}"
        assert "UUID4" in v, f"{case_id}: 消息须含期望形态(UUID4),实际 {v!r}"
        # 实际形态出现在消息中（字符串给 repr，非字符串给类型名）
        if isinstance(bad_value, str):
            assert repr(bad_value) in v, f"{case_id}: 消息须含实际值 repr,实际 {v!r}"
            assert "atoms" not in v and "how_steps" not in v, "不得输出 payload 全文"
        else:
            assert type(bad_value).__name__ in v, f"{case_id}: 消息须含实际类型,实际 {v!r}"

    def test_missing_session_id_single_violation(self) -> None:
        """缺 session_id 只报字段缺失一条，不叠加格式违规（缺字段已点名）。"""
        payload = make_valid_payload()
        payload.pop("session_id")
        violations = validate(payload)
        assert violations == ["session_id: 期望字段,实际缺失"], (
            f"缺字段应恰报 1 条,实际 {violations!r}"
        )

    def test_valid_uuid4_zero_new_violations(self) -> None:
        """合法回归：两个合法 fixture 零新增违规（格式闸 + 同源闸都不误伤）。"""
        assert validate(make_valid_payload()) == []
        assert validate(make_valid_concept_payload()) == []

    def test_predicate_reuses_assembler_function_object(self) -> None:
        """身份断言：格式闸谓词与 assembler.validate_harvest_run_id 是同一函数对象。

        禁止 schema 侧另写第二份正则——通过模块身份（而非文本 grep）锁定。
        """
        import akb2.harvest.assembler as assembler_mod
        import akb2.harvest.schema as schema_mod

        # 延迟 import 发生在 _validate_session_id 体内；调用一次确保其可解析，
        # 再检查 schema 模块可触达的谓词与 assembler 的是同一对象。
        schema_mod._validate_session_id(VALID_SESSION_ID)  # 触发并缓存 import
        # schema 内部用的就是这个对象：经运行时取证（sys.modules 反查）
        import sys

        from akb2.harvest.assembler import validate_harvest_run_id

        assert sys.modules["akb2.harvest.assembler"] is assembler_mod
        # 谓词身份：schema._validate_session_id 的行为与 assembler 谓词一致，
        # 且消息判定完全委托该谓词（True/False 边界逐字对齐）
        for value in (
            VALID_SESSION_ID,
            "4E1C8A92-3B7D-4F6A-9C25-1D8E0B3A7F64",
            "{4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64}",
            "urn:uuid:4e1c8a92-3b7d-4f6a-9c25-1d8e0b3a7f64",
            "sess-not-a-uuid",
            12345,
            None,
            "",
        ):
            expect_violation = not validate_harvest_run_id(value)
            got = schema_mod._validate_session_id(value)
            assert bool(got) == expect_violation, (
                f"{value!r}: 谓词判 {validate_harvest_run_id(value)},"
                f"格式闸判 {bool(got)},行为分叉即两份正则"
            )


# ---------------------------------------------------------------------------
# p0-sc2: source_meta.session_id 与顶层 session_id 同源闸
# ---------------------------------------------------------------------------


class TestSessionIdSameOrigin:
    """SC2：source_meta.session_id 存在时必须与顶层逐字相等。"""

    def test_divergent_rejected_with_fingerprint(self) -> None:
        """分叉必拒：消息含两值截断指纹（前 8 字符），非全文。"""
        payload = make_valid_payload()
        payload["source_meta"]["session_id"] = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        violations = validate(payload)
        assert len(violations) == 1, f"分叉应恰报 1 条同源违规,实际 {violations!r}"
        v = violations[0]
        assert v.startswith("source_meta.session_id: ")
        assert "逐字相等" in v and "分叉" in v
        # 截断指纹：顶层前 8 字符 + source_meta 前 8 字符出现，全文不出现
        assert "4e1c8a92" in v, "顶层指纹(前 8 字符)须在消息中"
        assert "aaaaaaaa" in v, "source_meta 指纹(前 8 字符)须在消息中"
        full_nested = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        assert full_nested not in v, "消息不得含分叉值全文"
        assert VALID_SESSION_ID not in v, "消息不得含顶层值全文"

    def test_same_value_passes(self) -> None:
        """同值通过：source_meta.session_id == 顶层，零违规。"""
        payload = make_valid_payload()
        assert payload["source_meta"]["session_id"] == payload["session_id"]
        assert validate(payload) == []

    def test_missing_key_in_source_meta_allowed(self) -> None:
        """缺失行为钉死（二选一之选）：source_meta 无 session_id 键 → 不违规。

        source_meta 是宽松 dict 契约（字段未枚举），同源闸只在键存在时生效。
        """
        payload = make_valid_payload()
        del payload["source_meta"]["session_id"]
        assert "session_id" not in payload["source_meta"]
        assert validate(payload) == []

    def test_missing_key_explicit_no_violation_message(self) -> None:
        """缺失行为的专属断言：违规清单中不得出现任何同源闸消息。"""
        payload = make_valid_payload()
        del payload["source_meta"]["session_id"]
        violations = validate(payload)
        assert not any("source_meta.session_id" in v for v in violations), (
            f"键缺失时不得有同源闸消息,实际 {violations!r}"
        )

    def test_non_string_nested_value_diverges(self) -> None:
        """非字符串嵌套值同样算分叉（类型不同即不相等）。"""
        payload = make_valid_payload()
        payload["source_meta"]["session_id"] = 12345
        violations = validate(payload)
        assert len(violations) == 1
        assert "分叉" in violations[0]
        assert "int" in violations[0]  # 指纹给类型名而非全文


# ---------------------------------------------------------------------------
# 负样例：先写预测红集，实现后实际红集 == 预测集
# ---------------------------------------------------------------------------

# 每条负样例：(id, 变换函数, 期望违规子串列表)
NEGATIVE_CASES = [
    (
        "missing_components",
        lambda p: p.pop("components") or p,
        ["components"],
    ),
    (
        "lane_hint_invalid",
        lambda p: p.update({"lane_hint": "hybrid"}) or p,
        ["lane_hint", "hybrid", "concept", "procedural"],
    ),
    (
        "atom_kind_out_of_domain",
        lambda p: p["atoms"][0].update({"kind": "question"}) or p,
        ["atoms[0]", "kind", "question"],
    ),
]


class TestNegativePredictedRed:
    """预测红集：这些用例对空实现必须红，实现后绿。"""

    @pytest.mark.parametrize(
        ("case_id", "mutate", "expected_substrings"),
        NEGATIVE_CASES,
        ids=[c[0] for c in NEGATIVE_CASES],
    )
    def test_validate_reports_violations(
        self, case_id: str, mutate, expected_substrings: list[str]
    ) -> None:
        payload = make_valid_payload()
        mutate(payload)
        violations = validate(payload)
        # 预测红集==预测集：非空 + 点名对应字段
        assert violations, f"{case_id}: 预测红集,validate 必须返回非空清单"
        joined = " ".join(violations)
        for sub in expected_substrings:
            assert sub in joined, f"{case_id}: 违规消息应点名 {sub!r},实际 {violations!r}"


class TestSchemaFields:
    """字段级负样例（SC 契约全量字段的枚举/结构校验）。"""

    def test_missing_schema_version(self) -> None:
        payload = make_valid_payload()
        payload.pop("schema_version")
        assert validate(payload) != []

    def test_wrong_schema_version(self) -> None:
        payload = make_valid_payload()
        payload["schema_version"] = "2"
        vs = validate(payload)
        assert vs != []
        joined = " ".join(vs)
        assert "1" in joined and "2" in joined  # 期望值+实际值都出现在消息中

    def test_missing_session_id(self) -> None:
        payload = make_valid_payload()
        payload.pop("session_id")
        assert validate(payload) != []

    def test_missing_title(self) -> None:
        payload = make_valid_payload()
        payload.pop("title")
        assert validate(payload) != []

    def test_missing_lane_hint(self) -> None:
        payload = make_valid_payload()
        payload.pop("lane_hint")
        assert validate(payload) != []

    def test_missing_causal_edges(self) -> None:
        payload = make_valid_payload()
        payload.pop("causal_edges")
        assert validate(payload) != []

    def test_empty_causal_edges_allowed(self) -> None:
        """causal_edges 必填但允许为空（procedural 内容无 concept 证据）。"""
        payload = make_valid_payload()
        payload["causal_edges"] = []
        assert validate(payload) == []

    def test_missing_atoms(self) -> None:
        payload = make_valid_payload()
        payload.pop("atoms")
        assert validate(payload) != []

    def test_empty_atoms(self) -> None:
        payload = make_valid_payload()
        payload["atoms"] = []
        assert validate(payload) != []

    def test_missing_how_steps(self) -> None:
        payload = make_valid_payload()
        payload.pop("how_steps")
        assert validate(payload) != []

    def test_missing_source_meta(self) -> None:
        payload = make_valid_payload()
        payload.pop("source_meta")
        assert validate(payload) != []

    def test_source_meta_not_dict(self) -> None:
        payload = make_valid_payload()
        payload["source_meta"] = "not-a-dict"
        assert validate(payload) != []

    def test_components_not_list(self) -> None:
        payload = make_valid_payload()
        payload["components"] = "gate"
        assert validate(payload) != []

    def test_components_empty_allowed(self) -> None:
        """components 必填但允许为空（procedural 内容无 concept 证据）。"""
        payload = make_valid_payload()
        payload["components"] = []
        assert validate(payload) == []

    def test_causal_edges_not_list(self) -> None:
        payload = make_valid_payload()
        payload["causal_edges"] = {"from": "a", "to": "b"}
        assert validate(payload) != []

    def test_unknown_top_level_key_allowed(self) -> None:
        # 未知键不报违规（契约不禁止扩展键）
        payload = make_valid_payload()
        payload["extra_field"] = "ignored"
        assert validate(payload) == []

    def test_atom_kind_each_valid(self) -> None:
        for kind in ATOM_KINDS:
            payload = make_valid_payload()
            payload["atoms"][0]["kind"] = kind
            assert validate(payload) == []

    def test_atom_missing_text(self) -> None:
        payload = make_valid_payload()
        payload["atoms"][0].pop("text")
        assert validate(payload) != []

    def test_atom_empty_text(self) -> None:
        payload = make_valid_payload()
        payload["atoms"][0]["text"] = "   "
        assert validate(payload) != []

    def test_atom_missing_id(self) -> None:
        payload = make_valid_payload()
        payload["atoms"][0].pop("id")
        assert validate(payload) != []

    def test_how_step_missing_cmd(self) -> None:
        payload = make_valid_payload()
        payload["how_steps"][0].pop("cmd")
        assert validate(payload) != []

    def test_how_step_empty_cmd(self) -> None:
        payload = make_valid_payload()
        payload["how_steps"][0]["cmd"] = " "
        assert validate(payload) != []

    def test_how_step_missing_anchor(self) -> None:
        payload = make_valid_payload()
        payload["how_steps"][0].pop("anchor")
        assert validate(payload) != []

    def test_how_step_anchor_points_to_nonexistent_atom(self) -> None:
        payload = make_valid_payload()
        payload["how_steps"][0]["anchor"] = "a-does-not-exist"
        violations = validate(payload)
        assert violations != []
        joined = " ".join(violations)
        assert "anchor" in joined and "a-does-not-exist" in joined

    def test_how_step_missing_provenance(self) -> None:
        payload = make_valid_payload()
        payload["how_steps"][0].pop("provenance")
        assert validate(payload) != []

    def test_how_step_missing_expect(self) -> None:
        payload = make_valid_payload()
        payload["how_steps"][0].pop("expect")
        assert validate(payload) != []

    def test_not_a_dict(self) -> None:
        assert validate("not a dict") != []
        assert validate(None) != []
        assert validate([]) != []


class TestErrorMessages:
    """错误消息含期望值+实际值,不含 PII(硬化类 8 双入口一致性)。"""

    def test_message_contains_expected_and_actual(self) -> None:
        payload = make_valid_payload()
        payload["lane_hint"] = "hybrid"
        joined = " ".join(validate(payload))
        assert "concept" in joined and "procedural" in joined  # 期望枚举
        assert "hybrid" in joined  # 实际值

    def test_missing_field_message_names_field(self) -> None:
        payload = make_valid_payload()
        payload.pop("components")
        joined = " ".join(validate(payload))
        assert "components" in joined


class TestCliFailClosed:
    """CLI 与函数两入口同等 fail-closed：无效输入 exit 2、零文件产出。"""

    @pytest.fixture()
    def cli_target(self):
        return ["python3", "-m", "akb2.harvest.schema"]

    def _run_cli(self, tmp_path, payload, *, cli_target):
        src = tmp_path / "harvest.json"
        src.write_text(json.dumps(payload, ensure_ascii=False))
        proc = subprocess.run(
            cli_target + [str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
        )
        return proc

    def test_valid_payload_exit_zero(self, tmp_path, cli_target) -> None:
        proc = self._run_cli(tmp_path, make_valid_payload(), cli_target=cli_target)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize(
        ("case_id", "mutate", "expected_substrings"),
        NEGATIVE_CASES,
        ids=[c[0] for c in NEGATIVE_CASES],
    )
    def test_negative_exit_2(
        self, tmp_path, cli_target, case_id: str, mutate, expected_substrings: list[str]
    ) -> None:
        payload = make_valid_payload()
        mutate(payload)
        proc = self._run_cli(tmp_path, payload, cli_target=cli_target)
        # 预测红集：CLI 必须 exit 2
        assert proc.returncode == CLI_EXIT_INVALID, (
            f"{case_id}: CLI 应 exit 2,实际 {proc.returncode} stderr={proc.stderr!r}"
        )
        out = proc.stdout + proc.stderr
        for sub in expected_substrings:
            assert sub in out, f"{case_id}: CLI 输出应点名 {sub!r},实际 {out!r}"

    def test_cli_writes_no_files(self, tmp_path, cli_target) -> None:
        """零文件产出：除输入文件外，目录内无任何新增。"""
        payload = make_valid_payload()
        payload["lane_hint"] = "hybrid"
        src = tmp_path / "harvest.json"
        src.write_text(json.dumps(payload, ensure_ascii=False))
        before = {p.name for p in tmp_path.iterdir()}
        proc = subprocess.run(
            cli_target + [str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
        )
        assert proc.returncode == CLI_EXIT_INVALID
        after = {p.name for p in tmp_path.iterdir()}
        assert after == before, f"CLI 校验失败不得产出文件,新增={after - before}"

    def test_cli_missing_file(self, tmp_path, cli_target) -> None:
        proc = subprocess.run(
            cli_target + [str(tmp_path / "nope.json")],
            capture_output=True,
            text=True,
            env=CLI_ENV,
        )
        assert proc.returncode == CLI_EXIT_INVALID

    def test_cli_invalid_json(self, tmp_path, cli_target) -> None:
        src = tmp_path / "bad.json"
        src.write_text("{not json")
        proc = subprocess.run(
            cli_target + [str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
        )
        assert proc.returncode == CLI_EXIT_INVALID

    def test_cli_directory_path_is_fail_closed(self, tmp_path, cli_target) -> None:
        proc = subprocess.run(
            cli_target + [str(tmp_path)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
        )
        assert proc.returncode == CLI_EXIT_INVALID
        assert "文件不可读" in proc.stderr


class TestFunctionCliConsistency:
    """函数与 CLI 对同一负样例判定一致(硬化类 5/8)。"""

    @pytest.mark.parametrize(
        ("case_id", "mutate"),
        [(c[0], c[1]) for c in NEGATIVE_CASES],
        ids=[c[0] for c in NEGATIVE_CASES],
    )
    def test_function_and_cli_agree(self, tmp_path, mutate, case_id: str) -> None:
        payload = make_valid_payload()
        mutate(payload)
        fn_violations = validate(payload)

        src = tmp_path / f"{case_id}.json"
        src.write_text(json.dumps(payload, ensure_ascii=False))
        proc = subprocess.run(
            ["python3", "-m", "akb2.harvest.schema", str(src)],
            capture_output=True,
            text=True,
            env=CLI_ENV,
        )
        assert (len(fn_violations) > 0) == (proc.returncode == CLI_EXIT_INVALID), (
            f"{case_id}: 函数与 CLI 判定不一致 — 函数违规={fn_violations!r} "
            f"CLI rc={proc.returncode}"
        )
