"""feishu_sync CLI 入口。

用法：
  python -m akb2.feishu_sync.cli --vault-root <path> [--date YYYY-MM-DD]
                                 [--target dm|group] [--state-path <abs>]

退出码：0=发送成功 / dry-run 成功 / 今日无可发布内容（静默）；1=有失败。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import config, notify
from .agent import run_sync
from .paths import resolve_state_path


def _today() -> str:
    # 规范来源是 wrapper 传入的 --date（Asia/Shanghai）；裸跑时退回本机日期
    return datetime.now().strftime("%Y-%m-%d")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="akb2.feishu_sync")
    parser.add_argument("--vault-root", required=True, help="Obsidian vault 根目录")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    parser.add_argument(
        "--target",
        choices=["dm", "group"],
        default="dm",
        help="dm=私信(测试) / group=XD WEB 群(上线)。默认 dm 防误发群。",
    )
    parser.add_argument("--state-path", default=None, help="幂等状态文件绝对路径（默认 repo/logs）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只发现/渲染，不触发飞书发送，不写幂等状态",
    )
    args = parser.parse_args(argv)

    vault_root = Path(args.vault_root).expanduser().resolve()
    if not vault_root.is_dir():
        notify.log_line(f"ERROR: --vault-root 不存在: {vault_root}")
        return 1

    date = args.date or _today()
    state_path = resolve_state_path(args.state_path)

    missing = config.missing_feishu_vars()
    if missing and not args.dry_run:
        notify.alert_failure(
            f"缺少飞书环境变量: {','.join(missing)}（请 source ~/.akb2-secrets.sh）"
        )
        return 1

    try:
        return run_sync(
            date=date,
            vault_root=vault_root,
            target=args.target,
            state_path=state_path,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 — 顶层兜底，未捕获异常也要告警 + exit 1
        notify.alert_failure(f"{date} 未捕获异常: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
