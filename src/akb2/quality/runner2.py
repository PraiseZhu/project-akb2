"""loop2 七段 runner。段 3-4 走 maker2；repair 换成定点修；独立锁 loop2-processing.lock。

C1 本轮只做可配置占位，不注册 launchd、不 bootin。
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.akb2.quality import runner as loop1_runner
from src.akb2.quality.maker2 import (
    compose_candidate as maker2_compose_candidate,
)
from src.akb2.quality.maker2 import fulfill_extract_contract, run_fixpoint
from src.akb2.quality.runner import (
    AVOID_WINDOWS,
    DISTILL_MAX_RETRIES,
    DISTILL_MODEL_FAMILY,
    FAILURE_KIND_CONTENT,
    LANE_INDEX_STATUS_OK,
    RunnerConfig,
    _acquire_processing_lock,
    _admission_failure_probe,
    _check_cross_machine_safety,
    _classify_failure_kind,
    _compute_raw_hash,
    _derive_raw_slug,
    _distill_abort_details,
    _distill_extract_worker_timeout_s,
    _existing_source_page_for_raw,
    _extract_max_attempts,
    _install_signal_handlers,
    _load_vault_concept_index,
    _log_event,
    _log_ts,
    _maybe_auto_infra_requeue,
    _release_processing_lock,
    _run_admission_segment,
    _run_promote_segment,
    _run_quality_segments,
    _run_weekly_spot_check,
    _scan_queue,
    _should_avoid,
)

LOOP2_POLL_INTERVAL_SECONDS = 300
LOOP2_ARTICLE_BUDGET_SECONDS = 900
LOOP2_EXTRACT_SEGMENT_TIMEOUT_S = 380.0
LOOP2_GRADER_TIMEOUT_S = 180
LOOP2_AUTO_INFRA_REQUEUE_MAX = 1
LOOP2_LOOP_ID = "loop2"
LOOP2_PROCESSING_LOCK_NAME = "loop2-processing.lock"
C1_LAUNCHD_BOOTIN = False  # 常量仍表示「主仓未批准生产 bootin」；本机 Pro 影子加载另记观察卡
# S1 影子跑：六闸仍判，但不写 wiki/sources。C1 未确认前默认开。
LOOP2_SHADOW_PROMOTE_ENV = "AKB2_LOOP2_SHADOW_PROMOTE"


def _shadow_promote_enabled() -> bool:
    """S1 默认开；显式 AKB2_LOOP2_SHADOW_PROMOTE=0 才允许写 vault。"""
    return os.environ.get(LOOP2_SHADOW_PROMOTE_ENV, "1") != "0"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    import yaml

    raw_fm = text[4:end]
    body = text[end + 5 :]
    parsed = yaml.safe_load(raw_fm) or {}
    if not isinstance(parsed, dict):
        return {}, text
    return parsed, body.lstrip("\n")


def _log2(
    config: RunnerConfig,
    event: str,
    node: str,
    content_hash: str,
    details: dict | None = None,
) -> bool:
    return _log_event(
        config, event, node, content_hash, details, loop_id=LOOP2_LOOP_ID
    )


def _run_maker2_segments(
    raw_path: str,
    raw_content: str,
    config: RunnerConfig,
    *,
    content_lane: str,
    known_concepts: list[str] | None,
    pending_queue: list[str] | None,
) -> tuple[bool, str, str, str | None, dict[str, Any]]:
    """段 3-4：extract_claims → fulfill_extract_contract → distill compose
    → maker2.compose_candidate → run_fixpoint。不复制 loop1 F4 增援 / procedural H1 / retake。
    """
    from src.akb2.quality.distill import (
        build_deterministic_extract_result,
        compose_candidate,
    )

    extract_result = loop1_runner._run_extract_claims_worker(
        raw_content, content_lane, loop_id=LOOP2_LOOP_ID
    )
    maker_fallback_used = False
    maker_primary_error = ""
    if not extract_result.success and os.environ.get("AKB2_MAKER_FALLBACK", "1") != "0":
        maker_primary_error = extract_result.error
        extract_result = build_deterministic_extract_result(
            raw_content,
            raw_path,
            content_lane=content_lane,
            loop_id=LOOP2_LOOP_ID,
        )
        maker_fallback_used = extract_result.success
    if not extract_result.success:
        return (
            False,
            f"extract_failed: {extract_result.error}",
            extract_result.error,
            None,
            {
                "failed_node": "distill_extract",
                "retries_used": extract_result.retries_used,
                "maker_fallback_used": maker_fallback_used,
                "maker_primary_error": maker_primary_error,
            },
        )

    content_lane = str(
        (extract_result.data or {}).get("content_lane") or content_lane or ""
    )
    contract = fulfill_extract_contract(
        extract_result.data, raw_content, content_lane=content_lane or "concept"
    )
    if not contract.floor_passed:
        return (
            False,
            f"extract_contract_failed: {contract.floor_reason}",
            contract.floor_reason,
            None,
            {
                "failed_node": "integrity_floor",
                "fabricated": contract.fabricated,
                "unanchored": contract.unanchored,
            },
        )
    extract_result.data = contract.extract_data
    floor_result = type(
        "FloorResult",
        (),
        {"passed": True, "failed_floors": [], "reason": "", "meta": {}},
    )()

    batch_id = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    compose_result = compose_candidate(
        extract_result,
        raw_path,
        batch_id,
        output_dir=str(Path(config.staging_dir) / "candidate"),
        raw_content=raw_content,
        content_lane=content_lane,
        known_concepts=known_concepts,
        pending_queue=pending_queue,
        loop_id=LOOP2_LOOP_ID,
    )
    if not compose_result.success:
        return (
            False,
            f"compose_failed: {compose_result.error}",
            compose_result.error,
            None,
            {"failed_node": "distill_compose"},
        )

    candidate_path = Path(compose_result.candidate_path)
    try:
        page_text = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"read_failed: {exc}", str(exc), None, {"failed_node": "distill_compose"}

    frontmatter, body = _split_frontmatter(page_text)
    if not frontmatter:
        frontmatter = dict(compose_result.frontmatter)
        body = page_text
    budgeted = maker2_compose_candidate(
        frontmatter, body, candidate_path=candidate_path, write=True
    )
    if not budgeted.success:
        return (
            False,
            f"compose2_failed: {budgeted.error}",
            budgeted.error,
            None,
            {"failed_node": "distill_compose"},
        )
    page_text = budgeted.page_text or candidate_path.read_text(encoding="utf-8")

    fix = run_fixpoint(
        page_text,
        raw_content=raw_content,
        content_lane=content_lane,
        pending_queue=pending_queue,
        known_concepts=known_concepts,
    )
    candidate_path.write_text(fix.text, encoding="utf-8")
    if not fix.success:
        return (
            False,
            f"fixpoint_failed: {fix.reason}",
            fix.reason,
            fix.text,
            {
                "failed_node": "repair_loop",
                "fixpoint_rounds": fix.rounds,
                "floor_result": floor_result,
            },
        )
    return (
        True,
        str(candidate_path),
        "",
        fix.text,
        {
            "retries_used": extract_result.retries_used,
            "content_lane": content_lane,
            "floor_result": floor_result,
            "fixpoint_rounds": fix.rounds,
            "frontmatter_degraded": bool(budgeted.frontmatter_degraded),
            "maker_fallback_used": maker_fallback_used,
            "maker_primary_error": maker_primary_error,
        },
    )


def _run_pipeline_segments(
    raw_path: str,
    raw_content: str,
    config: RunnerConfig,
) -> dict[str, Any]:
    content_hash = _compute_raw_hash(raw_path)
    rounds = 0
    article_started = time.monotonic()

    _log2(config, "intake_raw", "intake_raw", content_hash, {"raw_path": raw_path})

    existing_source = _existing_source_page_for_raw(raw_path, config)
    if existing_source is not None:
        details = {
            "reason": "duplicate_source",
            "raw_path": raw_path,
            "existing_source_path": str(existing_source),
            "slug": _derive_raw_slug(raw_path),
            "failure_kind": FAILURE_KIND_CONTENT,
        }
        _log2(config, "admission", "admission", content_hash, {"passed": False, **details})
        _log2(config, "abort", "admission", content_hash, details)
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "rejected",
            "error": f"准入未通过: duplicate_source: {existing_source}",
        }

    vault_index = _load_vault_concept_index(
        config, exclude_slug=_derive_raw_slug(raw_path)
    )
    admission_ret = _run_admission_segment(
        raw_path,
        raw_content,
        config,
        wiki_references=vault_index.get("wiki_references"),
        index_status=vault_index.get("status", LANE_INDEX_STATUS_OK),
        content_hash=content_hash,
        loop_id=LOOP2_LOOP_ID,
    )
    passed, reason = admission_ret[0], admission_ret[1]
    admission_checks = admission_ret[2] if len(admission_ret) > 2 else []
    content_lane = admission_ret[3] if len(admission_ret) > 3 else ""
    lane_reason = admission_ret[4] if len(admission_ret) > 4 else ""
    topic_borderline = admission_ret[5] if len(admission_ret) > 5 else False
    if not passed:
        admission_probe = _admission_failure_probe(reason, admission_checks, lane_reason)
        _log2(
            config,
            "abort",
            "admission",
            content_hash,
            {
                "reason": reason,
                "content_lane": content_lane,
                "lane_reason": lane_reason,
                "failure_kind": _classify_failure_kind(admission_probe),
            },
        )
        _maybe_auto_infra_requeue(
            config, content_hash, admission_probe, loop_id=LOOP2_LOOP_ID
        )
        error = reason if str(reason).startswith("准入未通过") else f"准入未通过: {reason}"
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "rejected",
            "error": error,
            "content_lane": content_lane,
            "failed_node": "admission",
        }

    _log2(
        config,
        "admission",
        "admission",
        content_hash,
        {
            "passed": True,
            "content_lane": content_lane,
            "lane_reason": lane_reason,
            "topic_borderline": bool(topic_borderline),
        },
    )

    _log2(
        config,
        "distill_extract",
        "distill_extract",
        content_hash,
        {
            "status": "start",
            "model_family": DISTILL_MODEL_FAMILY,
            "segment_timeout_s": LOOP2_EXTRACT_SEGMENT_TIMEOUT_S,
            "provider_max_retries": DISTILL_MAX_RETRIES,
            "extract_max_attempts": _extract_max_attempts(content_lane),
            "content_lane": content_lane,
        },
    )
    success, candidate_path, error_detail, candidate_text, distill_meta = _run_maker2_segments(
        raw_path,
        raw_content,
        config,
        content_lane=content_lane,
        known_concepts=vault_index.get("known_concepts"),
        pending_queue=vault_index.get("pending_queue"),
    )
    if not success:
        failed_node = distill_meta.get("failed_node", "distill_extract")
        details = _distill_abort_details(
            error_detail,
            failed_node,
            worker_timeout_s=_distill_extract_worker_timeout_s(raw_content),
            content_lane=content_lane,
        )
        details["segment_timeout_s"] = LOOP2_EXTRACT_SEGMENT_TIMEOUT_S
        details["failure_kind"] = _classify_failure_kind(error_detail)
        _log2(config, "abort", failed_node, content_hash, details)
        _maybe_auto_infra_requeue(
            config, content_hash, error_detail, loop_id=LOOP2_LOOP_ID
        )
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "failed",
            "error": f"提炼失败: {error_detail}",
            "content_lane": content_lane,
            "failed_node": failed_node,
            "error_class": "distill_failed",
        }

    _log2(
        config,
        "distill_extract",
        "distill_extract",
        content_hash,
        {
            "status": "success",
            "content_lane": content_lane,
            "maker_fallback_used": bool(distill_meta.get("maker_fallback_used")),
            "maker_primary_error": distill_meta.get("maker_primary_error", ""),
        },
    )
    _log2(
        config,
        "distill_compose",
        "distill_compose",
        content_hash,
        {
            "candidate_path": candidate_path,
            "fixpoint_rounds": distill_meta.get("fixpoint_rounds", 0),
        },
    )
    content_lane = str(distill_meta.get("content_lane") or content_lane or "")

    if time.monotonic() - article_started > LOOP2_ARTICLE_BUDGET_SECONDS:
        _log2(
            config,
            "abort",
            "runner2",
            content_hash,
            {"reason": "article_budget_exceeded", "limit_s": LOOP2_ARTICLE_BUDGET_SECONDS},
        )
        return {
            "converged": False,
            "rounds": 0,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "failed",
            "error": f"单篇超过 {LOOP2_ARTICLE_BUDGET_SECONDS}s",
            "failed_node": "runner2",
        }

    grader_result_path = (
        Path(config.grader_result_dir) / f"{Path(candidate_path).stem}.grader.json"
    )
    converged, error_class, error_detail, quality_meta = _run_quality_segments(
        candidate_text or "",
        candidate_path,
        raw_content,
        config,
        grader_result_path,
        content_lane=content_lane,
        floor_result=distill_meta.get("floor_result"),
        known_concepts=vault_index.get("known_concepts"),
        pending_queue=vault_index.get("pending_queue"),
        loop_id=LOOP2_LOOP_ID,
        grader_timeout_s=float(LOOP2_GRADER_TIMEOUT_S),
    )
    rounds = quality_meta.get("repair_rounds", 0) if quality_meta else 0
    _log2(
        config,
        "grade_structural",
        "grade_structural",
        content_hash,
        {
            "defect_count": quality_meta.get("a_defect_count", 0),
            "defects": quality_meta.get("a_defect_summary", []),
        }
        if quality_meta
        else {},
    )
    _log2(
        config,
        "grade_llm",
        "grade_llm",
        content_hash,
        {
            "defect_count": (
                quality_meta.get("b_defect_count", 0) if quality_meta else 0
            ),
            "fallback_used": (
                quality_meta.get("grader_fallback_used", False) if quality_meta else False
            ),
            "model_used": quality_meta.get("grader_model_used", "") if quality_meta else "",
            "primary_model": (
                quality_meta.get("grader_primary_model", "") if quality_meta else ""
            ),
            "fallback_model": (
                quality_meta.get("grader_fallback_model", "") if quality_meta else ""
            ),
            "suspended": (
                quality_meta.get("grader_suspended", False) if quality_meta else False
            ),
        },
    )
    if not converged:
        abort_details: dict[str, Any] = {
            "error_class": error_class,
            "error": error_detail,
        }
        if quality_meta:
            abort_details["defects"] = (
                quality_meta.get("final_defects_summary")
                or quality_meta.get("b_defect_summary")
                or []
            )
        _log2(
            config,
            "abort",
            "repair_loop" if error_class != "integrity_floor_failed" else "integrity_floor",
            content_hash,
            abort_details,
        )
        _maybe_auto_infra_requeue(
            config, content_hash, str(error_detail), loop_id=LOOP2_LOOP_ID
        )
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "rejected" if error_class == "integrity_floor_failed" else "failed",
            "error": error_detail,
            "content_lane": content_lane,
            "failed_node": "grader" if str(error_class).startswith("grader_") else "repair_loop",
            "error_class": error_class,
        }

    _log2(
        config,
        "repair_loop",
        "repair_loop",
        content_hash,
        {"converged": True, "repair_rounds": rounds},
    )
    if _shadow_promote_enabled():
        from src.akb2.quality.promote import evaluate_five_gates

        gates_ok, failed_gate, gates_detail = evaluate_five_gates(
            Path(candidate_path),
            Path(config.vault_root),
            grader_result_path,
            allow_proposed_node_only=True,
        )
        shadow_detail = (
            "ok" if gates_ok else f"{failed_gate}: {gates_detail}"
        )
        _log2(
            config,
            "promote",
            "promote",
            content_hash,
            {
                "mode": "shadow",
                "gates_ok": gates_ok,
                "detail": shadow_detail,
            },
        )
        _log2(
            config,
            "finalize",
            "finalize",
            content_hash,
            {"mode": "shadow", "gates_ok": gates_ok},
        )
        return {
            "converged": True,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "shadow",
            "final_status": "shadow_pass" if gates_ok else "shadow_blocked",
            "error": None if gates_ok else shadow_detail,
            "content_lane": content_lane,
            "failed_node": None if gates_ok else "promote",
            "error_class": None if gates_ok else "shadow_blocked",
        }

    promoted, promotion_mode, promote_detail = _run_promote_segment(
        candidate_path, config, grader_result_path, loop_id=LOOP2_LOOP_ID
    )
    if promoted:
        _log2(config, "promote", "promote", content_hash, {"mode": promotion_mode})
        _log2(config, "finalize", "finalize", content_hash, {"mode": promotion_mode})
        final_status = "promoted"
    else:
        _log2(config, "abort", "promote", content_hash, {"reason": promote_detail})
        final_status = "promotion_blocked"
    return {
        "converged": True,
        "rounds": rounds,
        "promoted": promoted,
        "promotion_mode": promotion_mode,
        "final_status": final_status,
        "error": None if promoted else promote_detail,
        "content_lane": content_lane,
        "failed_node": None,
        "error_class": None,
    }


def process_one(raw_path: str, config: RunnerConfig) -> dict[str, Any] | None:
    """公开单篇入口；复用 loop1 读文件，但七段走本模块。"""
    if loop1_runner._shutdown_flag:
        return {"status": "skipped", "reason": "shutdown"}
    raw_file = Path(raw_path)
    if not raw_file.exists():
        return {"status": "skipped", "reason": "file_not_found"}
    try:
        raw_content = raw_file.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "reason": f"read_error: {exc}"}
    result = _run_pipeline_segments(raw_path, raw_content, config)
    result["raw_path"] = raw_path
    result["status"] = "processed"
    return result


def _bind_process_one() -> None:
    """让 loop1 的 _process_one 调用点在本模块主循环里走 maker2。测试直接调 process_one。"""
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    if not C1_LAUNCHD_BOOTIN:
        # C1 占位：CLI 可跑，plist 本轮不装。
        pass

    parser = argparse.ArgumentParser(description="AKB2 loop2 quality runner")
    parser.add_argument("--raw-dir")
    parser.add_argument("--vault-root")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--spot-check", action="store_true")
    parser.add_argument("--poll-interval", type=int)
    parser.add_argument("--staging-dir")
    args = parser.parse_args(argv)

    explicit_root = args.vault_root or os.environ.get("AKB2_ROOT") or os.environ.get(
        "AKB2_VAULT_ROOT", ""
    )
    try:
        from src.akb2.loops.paths import get_akb2_root

        if explicit_root:
            vault_root = str(Path(explicit_root).expanduser().resolve())
        else:
            vault_root = str(get_akb2_root())
    except Exception:
        print("ERROR: 无法解析 vault root。请设置 --vault-root 或 AKB2_ROOT", file=sys.stderr)
        return 1

    raw_dir = args.raw_dir or os.path.join(vault_root, "raw-loop2")
    staging_dir = args.staging_dir or os.path.join(vault_root, "staging-loop2")
    poll_interval = args.poll_interval or int(
        os.environ.get("AKB2_POLL_INTERVAL", str(LOOP2_POLL_INTERVAL_SECONDS))
    )
    try:
        from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file

        ledger_path = str(
            resolve_state_file(
                "quality-ledger.jsonl",
                subdir=LEDGER_SUBDIR,
                legacy_path=os.path.join(staging_dir, "quality-ledger.jsonl"),
            )
        )
        failures_path = str(
            resolve_state_file(
                "failures.jsonl",
                subdir=LEDGER_SUBDIR,
                legacy_path=os.path.join(staging_dir, "failures.jsonl"),
            )
        )
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: 无法解析机器本地状态根: {exc}", file=sys.stderr)
        return 1

    config = RunnerConfig(
        raw_dir=raw_dir,
        ledger_path=ledger_path,
        staging_dir=staging_dir,
        vault_root=vault_root,
        failures_path=failures_path,
        grader_result_dir=os.path.join(staging_dir, "grader-results"),
        poll_interval_seconds=poll_interval,
        lock_name=LOOP2_PROCESSING_LOCK_NAME,
    )
    if not _check_cross_machine_safety(config):
        return 1
    if args.spot_check:
        weekly_dir = os.path.join(vault_root, "reports", "quality-loop2")
        _run_weekly_spot_check(config, weekly_dir, loop_id=LOOP2_LOOP_ID)
        return 0
    if args.once:
        queue = _scan_queue(config)
        if not queue:
            print(f"{_log_ts()} [runner2] 队列为空，无需处理", file=sys.stderr)
            return 0
        raw_path = queue[0]
        if not _acquire_processing_lock(config):
            print(f"{_log_ts()} [runner2] 无法获取处理锁", file=sys.stderr)
            return 1
        try:
            result = process_one(raw_path, config)
            if result:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            _release_processing_lock(config)
        return 0

    _install_signal_handlers()
    print(
        f"{_log_ts()} [runner2] 启动: raw_dir={config.raw_dir}, "
        f"poll={config.poll_interval_seconds}s "
        f"lock={LOOP2_PROCESSING_LOCK_NAME} c1_bootin={C1_LAUNCHD_BOOTIN}",
        file=sys.stderr,
    )
    while not loop1_runner._shutdown_flag:
        if _should_avoid(list(AVOID_WINDOWS)):
            time.sleep(60)
            continue
        queue = _scan_queue(config)
        if not queue:
            time.sleep(config.poll_interval_seconds)
            continue
        if not _acquire_processing_lock(config):
            time.sleep(config.poll_interval_seconds)
            continue
        try:
            raw_path = queue[0]
            result = process_one(raw_path, config)
            if result:
                print(
                    f"{_log_ts()} [runner2] 结果: {result.get('final_status')} — "
                    f"{Path(raw_path).name}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(f"{_log_ts()} [runner2] 处理异常: {exc}", file=sys.stderr)
        finally:
            _release_processing_lock(config)
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
