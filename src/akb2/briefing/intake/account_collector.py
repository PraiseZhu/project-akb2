"""Curator account collector — 逐 AI 策展账号抓取（替代泛关键词搜索）。

CLI: python -m akb2.briefing.intake.account_collector [--output <path>]
     [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--tier en|zh] [--dry-run]

真实抓取链路（替代 l0_collector 的泛关键词搜索，解决内容噪音）：
  对 config/curator-accounts.yaml 的 fetch_tier.<tier> 每个账号执行：
    twitter user-posts <h> -n <cap*2> --json
  RT / 回复 / 日期窗过滤在 raw 层 `_post_filter` 完成（user-posts 无服务端过滤），
  再聚合所有账号结果 + 按 id 去重。

  - **开跑前 preflight 探针**（`PREFLIGHT_PROBE_ACCOUNTS`）：第一个账号即命中上游
    全局故障签名 → 立刻 `UpstreamCollectorBroken`，剩余账号不再遍历；探针就是第一个
    正式账号的抓取，正常路径不多付一次调用
  - 单个账号失败/0 条 → 跳过（warn），不影响其他账号
  - 全部账号 0 条**且本轮未见全局故障** → 降级 fixture，标 _fallback_used；
    见过全局故障则显式失败（`_raise_if_upstream_fatal_seen`），绝不降级
  - 账号间 pacing（默认 3s）规避限流（实测 ~34 账号触 429，en 层 19 个安全）

默认单 tier=en（19 账号，向后兼容）。P2-1：`--merge`（或 env AKB2_FETCH_MERGE=1）
抓 en+zh 双 tier 合并，并按 tier 给每条打 `lang` 标（en/zh），跨 tier 按 id 去重；
配合 translate 的 per-tweet 分流（P3a：lang=zh 跳过 en→zh 翻译，保留中文原文）。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .base import REPO_ROOT
from .l0_collector import (
    FIXTURE_PATH,
    PATCH_RUNBOOK,
    TWITTER_BIN,
    TWITTER_TIMEOUT_SEC,
    UPSTREAM_FATAL_FAILFAST_STREAK,
    UpstreamCollectorBroken,
    _twitter_cli_to_akb2,
    diagnose_upstream_fatal,
    match_upstream_fatal,
)

CURATOR_CONFIG = REPO_ROOT / "config" / "curator-accounts.yaml"

_DEFAULT_TIER = "en"
_DEFAULT_CAP = 10           # 每账号最多取最新 N 条
_DEFAULT_PACING_SEC = 3.0   # 账号间停顿，规避限流
_DEFAULT_SUPPLEMENT_THRESHOLD = 30  # en 层低于此数时从 reserve_tier 补抓
_DEFAULT_MAX_RESERVE_ACCOUNTS = 10  # reserve 最多抓多少个账号（限流防护）

# 开跑前 preflight 探针的预算：遍历整个 tier 之前先看这么多个账号的结果。
# 取 1 是因为这类故障（ClientTransaction 初始化失败 / 补丁被 uv upgrade 冲掉）
# 对每个账号逐字复现 —— 第 1 个账号的结果已经是判据。
# 探针**不额外消耗配额**：它就是第一个正式账号的抓取，成功时结果照常进聚合，
# 正常路径的总调用次数与没有探针时完全一致（回归见 tests/briefing/
# test_intake_preflight.py::test_preflight_adds_no_extra_call_when_upstream_healthy）。
PREFLIGHT_PROBE_ACCOUNTS = 1


def _load_fetch_tier(tier: str = _DEFAULT_TIER) -> list[str]:
    """从 curator-accounts.yaml 读取账号列表（去掉行内注释/空白）。

    优先 fetch_tier.<tier>（en/zh）；找不到时回退顶层 <tier>_tier（如 reserve_tier，
    它在配置里是顶层 key 而非 fetch_tier 子项）。
    """
    try:
        with open(CURATOR_CONFIG) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        sys.stderr.write(f"WARNING: curator config not found at {CURATOR_CONFIG}\n")
        return []
    raw = (cfg.get("fetch_tier") or {}).get(tier)
    if raw is None:
        # 顶层 <tier>_tier 兼容（reserve_tier 等）
        raw = cfg.get(f"{tier}_tier")
    raw = raw or []
    # yaml 已剥注释；再防御性 strip + 去空
    return [str(h).strip() for h in raw if str(h).strip()]


def _tag_lang(tweets: list[dict], lang: str) -> list[dict]:
    """给每条 tweet 打 lang 标（en tier→en, zh tier→zh）。

    供 translate 阶段 per-tweet 分流（P3a：lang=zh 跳过 en→zh 翻译，保留中文原文）。
    """
    for t in tweets:
        t["lang"] = lang
    return tweets


class AccountCollector:
    source_name = "curator_accounts"

    def __init__(
        self,
        tier: str = _DEFAULT_TIER,
        per_account_cap: int = _DEFAULT_CAP,
        pacing_sec: float = _DEFAULT_PACING_SEC,
        supplement_threshold: int = _DEFAULT_SUPPLEMENT_THRESHOLD,
        max_reserve_accounts: int = _DEFAULT_MAX_RESERVE_ACCOUNTS,
    ):
        self.tier = tier
        self.per_account_cap = per_account_cap
        self.pacing_sec = pacing_sec
        self.supplement_threshold = supplement_threshold
        self.max_reserve_accounts = max_reserve_accounts
        self.used_fallback: bool = False
        # 连续命中同一上游全局故障签名的账号数；任一账号成功即归零。
        self._upstream_fatal_streak: int = 0
        self._upstream_fatal_sig: str | None = None
        self._upstream_fatal_sample: str = ""
        # **最近一次** _fetch_one 是否命中全局签名（每次调用开头清零）。
        # preflight 判据必须只看探针自己这一次的结果 —— 用 upstream_fatal_seen
        # （整轮粘滞）会让 en 层早先的一次抖动误杀 zh 层的探针。
        self._last_fatal_sig: str | None = None
        # 整轮是否见过全局故障（即使未达 fail-fast 阈值）—— 用于区分
        # 「上游工具坏了」与「curator 配置里 zh 账号不对」两类完全不同的 0 产量。
        self.upstream_fatal_seen: str | None = None

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

    def _fetch_one(
        self,
        handle: str,
        since_date: str | None,
        until_date: str | None,
    ) -> list[dict]:
        """抓单个账号；失败/0 条返回 []（不抛、不降级，由调用方聚合处理）。

        2026-07-28 端点切换：`search "" --from <h>` → `user-posts <h>`。
        当时 search 端点连续 404（疑似 SearchTimeline persisted queryId 轮换），
        UserTweets / UserByScreenName 等端点正常。
        ⚠ 同日 16:29 复测 `twitter search "" --from karpathy -n 1 --json` 已恢复
        `ok: true`，即"search 恒 404"这条结论**不再成立**（成因未定）。保留
        user-posts 不是因为 search 坏了，而是它才是"按账号取时间线"的对应端点。
        user-posts 不支持 --exclude / --since / --until，三类过滤移到
        `_post_filter`（raw 层）：
        RT 用 isRetweet 字段、回复用 text 前缀、日期窗用 createdAtISO。
        抓取量放大到 cap*2 再截断，补偿过滤损耗（有界：至多 2 倍）。
        """
        cmd = [
            TWITTER_BIN, "user-posts", handle,
            "-n", str(self.per_account_cap * 2),
            "--json",
        ]
        self._last_fatal_sig = None

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TWITTER_TIMEOUT_SEC
            )
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"WARNING: @{handle} timeout ({TWITTER_TIMEOUT_SEC}s) — skip\n")
            return []

        if result.returncode != 0:
            excerpt = (result.stderr or "")[:200].replace("\n", " | ")
            sys.stderr.write(
                f"WARNING: @{handle} twitter-cli exit {result.returncode}: {excerpt} — skip\n"
            )
            self._note_upstream_failure(result.stderr or "")
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            sys.stderr.write(f"WARNING: @{handle} output not JSON — skip\n")
            return []
        if not payload.get("ok"):
            sys.stderr.write(f"WARNING: @{handle} payload.ok=false — skip\n")
            # payload.ok=false 也可能是全局故障的下游表现（ClientTransaction 失败后
            # GraphQL 返回 404，warning 打在 stderr、错误体在 stdout）。两侧都查。
            self._note_upstream_failure((result.stderr or "") + (result.stdout or ""))
            return []
        raw = self._post_filter(
            handle, payload.get("data") or [], since_date, until_date
        )
        tweets = _twitter_cli_to_akb2(raw)
        if tweets:
            # 有账号真的取到了内容 → 上游没坏，连击归零（防单账号私密/限流误判为全局故障）
            self._upstream_fatal_streak = 0
            self._upstream_fatal_sig = None
        return tweets

    def _post_filter(
        self,
        handle: str,
        raw: list[dict],
        since_date: str | None,
        until_date: str | None,
    ) -> list[dict]:
        """user-posts 端点缺少服务端过滤，在 raw 层补齐（替代原 search 语法）。

        - RT：`isRetweet=true` 剔除（原 `--exclude retweets`）
        - 回复：text 以 "@" 开头剔除（原 `--exclude replies`；user-posts 走
          UserTweets 端点、对应 X 的 Posts tab，纯回复本就不在其中，此为兜底）
        - 日期窗：createdAtISO 与 since/until 做 ISO 字符串日界比较
          （原 `--since/--until`；缺 createdAtISO 的条目**剔除**——与 pipeline
          侧「缺时间戳即拒」的既有判据一致，不做保守保留）
        - 截断到 per_account_cap（抓取时放大了 2 倍）
        """
        kept: list[dict] = []
        dropped_rt = dropped_reply = dropped_window = dropped_nodate = 0
        for t in raw:
            if t.get("isRetweet"):
                dropped_rt += 1
                continue
            if str(t.get("text", "")).startswith("@"):
                dropped_reply += 1
                continue
            iso = str(t.get("createdAtISO", ""))
            day = iso[:10]
            if not day:
                dropped_nodate += 1
                continue
            if since_date and day < since_date:
                dropped_window += 1
                continue
            if until_date and day > until_date:
                dropped_window += 1
                continue
            kept.append(t)
            if len(kept) >= self.per_account_cap:
                break
        dropped = dropped_rt + dropped_reply + dropped_window + dropped_nodate
        if dropped:
            sys.stderr.write(
                f"INFO [account_collector]: @{handle} post-filter kept {len(kept)}"
                f"/{len(raw)} (rt={dropped_rt} reply={dropped_reply} "
                f"window={dropped_window} nodate={dropped_nodate})\n"
            )
        return kept

    def _note_upstream_failure(self, text: str) -> None:
        """记录一次失败是否属于上游全局故障，并维护连击计数。"""
        sig = match_upstream_fatal(text)
        if not sig:
            self._upstream_fatal_streak = 0
            self._upstream_fatal_sig = None
            return
        if sig == self._upstream_fatal_sig:
            self._upstream_fatal_streak += 1
        else:
            self._upstream_fatal_sig = sig
            self._upstream_fatal_streak = 1
        self.upstream_fatal_seen = sig
        self._last_fatal_sig = sig
        self._upstream_fatal_sample = text[:300].replace("\n", " | ")

    def _abort_if_upstream_broken(self) -> None:
        """连续命中同一全局签名达阈值 → 中止整轮（不降级 fixture）。"""
        if (
            self._upstream_fatal_sig
            and self._upstream_fatal_streak >= UPSTREAM_FATAL_FAILFAST_STREAK
        ):
            raise UpstreamCollectorBroken(
                self._upstream_fatal_sig,
                self._upstream_fatal_streak,
                self._upstream_fatal_sample,
                diagnosis=diagnose_upstream_fatal(self._upstream_fatal_sig),
            )

    def _abort_if_preflight_failed(self, tier: str, handle: str) -> None:
        """开跑前探针：前 PREFLIGHT_PROBE_ACCOUNTS 个账号命中全局签名即中止整轮。

        与 `_abort_if_upstream_broken`（连击 3）的分工：连击护栏是「跑着跑着坏了」
        的兜底；探针是「开跑前就知道」—— 本地补丁被 `uv tool upgrade twitter-cli`
        冲掉这类故障对每个账号逐字复现，没必要再赔 2 个账号的超时。

        判据只用**探针这一次调用**返回的全局故障签名（可观测行为）；补丁在位性、
        登录态只进归因文案，不参与是否中止的决定。
        """
        if not self._last_fatal_sig:
            return
        sys.stderr.write(
            f"ERROR [account_collector]: preflight 探针（tier={tier} @{handle}，预算 "
            f"{PREFLIGHT_PROBE_ACCOUNTS} 账号）命中上游全局故障签名，中止本轮 —— "
            "剩余账号不再遍历\n"
        )
        raise UpstreamCollectorBroken(
            self._last_fatal_sig,
            PREFLIGHT_PROBE_ACCOUNTS,
            self._upstream_fatal_sample,
            diagnosis=diagnose_upstream_fatal(self._last_fatal_sig),
        )

    def _fetch_tier_raw(
        self,
        tier: str,
        since_date: str | None = None,
        until_date: str | None = None,
        *,
        allow_reserve: bool = False,
    ) -> list[dict]:
        """抓单 tier 真实推文；twitter-cli 缺失 / 无账号 / 全空 → 返回 []（不降级 fixture）。

        降级 fixture 的决策交给调用方：单 tier 的 _fetch_real 仍兜底 fixture；
        collect_merged 不让单个空 tier 注入 fixture（precondition：zh 为 0 应修配置）。
        """
        if not Path(TWITTER_BIN).exists():
            sys.stderr.write(f"WARNING: twitter-cli not found at {TWITTER_BIN}\n")
            return []

        handles = _load_fetch_tier(tier)
        if not handles:
            sys.stderr.write(f"WARNING: fetch_tier.{tier} empty in curator config\n")
            return []

        seen: set[str] = set()
        aggregated: list[dict] = []
        ok_accounts = 0
        # 前 PREFLIGHT_PROBE_ACCOUNTS 个账号同时充当 preflight 探针：抓取照常进聚合，
        # 但一命中全局签名就立刻中止，不遍历剩下 46 个账号（也就不会被推进 429）。
        probe_boundary = min(PREFLIGHT_PROBE_ACCOUNTS, len(handles))
        for i, handle in enumerate(handles):
            if i > 0 and self.pacing_sec > 0:
                time.sleep(self.pacing_sec)  # 限流防护
            tweets = self._fetch_one(handle, since_date, until_date)
            if i < probe_boundary:
                self._abort_if_preflight_failed(tier, handle)
            self._abort_if_upstream_broken()
            if tweets:
                ok_accounts += 1
            for t in tweets:
                tid = str(t.get("id", ""))
                if tid and tid not in seen:
                    seen.add(tid)
                    aggregated.append(t)

        if aggregated:
            sys.stderr.write(
                f"INFO [account_collector]: tier={tier} {ok_accounts}/{len(handles)} accounts "
                f"yielded {len(aggregated)} unique tweets\n"
            )

        # SC-P3c-1: reserve 补抓。
        # 主 tier 不足时从 reserve_tier 顺序补（zh 不补，避免英文 reserve 污染中文）。
        if allow_reserve and len(aggregated) < self.supplement_threshold:
            reserve_handles = _load_fetch_tier("reserve")
            if reserve_handles:
                sys.stderr.write(
                    f"INFO [account_collector]: {len(aggregated)} tweets < threshold "
                    f"{self.supplement_threshold}, supplementing from reserve_tier "
                    f"(cap={self.max_reserve_accounts} accounts)\n"
                )
                reserve_used = 0
                for rh in reserve_handles:
                    if len(aggregated) >= self.supplement_threshold:
                        break
                    if reserve_used >= self.max_reserve_accounts:
                        break
                    if reserve_used > 0 and self.pacing_sec > 0:
                        time.sleep(self.pacing_sec)
                    new_tweets = self._fetch_one(rh, since_date, until_date)
                    self._abort_if_upstream_broken()
                    reserve_used += 1
                    for t in new_tweets:
                        tid = str(t.get("id", ""))
                        if tid and tid not in seen:
                            seen.add(tid)
                            aggregated.append(t)
                sys.stderr.write(
                    "INFO [account_collector]: after reserve supplement: "
                    f"{len(aggregated)} tweets\n"
                )

        return aggregated

    def _fetch_real(
        self,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict]:
        """单 tier 抓取（向后兼容）；全空降级 fixture。"""
        aggregated = self._fetch_tier_raw(
            self.tier, since_date, until_date, allow_reserve=True
        )
        if not aggregated:
            self._raise_if_upstream_fatal_seen()
            sys.stderr.write(
                f"WARNING: curator fetch_tier.{self.tier} returned 0 tweets — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()
        return aggregated

    def _raise_if_upstream_fatal_seen(self) -> None:
        """0 产量 + 本轮见过全局故障 → 显式失败，不降级 fixture。

        与 `_abort_if_upstream_broken` 的区别：后者是「连击达阈值就立刻停」（省时间），
        这里是「跑完了但一无所获，且已知上游坏了」的兜底归因（防伪装成无素材）。
        """
        if self.upstream_fatal_seen:
            raise UpstreamCollectorBroken(
                self.upstream_fatal_seen,
                max(self._upstream_fatal_streak, 1),
                self._upstream_fatal_sample,
                diagnosis=diagnose_upstream_fatal(self.upstream_fatal_seen),
            )

    def collect_merged(
        self,
        dry_run: bool = False,
        since_date: str | None = None,
        until_date: str | None = None,
    ) -> list[dict]:
        """P2-1: 抓 en+zh 双 tier，按 tier 打 lang 标，跨 tier 按 id 去重。

        - zh 产量为 0 时**不**注入 fixture（precondition：为 0 应先修 curator-accounts.yaml），
          仅 warn，本次仅 en 上线，不阻塞 pipeline。
        - en+zh 双 tier 均空才降级 fixture（保 pipeline 不崩）。
        """
        if dry_run:
            self.used_fallback = True
            return self._read_fixture()

        en = _tag_lang(
            self._fetch_tier_raw("en", since_date, until_date, allow_reserve=True), "en"
        )
        zh = _tag_lang(
            self._fetch_tier_raw("zh", since_date, until_date, allow_reserve=False), "zh"
        )
        if not zh:
            # 归因必须分清：见过上游全局故障时，0 产量的成因是工具坏了，
            # 让运维去"复核 curator-accounts.yaml"是把人指向错误方向。
            # 2026-07-28 事故里日志正是这么误导的（constitution 原则 4：失败可见 ≠ 归因可乱写）。
            if self.upstream_fatal_seen:
                sys.stderr.write(
                    "WARNING: fetch_tier.zh 产量为 0 —— 成因是上游 twitter-cli 全局故障"
                    f"（{self.upstream_fatal_seen}），**不是** curator-accounts.yaml 配置问题\n"
                )
            else:
                sys.stderr.write(
                    "WARNING: fetch_tier.zh 产量为 0 —— 本次仅 en 上线；"
                    "precondition 提示先复核 config/curator-accounts.yaml 的 zh 账号\n"
                )

        seen: set[str] = set()
        merged: list[dict] = []
        for t in en + zh:  # en 优先（同 id 跨 tier 取先到）
            tid = str(t.get("id", ""))
            if tid and tid not in seen:
                seen.add(tid)
                merged.append(t)

        if not merged:
            # 见过上游全局故障 → 显式失败，绝不降级 fixture。降级会把「工具坏了」
            # 伪装成「今天没素材」，正是 12 天假数据入库的成因。
            self._raise_if_upstream_fatal_seen()
            sys.stderr.write(
                "WARNING: en+zh 双 tier 均 0 tweets — fallback to fixture\n"
            )
            self.used_fallback = True
            return self._read_fixture()

        sys.stderr.write(
            f"INFO [account_collector]: merged en={len(en)} + zh={len(zh)} → {len(merged)} unique\n"
        )
        return merged


def main():
    parser = argparse.ArgumentParser(
        description="Curator account collector (per-account twitter fetch)"
    )
    parser.add_argument("--output", default="/tmp/account-out.json", help="Output JSON path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Use fixture instead of twitter-cli",
    )
    parser.add_argument("--since", default=None, help="Collect tweets since YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="Collect tweets until YYYY-MM-DD")
    parser.add_argument(
        "--tier",
        default=os.environ.get("AKB2_FETCH_TIER", _DEFAULT_TIER),
        help="curator fetch_tier (en|zh)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        default=os.environ.get("AKB2_FETCH_MERGE") == "1",
        help="P2-1: 抓 en+zh 双 tier 合并并按 tier 打 lang 标（覆盖 --tier）",
    )
    args = parser.parse_args()

    cap = int(os.environ.get("AKB2_ACCOUNT_CAP", _DEFAULT_CAP))
    pacing = float(os.environ.get("AKB2_ACCOUNT_PACING_SEC", _DEFAULT_PACING_SEC))

    collector = AccountCollector(tier=args.tier, per_account_cap=cap, pacing_sec=pacing)
    try:
        if args.merge:
            tweets = collector.collect_merged(
                dry_run=args.dry_run,
                since_date=args.since,
                until_date=args.until,
            )
        else:
            tweets = collector.collect(
                dry_run=args.dry_run,
                since_date=args.since,
                until_date=args.until,
            )
    except UpstreamCollectorBroken as exc:
        # 显式失败：不写 output、不落 fixture。调用方（pipeline）看到非零退出码即中止，
        # 不会拿到一份"看起来正常"的 tweets 列表。
        sys.stderr.write(
            f"ERROR [account_collector]: {exc}\n"
            f"ERROR [account_collector]: 排障入口 —— 先跑 `twitter status`（登录态）"
            f"与 `twitter user-posts karpathy -n 2 --json`（生产端点的采集能力）；"
            f"完整自检清单见 {PATCH_RUNBOOK} 的「自检」一节\n"
        )
        if exc.sample:
            sys.stderr.write(f"ERROR [account_collector]: 上游原始输出: {exc.sample}\n")
        return 4

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output: dict = {"tweets": tweets}
    if collector.used_fallback:
        output["_fallback_used"] = True
    with open(out_path, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
