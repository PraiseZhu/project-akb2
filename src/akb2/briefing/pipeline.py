"""End-to-end pipeline orchestrator for AKB2.0 social-trends daily briefing.

CLI: python3 -m akb2.briefing.pipeline --date YYYY-MM-DD --vault-root <path> [--dry-run-intake]

Pipeline stages:
  1. Intake  — subprocess l0_collector (--dry-run)
  2. Scoring — ScoreEngine.score_tweets() (direct import)
  3. Synthesis — synthesize() (direct import)
  4. Gate   — subprocess akb2_gate_check.py check (--dry-run)
  5. Output  — write briefing markdown to vault

Triple-assertion guarantee:
  proposed_gate_decision printed to stdout
  == value in frontmatter of final output file
  == gate CLI decision on final output file (gate is deterministic)

Design:
  - Write temp briefing to <vault-root>/staging/candidate/<date>-briefing.md
  - Run gate on temp file (guard_input_path accepts staging/candidate/)
  - Write final file to <vault-root>/AKB2.0/AI TRENDS/Social/<date> Daily Briefing.md
  - Publish via fsync + same-dir os.replace (no symlink; gate reject skips this step)
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zoneinfo
from pathlib import Path

from akb2.briefing.audit import _requires_chinese  # noqa: E402
from akb2.briefing.audit_fix import run_audit_fix_loop  # noqa: E402
from akb2.briefing.translate import _display_tweet_indices, translate_briefing  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent.parent
# worktree root (src/akb2/briefing/ -> src/akb2/ -> src/ -> worktree/)
GATE_CLI = Path(
    os.environ.get("AKB2_GATE_CLI")
    or str(REPO_ROOT / "src" / "akb2" / "gate" / "akb2_gate_check.py")
)

GATE_DECISION_ENUM = {
    "active", "needs_repair", "needs_human_review",
    "brief_only", "archive", "reject",
}

# ── 退出码（宪法 P4「失败可见」）───────────────────────────────────────────────
# 不变式：**未交付即非零**。0 只在 briefing 真正 copy 到发布目录后才返回。
# 具名化的动因（github 侧同源事故）：SKIPPED/BLOCKED 分支曾出现「exit=0 + vault 零文件」，
# 看退出码的监控（含 launchctl 的 LastExitStatus）会把零交付读成成功。裸字面量的
# `sys.exit(3)` 无法自证语义，也无法静态断言"所有未交付分支都非零"。
#
# 保留码位（本模块**不得**占用）：
#   124 = scripts/run_with_deadline.py 的 deadline 超时码
#   125 = backstop 脚本的主动放弃码
# 数值一律沿用既有约定不动：scripts/briefing_backstop.sh 与运维记录都按这些数字判读，
# 改数值会静默改变 backstop 的归因。本次只做"具名 + 静态可断言"，不动语义。
EXIT_DELIVERED = 0
EXIT_NOT_DELIVERED = 1            # 通用未交付：上游硬失败 / gate reject / vault 不可用
EXIT_AUDIT_FAIL = 2               # audit 判 fail，产出留 staging 不入库
EXIT_INTAKE_FIXTURE_BLOCKED = 3   # intake 降级到测试 fixture，硬阻断
EXIT_GATE_NOT_CONVERGED = 4       # gate 判定在最终文件上不收敛（有界重试后仍摆动）
EXIT_DEDUP_STORE_UNAVAILABLE = 5  # 跨日去重账本不可用（不敢放行，会重复发布）
# 静态自证用（tests/briefing/test_pipeline_exit_codes.py 断言）
EXIT_CODES = frozenset({
    EXIT_DELIVERED, EXIT_NOT_DELIVERED, EXIT_AUDIT_FAIL,
    EXIT_INTAKE_FIXTURE_BLOCKED, EXIT_GATE_NOT_CONVERGED, EXIT_DEDUP_STORE_UNAVAILABLE,
})
RESERVED_EXIT_CODES = frozenset({124, 125})

_TZ_SH = zoneinfo.ZoneInfo("Asia/Shanghai")
# 默认 3 天：19 个活跃账号在 2 天窗口下安静日仅 ~10 条原创、只够 3 主题；
# 放宽到 3 天提升素材量（够 4-5 主题），新鲜度损失可接受。可 AKB2_INTAKE_WINDOW_DAYS 覆盖。
_INTAKE_WINDOW_DAYS_DEFAULT = 3


def _stage_log(stage: str, event: str, elapsed: float | None = None) -> None:
    now = datetime.datetime.now(_TZ_SH).isoformat(timespec="seconds")
    suffix = f" elapsed={elapsed:.1f}s" if elapsed is not None else ""
    sys.stderr.write(f"{now} [briefing-stage] {stage} {event}{suffix}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Constitution 原则 7：7×24 管线禁用 -preview 模型（SC-3）
# ---------------------------------------------------------------------------

def _assert_no_preview_translate_models() -> None:
    """AKB2_TRANSLATE_MODEL / AKB2_TRANSLATE_FALLBACK_MODELS 含 preview → fail fast。

    7×24 管线不得使用 -preview 模型（不稳定、JSON 解析易失败靠兜底续命）。
    显式配置了 preview 模型属部署错误，立即 exit 1 而非静默降级。
    """
    from akb2.briefing.translate import _TRANSLATE_FALLBACK_MODELS, _TRANSLATE_MODEL

    offenders: list[str] = []
    main_model = os.environ.get("AKB2_TRANSLATE_MODEL", _TRANSLATE_MODEL)
    if "preview" in main_model:
        offenders.append(f"AKB2_TRANSLATE_MODEL={main_model}")

    fb_env = os.environ.get("AKB2_TRANSLATE_FALLBACK_MODELS")
    if fb_env:
        fb_models = [m.strip() for m in fb_env.split(",") if m.strip()]
    else:
        fb_models = _TRANSLATE_FALLBACK_MODELS
    for m in fb_models:
        if "preview" in m:
            offenders.append(f"AKB2_TRANSLATE_FALLBACK_MODELS contains {m}")

    if offenders:
        sys.stderr.write(
            "preview model is forbidden for 7x24 pipeline: "
            + "; ".join(offenders)
            + "\n"
        )
        sys.exit(EXIT_NOT_DELIVERED)


# ---------------------------------------------------------------------------
# 产出层日期兜底（SC-7）
# ---------------------------------------------------------------------------

def _filter_recent(tweets: list, date: str, days: int = _INTAKE_WINDOW_DAYS_DEFAULT) -> list:
    """保留 created_at 在 [date-(days-1), date] 范围内的推文（时区 Asia/Shanghai）。

    - 缺 created_at 或格式异常 → **丢弃**并 stderr 告警（fail-closed）：无时间戳的推文
      无法判定新鲜度，保守保留会让 N 天窗口失效、旧推文反复再入简报（缺陷 #6 附带）
    - days=3 表示今天+昨天+前天（近3天）
    """
    try:
        end_day = datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write(f"WARNING [filter_recent]: invalid date '{date}', skipping filter\n")
        return tweets

    start_day = end_day - datetime.timedelta(days=days - 1)
    # Asia/Shanghai 时区边界
    start_dt = datetime.datetime(
        start_day.year, start_day.month, start_day.day, 0, 0, 0, tzinfo=_TZ_SH
    )
    end_dt = datetime.datetime(
        end_day.year, end_day.month, end_day.day, 23, 59, 59, 999999, tzinfo=_TZ_SH
    )

    kept = []
    for t in tweets:
        raw_ts = t.get("created_at", "")
        if not raw_ts:
            sys.stderr.write(
                f"WARNING [filter_recent]: tweet {t.get('id','?')} missing created_at, "
                "dropping (无时间戳无法判新鲜度，保留会让时间窗失效)\n"
            )
            continue
        try:
            dt = datetime.datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            dt_sh = dt.astimezone(_TZ_SH)
            if start_dt <= dt_sh <= end_dt:
                kept.append(t)
        except (ValueError, TypeError):
            sys.stderr.write(
                f"WARNING [filter_recent]: tweet {t.get('id','?')} "
                f"bad created_at '{raw_ts}', dropping\n"
            )

    if len(kept) < len(tweets):
        sys.stderr.write(
            f"INFO [filter_recent]: dropped {len(tweets)-len(kept)} "
            f"out-of-window tweets, kept {len(kept)}\n"
        )

    return kept


# ---------------------------------------------------------------------------
# Stage 1: Intake
# ---------------------------------------------------------------------------

def run_intake(
    dry_run: bool,
    output_path: Path,
    since_date: str | None = None,
    until_date: str | None = None,
) -> dict:
    """Call the intake collector via subprocess, return parsed JSON dict.

    Collector 由 env AKB2_INTAKE_COLLECTOR 选择：
      - "account"（默认）：curator 逐账号抓取（account_collector），内容为策展 AI 账号
      - "keyword"：旧泛关键词搜索（l0_collector），噪音高，作回退
    两者 CLI 同构（--output/--dry-run/--since/--until），下游处理不变。
    """
    collector_mode = os.environ.get("AKB2_INTAKE_COLLECTOR", "account")
    collector_module = (
        "akb2.briefing.intake.l0_collector"
        if collector_mode == "keyword"
        else "akb2.briefing.intake.account_collector"
    )
    cmd = [
        sys.executable, "-m", collector_module,
        "--output", str(output_path),
    ]
    # P2-1: account collector 默认抓 en+zh 双 tier 合并（打 lang 标）；keyword 模式不支持 --merge
    if collector_mode != "keyword" and os.environ.get("AKB2_FETCH_MERGE", "1") == "1":
        cmd.append("--merge")
    if dry_run:
        cmd.append("--dry-run")
    if since_date:
        cmd += ["--since", since_date]
    if until_date:
        cmd += ["--until", until_date]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        ":" + env["PYTHONPATH"] if "PYTHONPATH" in env else ""
    )
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
    # 缺陷 #1 根因之一：collector 降级到 fixture 后仍 exit 0，原实现只在 returncode!=0
    # 时打印 stderr，于是 "fallback to fixture" 这类 WARNING 被完全吞掉、日志无从追查。
    # 宪法原则 4（失败可见）：子进程 stderr 无条件转发。
    if result.stderr:
        sys.stderr.write(result.stderr)
        sys.stderr.flush()
    if result.returncode != 0:
        sys.stderr.write(f"ERROR: intake failed (exit {result.returncode}):\n")
        sys.exit(EXIT_NOT_DELIVERED)

    with open(output_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 降级诊断落盘 + fixture 假数据识别（缺陷 #1）
# ---------------------------------------------------------------------------

def _diag_dir() -> Path:
    """social 链路诊断 JSON 的落盘目录。

    默认 `<repo>/logs/`：与 briefing-stderr.log 同处（运维第一眼看的地方）且已在
    .gitignore 内。github 链路把同类文件放在工程根，那两个名字是逐个登记进
    .gitignore 的，社媒不复制这个需要改 .gitignore 的做法。
    """
    return Path(os.environ.get("AKB2_BRIEFING_DIAG_DIR") or (REPO_ROOT / "logs"))


def _atomic_publish_text(src: Path, dest: Path) -> None:
    """把 src 耐久发布到 dest：同目录临时文件 + fsync + os.replace。

    shutil.copy2 在外置 APFS 上返回后，下一进程立刻 open(dest) 可能 ENOENT
    （2026-09-18 11:49 pipeline exit 0，backstop 2s 内 awk/cat 都 can't open file）。
    GitHub 侧 `_atomic_write_text` 已是这个形状；社媒发布必须同形。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    with open(src, "rb") as inf, open(tmp, "wb") as outf:
        outf.write(inf.read())
        outf.flush()
        os.fsync(outf.fileno())
    os.replace(tmp, dest)
    # 尽量把目录项也刷下去；外置卷上失败不得挡发布（文件已经 replace 成功）。
    try:
        dir_fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _write_social_diag(status: str, reason: str, date: str, extra: dict | None = None) -> Path:
    """写 social 简报诊断 JSON（形态对齐 github 链路的 briefing-skipped/failed.json）。

    github 链路的两个文件是它自己的产物，社媒用独立文件名 `briefing-social-<status>.json`，
    避免两条链路互相覆盖诊断。
    """
    payload = {
        "status": status,
        "reason": reason,
        "date": date,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    if extra:
        payload.update(extra)
    out = _diag_dir() / f"briefing-social-{status}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _fixture_tweet_ids() -> set[str]:
    """测试 fixture 里的 tweet id 集合（用于识别"冒充真实抓取"的假数据）。"""
    try:
        from akb2.briefing.intake.l0_collector import FIXTURE_PATH  # noqa: PLC0415

        data = json.loads(Path(FIXTURE_PATH).read_text())
    except (OSError, ValueError, ImportError) as exc:
        sys.stderr.write(f"WARNING [intake_guard]: 无法读取 fixture 用于比对: {exc}\n")
        return set()
    return {str(t.get("id", "")) for t in data.get("tweets", []) if t.get("id")}


def _intake_is_fixture_content(tweets: list[dict]) -> bool:
    """所有推文 id 都来自测试 fixture → 判定为 fixture 假数据。

    `_fallback_used` 标记是第一道防线，但它由 collector 自报；实证的 12+ 天假数据
    说明这条链路一旦标记丢失就完全不可见，故按内容再判一次（防御深度）。
    """
    ids = {str(t.get("id", "")) for t in tweets if t.get("id")}
    if not ids:
        return False
    fixture_ids = _fixture_tweet_ids()
    if not fixture_ids:
        return False
    return ids.issubset(fixture_ids)


# ---------------------------------------------------------------------------
# Stage 2: Scoring
# ---------------------------------------------------------------------------

def run_scoring(intake_data: dict) -> dict:
    """Call ScoreEngine directly, return scored data dict.

    返回值带 `novelty_mode` / `embed_degraded`：判重模式必须能被产出层落盘，
    否则语义去重降级为 sha1 精确去重（近重复漏检）在成品里零痕迹（缺陷 #6 附带）。
    """
    from akb2.briefing.scoring import ScoreEngine  # noqa: PLC0415

    engine = ScoreEngine()
    tweets = intake_data.get("tweets", [])
    scored_tweets = [engine.score_tweet(t) for t in tweets]
    if engine.novelty_mode != "semantic":
        sys.stderr.write(
            "WARNING [scoring]: novelty 判重为 exact_sha1（无嵌入客户端）——"
            "改写/转发型近重复不会被剔除\n"
        )
    return {
        "tweets": scored_tweets,
        "novelty_mode": engine.novelty_mode,
        "embed_degraded": engine.embed_degraded,
    }


# ---------------------------------------------------------------------------
# Stage 2b: 跨日去重账本（缺陷 #6）
# ---------------------------------------------------------------------------

# 账本文件名（机器本地状态根下的 ledgers/，避开 Syncthing 同步范围）
_SOCIAL_DEDUP_DB_NAME = "social-tweet-dedup.sqlite"


def _social_dedup_window_days(intake_window_days: int) -> int:
    """去重窗口不得小于采集窗口，否则窗口内相邻日必然重复（实证 143 个 URL 各出现 2 天）。"""
    override = int(os.environ.get("AKB2_SOCIAL_DEDUP_WINDOW_DAYS", "0") or 0)
    return max(intake_window_days, override)


def _open_social_dedup_store(window_days: int, *, min_window_days: int):
    """打开跨日去重账本（复用 github 链路的 DedupStore，键用 tweet id）。

    `min_window_days` = **社媒自己的采集窗口**，显式传给 DedupStore 作窗口下限。
    不传时 DedupStore 会用 GitHub 链路的 `RECENCY_WINDOW_DAYS`(7) 兜底——那等于
    GitHub 的常量在暗中决定社媒的去重窗口（社媒采集窗口默认 3 天，
    见 `_INTAKE_WINDOW_DAYS_DEFAULT`），社媒侧既无声明也无测试。故此处必传。

    账本不可用 → fail-closed（宪法原则 3）：没有账本就无法保证不重复，
    宁可不发也不发一份可能重复的简报。
    """
    import sqlite3  # noqa: PLC0415

    from akb2.briefing.github.dedup_sqlite import DedupStore  # noqa: PLC0415
    from akb2.common.state_paths import LEDGER_SUBDIR, state_file_path  # noqa: PLC0415

    try:
        db_path = state_file_path(_SOCIAL_DEDUP_DB_NAME, subdir=LEDGER_SUBDIR)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return DedupStore(
            db_path, window_days=window_days, min_window_days=min_window_days
        )
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        sys.stderr.write(
            f"ERROR [social_dedup]: 跨日去重账本不可用（{type(exc).__name__}: {exc}）——"
            "无账本无法保证不重复，拒绝发布（fail-closed）\n"
        )
        sys.exit(EXIT_DEDUP_STORE_UNAVAILABLE)


def apply_cross_day_dedup(
    tweets: list[dict], *, window_days: int, intake_window_days: int
) -> tuple[list[dict], list[str]]:
    """按 tweet id 剔除窗口内已发布过的推文。

    Args:
        window_days: 生效的去重窗口。
        intake_window_days: 社媒采集窗口 = 去重窗口下限（显式声明，不借 GitHub 常量）。

    Returns:
        (保留的推文, 待记账的 tweet id 列表) —— 记账**推迟到实际发布成功之后**
        （见 `mark_social_dedup_seen`），否则一次未发布的运行会白吃掉当天素材。
    """
    store = _open_social_dedup_store(window_days, min_window_days=intake_window_days)
    try:
        store.prune()
        kept: list[dict] = []
        keys: list[str] = []
        dropped = 0
        for t in tweets:
            tid = str(t.get("id", "")).strip()
            if not tid:
                sys.stderr.write(
                    "WARNING [social_dedup]: 推文缺 id，无法跨日去重，保留\n"
                )
                kept.append(t)
                continue
            if store.is_duplicate(tid):
                dropped += 1
                continue
            kept.append(t)
            if tid not in keys:
                keys.append(tid)
        if dropped:
            sys.stderr.write(
                f"INFO [social_dedup]: 跨日去重丢弃 {dropped} 条"
                f"（{window_days} 天窗口内已入过简报），保留 {len(kept)}\n"
            )
        return kept, keys
    finally:
        store.close()


def mark_social_dedup_seen(
    keys: list[str], *, window_days: int, intake_window_days: int
) -> None:
    """发布成功后把本期推文 id 记入账本（窗口下限同样显式传社媒采集窗口）。"""
    if not keys:
        return
    store = _open_social_dedup_store(window_days, min_window_days=intake_window_days)
    try:
        for k in keys:
            store.mark_seen(k)
    finally:
        store.close()
    sys.stderr.write(f"INFO [social_dedup]: 已记账 {len(keys)} 条 tweet id\n")


# ---------------------------------------------------------------------------
# Stage 3: Synthesis
# ---------------------------------------------------------------------------

def run_synthesis(scored_data: dict, date: str) -> dict:
    """Call synthesize() directly, return synth payload dict."""
    from akb2.briefing.synthesis import synthesize  # noqa: PLC0415

    return synthesize(scored_data, date=date)


# ---------------------------------------------------------------------------
# Stage 4: Gate check
# ---------------------------------------------------------------------------

def run_gate(akb_root: Path, input_file: Path, date: str) -> str:
    """Call gate CLI, parse proposed_gate_decision from stdout. Returns decision string."""
    if not GATE_CLI.exists():
        sys.stderr.write(f"ERROR: gate CLI not found at {GATE_CLI}\n")
        sys.exit(EXIT_NOT_DELIVERED)

    cmd = [
        sys.executable, str(GATE_CLI), "check",
        "--akb-root", str(akb_root),
        "--input", str(input_file),
        "--date", date,
        "--dry-run",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        ":" + env["PYTHONPATH"] if "PYTHONPATH" in env else ""
    )
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        sys.stderr.write(f"ERROR: gate CLI failed (exit {result.returncode}):\n")
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        sys.exit(EXIT_NOT_DELIVERED)

    # Parse proposed_gate_decision from stdout
    output = result.stdout
    import re
    m = re.search(
        r"proposed_gate_decision:\s*(active|needs_repair|needs_human_review|brief_only|archive|reject)",
        output,
    )
    if not m:
        sys.stderr.write("ERROR: gate CLI output did not contain proposed_gate_decision:\n")
        sys.stderr.write(output[:2000])
        sys.exit(EXIT_NOT_DELIVERED)

    decision = m.group(1)
    if decision not in GATE_DECISION_ENUM:
        sys.stderr.write(f"ERROR: gate decision '{decision}' not in valid enum\n")
        sys.exit(EXIT_NOT_DELIVERED)
    return decision


# ---------------------------------------------------------------------------
# Stage 5: Build markdown briefing
# ---------------------------------------------------------------------------

_ORPHAN_TOP_K = int(os.environ.get("AKB2_ORPHAN_TOP_K", "5"))
# 展示层质量过滤（2026-06-02 试跑发现的 3 个缺陷修复）
_MIN_DISPLAY_SCORE = float(os.environ.get("AKB2_MIN_DISPLAY_SCORE", "0.35"))
# 低于此分不入正文（去低分噪音）
_PER_AUTHOR_CAP = int(os.environ.get("AKB2_PER_AUTHOR_CAP", "2"))
# 单作者每主题/孤儿区上限（防霸屏）


def _apply_display_filters(
    tweets: list[dict],
    *,
    min_score: float = _MIN_DISPLAY_SCORE,
    per_author_cap: int = _PER_AUTHOR_CAP,
    rendered_ids: set[str] | None = None,
) -> list[dict]:
    """简报展示过滤（保持入参顺序）：

    0. 渲染幂等：已在别处渲染过的 id 直接跳过（`rendered_ids` 为跨调用共享集合）——
       修复"同一 tweet 被 LLM 分配给多个 focus_discussions 时每个小节各渲染一次"
       （缺陷 #5：20/40 天有同日重复，33 份多余副本，全部跨小节）。novelty 是内容
       相似度维度、同一对象恒为 1.0，管不了渲染重复。
    1. 剔除近重复：`dimensions.novelty == 0`（语义/精确去重已判定为重复的后出现者）——
       修复"novelty 软扣分 0.05 不足以剔重，重复内容仍进简报"（试跑发现 @lmsysorg 两条
       cosine=0.8776>0.85 公告都入选）。
    2. 最低分门槛：score < min_score 不入正文（去 0.27 这类低分噪音）。
    3. per-author 上限：同作者超过 per_author_cap 条丢弃（防单作者霸占整个主题）。
    """
    out: list[dict] = []
    author_count: dict[str, int] = {}
    for t in tweets:
        # 0) 渲染幂等（跨小节）
        tid = str(t.get("id", ""))
        if rendered_ids is not None and tid and tid in rendered_ids:
            continue
        dims = t.get("dimensions") or {}
        # 1) 剔重：novelty==0 表示已有近重复在前
        if "novelty" in dims and dims.get("novelty") == 0:
            continue
        # 2) 最低分
        if float(t.get("score", 0) or 0) < min_score:
            continue
        # 3) per-author 上限
        author = (t.get("author") or "").lower().lstrip("@")
        if author and author_count.get(author, 0) >= per_author_cap:
            continue
        author_count[author] = author_count.get(author, 0) + 1
        if rendered_ids is not None and tid:
            rendered_ids.add(tid)
        out.append(t)
    return out


def _collect_display_tweets(
    synth: dict,
    orphan_top_k: int = _ORPHAN_TOP_K,
) -> tuple[list[list[dict]], list[dict]]:
    """将推文按主题分组，返回 (per_topic_tweets, orphan_tweets)。

    per_topic_tweets[i] = focus_discussions[i] 对应的支撑推文列表
    （应用展示过滤：跨小节渲染幂等/剔重/最低分/per-author）
    orphan_tweets = 未被任何主题引用的高分推文，按 score 倒序取 top-k（同样过滤）

    缺陷 #5：`rendered_ids` 在所有小节 + 孤儿区之间共享，同一 tweet 只渲染一次
    （首次出现的小节）。
    """
    tweets = synth.get("tweets", [])
    id_to_tweet: dict[str, dict] = {str(t.get("id", "")): t for t in tweets}

    # 跨小节共享的"已渲染 id"集合（渲染幂等，不是内容相似度）
    rendered_ids: set[str] = set()
    all_referenced: set[str] = set()
    per_topic: list[list[dict]] = []
    for fd in synth.get("focus_discussions", []):
        topic_tweets = []
        for tid in fd.get("supporting_tweet_ids", []):
            sid = str(tid)
            if sid in id_to_tweet:
                topic_tweets.append(id_to_tweet[sid])
                all_referenced.add(sid)
        per_topic.append(_apply_display_filters(topic_tweets, rendered_ids=rendered_ids))

    # 孤儿高分：未被任何主题引用，按 score 倒序，过滤后取 top-k
    orphans_sorted = sorted(
        [t for t in tweets if str(t.get("id", "")) not in all_referenced],
        key=lambda t: t.get("score", 0),
        reverse=True,
    )
    orphans = _apply_display_filters(orphans_sorted, rendered_ids=rendered_ids)[:orphan_top_k]

    return per_topic, orphans


_INLINE_LINK_RE = re.compile(r"https?://\S+")
_PIC_TWITTER_RE = re.compile(r"\bpic\.twitter\.com/\S+")


def _strip_inline_links(text: str) -> str:
    """剥离正文内联链接（http(s) URL / pic.twitter 短链）。

    用户决策：每条推文只保留底部唯一 [原文] 链接，正文不再出现内联短链
    （避免与 [原文] 重复）。剥离后折叠多余空白。
    """
    if not text:
        return text
    cleaned = _INLINE_LINK_RE.sub("", text)
    cleaned = _PIC_TWITTER_RE.sub("", cleaned)
    # P0-2 SC-2：剥链接后清除孤立的 🔗 链接标记（推文里 🔗 只是"链接在此"标记，
    # 链接已剥 + 底部已有唯一 [原文]，正文残留 🔗 是无意义噪音）
    cleaned = cleaned.replace("🔗", "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _render_tweet(t: dict) -> list[str]:
    """渲染一条推文为 markdown 行列表（作者 + 评分 + 点题 + 中文正文 + 唯一 [原文] 链接）。

    用户决策：简报不保留英文原文（移除英文折叠块）；正文内联链接剥离，
    每条推文只在底部保留一个 [原文] 链接。长推优先展示中文核心段。
    """
    author = t.get("author", "")
    text = _strip_inline_links(t.get("text", ""))
    core_excerpt = _strip_inline_links(t.get("core_excerpt", ""))
    zh_point = t.get("zh_point", "")
    score = t.get("score", 0)
    url = t.get("url", "")

    parts: list[str] = [f"**{author}**（评分：{score:.3f}）"]
    if zh_point:
        parts += [f"*{zh_point}*", ""]

    body = core_excerpt or text
    parts += [f"> {body}", ""]

    if url:
        parts += [f"[原文]({url})", ""]

    return parts


# frontmatter 里放自由文本（audit issue 文案）的单条长度上限：审计文案会带 80 字
# 内容节选，整篇 20+ 条会把 frontmatter 撑到比正文还长，gate 解析成本也跟着涨。
_FRONTMATTER_TEXT_MAX = 200
# 自由文本列表（audit warn issues）落盘条数上限；超出只写总数 + 截断提示
_FRONTMATTER_LIST_MAX = 12


def _yaml_quote(text: str) -> str:
    """把自由文本渲染成 YAML 双引号标量（含冒号/引号/换行也不破坏 frontmatter）。

    `json.dumps` 的输出是 YAML 双引号标量的合法子集（同样的 `\\"` / `\\n` 转义规则），
    故直接复用，避免为此引入 yaml 依赖。
    """
    body = " ".join((text or "").split())
    if len(body) > _FRONTMATTER_TEXT_MAX:
        body = body[:_FRONTMATTER_TEXT_MAX] + "…"
    return json.dumps(body, ensure_ascii=False)


def _degradation_frontmatter_lines(degradation: dict | None) -> list[str]:
    """把降级状态渲染成 frontmatter 行（缺陷 #9）。

    结论要求：任何人打开产出文件都能判断本期是否降级过。因此 intake / synthesis /
    translate / novelty 四个来源状态一律显式落盘，并汇总 `degraded` + 原因列表。
    `None` 表示调用方未提供（老单测路径），此时不写这些行。
    """
    if degradation is None:
        return []
    reasons = [str(r) for r in (degradation.get("reasons") or [])]
    lines = [
        f"intake_source: {degradation.get('intake_source', 'unknown')}",
        f"synthesis_source: {degradation.get('synthesis_source', 'unknown')}",
        f"translate_status: {degradation.get('translate_status', 'unknown')}",
        f"novelty_mode: {degradation.get('novelty_mode', 'unknown')}",
        f"degraded: {'true' if reasons else 'false'}",
    ]
    # 缺陷 S-4：翻译总预算耗尽的原因（call_budget / time_budget）显式落盘，
    # 否则「后半截条目没送去翻译」与「模型逐条失败」在产出层无法区分。
    budget_exhausted = degradation.get("translate_budget_exhausted")
    if budget_exhausted:
        lines.append(f"translate_budget_exhausted: {budget_exhausted}")
    failed_fields = [str(f) for f in (degradation.get("translate_failed_fields") or [])]
    if failed_fields:
        lines.append("translate_failed_fields:")
        lines += [f"  - {f}" for f in failed_fields]
    # 缺陷 S-3：audit 的 warn 级确定性问题（standard_warn_issues）此前只留在
    # audit_briefing 的返回值里、pipeline 一处都不消费 —— 产出层看到 audit_verdict: warn
    # 却无从判读究竟 warn 在哪。issue 文案含冒号/引号/换行，必须走 YAML 双引号转义。
    # 条数上限：术语表/语言纯度检查逐字段出条，整篇 27.8 个受检字段最坏能出几十条，
    # 全量落盘会让 frontmatter 比正文还长；截断但把总数写出来（截断本身可判读）。
    warn_issues = [str(w) for w in (degradation.get("audit_warn_issues") or [])]
    if warn_issues:
        lines.append(f"audit_warn_issue_count: {len(warn_issues)}")
        lines.append("audit_warn_issues:")
        lines += [f"  - {_yaml_quote(w)}" for w in warn_issues[:_FRONTMATTER_LIST_MAX]]
        if len(warn_issues) > _FRONTMATTER_LIST_MAX:
            hidden = len(warn_issues) - _FRONTMATTER_LIST_MAX
            note = f"…另有 {hidden} 条已截断（完整清单见 audit.jsonl）"
            lines.append(f"  - {_yaml_quote(note)}")
    if reasons:
        lines.append("degradation_reasons:")
        lines += [f"  - {r}" for r in reasons]
    return lines


def _audit_frontmatter_lines(
    audit_verdict: str | None,
    audit_degraded: bool,
    fix_loop_reason: str | None,
) -> list[str]:
    """审计裁决 frontmatter 行（缺陷 #4：warn 必须自带 audit_degraded 标记）。"""
    if audit_verdict is None:
        return []
    lines = [f"audit_verdict: {audit_verdict}"]
    if audit_degraded:
        lines.append("audit_degraded: true")
    if fix_loop_reason == "max_rounds_reached":
        lines.append("fix_loop: max_rounds_reached")
    return lines


def build_briefing_markdown(
    synth: dict,
    gate_decision: str,
    date: str,
    collector_name: str = "account_collector",
    *,
    audit_verdict: str | None = None,
    audit_degraded: bool = False,
    fix_loop_reason: str | None = None,
    degradation: dict | None = None,
) -> str:
    """Build the final markdown briefing content with YAML frontmatter.

    The frontmatter includes all AKB gate-compatible fields so that a re-run of
    the gate CLI on this file produces the same proposed_gate_decision.
    collector_name: 实际 collector 名（SC-P3b-1）；dry-run 时传 "dry_run"。
    audit_* / degradation: 审计裁决与降级状态，直接进 frontmatter（不再靠字符串替换）。
    """
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    tweets = synth.get("tweets", [])
    focus_discussions = synth.get("focus_discussions", [])

    frontmatter_lines = [
        "---",
        f"date: {date}",
        f"proposed_gate_decision: {gate_decision}",
        *_audit_frontmatter_lines(audit_verdict, audit_degraded, fix_loop_reason),
        "source: social_trends_v1_1",
        *_degradation_frontmatter_lines(degradation),
        f"generated_at: {now_iso}",
        "page_status: candidate",
        "source_type: news",
        "template_type: synthesis",
        "evidence_paths:",
        f"  - social/{collector_name}",
        "future_questions:",
        "  - What are the top AI trends this week?",
        "  - Which models are gaining the most developer attention?",
        "related_synthesis:",
        "  - AKB2.0/AI TRENDS/Social",
        "---",
    ]
    frontmatter = "\n".join(frontmatter_lines)

    # Build body — v2 结构：执行摘要 → 核心讨论(嵌套推文) → 其他高分推文 → 数据摘要
    body_parts = [
        f"# {date} AI 社交趋势日报",
        "",
        "## 总览",
        # P0-2 SC-14 修复：用 gate HC-08 白名单内的标题（导读→总览），消除恒 needs_repair
        "",
        f"基于 {date} 公开时间线抓取，对 AI/ML 领域社交信号的综合分析。",
        f"质量门控：**{gate_decision}**",
        "",
    ]

    # 执行摘要（model 综合给出；fallback 时可能为空）
    executive_summary = synth.get("executive_summary", "")
    if executive_summary:
        body_parts += ["## 执行摘要", "", executive_summary, ""]

    # 核心讨论骨架：每主题 summary + 嵌套支撑推文
    per_topic, orphans = _collect_display_tweets(synth, _ORPHAN_TOP_K)
    if focus_discussions:
        body_parts += ["## 核心讨论", ""]
        for i, disc in enumerate(focus_discussions):
            topic = disc.get("topic", "")
            summary = disc.get("summary", "")
            body_parts += [f"### {topic}", "", summary, ""]
            for t in per_topic[i]:
                body_parts += _render_tweet(t)

    # 其他高分推文（孤儿：未被任何主题引用）
    if orphans:
        body_parts += ["## 其他高分推文", ""]
        for t in orphans:
            body_parts += _render_tweet(t)

    scoring_summary = synth.get("scoring_summary", {})
    total = scoring_summary.get("total_tweets", len(tweets))
    # 缺陷 #5：精选数必须等于正文实渲染条数（历史上写 12 而实渲染 16 条）
    rendered = sum(len(group) for group in per_topic) + len(orphans)
    body_parts += [
        "## 数据摘要",
        "",
        f"- 总推文数：{total}",
        f"- 精选推文：{rendered}",
        f"- 生成时间：{now_iso}",
        "- 数据源：social_trends_v1_1",
        "",
    ]

    body = "\n".join(body_parts)
    return frontmatter + "\n" + body


def build_temp_briefing_markdown(
    synth: dict,
    date: str,
    collector_name: str = "account_collector",
    *,
    degradation: dict | None = None,
) -> str:
    """Build briefing markdown WITHOUT gate decision in frontmatter (for initial gate check).
    collector_name: 实际 collector 名（SC-P3b-1）；dry-run 时传 "dry_run"。
    """
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    tweets = synth.get("tweets", [])

    frontmatter_lines = [
        "---",
        f"date: {date}",
        "page_status: candidate",
        "source_type: news",
        "template_type: synthesis",
        "source: social_trends_v1_1",
        *_degradation_frontmatter_lines(degradation),
        f"generated_at: {now_iso}",
        "evidence_paths:",
        f"  - social/{collector_name}",
        "future_questions:",
        "  - What are the top AI trends this week?",
        "  - Which models are gaining the most developer attention?",
        "related_synthesis:",
        "  - AKB2.0/AI TRENDS/Social",
        "---",
    ]
    frontmatter = "\n".join(frontmatter_lines)

    # v2 骨架（与 build_briefing_markdown 保持一致，Step5）：
    # 执行摘要 → 核心讨论(嵌套推文) → 其他高分推文 → 数据摘要。
    # temp 在翻译前用 synth_en 跑 gate，推文为英文（无 zh_point/core_excerpt），
    # _render_tweet 会自然降级为 `> text` + 原文链接。
    body_parts = [
        f"# {date} AI 社交趋势日报",
        "",
        "## 总览",
        # P0-2 SC-14 修复：用 gate HC-08 白名单内的标题（导读→总览），消除恒 needs_repair
        "",
        f"基于 {date} 公开时间线抓取，对 AI/ML 领域社交信号的综合分析。",
        "",
    ]

    executive_summary = synth.get("executive_summary", "")
    if executive_summary:
        body_parts += ["## 执行摘要", "", executive_summary, ""]

    focus_discussions = synth.get("focus_discussions", [])
    per_topic, orphans = _collect_display_tweets(synth, _ORPHAN_TOP_K)
    if focus_discussions:
        body_parts += ["## 核心讨论", ""]
        for i, disc in enumerate(focus_discussions):
            body_parts += [f"### {disc.get('topic', '')}", "", disc.get("summary", ""), ""]
            for t in per_topic[i]:
                body_parts += _render_tweet(t)

    if orphans:
        body_parts += ["## 其他高分推文", ""]
        for t in orphans:
            body_parts += _render_tweet(t)

    scoring_summary = synth.get("scoring_summary", {})
    total = scoring_summary.get("total_tweets", len(tweets))
    rendered = sum(len(group) for group in per_topic) + len(orphans)
    body_parts += [
        "## 数据摘要",
        "",
        f"- 总推文数：{total}",
        f"- 精选推文：{rendered}",
        f"- 生成时间：{now_iso}",
        "",
    ]

    body = "\n".join(body_parts)
    return frontmatter + "\n" + body


def _drop_untranslated_after_budget(synth: dict) -> dict:
    """预算耗尽后剪到能过 audit 的降级稿，避免英文残留整份拒发。

    2026-09-02：translate time_budget 耗尽，tweets[23].core_excerpt 仍是英文，
    standard=fail → 当天 Social 空。S-4 只把耗尽原因落盘，没把未译条目移出
    展示集。分析字段（摘要/主题）仍走 fail-closed，这里只剪推文/主题。

    剪完必须再闭合：支撑 < 2 的主题整段拿掉；删后晋升进展示集、却没有
    zh_point 的孤儿也剪掉，直到展示集稳定。
    """
    if not synth.get("translate_budget_exhausted"):
        return synth

    result = copy.deepcopy(synth)
    dropped_any = False

    while True:
        tweets = result.get("tweets") or []
        display_idxs = _display_tweet_indices(result)
        drop_idx: set[int] = set()
        for i, tw in enumerate(tweets):
            text = (tw.get("text") or "").strip()
            core = (tw.get("core_excerpt") or "").strip()
            if _requires_chinese(text) or _requires_chinese(core):
                drop_idx.add(i)
            elif i in display_idxs and not (tw.get("zh_point") or "").strip():
                drop_idx.add(i)
        if drop_idx:
            tweets = [tw for i, tw in enumerate(tweets) if i not in drop_idx]
            dropped_any = True

        kept_ids = {str(tw.get("id", "")) for tw in tweets if tw.get("id") is not None}
        surviving_refs: set[str] = set()
        surviving_topics = []
        for fd in result.get("focus_discussions") or []:
            ids = [tid for tid in (fd.get("supporting_tweet_ids") or []) if str(tid) in kept_ids]
            surviving_topics.append((fd, ids))
            surviving_refs.update(str(tid) for tid in ids)
        refill = [
            str(tw.get("id", ""))
            for tw in sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)
            if (tw.get("zh_point") or "").strip()
            and str(tw.get("id", "")) in kept_ids
            and str(tw.get("id", "")) not in surviving_refs
        ]
        fds = []
        dropped_topic = False
        claimed: set[str] = set()
        for fd, ids in surviving_topics:
            for tid in refill:
                if len(ids) >= 2:
                    break
                if tid not in ids and tid not in claimed:
                    ids.append(tid)
                    claimed.add(tid)
            if len(ids) < 2:
                dropped_topic = True
                continue
            fd = dict(fd)
            fd["supporting_tweet_ids"] = ids
            fds.append(fd)
        if dropped_topic:
            dropped_any = True

        # SC-1：分数最高的 3 条必须出现在某个主题的 supporting_tweet_ids
        if fds:
            top_ids = [
                str(tw.get("id", ""))
                for tw in sorted(tweets, key=lambda t: t.get("score", 0), reverse=True)[:3]
                if tw.get("id") is not None
            ]
            referenced = {str(tid) for fd in fds for tid in fd.get("supporting_tweet_ids") or []}
            for tid in top_ids:
                if tid in referenced:
                    continue
                tw = next((t for t in tweets if str(t.get("id", "")) == tid), None)
                if tw is None or not (tw.get("zh_point") or "").strip():
                    continue
                fds[0]["supporting_tweet_ids"].append(tid)
                referenced.add(tid)
                dropped_any = True

        result["tweets"] = tweets
        result["focus_discussions"] = fds
        if not drop_idx and not dropped_topic:
            break

    if dropped_any:
        sys.stderr.write(
            "WARNING [pipeline]: translate 预算耗尽，剪掉未译展示推文后保留 "
            f"{len(result.get('tweets') or [])} 条、"
            f"{len(result.get('focus_discussions') or [])} 个主题以免 audit 整份拒发\n"
        )
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AKB2.0 social-trends daily briefing pipeline")
    parser.add_argument("--date", required=True, help="Briefing date (YYYY-MM-DD)")
    parser.add_argument("--vault-root", required=True, help="Vault root directory path")
    parser.add_argument("--dry-run-intake", action="store_true", help="Use fixture data for intake")
    args = parser.parse_args(argv)

    date = args.date
    vault_root = Path(args.vault_root).resolve()
    dry_run_intake = args.dry_run_intake
    window_days = int(os.environ.get("AKB2_INTAKE_WINDOW_DAYS", str(_INTAKE_WINDOW_DAYS_DEFAULT)))

    # SC-3 / Constitution 原则 7：7×24 管线禁用 -preview 模型 — 任何昂贵阶段前 fail fast。
    _assert_no_preview_translate_models()

    # Fail-closed: check vault_root before running any expensive stages.
    if not vault_root.is_dir():
        sys.stderr.write(
            f"ERROR: --vault-root does not exist or is not a directory: {vault_root}\n"
            "Hint: ensure the vault is mounted and the path is correct before running.\n"
        )
        sys.exit(EXIT_NOT_DELIVERED)

    # 文件名 = 抓取日（去掉旧的 +1 偏移）
    briefing_filename = f"{date} Daily Briefing.md"

    # 采集层时间窗（SC-6）
    # twitter 的 --until 是排他上界（"早于该日"），要包含抓取当天必须 +1 天
    date_dt = datetime.datetime.strptime(date, "%Y-%m-%d")
    since_dt = date_dt - datetime.timedelta(days=window_days - 1)
    since_date_str = since_dt.strftime("%Y-%m-%d")
    until_date_str = (date_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # Stage 1: Intake
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        intake_tmp = Path(tf.name)
    stage_start = time.monotonic()
    _stage_log("intake", "start")
    try:
        intake_data = run_intake(
            dry_run=dry_run_intake,
            output_path=intake_tmp,
            since_date=since_date_str if not dry_run_intake else None,
            until_date=until_date_str if not dry_run_intake else None,
        )
    finally:
        intake_tmp.unlink(missing_ok=True)
    _stage_log("intake", "end", time.monotonic() - stage_start)

    # 缺陷 #1：intake 降级到测试 fixture 时**硬阻断**，绝不写入 Social。
    # 实证 40 天里 12+ 天整篇内容来自 tests/fixtures（status id 19250010000000000XX），
    # frontmatter 却写 proposed_gate_decision: active / audit_verdict: pass，
    # 冒充真实抓取且无任何降级标记。阻断放在 scoring/synthesis 之前（不烧 LLM 额度）。
    intake_tweets = intake_data.get("tweets", [])
    intake_is_fallback = bool(intake_data.get("_fallback_used", False))
    if not dry_run_intake:
        block_reason = None
        if intake_is_fallback:
            block_reason = "intake_fixture_fallback"
        elif _intake_is_fixture_content(intake_tweets):
            block_reason = "intake_fixture_content"
        if block_reason:
            diag_path = _write_social_diag(
                "blocked",
                block_reason,
                date,
                {
                    "tweets_count": len(intake_tweets),
                    "fallback_used_flag": intake_is_fallback,
                    "detail": (
                        "collector 降级到测试 fixture（或产出内容与 fixture 完全一致）；"
                        "此类数据不得冒充真实抓取写入 Social"
                    ),
                },
            )
            _stage_log("intake", "blocked")
            sys.stderr.write(
                f"ERROR: intake 降级到 fixture（{block_reason}）——硬阻断，"
                f"不写入 Social。诊断: {diag_path}\n"
            )
            print(f"briefing: BLOCKED ({block_reason})")
            sys.exit(EXIT_INTAKE_FIXTURE_BLOCKED)

    # Stage 2: Scoring
    stage_start = time.monotonic()
    _stage_log("scoring", "start")
    scored_data = run_scoring(intake_data)
    _stage_log("scoring", "end", time.monotonic() - stage_start)
    novelty_mode = scored_data.get("novelty_mode") or "unknown"
    embed_degraded = bool(scored_data.get("embed_degraded"))

    # 产出层日期兜底（SC-7）：去掉超出时间窗的旧推文
    # 跳过条件：dry-run 模式（fixture 日期与今日不同）；真实运行降级到 fixture 已在上面阻断
    if not dry_run_intake:
        scored_data["tweets"] = _filter_recent(
            scored_data.get("tweets", []), date, days=window_days
        )

    # Stage 2b: 跨日去重（缺陷 #6）——必须在 synthesis / translate 之前，
    # 否则重复内容照样进 LLM（烧 token）并可能被重新分配到新主题。
    dedup_keys: list[str] = []
    dedup_window = _social_dedup_window_days(window_days)
    if not dry_run_intake:
        stage_start = time.monotonic()
        _stage_log("cross_day_dedup", "start")
        deduped, dedup_keys = apply_cross_day_dedup(
            scored_data.get("tweets", []),
            window_days=dedup_window,
            intake_window_days=window_days,
        )
        scored_data["tweets"] = deduped
        _stage_log("cross_day_dedup", "end", time.monotonic() - stage_start)

    # Stage 3: Synthesis（产出英文结构化分析，语言交翻译阶段负责）
    stage_start = time.monotonic()
    _stage_log("synthesis", "start")
    synth_en = run_synthesis(scored_data, date=date)
    _stage_log("synthesis", "end", time.monotonic() - stage_start)

    # Prepare paths
    akb2_root = vault_root / "Praise Space" / "AKB2.0"
    staging_candidate_dir = akb2_root / "staging" / "candidate"
    staging_candidate_dir.mkdir(parents=True, exist_ok=True)
    social_dir = akb2_root / "AI TRENDS" / "Social"
    social_dir.mkdir(parents=True, exist_ok=True)

    # 缺陷 S-1：`--dry-run-intake` 跳过 `_filter_recent` 与跨日去重（上面两个
    # `if not dry_run_intake`），推文也**不进账本**。若这种产出仍写进 Social，
    # 次日真实运行会把同一批内容再发一遍——fail-closed 在 dry-run 分支上是开口的。
    #
    # 语义选择：**dry-run 的产出不视为已发布**（与缺陷 #1 一致——真实运行一旦发现
    # 内容来自 fixture 就 exit 3 硬阻断；dry-run 用的正是同一批 fixture 数据，
    # 没有理由让它绕过同一条红线）。因此 dry-run 写隔离目录 staging/dry-run/，
    # Social 完全不碰，账本也不动（排练不消耗素材）。
    dry_run_output_dir = akb2_root / "staging" / "dry-run"
    if dry_run_intake:
        dry_run_output_dir.mkdir(parents=True, exist_ok=True)
        publish_dir = dry_run_output_dir
        sys.stderr.write(
            "INFO [pipeline]: --dry-run-intake — 产出写隔离目录 "
            f"{publish_dir}，不写入 Social、不记账（dry-run 产出不视为已发布）\n"
        )
    else:
        publish_dir = social_dir

    real_briefing_path = staging_candidate_dir / briefing_filename
    final_briefing_path = publish_dir / briefing_filename

    # SC-P3b-1: collector_name 从实际模式推导；缺陷 #1：降级时不得报 account_collector
    collector_mode = os.environ.get("AKB2_INTAKE_COLLECTOR", "account")
    if dry_run_intake:
        effective_collector = "dry_run"
    elif intake_is_fallback:
        # 真实运行降级到 fixture 已在上游阻断；保留此分支以防将来新增放行路径时再度误导
        effective_collector = "fixture_fallback"
    else:
        effective_collector = (
            "l0_collector" if collector_mode == "keyword" else "account_collector"
        )

    # 缺陷 #9：intake / synthesis / novelty 三类降级状态汇总（translate 在其阶段后补齐）
    degradation: dict = {
        "intake_source": effective_collector,
        "synthesis_source": (synth_en.get("source") or {}).get("name") or "unknown",
        "translate_status": "not_run",
        "novelty_mode": novelty_mode,
        "reasons": [],
    }
    if dry_run_intake:
        degradation["reasons"].append("intake_dry_run")
    if intake_is_fallback and not dry_run_intake:
        degradation["reasons"].append("intake_fixture_fallback")
    if degradation["synthesis_source"] == "synthesis_fallback":
        degradation["reasons"].append("synthesis_fallback")
    if embed_degraded:
        degradation["reasons"].append("embeddings_unavailable")

    # Stage 4a: Write temp briefing → gate（自检①：schema/结构，语言无关）
    stage_start = time.monotonic()
    _stage_log("gate", "start")
    temp_content = build_temp_briefing_markdown(
        synth_en, date, collector_name=effective_collector, degradation=degradation
    )
    real_briefing_path.write_text(temp_content, encoding="utf-8")

    gate_decision = run_gate(akb_root=akb2_root, input_file=real_briefing_path, date=date)
    _stage_log("gate", "end", time.monotonic() - stage_start)

    if gate_decision == "reject":
        sys.stderr.write("gate decision: reject — skipping translate/audit/Social write\n")
        print(f"proposed_gate_decision: {gate_decision}")
        sys.exit(EXIT_NOT_DELIVERED)

    # Stage 4b: 翻译（全中文化）+ 译后审计定点修复收敛循环
    stage_start = time.monotonic()
    _stage_log("translate", "start")
    synth_zh = translate_briefing(synth_en)
    _stage_log("translate", "end", time.monotonic() - stage_start)

    # 缺陷 #9：翻译降级必须落盘。
    # 现网失败签名是 translate_briefing 原样返回入参对象（`return synth`），
    # 另外消费翻译模块通过返回值暴露的 translate_failed / translate_degraded 标志。
    translate_failed = bool(synth_zh is synth_en) or bool(synth_zh.get("translate_failed"))
    translate_degraded = bool(synth_zh.get("translate_degraded"))
    degradation["translate_failed_fields"] = list(synth_zh.get("translate_failed_fields") or [])
    if translate_failed:
        degradation["translate_status"] = "failed"
        degradation["reasons"].append("translate_failed")
        sys.stderr.write(
            "WARNING [pipeline]: translate 未产出译文（原样返回英文 synth）——"
            "产出将标 translate_status: failed\n"
        )
    elif translate_degraded:
        degradation["translate_status"] = "degraded"
        degradation["reasons"].append("translate_degraded")
    else:
        degradation["translate_status"] = "ok"

    # 缺陷 S-4：翻译总预算耗尽（总调用数 / 总时长）必须落盘可判读，
    # 否则「后半截条目根本没送去翻译」与「模型逐条失败」在产出层长得一样。
    translate_budget_exhausted = synth_zh.get("translate_budget_exhausted")
    if translate_budget_exhausted:
        degradation["translate_budget_exhausted"] = str(translate_budget_exhausted)
        degradation["reasons"].append(
            f"translate_budget_exhausted:{translate_budget_exhausted}"
        )
        sys.stderr.write(
            "WARNING [pipeline]: translate 总预算耗尽"
            f"（{translate_budget_exhausted}）——未翻译条目见 translate_failed_fields\n"
        )
        synth_zh = _drop_untranslated_after_budget(synth_zh)

    stage_start = time.monotonic()
    _stage_log("audit_fix", "start")
    synth_zh, verdict = run_audit_fix_loop(synth_zh, synth_en)
    _stage_log("audit_fix", "end", time.monotonic() - stage_start)
    audit_verdict = verdict["verdict"]  # pass | warn | fail
    fix_loop_reason = verdict.get("fix_loop_reason")
    # 缺陷 #4：warn 一律视为降级（llm_audit=skipped / 判 fail 后仅部分收敛），
    # 必须自带 audit_degraded 标记，不能只留一个含糊的 warn。
    audit_degraded = bool(verdict.get("audit_degraded")) or audit_verdict == "warn"
    if audit_degraded:
        degradation["reasons"].append("audit_degraded")

    # 缺陷 S-3：audit 的两个悬空键必须被消费并落盘。
    #   standard_warn_issues → 产出层能判读「warn 具体 warn 在哪」
    #   audit_log_failed     → 审计日志写盘失败此前只到 stderr，产出层无从判读
    #                          裁决记录已丢失（违反 constitution 原则 4 失败可见）
    degradation["audit_warn_issues"] = [
        str(w) for w in (verdict.get("standard_warn_issues") or [])
    ]
    if verdict.get("audit_log_failed"):
        degradation["reasons"].append("audit_log_write_failed")
        sys.stderr.write(
            "WARNING [pipeline]: 审计裁决未能写入 audit.jsonl——"
            "产出标 audit_log_write_failed（裁决记录丢失可判读）\n"
        )

    def _compose_final(decision: str) -> str:
        return build_briefing_markdown(
            synth_zh,
            decision,
            date,
            collector_name=effective_collector,
            audit_verdict=audit_verdict,
            audit_degraded=audit_degraded,
            fix_loop_reason=fix_loop_reason,
            degradation=degradation,
        )

    # Stage 5: 写最终内容（含 gate_decision + audit_verdict + 降级标记）
    real_briefing_path.write_text(_compose_final(gate_decision), encoding="utf-8")

    # Provisional enforcement matrix:
    #   fail  → 不入库（文件留 staging，不 copy 到 Social）
    #   warn  → 入库但 frontmatter 已标 audit_verdict: warn + audit_degraded: true
    #   pass  → 正常入库
    if audit_verdict == "fail":
        sys.stderr.write(
            f"audit verdict: fail — briefing留在 staging/candidate，不写入 Social。\n"
            f"standard_issues: {verdict.get('standard_issues')}\n"
        )
        _write_social_diag(
            "rejected",
            "audit_verdict_fail",
            date,
            {
                "standard": verdict.get("standard"),
                "llm_audit": verdict.get("llm_audit"),
                "standard_issues": verdict.get("standard_issues"),
                "staging_path": str(real_briefing_path),
            },
        )
        print(f"proposed_gate_decision: {gate_decision}")
        print(f"audit_verdict: {audit_verdict}")
        sys.exit(EXIT_AUDIT_FAIL)  # 2 = audit fail，不入库；调度器可据此区分成功(0)与审计拒绝(2)

    # Stage 5b（缺陷 #16）：gate 在**最终写入的那份文件**上复跑。
    # 原实现只在译前的英文临时文件上跑过 gate，模块 docstring 声称的三重断言
    # （stdout == frontmatter == gate CLI 对最终文件的判定）在代码里并不成立。
    stage_start = time.monotonic()
    _stage_log("gate_final", "start")
    final_gate_decision = run_gate(akb_root=akb2_root, input_file=real_briefing_path, date=date)
    if final_gate_decision != gate_decision:
        sys.stderr.write(
            f"WARNING [gate]: 最终文件判定 {final_gate_decision} != 译前判定 "
            f"{gate_decision}——以最终文件判定为准，重写 frontmatter 后复核一次\n"
        )
        gate_decision = final_gate_decision
        real_briefing_path.write_text(_compose_final(gate_decision), encoding="utf-8")
        confirm_decision = run_gate(
            akb_root=akb2_root, input_file=real_briefing_path, date=date
        )
        if confirm_decision != gate_decision:
            # 有界重试（1 次）后仍不收敛 → fail-closed，不发布一份自相矛盾的简报
            sys.stderr.write(
                f"ERROR [gate]: 判定在最终文件上不收敛（{gate_decision} → "
                f"{confirm_decision}）——不写入 Social\n"
            )
            _write_social_diag(
                "failed",
                "gate_decision_not_converged",
                date,
                {"first": gate_decision, "second": confirm_decision,
                 "staging_path": str(real_briefing_path)},
            )
            print(f"proposed_gate_decision: {confirm_decision}")
            sys.exit(EXIT_GATE_NOT_CONVERGED)
    _stage_log("gate_final", "end", time.monotonic() - stage_start)

    if gate_decision == "reject":
        sys.stderr.write(
            "gate decision on final file: reject — 不写入 Social（文件留 staging）\n"
        )
        _write_social_diag(
            "rejected", "gate_reject_on_final_file", date,
            {"staging_path": str(real_briefing_path)},
        )
        print(f"proposed_gate_decision: {gate_decision}")
        sys.exit(EXIT_NOT_DELIVERED)

    # Publish to Social（dry-run 时目标是 staging/dry-run，见上文 publish_dir）。
    # 必须 fsync + 同目录 replace：外置卷上 shutil.copy2 返回后立刻 open 可能
    # ENOENT（2026-09-18 backstop 写后 2s awk/cat 都打不开，判 false_recovery）。
    # 形状对齐 github `_atomic_write_text`。
    stage_start = time.monotonic()
    _stage_log("write_social", "start")
    _atomic_publish_text(real_briefing_path, final_briefing_path)
    real_briefing_path.unlink(missing_ok=True)
    sys.stderr.write("staging 中转已清除\n")
    _stage_log("write_social", "end", time.monotonic() - stage_start)

    # 缺陷 #6：只有真正发布之后才记账，避免未发布的运行白吃掉当天素材
    # （dry-run 不进这里：dedup_keys 恒空，账本不动）
    if dedup_keys:
        mark_social_dedup_seen(
            dedup_keys, window_days=dedup_window, intake_window_days=window_days
        )

    print(f"proposed_gate_decision: {gate_decision}")
    return EXIT_DELIVERED


if __name__ == "__main__":
    sys.exit(main())
