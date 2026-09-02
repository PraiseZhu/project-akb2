"""loop2 runner：loop1 账本默认不变，loop2 事件打标，1969-1984 谓词未改。"""

from __future__ import annotations

from pathlib import Path

from src.akb2.quality.admission import _log_rejection
from src.akb2.quality.backfill_requeue import build_requeue_event
from src.akb2.quality.llm_router import _append_route_switch_event, _record_article_budget_exceeded
from src.akb2.quality.promote import _write_ledger_event
from src.akb2.quality.requeue import run_requeue
from src.akb2.quality.runner import RunnerConfig, _log_event
from src.akb2.quality.runner2 import (
    C1_LAUNCHD_BOOTIN,
    LOOP2_EXTRACT_SEGMENT_TIMEOUT_S,
    LOOP2_LOOP_ID,
    LOOP2_PROCESSING_LOCK_NAME,
    LOOP2_SHADOW_PROMOTE_ENV,
    _log2,
    _shadow_promote_enabled,
    _split_frontmatter,
)


def test_c1_placeholder_not_bootin():
    assert C1_LAUNCHD_BOOTIN is False
    assert LOOP2_PROCESSING_LOCK_NAME == "loop2-processing.lock"
    assert LOOP2_PROCESSING_LOCK_NAME != "processing.lock"


def test_shadow_promote_default_on(monkeypatch):
    monkeypatch.delenv(LOOP2_SHADOW_PROMOTE_ENV, raising=False)
    assert _shadow_promote_enabled() is True
    monkeypatch.setenv(LOOP2_SHADOW_PROMOTE_ENV, "0")
    assert _shadow_promote_enabled() is False
    monkeypatch.setenv(LOOP2_SHADOW_PROMOTE_ENV, "1")
    assert _shadow_promote_enabled() is True


def test_runner2_defaults_to_loop2_dirs():
    """裸 CLI 不得落到 loop1 的 raw/staging。"""
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    assert 'os.path.join(vault_root, "raw-loop2")' in text
    assert 'os.path.join(vault_root, "staging-loop2")' in text
    assert 'os.path.join(vault_root, "raw")' not in text
    wrapper = Path(__file__).resolve().parents[2] / "scripts/akb2-loop2-pipeline.sh"
    sh = wrapper.read_text(encoding="utf-8")
    assert "raw-loop2" in sh
    assert "staging-loop2" in sh
    assert "-m src.akb2.quality.runner2" in sh


def test_loop2_extract_budget_covers_one_distill_attempt():
    """runner2 段预算不得短于 distill 单次 attempt（380s），否则会掐死可成功的慢抽取。"""
    from src.akb2.quality.distill import DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S

    assert LOOP2_EXTRACT_SEGMENT_TIMEOUT_S >= DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S


def test_loop2_pipeline_raises_http_segment_timeout():
    """实跑 abort：HTTP 仍 180s，段预算 380 救不了。包装脚本须抬 AKB2_EXTRACT_SEGMENT_TIMEOUT_S。"""
    wrapper = Path(__file__).resolve().parents[2] / "scripts/akb2-loop2-pipeline.sh"
    text = wrapper.read_text(encoding="utf-8")
    assert "AKB2_EXTRACT_SEGMENT_TIMEOUT_S" in text
    assert "360" in text


def test_loop2_abort_details_use_loop2_segment_timeout():
    """loop1 helper 默认写 180；runner2 abort 必须覆盖成 380，否则账本撒谎。"""
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    assert 'details["segment_timeout_s"] = LOOP2_EXTRACT_SEGMENT_TIMEOUT_S' in text


def test_grader_fallback_predicate_unedited():
    root = Path(__file__).resolve().parents[2]
    lines = (root / "src/akb2/quality/runner.py").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "if grader_result.fallback_used:" in line)
    block = "\n".join(lines[start : start + 14])
    assert "grader_fallback_not_allowed" in block
    assert "Gemini fallback 只记录诊断，不能作为通过条件" in block
    assert "return (" in block


def test_loop1_log_event_default(tmp_path: Path, monkeypatch):
    captured: list[dict] = []

    def fake_append(_path, entry):
        captured.append(entry)
        return True

    monkeypatch.setattr("src.akb2.loops.ledger.append_event", fake_append)
    config = RunnerConfig(
        raw_dir=str(tmp_path),
        ledger_path=str(tmp_path / "quality-ledger.jsonl"),
        staging_dir=str(tmp_path),
        vault_root=str(tmp_path),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(tmp_path),
        lock_dir=str(tmp_path),
    )
    _log_event(config, "intake_raw", "intake_raw", "abc")
    assert captured[0]["loop_id"] == "loop1"


def test_loop2_events_tagged(tmp_path: Path, monkeypatch):
    captured: list[dict] = []

    def fake_append(_path, entry):
        captured.append(entry)
        return True

    monkeypatch.setattr("src.akb2.loops.ledger.append_event", fake_append)
    config = RunnerConfig(
        raw_dir=str(tmp_path),
        ledger_path=str(tmp_path / "quality-ledger.jsonl"),
        staging_dir=str(tmp_path),
        vault_root=str(tmp_path),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(tmp_path),
        lock_dir=str(tmp_path),
        lock_name=LOOP2_PROCESSING_LOCK_NAME,
    )
    _log2(config, "intake_raw", "intake_raw", "abc")
    assert captured[0]["loop_id"] == LOOP2_LOOP_ID
    assert config.lock_name == LOOP2_PROCESSING_LOCK_NAME


def test_split_frontmatter():
    text = "---\nslug: x\n---\n\n# body\n"
    fm, body = _split_frontmatter(text)
    assert fm["slug"] == "x"
    assert body.startswith("# body")


def test_loop_id_defaults_on_helpers(tmp_path: Path, monkeypatch):
    captured: list[dict] = []

    def fake_append(_path, entry):
        captured.append(entry)
        return True

    monkeypatch.setattr("src.akb2.loops.ledger.append_event", fake_append)
    monkeypatch.setattr(
        "src.akb2.quality.llm_router._quality_ledger_path",
        lambda: tmp_path / "quality-ledger.jsonl",
    )
    monkeypatch.setattr(
        "src.akb2.quality.promote._resolve_ledger_path",
        lambda *_a, **_k: tmp_path / "quality-ledger.jsonl",
    )
    from src.akb2.quality.admission import AdmissionCheck
    from src.akb2.quality.backfill_requeue import Candidate

    check = AdmissionCheck(name="topic_relevance", passed=False, reason="x")
    _log_rejection(
        "raw.md",
        [check],
        str(tmp_path / "quality-ledger.jsonl"),
    )
    _write_ledger_event(tmp_path, "promote", "promote", "hash")
    _record_article_budget_exceeded(
        article_key="k", model_family="gpt-5.6-luna", consumer="t", limit=5
    )
    _append_route_switch_event(
        model_family="gpt-5.6-luna",
        from_model="codex/gpt-5.6-luna",
        to_model="gpt-5.6-luna",
        consumer="t",
    )
    event = build_requeue_event(
        Candidate(
            content_hash="h",
            first_hit_ts="t",
            hit_count=1,
            error_excerpt="e",
            raw_filename="f",
        ),
        "2026-09-01T00:00:00Z",
    )
    assert event["loop_id"] == "loop1"
    ids = {row.get("loop_id") for row in captured}
    assert ids == {"loop1"}
    _log_rejection(
        "raw.md",
        [check],
        str(tmp_path / "quality-ledger.jsonl"),
        loop_id="loop2",
    )
    assert captured[-1]["loop_id"] == "loop2"


def test_fail_closed_segment_shape():
    from src.akb2.quality.runner import RunnerConfig
    from src.akb2.quality.runner2 import _run_pipeline_segments

    # 文件不存在的路径由 process_one 处理；这里只保证导入七段入口存在。
    assert callable(_run_pipeline_segments)
    assert callable(run_requeue)
    assert RunnerConfig.__dataclass_fields__["lock_name"].default == ""


def test_loop2_abort_logs_defect_summary():
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    assert 'abort_details["defects"]' in text
    assert "final_defects_summary" in text


def test_runner2_uses_extract_worker_and_maker_fallback():
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    assert "_run_extract_claims_worker" in text
    assert "build_deterministic_extract_result" in text
    assert 'os.environ.get("AKB2_MAKER_FALLBACK"' in text
    maker = text.split("def _run_maker2_segments", 1)[1].split("\ndef ", 1)[0]
    assert "extract_claims(" not in maker


def test_runner2_requeue_and_grader_carry_loop2():
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    assert "loop_id=LOOP2_LOOP_ID" in text
    assert "grader_timeout_s=float(LOOP2_GRADER_TIMEOUT_S)" in text
    assert text.count("loop_id=LOOP2_LOOP_ID") >= 4


def test_intake_cli_defaults_quality_ledger():
    src = (
        Path(__file__).resolve().parents[2]
        / "src/akb2/quality/intake_x/orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "quality_ledger_path is None" in src
    assert "QUALITY_LEDGER_FILE" in src
    assert "--quality-ledger" in src


def test_shadow_path_skips_run_promote():
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    assert "evaluate_five_gates" in text
    assert 'promotion_mode": "shadow"' in text
    assert "_shadow_promote_enabled()" in text
    # 影子跑必须在调用 _run_promote_segment 之前分支，否则会写 wiki。
    shadow_idx = text.index("_shadow_promote_enabled()")
    promote_idx = text.index("_run_promote_segment(")
    assert shadow_idx < promote_idx
