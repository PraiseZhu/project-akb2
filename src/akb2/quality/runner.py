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
import re
import signal
import subprocess
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
DISTILL_TIMEOUT_S: float = 60.0
DISTILL_MAX_RETRIES: int = 0
DISTILL_EXTRACT_WORKER_TIMEOUT_S: float = 210.0
DISTILL_EXTRACT_WORKER_KILL_GRACE_S: float = 2.0
DISTILL_EXTRACT_WORKER_MODULE: str = "src.akb2.quality.distill_worker"

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
    golden_sample_path: str = ""  # 权威黄金样张(喂 repair 爬坡);空则不喂

    def __post_init__(self) -> None:
        if not self.lock_dir:
            self.lock_dir = os.path.join(self.staging_dir, "locks")


def _now() -> datetime:
    """当前 UTC 时间（可 mock）。"""
    return datetime.now(UTC)


def _load_golden_sample(config: RunnerConfig) -> str | None:
    """读权威黄金样张(addy active 页)作 repair 爬坡标杆;缺失返回 None(repair 降级 prompt)。

    路径优先级:config.golden_sample_path override > 从 raw_dir 派生
    (akb2_root = raw_dir.parent;golden = akb2_root/wiki/sources/addy-osmani-loop-engineering.md)。
    """
    golden_path = (
        Path(config.golden_sample_path)
        if config.golden_sample_path
        else Path(config.raw_dir).parent / "wiki/sources/addy-osmani-loop-engineering.md"
    )
    try:
        return golden_path.read_text(encoding="utf-8")
    except OSError:
        return None


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


def _derive_raw_slug(raw_path: str) -> str:
    """从 raw 文件名提取 source slug。"""
    return Path(raw_path).name.split("--raw--")[0]


def _read_frontmatter_value(path: Path, key: str) -> str:
    """Read one scalar YAML frontmatter value without exposing unrelated content."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 4)
    if end == -1:
        return ""
    pattern = rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$"
    match = re.search(pattern, text[4:end])
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _existing_source_page_for_raw(
    raw_path: str,
    config: RunnerConfig,
) -> Path | None:
    """Return existing active source page for the raw slug or source URL.

    A raw file whose slug or source URL already exists under `wiki/sources/` is not a new
    knowledge item. It should not enter distill as a "novel" candidate; otherwise
    GPT-5.4 correctly rejects it later as same-topic overlap.
    """
    slug = _derive_raw_slug(raw_path)
    source_dir = Path(config.vault_root) / "wiki" / "sources"
    if slug:
        source_page = source_dir / f"{slug}.md"
        if source_page.exists():
            return source_page

    raw_url = _read_frontmatter_value(Path(raw_path), "source_url")
    if not raw_url or not source_dir.exists():
        return None
    for source_page in sorted(source_dir.glob("*.md")):
        if _read_frontmatter_value(source_page, "source_url") == raw_url:
            return source_page
    return None


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


def _classify_distill_error(error_detail: str) -> str:
    """将 distill 失败归为可观测错误类，避免泛化 error。"""
    lowered = error_detail.lower()
    if (
        "timeout" in lowered
        or "deadline" in lowered
        or "超时" in error_detail
        or "总时限" in error_detail
        or "超过" in error_detail
    ):
        return "network_timeout"
    if "schema" in lowered or "校验" in error_detail:
        return "schema_validation_failed"
    if "json" in lowered or "非 json" in lowered:
        return "invalid_json"
    if "调用失败" in error_detail or "api" in lowered:
        return "provider_error"
    return "extract_failed"


def _distill_abort_details(
    error_detail: str,
    node: str,
) -> dict[str, Any]:
    """distill abort 的统一 details。"""
    return {
        "error": error_detail,
        "error_class": _classify_distill_error(error_detail),
        "model_family": "deepseek-v4-pro",
        "timeout_s": DISTILL_TIMEOUT_S,
        "max_retries": DISTILL_MAX_RETRIES,
        "retries_used": 0,
        "node": node,
    }


# ---------------------------------------------------------------------------
# 七段管道
# ---------------------------------------------------------------------------


# vault 概念索引 loader 的 typed status
LANE_INDEX_STATUS_OK = "ok"
LANE_INDEX_STATUS_FIRST_RUN = "first_run_no_index"
LANE_INDEX_STATUS_EMPTY = "configured_but_empty"
LANE_INDEX_STATUS_LOAD_ERROR = "load_error"

# _check_novelty 只用前 5 条 wiki_references
VAULT_WIKI_REFERENCES_MAX = 5


def _load_vault_concept_index(
    config: RunnerConfig,
    exclude_slug: str = "",
) -> dict[str, Any]:
    """确定性装载 vault 概念/wiki 索引（纯文件扫描，无 LLM，符合宪法 P5 单向 vault→工程）。

    Decision-2（洗稿生产接线）+ A3-8（图谱 resolve 生产真传 known/pending）：
        - known_concepts = glob `<akb2_root>/wiki/concepts/*.md` + `wiki/sources/*.md`
          的 slug 集（SC-2b-b1：概念页+源页，resolver 把两者都当合法 resolve 目标）
        - pending_queue  = 读 `<staging>/pending/concept-backlinks.jsonl` 的 concept slug
        - wiki_references = `<akb2_root>/wiki/**/*.md` 各页 TLDR 摘要（≤5），**排除当前源
          slug**（防自比对误判洗稿）
    vault 路径经 loops/paths.get_akb2_root/get_staging_root 解析（不硬编码）。

    Returns:
        {status, counts, wiki_references, known_concepts, pending_queue, source_paths}
        status ∈ {ok, first_run_no_index, configured_but_empty, load_error}：
          - ok：wiki 索引存在且非空，正常喂 novelty。
          - first_run_no_index：wiki 目录不存在（首跑无索引），允许空索引默认 pass。
          - configured_but_empty：wiki 目录存在但无任何概念/源页（异常空），生产应 hold/reject。
          - load_error：扫描抛错（路径解析失败等），生产应 hold/reject。
    """
    try:
        from src.akb2.loops.paths import get_akb2_root, get_staging_root

        akb2_root = get_akb2_root()
        staging_root = get_staging_root()
    except Exception as exc:
        return {
            "status": LANE_INDEX_STATUS_LOAD_ERROR,
            "counts": {"known_concepts": 0, "pending_queue": 0, "wiki_references": 0},
            "wiki_references": [],
            "known_concepts": [],
            "pending_queue": [],
            "source_paths": [],
            "error": f"vault 路径解析失败: {exc}",
        }

    wiki_dir = akb2_root / "wiki"
    if not wiki_dir.exists():
        return {
            "status": LANE_INDEX_STATUS_FIRST_RUN,
            "counts": {"known_concepts": 0, "pending_queue": 0, "wiki_references": 0},
            "wiki_references": [],
            "known_concepts": [],
            "pending_queue": [],
            "source_paths": [],
        }

    try:
        # SC-2b-b1：把 wiki/sources/*.md slug 折进 known_concepts——resolver docstring
        # 已明示 known_concepts=概念或源，但旧 loader 只 glob concepts，导致源 wikilink
        # （如 [[anthropic-effective-context-engineering]]）即便指向已存在源页也判死链
        # （生产也中招，不止校准环境）。
        concept_slugs = [p.stem for p in (wiki_dir / "concepts").glob("*.md")]
        source_slugs = [p.stem for p in (wiki_dir / "sources").glob("*.md")]
        known_concepts = sorted(set(concept_slugs) | set(source_slugs))
        wiki_pages = sorted(wiki_dir.rglob("*.md"))
        # §0.2/D10/G1：wiki_references 改结构化 {path,slug,title,tldr,source_url,taxonomy}
        # 供 _select_novelty_neighbors 按 slug/url/title/topic top-k 选近邻（修 idx6/idx10
        # 全库前 5 条无关引用导致 novelty 误拒）。
        wiki_references: list[dict[str, str]] = []
        source_paths: list[str] = []
        for page in wiki_pages:
            if exclude_slug and page.stem == exclude_slug:
                continue  # 排除当前处理源自身，防自比对误判
            tldr = _read_page_tldr(page)
            if not tldr:
                continue
            meta = _read_wiki_page_meta(page)
            wiki_references.append(
                {
                    "path": str(page),
                    "slug": page.stem,
                    "title": meta.get("title", "") or page.stem,
                    "tldr": tldr,
                    "source_url": meta.get("source_url", ""),
                    "taxonomy": "concept" if "concepts" in page.parts else "source",
                }
            )
            source_paths.append(str(page))
            if len(wiki_references) >= VAULT_WIKI_REFERENCES_MAX:
                break

        pending_queue = _read_pending_concepts(
            staging_root / "pending" / "concept-backlinks.jsonl"
        )
    except OSError as exc:
        return {
            "status": LANE_INDEX_STATUS_LOAD_ERROR,
            "counts": {"known_concepts": 0, "pending_queue": 0, "wiki_references": 0},
            "wiki_references": [],
            "known_concepts": [],
            "pending_queue": [],
            "source_paths": [],
            "error": f"vault 索引扫描失败: {exc}",
        }

    # wiki 目录存在但完全无概念页与可比对源摘要 → configured_but_empty（生产 hold/reject）
    if not known_concepts and not wiki_references:
        status = LANE_INDEX_STATUS_EMPTY
    else:
        status = LANE_INDEX_STATUS_OK

    return {
        "status": status,
        "counts": {
            "known_concepts": len(known_concepts),
            "pending_queue": len(pending_queue),
            "wiki_references": len(wiki_references),
        },
        "wiki_references": wiki_references,
        "known_concepts": known_concepts,
        "pending_queue": pending_queue,
        "source_paths": source_paths,
    }


def _read_wiki_page_meta(page: Path) -> dict[str, str]:
    """§0.2/D10/G1：读 wiki 页 frontmatter 的 title/source_url（结构化 wiki_references）。

    确定性纯文本提取，无 LLM。取不到返回空字符串。
    """
    meta: dict[str, str] = {}
    try:
        text = page.read_text(encoding="utf-8")
    except OSError:
        return meta
    if not text.lstrip().startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    fm = text[3:end]
    for key in ("title", "source_url", "canonical_title"):
        m = re.search(rf"^({key})\s*:\s*(.+?)\s*$", fm, re.M)
        if m:
            val = m.group(2).strip().strip("\"'")
            if val and key not in meta:
                meta[key] = val
    # canonical_title fallback for title
    if "title" not in meta and "canonical_title" in meta:
        meta["title"] = meta["canonical_title"]
    return meta


def _read_page_tldr(page: Path) -> str:
    """读取 wiki 页的 TLDR 摘要（frontmatter gate_reason 或 ## TLDR 节首段）。

    确定性纯文本提取，无 LLM。取不到返回空字符串。
    """
    try:
        text = page.read_text(encoding="utf-8")
    except OSError:
        return ""
    # 优先 ## TLDR 节首段
    m = re.search(r"(?ms)^##\s*TLDR\s*\n+(.+?)(?=\n#|\Z)", text)
    if m:
        snippet = m.group(1).strip()
        if snippet:
            return snippet[:800]
    # 退化：取正文首个非空段（跳过 frontmatter）
    body = text
    if body.lstrip().startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:]
    for para in body.split("\n\n"):
        para = para.strip()
        if para and not para.startswith("#"):
            return para[:800]
    return ""


def _read_pending_concepts(pending_path: Path) -> list[str]:
    """读 pending 队列 jsonl 的 concept_slug 集（promote.py 写入处的对账）。"""
    if not pending_path.exists():
        return []
    slugs: list[str] = []
    try:
        with pending_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                slug = entry.get("concept_slug")
                if slug:
                    slugs.append(str(slug))
    except OSError:
        return slugs
    return sorted(set(slugs))


def _run_admission_segment(
    raw_path: str,
    raw_content: str,
    config: RunnerConfig,
    wiki_references: list[str] | None = None,
    index_status: str = LANE_INDEX_STATUS_OK,
    content_hash: str = "",
) -> tuple[bool, str, list, str, str]:
    """段 2: 准入判定（Decision-2：装载 vault wiki_references 喂 novelty）。

    wiki_references 由 pipeline 经 _load_vault_concept_index 装载传入；novelty 在生产
    路径据此真判洗稿（不再恒空默认 pass）。index_status 为生产 configured_but_empty/
    load_error 时写 ledger `novelty_index_unavailable` 并返回 hold/reject（不静默 pass）。

    Returns:
        (passed, reason, checks, content_lane, lane_reason)
    """
    from src.akb2.quality.admission import run_admission

    # 生产索引异常（空/错）→ fail-closed hold/reject，ledger 可见（不静默放行洗稿）
    production_bad_index = index_status in (
        LANE_INDEX_STATUS_EMPTY,
        LANE_INDEX_STATUS_LOAD_ERROR,
    )
    if production_bad_index:
        _log_event(
            config,
            "abort",
            "admission",
            content_hash,
            {
                "reason": "novelty_index_unavailable",
                "index_status": index_status,
                "raw_path": raw_path,
            },
        )
        return (
            False,
            f"准入未通过: novelty_index_unavailable ({index_status})",
            [],
            "",
            "",
        )

    result = run_admission(
        raw_path=raw_path,
        raw_content=raw_content,
        ledger_path=config.ledger_path,
        wiki_references=wiki_references,
    )
    return result.passed, result.reason, result.checks, result.content_lane, result.lane_reason


def _compute_mechanism_rendering(
    extract_data: dict[str, Any], candidate_text: str
) -> dict[str, Any]:
    """§0.2/F3：机制渲染信息——供 D12 hidden_mechanism_edges 真算。

    - mechanism_section_rendered: 正文是否含 ## 机制组件与因果边证据 节
    - rendered_edge_count: 渲染的因果边表行数（粗计 |from|to|...| 行）
    - extract_source_causal_edge_count: extract_data 中 eligible edge 数
    - hidden: extract 有 eligible edge 但正文未渲染机制表/边 → True
    """
    edges = extract_data.get("causal_edges", []) or []
    eligible_count = sum(
        1 for e in edges
        if isinstance(e, dict)
        and e.get("edge_kind") in ("source_causal", "llm_evidence")
    )
    section_rendered = "## 机制组件与因果边证据" in (candidate_text or "")
    # 粗计渲染的边表行：机制节内以 | from | 或 |组件名| 开头的数据行
    rendered_edge_count = 0
    if section_rendered:
        # 数机制节内 "因果边" 表的数据行（含 edge_kind 的行）
        import re as _re
        section_match = _re.search(
            r"## 机制组件与因果边证据\s*(.*?)(?=\n## |\Z)",
            candidate_text or "",
            _re.DOTALL,
        )
        if section_match:
            section = section_match.group(1)
            edge_part = section.split("因果边：")[-1] if "因果边：" in section else ""
            for line in edge_part.splitlines():
                line = line.strip()
                if line.startswith("|") and "---" not in line and "from" not in line.lower():
                    rendered_edge_count += 1
    return {
        "mechanism_section_rendered": section_rendered,
        "rendered_edge_count": rendered_edge_count,
        "extract_source_causal_edge_count": eligible_count,
        "hidden": bool(eligible_count > 0 and (not section_rendered or rendered_edge_count == 0)),
    }


def _run_distill_segment(
    raw_path: str,
    raw_content: str,
    config: RunnerConfig,
    content_lane: str = "",
    known_concepts: list[str] | None = None,
    pending_queue: list[str] | None = None,
) -> tuple[bool, str, str, str | None, dict[str, Any]]:
    """段 3-4: 提炼。

    content_lane / known_concepts / pending_queue 由 pipeline 从准入段 + vault loader
    传入，透传给抽取子进程（A3-1）+ build_deterministic + compose_candidate（含
    compose 内第一道 self-gate 的图谱 resolve）。本 step 仅穿线、不改判定。

    Returns:
        (success, candidate_path_or_error, error_detail, candidate_text, distill_meta)
    """
    from src.akb2.quality.distill import (
        build_deterministic_extract_result,
        compose_candidate,
    )

    # 段 1: 结构化抽取（携 content_lane 穿过子进程边界 — A3-1）
    extract_result = _run_extract_claims_worker(raw_content, content_lane)
    maker_fallback_used = False
    maker_primary_error = ""
    if not extract_result.success and os.environ.get("AKB2_MAKER_FALLBACK", "1") != "0":
        maker_primary_error = extract_result.error
        extract_result = build_deterministic_extract_result(
            raw_content, raw_path, content_lane=content_lane
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
                # §0.2/D9：fallback 失败路径也须携 fallback_quality_basis（不靠 error
                # string 解析），供 calibration 漏斗归因。
                "fallback_quality_basis": extract_result.data.get("fallback_quality_basis"),
            },
        )

    # step-3 SC-3b/SC-3h：完整性地板在 distill 段评估（持有结构化 extract，A-R4 保留）。
    # FloorResult 是小对象，随 distill_meta 携出（保持 5 元组契约，不扩 tuple arity 避免
    # 破坏既有 5 元组解包的存量调用方/测试）；评分段先看本结果 early-return（加权分不算）。
    from src.akb2.quality.integrity_floor import evaluate_integrity_floor

    floor_result = evaluate_integrity_floor(extract_result.data, content_lane)

    # §0.2/F1 太脆：concept primary success 但 0 eligible edge（LLM 漏 edge_kind/anchor
    # 字段）→ floor 标 needs_causal_edge_retake。runner 用确定性 _build_real_causal_edges
    # 从 raw 补抽带真实 §-anchor 的 source_causal 边（不造假），合并进 extract_data，
    # 重评 floor。仍 0 eligible → fail-closed（不放松 F4）。
    if (
        str(content_lane).lower() == "concept"
        and floor_result.meta.get("needs_causal_edge_retake")
        and extract_result.success
    ):
        from src.akb2.quality.distill import _build_real_causal_edges

        components = extract_result.data.get("mechanism_components", []) or []
        retake_edges = _build_real_causal_edges(components, raw_content)
        if retake_edges:
            # 合并：保留 LLM 原有 eligible 边（若有），补 fallback 抽的真实边
            existing = list(extract_result.data.get("causal_edges", []) or [])
            existing_pairs = {(e.get("from"), e.get("to")) for e in existing if isinstance(e, dict)}
            for e in retake_edges:
                if (e.get("from"), e.get("to")) not in existing_pairs:
                    existing.append(e)
            extract_result.data["causal_edges"] = existing
            floor_result = evaluate_integrity_floor(extract_result.data, content_lane)
            floor_result.meta["causal_edge_retake_applied"] = True

    # 段 2: 成稿
    ts = _now().strftime("%Y-%m-%d-%H%M%S")
    batch_id = f"{ts}"
    compose_result = compose_candidate(
        extract_result=extract_result,
        raw_path=raw_path,
        batch_id=batch_id,
        output_dir=config.staging_dir,
        raw_content=raw_content,
        content_lane=content_lane,
        known_concepts=known_concepts,
        pending_queue=pending_queue,
    )
    if not compose_result.success:
        return (
            False,
            f"compose_failed: {compose_result.error}",
            compose_result.error,
            None,
            {
                "failed_node": "distill_compose",
                "retries_used": extract_result.retries_used,
                "maker_fallback_used": maker_fallback_used,
                "maker_primary_error": maker_primary_error,
            },
        )

    # 读取候选页文本
    try:
        candidate_text = Path(compose_result.candidate_path).read_text(encoding="utf-8")
    except OSError as exc:
        return (
            False,
            f"read_failed: {exc}",
            str(exc),
            None,
            {
                "failed_node": "distill_compose",
                "retries_used": extract_result.retries_used,
                "maker_fallback_used": maker_fallback_used,
                "maker_primary_error": maker_primary_error,
            },
        )

    return (
        True,
        compose_result.candidate_path,
        "",
        candidate_text,
        {
            "retries_used": extract_result.retries_used,
            "maker_fallback_used": maker_fallback_used,
            "maker_primary_error": maker_primary_error,
            # step-3 SC-3b/SC-3h：完整性地板结果随 distill_meta 携出，供评分段 early-return。
            "floor_result": floor_result,
            # §0.2/D9：fallback 成功路径也携 fallback_quality_basis（机读质量基线）。
            "fallback_quality_basis": extract_result.data.get("fallback_quality_basis"),
            # §0.2/F3：机制渲染信息——供 D12 hidden_mechanism_edges 真算
            # （source_causal_edge_count>0 但正文未渲染机制表/边 → hidden>0）。
            "mechanism_rendering": _compute_mechanism_rendering(
                extract_result.data, candidate_text
            ),
        },
    )


def _run_extract_claims_worker(raw_content: str, content_lane: str = "") -> Any:
    """Run DeepSeek extract in a killable child process.

    SDK/http stacks can occasionally ignore in-process timers while waiting on
    the gateway. Keeping extract in a subprocess gives the runner a hard outer
    deadline and lets it write the fail-closed abort ledger itself.

    A3-1（抽取子进程过界）：向子进程传携 content_lane 的 JSON payload（非裸
    raw_content），使 lane 穿过子进程边界到达 extract_claims——否则概念正样本在
    extract 阶段被 H1 门拒、走不到 compose（lane-aware extract 改判属 step-3）。
    """
    from src.akb2.quality.distill import ExtractResult

    cmd = [sys.executable, "-m", DISTILL_EXTRACT_WORKER_MODULE]
    worker_input = json.dumps(
        {"raw_content": raw_content, "content_lane": content_lane},
        ensure_ascii=False,
    )
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(
            input=worker_input,
            timeout=DISTILL_EXTRACT_WORKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        if proc is not None:
            _kill_process_group(proc, DISTILL_EXTRACT_WORKER_KILL_GRACE_S)
        return ExtractResult(
            success=False,
            retries_used=0,
            error=(
                "extract_worker_timeout: "
                f"抽取子进程超过总时限 {DISTILL_EXTRACT_WORKER_TIMEOUT_S}s"
            ),
        )
    except OSError as exc:
        return ExtractResult(
            success=False,
            retries_used=0,
            error=f"extract_worker_os_error: {exc}",
        )

    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()
    if proc.returncode != 0:
        detail = stderr[-1000:] or stdout[-1000:] or "no worker output"
        return ExtractResult(
            success=False,
            retries_used=0,
            error=f"extract_worker_exit_{proc.returncode}: {detail}",
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        detail = stdout[-1000:] or stderr[-1000:] or "empty worker output"
        return ExtractResult(
            success=False,
            retries_used=0,
            error=f"extract_worker_invalid_json: {detail}",
        )
    if not isinstance(payload, dict):
        return ExtractResult(
            success=False,
            retries_used=0,
            error=f"extract_worker_invalid_payload: {type(payload).__name__}",
        )

    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}
    return ExtractResult(
        success=bool(payload.get("success")),
        data=data,
        retries_used=int(payload.get("retries_used") or 0),
        error=str(payload.get("error") or ""),
    )


def _kill_process_group(proc: subprocess.Popen[Any], grace_s: float) -> None:
    """Terminate a worker process group, escalating to SIGKILL after grace."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()


def _check_concept_hard_gates(
    entailment: Any,
    dim_scores: dict[str, float] | None,
    content_lane: str,
    base_meta: dict[str, Any],
) -> tuple[str, str, dict[str, Any]] | None:
    """concept 车道硬门：F3 层② entailment + 加权分过阈（codex P1 修复）。

    initial 无 defects 路径 + repair 收敛后路径都调（防 repair 绕过 poisoned-trace/低分）。
    entailment 是异族 grader 地板（fail 不被加权分补偿），故先判 entailment 再判加权分。

    Args:
        entailment: grader_result.interpretation_entailment（True/False/None）。
        dim_scores: grader_result.dimension_scores（或 repair final_grader_meta 携出的）。
        content_lane: 仅 concept 查；procedural/未知直接返回 None（零回归）。
        base_meta: 已含 a/b_defect_count + grader_meta + repair_rounds + final_defects_summary。

    Returns:
        None = 通过；else (error_class, error_detail, meta) 供 runner return。
    """
    if str(content_lane or "").strip().lower() != "concept":
        return None
    if entailment is False:
        return (
            "poisoned_trace_entailment",
            "F3 层② 异族 grader 判解读未从 cited anchor 推出（poisoned-trace）",
            {**base_meta, "interpretation_entailment": False, "rejection": "poisoned_trace",
             "weighted_score_not_computed": True},
        )
    if entailment is None:
        # concept 必填 entailment；None=grader 未评（旧响应/fallback 残留）→ fail-closed
        return (
            "entailment_not_evaluated",
            "concept 车道 require interpretation_entailment，grader 未评（fail-closed）",
            {**base_meta, "interpretation_entailment": None,
             "rejection": "entailment_not_evaluated",
             "weighted_score_not_computed": True},
        )
    from src.akb2.quality.weighted_score import compute_weighted_score
    score_result = compute_weighted_score(dim_scores or {}, content_lane)
    if not score_result.passed:
        return (
            "weighted_score_below_threshold",
            f"concept 加权分 {score_result.score} < {score_result.threshold}（过阈不排序）",
            {**base_meta, "interpretation_entailment": True,
             "weighted_score": score_result.score, "weighted_threshold": score_result.threshold,
             "rejection": "weighted_score_below_threshold"},
        )
    # wA#3：通过时把异族硬判证据写回 base_meta，使落盘 PASS artifact 带证据（非 null），
    # 不丢 interpretation_entailment / weighted_score 证据链（护栏⑥）。
    base_meta["interpretation_entailment"] = True
    base_meta["weighted_score"] = score_result.score
    base_meta["weighted_threshold"] = score_result.threshold
    return None


def _run_quality_segments(
    candidate_text: str,
    candidate_path: str,
    raw_content: str,
    config: RunnerConfig,
    grader_result_path: Path | None = None,
    content_lane: str = "",
    floor_result: Any = None,
    known_concepts: list[str] | None = None,
    pending_queue: list[str] | None = None,
) -> tuple[bool, str, str, dict[str, Any]]:
    """段 5-6: 结构校验 + 评分 + 修复。

    content_lane（step-1 透传字段）：传给 run_grader_detailed / run_repair_loop，
    仅随评分/修复记录，不参与判定（lane-aware 判定属 step-3）。

    floor_result（step-3 SC-3b/SC-3h）：distill 段评估的完整性地板结果（FloorResult）。
    评分段 MUST 先看本结果——任一地板成员 fail → 直接 reject、加权分函数 NOT 调用、
    grader NOT called（显式 early-return + 日志 floor_failed + weighted_score_not_computed +
    grader_not_called，宪法原则 8(a) 不可补偿）。None 时（旧测试调用方/无地板）按旧行为跑。

    converged=True 表示最终稿结构层(A)+评分层(B)零缺陷。此时把 grader PASS
    结果落盘到 grader_result_path，供段 7 的 Gate-5 独立复核读取——消除
    runner 升格路径 grader_result_path=None 导致 Gate-5 恒 fail 的接线缺口。

    Returns:
        (converged, error_class, error_detail, quality_meta)
        quality_meta: {
            a_defect_count, a_defect_summary,
            b_defect_count, b_defect_summary,
            repair_rounds, final_defects_summary
        }
    """
    from src.akb2.quality.checks_structural import run_candidate_self_gate
    from src.akb2.quality.grader import (
        REQUIRED_RUBRIC_VERSION,
        _dimensions_for_lane,
        run_grader_detailed,
    )
    from src.akb2.quality.repair import run_repair_loop

    def _defect_summary(defects: list[Any]) -> list[dict[str, str]]:
        return [
            {
                "dimension": getattr(d, "dimension", ""),
                "location": getattr(d, "location", ""),
                "defect": getattr(d, "defect", ""),
            }
            for d in defects
        ]

    def _persist_grader_pass(grader_meta: dict[str, Any]) -> None:
        """收敛后落盘 grader PASS 结果（Gate-5 读取的 canonical 格式）。

        仅在 converged=True 时调用——此时缺陷已清零，status=PASS 为事实，
        非伪造。grader_result_path 为 None 时（旧测试调用方）为 no-op。

        fail-closed: 若 grader_meta 标记 fallback_used/suspended（degraded 评分），
        写 status=DEGRADED 而非 PASS，阻断升格——degraded grader 不可作为通过条件。
        """
        if grader_result_path is None:
            return
        is_degraded = bool(
            grader_meta.get("grader_fallback_used", False)
            or grader_meta.get("grader_suspended", False)
        )
        status = "DEGRADED" if is_degraded else "PASS"
        grader_result_path.parent.mkdir(parents=True, exist_ok=True)
        grader_result_path.write_text(
            json.dumps(
                {
                    "status": status,
                    # codex worker #3：lane-aware dimensions_checked（concept=7, procedural=10）
                    "dimensions_checked": len(_dimensions_for_lane(content_lane)),
                    "content_lane": content_lane,
                    "rubric_version": REQUIRED_RUBRIC_VERSION,
                    "interpretation_entailment": grader_meta.get("interpretation_entailment"),
                    "weighted_score": grader_meta.get("weighted_score"),
                    "weighted_threshold": grader_meta.get("weighted_threshold"),
                    "findings": [],
                    "model_used": grader_meta.get("grader_model_used", ""),
                    "primary_model": grader_meta.get("grader_primary_model", ""),
                    "fallback_used": grader_meta.get("grader_fallback_used", False),
                    "primary_failure_reason": grader_meta.get(
                        "grader_primary_failure_reason", ""
                    ),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    grader_meta: dict[str, Any] = {
        "grader_model_used": "",
        "grader_primary_model": "",
        "grader_fallback_model": "",
        "grader_suspended": False,
        "grader_fallback_used": False,
        "grader_primary_failed": False,
        "grader_primary_failure_reason": "",
        "grader_fallback_failed": False,
        "grader_fallback_failure_reason": "",
    }
    b_defects: list[Any] = []

    # step-3 SC-3b/SC-3h：完整性地板 early-return（宪法原则 8(a) 不可补偿）。
    # 任一地板成员 fail → 直接 reject，加权分函数 NOT 调用、grader NOT called。
    # 这一步 MUST 在 A 层 self-gate 与 grader 之前——floor fail 时这两者都不跑，
    # 避免「辅助维满分顶过地板 fail」的补偿。floor_result=None 时按旧行为（无地板跑全链）。
    if floor_result is not None and not getattr(floor_result, "passed", True):
        failed_floors = list(getattr(floor_result, "failed_floors", []))
        floor_reason = getattr(floor_result, "reason", "")
        meta = {
            "a_defect_count": 0,
            "a_defect_summary": [],
            "b_defect_count": 0,
            "b_defect_summary": [],
            **grader_meta,
            "repair_rounds": 0,
            "final_defects_summary": [],
            # 日志硬证：地板 fail / 加权分未算 / grader 未调（SC-3b/SC-3h 断言这三个 key）
            "floor_failed": failed_floors,
            "weighted_score_not_computed": True,
            "grader_not_called": True,
            "content_lane": content_lane,
            # P3：floor_result.meta（如 traceability_skipped_no_grounded_anchor）作为子 dict
            # 穿进 quality_meta，让 694d423 的可见诊断不丢（w7：原未穿到 runner）。
            "floor_meta": getattr(floor_result, "meta", {}) or {},
        }
        return (
            False,
            "integrity_floor_failed",
            f"完整性地板未过（不可补偿）: {floor_reason}",
            meta,
        )

    # A 层: 结构校验。结构缺陷不直接截断 repair；可机器修复的问题
    # 进入同一个有界 repair/fail-closed 闭环，修不掉再停链。
    # step-3 SC-3e/A3-8：runner 第二道 self-gate 真传 known_concepts/pending_queue +
    # content_lane（与 compose 第一闸同一 known/pending 输入复核同语义）。
    a_defects = run_candidate_self_gate(
        candidate_text,
        raw_content,
        pending_queue=pending_queue,
        known_concepts=known_concepts,
        content_lane=content_lane,
    )

    if not a_defects:
        # B 层: LLM 评分。只有 A 层通过时才调用，避免对结构不合格稿件花费裁判预算。
        grader_result = run_grader_detailed(candidate_text, content_lane=content_lane)
        b_defects = grader_result.defects
        grader_meta = {
            "grader_model_used": grader_result.model_used,
            "grader_primary_model": grader_result.primary_model,
            "grader_fallback_model": grader_result.fallback_model,
            "grader_suspended": grader_result.suspended,
            "grader_fallback_used": grader_result.fallback_used,
            "grader_primary_failed": grader_result.primary_failed,
            "grader_primary_failure_reason": grader_result.primary_failure_reason,
            "grader_fallback_failed": grader_result.fallback_failed,
            "grader_fallback_failure_reason": grader_result.fallback_failure_reason,
        }
        if grader_result.suspended:
            meta = {
                "a_defect_count": 0, "a_defect_summary": [],
                "b_defect_count": 0, "b_defect_summary": [],
                **grader_meta,
                "repair_rounds": 0, "final_defects_summary": [],
            }
            return False, "grader_all_down", "评分器双不可用，稿件挂起", meta

        if grader_result.fallback_used:
            meta = {
                "a_defect_count": 0,
                "a_defect_summary": [],
                "b_defect_count": len(b_defects),
                "b_defect_summary": _defect_summary(b_defects),
                **grader_meta,
                "repair_rounds": 0,
                "final_defects_summary": _defect_summary(b_defects),
            }
            return (
                False,
                "grader_fallback_not_allowed",
                "GPT-5.4 裁判不可用，Gemini fallback 只记录诊断，不能作为通过条件",
                meta,
            )

        # step-3 SC-3c/SC-3d concept 门不在此（移到 `if not all_defects:` 内）：
        # 有 defects 进 repair（defects actionable），无 defects 才查 entailment/加权分。

    # 无缺陷 → 通过
    all_defects = a_defects + b_defects
    if not all_defects:
        # step-3 SC-3c/SC-3d：concept 车道无 defects 后，判 F3 层② entailment + 加权分过阈
        # （initial 无 defects 路径；repair 收敛后路径在下方也调同一 helper——codex P1 修复）。
        if str(content_lane or "").strip().lower() == "concept":
            _base = {
                "a_defect_count": 0, "a_defect_summary": [],
                "b_defect_count": 0, "b_defect_summary": [],
                **grader_meta,
                "repair_rounds": 0, "final_defects_summary": [],
            }
            _gate = _check_concept_hard_gates(
                grader_result.interpretation_entailment,
                grader_result.dimension_scores,
                content_lane,
                _base,
            )
            if _gate is not None:
                return (False, _gate[0], _gate[1], _gate[2])
            # wA#3：concept 通过时把硬门回填的证据并回 grader_meta，落盘 PASS 带证据。
            for _ev in ("interpretation_entailment", "weighted_score", "weighted_threshold"):
                if _ev in _base:
                    grader_meta[_ev] = _base[_ev]
        _persist_grader_pass(grader_meta)
        meta = {
            "a_defect_count": 0, "a_defect_summary": [],
            "b_defect_count": 0, "b_defect_summary": [],
            **grader_meta,
            "repair_rounds": 0, "final_defects_summary": [],
        }
        return True, "", "", meta

    meta_b: dict[str, Any] = {
        "a_defect_count": len(a_defects),
        "a_defect_summary": _defect_summary(a_defects),
        "b_defect_count": len(b_defects),
        "b_defect_summary": _defect_summary(b_defects),
        **grader_meta,
    }

    # 修复循环(根因③:喂权威黄金样张作 repair 爬坡标杆)
    golden_text = _load_golden_sample(config)
    repair_result = run_repair_loop(
        candidate_text=candidate_text,
        a_defects=a_defects,
        b_defects=b_defects,
        failures_path=config.failures_path,
        golden_sample_text=golden_text,
        raw_text=raw_content,
        content_lane=content_lane,
        known_concepts=known_concepts,
        pending_queue=pending_queue,
    )

    repair_rounds = repair_result.get("rounds", 0)
    final_defects = repair_result.get("final_defects", [])
    final_grader_meta = repair_result.get("final_grader_meta") or {}
    effective_grader_meta = {
        **grader_meta,
        **{
            key: value
            for key, value in final_grader_meta.items()
            if value not in ("", None)
        },
    }
    meta_final: dict[str, Any] = {
        **meta_b,
        **effective_grader_meta,
        "repair_rounds": repair_rounds,
        "final_defects_summary": _defect_summary(final_defects),
        "repair_trace_paths": repair_result.get("trace_paths", []),
    }

    if not repair_result["converged"]:
        error_class = repair_result.get(
            "failure_signature", "distill_no_converge"
        )
        remaining = len(final_defects)
        return False, error_class, f"修复不收敛: {remaining} 项缺陷残留", meta_final

    # 问题2(Codex):repair 收敛后把 final_text 写回 candidate 文件
    # (否则 promote 段读修复前 candidate,C+ 真链路不成立)
    final_text = repair_result.get("final_text")
    if final_text and final_text != candidate_text:
        Path(candidate_path).write_text(final_text, encoding="utf-8")
    # step-3 codex P1 修复：repair 收敛后复查 concept 硬门
    # （poisoned-trace/低分不能靠"先有 defect 进 repair、repair 收敛"绕过——final grader_meta
    # 由 repair._grader_meta_from_result 携 interpretation_entailment + dimension_scores）。
    _gate = _check_concept_hard_gates(
        effective_grader_meta.get("interpretation_entailment"),
        effective_grader_meta.get("dimension_scores") or {},
        content_lane,
        meta_final,
    )
    if _gate is not None:
        return (False, _gate[0], _gate[1], _gate[2])
    # wA#3：repair 收敛通过时把硬门回填的证据并回 effective_grader_meta，落盘 PASS 带证据。
    for _ev in ("interpretation_entailment", "weighted_score", "weighted_threshold"):
        if _ev in meta_final:
            effective_grader_meta[_ev] = meta_final[_ev]
    _persist_grader_pass(effective_grader_meta)
    return True, "", "", meta_final


def _run_promote_segment(
    candidate_path: str,
    config: RunnerConfig,
    grader_result_path: Path | None = None,
) -> tuple[bool, str, str]:
    """段 7: 升格。

    grader_result_path 由段 5-6 收敛后落盘，传入 Gate-5 做独立复核。
    为 None 时（旧调用方）保持原行为（Gate-5 fail-closed）。

    Returns:
        (success, promotion_mode, detail)
    """
    from src.akb2.quality.promote import run_promote

    cpath = Path(candidate_path)
    vault_root = Path(config.vault_root)

    success, detail, snapshot_id = run_promote(
        candidate_path=cpath,
        vault_root=vault_root,
        grader_result_path=grader_result_path,
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

    existing_source = _existing_source_page_for_raw(raw_path, config)
    if existing_source is not None:
        details = {
            "reason": "duplicate_source",
            "raw_path": raw_path,
            "existing_source_path": str(existing_source),
            "slug": _derive_raw_slug(raw_path),
        }
        _log_event(config, "admission", "admission", content_hash, {
            "passed": False,
            **details,
        })
        _log_event(config, "abort", "admission", content_hash, details)
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "rejected",
            "error": f"准入未通过: duplicate_source: {existing_source}",
        }

    # Decision-2 / A3-8：装载 vault 概念索引（wiki_references → novelty；
    # known_concepts/pending_queue → 图谱 resolve）。loader 可 monkeypatch 注入（测试用）。
    vault_index = _load_vault_concept_index(
        config, exclude_slug=_derive_raw_slug(raw_path)
    )

    # Segment 2: 准入（喂 wiki_references；生产空/错索引 hold/reject）
    # 容错解包：mock 旧 3 元组 (passed, reason, checks) 仍兼容；真实返回 4 元组携 lane。
    admission_ret = _run_admission_segment(
        raw_path,
        raw_content,
        config,
        wiki_references=vault_index.get("wiki_references"),
        index_status=vault_index.get("status", LANE_INDEX_STATUS_OK),
        content_hash=content_hash,
    )
    passed, reason = admission_ret[0], admission_ret[1]
    content_lane = admission_ret[3] if len(admission_ret) > 3 else ""
    lane_reason = admission_ret[4] if len(admission_ret) > 4 else ""
    if not passed:
        _log_event(config, "abort", "admission", content_hash,
                   {"reason": reason, "content_lane": content_lane, "lane_reason": lane_reason})
        # SC-6：reason 已带"准入未通过:"前缀（admission.py 拼装），此处幂等不再重复加，
        # 否则 factory.json failure_reason 出现"准入未通过: 准入未通过: ..."双前缀。
        error = reason if str(reason).startswith("准入未通过") else f"准入未通过: {reason}"
        # P0-0：失败返回携机读诊断字段（calibration 据此区分真实失败阶段）。
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "rejected",
            "error": error,
            "content_lane": content_lane,
            "failed_node": "admission",
            "error_class": error.split(":", 1)[1].strip() if ":" in error else "admission",
        }

    _log_event(config, "admission", "admission", content_hash,
               {"passed": True, "content_lane": content_lane, "lane_reason": lane_reason})

    # Segment 3-4: 提炼
    _log_event(
        config,
        "distill_extract",
        "distill_extract",
        content_hash,
        {
            "status": "start",
            "model_family": "deepseek-v4-pro",
            "timeout_s": DISTILL_TIMEOUT_S,
            "max_retries": DISTILL_MAX_RETRIES,
            "content_lane": content_lane,
        },
    )
    success, candidate_path, error_detail, candidate_text, distill_meta = _run_distill_segment(
        raw_path,
        raw_content,
        config,
        content_lane=content_lane,
        known_concepts=vault_index.get("known_concepts"),
        pending_queue=vault_index.get("pending_queue"),
    )
    if not success:
        failed_node = distill_meta.get("failed_node", "distill_extract")
        details = _distill_abort_details(error_detail, failed_node)
        details["retries_used"] = distill_meta.get("retries_used", 0)
        details["maker_fallback_used"] = distill_meta.get("maker_fallback_used", False)
        if distill_meta.get("maker_primary_error"):
            details["maker_primary_error"] = distill_meta.get("maker_primary_error")
        # §0.2/D9：fallback 失败路径透传 fallback_quality_basis（不靠 error string）
        if distill_meta.get("fallback_quality_basis"):
            details["fallback_quality_basis"] = distill_meta.get("fallback_quality_basis")
        _log_event(config, "abort", failed_node, content_hash, details)
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
            # §0.2/D9：calibration 漏斗归因需 fallback_quality_basis（component/eligible edge 计数）
            "fallback_quality_basis": distill_meta.get("fallback_quality_basis"),
        "mechanism_rendering": distill_meta.get("mechanism_rendering"),
        }

    _log_event(config, "distill_extract", "distill_extract", content_hash,
               {"status": "success",
                "model_family": "deepseek-v4-pro",
               "timeout_s": DISTILL_TIMEOUT_S,
               "max_retries": DISTILL_MAX_RETRIES,
               "retries_used": distill_meta.get("retries_used", 0),
                "maker_fallback_used": distill_meta.get("maker_fallback_used", False),
                **(
                    {"maker_primary_error": distill_meta.get("maker_primary_error")}
                    if distill_meta.get("maker_primary_error")
                    else {}
                )})
    _log_event(config, "distill_compose", "distill_compose", content_hash,
               {"candidate_path": candidate_path})

    # Segment 5-6: 质量评估 + 修复
    # grader 结果落盘路径（按 candidate 命名），收敛时写入供段 7 Gate-5 复核
    grader_result_path = (
        Path(config.grader_result_dir) / f"{Path(candidate_path).stem}.grader.json"
    )
    converged, error_class, error_detail, quality_meta = _run_quality_segments(
        candidate_text, candidate_path, raw_content, config, grader_result_path,
        content_lane=content_lane,
        # step-3 SC-3b/SC-3h：把 distill 段评估的完整性地板结果喂评分段（early-return）。
        floor_result=distill_meta.get("floor_result"),
        # step-3 SC-3e/A3-8：runner 第二道 self-gate 真传 vault loader 的 known/pending。
        known_concepts=vault_index.get("known_concepts"),
        pending_queue=vault_index.get("pending_queue"),
    )
    # P2: rounds 回填真实 repair 轮数（修复原恒为 0 的报告 bug）
    rounds = quality_meta.get("repair_rounds", 0) if quality_meta else 0

    # P1: grade 事件携带缺陷详情（修复原 {} 空落账）
    _log_event(config, "grade_structural", "grade_structural", content_hash,
               {"defect_count": quality_meta.get("a_defect_count", 0),
                "defects": quality_meta.get("a_defect_summary", [])} if quality_meta else {})
    final_defects_summary = (
        quality_meta.get("final_defects_summary", []) if quality_meta else []
    )
    final_b_defects = final_defects_summary if not converged else []
    _log_event(config, "grade_llm", "grade_llm", content_hash,
               {"defect_count": len(final_b_defects),
                "defects": final_b_defects,
                "initial_defect_count": quality_meta.get("b_defect_count", 0),
                "initial_defects": quality_meta.get("b_defect_summary", []),
                "model_used": quality_meta.get("grader_model_used", ""),
                "primary_model": quality_meta.get("grader_primary_model", ""),
                "fallback_model": quality_meta.get("grader_fallback_model", ""),
                "suspended": quality_meta.get("grader_suspended", False),
                "fallback_used": quality_meta.get("grader_fallback_used", False),
                "primary_failed": quality_meta.get("grader_primary_failed", False),
                "primary_failure_reason": quality_meta.get(
                    "grader_primary_failure_reason", ""
                ),
                "fallback_failed": quality_meta.get("grader_fallback_failed", False),
                "fallback_failure_reason": quality_meta.get(
                    "grader_fallback_failure_reason", ""
                )} if quality_meta else {})

    if not converged:
        # step-3 SC-3b/SC-3h：完整性地板 fail 的 reject 走专用 abort 节点，
        # 日志显式带 floor_failed + weighted_score_not_computed + grader_not_called
        # （不可补偿硬证落账），final_status=rejected（区别于 repair 修不掉的 failed）。
        if error_class == "integrity_floor_failed" and quality_meta:
            _log_event(config, "abort", "integrity_floor", content_hash,
                       {"error_class": error_class, "error": error_detail,
                        "content_lane": quality_meta.get("content_lane", content_lane),
                        "floor_failed": quality_meta.get("floor_failed", []),
                        "weighted_score_not_computed": quality_meta.get(
                            "weighted_score_not_computed", True
                        ),
                        "grader_not_called": quality_meta.get("grader_not_called", True),
                        "floor_meta": quality_meta.get("floor_meta", {})})
            return {
                "converged": False,
                "rounds": rounds,
                "promoted": False,
                "promotion_mode": "none",
                "final_status": "rejected",
                "error": f"完整性地板未过（不可补偿）: {error_detail}",
                "content_lane": quality_meta.get("content_lane", content_lane),
                "failed_node": "integrity_floor",
                "error_class": error_class,
            }
        _log_event(config, "abort", "repair_loop", content_hash,
                   {"error_class": error_class, "error": error_detail,
                    "repair_rounds": rounds,
                    "final_defects": final_defects_summary,
                    "repair_trace_paths": (
                        quality_meta.get("repair_trace_paths", []) if quality_meta else []
                    ),
                })
        return {
            "converged": False,
            "rounds": rounds,
            "promoted": False,
            "promotion_mode": "none",
            "final_status": "failed",
            "error": f"质量不达标: {error_class} — {error_detail}",
            "content_lane": content_lane,
            "failed_node": "grader" if str(error_class).startswith("grader_") else "repair_loop",
            "error_class": error_class,
        }

    _log_event(config, "repair_loop", "repair_loop", content_hash,
               {"converged": True,
                "repair_rounds": rounds,
                "repair_trace_paths": (
                    quality_meta.get("repair_trace_paths", []) if quality_meta else []
                )})

    # Segment 7: 升格（传入段 5-6 落盘的 grader 结果供 Gate-5 复核）
    promoted, promotion_mode, promote_detail = _run_promote_segment(
        candidate_path, config, grader_result_path
    )

    if promoted:
        _log_event(config, "promote", "promote", content_hash,
                   {"mode": promotion_mode})
        _log_event(config, "finalize", "finalize", content_hash,
                   {"mode": promotion_mode})
    else:
        _log_event(config, "abort", "promote", content_hash,
                   {"reason": promote_detail})

    # §0.2/D4：promote 三分类——active_promoted / proposal_passed / promotion_blocked。
    # active_promoted: AUTO_PROMOTE=1 + 真写 active（real_link+high）；proposal_passed:
    # AUTO_PROMOTE=0 + proposal 口径过（含 proposed_node_only 灰区）；promotion_blocked:
    # quality 过但 Gate-1/4/5/CLI 非允许失败或 AUTO_PROMOTE=1 proposed_node_only 二次拒
    # （不计 positive pass，防 proposal_passed 虚高）。
    _auto_promote_env = os.environ.get("AKB2_AUTO_PROMOTE", "0")
    if promoted and _auto_promote_env == "1":
        _promotion_outcome = "active_promoted"
    elif promoted:
        _promotion_outcome = "proposal_passed"
    else:
        _promotion_outcome = "promotion_blocked"
    # §0.2/F4：final_status 从 promotion_outcome 派生（旧 blocked 时仍写 proposal_passed 矛盾）
    _final_status = {
        "active_promoted": "promoted",
        "proposal_passed": "proposal_passed",
        "promotion_blocked": "promotion_blocked",
    }.get(_promotion_outcome, "proposal_passed")

    return {
        "converged": True,
        "rounds": rounds,
        "promoted": promoted,
        "promotion_mode": promotion_mode,
        "final_status": _final_status,
        "promotion_outcome": _promotion_outcome,
        "error": None if promoted else promote_detail,
        "content_lane": content_lane,
        "failed_node": None,
        "error_class": None,
        # §0.2/D9：成功路径也透传 fallback_quality_basis（fallback 成功时为机读质量基线；
        # LLM 成功时为 None）。calibration 漏斗归因可见。
        "fallback_quality_basis": distill_meta.get("fallback_quality_basis"),
        # §0.2/F3：机制渲染信息透传（D12 hidden_mechanism_edges 真算）
        "mechanism_rendering": distill_meta.get("mechanism_rendering"),
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
            from src.akb2.quality.grader import run_grader_detailed
            grader_result = run_grader_detailed(_text)
        except Exception:
            results.append(False)
            continue

        # step-6: 拒收 degraded PASS — fallback_used/suspended 的空缺陷不可信，
        # 否则 gemini 兜底的空缺陷会被当 pass，抽检达标率虚高。
        if grader_result.suspended or grader_result.fallback_used:
            results.append(False)
        else:
            results.append(len(grader_result.defects) == 0)

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

        # 自动校准任务，无需人工介入 — 两计数保持 0（plan SC-6.1）
        manual_count = 0
        quality_count = 0

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
    parser.add_argument(
        "--staging-dir",
        help="staging 根目录（测试隔离用；显式传入优先于 vault.toml 的 get_staging_root）",
    )

    args = parser.parse_args(argv)

    # 解析 vault root。这里的 vault_root 语义是 AKB2.0 子根，不是 Obsidian 父根。
    explicit_root = args.vault_root or os.environ.get("AKB2_ROOT") or os.environ.get(
        "AKB2_VAULT_ROOT", ""
    )
    try:
        from src.akb2.loops.paths import get_akb2_root

        if explicit_root:
            if Path(explicit_root).expanduser().name == "AKB2.0":
                vault_root = str(Path(explicit_root).expanduser().resolve())
            else:
                vault_root = str(
                    Path(explicit_root)
                    .expanduser()
                    .joinpath("Praise Space", "AKB2.0")
                    .resolve()
                )
        else:
            vault_root = str(get_akb2_root())
    except Exception:
        print("ERROR: 无法解析 vault root。请设置 --vault-root 或 AKB2_ROOT",
              file=sys.stderr)
        return 1

    # 解析 raw dir
    raw_dir = args.raw_dir
    if not raw_dir:
        raw_dir = os.path.join(vault_root, "raw")

    # 解析 staging dir：显式 --staging-dir 优先；否则跟随已解析 vault_root，避免
    # raw/ledger/staging 分裂到不同机器路径。
    if args.staging_dir:
        staging_dir = args.staging_dir
    else:
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
