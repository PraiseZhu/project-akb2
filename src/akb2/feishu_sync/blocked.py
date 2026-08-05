"""读取上游链路的阻断/失败诊断，供告警文案引用真因。

背景（2026-07-28 事故）：社媒链路因上游 twitter-cli 全局故障连续两天被硬阻断，
诊断已正确落盘到 `logs/briefing-social-blocked.json`，但飞书告警只说
「ai_trends 缺失（期望路径: …）」—— 运维看到的是"文件没了"，看不到"为什么没"，
两天都没人能从告警定位到真因。本模块把已有诊断接进告警文案。

纯读：文件不存在 / 格式不对 / 日期不匹配 → 返回 None，绝不让告警本身失败。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import discover as discover_mod

# 仓库根：src/akb2/feishu_sync/blocked.py → parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]

# kind → 该链路可能写出的诊断文件（相对仓库根，按优先级排列）。
# social 用 logs/ 下的 briefing-social-*.json；github 链路的两个文件在工程根
# （历史形态，逐个登记进 .gitignore），此处只读不改它们的位置。
_DIAG_FILES: dict[str, tuple[str, ...]] = {
    "ai_trends": (
        "logs/briefing-social-blocked.json",
        "logs/briefing-social-failed.json",
        "logs/briefing-social-skipped.json",
    ),
    "github": (
        "briefing-failed.json",
        "briefing-skipped.json",
    ),
    # 视频链路（2026-07-28 事故：0 产出 + exit 0 + 无告警出口）。写入侧见
    # `akb2.video.blocked`；status=ok 时不写 `reason`，所以恢复后这里自然返回 None。
    "video": (
        "logs/video-blocked.json",
    ),
}


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def diagnostic_payload(kind: str, repo_root: Path | None = None) -> dict | None:
    root = repo_root or REPO_ROOT
    for rel in _DIAG_FILES.get(kind, ()):
        data = _load(root / rel)
        if data:
            return data
    return None


def blocking_reason(kind: str, date: str, repo_root: Path | None = None) -> str | None:
    """返回该 kind 在 date 当天被阻断/失败的一句话真因；无可用诊断则 None。

    只接受 `date` 与诊断里的日期一致的记录 —— 诊断文件是**覆盖写**的单份文件，
    不加日期校验会把昨天的原因贴到今天的告警上（比不说原因更糟）。
    github 链路的诊断没有 `date` 字段，退回用 `generated_at` 的日期前缀比对。
    """
    root = repo_root or REPO_ROOT
    for rel in _DIAG_FILES.get(kind, ()):
        data = _load(root / rel)
        if not data:
            continue
        stamp = str(data.get("date") or "")
        if not stamp:
            stamp = str(data.get("generated_at") or "")[:10]
        if stamp != date:
            continue
        reason = str(data.get("reason") or "").strip()
        if not reason:
            continue
        status = str(data.get("status") or "unknown").strip()
        detail = str(data.get("detail") or "").strip()
        text = f"{status}: {reason}"
        if detail:
            text = f"{text}（{detail}）"
        return text
    return None


def backstop_artifact_status(kind: str, date: str, vault_root: Path) -> str:
    path = discover_mod.expected_path(vault_root, date, kind)
    if path and path.exists():
        return f"已发现 {path.name}"
    if path:
        return f"未发现 {path.name}"
    return "不适用"


def next_step_command(kind: str, date: str, vault_root: Path) -> str | None:
    if kind != "github":
        return None
    product_exists = False
    path = discover_mod.expected_path(vault_root, date, kind)
    if path and path.exists():
        product_exists = True

    reconcile = (
        f"bash scripts/feishu_sync.sh --vault-root '{vault_root}' --date {date} "
        "--target group --reconcile-missing"
    )
    if product_exists:
        return reconcile
    return (
        f"bash scripts/github_briefing_backstop.sh --vault-root '{vault_root}'"
        f" && {reconcile}"
    )
