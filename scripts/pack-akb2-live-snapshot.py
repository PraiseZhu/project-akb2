#!/usr/bin/env python3
"""把当前 vault 的 promoted 笔记打成 Cindy 插件 snapshot.json。

只读扫描 wiki/sources/；不写 vault。默认写到
cindy-plugin/akb2-live/snapshot.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from akb2.loops.paths import get_akb2_root  # noqa: E402
from akb2.retrieval import engine  # noqa: E402

DEFAULT_OUT = REPO / "cindy-plugin" / "akb2-live" / "snapshot.json"


def build_snapshot(root: Path) -> dict:
    sources = root / engine.SOURCES_SUBDIR
    notes: list[dict] = []
    if sources.is_dir():
        for note_path in sorted(sources.rglob("*.md")):
            rel = note_path.relative_to(root).as_posix()
            doc = engine.read_promoted(root, rel)
            if doc is None:
                continue
            notes.append(doc.to_dict())
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包 AKB2 live 插件快照")
    parser.add_argument("--vault-root", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        root = args.vault_root if args.vault_root is not None else get_akb2_root()
    except RuntimeError as exc:
        print(f"vault 根解析失败: {exc}", file=sys.stderr)
        return 3
    payload = build_snapshot(root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(payload['notes'])} notes -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
