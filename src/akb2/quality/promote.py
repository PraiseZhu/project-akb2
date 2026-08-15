"""AKB2 Quality -- 五闸升格判定与收尾模块。

六闸自动升格判定(Gate-0 溯源 + 五闸;全过唯一终态=写 wiki/sources active,
双模式拆除后无 proposal 写路径)
+ 升格前快照与 rollback CLI + 升格收尾完整动作(frontmatter 翻转/索引摘要行/
概念回链/维护记录/lint 复验 0)。

Gate 判定复用现有 gate/lint CLI 不改其逻辑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent  # src/akb2/quality/
_AKB2_SRC = _MODULE_DIR.parent  # src/akb2/
_GATE_SCRIPT = _AKB2_SRC / "gate" / "akb2_gate_check.py"
_LINT_SCRIPT = _AKB2_SRC / "lint" / "akb2_lint.py"
_PROJECT_ROOT = _AKB2_SRC.parent.parent  # Project AKB2/

# 快照目录（工程 owned，非 vault）
_SNAPSHOTS_DIR = _PROJECT_ROOT / "snapshots"

# 预算常量（宪法 P4）
GATE_SUBPROCESS_TIMEOUT_SEC = 120
LINT_SUBPROCESS_TIMEOUT_SEC = 60
SNAPSHOT_MAX_COUNT = 50  # FR-015: 前 50 条快照


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class GateCheckResult:
    """单闸判定结果。"""
    passed: bool
    gate_name: str
    reason: str


# ---------------------------------------------------------------------------
# 闸判定核心
# ---------------------------------------------------------------------------


def _run_gate_check(
    candidate_path: Path, vault_root: Path
) -> tuple[bool, str, dict[str, Any]]:
    """Gate 1-3: subprocess 调 gate CLI 取 10/10 + decision_confidence + graph_readiness。

    Returns:
        (all_10_pass, detail_str, parsed_data)
        parsed_data 包含: proposed_gate_decision, decision_confidence, graph_readiness, hard_checks
    """
    if not _GATE_SCRIPT.exists():
        return False, f"gate CLI not found at {_GATE_SCRIPT}", {}

    candidate_path = candidate_path.expanduser().resolve()
    vault_root = vault_root.expanduser().resolve()

    # 确保 input 是相对路径（gate CLI 的 guard_input_path 会检查）
    try:
        rel_input = candidate_path.relative_to(vault_root)
    except ValueError:
        return False, f"candidate {candidate_path} not under vault_root {vault_root}", {}

    cmd = [
        sys.executable,
        str(_GATE_SCRIPT),
        "check",
        "--akb-root", str(vault_root),
        "--input", str(rel_input),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=GATE_SUBPROCESS_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired:
        return False, "gate CLI subprocess timed out", {}
    except OSError as exc:
        return False, f"gate CLI subprocess OS error: {exc}", {}

    stdout = result.stdout
    stderr = result.stderr

    if stderr.strip():
        # gate CLI 可能输出非致命 stderr（如 parse errors）
        pass

    # 解析 proposed_gate_decision
    decision_match = re.search(
        r"proposed_gate_decision:\s*(\S+)", stdout
    )
    proposed_decision = decision_match.group(1) if decision_match else ""

    # 解析 decision_confidence
    confidence_match = re.search(
        r"decision_confidence:\s*(\S+)", stdout
    )
    decision_confidence = confidence_match.group(1) if confidence_match else ""

    # 解析 graph_readiness
    graph_match = re.search(
        r"graph_readiness:\s*(\S+)", stdout
    )
    graph_readiness = graph_match.group(1) if graph_match else ""

    # 解析 hard_checks: 统计 pass 数量
    # gate CLI 输出中 hard_checks 不直接打印为结构化数据，
    # 但 proposed_gate_decision: active 意味着 9 项 critical 全过 + HC-10 总是 pass
    all_10_pass = (proposed_decision == "active")

    parsed = {
        "proposed_gate_decision": proposed_decision,
        "decision_confidence": decision_confidence,
        "graph_readiness": graph_readiness,
        "all_10_pass": all_10_pass,
    }

    detail = (
        f"gate CLI: proposed={proposed_decision}, "
        f"confidence={decision_confidence}, "
        f"graph={graph_readiness}, "
        f"10/10={'yes' if all_10_pass else 'no'}"
    )
    return all_10_pass, detail, parsed


def _run_lint_check(file_path: Path, vault_root: Path) -> tuple[bool, str]:
    """Gate 4: subprocess 调 lint CLI 取 0 findings（exit code 0 = no P0/P1）。"""
    if not _LINT_SCRIPT.exists():
        return False, f"lint CLI not found at {_LINT_SCRIPT}"

    cmd = [
        sys.executable,
        str(_LINT_SCRIPT),
        "--vault-root", str(vault_root),
        str(file_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=LINT_SUBPROCESS_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired:
        return False, "lint CLI subprocess timed out"
    except OSError as exc:
        return False, f"lint CLI subprocess OS error: {exc}"

    # exit code 0 = no P0/P1 findings
    if result.returncode == 0:
        findings_count = 0
        # 尝试从 stdout 提取 findings count
        count_match = re.search(r"(\d+)\s+findings?", result.stdout)
        if count_match:
            findings_count = int(count_match.group(1))
        return True, f"lint: 0 P0/P1 findings (total: {findings_count})"
    else:
        # 提取 findings 信息
        p0_match = re.search(r"P0:\s*(\d+)", result.stdout)
        p1_match = re.search(r"P1:\s*(\d+)", result.stdout)
        p0 = int(p0_match.group(1)) if p0_match else 0
        p1 = int(p1_match.group(1)) if p1_match else 0
        return False, f"lint: {p0} P0, {p1} P1 findings (exit code {result.returncode})"


def _read_grader_result(grader_result_path: Path) -> tuple[bool, str]:
    """Gate 5: 读 step-4 grader 结果文件断言 PASS。"""
    if not grader_result_path or not grader_result_path.exists():
        return False, f"grader result file not found: {grader_result_path}"

    try:
        data = json.loads(grader_result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"grader result file read error: {exc}"

    status = data.get("status", "")
    if status == "PASS":
        # fail-closed: degraded grader 的 PASS 拒收（fallback_used/suspended 不可作为通过条件）
        if data.get("fallback_used") or data.get("suspended"):
            return False, "degraded grader, fail-closed"
        dims = data.get("dimensions_checked", "?")
        return True, f"grader: PASS ({dims} dimensions checked, 0 findings)"
    else:
        findings = data.get("findings", [])
        return False, f"grader: {status} ({len(findings)} findings)"


def _check_source_provenance(candidate_path: Path) -> tuple[bool, str]:
    """F52 Gate-0: 候选页 frontmatter source_url 非空硬校验。

    只做存在性+非空判定（不校验 URL 格式——那是 lint 的事），
    拦截「元数据在上游丢失后带着空溯源升格」这一类事故。
    """
    try:
        text = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"candidate unreadable: {exc}"
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return False, "candidate has no frontmatter"
    url_match = re.search(r"^source_url:\s*(.*)$", m.group(1), re.M)
    if not url_match:
        return False, "source_url missing from candidate frontmatter"
    value = url_match.group(1).strip().strip("'\"")
    if not value:
        return False, "source_url is empty in candidate frontmatter"
    return True, f"source_url={value}"


def evaluate_five_gates(
    candidate_path: Path,
    vault_root: Path,
    grader_result_path: Path | None = None,
    allow_proposed_node_only: bool = False,
) -> tuple[bool, str, str]:
    """串行执行五闸判定。

    Gate 1: gate CLI 10/10 hard checks pass
    Gate 2: decision_confidence == "high"（§0.2/D8：allow_proposed_node_only 时接受
            "medium" + graph_readiness=="proposed_node_only"）
    Gate 3: graph_readiness == "real_link"（§0.2/D8：allow 时接受 "proposed_node_only"）
    Gate 4: lint CLI 0 P0/P1 findings
    Gate 5: grader result PASS

    §0.2/D8：allow_proposed_node_only 让冷启动 concept 候选（proposed_graph_nodes，
    无 real_link）过 Gate-2/3。[修宪 2026-07-06,用户裁决] 旧「开关=1 二次拒
    proposed_node_only 写 active」已废除——五闸全过即可 auto-active,graph_pending
    落账可见,缺失概念进 pending 队列由图谱兜底链路补全。

    Returns:
        (all_passed, failed_gate_name, reason)
        任一闸 fail 返回 (False, 闸名, 原因) 且候选留 staging。
    """
    # Gate 0 (F52): 溯源硬校验——source_url 空/缺失 → 拒升格。
    # 实锤事故: raw frontmatter YAML 写坏 → _read_raw_frontmatter 吞错 →
    # 候选以 source_url='' 一路过五闸写 active(zed/smashing 两篇),
    # wiki 页无法回链原文。五闸此前无任何一道检查溯源字段。
    prov_ok, prov_detail = _check_source_provenance(candidate_path)
    if not prov_ok:
        return False, "Gate-0 (source provenance)", prov_detail

    # Gate 1-3: gate CLI
    gate_ok, gate_detail, gate_parsed = _run_gate_check(candidate_path, vault_root)
    if not gate_ok:
        return False, "Gate-1 (10/10 hard checks)", gate_detail

    confidence = gate_parsed.get("decision_confidence", "")
    graph = gate_parsed.get("graph_readiness", "")
    is_proposed_node_only = graph == "proposed_node_only"

    # Gate 2: decision_confidence
    if confidence == "high":
        pass  # 高置信，过
    elif allow_proposed_node_only and confidence == "medium" and is_proposed_node_only:
        pass  # §0.2/D8：proposal 模式接受 medium + proposed_node_only
    else:
        return False, "Gate-2 (decision_confidence)", (
            f"decision_confidence={confidence}, expected 'high'"
            + (" (or 'medium'+proposed_node_only when allowed)" if allow_proposed_node_only else "")
            + f". {gate_detail}"
        )

    # Gate 3: graph_readiness
    if graph == "real_link":
        pass  # 真实图谱链接，过
    elif allow_proposed_node_only and is_proposed_node_only:
        pass  # §0.2/D8：proposal 接受 proposed_node_only（待 adjudication）
    else:
        return False, "Gate-3 (graph_readiness)", (
            f"graph_readiness={graph}, expected 'real_link'"
            + (" (or 'proposed_node_only' when allowed)" if allow_proposed_node_only else "")
            + f". {gate_detail}"
        )

    # Gate 4: lint
    lint_ok, lint_detail = _run_lint_check(candidate_path, vault_root)
    if not lint_ok:
        return False, "Gate-4 (lint 0 findings)", lint_detail

    # Gate 5: grader
    grader_ok, grader_detail = _read_grader_result(grader_result_path)
    if not grader_ok:
        return False, "Gate-5 (grader PASS)", grader_detail

    # §0.2/D8：proposed_node_only 过闸时标 graph_pending（run_promote 据此二次拒 active）
    pending_marker = " [graph_pending=true]" if is_proposed_node_only else ""
    return True, "ALL", (
        f"Five gates passed: {gate_detail}; {lint_detail}; {grader_detail}{pending_marker}"
    )


# ---------------------------------------------------------------------------
# 快照与回滚
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """计算文件 SHA-256 hex 摘要。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _generate_snapshot_id() -> str:
    """生成快照 ID: promote-<YYYYMMDD>T<HHMMSS>-<uuid8>"""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"promote-{ts}-{uid}"


def snapshot_before_promote(
    vault_root: Path,
    affected_paths: list[Path],
) -> str:
    """升格写入前快照全部将改文件。

    把 affected_paths 中每个文件复制到 snapshots/<snapshot_id>/ 下，
    保持相对 vault_root 的目录结构，并写回滚清单 manifest。

    Args:
        vault_root: vault 根目录。
        affected_paths: 将被修改的文件绝对路径列表。

    Returns:
        snapshot_id: 快照 ID 字符串。
    """
    snapshot_id = _generate_snapshot_id()
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict[str, str]] = {}
    created_files: list[str] = []
    for fpath in affected_paths:
        try:
            rel = fpath.relative_to(vault_root)
        except ValueError:
            # 文件不在 vault 下，跳过
            continue
        if not fpath.exists():
            # promote 前不存在 → 记为"将新建";回滚语义 = 删除该文件,
            # 恢复"promote 前不存在"的状态(F33 Bug-A:此前直接跳过,
            # 回滚后新建 active 页残留成孤儿)
            created_files.append(str(rel))
            continue

        dest = snapshot_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(fpath), str(dest))

        file_hash = _sha256_file(fpath)
        manifest[str(rel)] = {
            "sha256": file_hash,
            "size": fpath.stat().st_size,
        }

    # 写 manifest
    manifest_data = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(UTC).isoformat(),
        "vault_root": str(vault_root),
        "file_count": len(manifest),
        "files": manifest,
        "created_files": created_files,
    }
    manifest_path = snapshot_dir / "rollback-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 清理超出 SNAPSHOT_MAX_COUNT 的旧快照
    _prune_old_snapshots()

    return snapshot_id


def _prune_old_snapshots() -> None:
    """清理超出 SNAPSHOT_MAX_COUNT 的旧快照（按创建时间排序，保留最新）。"""
    if not _SNAPSHOTS_DIR.exists():
        return
    snapshots = sorted(
        [d for d in _SNAPSHOTS_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for old in snapshots[SNAPSHOT_MAX_COUNT:]:
        shutil.rmtree(str(old), ignore_errors=True)


def rollback_snapshot(snapshot_id: str) -> bool:
    """按 manifest 逐文件还原快照。

    Args:
        snapshot_id: 快照 ID。

    Returns:
        True 如果全部还原成功。
    """
    snapshot_dir = _SNAPSHOTS_DIR / snapshot_id
    if not snapshot_dir.exists():
        print(f"ERROR: snapshot {snapshot_id} not found at {snapshot_dir}", file=sys.stderr)
        return False

    manifest_path = snapshot_dir / "rollback-manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: manifest not found in snapshot {snapshot_id}", file=sys.stderr)
        return False

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: failed to read manifest: {exc}", file=sys.stderr)
        return False

    vault_root = Path(manifest.get("vault_root", ""))
    files = manifest.get("files", {})
    success = True

    for rel_path_str, info in files.items():
        target = vault_root / rel_path_str
        source = snapshot_dir / rel_path_str

        if not source.exists():
            print(f"WARN: snapshot source missing: {source}", file=sys.stderr)
            success = False
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))

        # 验证还原后 hash
        restored_hash = _sha256_file(target)
        expected_hash = info.get("sha256", "")
        if restored_hash != expected_hash:
            print(
                f"WARN: hash mismatch for {rel_path_str}: "
                f"expected={expected_hash[:16]}..., got={restored_hash[:16]}...",
                file=sys.stderr,
            )
            success = False

    # F33 Bug-A: promote 期间新建的文件,回滚 = 删除(旧 manifest 无此 key 时跳过)
    for rel_path_str in manifest.get("created_files", []):
        target = vault_root / rel_path_str
        if target.exists():
            try:
                target.unlink()
            except OSError as exc:
                print(
                    f"WARN: failed to remove created file {rel_path_str}: {exc}",
                    file=sys.stderr,
                )
                success = False

    if success:
        print(f"Rollback complete: {len(files)} file(s) restored from snapshot {snapshot_id}")
    else:
        print(f"Rollback completed with warnings for snapshot {snapshot_id}", file=sys.stderr)

    return success


# ---------------------------------------------------------------------------
# 升格收尾动作
# ---------------------------------------------------------------------------


# 机器 reviewer 身份（CONTENT-LIFECYCLE §6.1，用户 2026-06-13 预批常驻委托）
MACHINE_REVIEWER = "akb2-quality-loop-v1"


def _flip_frontmatter(text: str, slug: str) -> str:
    """翻转 frontmatter: status: active + gate_decision: active + 机器 reviewer 签名。

    同时去掉候选自评前缀（如 "候选自评: " 等 pattern）。
    机器 reviewer 签名（2026-06-17 修宪）：五闸全过经本函数升 active 时，
    必须写全 reviewer/reviewed_at（gate_reason 由 distill 候选阶段已写入），
    满足 CONTENT-LIFECYCLE §1.3 不变量 2 + §7 第 8 条。
    """
    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not fm_match:
        return text

    fm_text = fm_match.group(1)
    body = text[fm_match.end():]

    # 翻转 status
    fm_text = re.sub(r"^page_status:\s*.*$", "page_status: active", fm_text, flags=re.M)
    fm_text = re.sub(r"^status:\s*.*$", "status: active", fm_text, flags=re.M)

    # 翻转 gate_decision
    if re.search(r"^gate_decision:", fm_text, re.M):
        fm_text = re.sub(r"^gate_decision:\s*.*$", "gate_decision: active", fm_text, flags=re.M)
    else:
        fm_text += "\ngate_decision: active"

    # 添加 slug（如不存在）
    if not re.search(r"^slug:", fm_text, re.M):
        fm_text += f"\nslug: {slug}"

    # 机器 reviewer 签名（active 必带 reviewer + reviewed_at）
    reviewed_at = datetime.now(UTC).strftime("%Y-%m-%d")
    if re.search(r"^reviewer:", fm_text, re.M):
        fm_text = re.sub(r"^reviewer:\s*.*$", f"reviewer: {MACHINE_REVIEWER}", fm_text, flags=re.M)
    else:
        fm_text += f"\nreviewer: {MACHINE_REVIEWER}"
    if re.search(r"^reviewed_at:", fm_text, re.M):
        fm_text = re.sub(r"^reviewed_at:\s*.*$", f"reviewed_at: {reviewed_at}", fm_text, flags=re.M)
    else:
        fm_text += f"\nreviewed_at: {reviewed_at}"

    return f"---\n{fm_text}\n---\n{body}"


def _parse_proposed_graph_node_labels(fm_text: str) -> list[str]:
    """从 frontmatter 的 proposed_graph_nodes block 提取节点名(slug 优先,缺则 label)。

    [F33 Bug-B 方案①,用户裁决] auto_active(graph_pending)路径 related_concepts
    恒空 → R14 必炸 + pending 概念队列(修宪图谱兜底链路唯一进水口)永远断供。
    材料取自候选自身(Gate-3 proposed_node_only 保证该 block 非空),非编造。

    [F33-R reviewer CONCERN-1] slug 优先:distill `_slugify_component_id` 保证
    slug 为单 token 机器安全值,而多词 label(如 "Reinforcement Learning")用
    `\\S+` 捕获会截断;下游 `_slug_normalize_variants` 已做 `_↔-` 归一,slug 与
    hyphen label 等价。值捕获到行尾再剥引号,防任何形态截断。
    """
    block_match = re.search(r"^proposed_graph_nodes:\n((?:[ \t]+.*\n?)+)", fm_text, re.M)
    if not block_match:
        return []
    labels: list[str] = []
    for item in re.split(r"^[ \t]+-[ \t]", block_match.group(1), flags=re.M):
        if not item.strip():
            continue
        val = (re.search(r"^\s*slug:[ \t]*(.+?)[ \t]*$", item, re.M)
               or re.search(r"^\s*label:[ \t]*(.+?)[ \t]*$", item, re.M))
        if val:
            name = val.group(1).strip().strip('"').strip("'")
            if name and name not in labels:
                labels.append(name)
    return labels


def _inject_related_concepts(active_text: str, labels: list[str]) -> str:
    """active 文本的空/缺失 related_concepts 用 labels 回填(F33 Bug-B)。

    仅当现值为空 inline list 或字段缺失时写入;已有非空值(inline 或 block)
    一律不动。labels 为空时原样返回——留给 finalize lint 复验 R14 阻断,fail-closed。
    """
    if not labels:
        return active_text
    fm_match = re.match(r"^---\n(.*?)\n---\n", active_text, re.DOTALL)
    if not fm_match:
        return active_text
    fm_text = fm_match.group(1)
    body = active_text[fm_match.end():]
    inline = f"related_concepts: [{', '.join(labels)}]"
    if re.search(r"^related_concepts:\s*\[\s*\]\s*$", fm_text, re.M):
        fm_text = re.sub(r"^related_concepts:\s*\[\s*\]\s*$", inline, fm_text, flags=re.M)
    elif not re.search(r"^related_concepts:", fm_text, re.M):
        fm_text += f"\n{inline}"
    else:
        return active_text
    return f"---\n{fm_text}\n---\n{body}"


def _add_index_summary_line(
    index_path: Path,
    slug: str,
    source_label: str,
    date_str: str,
    core_content: str,
    concepts: list[str],
    basis: str,
) -> None:
    """在 00-Index.md 对应分组插入人读摘要行。

    格式: - [[<slug>]] — <source> · <date> · <core content> · serves <concepts> · basis: <basis>
    """
    content = index_path.read_text(encoding="utf-8")

    concepts_str = ", ".join(concepts) if concepts else "none"
    summary_line = (
        f"- [[{slug}]] — {source_label} · {date_str} · "
        f"{core_content} · serves {concepts_str} · basis: {basis}"
    )

    # 查找 §4 blog 分组下的当前月份或最近分组
    # 在最后一条条目行后插入
    lines = content.split("\n")
    insert_idx = None
    in_blog_section = False
    last_entry_line = -1

    for i, line in enumerate(lines):
        if line.startswith("## §4 blog"):
            in_blog_section = True
            continue
        if in_blog_section and line.startswith("## "):
            # 离开 blog section
            insert_idx = last_entry_line + 1 if last_entry_line >= 0 else i
            break
        if in_blog_section and line.startswith("- [["):
            last_entry_line = i

    if insert_idx is None:
        # 未找到插入点，追加到文件末尾
        insert_idx = len(lines)

    # 检查是否已存在同 slug 条目（去重）
    slug_pattern = f"- [[{slug}]]"
    for line in lines:
        if slug_pattern in line:
            return  # 已存在，跳过

    lines.insert(insert_idx, summary_line)
    index_path.write_text("\n".join(lines), encoding="utf-8")


def _add_index_revision_line(index_path: Path, slug: str, gate_result: str) -> None:
    """在 00-Index.md 修订历史表追加行。"""
    content = index_path.read_text(encoding="utf-8")
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    revision_line = f"| {date_str} | Added {slug} | gate: {gate_result} |"

    # 在修订历史表的最后一行后插入
    lines = content.split("\n")
    insert_idx = None
    in_revision = False
    last_table_row = -1

    for i, line in enumerate(lines):
        if line.startswith("## 修订历史"):
            in_revision = True
            continue
        if in_revision and line.startswith("## "):
            insert_idx = last_table_row + 1 if last_table_row >= 0 else i
            break
        if in_revision and line.startswith("| "):
            last_table_row = i

    if insert_idx is None:
        if last_table_row >= 0:
            insert_idx = last_table_row + 1
        else:
            insert_idx = len(lines)

    lines.insert(insert_idx, revision_line)
    index_path.write_text("\n".join(lines), encoding="utf-8")


def _add_concept_backlink(
    concept_path: Path,
    source_slug: str,
    relation: str,
    strength: str,
    evidence: str,
) -> None:
    """在概念页图谱关系表添加回边(target/relation/strength/evidence 四列)。

    概念不存在 → 不建 wikilink，落 pending 队列（黄金样张坑 5）。
    """
    if not concept_path.exists():
        return  # 由调用方处理 pending 队列

    content = concept_path.read_text(encoding="utf-8")

    # 查找图谱关系表
    backlink_row = f"| [[{source_slug}]] | {relation} | {strength} | {evidence} |"

    # 检查是否已存在同 slug 回链
    if f"[[{source_slug}]]" in content:
        return  # 已存在，跳过

    # 在图谱关系表的最后一行数据行后插入
    lines = content.split("\n")
    insert_idx = None
    in_graph_section = False
    last_table_row = -1

    for i, line in enumerate(lines):
        if line.startswith("## 图谱关系"):
            in_graph_section = True
            continue
        if in_graph_section and line.startswith("## "):
            insert_idx = last_table_row + 1 if last_table_row >= 0 else i
            break
        if in_graph_section and line.startswith("| "):
            last_table_row = i

    if insert_idx is None:
        if last_table_row >= 0:
            insert_idx = last_table_row + 1
        else:
            # 没有找到表，在 ## 图谱关系 后添加
            for i, line in enumerate(lines):
                if line.startswith("## 图谱关系"):
                    # 检查下一行是否是表头
                    if i + 1 < len(lines) and not lines[i + 1].startswith("|"):
                        # 需要插入表头
                        lines.insert(i + 1, "")
                        lines.insert(i + 2, "| 目标 | 关系 | 强度 | 证据 |")
                        lines.insert(i + 3, "|------|------|------|------|")
                        insert_idx = i + 4
                    else:
                        insert_idx = i + 1
                    break
            if insert_idx is None:
                insert_idx = len(lines)

    lines.insert(insert_idx, backlink_row)
    concept_path.write_text("\n".join(lines), encoding="utf-8")


def _add_maintenance_record(
    file_path: Path,
    action: str,
    detail: str = "",
) -> None:
    """在维护记录表追加一行。超 5 条先合并同日条目（黄金样张坑 4）。"""
    content = file_path.read_text(encoding="utf-8")
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    new_row = f"| {date_str} | {action} | {detail} |" if detail else f"| {date_str} | {action} |"

    # 查找维护记录表
    lines = content.split("\n")
    in_maintenance = False
    table_rows: list[tuple[int, str]] = []  # (line_idx, line_content)

    for i, line in enumerate(lines):
        if line.startswith("## 维护记录"):
            in_maintenance = True
            continue
        if in_maintenance and line.startswith("## "):
            break
        if in_maintenance and line.startswith("| "):
            table_rows.append((i, line))

    if not table_rows:
        # 没有维护记录表，创建一个
        for i, line in enumerate(lines):
            if line.startswith("## 维护记录"):
                lines.insert(i + 1, "")
                lines.insert(i + 2, "| 日期 | 操作 | 详情 |")
                lines.insert(i + 3, "|------|------|------|")
                lines.insert(i + 4, new_row)
                break
        file_path.write_text("\n".join(lines), encoding="utf-8")
        return

    # 检查是否超过 5 条：超了先合并已有同日重复条目腾出空间（黄金样张坑 4），再追加新行
    if len(table_rows) >= 5:
        # 按日期分组现有行（保持出现顺序）
        date_groups: dict[str, list[tuple[int, str]]] = {}
        date_order: list[str] = []
        for idx, row in table_rows:
            parts = row.split("|")
            row_date = parts[1].strip() if len(parts) >= 2 else ""
            if row_date not in date_groups:
                date_groups[row_date] = []
                date_order.append(row_date)
            date_groups[row_date].append((idx, row))

        # 找第一个有 >1 行的同日组并合并（保留首行，详情串联，标记 (merged)）
        for row_date in date_order:
            group = date_groups[row_date]
            if len(group) <= 1:
                continue
            first_idx, first_row = group[0]
            first_parts = first_row.split("|")
            first_action = first_parts[2].strip() if len(first_parts) >= 3 else ""
            detail_parts = []
            for _, row in group:
                parts = row.split("|")
                if len(parts) >= 4 and parts[3].strip():
                    detail_parts.append(parts[3].strip())
            merged_detail = "; ".join(detail_parts)
            lines[first_idx] = f"| {row_date} | {first_action} (merged) | {merged_detail} |"
            # 删除该组其余行（倒序 pop 防索引漂移）
            for idx, _ in reversed(group[1:]):
                lines.pop(idx)
            break

    # 追加新行：合并后重新定位维护记录表尾（索引可能已漂移）
    insert_idx = None
    in_maint = False
    for i, line in enumerate(lines):
        if line.startswith("## 维护记录"):
            in_maint = True
            continue
        if in_maint and line.startswith("## "):
            break
        if in_maint and line.startswith("| "):
            insert_idx = i + 1
    if insert_idx is None:
        insert_idx = len(lines)
    lines.insert(insert_idx, new_row)
    file_path.write_text("\n".join(lines), encoding="utf-8")


def _lint_reverify(
    touched_paths: list[Path],
    vault_root: Path,
) -> tuple[bool, str]:
    """全部触及文件 subprocess lint 复验。非 0 即整体失败。

    Returns:
        (all_clean, detail_str)
    """
    all_findings: list[str] = []
    for fpath in touched_paths:
        if not fpath.exists():
            continue
        # F34:vault 笔记 lint 只适用于 markdown 内容页;touched 里的机器文件
        # (pending 队列 concept-backlinks.jsonl 等)不进复验——Bug-B 打通供水后
        # 该文件首次进 touched,即被误 lint 判 P1 整体回滚(2026-07-08T20:10 账本)
        if fpath.suffix != ".md":
            continue
        ok, detail = _run_lint_check(fpath, vault_root)
        if not ok:
            all_findings.append(f"{fpath.name}: {detail}")

    if all_findings:
        return False, "lint re-verify failed: " + "; ".join(all_findings)
    return True, "lint re-verify: all files 0 P0/P1"


def finalize_promotion(
    candidate_path: Path,
    vault_root: Path,
    snapshot_id: str,
    source_label: str = "",
    core_content: str = "",
) -> tuple[bool, str, list[Path]]:
    """执行升格收尾动作（run_promote 六闸全过后调用）。

    顺序:
    1. frontmatter 翻转 → active 页
    2. 00-Index.md 插入摘要行 + 修订历史行
    3. 概念页回链
    4. staging 候选保留
    5. 维护记录追加
    6. 全部触及文件 lint 复验 0，非 0 即整体失败并自动回滚

    Returns:
        (success, detail, touched_paths)
    """
    # 读取候选
    candidate_text = candidate_path.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\n(.*?)\n---\n", candidate_text, re.DOTALL)
    if not fm_match:
        return False, "candidate has no frontmatter", []

    fm_text = fm_match.group(1)

    # 提取候选元数据
    slug = ""
    slug_match = re.search(r"^slug:\s*(\S+)", fm_text, re.M)
    if slug_match:
        slug = slug_match.group(1)
    else:
        slug = candidate_path.stem

    title = ""
    title_match = re.search(r"^title:\s*(.+)", fm_text, re.M)
    if title_match:
        title = title_match.group(1).strip().strip('"').strip("'")

    date_str = ""
    date_match = re.search(r"^date:\s*(\S+)", fm_text, re.M)
    if date_match:
        date_str = date_match.group(1)

    related_concepts: list[str] = []
    rc_match = re.search(r"^related_concepts:\s*\[(.*?)\]", fm_text, re.M)
    if rc_match:
        raw = rc_match.group(1)
        related_concepts = [
            c.strip().strip('"').strip("'")
            for c in raw.split(",")
            if c.strip()
        ]
    else:
        # 尝试 YAML block list
        rc_block = re.search(
            r"^related_concepts:\n((?:\s+-\s+.+\n?)+)", fm_text, re.M
        )
        if rc_block:
            related_concepts = re.findall(r"^\s+-\s+(.+)", rc_block.group(1), re.M)
            related_concepts = [c.strip().strip('"').strip("'") for c in related_concepts]

    # F33 Bug-B(方案①):related 为空时用候选自身 proposed_graph_nodes 回填,
    # 同时给 Step-3 pending 队列供水;仍为空则 R14 在 lint 复验阻断(fail-closed)
    if not related_concepts:
        related_concepts = _parse_proposed_graph_node_labels(fm_text)

    # 确定 source_label
    if not source_label:
        source_label = title or slug
    if not core_content:
        # 从 TLDR 提取
        body_after_fm = candidate_text[fm_match.end():]
        tldr_match = re.search(r"^## TLDR\n\n(.+)", body_after_fm, re.M)
        if tldr_match:
            core_content = tldr_match.group(1).strip()[:120]
        else:
            core_content = title or slug

    gate_result = "gate: active (five-gate auto-promote)"

    # Step 1: frontmatter 翻转 → active 页(+ F33 Bug-B related 回填)
    active_text = _flip_frontmatter(candidate_text, slug)
    active_text = _inject_related_concepts(active_text, related_concepts)
    active_path = vault_root / "wiki" / "sources" / f"{slug}.md"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(active_text, encoding="utf-8")

    touched: list[Path] = [active_path]

    # Step 2: 00-Index.md 更新
    index_path = vault_root / "wiki" / "sources" / "00-Index.md"
    if index_path.exists():
        _add_index_summary_line(
            index_path, slug, source_label, date_str or "unknown",
            core_content, related_concepts, "gate: active (five-gate auto-promote)"
        )
        _add_index_revision_line(index_path, slug, gate_result)
        touched.append(index_path)

    # Step 3: 概念页回链
    pending_concepts: list[str] = []
    for concept_slug in related_concepts:
        concept_path = vault_root / "wiki" / "concepts" / f"{concept_slug}.md"
        if concept_path.exists():
            # 确定 strength
            strength = "medium"  # 默认
            # 尝试从图谱关系表提取
            graph_match = re.search(
                rf"\| \[\[{re.escape(concept_slug)}\]\] \| \S+ \| (\S+) \|",
                candidate_text
            )
            if not graph_match:
                graph_match = re.search(
                    rf"\| {re.escape(concept_slug)} \| \S+ \| (\S+) \|",
                    candidate_text
                )
            if graph_match:
                strength = graph_match.group(1)

            evidence = f"Auto-promoted from {slug}"
            _add_concept_backlink(concept_path, slug, "source", strength, evidence)
            _add_maintenance_record(
                concept_path, f"Added backlink from {slug}",
                "gate: active (five-gate auto-promote)"
            )
            touched.append(concept_path)
        else:
            # 概念不存在 → 落 pending 队列（黄金样张坑 5）
            pending_concepts.append(concept_slug)

    # 写 pending 队列
    if pending_concepts:
        pending_path = vault_root / "staging" / "pending" / "concept-backlinks.jsonl"
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        with pending_path.open("a", encoding="utf-8") as f:
            for pc in pending_concepts:
                f.write(json.dumps({
                    "source_slug": slug,
                    "concept_slug": pc,
                    "ts": datetime.now(UTC).isoformat(),
                    "status": "pending",
                }, ensure_ascii=False) + "\n")
        touched.append(pending_path)

    # Step 4: staging 候选保留（不删）

    # Step 5: 维护记录追加
    _add_maintenance_record(
        active_path, "Promoted to active",
        f"gate: active (five-gate auto-promote); snapshot: {snapshot_id}"
    )
    # 也在候选页追加维护记录
    _add_maintenance_record(
        candidate_path, "Promoted to active (copy in wiki/sources/)",
        f"gate: active; snapshot: {snapshot_id}"
    )

    # Step 6: lint 复验
    lint_ok, lint_detail = _lint_reverify(touched, vault_root)
    if not lint_ok:
        # 自动回滚
        rollback_snapshot(snapshot_id)
        msg = (
            f"lint re-verify failed, rolled back to snapshot "
            f"{snapshot_id}: {lint_detail}"
        )
        return False, msg, touched

    return True, f"Promotion finalized: {len(touched)} file(s) touched, lint 0", touched


# ---------------------------------------------------------------------------
# 主入口（账本事件 + run_promote）
# ---------------------------------------------------------------------------


def _resolve_ledger_path(vault_root: Path, name: str) -> Path:
    """账本路径解析：与 runner 共用同一份（机器本地状态根，避开跨机同步）。

    2026-07-28：runner 的账本已移出 Syncthing 同步范围（`common/state_paths`），
    而本模块此前仍拼 `<vault>/staging/<name>` → 两份账本分裂。分裂的后果不是
    "少记一条"，而是 runner 的 `_is_processed` 读不到 promote 写的 finalize 事件
    → 判定"未处理" → 同一篇重复走完整管线并重复入库。

    `state_paths` 不可用时**回退旧路径并告警**（而不是静默失败丢账本）：
    账本写入失败会让幂等判定失效，必须可见。
    """
    legacy = vault_root / "staging" / name
    try:
        from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
    except ImportError:
        sys.stderr.write(
            f"WARNING [promote]: state_paths 不可用，账本回退旧路径 {legacy}"
            "（与 runner 可能不是同一份，幂等判定有风险）\n"
        )
        return legacy
    return Path(resolve_state_file(name, subdir=LEDGER_SUBDIR, legacy_path=legacy))


def _write_ledger_event(
    vault_root: Path,
    event_type: str,
    node: str,
    content_hash: str = "",
    details: dict | None = None,
) -> bool:
    """落账事件到 quality-ledger.jsonl。"""
    try:
        from src.akb2.loops.ledger import append_event
    except ImportError:
        return False

    # 必须与 runner 读的是同一份账本（runner.py:3078 走 resolve_state_file）。
    # 2026-07-28 事故风险：promote 写 vault 旧账本、runner 读机器本地新账本 →
    # finalize 事件 runner 看不到 → `_is_processed` 判"未处理" → 同一篇重复入库。
    ledger_path = _resolve_ledger_path(vault_root, "quality-ledger.jsonl")
    event = {
        "loop_id": "loop1",
        "node": node,
        "event": event_type,
        "content_hash": content_hash,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "details": details or {},
    }
    return append_event(str(ledger_path), event)


def run_promote(
    candidate_path: Path,
    vault_root: Path,
    grader_result_path: Path | None = None,
) -> tuple[bool, str, str | None]:
    """主入口：六闸判定（含 Gate-0 溯源），全过唯一终态 = 写 active。

    [P2 重构] 双模式环境开关已拆除：不再有 proposal 写路径，
    六闸全过即写 wiki/sources active；旧开关环境变量（无论 0/1/任意值/未设置）
    对本函数零影响（p2-drop-dual-mode / p2-env-zero-mutation）。
    runner 只消费本函数返回值。

    Args:
        candidate_path: 候选页绝对路径。
        vault_root: vault 根目录。
        grader_result_path: step-4 grader 结果文件路径。

    Returns:
        (success, detail, snapshot_id)
        snapshot_id 仅在自动升格成功时非 None。
    """
    candidate_hash = _sha256_file(candidate_path) if candidate_path.exists() else ""

    # 六闸判定（§0.2/D8：allow_proposed_node_only 让冷启动 concept 过 Gate-2/3）
    gates_ok, failed_gate, gates_detail = evaluate_five_gates(
        candidate_path, vault_root, grader_result_path,
        allow_proposed_node_only=True,
    )

    if not gates_ok:
        # 落账 failure 事件
        _write_ledger_event(
            vault_root, "abort", "promote", candidate_hash,
            {"failed_gate": failed_gate, "reason": gates_detail}
        )
        return False, f"Five gates failed at {failed_gate}: {gates_detail}", None

    # [修宪 2026-07-06,用户裁决「那就改宪法 我要自动化」] §0.2/D8 旧二次拒废除:
    # graph_readiness=proposed_node_only(graph_pending)**允许**写 active——冷启动
    # 死锁(wiki 空 → 无 real_link 可指 → 第一篇永远进不来)由此打破。
    # 质量底线不变:候选仍须六闸全过(gate 10/10 + lint 0 + grader PASS);缺失概念
    # 落 pending 队列,图谱由 D8 兜底链路(pending 共指累积 → 图谱校准补边)补全。
    # fail-visible:graph_pending 状态随 gates_detail 落账,不静默(p2-keep-graph-pending)。
    if "[graph_pending=true]" in gates_detail:
        _write_ledger_event(
            vault_root, "promote", "promote", candidate_hash,
            {
                "mode": "auto_active_graph_pending",
                "note": "修宪 2026-07-06:proposed_node_only 允许 auto-active,"
                        "概念落 pending 队列由图谱兜底链路补全",
                "gates_detail": gates_detail,
            }
        )

    # 确定受影响文件列表
    slug = candidate_path.stem
    fm_match = re.match(
        r"^---\n(.*?)\n---\n", candidate_path.read_text(encoding="utf-8"), re.DOTALL
    )
    related_concepts: list[str] = []
    if fm_match:
        fm_text = fm_match.group(1)
        rc_match = re.search(r"^related_concepts:\s*\[(.*?)\]", fm_text, re.M)
        if rc_match:
            related_concepts = [
                c.strip().strip('"').strip("'")
                for c in rc_match.group(1).split(",")
                if c.strip()
            ]
        # F33 Bug-B:与 finalize 同口径的 effective 列表,保证若回填概念
        # 恰好已存在对应页,也会被快照覆盖(回滚完整性)
        if not related_concepts:
            related_concepts = _parse_proposed_graph_node_labels(fm_text)

    affected = [candidate_path]
    active_target = vault_root / "wiki" / "sources" / f"{slug}.md"
    affected.append(active_target)
    index_path = vault_root / "wiki" / "sources" / "00-Index.md"
    if index_path.exists():
        affected.append(index_path)
    # F34:pending 队列文件也进快照——finalize Step-3 会追加行,失败回滚须
    # 连同还原(既存则 restore,不存在则 created_files 删除,Bug-A 机制兜住),
    # 否则失败轮次的 stale 供水残留(2026-07-08T20:10 实测残留 2 行)
    affected.append(vault_root / "staging" / "pending" / "concept-backlinks.jsonl")
    for cs in related_concepts:
        cp = vault_root / "wiki" / "concepts" / f"{cs}.md"
        if cp.exists():
            affected.append(cp)

    # Step.3: 快照
    snapshot_id = snapshot_before_promote(vault_root, affected)

    # 落账 promote 事件
    _write_ledger_event(
        vault_root, "promote", "promote", candidate_hash,
        {
            "mode": "active",
            "snapshot_id": snapshot_id,
            "gates_detail": gates_detail,
        }
    )

    # Step.4: 收尾
    success, detail, touched = finalize_promotion(
        candidate_path, vault_root, snapshot_id
    )

    if success:
        _write_ledger_event(
            vault_root, "finalize", "finalize", candidate_hash,
            {
                "mode": "active",
                "snapshot_id": snapshot_id,
                "files_touched": len(touched),
            }
        )
        return True, f"Auto-promote success: {detail}", snapshot_id
    else:
        _write_ledger_event(
            vault_root, "abort", "finalize", candidate_hash,
            {
                "mode": "active",
                "snapshot_id": snapshot_id,
                "reason": detail,
            }
        )
        return False, f"Auto-promote failed: {detail}", snapshot_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_rollback(snapshot_id: str) -> int:
    """--rollback 子命令：按 manifest 逐文件还原。"""
    success = rollback_snapshot(snapshot_id)
    return 0 if success else 1


def _cmd_promote(args: argparse.Namespace) -> int:
    """promote 子命令：执行五闸判定 + 升格/提议。"""
    candidate_path = Path(args.candidate).resolve()
    vault_root = Path(args.vault_root).resolve() if args.vault_root else None

    if vault_root is None:
        print("ERROR: --vault-root is required", file=sys.stderr)
        return 2

    if not candidate_path.exists():
        print(f"ERROR: candidate not found: {candidate_path}", file=sys.stderr)
        return 2

    grader_path = Path(args.grader_result).resolve() if args.grader_result else None

    success, detail, snapshot_id = run_promote(candidate_path, vault_root, grader_path)

    print(f"{'PASS' if success else 'FAIL'}: {detail}")
    if snapshot_id:
        print(f"snapshot_id: {snapshot_id}")

    return 0 if success else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AKB2 Quality — Five-Gate Promote CLI"
    )
    sub = parser.add_subparsers(dest="cmd")

    # promote 子命令
    prom = sub.add_parser("promote", help="Run five-gate check and promote")
    prom.add_argument("--candidate", required=True, help="Path to candidate .md file")
    prom.add_argument("--vault-root", required=True, help="AKB2.0 vault root directory")
    prom.add_argument("--grader-result", help="Path to step-4 grader result JSON")
    prom.set_defaults(func=_cmd_promote)

    # rollback 子命令
    roll = sub.add_parser("rollback", help="Rollback a promotion from snapshot")
    roll.add_argument("snapshot_id", help="Snapshot ID to rollback")
    roll.set_defaults(func=lambda a: _cmd_rollback(a.snapshot_id))

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
