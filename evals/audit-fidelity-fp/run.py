#!/usr/bin/env python3
"""audit fail 级确定性检查的**离线误报率评测**。

被测对象：`akb2.briefing.audit._check_translation_fidelity` 里 4 个 fail 级 kind
（`number_mismatch` / `url_missing` / `handle_missing` / `metric_fabricated`）。
它们是日报可用性单点 —— 一次误报即 `standard=fail` → `verdict=fail` → 当天不发布。

两个必须分开的概念（上一轮被混用过，这里在数据结构上分开）：

* **曝光面** `exposure` —— 有多少配对会触发某个判据。分母是配对数，只描述"多久开火
  一次"，**不含对错**。恒等回放也能算它。
* **误报率** `false_positive` —— 触发中有多少是错的。它需要「英文原文 ↔ 中文译文」
  真实配对 **加上人工裁决**；两者缺一律输出 `null` + `insufficient_reason`，
  绝不给一个看起来像结论的数字。

语料来源（`--corpus-mode`）：

* `paired`（默认）—— 读 `translate.dump_fidelity_pairs` 落盘的真实译前/译后配对
  （机器本地状态根 `<state_root>/fidelity-samples/*.jsonl`）。**只有这个模式能算
  误报率。**
* `identity` —— 用已发布中文简报自身当 en 侧（en := zh）回放 vault，**只能**算曝光面。
  为什么不能算误报率：en == zh 时每个数字/URL/@handle 必然逐字保留，数字/URL/handle
  三类判据在构造上不可能报错，"0 误报"是同义反复。它唯一的有效用途是暴露"en 侧其实
  不是英文"这类前提破坏（metric_fabricated 的缺席检验会 100% 命中）。本模式下
  `false_positive` 一律 null，verdict 固定 `exposure_only`。

人工裁决：`--labels <json>`，形如
    {"<trigger_id>": {"verdict": "true_positive"|"false_positive", "note": "..."}}
`trigger_id` 由报告里的 `pending_triggers` 给出（kind/field/en/zh 的 sha256 前 16 位，
内容不变则 id 稳定，可逐步累积裁决）。

退出码：0 = verdict 为 measured；3 = 样本不足 / 未裁决 / 仅曝光面（fail-visible：
想拿数字的调用方会拿到非零）；2 = 参数或语料读取错误。

用法::

    # 真实配对语料（默认读状态根）
    python3 evals/audit-fidelity-fp/run.py --report-out /tmp/fp.json

    # 曝光面回放（只读 vault，绝不写）
    python3 evals/audit-fidelity-fp/run.py --corpus-mode identity \\
        --vault-root "/Volumes/AKB2/Obsidian" --report-out /tmp/exposure.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from datetime import date as date_cls
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT / "src",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from akb2.briefing.audit import (  # noqa: E402
    _check_translation_fidelity,
    _translation_pairs,
)
from akb2.briefing.translate import (  # noqa: E402
    FIDELITY_SAMPLE_RETENTION_DAYS,
    fidelity_sample_dir,
)

# 被测的 4 个 fail 级 kind（与 audit_fix._RETRANSLATE_KINDS 覆盖的确定性判据同集合）
FAIL_KINDS: tuple[str, ...] = (
    "number_mismatch",
    "url_missing",
    "handle_missing",
    "metric_fabricated",
)

# 出数门槛（具名常量，宪法原则 1/4：不够就明说不够，不给假结论）
# 30 个配对 ≈ 一天的受检字段量（实测 26.0 个/篇）：低于一天的量连"这判据会不会开火"
# 都说不准，更不该报误报率。
MIN_PAIRS_FOR_RATE = 30
# 单个 kind 至少要有这么多条人工裁决才给它出误报率。5 条只够给出"量级"，不是精确率；
# 报告里的 `confidence` 字段会如实标注这一点。
MIN_ADJUDICATED_PER_KIND = 5

_VERDICT_TRUE = "true_positive"
_VERDICT_FALSE = "false_positive"

_BRIEFING_GLOB = "AI TRENDS/Social/*Daily Briefing*.md"
_TWEET_HEAD_RE = re.compile(r"^\*\*(@[A-Za-z0-9_]{2,15})\*\*")
_FILE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------------------
# 语料加载
# ---------------------------------------------------------------------------

def load_paired_corpus(corpus_dir: Path, *, max_age_days: int) -> dict:
    """读配对语料目录，返回 {pairs, stats}。过期样本按保留天数排除并计数。"""
    stats = {
        "files": 0, "lines": 0, "malformed_lines": 0,
        "pairs_total": 0, "pairs_expired_excluded": 0,
        "pairs_identity_excluded": 0, "pairs_usable": 0,
        "dates": [], "days_total": 0,
    }
    pairs: list[dict] = []
    if not corpus_dir.is_dir():
        return {"pairs": pairs, "stats": stats}

    today = datetime.now(UTC).date()
    for path in sorted(corpus_dir.glob("*.jsonl")):
        stats["files"] += 1
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            stats["lines"] += 1
            try:
                rec = json.loads(raw)
                field, en, zh = rec["field"], rec["en"], rec["zh"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                # 不静默丢：坏行数进报告，否则"语料变少了"会被读成"判据不开火了"
                stats["malformed_lines"] += 1
                print(f"WARNING: {path.name} 有坏行（{exc}），已计入 malformed_lines",
                      file=sys.stderr)
                continue
            stats["pairs_total"] += 1
            rec_date = str(rec.get("date") or "")
            if _age_days(rec_date, today) > max_age_days:
                stats["pairs_expired_excluded"] += 1
                continue
            if not en or not zh or en == zh:
                stats["pairs_identity_excluded"] += 1
                continue
            pairs.append({"field": field, "en": en, "zh": zh, "date": rec_date})
    stats["pairs_usable"] = len(pairs)
    stats["dates"] = sorted({p["date"] for p in pairs if p["date"]})
    stats["days_total"] = len(stats["dates"])
    return {"pairs": pairs, "stats": stats}


def _age_days(date_str: str, today: date_cls) -> int:
    """样本年龄（天）。日期缺失/不可解析 → 0（当作新鲜，不静默丢样本）。"""
    try:
        return (today - date_cls.fromisoformat(date_str)).days
    except (ValueError, TypeError):
        return 0


def load_identity_corpus(vault_root: Path) -> dict:
    """从 vault 已发布中文简报构造 en := zh 的恒等配对（**只读**，仅算曝光面）。"""
    akb2_root = vault_root / "Praise Space" / "AKB2.0"
    files = sorted(akb2_root.glob(_BRIEFING_GLOB))
    stats = {
        "files": len(files), "lines": 0, "malformed_lines": 0,
        "pairs_total": 0, "pairs_expired_excluded": 0,
        "pairs_identity_excluded": 0, "pairs_usable": 0,
        "dates": [], "days_total": 0,
    }
    pairs: list[dict] = []
    for path in files:
        m = _FILE_DATE_RE.search(path.name)
        day = m.group(1) if m else ""
        synth = parse_published_briefing(path.read_text(encoding="utf-8"))
        for pair in _translation_pairs(synth, synth):
            if not pair["zh"]:
                continue
            pairs.append({
                "field": pair["field"], "en": pair["zh"], "zh": pair["zh"],
                "date": day, "source_file": path.name,
            })
    stats["pairs_total"] = len(pairs)
    stats["pairs_usable"] = len(pairs)
    stats["dates"] = sorted({p["date"] for p in pairs if p["date"]})
    stats["days_total"] = len(stats["dates"])
    return {"pairs": pairs, "stats": stats}


def parse_published_briefing(text: str) -> dict:
    """把已发布简报 markdown 还原成 synth 形状（executive_summary / 主题 / 推文）。

    只解析确定性 markdown 结构（`## 执行摘要` / `### <主题>` / `**@handle**` + `>` 引用），
    解析不到的部分留空 —— 空字段被 `_translation_pairs` 与检查器自然跳过。
    """
    lines = text.splitlines()
    exec_summary: list[str] = []
    fds: list[dict] = []
    tweets: list[dict] = []
    section = None
    cur_fd: dict | None = None
    cur_tweet: dict | None = None

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("## "):
            section = line[3:].strip()
            cur_fd, cur_tweet = None, None
            continue
        if line.startswith("### "):
            cur_fd = {
                "topic": line[4:].strip(), "summary": "",
                "supporting_tweet_ids": [],
            }
            fds.append(cur_fd)
            cur_tweet = None
            continue
        if section == "执行摘要" and line.strip():
            exec_summary.append(line.strip())
            continue
        head = _TWEET_HEAD_RE.match(line)
        if head and cur_fd is not None:
            tid = f"t{len(tweets)}"
            cur_tweet = {"id": tid, "author": head.group(1), "text": "", "score": 1.0}
            tweets.append(cur_tweet)
            cur_fd["supporting_tweet_ids"].append(tid)
            continue
        if line.startswith(">") and cur_tweet is not None:
            body = line.lstrip("> ").strip()
            cur_tweet["text"] = (cur_tweet["text"] + " " + body).strip()
            continue
        if cur_fd is not None and cur_tweet is None and line.strip():
            if not line.startswith(("*", "[", "#", "|", "-")):
                cur_fd["summary"] = (cur_fd["summary"] + " " + line.strip()).strip()

    return {
        "executive_summary": " ".join(exec_summary).strip(),
        "focus_discussions": fds,
        "tweets": tweets,
    }


# ---------------------------------------------------------------------------
# 判据执行（走生产函数，不复刻严重度规则）
# ---------------------------------------------------------------------------

def _pair_to_synths(field: str, en: str, zh: str) -> tuple[dict, dict, str] | None:
    """把单个配对还原成最小 synth 对，使 `_translation_pairs` 恰好产出该字段。

    严重度依赖字段身份（例如推文正文的 url_missing 只判 warn），所以必须把配对放回
    同类字段槽位，不能一律塞 executive_summary —— 否则评测口径与生产不同。
    """
    if field == "executive_summary":
        return ({"executive_summary": zh}, {"executive_summary": en}, "executive_summary")
    if field.startswith("focus_discussions[") and field.endswith("].summary"):
        return (
            {"focus_discussions": [{"topic": "", "summary": zh}]},
            {"focus_discussions": [{"topic": "", "summary": en}]},
            "focus_discussions[0].summary",
        )
    if field.startswith("focus_discussions[") and field.endswith("].topic"):
        return (
            {"focus_discussions": [{"topic": zh, "summary": ""}]},
            {"focus_discussions": [{"topic": en, "summary": ""}]},
            "focus_discussions[0].topic",
        )
    if field.startswith("tweets[") and field.endswith("].text"):
        def _mk(text: str) -> dict:
            return {
                "tweets": [{"id": "p0", "text": text, "lang": "en", "score": 1.0}],
                "focus_discussions": [
                    {"topic": "", "summary": "", "supporting_tweet_ids": ["p0"]}
                ],
            }
        return (_mk(zh), _mk(en), "tweets[0].text")
    return None


def trigger_id(kind: str, field: str, en: str, zh: str) -> str:
    payload = "\x00".join((kind, field, en, zh)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def run_checks(pairs: list[dict], *, disable_cjk_gate: bool = False) -> tuple[list[dict], dict]:
    """对每个配对跑生产判据，返回 (fail 级触发列表, 统计)。

    `disable_cjk_gate=True` 把 `audit._is_cjk_source` 强制回 False，即**还原
    `_check_metric_fabrication` 加 CJK 源门之前的实现**。用途只有一个：让"加门前
    恒等回放会打出多少条 metric_fabricated"这个数字可复现（该门是否还在起作用，
    靠对比开/关两次的曝光面来判，而不是靠注释里写个数字）。
    """
    triggers: list[dict] = []
    stats = {
        "pairs_checked": 0, "pairs_unrecognized_field": 0,
        "cjk_source_gate_disabled": bool(disable_cjk_gate),
    }
    import akb2.briefing.audit as audit_mod
    original_gate = audit_mod._is_cjk_source
    if disable_cjk_gate:
        audit_mod._is_cjk_source = lambda _en: False
    try:
        for pair in pairs:
            built = _pair_to_synths(pair["field"], pair["en"], pair["zh"])
            if built is None:
                stats["pairs_unrecognized_field"] += 1
                print(f"WARNING: 未识别字段 {pair['field']!r}，未参与判定", file=sys.stderr)
                continue
            zh_synth, en_synth, target = built
            stats["pairs_checked"] += 1
            for issue in _check_translation_fidelity(zh_synth, en_synth):
                if issue["field"] != target or issue.get("severity") != "fail":
                    continue
                if issue["kind"] not in FAIL_KINDS:
                    continue
                triggers.append({
                    "id": trigger_id(
                        issue["kind"], pair["field"], pair["en"], pair["zh"]
                    ),
                    "kind": issue["kind"],
                    "field": pair["field"],
                    "date": pair.get("date", ""),
                    "msg": issue["msg"],
                    "en": pair["en"],
                    "zh": pair["zh"],
                })
    finally:
        audit_mod._is_cjk_source = original_gate
    return triggers, stats


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def build_report(
    *,
    corpus_mode: str,
    corpus_dir: str,
    corpus: dict,
    triggers: list[dict],
    check_stats: dict,
    labels: dict,
    max_age_days: int,
) -> dict:
    pairs_usable = corpus["stats"]["pairs_usable"]
    exposure_kinds: dict[str, dict] = {}
    fp_kinds: dict[str, dict] = {}
    pending: list[dict] = []

    triggering_pair_keys: set[tuple[str, str]] = set()
    for kind in FAIL_KINDS:
        hits = [t for t in triggers if t["kind"] == kind]
        exposure_kinds[kind] = {
            "triggers": len(hits),
            "pairs_triggering": len({(t["field"], t["zh"]) for t in hits}),
            "days_triggering": len({t["date"] for t in hits if t["date"]}),
            "trigger_rate_per_pair": (
                round(len(hits) / pairs_usable, 4) if pairs_usable else None
            ),
        }
        for hit in hits:
            triggering_pair_keys.add((hit["field"], hit["zh"]))

        adjudged = [(t, labels.get(t["id"], {}).get("verdict")) for t in hits]
        tp = sum(1 for _t, v in adjudged if v == _VERDICT_TRUE)
        fp = sum(1 for _t, v in adjudged if v == _VERDICT_FALSE)
        unlabeled = [t for t, v in adjudged if v not in (_VERDICT_TRUE, _VERDICT_FALSE)]
        pending.extend({
            "id": t["id"], "kind": t["kind"], "field": t["field"], "date": t["date"],
            "msg": t["msg"],
            "en": t["en"][:400], "zh": t["zh"][:400],
        } for t in unlabeled)

        reason = None
        rate = None
        if corpus_mode == "identity":
            reason = (
                "identity_replay_cannot_measure_false_positive: en == zh 时数字/URL/"
                "@handle 必然逐字保留，判据在构造上不可能报错，任何"
                "「0 误报」都是同义反复"
            )
        elif pairs_usable < MIN_PAIRS_FOR_RATE:
            reason = (
                f"insufficient_corpus: pairs_usable={pairs_usable} < "
                f"MIN_PAIRS_FOR_RATE={MIN_PAIRS_FOR_RATE}"
            )
        elif not hits:
            reason = "no_trigger: 该判据在本语料上一次未触发，误报率无定义（曝光面为 0）"
        elif tp + fp < MIN_ADJUDICATED_PER_KIND:
            reason = (
                f"insufficient_adjudication: adjudicated={tp + fp} < "
                f"MIN_ADJUDICATED_PER_KIND={MIN_ADJUDICATED_PER_KIND}"
                f"，待裁决 {len(unlabeled)} 条（见 pending_triggers）"
            )
        else:
            rate = round(fp / (tp + fp), 4)

        fp_kinds[kind] = {
            "adjudicated_total": tp + fp,
            "true_positive": tp,
            "false_positive": fp,
            "pending_human": len(unlabeled),
            "false_positive_rate": rate,
            "confidence": (
                None if rate is None
                else ("量级估计" if tp + fp < 20 else "可用于门控讨论")
            ),
            "insufficient_reason": reason,
        }

    measurable = [k for k in FAIL_KINDS if exposure_kinds[k]["triggers"] > 0]
    measured = [k for k in measurable if fp_kinds[k]["false_positive_rate"] is not None]
    if corpus_mode == "identity":
        verdict = "exposure_only"
    elif pairs_usable < MIN_PAIRS_FOR_RATE:
        verdict = "insufficient_sample"
    elif not measurable:
        verdict = "no_exposure"
    elif len(measured) == len(measurable):
        verdict = "measured"
    elif measured:
        verdict = "partially_measured"
    else:
        verdict = "insufficient_sample"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "checked_kinds": list(FAIL_KINDS),
        "corpus": {
            "mode": corpus_mode,
            "dir": corpus_dir,
            "max_age_days": max_age_days,
            **corpus["stats"],
        },
        "thresholds": {
            "MIN_PAIRS_FOR_RATE": MIN_PAIRS_FOR_RATE,
            "MIN_ADJUDICATED_PER_KIND": MIN_ADJUDICATED_PER_KIND,
        },
        # 曝光面 ≠ 误报率：前者只说判据多久开火一次，后者说开火里有多少是错的
        "exposure": {
            "pairs_checked": check_stats["pairs_checked"],
            "pairs_unrecognized_field": check_stats["pairs_unrecognized_field"],
            "cjk_source_gate_disabled": check_stats["cjk_source_gate_disabled"],
            "pairs_triggering_any_fail_kind": len(triggering_pair_keys),
            "per_kind": exposure_kinds,
        },
        "false_positive": {"per_kind": fp_kinds},
        "verdict": verdict,
        "pending_triggers": pending,
    }


def render_summary(report: dict) -> str:
    c = report["corpus"]
    lines = [
        "audit fail 级检查误报率评测",
        f"  语料模式: {c['mode']}  目录: {c['dir']}",
        f"  覆盖天数: {c['days_total']}  文件/来源: {c['files']}",
        f"  配对: 总 {c['pairs_total']} / 可用 {c['pairs_usable']}"
        f" / 过期排除 {c['pairs_expired_excluded']}"
        f" / 恒等排除 {c['pairs_identity_excluded']}"
        f" / 坏行 {c['malformed_lines']}",
        f"  判定: verdict={report['verdict']}",
        "",
        "曝光面（判据多久开火一次，不含对错）:",
    ]
    for kind, ex in report["exposure"]["per_kind"].items():
        lines.append(
            f"  - {kind}: 触发 {ex['triggers']} 次 / 涉及配对 {ex['pairs_triggering']}"
            f" / 涉及天数 {ex['days_triggering']} / 每配对 {ex['trigger_rate_per_pair']}"
        )
    lines.append("")
    lines.append("误报率（触发中有多少是错的，需人工裁决）:")
    for kind, fp in report["false_positive"]["per_kind"].items():
        if fp["false_positive_rate"] is None:
            lines.append(
                f"  - {kind}: 样本不足，无法给出误报率 —— {fp['insufficient_reason']}"
            )
        else:
            lines.append(
                f"  - {kind}: {fp['false_positive_rate']:.1%}"
                f"（假阳 {fp['false_positive']}/裁决 {fp['adjudicated_total']}"
                f"，待裁决 {fp['pending_human']}，置信度: {fp['confidence']}）"
            )
    if report["verdict"] != "measured":
        lines.append("")
        lines.append(
            "结论: 样本不足，无法给出误报率。"
            "补语料 = 让 briefing 链路多跑几天（translate 会自动落盘配对），"
            f"补裁决 = 对 pending_triggers（{len(report['pending_triggers'])} 条）"
            "逐条人工判定后用 --labels 传回。"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-mode", choices=("paired", "identity"), default="paired")
    ap.add_argument("--corpus-dir", default=None,
                    help="paired 模式语料目录（默认 = translate 的状态根采样目录）")
    ap.add_argument("--vault-root", default=None,
                    help="identity 模式的 vault 根（只读）")
    ap.add_argument("--labels", default=None, help="人工裁决 JSON")
    ap.add_argument("--max-age-days", type=int, default=FIDELITY_SAMPLE_RETENTION_DAYS,
                    help="超过该天数的样本按过期排除（默认 = 采样保留天数常量）")
    ap.add_argument("--report-out", default=None, help="结构化报告落盘路径（JSON）")
    ap.add_argument(
        "--replay-without-cjk-gate", action="store_true",
        help="把 audit._is_cjk_source 强制回 False（= 还原加 CJK 源门之前的 "
             "_check_metric_fabrication），用于复现该门修掉了多少条构造性触发",
    )
    args = ap.parse_args(argv)

    if args.corpus_mode == "identity":
        if not args.vault_root:
            print("ERROR: identity 模式必须给 --vault-root", file=sys.stderr)
            return 2
        vault = Path(args.vault_root).expanduser()
        if not vault.is_dir():
            print(f"ERROR: vault 不存在: {vault}", file=sys.stderr)
            return 2
        corpus = load_identity_corpus(vault)
        corpus_dir = str(vault)
    else:
        corpus_dir_path = (
            Path(args.corpus_dir).expanduser() if args.corpus_dir
            else fidelity_sample_dir()
        )
        corpus = load_paired_corpus(corpus_dir_path, max_age_days=args.max_age_days)
        corpus_dir = str(corpus_dir_path)

    labels: dict = {}
    if args.labels:
        labels_path = Path(args.labels).expanduser()
        if not labels_path.is_file():
            print(f"ERROR: labels 文件不存在: {labels_path}", file=sys.stderr)
            return 2
        labels = json.loads(labels_path.read_text(encoding="utf-8"))

    triggers, check_stats = run_checks(
        corpus["pairs"], disable_cjk_gate=args.replay_without_cjk_gate
    )
    report = build_report(
        corpus_mode=args.corpus_mode,
        corpus_dir=corpus_dir,
        corpus=corpus,
        triggers=triggers,
        check_stats=check_stats,
        labels=labels,
        max_age_days=args.max_age_days,
    )

    if args.report_out:
        out = Path(args.report_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入: {out}")
    print(render_summary(report))
    return 0 if report["verdict"] == "measured" else 3


if __name__ == "__main__":
    raise SystemExit(main())
