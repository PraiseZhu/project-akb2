"""sc-skill-fixtures 验收：prompt 契约 fixture 语料 + 确定性校验（零 LLM）。

change 实现面：
- tests/harvest/fixtures/prompt/ 下 1 个合格 harvest JSON + 每条约束反例各 1
  （concept 缺组件 / 缺因果边 / procedural how<5 / verbatim<60% /
  how_steps.anchor 指向不存在的 atom id）。
- 校验脚本复用 akb2.harvest 的 schema + 车道校验器（不另写第二份校验逻辑）；
  verbatim 占比按 atoms 中 kind∈{command,output,error,diff} 的字符数确定性计算
  （schema.py 无此校验器——verbatim 阈值当前只存在于 SKILL.md，实现方是本 SC）；
  锚点可解析性逐条查 atom id 存在，且 id 重复即拒（不取第一个）。

expect 覆盖（类 6 exact 阈值与锚点判定 + 类 8 红集==预测集）：
① concept/procedural 车道判定阈值边界三例（恰好/差一不足/差一超出）
② verbatim 60% 边界三例（恰好 60% / 59.9% / 60.1%），整数算术判定无浮点边界
③ §A/E/H/M/Z 五锚点各「存在 / 缺失 / 重复出现」三例
④ 阈值常量全仓只有一处定义（身份断言：fixtures 校验器与实现引用同一常量对象）
⑤ 文档—代码阈值一致性硬断言（P1）：从 SKILL.md 正则提取四阈值，
   与实现常量逐字相等；变异验证：文档数值改一位 → 提取值必变（断言必红）

fixture 语料同时是 skill 文档的可执行示例：按 SKILL.md ③ 写出的合格 JSON
必须真过校验器（holds 加固类 7）。
"""

from __future__ import annotations

import functools
import itertools
import json
import re
from pathlib import Path

import pytest
from akb2.harvest.schema import (
    LANE_CONCEPT,
    LANE_CONCEPT_MIN_CAUSAL_EDGES,
    LANE_CONCEPT_MIN_COMPONENTS,
    LANE_PROCEDURAL,
    LANE_PROCEDURAL_MIN_HOW_STEPS,
    run_lane_check,
)
from akb2.harvest.schema import (
    validate as schema_validate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "prompt"

SKILL_MD_PATH = Path(
    "/Users/praise/AI-Agent/Claude/capabilities/source/skills/"
    "claude-active/akb2-harvest/SKILL.md"
)

# ---------------------------------------------------------------------------
# 确定性校验器（复用 schema + lane；verbatim 与锚点唯一性为本 SC 新增，
# 纯确定性、零 LLM。阈值常量与 SKILL.md 的逐字一致性由 expect ⑤ 锁定）
# ---------------------------------------------------------------------------

# verbatim 类 atoms（SKILL.md ③ 逐字）
VERBATIM_KINDS = ("command", "output", "error", "diff")

# verbatim 占比阈值（百分比整数；expect ⑤ 断言与 SKILL.md 正则提取值逐字相等）
VERBATIM_MIN_PERCENT = 60

# §A/E/H/M/Z 五锚点（SKILL.md ③「供 [raw §X] 锚点」的 X）
ANCHOR_IDS = ("A", "E", "H", "M", "Z")


def _verbatim_char_counts(payload: dict) -> tuple[int, int]:
    """atoms 中 verbatim 类字符数与全部字符数（按 text 的字符数，确定性）。"""
    atoms = payload.get("atoms")
    if not isinstance(atoms, list):
        return 0, 0
    verbatim = 0
    total = 0
    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        text = atom.get("text")
        if not isinstance(text, str):
            continue
        total += len(text)
        if atom.get("kind") in VERBATIM_KINDS:
            verbatim += len(text)
    return verbatim, total


def compute_verbatim_ratio(payload: dict) -> float:
    """verbatim 字符数占比（0..1）；无 atoms 时返回 0.0（fail-closed）。"""
    verbatim, total = _verbatim_char_counts(payload)
    return verbatim / total if total else 0.0


def verbatim_pass(payload: dict) -> bool:
    """verbatim 占比 ≥60% 判定：整数算术（v*100 >= 60*t），无浮点边界问题。"""
    verbatim, total = _verbatim_char_counts(payload)
    return verbatim * 100 >= VERBATIM_MIN_PERCENT * total


def verbatim_violation(payload: dict) -> list[str]:
    """verbatim 不足 → 违规消息（含期望值+实际值）；足则空。"""
    if verbatim_pass(payload):
        return []
    verbatim, total = _verbatim_char_counts(payload)
    pct = round(100 * verbatim / total, 1) if total else 0.0
    return [f"verbatim 占比期望 ≥{VERBATIM_MIN_PERCENT}%,实际 {pct}%"]


def anchor_uniqueness_violations(payload: dict) -> list[str]:
    """atoms[].id 唯一性：重复 id 即拒（不取第一个，锚点解析歧义）。"""
    atoms = payload.get("atoms")
    if not isinstance(atoms, list):
        return []
    seen: dict[str, int] = {}
    violations: list[str] = []
    for idx, atom in enumerate(atoms):
        if not isinstance(atom, dict):
            continue
        aid = atom.get("id")
        if not isinstance(aid, str):
            continue
        if aid in seen:
            violations.append(
                f"atoms[{idx}].id: 期望唯一,实际与 atoms[{seen[aid]}] 重复 {aid!r}"
            )
        else:
            seen[aid] = idx
    return violations


def validate_fixture(payload: dict) -> list[str]:
    """全约束校验（fail-closed 逐级）：schema → lane → verbatim → 锚点 id 唯一。

    第一道失败的闸门返回其全部违规并停止（lane 判定假设 schema 已过；
    红集==预测集由测试按闸门归属断言）。
    """
    violations = list(schema_validate(payload))
    if violations:
        return violations
    ok, _code, msg = run_lane_check(payload)
    if not ok:
        return [msg]
    violations = verbatim_violation(payload)
    if violations:
        return violations
    return anchor_uniqueness_violations(payload)


def load_fixture(name: str) -> dict:
    """按名加载 fixture JSON（fixtures 目录即 skill 文档的可执行示例语料）。"""
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _base(
    *,
    lane_hint: str,
    components: list[str],
    causal_edges: list[dict],
    how_steps: list[dict],
    atoms: list[dict] | None = None,
) -> dict:
    if atoms is None:
        atoms = [
            {"id": "A", "kind": "command", "text": "python3 -m akb2.quality.pipeline --dry-run"},
            {"id": "E", "kind": "output", "text": "exit 0: 全部通过"},
            {"id": "H", "kind": "diff", "text": "gate.py 增补 components 存在性校验"},
            {"id": "M", "kind": "context", "text": "此前 runner 未校验 components 存在性"},
            {"id": "Z", "kind": "command", "text": "python3 -m pytest tests/test_gate.py -q"},
        ]
    return {
        "schema_version": "1",
        "session_id": "sess-fixtures-boundary-test",
        "title": "阈值边界构造 fixture",
        "lane_hint": lane_hint,
        "components": components,
        "causal_edges": causal_edges,
        "atoms": atoms,
        "how_steps": how_steps,
        "source_meta": {"tool": "claude-code"},
    }


def _how(n: int) -> list[dict]:
    """n 条 how 步骤，anchor 依次指向存在的 atom id（A/E/H/M/Z 循环，够 6 条）。"""
    anchor_pool = ("A", "E", "H", "M", "Z", "A")
    return [
        {
            "anchor": anchor_pool[i],
            "provenance": "session log",
            "cmd": f"cmd-{i}",
            "expect": "ok",
        }
        for i in range(n)
    ]


def _concept_payload(components: int, edges: int, *, how_steps: int = 3) -> dict:
    return _base(
        lane_hint=LANE_CONCEPT,
        components=[f"c{i}" for i in range(components)],
        causal_edges=[{"from": "c0", "to": "c1"} for _ in range(edges)],
        how_steps=_how(how_steps),
    )


def _proc_payload(how_steps: int) -> dict:
    return _base(
        lane_hint=LANE_PROCEDURAL,
        components=[],
        causal_edges=[],
        how_steps=_how(how_steps),
    )


# ---------------------------------------------------------------------------
# 合格 fixture 全绿 + 反例红集==预测集（加固类 7/8）
# ---------------------------------------------------------------------------

# 每条反例：(fixture 名, 精确期望红集消息)。红集==预测集 = 实测违规清单
# 与期望消息逐字相等（非子串匹配）——不同缺口的消息含各自的「期望值+实际值」
# 段，互不匹配；交叉互换后断言必红（定位能力硬证据，lead 裁决补强）。
VALID_FIXTURE = "valid_concept"
NEGATIVE_FIXTURES = [
    (
        "concept_missing_components",
        "lane_hint=concept 证据不足:components 期望 ≥3 个,实际 1 个"
        "(how_steps=5/5 cmd 非空)",
    ),
    (
        "concept_missing_causal_edges",
        "lane_hint=concept 证据不足:causal_edges 期望 ≥1 条,实际 0 条"
        "(how_steps=5/5 cmd 非空)",
    ),
    (
        "procedural_how_lt5",
        "lane_hint=procedural 证据不足:how_steps 期望 ≥5 条,实际 4 条"
        "(components=3,causal_edges=1)",
    ),
    ("verbatim_lt60", "verbatim 占比期望 ≥60%,实际 40.0%"),
    (
        "anchor_missing_atom",
        "how_steps[0].anchor: 期望指向存在的 atom id,实际 'NOPE'",
    ),
]


@functools.cache
def _fixture_violations(name: str) -> tuple[str, ...]:
    """同一反例在交叉互换断言中复用一次确定性校验结果。"""
    return tuple(validate_fixture(load_fixture(name)))


def test_valid_fixture_passes_all_checks() -> None:
    """合格 fixture 全绿：schema + lane + verbatim + 锚点唯一 零违规。

    该 fixture 即 SKILL.md ③ 的可执行示例——按文档写出的合格 JSON
    必须真过校验器（holds 加固类 7）。
    """
    payload = load_fixture(VALID_FIXTURE)
    assert validate_fixture(payload) == []


@pytest.mark.parametrize(
    ("name", "expected_message"),
    NEGATIVE_FIXTURES,
    ids=[f[0] for f in NEGATIVE_FIXTURES],
)
def test_negative_fixture_red_exactly_predicted(name: str, expected_message: str) -> None:
    """反例恰好红在预测的那条约束：实测违规清单与期望消息逐字相等。

    精确点名「期望值+实际值」段（非子串匹配）：若反例因别的原因红
    （JSON 解析错 / schema 版本 / 拼写 / 歧义分支），消息对不上必红。
    """
    violations = _fixture_violations(name)
    assert violations == (expected_message,), (
        f"{name}: 预测红集 {expected_message!r},实际 {violations!r}"
    )


# 交叉互换对：任一反例 × 其它反例的期望消息（全排列 i≠j，共 5×4=20 对；
# 三 lane 反例两两互换的 3 对是 lead 点名的核心，全部反向 = 定位能力硬证据）
_CROSS_SWAP_PAIRS = [
    (a[0], b[1]) for a, b in itertools.permutations(NEGATIVE_FIXTURES, 2)
]


@pytest.mark.parametrize(
    ("name", "foreign_message"),
    _CROSS_SWAP_PAIRS,
    ids=[f"{n}<-{m[:26]}" for n, m in _CROSS_SWAP_PAIRS],
)
def test_negative_fixture_messages_mutually_exclusive(
    name: str, foreign_message: str
) -> None:
    """交叉互换：任一反例的实测红集与其它反例的期望消息互不匹配。

    防「期望消息整体同构化」退化（如全部写成同一个歧义串）：任一互换
    命中即红。三 lane 反例两两互换的 3 对含在内。
    """
    violations = _fixture_violations(name)
    assert violations != (foreign_message,), (
        f"{name}: 实测红集与 {foreign_message!r} 交叉命中,定位能力缺失"
    )


# ---------------------------------------------------------------------------
# ① 车道判定阈值边界三例（恰好命中 / 差一不足 / 差一超出）
# ---------------------------------------------------------------------------


class TestLaneThresholdBoundaries:
    """概念 vs 流程车道的判定阈值：边界三例断言取舍方向唯一且与实现一致。

    边界值一律引用 schema 常量构造（同一常量对象，防两处漂移——expect ④）。
    """

    def test_components_exact_minimum_passes(self) -> None:
        payload = _concept_payload(LANE_CONCEPT_MIN_COMPONENTS, 1)
        ok, code, _ = run_lane_check(payload)
        assert ok is True and code == 0

    def test_components_one_below_fails(self) -> None:
        payload = _concept_payload(LANE_CONCEPT_MIN_COMPONENTS - 1, 1)
        ok, code, _ = run_lane_check(payload)
        assert ok is False and code != 0

    def test_components_one_above_passes(self) -> None:
        payload = _concept_payload(LANE_CONCEPT_MIN_COMPONENTS + 1, 1)
        ok, code, _ = run_lane_check(payload)
        assert ok is True and code == 0

    def test_causal_edges_exact_minimum_passes(self) -> None:
        payload = _concept_payload(3, LANE_CONCEPT_MIN_CAUSAL_EDGES)
        ok, code, _ = run_lane_check(payload)
        assert ok is True and code == 0

    def test_causal_edges_zero_fails(self) -> None:
        payload = _concept_payload(3, LANE_CONCEPT_MIN_CAUSAL_EDGES - 1)
        ok, code, _ = run_lane_check(payload)
        assert ok is False and code != 0

    def test_causal_edges_one_above_passes(self) -> None:
        payload = _concept_payload(3, LANE_CONCEPT_MIN_CAUSAL_EDGES + 1)
        ok, code, _ = run_lane_check(payload)
        assert ok is True and code == 0

    def test_how_steps_exact_minimum_passes(self) -> None:
        payload = _proc_payload(LANE_PROCEDURAL_MIN_HOW_STEPS)
        ok, code, _ = run_lane_check(payload)
        assert ok is True and code == 0

    def test_how_steps_one_below_fails(self) -> None:
        payload = _proc_payload(LANE_PROCEDURAL_MIN_HOW_STEPS - 1)
        ok, code, _ = run_lane_check(payload)
        assert ok is False and code != 0

    def test_how_steps_one_above_passes(self) -> None:
        payload = _proc_payload(LANE_PROCEDURAL_MIN_HOW_STEPS + 1)
        ok, code, _ = run_lane_check(payload)
        assert ok is True and code == 0


# ---------------------------------------------------------------------------
# ② verbatim 60% 边界三例（恰好 60% / 59.9% / 60.1%）
# ---------------------------------------------------------------------------


def _verbatim_boundary_payload(verbatim_chars: int, nonverbatim_chars: int) -> dict:
    """构造 verbatim 类（command）与 judgment 类（非 verbatim）字符数精确的 payload。"""
    atoms = [
        {"id": "A", "kind": "command", "text": "x" * verbatim_chars},
        {"id": "E", "kind": "judgment", "text": "y" * nonverbatim_chars},
    ]
    return _base(
        lane_hint=LANE_CONCEPT,
        components=["c0", "c1", "c2"],
        causal_edges=[{"from": "c0", "to": "c1"}],
        how_steps=_how(3),
        atoms=atoms,
    )


@pytest.mark.parametrize(
    ("verbatim_chars", "nonverbatim_chars", "expected"),
    [
        (600, 400, True),    # 恰好 60% → 通过（≥ 含边界）
        (599, 401, False),   # 59.9% → 差一不足 → 拒
        (601, 399, True),    # 60.1% → 差一超出 → 通过
    ],
    ids=["exact_60", "below_59_9", "above_60_1"],
)
def test_verbatim_ratio_boundary(
    verbatim_chars: int, nonverbatim_chars: int, expected: bool
) -> None:
    """verbatim 占比 60% 边界：取舍方向唯一且与实现判定一致。"""
    payload = _verbatim_boundary_payload(verbatim_chars, nonverbatim_chars)
    assert verbatim_pass(payload) is expected


def test_verbatim_ratio_value_computed_not_estimated() -> None:
    """占比按字符数确定性计算（非自估）：精确数值断言。"""
    payload = _verbatim_boundary_payload(600, 400)
    assert compute_verbatim_ratio(payload) == pytest.approx(0.6)
    payload = _verbatim_boundary_payload(599, 401)
    assert compute_verbatim_ratio(payload) == pytest.approx(0.599)


# ---------------------------------------------------------------------------
# ③ §A/E/H/M/Z 五锚点：存在 / 缺失 / 重复出现 各三例
# ---------------------------------------------------------------------------


class TestFiveAnchors:
    """锚点解析：缺失即拒、重复即拒、不取第一个（§A/E/H/M/Z 五锚点）。"""

    @pytest.mark.parametrize("anchor_id", ANCHOR_IDS)
    def test_anchor_present_resolves(self, anchor_id: str) -> None:
        """存在：atoms 含该 id，how_steps 指向它 → 可解析，全约束绿。"""
        payload = load_fixture(VALID_FIXTURE)
        payload["how_steps"] = [
            {"anchor": anchor_id, "provenance": "log", "cmd": "c", "expect": "ok"}
        ]
        assert validate_fixture(payload) == []

    @pytest.mark.parametrize("anchor_id", ANCHOR_IDS)
    def test_anchor_missing_rejected(self, anchor_id: str) -> None:
        """缺失：anchor 指向不存在的 atom id → 拒（schema 锚点可解析性）。"""
        payload = load_fixture(VALID_FIXTURE)
        payload["how_steps"] = [
            {"anchor": "NOPE", "provenance": "log", "cmd": "c", "expect": "ok"}
        ]
        violations = validate_fixture(payload)
        assert violations
        assert any("anchor" in v and "NOPE" in v for v in violations)

    @pytest.mark.parametrize("anchor_id", ANCHOR_IDS)
    def test_anchor_duplicate_rejected(self, anchor_id: str) -> None:
        """重复出现：atoms 内同一 id 定义两次 → 拒（不取第一个）。"""
        payload = load_fixture(VALID_FIXTURE)
        payload["atoms"].append(
            {"id": anchor_id, "kind": "context", "text": "重复定义的锚"}
        )
        violations = validate_fixture(payload)
        assert violations
        assert any("重复" in v and anchor_id in v for v in violations)


# ---------------------------------------------------------------------------
# ④ 阈值常量全仓只有一处定义（身份断言，防两处漂移）
# ---------------------------------------------------------------------------

_THRESHOLD_CONSTANTS = (
    "LANE_PROCEDURAL_MIN_HOW_STEPS",
    "LANE_CONCEPT_MIN_COMPONENTS",
    "LANE_CONCEPT_MIN_CAUSAL_EDGES",
)


def _definition_sites(pattern: re.Pattern, root: Path) -> list[str]:
    """root 下所有 .py 中命中赋值定义的 (相对路径:行号) 列表。"""
    hits: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{py.relative_to(REPO_ROOT)}:{lineno}")
    return hits


@pytest.mark.parametrize("constant", _THRESHOLD_CONSTANTS)
def test_lane_constants_defined_exactly_once(constant: str) -> None:
    """LANE_* 阈值常量在全仓（src+tests）只有一处定义：schema.py。"""
    pattern = re.compile(rf"^\s*{constant}\s*=")
    hits = [
        site
        for root in (REPO_ROOT / "src", REPO_ROOT / "tests")
        for site in _definition_sites(pattern, root)
    ]
    assert len(hits) == 1, f"{constant} 定义点应为 1 处,实际 {hits}"
    assert "schema.py" in hits[0], f"{constant} 应定义于 schema.py,实际 {hits}"


def test_verbatim_constant_defined_exactly_once() -> None:
    """VERBATIM_MIN_PERCENT 在全仓（src+tests）只有一处定义：本文件。"""
    pattern = re.compile(r"^\s*VERBATIM_MIN_PERCENT\s*=")
    hits = [
        site
        for root in (REPO_ROOT / "src", REPO_ROOT / "tests")
        for site in _definition_sites(pattern, root)
    ]
    assert len(hits) == 1, f"VERBATIM_MIN_PERCENT 定义点应为 1 处,实际 {hits}"
    assert "test_prompt_fixtures.py" in hits[0]


def test_validator_references_same_constant_objects() -> None:
    """身份断言：校验器与实现引用同一常量对象（import 即同一绑定）。"""
    import akb2.harvest.schema as schema_mod

    assert schema_mod.LANE_CONCEPT_MIN_COMPONENTS is LANE_CONCEPT_MIN_COMPONENTS
    assert schema_mod.LANE_CONCEPT_MIN_CAUSAL_EDGES is LANE_CONCEPT_MIN_CAUSAL_EDGES
    assert (
        schema_mod.LANE_PROCEDURAL_MIN_HOW_STEPS is LANE_PROCEDURAL_MIN_HOW_STEPS
    )


# ---------------------------------------------------------------------------
# ⑤ 文档—代码阈值一致性硬断言（P1，GPT 质询 rev153-lead 自查）
# ---------------------------------------------------------------------------

_THRESHOLD_PATTERNS = {
    "verbatim": r"verbatim 占比：≥(\d+)%",
    "components": r"concept 车道）：≥(\d+) 命名组件",
    "causal_edges": r"≥(\d+) 因果边",
    "how_steps": r"procedural 车道）：≥(\d+) 条 how 步骤",
}


@pytest.fixture(scope="module")
def skill_md() -> str:
    if not SKILL_MD_PATH.exists():
        pytest.skip("SKILL.md 未生成（本测试依赖真实工件）")
    return SKILL_MD_PATH.read_text(encoding="utf-8")


def extract_thresholds(skill_md: str) -> dict[str, int]:
    """从 SKILL.md 正则提取四阈值数值（读文档取值，非硬编码）。"""
    extracted: dict[str, int] = {}
    for key, pattern in _THRESHOLD_PATTERNS.items():
        match = re.search(pattern, skill_md)
        assert match, f"SKILL.md 缺少阈值条款: {key!r}（模式 {pattern!r}）"
        extracted[key] = int(match.group(1))
    return extracted


def test_doc_code_thresholds_verbatim_equal(skill_md: str) -> None:
    """文档阈值与实现常量逐字相等：verbatim 60% + 车道三阈值。"""
    extracted = extract_thresholds(skill_md)
    assert extracted["verbatim"] == VERBATIM_MIN_PERCENT, (
        f"SKILL.md verbatim 阈值 {extracted['verbatim']}% 与实现 "
        f"VERBATIM_MIN_PERCENT={VERBATIM_MIN_PERCENT} 漂移"
    )
    assert extracted["components"] == LANE_CONCEPT_MIN_COMPONENTS
    assert extracted["causal_edges"] == LANE_CONCEPT_MIN_CAUSAL_EDGES
    assert extracted["how_steps"] == LANE_PROCEDURAL_MIN_HOW_STEPS


def test_threshold_extraction_sensitive_to_doc_change(skill_md: str) -> None:
    """变异验证：SKILL.md 任一数值改掉一位 → 提取值随之变化（断言必红）。

    证明提取是「读文档取值再比对」，不是「都是 60」这种巧合相等。
    """
    mutated = skill_md.replace("≥60%", "≥61%")
    assert extract_thresholds(mutated)["verbatim"] == 61
    mutated = skill_md.replace("≥3 命名组件", "≥4 命名组件")
    assert extract_thresholds(mutated)["components"] == 4
    mutated = skill_md.replace("≥1 因果边", "≥2 因果边")
    assert extract_thresholds(mutated)["causal_edges"] == 2
    mutated = skill_md.replace("≥5 条 how 步骤", "≥6 条 how 步骤")
    assert extract_thresholds(mutated)["how_steps"] == 6
