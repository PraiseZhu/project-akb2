#!/usr/bin/env python3
"""
AKB 2.0 Phase C-3 Gate 自动检查 — L3 提议层
=============================================

只读 staging/candidate/ 的候选页,按 INTAKE-GATE §4 7 项硬指标
+ LINT-RULES GATE-* / GRAPH-* / SOURCE-* 域 + H7+H8 闭环规则
做静态判定,输出 L3 manifest 提议(不创建 active,不改 candidate)。

严格 L3 提议层:
  - 不创建 wiki/toolbox active 内容
  - 不修改 candidate / wiki / toolbox / 00-Index.md
  - 不调用 LLM
  - 不接旧 fetch / synth / translate / NLM / cron
  - L4 提升必须由 reviewer 在独立授权 plan 中执行

依赖:
  - Python 3.9+
  - PyYAML >=6.0(frontmatter parser)

承接标准:
  - AKB-2.0-CONTENT-STANDARD.md (root)
  - docs/AKB-2.0-INTAKE-GATE.md §4 7 项硬指标 + §3 6 gate_decision
  - docs/AKB-2.0-LINT-RULES.md GATE-* / GRAPH-* / SOURCE-* 域
  - docs/AKB-2.0-AUTOMATION-BOUNDARY.md §5.4 lock + §6.2 manifest 路径 + §6.3 11 字段最小集 + §11.6 自动索引契约
  - docs/AKB-2.0-PAGE-TEMPLATES.md §3 frontmatter + §4.0 graph_role 默认 + §4.2 source key_findings 双轨

详见同目录 README.md。
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "ERROR: PyYAML not installed. Run: pip install pyyaml>=6.0\n"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "akb-gate-proposal-v1"
MANIFEST_TYPE = "ingest"  # 承接 AUTOMATION-BOUNDARY §6.2 8 类
PROPOSAL_LEVEL = "L3"
ACTOR = "akb_gate_check"

# INTAKE-GATE §3 6 gate_decision 枚举
GATE_DECISION_ENUM = {
    "active",
    "needs_repair",
    "needs_human_review",
    "brief_only",
    "archive",
    "reject",
}

# CONTENT-LIFECYCLE §2 9 page_status 枚举
PAGE_STATUS_ENUM = {
    "candidate",
    "needs_repair",
    "needs_human_review",
    "active",
    "brief_only",
    "stale",
    "deprecated",
    "archived",
    "rejected",
}

# SOURCE-TYPE-POLICY 12 source_type 枚举
SOURCE_TYPE_ENUM = {
    "paper", "docs", "course", "research",
    "blog", "video", "design", "work-note",
    "tool", "forum", "reddit", "news",
}

# PAGE-TEMPLATES §4 13 active 模板 (query-result added for knowledge compounding)
TEMPLATE_TYPE_ENUM = {
    "concept", "source", "entity", "synthesis",
    "project", "design", "tool-understanding", "tool-card",
    "workflow", "prompt", "checklist", "agent-procedure",
    "query-result",
}

# GRAPH-LINKING §6.1 7 graph_role(H8 闭环真实枚举)
GRAPH_ROLE_ENUM = {
    "hub", "cluster_center", "bridge", "leaf",
    "evidence", "procedure", "design-memory",
}

# GRAPH-LINKING §6.2 3 graph_status
GRAPH_STATUS_ENUM = {"healthy", "orphan_risk", "hub_overload"}

# 12 related_* 字段(H2 闭环 + GRAPH-LINKING §9.3)
RELATED_FIELDS = [
    "related_concepts", "related_sources", "related_entities",
    "related_projects", "related_designs", "related_tools",
    "related_synthesis", "related_workflows", "related_prompts",
    "related_checklists", "related_agent_procedures",
    "related_to",
]

# 不变量保护:扫描后比对的关键路径(0 mtime 变化要求)
PROTECTED_PATHS = ["wiki", "toolbox", "staging/candidate", "00-Index.md"]

# CLI 退出码
EXIT_OK = 0
EXIT_ARG_ERROR = 1
EXIT_GUARD_VIOLATION = 2  # 越权写入风险 / manifest 写失败
EXIT_LIVE_LOCK = 3  # 活锁拒绝


# ---------------------------------------------------------------------------
# Frontmatter parser(PyYAML — 微调 1)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str, list[str]]:
    """解析 markdown frontmatter。

    返回 (frontmatter_dict, body_text, parse_errors)。
    PyYAML 严格 parser 失败时 fallback 到宽容 line-based parser
    (容忍中文特殊字符未引号包裹的 list items / inline values)。
    """
    errors: list[str] = []
    m = _FRONTMATTER_RE.match(text)
    if not m:
        errors.append("no_frontmatter_block")
        return {}, text, errors
    yaml_text = m.group(1)
    body = text[m.end():]
    # 主 parser:PyYAML safe_load
    try:
        data = yaml.safe_load(yaml_text)
        if isinstance(data, dict):
            return data, body, errors
        errors.append("frontmatter_not_dict (yaml strict)")
    except yaml.YAMLError as exc:
        errors.append(f"yaml_parse_error: {str(exc)[:200]}")
    # Fallback:宽容 line-based parser(只提取关键字段)
    fallback_data, fallback_errs = _fallback_parse(yaml_text)
    errors.extend(fallback_errs)
    if fallback_data:
        errors.append("used_fallback_parser")
        return fallback_data, body, errors
    return {}, body, errors


def _fallback_parse(yaml_text: str) -> tuple[dict, list[str]]:
    """宽容 line-based frontmatter parser。

    支持:
    - scalar `key: value`
    - inline list `key: [a, b, c]` 或 `key: []`
    - block list:
        key:
          - item 1
          - item 2(任意 unicode + 特殊字符,不需引号)
    - 嵌套 dict(简单 1 层,如 origin: { ... })

    跳过:
    - 多行 string `>` `|`
    - 复杂嵌套
    """
    errs: list[str] = []
    data: dict[str, Any] = {}
    lines = yaml_text.split("\n")
    i = 0
    current_list_key: str | None = None
    current_list: list[str] | None = None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        # block list item(2 spaces + "-")
        if line.startswith("  - ") or line.startswith("- "):
            if current_list is not None:
                item = line.lstrip()[2:].strip()
                # 去掉首尾 quote
                if (item.startswith('"') and item.endswith('"')) or \
                   (item.startswith("'") and item.endswith("'")):
                    item = item[1:-1]
                current_list.append(item)
            i += 1
            continue
        # scalar / inline list / block list head
        if ":" in line and not line.startswith(" "):
            # 提交上一个 list
            if current_list_key is not None and current_list is not None:
                data[current_list_key] = current_list
                current_list_key = None
                current_list = None
            key, sep, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if not rest:
                # block list head 或 block dict head
                # peek 下一非空行
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and (lines[j].startswith("  - ") or lines[j].startswith("- ")):
                    current_list_key = key
                    current_list = []
                else:
                    # 空 scalar 或 dict head;跳过 dict block(简化)
                    data[key] = None
            elif rest.startswith("[") and rest.endswith("]"):
                # inline list
                inner = rest[1:-1].strip()
                if not inner:
                    data[key] = []
                else:
                    items = [x.strip().strip('"').strip("'") for x in inner.split(",")]
                    data[key] = items
            else:
                # scalar
                # 去 quote
                v = rest
                if (v.startswith('"') and v.endswith('"')) or \
                   (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                # 类型推断
                if v == "null" or v == "~":
                    data[key] = None
                elif v == "true":
                    data[key] = True
                elif v == "false":
                    data[key] = False
                else:
                    data[key] = v
        i += 1
    # 提交最后一个 list
    if current_list_key is not None and current_list is not None:
        data[current_list_key] = current_list
    return data, errs


# ---------------------------------------------------------------------------
# 路径守卫
# ---------------------------------------------------------------------------


def guard_input_path(akb_root: Path, input_path: Path) -> None:
    """只允许扫描 staging/candidate/ 下文件。"""
    try:
        rel = input_path.resolve().relative_to(akb_root.resolve())
    except ValueError:
        raise SystemExit(
            f"input path '{input_path}' is not under akb_root '{akb_root}'"
        )
    parts = rel.parts
    if len(parts) < 2 or parts[0] != "staging" or parts[1] != "candidate":
        raise SystemExit(
            f"input path must be under staging/candidate/, got '{rel}'"
        )


def collect_candidates(input_path: Path) -> list[Path]:
    """收集要扫描的 .md 文件列表(支持单文件 / 目录递归)。"""
    if input_path.is_file():
        return [input_path] if input_path.suffix == ".md" else []
    files: list[Path] = []
    for root, _dirs, names in os.walk(input_path):
        for name in names:
            if name.endswith(".md"):
                files.append(Path(root) / name)
    return sorted(files)


# ---------------------------------------------------------------------------
# 越权写入护栏 — 扫描前后 mtime 比对
# ---------------------------------------------------------------------------


def snapshot_protected(akb_root: Path) -> dict[str, float]:
    """在扫描 / 写入前后 snapshot wiki/toolbox/staging/candidate/00-Index.md mtime。"""
    snapshot: dict[str, float] = {}
    for rel in ["wiki", "toolbox", "staging/candidate"]:
        p = akb_root / rel
        if not p.exists():
            continue
        for root, _dirs, names in os.walk(p):
            for name in names:
                if name.endswith(".md"):
                    fp = Path(root) / name
                    try:
                        snapshot[str(fp)] = fp.stat().st_mtime
                    except OSError:
                        pass
    # 顶层 00-Index.md(各目录的)
    for rel in ["wiki", "toolbox"]:
        idx = akb_root / rel / "00-Index.md"
        if idx.exists():
            try:
                snapshot[str(idx)] = idx.stat().st_mtime
            except OSError:
                pass
    return snapshot


def diff_protected(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """对比 mtime。返回被改动 / 新增 / 删除的路径列表。"""
    changes: list[str] = []
    for path, mtime in before.items():
        if path not in after:
            changes.append(f"deleted: {path}")
        elif after[path] != mtime:
            changes.append(f"modified: {path}")
    for path in after:
        if path not in before:
            changes.append(f"created: {path}")
    return changes


# ---------------------------------------------------------------------------
# Lock(承接 AUTOMATION-BOUNDARY §5.4 — 微调 4)
# ---------------------------------------------------------------------------


def acquire_lock(akb_root: Path, operation_id: str) -> Path:
    """获取 .akb-runtime/akb-write.lock。

    成功获取 → 写 lock 文件(包含 PID + heartbeat)+ 返回 Path
    失败时 sys.exit(非 0):
    - PID 活跃 → 退出 3(活锁;不写不报错)
    - Stale lock(holder 死)→ 退出 3(报告但不自动删除;reviewer 手动检查后再跑)

    承接 AUTOMATION-BOUNDARY §5.4(stale 检测不依赖纯 mtime + §9.2 失败必须落 manifest 原则:
    本工具未持锁就退出,不写 manifest,manifest writer 永不在未持锁状态调用)。
    """
    runtime_dir = akb_root / ".akb-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_dir / "akb-write.lock"
    if lock_path.exists():
        try:
            existing = json.loads(lock_path.read_text())
            holder_pid = existing.get("pid")
        except (OSError, json.JSONDecodeError):
            existing = None
            holder_pid = None
        if holder_pid and is_process_alive(holder_pid):
            sys.stderr.write(
                f"ERROR: live lock held by active PID {holder_pid}; aborting (exit 3).\n"
                f"  lock: {lock_path}\n"
                f"  holder: {existing}\n"
                f"  Wait for holder to release, or check holder process.\n"
            )
            sys.exit(EXIT_LIVE_LOCK)
        # Stale lock — 不自动删除(承接 §5.4),但也不允许跳过 lock 写 manifest
        sys.stderr.write(
            f"ERROR: stale lock detected at {lock_path} (holder PID {holder_pid} not alive); "
            "aborting (exit 3) to preserve single-writer invariant.\n"
            f"  holder: {existing}\n"
            "  Reporting but not auto-deleting (承接 AUTOMATION-BOUNDARY §5.4).\n"
            "  Action: manually inspect and remove the lock file if confirmed stale, "
            "then re-run.\n"
        )
        sys.exit(EXIT_LIVE_LOCK)
    lock_data = {
        "pid": os.getpid(),
        "operation_id": operation_id,
        "actor": ACTOR,
        "started_at": _dt.datetime.now().isoformat(),
        "heartbeat_at": _dt.datetime.now().isoformat(),
    }
    lock_path.write_text(json.dumps(lock_data, indent=2))
    return lock_path


def release_lock(lock_path: Path, our_pid: int) -> None:
    """释放 lock。承接 AUTOMATION-BOUNDARY §5.4:不删新持有者 lock(检查 PID 匹配)。"""
    if not lock_path.exists():
        return
    try:
        existing = json.loads(lock_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if existing.get("pid") != our_pid:
        sys.stderr.write(
            f"WARN: lock at {lock_path} held by different PID "
            f"{existing.get('pid')} (we are {our_pid}); not removing.\n"
        )
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def is_process_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but owned by another user — treat as alive (defensive)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 10 条硬检查
# ---------------------------------------------------------------------------


def check_candidate_only(rel_path: str) -> tuple[bool, str]:
    """HC-01 candidate-only:文件必须在 staging/candidate/ 下。"""
    if rel_path.startswith("staging/candidate/"):
        return True, "in staging/candidate/"
    return False, f"not under staging/candidate/: {rel_path}"


def check_page_status(fm: dict) -> tuple[bool, str]:
    """HC-02 page_status:必须 candidate(承接 §13 命名空间分离)。

    容忍旧 `status: candidate`(H6 backlog)— 标 P3 但不 fail。
    """
    if fm.get("page_status") == "candidate":
        return True, "page_status=candidate"
    if fm.get("status") == "candidate":
        return True, "uses legacy `status: candidate` (H6 backlog — P3)"
    return False, "page_status != candidate"


def check_source_type(fm: dict) -> tuple[bool, str]:
    """HC-03 source_type:必须在 SOURCE-TYPE-POLICY 12 枚举内。

    Phase B sample-validation 是临时类(允许)。
    """
    st = fm.get("source_type")
    if st is None:
        return False, "missing source_type"
    if st in SOURCE_TYPE_ENUM:
        return True, f"source_type={st}"
    if st == "phase-b-sample-validation":
        return True, f"source_type={st} (Phase B sample candidate)"
    return False, f"source_type={st} not in 12 enum"


def check_template_type(fm: dict) -> tuple[bool, str]:
    """HC-04 template_type:必须在 PAGE-TEMPLATES 12 active 枚举内。"""
    tt = fm.get("template_type") or fm.get("doc_type")
    if tt is None:
        return False, "missing template_type/doc_type"
    if tt in TEMPLATE_TYPE_ENUM:
        return True, f"template_type={tt}"
    return False, f"template_type={tt} not in 12 active enum"


def check_evidence(fm: dict) -> tuple[bool, str]:
    """HC-05 evidence:active 必填 ≥1 条 evidence_paths / evidence / source_refs。"""
    for key in ("evidence_paths", "evidence", "source_refs"):
        v = fm.get(key)
        if isinstance(v, list) and len(v) >= 1:
            return True, f"{key} has {len(v)} item(s)"
    return False, "missing evidence_paths/evidence/source_refs (≥1)"


def check_future_questions(fm: dict) -> tuple[bool, str]:
    """HC-06 future_questions:active 必填 2-3 条具体问题(INTAKE-GATE §4 #3)。"""
    fq = fm.get("future_questions")
    if not isinstance(fq, list):
        return False, "missing or non-list future_questions"
    n = len(fq)
    if n < 2:
        return False, f"future_questions has {n} item(s); need 2-3"
    if n > 3:
        return False, f"future_questions has {n} item(s); need 2-3"
    return True, f"future_questions has {n} item(s)"


def check_graph_link(fm: dict) -> tuple[bool, str]:
    """HC-07 graph link:至少 1 个 related_* 非空 OR proposed_graph_nodes 非空(Phase B candidate readiness)。

    query-result 模板: derived_from 非空列表可替代 related_* (知识复利溯源链)。
    L4 时必须 ≥1 真实 related_* 非空(承接 sample-register proposed_graph_nodes 边界)。
    """
    if fm.get("template_type") == "query-result":
        df = fm.get("derived_from")
        if isinstance(df, list) and len(df) > 0:
            return True, f"query-result: derived_from has {len(df)} source(s)"
    for f in RELATED_FIELDS:
        v = fm.get(f)
        if isinstance(v, list) and len(v) > 0:
            return True, f"{f} has {len(v)} link(s)"
    pgn = fm.get("proposed_graph_nodes")
    if isinstance(pgn, list) and len(pgn) > 0:
        return True, (
            f"proposed_graph_nodes has {len(pgn)} item(s) "
            "(L3 OK; L4 must materialize_graph_link)"
        )
    return False, "no related_* link AND no proposed_graph_nodes"


def check_body_structure(body: str, template_type: str | None) -> tuple[bool, str]:
    """HC-08 正文结构:必须有 ## TLDR(或等价)。

    Source 模板加 H7 双轨规则:if frontmatter has key_findings then 必须有 `## 关键发现`
    """
    headings = re.findall(r"^##\s+([^\n]+)", body, re.M)
    has_tldr = any(re.match(r"(TLDR|TL;DR|核心摘要|总览)", h.strip(), re.I) for h in headings)
    if not has_tldr:
        return False, "missing `## TLDR` or equivalent heading"
    if template_type == "source":
        # H7 双轨:正文 `## 关键发现` 章节是 frontmatter `key_findings` 的展开
        has_kf_section = any("关键发现" in h or "key_findings" in h.lower() for h in headings)
        if not has_kf_section:
            return False, "source template missing `## 关键发现` section (H7 双轨)"
    return True, f"body has {len(headings)} H2 sections"


def check_no_auto_active(fm: dict) -> tuple[bool, str]:
    """HC-09 no-auto-active:gate_decision 必须 null / 不 active(candidate frontmatter 不预填 active)。

    INTAKE-GATE 不变量 3:没有自动 active。
    """
    gd = fm.get("gate_decision")
    if gd is None or gd == "null":
        return True, "gate_decision is null (correct for candidate)"
    if gd == "active":
        return False, "gate_decision: active in candidate frontmatter (forbidden)"
    return True, f"gate_decision={gd} (non-active, OK)"


def check_toolbox_handoff(rel_path: str, fm: dict) -> tuple[bool, str]:
    """HC-10 toolbox-handoff:工具类 candidate 应转交 TOOL-INTAKE-GATE。"""
    template_type = fm.get("template_type") or fm.get("doc_type") or ""
    if template_type in ("tool-card", "tool-understanding"):
        return True, f"toolbox candidate ({template_type}) — handoff to TOOL-INTAKE-GATE"
    return True, "not a toolbox candidate"


HARD_CHECKS = [
    ("HC-01-CANDIDATE-ONLY", check_candidate_only),
    ("HC-02-PAGE-STATUS", check_page_status),
    ("HC-03-SOURCE-TYPE", check_source_type),
    ("HC-04-TEMPLATE-TYPE", check_template_type),
    ("HC-05-EVIDENCE", check_evidence),
    ("HC-06-FUTURE-QUESTIONS", check_future_questions),
    ("HC-07-GRAPH-LINK", check_graph_link),
    ("HC-08-BODY-STRUCTURE", check_body_structure),
    ("HC-09-NO-AUTO-ACTIVE", check_no_auto_active),
    ("HC-10-TOOLBOX-HANDOFF", check_toolbox_handoff),
]


def run_hard_checks(rel_path: str, fm: dict, body: str) -> list[dict]:
    """跑全部 10 条硬检查,返回 [{rule, pass, detail}, ...]

    query-result 模板跳过 HC-03 (source_type) 和 HC-05 (evidence)。
    """
    is_query_result = fm.get("template_type") == "query-result"
    results: list[dict] = []
    for rule_id, check_fn in HARD_CHECKS:
        if is_query_result and rule_id in ("HC-03-SOURCE-TYPE", "HC-05-EVIDENCE"):
            results.append({"rule": rule_id, "pass": True, "detail": "skipped for query-result"})
            continue
        # 不同检查需要的参数不同 — dispatch by signature
        if rule_id == "HC-01-CANDIDATE-ONLY":
            ok, detail = check_fn(rel_path)
        elif rule_id == "HC-08-BODY-STRUCTURE":
            ok, detail = check_fn(body, fm.get("template_type") or fm.get("doc_type"))
        elif rule_id == "HC-10-TOOLBOX-HANDOFF":
            ok, detail = check_fn(rel_path, fm)
        else:
            ok, detail = check_fn(fm)
        results.append({"rule": rule_id, "pass": bool(ok), "detail": detail})
    return results


# ---------------------------------------------------------------------------
# 6 决策矩阵
# ---------------------------------------------------------------------------


def derive_proposal(
    hard_checks: list[dict],
    fm: dict,
    rel_path: str,
) -> dict:
    """根据硬检查 + frontmatter 推导 proposed_gate_decision + repair_actions。"""
    failed = [c for c in hard_checks if not c["pass"]]
    failed_rules = [c["rule"] for c in failed]

    template_type = fm.get("template_type") or fm.get("doc_type") or ""
    source_type = fm.get("source_type") or ""

    # toolbox handoff 优先(HC-10 已 pass 但 template_type 标识工具)— 转交,不在本 gate 裁决
    if template_type in ("tool-card", "tool-understanding"):
        return {
            "proposed_gate_decision": "needs_human_review",
            "decision_confidence": "medium",
            "gate_reason_proposal": (
                f"toolbox candidate (template_type={template_type}); "
                "handoff to TOOL-INTAKE-GATE — out of scope for AKB GATE"
            ),
            "repair_actions": ["transfer_to_toolbox_intake_gate"],
            "graph_readiness": _graph_readiness(fm),
            "l4_requirements": _l4_requirements(fm),
        }

    # forum / reddit + 旧 page_status 等高风险 source_type 默认 needs_human_review
    if source_type in ("forum", "reddit"):
        return {
            "proposed_gate_decision": "needs_human_review",
            "decision_confidence": "low",
            "gate_reason_proposal": (
                f"source_type={source_type} requires strict_gate "
                "(SOURCE-TYPE-POLICY); reviewer must verify high-value + traceable"
            ),
            "repair_actions": ["reviewer_verify_high_value", "reviewer_verify_traceability"],
            "graph_readiness": _graph_readiness(fm),
            "l4_requirements": _l4_requirements(fm),
        }

    # 全过 7 项硬指标(等价 INTAKE-GATE §4)→ active
    # HC-01 / HC-02 / HC-03 / HC-04 / HC-05 / HC-06 / HC-07 / HC-08 / HC-09 全过 → active
    critical = {
        "HC-01-CANDIDATE-ONLY", "HC-02-PAGE-STATUS",
        "HC-03-SOURCE-TYPE", "HC-04-TEMPLATE-TYPE",
        "HC-05-EVIDENCE", "HC-06-FUTURE-QUESTIONS",
        "HC-07-GRAPH-LINK", "HC-08-BODY-STRUCTURE",
        "HC-09-NO-AUTO-ACTIVE",
    }
    failed_critical = [r for r in failed_rules if r in critical]

    if not failed_critical:
        # 检查 graph_readiness — 若全是 proposed_graph_nodes,L4 必须 materialize
        graph_readiness = _graph_readiness(fm)
        return {
            "proposed_gate_decision": "active",
            "decision_confidence": "medium" if graph_readiness == "proposed_node_only" else "high",
            "gate_reason_proposal": (
                "all 9 critical hard checks pass; "
                "INTAKE-GATE §4 7 项硬指标 satisfied via static analysis; "
                "reviewer must materialize gate_reason on L4 promotion"
            ),
            "repair_actions": [],
            "graph_readiness": graph_readiness,
            "l4_requirements": _l4_requirements(fm),
        }

    # 缺关键字段但有价值 → needs_repair
    return {
        "proposed_gate_decision": "needs_repair",
        "decision_confidence": "medium",
        "gate_reason_proposal": (
            f"failed critical hard checks: {failed_critical}; "
            "candidate has structure but missing required fields"
        ),
        "repair_actions": [
            f"fix:{r}" for r in failed_critical
        ],
        "graph_readiness": _graph_readiness(fm),
        "l4_requirements": _l4_requirements(fm),
    }


def _graph_readiness(fm: dict) -> str:
    """判断 graph readiness:real_link / proposed_node_only / none"""
    has_real_link = False
    for f in RELATED_FIELDS:
        v = fm.get(f)
        if isinstance(v, list) and len(v) > 0:
            has_real_link = True
            break
    if has_real_link:
        return "real_link"
    pgn = fm.get("proposed_graph_nodes")
    if isinstance(pgn, list) and len(pgn) > 0:
        return "proposed_node_only"
    return "none"


def _l4_requirements(fm: dict) -> list[str]:
    """L4 提升需要补的事项(本工具不做 L4,只列要求)。"""
    reqs: list[str] = []
    if _graph_readiness(fm) == "proposed_node_only":
        reqs.append("materialize_graph_link")
    return reqs


# ---------------------------------------------------------------------------
# Manifest 写入(微调 5:双层 schema)
# ---------------------------------------------------------------------------


def build_manifest_record(
    candidate_path: Path,
    akb_root: Path,
    fm: dict,
    body: str,
    proposal: dict,
    hard_checks: list[dict],
    operation_id: str,
) -> dict:
    """构建 manifest JSONL 一行(一条 candidate)。

    满足两层:
    - 全局 AUTOMATION-BOUNDARY §6.3 manifest 11 字段最小集
    - C-3 子 schema akb-gate-proposal-v1 专属字段
    """
    rel_candidate = str(candidate_path.relative_to(akb_root))
    now = _dt.datetime.now().isoformat()
    record = {
        # === C-3 子 schema (akb-gate-proposal-v1) ===
        "schema_version": SCHEMA_VERSION,
        "proposal_level": PROPOSAL_LEVEL,
        "operation": "gate_check_proposal",
        "candidate_path": rel_candidate,
        "source_type": fm.get("source_type"),
        "template_type": fm.get("template_type") or fm.get("doc_type"),
        "page_status": fm.get("page_status") or fm.get("status"),
        "proposed_gate_decision": proposal["proposed_gate_decision"],
        "proposed_lifecycle_status": _proposed_lifecycle_status(
            proposal["proposed_gate_decision"]
        ),
        "decision_confidence": proposal["decision_confidence"],
        "hard_checks": hard_checks,
        "gate_reason_proposal": proposal["gate_reason_proposal"],
        "proposed_gate_reason": proposal["gate_reason_proposal"],  # alias
        "repair_actions": proposal["repair_actions"],
        "graph_readiness": proposal.get("graph_readiness"),
        "l4_requirements": proposal.get("l4_requirements", []),
        "reviewer_required": True,
        "forbidden_active_write_guard": True,
        "not_active_promotion": True,
        "outcome": "proposed",
        # === 全局 AUTOMATION-BOUNDARY §6.3 11 字段最小集 ===
        "manifest_type": MANIFEST_TYPE,
        "operation_id": operation_id,
        "actor": ACTOR,
        "started_at": now,
        "finished_at": now,
        "source": "akb_gate_check L3 proposal scan",
        "reason": (
            f"L3 static gate check on {rel_candidate}; "
            "reviewer must approve L4 in separate plan"
        ),
        "affected_files": [rel_candidate],
        "decision_refs": [],  # L3 不引用 reviewer decision (那是 L4 工作)
        "rollback": "rolled_back: false (read-only L3 proposal; no state changed)",
        # 兼容字段
        "created_at": now,
    }
    return record


def _proposed_lifecycle_status(gate_decision: str) -> str:
    """gate_decision → page_status mapping(承接 LIFECYCLE §3)。"""
    mapping = {
        "active": "active",
        "needs_repair": "needs_repair",
        "needs_human_review": "needs_human_review",
        "brief_only": "brief_only",
        "archive": "archived",
        "reject": "rejected",
    }
    return mapping.get(gate_decision, "candidate")


def write_manifest(
    akb_root: Path,
    records: list[dict],
    date_str: str,
) -> Path:
    """追加写 manifests/ingest/<YYYY-MM-DD>.jsonl。"""
    manifest_dir = akb_root / "manifests" / "ingest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{date_str}.jsonl"
    with manifest_path.open("a") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    return manifest_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    akb_root = Path(args.akb_root).resolve()
    if not akb_root.is_dir():
        sys.stderr.write(f"ERROR: akb-root not a dir: {akb_root}\n")
        return EXIT_ARG_ERROR

    input_path = (akb_root / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input).resolve()
    guard_input_path(akb_root, input_path)

    candidates = collect_candidates(input_path)
    if not candidates:
        sys.stderr.write(f"ERROR: no .md files found under {input_path}\n")
        return EXIT_ARG_ERROR

    operation_id = f"akb-gate-check-{uuid.uuid4().hex[:12]}"
    date_str = args.date or _dt.date.today().isoformat()
    our_pid = os.getpid()

    # 越权护栏 — 扫描前 snapshot
    snapshot_before = snapshot_protected(akb_root)

    # 跑全部 candidate
    records: list[dict] = []
    summary: dict[str, int] = {d: 0 for d in GATE_DECISION_ENUM}
    parse_errors: list[tuple[str, list[str]]] = []
    for cand in candidates:
        try:
            text = cand.read_text(encoding="utf-8")
        except OSError as exc:
            parse_errors.append((str(cand), [f"read_error: {exc}"]))
            continue
        fm, body, errs = parse_frontmatter(text)
        if errs:
            parse_errors.append((str(cand.relative_to(akb_root)), errs))
            # 仍尝试 hard checks(可能部分能跑)
        rel_path = str(cand.relative_to(akb_root))
        hard_checks = run_hard_checks(rel_path, fm, body)
        proposal = derive_proposal(hard_checks, fm, rel_path)
        record = build_manifest_record(
            cand, akb_root, fm, body, proposal, hard_checks, operation_id
        )
        records.append(record)
        summary[proposal["proposed_gate_decision"]] += 1

    # 越权护栏 — 扫描后 snapshot 比对
    snapshot_after = snapshot_protected(akb_root)
    diff = diff_protected(snapshot_before, snapshot_after)
    if diff:
        sys.stderr.write(
            "ERROR: protected paths modified during scan (this is a bug):\n"
        )
        for d in diff:
            sys.stderr.write(f"  {d}\n")
        return EXIT_GUARD_VIOLATION

    # 输出 summary
    print(f"# AKB Gate Check L3 Proposal — {date_str}")
    print(f"operation_id: {operation_id}")
    print(f"candidates_scanned: {len(records)}")
    print(f"parse_errors: {len(parse_errors)}")
    print()
    print("## proposed_gate_decision distribution")
    for d in ("active", "needs_repair", "needs_human_review", "brief_only", "archive", "reject"):
        print(f"  {d}: {summary[d]}")
    print()
    if parse_errors:
        print("## parse errors")
        for path, errs in parse_errors:
            print(f"  {path}: {errs}")
        print()
    print("## per-candidate proposals")
    for r in records:
        print(f"  {r['candidate_path']}")
        print(f"    proposed_gate_decision: {r['proposed_gate_decision']}")
        print(f"    decision_confidence: {r['decision_confidence']}")
        print(f"    graph_readiness: {r['graph_readiness']}")
        if r["repair_actions"]:
            print(f"    repair_actions: {r['repair_actions']}")
    print()

    # Manifest 写入(双 flag 必需)
    if args.write_manifest:
        if not args.allow_l3_manifest_write:
            sys.stderr.write(
                "ERROR: --write-manifest requires --allow-l3-manifest-write "
                "(double-flag authorization)\n"
            )
            return EXIT_ARG_ERROR
        # 越权护栏 — 写入前再 snapshot
        pre_write_snapshot = snapshot_protected(akb_root)
        # 获取 lock
        lock_path = acquire_lock(akb_root, operation_id)
        try:
            manifest_path = write_manifest(akb_root, records, date_str)
            # 越权护栏 — 写入后 snapshot 比对(应只动 manifests/,不动 wiki/toolbox/staging/)
            post_write_snapshot = snapshot_protected(akb_root)
            write_diff = diff_protected(pre_write_snapshot, post_write_snapshot)
            if write_diff:
                sys.stderr.write(
                    "ERROR: protected paths modified during manifest write:\n"
                )
                for d in write_diff:
                    sys.stderr.write(f"  {d}\n")
                return EXIT_GUARD_VIOLATION
            print("## manifest written")
            print(f"  path: {manifest_path}")
            print(f"  records: {len(records)}")
        finally:
            release_lock(lock_path, our_pid)

    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AKB 2.0 Phase C-3 Gate Check (L3 proposal layer)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    chk = sub.add_parser("check", help="Run L3 gate check on candidates")
    chk.add_argument("--akb-root", required=True, help="AKB 2.0 root directory")
    chk.add_argument("--input", required=True, help="Path under staging/candidate/")
    chk.add_argument("--date", help="Manifest date (YYYY-MM-DD); default today")
    chk.add_argument("--dry-run", action="store_true", default=True,
                     help="Default; print summary only")
    chk.add_argument("--write-manifest", action="store_true",
                     help="Write manifests/ingest/<DATE>.jsonl (requires --allow-l3-manifest-write)")
    chk.add_argument("--allow-l3-manifest-write", action="store_true",
                     help="Confirm L3 manifest write authorization (paired with --write-manifest)")
    chk.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
