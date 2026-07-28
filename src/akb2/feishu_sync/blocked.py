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
}


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
