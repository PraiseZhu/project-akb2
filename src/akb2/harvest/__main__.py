"""harvest 组装 CLI 入口:``python3 -m akb2.harvest``(sc-asm-cli + p1a-sc0)。

用法:
    python3 -m akb2.harvest <file> --harvest-run-id <id> [--redact]
        [--vault-root <path>]

- ``--harvest-run-id`` 必填(无默认值,缺失即 exit 2);该值**只透传不生成**,
  原样贯通到 ``ledger.run_harvest_with_ledger`` 与 ``assemble``。
- ``--redact`` 唯一动作是把 policy 映射为 Redact 传入;CLI 侧**零文本预处理、
  零写盘**,替换只发生在 assembler 内部。
- ``--vault-root`` 覆盖仅测试用(写入 env VAULT_ROOT 供 paths 解析)。

生产路径(p1a-sc0):CLI 不再直调 ``assembler.assemble``,改经事务编排器
``ledger.run_harvest_with_ledger``(九步:查 committed → 预约 RUN_ID/CONTENT
→ 写 pending → assemble 落盘前段 → 预约 DESTNAME → assemble(phase='place')
落位 → 写 committed)。身份三元组在调用编排器**之前**算死,且必须先过
``schema.validate`` + ``schema.run_lane_check``(与 assemble 内部同一函数
对象,不另写闸);违规 exit 2、零台账副作用(不写 pending/committed):

- session_id = payload['session_id'](小写 canonical UUID4,已过 p0 闸)
- dest_filename = f"{session_id}--{compute_article_id('cindy://session/'
  + session_id)}--raw--{UTC 当天}.md"(与 archive_raw 缺省拼法同一纯函数;
  date 取调用编排器当下 UTC 日,注入 assemble 禁其再 now())
- content_fingerprint = sha256(utf-8(_render_body(payload)))(与 assemble
  内部渲染共用 _render_body,禁止第二份拼接)

assemble_fn 闭包(单次 assemble 调用、单次 try/finally,不拆两段):
- 第一次回调(无 phase):无副作用哨兵,只 return {'success': True}——
  不创建 .harvest-iso--*、不调 assemble()。
- 第二次回调(phase='place', reservation=destname_rsv):唯一一次
  ``assemble(..., reservation=, dest_filename=, date_str=)`` 走生产注入
  模式返回绝对路径;闭包校验 basename==dest_filename 后映射为
  {'success': True, 'raw_abspath': ...}。五类已知准入/落位异常
  (AssemblyRejected/RedactionRejected/PlaceExists/AnchorDrift/Preflight)
  结构化为 {'success': False, 'error': '类型名: 消息'} 不冒泡;其它
  OSError/TypeError 显式放行冒泡(由编排器收成 outcome)。

退出码(按 ledger outcome / error 前缀还原,main 是编排器结果的唯一消费者):
    0 = created(落位成功,stderr 已落位 {basename})
    2 = schema/lane/run_id 参数非法、AssemblyRejected/AnchorDrift/Preflight、
        assemble_failed、content_conflict
    3 = redaction 命中(Reject 默认策略)
    4 = PlaceExists 或 destname_conflict
    5 = already_committed(同身份幂等跳过)
    6 = run_id_conflict(同 run_id 不同身份,硬拒绝)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from akb2.harvest import assembler, ledger, schema
from akb2.harvest.anchors import AnchorDriftError
from akb2.harvest.redaction import RedactionPolicy
from src.akb2.quality.intake.models import compute_article_id

# run_id 校验与 assembler 共用同一函数对象(身份断言防两份实现漂移)
validate_harvest_run_id = assembler.validate_harvest_run_id

EXIT_OK = 0
EXIT_REJECTED = 2
EXIT_REDACTION = 3
EXIT_EXISTS = 4

# 闭包结构化的五类已知准入/落位异常(其余 OSError/TypeError 显式放行冒泡)
_STRUCTURED_EXCEPTIONS = (
    assembler.AssemblyRejectedError,
    AnchorDriftError,
    assembler.RedactionRejectedError,
    assembler.PlaceExistsError,
    assembler.PreflightError,
)


def _report_outcome(result: dict) -> int:
    """编排器结果 → CLI 退出码(stderr 具名,零 traceback、零正文回显)。

    main() 是编排器 outcome/error 的唯一消费者;此处不得把所有非 created
    一律打成通用 2(3/4/5/6 四支语义由此还原)。
    """
    outcome = result.get("outcome")
    error = result.get("error") or ""
    if outcome == "created":
        print(f"harvest: 已落位 {result.get('filename')}", file=sys.stderr)
        return EXIT_OK
    if outcome == "already_committed":
        print("harvest: already_committed: 同身份已提交,幂等跳过",
              file=sys.stderr)
        return ledger.EXIT_ALREADY_COMMITTED
    if outcome == "run_id_conflict":
        print("harvest: run_id_conflict: 同 run_id 不同身份,硬拒绝",
              file=sys.stderr)
        return ledger.EXIT_RUN_ID_CONFLICT
    if outcome == "content_conflict":
        print("harvest: content_conflict: 内容键被并发持有", file=sys.stderr)
        return EXIT_REJECTED
    # error 类型前缀优先于 outcome 兜底(前缀即闭包保留的异常类型名)
    if error.startswith("RedactionRejectedError:"):
        print(f"harvest: {error}", file=sys.stderr)
        return EXIT_REDACTION
    if error.startswith("PlaceExistsError:"):
        print(f"harvest: {error}", file=sys.stderr)
        return EXIT_EXISTS
    if outcome == "destname_conflict":
        print("harvest: destname_conflict: 落位目标名被并发持有",
              file=sys.stderr)
        return EXIT_EXISTS
    # AssemblyRejected / AnchorDrift / Preflight 前缀、assemble_failed、
    # place_failed 其余前缀 → 统一 2
    if error:
        print(f"harvest: 拒绝落位: {error}", file=sys.stderr)
    else:
        print(f"harvest: 落位失败: {outcome}", file=sys.stderr)
    return EXIT_REJECTED


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

    # identity 前置闸(p1a-sc0 ③):先闸后算——schema.validate + run_lane_check
    # 与 assemble 内部同一函数对象;违规 exit 2,不调编排器、零台账副作用
    # (缺字段/非 dict/非字符串 session_id/lane 不足/atoms 畸形在这里先拒,
    # 不会先写 pending 再被后段拒)。
    violations = schema.validate(payload)
    if violations:
        print("harvest: schema 校验失败: " + "; ".join(violations[:5]),
              file=sys.stderr)
        return EXIT_REJECTED
    lane_ok, _lane_code, lane_msg = schema.run_lane_check(payload)
    if not lane_ok:
        print(f"harvest: {lane_msg}", file=sys.stderr)
        return EXIT_REJECTED

    policy = RedactionPolicy.Redact if args.redact else RedactionPolicy.Reject

    # identity 算死(调用编排器之前;编排器在任何回调前就要用
    # session_id+content_fingerprint 算 ck 并写 pending,回调没有回写通道):
    # - date 取当下 UTC 日,注入 assemble(date_str),禁止其内部再 now();
    # - fingerprint 与 assemble 内部渲染共用 _render_body(同一函数对象)。
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    session_id = payload["session_id"]
    article_id = compute_article_id("cindy://session/" + session_id)
    dest_filename = f"{session_id}--{article_id}--raw--{date_str}.md"
    content_fingerprint = hashlib.sha256(
        assembler._render_body(payload).encode("utf-8")).hexdigest()

    def assemble_fn(*, run_id: str, dest_filename: str,
                    phase: str | None = None,
                    reservation=None) -> dict:
        """编排器回调:第一次无副作用哨兵,第二次(phase='place')唯一落盘。"""
        if phase != "place":
            return {"success": True}  # 无副作用哨兵:不建隔离目录、不调 assemble
        try:
            raw_abspath = assembler.assemble(
                payload, harvest_run_id=run_id, policy=policy,
                reservation=reservation, dest_filename=dest_filename,
                date_str=date_str)
        except _STRUCTURED_EXCEPTIONS as exc:
            # 五类已知准入/落位异常 → 结构化数据(error 保留类型前缀),
            # 编排器据此映射 place_failed;不得冒泡。
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        if Path(raw_abspath).name != dest_filename:
            return {
                "success": False,
                "error": (f"basename 不符: 期望 {dest_filename!r}, "
                          f"实际 {Path(raw_abspath).name!r}"),
            }
        return {"success": True, "raw_abspath": raw_abspath}

    # raw_dir 与隔离目录选址同源(assembler 内部读同一份 roots.json);
    # 绝对路径推导禁止 VAULT_ROOT。
    try:
        raw_dir = json.loads(
            (Path(os.environ.get("AKB2_HARVEST_DEPLOY_ROOT", ""))
             / "roots.json").read_text(encoding="utf-8"))["raw_dir"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"harvest: 部署根 roots.json 不可读: {exc}", file=sys.stderr)
        return EXIT_REJECTED

    try:
        result = ledger.run_harvest_with_ledger(
            str(ledger.resolve_ledger_path()),
            run_id=args.harvest_run_id,
            session_id=session_id,
            content_fingerprint=content_fingerprint,
            dest_filename=dest_filename,
            raw_dir=raw_dir,
            assemble_fn=assemble_fn,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        # 编排器外围失败(账本路径 fail-closed / 锁需人工裁定 / 锚点漂移):
        # 具名 exit 2,不留 traceback。
        print(f"harvest: 编排失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REJECTED

    return _report_outcome(result)


if __name__ == "__main__":
    sys.exit(main())
