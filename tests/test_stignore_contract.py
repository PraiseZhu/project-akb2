"""`.stignore` 的契约测试（R1 审查 F6 / F18 / M5）。

两条不变量：

1. **告警的唯一归因素材不得留在跨机同步范围内**（SC-R1-36）
   `.stignore` 头部把 `briefing-failed.json` / `briefing-skipped.json` 点名为
   「飞书告警**唯一**的归因素材来源」「最后两条尤其要命」，却一个模式都没写 ——
   为了保护告警真相来源而加的文件，恰恰没保护它。它们写在**仓库根**，既不在 `logs`
   也不在 `_tmp` 下，上面的目录模式一条都盖不到；`*.sync-conflict-*` 只阻止**已产生的**
   冲突副本继续繁殖，不阻止 live 文件再次分叉。

2. **交付口径必须与自身生效前提一致**（SC-R1-37）
   PR body 与 commit message 把 `.stignore` 平铺直叙成已交付项，而文件自陈「只在该目录
   就是 Syncthing folder 根时被读取」，且本仓多处注释早已断定 folder 根是 `~/AI-Agent`
   —— 按其自身判据不生效。不生效的交付物必须登记为残留，不能算已交付。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STIGNORE = REPO / ".stignore"
RESIDUALS = REPO / "docs" / "allowed-residuals.md"

# 告警读取侧按固定文件名读的两个诊断文件（feishu_sync/blocked.py）
ALERT_DIAGNOSTIC_FILES = ("briefing-failed.json", "briefing-skipped.json")


def _patterns() -> list[str]:
    """`.stignore` 的**有效模式行**（去注释与空行）。"""
    lines: list[str] = []
    for raw in STIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        lines.append(line)
    assert lines, ".stignore 没有任何有效模式 —— 断言失去意义"
    return lines


def test_alert_diagnostics_excluded_from_sync() -> None:
    """两个告警诊断文件必须各有一条**自己的**模式，且反向锚点不破。"""
    patterns = _patterns()
    for name in ALERT_DIAGNOSTIC_FILES:
        assert name in patterns, (
            f"{name} 没有 .stignore 模式 —— 告警唯一的归因素材仍在跨机同步范围内。"
            f"当前模式: {patterns}"
        )

    # 反向锚点①：`*.sync-conflict-*` 仍在（它管的是另一件事，不能被顺手删掉）
    assert "*.sync-conflict-*" in patterns, "冲突副本模式被删了"
    # 反向锚点②：既有的关键模式一条都不能少（防"加两行顺手改坏别的"）
    for anchor in (".git", ".git/**", "*.sqlite", "logs", "_tmp", ".cindy-worktrees"):
        assert anchor in patterns, f"既有模式 {anchor} 丢了"
    # 反向锚点③：不得误伤 `.json` 全类（config/*.json 等仍要同步）
    assert "*.json" not in patterns, "把全部 json 排除了 —— 过宽"


def test_precondition_and_residual_recorded() -> None:
    """生效前提写在文件头部 + 残留已在 allowed-residuals.md 登记。"""
    head = STIGNORE.read_text(encoding="utf-8")
    # 前提必须写成**已实测的结论**，不是"待 owner 核对"
    assert ".stfolder" in head, ".stignore 头部没写 folder 根的实测证据"
    assert "folder 根" in head, ".stignore 头部没说明生效前提"
    assert "不读它" in head or "不生效" in head, (
        ".stignore 头部没如实说明「本文件当前不生效」"
    )
    assert "allowed-residuals.md" in head, ".stignore 头部没指向残留登记"

    residuals = RESIDUALS.read_text(encoding="utf-8")
    assert ".stignore" in residuals, "残留台账里没有 .stignore 这一条"
    stignore_section = residuals[residuals.index("`.stignore` 入仓") :]
    section = stignore_section[: stignore_section.find("\n## ")]
    for required in ("folder 根", "后续动作", "~/AI-Agent"):
        assert required in section, f"残留条目缺 {required}：\n{section[:500]}"


def test_backstop_slot_residual_recorded() -> None:
    """SC-R1-14：「模板已改 11:00、运行态待重装」必须登记，不能算已交付。

    `AKB2_AUTO_PROMOTE 0→1` 是「模板追平运行态」（改完即一致），
    `12:05→11:00` 是「模板领先运行态」（改完**不**一致）—— 两者性质相反，
    不能并列成同一类"已确认的配置变更"。
    """
    residuals = RESIDUALS.read_text(encoding="utf-8")
    assert "backstop 第二时段 11:00" in residuals, "两个 backstop 的待重装残留没登记"
    section = residuals[residuals.index("backstop 第二时段 11:00") :]
    section = section[: section.find("\n## ")]
    assert "com.akb2.briefing.backstop" in section, section[:400]
    assert "com.akb2.github_briefing.backstop" in section, section[:400]
    assert "installed_stale" in section, "残留条目没说明 parity 会报的方向词"
    assert "manual_existing_runtime" in section, "残留条目没说明为什么没脚本能装"


def test_stignore_has_no_device_identifiers() -> None:
    """S6：Syncthing 设备标识与 pid 不该留在仓库文件里（个人基础设施标识）。

    排查价值用"两台设备"表述即可保留，不必留标识。
    """
    text = STIGNORE.read_text(encoding="utf-8")
    # Syncthing 设备 ID 前缀形态：7 位大写字母/数字**且同时含字母与数字**
    #（纯字母的 token 如 PROJECT 是普通英文词，不是设备标识）
    device_ids = [
        token
        for token in re.findall(r"(?<![A-Z0-9])[A-Z0-9]{7}(?![A-Z0-9])", text)
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
    ]
    assert not device_ids, f".stignore 里出现 Syncthing 设备标识: {sorted(set(device_ids))}"
    assert not re.search(r"\bpid\s*\d{3,}", text), ".stignore 里写死了本机 pid"
