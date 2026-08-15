"""sc-register-verify-discrimination 验收：verify 闸门的鉴别力（红得准、绿得真）。

被测对象：scripts/verify_akb2_retrieval_registration.py（sc-register-mcp 的机器闸门）。

packet SC 的脚本化 4 步（核心用例 end-to-end 跑完）：
    备份 → 移除 mcpServers.akb2-retrieval → 跑 verify 必须红，且红集恰为
    sc-register-mcp 一条（exit 1 = 前置失败，stderr 点名本键未注册，
    不掺杂 ①②③ 段的误伤标记）→ 还原 → 再跑必须绿（三段标记齐全）
    且配置与备份 sha256 字节级相等；全程其它键逐字未变
    （红态剥本键后 deep-equal + 终态 sha256 相等两级断言）。

鉴别力补充用例（证明红/绿都不是空转——闸门能区分不同失坏形态）：
    ①段 env.PYTHONPATH 篡改      → exit 2（红因 = 权威源不一致）
    ②段 live 与 post 快照脱钩     → exit 3（红因 = 其它键被改动/快照陈旧）
    ③段条目缺 vault 根 env        → exit 4（红因 = 真实握手失败，lifespan fail-closed）

全部在 tmp fixture 完成（--config/--backup-dir 指向 tmp_path），不触碰真实
~/.claude.json；零 skip——mcp 不可导入时握手失败直接红，不降级跳过。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_akb2_retrieval_registration.py"
SERVER_KEY = "akb2-retrieval"
AUTHORITY_REL = Path("toolbox/cc-akb2-retrieval/mcp_server/server.py")

# verify 脚本的退出码契约（与被测脚本具名常量同值；此处按黑盒口径重复声明，
# 被测脚本改码时本文件的红/绿判定应随之下调，形成跨文件锚定）
EXIT_OK = 0
EXIT_PRECONDITION = 1
EXIT_AUTHORITY = 2
EXIT_OTHER_KEYS = 3
EXIT_HANDSHAKE = 4

# 环境里的 vault 根变量在所有 verify 子进程调用前剥掉：
# ③段的红因判定（条目缺 vault env → 握手失败）不允许被父进程泄漏的
# AKB2_ROOT 假绿，PYTHONPATH 同理（条目 overlay 才是唯一来源）。
SCRUB_ENV_KEYS = ("AKB2_ROOT", "AKB2_VAULT_ROOT", "VAULT_ROOT", "PYTHONPATH")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_server_key(config: dict[str, Any]) -> dict[str, Any]:
    """深拷贝并剥掉 mcpServers.akb2-retrieval（与被测脚本同口径的『其它键』视角）。"""
    stripped = json.loads(json.dumps(config))
    servers = stripped.get("mcpServers")
    if isinstance(servers, dict):
        servers.pop(SERVER_KEY, None)
    return stripped


def build_registered_config(vault_root: Path) -> dict[str, Any]:
    """构造三段全绿的自洽注册态（克隆真实注册形态，路径全部指向本仓/tmp）。

    - args[0] 指向本仓权威源（①段 sha 内容锚定）
    - env.PYTHONPATH 指向本仓 src/（①段逐字口径）
    - command 与 fixture 内 akb-search 条目逐字相等（①段克隆注册模式判定）
    - env.AKB2_ROOT 指向 tmp vault（③段 lifespan fail-closed 解析用）
    """
    return {
        "userID": "u-disc",
        "numStartups": 7,
        "projects": {"/tmp/x": {"allowedTools": ["Read"]}},
        "mcpServers": {
            "akb-search": {"command": sys.executable, "args": ["-y", "akb-search"]},
            SERVER_KEY: {
                "command": sys.executable,
                "args": [str(REPO_ROOT / AUTHORITY_REL)],
                "env": {
                    "PYTHONPATH": str(REPO_ROOT / "src"),
                    "AKB2_ROOT": str(vault_root),
                },
            },
        },
    }


def write_green_state(
    cfg: Path, backup_dir: Path, doc: dict[str, Any]
) -> None:
    """落盘注册态 + 配套 pre/post 快照（②段自洽：剥本键后三者逐键相等）。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "post.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    (backup_dir / "pre.json").write_text(
        json.dumps(strip_server_key(doc), indent=2), encoding="utf-8"
    )
    cfg.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def run_verify(cfg: Path, backup_dir: Path, timeout: float = 120.0
               ) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB_ENV_KEYS}
    return subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT),
         "--config", str(cfg), "--backup-dir", str(backup_dir),
         "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


# ---------------------------------------------------------------------------
# 核心：SC 的脚本化 4 步（备份 → 移除本键 → 红且恰为一条 → 还原 → 绿 + sha 相等）
# ---------------------------------------------------------------------------

def test_four_step_unregister_restore_discrimination(tmp_path):
    """SC 主链路：注册条目被移除时闸门必须变红，且红集恰为 sc-register-mcp
    一条；字节级还原后必须全绿，配置与备份 sha256 相等，其它键全程逐字未变。"""
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = tmp_path / ".claude.json"
    backup_dir = tmp_path / "reg-backup"
    doc = build_registered_config(vault)
    write_green_state(cfg, backup_dir, doc)

    # 步1 备份（字节级快照 + 独立副本充当还原源）
    backup_bytes = cfg.read_bytes()
    backup_sha = sha256_bytes(backup_bytes)
    backup_copy = tmp_path / "backup-copy.json"
    backup_copy.write_bytes(backup_bytes)

    # 步2 移除 mcpServers.akb2-retrieval（模拟注册丢失/被删）
    red_doc = json.loads(cfg.read_text(encoding="utf-8"))
    del red_doc["mcpServers"][SERVER_KEY]
    cfg.write_text(json.dumps(red_doc, indent=2), encoding="utf-8")

    # 红态其它键逐字未变：剥本键后与备份 deep-equal（值级断言）
    assert strip_server_key(red_doc) == strip_server_key(
        json.loads(backup_bytes)
    ), "移除本键不得牵动其它任何键"

    # 步3 verify 必须红，且红集恰为 sc-register-mcp 一条：
    # exit 1（前置失败）+ stderr 点名本键未注册 + 无 ①②③ 段误伤标记
    red = run_verify(cfg, backup_dir)
    assert red.returncode == EXIT_PRECONDITION, (
        f"移除注册条目后应以前置失败变红，got exit={red.returncode} "
        f"stderr={red.stderr!r}"
    )
    assert SERVER_KEY in red.stderr and "未注册" in red.stderr, red.stderr
    for collateral in ("①权威源不一致", "②其它键被改动", "③握手失败"):
        assert collateral not in red.stderr, (
            f"红集超出 sc-register-mcp 一条（混入 {collateral}）: {red.stderr!r}"
        )
    # 红的瞬间绿色标记一个都不许出现（非空转证明）
    for marker in ("REG_AUTHORITY_MATCH", "REG_OTHER_KEYS_INTACT", "REG_HANDSHAKE_OK"):
        assert marker not in red.stdout, red.stdout

    # 步4 还原（字节级）→ 再跑必须绿，三段标记齐全
    cfg.write_bytes(backup_copy.read_bytes())
    assert sha256_bytes(cfg.read_bytes()) == backup_sha
    green = run_verify(cfg, backup_dir)
    assert green.returncode == EXIT_OK, green.stderr
    assert "REG_AUTHORITY_MATCH" in green.stdout, green.stdout
    assert "REG_OTHER_KEYS_INTACT" in green.stdout, green.stdout
    assert f"REG_HANDSHAKE_OK {SERVER_KEY}" in green.stdout, green.stdout

    # 终态与备份 sha256 相等：全过程结束后配置逐字节回到备份（其它键逐字未变
    # 的字节级收口，覆盖还原写本身没有引入任何字节漂移）
    assert sha256_bytes(cfg.read_bytes()) == backup_sha


# ---------------------------------------------------------------------------
# 鉴别力：闸门能区分不同失坏形态（红因各自落在对应段，exit 码不串位）
# ---------------------------------------------------------------------------

def test_discriminates_wrong_pythonpath(tmp_path):
    """①段：条目在、快照自洽，但 env.PYTHONPATH 指向别处 → exit 2。

    若闸门只认『条目在不在』，本用例会假绿——这是鉴别力的最小反例。"""
    vault = tmp_path / "vault"
    cfg = tmp_path / ".claude.json"
    backup_dir = tmp_path / "reg-backup"
    doc = build_registered_config(vault)
    doc["mcpServers"][SERVER_KEY]["env"]["PYTHONPATH"] = str(tmp_path / "elsewhere" / "src")
    write_green_state(cfg, backup_dir, doc)

    proc = run_verify(cfg, backup_dir)
    assert proc.returncode == EXIT_AUTHORITY, (
        f"PYTHONPATH 篡改应落 ①段 exit 2，got {proc.returncode}: {proc.stderr!r}"
    )
    assert "env.PYTHONPATH" in proc.stderr, proc.stderr


def test_discriminates_other_key_drift_after_snapshot(tmp_path):
    """②段：pre/post 快照自洽，但 live 配置的其它键在快照后被改 → exit 3。

    锁死『快照自洽 ≠ 其它键保留』的鉴别缺口：只比 pre/post 会对 live 的
    漂移视而不见（被测脚本 check_other_keys 的 post/live 绑定分支）。"""
    vault = tmp_path / "vault"
    cfg = tmp_path / ".claude.json"
    backup_dir = tmp_path / "reg-backup"
    doc = build_registered_config(vault)
    write_green_state(cfg, backup_dir, doc)

    tampered = json.loads(cfg.read_text(encoding="utf-8"))
    tampered["userID"] = "tampered-after-snapshot"
    cfg.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    proc = run_verify(cfg, backup_dir)
    assert proc.returncode == EXIT_OTHER_KEYS, (
        f"live 其它键漂移应落 ②段 exit 3，got {proc.returncode}: {proc.stderr!r}"
    )
    assert "userID" in proc.stderr, proc.stderr


def test_discriminates_handshake_without_vault_env(tmp_path):
    """③段：条目/快照全绿，但缺 AKB2_ROOT（且 verify 子进程环境也已剥净）
    → lifespan fail-closed → 真实握手失败 exit 4。

    ①② 段都照常通过，证明 exit 4 确实来自③段的运行时握手而非前置误伤。"""
    vault = tmp_path / "vault"
    cfg = tmp_path / ".claude.json"
    backup_dir = tmp_path / "reg-backup"
    doc = build_registered_config(vault)
    del doc["mcpServers"][SERVER_KEY]["env"]["AKB2_ROOT"]
    write_green_state(cfg, backup_dir, doc)

    proc = run_verify(cfg, backup_dir)
    assert proc.returncode == EXIT_HANDSHAKE, (
        f"缺 vault env 应落 ③段 exit 4，got {proc.returncode}: {proc.stderr!r}"
    )
    # ①② 段标记已打出的前提下才轮到③段红（exit 4 只可能在其后）
    assert "REG_AUTHORITY_MATCH" in proc.stdout, proc.stdout
    assert "REG_OTHER_KEYS_INTACT" in proc.stdout, proc.stdout
