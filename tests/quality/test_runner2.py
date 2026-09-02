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
    LOOP2_EXTRACT_ATTEMPT_TIMEOUT_S,
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
    """attempt 必须盖住 claims+how_params 两段 HTTP；账本 segment 记真实 per-call。"""
    from src.akb2.quality.distill import (
        DISTILL_EXTRACT_ATTEMPT_SLACK_S,
        DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S,
        EXTRACT_SEGMENT_TIMEOUT_S,
    )

    assert DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S == (
        2 * EXTRACT_SEGMENT_TIMEOUT_S + DISTILL_EXTRACT_ATTEMPT_SLACK_S
    )
    assert LOOP2_EXTRACT_SEGMENT_TIMEOUT_S == EXTRACT_SEGMENT_TIMEOUT_S
    assert LOOP2_EXTRACT_ATTEMPT_TIMEOUT_S == DISTILL_EXTRACT_ATTEMPT_TIMEOUT_S


def test_loop2_pipeline_raises_http_segment_timeout():
    """实跑 abort：HTTP 仍 180s，段预算 380 救不了。包装脚本须抬 AKB2_EXTRACT_SEGMENT_TIMEOUT_S。"""
    wrapper = Path(__file__).resolve().parents[2] / "scripts/akb2-loop2-pipeline.sh"
    text = wrapper.read_text(encoding="utf-8")
    assert "AKB2_EXTRACT_SEGMENT_TIMEOUT_S" in text
    assert "360" in text


def test_loop2_abort_details_use_loop2_segment_timeout():
    """runner2 abort 必须写真实 HTTP 段超时，不得把 attempt 380 冒充 segment。"""
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    assert 'details["segment_timeout_s"] = LOOP2_EXTRACT_SEGMENT_TIMEOUT_S' in text
    assert 'details["attempt_timeout_s"] = LOOP2_EXTRACT_ATTEMPT_TIMEOUT_S' in text
    assert "LOOP2_EXTRACT_SEGMENT_TIMEOUT_S = EXTRACT_SEGMENT_TIMEOUT_S" in text


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
    assert "loop_id=LOOP2_LOOP_ID" in maker
    assert '"model_used"' in text
    assert "maker_fallback_used" in text
    assert 'extract_result.data or {}).get("content_lane")' in maker
    assert "loop_id=LOOP2_LOOP_ID" in text.split("def main", 1)[1]


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
    assert "skipped_prefilter_budget" in src
    assert "intake_x_prefilter_budget" in src
    assert "quality_ledger_path=args.quality_ledger" in src
    assert 'add_argument("--quality-ledger", default=None)' in src
    assert "args.quality_ledger or None" not in src


def test_admission_bad_index_preserves_loop_id(tmp_path: Path, monkeypatch):
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
    )
    from src.akb2.quality.runner import LANE_INDEX_STATUS_EMPTY, _run_admission_segment

    passed, reason, *_rest = _run_admission_segment(
        str(tmp_path / "raw.md"),
        "body",
        config,
        index_status=LANE_INDEX_STATUS_EMPTY,
        content_hash="abc",
        loop_id="loop2",
    )
    assert passed is False
    assert "novelty_index_unavailable" in reason
    assert captured[0]["loop_id"] == "loop2"


def test_runner2_maker_fallback_passes_loop2():
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/runner2.py"
    text = src.read_text(encoding="utf-8")
    maker = text.split("def _run_maker2_segments", 1)[1].split("\ndef ", 1)[0]
    assert "loop_id=LOOP2_LOOP_ID" in maker


def test_llm_gateway_429_carries_loop_id():
    src = Path(__file__).resolve().parents[2] / "src/akb2/quality/llm_router.py"
    text = src.read_text(encoding="utf-8")
    fn = text.split("def _append_quality_budget_event", 1)[1].split("\ndef ", 1)[0]
    assert 'loop_id: str = "loop1"' in fn
    assert '"loop_id": loop_id or "loop1"' in fn
    assert "loop_id=loop_id" in text.split("if diagnostics.provider_code == PROVIDER_CODE_429", 1)[1][:800]


def test_runner2_fixpoint_follows_compose_demoted_lane(tmp_path: Path, monkeypatch):
    """compose 把 tutorial 降成 pattern 后，fixpoint 不得再按 tutorial 卡 SG-how-density。"""
    from types import SimpleNamespace

    from src.akb2.quality import runner2 as r2
    from src.akb2.quality.runner import RunnerConfig

    captured: dict[str, str] = {}

    class _Extract:
        success = True
        error = ""
        retries_used = 0
        data = {"content_lane": "tutorial", "how_steps": [{"text": "one"}]}

    def fake_compose(extract_result, raw_path, batch_id, **kwargs):
        extract_result.data["content_lane"] = "pattern"
        cand = tmp_path / "cand.md"
        cand.write_text("---\ncontent_lane: pattern\n---\n# body\n", encoding="utf-8")
        return SimpleNamespace(
            success=True,
            error="",
            candidate_path=str(cand),
            frontmatter={"content_lane": "pattern"},
        )

    monkeypatch.setattr(
        r2.loop1_runner, "_run_extract_claims_worker", lambda *a, **k: _Extract()
    )
    monkeypatch.setattr(
        r2,
        "fulfill_extract_contract",
        lambda data, raw, content_lane="concept": SimpleNamespace(
            floor_passed=True,
            floor_reason="",
            extract_data=data,
            fabricated=False,
            unanchored=[],
        ),
    )
    monkeypatch.setattr("src.akb2.quality.distill.compose_candidate", fake_compose)
    monkeypatch.setattr(
        r2,
        "maker2_compose_candidate",
        lambda frontmatter, body, candidate_path=None, write=False: SimpleNamespace(
            success=True,
            error="",
            page_text="---\ncontent_lane: pattern\n---\n# body\n",
            frontmatter=frontmatter,
            frontmatter_degraded=False,
        ),
    )

    def fake_fixpoint(page_text, **kwargs):
        captured["content_lane"] = kwargs["content_lane"]
        return SimpleNamespace(success=True, text=page_text, rounds=0, reason="")

    monkeypatch.setattr(r2, "run_fixpoint", fake_fixpoint)

    config = RunnerConfig(
        raw_dir=str(tmp_path),
        ledger_path=str(tmp_path / "quality-ledger.jsonl"),
        staging_dir=str(tmp_path),
        vault_root=str(tmp_path),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(tmp_path),
    )
    raw = tmp_path / "raw.md"
    raw.write_text("body", encoding="utf-8")
    ok, _path, err, _text, meta = r2._run_maker2_segments(
        str(raw),
        "body",
        config,
        content_lane="tutorial",
        known_concepts=[],
        pending_queue=[],
    )
    assert ok is True, err
    assert captured["content_lane"] == "pattern"
    assert meta["content_lane"] == "pattern"


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
    assert '"candidate_path": candidate_path' in text
    assert text.count('"candidate_path": candidate_path') >= 2
