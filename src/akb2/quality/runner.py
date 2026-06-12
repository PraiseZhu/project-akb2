"""24h 常驻串行单工 daemon — 文件队列驱动 + 七段生命线。

公开接口:
    RunnerConfig — 配置数据类
    main() — daemon 主入口
    _scan_queue(config) -> list[str]  — 扫描待处理 raw 文件
    _process_one(raw_path, config) -> dict  — 处理单篇
    _run_pipeline_segments(raw_path, raw_content, config) -> dict  — 七段串联

宪法对齐:
    P1 (有界): POLL_INTERVAL_SECONDS 具名常量，无 while True 内层无限
    P3 (fail-closed): 任一段失败后续段调用计数为 0
    P4 (预算显式): AVOID_WINDOWS/POLL_INTERVAL 用具名常量
    P7 (异族分离): 调用 step-2~5 模块，不改其逻辑
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 具名常量（宪法 P1/P4）
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS: int = 300  # 5 分钟轮询间隔
AVOID_WINDOW_MINUTES: int = 15  # 避让窗口 ± 分钟
WEEKLY_SPOT_CHECK_DAY: int = 6  # 周日 (0=Mon, 6=Sun)
SPOT_CHECK_SAMPLE_SIZE: int = 3  # 周抽检样本数
SPOT_CHECK_PASS_THRESHOLD: float = 0.8  # 达标率阈值
MAX_PROCESSING_LOCK_AGE_SEC: int = 3600  # 锁文件最大年龄（超时自动清理）

# 避让窗口定义：三链路 launchd 触发时刻 (UTC hour, UTC minute)
# 实测读取自现有 plist (CST=UTC+8):
#   github_briefing: 05:30 CST = 21:30 UTC → (21, 30)
#   briefing:        07:30 CST = 23:30 UTC → (23, 30)
#   feishu-sync:     10:00 CST = 02:00 UTC → (2, 0)
AVOID_WINDOWS: list[tuple[int, int]] = [
    (2, 0),    # feishu-sync (10:00 CST)
    (21, 30),  # github_briefing (05:30 CST)
    (23, 30),  # briefing (07:30 CST)
]

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

_shutdown_flag: bool = False


def _install_signal_handlers() -> None:
    """安装 SIGTERM/SIGINT 信号处理器。"""
    def _handler(signum: int, frame: Any) -> None:
        global _shutdown_flag
        _shutdown_flag = True
        print(f"[runner] 收到信号 {signum}，优雅退出中...", file=sys.stderr)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


# ---------------------------------------------------------------------------
# 配置类
# ---------------------------------------------------------------------------


@dataclass
class RunnerConfig:
    """Runner 运行配置。

    所有路径均为绝对路径。
    """

    raw_dir: str
    ledger_path: str
    staging_dir: str
    vault_root: str
    failures_path: str
    grader_result_dir: str
    poll_interval_seconds: int = POLL_INTERVAL_SECONDS
    auto_promote: bool = False
    lock_dir: str = ""

    def __post_init__(self) -> None:
        if not self.lock_dir:
            self.lock_dir = os.path.join(self.staging_dir, "locks")


def _now() -> datetime:
    """当前 UTC 时间（可 mock）。"""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# 避让窗口
# ---------------------------------------------------------------------------


def _should_avoid(windows: list[tuple[int, int]]) -> bool:
    """判断当前是否在避让窗口内。

    任一避让窗口 ± AVOID_WINDOW_MINUTES 内 → True。
    """
    now = _now()
    current_minutes = now.hour * 60 + now.minute

    for wh, wm in windows:
        window_center = wh * 60 + wm
        window_start = window_center - AVOID_WINDOW_MINUTES
        window_end = window_center + AVOID_WINDOW_MINUTES

        # 处理跨午夜
        if window_start < 0:
            if current_minutes >= (24 * 60 + window_start) or current_minutes <= window_end:
                return True
        elif window_end >= 24 * 60:
            if current_minutes >= window_start or current_minutes <= (window_end - 24 * 60):
                return True
        else:
            if window_start <= current_minutes <= window_end:
                return True

    return False


# ---------------------------------------------------------------------------
# 文件队列
# ---------------------------------------------------------------------------


def _compute_raw_hash(raw_path: str) -> str:
    """计算 raw 文件路径的短哈希（用于账本去重）。"""
    return hashlib.sha256(raw_path.encode("utf-8")).hexdigest()[:16]


def _is_processed(content_hash: str, ledger_path: str) -> bool:
    """检查 content_hash 在账本中是否有 finalize 或 abort 事件。"""
    ledger = Path(ledger_path)
    if not ledger.exists():
        return False

    terminal_events = {"finalize", "abort"}
    try:
        with open(ledger, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    entry.get("content_hash") == content_hash
                    and entry.get("event") in terminal_events
                ):
                    return True
    except OSError:
        return False

    return False


def _scan_queue(config: RunnerConfig) -> list[str]:
    """扫描 raw 目录，返回未处理的 raw 文件路径列表。

    raw 有而账本未标记 finalize/abort = 待处理。
    """
    raw_dir = Path(config.raw_dir)
    if not raw_dir.exists():
        return []

    queue: list[str] = []
    for fpath in sorted(raw_dir.glob("*.md")):
        # 只匹配 --raw-- 模式的文件名
        if "--raw--" not in fpath.name:
            continue
        raw_path_str = str(fpath)
        content_hash = _compute_raw_hash(raw_path_str)
        if not _is_processed(content_hash, config.ledger_path):
            queue.append(raw_path_str)

    return queue


# ---------------------------------------------------------------------------
# 串行单工锁
# ---------------------------------------------------------------------------


def _acquire_processing_lock(config: RunnerConfig) -> bool:
    """获取处理锁（排他）。

    使用文件锁确保同一时刻仅 1 篇在处理。
    返回 True 表示成功获取锁。
    """
    lock_dir = Path(config.lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "processing.lock"

    # 检查已有锁是否过期
    if lock_file.exists():
        try:
            lock_age = time.time() - lock_file.stat().st_mtime
            if lock_age > MAX_PROCESSING_LOCK_AGE_SEC:
                lock_file.unlink(missing_ok=True)
            else:
                return False
        except OSError:
            return False

    # 尝试创建锁文件
    try:
        lock_file.write_text(
            json.dumps({
                "pid": os.getpid(),
                "ts": _now().isoformat(),
            }),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def _release_processing_lock(config: RunnerConfig) -> None:
    """释放处理锁。"""
    lock_file = Path(config.lock_dir) / "processing.lock"
    lock_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 账本事件
# ---------------------------------------------------------------------------


def _log_event(
    config: RunnerConfig,
    event: str,
    node: str,
    content_hash: str,
    details: dict | None = None,
) -> bool:
    """记录一条账本事件。"""
    try:
        from src.akb2.loops.ledger import append_event
    except ImportError:
        return False

    entry: dict[str, Any] = {
        "loop_id": "loop1",
        "node": node,
        "event": event,
        "content_hash": content_hash,
        "ts": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "details": details or {},
    }
    return append_event(config.ledger_path, entry)


# ---------------------------------------------------------------------------
# 七段管道
# ---------------------------------------------------------------------------


def _run_admission_segment(
    raw_path: str,
    raw_content: str,
    config: RunnerConfig,
) -> tuple[bool, str, list]:
    """段 2: 准入判定。

    Returns:
        (passed, reason, checks)
    """
    from src.akb2.quality.admission import run_admission

    result = run_admission(
        raw_path=raw_path,
        raw_content=raw_content,
        ledger_path=config.ledger_path,
    )
    return result.passed, result.reason, result.checks


def _run_distill_segment(
    raw_path: str,
    raw_content: str,
    config: RunnerConfig,
) -> tuple[bool, str, str, str | None]:
    """段 3-4: 提炼。

    Returns:
        (success, candidate_path_or_error, error_detail, candidate_text)
    """
    from src.akb2.quality.distill import compose_candidate, extract_claims

    # 段 1: 结构化抽取
    extract_result = extract_claims(raw_content)
    if not extract_result.success:
        return False, f"extract_failed: {extract_result.error}", extract_result.error, None

    # 段 2: 成稿
    ts = _now().strftime("%Y-%m-%d-%H%M%S")
    batch_id = f"{ts}"
    compose_result = compose_candidate(
        extract_result=extract_result,
        raw_path=raw_path,
        batch_id=batch_id,
        output_dir=config.staging_dir,
    )
    if not compose_result.success:
        return False, f"compose_failed: {compose_result.error}", compose_result.error, None

    # 读取候选页文本
    try:
        candidate_text = Path(compose_result.candidate_path).read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"read_failed: {exc}", str(exc), None

    return True, compose_result.candidate_path, "", candidate_text


def _run_quality_segments(
    candidate_text: str,
    raw_content: str,
    config: RunnerConfig,
) -> tuple[bool, str, str]:
    """段 5-6: 结构校验 + 评分 + 修复。

    Returns:
        (converged, error_class, error_detail)
    """
    from src.akb2.quality.checks_structural import run_structural_checks
    from src.akb2.quality.grader import run_grader
    from src.akb2.quality.repair import run_repair_loop

    # A 层: 结构校验
    a_defects = run_structural_checks(candidate_text, raw_content)
    if a_defects:
        a_dimensions = {d.dimension for d in a_defects}
        return False, "structural_fail", f"A 层缺陷: {a_dimensions}"

    # B 层: LLM 评分
    b_defects, model_used, suspended = run_grader(candidate_text)
    if suspended:
        return False, "grader_all_down", "评分器双不可用，稿件挂起"

    # 无缺陷 → 通过
    all_defects = a_defects + b_defects
    if not all_defects:
        return True, "", ""

    # 修复循环
    repair_result = run_repair_loop(
        candidate_text=candidate_text,
        a_defects=a_defects,
        b_defects=b_defects,
        failures_path=config.failures_path,
    )

    if not repair_result["converged"]:
        error_class = repair_result.get(
            "failure_signature", "distill_no_converge"
        )
        remaining = len(repair_result.get("final_defects", []))
        return False, error_class, f"修复不收敛: {remaining} 项缺陷残留"

    return True, "", ""


def _run_promote_segment(
    candidate_path: str,
    config: RunnerConfig,
) -> tuple[bool, str, str]:
    """段 7: 升格。

    Returns:
        (success, promotion_mode, detail)
    """
    from src.akb2.quality.promote import run_promote

    cpath = Path(candidate_path)
    vault_root = Path(config.vault_root)

    success, detail, snapshot_id = run_promote(
        candidate_path=cpath,
        vault_root=vault_root,
        grader_result_path=None,
    )

    mode = "active" if config.auto_promote else "proposal"
    if snapshot_id:
        mode = "active"

    return success, mode, detail


def _run_pipeline_segments(
    raw_path: str,
    raw_content: str,
    config: RunnerConfig,
) -> dict[str, Any]:
    """串联七段管道，fail-closed 停链。

    Returns:
        dict: {
            "converged": bool,
            "rounds": int,
            "promoted": bool,
            "promotion_mode": str,
            "final_status": str,
            "error": str | None,
        }
    """
    content_hash = _compute_raw_hash(raw_path)
    rounds = 0

    # Segment 1: 记录 intake
    _log_event(config, "intake_raw", "intake_raw", content_hash,
               {"raw_path": raw_path})

    # Segment 2: 准入
    passed, reason, checks = _run_admission_segment(raw_path, raw_content, config)
    if not passed:
        _log_event(config, "abort", "admission", content_hash,
                   {"reason": reason})
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "rejected",
            "error": f"准入未通过: {reason}",
        }

    _log_event(config, "admission", "admission", content_hash,
               {"passed": True})

    # Segment 3-4: 提炼
    success, candidate_path, error_detail, candidate_text = _run_distill_segment(
        raw_path, raw_content, config
    )
    if not success:
        _log_event(config, "abort", "distill_compose", content_hash,
                   {"error": error_detail})
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "failed",
            "error": f"提炼失败: {error_detail}",
        }

    _log_event(config, "distill_extract", "distill_extract", content_hash)
    _log_event(config, "distill_compose", "distill_compose", content_hash,
               {"candidate_path": candidate_path})

    # Segment 5-6: 质量评估 + 修复
    converged, error_class, error_detail = _run_quality_segments(
        candidate_text, raw_content, config
    )
    _log_event(config, "grade_structural", "grade_structural", content_hash)
    _log_event(config, "grade_llm", "grade_llm", content_hash)

    if not converged:
        _log_event(config, "abort", "repair_loop", content_hash,
                   {"error_class": error_class, "error": error_detail})
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "failed",
            "error": f"质量不达标: {error_class} — {error_detail}",
        }

    _log_event(config, "repair_loop", "repair_loop", content_hash,
               {"converged": True})

    # Segment 7: 升格
    promoted, promotion_mode, promote_detail = _run_promote_segment(
        candidate_path, config
    )

    if promoted:
        _log_event(config, "promote", "promote", content_hash,
                   {"mode": promotion_mode})
        _log_event(config, "finalize", "finalize", content_hash,
                   {"mode": promotion_mode})
    else:
        _log_event(config, "abort", "promote", content_hash,
                   {"reason": promote_detail})

    return {
        "converged": True,
        "rounds": rounds,
        "promoted": promoted,
        "promotion_mode": promotion_mode,
        "final_status": "promoted" if promoted else "proposal_passed",
        "error": None if promoted else promote_detail,
    }


# ---------------------------------------------------------------------------
# 单篇处理
# ---------------------------------------------------------------------------


def _process_one(raw_path: str, config: RunnerConfig) -> dict[str, Any] | None:
    """处理单篇 raw 文件。

    Returns:
        dict: 处理结果，或 None 表示跳过。
    """
    global _shutdown_flag

    # 检查 shutdown
    if _shutdown_flag:
        return {"status": "skipped", "reason": "shutdown"}

    raw_file = Path(raw_path)
    if not raw_file.exists():
        return {"status": "skipped", "reason": "file_not_found"}

    # 读取 raw 内容
    try:
        raw_content = raw_file.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "reason": f"read_error: {exc}"}

    # 执行七段管道
    result = _run_pipeline_segments(raw_path, raw_content, config)
    result["raw_path"] = raw_path
    result["status"] = "processed"

    return result


# ---------------------------------------------------------------------------
# 周抽检
# ---------------------------------------------------------------------------


def _is_weekly_check_time() -> bool:
    """判断是否到达周抽检时刻。

    周日 UTC 00:00-01:00 窗口内触发一次。
    """
    now = _now()
    if now.weekday() != WEEKLY_SPOT_CHECK_DAY:
        return False
    # 仅在 00:00-01:00 UTC 窗口内触发（避免重复）
    return now.hour == 0


def _sample_recent_promoted(config: RunnerConfig) -> list[dict[str, str]]:
    """从账本抽最近期入库的 SPOT_CHECK_SAMPLE_SIZE 篇。

    Returns:
        list of {"path": str, "slug": str}
    """
    ledger = Path(config.ledger_path)
    if not ledger.exists():
        return []

    promoted_items: list[dict[str, str]] = []
    try:
        with open(ledger, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    entry.get("event") == "finalize"
                    and entry.get("node") == "finalize"
                ):
                    details = entry.get("details", {})
                    candidate_path = details.get("candidate_path", "")
                    if candidate_path:
                        slug = Path(candidate_path).stem
                        promoted_items.append({
                            "path": candidate_path,
                            "slug": slug,
                        })
    except OSError:
        return []

    # 取最近的 SPOT_CHECK_SAMPLE_SIZE 篇
    return promoted_items[-SPOT_CHECK_SAMPLE_SIZE:]


def _regrade_sample(samples: list[dict[str, str]]) -> list[bool]:
    """对抽检样本重新过 grader。

    Returns:
        list[bool]: 每篇是否通过 (True=pass)
    """
    results: list[bool] = []
    for sample in samples:
        sample_path = Path(sample["path"])
        if not sample_path.exists():
            results.append(False)
            continue

        try:
            _text = sample_path.read_text(encoding="utf-8")
        except OSError:
            results.append(False)
            continue

        try:
            from src.akb2.quality.grader import run_grader
            defects, _model, suspended = run_grader(_text)
        except Exception:
            results.append(False)
            continue

        if suspended:
            results.append(False)
        else:
            results.append(len(defects) == 0)

    return results


def _run_weekly_spot_check(
    config: RunnerConfig,
    weekly_dir: str,
) -> None:
    """执行周抽检。

    抽近期入库 3 篇重过 grader，pass_rate < 80% 写校准任务文件 +
    落账 + 产 weekly-summary.json。
    """
    samples = _sample_recent_promoted(config)
    weekly_path = Path(weekly_dir)
    weekly_path.mkdir(parents=True, exist_ok=True)

    if len(samples) < SPOT_CHECK_SAMPLE_SIZE:
        # 样本不足，创建 minimal summary
        summary = {
            "ts": _now().isoformat(),
            "samples_checked": len(samples),
            "pass_rate": None,
            "below_threshold": False,
            "manual_intervention_required_count": 0,
            "quality_feedback_required_count": 0,
            "note": f"样本不足: {len(samples)} < {SPOT_CHECK_SAMPLE_SIZE}",
        }
        (weekly_path / "weekly-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return

    # 重新评分
    results = _regrade_sample(samples)
    pass_count = sum(1 for r in results if r)
    pass_rate = pass_count / len(results) if results else 0.0

    manual_count = 0
    quality_count = 0

    if pass_rate < SPOT_CHECK_PASS_THRESHOLD:
        # 达标率 < 80%: 创建校准任务
        task = {
            "triggered_at": _now().isoformat(),
            "reason": (
                f"周抽检达标率 {pass_rate:.0%} < {SPOT_CHECK_PASS_THRESHOLD:.0%} "
                f"({pass_count}/{len(results)} pass)"
            ),
            "action": "calibration",
            "sample_slugs": [s["slug"] for s in samples],
            "sample_results": results,
        }
        task_path = weekly_path / "calibration-task.json"
        task_path.write_text(
            json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 落账
        _log_event(
            config, "weekly_spot_check", "grade_llm",
            f"spot-check-{_now().strftime('%Y%m%d')}",
            {
                "pass_rate": pass_rate,
                "pass_count": pass_count,
                "total": len(results),
                "calibration_task": str(task_path),
            },
        )

        manual_count = 1
        quality_count = SPOT_CHECK_SAMPLE_SIZE - pass_count

    # 产出 weekly-summary.json
    summary = {
        "ts": _now().isoformat(),
        "samples_checked": len(samples),
        "pass_count": pass_count,
        "pass_rate": pass_rate,
        "below_threshold": pass_rate < SPOT_CHECK_PASS_THRESHOLD,
        "manual_intervention_required_count": manual_count,
        "quality_feedback_required_count": quality_count,
        "calibration_task_reason": (
            f"达标率 {pass_rate:.0%} < {SPOT_CHECK_PASS_THRESHOLD:.0%}"
            if pass_rate < SPOT_CHECK_PASS_THRESHOLD
            else "N/A (above threshold)"
        ),
        "sample_slugs": [s["slug"] for s in samples],
    }
    (weekly_path / "weekly-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def _run_main_loop(config: RunnerConfig) -> int:
    """主轮询循环：扫描队列 → 处理 → sleep → 重复。

    退出码:
        0 — SIGTERM 优雅退出
        1 — 不可恢复错误
    """
    global _shutdown_flag

    _install_signal_handlers()
    avoid_windows = list(AVOID_WINDOWS)

    print(f"[runner] 启动: raw_dir={config.raw_dir}, poll={config.poll_interval_seconds}s",
          file=sys.stderr)
    print(f"[runner] auto_promote={'ON' if config.auto_promote else 'OFF (proposal mode)'}",
          file=sys.stderr)

    last_spot_check_date: str = ""

    while not _shutdown_flag:
        # 周抽检检查位
        if _is_weekly_check_time():
            today = _now().strftime("%Y-%m-%d")
            if today != last_spot_check_date:
                print("[runner] 触发周抽检...", file=sys.stderr)
                weekly_dir = Path(config.vault_root) / "reports" / "quality-loop"
                _run_weekly_spot_check(config, str(weekly_dir))
                last_spot_check_date = today

        # 避让窗口检查
        if _should_avoid(avoid_windows):
            time.sleep(60)  # 避让中每分钟检查一次
            continue

        # 扫描队列
        queue = _scan_queue(config)
        if not queue:
            time.sleep(config.poll_interval_seconds)
            continue

        # 获取处理锁
        if not _acquire_processing_lock(config):
            time.sleep(config.poll_interval_seconds)
            continue

        try:
            # 串行处理队列中的每一篇（每次只取 1 篇）
            raw_path = queue[0]
            print(f"[runner] 处理: {Path(raw_path).name}", file=sys.stderr)
            result = _process_one(raw_path, config)

            if result:
                status = result.get("final_status", result.get("status", "unknown"))
                print(f"[runner] 结果: {status} — {Path(raw_path).name}", file=sys.stderr)
        except Exception as exc:
            print(f"[runner] 处理异常: {exc}", file=sys.stderr)
        finally:
            _release_processing_lock(config)

        # 篇间短暂 sleep 防止紧循环
        time.sleep(5)

    print("[runner] 优雅退出完成", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """daemon 主入口。

    环境变量:
        AKB2_AUTO_PROMOTE=1  启用自动升格（出厂形态）
        AKB2_VAULT_ROOT      覆盖 vault 根路径
        AKB2_POLL_INTERVAL   覆盖轮询间隔（秒）
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="AKB2 Quality Runner — 24h 常驻串行单工 daemon"
    )
    parser.add_argument(
        "--raw-dir",
        help="raw 文件目录（默认从 vault.toml 解析）",
    )
    parser.add_argument(
        "--vault-root",
        help="vault 根目录（默认从 vault.toml 解析）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="处理队列中的一篇后退出（调试用）",
    )
    parser.add_argument(
        "--spot-check",
        action="store_true",
        help="仅运行周抽检后退出",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        help="轮询间隔（秒，默认 300）",
    )

    args = parser.parse_args(argv)

    # 解析 vault root
    vault_root = args.vault_root or os.environ.get("AKB2_VAULT_ROOT", "")
    if not vault_root:
        try:
            from src.akb2.loops.paths import get_akb2_root

            vault_root = str(get_akb2_root())
        except Exception:
            print("ERROR: 无法解析 vault root。请设置 --vault-root 或 AKB2_VAULT_ROOT",
                  file=sys.stderr)
            return 1

    # 解析 raw dir
    raw_dir = args.raw_dir
    if not raw_dir:
        raw_dir = os.path.join(vault_root, "raw")

    # 解析 staging dir
    try:
        from src.akb2.loops.paths import get_staging_root

        staging_dir = str(get_staging_root())
    except Exception:
        staging_dir = os.path.join(vault_root, "staging")

    auto_promote = os.environ.get("AKB2_AUTO_PROMOTE", "0") == "1"
    poll_interval = args.poll_interval or int(
        os.environ.get("AKB2_POLL_INTERVAL", str(POLL_INTERVAL_SECONDS))
    )

    config = RunnerConfig(
        raw_dir=raw_dir,
        ledger_path=os.path.join(staging_dir, "quality-ledger.jsonl"),
        staging_dir=staging_dir,
        vault_root=vault_root,
        failures_path=os.path.join(staging_dir, "failures.jsonl"),
        grader_result_dir=os.path.join(staging_dir, "grader-results"),
        poll_interval_seconds=poll_interval,
        auto_promote=auto_promote,
    )

    # --spot-check: 仅运行周抽检
    if args.spot_check:
        weekly_dir = os.path.join(vault_root, "reports", "quality-loop")
        _run_weekly_spot_check(config, weekly_dir)
        print("[runner] 周抽检完成", file=sys.stderr)
        return 0

    # --once: 处理一篇后退出
    if args.once:
        queue = _scan_queue(config)
        if not queue:
            print("[runner] 队列为空，无需处理", file=sys.stderr)
            return 0
        raw_path = queue[0]
        if not _acquire_processing_lock(config):
            print("[runner] 无法获取处理锁", file=sys.stderr)
            return 1
        try:
            result = _process_one(raw_path, config)
            if result:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            _release_processing_lock(config)
        return 0

    # 常驻模式
    return _run_main_loop(config)


if __name__ == "__main__":
    sys.exit(main())
