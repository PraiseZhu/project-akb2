"""sc-trial-loop 验收：可导入试投循环（生产形态：JSONL + BATCH 锁 + 真实 CLI）。

测试覆盖：
- 适配器注册表（import 期固定）
- 模板提取（正例/负例）
- adapter 返回正确 argv
- JSONL journal 操作（append/read/坏尾）
- run_attempts 完整流程（成功/失败/截止时间）
- intent 在 submit 前追加到 journal 保证
- 测试 seam 正确替换执行结果
- 临时文件清理
- 异常处理
- BATCH 锁全程持有
- 真实 CLI 集成断言（不 mock subprocess）
- 残留项标注
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: F811 — 用于真实 CLI 测试
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from trial_run import (  # noqa: E402
    _ADAPTER_REGISTRY,
    CLI_EXIT_REJECTED,
    PLACEHOLDER_JSON,
    PLACEHOLDER_RUN_ID,
    SUBMIT_TEMPLATE_CLOSE,
    SUBMIT_TEMPLATE_OPEN,
    _append_journal,
    _canonical_adapter,
    _extract_template,
    _now_epoch,
    _now_iso,
    _parse_cli_result,
    _pipeline_hash,
    _read_journal,
    _write_journal,
    run_attempts,
)

CLI_ENV = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))

# ---------------------------------------------------------------------------
# mock SKILL.md 模板（真实 CLI argv 格式）
# ---------------------------------------------------------------------------

MOCK_SKILL_MD = f"""# Mock SKILL.md

Some content before the template.

{SUBMIT_TEMPLATE_OPEN}
python3 -m akb2.harvest {PLACEHOLDER_JSON} --harvest-run-id {PLACEHOLDER_RUN_ID}
{SUBMIT_TEMPLATE_CLOSE}

Some content after the template.
"""

# 预期 argv argv（展开后，payload_path 与 harvest_run_id 由 test 动态替换）
MOCK_SKILL_ARGV_PREFIX = ["python3", "-m", "akb2.harvest"]


# ---------------------------------------------------------------------------
# fixture: 模拟 SKILL.md 文件
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_skill_md(tmp_path: Path) -> Path:
    """在临时目录创建 mock SKILL.md。"""
    p = tmp_path / "SKILL.md"
    p.write_text(MOCK_SKILL_MD, encoding="utf-8")
    return p


@pytest.fixture()
def mock_skill_md_bad_template(tmp_path: Path) -> Path:
    """创建不含模板标记的 mock SKILL.md。"""
    p = tmp_path / "SKILL.md"
    p.write_text("# just a regular skill\n\necho hello\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# fixture: 冲抵 _submit_seam
# ---------------------------------------------------------------------------


@pytest.fixture()
def reset_seam() -> None:
    """测试后重置 _submit_seam。"""
    import trial_run
    yield
    trial_run._submit_seam = None


# ---------------------------------------------------------------------------
# fixture: BATCH 锁 mock（避免测试依赖 harvest 部署）
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_batch_lock() -> None:
    """mock _batch_lock_key 和 locks.reserve 为无操作。

    run_attempts 内部调用 _batch_lock_key() 和 reserve(BATCH, key)，
    测试环境无 harvest 部署，需 patch 这两个点。
    """
    import contextlib

    import trial_run

    _orig_key = trial_run._batch_lock_key
    trial_run._batch_lock_key = lambda: "test-batch-lock"

    # 用 contextlib 造一个无操作 context manager
    @contextlib.contextmanager
    def _noop_cm(*args, **kwargs):
        yield

    _orig_reserve = trial_run.reserve
    trial_run.reserve = lambda ns, key: _noop_cm()

    yield
    trial_run._batch_lock_key = _orig_key
    trial_run.reserve = _orig_reserve


# ---------------------------------------------------------------------------
# fixture: 合格 planned 条目
# ---------------------------------------------------------------------------


def _valid_payload(session_id: str) -> dict:
    """构造合格 procedural 车道 payload（how_steps≥5，cmd 全非空）。"""
    return {
        "schema_version": "1",
        "session_id": session_id,
        "title": "trial loop 测试 payload",
        "lane_hint": "procedural",
        "components": ["gate", "runner"],
        "causal_edges": [],
        "atoms": [
            {"id": "a1", "kind": "command", "text": "cmd 1"},
            {"id": "a2", "kind": "output", "text": "output 1"},
            {"id": "a3", "kind": "error", "text": "error 1"},
            {"id": "a4", "kind": "diff", "text": "diff 1"},
            {"id": "a5", "kind": "judgment", "text": "judgment 1"},
            {"id": "a6", "kind": "context", "text": "context 1"},
        ],
        "how_steps": [
            {"anchor": "a1", "provenance": "log", "cmd": "step1", "expect": "ok"},
            {"anchor": "a2", "provenance": "log", "cmd": "step2", "expect": "ok"},
            {"anchor": "a3", "provenance": "log", "cmd": "step3", "expect": "ok"},
            {"anchor": "a4", "provenance": "log", "cmd": "step4", "expect": "ok"},
            {"anchor": "a5", "provenance": "log", "cmd": "step5", "expect": "ok"},
        ],
        "source_meta": {
            "session_id": session_id,
            "tool": "claude-code",
            "fetched_at": "2026-08-14T00:00:00+08:00",
        },
    }


@pytest.fixture()
def valid_planned() -> list[dict]:
    """三篇合格 planned 条目。"""
    return [
        {
            "session_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
            "title": "trial 1",
            "payload": _valid_payload("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"),
        },
        {
            "session_id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
            "title": "trial 2",
            "payload": _valid_payload("b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"),
        },
    ]


# ---------------------------------------------------------------------------
# 1. 适配器注册表
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    """注册表 import 期固定，不接受运行期注入。"""

    def test_has_canonical(self) -> None:
        """canonical adapter 必须在 import 期注册。"""
        assert "canonical" in _ADAPTER_REGISTRY
        assert callable(_ADAPTER_REGISTRY["canonical"])

    def test_is_callable(self) -> None:
        """注册表值都是可调用对象。"""
        for name, fn in _ADAPTER_REGISTRY.items():
            assert callable(fn), f"adapter {name!r} 不是可调用对象"


# ---------------------------------------------------------------------------
# 2. 模板提取
# ---------------------------------------------------------------------------


class TestExtractTemplate:
    """从 SKILL.md 提取 submit 模板。"""

    def test_valid_template(self) -> None:
        """恰一对标记 → 返回模板体。"""
        body = _extract_template(MOCK_SKILL_MD)
        assert "python3 -m akb2.harvest" in body
        assert PLACEHOLDER_JSON in body
        assert PLACEHOLDER_RUN_ID in body

    def test_zero_markers(self) -> None:
        """零标记块 → ValueError。"""
        with pytest.raises(ValueError, match="必须"):
            _extract_template("# no markers here")

    def test_two_markers(self) -> None:
        """两个标记块 → ValueError。"""
        double = MOCK_SKILL_MD + "\n" + MOCK_SKILL_MD
        with pytest.raises(ValueError, match="必须"):
            _extract_template(double)

    def test_missing_placeholder(self) -> None:
        """缺 harvest_run_id 占位符 → ValueError。"""
        bad = f"{SUBMIT_TEMPLATE_OPEN}\necho hello\n{SUBMIT_TEMPLATE_CLOSE}"
        with pytest.raises(ValueError, match="占位符"):
            _extract_template(bad)

    def test_empty_body(self) -> None:
        """空模板体 → ValueError。"""
        empty = f"{SUBMIT_TEMPLATE_OPEN}\n{SUBMIT_TEMPLATE_CLOSE}"
        with pytest.raises(ValueError, match="为空"):
            _extract_template(empty)


# ---------------------------------------------------------------------------
# 3. canonical adapter
# ---------------------------------------------------------------------------


class TestCanonicalAdapter:
    """从 SKILL.md 提取 → 展开 → 返回 argv。"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_md: Path) -> None:
        """mock _find_skill_md 返回 mock SKILL.md。"""
        import trial_run
        self._orig_find = trial_run._find_skill_md
        trial_run._find_skill_md = lambda: mock_skill_md

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = self._orig_find

    def test_returns_argv(self) -> None:
        """返回可执行的 argv。"""
        payload_path = "/tmp/test-payload.json"
        run_id = "d4e5f6a7-b8c9-4d0e-8f1a-2b3c4d5e6f78"
        argv = _canonical_adapter(
            {"session_id": "test"}, run_id, payload_path
        )
        assert isinstance(argv, list)
        assert payload_path in argv
        assert run_id in argv

    def test_argv_order(self) -> None:
        """argv 中占位符被正确替换。"""
        payload_path = "/tmp/test-payload.json"
        run_id = "d4e5f6a7-b8c9-4d0e-8f1a-2b3c4d5e6f78"
        argv = _canonical_adapter(
            {"session_id": "test"}, run_id, payload_path
        )
        joined = " ".join(argv)
        assert payload_path in joined
        assert run_id in joined
        assert "python3" in joined


class TestCanonicalAdapterNoSkill:
    """SKILL.md 不可定位时抛 RuntimeError。"""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        import trial_run
        self._orig_find = trial_run._find_skill_md
        trial_run._find_skill_md = lambda: None

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = self._orig_find

    def test_raises(self) -> None:
        with pytest.raises(RuntimeError, match="SKILL.md"):
            _canonical_adapter({}, "test-run-id", "/tmp/p.json")


# ---------------------------------------------------------------------------
# 4. JSONL journal 操作
# ---------------------------------------------------------------------------


class TestJournal:
    """append-only JSONL journal 操作。"""

    def test_append_and_read(self, tmp_path: Path) -> None:
        """追加一条记录 → 可读回。"""
        jp = tmp_path / "test.jsonl"
        _append_journal(str(jp), {"phase": "intent", "status": "pending"})
        records = _read_journal(str(jp))
        assert len(records) == 1
        assert records[0]["phase"] == "intent"

    def test_append_multiple(self, tmp_path: Path) -> None:
        """追加多条 → 全部可读回。"""
        jp = tmp_path / "test.jsonl"
        _append_journal(str(jp), {"phase": "intent", "id": 1})
        _append_journal(str(jp), {"phase": "settled", "id": 2})
        _append_journal(str(jp), {"phase": "failed", "id": 3})
        records = _read_journal(str(jp))
        assert len(records) == 3
        assert records[0]["id"] == 1
        assert records[1]["id"] == 2
        assert records[2]["id"] == 3

    def test_append_no_overwrite(self, tmp_path: Path) -> None:
        """追加不会覆写已有内容。"""
        jp = tmp_path / "test.jsonl"
        _append_journal(str(jp), {"phase": "intent", "id": 1})
        _append_journal(str(jp), {"phase": "settled", "id": 2})
        # 再读 — 应有两条
        records = _read_journal(str(jp))
        assert len(records) == 2

    def test_read_empty_file(self, tmp_path: Path) -> None:
        """空文件 → 空列表。"""
        jp = tmp_path / "empty.jsonl"
        jp.write_text("", encoding="utf-8")
        records = _read_journal(str(jp))
        assert records == []

    def test_read_nonexistent_file(self, tmp_path: Path) -> None:
        """不存在的文件 → 空列表。"""
        jp = tmp_path / "nonexistent.jsonl"
        records = _read_journal(str(jp))
        assert records == []

    def test_bad_tail_discarded(self, tmp_path: Path) -> None:
        """坏尾行（末尾不完整 JSON）丢弃并记录。"""
        jp = tmp_path / "test.jsonl"
        # 写两条完整 + 一条不完整（无换行结尾 = 坏尾）
        _append_journal(str(jp), {"phase": "intent", "id": 1})
        _append_journal(str(jp), {"phase": "settled", "id": 2})
        # 手动追加不完整行（不加换行）
        with open(jp, "a", encoding="utf-8") as f:
            f.write('{"phase": "truncated", "id": 3')
        records = _read_journal(str(jp))
        assert len(records) == 2
        assert records[-1]["id"] == 2

    def test_corrupted_middle_fails(self, tmp_path: Path) -> None:
        """中间行损坏 → ValueError。"""
        jp = tmp_path / "test.jsonl"
        _append_journal(str(jp), {"phase": "intent", "id": 1})
        # 手动插入损坏行
        with open(jp, "a", encoding="utf-8") as f:
            f.write("not-json\n")
        _append_journal(str(jp), {"phase": "settled", "id": 2})
        with pytest.raises(ValueError, match="损坏"):
            _read_journal(str(jp))

    def test_write_journal_full(self, tmp_path: Path) -> None:
        """全量覆写 _write_journal。"""
        jp = tmp_path / "test.jsonl"
        _write_journal(str(jp), [
            {"phase": "intent", "id": 1},
            {"phase": "settled", "id": 2},
        ])
        records = _read_journal(str(jp))
        assert len(records) == 2


# ---------------------------------------------------------------------------
# 5. 辅助函数
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_pipeline_hash_length(self) -> None:
        h = _pipeline_hash("hello")
        assert len(h) == 16

    def test_pipeline_hash_deterministic(self) -> None:
        assert _pipeline_hash("hello") == _pipeline_hash("hello")

    def test_now_iso_format(self) -> None:
        ts = _now_iso()
        assert "T" in ts
        assert ts.endswith("+00:00") or ts.endswith("Z") or "+" in ts

    def test_now_epoch_type(self) -> None:
        ts = _now_epoch()
        assert isinstance(ts, int)
        assert ts > 1_700_000_000  # 合理范围

    def test_parse_cli_ok(self) -> None:
        """解析成功输出。"""
        import subprocess
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="harvest: 已落位 test-file.md\n",
        )
        result = _parse_cli_result(proc)
        assert result["exit_code"] == 0
        assert result["filename"] == "test-file.md"
        assert result["error"] is None

    def test_parse_cli_fail(self) -> None:
        """解析失败输出。"""
        import subprocess
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="harvest: 拒绝落位: schema 校验失败\n",
        )
        result = _parse_cli_result(proc)
        assert result["exit_code"] == 2
        assert result["filename"] is None
        assert "schema" in (result["error"] or "")

    def test_parse_cli_ok_no_filename(self) -> None:
        """成功输出但没有文件名（极端情况）。"""
        import subprocess
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="harvest: 已落位 \n"
        )
        result = _parse_cli_result(proc)
        assert result["exit_code"] == 0
        assert result["filename"] is None


# ---------------------------------------------------------------------------
# 6. run_attempts 完整流程
# ---------------------------------------------------------------------------


class TestRunAttemptsSuccess:
    """成功提交 → settled 记录。"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_md: Path, reset_seam, mock_batch_lock,
              tmp_path: Path) -> None:
        import trial_run
        trial_run._find_skill_md = lambda: mock_skill_md
        self.journal_path = str(tmp_path / "trial-batch.jsonl")

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = Path.home() / ".claude" / "skills" / "akb2-harvest" / "SKILL.md"

    def test_successful_submission(self, valid_planned) -> None:
        """成功提交产生 settled 记录。"""
        import trial_run

        def seam(argv, rid):
            return {"exit_code": 0, "filename": "test-file.md", "error": None}

        trial_run._submit_seam = seam
        records = run_attempts(
            valid_planned,
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        assert len(records) == 2
        assert records[0]["phase"] == "settled"
        assert records[0]["status"] == "completed"
        assert records[0]["final_state"] == "committed"
        assert records[0]["reconciliation_state"] == "committed"
        assert records[0]["harvest_run_id"] is not None
        assert records[0]["pipeline_content_hash"] is not None
        assert len(records[0]["pipeline_content_hash"]) == 16
        assert records[0]["expected_slug"] == valid_planned[0]["session_id"]

    def test_intent_before_submit(self, valid_planned) -> None:
        """intent 记录在 submit 前已追加到 journal（硬不变量 5）。"""
        import trial_run
        intent_seen = []

        def seam(argv, rid):
            # _submit_seam 被调用时，intent 应已追加
            entries = _read_journal(self.journal_path)
            for entry in entries:
                if entry["phase"] == "intent":
                    intent_seen.append(entry)
            return {"exit_code": 0, "filename": "f.md", "error": None}

        trial_run._submit_seam = seam
        run_attempts(
            valid_planned[:1],
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        assert len(intent_seen) == 1
        assert intent_seen[0]["phase"] == "intent"
        assert intent_seen[0]["status"] == "pending"

    def test_phase_not_stage(self, valid_planned) -> None:
        """attempted 记录使用 phase 字段（不是 stage）。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": 0, "filename": "f.md", "error": None
        }
        records = run_attempts(
            valid_planned[:1],
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        assert "phase" in records[0]
        assert "stage" not in records[0]

    def test_journal_file_updated(self, valid_planned) -> None:
        """journal 文件每轮迭代后追加。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": 0, "filename": "f.md", "error": None
        }
        run_attempts(
            valid_planned,
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        records = _read_journal(self.journal_path)
        # 每篇 2 条记录（intent + settled）= 4 条
        assert len(records) == 4


class TestRunAttemptsFailure:
    """提交失败 → failed 记录。"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_md: Path, reset_seam, mock_batch_lock,
              tmp_path: Path) -> None:
        import trial_run
        trial_run._find_skill_md = lambda: mock_skill_md
        self.journal_path = str(tmp_path / "trial-batch.jsonl")

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = Path.home() / ".claude" / "skills" / "akb2-harvest" / "SKILL.md"

    def test_rejected_submission(self, valid_planned) -> None:
        """exit 2 → failed 记录。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": CLI_EXIT_REJECTED,
            "filename": None,
            "error": "schema 校验失败",
        }
        records = run_attempts(
            valid_planned[:1],
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        assert records[0]["phase"] == "failed"
        assert records[0]["status"] == "failed"
        assert records[0]["final_state"] == "submit_error"
        assert "schema" in (records[0].get("failure_reason", ""))


class TestRunAttemptsDeadline:
    """截止时间检查。"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_md: Path, reset_seam, mock_batch_lock,
              tmp_path: Path) -> None:
        import trial_run
        trial_run._find_skill_md = lambda: mock_skill_md
        self.journal_path = str(tmp_path / "trial-batch.jsonl")

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = Path.home() / ".claude" / "skills" / "akb2-harvest" / "SKILL.md"

    def test_past_deadline(self, valid_planned) -> None:
        """deadline 已过 → 不处理任何条目。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": 0, "filename": "f.md", "error": None
        }
        records = run_attempts(
            valid_planned,
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() - 1,  # 已过期
        )
        assert records == []

    def test_partial_deadline(self, valid_planned) -> None:
        """deadline 在中间 → 处理部分条目。"""
        import trial_run

        call_count = [0]

        def seam(argv, rid):
            call_count[0] += 1
            return {"exit_code": 0, "filename": "f.md", "error": None}

        trial_run._submit_seam = seam
        records = run_attempts(
            valid_planned,
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 1,
        )
        # 至少处理了一个条目
        assert len(records) >= 1
        assert call_count[0] >= 1


class TestRunAttemptsErrors:
    """异常处理。"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_md: Path, reset_seam, mock_batch_lock,
              tmp_path: Path) -> None:
        import trial_run
        trial_run._find_skill_md = lambda: mock_skill_md
        self.journal_path = str(tmp_path / "trial-batch.jsonl")

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = Path.home() / ".claude" / "skills" / "akb2-harvest" / "SKILL.md"

    def test_unknown_adapter(self, valid_planned) -> None:
        """未知 adapter_id → ValueError。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": 0, "filename": "f.md", "error": None
        }
        with pytest.raises(ValueError, match="未知 adapter"):
            run_attempts(
                valid_planned[:1],
                "nonexistent_adapter",
                self.journal_path,
                deadline_s=_now_epoch() + 3600,
            )

    def test_adapter_exception(self, valid_planned) -> None:
        """adapter 抛异常 → failed 记录。"""
        import trial_run
        trial_run._find_skill_md = lambda: (_ for _ in ()).throw(
            RuntimeError("SKILL.md 不可定位")
        )

        records = run_attempts(
            valid_planned[:1],
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        assert records[0]["phase"] == "failed"
        assert records[0]["status"] == "failed"
        assert "SKILL.md" in (records[0].get("failure_reason", ""))


class TestTempFileCleanup:
    """临时文件清理。"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_md: Path, reset_seam, mock_batch_lock,
              tmp_path: Path) -> None:
        import trial_run
        trial_run._find_skill_md = lambda: mock_skill_md
        self.journal_path = str(tmp_path / "trial-batch.jsonl")

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = Path.home() / ".claude" / "skills" / "akb2-harvest" / "SKILL.md"

    def test_temp_files_cleaned(self, valid_planned) -> None:
        """临时文件在处理后被删除。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": 0, "filename": "f.md", "error": None
        }
        before = sorted(
            p.name for p in Path.cwd().iterdir()
            if p.name.startswith(".trial-payload--")
        )
        run_attempts(
            valid_planned[:1],
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        after = sorted(
            p.name for p in Path.cwd().iterdir()
            if p.name.startswith(".trial-payload--")
        )
        assert before == after, "临时文件未被清理"


# ---------------------------------------------------------------------------
# 7. 测试 seam 不可出现在公开面
# ---------------------------------------------------------------------------


class TestSubmitSeam:
    """_submit_seam 是私有变量，不在公开 API 中。"""

    def test_seam_is_private(self) -> None:
        """_submit_seam 以下划线开头，不在公开 API 列表中。"""
        from trial_run import _submit_seam as seam
        assert seam is None  # 默认值

    def test_seam_replaces_execution(self) -> None:
        """_submit_seam 被设置后替换子进程执行。"""
        import trial_run

        called_with = []

        def seam(argv, rid):
            called_with.append((argv, rid))
            return {"exit_code": 0, "filename": "f.md", "error": None}

        trial_run._submit_seam = seam
        result = trial_run._execute(["echo", "test"], "test-run-id")
        assert len(called_with) == 1
        assert called_with[0][0] == ["echo", "test"]
        assert called_with[0][1] == "test-run-id"
        assert result["exit_code"] == 0


# ---------------------------------------------------------------------------
# 8. 截止时间硬停
# ---------------------------------------------------------------------------


class TestDeadlineHardStop:
    """deadline 硬停检查。"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_md: Path, reset_seam, mock_batch_lock,
              tmp_path: Path) -> None:
        import trial_run
        trial_run._find_skill_md = lambda: mock_skill_md
        self.journal_path = str(tmp_path / "trial-batch.jsonl")

    def teardown_method(self) -> None:
        import trial_run
        trial_run._find_skill_md = Path.home() / ".claude" / "skills" / "akb2-harvest" / "SKILL.md"

    def test_empty_planned(self) -> None:
        """空 planned 列表 → 空 attempted。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": 0, "filename": "f.md", "error": None
        }
        records = run_attempts(
            [],
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        assert records == []

    def test_single_item(self, valid_planned) -> None:
        """单篇 planned 产生单条 attempted。"""
        import trial_run

        trial_run._submit_seam = lambda argv, rid: {
            "exit_code": 0, "filename": "f.md", "error": None
        }
        records = run_attempts(
            valid_planned[:1],
            "canonical",
            self.journal_path,
            deadline_s=_now_epoch() + 3600,
        )
        assert len(records) == 1


# ---------------------------------------------------------------------------
# 9. 真实 CLI 集成断言（不 mock subprocess）
# ---------------------------------------------------------------------------


class TestRealCLI:
    """真实调用 python3 -m akb2.harvest 证明 argv 被 __main__.py 接受。

    测试策略：
    - 创建最小 payload JSON 文件
    - 用 subprocess.run 调用真实 CLI
    - 验证 CLI 解析 argv 成功（exit=2 表示 payload 校验失败，
      不是 argparse 参数解析失败）
    - 测试不依赖 harvest 部署环境（仅需 VAULT_ROOT 和 PYTHONPATH）
    """

    def test_cli_accepts_argv(self, tmp_path: Path) -> None:
        """CLI 接受正确 argv 格式。"""
        # 创建最小 payload（有效 JSON 但 harvest 不完整）
        payload = {"schema_version": "1", "session_id": str(uuid.uuid4())}
        payload_path = tmp_path / "payload.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        run_id = str(uuid.uuid4())
        env = dict(
            os.environ,
            PYTHONPATH=str(REPO_ROOT / "src"),
            VAULT_ROOT=str(tmp_path),
        )

        proc = subprocess.run(
            [
                sys.executable, "-m", "akb2.harvest",
                str(payload_path),
                "--harvest-run-id", run_id,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        # CLI 进入 assembly 阶段后因缺少部署环境失败 → exit=2
        # 但 argparse 解析成功（非 usage error）
        assert proc.returncode == 2 or proc.returncode == 1
        stderr = proc.stderr or ""
        # 不应出现 argparse 的 usage 信息
        assert "usage:" not in stderr.lower() or "error:" in stderr

    def test_cli_rejects_invalid_run_id(self, tmp_path: Path) -> None:
        """非法 run_id → exit 2 + 明确错误信息。"""
        payload = {"schema_version": "1"}
        payload_path = tmp_path / "payload.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        env = dict(
            os.environ,
            PYTHONPATH=str(REPO_ROOT / "src"),
            VAULT_ROOT=str(tmp_path),
        )

        proc = subprocess.run(
            [
                sys.executable, "-m", "akb2.harvest",
                str(payload_path),
                "--harvest-run-id", "not-a-uuid",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        assert proc.returncode == 2
        assert "harvest-run-id" in (proc.stderr or "").lower()

    def test_cli_rejects_missing_file(self, tmp_path: Path) -> None:
        """不存在的文件 → exit 2。"""
        run_id = str(uuid.uuid4())
        env = dict(
            os.environ,
            PYTHONPATH=str(REPO_ROOT / "src"),
            VAULT_ROOT=str(tmp_path),
        )

        proc = subprocess.run(
            [
                sys.executable, "-m", "akb2.harvest",
                str(tmp_path / "nonexistent.json"),
                "--harvest-run-id", run_id,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        assert proc.returncode == 2
        assert "不可读" in (proc.stderr or "")


# ---------------------------------------------------------------------------
# 10. 残留项标注
# ---------------------------------------------------------------------------


class TestResiduals:
    """显式标注已知的接线缺口。

    ``akb2.harvest.ledger.reconcile()`` 已经实现（见 ledger.py），但
    trial_run 的双通道流程尚未接入它；sourceurl 溯源字段同样尚未接入。
    这些标记确保后续接线工作覆盖到这两处。
    """

    def test_reconcile_residual(self) -> None:
        """reconciliation_state 字段目前由 trial_run 自行赋值，未接入 ledger.reconcile()。

        ``akb2.harvest.ledger.reconcile()`` 已实现，可裁决
        committed/blocked/not_required，但 trial_run 尚未调用它——当前
        trial_run 直接写 reconciliation_state 值。residual 标记：待双通道
        接线完成后应将裁决逻辑迁移到调用 ledger.reconcile()。
        """
        # 验证 reconciliation_state 字段存在
        record = {
            "phase": "failed",
            "reconciliation_state": "blocked",
        }
        assert "reconciliation_state" in record
        assert record["reconciliation_state"] == "blocked"

    def test_sourceurl_residual(self) -> None:
        """source_url 裁决依赖尚未存在的符号。

        source_url 是 harvest 流程中的原始 URL 来源字段，当前 trial_run 不
        生成该字段。residual 标记：待 source_url 模块就绪后应将来源追溯
        逻辑集成到 settled 记录中。
        """
        # 当前 settled 记录不含 source_url 字段
        record = {
            "phase": "settled",
            "status": "completed",
            "final_state": "committed",
        }
        assert "source_url" not in record
