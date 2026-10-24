#!/usr/bin/env python3
"""mutation_harness — 全项目反向变异单一执行器（g4 / sc-harness-mutation）。

流程：
    按 --mutation <name> 从内置变异注册表取一处源码变异并应用 → 跑 --tests 指定的
    测试集 → 解析实际红集（junitxml）→ 与 --predicted-red 逐条 exact 比对 →
    无论成败在 finally 还原源文件并复核 sha256 与变异前相等。

红集标识格式（--predicted-red 两种等价写法）：
    "<junit classname>.<junit name>"，例如
    tests.loops.test_ledger_lifeline.test_duplicate_event_returns_false
    或 pytest nodeid "tests/loops/test_ledger_lifeline.py::test_duplicate_event_returns_false"
    （解析时统一归一化为 classname.name 再比对）

变异目标（Mutation.file）支持两种形态：
    repo 相对路径（锚到 REPO_ROOT），或仓外绝对路径（如 live skill 源，
    host-local 真相源）。两类目标的存在性检查都推迟到该条目被 --mutation
    实际选中时才做（run_once 内 fail-closed）：注册表是全项目单一事实源，
    拆分链前节上后置条目的仓内目标尚未落地，CI 上仓外目标必然缺失——
    启动期拦截会让无关变异调用被拖累失败。启动期只校验结构性约束
    （禁改路径 / anchor != replacement / 键名）。

机器可读输出（stdout，行首 token）：
    SOURCE_RESTORED path=<repo相对路径> sha256=<变异前摘要>
    SOURCE_RESTORE_FAILED path=... expected=<摘要> actual=<摘要>
    MUTATION_RED_SET_MATCH actual=<n> predicted=<n>
    MUTATION_RED_SET_MISMATCH actual=<n> predicted=<n> only_actual=[...] only_predicted=[...]
    RED_ACTUAL <红集id>    （每个实际红测试一行，诊断用）

退出码：
    0  红集 exact 匹配（非空）且源文件已还原
    1  红集不匹配（源文件已还原）
    2  执行器自身错误：未知变异 / 锚点漂移 / pytest 启动失败或超时 /
       收集 0 个测试 / 实际红集为空（0 红不当过）/ junit 未生成或不可解析 /
       还原后 sha256 不符。细节见 stderr。

边界（g4 派工包）：注册表条目禁指 src/akb2/quality/，启动时注册表校验兜底。
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# g4 派工包禁做路径：注册表任何条目不得指向该前缀之下
FORBIDDEN_PREFIX = "src/akb2/quality/"

# 具名预算常量（宪法 P4）：pytest 子进程默认超时
DEFAULT_PYTEST_TIMEOUT_SEC = 600

EXIT_MATCH = 0
EXIT_MISMATCH = 1
EXIT_ERROR = 2


class HarnessError(RuntimeError):
    """执行器自身错误（区别于"红集不匹配"这种正常判定结果）。"""


@dataclass(frozen=True)
class Mutation:
    """一处源码变异：在 file 中把唯一出现的 anchor 字节串替换为 replacement。"""

    name: str
    file: str  # repo 相对路径，或仓外绝对路径（live skill 等外部真相源）
    anchor: str
    replacement: str
    description: str


def resolve_mutation_file(file: str) -> Path:
    """变异目标路径归一化：绝对路径原样返回，相对路径锚到 REPO_ROOT。"""
    p = Path(file)
    return p if p.is_absolute() else REPO_ROOT / p


# ---------------------------------------------------------------------------
# 内置变异注册表（全项目反向变异的单一事实源；新增条目按同构 dataclass 追加）
# ---------------------------------------------------------------------------

REGISTRY: dict[str, Mutation] = {
    "ledger_dedup_disabled": Mutation(
        name="ledger_dedup_disabled",
        file="src/akb2/loops/ledger.py",
        anchor="found = True",
        replacement="found = False",
        description="幂等去重判定永不命中：重复事件不再被识别，账本出现重复行",
    ),
    # live skill 源在仓外（~/.claude/skills symlink 的指向目标）；用绝对路径
    # 引用，不复制进仓。锚点是 SKILL.md 里唯一的 CLI 兜底命令模块路径。
    "skill-routing-broken-path": Mutation(
        name="skill-routing-broken-path",
        file=str(
            Path.home()
            / "AI-Agent"
            / "Claude"
            / "capabilities"
            / "source"
            / "skills"
            / "claude-active"
            / "akb-search"
            / "SKILL.md"
        ),
        anchor="python3 -m akb2.retrieval.cli",
        replacement="python3 -m akb2.retrieval.broken",
        description=(
            "akb-search skill 的 CLI 兜底命令指向不存在模块：路由测试必须红"
        ),
    ),
    # sc-engine-readonly：引擎读路径被改为「读后顺手写一份」——只读契约
    # 测试的字节级树 hash 断言必须红（两条入口各自的 no_write 测试）。
    "retrieval-readonly-open": Mutation(
        name="retrieval-readonly-open",
        file="src/akb2/retrieval/engine.py",
        anchor="text = note_path.read_text(encoding=\"utf-8\")",
        replacement=(
            "text = note_path.read_text(encoding=\"utf-8\")\n"
            "            note_path.parent.joinpath(\".mutation-readonly-write\").write_text(\"x\")"
        ),
        description=(
            "引擎扫描 promoted 笔记时向 vault 写入标记文件：只读契约测试必红"
        ),
    ),
    # sc-mcp-import-boundary-runtime：绕过静态 AST 断言的三条路径之一——
    # 函数体内动态 import 写管线模块。静态轨看不见，运行期 meta_path 审计必红。
    "mcp-dynamic-import-bypass": Mutation(
        name="mcp-dynamic-import-bypass",
        file="toolbox/cc-akb2-retrieval/mcp_server/server.py",
        anchor="from akb2.retrieval import engine",
        replacement=(
            "import importlib as _il; _il.import_module('akb2.quality.llm_router')\n"
            "    from akb2.retrieval import engine"
        ),
        description=(
            "server 工具函数内动态 import akb2.quality.llm_router（写管线模块）："
            "静态 AST 边界轨看不见、运行期 import 审计必红"
        ),
    ),
    # sc-mcp-readonly-smoke：server 数据面被改为可写——stdio 冒烟测试的
    # 字节级不变断言必红。
    "mcp-readonly-open": Mutation(
        name="mcp-readonly-open",
        file="toolbox/cc-akb2-retrieval/mcp_server/server.py",
        anchor="results = engine.search(",
        replacement=(
            "import pathlib as _p; "
            "_p.Path(str(res[\"vault_root\"]) + \"/.mutation-mcp-write\").write_text(\"x\")\n"
            "    results = engine.search("
        ),
        description=(
            "server 检索工具向 vault 写标记文件：stdio 冒烟只读断言必红"
        ),
    ),
    # sc-e2e-helper-contract（c 格）：noop-proof 的指纹断言变异——在采样
    # 前往被采样目录 touch 一个新文件，零写证明必须红且差异集恰为该文件。
    # 注入点：run_dual_channels 开头（前后两次指纹之间）。
    "e2e-noop-touch": Mutation(
        name="e2e-noop-touch",
        file="scripts/e2e_noop_proof.py",
        anchor="def run_dual_channels(root: Path, query: str, limit: int) -> None:",
        replacement=(
            "def run_dual_channels(root: Path, query: str, limit: int) -> None:\n"
            "    Path(root / \".mutation-noop-touch\").write_text(\"x\")  # 变异注入：非法写入"
        ),
        description=(
            "在前后指纹之间向 vault 写一个新文件：noop-proof 零写断言必红"
        ),
    ),
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_registry() -> list[str]:
    """返回注册表问题清单（空列表 = 合法）。在 main 里启动即查，fail-closed。

    禁改校验对绝对路径同样生效：先归一化到绝对路径，再判是否落在仓内
    FORBIDDEN_PREFIX 之下（防止用仓外前缀字符串绕过 startswith 检查）。

    存在性检查不在启动期做（无论仓内相对路径还是仓外绝对路径）：注册表是
    全项目单一事实源，后置 PR 尚未落地时其仓内目标文件在本节 checkout 上
    必然缺失——若启动期拦截，会让拆分链前节上*所有*变异调用（包括只依赖
    ledger 等已落地文件的基线条目）都被无关条目拖累成 exit 2。
    仓外绝对路径（如 live skill 源）同理：host-local 真相源在 CI / 其他
    机器上必然缺失。两类条目的存在性检查统一推迟到 run_once 里、且仅在该
    条目被 --mutation 实际选中时才做（缺失时 fail-closed，报可诊断错误）。
    """
    problems: list[str] = []
    for mut in REGISTRY.values():
        target = resolve_mutation_file(mut.file)
        if mut.file.startswith(FORBIDDEN_PREFIX) or target.resolve().is_relative_to(
            (REPO_ROOT / FORBIDDEN_PREFIX).resolve()
        ):
            problems.append(f"{mut.name}: 目标在禁改路径 {mut.file}")
        if mut.anchor == mut.replacement:
            problems.append(f"{mut.name}: anchor 与 replacement 相同")
    return problems


def apply_mutation(target: Path, mut: Mutation) -> None:
    """把 mut 应用到 target（字节级替换，要求锚点恰好出现一次）。

    锚点计数 != 1 视为源码漂移，拒绝应用——宁可失败也不改错位置。
    """
    data = target.read_bytes()
    anchor = mut.anchor.encode("utf-8")
    count = data.count(anchor)
    if count != 1:
        raise HarnessError(
            f"变异 {mut.name} 锚点在 {mut.file} 中出现 {count} 次（要求恰好 1 次），源码已漂移"
        )
    mutated = data.replace(anchor, mut.replacement.encode("utf-8"), 1)
    if mutated == data:
        raise HarnessError(f"变异 {mut.name} 应用后内容未变化")
    target.write_bytes(mutated)


def _run_pytest(
    tests: list[str], junit: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *tests,
        "--junitxml",
        str(junit),
        "--tb=no",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    try:
        return subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"pytest 超时（{timeout}s）: {' '.join(tests)}") from exc


def parse_red_set(junit: Path) -> set[str]:
    """从 junitxml 解析实际红集（failure/error 的 testcase）。

    收集 0 个测试时抛 HarnessError：空测试集会让"空红集==空预测"静默成立，
    这是必须显式失败的场景（宪法 P4 失败可见），不是匹配成功。
    """
    if not junit.is_file():
        raise HarnessError(f"junitxml 未生成: {junit}（pytest 未跑到会话结束）")
    # 用 stdlib ET 而非 defusedxml：输入是我们自己拉起的 pytest 子进程写出的
    # junitxml（自有 tempdir、非不可信来源），且 defusedxml 不在 pyproject 钉死
    # 依赖集内，为防御不存在的威胁引入新依赖反而破坏依赖可复现性。
    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError as exc:
        raise HarnessError(f"junitxml 解析失败: {junit}: {exc}") from exc

    red: set[str] = set()
    total = 0
    for testcase in root.iter("testcase"):
        total += 1
        if any(testcase.find(tag) is not None for tag in ("failure", "error")):
            classname = testcase.get("classname") or ""
            name = testcase.get("name") or ""
            red.add(f"{classname}.{name}")
    if total == 0:
        raise HarnessError("测试集收集到 0 个测试，拒绝空集静默匹配")
    return red


def _normalize_test_id(test_id: str) -> str:
    """把 pytest nodeid 归一化为 junit classname.name 形态。

    tests/loops/test_ledger_lifeline.py::test_duplicate_event_returns_false
        → tests.loops.test_ledger_lifeline.test_duplicate_event_returns_false

    参数化 / 类内测试的 :: 后各段以 . 连接，与 pytest junit 的
    classname（模块路径点化 + 类名）与 name（含 [param]）拼接规则一致。
    已是 classname.name 形态（无 ::）的条目原样返回。
    """
    if "::" not in test_id:
        return test_id
    path, *rest = test_id.split("::")
    module = path.replace("/", ".").removesuffix(".py")
    return ".".join([module, *rest])


def parse_predicted_red(raw: str) -> set[str]:
    """逗号分隔的预测红集 → set（空白条目丢弃；空串 = 预测无红，
    但实际红集为空时无论预测如何都按执行器错误处理，见 run_once）。

    每条接受 junit classname.name 或 pytest nodeid（path::test）两种等价写法，
    统一归一化后比对，与 parse_red_set 的输出同一命名空间。
    """
    return {
        _normalize_test_id(part.strip()) for part in raw.split(",") if part.strip()
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def run_once(args: argparse.Namespace) -> int:
    """执行一次完整变异周期：应用 → 跑测试 → 比对 → finally 还原 + sha256 复核。"""
    mut = REGISTRY.get(args.mutation)
    if mut is None:
        raise HarnessError(
            f"未知变异 '{args.mutation}'（可用: {', '.join(sorted(REGISTRY))}）"
        )
    target = resolve_mutation_file(mut.file)
    if not target.is_file():
        raise HarnessError(
            f"变异 {mut.name} 目标文件不存在: {mut.file}"
            "（仓外路径是 host-local 真相源，本机缺失时无法执行；"
            "仓内路径缺失说明该条目所属 PR 尚未落地到当前 checkout）"
        )
    original = target.read_bytes()
    sha_pre = _sha256_bytes(original)

    restore_ok = False
    actual: set[str] = set()
    predicted: set[str] = set()
    try:
        apply_mutation(target, mut)
        print(f"[mutation-harness] 已应用变异 {mut.name} -> {mut.file}", file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="mutation-harness-") as tmpdir:
            junit = Path(tmpdir) / "junit.xml"
            proc = _run_pytest(list(args.tests), junit, args.timeout)
            print(
                f"[mutation-harness] pytest rc={proc.returncode} tests={args.tests}",
                file=sys.stderr,
            )
            actual = parse_red_set(junit)
        predicted = parse_predicted_red(args.predicted_red)
    finally:
        # 任何退出路径（含异常传播）都必须走到这里：还原 + sha256 复核
        target.write_bytes(original)
        sha_post = _sha256_bytes(target.read_bytes())
        restore_ok = sha_post == sha_pre
        if restore_ok:
            print(f"SOURCE_RESTORED path={mut.file} sha256={sha_pre}")
        else:
            print(
                f"SOURCE_RESTORE_FAILED path={mut.file} expected={sha_pre} actual={sha_post}"
            )

    for test_id in sorted(actual):
        print(f"RED_ACTUAL {test_id}")

    if not actual:
        # 实际红集为空 = 变异没被任何测试观察到。无论预测是否为空都
        # fail-closed：0 红 + 空 predicted 的「空==空」是假绿，不是匹配成功
        # （与 parse_red_set 拒绝空收集同一宪法口径：失败必须可见）。
        print(
            "MUTATION_RED_SET_EMPTY actual=0 predicted="
            f"{len(predicted)}（变异未触发任何红测试，测试集对该变异无鉴别力）",
            file=sys.stderr,
        )
        return EXIT_ERROR

    matched = actual == predicted
    if matched:
        print(f"MUTATION_RED_SET_MATCH actual={len(actual)} predicted={len(predicted)}")
    else:
        only_actual = sorted(actual - predicted)
        only_predicted = sorted(predicted - actual)
        print(
            f"MUTATION_RED_SET_MISMATCH actual={len(actual)} predicted={len(predicted)}"
            f" only_actual={only_actual} only_predicted={only_predicted}"
        )

    if not restore_ok:
        return EXIT_ERROR
    return EXIT_MATCH if matched else EXIT_MISMATCH


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mutation_harness",
        description="全项目反向变异执行器：应用一处变异 → 跑测试 → 红集 exact 比对 → 还原源文件",
    )
    parser.add_argument("--mutation", required=True, help="内置变异注册表中的名称")
    parser.add_argument(
        "--tests",
        required=True,
        nargs="+",
        help="pytest 测试选择器（文件 / nodeid / 目录，一个或多个）",
    )
    parser.add_argument(
        "--predicted-red",
        required=True,
        help=(
            "预测红集（逗号分隔；每条支持 junit classname.name 或 pytest nodeid"
            " path::test 两种等价写法；空串 = 预测无红，"
            "但实际红集为空时执行器报错而非判定匹配）"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_PYTEST_TIMEOUT_SEC,
        help=f"pytest 超时秒数（默认 {DEFAULT_PYTEST_TIMEOUT_SEC}）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    problems = _validate_registry()
    if problems:
        for problem in problems:
            print(f"[mutation-harness] 注册表非法: {problem}", file=sys.stderr)
        return EXIT_ERROR
    try:
        return run_once(args)
    except HarnessError as exc:
        print(f"[mutation-harness] {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
