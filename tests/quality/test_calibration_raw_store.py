"""test_calibration_raw_store.py — P0 raw 留存基建测试。

覆盖:
    - 按 sha 落盘 <dir>/<sha>.md，内容=完整 raw（含 frontmatter），返 path + file_sha
    - sha 幂等跳过（文件已存在不重写、不重算 file hash）
    - 开关关闭（dir=None/空）→ no-op
    - 写失败（OSError）不致命（返 error，不抛异常）

[N4] sha 语义：raw_content_sha256 是 chrome-stripped body hash（去重 funnel key 用），
    非落盘文件 digest；落盘文件完整性另存 raw_file_sha256（hash of 完整 raw）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.akb2.quality.calibration import (
    _persist_raw_content,
    _resolve_raw_store_dir,
)

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_BODY = "Real body about prompt caching mechanism with named components."


def _body_sha(body: str) -> str:
    """模拟 _compute_raw_content_sha256 的输出（chrome-stripped body hash）。"""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# P0：raw 留存 helper
# ---------------------------------------------------------------------------


class TestPersistRawContent:
    def test_writes_sha_named_file_with_full_raw(self, tmp_path):
        """按 sha 落盘 <dir>/<sha>.md，内容=完整 raw（含 frontmatter），返 path + file_sha。"""
        raw = (
            "---\n"
            "artifact_type: raw\n"
            "fetched_at: 2026-07-02T00:00:00Z\n"
            "---\n\n"
            f"{_BODY}\n"
        )
        sha = _body_sha(_BODY)  # body hash（模拟 _compute_raw_content_sha256 输出）
        store = tmp_path / "raw-store"

        path, file_sha, err = _persist_raw_content(raw, sha, str(store))

        assert err is None
        assert path == str(store / f"{sha}.md")
        # 落盘内容=完整 raw（frontmatter + body），不是只有 body
        assert Path(path).read_text(encoding="utf-8") == raw
        # [N4] file_sha 是落盘文件（完整 raw）的 digest，区别于 body hash（sha）
        assert file_sha == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert file_sha != sha  # body hash ≠ file hash（file 含 frontmatter）

    def test_idempotent_skip_when_sha_file_exists(self, tmp_path):
        """sha 幂等：文件已存在 → 不重写、不重算 file_sha，内容保持首次写入。"""
        # 两个 raw 同 body 不同 fetched_at → 同 body sha（dedup 为 1 的前提）
        raw1 = f"---\nfetched_at: t1\n---\n\n{_BODY}\n"
        raw2 = f"---\nfetched_at: t2\n---\n\n{_BODY}\n"
        sha = _body_sha(_BODY)
        store = tmp_path / "raw-store"

        path1, file_sha1, err1 = _persist_raw_content(raw1, sha, str(store))
        # 同 sha 第二次调（文件已存在 → 跳过，不覆盖 raw1）
        path2, file_sha2, err2 = _persist_raw_content(raw2, sha, str(store))

        assert err1 is None and err2 is None
        assert path1 == path2  # 同 sha → 同路径
        assert file_sha1 is not None  # 首次写算 file hash
        assert file_sha2 is None  # 幂等跳过不重算 file hash
        # 文件内容仍是首次的 raw1（未被 raw2 覆盖）
        assert Path(path2).read_text(encoding="utf-8") == raw1

    def test_disabled_when_dir_none_or_empty(self):
        """开关关闭（dir=None/空）→ no-op，不写任何文件。"""
        sha = _body_sha(_BODY)
        # dir=None
        path, file_sha, err = _persist_raw_content(_BODY, sha, None)
        assert path is None and file_sha is None and err is None
        # dir=空串
        path2, file_sha2, err2 = _persist_raw_content(_BODY, sha, "")
        assert path2 is None and file_sha2 is None and err2 is None
        # raw/sha 缺失同效
        assert _persist_raw_content(None, sha, "/tmp/x") == (None, None, None)
        assert _persist_raw_content(_BODY, None, "/tmp/x") == (None, None, None)
        # _resolve_raw_store_dir: persist_raw=False → None（开关关闭语义）
        assert _resolve_raw_store_dir("/x/y.json", persist_raw=False) is None
        assert _resolve_raw_store_dir("", persist_raw=True) is None
        # persist_raw=True → <report_out 同级>/raw-store
        assert _resolve_raw_store_dir("/x/y.json", persist_raw=True) == "/x/raw-store"

    def test_oserror_nonfatal(self, tmp_path, monkeypatch):
        """写失败（OSError）不致命：返 error，不抛异常（管线继续）。"""
        raw = _BODY
        sha = _body_sha(_BODY)
        store = tmp_path / "raw-store"

        def _raise(*a, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(Path, "write_text", _raise)

        path, file_sha, err = _persist_raw_content(raw, sha, str(store))
        assert path is None
        assert file_sha is None
        assert err is not None and "OSError" in err
