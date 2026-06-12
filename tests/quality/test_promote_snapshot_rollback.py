"""SC-5.3 (FR-015): 快照 -> 升格 -> 回滚 sha256 往返全等。

升格前快照全部将改文件 + manifest 至 snapshots/<snapshot_id>/,
python3 -m akb2.quality.promote --rollback <snapshot_id> 可逐字节还原。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256 hex 摘要。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(directory: Path) -> str:
    """目录级聚合 SHA-256。"""
    h = hashlib.sha256()
    files = sorted(
        p for p in directory.rglob("*")
        if p.is_file()
        and p.name != ".DS_Store"
        and p.suffix != ".pyc"
        and "__pycache__" not in p.parts
    )
    for p in files:
        h.update(str(p.relative_to(directory)).encode())
        h.update(sha256_file(p).encode())
    return h.hexdigest()


@pytest.fixture
def snapshot_vault_root(tmp_path: Path) -> Path:
    """创建 fixture vault 用于快照测试。"""
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "wiki" / "sources").mkdir(parents=True)
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "manifests" / "ingest").mkdir(parents=True)

    # 创建一些将被修改的文件
    source_file = vault / "wiki" / "sources" / "test-snapshot.md"
    source_file.write_text("""---
page_status: active
slug: test-snapshot
title: Snapshot Test
---

# Snapshot Test Page

Original content for snapshot testing.
""")

    concept_file = vault / "wiki" / "concepts" / "concept-snapshot.md"
    concept_file.write_text("""---
page_status: active
slug: concept-snapshot
graph_role: leaf
---

## TLDR

Snapshot concept page.

## 图谱关系

| 目标 | 关系 | 强度 | 证据 |
|------|------|------|------|

## 维护记录

| 2026-06-13 | Initial creation | |""")

    index_file = vault / "wiki" / "sources" / "00-Index.md"
    index_file.write_text("""# 00-Index

## §4 blog

### 2026-06

- [[existing-slug]] — Test source · 2026-06-01 · Original content · serves none · basis: manual

## 修订历史

| 2026-06-01 | Initial index | |""")

    return vault


class TestSnapshotRollback:
    """快照-回滚往返完整性。"""

    def test_snapshot_creates_manifest(self, snapshot_vault_root: Path):
        """快照创建 manifest 含所有文件 hash。"""
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import snapshot_before_promote

        # 临时覆盖快照目录到 tmp
        orig_snapshots = promote_mod._SNAPSHOTS_DIR
        try:
            promote_mod._SNAPSHOTS_DIR = snapshot_vault_root / "snapshots"
            affected = [
                snapshot_vault_root / "wiki" / "sources" / "test-snapshot.md",
                snapshot_vault_root / "wiki" / "concepts" / "concept-snapshot.md",
                snapshot_vault_root / "wiki" / "sources" / "00-Index.md",
            ]
            snapshot_id = snapshot_before_promote(snapshot_vault_root, affected)

            assert snapshot_id.startswith("promote-"), f"Bad snapshot ID: {snapshot_id}"

            snapshot_dir = promote_mod._SNAPSHOTS_DIR / snapshot_id
            assert snapshot_dir.exists()

            manifest_path = snapshot_dir / "rollback-manifest.json"
            assert manifest_path.exists()

            manifest = json.loads(manifest_path.read_text())
            assert manifest["snapshot_id"] == snapshot_id
            assert manifest["file_count"] == 3

            # 验证所有文件都备份了
            for rel_path_str in manifest["files"]:
                backed_up = snapshot_dir / rel_path_str
                assert backed_up.exists(), f"Missing backup: {backed_up}"
        finally:
            promote_mod._SNAPSHOTS_DIR = orig_snapshots

    def test_rollback_restores_files_exactly(self, snapshot_vault_root: Path):
        """快照 -> 修改 -> 回滚 -> sha256 全等。"""
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import (
            rollback_snapshot,
            snapshot_before_promote,
        )

        source_file = snapshot_vault_root / "wiki" / "sources" / "test-snapshot.md"
        concept_file = snapshot_vault_root / "wiki" / "concepts" / "concept-snapshot.md"
        index_file = snapshot_vault_root / "wiki" / "sources" / "00-Index.md"

        # 记录原始 hash
        original_source_hash = sha256_file(source_file)
        original_concept_hash = sha256_file(concept_file)
        original_index_hash = sha256_file(index_file)

        # 快照
        orig_snapshots = promote_mod._SNAPSHOTS_DIR
        try:
            promote_mod._SNAPSHOTS_DIR = snapshot_vault_root / "snapshots"
            affected = [source_file, concept_file, index_file]
            snapshot_id = snapshot_before_promote(snapshot_vault_root, affected)

            # 修改文件（模拟升格写入）
            source_file.write_text(source_file.read_text() + "\n## Modified Content\nNew content added.\n")
            concept_file.write_text(concept_file.read_text() + "\n| test-snapshot | source | medium | Rollback test |\n")
            index_file.write_text(index_file.read_text() + "\n- [[test-snapshot]] — Modified entry\n")

            # 验证已修改
            assert sha256_file(source_file) != original_source_hash
            assert sha256_file(concept_file) != original_concept_hash
            assert sha256_file(index_file) != original_index_hash

            # 回滚
            ok = rollback_snapshot(snapshot_id)
            assert ok is True

            # 验证 sha256 全等
            assert sha256_file(source_file) == original_source_hash, (
                "Source file hash mismatch after rollback"
            )
            assert sha256_file(concept_file) == original_concept_hash, (
                "Concept file hash mismatch after rollback"
            )
            assert sha256_file(index_file) == original_index_hash, (
                "Index file hash mismatch after rollback"
            )
        finally:
            promote_mod._SNAPSHOTS_DIR = orig_snapshots

    def test_rollback_nonexistent_snapshot(self, snapshot_vault_root: Path):
        """回滚不存在的快照返回 False。"""
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import rollback_snapshot

        orig_snapshots = promote_mod._SNAPSHOTS_DIR
        try:
            promote_mod._SNAPSHOTS_DIR = snapshot_vault_root / "snapshots"
            ok = rollback_snapshot("nonexistent-snapshot-id")
            assert ok is False
        finally:
            promote_mod._SNAPSHOTS_DIR = orig_snapshots

    def test_snapshot_handles_missing_file(self, snapshot_vault_root: Path):
        """快照时跳过不存在的文件，不报错。"""
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import snapshot_before_promote

        orig_snapshots = promote_mod._SNAPSHOTS_DIR
        try:
            promote_mod._SNAPSHOTS_DIR = snapshot_vault_root / "snapshots"
            affected = [
                snapshot_vault_root / "wiki" / "sources" / "test-snapshot.md",
                snapshot_vault_root / "wiki" / "nonexistent" / "missing.md",
            ]
            snapshot_id = snapshot_before_promote(snapshot_vault_root, affected)

            snapshot_dir = promote_mod._SNAPSHOTS_DIR / snapshot_id
            manifest = json.loads((snapshot_dir / "rollback-manifest.json").read_text())
            # 只有存在的文件被备份
            assert manifest["file_count"] == 1
        finally:
            promote_mod._SNAPSHOTS_DIR = orig_snapshots

    def test_snapshot_pruning(self, snapshot_vault_root: Path):
        """超出 SNAPSHOT_MAX_COUNT(50) 的旧快照被清理。"""
        import src.akb2.quality.promote as promote_mod
        from src.akb2.quality.promote import (
            SNAPSHOT_MAX_COUNT,
            snapshot_before_promote,
        )

        orig_snapshots = promote_mod._SNAPSHOTS_DIR
        try:
            snap_dir = snapshot_vault_root / "snapshots"
            promote_mod._SNAPSHOTS_DIR = snap_dir

            affected = [snapshot_vault_root / "wiki" / "sources" / "test-snapshot.md"]

            # 创建超过上限的快照（每次 snapshot_before_promote 会触发 prune）
            for _ in range(SNAPSHOT_MAX_COUNT + 10):
                snapshot_before_promote(snapshot_vault_root, affected)

            # 统计实际快照目录数（应被 prune 到不超过 50）
            actual_dirs = [d for d in snap_dir.iterdir() if d.is_dir()]
            assert len(actual_dirs) <= SNAPSHOT_MAX_COUNT, (
                f"Should have at most {SNAPSHOT_MAX_COUNT} snapshots after pruning, "
                f"got {len(actual_dirs)}"
            )
        finally:
            promote_mod._SNAPSHOTS_DIR = orig_snapshots
