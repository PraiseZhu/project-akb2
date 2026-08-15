#!/usr/bin/env python3
"""candidate_bound_run — p0-harness 候选绑定 wrapper。

归属：sc-anchor-set-contract（anchor 集合派生规则与职责边界的单一 owner）
     + sc-harness-candidate-bind（候选 artifact 产出与被包命令的绑定执行）。

两种模式：
  --emit-candidate <仓外路径> --scope <priority>
      从本轮 task-manifest 全部 SC 的 anchor_paths 取并集，按路径归属二分：
        (a) 仓内相对路径   → scope_paths（只记清单供审计，不进 external_hashes；
                             其漂移由 dirty=0 单一 owner 判定）
        (b) 仓外白名单路径 → external_hashes（文件级 sha256；文件不存在记 null
                             并列入 external_pending）
      artifact 同时落盘 scope_paths 与 external_hashes 各自的 set_digest。
      白名单（规范化为仓外绝对路径）：
        - .claude.json            → $AKB2_CBR_HOME/.claude.json（默认 $HOME）
        - capabilities/...        → $AKB2_CBR_CAPABILITIES_ROOT/...（默认
                                    ~/AI-Agent/Claude/capabilities）
      路径越界（既非仓内相对、也非白名单内仓外路径）→ fail-closed 拒绝产出。

  --candidate-manifest <仓外路径> --report-dir <仓外目录> -- <cmd...>
      执行前校验 HEAD == artifact.head_sha 且 dirty=0 且 external_hashes 逐一
      相等，执行被包命令，执行后再校验同样三项；两次都过才透传被包命令退出码。
      读入 artifact 时先做格式世代校验：缺 candidate_scope（或任一必需键）、
      含废弃键 anchor_hashes/pending_paths、external_hashes 混入仓内路径
      → STALE_ARTIFACT_FORMAT 非 0，不降级兼容。
      每次运行按**当前** task-manifest 重算 scope/external 集合的 set_digest
      并与 artifact 内值严格比较（防『改了 manifest 却拿旧 artifact 继续跑』）。

诚实边界（措辞硬约束，不得升级）：本 wrapper 保证的是『被包命令不产生
git 可见（tracked/untracked 非 ignored）的仓内改动』，**不是**『不往仓内写
任何字节』——.gitignore 覆盖的缓存（__pycache__/.pytest_cache 等）不在
观察面内，因此 dirty 判据固定为**不带 --ignored** 的 git status --porcelain。

术语单一 owner：external_hashes（仓外，比 hash）/ scope_paths（仓内，只清单）
/ external_pending（仓外待建）。旧名 anchor_hashes、pending_paths 全域废弃，
只允许出现在负断言与废弃说明中。

退出码：0 成功/透传；2 用法错误；3 STALE_ARTIFACT_FORMAT；4 执行前校验失败；
5 执行后校验失败；6 manifest 漂移 / 路径越界 / 仓内写入面违规。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_STALE_FORMAT = 3
EXIT_PRE_CHECK = 4
EXIT_POST_CHECK = 5
EXIT_MANIFEST_DRIFT = 6

STALE_MARK = "STALE_ARTIFACT_FORMAT"

# 十二键完整集合（格式世代）：缺任一 → STALE_ARTIFACT_FORMAT。
# generation（sc-candidate-artifact-lifecycle）：基线 artifact = 0，每次
# re-emit 严格 +1 ——『不可变』指每一代内容不再就地修改，re-emit 产生新世代。
REQUIRED_KEYS = (
    "candidate_scope",
    "branch",
    "head_sha",
    "base_sha",
    "dirty_count",
    "generation",
    "scope_paths",
    "scope_set_digest",
    "external_hashes",
    "external_set_digest",
    "external_pending",
    "emitted_at",
)

# 旧世代键名，全域废弃：出现在 artifact 中 → STALE_ARTIFACT_FORMAT。
DEPRECATED_KEYS = ("anchor_hashes", "pending_paths")

DEFAULT_TASK_MANIFEST = "~/.claude/.goal/akb2-cindy-interface/task-manifest.json"
ARTIFACT_SUFFIX = ".candidate.json"
REPORT_FILENAME = "candidate-run-report.json"


def _log(message: str) -> None:
    print(f"candidate_bound_run: {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# git 观察面（dirty 判据固定为不带 --ignored 的 porcelain）
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def _repo_root() -> Path | None:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if r.returncode != 0:
        return None
    return Path(r.stdout.strip()).resolve()


def _head_sha(repo: Path) -> str:
    r = _git(repo, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def _branch(repo: Path) -> str:
    r = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def _base_sha(repo: Path) -> str:
    for ref in ("origin/main", "main"):
        r = _git(repo, "merge-base", "HEAD", ref)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return _head_sha(repo)


def _dirty_count(repo: Path) -> int:
    """不带 --ignored 的 porcelain 行数（ignored 缓存不计入观察面）。"""
    r = _git(repo, "status", "--porcelain")
    if r.returncode != 0:
        return -1
    return len([line for line in r.stdout.splitlines() if line.strip()])


def _inside_repo(path: Path, repo_root: Path) -> bool:
    try:
        path.resolve().relative_to(repo_root)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# anchor 集合派生（sc-anchor-set-contract 单一 owner）
# ---------------------------------------------------------------------------


def _env_home_root() -> Path:
    return Path(
        os.environ.get("AKB2_CBR_HOME") or os.path.expanduser("~")
    ).resolve()


def _env_capabilities_root() -> Path:
    raw = os.environ.get("AKB2_CBR_CAPABILITIES_ROOT")
    if not raw:
        raw = "~/AI-Agent/Claude/capabilities"
    return Path(os.path.expanduser(raw)).resolve()


def normalize_anchor(raw: str) -> tuple[str, str]:
    """按路径归属二分，返回 (kind, normalized)。

    kind ∈ {"repo", "external", "reject"}：
      - repo     仓内相对路径（原样保留，git 观察面负责其漂移）
      - external 白名单仓外路径（规范化为绝对路径）
      - reject   越界（fail-closed）
    """
    home = _env_home_root()
    caps = _env_capabilities_root()
    claude_json = home / ".claude.json"

    def _under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    # 形态一：~ 展开写法
    if raw.startswith("~"):
        expanded = Path(os.path.expanduser(raw)).resolve()
        if expanded == claude_json or _under(expanded, caps):
            return "external", str(expanded)
        return "reject", raw

    # 形态二：绝对路径
    if raw.startswith("/"):
        expanded = Path(raw).resolve()
        if expanded == claude_json or _under(expanded, caps):
            return "external", str(expanded)
        return "reject", raw

    # 形态三：相对写法——先试白名单规范化，再判仓内/越界
    if raw == ".claude.json":
        return "external", str(claude_json)
    if raw == "capabilities" or raw.startswith("capabilities/"):
        expanded = (caps / raw[len("capabilities/") :]).resolve()
        if _under(expanded, caps):
            return "external", str(expanded)
        return "reject", raw

    parts = raw.split("/")
    if ".." in parts or raw.startswith("/"):
        return "reject", raw
    if not raw:
        return "reject", raw
    return "repo", raw


def _set_digest(items: list[str] | tuple[str, ...] | set[str]) -> str:
    """对路径集合（顺序无关、去重）取确定性 digest。"""
    return hashlib.sha256("\n".join(sorted(set(items))).encode()).hexdigest()


def _sha256_file(path: Path) -> str | None:
    """文件级 sha256；不存在记 None；目录等非普通文件按 reject 处理抛错。"""
    if path.is_dir():
        raise ValueError(f"anchor 是目录而非文件: {path}")
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except FileNotFoundError:
        return None
    return h.hexdigest()


def derive_anchor_sets(manifest_path: Path) -> dict:
    """从 task-manifest 全部 SC 的 anchor_paths 取并集并二分。

    返回 {"scope_paths", "external_hashes", "external_pending",
          "scope_set_digest", "external_set_digest"}；越界抛 AnchorReject。
    """

    class AnchorReject(Exception):
        pass

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnchorReject(f"task-manifest 不可读: {manifest_path}: {exc}") from exc

    union: list[str] = []
    for sc in manifest.get("scs", []):
        for p in sc.get("anchor_paths") or []:
            if p not in union:
                union.append(p)

    scope: list[str] = []
    external: dict[str, str | None] = {}
    pending: list[str] = []
    for raw in union:
        kind, norm = normalize_anchor(raw)
        if kind == "reject":
            raise AnchorReject(f"anchor 路径越界（非仓内相对、也非白名单仓外）: {raw!r}")
        if kind == "repo":
            scope.append(norm)
        else:
            digest = _sha256_file(Path(norm))
            external[norm] = digest
            if digest is None:
                pending.append(norm)

    return {
        "scope_paths": sorted(set(scope)),
        "external_hashes": dict(sorted(external.items())),
        "external_pending": sorted(set(pending)),
        "scope_set_digest": _set_digest(scope),
        "external_set_digest": _set_digest(external.keys()),
    }


# ---------------------------------------------------------------------------
# emit 模式
# ---------------------------------------------------------------------------


def _read_prior_generation(out_path: Path, scope: str) -> tuple[int | None, str | None]:
    """读同 scope 旧世代 artifact 的 generation；读不到返回 (None, 原因)。

    只认 candidate_scope 同名的旧文件——跨 priority 的旧 artifact 不参与
    世代计数（每个 priority 的世代链独立，R5-C1）。

    旧世代引导：generation 键引入前的十一键 artifact 是合法前世代
    （generation 按基线 0 处理，re-emit 产出 generation=1）——把缺键
    一律判非法会锁死全部既有 artifact 的世代链。candidate_scope 不符
    或 generation 存在但类型非法才是真非法。
    """
    if not out_path.is_file():
        return None, None
    try:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"旧 artifact 不可读: {exc}"
    if not isinstance(prior, dict) or prior.get("candidate_scope") != scope:
        return None, (
            f"旧 artifact candidate_scope={prior.get('candidate_scope')!r}"
            f" 与 --scope={scope!r} 不符"
        )
    gen = prior.get("generation", 0)  # 缺键 = 世代键引入前的基线旧世代
    if not isinstance(gen, int) or isinstance(gen, bool) or gen < 0:
        return None, f"旧 artifact generation 非法: {gen!r}"
    return gen, None


def cmd_emit(args: argparse.Namespace) -> int:
    scope = args.scope
    out_path = Path(args.emit_candidate).expanduser()
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path = out_path.resolve()

    repo_root = _repo_root()
    if repo_root is None:
        _log("emit 必须在 git 仓库内执行（cwd 不属于任何 repo）")
        return EXIT_USAGE
    if _inside_repo(out_path, repo_root):
        _log(f"emit 输出路径必须仓外（不得位于 repo 内）: {out_path}")
        return EXIT_MANIFEST_DRIFT

    # 文件名 <priority>.candidate.json ↔ candidate_scope 配对守卫
    if out_path.name != f"{scope}{ARTIFACT_SUFFIX}":
        _log(
            f"emit 输出文件名必须为 '{scope}{ARTIFACT_SUFFIX}'（与 --scope 配对），"
            f"实际: {out_path.name}"
        )
        return EXIT_USAGE

    manifest_path = _task_manifest_path()
    try:
        derived = derive_anchor_sets(manifest_path)
    except Exception as exc:  # AnchorReject / manifest 缺失
        _log(f"anchor 集合派生失败: {exc}")
        return EXIT_MANIFEST_DRIFT

    # 世代语义（sc-candidate-artifact-lifecycle）：旧文件存在且合法 → 严格 +1；
    # 不存在 → 基线 generation=0。旧文件非法时 fail-closed 拒绝 re-emit——
    # 静默归零会让世代链断裂（执行期 materialization 检测依赖世代单调）。
    prior_gen, gen_reason = _read_prior_generation(out_path, scope)
    if prior_gen is None and gen_reason is not None:
        _log(f"re-emit 拒绝：{gen_reason}")
        return EXIT_MANIFEST_DRIFT
    generation = 0 if prior_gen is None else prior_gen + 1

    artifact = {
        "candidate_scope": scope,
        "branch": _branch(repo_root),
        "head_sha": _head_sha(repo_root),
        "base_sha": _base_sha(repo_root),
        "dirty_count": _dirty_count(repo_root),
        "generation": generation,
        "scope_paths": derived["scope_paths"],
        "scope_set_digest": derived["scope_set_digest"],
        "external_hashes": derived["external_hashes"],
        "external_set_digest": derived["external_set_digest"],
        "external_pending": derived["external_pending"],
        "emitted_at": datetime.now(UTC).isoformat(),
    }

    # 原子替换（sc-candidate-artifact-lifecycle）：先完整写同目录临时文件 →
    # 写后校验（candidate_scope 与双 set_digest 回读比对）→ os.replace。
    # 绝不就地修改旧 artifact —— wrapper 读取途中不会看到半写状态
    # （hardening 1：写入必须原子，校验在替换之前完成）。校验不过 →
    # 删临时文件退出，旧 artifact 原封不动（原子性反证路径）。
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(f".{out_path.name}.tmp-{os.getpid()}")
    try:
        tmp_path.write_text(
            json.dumps(artifact, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        # 写后校验：candidate_scope + 双 digest 回读比对（替换前完成）
        check = json.loads(tmp_path.read_text(encoding="utf-8"))
        if (
            check["candidate_scope"] != scope
            or check["scope_set_digest"] != derived["scope_set_digest"]
            or check["external_set_digest"] != derived["external_set_digest"]
        ):
            _log(
                "re-emit 写后校验失败（临时文件内容与派生集不符，旧 artifact 未动）"
            )
            return EXIT_MANIFEST_DRIFT
        os.replace(tmp_path, out_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    print(
        f"emitted {out_path} scope={scope} generation={generation} "
        f"repo_anchors={len(artifact['scope_paths'])} "
        f"external_anchors={len(artifact['external_hashes'])} "
        f"external_pending={len(artifact['external_pending'])}"
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# run 模式（被包命令绑定执行）
# ---------------------------------------------------------------------------


def _task_manifest_path() -> Path:
    raw = os.environ.get("AKB2_CBR_TASK_MANIFEST") or DEFAULT_TASK_MANIFEST
    return Path(os.path.expanduser(raw)).resolve()


def _load_artifact(path: Path) -> tuple[dict | None, str | None]:
    """读入并做格式世代校验；违者返回 (None, 原因)——STALE_ARTIFACT_FORMAT。"""
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"artifact 不可读: {exc}"
    if not isinstance(artifact, dict):
        return None, "artifact 顶层不是 JSON object"

    missing = [k for k in REQUIRED_KEYS if k not in artifact]
    if missing:
        return None, f"缺少必需键: {','.join(missing)}"
    deprecated = [k for k in DEPRECATED_KEYS if k in artifact]
    if deprecated:
        return None, f"含废弃键: {','.join(deprecated)}"

    external = artifact["external_hashes"]
    if not isinstance(external, dict):
        return None, "external_hashes 不是 object"
    for key in external:
        kind, _ = normalize_anchor(key)
        if kind != "external":
            return None, f"external_hashes 混入非仓外路径: {key!r}"

    # 文件名 <priority>.candidate.json ↔ candidate_scope 配对
    expected_name = f"{artifact['candidate_scope']}{ARTIFACT_SUFFIX}"
    if path.name != expected_name:
        return None, (
            f"artifact 文件名 {path.name!r} 与 candidate_scope "
            f"{artifact['candidate_scope']!r} 不配对（应为 {expected_name!r}）"
        )
    gen = artifact["generation"]
    if not isinstance(gen, int) or isinstance(gen, bool) or gen < 0:
        return None, f"generation 非法: {gen!r}（须为非负整数）"
    return artifact, None


def _check_external(external: dict[str, str | None]) -> list[dict]:
    """逐一比对当前 sha256 与 artifact 记录值；null ⇒ 文件必须不存在。"""
    mismatches = []
    for path_raw, expected in sorted(external.items()):
        actual = _sha256_file(Path(path_raw))
        if actual != expected:
            mismatches.append(
                {"path": path_raw, "expected": expected, "actual": actual}
            )
    return mismatches


def _check_phase(repo_root: Path, artifact: dict) -> dict:
    head_actual = _head_sha(repo_root)
    dirty = _dirty_count(repo_root)
    mismatches = _check_external(artifact["external_hashes"])
    return {
        "head_ok": head_actual == artifact["head_sha"],
        "head_expected": artifact["head_sha"],
        "head_actual": head_actual,
        "dirty_ok": dirty == 0,
        "dirty_count": dirty,
        "external_ok": not mismatches,
        "external_mismatches": mismatches,
    }


def _phase_failed(phase: dict) -> list[str]:
    reasons = []
    if not phase["head_ok"]:
        reasons.append(
            f"head_sha 漂移 (expected={phase['head_expected']}, "
            f"actual={phase['head_actual']})"
        )
    if not phase["dirty_ok"]:
        reasons.append(f"dirty_count={phase['dirty_count']} (git 可见改动非零)")
    if not phase["external_ok"]:
        for m in phase["external_mismatches"]:
            reasons.append(
                f"external anchor 漂移: {m['path']} "
                f"(expected={m['expected']}, actual={m['actual']})"
            )
    return reasons


def cmd_run(args: argparse.Namespace) -> int:
    artifact_path = Path(args.candidate_manifest).expanduser().resolve()
    report_dir = Path(args.report_dir).expanduser()
    if not report_dir.is_absolute():
        report_dir = Path.cwd() / report_dir
    report_dir = report_dir.resolve()

    repo_root = _repo_root()
    if repo_root is None:
        _log("run 必须在 git 仓库内执行（cwd 不属于任何 repo）")
        return EXIT_USAGE
    if _inside_repo(report_dir, repo_root):
        _log(f"--report-dir 必须仓外（不得位于 repo 内）: {report_dir}")
        return EXIT_MANIFEST_DRIFT

    artifact, stale_reason = _load_artifact(artifact_path)
    if artifact is None:
        _log(f"{STALE_MARK}: {stale_reason}")
        return EXIT_STALE_FORMAT

    # 按当前 manifest 重算集合 digest，防『改 manifest 不 re-emit 拿旧 artifact 跑』
    manifest_path = _task_manifest_path()
    try:
        derived = derive_anchor_sets(manifest_path)
    except Exception as exc:
        _log(f"anchor 集合派生失败（按当前 manifest 重算）: {exc}")
        return EXIT_MANIFEST_DRIFT
    drift_reasons = []
    if derived["scope_set_digest"] != artifact["scope_set_digest"]:
        drift_reasons.append("scope_set_digest 与当前 manifest 派生值不等")
    if derived["external_set_digest"] != artifact["external_set_digest"]:
        drift_reasons.append("external_set_digest 与当前 manifest 派生值不等")
    if drift_reasons:
        _log(
            "MANIFEST_DRIFT: " + "; ".join(drift_reasons)
            + f"（artifact={artifact_path}, manifest={manifest_path}）"
        )
        return EXIT_MANIFEST_DRIFT

    report_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "candidate": str(artifact_path),
        "candidate_scope": artifact["candidate_scope"],
        "generation": artifact["generation"],
        "emitted_at": artifact["emitted_at"],
        "cmd": args.cmd,
        "task_manifest": str(manifest_path),
        "pre": None,
        "post": None,
        "cmd_rc": None,
        "wrapper_exit": None,
        "finished_at": None,
    }

    pre = _check_phase(repo_root, artifact)
    report["pre"] = pre
    if reasons := _phase_failed(pre):
        report["wrapper_exit"] = EXIT_PRE_CHECK
        report["finished_at"] = datetime.now(UTC).isoformat()
        _write_report(report_dir, report)
        _log("PRE_CHECK_FAIL: " + "; ".join(reasons))
        return EXIT_PRE_CHECK

    cmd_rc = subprocess.call(args.cmd)
    report["cmd_rc"] = cmd_rc

    post = _check_phase(repo_root, artifact)
    report["post"] = post
    if reasons := _phase_failed(post):
        report["wrapper_exit"] = EXIT_POST_CHECK
        report["finished_at"] = datetime.now(UTC).isoformat()
        _write_report(report_dir, report)
        _log("POST_CHECK_FAIL: " + "; ".join(reasons))
        return EXIT_POST_CHECK

    report["wrapper_exit"] = EXIT_OK
    report["finished_at"] = datetime.now(UTC).isoformat()
    _write_report(report_dir, report)
    return cmd_rc


def _write_report(report_dir: Path, report: dict) -> None:
    """执行报告只写仓外 --report-dir，不新增任何仓内写入面。"""
    path = report_dir / REPORT_FILENAME
    path.write_text(
        json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="candidate_bound_run.py",
        description=(
            "候选绑定 wrapper：emit 产出 priority 专属候选 artifact；"
            "run 在前后双校验（HEAD/dirty/external_hashes）下执行被包命令。"
        ),
    )
    parser.add_argument(
        "--emit-candidate", metavar="仓外路径",
        help="产出 <priority>.candidate.json（须与 --scope 配对）",
    )
    parser.add_argument(
        "--scope", metavar="priority",
        help="priority id（如 p0-harness），与 emit 输出文件名配对",
    )
    parser.add_argument(
        "--candidate-manifest", metavar="仓外路径",
        help="消费的候选 artifact 路径（<priority>.candidate.json）",
    )
    parser.add_argument(
        "--report-dir", metavar="仓外目录",
        help="wrapper 执行报告落盘目录（必须仓外）",
    )
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后的被包命令")
    args = parser.parse_args(argv)

    if args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]

    if args.emit_candidate and args.candidate_manifest:
        parser.error("--emit-candidate 与 --candidate-manifest 互斥")
    if args.emit_candidate:
        if not args.scope:
            parser.error("--emit-candidate 必须与 --scope 配对")
    elif args.candidate_manifest:
        if args.report_dir is None:
            parser.error("--candidate-manifest 必须与 --report-dir 配对")
        if not args.cmd:
            parser.error("-- 之后必须给出被包命令")
    else:
        parser.error(
            "需要 --emit-candidate <路径> --scope <priority>，或 "
            "--candidate-manifest <路径> --report-dir <目录> -- <cmd...>"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.emit_candidate:
        return cmd_emit(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
