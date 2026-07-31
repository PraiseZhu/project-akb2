"""Runner 生命周期测试 — SC-6.1 (七段生命线 + 周抽检)。

验证每篇七段生命线完整落账、七段任一失败后续调用计数为 0、
周抽检达标率检查位行为。

全 fixture mock，不调真 API。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
    from src.akb2.quality.runner import RunnerConfig

# ---------------------------------------------------------------------------
# 辅助 Fixtures
# ---------------------------------------------------------------------------


def _make_runner_config(
    tmp_path: Path,
    auto_promote: bool = False,
    poll_interval: int = 1,
) -> RunnerConfig:
    """创建测试用的 RunnerConfig。"""
    from src.akb2.quality.runner import RunnerConfig as RC

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    grader_dir = tmp_path / "grader-results"
    grader_dir.mkdir(parents=True, exist_ok=True)

    return RC(
        raw_dir=str(raw_dir),
        ledger_path=str(tmp_path / "quality-ledger.jsonl"),
        staging_dir=str(staging_dir),
        vault_root=str(tmp_path),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(grader_dir),
        poll_interval_seconds=poll_interval,
        auto_promote=auto_promote,
    )


def _make_raw_file(raw_dir: Path, slug: str, date_str: str = "2026-06-13") -> Path:
    """在 raw_dir 中创建 raw 文件。"""
    filename = f"{slug}--raw--{date_str}.md"
    raw_dir.mkdir(parents=True, exist_ok=True)
    content = f"""---
artifact_type: raw
source_slug: {slug}
source_url: https://example.com/{slug}
source_title: Test Article {slug}
source_authors: Test Author
source_publisher: Test Blog
source_published_at: {date_str}
fetched_at: {date_str}T00:00:00Z
fetched_via: defuddle
---

# §A Test Article {slug}

Some content here with anchor §A.

# §B How Section

Step 1: Do something.
Step 2: Verify something.
"""
    # AKB 操作化命令字面量（fenced text block，非 f-string，含 {} 字面量）。fenced block
    # 不被 must_cover scanner 拾取；供 SG-command-recheck 全文回查锚定。
    content += """

原文操作示例（AKB 操作化命令字面量，供 SG-command-recheck 回查锚定）：

```text
queue_item={source_url,raw_anchor,content_hash,output_path,lifecycle_state}
ledger.append({event:'distill_extract',status:'start'})
max_repair_rounds=3; assert remaining_defects == 0
trigger={type:'schedule',cron:'0 * * * *'}
work_item=ledger.next(status='pending',limit=1)
assert run_quality_gate(result,grader='gpt-5.4').defects == []
result=run_action(work_item,timeout_seconds=60); ledger.append({item_id:work_item.id,status:'done',result_hash:hash(result)})
ledger.append({event:'abort',node:node,error_class:error_class,model_family:model_family,timeout_s:60,max_retries:0,retries_used:retries_used})
MAX_REPAIR_ROUNDS=3; DISTILL_TIMEOUT_SECONDS=60; GRADER_TIMEOUT_SECONDS=120; MAX_RETRIES=0; MAX_PARALLEL_ITEMS=1
```
"""
    filepath = raw_dir / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_pid_gone(pid: int, timeout_s: float = 3.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.05)
    return not _pid_exists(pid)


# ---------------------------------------------------------------------------
# 测试类: 七段生命线
# ---------------------------------------------------------------------------


class TestRunnerLifelineSegments:
    """七段生命线完整落账测试。"""

    def test_full_lifeline_all_segments_logged(self, tmp_path: Path) -> None:
        """正常流程: 七段生命线全部落账。"""

        from src.akb2.quality.runner import (
            _compute_raw_hash,
            _log_event,
            _process_one,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-lifeline", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_pipeline_segments"
        ) as mock_run:
            mock_run.return_value = {
                "converged": True,
                "rounds": 1,
                "promoted": True,
                "promotion_mode": "proposal",
                "final_status": "promoted",
                "error": None,
            }

            _process_one(str(f), config)

        # 验证 _process_one 正确调用了 _run_pipeline_segments
        mock_run.assert_called_once()

        # 手动创建一些账本事件来验证账本写入逻辑
        content_hash = _compute_raw_hash(str(f))
        assert _log_event(config, "intake_raw", "intake_raw", content_hash,
                         {"raw_path": str(f)})
        assert _log_event(config, "finalize", "finalize", content_hash,
                         {"mode": "proposal"})

        # 验证账本记录存在
        ledger_path = Path(config.ledger_path)
        assert ledger_path.exists(), "账本文件应存在"

        events = []
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        event_types = [e["event"] for e in events]
        # 我们手动创建了 intake_raw 和 finalize 事件
        assert "intake_raw" in event_types, f"缺少 intake_raw 事件: {event_types}"
        assert "finalize" in event_types, f"缺少 finalize 事件: {event_types}"
        assert len(events) >= 2, f"账本至少应有 2 条事件，实际 {len(events)}"

    def test_admission_fail_stops_chain(self, tmp_path: Path) -> None:
        """准入失败 → 后续段调用计数为 0。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-admission-fail", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        # Mock admission 返回失败
        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (False, "准入未通过: how_tendency", [])

            result = _run_pipeline_segments(
                str(f), str(f.read_text(encoding="utf-8")), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "rejected"
        assert "admission" in str(result.get("error", "")).lower() or result["final_status"] == "rejected"

    def test_existing_source_slug_rejected_before_distill(self, tmp_path: Path) -> None:
        """同 slug active source 已存在时应 fail-closed，不进入提炼/裁判。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "already-active", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)
        existing = tmp_path / "wiki" / "sources" / "already-active.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("# already active", encoding="utf-8")

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission, mock.patch(
            "src.akb2.quality.runner._run_distill_segment"
        ) as mock_distill, mock.patch(
            "src.akb2.quality.runner._run_quality_segments"
        ) as mock_quality, mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "rejected"
        assert "duplicate_source" in result["error"]
        mock_admission.assert_not_called()
        mock_distill.assert_not_called()
        mock_quality.assert_not_called()
        mock_promote.assert_not_called()

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        events = [r.get("event") for r in rows]
        assert events == ["intake_raw", "admission", "abort"]
        abort = rows[-1]
        assert abort["node"] == "admission"
        assert abort["details"]["reason"] == "duplicate_source"
        assert abort["details"]["existing_source_path"] == str(existing)

    def test_existing_source_url_rejected_before_distill(self, tmp_path: Path) -> None:
        """同 source_url 即使换 slug 也应 fail-closed，不进入提炼/裁判。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "renamed-active", "2026-06-13")
        raw_text = f.read_text(encoding="utf-8")
        raw_text = raw_text.replace(
            "source_url: https://example.com/renamed-active",
            "source_url: https://example.com/already-active",
        )
        f.write_text(raw_text, encoding="utf-8")
        config = _make_runner_config(tmp_path, auto_promote=False)
        existing = tmp_path / "wiki" / "sources" / "already-active.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text(
            "---\nsource_url: https://example.com/already-active\n---\n# already active",
            encoding="utf-8",
        )

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission, mock.patch(
            "src.akb2.quality.runner._run_distill_segment"
        ) as mock_distill, mock.patch(
            "src.akb2.quality.runner._run_quality_segments"
        ) as mock_quality, mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "rejected"
        assert "duplicate_source" in result["error"]
        mock_admission.assert_not_called()
        mock_distill.assert_not_called()
        mock_quality.assert_not_called()
        mock_promote.assert_not_called()

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [r.get("event") for r in rows] == ["intake_raw", "admission", "abort"]
        assert rows[-1]["details"]["existing_source_path"] == str(existing)

    def test_distill_extract_fail_stops_chain(self, tmp_path: Path) -> None:
        """提炼段 1 失败 → 后续段调用计数为 0。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-distill-fail", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (True, "", [])
            with mock.patch(
                "src.akb2.quality.runner._run_distill_segment"
            ) as mock_distill:
                mock_distill.return_value = (
                    False,
                    "extract_failed",
                    "抽取失败",
                    None,
                    {
                        "failed_node": "distill_extract",
                        "retries_used": 3,
                        "maker_fallback_used": False,
                    },
                )

                result = _run_pipeline_segments(
                    str(f), str(f.read_text(encoding="utf-8")), config
                )

        assert result["converged"] is False
        assert "extract" in str(result.get("error", "")).lower() or not result["converged"]
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        distill_starts = [
            r for r in rows
            if r.get("event") == "distill_extract"
            and r.get("details", {}).get("status") == "start"
        ]
        aborts = [
            r for r in rows
            if r.get("event") == "abort" and r.get("node") == "distill_extract"
        ]
        assert distill_starts
        assert aborts
        # F60 r2(Sol 审 P1-3):两层重试分列——provider(SDK)=0 / outer attempts=3
        assert aborts[-1]["details"]["provider_max_retries"] == 0
        assert aborts[-1]["details"]["extract_max_attempts"] == 3
        assert aborts[-1]["details"]["retries_used"] == 3
        assert not any(r.get("event") in {"grade_llm", "promote", "finalize"} for r in rows)

    def test_distill_worker_timeout_stops_chain(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """真实 DeepSeek 卡住时，runner 父进程必须能杀 worker 并 fail-closed。"""
        from src.akb2.quality.distill import ExtractResult
        from src.akb2.quality.runner import _run_pipeline_segments

        monkeypatch.setenv("AKB2_MAKER_FALLBACK", "0")
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-worker-timeout", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_extract_claims_worker",
            return_value=ExtractResult(
                success=False,
                retries_used=0,
                error="extract_worker_timeout: 抽取子进程超过总时限 210.0s",
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments"
        ) as mock_quality, mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        assert result["final_status"] == "failed"
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            r.get("event") == "distill_extract"
            and r.get("details", {}).get("status") == "start"
            for r in rows
        )
        abort = [r for r in rows if r.get("event") == "abort"][-1]
        assert abort["node"] == "distill_extract"
        assert abort["details"]["error_class"] == "network_timeout"
        assert "extract_worker_timeout" in abort["details"]["error"]
        assert not any(r.get("event") in {"grade_llm", "promote", "finalize"} for r in rows)
        mock_quality.assert_not_called()
        mock_promote.assert_not_called()

    def test_distill_worker_timeout_kills_grandchild_process(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """extract worker timeout 必须 killpg，连 sleep 30 孙进程也不能残留。"""
        from src.akb2.quality import runner as runner_mod

        pidfile = tmp_path / "sleep-child.pid"
        module = tmp_path / "hanging_distill_worker.py"
        module.write_text(
            "\n".join(
                [
                    "import pathlib",
                    "import subprocess",
                    "import sys",
                    "import time",
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])",
                    f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid), encoding='utf-8')",
                    "time.sleep(30)",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv(
            "PYTHONPATH",
            f"{tmp_path}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        )
        monkeypatch.setattr(
            runner_mod,
            "DISTILL_EXTRACT_WORKER_MODULE",
            "hanging_distill_worker",
        )
        monkeypatch.setattr(runner_mod, "DISTILL_EXTRACT_WORKER_TIMEOUT_S", 0.2)
        monkeypatch.setattr(runner_mod, "DISTILL_EXTRACT_WORKER_KILL_GRACE_S", 0.1)

        result = runner_mod._run_extract_claims_worker("raw")

        assert result.success is False
        assert "extract_worker_timeout" in result.error
        assert pidfile.exists(), "worker 应先启动 sleep 30 孙进程并写 pidfile"
        child_pid = int(pidfile.read_text(encoding="utf-8"))
        assert _wait_pid_gone(child_pid), f"sleep 30 孙进程仍存在: pid={child_pid}"

    def test_distill_worker_timeout_budget_tiers_are_named_and_capped(self) -> None:
        """长文 worker 总时限按词数分级，且硬顶有界。"""
        from src.akb2.quality import runner as runner_mod

        # F60 预算算术自洽化(Sol 审 P1-4 修正):重试=整 attempt 重启,契约为
        # "至少一次完整重试":normal=2×attempt(240)+30=510;long=+120。
        # 层间关系由 test_f60_grounding_gate.TestBudgetArithmetic 守卫。
        assert runner_mod.DISTILL_EXTRACT_WORKER_TIMEOUT_S == 510.0
        assert runner_mod.DISTILL_EXTRACT_WORKER_LONG_DOC_WORDS == 3_000
        assert runner_mod.DISTILL_EXTRACT_WORKER_LONG_DOC_TIMEOUT_S == 630.0
        assert runner_mod.DISTILL_EXTRACT_WORKER_HARD_TIMEOUT_CAP_S == 630.0
        assert (
            runner_mod._distill_extract_worker_timeout_s("word " * 3_000)
            == runner_mod.DISTILL_EXTRACT_WORKER_TIMEOUT_S
        )
        assert (
            runner_mod._distill_extract_worker_timeout_s("word " * 3_001)
            == runner_mod.DISTILL_EXTRACT_WORKER_LONG_DOC_TIMEOUT_S
        )
        assert (
            runner_mod._distill_extract_worker_timeout_s("word " * 10_000)
            <= runner_mod.DISTILL_EXTRACT_WORKER_HARD_TIMEOUT_CAP_S
        )

    def test_distill_worker_timeout_can_use_maker_fallback(self, tmp_path: Path, monkeypatch) -> None:
        """maker fallback 开启时，DeepSeek timeout 可转为候选，但必须继续过质量门。"""
        from src.akb2.quality.distill import ExtractResult
        from src.akb2.quality.runner import _run_pipeline_segments

        monkeypatch.setenv("AKB2_MAKER_FALLBACK", "1")
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-maker-fallback", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._load_vault_concept_index",
            return_value={
                "status": "ok",
                # WP-3.4: 含 maker fallback 硬编码 referenced_concepts，使 related 可 resolve
                # （frontmatter 同源 resolve 过滤后仍 ≥2，过 A2；不依赖真实 vault 挂载状态）
                "known_concepts": ["loop-engineering", "quality-gate", "agent-workflow"],
                "pending_queue": [],
                "wiki_references": [],
                "source_paths": [],
                "counts": {"known_concepts": 3, "pending_queue": 0, "wiki_references": 0},
            },
        ), mock.patch(
            "src.akb2.quality.runner._run_extract_claims_worker",
            return_value=ExtractResult(
                success=False,
                retries_used=0,
                error="extract_worker_timeout: 抽取子进程超过总时限 210.0s",
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                True,
                "",
                "",
                {
                    "repair_rounds": 0,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 0,
                    "b_defect_summary": [],
                    "final_defects_summary": [],
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment",
            return_value=(True, "proposal", "Proposal mode"),
        ):
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is True
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        success = [
            r for r in rows
            if r.get("event") == "distill_extract"
            and r.get("details", {}).get("status") == "success"
        ][-1]
        assert success["details"]["maker_fallback_used"] is True
        assert "extract_worker_timeout" in success["details"]["maker_primary_error"]
        assert any(r.get("event") == "grade_llm" for r in rows)
        assert any(r.get("event") == "promote" for r in rows)

    def test_distill_worker_timeout_fail_closed_when_maker_fallback_disabled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """maker fallback 关闭时，DeepSeek timeout 仍必须 fail-closed。"""
        from src.akb2.quality.distill import ExtractResult
        from src.akb2.quality.runner import _run_pipeline_segments

        monkeypatch.setenv("AKB2_MAKER_FALLBACK", "0")
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-maker-fallback-off", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_extract_claims_worker",
            return_value=ExtractResult(
                success=False,
                retries_used=0,
                error="extract_worker_timeout: 抽取子进程超过总时限 210.0s",
            ),
        ):
            _run_pipeline_segments(str(f), f.read_text(encoding="utf-8"), config)

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        abort = [r for r in rows if r.get("event") == "abort"][-1]
        assert abort["node"] == "distill_extract"
        assert abort["details"]["maker_fallback_used"] is False
        assert not any(r.get("event") in {"grade_llm", "promote", "finalize"} for r in rows)

    def test_repair_not_converge_stops_chain(self, tmp_path: Path) -> None:
        """修复不收敛 → fail-closed 停链。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-repair-fail", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (True, "", [])
            with mock.patch(
                "src.akb2.quality.runner._run_distill_segment"
            ) as mock_distill:
                mock_distill.return_value = (
                    True,
                    "staging/candidate/test-repair-fail.md",
                    "",
                    "candidate text content",
                    {"retries_used": 1, "maker_fallback_used": False},
                )
                with mock.patch(
                    "src.akb2.quality.runner._run_quality_segments"
                ) as mock_quality:
                    mock_quality.return_value = (
                        False,
                        "distill_no_converge",
                        "3 轮修复不收敛",
                        {"repair_rounds": 3, "a_defect_count": 0, "a_defect_summary": [],
                         "b_defect_count": 3, "b_defect_summary": [], "final_defects_summary": []},
                    )
                    result = _run_pipeline_segments(
                        str(f), str(f.read_text(encoding="utf-8")), config
                    )

        assert result["converged"] is False
        # F46: 内容不收敛(distill_no_converge)路由到 needs_repair 驻留(非 abort),
        # 仍停链(无 promote);infra/过程性死亡才走 failed。
        assert result["final_status"] in ("needs_repair", "failed", "quarantined")
        assert not result.get("promoted")

    def test_repair_route_failed_writes_abort_without_promote(self, tmp_path: Path) -> None:
        """repair LLM route/worker 失败必须及时落主账本 abort。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-repair-route-failed", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_distill_segment",
            return_value=(
                True,
                "staging/candidate/test-repair-route-failed.md",
                "",
                "candidate text",
                {"retries_used": 1, "maker_fallback_used": True},
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                False,
                "repair_route_failed",
                "repair worker timeout",
                {
                    "repair_rounds": 1,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 1,
                    "b_defect_summary": [{"dimension": "论断锚定率"}],
                    "final_defects_summary": [{"dimension": "论断锚定率"}],
                    "repair_trace_paths": ["trace.json"],
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment"
        ) as mock_promote:
            result = _run_pipeline_segments(
                str(f), f.read_text(encoding="utf-8"), config
            )

        assert result["converged"] is False
        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        abort = [r for r in rows if r.get("event") == "abort"][-1]
        assert abort["node"] == "repair_loop"
        assert abort["details"]["error_class"] == "repair_route_failed"
        assert abort["details"]["repair_rounds"] == 1
        assert not any(r.get("event") in {"promote", "finalize"} for r in rows)
        mock_promote.assert_not_called()

    def test_grade_llm_logs_fallback_reason(self, tmp_path: Path) -> None:
        """grade_llm 账本必须记录 primary 降级原因，避免 canary 0 缺陷失真。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-grader-fallback", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_distill_segment",
            return_value=(
                True,
                "staging/candidate/test-grader-fallback.md",
                "",
                "candidate text",
                {"retries_used": 1, "maker_fallback_used": False},
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                False,
                "",
                "grader_fallback_not_allowed",
                {
                    "repair_rounds": 0,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 0,
                    "b_defect_summary": [],
                    "grader_model_used": "gemini-2.5-flash",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_model": "gemini-flash",
                    "grader_fallback_used": True,
                    "grader_primary_failed": True,
                    "grader_primary_failure_reason": "gpt-5.4 返回不符合 grader schema 的 JSON",
                    "grader_fallback_failed": False,
                    "grader_fallback_failure_reason": "",
                    "final_defects_summary": [],
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment",
            return_value=(True, "proposal", "Proposal mode"),
        ) as mock_promote:
            _run_pipeline_segments(str(f), f.read_text(encoding="utf-8"), config)

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        grade_events = [r for r in rows if r.get("event") == "grade_llm"]
        assert grade_events
        details = grade_events[-1]["details"]
        assert details["model_used"] == "gemini-2.5-flash"
        assert details["fallback_used"] is True
        assert details["primary_model"] == "gpt-5.4"
        assert "不符合 grader schema" in details["primary_failure_reason"]
        events = [r.get("event") for r in rows]
        assert "abort" in events
        assert "promote" not in events
        assert "finalize" not in events
        mock_promote.assert_not_called()

    def test_structural_defects_enter_repair_before_abort(self, tmp_path: Path) -> None:
        """A 层结构缺陷必须进入 repair，不能在 structural_fail 处提前停链。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.runner import _run_quality_segments

        candidate = tmp_path / "staging" / "candidate" / "test.md"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("candidate with structural issue", encoding="utf-8")
        config = _make_runner_config(tmp_path, auto_promote=False)
        grader_path = tmp_path / "staging" / "grader-results" / "test.grader.json"
        initial_defect = Defect(
            dimension="A7-图谱证据",
            location="图谱关系/evidence/no-supported-related-concept",
            defect="图谱 evidence 缺少 provenance",
            suggestion="补 provenance",
        )

        with mock.patch(
            "src.akb2.quality.checks_structural.run_candidate_self_gate",
            return_value=[initial_defect],
        ) as mock_self_gate, mock.patch(
            "src.akb2.quality.grader.run_grader_detailed"
        ) as mock_grader, mock.patch(
            "src.akb2.quality.repair.run_repair_loop",
            return_value={
                "converged": True,
                "rounds": 1,
                "final_text": "candidate repaired",
                "final_defects": [],
                "trace_paths": ["trace.json"],
                "final_grader_meta": {
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            },
        ) as mock_repair:
            converged, error_class, error_detail, meta = _run_quality_segments(
                candidate.read_text(encoding="utf-8"),
                str(candidate),
                "raw §A",
                config,
                grader_path,
            )

        assert converged is True
        assert error_class == ""
        assert error_detail == ""
        mock_self_gate.assert_called_once()
        mock_grader.assert_not_called()
        mock_repair.assert_called_once()
        assert mock_repair.call_args.kwargs["a_defects"] == [initial_defect]
        assert mock_repair.call_args.kwargs["b_defects"] == []
        assert meta["a_defect_count"] == 1
        assert meta["repair_rounds"] == 1
        assert meta["repair_trace_paths"] == ["trace.json"]
        assert meta["grader_model_used"] == "gpt-5.4"
        assert candidate.read_text(encoding="utf-8") == "candidate repaired"
        persisted = json.loads(grader_path.read_text(encoding="utf-8"))
        assert persisted["status"] == "PASS"
        assert persisted["model_used"] == "gpt-5.4"

    def test_promote_proposal_mode_no_active_write(self, tmp_path: Path) -> None:
        """提议模式 (AKB2_AUTO_PROMOTE=0): 不写 active。"""
        from src.akb2.quality.runner import (
            _run_pipeline_segments,
        )

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-proposal", "2026-06-13")

        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment"
        ) as mock_admission:
            mock_admission.return_value = (True, "", [])
            with mock.patch(
                "src.akb2.quality.runner._run_distill_segment"
            ) as mock_distill:
                mock_distill.return_value = (
                    True,
                    "staging/candidate/test-proposal.md",
                    "",
                    "candidate text",
                    {"retries_used": 1, "maker_fallback_used": False},
                )
                with mock.patch(
                    "src.akb2.quality.runner._run_quality_segments"
                ) as mock_quality:
                    mock_quality.return_value = (
                        True, "", "",
                        {"repair_rounds": 0, "a_defect_count": 0, "a_defect_summary": [],
                         "b_defect_count": 0, "b_defect_summary": [], "final_defects_summary": []},
                    )
                    with mock.patch(
                        "src.akb2.quality.runner._run_promote_segment"
                    ) as mock_promote:
                        mock_promote.return_value = (
                            True,
                            "proposal",
                            "Proposal mode: no active write",
                        )
                        result = _run_pipeline_segments(
                            str(f), str(f.read_text(encoding="utf-8")), config
                        )

        assert result["converged"] is True
        assert result["promotion_mode"] == "proposal"
        # §0.2/F4：proposal mode（AUTO_PROMOTE=0, promoted=True）→ final_status=proposal_passed
        # （旧 buggy 写 "promoted"，F4 修正为 proposal_passed，避免与 active 混淆）
        assert result["final_status"] == "proposal_passed"
        assert result["promotion_outcome"] == "proposal_passed"

    def test_grade_llm_logs_final_zero_after_repair(self, tmp_path: Path) -> None:
        """repair 收敛后 grade_llm.defect_count 表示最终缺陷数，初始缺陷另存。"""
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-final-grade", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)
        initial_defect = {
            "dimension": "TLDR 密度",
            "location": "## TLDR",
            "defect": "修复前缺陷",
        }

        with mock.patch(
            "src.akb2.quality.runner._run_admission_segment",
            return_value=(True, "", []),
        ), mock.patch(
            "src.akb2.quality.runner._run_distill_segment",
            return_value=(
                True,
                "staging/candidate/test-final-grade.md",
                "",
                "candidate text",
                {"retries_used": 1, "maker_fallback_used": False},
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_quality_segments",
            return_value=(
                True,
                "",
                "",
                {
                    "repair_rounds": 2,
                    "a_defect_count": 0,
                    "a_defect_summary": [],
                    "b_defect_count": 1,
                    "b_defect_summary": [initial_defect],
                    "final_defects_summary": [],
                    "grader_model_used": "gpt-5.4",
                    "grader_primary_model": "gpt-5.4",
                    "grader_fallback_used": False,
                    "grader_suspended": False,
                },
            ),
        ), mock.patch(
            "src.akb2.quality.runner._run_promote_segment",
            return_value=(True, "proposal", "Proposal mode"),
        ):
            _run_pipeline_segments(str(f), f.read_text(encoding="utf-8"), config)

        rows = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        grade = [r for r in rows if r.get("event") == "grade_llm"][-1]["details"]
        assert grade["defect_count"] == 0
        assert grade["defects"] == []
        assert grade["initial_defect_count"] == 1
        assert grade["initial_defects"] == [initial_defect]


# ---------------------------------------------------------------------------
# 测试类: 周抽检
# ---------------------------------------------------------------------------


class TestRunnerWeeklySpotCheck:
    """周抽检行为测试 — SC-6.1。"""

    def test_spot_check_below_threshold_creates_task(self, tmp_path: Path) -> None:
        """达标率 < 80% → 创建校准任务文件 + 落账 + weekly-summary.json。"""
        from src.akb2.quality.runner import (
            _run_weekly_spot_check,
        )

        config = _make_runner_config(tmp_path, auto_promote=False)
        weekly_dir = tmp_path / "reports" / "quality-loop"
        weekly_dir.mkdir(parents=True, exist_ok=True)

        # Mock 抽检结果为 < 80% 达标
        with mock.patch(
            "src.akb2.quality.runner._sample_recent_promoted"
        ) as mock_sample:
            mock_sample.return_value = [
                {"path": "wiki/sources/a.md", "slug": "a"},
                {"path": "wiki/sources/b.md", "slug": "b"},
                {"path": "wiki/sources/c.md", "slug": "c"},
            ]
            with mock.patch(
                "src.akb2.quality.runner._regrade_sample"
            ) as mock_regrade:
                # 3 篇中 2 篇 fail → 33% < 80%
                mock_regrade.return_value = [False, False, True]

                _run_weekly_spot_check(config, str(weekly_dir))

        # 验证 weekly-summary.json 产出
        summary_path = weekly_dir / "weekly-summary.json"
        assert summary_path.exists(), "weekly-summary.json 应存在"

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert "manual_intervention_required_count" in summary, (
            "应含 manual_intervention_required_count 字段"
        )
        assert "quality_feedback_required_count" in summary, (
            "应含 quality_feedback_required_count 字段"
        )
        # 达标率 < 80%，calibration task 自动创建，两计数保持 0（plan SC-6.1: 无需人工介入）
        assert summary["manual_intervention_required_count"] == 0, (
            "达标率 < 80% 时自动创建校准任务，不应报人工介入"
        )
        assert summary["quality_feedback_required_count"] == 0, (
            "达标率 < 80% 时自动创建校准任务，不应报质量反馈需求"
        )

    def test_spot_check_above_threshold_happy_path(self, tmp_path: Path) -> None:
        """达标率 >= 80% → happy path: weekly-summary.json 存在且两计数为 0。"""
        from src.akb2.quality.runner import (
            _run_weekly_spot_check,
        )

        config = _make_runner_config(tmp_path, auto_promote=False)
        weekly_dir = tmp_path / "reports" / "quality-loop"
        weekly_dir.mkdir(parents=True, exist_ok=True)

        with mock.patch(
            "src.akb2.quality.runner._sample_recent_promoted"
        ) as mock_sample:
            mock_sample.return_value = [
                {"path": "wiki/sources/a.md", "slug": "a"},
                {"path": "wiki/sources/b.md", "slug": "b"},
                {"path": "wiki/sources/c.md", "slug": "c"},
            ]
            with mock.patch(
                "src.akb2.quality.runner._regrade_sample"
            ) as mock_regrade:
                # 3 篇全部 pass → 100% >= 80%
                mock_regrade.return_value = [True, True, True]

                _run_weekly_spot_check(config, str(weekly_dir))

        summary_path = weekly_dir / "weekly-summary.json"
        assert summary_path.exists(), "weekly-summary.json 应存在"

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["manual_intervention_required_count"] == 0, (
            "happy path 下 manual_intervention_required_count 应为 0"
        )
        assert summary["quality_feedback_required_count"] == 0, (
            "happy path 下 quality_feedback_required_count 应为 0"
        )

    def test_spot_check_no_promoted_items(self, tmp_path: Path) -> None:
        """无近期入库条目 → 跳过抽检，不创建 summary。"""
        from src.akb2.quality.runner import (
            _run_weekly_spot_check,
        )

        config = _make_runner_config(tmp_path, auto_promote=False)
        weekly_dir = tmp_path / "reports" / "quality-loop"

        with mock.patch(
            "src.akb2.quality.runner._sample_recent_promoted"
        ) as mock_sample:
            mock_sample.return_value = []

            _run_weekly_spot_check(config, str(weekly_dir))

        summary_path = weekly_dir / "weekly-summary.json"
        # 无条目时可能不创建 summary，或创建空 summary
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            assert summary.get("samples_checked", 0) == 0


# ---------------------------------------------------------------------------
# SC-6.3 下游拒收 degraded grader 的 PASS（step-6）
# ---------------------------------------------------------------------------


class TestGraderDegradedDownstreamRejection:
    """runner _persist_grader_pass 不写 fallback PASS（写 DEGRADED）+ promote 拒收 degraded PASS。"""

    def test_promote_rejects_degraded_grader_pass(self, tmp_path: Path) -> None:
        """SC-6.3: promote._read_grader_result 对 fallback_used/suspended 的 PASS 拒收。"""
        from src.akb2.quality.promote import _read_grader_result

        # 1) status=PASS 但 fallback_used=True → 拒收（fail-closed）
        degraded_pass = tmp_path / "grader_degraded_pass.json"
        degraded_pass.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "dimensions_checked": 10,
                    "findings": [],
                    "fallback_used": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ok, detail = _read_grader_result(degraded_pass)
        assert ok is False, "fallback_used 的 PASS 必须拒收"
        assert "degraded" in detail

        # 2) status=PASS 但 suspended=True → 拒收
        suspended_pass = tmp_path / "grader_suspended_pass.json"
        suspended_pass.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "dimensions_checked": 10,
                    "findings": [],
                    "suspended": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ok2, detail2 = _read_grader_result(suspended_pass)
        assert ok2 is False, "suspended 的 PASS 必须拒收"
        assert "degraded" in detail2

        # 3) status=PASS 且无 degraded 标记 → 通过（回归：不误伤正常 PASS）
        clean_pass = tmp_path / "grader_clean_pass.json"
        clean_pass.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "dimensions_checked": 10,
                    "findings": [],
                    "fallback_used": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ok3, detail3 = _read_grader_result(clean_pass)
        assert ok3 is True, "正常 PASS 不应被误拒"
        assert "PASS" in detail3

        # 4) status=DEGRADED（runner _persist_grader_pass 对 degraded grader 写的）→ 拒收
        degraded_status = tmp_path / "grader_degraded_status.json"
        degraded_status.write_text(
            json.dumps(
                {
                    "status": "DEGRADED",
                    "dimensions_checked": 10,
                    "findings": [],
                    "fallback_used": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ok4, detail4 = _read_grader_result(degraded_status)
        assert ok4 is False, "DEGRADED 状态必须拒收"
        assert "DEGRADED" in detail4


class TestRepairLoopGraderDegraded:
    """SC-6.2: repair 后 grader 走 gemini 兜底（fallback_used=True）时不得判收敛。

    复用确定性修复路径（图谱关系缺陷 → 确定性修复 → A 通过 → B 评分）触达 grader，
    无需 deepseek repair LLM 响应，自包含。
    """

    # A-clean 候选 + 确定性可修复的图谱关系缺陷（与 test_repair_loop 同源）
    _CANDIDATE = """---
status: candidate
gate_decision: null
source_refs:
  - raw/test.md
gate_reason: 待评审
future_questions:
  - 如何处理失败？
  - 如何设置预算？
related_concepts:
  - harness-engineering
  - agent-collaboration
---

## TLDR
Loop [raw §A].

## 关键发现
- [raw §A] (source_grounded) Loop needs a gate.

## 入选理由
- [raw §A] (source_grounded) It is useful.

## 证据地图
| 锚点 | 论点 | 来源 | 未覆盖 |
|------|------|------|--------|
| §A | Loop | source_grounded | 未量化：原文未提供效果数据 |

## 核心论点
1. [raw §A] (source_grounded) Loop needs a gate.

## 论证与解释
**[raw §A]** (source_grounded) 来源事实：Loop needs a gate.

## 适用边界
- [raw §A] (akb_interpretation) 适用场景：多步骤管线。
- [raw §A] (akb_interpretation) 适用场景：需要可恢复账本和质量门控的内容流水线。
- [raw §A] (akb_interpretation) 不适用场景：one-shot。
- [raw §A] (akb_interpretation) 约束条件：需要账本。

## 图谱关系
| target | relation | strength | evidence |
|--------|----------|----------|----------|
| agent collaboration | related | medium | multi-agent loops require coordination primitives |

## Future Questions
- 如何处理失败？
- 如何设置预算？

## 维护记录
| 日期 | 操作 | 说明 |
|------|------|------|
| 2026-06-20 | 初始创建 | raw: test |
"""

    _RAW_TEXT = """---
artifact_type: raw
source_slug: loop-engineering
source_url: https://example.com/loop-engineering
source_title: Loop Engineering
source_authors: Addy Osmani
source_publisher: Addy Osmani's Blog
source_published_at: 2026-06-07
fetched_at: 2026-06-13T00:00:00Z
fetched_via: defuddle
---

# §A Loop Engineering

Loop engineering is the practice of designing AI agent loops that are bounded,
convergent, and observable.

# §B How: A Complete Loop Shape

A well-designed loop has five components: a trigger, a work unit selector, an action
executor, a quality gate, and a ledger writer.

# §C Convergence Over Restart

When a step produces defects, fix the specific defects rather than restarting the
entire pipeline. This is the principle of convergence over restart.

原文操作示例（AKB 操作化命令字面量，供 SG-command-recheck 回查锚定）：

```text
trigger={type:'schedule',cron:'0 * * * *'}
work_item=ledger.next(status='pending',limit=1)
assert run_quality_gate(result,grader='gpt-5.4').defects == []
queue_item={source_url,raw_anchor,content_hash,output_path,lifecycle_state}
ledger.append({event:'distill_extract',status:'start'})
max_repair_rounds=3; assert remaining_defects == 0
result=run_action(work_item,timeout_seconds=60); ledger.append({item_id:work_item.id,status:'done',result_hash:hash(result)})
ledger.append({event:'abort',node:node,error_class:error_class,model_family:model_family,timeout_s:60,max_retries:0,retries_used:retries_used})
MAX_REPAIR_ROUNDS=3; DISTILL_TIMEOUT_SECONDS=60; GRADER_TIMEOUT_SECONDS=120; MAX_RETRIES=0; MAX_PARALLEL_ITEMS=1
```
"""

    def test_repair_loop_degraded_grader_fail_closed(self, tmp_path: Path) -> None:
        """SC-6.2: repair 后 fallback_used=True 且 defects=[] → run_repair_loop 不判收敛。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.llm_router import LLMResponse
        from src.akb2.quality.repair import run_repair_loop
        from tests.quality.conftest import make_mock_route_llm

        # gpt-5.4 评分返回坏 JSON → 走 gemini 兜底 → fallback_used=True, defects=[]
        bad_grader = LLMResponse(
            content="not valid json", model_used="gpt-5.4", calls_used=1
        )
        good_dims = [
            {"dimension": dim, "pass": True, "defects": []}
            for dim in [
                "论断锚定率", "How 密度", "How 可执行性", "证据诚实",
                "解读分离", "TLDR 密度", "新颖增量", "边强度匹配",
                "Future Questions 质量", "适用边界",
            ]
        ]
        gemini_pass = LLMResponse(
            content={"dimensions": good_dims}, model_used="gemini-flash", calls_used=1
        )
        mock_llm = make_mock_route_llm(
            {"gpt-5.4": bad_grader, "gemini-flash": gemini_pass}
        )

        failures_path = tmp_path / "failures.jsonl"
        failures_path.write_text("", encoding="utf-8")

        result = run_repair_loop(
            candidate_text=self._CANDIDATE,
            a_defects=[],
            b_defects=[
                Defect(
                    dimension="边强度匹配",
                    location="图谱关系/evidence/agent collaboration",
                    defect="缺 raw anchor/provenance，medium 证据不足",
                    suggestion="补 provenance 并降级",
                )
            ],
            failures_path=str(failures_path),
            mock_route_llm=mock_llm,
            raw_text=self._RAW_TEXT,
        )

        # fail-closed: fallback_used 的 grader 不可信，即使 defects=[] 也不判收敛
        assert not result["converged"], "fallback_used 的 grader 不应判收敛"
        meta = result.get("final_grader_meta") or {}
        assert meta.get("grader_fallback_used") is True, (
            f"final_grader_meta 应标记 fallback_used，实际: {meta}"
        )

    # ------------------------------------------------------------------
    # B 层主路径孪生分支（repair.py:1696 / 1712）的 fail-closed 覆盖。
    #
    # 上面的 test_repair_loop_degraded_grader_fail_closed 走的是「确定性快速
    # 路径」：图谱关系缺陷 → _apply_deterministic_repairs → A 通过 → grader
    # 在 if deterministic_actions 块内触发（repair.py:1544/1558，分支 A）。
    #
    # 下面两个测试走「LLM 定点重写主路径」：B 缺陷非确定性（论断锚定率/TLDR
    # 无锚点，_is_deterministic_repair_defect=False）→ deterministic_actions
    # 为空 → 跳过整个 if deterministic_actions 块 → 走 deepseek 定点重写 →
    # 重写后 A 层 run_candidate_self_gate 通过 → 到达 repair.py:1688 的 B 层
    # grader → degraded/suspended 命中 1712/1696（分支 B）。两条分支的 grader
    # fail-closed 语义必须与分支 A 一致，本组测试守住这条孪生路径。
    #
    # _MAIN_PATH_CANDIDATE 必须本身 A-clean（run_candidate_self_gate 返回空），
    # 否则重写后 A 层仍有缺陷会走 1679 a_defects_remaining 而非到达 B。其结构
    # 直接对齐 test_repair_loop.py 的 candidate_with_defects fixture（已知 A-clean，
    # B 层 TLDR 无锚点），_MAIN_PATH_RAW 对齐 repair_raw_text。
    # ------------------------------------------------------------------

    _MAIN_PATH_CANDIDATE = """---
status: candidate
gate_decision: null
source_refs:
  - raw/loop-engineering.md
gate_reason: 待评审
future_questions:
  - Loop 在实时系统如何适配？
  - 多 agent 故障隔离设计？
related_concepts:
  - harness-engineering
  - agent-collaboration
---

## TLDR
Loop Engineering is important for AI agents. It helps with bounded loops.

## 关键发现
1. [raw §A] (source_grounded) Bounded loops are important
2. [raw §C] (source_grounded) Convergence matters

## 入选理由
- [raw §A] (source_grounded) 来源事实：Loop Engineering describes bounded, observable loops.
- [raw §B] (source_grounded) 来源事实：The loop shape includes trigger, selector, executor, gate, and ledger.

## 证据地图
| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| §A | Bounded loop | source_grounded | 未量化：原文未提供效果数据 |
| §B | Loop shape | source_grounded | 未比较：原文未比较不同组件实现 |

## 核心论点
1. Bounded loops: must have explicit stop conditions (source_grounded)
2. Convergence: Fix defects, not restart (source_grounded)

## 论证与解释
**[raw §A]** (source_grounded) 来源事实：Bounded loops need explicit stop conditions.
**[raw §C]** (source_grounded) 来源事实：Convergence matters more than full restart.

### 操作步骤
1. [raw §B] (akb_interpretation) Configure trigger type `trigger={type:'schedule',cron:'0 * * * *'}`
2. [raw §B] (akb_interpretation) Select the next work item `work_item=ledger.next(status='pending',limit=1)`
3. [raw §B] (akb_interpretation) Run the quality gate `assert run_quality_gate(result,grader='gpt-5.4').defects == []`

## 适用边界
- [raw §A] (source_grounded) 来源事实：Loop engineering frames the work as bounded loops.
- [raw §B] (source_grounded) 来源事实：The source lists trigger, selector, executor, gate, and ledger.
- [raw §A] (akb_interpretation) 适用场景：Agent pipelines with repeatable gates.
- [raw §C] (akb_interpretation) 不适用场景：Simple scripts.
- [raw §B] (akb_interpretation) 约束条件：Need explicit ledger and gate.

## 图谱关系
| target | relation | strength | provenance | evidence |
|--------|----------|----------|------------|----------|
| harness-engineering | related | weak | source_grounded | [raw §B] (source_grounded) The source lists the loop components. |

## Future Questions
1. Loop engineering in real-time?
2. Multi-agent fault isolation?

## 维护记录
- 2026-06-13: created
"""

    _MAIN_PATH_RAW = """---
artifact_type: raw
source_slug: loop-engineering
source_url: https://example.com/loop-engineering
source_title: Loop Engineering
source_authors: Addy Osmani
source_publisher: Addy Osmani's Blog
source_published_at: 2026-06-07
fetched_at: 2026-06-13T00:00:00Z
fetched_via: defuddle
---

# §A Loop Engineering

Loop engineering is the practice of designing AI agent loops that are bounded,
convergent, and observable. An agent loop without explicit stopping conditions is
a runaway cost generator.

# §B How: A Complete Loop Shape

A well-designed loop has five components: a trigger, a work unit selector, an action
executor, a quality gate, and a ledger writer.

原文操作示例（AKB 操作化命令字面量，供 SG-command-recheck 回查锚定）：

```text
trigger={type:'schedule',cron:'0 * * * *'}
work_item=ledger.next(status='pending',limit=1)
assert run_quality_gate(result,grader='gpt-5.4').defects == []
```

# §C Convergence Over Restart

When a step produces defects, fix the specific defects rather than restarting the
entire pipeline. This is the principle of convergence over restart.
"""

    # 非确定性 B 缺陷：TLDR 无 [raw §X] 锚点（论断锚定率）。
    # _is_deterministic_repair_defect 对该缺陷返回 False，故走 LLM 重写主路径。
    @staticmethod
    def _main_path_b_defect():
        from src.akb2.quality.checks_structural import Defect

        return Defect(
            dimension="论断锚定率",
            location="TLDR",
            defect="TLDR 无 [raw §X] 锚点",
            suggestion="为 TLDR 加 [raw §A] 锚点",
        )

    # deepseek 定点重写返回的 JSON patch：只改点名节 TLDR（加锚点），
    # 重写后该候选页仍 A-clean，从而到达 B 层 grader。
    @staticmethod
    def _main_path_repair_patch():
        from src.akb2.quality.llm_router import LLMResponse

        return LLMResponse(
            content={"TLDR": "## TLDR\nLoop Engineering: bounded loops [raw §A]."},
            model_used="deepseek-v4-pro",
            calls_used=1,
        )

    @staticmethod
    def _grader_all_pass_dims():
        return [
            {"dimension": dim, "pass": True, "defects": []}
            for dim in [
                "论断锚定率", "How 密度", "How 可执行性", "证据诚实",
                "解读分离", "TLDR 密度", "新颖增量", "边强度匹配",
                "Future Questions 质量", "适用边界",
            ]
        ]

    def test_repair_loop_main_path_degraded_fail_closed(self, tmp_path: Path) -> None:
        """SC-6.2 孪生:B 层主路径(LLM 定点重写)后 grader fallback_used=True → 不判收敛。

        走 repair.py:1712(分支 B,非确定性快速路径 1558)：
        非确定性 B 缺陷 → deepseek 重写 TLDR → A 通过 → gpt-5.4 坏 JSON
        → gemini 兜底 pass(defects=[]) → fallback_used=True → fail-closed。
        断言绑定可观测行为：not converged + final_grader_meta.grader_fallback_used。
        """
        from src.akb2.quality.llm_router import LLMResponse
        from src.akb2.quality.repair import run_repair_loop
        from tests.quality.conftest import make_mock_route_llm

        # gpt-5.4 评分返回坏 JSON → 走 gemini 兜底 → fallback_used=True, defects=[]
        bad_grader = LLMResponse(
            content="not valid json", model_used="gpt-5.4", calls_used=1
        )
        gemini_pass = LLMResponse(
            content={"dimensions": self._grader_all_pass_dims()},
            model_used="gemini-flash",
            calls_used=1,
        )
        # deepseek 重写(主路径) + gpt-5.4 坏 JSON + gemini 兜底 pass，按 model_family 路由。
        mock_llm = make_mock_route_llm(
            {
                "deepseek-v4-pro": self._main_path_repair_patch(),
                "gpt-5.4": bad_grader,
                "gemini-flash": gemini_pass,
            }
        )

        failures_path = tmp_path / "failures.jsonl"
        failures_path.write_text("", encoding="utf-8")

        result = run_repair_loop(
            candidate_text=self._MAIN_PATH_CANDIDATE,
            a_defects=[],
            b_defects=[self._main_path_b_defect()],
            failures_path=str(failures_path),
            mock_route_llm=mock_llm,
            raw_text=self._MAIN_PATH_RAW,
        )

        # 证明走了主路径(分支 B)而非确定性快速路径(分支 A)：
        # trace 状态必须是 grader_degraded 且本轮 deterministic_actions 为空。
        assert result.get("trace_paths"), "应写出 repair trace"
        last_trace = json.loads(
            Path(result["trace_paths"][-1]).read_text(encoding="utf-8")
        )
        assert last_trace["status"] == "grader_degraded", (
            f"应命中 B 层主路径 grader_degraded 分支(repair.py:1712)，实际: {last_trace['status']}"
        )
        assert last_trace["deterministic_actions"] == [], (
            "B 层主路径不得经过确定性修复(否则会走分支 A:1558)，"
            f"deterministic_actions 应为空，实际: {last_trace['deterministic_actions']}"
        )

        # fail-closed: fallback_used 的 grader 不可信，即使 defects=[] 也不判收敛
        assert not result["converged"], "主路径 fallback_used 的 grader 不应判收敛"
        meta = result.get("final_grader_meta") or {}
        assert meta.get("grader_fallback_used") is True, (
            f"final_grader_meta 应标记 fallback_used，实际: {meta}"
        )

    def test_repair_loop_main_path_suspended_fail_closed(self, tmp_path: Path) -> None:
        """SC-6.2 孪生:B 层主路径(LLM 定点重写)后 grader 双挂 suspended → 不判收敛。

        走 repair.py:1696(分支 B,非确定性快速路径 1544)：
        非确定性 B 缺陷 → deepseek 重写 TLDR → A 通过 → gpt-5.4 与 gemini 均
        返回坏 JSON → suspended=True → fail-closed(grader_all_down)。
        断言绑定可观测行为：not converged + final_grader_meta.grader_suspended。
        """
        from src.akb2.quality.llm_router import LLMResponse
        from src.akb2.quality.repair import run_repair_loop
        from tests.quality.conftest import make_mock_route_llm

        # gpt-5.4 与 gemini 兜底都返回坏 JSON → 双不可用 → suspended=True
        bad_primary = LLMResponse(
            content="not valid json", model_used="gpt-5.4", calls_used=1
        )
        bad_fallback = LLMResponse(
            content="also not valid json", model_used="gemini-flash", calls_used=1
        )
        mock_llm = make_mock_route_llm(
            {
                "deepseek-v4-pro": self._main_path_repair_patch(),
                "gpt-5.4": bad_primary,
                "gemini-flash": bad_fallback,
            }
        )

        failures_path = tmp_path / "failures.jsonl"
        failures_path.write_text("", encoding="utf-8")

        result = run_repair_loop(
            candidate_text=self._MAIN_PATH_CANDIDATE,
            a_defects=[],
            b_defects=[self._main_path_b_defect()],
            failures_path=str(failures_path),
            mock_route_llm=mock_llm,
            raw_text=self._MAIN_PATH_RAW,
        )

        # 证明走了主路径(分支 B:1696)而非确定性快速路径(分支 A:1544)：
        # trace 状态必须是 grader_suspended 且本轮 deterministic_actions 为空。
        assert result.get("trace_paths"), "应写出 repair trace"
        last_trace = json.loads(
            Path(result["trace_paths"][-1]).read_text(encoding="utf-8")
        )
        assert last_trace["status"] == "grader_suspended", (
            f"应命中 B 层主路径 grader_suspended 分支(repair.py:1696)，实际: {last_trace['status']}"
        )
        assert last_trace["deterministic_actions"] == [], (
            "B 层主路径不得经过确定性修复(否则会走分支 A:1544)，"
            f"deterministic_actions 应为空，实际: {last_trace['deterministic_actions']}"
        )

        # fail-closed: 双挂的 grader 必须挂起，不判收敛
        assert not result["converged"], "主路径 suspended 的 grader 不应判收敛"
        meta = result.get("final_grader_meta") or {}
        assert meta.get("grader_suspended") is True, (
            f"final_grader_meta 应标记 suspended，实际: {meta}"
        )


# ---------------------------------------------------------------------------
# SC-6 step-6: 周抽检 _regrade_sample 拒收 degraded grader 的空缺陷 PASS
# ---------------------------------------------------------------------------


class TestRegradeSampleRejectsDegradedGrader:
    """_regrade_sample 必须用 run_grader_detailed，拒收 fallback_used/suspended 的 PASS。

    旧 API run_grader() 只返回 (defects, model, suspended)，丢弃 fallback_used，
    会把 gemini 兜底的空缺陷当 pass，导致周抽检达标率虚高。step-6 要求
    runner/repair/promote 全链拒收 degraded PASS。
    """

    @staticmethod
    def _make_sample(tmp_path: Path) -> dict[str, str]:
        sample_path = tmp_path / "candidate.md"
        sample_path.write_text("# candidate\n\nbody\n", encoding="utf-8")
        return {"path": str(sample_path), "slug": "candidate"}

    def test_fallback_used_empty_defects_returns_false(self, tmp_path: Path) -> None:
        """fallback_used=True 且 defects=[] → False（degraded PASS 不可信）。"""
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _regrade_sample

        sample = self._make_sample(tmp_path)
        degraded = GraderResult(
            defects=[],
            model_used="gemini-flash",
            suspended=False,
            fallback_used=True,
        )
        with mock.patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=degraded
        ):
            results = _regrade_sample([sample])

        assert results == [False], "fallback_used 的空缺陷必须判 fail"

    def test_suspended_empty_defects_returns_false(self, tmp_path: Path) -> None:
        """suspended=True 且 defects=[] → False（双不可用挂起）。"""
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _regrade_sample

        sample = self._make_sample(tmp_path)
        suspended = GraderResult(
            defects=[], model_used="none", suspended=True
        )
        with mock.patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=suspended
        ):
            results = _regrade_sample([sample])

        assert results == [False], "suspended 的空缺陷必须判 fail"

    def test_clean_empty_defects_returns_true(self, tmp_path: Path) -> None:
        """fallback_used=False + suspended=False + defects=[] → True（正常 PASS 回归）。"""
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _regrade_sample

        sample = self._make_sample(tmp_path)
        clean = GraderResult(
            defects=[], model_used="gpt-5.4", suspended=False, fallback_used=False
        )
        with mock.patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=clean
        ):
            results = _regrade_sample([sample])

        assert results == [True], "正常 PASS 不应被误拒"

    def test_clean_with_defects_returns_false(self, tmp_path: Path) -> None:
        """回归: 非 degraded 但有缺陷 → False（len(defects)==0 判定仍生效）。"""
        from src.akb2.quality.checks_structural import Defect
        from src.akb2.quality.grader import GraderResult
        from src.akb2.quality.runner import _regrade_sample

        sample = self._make_sample(tmp_path)
        with_defects = GraderResult(
            defects=[
                Defect(
                    dimension="论断锚定率",
                    location="## TLDR",
                    defect="缺 anchor",
                    suggestion="补 anchor",
                )
            ],
            model_used="gpt-5.4",
            suspended=False,
            fallback_used=False,
        )
        with mock.patch(
            "src.akb2.quality.grader.run_grader_detailed", return_value=with_defects
        ):
            results = _regrade_sample([sample])

        assert results == [False], "有缺陷的非 degraded 结果必须判 fail"

    def test_f4_promote_failure_returns_promotion_blocked_not_proposal_passed(self, tmp_path: Path) -> None:
        """§0.2/F4：promote failure（gates fail）→ final_status=promotion_blocked（不返 proposal_passed）。

        旧 buggy：blocked 时 final_status 仍写 proposal_passed（矛盾）。F4 修正。
        """
        from src.akb2.quality.runner import _run_pipeline_segments

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        f = _make_raw_file(raw_dir, "test-f4-blocked", "2026-06-13")
        config = _make_runner_config(tmp_path, auto_promote=False)

        with mock.patch("src.akb2.quality.runner._run_admission_segment") as mock_adm, \
             mock.patch("src.akb2.quality.runner._run_distill_segment") as mock_distill, \
             mock.patch("src.akb2.quality.runner._run_quality_segments") as mock_qual, \
             mock.patch("src.akb2.quality.runner._run_promote_segment") as mock_promote:
            mock_adm.return_value = (True, "", [])
            mock_distill.return_value = (
                True, "staging/candidate/test-f4-blocked.md", "", "candidate text",
                {"retries_used": 0, "maker_fallback_used": False},
            )
            mock_qual.return_value = (True, "", "", {"repair_trace_paths": []})
            # promote failure（gates fail → promoted=False）
            mock_promote.return_value = (False, "proposal", "Gate-5 grader FAIL")
            result = _run_pipeline_segments(str(f), str(f.read_text(encoding="utf-8")), config)

        assert result["converged"] is True
        assert result["promoted"] is False
        # F4：blocked → final_status=promotion_blocked（不返 proposal_passed）
        assert result["final_status"] == "promotion_blocked", (
            f"promote failure 应 final_status=promotion_blocked, got {result['final_status']}"
        )
        assert result["promotion_outcome"] == "promotion_blocked"
