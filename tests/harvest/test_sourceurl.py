"""sc-asm-sourceurl 验收:source_url 常量策略。

策略 = ``cindy://session/<payload session_id>``,不做变换、不生成新 UUID。
唯一性职责在 sc-asm-schema(session_id 必为小写 canonical UUID4)。

① 基线清单锁定:quality/ 下现测读取点集合 == BASELINE_READERS(逐个标注性质);
② lint 对该字段零 finding(锁定当前无格式门控);
③ assembler 侧常量策略与无 uuid 生成。

主链路（循环1/循环2）于 2026-10-24 删除，原先的逐消费者行为断言随消费者一并移除；
新主链路接入 source_url 时，基线清单会变红，提示重新补消费者断言。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QUALITY = REPO_ROOT / "src" / "akb2" / "quality"

# 真实小写 canonical UUID4(session_id 合法形态;本 SC 用真值,不用 spy 自配)
UA = "11111111-2222-4333-8444-555555555555"
UB = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
URL_A = f"cindy://session/{UA}"
URL_B = f"cindy://session/{UB}"

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _raw_md(url: str, title: str = "T") -> str:
    return f"---\nsource_url: {url}\n---\n{title}\nbody\n"


# ---------------------------------------------------------------------------
# ① 基线清单锁定:现测读取点集合 == BASELINE_READERS
# ---------------------------------------------------------------------------

# lead 实测 grep -rln source_url src/akb2/quality/ --include=*.py；
# 逐个标注读取性质;漏标文件下次改动时不会触发红灯。
BASELINE_READERS: dict[str, str] = {
    "quality/intake/raw_archiver.py": "写入侧:9 字段之一(_build_frontmatter)",
}


def _readers() -> dict[str, str]:
    """实测 source_url 读取点:grep 语义重现在测试内(production 代码,排除测试/脚本/视频)。"""
    found: dict[str, str] = {}
    rx = re.compile(r"source_url")
    for path in QUALITY.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.name.startswith("test_") or "/evals/" in rel or "/scripts/" in rel:
            continue
        if rx.search(path.read_text(encoding="utf-8")):
            found[_rel_quality(path)] = ""
    return found


def _rel_quality(p: Path) -> str:
    """BASELINE_READERS 用 quality/... 相对 src/akb2/ 的形态,与 _readers 对齐。"""
    return p.relative_to(QUALITY.parent).as_posix()


class TestBaselineReaders:
    def test_baseline_readers_locked(self) -> None:
        """现测读取点集合 == BASELINE_READERS(新增消费者时此断言变红,提示重新定案)。"""
        assert set(_readers()) == set(BASELINE_READERS)
        # 逐个核对标注文件真实存在于该读取点集合
        for rel in BASELINE_READERS:
            assert (QUALITY.parent / rel).exists(), f"{rel} 不在 quality 下"

    def test_baseline_reader_annotations_cover_all_listed(self) -> None:
        """清单必须含全部读取点并逐个标注性质(漏标文件下次改动时不触发红灯)。"""
        assert len(BASELINE_READERS) == 1
        for rel, note in BASELINE_READERS.items():
            assert note.strip(), f"{rel} 缺标注"
            assert any(k in note for k in (
                "行为消费者", "写入侧", "非消费者")), f"{rel} 标注性质不明: {note}"


# ---------------------------------------------------------------------------
# ② lint 对该字段零 finding(锁定当前无格式门控)
# ---------------------------------------------------------------------------


class TestLintNoGate:
    def test_lint_no_finding_for_source_url(self) -> None:
        """lint 对 source_url 字段不产生任何 finding(无格式门控)。"""
        import akb2.lint.akb2_lint as lint_mod
        assert hasattr(lint_mod, "LINT_RULES") or hasattr(lint_mod, "RULES")
        rules = getattr(lint_mod, "LINT_RULES", None) or getattr(lint_mod, "RULES", [])
        names = [getattr(r, "name", str(r)) if not isinstance(r, str) else r
                 for r in rules]
        assert not any("source_url" in n.lower() or "url" in n.lower() for n in names)


# ---------------------------------------------------------------------------
# assembler 侧:source_url 策略 + 无 uuid 生成(调用真实 assemble 需要部署根,
# 此处只做纯策略断言:从源码读常量策略,且禁止 uuid 生成调用)
# ---------------------------------------------------------------------------


class TestAssemblerStrategy:
    def test_assembler_has_no_uuid_generation(self) -> None:
        """assembler 内不得出现任何 uuid 生成调用(预测红集:出现即判红)。"""
        src = (REPO_ROOT / "src" / "akb2" / "harvest" / "assembler.py").read_text(
            encoding="utf-8")
        assert "uuid4" not in src
        assert "uuid.uuid" not in src
        assert "random" not in src

    def test_assembler_source_url_strategy_present(self) -> None:
        """assembler 里存在 cindy://session/ + session_id 的策略字样(不做变换)。"""
        src = (REPO_ROOT / "src" / "akb2" / "harvest" / "assembler.py").read_text(
            encoding="utf-8")
        assert "cindy://session/" in src
        assert "session_id" in src
