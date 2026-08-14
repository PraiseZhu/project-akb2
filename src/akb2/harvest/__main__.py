"""harvest 组装 CLI 入口:``python3 -m akb2.harvest``(sc-asm-cli)。

用法:
    python3 -m akb2.harvest <file> --harvest-run-id <id> [--redact]
        [--vault-root <path>]

- ``--harvest-run-id`` 必填(无默认值,缺失即 exit 2);该值**只透传不生成**,
  原样贯通到 ``assemble(payload, *, harvest_run_id, ...)``。
- ``--redact`` 唯一动作是把 policy 映射为 Redact 传入;CLI 侧**零文本预处理、
  零写盘**,替换只发生在 assembler 内部。
- ``--vault-root`` 覆盖仅测试用(写入 env VAULT_ROOT 供 paths 解析)。

退出码:
    0 = 落位成功(打印最终文件名)
    2 = 内容准入失败(schema / 车道 / run_id 非法 / 预检失败 / 锚点漂移)
    3 = redaction 命中(Reject 默认策略)
    4 = 落位目标已存在(RENAME_EXCL 拒 EEXIST)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from akb2.harvest import assembler
from akb2.harvest.anchors import AnchorDriftError
from akb2.harvest.redaction import RedactionPolicy

# run_id 校验与 assembler 共用同一函数对象(身份断言防两份实现漂移)
validate_harvest_run_id = assembler.validate_harvest_run_id

EXIT_OK = 0
EXIT_REJECTED = 2
EXIT_REDACTION = 3
EXIT_EXISTS = 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m akb2.harvest",
        description="harvest JSON → raw 组装落盘(隔离目录 + 原子改名)",
    )
    parser.add_argument("file", help="harvest JSON 文件路径")
    parser.add_argument("--harvest-run-id", required=True, metavar="UUID4",
                        help="归属键(小写 canonical UUID4,必填,只透传不生成)")
    parser.add_argument("--redact", action="store_true",
                        help="命中凭证时替换 [REDACTED] 后继续落盘(默认 Reject)")
    parser.add_argument("--vault-root", default=None, metavar="PATH",
                        help="vault 根覆盖(仅测试用)")
    args = parser.parse_args(argv)

    # run_id 解析阶段强制校验(与 assemble 入口同一函数;非法即 exit 2 零产物)
    if not validate_harvest_run_id(args.harvest_run_id):
        print(
            f"harvest: --harvest-run-id 非法(须为小写 canonical UUID4): "
            f"{args.harvest_run_id!r}",
            file=sys.stderr,
        )
        return EXIT_REJECTED

    if args.vault_root:
        os.environ["VAULT_ROOT"] = args.vault_root

    try:
        with open(args.file, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        print(f"harvest: 文件不可读: {args.file}", file=sys.stderr)
        return EXIT_REJECTED
    except OSError as exc:
        print(f"harvest: 文件不可读: {args.file}: {exc}", file=sys.stderr)
        return EXIT_REJECTED
    except json.JSONDecodeError as exc:
        print(f"harvest: JSON 非法: {exc}", file=sys.stderr)
        return EXIT_REJECTED

    policy = RedactionPolicy.Redact if args.redact else RedactionPolicy.Reject
    try:
        filename = assembler.assemble(
            payload, harvest_run_id=args.harvest_run_id, policy=policy)
    except (assembler.AssemblyRejectedError, AnchorDriftError,
            assembler.PreflightError) as exc:
        print(f"harvest: 拒绝落位: {exc}", file=sys.stderr)
        return EXIT_REJECTED
    except assembler.RedactionRejectedError as exc:
        print(f"harvest: {exc}", file=sys.stderr)
        return EXIT_REDACTION
    except assembler.PlaceExistsError as exc:
        print(f"harvest: {exc}", file=sys.stderr)
        return EXIT_EXISTS

    print(f"harvest: 已落位 {filename}", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
