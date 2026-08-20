"""L0 公共时间线 collector（keyword 回退模式）。

CLI: python -m akb2.briefing.intake.l0_collector [--output <path>] [--dry-run]
     默认走真实抓取（twitter-cli）。--dry-run 强制读 fixture。

真实抓取链路：
  twitter-cli (cookie auth) → twitter_cli_to_akb2() adapter → AKB2 schema

2026-07-28 起**真实抓取失败一律 fail-closed**（`KeywordSearchEndpointUnavailable`），
不再降级 fixture。原实现的每个失败分支都是 `used_fallback=True; _read_fixture()`，
一旦有人把 `AKB2_INTAKE_COLLECTOR=keyword` 打开，抓取失败会静默变成 12 条 fixture
假推文冒充真实抓取 —— 这正是 2026-06/07 连续 12 天假日报的成因（constitution
原则 3 fail-closed / 原则 4 失败可见）。fixture 只保留给显式 `--dry-run`。

本模块同时承载两条链路共用的上游故障归因（`diagnose_upstream_fatal`），
account 模式从这里 import。签名 / fail-fast 阈值 / payload 适配器的定义点在
`akb2.shared.twitter_cli_constants`；下面只做 re-export，保证既有
`from ...l0_collector import UPSTREAM_FATAL_SIGNATURES` 路径拿到同一对象。
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from akb2.shared import twitter_cli_constants as _tw_cli

from .base import REPO_ROOT, BaseCollector, load_config

# re-export：定义点在 akb2.shared.twitter_cli_constants，这里保持同一对象。
UPSTREAM_FATAL_SIGNATURES = _tw_cli.UPSTREAM_FATAL_SIGNATURES
UPSTREAM_FATAL_FAILFAST_STREAK = _tw_cli.UPSTREAM_FATAL_FAILFAST_STREAK
UPSTREAM_HARD_FAILURE_FAILFAST_STREAK = _tw_cli.UPSTREAM_HARD_FAILURE_FAILFAST_STREAK
HARD_FAILURE_TIMEOUT = _tw_cli.HARD_FAILURE_TIMEOUT
HARD_FAILURE_BAD_OUTPUT = _tw_cli.HARD_FAILURE_BAD_OUTPUT
_twitter_cli_to_akb2 = _tw_cli._twitter_cli_to_akb2

FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"

TWITTER_BIN = shutil.which("twitter") or str(Path.home() / ".local" / "bin" / "twitter")
TWITTER_TIMEOUT_SEC = 60


class UpstreamCollectorBroken(RuntimeError):
    """twitter-cli 侧全局故障 —— 整轮抓取无意义，立即中止且**不降级 fixture**。

    降级 fixture 会把「上游工具坏了」伪装成「今天没素材」，这正是 2026-06/07
    连续 12 天假数据冒充真实抓取的成因。这里选择显式失败（constitution 原则 4）。
    """

    def __init__(
        self,
        signature: str,
        accounts_tried: int,
        sample: str = "",
        diagnosis: str = "",
    ) -> None:
        self.signature = signature
        self.accounts_tried = accounts_tried
        self.sample = sample
        self.diagnosis = diagnosis
        msg = (
            f"twitter-cli 全局故障（{accounts_tried} 个账号命中同一签名）: "
            f"{signature}；curator 账号配置非成因（同一签名对每个账号逐字复现）"
        )
        if diagnosis:
            msg += f"\n{diagnosis}"
        super().__init__(msg)


def match_upstream_fatal(stderr_text: str) -> str | None:
    """命中全局故障签名则返回该签名，否则 None（= 可能只是单账号问题）。"""
    if not stderr_text:
        return None
    for sig in UPSTREAM_FATAL_SIGNATURES:
        if sig in stderr_text:
            return sig
    return None


# ---------------------------------------------------------------------------
# 上游故障成因归因（三种成因）—— account / keyword 两条链路共用
#
# 主判定始终是「探针调用是否返回全局故障签名」这个可观测行为；下面两个探测
# （补丁在位性 / 登录态）**只用来给运维指下一步**，不参与是否中止的决定。
# 探测失败一律回报 "unknown" 并把原因写进文案（不静默、不改变判定）。
# ---------------------------------------------------------------------------

PATCH_RUNBOOK = "docs/runbooks/twitter-cli-clienttransaction-patch.md"
LOCAL_PATCH_MARKER = "LOCAL PATCH 2026-07-28 (AKB2)"
# 本地补丁所在文件（工作区外，`uv tool upgrade twitter-cli` 会覆盖它）。
# python3.12 只是当前安装的解释器版本，用 glob 兜住升级换版本的情况。
LOCAL_PATCH_GLOB = ".local/share/uv/tools/twitter-cli/lib/*/site-packages/twitter_cli/client.py"
# 诊断命令预算：仅在已经确定要抛错的路径上跑一次 `twitter status`。
DIAG_CMD_TIMEOUT_SEC = 20

CAUSE_LOGIN_EXPIRED = "登录态失效"
CAUSE_PATCH_MISSING = "本地补丁缺失（很可能被 uv tool upgrade 冲掉）"
CAUSE_PATCH_PRESENT_UPSTREAM_CHANGED = "补丁在位但上游又变了"
CAUSE_UNKNOWN = "成因未能自动判定"


def _patch_state() -> tuple[str, str]:
    """本地补丁在位性 → (state, detail)，state ∈ present|absent|unknown。

    判据是补丁注释块 marker，而不是「文件存在」——文件被 upgrade 覆盖后依然存在。
    """
    try:
        matches = sorted(Path.home().glob(LOCAL_PATCH_GLOB))
    except OSError as exc:
        return "unknown", f"枚举 twitter-cli 安装目录失败: {exc}"
    if not matches:
        return "unknown", f"未找到 ~/{LOCAL_PATCH_GLOB}（安装路径可能变了）"
    for path in matches:
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            return "unknown", f"读取 {path} 失败: {exc}"
        if LOCAL_PATCH_MARKER in text:
            return "present", str(path)
    return "absent", str(matches[0])


def _login_state() -> tuple[str, str]:
    """twitter-cli 登录态 → (state, detail)，state ∈ authenticated|unauthenticated|unknown。"""
    try:
        proc = subprocess.run(
            [TWITTER_BIN, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=DIAG_CMD_TIMEOUT_SEC,
        )
        payload = json.loads(proc.stdout or "{}")
        authed = bool((payload.get("data") or {}).get("authenticated"))
    except Exception as exc:  # noqa: BLE001
        # 诊断失败绝不能掩盖它要解释的那个主故障：回报 unknown 并把原因带出去。
        return "unknown", f"`twitter status --json` 未能判定: {type(exc).__name__}: {exc}"
    state = "authenticated" if authed else "unauthenticated"
    return state, f"twitter status ok={payload.get('ok')}"


def diagnose_upstream_fatal(signature: str | None = None) -> str:
    """把一次全局故障归到三种成因之一，并给出下一步（供 stderr / 异常文案直接用）。"""
    login, login_detail = _login_state()
    patch, patch_detail = _patch_state()

    if login == "unauthenticated":
        cause = CAUSE_LOGIN_EXPIRED
        action = "重新登录 twitter-cli：`twitter login`，登录后重跑本链路"
    elif login == "unknown":
        cause = CAUSE_UNKNOWN
        action = (
            f"登录态无法判定 —— 先手跑 `twitter status`，再按 {PATCH_RUNBOOK} "
            "的「自检」一节逐条确认"
        )
    elif patch == "absent":
        cause = CAUSE_PATCH_MISSING
        action = f"按 {PATCH_RUNBOOK} 的「修复 1：本地补丁」重打补丁后重跑"
    elif patch == "present":
        cause = CAUSE_PATCH_PRESENT_UPSTREAM_CHANGED
        action = f"跑 {PATCH_RUNBOOK} 的「验证方式」三连命令，定位新的失效层"
    else:
        cause = CAUSE_UNKNOWN
        action = f"按 {PATCH_RUNBOOK} 的「自检」与「验证方式」两节逐条排查"

    return (
        f"成因判定: {cause}\n"
        f"  上游签名: {signature or '(未命中已知签名)'}\n"
        f"  登录态: {login}（{login_detail}）\n"
        f"  本地补丁: {patch}（{patch_detail}）\n"
        f"  下一步: {action}"
    )



_KEYWORD_NO_EQUIVALENT_ENDPOINT = (
    "keyword 模式是跨账号关键词搜索，user-posts（单账号时间线）无等价能力，"
    "无法像 account 模式那样换端点"
)


class KeywordSearchEndpointUnavailable(RuntimeError):
    """keyword 回退模式的真实抓取不可用 → 拒绝启动，且**绝不降级 fixture**。"""

    def __init__(
        self,
        reason: str,
        *,
        signature: str | None = None,
        sample: str = "",
        diagnosis: str = "",
    ) -> None:
        self.reason = reason
        self.signature = signature
        self.sample = sample
        self.diagnosis = diagnosis
        msg = (
            f"keyword 回退模式拒绝启动：真实抓取不可用（{reason}）。"
            f"{_KEYWORD_NO_EQUIVALENT_ENDPOINT}；"
            "因此这里 fail-closed —— 降级 fixture 会让 12 条样例推文冒充真实抓取"
            "（2026-06/07 连续 12 天假日报的成因）"
        )
        if signature:
            msg += f"\n上游全局故障签名: {signature}"
        if diagnosis:
            msg += f"\n{diagnosis}"
        super().__init__(msg)


class L0Collector(BaseCollector):
    source_name = "l0_public_timeline"

    def __init__(self, config=None):
        super().__init__(config)
        self.used_fallback: bool = False  # set True when fixture was used as fallback

    def collect(
        self,
        dry_run: bool = False,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict]:
        if dry_run:
            self.used_fallback = True
            return self._read_fixture()
        return self._fetch_real(since_date=since_date, until_date=until_date)

    def _read_fixture(self) -> list[dict]:
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        return data.get("tweets", [])

    def _refuse(self, reason: str, sample: str = "") -> "KeywordSearchEndpointUnavailable":
        """构造 fail-closed 异常，并与 UPSTREAM_FATAL_SIGNATURES 打通归因。"""
        sig = match_upstream_fatal(sample)
        return KeywordSearchEndpointUnavailable(
            reason,
            signature=sig,
            sample=sample[:300].replace("\n", " | "),
            diagnosis=diagnose_upstream_fatal(sig) if sig else "",
        )

    def _fetch_real(
        self,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict]:
        """通过 twitter-cli 拉真实推文；任何失败都 fail-closed，不降级 fixture。

        这次 search 调用本身就是「端点可用性探针」（额外探针调用数 = 0
        = 0）：它成功即端点可用，失败即拒绝启动并把成因写清楚。
        """
        cfg = (self.config or load_config()).get("sources", {}).get("l0", {})
        query = cfg.get("query", "AI OR LLM OR AGI")
        # 拆掉配置 query 里 inline 的 lang/exclude 标志,统一通过 flag 传
        query_clean = (
            query.replace("lang:en", "").replace("-is:retweet", "").strip()
        )
        max_results = int(cfg.get("max_results", 20))

        if not Path(TWITTER_BIN).exists():
            raise self._refuse(f"twitter-cli 不存在于 {TWITTER_BIN}")

        cmd = [
            TWITTER_BIN, "search", query_clean,
            "-t", "latest",           # 按时间倒序，确保近期推文优先
            "--lang", "en",
            "--exclude", "retweets",
            "-n", str(max_results),
            "--json",
        ]
        if since_date:
            cmd += ["--since", since_date]
        if until_date:
            cmd += ["--until", until_date]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TWITTER_TIMEOUT_SEC
            )
        except subprocess.TimeoutExpired:
            raise self._refuse(f"twitter-cli 超时（{TWITTER_TIMEOUT_SEC}s）") from None

        if result.returncode != 0:
            excerpt = (result.stderr or "")[:300].replace("\n", " | ")
            raise self._refuse(
                f"twitter-cli exit {result.returncode}: {excerpt}",
                sample=(result.stderr or "") + (result.stdout or ""),
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise self._refuse(f"twitter-cli 输出非 JSON: {e}", sample=result.stdout or "") from e

        if not payload.get("ok"):
            raise self._refuse(
                "twitter-cli payload.ok=false",
                sample=(result.stderr or "") + (result.stdout or ""),
            )

        raw = payload.get("data") or []
        tweets = _twitter_cli_to_akb2(raw)
        if not tweets:
            # 0 条不降级 fixture：健康的泛关键词搜索（"AI OR LLM OR AGI"）不可能
            # 真的一条没有，"空结果"与"端点静默失效"在这里无法区分，混淆即假数据。
            raise self._refuse("search 返回 0 条 —— 无法与端点静默失效区分")
        return tweets


def main():
    parser = argparse.ArgumentParser(description="L0 public timeline collector")
    parser.add_argument("--output", default="/tmp/l0-out.json", help="Output JSON path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Use fixture data instead of twitter-cli",
    )
    parser.add_argument("--since", default=None, help="Collect tweets since YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="Collect tweets until YYYY-MM-DD")
    args = parser.parse_args()
    collector = L0Collector()
    try:
        tweets = collector.collect(
            dry_run=args.dry_run,
            since_date=args.since,
            until_date=args.until,
        )
    except KeywordSearchEndpointUnavailable as exc:
        # 显式失败：不写 output、不落 fixture。pipeline 看到非零退出码即中止，
        # 不会拿到一份"看起来正常"的 tweets 列表。
        sys.stderr.write(f"ERROR [l0_collector]: {exc}\n")
        if exc.sample:
            sys.stderr.write(f"ERROR [l0_collector]: 上游原始输出: {exc.sample}\n")
        return 4
    # 顶层写入 _fallback_used 标记供 pipeline 层跳过日期过滤（SC-7）
    import json as _json
    from pathlib import Path as _Path
    out_path = _Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output = {"tweets": tweets}
    if collector.used_fallback:
        output["_fallback_used"] = True
    with open(out_path, "w") as f:
        _json.dump(output, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
