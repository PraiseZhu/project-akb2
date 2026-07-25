"""test_intake_frontmatter_repair.py — F53 存量坏 raw frontmatter 修复 CLI 单测。

覆盖:
    - @ 开头作者坏块 → 修复后可 yaml 解析,正文字节不动
    - 多行作者坏块 → 9 字段不齐(publisher 被撑破行吞掉时) → unrepairable 不动文件
    - 健康文件 → 跳过不动
    - dry-run 默认不写文件
    - --limit 有界
    - 审计 JSONL 落盘

全 fixture,不打真 vault。
"""

from __future__ import annotations

import json

import yaml

from src.akb2.quality.intake.frontmatter_repair import main, scan_and_repair

_BAD_AT_AUTHOR = """---
artifact_type: raw
source_slug: zed-blog
source_url: "https://zed.dev/blog/zed-ai"
source_title: Introducing Zed AI - Zed Blog
source_authors: @nathansobo
source_publisher: zed.dev
source_published_at: 08/20/2024
fetched_at: "2026-07-21T02:18:11Z"
fetched_via: scrapling
---

## 锚点目录

§A_CONTENT_START

Body text stays byte-identical.
"""

_HEALTHY = """---
artifact_type: raw
source_slug: ok-blog
source_url: https://ok.dev/a
source_title: T
source_authors: A
source_publisher: ok.dev
source_published_at: '2026-01-01'
fetched_at: '2026-01-01T00:00:00Z'
fetched_via: defuddle
---

Body.
"""

# @ 开头值炸 YAML(不可解析)且 source_publisher 行整体缺失(逐行提取后不齐 9 字段)
_BAD_MISSING_FIELD = """---
artifact_type: raw
source_slug: broken
source_url: "https://x.com/a"
source_title: T
source_authors: @someone
source_published_at: '2026-01-01'
fetched_at: '2026-01-01T00:00:00Z'
fetched_via: defuddle
---

Body.
"""


class TestScanAndRepair:
    def test_bad_at_author_repaired_body_untouched(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        p = raw_dir / "zed--x--raw--2026-07-21.md"
        p.write_text(_BAD_AT_AUTHOR, encoding="utf-8")
        old_body = _BAD_AT_AUTHOR.split("---\n", 2)[2]

        result = scan_and_repair(raw_dir, apply=True)
        assert result["repaired"] == [str(p)]
        assert result["unrepairable"] == []

        new_text = p.read_text(encoding="utf-8")
        # 修复后 frontmatter 可解析且字段保真
        import re
        m = re.match(r"^---\n(.*?)\n---\n", new_text, re.DOTALL)
        fm = yaml.safe_load(m.group(1))
        assert fm["source_authors"] == "@nathansobo"
        assert fm["source_url"] == "https://zed.dev/blog/zed-ai"
        # 正文字节不动
        assert new_text.split("---\n", 2)[2] == old_body

    def test_healthy_skipped(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        p = raw_dir / "ok--x--raw--2026-01-01.md"
        p.write_text(_HEALTHY, encoding="utf-8")

        result = scan_and_repair(raw_dir, apply=True)
        assert result["healthy"] == 1
        assert result["repaired"] == []
        assert p.read_text(encoding="utf-8") == _HEALTHY  # 一字不动

    def test_missing_field_unrepairable_untouched(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        p = raw_dir / "broken--x--raw--2026-01-01.md"
        p.write_text(_BAD_MISSING_FIELD, encoding="utf-8")

        result = scan_and_repair(raw_dir, apply=True)
        assert result["repaired"] == []
        assert len(result["unrepairable"]) == 1
        assert "source_publisher" in result["unrepairable"][0]["missing"]
        assert p.read_text(encoding="utf-8") == _BAD_MISSING_FIELD  # 不动文件

    def test_dry_run_default_no_write(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        p = raw_dir / "zed--x--raw--2026-07-21.md"
        p.write_text(_BAD_AT_AUTHOR, encoding="utf-8")

        result = scan_and_repair(raw_dir, apply=False)
        assert result["repaired"] == [str(p)]
        assert result["records"][0]["status"] == "would_repair"
        assert p.read_text(encoding="utf-8") == _BAD_AT_AUTHOR  # 未写

    def test_limit_bounds_repairs(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        for i in range(3):
            (raw_dir / f"bad{i}--x--raw--2026-01-01.md").write_text(
                _BAD_AT_AUTHOR, encoding="utf-8"
            )
        result = scan_and_repair(raw_dir, apply=False, limit=2)
        assert len(result["repaired"]) == 2


class TestMainCli:
    def test_cli_dry_run_with_audit(self, tmp_path):
        vault = tmp_path / "vault"
        raw_dir = vault / "raw"
        raw_dir.mkdir(parents=True)
        (raw_dir / "zed--x--raw--2026-07-21.md").write_text(
            _BAD_AT_AUTHOR, encoding="utf-8"
        )
        audit = tmp_path / "audit.jsonl"

        code = main([
            "--vault-root", str(vault),
            "--audit-out", str(audit),
        ])
        assert code == 0
        lines = [json.loads(x) for x in audit.read_text().splitlines()]
        assert lines[0]["mode"] == "dry-run"
        assert lines[0]["repaired_count"] == 1
        assert lines[1]["status"] == "would_repair"

    def test_cli_missing_vault_root_fails_closed(self, monkeypatch):
        monkeypatch.delenv("AKB2_VAULT_ROOT", raising=False)
        assert main([]) == 2

    def test_cli_unrepairable_exit_1(self, tmp_path):
        vault = tmp_path / "vault"
        raw_dir = vault / "raw"
        raw_dir.mkdir(parents=True)
        (raw_dir / "broken--x--raw--2026-01-01.md").write_text(
            _BAD_MISSING_FIELD, encoding="utf-8"
        )
        assert main(["--vault-root", str(vault), "--apply"]) == 1
