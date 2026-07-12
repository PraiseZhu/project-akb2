"""test_f47_synthesis_assembler.py — F47 1对多合成提炼装配层。

用户裁决(2026-07-12):同主题簇多篇"过准入但死于地板/评分"的薄文,聚合装配
为一份合成 raw,走**完全相同**的管线全链重考——1对多发生在 raw 装配层,
判定链零改动。

红线锁:逐字搬运零编造(成员正文原文进合成 raw);每节带出处(标题/URL/
成员文件名);复用 archive_raw 的锚点断言 fail-closed;有界 2-5 成员;
同簇同 topic 幂等(确定性 identity);准入被拒的成员不进建议池。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.akb2.quality.synthesis_assembler import (
    SYNTHESIS_MAX_MEMBERS,
    SYNTHESIS_MIN_MEMBERS,
    _parse_member,
    _synthesis_identity,
    assemble_synthesis,
    suggest_clusters,
)

_PARA = (
    "The scheduler dispatches tasks to workers when the queue fills. "
    "Each worker validates input before execution and reports back."
)


def _mk_member(tmp_path: Path, name: str, title: str, url: str,
               paras: int = 6) -> Path:
    body = "\n\n".join(f"{_PARA} Section {i} detail." for i in range(paras))
    text = (
        "---\n"
        'artifact_type: "raw"\n'
        f'source_slug: "{name.split("--")[0]}"\n'
        f'source_url: "{url}"\n'
        f'source_title: "{title}"\n'
        "---\n\n"
        "## 锚点目录\n\n"
        "| 锚点 | 段落 |\n|---|---|\n| §A | 开头 |\n| §Z | 结尾 |\n\n"
        "## §A_CONTENT_START\n\n"
        f"{body}\n\n"
        "## §Z_CONTENT_END\n\n"
        "end.\n"
    )
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class TestMemberParsing:
    def test_strips_old_anchor_skeleton(self, tmp_path: Path) -> None:
        p = _mk_member(tmp_path, "src-a--111--raw--2026-07-10.md", "T1", "https://a/x")
        mem = _parse_member(p)
        assert "§A" not in mem["body"] and "锚点目录" not in mem["body"]
        assert _PARA.split(".")[0] in mem["body"], "正文逐字保留"

    def test_no_frontmatter_failclosed(self, tmp_path: Path) -> None:
        p = tmp_path / "bad--raw--2026-07-10.md"
        p.write_text("no fm", encoding="utf-8")
        with pytest.raises(ValueError):
            _parse_member(p)


class TestAssemble:
    def _members(self, tmp_path: Path, n: int) -> list[Path]:
        return [
            _mk_member(tmp_path, f"src-{i}--{i:03d}--raw--2026-07-10.md",
                       f"Agent memory design part {i}", f"https://ex{i}.com/p")
            for i in range(n)
        ]

    def test_assembles_with_provenance_and_anchors(self, tmp_path: Path) -> None:
        out = tmp_path / "rawout"
        out.mkdir()
        r = assemble_synthesis(self._members(tmp_path, 3), "agent-memory", out)
        assert r.success, r.error
        text = Path(r.output_path).read_text(encoding="utf-8")
        # 出处三要素:标题/URL/成员文件名(溯源链)
        assert "## 来源1: Agent memory design part 0" in text
        assert "https://ex1.com/p" in text
        assert "src-2--002--raw--2026-07-10.md" in text
        # 新锚点骨架(archive_raw 注入,必需锚全命中)
        assert "锚点目录" in text and "§A" in text and "§Z" in text
        # 正文逐字(零改写)
        assert _PARA.split(".")[0] in text
        # frontmatter 合成标识
        assert "synthesis_assembler" in text and "synthesis-agent-memory" in text

    def test_member_bounds(self, tmp_path: Path) -> None:
        out = tmp_path / "o"
        out.mkdir()
        with pytest.raises(ValueError):
            assemble_synthesis(self._members(tmp_path, 1), "t-x", out)
        with pytest.raises(ValueError):
            assemble_synthesis(
                self._members(tmp_path, SYNTHESIS_MAX_MEMBERS + 1), "t-x", out)
        assert SYNTHESIS_MIN_MEMBERS == 2 and SYNTHESIS_MAX_MEMBERS == 5

    def test_topic_kebab_enforced(self, tmp_path: Path) -> None:
        out = tmp_path / "o"
        out.mkdir()
        with pytest.raises(ValueError):
            assemble_synthesis(self._members(tmp_path, 2), "Bad Topic!", out)

    def test_identity_deterministic(self, tmp_path: Path) -> None:
        """同簇同 topic → 同 URI(幂等,重跑不产生第二份身份)。"""
        ms = self._members(tmp_path, 2)
        a = _synthesis_identity([_parse_member(p) for p in ms], "t-a")
        b = _synthesis_identity([_parse_member(p) for p in reversed(ms)], "t-a")
        assert a == b


class TestSuggestClusters:
    def _ledger(self, tmp_path: Path, entries: list[tuple[str, str]]) -> Path:
        led = tmp_path / "ledger.jsonl"
        lines = []
        for h, kind in entries:
            h = h + "0" * 10
            lines.append({"content_hash": h, "event": "intake_raw", "details": {}})
            if kind == "floor":
                lines.append({"content_hash": h, "event": "abort",
                              "details": {"error": "完整性地板未过"}})
            elif kind == "adm":
                lines.append({"content_hash": h, "event": "abort",
                              "details": {"reason": "准入未通过: how_tendency"}})
            elif kind == "fin":
                lines.append({"content_hash": h, "event": "finalize", "details": {}})
        led.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        return led

    def test_groups_same_topic_excludes_admission_rejects(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        raw.mkdir()
        f1 = _mk_member(raw, "a--001--raw--2026-07-10.md",
                        "Agent memory architecture deep dive", "https://a/1")
        f2 = _mk_member(raw, "b--002--raw--2026-07-10.md",
                        "Agent memory architecture patterns", "https://b/2")
        f3 = _mk_member(raw, "c--003--raw--2026-07-10.md",
                        "CSS container queries explained", "https://c/3")
        f4 = _mk_member(raw, "d--004--raw--2026-07-10.md",
                        "Agent memory architecture notes", "https://d/4")
        fake_hash = {f1.name: "h1", f2.name: "h2", f3.name: "h3", f4.name: "h4"}
        led = self._ledger(tmp_path, [("h1", "floor"), ("h2", "floor"),
                                      ("h3", "floor"), ("h4", "adm")])
        clusters = suggest_clusters(
            led, raw, compute_hash_fn=lambda p: fake_hash[Path(p).name] + "0" * 10)
        assert len(clusters) == 1
        assert set(clusters[0]) == {f1.name, f2.name}, (
            "同主题两篇成簇;CSS 篇不同主题不入;准入被拒的 h4 不进池")

    def test_finalized_not_in_pool(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        raw.mkdir()
        f1 = _mk_member(raw, "a--001--raw--2026-07-10.md",
                        "Agent memory architecture deep dive", "https://a/1")
        f2 = _mk_member(raw, "b--002--raw--2026-07-10.md",
                        "Agent memory architecture patterns", "https://b/2")
        fake = {f1.name: "h1", f2.name: "h2"}
        led = self._ledger(tmp_path, [("h1", "fin"), ("h2", "floor")])
        clusters = suggest_clusters(
            led, raw, compute_hash_fn=lambda p: fake[Path(p).name] + "0" * 10)
        assert clusters == [], "已入库的不进合成池;单篇不成簇"

    def test_synthesis_raw_not_reclustered(self, tmp_path: Path) -> None:
        """合成 raw 自身不进建议池(防套娃)。"""
        raw = tmp_path / "raw"
        raw.mkdir()
        f1 = _mk_member(raw, "synthesis-x--001--raw--2026-07-10.md",
                        "主题综述 x", "synthesis://x/1")
        _mk_member(raw, "b--002--raw--2026-07-10.md",
                   "Agent memory architecture", "https://b/2")
        led = self._ledger(tmp_path, [("h1", "floor"), ("h2", "floor")])
        clusters = suggest_clusters(
            led, raw, compute_hash_fn=lambda p: ("h1" if "synthesis" in str(p) else "h2") + "0" * 10)
        assert all(f1.name not in c for c in clusters)
